"""状态面板及日志滚动行为。

面板是全局唯一的进度与日志区域：一眼看到“测量到哪一步”和“刚刚发生了什么”。
它只消费主题层的语义属性（按钮层级由 ``variant`` 声明），不在本地写死颜色。
"""

from __future__ import annotations

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .theme import set_button_variant


class StatusPanel(QFrame):
    """拥有全局进度和日志 UI，窗口只消费其公开控件。"""

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusPanel")
        self.log_user_scrolling = False
        status_layout = QVBoxLayout(self)
        status_layout.setContentsMargins(16, 10, 16, 12)
        status_layout.setSpacing(8)

        progress_layout = QHBoxLayout()
        progress_layout.setSpacing(10)
        progress_label = QLabel("测量进度:")
        progress_label.setObjectName("statusCaption")
        progress_layout.addWidget(progress_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(380)
        self.progress_bar.setTextVisible(False)
        progress_layout.addWidget(self.progress_bar)
        self.progress_value_label = QLabel("0%")
        self.progress_value_label.setObjectName("statusCaption")
        self.progress_value_label.setMinimumWidth(46)
        progress_layout.addWidget(self.progress_value_label)
        progress_layout.addStretch(1)
        self.progress_bar.valueChanged.connect(self._on_progress_value_changed)
        status_layout.addLayout(progress_layout)

        log_header = QHBoxLayout()
        log_header.setSpacing(8)
        log_title = QLabel("实时日志")
        log_title.setObjectName("statusCaption")
        log_header.addWidget(log_title)
        log_header.addStretch(1)
        clear_button = QPushButton("清除日志")
        set_button_variant(clear_button, "secondary")
        clear_button.clicked.connect(self.clear_log)
        log_header.addWidget(clear_button)
        status_layout.addLayout(log_header)

        self.log_text = QTextEdit()
        self.log_text.setObjectName("logView")
        self.log_text.setMaximumHeight(120)
        self.log_text.setToolTip("实时日志：测量进度、仪器连接与归档结果")
        self.log_text.verticalScrollBar().valueChanged.connect(self.on_log_scroll_changed)
        status_layout.addWidget(self.log_text)

    def _on_progress_value_changed(self, value: int) -> None:
        """进度条旁的百分比文字：只做展示，不改变进度语义。"""
        self.progress_value_label.setText(f"{int(value)}%")

    def add_log_message(self, message: str) -> None:
        self.log_text.append(str(message))
        if not self.log_user_scrolling:
            cursor = self.log_text.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.log_text.setTextCursor(cursor)
            scrollbar = self.log_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def on_log_scroll_changed(self, value: int) -> None:
        scrollbar = self.log_text.verticalScrollBar()
        if value >= scrollbar.maximum() - 5:
            self.log_user_scrolling = False
        elif value < scrollbar.maximum() - 10:
            self.log_user_scrolling = True

    def clear_log(self) -> None:
        self.log_text.clear()
        self.log_user_scrolling = False

    def update_status(self) -> None:
        """保留定时器扩展点，当前无额外轮询状态。"""


__all__ = ["StatusPanel"]
