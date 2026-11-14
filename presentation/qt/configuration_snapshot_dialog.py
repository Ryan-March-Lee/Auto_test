"""选择已保存配置快照的对话框。

欢迎页的“加载配置”不再让用户在文件系统里找 JSON：对话框只列出配置快照仓储
``list()`` 返回的正式快照（名称 + 保存时间），选中后把
:class:`application.ports.configuration_snapshots.ConfigurationSnapshot` 交回调用方。
读取与加载仍由仓储/协调器完成；本模块不解析配置内容、不写文件，也不创建执行上下文。
"""

from __future__ import annotations

from datetime import datetime
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

from application.ports.configuration_snapshots import ConfigurationSnapshot

from .theme import set_button_variant

_COLUMNS = ("配置名称", "保存时间")

__all__ = ["ConfigurationSnapshotDialog"]


def _format_saved_at(value: str) -> str:
    """把快照元数据里的 UTC ISO 时间显示成本地时间；解析失败时保留原文。"""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return str(value)
    if moment.tzinfo is None:
        return moment.strftime("%Y-%m-%d %H:%M:%S")
    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _set_cell(table: QTableWidget, row: int, column: int, text: Any) -> None:
    item = QTableWidgetItem(str(text))
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    table.setItem(row, column, item)


class ConfigurationSnapshotDialog(QDialog):
    """列出已保存配置快照；取消时 ``selected_snapshot`` 保持 ``None``。"""

    def __init__(
        self,
        repository: Any,
        *,
        log_callback: Callable[[str], Any] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository
        self.log_callback = log_callback or (lambda _message: None)
        self.selected_snapshot: ConfigurationSnapshot | None = None
        self._snapshots: tuple[ConfigurationSnapshot, ...] = ()
        self.setWindowTitle("加载配置")
        self.setModal(True)
        self.resize(560, 400)
        self._build_ui()
        self.refresh()

    # -- 界面 ---------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)

        hint = QLabel("选择一份已保存的配置，加载后直接进入工作区的“测试配置”页。")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = QTableWidget()
        self.table.setColumnCount(len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(list(_COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._sync_load_button)
        self.table.itemDoubleClicked.connect(lambda _item: self.accept())
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Fixed)
        self.table.setColumnWidth(1, 170)
        layout.addWidget(self.table, 1)

        self.empty_hint = QLabel(
            "还没有已保存的配置。请先在测试配置页“保存配置”后再加载。"
        )
        self.empty_hint.setObjectName("emptyHint")
        self.empty_hint.setWordWrap(True)
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
        self.load_button = QPushButton("加载选中配置")
        set_button_variant(self.load_button, "primary")
        self.load_button.setEnabled(False)
        self.load_button.clicked.connect(self.accept)
        buttons.addWidget(self.load_button)
        layout.addLayout(buttons)

    # -- 列表 ---------------------------------------------------------------

    def refresh(self) -> tuple[ConfigurationSnapshot, ...]:
        """重新读取快照并填表；读取失败时清空表格并保留失败原因。"""
        self.selected_snapshot = None
        try:
            snapshots = (
                tuple(self.repository.list()) if self.repository is not None else ()
            )
        except (OSError, ValueError, RuntimeError) as error:
            self._snapshots = ()
            self._clear_table()
            self.empty_hint.setText("读取已保存配置失败，请刷新重试。")
            self.empty_hint.setVisible(True)
            self.log_callback(f"读取已保存配置失败: {error}")
            self._sync_load_button()
            return self._snapshots

        self._snapshots = snapshots
        with QSignalBlocker(self.table):
            self.table.setRowCount(0)
            for index, snapshot in enumerate(snapshots):
                self.table.insertRow(index)
                _set_cell(self.table, index, 0, snapshot.display_name)
                _set_cell(self.table, index, 1, _format_saved_at(snapshot.saved_at))
        self.empty_hint.setText(
            "还没有已保存的配置。请先在测试配置页“保存配置”后再加载。"
        )
        self.empty_hint.setVisible(not snapshots)
        self._sync_load_button()
        return snapshots

    def selected_row_snapshot(self) -> ConfigurationSnapshot | None:
        """当前选中的快照；未选中时返回 ``None``。"""
        row = self.table.currentRow()
        if 0 <= row < len(self._snapshots):
            return self._snapshots[row]
        return None

    def accept(self) -> None:
        """确认加载选中的快照；未选中时留在对话框里提示。"""
        snapshot = self.selected_row_snapshot()
        if snapshot is None:
            QMessageBox.warning(self, "加载配置", "请先选择一份配置")
            return
        self.selected_snapshot = snapshot
        super().accept()

    # -- 内部 ---------------------------------------------------------------

    def _clear_table(self) -> None:
        with QSignalBlocker(self.table):
            self.table.setRowCount(0)

    def _sync_load_button(self) -> None:
        self.load_button.setEnabled(self.selected_row_snapshot() is not None)
