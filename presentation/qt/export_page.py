"""结果文件列表和离线导出页面。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
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


@dataclass(frozen=True)
class ResultFile:
    path: Path
    file_type: str

    def __iter__(self):
        """保持旧的 ``(path, file_type)`` 解包兼容性。"""
        yield self.path
        yield self.file_type


def list_result_files(results_dir: Path, cable_loss_file: Path) -> list[ResultFile]:
    patterns = (
        (cable_loss_file.name, "线损数据"),
        ("driver_power_mapping_*.json", "驱动映射"),
        ("amplifier_measurement_*.json", "功放测试"),
    )
    result: list[ResultFile] = []
    for pattern, file_type in patterns:
        for path in sorted(
            results_dir.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True
        ):
            if path.is_file():
                result.append(ResultFile(path=path, file_type=file_type))
    return result


class ExportPage(QWidget):
    """封装结果文件列表以及 JSON/CSV/PDF 导出操作。"""

    def __init__(
        self,
        *,
        results_dir: Path,
        cable_loss_file: Path,
        visualizer_factory: Callable[[], Any],
        log_callback: Callable[[str], Any] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.results_dir = Path(results_dir).resolve()
        self.cable_loss_file = Path(cable_loss_file).resolve()
        self.visualizer_factory = visualizer_factory
        self.log_callback = log_callback or (lambda _message: None)
        self._row_files: list[ResultFile] = []
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
        self._row_files = list_result_files(self.results_dir, self.cable_loss_file)
        self.file_table.setRowCount(0)
        for row, result_file in enumerate(self._row_files):
            self.file_table.insertRow(row)
            self.file_table.setItem(row, 0, QTableWidgetItem(result_file.path.name))
            self.file_table.setItem(row, 1, QTableWidgetItem(result_file.file_type))
            self.file_table.setItem(
                row,
                2,
                QTableWidgetItem(
                    datetime.fromtimestamp(result_file.path.stat().st_mtime).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                ),
            )

    def _selected_file(self) -> ResultFile | None:
        row = self.file_table.currentRow()
        if row < 0 or row >= len(self._row_files):
            QMessageBox.warning(self, "导出", "请先选择要导出的文件")
            return None
        result_file = self._row_files[row]
        try:
            path = result_file.path.resolve()
            path.relative_to(self.results_dir)
        except ValueError:
            QMessageBox.warning(self, "导出失败", "选中文件不在结果目录中")
            return None
        if not path.is_file():
            QMessageBox.warning(self, "导出失败", f"源文件不存在: {path.name}")
            return None
        return ResultFile(path=path, file_type=result_file.file_type)

    def export_json(self):
        source = self._selected_file()
        if source is None:
            return False
        target, _ = QFileDialog.getSaveFileName(
            self, "保存JSON文件", source.path.name, "JSON files (*.json)"
        )
        if not target:
            return False
        try:
            shutil.copy2(source.path, target)
            self.log_callback(f"JSON文件已导出: {target}")
            QMessageBox.information(self, "导出成功", f"文件已导出到: {target}")
            return True
        except Exception as error:
            self.log_callback(f"JSON导出失败: {error}")
            QMessageBox.warning(self, "导出失败", str(error))
            return False

    def _selected_amplifier(self) -> ResultFile | None:
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
            self, "保存CSV文件", source.path.with_suffix(".csv").name, "CSV files (*.csv)"
        )
        if not target:
            return False
        try:
            visualizer = self.visualizer_factory()
            visualizer.generate_csv_report(str(source.path))
            generated = Path(visualizer.output_dir) / "full_sweep_data.csv"
            if not generated.is_file():
                raise FileNotFoundError(f"未生成CSV文件: {generated}")
            shutil.copy2(generated, target)
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
            visualizer = self.visualizer_factory()
            visualizer.create_summary_report(str(source.path))
            self.log_callback("PDF报告已生成在test_results文件夹中")
            QMessageBox.information(self, "导出成功", "PDF报告已生成在test_results文件夹中")
            return True
        except Exception as error:
            self.log_callback(f"PDF导出失败: {error}")
            QMessageBox.warning(self, "导出失败", str(error))
            return False
