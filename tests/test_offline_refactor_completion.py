import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.events import StoppedEvent
from app.run_context import prepare_run
from config_models import RunConfiguration
from domain.models import AmplifierMeasurementResult, AmplifierScanPoint
from measurement_services import AmplifierMeasurementService
from persistence.config_repository import ConfigurationRepository
from result_reading import load_result_model
from result_storage import save_measurement_model
from analysis.reporting import export_csv, render_html_report
from release_manifest import build_manifest


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


class OfflineRefactorCompletionTests(unittest.TestCase):
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
            with patch("result_storage.TEST_RESULTS_DIR", root / "results"):
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
            event_sink=events, sleep_fn=lambda _: None,
        )
        service.perform_power_sweep(1.0)
        self.assertTrue(any(isinstance(event, StoppedEvent) for event in events.items))
        self.assertNotIn(("set_power", -1), instrument.operations)

    def test_versioned_model_and_reports_are_round_trippable(self):
        result = AmplifierMeasurementResult(
            run_id="report-run",
            points=(AmplifierScanPoint(1.0, 1.0, 20.0, 19.0),),
            plan_snapshot={"frequencies": [1.0]},
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model_path = save_measurement_model(result, legacy_payload={"results": {}}, run_directory=root)
            loaded = load_result_model(model_path)
            self.assertEqual(loaded.run_id, "report-run")
            csv_path = export_csv(loaded, root / "report.csv")
            html_path = render_html_report(loaded, root / "report.html")
            self.assertTrue(csv_path.exists())
            self.assertIn("report-run", html_path.read_text(encoding="utf-8"))

    def test_release_manifest_contains_release_and_rollback_boundaries(self):
        manifest = build_manifest(Path(__file__).resolve().parents[1])
        self.assertIn("launcher.py", manifest["release_files"])
        self.assertIn("enhanced_workers.py", manifest["rollback_files"])
        self.assertEqual(manifest["real_device_acceptance"], "pending")


if __name__ == "__main__":
    unittest.main()
