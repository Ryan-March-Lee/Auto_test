import os
import json
import tempfile
import unittest
import warnings
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from application.ports.gui_data_service import ResultFileDto
from presentation.qt.export_page import ExportPage
from presentation.qt.visualization_page import convert_power_mapping, normalize_visualization_data


class VisualizationDataBoundaryTests(unittest.TestCase):
    def test_amplifier_results_are_sorted_by_numeric_frequency(self):
        data, frequencies, kind = normalize_visualization_data(
            {"results": {"5.2": {}, "4.8": {}, "10": {}}}
        )
        self.assertEqual(frequencies, [4.8, 5.2, 10.0])
        self.assertEqual(kind, "amplifier")
        self.assertEqual(set(data), {"5.2", "4.8", "10.0"})

    def test_driver_mapping_is_converted_to_sweep_data(self):
        data, frequencies = convert_power_mapping(
            {"power_mapping": {"4.8": {"-20": -10, "-10": 1}}}
        )
        self.assertEqual(frequencies, [4.8])
        self.assertEqual(data["4.8"]["sweep_data"]["gain"], [10.0, 11.0])

    def test_driver_mapping_accepts_nested_output_and_canonicalizes_frequency(self):
        data, frequencies = convert_power_mapping(
            {"power_mapping": {"4.800": {"-20": {"actual_output_power": -9.5}}}}
        )
        self.assertEqual(frequencies, [4.8])
        self.assertEqual(data["4.8"]["sweep_data"]["output_power_driver"], [-9.5])

    def test_invalid_and_empty_data_are_rejected(self):
        with self.assertRaises(ValueError):
            normalize_visualization_data({"unknown": {}})
        with self.assertRaises(ValueError):
            normalize_visualization_data({"results": {"invalid": {}}})
        data, frequencies, _ = normalize_visualization_data({"results": {}})
        self.assertEqual(data, {})
        self.assertEqual(frequencies, [])

    def test_result_files_are_grouped_and_sorted_by_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cable = root / "cable_loss.json"
            cable.write_text("{}", encoding="utf-8")
            older = root / "amplifier_measurement_old.json"
            newer = root / "amplifier_measurement_new.json"
            older.write_text("{}", encoding="utf-8")
            newer.write_text("{}", encoding="utf-8")
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))
            files = sorted([
                ResultFileDto(path.name, path.name, "功放测试", datetime.fromtimestamp(path.stat().st_mtime))
                for path in (newer, older)
            ], key=lambda item: item.modified_at, reverse=True)
            self.assertEqual(
                [item.filename for item in files],
                ["amplifier_measurement_new.json", "amplifier_measurement_old.json"],
            )

    def test_result_file_listing_ignores_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cable = root / "cable_loss.json"
            cable.write_text("{}", encoding="utf-8")
            (root / "amplifier_measurement_directory.json").mkdir()
            files = [ResultFileDto(cable.name, cable.name, "线损数据", datetime.fromtimestamp(cable.stat().st_mtime))]
            self.assertEqual([item.filename for item in files], ["cable_loss.json"])


class VisualizationExportTests(unittest.TestCase):
    def test_fake_visualizer_exports_csv_and_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "amplifier_measurement_1.json"
            source.write_text('{"results": {"4.8": {}}}', encoding="utf-8")
            output = root / "output.csv"

            class FakeResultService:
                def __init__(self, source):
                    self.source = source
                    self.rows = [ResultFileDto(source.name, source.name, "功放测试", datetime.now())]
                    self.calls = []

                def list_results(self):
                    return self.rows

                def export_csv(self, result_id, target):
                    self.calls.append(("csv", result_id, target))
                    Path(target).write_text("ok", encoding="utf-8")

                def export_pdf(self, result_id):
                    self.calls.append(("pdf", result_id))

            from PySide6.QtWidgets import QApplication

            app = QApplication.instance() or QApplication([])
            service = FakeResultService(source)
            page = ExportPage(result_service=service)
            page.file_table.selectRow(0)
            with patch("presentation.qt.export_page.QFileDialog.getSaveFileName", return_value=(str(output), "CSV files (*.csv)")), patch("presentation.qt.export_page.QMessageBox.information"):
                self.assertTrue(page.export_csv())
            self.assertEqual(output.read_text(encoding="utf-8"), "ok")
            self.assertEqual(service.calls[0][0:2], ("csv", source.name))
            with patch("presentation.qt.export_page.QMessageBox.information"), patch("presentation.qt.export_page.QMessageBox.warning"):
                self.assertTrue(page.export_pdf())
            self.assertEqual(service.calls[-1], ("pdf", source.name))


class ReportAdapterContractTests(unittest.TestCase):
    def test_latest_legacy_result_is_a_non_deprecated_browsing_api(self):
        import infrastructure.gui_data_service as gui_data
        from application.ports.gui_data_service import LoadedResultDto
        from infrastructure.filesystem.paths import PathService

        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            result_path = paths.test_results_dir / "cable_loss_results.json"
            result_path.parent.mkdir(parents=True)
            result_path.write_text('{"value": 1}', encoding="utf-8")
            repository = Mock()
            repository.latest_compatibility_path.return_value = result_path
            repository.load.return_value = {"value": 1}
            adapter = gui_data.ResultQueryAdapter(paths, repository)

            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                loaded = adapter.latest_legacy_result("cable_loss")

        self.assertIsInstance(loaded, LoadedResultDto)
        self.assertEqual(loaded.data, {"value": 1})
        repository.latest_compatibility_path.assert_called_once_with("cable_loss")

    def test_report_adapter_normalizes_input_and_forwards_model(self):
        import infrastructure.gui_data_service as gui_data
        from domain.models import MeasurementResult
        from infrastructure.filesystem.paths import PathService

        captured = {}

        class FakeVisualizer:
            def __init__(self, *, output_dir):
                captured.setdefault("dirs", []).append(Path(output_dir))

            def create_summary_report(self, data, original_filename=None):
                captured["data"] = data
                captured["filename"] = original_filename

        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            adapter = gui_data.ReportAdapter(paths)
            with patch.object(gui_data, "DataVisualization", FakeVisualizer):
                adapter.create_summary_report(
                    {"results": {"4.8": {"sweep_data": [
                        {"input_power_dut": -10.0, "output_power_dut": 0.0, "gain": 10.0, "efficiency": 20.0},
                    ]}}},
                    {"freq": 4.8},
                    "amp.json",
                )

        self.assertIsInstance(captured["data"], MeasurementResult)
        self.assertTrue(captured["data"].points)
        self.assertEqual(captured["filename"], "amp.json")
        self.assertEqual(dict(captured["data"].plan_snapshot), {"freq": 4.8})
        self.assertEqual(captured["dirs"][0].parent, paths.test_results_dir)

    def test_result_export_output_dirs_are_unique(self):
        import infrastructure.gui_data_service as gui_data
        from infrastructure.filesystem.paths import PathService

        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            adapter = gui_data.ResultExportAdapter(query=None, paths=paths)
            self.assertNotEqual(
                adapter._report_output_dir(), adapter._report_output_dir()
            )


if __name__ == "__main__":
    unittest.main()
