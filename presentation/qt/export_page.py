"""结果文件列表和离线导出页面。"""

from __future__ import annotations

from typing import Any, Callable

from application.ports.gui_data_service import ResultFileDto

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


class ExportPage(QWidget):
    """封装结果文件列表以及 JSON/CSV/PDF 导出操作。"""

    def __init__(
        self,
        *,
        result_service,
        log_callback: Callable[[str], Any] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.result_service = result_service
        self.log_callback = log_callback or (lambda _message: None)
        self._row_files: list[ResultFileDto] = []
        self._build_ui()
        self.refresh_file_list()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        file_group = QGroupBox("数据文件")
        file_layout = QVBoxLayout(file_group)
        self.file_table = QTableWidget()
        self.file_table.setColumnCount(3)
        self.file_table.setHorizontalHeaderLabels(["文件名", "类型", "修改时间"])
        file_layout.addWidget(self.file_table)
        refresh_button = QPushButton("刷新文件列表")
        refresh_button.clicked.connect(self.refresh_file_list)
        file_layout.addWidget(refresh_button)
        layout.addWidget(file_group)

        export_group = QGroupBox("导出控制")
        export_layout = QHBoxLayout(export_group)
        self.export_json_btn = QPushButton("导出JSON")
        self.export_csv_btn = QPushButton("导出CSV")
        self.export_pdf_btn = QPushButton("导出PDF报告")
        for button, handler in (
            (self.export_json_btn, self.export_json),
            (self.export_csv_btn, self.export_csv),
            (self.export_pdf_btn, self.export_pdf),
        ):
            button.clicked.connect(handler)
            export_layout.addWidget(button)
        layout.addWidget(export_group)

    def refresh_file_list(self):
        self._row_files = self.result_service.list_results()
        self.file_table.setRowCount(0)
        for row, result_file in enumerate(self._row_files):
            self.file_table.insertRow(row)
            self.file_table.setItem(row, 0, QTableWidgetItem(result_file.filename))
            self.file_table.setItem(row, 1, QTableWidgetItem(result_file.file_type))
            self.file_table.setItem(
                row,
                2,
                QTableWidgetItem(
                    result_file.modified_at.strftime("%Y-%m-%d %H:%M:%S")
                ),
            )

    def _selected_file(self) -> ResultFileDto | None:
        row = self.file_table.currentRow()
        if row < 0 or row >= len(self._row_files):
            QMessageBox.warning(self, "导出", "请先选择要导出的文件")
            return None
        return self._row_files[row]

    def export_json(self):
        source = self._selected_file()
        if source is None:
            return False
        target, _ = QFileDialog.getSaveFileName(
            self, "保存JSON文件", source.filename, "JSON files (*.json)"
        )
        if not target:
            return False
        try:
            self.result_service.export_json(source.result_id, target)
            self.log_callback(f"JSON文件已导出: {target}")
            QMessageBox.information(self, "导出成功", f"文件已导出到: {target}")
            return True
        except Exception as error:
            self.log_callback(f"JSON导出失败: {error}")
            QMessageBox.warning(self, "导出失败", str(error))
            return False

    def _selected_amplifier(self) -> ResultFileDto | None:
        source = self._selected_file()
        if source is None:
            return None
        if source.file_type != "功放测试":
            QMessageBox.warning(self, "导出", "只有功放测试数据支持此导出")
            return None
        return source

    def export_csv(self):
        source = self._selected_amplifier()
        if source is None:
            return False
        target, _ = QFileDialog.getSaveFileName(
            self, "保存CSV文件", source.filename.removesuffix(".json") + ".csv", "CSV files (*.csv)"
        )
        if not target:
            return False
        try:
            self.result_service.export_csv(source.result_id, target)
            self.log_callback(f"CSV文件已导出: {target}")
            QMessageBox.information(self, "导出成功", f"文件已导出到: {target}")
            return True
        except Exception as error:
            self.log_callback(f"CSV导出失败: {error}")
            QMessageBox.warning(self, "导出失败", str(error))
            return False

    def export_pdf(self):
        source = self._selected_amplifier()
        if source is None:
            return False
        try:
            self.result_service.export_pdf(source.result_id)
            self.log_callback("PDF报告已生成在test_results文件夹中")
            QMessageBox.information(self, "导出成功", "PDF报告已生成在test_results文件夹中")
            return True
        except Exception as error:
            self.log_callback(f"PDF导出失败: {error}")
            QMessageBox.warning(self, "导出失败", str(error))
            return False
