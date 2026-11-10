"""步骤 11：历史运行页的页面状态渲染入口（纯表现层）。

覆盖：

- ``RunHistoryPageState`` 是只读快照：拒绝负行数、布尔/浮点行数与不合法的选中运行；
- ``render_state()`` 是空表说明与“重新生成”按钮的唯一写入点：显式快照按键值渲染，
  不传快照时从运行表格与视图模型推导；
- 全量刷新、单表刷新与选中变化都只调用一次渲染入口；
- 读取失败时表格与视图模型一起清空：看不到的行不能继续参与选择；
- 生产代码里没有渲染入口与一次性构造函数之外的状态写入，动态控件更是只由渲染入口
  写入，后续加过渡时不会漏掉分支。
"""

import ast
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelection, QItemSelectionModel, Qt
from PySide6.QtWidgets import QAbstractItemView, QMessageBox

from application.dto.run_deletion import RunDeletionFailure, RunDeletionOutcome
from application.dto.run_history import (
    RunArchiveState,
    RunArtifactSummary,
    RunHistorySummary,
    RunStageSummary,
)
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
OTHER_RUN_ID = "20261027T010203Z_abcdef12"
CREATED_AT = datetime(2026, 10, 28, 1, 2, 3, tzinfo=timezone.utc)

# 每次状态变化只允许经由这两个方法写控件状态：``_build_ui`` 是一次性结构配置，
# ``render_state`` 是唯一的动态渲染入口。其余方法出现状态写入即视为绕过门禁。
_STATE_WRITE_METHODS = frozenset({"setVisible", "setEnabled", "setText", "setProperty"})
_STATE_WRITE_OWNERS = frozenset(
    {"RunHistoryPage._build_ui", "RunHistoryPage.render_state"}
)
# 这些控件的值随状态变化（而不是随结构生成）：只允许渲染入口写它们，
# 比“整个 _build_ui 都放行”更精确。
_DYNAMIC_STATE_WIDGETS = frozenset(
    {
        "self.run_empty_hint",
        "self.regenerate_button",
        "self.open_run_dir_button",
        "self.open_report_button",
        "self.delete_runs_button",
    }
)


class _StaticRuns:
    """固定返回一批运行摘要的只读运行来源替身；失败可由 ``failure`` 注入。"""

    def __init__(self, summaries=(), *, failure=None):
        self.summaries = tuple(summaries)
        self.failure = failure

    def list_runs(self):
        if self.failure is not None:
            raise self.failure
        return self.summaries


def _summary(
    *,
    can_regenerate: bool = True,
    report_completed: bool = False,
    run_id: str = RUN_ID,
    run_status: RunStatus = RunStatus.COMPLETED,
) -> RunHistorySummary:
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
    if report_completed:
        artifacts = artifacts + (
            RunArtifactSummary(
                ArtifactKind.REPORT,
                ArtifactStatus.COMPLETED,
                source_result=AMPLIFIER_RESULT_PATH,
                stage=STAGE_AMPLIFIER,
            ),
        )
    return RunHistorySummary(
        run_id=run_id,
        created_at=CREATED_AT,
        state=RunArchiveState.USABLE,
        run_status=run_status,
        stages=(
            RunStageSummary(
                STAGE_AMPLIFIER, StageStatus.COMPLETED, AMPLIFIER_RESULT_PATH
            ),
        ),
        artifacts=artifacts,
    )


def _run_row(
    *,
    can_regenerate: bool = True,
    run_id: str = RUN_ID,
    run_status: RunStatus = RunStatus.COMPLETED,
) -> RunHistoryRow:
    return RunHistoryRow.from_summary(
        _summary(
            can_regenerate=can_regenerate,
            run_id=run_id,
            run_status=run_status,
        )
    )


def _page(*, summaries=(), runs=None, log_callback=None) -> RunHistoryPage:
    return RunHistoryPage(
        history=HistoryViewModel(
            runs=_StaticRuns(summaries) if runs is None else runs
        ),
        log_callback=log_callback,
    )


class _FakeRunService:
    """历史运行服务替身：记录删除批次，并让摘要来源同步移除已删除运行。"""

    def __init__(self, source, *, outcomes=None):
        self.source = source
        self.outcomes = dict(outcomes or {})
        self.batches = []

    def regenerate_failed_artifacts(self, run_id):
        raise AssertionError("删除用例不应触发重新生成")

    def delete_runs(self, run_ids):
        ids = tuple(run_ids)
        self.batches.append(ids)
        outcome = self.outcomes.get(
            ids, RunDeletionOutcome(deleted_run_ids=ids)
        )
        self.source.summaries = tuple(
            summary
            for summary in self.source.summaries
            if summary.run_id not in outcome.deleted_run_ids
        )
        return outcome


def _page_with_service(summaries, *, outcomes=None):
    source = _StaticRuns(summaries)
    service = _FakeRunService(source, outcomes=outcomes)
    page = RunHistoryPage(
        history=HistoryViewModel(runs=source), run_service=service
    )
    return page, service, source


def _select_rows(page, *rows: int) -> None:
    """在运行表格里选中指定行（ExtendedSelection）。"""
    model = page.run_table.model()
    selection = QItemSelection()
    for row in rows:
        selection.select(
            model.index(row, 0),
            model.index(row, page.run_table.columnCount() - 1),
        )
    page.run_table.selectionModel().select(
        selection, QItemSelectionModel.ClearAndSelect
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
        self.assertFalse(state.can_regenerate)

    def test_counts_must_be_non_negative_integers(self):
        for value in (-1, True, 1.5, "2"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    RunHistoryPageState(run_count=value)

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

    def test_open_actions_follow_the_selected_run(self):
        row = RunHistoryRow.from_summary(
            _summary(can_regenerate=False, report_completed=True)
        )
        state = RunHistoryPageState(selected_run=row)

        self.assertTrue(state.can_open_directory)
        self.assertTrue(state.can_open_report)

        no_report = RunHistoryPageState(
            selected_run=RunHistoryRow.from_summary(_summary(can_regenerate=False))
        )
        self.assertTrue(no_report.can_open_directory)
        self.assertFalse(no_report.can_open_report)

    def test_delete_follows_all_selected_runs(self):
        terminal = _run_row()
        running = _run_row(run_id=OTHER_RUN_ID, run_status=RunStatus.RUNNING)
        state = RunHistoryPageState(
            selected_run=running, selected_runs=(terminal, running)
        )

        self.assertTrue(state.can_delete)
        self.assertEqual(state.deletable_runs, (terminal,))

        only_running = RunHistoryPageState(
            selected_run=running, selected_runs=(running,)
        )
        self.assertFalse(only_running.can_delete)
        self.assertEqual(only_running.deletable_runs, ())

    def test_selected_runs_cannot_repeat(self):
        row = _run_row()

        with self.assertRaises(ValueError):
            RunHistoryPageState(selected_runs=(row, row))
        with self.assertRaises(TypeError):
            RunHistoryPageState(selected_runs=[row])


class RunHistoryPageRenderingTests(unittest.TestCase):
    """页面只有 ``render_state`` 一条可见状态写入路径。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_initial_render_shows_empty_hint_and_disables_regeneration(self):
        page = _page()

        self.assertFalse(page.run_empty_hint.isHidden())
        self.assertFalse(page.regenerate_button.isEnabled())
        self.assertFalse(page.open_run_dir_button.isEnabled())
        self.assertFalse(page.open_report_button.isEnabled())

    def test_run_table_is_a_read_only_display_panel(self):
        page = _page(summaries=[_summary()])

        table = page.run_table
        self.assertEqual(table.editTriggers(), QAbstractItemView.NoEditTriggers)
        for row in range(table.rowCount()):
            for column in range(table.columnCount()):
                item = table.item(row, column)
                if item is not None:
                    self.assertFalse(item.flags() & Qt.ItemIsEditable)

    def test_refresh_renders_rows_and_selection_enables_regeneration(self):
        page = _page(summaries=[_summary()])

        self.assertEqual(page.run_table.rowCount(), 1)
        self.assertTrue(page.run_empty_hint.isHidden())
        self.assertFalse(page.regenerate_button.isEnabled())

        page.run_table.setCurrentCell(0, 0)

        self.assertTrue(page.regenerate_button.isEnabled())

    def test_explicit_state_is_rendered_without_reading_the_tables(self):
        page = _page(summaries=[_summary(can_regenerate=False)])

        page.render_state(RunHistoryPageState(run_count=0, selected_run=None))

        # 表格里明明有运行，但显式快照说“空”：渲染只认快照。
        self.assertEqual(page.run_table.rowCount(), 1)
        self.assertFalse(page.run_empty_hint.isHidden())
        self.assertFalse(page.regenerate_button.isEnabled())

        page.render_state(
            RunHistoryPageState(
                run_count=5,
                selected_run=_run_row(can_regenerate=True),
            )
        )

        self.assertTrue(page.run_empty_hint.isHidden())
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
            "空表说明与运行动作按钮只能由 render_state 写入",
        )
        # 门禁不能因为“没人写”而空过：这些动态控件都必须真的由渲染入口写入。
        self.assertEqual(
            {
                receiver
                for _owner, receiver, _method in calls
                if receiver in _DYNAMIC_STATE_WIDGETS
            },
            set(_DYNAMIC_STATE_WIDGETS),
        )


class RunHistoryDeletionPageTests(unittest.TestCase):
    """历史运行页的删除按钮：多选、确认与逐项结果。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_delete_button_is_disabled_until_a_deletable_run_is_selected(self):
        page, _service, _source = _page_with_service([_summary()])

        self.assertFalse(page.delete_runs_button.isEnabled())

        _select_rows(page, 0)

        self.assertTrue(page.delete_runs_button.isEnabled())

    def test_multi_selection_deletes_all_terminal_runs(self):
        page, service, _source = _page_with_service(
            [_summary(run_id=RUN_ID), _summary(run_id=OTHER_RUN_ID)]
        )
        _select_rows(page, 0, 1)

        with patch(
            "presentation.qt.run_history_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch(
            "presentation.qt.run_history_page.QMessageBox.information"
        ) as information:
            self.assertTrue(page.delete_selected_runs())

        self.assertEqual(service.batches, [(RUN_ID, OTHER_RUN_ID)])
        self.assertEqual(page.run_table.rowCount(), 0)
        message = information.call_args.args[2]
        self.assertIn(RUN_ID, message)
        self.assertIn(OTHER_RUN_ID, message)

    def test_running_runs_are_kept_out_of_the_delete_batch(self):
        page, service, _source = _page_with_service(
            [
                _summary(run_id=RUN_ID),
                _summary(run_id=OTHER_RUN_ID, run_status=RunStatus.RUNNING),
            ]
        )
        _select_rows(page, 0, 1)

        with patch(
            "presentation.qt.run_history_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ) as question, patch(
            "presentation.qt.run_history_page.QMessageBox.information"
        ) as information:
            self.assertTrue(page.delete_selected_runs())

        self.assertEqual(service.batches, [(RUN_ID,)])
        self.assertIn("正在进行中", question.call_args.args[2])
        self.assertIn(f"进行中未删除: {OTHER_RUN_ID}", information.call_args.args[2])

    def test_only_running_selection_keeps_delete_disabled(self):
        page, service, _source = _page_with_service(
            [_summary(run_status=RunStatus.RUNNING)]
        )
        _select_rows(page, 0)

        self.assertFalse(page.delete_runs_button.isEnabled())
        with patch(
            "presentation.qt.run_history_page.QMessageBox.warning"
        ) as warning:
            self.assertFalse(page.delete_selected_runs())

        self.assertEqual(service.batches, [])
        self.assertIn("正在进行中", warning.call_args.args[2])

    def test_cancelled_confirmation_does_not_delete(self):
        page, service, _source = _page_with_service([_summary()])
        _select_rows(page, 0)

        with patch(
            "presentation.qt.run_history_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            self.assertFalse(page.delete_selected_runs())

        self.assertEqual(service.batches, [])

    def test_missing_deletion_service_keeps_the_button_disabled(self):
        page = _page(summaries=[_summary()])
        _select_rows(page, 0)

        self.assertFalse(page.delete_runs_button.isEnabled())
        with patch(
            "presentation.qt.run_history_page.QMessageBox.warning"
        ) as warning:
            self.assertFalse(page.delete_selected_runs())

        self.assertIn("未装配", warning.call_args.args[2])

    def test_deletion_message_reports_each_failed_run(self):
        failure = RunDeletionFailure(OTHER_RUN_ID, "运行正在进行中，不能删除")
        outcome = RunDeletionOutcome((RUN_ID,), (failure,))

        message = RunHistoryPage._deletion_message(
            outcome, (_run_row(run_id=OTHER_RUN_ID, run_status=RunStatus.RUNNING),)
        )

        self.assertIn(f"已删除运行: {RUN_ID}", message)
        self.assertIn(f"运行 {OTHER_RUN_ID} 未删除", message)
        self.assertIn("进行中未删除", message)

    def test_deletion_message_rejects_non_dto_outcomes(self):
        with self.assertRaises(TypeError):
            RunHistoryPage._deletion_message(object())


if __name__ == "__main__":
    unittest.main()
