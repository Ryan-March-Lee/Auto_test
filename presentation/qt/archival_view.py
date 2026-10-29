"""归档结果分组控件：把 ``ArchivalSummary`` 显示成文案和“打开”按钮。

控件只负责呈现与系统打开动作：位置由窗口注入的 ``location_provider`` 提供，
本控件不判断文件是否存在，也不拼接导出规则。页面的归档状态机在
:mod:`presentation.qt.archival_page`。
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .archival_status import (
    TONE_ERROR,
    TONE_INFO,
    TONE_SUCCESS,
    TONE_WARNING,
    ArchivalSummary,
)
from .theme import TOKENS, set_button_variant


# 状态文字只取主题令牌，色调与全局一致（信息/成功/警告/错误）。
_TONE_STYLES = {
    TONE_INFO: f"color: {TOKENS['primary_pressed']}; font-weight: 600;",
    TONE_SUCCESS: f"color: {TOKENS['success']}; font-weight: 600;",
    TONE_WARNING: f"color: {TOKENS['warning']}; font-weight: 600;",
    TONE_ERROR: f"color: {TOKENS['danger']}; font-weight: 600;",
}

_IDLE_SUMMARY = ArchivalSummary(
    headline="尚未执行归档",
    detail="测量结束后会自动保存正式结果并生成交付产物。",
    tone=TONE_INFO,
)

LocationProvider = Callable[[str, str | None], str | None]
RunIdProvider = Callable[[], str | None]


class ArchivalStatusView(QGroupBox):
    """一次归档结果的标题、说明与“打开结果目录/测试报告”快捷动作。

    ``current_run_id_provider`` 让动作跟随当前运行：位置解析只服务当前运行，
    因此不属于当前运行的旧摘要不应留下可点击却打不开的按钮。
    """

    def __init__(
        self,
        *,
        location_provider: LocationProvider | None = None,
        log_callback: Callable[[str], object] | None = None,
        current_run_id_provider: RunIdProvider | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("归档结果", parent)
        self._location_provider = location_provider
        self._log_callback = log_callback or (lambda _message: None)
        self._current_run_id_provider = current_run_id_provider
        self.summary: ArchivalSummary | None = None
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        self.headline_label = QLabel()
        self.headline_label.setWordWrap(True)
        # 错误原因、产物原因与路径都是运行期文本，不能交给 Qt 当富文本解析。
        self.headline_label.setTextFormat(Qt.PlainText)
        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        self.detail_label.setProperty("role", "hint")
        self.detail_label.setTextFormat(Qt.PlainText)
        layout.addWidget(self.headline_label)
        layout.addWidget(self.detail_label)
        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        self.open_location_btn = QPushButton("打开结果目录")
        set_button_variant(self.open_location_btn, "secondary")
        self.open_location_btn.clicked.connect(self.open_location)
        self.open_report_btn = QPushButton("打开测试报告")
        set_button_variant(self.open_report_btn, "secondary")
        self.open_report_btn.clicked.connect(self.open_report)
        buttons.addWidget(self.open_location_btn)
        buttons.addWidget(self.open_report_btn)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.show_summary(_IDLE_SUMMARY)

    def show_summary(self, summary: ArchivalSummary) -> None:
        """显示一个归档提示；按钮状态完全由提示本身决定。"""
        if not isinstance(summary, ArchivalSummary):
            raise TypeError("归档展示只接受 ArchivalSummary")
        self.summary = summary
        self.headline_label.setText(summary.headline)
        self.headline_label.setStyleSheet(_TONE_STYLES.get(summary.tone, ""))
        self.detail_label.setText(summary.detail)
        usable = self._is_usable(summary)
        self.open_location_btn.setEnabled(usable and summary.can_open_location)
        self.open_report_btn.setEnabled(usable and summary.can_open_report)

    def _is_usable(self, summary: ArchivalSummary) -> bool:
        """提示是否仍可执行快捷动作：需要位置入口，且属于当前运行。

        “打开结果目录/报告”由窗口按当前运行解析，因此旧运行的摘要即使带着
        ``run_id`` 也不该留下可用按钮；用户仍可从说明里的运行 ID 定位历史结果。
        """
        if self._location_provider is None:
            return False
        provider = self._current_run_id_provider
        if provider is None or summary.run_id is None:
            return True
        current = provider()
        return isinstance(current, str) and current == summary.run_id

    def clear(self) -> None:
        """回到“尚未执行归档”的中性状态。"""
        self.show_summary(_IDLE_SUMMARY)

    def open_location(self) -> bool:
        """打开当前运行的归档目录。"""
        return self._open(None, "结果目录")

    def open_report(self) -> bool:
        """打开当前归档结果已生成的测试报告。"""
        summary = self.summary
        report_path = summary.report_path if summary is not None else None
        return self._open(report_path, "测试报告")

    def _open(self, relative_path: str | None, label: str) -> bool:
        summary = self.summary
        if summary is None or not summary.can_open_location:
            self._log_callback(f"当前没有可打开的归档{label}")
            return False
        if not self._is_usable(summary):
            self._log_callback(
                f"归档{label}属于运行 {summary.run_id}，已不是当前运行；"
                "请在“历史运行”页查看历史运行"
            )
            return False
        provider = self._location_provider
        if provider is None:
            self._log_callback(f"未接入归档{label}位置入口，无法打开")
            return False
        try:
            target = provider(summary.run_id, relative_path)
        except (OSError, ValueError) as error:
            self._log_callback(f"无法定位归档{label}: {error}")
            return False
        if not isinstance(target, str) or not target.strip():
            self._log_callback(
                f"归档{label}位置不可用，请在“历史运行”页查看运行 {summary.run_id}"
            )
            return False
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(target)):
            self._log_callback(f"系统无法打开归档{label}: {target}")
            return False
        self._log_callback(f"已打开归档{label}: {target}")
        return True


__all__ = ["ArchivalStatusView"]
