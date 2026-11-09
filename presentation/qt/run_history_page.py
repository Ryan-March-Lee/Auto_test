"""历史运行与结果管理页面（重构计划步骤 7、步骤 10 收口）。

正常测试流程已经自动归档结果，用户不需要先来这里选择文件再手动导出。本页面只承担
**历史管理**，页面类名、路由与模块名都不再沿用“导出页”语义：

- 上半部分列出历史运行，并显示每次运行的阶段进度、正式结果数与交付产物状态；
- 下半部分列出历史结果，作为“另存为”的数据来源；
- 辅助动作只保留两类：对失败产物执行“重新生成”（写回原运行目录），以及对单个结果
  执行“另存为”（JSON/CSV/测试报告副本，由用户在菜单里选择格式）。

页面职责边界：

- 列表与状态全部来自只读历史视图模型（``HistoryViewModel`` + 应用层运行摘要），页面
  不读 manifest、不拼接运行目录路径，也不判断文件是否存在。
- 重新生成走应用层的 ``RunHistoryServicePort``：页面只传“已列出的运行标识”，不传阶段、
  不传路径；应用层决定用哪个阶段重试，并把产物写回受控位置。
- 导出走 ``ResultExportServicePort``：页面只构造 ``ExportRequest``，并在导出前按
  **结果所属的运行**核对可用性（不能只看运行表格的选中行）。
- 用户可见文案属于本页面：应用层返回结构化结果（``ArchivalOutcome`` /
  ``ExportOutcome``），页面据此生成日志与对话框文本。
- 页面可见状态只有一个渲染入口：刷新、选中变化与读取失败都只更新表格数据，再由
  ``render_state()`` 按只读的 ``RunHistoryPageState`` 一次渲染空表说明与按钮可用性。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QSignalBlocker, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from application.dto.archival import ArchivalOutcome
from application.dto.export_requests import (
    ExportFormat,
    ExportOutcome,
    ExportRequest,
)
from application.ports.gui_data_service import (
    GuiDataService,
    ResultExportServicePort,
    RunHistoryServicePort,
)
from domain.run.models import ArtifactKind

from .history_view_model import (
    HistoryRow,
    HistoryViewModel,
    RunHistoryRow,
    artifact_kind_text,
)
from .theme import set_button_variant

_EXPORT_FILTERS = {
    ExportFormat.JSON: "JSON files (*.json)",
    ExportFormat.CSV: "CSV files (*.csv)",
}
# “另存为”菜单：一个入口覆盖 JSON、CSV 与测试报告副本，具体格式由用户选择。
_SAVE_AS_ACTIONS = (
    ("JSON…", ExportFormat.JSON),
    ("CSV…", ExportFormat.CSV),
    ("测试报告…", ExportFormat.REPORT),
)
# 历史运行表格的列顺序；页面只按视图模型给出的文本填表。
_RUN_COLUMNS = ("运行", "创建时间", "状态", "阶段", "正式结果", "交付产物", "说明")


def _save_dialog_title(export_format: ExportFormat) -> str:
    """“另存为”文件对话框标题；测试报告不使用文件对话框。"""
    if export_format is ExportFormat.JSON:
        return "另存结果"
    if export_format is ExportFormat.CSV:
        return "另存 CSV"
    raise ValueError(f"该格式不使用文件对话框: {export_format}")


def _save_action_text(export_format: ExportFormat) -> str:
    """日志里区分“另存为”和“生成报告副本”，避免文案与操作不符。"""
    if export_format is ExportFormat.REPORT:
        return "测试报告副本生成"
    return f"{export_format.label} 另存为"


@dataclass(frozen=True)
class RunHistoryPageState:
    """历史管理页可见状态的只读快照。

    页面不再在刷新、选中与读取失败分支里各自调用 ``setVisible`` / ``setEnabled``：
    这些分支只更新两张表格的数据，再交给 :meth:`RunHistoryPage.render_state` 一次渲染。
    状态对象因此只保留驱动外观所需的事实（行数与当前选中的运行），行内展示文本、
    “能否重新生成”与“能否打开目录/报告”仍由视图模型给出。以后增加淡入、禁用或
    局部过渡时只改渲染入口，不需要追踪每个回调。
    """

    run_count: int = 0
    result_count: int = 0
    selected_run: RunHistoryRow | None = None

    def __post_init__(self) -> None:
        for name in ("run_count", "result_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} 必须是非负整数，收到: {value!r}")
        if self.selected_run is not None and not isinstance(
            self.selected_run, RunHistoryRow
        ):
            raise TypeError("selected_run 必须是 RunHistoryRow 或 None")

    @property
    def run_list_empty(self) -> bool:
        """历史运行表格是否没有内容（决定空表说明是否显示）。"""
        return self.run_count == 0

    @property
    def result_list_empty(self) -> bool:
        """历史结果表格是否没有内容（决定空表说明是否显示）。"""
        return self.result_count == 0

    @property
    def can_regenerate(self) -> bool:
        """选中的运行是否有可重新生成的失败产物。"""
        return self.selected_run is not None and self.selected_run.can_regenerate

    @property
    def can_open_directory(self) -> bool:
        """选中的运行是否允许打开运行目录。"""
        return self.selected_run is not None and self.selected_run.can_open_directory

    @property
    def can_open_report(self) -> bool:
        """选中的运行是否已生成可打开的测试报告。"""
        return self.selected_run is not None and self.selected_run.can_open_report


class RunHistoryPage(QWidget):
    """历史运行/结果管理页：运行与产物状态、失败产物重新生成、结果另存为。"""

    def __init__(
        self,
        *,
        history: HistoryViewModel,
        export_service: ResultExportServicePort,
        run_service: RunHistoryServicePort | None = None,
        log_callback: Callable[[str], Any] | None = None,
        parent=None,
    ):
        if not isinstance(history, HistoryViewModel):
            raise TypeError("历史管理页必须使用只读 HistoryViewModel")
        if not callable(getattr(export_service, "execute", None)):
            raise TypeError("导出服务必须提供 execute(request) 能力")
        if run_service is not None and not callable(
            getattr(run_service, "regenerate_failed_artifacts", None)
        ):
            raise TypeError("历史运行服务必须提供 regenerate_failed_artifacts(run_id)")
        super().__init__(parent)
        self.history = history
        self.export_service = export_service
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
        # 历史运行是只读展示面板：用户只能选择、查看与另存为，不能就地改写词条。
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
        self.open_run_dir_button = QPushButton("打开运行目录")
        set_button_variant(self.open_run_dir_button, "secondary")
        self.open_run_dir_button.clicked.connect(self.open_run_directory)
        run_actions.addWidget(self.open_run_dir_button)
        self.open_report_button = QPushButton("打开测试报告")
        set_button_variant(self.open_report_button, "secondary")
        self.open_report_button.clicked.connect(self.open_test_report)
        run_actions.addWidget(self.open_report_button)
        run_actions.addStretch(1)
        run_refresh_button = QPushButton("刷新历史运行")
        set_button_variant(run_refresh_button, "secondary")
        run_refresh_button.clicked.connect(self.refresh_run_list)
        run_actions.addWidget(run_refresh_button)
        run_layout.addLayout(run_actions)
        layout.addWidget(run_group, 1)

        result_group = QGroupBox("历史结果")
        result_layout = QVBoxLayout(result_group)
        result_layout.setSpacing(8)
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(4)
        self.result_table.setHorizontalHeaderLabels(["文件名", "类型", "来源", "修改时间"])
        self.result_table.verticalHeader().setVisible(False)
        self.result_table.setAlternatingRowColors(True)
        self.result_table.setSelectionBehavior(QTableWidget.SelectRows)
        # 历史结果同样只用于查看与另存为，不提供单元格编辑。
        self.result_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.result_table.setMinimumHeight(140)
        result_header = self.result_table.horizontalHeader()
        result_header.setSectionResizeMode(0, QHeaderView.Stretch)
        # 类型/来源/修改时间按固定宽度显示，文件名吃掉剩余宽度。
        for column, width in ((1, 110), (2, 110), (3, 170)):
            result_header.setSectionResizeMode(column, QHeaderView.Fixed)
            self.result_table.setColumnWidth(column, width)
        result_layout.addWidget(self.result_table, 1)
        self.result_empty_hint = QLabel("尚无历史结果：测量完成归档后结果会出现在这里。")
        self.result_empty_hint.setObjectName("emptyHint")
        result_layout.addWidget(self.result_empty_hint)
        result_actions = QHBoxLayout()
        result_actions.addStretch(1)
        result_refresh_button = QPushButton("刷新结果列表")
        set_button_variant(result_refresh_button, "secondary")
        result_refresh_button.clicked.connect(self.refresh_result_list)
        result_actions.addWidget(result_refresh_button)
        result_layout.addLayout(result_actions)
        layout.addWidget(result_group, 1)

        action_group = QGroupBox("历史操作")
        action_layout = QHBoxLayout(action_group)
        action_layout.setSpacing(10)
        self.regenerate_button = QPushButton("重新生成失败的产物")
        set_button_variant(self.regenerate_button, "primary")
        self.regenerate_button.clicked.connect(self.regenerate_failed_artifacts)
        action_layout.addWidget(self.regenerate_button)
        self.save_as_button = QPushButton("另存为…")
        set_button_variant(self.save_as_button, "secondary")
        self.save_as_button.setToolTip(
            "将选中的历史结果另存为 JSON、CSV 或测试报告副本"
        )
        self.save_as_menu = QMenu(self.save_as_button)
        for text, export_format in _SAVE_AS_ACTIONS:
            action = self.save_as_menu.addAction(text)
            action.triggered.connect(
                lambda _checked=False, value=export_format: self.export(value)
            )
        self.save_as_button.setMenu(self.save_as_menu)
        action_layout.addWidget(self.save_as_button)
        action_layout.addStretch(1)
        layout.addWidget(action_group)

    # -- 生命周期 -----------------------------------------------------------

    def on_activated(self):
        """页面可见时刷新两份列表：历史运行与结果都可能在别处变化。"""
        self.refresh()

    def refresh(self):
        """刷新历史运行与历史结果列表。

        测量可能在别的页签完成，所以完成信号与页面激活都会走这里；两张表必须一起
        更新，否则运行状态、阶段与交付产物状态会停在测量前的快照。读取失败只由
        各自的 ``_load_*_rows()`` 处理：清空该表、丢弃视图模型里的陈旧行并记日志，
        刷新失败不能让完成信号或切换标签页抛异常。两张表都填好之后只调用一次
        :meth:`render_state`。
        """
        rows = self._load_run_rows()
        results = self._load_result_rows()
        self.render_state()
        return rows, results

    # -- 状态渲染 -----------------------------------------------------------

    def render_state(
        self, state: RunHistoryPageState | None = None
    ) -> RunHistoryPageState:
        """页面可见状态的唯一渲染入口：空表说明与运行动作按钮都在这里更新。

        不传 ``state`` 时先把两张表格的当前行同步给视图模型，再从表格与视图模型取
        当前快照（越界选择会一并清空），因此在刷新、选中变化与读取失败之后都走同一条
        渲染路径；传入显式快照时只按键值渲染，不动选中状态。以后增加淡入、禁用或局部
        过渡时只改这一处，不再分散到各个回调里。
        """
        if state is None:
            self._sync_selection_from_tables()
            state = self._snapshot_state()
        elif not isinstance(state, RunHistoryPageState):
            raise TypeError("页面渲染必须使用 RunHistoryPageState")
        self.run_empty_hint.setVisible(state.run_list_empty)
        self.result_empty_hint.setVisible(state.result_list_empty)
        self.regenerate_button.setEnabled(state.can_regenerate)
        location_ready = self._location_service_ready()
        self.open_run_dir_button.setEnabled(state.can_open_directory and location_ready)
        self.open_report_button.setEnabled(state.can_open_report and location_ready)
        return state

    def _location_service_ready(self) -> bool:
        """历史运行服务是否装配了位置入口；没有时只禁用打开动作，不影响列表与导出。"""
        return self.run_service is not None and callable(
            getattr(self.run_service, "run_directory", None)
        ) and callable(getattr(self.run_service, "artifact_location", None))

    def _sync_selection_from_tables(self) -> None:
        """把两张表格的当前行写回视图模型；空表或越界行会清空对应选择。"""
        self.history.select_run(self.run_table.currentRow())
        self.history.select(self.result_table.currentRow())

    def _snapshot_state(self) -> RunHistoryPageState:
        """从当前表格与视图模型推出可见状态快照，本身不修改任何状态。"""
        return RunHistoryPageState(
            run_count=self.run_table.rowCount(),
            result_count=self.result_table.rowCount(),
            selected_run=self.history.selected_run,
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
            # 表格已空，视图模型里的旧摘要与选中运行也必须一起丢弃：否则导出仍会
            # 按照用户已经看不见的运行做可用性判定。
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

    def refresh_result_list(self):
        """重新读取只读历史结果列表并刷新表格，然后渲染一次页面状态。"""
        rows = self._load_result_rows()
        self.render_state()
        return rows

    def _load_result_rows(self):
        """读取历史结果并填表；失败时清空表格与陈旧行并返回空元组。

        失败处理和 :meth:`_load_run_rows` 一致，因此 ``refresh()`` 与单独的
        ``refresh_result_list()`` 走同一条路径，不会有一个抛异常、另一个只记日志。
        """
        try:
            rows = self.history.reload()
        except (OSError, ValueError, RuntimeError) as error:
            _clear_table(self.result_table)
            self.history.reset_results()
            self.log_callback(f"读取历史结果失败: {error}")
            return ()
        with QSignalBlocker(self.result_table):
            self.result_table.setRowCount(0)
            for index, row in enumerate(rows):
                self.result_table.insertRow(index)
                _set_cell(self.result_table, index, 0, row.filename)
                _set_cell(self.result_table, index, 1, row.file_type)
                _set_cell(self.result_table, index, 2, row.source_label)
                _set_cell(self.result_table, index, 3, row.modified_text)
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

    def selected_row(self) -> HistoryRow | None:
        """把结果表格选中行交给视图模型；未选中时提示并返回 ``None``。"""
        row = self.history.select(self.result_table.currentRow())
        if row is None:
            QMessageBox.warning(self, "另存为", "请先选择要另存的历史结果")
        return row

    # -- 打开运行目录 / 测试报告 --------------------------------------------

    def open_run_directory(self) -> bool:
        """打开选中历史运行的目录；路径由应用层位置入口解析。"""
        row = self.selected_run_row()
        if row is None:
            return False
        if not row.can_open_directory:
            QMessageBox.warning(
                self,
                "无法打开",
                f"运行 {row.run_id} {row.state_text}，不能打开运行目录",
            )
            return False
        return self._open_location(row.run_id, None, "运行目录")

    def open_test_report(self) -> bool:
        """打开选中历史运行已生成的测试报告。"""
        row = self.selected_run_row()
        if row is None:
            return False
        if not row.can_open_report:
            QMessageBox.warning(
                self,
                "无法打开",
                f"运行 {row.run_id} 没有已生成的测试报告",
            )
            return False
        return self._open_location(row.run_id, ArtifactKind.REPORT, "测试报告")

    def _open_location(self, run_id: str, kind: ArtifactKind | None, label: str) -> bool:
        """把应用层给出的本地位置交给系统打开；页面不拼路径、不判断文件。"""
        service = self.run_service
        if not self._location_service_ready():
            self.log_callback(f"未装配历史运行位置入口，无法打开{label}")
            QMessageBox.warning(self, "无法打开", f"未装配历史运行位置入口，无法打开{label}")
            return False
        try:
            if kind is None:
                target = service.run_directory(run_id)
            else:
                target = service.artifact_location(run_id, kind)
        except (OSError, ValueError, RuntimeError) as error:
            self.log_callback(f"无法定位{label}: {error}")
            QMessageBox.warning(self, "无法打开", f"无法定位{label}: {error}")
            return False
        if not isinstance(target, str) or not target.strip():
            self.log_callback(f"运行 {run_id} 没有可打开的{label}")
            QMessageBox.warning(self, "无法打开", f"运行 {run_id} 没有可打开的{label}")
            return False
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(target)):
            self.log_callback(f"系统无法打开{label}: {target}")
            QMessageBox.warning(self, "无法打开", f"系统无法打开{label}: {target}")
            return False
        self.log_callback(f"已打开{label}: {target}")
        return True

    # -- 重新生成失败产物 ---------------------------------------------------

    def regenerate_failed_artifacts(self):
        """对选中的历史运行重新生成失败的交付产物（写回原运行目录）。"""
        row = self.selected_run_row()
        if row is None:
            return False
        if not row.can_regenerate:
            QMessageBox.warning(
                self,
                "无法重新生成",
                f"运行 {row.run_id} 没有可重新生成的失败产物"
                f"（当前产物状态：{row.artifact_text}）",
            )
            return False
        if self.run_service is None:
            QMessageBox.warning(self, "无法重新生成", "未装配历史运行服务")
            return False
        try:
            outcome = self.run_service.regenerate_failed_artifacts(row.run_id)
            message = self._regeneration_message(outcome)
        except (OSError, ValueError, RuntimeError) as error:
            self.log_callback(f"重新生成失败: {error}")
            QMessageBox.warning(self, "重新生成失败", str(error))
            return False
        self.log_callback(message)
        QMessageBox.information(self, "重新生成结果", message)
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

    # -- 另存为 -------------------------------------------------------------

    def export(self, export_format: ExportFormat):
        """按选定格式另存当前历史结果；页面只构造导出请求。"""
        row = self.selected_row()
        if row is None:
            return False
        blocker = self._export_blocker(row)
        if blocker is not None:
            QMessageBox.warning(self, "无法另存为", blocker)
            return False
        target = None
        if export_format.needs_target:
            target, _ = QFileDialog.getSaveFileName(
                self,
                _save_dialog_title(export_format),
                row.suggested_filename(export_format.suffix),
                _EXPORT_FILTERS[export_format],
            )
            if not target:
                return False
        try:
            request = ExportRequest(
                result_id=row.result_id, format=export_format, target=target
            )
            outcome = self.export_service.execute(request)
            message = self._success_message(outcome)
        except Exception as error:  # noqa: BLE001 - 另存失败必须让用户看到原因
            self.log_callback(f"{_save_action_text(export_format)}失败: {error}")
            QMessageBox.warning(self, "另存为失败", str(error))
            return False
        self.log_callback(message)
        QMessageBox.information(self, "另存为成功", message)
        return True

    def _export_blocker(self, row: HistoryRow) -> str | None:
        """另存为的运行级前置条件；返回 ``None`` 表示可以另存为。

        判定绑定**结果所属的运行**（``HistoryRow.run_id``），而不是只看运行表格的当前
        选中行：两张表格可以各选一行，只看选中行会让“界面说运行不可用、实际另存另一
        次运行的结果”成为可能。三种情况拒绝另存为：

        1. 运行表格里选中了不可用的运行：当前上下文本身就不可另存为；
        2. 结果所属运行不在历史运行列表里（含未登记运行的结果）：无法确认可用性；
        3. 结果所属运行与选中运行不是同一次运行：避免用户以为自己另存了选中的那一次。

        未装配运行摘要来源时（例如可视化页复用本页面的结果列表）只保留第 1 项的
        基础判定，其余交给导出用例与历史查询端口。
        """
        selected = self.history.selected_run
        if selected is not None and not selected.browsable:
            return f"运行 {selected.run_id} {selected.state_text}，不能另存其结果"
        if self.history.run_source is None:
            return None
        if row.run_id is None:
            return "所选结果没有登记到运行目录，不能另存为副本"
        owner = self.history.run_row(row.run_id)
        if owner is None:
            return f"运行 {row.run_id} 不在历史运行列表中，请刷新后重试"
        if not owner.browsable:
            return f"运行 {owner.run_id} {owner.state_text}，不能另存其结果"
        if selected is not None and selected.run_id != owner.run_id:
            return (
                f"所选结果属于运行 {owner.run_id}，与当前选中的运行 "
                f"{selected.run_id} 不是同一次运行；请选择该运行自己的结果再另存为"
            )
        return None

    @staticmethod
    def _success_message(outcome: ExportOutcome) -> str:
        """把结构化导出结果写成用户可见文案。"""
        if not isinstance(outcome, ExportOutcome):
            raise TypeError("导出服务必须返回 ExportOutcome")
        if outcome.format is ExportFormat.REPORT:
            return f"测试报告副本已生成: {outcome.artifact_path}"
        if outcome.format is ExportFormat.JSON:
            return f"结果已另存为: {outcome.artifact_path}"
        return f"CSV 已另存为: {outcome.artifact_path}"


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
    """按 GUI 数据门面装配历史管理页。

    窗口只做装配：页面需要的三份依赖（结果查询、导出用例、运行摘要/重新生成）都来自
    同一个门面，页面自己持有视图模型，窗口不再逐项拼装页面内部结构。
    """
    run_service = gui_data_service.run_history
    return RunHistoryPage(
        history=HistoryViewModel(gui_data_service.results, runs=run_service),
        export_service=gui_data_service.export_requests,
        run_service=run_service,
        log_callback=log_callback,
        parent=parent,
    )


__all__ = ["RunHistoryPage", "build_run_history_page"]
