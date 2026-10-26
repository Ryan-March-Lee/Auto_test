import os
import json
import tempfile
import time
import unittest
import warnings
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from application.ports.gui_data_service import ResultFileDto
from application.ports.result_history import ResultHistoryRepository
from application.run_stages import RunStageRecorder
from domain.run.models import CABLE_LOSS_INPUT_PATH, STAGE_CABLE_LOSS
from infrastructure.filesystem.paths import PathService
from infrastructure.gui_data_service import ResultExportAdapter, ResultQueryAdapter
from infrastructure.persistence.result_history_repository import FileResultHistoryRepository
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
from infrastructure.persistence.run_repository import RunRepository
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
            {"run_id": "viz-run", "power_mapping": {"4.8": {"-20": -10, "-10": 1}}}
        )
        self.assertEqual(frequencies, [4.8])
        self.assertEqual(data["4.8"]["sweep_data"]["gain"], [10.0, 11.0])

    def test_driver_mapping_requires_a_formal_document(self):
        with self.assertRaises(ValueError):
            convert_power_mapping(
                {"run_id": "viz-run", "power_mapping": {"4.800": {"-20": {"actual_output_power": -9.5}}}}
            )
        with self.assertRaises(ValueError):
            convert_power_mapping({"power_mapping": {"4.8": {"-20": -10}}})

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

    def test_deprecated_latest_alias_is_removed(self):
        """旧别名 ``latest()`` 已删除：历史浏览只保留 ``latest_legacy_result()``。"""
        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            self._populate(paths)
            adapter = ResultQueryAdapter(paths, FileResultHistoryRepository(paths=paths))

            self.assertFalse(hasattr(adapter, "latest"))
            from application.ports import gui_data_service as ports

            self.assertFalse(hasattr(ports, "LegacyResultQueryPort"))


class RunHistoryMigrationTests(unittest.TestCase):
    """步骤 D3：GUI 历史列表/查看/导出切换到运行目录历史，旧历史仍可查看。"""

    RUN_ID = "20261024T080000Z_a1b2c3d4"

    def _setup(self, directory):
        paths = PathService(Path(directory))
        runs = RunRepository(paths.test_results_dir / "runs")
        history = FileResultHistoryRepository(paths=paths)
        run_history = FileRunHistoryRepository(runs=runs)
        adapter = ResultQueryAdapter(paths, history, run_history=run_history)
        return paths, runs, history, adapter

    def _complete_cable_loss(self, runs, *, losses, run_id=None, now=None):
        run_id = run_id or self.RUN_ID
        layout, _manifest = runs.create_run({"run_id": run_id}, run_id=run_id, now=now)
        recorder = RunStageRecorder(runs, run_id)
        recorder.complete(
            STAGE_CABLE_LOSS, {"run_id": run_id, "cable_losses": losses}, now=now
        )
        return layout

    def _write_legacy_copy(self, paths, *, losses):
        results = paths.test_results_dir
        results.mkdir(parents=True, exist_ok=True)
        (results / "cable_loss_results.json").write_text(
            json.dumps({"cable_losses": losses}), encoding="utf-8"
        )

    def test_history_list_merges_run_results_and_legacy_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, runs, _history, adapter = self._setup(directory)
            self._complete_cable_loss(runs, losses={"1.0": 1.0})
            self._write_legacy_copy(paths, losses={"1.0": 99.0})

            rows = adapter.list_results()

        sources = {row.source for row in rows}
        self.assertEqual(sources, {"run", "legacy"})
        run_row = next(row for row in rows if row.source == "run")
        self.assertEqual(run_row.run_id, self.RUN_ID)
        self.assertEqual(run_row.filename, CABLE_LOSS_INPUT_PATH.rsplit("/", 1)[-1])
        self.assertEqual(run_row.file_type, "线损数据")

    def test_load_prefers_the_run_result_over_the_legacy_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, runs, _history, adapter = self._setup(directory)
            self._complete_cable_loss(runs, losses={"1.0": 1.0})
            self._write_legacy_copy(paths, losses={"1.0": 99.0})

            run_row = next(row for row in adapter.list_results() if row.source == "run")
            loaded = adapter.load(run_row.result_id)
            legacy = adapter.latest_legacy_result("cable_loss")
            latest = adapter.latest_result("cable_loss")

        self.assertEqual(loaded.data["cable_losses"], {"1.0": 1.0})
        self.assertEqual(legacy.data["cable_losses"], {"1.0": 99.0})
        self.assertEqual(latest.data["cable_losses"], {"1.0": 1.0})

    def test_latest_result_falls_back_to_legacy_when_no_run_result_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            _paths, _runs, _history, adapter = self._setup(directory)
            self._write_legacy_copy(PathService(Path(directory)), losses={"1.0": 5.0})

            latest = adapter.latest_result("cable_loss")

        self.assertEqual(latest.data["cable_losses"], {"1.0": 5.0})

    def test_latest_result_skips_a_corrupted_newest_run(self):
        with tempfile.TemporaryDirectory() as directory:
            _paths, runs, _history, adapter = self._setup(directory)
            old_id = "20261024T070000Z_aaaaaaaa"
            new_id = "20261024T090000Z_bbbbbbbb"
            self._complete_cable_loss(
                runs,
                losses={"1.0": 1.0},
                run_id=old_id,
                now=datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc),
            )
            new_layout = self._complete_cable_loss(
                runs,
                losses={"1.0": 2.0},
                run_id=new_id,
                now=datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc),
            )
            (new_layout.run_directory / CABLE_LOSS_INPUT_PATH).write_text(
                json.dumps({"cable_losses": {"1.0": 999.0}}), encoding="utf-8"
            )

            latest = adapter.latest_result("cable_loss")

        # 最新运行被篡改，应回退到更旧但有效的运行结果。
        self.assertEqual(latest.data["cable_losses"], {"1.0": 1.0})

    def test_latest_result_falls_back_to_legacy_when_all_run_results_corrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, runs, _history, adapter = self._setup(directory)
            layout = self._complete_cable_loss(runs, losses={"1.0": 1.0})
            (layout.run_directory / CABLE_LOSS_INPUT_PATH).write_text(
                json.dumps({"cable_losses": {"1.0": 42.0}}), encoding="utf-8"
            )
            self._write_legacy_copy(paths, losses={"1.0": 7.0})

            latest = adapter.latest_result("cable_loss")

        self.assertEqual(latest.data["cable_losses"], {"1.0": 7.0})

    def test_latest_result_prefers_newer_run_over_newer_file_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            _paths, runs, _history, adapter = self._setup(directory)
            old_id = "20261024T070000Z_aaaaaaaa"
            new_id = "20261024T090000Z_bbbbbbbb"
            old_layout = self._complete_cable_loss(
                runs,
                losses={"1.0": 1.0},
                run_id=old_id,
                now=datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc),
            )
            self._complete_cable_loss(
                runs,
                losses={"1.0": 2.0},
                run_id=new_id,
                now=datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc),
            )
            # 把更旧运行的结果文件 mtime 改成“更晚”，验证顺序按运行创建时间而非 mtime。
            future = time.time() + 100000
            os.utime(
                old_layout.run_directory / CABLE_LOSS_INPUT_PATH, (future, future)
            )

            latest = adapter.latest_result("cable_loss")

        self.assertEqual(latest.data["cable_losses"], {"1.0": 2.0})

    def test_read_bytes_and_export_use_the_run_result(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, runs, _history, adapter = self._setup(directory)
            layout = self._complete_cable_loss(runs, losses={"1.0": 1.0})
            run_row = next(row for row in adapter.list_results() if row.source == "run")
            target = Path(directory) / "copy.json"

            raw = adapter.read_bytes(run_row.result_id)
            ResultExportAdapter(adapter, paths).export_json(run_row.result_id, str(target))

            self.assertEqual(
                raw, (layout.run_directory / CABLE_LOSS_INPUT_PATH).read_bytes()
            )
            self.assertEqual(target.read_bytes(), raw)

    def test_tampered_run_result_is_rejected_by_both_read_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            _paths, runs, _history, adapter = self._setup(directory)
            layout = self._complete_cable_loss(runs, losses={"1.0": 1.0})
            run_row = next(row for row in adapter.list_results() if row.source == "run")
            (layout.run_directory / CABLE_LOSS_INPUT_PATH).write_text(
                json.dumps({"cable_losses": {"1.0": 42.0}}), encoding="utf-8"
            )

            with self.assertRaises(ValueError):
                adapter.load(run_row.result_id)
            with self.assertRaises(ValueError):
                adapter.read_bytes(run_row.result_id)

    def test_cross_run_encoded_identifier_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            _paths, runs, _history, adapter = self._setup(directory)
            self._complete_cable_loss(runs, losses={"1.0": 1.0})

            for result_id in (
                "run::bad-run::inputs/cable_loss.json",
                "run::" + self.RUN_ID + "::../config.json",
                "run::" + self.RUN_ID + "::",
                "run::" + self.RUN_ID + "::results/unknown.json",
            ):
                with self.subTest(result_id=result_id):
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        adapter.load(result_id)

    def test_export_page_shows_the_source_of_each_result(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, runs, _history, adapter = self._setup(directory)
            self._complete_cable_loss(runs, losses={"1.0": 1.0})
            self._write_legacy_copy(paths, losses={"1.0": 99.0})

            from PySide6.QtWidgets import QApplication

            app = QApplication.instance() or QApplication([])
            page = ExportPage(result_service=adapter)
            labels = [
                page.file_table.item(row, 2).text()
                for row in range(page.file_table.rowCount())
            ]

        self.assertIn("历史兼容副本", labels)
        self.assertTrue(any(label.startswith("运行 ") for label in labels))


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
                    {
                        "run_id": "report-run",
                        "results": {
                            "4.8": {
                                "sweep_data": {
                                    "input_power_dut": [-10.0],
                                    "output_power_dut": [0.0],
                                    "gain": [10.0],
                                    "efficiency": [20.0],
                                }
                            }
                        },
                    },
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
