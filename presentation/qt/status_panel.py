"""状态面板及日志滚动行为。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


class StatusPanel(QFrame):
    """拥有全局进度和日志 UI，窗口只消费其公开控件和回调。"""

    def __init__(
        self,
        *,
        chat_toggle_callback: Callable[[], Any] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.log_user_scrolling = False
        self.setFrameStyle(QFrame.StyledPanel)
        status_layout = QVBoxLayout(self)

        progress_layout = QHBoxLayout()
        progress_label = QLabel("测量进度:")
        progress_label.setStyleSheet("color: black; font-weight: bold;")
        progress_layout.addWidget(progress_label)
        self.progress_bar = QProgressBar()
        progress_layout.addWidget(self.progress_bar)
        status_layout.addLayout(progress_layout)

        log_group = QGroupBox("实时日志")
        log_group.setStyleSheet("QGroupBox { color: black; font-weight: bold; }")
        log_layout = QVBoxLayout(log_group)
        self.log_text = QTextEdit()
        self.log_text.setMaximumHeight(180)
        self.log_text.setFont(QFont("Consolas", 9))
        self.log_text.verticalScrollBar().valueChanged.connect(self.on_log_scroll_changed)
        log_layout.addWidget(self.log_text)

        buttons = QHBoxLayout()
        clear_button = QPushButton("清除日志")
        clear_button.clicked.connect(self.clear_log)
        buttons.addWidget(clear_button)
        self.ai_assistant_btn = QPushButton("💬 CHAT")
        self.ai_assistant_btn.setCheckable(True)
        self.ai_assistant_btn.setStyleSheet(
            "QPushButton { background-color: #28a745; color: white; border: none; "
            "border-radius: 5px; padding: 8px 16px; font-weight: bold; }"
            "QPushButton:hover { background-color: #218838; }"
            "QPushButton:checked { background-color: #1e7e34; }"
        )
        if chat_toggle_callback is not None:
            self.ai_assistant_btn.clicked.connect(chat_toggle_callback)
        buttons.addWidget(self.ai_assistant_btn)
        log_layout.addLayout(buttons)
        status_layout.addWidget(log_group)

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
