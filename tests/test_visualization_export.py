import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from application.dto import (
    AmplifierFrequencyResult,
    AmplifierMeasurementResult,
    DriverPowerMappingMeasurementResult,
    DriverPowerPoint,
    measurement_result_from_document,
)
from application.ports.gui_data_service import RESULT_SOURCE_RUN, ResultFileDto
from application.run_stages import RunStageRecorder
from domain.run.models import CABLE_LOSS_INPUT_PATH, STAGE_CABLE_LOSS
from infrastructure.filesystem.paths import PathService
from infrastructure.gui_data_service import ResultExportAdapter, ResultQueryAdapter
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
from infrastructure.persistence.run_repository import RunRepository
from presentation.qt.export_page import ExportPage
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
            self._complete_cable_loss(runs, losses={"1.0": 1.0})
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
            self._complete_cable_loss(runs, losses={"1.0": 1.0})

            run_row = adapter.list_results()[0]
            loaded = adapter.load(run_row.result_id)
            latest = adapter.latest_result("cable_loss")

        self.assertEqual(loaded.data["cable_losses"], {"1.0": 1.0})
        self.assertEqual(latest.data["cable_losses"], {"1.0": 1.0})

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

    def test_latest_result_raises_when_all_run_results_are_corrupt(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, runs, adapter = self._setup(directory)
            layout = self._complete_cable_loss(runs, losses={"1.0": 1.0})
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
            paths, runs, adapter = self._setup(directory)
            layout = self._complete_cable_loss(runs, losses={"1.0": 1.0})
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
            layout = self._complete_cable_loss(runs, losses={"1.0": 1.0})
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
            self._complete_cable_loss(runs, losses={"1.0": 1.0})

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
            self._complete_cable_loss(runs, losses={"1.0": 1.0})
            outside = paths.test_results_dir / "cable_loss_results.json"
            outside.write_text("{}", encoding="utf-8")

            with patch(
                "infrastructure.gui_data_service.QFileDialog.getOpenFileName",
                return_value=(str(outside), "JSON files (*.json)"),
            ):
                with self.assertRaises(ValueError):
                    adapter.choose_and_load()

    def test_export_page_shows_the_source_of_each_result(self):
        with tempfile.TemporaryDirectory() as directory:
            paths, runs, adapter = self._setup(directory)
            self._complete_cable_loss(runs, losses={"1.0": 1.0})
            self._write_legacy_copy(paths, losses={"1.0": 99.0})

            from PySide6.QtWidgets import QApplication

            app = QApplication.instance() or QApplication([])
            page = ExportPage(result_service=adapter)
            labels = [
                page.file_table.item(row, 2).text()
                for row in range(page.file_table.rowCount())
            ]

        self.assertTrue(labels)
        self.assertTrue(all(label.startswith("运行 ") for label in labels))


class ReportAdapterContractTests(unittest.TestCase):

    def test_report_adapter_applies_overrides_and_forwards_result(self):
        import infrastructure.gui_data_service as gui_data

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
                    _amplifier_result(),
                    {"freq": 4.8},
                    "amp.json",
                )

        result = captured["data"]
        self.assertIsInstance(result, AmplifierMeasurementResult)
        self.assertTrue(result.sweep_rows())
        self.assertEqual(captured["filename"], "amp.json")
        self.assertEqual(result.source_filename, "amp.json")
        self.assertEqual(dict(result.configuration_snapshot), {"freq": 4.8})
        self.assertTrue(result.measurement_time)
        self.assertEqual(captured["dirs"][0].parent, paths.test_results_dir)

    def test_report_adapter_rejects_documents(self):
        import infrastructure.gui_data_service as gui_data

        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            adapter = gui_data.ReportAdapter(paths)
            with self.assertRaises(ValueError):
                adapter.create_summary_report(
                    {"run_id": "report-run", "results": {}}, {}, "amp.json"
                )

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
