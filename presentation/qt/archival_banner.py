"""测量页上的归档失败提示条。

提示条只在“正式结果已保存、但交付产物生成失败”时出现，把一条原本只进日志的警告
变成可操作的状态：用户不必记住去哪个页签，也不用重新测量。

边界：

- 只消费 :class:`ArchivalSummary`，不读文件、不判断产物是否存在、不拼路径；
- 点击后只发出运行标识，由窗口转给应用层用例。页面不持有历史运行服务，也不决定
  重试哪个阶段；
- 默认隐藏；成功后由页面清空，失败时保持可见，让用户能再次尝试。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from .archival_status import ArchivalSummary
from .theme import set_button_variant


class ArchivalBanner(QFrame):
    """一条“交付产物未生成”的可操作提示。"""

    #: 携带运行标识：补生成只能针对已经登记的那次运行。
    regenerate_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("archivalBanner")
        self._run_id: str | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        self.message = QLabel()
        self.message.setObjectName("archivalBannerMessage")
        self.message.setWordWrap(True)
        layout.addWidget(self.message, 1)
        self.regenerate_button = QPushButton("补生成产物")
        set_button_variant(self.regenerate_button, "secondary")
        self.regenerate_button.setToolTip(
            "使用已保存的正式结果补生成交付产物，不会重跑测量"
        )
        self.regenerate_button.clicked.connect(self._request_regeneration)
        layout.addWidget(self.regenerate_button)
        self.hide()

    # -- 状态 ---------------------------------------------------------------

    def show_summary(self, summary: ArchivalSummary) -> None:
        """按归档摘要更新提示条；不可补生成时保持隐藏。"""
        if not isinstance(summary, ArchivalSummary):
            raise TypeError("归档提示条只接受 ArchivalSummary")
        if not summary.can_regenerate or summary.run_id is None:
            self.clear()
            return
        self._run_id = summary.run_id
        self.message.setText(f"{summary.headline}：{summary.detail}")
        self.regenerate_button.setVisible(True)
        self.show()

    def clear(self) -> None:
        """收起提示条；不清空日志，历史提示仍保留在实时日志里。"""
        self._run_id = None
        self.message.clear()
        self.regenerate_button.setVisible(False)
        self.hide()

    @property
    def run_id(self) -> str | None:
        """当前提示条指向的运行标识；隐藏时为 ``None``。"""
        return self._run_id

    # -- 交互 ---------------------------------------------------------------

    def _request_regeneration(self) -> None:
        if self._run_id is None:
            return
        self.regenerate_requested.emit(self._run_id)


__all__ = ["ArchivalBanner"]
