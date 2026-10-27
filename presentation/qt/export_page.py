"""结果文件列表和导出页面。

页面只做两件事：把只读历史视图模型的行显示成表格，以及把用户的导出意图写成
:class:`application.dto.export_requests.ExportRequest`。列表来自只读历史查询端口，
导出由 application 导出用例执行；页面不读写任何结果文件路径，也不解析结果文档。

用户可见文案属于本页面：应用层只返回结构化的 :class:`ExportOutcome`（格式、来源
文件名、实际产物位置），页面据此生成日志与对话框文本。
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from application.dto.export_requests import (
    ExportFormat,
    ExportOutcome,
    ExportRequest,
)
from application.ports.gui_data_service import ResultExportServicePort

from .history_view_model import HistoryRow, HistoryViewModel

_EXPORT_FILTERS = {
    ExportFormat.JSON: "JSON files (*.json)",
    ExportFormat.CSV: "CSV files (*.csv)",
}
_BUTTONS = (
    ("export_json_btn", "导出JSON", ExportFormat.JSON),
    ("export_csv_btn", "导出CSV", ExportFormat.CSV),
    ("export_report_btn", "导出测试报告", ExportFormat.REPORT),
)


class ExportPage(QWidget):
    """封装结果文件列表以及 JSON/CSV/测试报告导出入口。"""

    def __init__(
        self,
        *,
        history: HistoryViewModel,
        export_service: ResultExportServicePort,
        log_callback: Callable[[str], Any] | None = None,
        parent=None,
    ):
        if not isinstance(history, HistoryViewModel):
            raise TypeError("导出页面必须使用只读 HistoryViewModel")
        if not callable(getattr(export_service, "execute", None)):
            raise TypeError("导出服务必须提供 execute(request) 能力")
        super().__init__(parent)
        self.history = history
        self.export_service = export_service
        self.log_callback = log_callback or (lambda _message: None)
        self._build_ui()
        self.refresh_file_list()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        file_group = QGroupBox("数据文件")
        file_layout = QVBoxLayout(file_group)
        self.file_table = QTableWidget()
        self.file_table.setColumnCount(4)
        self.file_table.setHorizontalHeaderLabels(["文件名", "类型", "来源", "修改时间"])
        file_layout.addWidget(self.file_table)
        refresh_button = QPushButton("刷新文件列表")
        refresh_button.clicked.connect(self.refresh_file_list)
        file_layout.addWidget(refresh_button)
        layout.addWidget(file_group)

        export_group = QGroupBox("导出控制")
        export_layout = QHBoxLayout(export_group)
        for attribute, text, export_format in _BUTTONS:
            button = QPushButton(text)
            button.clicked.connect(
                lambda _checked=False, value=export_format: self.export(value)
            )
            setattr(self, attribute, button)
            export_layout.addWidget(button)
        layout.addWidget(export_group)

    # -- 列表 ---------------------------------------------------------------

    def refresh_file_list(self):
        """重新读取只读历史列表并刷新表格。"""
        rows = self.history.reload()
        self.file_table.setRowCount(0)
        for index, row in enumerate(rows):
            self.file_table.insertRow(index)
            self.file_table.setItem(index, 0, QTableWidgetItem(row.filename))
            self.file_table.setItem(index, 1, QTableWidgetItem(row.file_type))
            self.file_table.setItem(index, 2, QTableWidgetItem(row.source_label))
            self.file_table.setItem(index, 3, QTableWidgetItem(row.modified_text))
        return rows

    def selected_row(self) -> HistoryRow | None:
        """把表格选中行交给视图模型；未选中时提示并返回 ``None``。"""
        row = self.history.select(self.file_table.currentRow())
        if row is None:
            QMessageBox.warning(self, "导出", "请先选择要导出的文件")
        return row

    # -- 导出 ---------------------------------------------------------------

    def export_json(self):
        return self.export(ExportFormat.JSON)

    def export_csv(self):
        return self.export(ExportFormat.CSV)

    def export_report(self):
        return self.export(ExportFormat.REPORT)

    def export(self, export_format: ExportFormat):
        """按格式导出当前选择的历史结果；页面只构造导出请求。"""
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

    @staticmethod
    def _success_message(outcome: ExportOutcome) -> str:
        """把结构化导出结果写成用户可见文案。"""
        if not isinstance(outcome, ExportOutcome):
            raise TypeError("导出服务必须返回 ExportOutcome")
        if outcome.format is ExportFormat.REPORT:
            return f"测试报告已生成: {outcome.artifact_path}"
        return f"{outcome.filename} 已导出到 {outcome.artifact_path}"


__all__ = ["ExportPage"]
