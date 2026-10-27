import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.events import StoppedEvent
from app.run_context import prepare_run
from domain.configuration.models import RunConfiguration
from application.dto import AmplifierFrequencyResult, AmplifierMeasurementResult
from application.measurements.services import AmplifierMeasurementService
from infrastructure.config.json_config_repository import ConfigurationRepository
from application.dto import measurement_result_from_document
from infrastructure.persistence.json_result_repository import load_json_result
from infrastructure.persistence.json_result_repository import save_measurement_model
from analysis.reporting import export_csv, render_html_report
from release_manifest import build_manifest
from tests.fakes import safety_cleanup_for


class _Instrument:
    signal_gen = object()

    def __init__(self):
        self.current_power = -40
        self.operations = []

    def set_power(self, value):
        self.current_power = value
        self.operations.append(("set_power", value))

    def set_frequency(self, value):
        self.operations.append(("set_frequency", value))

    def set_center_frequency(self, value):
        self.operations.append(("set_center_frequency", value))

    def set_span(self, value):
        self.operations.append(("set_span", value))

    def setup_dut_power(self):
        self.operations.append(("setup_dut_power",))

    def power_on_sequence(self):
        self.operations.append(("power_on_sequence",))

    def power_off_sequence(self):
        self.operations.append(("power_off_sequence",))

    def rf_output_on(self):
        self.operations.append(("rf_on",))

    def rf_output_off(self):
        self.operations.append(("rf_off",))

    def measure_power_with_average(self):
        return self.current_power + 20

    def read_voltage(self, *_):
        return 5.0

    def read_current(self, *_):
        return 1.0

    def close_all(self, **_):
        return []


class _Events:
    def __init__(self):
        self.items = []

    def publish(self, event):
        self.items.append(event)


class OfflineCompletionTests(unittest.TestCase):
    def test_measurement_workers_are_created_outside_main_window_module(self):
        import ast

        project_root = Path(__file__).resolve().parents[1]
        gui_source = (project_root / "presentation" / "qt" / "main_window.py").read_text(encoding="utf-8-sig")
        class_names = {
            node.name
            for node in ast.walk(ast.parse(gui_source))
            if isinstance(node, ast.ClassDef)
        }

        removed_workers = {
            "LegacyWorkerSignals",
            "LegacyBaseWorker",
            "LegacyInstrumentWorker",
            "LegacyCableLossWorker",
            "LegacyDriverMappingWorker",
            "LegacyAmplifierWorker",
        }
        self.assertTrue(removed_workers.isdisjoint(class_names))
        imported_modules = {
            node.module: {alias.name for alias in node.names}
            for node in ast.walk(ast.parse(gui_source))
            if isinstance(node, ast.ImportFrom)
        }
        self.assertIn("InstrumentWorker", imported_modules["presentation.qt.workers"])
        self.assertNotIn("CableLossWorker", imported_modules["presentation.qt.workers"])
        self.assertNotIn("DriverMappingWorker", imported_modules["presentation.qt.workers"])
        self.assertNotIn("AmplifierWorker", imported_modules["presentation.qt.workers"])

    def test_prepare_run_writes_context_before_hardware_factory(self):
        plan = {
            "schema_version": "1.0", "template": False, "frequencies": {"values": [1.0], "unit": "GHz"},
            "signal_source": {"start_power": -10, "stop_power": 0, "step": 1, "unit": "dBm"},
            "compression_point": {"type": "3dB"}, "attenuator": {"value": 0, "unit": "dB"},
            "dut": {"max_input_power": 30, "power_roles": {"gate": {"role": "gate", "voltage": 2, "current": 1}}},
            "driver_mode": {"enabled": False, "power_roles": {}},
        }
        mapping = {
            "schema_version": "1.0", "template": False,
            "instruments": {
                "signal_generator": {"model": "SG", "visa_address": "sim::sg"},
                "spectrum_analyzer": {"model": "SA", "visa_address": "sim::sa"},
                "power_supply": {"model": "PS", "visa_address": "sim::ps"},
            },
            "dut_power_channels": [{"channel": "A", "role": "gate", "connection": "gate"}],
            "driver_mode": {"enabled": False, "power_channels": []},
            "wiring": {"confirmed": True, "connection_note": "offline simulation"},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan_path = root / "plan.json"
            mapping_path = root / "mapping.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
            loaded = ConfigurationRepository().load_for_run(plan_path, mapping_path)
            self.assertTrue(loaded.valid)
            with patch("infrastructure.persistence.json_result_repository.TEST_RESULTS_DIR", root / "results"):
                prepared = prepare_run(loaded, run_id="offline-run", software_version="test")
            self.assertEqual(prepared.context.run_id, "offline-run")
            self.assertEqual(prepared.context.power_channel_mapping["gate"], "gate")
            self.assertTrue((prepared.run_directory / "run_mapping_snapshot.json").exists())

    def test_dut_protection_publishes_stop_and_does_not_raise_power(self):
        events = _Events()
        instrument = _Instrument()
        config = {
            "compression_point": {"type": "3dB"}, "attenuator": {"type": "0dB"},
            "dut_config": {"max_input_power": -2},
            "signal_source": {"start_power": -3, "stop_power": 1, "step": 1},
            "power_supply_assignment": {"dut_amplifier": {"supplies": {"main": {"name": "PS", "channel": ["A"]}}}},
        }
        service = AmplifierMeasurementService(
            config, instrument, {"cable_losses": {"1.0": {"cable1": 0, "cable2": 0, "cable4": 0}}},
            run_id="run-amplifier",
            safety_cleanup=safety_cleanup_for(instrument),
            event_sink=events, sleep_fn=lambda _: None,
        )
        service.perform_power_sweep(1.0)
        self.assertTrue(any(isinstance(event, StoppedEvent) for event in events.items))
        self.assertNotIn(("set_power", -1), instrument.operations)

    def test_versioned_model_and_reports_are_round_trippable(self):
        result = AmplifierMeasurementResult(
            run_id="report-run",
            frequencies=(
                AmplifierFrequencyResult(
                    frequency_ghz="1.0",
                    sweep_data={
                        "input_power_dut": [1.0],
                        "output_power_dut": [20.0],
                        "gain": [19.0],
                    },
                ),
            ),
            configuration_snapshot={"frequencies": [1.0]},
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_path = save_measurement_model(result, run_directory=root)
            loaded = measurement_result_from_document(load_json_result(model_path))
            self.assertEqual(loaded.run_id, "report-run")
            document = load_json_result(model_path)
            self.assertNotIn("legacy_payload", document)
            csv_path = export_csv(loaded, root / "report.csv")
            html_path = render_html_report(loaded, root / "report.html")
            self.assertTrue(csv_path.exists())
            self.assertIn("report-run", html_path.read_text(encoding="utf-8"))

    def test_release_manifest_contains_release_and_rollback_boundaries(self):
        manifest = build_manifest(Path(__file__).resolve().parents[1])
        self.assertIn("launcher.py", manifest["release_files"])
        self.assertIn("domain/measurement", manifest["release_files"])
        self.assertIn("application", manifest["release_files"])
        self.assertIn("measurement_calculations.py", manifest["compatibility_files"])
        self.assertIn("measurement_services.py", manifest["compatibility_files"])
        self.assertIn("measurement_lifecycle.py", manifest["compatibility_files"])
        for removed in (
            "enhanced_main_gui.py",
            "instrument_control.py",
            "data_visualization.py",
            "connection_diagrams.py",
            "legacy_config_conversion.py",
            "llm.py",
            "web_search_tools.py",
        ):
            self.assertNotIn(removed, manifest["compatibility_files"])
            self.assertNotIn(removed, manifest["rollback_files"])
        self.assertNotIn("amplifier_measurement.py", manifest["rollback_files"])
        self.assertEqual(manifest["rollback_files"], [])
        self.assertNotIn("enhanced_workers.py", manifest["rollback_files"])
        self.assertEqual(manifest["real_device_acceptance"], "pending")

    def test_release_manifest_constants_reference_existing_paths(self):
        import release_manifest

        project_root = Path(__file__).resolve().parents[1]
        for entry in (
            release_manifest.RELEASE_FILES
            + release_manifest.ROLLBACK_FILES
            + release_manifest.COMPATIBILITY_FILES
        ):
            self.assertTrue((project_root / entry).exists(), f"发布清单条目不存在: {entry}")


if __name__ == "__main__":
    unittest.main()
