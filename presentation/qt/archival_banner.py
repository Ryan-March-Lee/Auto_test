"""测量页上的归档失败提示条。

提示条只在“正式结果已保存、但交付产物生成失败”时出现，把一条原本只进日志的警告
变成页面上可见的状态。交付产物补生成能力保留在应用层，但历史运行页面已从界面移除，
本提示条因此不再暗示页面会自动补生成，只负责展示失败事实与运行 ID。

边界：

- 只消费 :class:`ArchivalSummary`，不读文件、不判断产物是否存在、不拼路径；
- 不触发应用层动作，也不持有历史运行服务；
- 默认隐藏；成功或没有可补生成产物时由页面清空。
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from .archival_status import ArchivalSummary


class ArchivalBanner(QFrame):
    """一条“交付产物未生成”的状态提示。"""

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
        self.hide()

    # -- 状态 ---------------------------------------------------------------

    def show_summary(self, summary: ArchivalSummary) -> None:
        """按归档摘要更新提示条；没有可补生成产物时保持隐藏。"""
        if not isinstance(summary, ArchivalSummary):
            raise TypeError("归档提示条只接受 ArchivalSummary")
        if not summary.can_regenerate or summary.run_id is None:
            self.clear()
            return
        self._run_id = summary.run_id
        self.message.setText(f"{summary.headline}：{summary.detail}")
        self.show()

    def clear(self) -> None:
        """收起提示条；不清空日志，历史提示仍保留在实时日志里。"""
        self._run_id = None
        self.message.clear()
        self.hide()

    @property
    def run_id(self) -> str | None:
        """当前提示条指向的运行标识；隐藏时为 ``None``。"""
        return self._run_id


__all__ = ["ArchivalBanner"]
