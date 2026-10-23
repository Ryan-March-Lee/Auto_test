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
from application.ports.result_history import ResultHistoryRepository
from infrastructure.filesystem.paths import PathService
from infrastructure.gui_data_service import ResultExportAdapter, ResultQueryAdapter
from infrastructure.persistence.result_history_repository import FileResultHistoryRepository
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


class HistoryBrowsingReadOnlyTests(unittest.TestCase):
    """步骤 D：历史查看/导出只读，且只依赖专用历史端口。"""

    def _populate(self, paths: PathService) -> Path:
        results = paths.test_results_dir
        results.mkdir(parents=True)
        (results / "cable_loss_results.json").write_text(
            '{"cable_losses": {"1.0": 0.5}}', encoding="utf-8"
        )
        (results / "driver_power_mapping_20261001_110000.json").write_text(
            '{"power_mapping": {"1.0": {}}}', encoding="utf-8"
        )
        (results / "driver_power_mapping_model.json").write_text("{}", encoding="utf-8")
        return results

    def test_latest_legacy_result_is_a_non_deprecated_browsing_api(self):
        from application.ports.gui_data_service import LoadedResultDto

        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            self._populate(paths)
            adapter = ResultQueryAdapter(paths, FileResultHistoryRepository(paths=paths))

            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                loaded = adapter.latest_legacy_result("cable_loss")

        self.assertIsInstance(loaded, LoadedResultDto)
        self.assertEqual(loaded.result_id, "cable_loss_results.json")
        self.assertEqual(loaded.data, {"cable_losses": {"1.0": 0.5}})

    def test_history_query_adapter_depends_only_on_the_read_only_history_port(self):
        from application.ports.result_history import HistoryResultEntry

        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            self._populate(paths)
            now = datetime.now()
            history = Mock(spec=ResultHistoryRepository)
            history.list_entries.return_value = (
                HistoryResultEntry("cable_loss_results.json", "cable_loss", now),
            )
            history.latest_result_id.return_value = "cable_loss_results.json"
            history.load.return_value = {"cable_losses": {"1.0": 0.5}}
            adapter = ResultQueryAdapter(paths, history)

            rows = adapter.list_results()
            loaded = adapter.latest_legacy_result("cable_loss")
            adapter.read_bytes("cable_loss_results.json")

        self.assertEqual([row.filename for row in rows], ["cable_loss_results.json"])
        self.assertEqual(rows[0].file_type, "线损数据")
        self.assertEqual(loaded.data, {"cable_losses": {"1.0": 0.5}})
        history.latest_result_id.assert_called_once_with("cable_loss")
        history.read_bytes.assert_called_once_with("cable_loss_results.json")
        # 只读端口没有正式测量输入/写入能力，历史浏览无法误用它们。
        for forbidden in ("latest_path", "legacy_path", "save", "save_compatibility_copy"):
            with self.assertRaises(AttributeError):
                getattr(history, forbidden)

    def test_history_repository_rejects_results_outside_the_history_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = PathService(root)
            results = self._populate(paths)
            history = FileResultHistoryRepository(paths=paths)
            outside = root / "runs" / "run-1" / "inputs"
            outside.mkdir(parents=True)
            (outside / "cable_loss.json").write_text("{}", encoding="utf-8")

            for result_id in (
                "../config.json",
                "runs/run-1/inputs/cable_loss.json",
                str(results / "cable_loss_results.json"),
                "cable_loss_results.txt",
                "",
            ):
                with self.assertRaises(ValueError):
                    history.load(result_id)

            with self.assertRaises(FileNotFoundError):
                history.load("missing.json")

    def test_history_listing_and_loading_leave_files_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            results = self._populate(paths)
            adapter = ResultQueryAdapter(paths, FileResultHistoryRepository(paths=paths))
            before = {
                path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in results.iterdir()
            }

            rows = adapter.list_results()
            adapter.load("cable_loss_results.json")
            adapter.latest_legacy_result("driver_power_mapping")

            after = {
                path.name: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in results.iterdir()
            }
            self.assertEqual(after, before)
            # canonical model 文件不属于全局历史结果，不会出现在列表里。
            self.assertEqual(
                sorted(row.filename for row in rows),
                [
                    "cable_loss_results.json",
                    "driver_power_mapping_20261001_110000.json",
                ],
            )

    def test_history_export_copies_source_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            results = self._populate(paths)
            source = results / "cable_loss_results.json"
            original = source.read_bytes()
            query = ResultQueryAdapter(paths, FileResultHistoryRepository(paths=paths))
            exports = ResultExportAdapter(query, paths)
            target = Path(directory) / "copy.json"

            exports.export_json("cable_loss_results.json", str(target))

            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(source.read_bytes(), original)

    def test_history_export_reports_missing_source_and_directory_target(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            results = self._populate(paths)
            query = ResultQueryAdapter(paths, FileResultHistoryRepository(paths=paths))
            exports = ResultExportAdapter(query, paths)

            with self.assertRaises(FileNotFoundError):
                exports.export_json("missing.json", str(Path(directory) / "copy.json"))
            with self.assertRaises(OSError):
                exports.export_json("cable_loss_results.json", str(results))

    def test_deprecated_latest_alias_still_delegates_with_a_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            self._populate(paths)
            adapter = ResultQueryAdapter(paths, FileResultHistoryRepository(paths=paths))

            with warnings.catch_warnings(record=True) as captured:
                warnings.simplefilter("always", DeprecationWarning)
                loaded = adapter.latest("cable_loss")

        self.assertEqual(loaded.data, {"cable_losses": {"1.0": 0.5}})
        self.assertTrue(
            any(issubclass(item.category, DeprecationWarning) for item in captured)
        )


class ReportAdapterContractTests(unittest.TestCase):

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
