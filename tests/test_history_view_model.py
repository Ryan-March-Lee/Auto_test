"""步骤 11：历史查看只读视图模型的独立测试（不需要 Qt 控件）。

覆盖：列表行只含结果标识与展示文本、选中语义、加载只接受正式结果 DTO、
频率浏览状态，以及视图模型不依赖 Qt、不接触文件路径。
"""

import ast
import unittest
from datetime import datetime
from pathlib import Path

from application.dto import (
    AmplifierFrequencyResult,
    AmplifierMeasurementResult,
    DriverPowerMappingMeasurementResult,
    DriverPowerPoint,
)
from application.ports.gui_data_service import (
    RESULT_SOURCE_RUN,
    LoadedResultDto,
    ResultFileDto,
)
from presentation.qt.history_view_model import (
    HistoryRow,
    HistoryViewModel,
    LoadedHistoryResult,
    result_kind,
    result_plot_series,
)

ROOT = Path(__file__).resolve().parents[1]
VIEW_MODEL_SOURCE = ROOT / "presentation" / "qt" / "history_view_model.py"
RUN_ID = "20261027T010203Z_abcdef12"


def _amplifier_result() -> AmplifierMeasurementResult:
    return AmplifierMeasurementResult(
        run_id=RUN_ID,
        frequencies=(
            AmplifierFrequencyResult(
                frequency_ghz="5.2",
                sweep_data={"input_power_dut": [-10.0], "output_power_dut": [1.0]},
            ),
            AmplifierFrequencyResult(
                frequency_ghz="4.8",
                sweep_data={"input_power_dut": [-20.0], "output_power_dut": [-9.0]},
            ),
        ),
    )


def _driver_result() -> DriverPowerMappingMeasurementResult:
    return DriverPowerMappingMeasurementResult(
        run_id=RUN_ID,
        points=(
            DriverPowerPoint(
                frequency_ghz="4.8", input_power_dbm=-20.0, output_power_dbm=-10.0
            ),
        ),
    )


def _row(name: str, *, run_id: str | None = RUN_ID) -> ResultFileDto:
    return ResultFileDto(
        result_id=f"run::{run_id or 'unknown'}::{name}",
        filename=name.rsplit("/", 1)[-1],
        file_type="功放测试",
        modified_at=datetime(2026, 10, 27, 1, 2, 3),
        run_id=run_id,
    )


class _FakeQuery:
    """只读历史查询替身：只返回正式 DTO，记录调用。"""

    def __init__(self, rows, results=None):
        self._rows = list(rows)
        self._results = dict(results or {})
        self.loaded_ids: list[str] = []

    def list_results(self):
        return list(self._rows)

    def load(self, result_id):
        self.loaded_ids.append(result_id)
        loaded = self._results.get(result_id)
        if loaded is None:
            raise FileNotFoundError(result_id)
        return loaded

    def read_bytes(self, result_id):  # pragma: no cover - 历史查看不使用
        raise AssertionError("历史查看不得逐字节读取结果")

    def choose_and_load(self):  # pragma: no cover - 文件选择属于基础设施
        raise AssertionError("视图模型不得打开文件对话框")

    def latest_result(self, result_type):  # pragma: no cover - 步骤 8 已移除隐式来源
        raise AssertionError("视图模型不得按“最新结果”加载")


class HistoryRowTests(unittest.TestCase):
    def test_row_only_carries_identifier_and_display_text(self):
        row = HistoryRow.from_dto(_row("inputs/amplifier_measurement.json"))

        self.assertEqual(
            {field for field in row.__dataclass_fields__},
            {"result_id", "filename", "file_type", "modified_at", "source", "run_id"},
        )
        self.assertFalse(hasattr(row, "path"))
        self.assertFalse(hasattr(row, "file_path"))
        self.assertEqual(row.source, RESULT_SOURCE_RUN)
        self.assertEqual(row.source_label, f"运行 {RUN_ID}")
        self.assertEqual(row.modified_text, "2026-10-27 01:02:03")

    def test_unregistered_source_is_labelled_explicitly(self):
        row = HistoryRow.from_dto(_row("cable_loss.json", run_id=None))

        self.assertEqual(row.source_label, "未登记来源")

    def test_row_rejects_objects_that_are_not_history_rows(self):
        with self.assertRaises(TypeError):
            HistoryRow.from_dto({"result_id": "x"})

    def test_suggested_filename_swaps_the_suffix(self):
        row = HistoryRow.from_dto(_row("results/amplifier_measurement.json"))

        self.assertEqual(
            row.suggested_filename("csv"), "amplifier_measurement.csv"
        )
        self.assertEqual(
            row.suggested_filename(".json"), "amplifier_measurement.json"
        )


class HistoryViewModelTests(unittest.TestCase):
    def _view_model(self, rows=None, results=None):
        query = _FakeQuery(rows if rows is not None else [_row("a.json")], results)
        return HistoryViewModel(query), query

    def test_reload_builds_read_only_rows(self):
        view_model, _query = self._view_model([_row("a.json"), _row("b.json")])

        rows = view_model.reload()

        self.assertEqual([row.filename for row in rows], ["a.json", "b.json"])
        self.assertEqual(view_model.rows, rows)
        self.assertIsInstance(view_model.rows, tuple)

    def test_selection_clears_when_the_row_disappears(self):
        query = _FakeQuery([_row("a.json")])
        view_model = HistoryViewModel(query)
        view_model.reload()
        self.assertIsNotNone(view_model.select(0))

        query._rows = []
        view_model.reload()

        self.assertIsNone(view_model.selected)
        self.assertIsNone(view_model.selected_index)

    def test_invalid_selection_clears_the_current_one(self):
        view_model, _query = self._view_model()
        view_model.reload()
        view_model.select(0)

        for index in (None, -1, 5, "0", True):
            with self.subTest(index=index):
                self.assertIsNone(view_model.select(index))
                self.assertIsNone(view_model.selected)

    def test_show_builds_plot_series_from_the_formal_dto(self):
        view_model, _query = self._view_model()
        loaded = view_model.show(
            LoadedResultDto("run::r::a.json", "a.json", _amplifier_result())
        )

        self.assertIsInstance(loaded, LoadedHistoryResult)
        self.assertEqual(loaded.frequencies, (4.8, 5.2))
        self.assertEqual(
            loaded.sweep_data_at(0), {"input_power_dut": [-20.0], "output_power_dut": [-9.0]}
        )
        self.assertEqual(
            loaded.plot_record(1),
            {"frequency": 5.2, "sweep_data": {"input_power_dut": [-10.0], "output_power_dut": [1.0]}},
        )
        self.assertEqual(loaded.label, "功放测试")
        self.assertEqual(loaded.kind, result_kind(loaded.result))
        self.assertEqual(view_model.frequency_index, 0)

    def test_loaded_series_is_frozen_and_snapshots_are_independent(self):
        """视图模型缓存不可被外部改写：内部是元组，对外返回新列表副本。"""
        view_model, _query = self._view_model()
        loaded = view_model.show(
            LoadedResultDto("run::r::a.json", "a.json", _amplifier_result())
        )

        with self.assertRaises(TypeError):
            loaded.series["4.8"]["input_power_dut"] = ()  # type: ignore[index]
        self.assertIsInstance(loaded.series["4.8"]["input_power_dut"], tuple)

        snapshot = loaded.series_snapshot()
        snapshot["4.8"]["sweep_data"]["input_power_dut"].append(999.0)
        snapshot["4.8"]["sweep_data"].clear()

        self.assertEqual(loaded.sweep_data_at(0)["input_power_dut"], [-20.0])
        self.assertEqual(view_model.current_series["input_power_dut"], [-20.0])
        self.assertEqual(len(loaded.series_snapshot()["4.8"]["sweep_data"]), 2)

    def test_sweep_data_copies_do_not_leak_between_reads(self):
        view_model, _query = self._view_model()
        view_model.show(
            LoadedResultDto("run::r::a.json", "a.json", _amplifier_result())
        )

        first = view_model.current_series
        first["output_power_dut"].append(123.0)

        self.assertEqual(view_model.current_series["output_power_dut"], [-9.0])

    def test_show_rejects_documents_and_non_dtos(self):
        view_model, _query = self._view_model()

        with self.assertRaises(TypeError):
            view_model.show({"result_id": "x", "filename": "x.json"})
        with self.assertRaises(ValueError):
            view_model.show(
                LoadedResultDto(
                    "run::r::a.json",
                    "a.json",
                    DriverPowerMappingMeasurementResult(run_id=RUN_ID),
                )
            )

    def test_load_selected_requires_a_selection(self):
        view_model, query = self._view_model(
            results={
                _row("a.json").result_id: LoadedResultDto(
                    _row("a.json").result_id, "a.json", _driver_result()
                )
            }
        )
        view_model.reload()

        with self.assertRaises(ValueError):
            view_model.load_selected()
        self.assertEqual(query.loaded_ids, [])

        view_model.select(0)
        loaded = view_model.load_selected()

        self.assertEqual(query.loaded_ids, [_row("a.json").result_id])
        self.assertEqual(loaded.label, "驱动映射")

    def test_loaded_result_keeps_the_history_identifier(self):
        """步骤 8：已加载结果必须带历史标识，动作不再靠“最新结果”找回来源。"""
        view_model, _query = self._view_model()
        loaded = view_model.show(
            LoadedResultDto("run::r::a.json", "a.json", _amplifier_result())
        )

        self.assertEqual(loaded.result_id, "run::r::a.json")
        self.assertEqual(view_model.loaded.result_id, "run::r::a.json")

    def test_view_model_has_no_implicit_latest_result_entry(self):
        """步骤 8：视图模型不提供“最新结果”入口，来源只能由调用方显式给出。"""
        view_model, _query = self._view_model()

        self.assertFalse(hasattr(view_model, "load_latest"))

    def test_frequency_navigation_stops_at_the_boundaries(self):
        view_model, _query = self._view_model()
        view_model.show(
            LoadedResultDto("run::r::a.json", "a.json", _amplifier_result())
        )

        self.assertEqual(view_model.current_frequency, 4.8)
        self.assertEqual(view_model.current_series["output_power_dut"], [-9.0])
        self.assertTrue(view_model.has_next)
        self.assertFalse(view_model.has_previous)

        self.assertTrue(view_model.next_frequency())
        self.assertEqual(view_model.current_frequency, 5.2)
        self.assertFalse(view_model.has_next)
        self.assertFalse(view_model.next_frequency())
        self.assertTrue(view_model.previous_frequency())
        self.assertEqual(view_model.frequency_index, 0)

    def test_clear_returns_to_the_unloaded_state(self):
        view_model, _query = self._view_model()
        view_model.show(
            LoadedResultDto("run::r::a.json", "a.json", _amplifier_result())
        )

        view_model.clear()

        self.assertIsNone(view_model.loaded)
        self.assertIsNone(view_model.current_frequency)
        self.assertIsNone(view_model.current_series)
        self.assertEqual(view_model.frequency_count, 0)

    def test_plot_series_rejects_documents(self):
        with self.assertRaises(ValueError):
            result_plot_series({"results": {}})


class HistoryViewModelBoundaryTests(unittest.TestCase):
    """视图模型是纯 Python：不依赖 Qt，也不读写文件。"""

    def test_module_has_no_qt_dependency(self):
        source = VIEW_MODEL_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        self.assertNotIn("PySide6", imported)
        self.assertNotIn("infrastructure", imported)

    def test_module_does_not_touch_the_file_system(self):
        called = {
            node.func.attr
            for node in ast.walk(ast.parse(VIEW_MODEL_SOURCE.read_text(encoding="utf-8")))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }

        for forbidden in ("open", "read_text", "write_text", "stat", "resolve", "glob"):
            self.assertNotIn(forbidden, called)

    def test_measurement_pages_do_not_call_legacy_payload(self):
        for relative in (
            "presentation/qt/visualization_page.py",
            "presentation/qt/export_page.py",
            "presentation/qt/history_view_model.py",
        ):
            called = {
                node.func.attr
                for node in ast.walk(
                    ast.parse((ROOT / relative).read_text(encoding="utf-8"))
                )
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            }
            with self.subTest(module=relative):
                self.assertNotIn("legacy_payload", called)
                self.assertNotIn("to_legacy_payload", called)


if __name__ == "__main__":
    unittest.main()
