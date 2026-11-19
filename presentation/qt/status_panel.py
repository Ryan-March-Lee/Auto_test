"""测量页左栏的一行运行状态。

测量事实（频点、曲线、读数、进度）已经由结果面、频点胶片条与阶段条表达，这里不再
复制一份数值或进度条。面板只保留**最近一条状态**：接线确认、仪器连接、归档结果，
以及那些“不能打断测量、又不能静默”的失败。完整历史留在消息列表里（tooltip 显示
最近几条），不再占用左栏高度。

面板只有一份，由主窗口在标签页切换时移入对应测量页的 :class:`StatusSlot`：状态
文本与消息历史跟着当前页走，不会三份各说各话。测试配置、数据可视化和历史运行页面
不显示它，但面板在此期间仍是全部消息的接收方，切回测量页时原样出现。
"""

from __future__ import annotations

from collections import deque
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

__all__ = ["StatusPanel", "StatusSlot"]

#: 还没有任何消息时的占位：面板始终占着这一行，不因第一条消息而跳高。
_NO_STATUS_TEXT = "暂无状态"

#: 一条消息全部为空白时的显示占位：仍然算一条，但这一行不能看起来是空的。
_EMPTY_MESSAGE_TEXT = "—"

#: tooltip 里保留的最近消息条数：完整内容还能查到，但不增加面板高度。
_TOOLTIP_HISTORY = 6

#: 会话内保留的消息条数；只用于 tooltip 与测试读取，超出后丢弃最早的。
_MAX_MESSAGES = 200


def _single_line(message: str) -> str:
    """把多行消息压成一行：换行与连续空白折叠成单个空格。"""
    return " ".join(message.split()) or _EMPTY_MESSAGE_TEXT


def _tooltip(messages: list[str]) -> str:
    """把最近几条消息压成只显示文本的 tooltip；消息之间空一行。"""
    blocks: list[str] = []
    for message in messages:
        normalized = message.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.strip():
            continue
        blocks.append(escape(normalized).replace("\n", "<br>"))
    if not blocks:
        return ""
    return f"<qt>{'<br><br>'.join(blocks)}</qt>"


class _StatusLine(QLabel):
    """单行状态：按可用宽度尾部省略，完整内容在 tooltip。

    ``text()`` 返回当前显示的省略文本，``full_text()`` 返回未省略的原文；布局按
    完整文本申请空间，窗口不够宽时再由 ``resizeEvent`` 收敛。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self._full_text = ""
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.set_full_text(_NO_STATUS_TEXT)

    def set_full_text(self, text: str) -> None:
        """写入完整文本；显示文本在可用宽度里省略，完整值留在 tooltip 里。"""
        self._full_text = text
        self._apply_elision()

    def full_text(self) -> str:
        """未省略的原文。"""
        return self._full_text

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 覆盖方法
        super().resizeEvent(event)
        self._apply_elision()

    def _apply_elision(self) -> None:
        width = self.contentsRect().width()
        if width <= 0:
            QLabel.setText(self, self._full_text)
            return
        shown = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, width
        )
        QLabel.setText(self, shown)


class StatusSlot(QWidget):
    """测量页左栏里留给状态面板的位置。

    页面只负责在“测量控制”下方留出这块容器，不创建也不持有状态控件；窗口在页面
    成为当前页时把唯一的面板挂进来，离开时再收回。面板固定一行高，槽里多出的高度
    留在面板下方，不再做折叠/展开的布局切换。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusSlot")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # 槽通常比面板高：默认会把唯一子项垂直居中，这里显式钉在顶部。
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

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
    """一行运行状态：最近一条消息 + 最近几条的 tooltip 历史。"""

    #: 面板最小宽度：再窄状态文本就只剩几个字了。
    MINIMUM_WIDTH = 300

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("statusPanel")
        self.setMinimumWidth(self.MINIMUM_WIDTH)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        # 面板只展示最后一条；历史留在内存里供 tooltip 与测试读取，不回头解析
        # 控件文本。一条消息可能包含多行，压缩显示只发生在这里。
        self._messages: deque[str] = deque(maxlen=_MAX_MESSAGES)

        status_layout = QHBoxLayout(self)
        # 面板嵌在测量页左栏里，左边缘与“测量控制”分组框对齐，不画自己的外框。
        # 上下各留 4px：单行文字不会贴着控制面或归档提示条。
        status_layout.setContentsMargins(0, 4, 0, 4)
        status_layout.setSpacing(8)

        self.caption = QLabel("最近状态", self)
        self.caption.setObjectName("statusCaption")
        status_layout.addWidget(self.caption)

        self.status_line = _StatusLine(self)
        self.status_line.setObjectName("statusLine")
        status_layout.addWidget(self.status_line, 1)

    # ---- 消息 ---------------------------------------------------------------

    def add_log_message(self, message: str) -> None:
        """接收一条消息：写入历史，并把最后一条放到这一行。"""
        text = str(message)
        self._messages.append(text)
        self.status_line.set_full_text(_single_line(text))
        self._sync_tooltip()

    def messages(self) -> tuple[str, ...]:
        """本次会话收到的消息原文（按到达顺序，最多 ``_MAX_MESSAGES`` 条）。"""
        return tuple(self._messages)

    def latest_message(self) -> str | None:
        """最近一条消息原文；还没有消息时为 ``None``。"""
        return self._messages[-1] if self._messages else None

    def status_text(self) -> str:
        """当前一行的完整文本（未按宽度省略）。"""
        return self.status_line.full_text()

    def clear_log(self) -> None:
        """清空消息历史并回到占位文本。"""
        self._messages.clear()
        self.status_line.set_full_text(_NO_STATUS_TEXT)
        self._sync_tooltip()

    def _sync_tooltip(self) -> None:
        recent = list(self._messages)[-_TOOLTIP_HISTORY:]
        tooltip = _tooltip(recent)
        self.status_line.setToolTip(tooltip)

    def update_status(self) -> None:
        """保留定时器扩展点，当前无额外轮询状态。"""
