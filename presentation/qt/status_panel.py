"""状态面板及日志滚动行为。

面板是全局唯一的进度与日志区域，挂在**当前测量页左栏的“测量控制”下方**：一眼看到
“测量到哪一步”和“刚刚发生了什么”，右侧整块宽度则留给测量结果表与频点浏览图。它只
消费主题层的语义属性（按钮层级由 ``variant`` 声明），不在本地写死颜色。

日志默认折叠：标题行显示“实时日志”、最后一条消息、条数与展开/收起箭头，日志框
只在展开时出现，左栏因此保持紧凑。折叠时面板只占标题行高度，并由 :class:`StatusSlot`
钉在槽顶部；展开后清除对齐、继续填满槽的剩余高度。折叠只是显示状态——``add_log_message``、
滚动跟随与清除逻辑都不变，日志仍全部进入同一个控件；展开状态属于面板本身，切页时
由主窗口连同面板一起挂到下一个测量页，所以本次会话内保持用户的选择。

面板只有一份，由主窗口在标签页切换时移入对应测量页的 :class:`StatusSlot`：日志历史、
进度值和“清除日志”跟着当前页走，不会三份各说各话。测试配置、数据可视化和历史运行
页面不显示它，但面板在此期间仍是全部日志的接收方，切回测量页时原样出现。
"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .theme import set_button_variant

#: 还没有日志时的摘要文案；它不参与日志计数，也不写入 ``log_text``。
_NO_LOG_TEXT = "暂无日志"

#: 一条消息全部为空白时的摘要占位：仍然计为一条，但不让标题行看起来是空的。
_EMPTY_MESSAGE_TEXT = "—"

#: 展开/收起按钮的文案与无障碍描述；按钮只有箭头，含义由这里给出。
_LOG_TOGGLE_EXPANDED_TEXT = "▴"
_LOG_TOGGLE_COLLAPSED_TEXT = "▾"
_LOG_TOGGLE_EXPANDED_ACTION = "收起实时日志"
_LOG_TOGGLE_COLLAPSED_ACTION = "展开实时日志"


class StatusSlot(QWidget):
    """测量页左栏里留给状态面板的位置。

    页面只负责在“测量控制”下方留出这块容器，不创建也不持有进度与日志控件；窗口在
    页面成为当前页时把唯一的状态面板挂进来，离开时再收回。折叠时由
    :meth:`sync_panel_alignment` 把面板钉在槽顶部，展开时恢复填满剩余高度。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusSlot")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

    def mount(self, panel: QWidget) -> None:
        """挂上状态面板；已经挂在本容器时只恢复可见并重新对齐。"""
        layout = self.layout()
        if layout.indexOf(panel) < 0:
            layout.addWidget(panel)
        # 面板的展开状态属于它自己，切页重挂时必须按当前状态重新应用对齐，
        # 否则新槽的默认布局会把折叠面板垂直居中。
        self.sync_panel_alignment(panel)
        panel.show()

    def sync_panel_alignment(self, panel: QWidget) -> None:
        """折叠时把面板钉在槽顶部；展开时清除对齐，让它填满剩余高度。

        只把面板的纵向尺寸策略收成 ``Maximum`` 还不够：槽里只有这一个不可增长的
        控件时，``QBoxLayout`` 会把剩余高度平均分到上下，进度与日志会浮在左栏中间。
        因此位置必须由槽显式给出；展开后清除对齐，面板才能继续占满整个槽。
        """
        layout = self.layout()
        if layout is None or layout.indexOf(panel) < 0:
            return
        expanded = getattr(panel, "is_log_expanded", lambda: True)()
        layout.setAlignment(
            panel,
            Qt.Alignment() if expanded else Qt.AlignmentFlag.AlignTop,
        )
        layout.invalidate()

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
        # 摘要与条数由面板自己按“接收过几次消息”记账，不回头解析日志控件文本：
        # 一条消息可能包含多行，blockCount 不能当条数用。最后一条消息保留原文，
        # 标题行按需压平，tooltip 仍能给出完整内容。
        self._log_message_count = 0
        self._last_log_message = ""
        self._log_expanded = False
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

        # 折叠时仍要看得见“刚刚发生了什么”：摘要只占一行，完整消息另在日志里；
        # 宽度可被压缩，长消息由 Qt 裁切，完整文本放在 tooltip。
        self.log_summary_label = QLabel(_NO_LOG_TEXT)
        self.log_summary_label.setObjectName("logSummary")
        self.log_summary_label.setProperty("role", "hint")
        self.log_summary_label.setTextFormat(Qt.TextFormat.PlainText)
        self.log_summary_label.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.log_summary_label.setMinimumWidth(0)
        log_header.addWidget(self.log_summary_label, 1)

        self.log_count_label = QLabel("0 条")
        self.log_count_label.setObjectName("statusCaption")
        self.log_count_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        log_header.addWidget(self.log_count_label)

        clear_button = QPushButton("清除日志")
        set_button_variant(clear_button, "ghost")
        clear_button.clicked.connect(self.clear_log)
        log_header.addWidget(clear_button)

        self.log_toggle_btn = QPushButton(_LOG_TOGGLE_COLLAPSED_TEXT)
        self.log_toggle_btn.setObjectName("logToggle")
        self.log_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_button_variant(self.log_toggle_btn, "ghost")
        self.log_toggle_btn.clicked.connect(self.toggle_log)
        log_header.addWidget(self.log_toggle_btn)
        status_layout.addLayout(log_header)

        self.log_text = QTextEdit()
        self.log_text.setObjectName("logView")
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(180)
        self.log_text.setToolTip("实时日志：测量进度、仪器连接与归档结果")
        self.log_text.verticalScrollBar().valueChanged.connect(self.on_log_scroll_changed)
        status_layout.addWidget(self.log_text, 1)

        self._sync_log_summary()
        self.set_log_expanded(False)

    def _on_progress_value_changed(self, value: int) -> None:
        """进度条旁的百分比文字：只做展示，不改变进度语义。"""
        self.progress_value_label.setText(f"{int(value)}%")

    def is_log_expanded(self) -> bool:
        """日志框当前是否展开；默认折叠，用户的展开选择在本次会话内保持。"""
        return self._log_expanded

    def set_log_expanded(self, expanded: bool) -> None:
        """展开/收起日志框；只切换可见性，不改变已接收的消息与滚动跟随。"""
        if not isinstance(expanded, bool):
            raise TypeError("日志展开状态必须是 bool")
        self._log_expanded = expanded
        self.log_text.setVisible(expanded)
        # 折叠时不要再占满左栏剩余高度：把纵向尺寸策略收成“最多到 sizeHint”，
        # 左栏才会随日志收起而明显变短；展开后恢复可扩展，日志继续吃满剩余空间。
        policy = self.sizePolicy()
        policy.setVerticalPolicy(
            QSizePolicy.Policy.Preferred
            if expanded
            else QSizePolicy.Policy.Maximum
        )
        self.setSizePolicy(policy)
        self.updateGeometry()
        if expanded:
            self.log_toggle_btn.setText(_LOG_TOGGLE_EXPANDED_TEXT)
            self.log_toggle_btn.setAccessibleName(_LOG_TOGGLE_EXPANDED_ACTION)
            self.log_toggle_btn.setToolTip(_LOG_TOGGLE_EXPANDED_ACTION)
        else:
            self.log_toggle_btn.setText(_LOG_TOGGLE_COLLAPSED_TEXT)
            self.log_toggle_btn.setAccessibleName(_LOG_TOGGLE_COLLAPSED_ACTION)
            self.log_toggle_btn.setToolTip(_LOG_TOGGLE_COLLAPSED_ACTION)
        # 位置由宿主槽决定：折叠时贴顶，展开时填满；面板单独使用时没有槽，
        # 只保留尺寸策略，不额外依赖父控件类型。
        host = self.parentWidget()
        if isinstance(host, StatusSlot):
            host.sync_panel_alignment(self)

    def toggle_log(self) -> None:
        """在展开与折叠之间切换；等价于用户点击标题行右侧的箭头。"""
        self.set_log_expanded(not self._log_expanded)

    def _sync_log_summary(self) -> None:
        """把条数与最后一条消息写进标题行；摘要不写入日志控件。"""
        self.log_count_label.setText(f"{self._log_message_count} 条")
        raw = self._last_log_message
        if not raw:
            self.log_summary_label.setText(_NO_LOG_TEXT)
            self.log_summary_label.setToolTip("")
            return
        summary = " ".join(raw.split())
        self.log_summary_label.setText(summary or _EMPTY_MESSAGE_TEXT)
        self.log_summary_label.setToolTip(self._tooltip_text(raw))

    @staticmethod
    def _tooltip_text(message: str) -> str:
        """把日志原文转成只显示文本的 tooltip，避免被 Qt 当成富文本解析。

        ``<qt>`` 是显式的富文本容器；先转义尖括号与 ``&``，再把换行变成 ``<br>``，
        这样日志里出现的 ``<b>``、``&`` 或类似 HTML 的内容不会改变 tooltip 的排版，
        原始多行消息也能在提示里保持分行。
        """
        normalized = message.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.strip():
            return ""
        escaped = escape(normalized).replace("\n", "<br>")
        return f"<qt>{escaped}</qt>"

    def add_log_message(self, message: str) -> None:
        text = str(message)
        self.log_text.append(text)
        self._log_message_count += 1
        # tooltip 保留原始文本，标题行只显示压成一行后的摘要；完整内容仍在 log_text。
        self._last_log_message = text
        self._sync_log_summary()
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
        self._log_message_count = 0
        self._last_log_message = ""
        self._sync_log_summary()

    def update_status(self) -> None:
        """保留定时器扩展点，当前无额外轮询状态。"""


__all__ = ["StatusPanel", "StatusSlot"]
