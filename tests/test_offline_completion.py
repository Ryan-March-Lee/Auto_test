import tempfile
import unittest
from pathlib import Path

from app.events import StoppedEvent
from application.dto import AmplifierFrequencyResult, AmplifierMeasurementResult
from application.measurements.services import AmplifierMeasurementService
from application.dto import measurement_result_from_document
from infrastructure.persistence.json_result_repository import load_json_result
from infrastructure.persistence.json_result_repository import save_measurement_model
from analysis.reporting import export_csv, render_html_report
from release_manifest import build_manifest
from tests.contract_doubles import amplifier_inputs
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
        # 步骤 10：主窗口不再导入任何 worker；仪器连接 worker 归运行协调器。
        self.assertNotIn("presentation.qt.workers", imported_modules)

        coordinator_source = (
            project_root / "presentation" / "qt" / "application_coordinator.py"
        ).read_text(encoding="utf-8-sig")
        coordinator_imports = {
            node.module: {alias.name for alias in node.names}
            for node in ast.walk(ast.parse(coordinator_source))
            if isinstance(node, ast.ImportFrom)
        }
        self.assertEqual(coordinator_imports["workers"], {"InstrumentWorker"})

    def test_dut_protection_publishes_stop_and_does_not_raise_power(self):
        events = _Events()
        instrument = _Instrument()
        inputs = amplifier_inputs(
            start_power_dbm=-3.0,
            stop_power_dbm=1.0,
            step_db=1.0,
            compression_db=3.0,
            attenuator_db=0.0,
            max_input_power_dbm=-2.0,
            supplies=(("PS", "A"),),
        )
        service = AmplifierMeasurementService(
            inputs, instrument, {"cable_losses": {"1.0": {"cable1": 0, "cable2": 0, "cable4": 0}}},
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

    def test_release_manifest_contains_release_boundaries_only(self):
        """步骤 16：兼容/回滚文件清单已删除，清单只登记正式发布边界。"""
        manifest = build_manifest(Path(__file__).resolve().parents[1])
        self.assertIn("launcher.py", manifest["release_files"])
        self.assertIn("domain/measurement", manifest["release_files"])
        self.assertIn("application", manifest["release_files"])
        self.assertNotIn("compatibility_files", manifest)
        self.assertNotIn("rollback_files", manifest)
        for retired in (
            "measurement_calculations.py",
            "measurement_services.py",
            "measurement_lifecycle.py",
            "app_logging.py",
            "project_paths.py",
            "amplifier_measurement.py",
            "enhanced_main_gui.py",
        ):
            self.assertNotIn(retired, manifest["release_files"])
        self.assertEqual(manifest["real_device_acceptance"], "pending")
        self.assertEqual(
            manifest["evidence"]["rollback_decision"], "pending"
        )

    def test_release_manifest_constants_reference_existing_paths(self):
        import release_manifest

        project_root = Path(__file__).resolve().parents[1]
        for entry in release_manifest.RELEASE_FILES:
            self.assertTrue((project_root / entry).exists(), f"发布清单条目不存在: {entry}")
        self.assertFalse(hasattr(release_manifest, "ROLLBACK_FILES"))
        self.assertFalse(hasattr(release_manifest, "COMPATIBILITY_FILES"))


if __name__ == "__main__":
    unittest.main()
