"""步骤 11：历史管理页的页面状态渲染入口（纯表现层）。

覆盖：

- ``RunHistoryPageState`` 是只读快照：拒绝负行数、布尔/浮点行数与不合法的选中运行；
- ``render_state()`` 是空表说明与“重新生成”按钮的唯一写入点：显式快照按键值渲染，
  不传快照时从两张表格与视图模型推导；
- 全量刷新、单表刷新与选中变化都只调用一次渲染入口；
- 读取失败时表格与视图模型一起清空：看不到的行不能继续参与选择或导出判定；
- 生产代码里没有渲染入口与一次性构造函数之外的状态写入，三个动态控件更是只由渲染
  入口写入，后续加过渡时不会漏掉分支。
"""

import ast
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from application.dto.run_history import (
    RunArchiveState,
    RunArtifactSummary,
    RunHistorySummary,
    RunStageSummary,
)
from application.ports.gui_data_service import ResultFileDto
from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    STAGE_AMPLIFIER,
    ArtifactKind,
    ArtifactStatus,
    RunStatus,
    StageStatus,
)
from presentation.qt.history_view_model import HistoryViewModel, RunHistoryRow
from presentation.qt.run_history_page import RunHistoryPage, RunHistoryPageState


ROOT = Path(__file__).resolve().parents[2]
PAGE_SOURCE = ROOT / "presentation" / "qt" / "run_history_page.py"
RUN_ID = "20261028T010203Z_abcdef12"
CREATED_AT = datetime(2026, 10, 28, 1, 2, 3, tzinfo=timezone.utc)

# 每次状态变化只允许经由这两个方法写控件状态：``_build_ui`` 是一次性结构配置，
# ``render_state`` 是唯一的动态渲染入口。其余方法出现状态写入即视为绕过门禁。
_STATE_WRITE_METHODS = frozenset({"setVisible", "setEnabled", "setText", "setProperty"})
_STATE_WRITE_OWNERS = frozenset(
    {"RunHistoryPage._build_ui", "RunHistoryPage.render_state"}
)
# 这三个控件的值随状态变化（而不是随结构生成）：只允许渲染入口写它们，
# 比“整个 _build_ui 都放行”更精确。
_DYNAMIC_STATE_WIDGETS = frozenset(
    {"self.run_empty_hint", "self.result_empty_hint", "self.regenerate_button"}
)


class _ResultQuery:
    """状态测试不读文件：按给定行返回结果描述，失败可由 ``failure`` 注入。"""

    def __init__(self, rows=(), *, failure=None):
        self.rows = tuple(rows)
        self.failure = failure

    def list_results(self):
        if self.failure is not None:
            raise self.failure
        return list(self.rows)

    def load(self, result_id):
        raise AssertionError(f"状态测试不加载结果: {result_id}")


class _StaticRuns:
    """固定返回一批运行摘要的只读运行来源替身；失败可由 ``failure`` 注入。"""

    def __init__(self, summaries=(), *, failure=None):
        self.summaries = tuple(summaries)
        self.failure = failure

    def list_runs(self):
        if self.failure is not None:
            raise self.failure
        return self.summaries


class _StubExport:
    """导出服务替身：状态测试不触发导出。"""

    def execute(self, request):
        raise AssertionError("状态测试不导出")


def _summary(*, can_regenerate: bool = True) -> RunHistorySummary:
    artifacts = ()
    if can_regenerate:
        artifacts = (
            RunArtifactSummary(
                ArtifactKind.CSV,
                ArtifactStatus.FAILED,
                source_result=AMPLIFIER_RESULT_PATH,
                stage=STAGE_AMPLIFIER,
                error="磁盘已满",
            ),
        )
    return RunHistorySummary(
        run_id=RUN_ID,
        created_at=CREATED_AT,
        state=RunArchiveState.USABLE,
        run_status=RunStatus.COMPLETED,
        stages=(
            RunStageSummary(
                STAGE_AMPLIFIER, StageStatus.COMPLETED, AMPLIFIER_RESULT_PATH
            ),
        ),
        artifacts=artifacts,
    )


def _run_row(*, can_regenerate: bool = True) -> RunHistoryRow:
    return RunHistoryRow.from_summary(_summary(can_regenerate=can_regenerate))


def _result_file() -> ResultFileDto:
    return ResultFileDto(
        result_id=f"run::{RUN_ID}::results/amplifier_measurement_1.0.json",
        filename="amplifier_measurement_1.0.json",
        file_type="amplifier",
        modified_at=CREATED_AT,
        run_id=RUN_ID,
    )


def _page(*, summaries=(), runs=None, query=None, log_callback=None) -> RunHistoryPage:
    return RunHistoryPage(
        history=HistoryViewModel(
            _ResultQuery() if query is None else query,
            runs=_StaticRuns(summaries) if runs is None else runs,
        ),
        export_service=_StubExport(),
        log_callback=log_callback,
    )


def _state_write_calls(source: str) -> set[tuple[str, str | None, str]]:
    """返回 ``(类.方法, 接收者文本, 状态方法名)`` 三元组集合。"""
    owners = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ClassDef):
            continue
        for method in node.body:
            if not isinstance(method, ast.FunctionDef):
                continue
            for call in ast.walk(method):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and call.func.attr in _STATE_WRITE_METHODS
                ):
                    owners.add(
                        (
                            f"{node.name}.{method.name}",
                            _receiver_text(call.func.value),
                            call.func.attr,
                        )
                    )
    return owners


def _receiver_text(node) -> str | None:
    """把 ``self.regenerate_button`` 这类接收者还原成文本；其他形式返回 ``None``。"""
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        return f"{node.value.id}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return None


def _page_source_calls() -> set[tuple[str, str | None, str]]:
    """解析页面源码，返回全部控件状态写入调用。"""
    source = PAGE_SOURCE.read_text(encoding="utf-8-sig")
    return _state_write_calls(source)


class RunHistoryPageStateTests(unittest.TestCase):
    """状态对象只描述外观所需事实，不合法输入立即失败。"""

    def test_default_state_is_empty_and_cannot_regenerate(self):
        state = RunHistoryPageState()

        self.assertTrue(state.run_list_empty)
        self.assertTrue(state.result_list_empty)
        self.assertFalse(state.can_regenerate)

    def test_counts_must_be_non_negative_integers(self):
        for value in (-1, True, 1.5, "2"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    RunHistoryPageState(run_count=value)
                with self.assertRaises(ValueError):
                    RunHistoryPageState(result_count=value)

    def test_selected_run_must_come_from_the_view_model(self):
        with self.assertRaises(TypeError):
            RunHistoryPageState(selected_run=object())

    def test_can_regenerate_follows_the_selected_run(self):
        self.assertFalse(
            RunHistoryPageState(selected_run=_run_row(can_regenerate=False))
            .can_regenerate
        )
        self.assertTrue(
            RunHistoryPageState(selected_run=_run_row(can_regenerate=True))
            .can_regenerate
        )


class RunHistoryPageRenderingTests(unittest.TestCase):
    """页面只有 ``render_state`` 一条可见状态写入路径。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_initial_render_shows_empty_hints_and_disables_regeneration(self):
        page = _page()

        self.assertFalse(page.run_empty_hint.isHidden())
        self.assertFalse(page.result_empty_hint.isHidden())
        self.assertFalse(page.regenerate_button.isEnabled())

    def test_refresh_renders_rows_and_selection_enables_regeneration(self):
        page = _page(summaries=[_summary()])

        self.assertEqual(page.run_table.rowCount(), 1)
        self.assertTrue(page.run_empty_hint.isHidden())
        self.assertFalse(page.regenerate_button.isEnabled())

        page.run_table.setCurrentCell(0, 0)

        self.assertTrue(page.regenerate_button.isEnabled())

    def test_explicit_state_is_rendered_without_reading_the_tables(self):
        page = _page(summaries=[_summary(can_regenerate=False)])

        page.render_state(
            RunHistoryPageState(run_count=0, result_count=0, selected_run=None)
        )

        # 表格里明明有运行，但显式快照说“空”：渲染只认快照。
        self.assertEqual(page.run_table.rowCount(), 1)
        self.assertFalse(page.run_empty_hint.isHidden())
        self.assertFalse(page.regenerate_button.isEnabled())

        page.render_state(
            RunHistoryPageState(
                run_count=5,
                result_count=2,
                selected_run=_run_row(can_regenerate=True),
            )
        )

        self.assertTrue(page.run_empty_hint.isHidden())
        self.assertTrue(page.result_empty_hint.isHidden())
        self.assertTrue(page.regenerate_button.isEnabled())

    def test_derived_state_clears_a_stale_selection(self):
        page = _page(summaries=[_summary()])
        page.run_table.setCurrentCell(0, 0)
        self.assertIsNotNone(page.history.selected_run)

        page.history.run_source.summaries = ()
        page.refresh()

        self.assertEqual(page.run_table.rowCount(), 0)
        self.assertIsNone(page.history.selected_run)
        self.assertFalse(page.regenerate_button.isEnabled())
        self.assertFalse(page.run_empty_hint.isHidden())

    def test_render_state_rejects_foreign_values(self):
        page = _page()

        with self.assertRaises(TypeError):
            page.render_state(object())

    def test_refresh_calls_the_single_renderer_once(self):
        page = _page(summaries=[_summary()])

        with patch.object(page, "render_state", wraps=page.render_state) as render:
            page.refresh()

        self.assertEqual(render.call_count, 1)

    def test_table_refresh_and_selection_each_render_once(self):
        page = _page(summaries=[_summary()])

        with patch.object(page, "render_state", wraps=page.render_state) as render:
            page.refresh_run_list()
        self.assertEqual(render.call_count, 1)

        with patch.object(page, "render_state", wraps=page.render_state) as render:
            page.refresh_result_list()
        self.assertEqual(render.call_count, 1)

        with patch.object(page, "render_state", wraps=page.render_state) as render:
            page.run_table.setCurrentCell(0, 0)
        self.assertEqual(render.call_count, 1)

    def test_explicit_state_does_not_touch_the_selection(self):
        page = _page(summaries=[_summary()])
        page.run_table.setCurrentCell(0, 0)

        page.render_state(RunHistoryPageState())

        # 显式快照是纯投影：只按键值渲染，不回写表格选择。
        self.assertEqual(page.history.selected_run_index, 0)
        self.assertFalse(page.regenerate_button.isEnabled())

    def test_run_list_failure_clears_the_stale_runs_and_selection(self):
        page = _page(summaries=[_summary()])
        page.run_table.setCurrentCell(0, 0)
        self.assertIsNotNone(page.history.selected_run)
        page.history.run_source.failure = OSError("运行根目录不可读")

        with patch.object(page, "render_state", wraps=page.render_state) as render:
            page.refresh()

        self.assertEqual(render.call_count, 1)
        self.assertEqual(page.run_table.rowCount(), 0)
        # 表格已空，视图模型里的旧摘要与选中运行必须一起消失。
        self.assertIsNone(page.history.selected_run)
        self.assertIsNone(page.history.run_row(RUN_ID))
        self.assertFalse(page.regenerate_button.isEnabled())
        self.assertFalse(page.run_empty_hint.isHidden())

    def test_result_list_failure_clears_the_stale_results_and_selection(self):
        query = _ResultQuery([_result_file()])
        messages = []
        page = _page(query=query, log_callback=messages.append)
        page.result_table.setCurrentCell(0, 0)
        # 结果表没有选中信号：真实路径由导出动作把当前行写回视图模型。
        self.assertIsNotNone(page.selected_row())
        query.failure = OSError("结果根目录不可读")

        with patch.object(page, "render_state", wraps=page.render_state) as render:
            page.refresh_result_list()

        self.assertEqual(render.call_count, 1)
        self.assertEqual(page.result_table.rowCount(), 0)
        self.assertIsNone(page.history.selected)
        self.assertIsNone(page.history.row_at(0))
        self.assertFalse(page.result_empty_hint.isHidden())
        self.assertTrue(any("读取历史结果失败" in message for message in messages))

    def test_result_failure_during_refresh_does_not_raise_and_renders_once(self):
        """单表刷新与全量刷新共用同一条失败路径：都不抛异常、都只渲染一次。"""
        query = _ResultQuery([_result_file()], failure=OSError("结果根目录不可读"))

        with patch.object(RunHistoryPage, "render_state", autospec=True) as render:
            page = _page(summaries=[_summary()], query=query)

        self.assertEqual(render.call_count, 1)
        self.assertEqual(page.result_table.rowCount(), 0)
        self.assertEqual(page.run_table.rowCount(), 1)

    def test_state_writes_stay_inside_the_renderer(self):
        owners = {owner for owner, _receiver, _method in _page_source_calls()}

        self.assertEqual(
            owners,
            set(_STATE_WRITE_OWNERS),
            "页面状态写入只能出现在 _build_ui（一次性结构配置）与 "
            "render_state（唯一动态渲染入口）里",
        )

    def test_dynamic_state_widgets_are_written_only_by_the_renderer(self):
        calls = _page_source_calls()

        self.assertEqual(
            {
                owner
                for owner, receiver, _method in calls
                if receiver in _DYNAMIC_STATE_WIDGETS
            },
            {"RunHistoryPage.render_state"},
            "空表说明与“重新生成”按钮只能由 render_state 写入",
        )
        # 门禁不能因为“没人写”而空过：三个动态控件都必须真的由渲染入口写入。
        self.assertEqual(
            {
                receiver
                for _owner, receiver, _method in calls
                if receiver in _DYNAMIC_STATE_WIDGETS
            },
            set(_DYNAMIC_STATE_WIDGETS),
        )


if __name__ == "__main__":
    unittest.main()
