"""历史运行与结果管理页面（重构计划步骤 7）。

本页面接替原来的“数据导出”语义：正常测试流程已经自动归档结果，用户不再需要先来
这里选择文件再手动导出。页面现在只承担**历史管理**：

- 上半部分列出历史运行，并显示每次运行的阶段进度、正式结果数与交付产物状态；
- 下半部分保留历史结果列表，作为“导出副本”的数据来源；
- 辅助动作只保留两类：对失败产物执行“重新生成”（写回原运行目录），以及对单个结果
  执行“导出副本”（由用户选择目标路径）。

页面职责边界：

- 列表与状态全部来自只读历史视图模型（``HistoryViewModel`` + 应用层运行摘要），页面
  不读 manifest、不拼接运行目录路径，也不判断文件是否存在。
- 重新生成走应用层的 ``RunHistoryServicePort``：页面只传“已列出的运行标识”，不传阶段、
  不传路径；应用层决定用哪个阶段重试，并把产物写回受控位置。
- 导出走 ``ResultExportServicePort``：页面只构造 ``ExportRequest``。
- 用户可见文案属于本页面：应用层返回结构化结果（``ArchivalOutcome`` /
  ``ExportOutcome``），页面据此生成日志与对话框文本。
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
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

from .history_view_model import (
    HistoryRow,
    HistoryViewModel,
    RunHistoryRow,
    artifact_kind_text,
)

_EXPORT_FILTERS = {
    ExportFormat.JSON: "JSON files (*.json)",
    ExportFormat.CSV: "CSV files (*.csv)",
}
_BUTTONS = (
    ("export_json_btn", "导出JSON副本", ExportFormat.JSON),
    ("export_csv_btn", "导出CSV副本", ExportFormat.CSV),
    ("export_report_btn", "导出测试报告副本", ExportFormat.REPORT),
)
# 历史运行表格的列顺序；页面只按视图模型给出的文本填表。
_RUN_COLUMNS = ("运行", "创建时间", "状态", "阶段", "正式结果", "交付产物", "说明")


class ExportPage(QWidget):
    """历史运行/结果管理页：运行与产物状态、失败产物重新生成、结果导出副本。"""

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

        run_group = QGroupBox("历史运行")
        run_layout = QVBoxLayout(run_group)
        self.run_table = QTableWidget()
        self.run_table.setColumnCount(len(_RUN_COLUMNS))
        self.run_table.setHorizontalHeaderLabels(list(_RUN_COLUMNS))
        _fit_columns(self.run_table, stretch_column=len(_RUN_COLUMNS) - 1)
        self.run_table.itemSelectionChanged.connect(self._on_run_selection_changed)
        run_layout.addWidget(self.run_table)
        run_refresh_button = QPushButton("刷新历史运行")
        run_refresh_button.clicked.connect(self.refresh_run_list)
        run_layout.addWidget(run_refresh_button)
        layout.addWidget(run_group)

        file_group = QGroupBox("历史结果")
        file_layout = QVBoxLayout(file_group)
        self.file_table = QTableWidget()
        self.file_table.setColumnCount(4)
        self.file_table.setHorizontalHeaderLabels(["文件名", "类型", "来源", "修改时间"])
        _fit_columns(self.file_table, stretch_column=0)
        file_layout.addWidget(self.file_table)
        refresh_button = QPushButton("刷新结果列表")
        refresh_button.clicked.connect(self.refresh_file_list)
        file_layout.addWidget(refresh_button)
        layout.addWidget(file_group)

        action_group = QGroupBox("历史操作")
        action_layout = QHBoxLayout(action_group)
        self.regenerate_button = QPushButton("重新生成失败的产物")
        self.regenerate_button.setEnabled(False)
        self.regenerate_button.clicked.connect(self.regenerate_failed_artifacts)
        action_layout.addWidget(self.regenerate_button)
        for attribute, text, export_format in _BUTTONS:
            button = QPushButton(text)
            button.clicked.connect(
                lambda _checked=False, value=export_format: self.export(value)
            )
            setattr(self, attribute, button)
            action_layout.addWidget(button)
        layout.addWidget(action_group)

    # -- 生命周期 -----------------------------------------------------------

    def on_activated(self):
        """页面可见时刷新两份列表：历史运行与结果都可能在别处变化。"""
        self.refresh()

    def refresh(self):
        """刷新历史运行与历史结果列表；没有运行摘要来源时只刷新结果列表。"""
        rows = self.refresh_run_list()
        return rows, self.refresh_file_list()

    # -- 列表 ---------------------------------------------------------------

    def refresh_run_list(self):
        """重新读取历史运行摘要并刷新表格。"""
        if self.history.run_source is None:
            self.run_table.setRowCount(0)
            self._on_run_selection_changed()
            return ()
        rows = self.history.reload_runs()
        self.run_table.setRowCount(0)
        for index, row in enumerate(rows):
            self.run_table.insertRow(index)
            for column, text in enumerate(_run_row_texts(row)):
                _set_cell(self.run_table, index, column, text)
        self._on_run_selection_changed()
        return rows

    def refresh_file_list(self):
        """重新读取只读历史列表并刷新表格。"""
        rows = self.history.reload()
        self.file_table.setRowCount(0)
        for index, row in enumerate(rows):
            self.file_table.insertRow(index)
            _set_cell(self.file_table, index, 0, row.filename)
            _set_cell(self.file_table, index, 1, row.file_type)
            _set_cell(self.file_table, index, 2, row.source_label)
            _set_cell(self.file_table, index, 3, row.modified_text)
        return rows

    def _on_run_selection_changed(self):
        """选中运行变化时同步“重新生成”按钮：只有可重新生成的运行才可点。"""
        row = self.history.select_run(self.run_table.currentRow())
        self.regenerate_button.setEnabled(row is not None and row.can_regenerate)

    def selected_run_row(self) -> RunHistoryRow | None:
        """把运行表格选中行交给视图模型；未选中时提示并返回 ``None``。"""
        row = self.history.select_run(self.run_table.currentRow())
        if row is None:
            QMessageBox.warning(self, "历史运行", "请先选择一次历史运行")
        return row

    def selected_row(self) -> HistoryRow | None:
        """把表格选中行交给视图模型；未选中时提示并返回 ``None``。"""
        row = self.history.select(self.file_table.currentRow())
        if row is None:
            QMessageBox.warning(self, "导出副本", "请先选择要导出的结果")
        return row

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

    # -- 导出副本 -----------------------------------------------------------

    def export_json(self):
        return self.export(ExportFormat.JSON)

    def export_csv(self):
        return self.export(ExportFormat.CSV)

    def export_report(self):
        return self.export(ExportFormat.REPORT)

    def export(self, export_format: ExportFormat):
        """按格式导出当前选择的历史结果；页面只构造导出请求。"""
        if not self._selected_run_allows_export():
            return False
        row = self.selected_row()
        if row is None:
            return False
        target = None
        if export_format.needs_target:
            target, _ = QFileDialog.getSaveFileName(
                self,
                f"保存{export_format.label}文件",
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
        except Exception as error:  # noqa: BLE001 - 导出失败必须让用户看到原因
            self.log_callback(f"{export_format.label}导出失败: {error}")
            QMessageBox.warning(self, "导出失败", str(error))
            return False
        self.log_callback(message)
        QMessageBox.information(self, "导出成功", message)
        return True

    def _selected_run_allows_export(self) -> bool:
        """选中的历史运行不可用时拒绝导出；未选中运行时不额外拦截。"""
        run_row = self.history.selected_run
        if run_row is None or run_row.browsable:
            return True
        QMessageBox.warning(
            self,
            "无法导出",
            f"运行 {run_row.run_id} {run_row.state_text}，不能导出其结果",
        )
        return False

    @staticmethod
    def _success_message(outcome: ExportOutcome) -> str:
        """把结构化导出结果写成用户可见文案。"""
        if not isinstance(outcome, ExportOutcome):
            raise TypeError("导出服务必须返回 ExportOutcome")
        if outcome.format is ExportFormat.REPORT:
            return f"测试报告已生成副本: {outcome.artifact_path}"
        return f"{outcome.filename} 已导出到 {outcome.artifact_path}（副本）"


def _fit_columns(table: QTableWidget, *, stretch_column: int) -> None:
    """按内容排列表头，并让指定列吃掉剩余宽度（文件名/说明这类长短不一的文本列）。"""
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeToContents)
    header.setSectionResizeMode(stretch_column, QHeaderView.Stretch)


def _set_cell(table: QTableWidget, row: int, column: int, text: str) -> None:
    """写入一个单元格；完整文本同时放进 tooltip，列宽不够时仍可查看。"""
    item = QTableWidgetItem(text)
    item.setToolTip(text)
    table.setItem(row, column, item)


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


def build_history_page(
    gui_data_service: GuiDataService,
    *,
    log_callback: Callable[[str], Any] | None = None,
    parent=None,
) -> ExportPage:
    """按 GUI 数据门面装配历史管理页。

    窗口只做装配：页面需要的三份依赖（结果查询、导出用例、运行摘要/重新生成）都来自
    同一个门面，页面自己持有视图模型，窗口不再逐项拼装页面内部结构。
    """
    run_service = gui_data_service.run_history
    return ExportPage(
        history=HistoryViewModel(gui_data_service.results, runs=run_service),
        export_service=gui_data_service.export_requests,
        run_service=run_service,
        log_callback=log_callback,
        parent=parent,
    )


__all__ = ["ExportPage", "build_history_page"]
