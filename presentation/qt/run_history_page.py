"""历史运行与结果管理页面（重构计划步骤 7、步骤 10 收口）。

正常测试流程已经自动归档结果，用户不需要先来这里选择文件再手动导出。本页面只承担
**历史管理**，页面类名、路由与模块名都不再沿用“导出页”语义：

- 页面只列出历史运行，并显示每次运行的阶段进度、正式结果数与交付产物状态；
- 页面动作只保留运行级维护：对未生成的交付产物执行“补生成”（写回原运行目录，不重跑
  测量），以及删除用户明确选中的历史运行（整目录删除，进行中的运行不允许删除）。
  历史结果浏览、测试报告查看与另存为都属于“查看结果”的动作，已移到数据可视化页
  （见 ``visualization_page``）；页面不再提供文件系统入口。

页面职责边界：

- 列表与状态全部来自只读历史视图模型（``HistoryViewModel`` + 应用层运行摘要），页面
  不读 manifest、不拼接运行目录路径，也不判断文件是否存在。
- 补生成走应用层的 ``RunHistoryServicePort``：页面只传“已列出的运行标识”，不传阶段、
  不传路径；应用层决定用哪个阶段重试，并把产物写回受控位置。
- 删除同样只传“已列出的运行标识”：页面对多选行做确认与文案，目录边界、进行中复核与
  实际删除由应用层和删除适配器完成，页面不调用任何文件系统删除接口。
- 用户可见文案属于本页面：应用层返回结构化结果（``ArchivalOutcome`` /
  ``RunDeletionOutcome``），页面据此生成日志与对话框文本。
- 页面可见状态只有一个渲染入口：刷新、选中变化与读取失败都只更新表格数据，再由
  ``render_state()`` 按只读的 ``RunHistoryPageState`` 一次渲染空表说明与按钮可用性。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from application.dto.archival import ArchivalOutcome
from application.dto.run_deletion import RunDeletionOutcome
from application.ports.gui_data_service import (
    GuiDataService,
    RunHistoryServicePort,
)

from .history_view_model import (
    HistoryViewModel,
    RunHistoryRow,
    artifact_kind_text,
)
from .theme import set_button_variant

# 历史运行表格的列顺序；页面只按视图模型给出的文本填表。
_RUN_COLUMNS = ("运行", "创建时间", "状态", "阶段", "正式结果", "交付产物", "说明")


@dataclass(frozen=True)
class RunHistoryPageState:
    """历史运行页可见状态的只读快照。

    页面不再在刷新、选中与读取失败分支里各自调用 ``setVisible`` / ``setEnabled``：
    这些分支只更新运行表格的数据，再交给 :meth:`RunHistoryPage.render_state` 一次渲染。
    状态对象因此只保留驱动外观所需的事实（行数、当前运行与多选运行），行内展示文本与
    “能否重新生成/删除”仍由视图模型给出。以后增加淡入、禁用或
    局部过渡时只改渲染入口，不需要追踪每个回调。
    """

    run_count: int = 0
    selected_run: RunHistoryRow | None = None
    selected_runs: tuple[RunHistoryRow, ...] = ()

    def __post_init__(self) -> None:
        value = self.run_count
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"run_count 必须是非负整数，收到: {value!r}")
        if self.selected_run is not None and not isinstance(
            self.selected_run, RunHistoryRow
        ):
            raise TypeError("selected_run 必须是 RunHistoryRow 或 None")
        if not isinstance(self.selected_runs, tuple):
            raise TypeError("selected_runs 必须是 RunHistoryRow 元组")
        if not all(isinstance(row, RunHistoryRow) for row in self.selected_runs):
            raise TypeError("selected_runs 中的每项必须是 RunHistoryRow")
        run_ids = [row.run_id for row in self.selected_runs]
        if len(set(run_ids)) != len(run_ids):
            raise ValueError("selected_runs 不能包含重复的运行")

    @property
    def run_list_empty(self) -> bool:
        """历史运行表格是否没有内容（决定空表说明是否显示）。"""
        return self.run_count == 0

    @property
    def can_regenerate(self) -> bool:
        """选中的运行是否有可重新生成的失败产物。"""
        return self.selected_run is not None and self.selected_run.can_regenerate

    @property
    def deletable_runs(self) -> tuple[RunHistoryRow, ...]:
        """多选运行中允许删除的部分（进行中的运行不在其中）。"""
        return tuple(row for row in self.selected_runs if row.can_delete)

    @property
    def can_delete(self) -> bool:
        """多选里是否至少有一条非进行中的运行可以删除。"""
        return bool(self.deletable_runs)


class RunHistoryPage(QWidget):
    """历史运行页：运行与产物状态、失败产物重新生成、删除选中的运行。"""

    def __init__(
        self,
        *,
        history: HistoryViewModel,
        run_service: RunHistoryServicePort | None = None,
        log_callback: Callable[[str], Any] | None = None,
        parent=None,
    ):
        if not isinstance(history, HistoryViewModel):
            raise TypeError("历史管理页必须使用只读 HistoryViewModel")
        if run_service is not None and not callable(
            getattr(run_service, "regenerate_failed_artifacts", None)
        ):
            raise TypeError("历史运行服务必须提供 regenerate_failed_artifacts(run_id)")
        super().__init__(parent)
        self.history = history
        self.run_service = run_service
        self.log_callback = log_callback or (lambda _message: None)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(12)

        run_group = QGroupBox("历史运行")
        run_layout = QVBoxLayout(run_group)
        run_layout.setSpacing(8)
        self.run_table = QTableWidget()
        self.run_table.setColumnCount(len(_RUN_COLUMNS))
        self.run_table.setHorizontalHeaderLabels(list(_RUN_COLUMNS))
        self.run_table.verticalHeader().setVisible(False)
        self.run_table.setAlternatingRowColors(True)
        self.run_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.run_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        # 历史运行是只读展示面板：用户只能选择与查看，不能就地改写词条。
        self.run_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.run_table.setMinimumHeight(140)
        _fit_columns(self.run_table, stretch_column=len(_RUN_COLUMNS) - 1)
        self.run_table.itemSelectionChanged.connect(self._on_run_selection_changed)
        run_layout.addWidget(self.run_table, 1)
        self.run_empty_hint = QLabel("尚无历史运行：完成一次测量后运行记录会出现在这里。")
        self.run_empty_hint.setObjectName("emptyHint")
        run_layout.addWidget(self.run_empty_hint)
        run_actions = QHBoxLayout()
        run_actions.setSpacing(10)
        self.delete_runs_button = QPushButton("删除选中的运行")
        set_button_variant(self.delete_runs_button, "danger")
        self.delete_runs_button.clicked.connect(self.delete_selected_runs)
        run_actions.addWidget(self.delete_runs_button)
        run_actions.addStretch(1)
        run_refresh_button = QPushButton("刷新历史运行")
        set_button_variant(run_refresh_button, "secondary")
        run_refresh_button.clicked.connect(self.refresh_run_list)
        run_actions.addWidget(run_refresh_button)
        run_layout.addLayout(run_actions)
        layout.addWidget(run_group, 1)

        action_group = QGroupBox("交付产物")
        action_layout = QHBoxLayout(action_group)
        action_layout.setSpacing(10)
        self.regenerate_button = QPushButton("补生成未生成的产物")
        self.regenerate_button.setToolTip(
            "使用已保存的正式结果补生成 CSV 或测试报告，不会重跑测量"
        )
        set_button_variant(self.regenerate_button, "primary")
        self.regenerate_button.clicked.connect(self.regenerate_failed_artifacts)
        action_layout.addWidget(self.regenerate_button)
        action_layout.addStretch(1)
        layout.addWidget(action_group)

    # -- 生命周期 -----------------------------------------------------------

    def on_activated(self):
        """页面可见时刷新运行列表：历史运行可能在别处变化。"""
        self.refresh()

    def refresh(self):
        """刷新历史运行列表。

        测量可能在别的页签完成，所以完成信号与页面激活都会走这里。读取失败由
        :meth:`_load_run_rows` 处理：清空表格、丢弃视图模型里的陈旧行并记日志，
        刷新失败不能让完成信号或切换标签页抛异常。表格填好之后只调用一次
        :meth:`render_state`。
        """
        rows = self._load_run_rows()
        self.render_state()
        return rows

    # -- 状态渲染 -----------------------------------------------------------

    def render_state(
        self, state: RunHistoryPageState | None = None
    ) -> RunHistoryPageState:
        """页面可见状态的唯一渲染入口：空表说明与运行动作按钮都在这里更新。

        不传 ``state`` 时先把表格的当前行同步给视图模型，再从表格与视图模型取
        当前快照（越界选择会一并清空），因此在刷新、选中变化与读取失败之后都走同一条
        渲染路径；传入显式快照时只按键值渲染，不动选中状态。以后增加淡入、禁用或局部
        过渡时只改这一处，不再分散到各个回调里。
        """
        if state is None:
            self._sync_run_selection()
            state = self._snapshot_state()
        elif not isinstance(state, RunHistoryPageState):
            raise TypeError("页面渲染必须使用 RunHistoryPageState")
        self.run_empty_hint.setVisible(state.run_list_empty)
        self.regenerate_button.setEnabled(state.can_regenerate)
        deletion_ready = self._deletion_service_ready()
        self.delete_runs_button.setEnabled(state.can_delete and deletion_ready)
        return state

    def _deletion_service_ready(self) -> bool:
        """历史运行服务是否装配了删除入口；没有时只禁用删除动作，不影响列表。"""
        return self.run_service is not None and callable(
            getattr(self.run_service, "delete_runs", None)
        )

    def _sync_run_selection(self) -> None:
        """把运行表格的多选写回视图模型；空表或越界行会清空选择。"""
        selection = self.run_table.selectionModel()
        indexes = () if selection is None else selection.selectedRows()
        self.history.select_runs(index.row() for index in indexes)
        # 单运行动作（补生成）跟随当前行：只有当前行确实在多选里时才推进，
        # 避免把用户为删除而做的多选收敛掉。
        current = self.run_table.currentRow()
        if current in self.history.selected_run_indices:
            self.history.select_run(current)

    def _snapshot_state(self) -> RunHistoryPageState:
        """从当前表格与视图模型推出可见状态快照，本身不修改任何状态。"""
        return RunHistoryPageState(
            run_count=self.run_table.rowCount(),
            selected_run=self.history.selected_run,
            selected_runs=self.history.selected_runs,
        )

    # -- 列表 ---------------------------------------------------------------

    def refresh_run_list(self):
        """重新读取历史运行摘要并刷新表格，然后渲染一次页面状态。"""
        rows = self._load_run_rows()
        self.render_state()
        return rows

    def _load_run_rows(self):
        """读取历史运行摘要并填表；失败时清空表格与陈旧摘要并返回空元组。"""
        if self.history.run_source is None:
            _clear_table(self.run_table)
            return ()
        try:
            rows = self.history.reload_runs()
        except (OSError, ValueError, RuntimeError) as error:
            _clear_table(self.run_table)
            # 表格已空，视图模型里的旧摘要与选中运行也必须一起丢弃，避免页面按
            # 用户已经看不见的运行启用动作。
            self.history.reset_runs()
            self.log_callback(f"读取历史运行失败: {error}")
            return ()
        # 批量填表时不逐行触发选中变化：一次读取只产生一次状态渲染。
        with QSignalBlocker(self.run_table):
            self.run_table.setRowCount(0)
            for index, row in enumerate(rows):
                self.run_table.insertRow(index)
                for column, text in enumerate(_run_row_texts(row)):
                    _set_cell(self.run_table, index, column, text)
        return rows

    def _on_run_selection_changed(self):
        """选中运行变化时只推进状态：按钮是否可点由 :meth:`render_state` 统一决定。"""
        self.render_state()

    def selected_run_row(self) -> RunHistoryRow | None:
        """把运行表格选中行交给视图模型；未选中时提示并返回 ``None``。"""
        row = self.history.select_run(self.run_table.currentRow())
        if row is None:
            QMessageBox.warning(self, "历史运行", "请先选择一次历史运行")
        return row

    # -- 补生成失败产物 -----------------------------------------------------

    def regenerate_failed_artifacts(self):
        """对选中的历史运行补生成未生成的交付产物（写回原运行目录）。"""
        row = self.selected_run_row()
        if row is None:
            return False
        if not row.can_regenerate:
            QMessageBox.warning(
                self,
                "无法补生成",
                f"运行 {row.run_id} 没有待补生成的交付产物"
                f"（当前产物状态：{row.artifact_text}）",
            )
            return False
        if self.run_service is None:
            QMessageBox.warning(self, "无法补生成", "未装配历史运行服务")
            return False
        try:
            outcome = self.run_service.regenerate_failed_artifacts(row.run_id)
            message = self._regeneration_message(outcome)
        except (OSError, ValueError, RuntimeError) as error:
            self.log_callback(f"补生成交付产物失败: {error}")
            QMessageBox.warning(self, "补生成失败", str(error))
            return False
        self.log_callback(message)
        QMessageBox.information(self, "补生成结果", message)
        self.refresh()
        return True

    @staticmethod
    def _regeneration_message(outcome: ArchivalOutcome) -> str:
        """把结构化归档结果写成用户可见文案。"""
        if not isinstance(outcome, ArchivalOutcome):
            raise TypeError("历史运行服务必须返回 ArchivalOutcome")
        parts = []
        for artifact in outcome.artifacts:
            label = artifact_kind_text(artifact.kind)
            if artifact.path is not None:
                parts.append(f"{label} 已生成: {artifact.path}")
            elif artifact.reason:
                parts.append(f"{label} 未生成: {artifact.reason}")
            else:
                parts.append(f"{label} 不适用")
        return f"运行 {outcome.run_id} 的交付产物：" + "；".join(parts)

    # -- 删除历史运行 ---------------------------------------------------------

    def delete_selected_runs(self) -> bool:
        """删除多选中的历史运行（整目录删除，进行中的运行不在可删范围）。

        页面只负责：确认多选、生成用户文案、调用应用层删除用例并刷新列表。
        运行标识核对、进行中复核与目录边界都由应用层与删除适配器完成。
        """
        rows = self.history.selected_runs
        if not rows:
            QMessageBox.warning(self, "删除历史运行", "请先选择要删除的历史运行")
            return False
        deletable = tuple(row for row in rows if row.can_delete)
        blocked = tuple(row for row in rows if not row.can_delete)
        if not deletable:
            QMessageBox.warning(
                self,
                "无法删除",
                "选中的运行正在进行中，不能删除；请等待运行结束后再试",
            )
            return False
        if not self._deletion_service_ready():
            QMessageBox.warning(self, "无法删除", "未装配历史运行删除入口")
            return False
        if not self._confirm_deletion(deletable, blocked):
            return False
        try:
            outcome = self.run_service.delete_runs(
                [row.run_id for row in deletable]
            )
        except (OSError, ValueError, RuntimeError) as error:
            self.log_callback(f"删除历史运行失败: {error}")
            QMessageBox.warning(self, "删除失败", str(error))
            return False
        message = self._deletion_message(outcome, blocked)
        self.log_callback(message)
        if outcome.has_failures:
            QMessageBox.warning(self, "删除结果", message)
        else:
            QMessageBox.information(self, "删除结果", message)
        self.refresh()
        return True

    def _confirm_deletion(
        self,
        deletable: tuple[RunHistoryRow, ...],
        blocked: tuple[RunHistoryRow, ...],
    ) -> bool:
        """删除前的普通确认框：列出将删除的运行与不会删除的进行中运行。"""
        lines = ["将永久删除以下运行及其全部结果与交付产物："]
        lines.extend(
            f"- {row.run_id}（{row.created_text}，{row.status_text}）"
            for row in deletable
        )
        if blocked:
            lines.append("")
            lines.append("以下运行正在进行中，不会删除：")
            lines.extend(f"- {row.run_id}" for row in blocked)
        lines.append("")
        lines.append("此操作不可恢复，是否继续？")
        answer = QMessageBox.question(
            self,
            "删除历史运行",
            "\n".join(lines),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    @staticmethod
    def _deletion_message(
        outcome: RunDeletionOutcome,
        blocked: tuple[RunHistoryRow, ...] = (),
    ) -> str:
        """把结构化删除结果写成用户可见文案。"""
        if not isinstance(outcome, RunDeletionOutcome):
            raise TypeError("历史运行服务必须返回 RunDeletionOutcome")
        parts = []
        if outcome.deleted_run_ids:
            parts.append("已删除运行: " + "、".join(outcome.deleted_run_ids))
        for failure in outcome.failures:
            parts.append(f"运行 {failure.run_id} 未删除: {failure.reason}")
        if blocked:
            parts.append(
                "进行中未删除: " + "、".join(row.run_id for row in blocked)
            )
        if not parts:
            parts.append("没有删除任何运行")
        return "；".join(parts)


def _fit_columns(table: QTableWidget, *, stretch_column: int) -> None:
    """按内容排列表头，并让指定列吃掉剩余宽度（文件名/说明这类长短不一的文本列）。"""
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeToContents)
    header.setSectionResizeMode(stretch_column, QHeaderView.Stretch)


def _set_cell(table: QTableWidget, row: int, column: int, text: str) -> None:
    """写入一个单元格；完整文本同时放进 tooltip，列宽不够时仍可查看。"""
    item = QTableWidgetItem(text)
    # 双重保险：即使将来误开了编辑触发器，条目本身也不接受就地编辑。
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    item.setToolTip(text)
    table.setItem(row, column, item)


def _clear_table(table: QTableWidget) -> None:
    """清空表格且不触发选中变化：调用方随后只渲染一次状态。"""
    with QSignalBlocker(table):
        table.setRowCount(0)


def _run_row_texts(row: RunHistoryRow) -> tuple[str, ...]:
    """把一条运行摘要排成表格文本；顺序与 :data:`_RUN_COLUMNS` 一致。"""
    return (
        row.run_id,
        row.created_text,
        row.status_text,
        row.stage_text,
        row.result_text,
        row.artifact_text,
        row.problem_text,
    )


def build_run_history_page(
    gui_data_service: GuiDataService,
    *,
    log_callback: Callable[[str], Any] | None = None,
    parent=None,
) -> RunHistoryPage:
    """按 GUI 数据门面装配历史运行页。

    窗口只做装配：页面需要的运行摘要/重新生成依赖来自同一个门面，页面自己持有视图
    模型，窗口不再逐项拼装页面内部结构。历史结果浏览、报告查看与另存为属于数据
    可视化页，页面不再提供打开运行目录动作。
    """
    run_service = gui_data_service.run_history
    return RunHistoryPage(
        history=HistoryViewModel(runs=run_service),
        run_service=run_service,
        log_callback=log_callback,
        parent=parent,
    )


__all__ = ["RunHistoryPage", "build_run_history_page"]
