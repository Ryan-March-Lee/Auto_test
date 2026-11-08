"""状态面板及日志滚动行为。

面板是全局唯一的进度与日志区域，挂在**当前测量页左栏的“测量控制”下方**：一眼看到
“测量到哪一步”和“刚刚发生了什么”，右侧整块宽度则留给测量结果表与频点浏览图。它只
消费主题层的语义属性（按钮层级由 ``variant`` 声明），不在本地写死颜色。

面板只有一份，由主窗口在标签页切换时移入对应测量页的 :class:`StatusSlot`：日志历史、
进度值和“清除日志”跟着当前页走，不会三份各说各话。测试配置、数据可视化和历史运行
页面不显示它，但面板在此期间仍是全部日志的接收方，切回测量页时原样出现。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
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


class StatusSlot(QWidget):
    """测量页左栏里留给状态面板的位置。

    页面只负责在“测量控制”下方留出这块容器，不创建也不持有进度与日志控件；窗口在
    页面成为当前页时把唯一的状态面板挂进来，离开时再收回。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusSlot")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

    def mount(self, panel: QWidget) -> None:
        """挂上状态面板；已经挂在本容器时只恢复可见。"""
        layout = self.layout()
        if layout.indexOf(panel) < 0:
            layout.addWidget(panel)
        panel.show()

    def unmount(self, panel: QWidget) -> None:
        """收回状态面板：先移出布局再隐藏，等待挂到下一个测量页。"""
        self.layout().removeWidget(panel)
        panel.hide()


class StatusPanel(QFrame):
    """拥有全局进度和日志 UI，窗口只消费其公开控件。"""

    #: 面板最小宽度：再窄进度条与“清除日志”按钮就挤在一起了。
    MINIMUM_WIDTH = 300

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusPanel")
        self.setMinimumWidth(self.MINIMUM_WIDTH)
        self.log_user_scrolling = False
        status_layout = QVBoxLayout(self)
        # 面板本身不再画外框：它嵌在测量页左栏里，左边缘与“测量控制”分组框对齐。
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(8)

        progress_layout = QHBoxLayout()
        progress_layout.setSpacing(8)
        progress_label = QLabel("测量进度")
        progress_label.setObjectName("statusCaption")
        progress_layout.addWidget(progress_label)
        progress_layout.addStretch(1)
        self.progress_value_label = QLabel("0%")
        self.progress_value_label.setObjectName("statusCaption")
        self.progress_value_label.setMinimumWidth(40)
        self.progress_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        progress_layout.addWidget(self.progress_value_label)
        status_layout.addLayout(progress_layout)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setMinimumWidth(0)
        self.progress_bar.valueChanged.connect(self._on_progress_value_changed)
        status_layout.addWidget(self.progress_bar)

        log_header = QHBoxLayout()
        log_header.setSpacing(8)
        log_title = QLabel("实时日志")
        log_title.setObjectName("statusCaption")
        log_header.addWidget(log_title)
        log_header.addStretch(1)
        clear_button = QPushButton("清除日志")
        set_button_variant(clear_button, "ghost")
        clear_button.clicked.connect(self.clear_log)
        log_header.addWidget(clear_button)
        status_layout.addLayout(log_header)

        self.log_text = QTextEdit()
        self.log_text.setObjectName("logView")
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(180)
        self.log_text.setToolTip("实时日志：测量进度、仪器连接与归档结果")
        self.log_text.verticalScrollBar().valueChanged.connect(self.on_log_scroll_changed)
        status_layout.addWidget(self.log_text, 1)

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


__all__ = ["StatusPanel", "StatusSlot"]
