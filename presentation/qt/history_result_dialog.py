"""选择历史结果对话框。

数据可视化页的“加载测试数据”通过这里选择结果，不再让用户在运行目录里翻 JSON。
对话框只使用只读 ``HistoryViewModel``：列表来自 ``ResultQueryPort.list_results()``，
加载只经过 ``query.load(result_id)``，不解析文件路径、不直接访问文件系统。
"""

from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from application.ports.gui_data_service import LoadedResultDto

from .history_view_model import HistoryRow, HistoryViewModel
from .theme import set_button_variant

_RESULT_COLUMNS = ("文件名", "类型", "来源", "修改时间")

__all__ = ["HistoryResultDialog"]


class HistoryResultDialog(QDialog):
    """列出历史结果，返回用户选中的正式结果 DTO；取消时 ``loaded_result`` 为 ``None``。"""

    def __init__(
        self,
        history: HistoryViewModel,
        *,
        log_callback: Callable[[str], Any] | None = None,
        parent=None,
    ):
        if not isinstance(history, HistoryViewModel):
            raise TypeError("历史结果对话框必须使用只读 HistoryViewModel")
        super().__init__(parent)
        self.history = history
        self.log_callback = log_callback or (lambda _message: None)
        self.loaded_result: LoadedResultDto | None = None
        self.setWindowTitle("选择历史结果")
        self.setModal(True)
        self.resize(760, 440)
        self._build_ui()
        self.refresh()

    # -- 界面 ---------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)

        hint = QLabel("选择一条已归档的历史结果，加载到数据可视化页查看。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget()
        self.table.setColumnCount(len(_RESULT_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(_RESULT_COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._sync_load_button)
        self.table.itemDoubleClicked.connect(lambda _item: self.accept())
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column, width in ((1, 110), (2, 110), (3, 170)):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
            self.table.setColumnWidth(column, width)
        layout.addWidget(self.table, 1)

        self.empty_hint = QLabel("尚无历史结果：测量完成归档后结果会出现在这里。")
        self.empty_hint.setObjectName("emptyHint")
        layout.addWidget(self.empty_hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.refresh_button = QPushButton("刷新")
        set_button_variant(self.refresh_button, "secondary")
        self.refresh_button.clicked.connect(self.refresh)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)
        self.cancel_button = QPushButton("取消")
        set_button_variant(self.cancel_button, "secondary")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        self.load_button = QPushButton("加载选中结果")
        set_button_variant(self.load_button, "primary")
        self.load_button.setEnabled(False)
        self.load_button.clicked.connect(self.accept)
        buttons.addWidget(self.load_button)
        layout.addLayout(buttons)

    # -- 列表 ---------------------------------------------------------------

    def refresh(self):
        """重新读取历史结果并填表；读取失败时清空表格并保留失败原因。"""
        try:
            rows = self.history.reload()
        except (OSError, ValueError, RuntimeError) as error:
            self._clear_table()
            self.history.reset_results()
            self.empty_hint.setText("读取历史结果失败，请刷新重试。")
            self.empty_hint.setVisible(True)
            self.log_callback(f"读取历史结果失败: {error}")
            self._sync_load_button()
            return ()
        with QSignalBlocker(self.table):
            self.table.setRowCount(0)
            for index, row in enumerate(rows):
                self.table.insertRow(index)
                for column, text in enumerate(_row_texts(row)):
                    _set_cell(self.table, index, column, text)
        self.empty_hint.setVisible(not rows)
        self._sync_load_button()
        return rows

    def selected_row(self) -> HistoryRow | None:
        """当前选中的历史结果；未选中时返回 ``None``。"""
        return self.history.select(self.table.currentRow())

    def accept(self):
        """加载选中的结果；读取失败时留在对话框里提示，不返回半截状态。"""
        row = self.selected_row()
        if row is None:
            QMessageBox.warning(self, "选择历史结果", "请先选择一条历史结果")
            return
        query = self.history.query
        if query is None:
            QMessageBox.warning(self, "无法加载", "未配置历史结果查询服务")
            return
        try:
            self.loaded_result = query.load(row.result_id)
        except (OSError, ValueError) as error:
            self.log_callback(f"加载历史结果失败: {error}")
            QMessageBox.warning(self, "无法加载", f"加载历史结果失败: {error}")
            return
        super().accept()

    # -- 内部工具 -----------------------------------------------------------

    def _sync_load_button(self) -> None:
        """只有选中一行时才能加载；表格空或没有当前行时禁用。"""
        self.load_button.setEnabled(self.selected_row() is not None)

    def _clear_table(self) -> None:
        with QSignalBlocker(self.table):
            self.table.setRowCount(0)


def _row_texts(row: HistoryRow) -> tuple[str, ...]:
    """把一条历史结果排成表格文本；顺序与 ``_RESULT_COLUMNS`` 一致。"""
    return (
        row.filename,
        row.file_type,
        row.source_label,
        row.modified_text,
    )


def _set_cell(table: QTableWidget, row: int, column: int, text: str) -> None:
    """写入一个只读单元格；完整文本放进 tooltip。"""
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    item.setToolTip(text)
    table.setItem(row, column, item)
