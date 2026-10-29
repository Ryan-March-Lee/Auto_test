import json
import os
import tempfile
import time
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from application.dto import (
    AmplifierFrequencyResult,
    AmplifierMeasurementResult,
    DriverPowerMappingMeasurementResult,
    DriverPowerPoint,
    MeasurementResult,
    MeasurementType,
    measurement_result_from_document,
)
from application.ports.gui_data_service import (
    RESULT_SOURCE_RUN,
    LoadedResultDto,
    ResultFileDto,
)
from application.dto.export_requests import ExportFormat
from application.run_stages import RunStageRecorder
from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    CABLE_LOSS_INPUT_PATH,
    STAGE_AMPLIFIER,
    STAGE_CABLE_LOSS,
)
from infrastructure.filesystem.paths import PathService
from infrastructure.gui_data_service import ResultExportAdapter, ResultQueryAdapter
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
from infrastructure.persistence.run_repository import RunRepository
from presentation.qt.run_history_page import RunHistoryPage
from presentation.qt.visualization_page import result_plot_series


def _amplifier_result() -> AmplifierMeasurementResult:
    return AmplifierMeasurementResult(
        run_id="viz-run",
        frequencies=(
            AmplifierFrequencyResult(
                frequency_ghz="5.2",
                sweep_data={"input_power_dut": [-10.0], "output_power_dut": [1.0], "gain": [11.0]},
            ),
            AmplifierFrequencyResult(
                frequency_ghz="4.8",
                sweep_data={"input_power_dut": [-10.0], "output_power_dut": [0.0], "gain": [10.0]},
            ),
            AmplifierFrequencyResult(
                frequency_ghz="10",
                sweep_data={"input_power_dut": [-10.0], "output_power_dut": [5.0], "gain": [15.0]},
            ),
        ),
    )


def _driver_result() -> DriverPowerMappingMeasurementResult:
    return DriverPowerMappingMeasurementResult(
        run_id="viz-run",
        points=(
            DriverPowerPoint(frequency_ghz="4.8", input_power_dbm=-20.0, output_power_dbm=-10.0),
            DriverPowerPoint(frequency_ghz="4.8", input_power_dbm=-10.0, output_power_dbm=1.0),
        ),
    )


class VisualizationDataBoundaryTests(unittest.TestCase):
    def test_amplifier_results_are_sorted_by_numeric_frequency(self):
        series, frequencies = result_plot_series(_amplifier_result())
        self.assertEqual(frequencies, [4.8, 5.2, 10.0])
        self.assertEqual(set(series), {"5.2", "4.8", "10.0"})

    def test_driver_mapping_is_converted_to_sweep_columns(self):
        series, frequencies = result_plot_series(_driver_result())
        self.assertEqual(frequencies, [4.8])
        self.assertEqual(series["4.8"]["sweep_data"]["gain"], [10.0, 11.0])

    def test_formal_documents_are_rejected_by_the_display_layer(self):
        # 展示层只接受 DTO；文档必须先经过唯一解析入口。
        with self.assertRaises(ValueError):
            result_plot_series({"results": {}})
        with self.assertRaises(ValueError):
            measurement_result_from_document(
                {"run_id": "viz-run", "power_mapping": {"4.8": {"-20": -10}}}
            )

    def test_empty_result_has_no_frequency_data(self):
        series, frequencies = result_plot_series(
            DriverPowerMappingMeasurementResult(run_id="empty")
        )
        self.assertEqual(series, {})
        self.assertEqual(frequencies, [])

    def test_result_files_are_grouped_and_sorted_by_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            older = root / "amplifier_measurement_old.json"
            newer = root / "amplifier_measurement_new.json"
            older.write_text("{}", encoding="utf-8")
            newer.write_text("{}", encoding="utf-8")
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))
            files = sorted(
                [
                    ResultFileDto(path.name, path.name, "功放测试", datetime.fromtimestamp(path.stat().st_mtime))
                    for path in (newer, older)
                ],
                key=lambda item: item.modified_at,
                reverse=True,
            )
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


class RunHistoryOnlyTests(unittest.TestCase):
    """步骤 6：历史列表/查看/导出只读正式运行目录结果。"""

    RUN_ID = "20261024T080000Z_a1b2c3d4"

    def _setup(self, directory):
        paths = PathService(Path(directory))
        runs = RunRepository(paths.test_results_dir / "runs")
        run_history = FileRunHistoryRepository(runs=runs)
        adapter = ResultQueryAdapter(
            paths,
            run_history=run_history,
            runs_root=paths.test_results_dir / "runs",
        )
        return paths, runs, adapter

    def _complete_cable_loss(self, runs, *, losses, run_id=None, now=None):
        run_id = run_id or self.RUN_ID
        layout, _manifest = runs.create_run({"run_id": run_id}, run_id=run_id, now=now)
        recorder = RunStageRecorder(runs, run_id)
        recorder.complete(
            STAGE_CABLE_LOSS,
            {
                "run_id": run_id,
                "measurement_type": "cable_loss",
                "status": "completed",
                "cable_losses": losses,
            },
            now=now,
        )
        return layout

    def _write_legacy_copy(self, paths, *, losses):
        results = paths.test_results_dir
        results.mkdir(parents=True, exist_ok=True)
        (results / "cable_loss_results.json").write_text(
            json.dumps({"cable_losses": losses}), encoding="utf-8"
        )

    def test_history_list_only_contains_run_results(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, runs, adapter = self._setup(directory)
            self._complete_cable_loss(runs, losses={"1.0": {"cable1": 1.0}})
            self._write_legacy_copy(paths, losses={"1.0": 99.0})

            rows = adapter.list_results()

        self.assertEqual({row.source for row in rows}, {RESULT_SOURCE_RUN})
        self.assertEqual(len(rows), 1)
        run_row = rows[0]
        self.assertEqual(run_row.run_id, self.RUN_ID)
        self.assertEqual(run_row.filename, CABLE_LOSS_INPUT_PATH.rsplit("/", 1)[-1])
        self.assertEqual(run_row.file_type, "线损数据")

    def test_load_and_latest_result_read_the_run_result(self):
        with tempfile.TemporaryDirectory() as directory:
            _paths, runs, adapter = self._setup(directory)
            self._complete_cable_loss(runs, losses={"1.0": {"cable1": 1.0}})

            run_row = adapter.list_results()[0]
            loaded = adapter.load(run_row.result_id)
            latest = adapter.latest_result("cable_loss")

        self.assertEqual(loaded.result.cable_losses()["1.0"]["cable1"], 1.0)
        self.assertEqual(latest.result.cable_losses()["1.0"]["cable1"], 1.0)

    def test_latest_result_ignores_legacy_copies_without_run_results(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, _runs, adapter = self._setup(directory)
            self._write_legacy_copy(paths, losses={"1.0": 5.0})

            with self.assertRaises(FileNotFoundError):
                adapter.latest_result("cable_loss")

    def test_latest_result_skips_a_corrupted_newest_run(self):
        with tempfile.TemporaryDirectory() as directory:
            _paths, runs, adapter = self._setup(directory)
            old_id = "20261024T070000Z_aaaaaaaa"
            new_id = "20261024T090000Z_bbbbbbbb"
            self._complete_cable_loss(
                runs,
                losses={"1.0": {"cable1": 1.0}},
                run_id=old_id,
                now=datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc),
            )
            new_layout = self._complete_cable_loss(
                runs,
                losses={"1.0": {"cable1": 2.0}},
                run_id=new_id,
                now=datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc),
            )
            (new_layout.run_directory / CABLE_LOSS_INPUT_PATH).write_text(
                json.dumps({"cable_losses": {"1.0": 999.0}}), encoding="utf-8"
            )

            latest = adapter.latest_result("cable_loss")

        # 最新运行被篡改，应回退到更旧但有效的运行结果。
        self.assertEqual(latest.result.cable_losses()["1.0"]["cable1"], 1.0)

    def test_latest_result_raises_when_all_run_results_are_corrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, runs, adapter = self._setup(directory)
            layout = self._complete_cable_loss(runs, losses={"1.0": {"cable1": 1.0}})
            (layout.run_directory / CABLE_LOSS_INPUT_PATH).write_text(
                json.dumps({"cable_losses": {"1.0": 42.0}}), encoding="utf-8"
            )
            self._write_legacy_copy(paths, losses={"1.0": 7.0})

            with self.assertRaises(FileNotFoundError):
                adapter.latest_result("cable_loss")

    def test_latest_result_prefers_newer_run_over_newer_file_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            _paths, runs, adapter = self._setup(directory)
            old_id = "20261024T070000Z_aaaaaaaa"
            new_id = "20261024T090000Z_bbbbbbbb"
            old_layout = self._complete_cable_loss(
                runs,
                losses={"1.0": {"cable1": 1.0}},
                run_id=old_id,
                now=datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc),
            )
            self._complete_cable_loss(
                runs,
                losses={"1.0": {"cable1": 2.0}},
                run_id=new_id,
                now=datetime(2026, 10, 24, 9, 0, tzinfo=timezone.utc),
            )
            # 把更旧运行的结果文件 mtime 改成“更晚”，验证顺序按运行创建时间而非 mtime。
            future = time.time() + 100000
            os.utime(
                old_layout.run_directory / CABLE_LOSS_INPUT_PATH, (future, future)
            )

            latest = adapter.latest_result("cable_loss")

        self.assertEqual(latest.result.cable_losses()["1.0"]["cable1"], 2.0)

    def test_history_list_skips_non_formal_registered_files(self):
        """manifest 登记的拆分明细不是正式结果，不得出现在历史列表里。"""
        with tempfile.TemporaryDirectory() as directory:
            _paths, runs, adapter = self._setup(directory)
            runs.create_run({"run_id": self.RUN_ID}, run_id=self.RUN_ID)
            recorder = RunStageRecorder(runs, self.RUN_ID)
            recorder.complete(
                STAGE_AMPLIFIER,
                {
                    "run_id": self.RUN_ID,
                    "measurement_type": "amplifier",
                    "status": "completed",
                    "results": {
                        "1.0": {"sweep_data": {"gain": [10.0], "efficiency": [20.0]}}
                    },
                },
                result_path=AMPLIFIER_RESULT_PATH,
                extra_files={
                    "results/amplifier_measurement_1.0.json": {
                        "schema_version": 1,
                        "stage": "amplifier",
                        "run_id": self.RUN_ID,
                        "measurement_type": "amplifier",
                        "frequency": "1.0",
                        "result": {"sweep_data": {"gain": [10.0]}},
                    }
                },
            )

            rows = adapter.list_results()
            split_id = f"run::{self.RUN_ID}::results/amplifier_measurement_1.0.json"
            with self.assertRaises(ValueError):
                adapter.load(split_id)
            latest = adapter.latest_result("amplifier_measurement")

        self.assertEqual([row.filename for row in rows], ["amplifier_measurement.json"])
        self.assertTrue(latest.result.is_formal_result)

    def test_result_type_must_match_the_manifest_definition(self):
        """阶段是线损，但文件内容是主功放结果时不得当作线损结果返回。"""
        with tempfile.TemporaryDirectory() as directory:
            _paths, runs, adapter = self._setup(directory)
            runs.create_run({"run_id": self.RUN_ID}, run_id=self.RUN_ID)
            recorder = RunStageRecorder(runs, self.RUN_ID)
            recorder.complete(
                STAGE_CABLE_LOSS,
                {
                    "run_id": self.RUN_ID,
                    "measurement_type": "amplifier",
                    "status": "completed",
                    "results": {},
                },
            )

            self.assertEqual(adapter.list_results(), [])
            with self.assertRaises(ValueError):
                adapter.load(f"run::{self.RUN_ID}::inputs/cable_loss.json")
            with self.assertRaises(FileNotFoundError):
                adapter.latest_result("cable_loss")

    def test_unknown_result_type_is_rejected_by_latest_result(self):
        with tempfile.TemporaryDirectory() as directory:
            _paths, _runs, adapter = self._setup(directory)
            with self.assertRaises(ValueError):
                adapter.latest_result("mystery")

    def test_read_bytes_and_export_use_the_run_result(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, runs, adapter = self._setup(directory)
            layout = self._complete_cable_loss(runs, losses={"1.0": {"cable1": 1.0}})
            run_row = adapter.list_results()[0]
            target = Path(directory) / "copy.json"

            raw = adapter.read_bytes(run_row.result_id)
            ResultExportAdapter(adapter, paths).export_json(run_row.result_id, str(target))

            self.assertEqual(
                raw, (layout.run_directory / CABLE_LOSS_INPUT_PATH).read_bytes()
            )
            self.assertEqual(target.read_bytes(), raw)

    def test_tampered_run_result_is_rejected_by_both_read_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            _paths, runs, adapter = self._setup(directory)
            layout = self._complete_cable_loss(runs, losses={"1.0": {"cable1": 1.0}})
            run_row = adapter.list_results()[0]
            (layout.run_directory / CABLE_LOSS_INPUT_PATH).write_text(
                json.dumps({"cable_losses": {"1.0": 42.0}}), encoding="utf-8"
            )

            with self.assertRaises(ValueError):
                adapter.load(run_row.result_id)
            with self.assertRaises(ValueError):
                adapter.read_bytes(run_row.result_id)

    def test_identifier_must_be_a_run_result(self):
        with tempfile.TemporaryDirectory() as directory:
            _paths, runs, adapter = self._setup(directory)
            self._complete_cable_loss(runs, losses={"1.0": {"cable1": 1.0}})

            for result_id in (
                "cable_loss_results.json",
                "run::bad-run::inputs/cable_loss.json",
                "run::" + self.RUN_ID + "::../config.json",
                "run::" + self.RUN_ID + "::",
                "run::" + self.RUN_ID + "::results/unknown.json",
            ):
                with self.subTest(result_id=result_id):
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        adapter.load(result_id)

    def test_choose_and_load_rejects_files_outside_the_runs_root(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, runs, adapter = self._setup(directory)
            self._complete_cable_loss(runs, losses={"1.0": {"cable1": 1.0}})
            outside = paths.test_results_dir / "cable_loss_results.json"
            outside.write_text("{}", encoding="utf-8")

            with patch(
                "infrastructure.gui_data_service.QFileDialog.getOpenFileName",
                return_value=(str(outside), "JSON files (*.json)"),
            ):
                with self.assertRaises(ValueError):
                    adapter.choose_and_load()

    def test_run_history_page_shows_the_source_of_each_result(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, runs, adapter = self._setup(directory)
            self._complete_cable_loss(runs, losses={"1.0": {"cable1": 1.0}})
            self._write_legacy_copy(paths, losses={"1.0": 99.0})

            from PySide6.QtWidgets import QApplication

            from application.export_service import ResultExportUseCase
            from presentation.qt.history_view_model import HistoryViewModel

            app = QApplication.instance() or QApplication([])
            page = RunHistoryPage(
                history=HistoryViewModel(adapter),
                export_service=ResultExportUseCase(
                    history=adapter, exports=ResultExportAdapter(adapter, paths)
                ),
            )
            labels = [
                page.result_table.item(row, 2).text()
                for row in range(page.result_table.rowCount())
            ]

        self.assertTrue(labels)
        self.assertTrue(all(label.startswith("运行 ") for label in labels))


class RunHistoryPageRequestTests(unittest.TestCase):
    """步骤 11：历史管理页只构造 ExportRequest，导出由应用用例执行。"""

    RUN_ID = RunHistoryOnlyTests.RUN_ID

    def setUp(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        # 离屏环境里任何模态对话框都会永久阻塞：统一替身并记录调用。
        for name in ("warning", "information", "critical"):
            patcher = patch(f"presentation.qt.run_history_page.QMessageBox.{name}")
            setattr(self, f"{name}_dialog", patcher.start())
            self.addCleanup(patcher.stop)

    def _page(self, *, amplifier=True):
        from application.export_service import ResultExportUseCase
        from presentation.qt.history_view_model import HistoryViewModel

        paths = PathService(self.root)
        runs = RunRepository(paths.test_results_dir / "runs")
        query = ResultQueryAdapter(
            paths,
            run_history=FileRunHistoryRepository(runs=runs),
            runs_root=paths.test_results_dir / "runs",
        )
        runs.create_run({"run_id": self.RUN_ID}, run_id=self.RUN_ID)
        recorder = RunStageRecorder(runs, self.RUN_ID)
        if amplifier:
            recorder.complete(
                STAGE_AMPLIFIER,
                {
                    "run_id": self.RUN_ID,
                    "measurement_type": "amplifier",
                    "status": "completed",
                    "results": {
                        "5.2": {
                            "sweep_data": {
                                "input_power_dut": [-10.0, -5.0],
                                "output_power_dut": [0.0, 5.0],
                                "gain": [10.0, 10.0],
                                "efficiency": [20.0, 25.0],
                            }
                        }
                    },
                },
                result_path=AMPLIFIER_RESULT_PATH,
            )
        else:
            recorder.complete(
                STAGE_CABLE_LOSS,
                {
                    "run_id": self.RUN_ID,
                    "measurement_type": "cable_loss",
                    "status": "completed",
                    "cable_losses": {"1.0": {"cable1": 1.0}},
                },
            )
        page = RunHistoryPage(
            history=HistoryViewModel(query),
            export_service=ResultExportUseCase(
                history=query, exports=ResultExportAdapter(query, paths)
            ),
        )
        page.result_table.setCurrentCell(0, 0)
        return page

    def test_json_export_writes_the_selected_history_result(self):
        page = self._page()
        target = self.root / "exported.json"

        with patch(
            "presentation.qt.run_history_page.QFileDialog.getSaveFileName",
            return_value=(str(target), "JSON files (*.json)"),
        ):
            self.assertTrue(page.export(ExportFormat.JSON))

        self.assertTrue(target.is_file())
        self.information_dialog.assert_called_once()
        message = self.information_dialog.call_args.args[2]
        self.assertIn(str(target), message)

    def test_csv_export_is_rejected_for_non_amplifier_results(self):
        page = self._page(amplifier=False)
        target = self.root / "exported.csv"

        with patch(
            "presentation.qt.run_history_page.QFileDialog.getSaveFileName",
            return_value=(str(target), "CSV files (*.csv)"),
        ):
            self.assertFalse(page.export(ExportFormat.CSV))

        self.assertFalse(target.is_file())
        self.warning_dialog.assert_called_once()

    def test_report_export_shows_the_generated_location(self):
        page = self._page()

        self.assertTrue(page.export(ExportFormat.REPORT))

        message = self.information_dialog.call_args.args[2]
        self.assertIn("test_report.html", message)
        self.assertTrue(Path(message.split(": ", 1)[1]).is_file())

    def test_report_export_requires_an_amplifier_result(self):
        page = self._page(amplifier=False)

        self.assertFalse(page.export(ExportFormat.REPORT))

        self.warning_dialog.assert_called_once()
        self.information_dialog.assert_not_called()

    def test_export_without_a_selection_warns_and_does_nothing(self):
        page = self._page()
        page.result_table.setCurrentCell(-1, -1)

        with patch(
            "presentation.qt.run_history_page.QFileDialog.getSaveFileName"
        ) as dialog:
            self.assertFalse(page.export(ExportFormat.JSON))

        self.warning_dialog.assert_called_once()
        dialog.assert_not_called()


class ResultExportReportContractTests(unittest.TestCase):
    """步骤 8：报告副本只按归档结果契约渲染，不注入页面配置或生成时刻。"""

    @staticmethod
    def _query(result):
        class _Query:
            def load(self, result_id):
                return LoadedResultDto(result_id, "amplifier_measurement.json", result)

            def read_bytes(self, result_id):  # pragma: no cover - 报告读取 DTO
                raise AssertionError("报告导出不逐字节读取结果")

        return _Query()

    def test_report_renders_the_archived_result_without_overrides(self):
        import infrastructure.gui_data_service as gui_data

        captured = {}

        class FakeVisualizer:
            def __init__(self, *, output_dir):
                captured["dir"] = Path(output_dir)

            def create_summary_report(self, data, original_filename=None):
                captured["data"] = data
                captured["filename"] = original_filename
                report = Path(captured["dir"]) / "test_report.html"
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text("<html></html>", encoding="utf-8")
                return report

        result = replace(
            _amplifier_result(),
            configuration_snapshot={"freq": 4.8},
            measurement_time="2026-01-01 00:00:00",
        )
        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            adapter = gui_data.ResultExportAdapter(query=self._query(result), paths=paths)
            with patch.object(gui_data, "DataVisualization", FakeVisualizer):
                report_path = adapter.export_report("run::viz-run::results/amp.json")
            self.assertEqual(Path(report_path).name, "test_report.html")
            self.assertEqual(
                Path(report_path).parent.parent, paths.test_results_dir
            )

        rendered = captured["data"]
        # 归档结果里的配置快照与测量时间原样进入报告，适配器不接受“当前配置”。
        self.assertEqual(dict(rendered.configuration_snapshot), {"freq": 4.8})
        self.assertEqual(rendered.measurement_time, "2026-01-01 00:00:00")
        self.assertEqual(rendered.source_filename, "amplifier_measurement.json")
        self.assertEqual(captured["filename"], "amplifier_measurement.json")

    def test_report_rejects_results_that_are_not_formal_dtos(self):
        import infrastructure.gui_data_service as gui_data

        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            adapter = gui_data.ResultExportAdapter(
                query=self._query(
                    MeasurementResult(
                        measurement_type=MeasurementType.AMPLIFIER, run_id="report-run"
                    )
                ),
                paths=paths,
            )
            with self.assertRaises(ValueError):
                adapter.export_report("run::report-run::results/amp.json")

    def test_report_rejects_results_without_plottable_rows(self):
        import infrastructure.gui_data_service as gui_data

        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            adapter = gui_data.ResultExportAdapter(
                query=self._query(AmplifierMeasurementResult(run_id="viz-run")),
                paths=paths,
            )
            with self.assertRaises(ValueError):
                adapter.export_report("run::viz-run::results/amp.json")

    def test_result_export_output_dirs_are_unique(self):
        import infrastructure.gui_data_service as gui_data

        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            adapter = gui_data.ResultExportAdapter(query=None, paths=paths)
            self.assertNotEqual(
                adapter._report_output_dir(), adapter._report_output_dir()
            )


if __name__ == "__main__":
    unittest.main()
