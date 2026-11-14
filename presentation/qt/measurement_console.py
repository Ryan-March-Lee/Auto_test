"""测量页左栏的运行控制台：运行事实 + 前置检查。

P0–P2 之后左栏仍是“按钮 + 空档 + 折叠日志”：用户看得到操作，却看不到这次运行属于
哪次、结果会落到哪里、开始测量前还缺什么。本模块把这些**页面已经知道的事实**排成
一块紧凑的控制台，放在测量控制面与状态面板之间：

- 运行标识、测试模式、结果归档位置；
- 前置检查：执行会话、校准输入、接线与归档位置，每行一个 LED；
- 状态只有三种，且都不伪造测量结论：``READY``（已满足）、``WAITING``（还缺）、
  ``OPTIONAL``（这一步不使用或只能在开始时确认）。

控制台只读：不连接 controller、不启动测量、不读文件、不弹窗。所有值都来自页面构造
时注入的 provder 与页面自己保存的选择；LED 表达的是页面**现有判据**，不是第二套
测量状态机。颜色与字号全部由 :mod:`presentation.qt.theme` 的
``consoleFactLabel`` / ``consoleFactValue`` / ``consoleCheckLabel`` /
``consoleCheckDetail`` / ``consoleLed`` 选择器给出。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .measurement_surface import (
    CONTROL_SURFACE,
    build_surface,
    hairline,
    section_caption,
)

__all__ = [
    "CheckState",
    "ConsoleCheck",
    "MeasurementRunConsole",
    "archive_check",
    "input_check",
    "run_session_check",
    "wiring_check",
]


class CheckState(str, Enum):
    """前置检查的三种显示状态；不包含“失败”，失败由状态面板与日志承担。"""

    READY = "ready"
    WAITING = "waiting"
    OPTIONAL = "optional"


@dataclass(frozen=True)
class ConsoleCheck:
    """一行前置检查：标签、LED 状态与右侧的短说明。"""

    label: str
    state: CheckState
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("检查标签不能为空")
        if not isinstance(self.state, CheckState):
            raise TypeError("检查状态必须是 CheckState")
        if not isinstance(self.detail, str):
            raise TypeError("检查说明必须是字符串")


def _middle_elide(text: str, limit: int = 36) -> str:
    """从中间省略长文本：保留开头与结尾（例如运行标识的时间戳）。"""
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"
    head = (limit - 1) // 2
    tail = limit - 1 - head
    return f"{text[:head]}…{text[-tail:]}"


def _shorten_path(value: str | None) -> str:
    """归档目录只展示末尾目录；运行标识这类长目录名不再叠加第二层省略。

    ``…/`` 已经说明这是路径后缀，因此长运行目录直接显示最后一段（完整路径在
    tooltip）；短目录名则保留最后两段，帮助区分 ``run-1/artifacts`` 这类结构。
    """
    if not value:
        return ""
    normalized = value.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    if len(parts) <= 2:
        return _middle_elide(normalized, 28)
    last = parts[-1]
    if len(last) >= 20:
        text = f"…/{last}"
    else:
        text = f"…/{parts[-2]}/{last}"
    return _middle_elide(text, 28)


def run_session_check(run_id: str | None) -> ConsoleCheck:
    """执行会话是否已经具备当前运行；只读无副作用的运行标识。

    不能用 ``resolve_measurement_run()``：它在未连接时会弹出错误对话框，而控制台
    会在每次切页/状态变化时刷新。运行标识只由 ``current_run_id()`` 这类纯查询给出。
    """
    if isinstance(run_id, str) and run_id.strip():
        return ConsoleCheck("执行会话", CheckState.READY, "已连接运行")
    return ConsoleCheck("执行会话", CheckState.WAITING, "请先连接仪器")


def input_check(label: str, selected: bool) -> ConsoleCheck:
    """校准输入是否已经由用户显式选择；只读页面保存的选择状态。"""
    if not isinstance(selected, bool):
        raise TypeError("校准输入选择状态必须是 bool")
    if selected:
        return ConsoleCheck(label, CheckState.READY, "已选择")
    return ConsoleCheck(label, CheckState.WAITING, "待选择")


def wiring_check() -> ConsoleCheck:
    """接线没有常驻确认状态：只在开始弹窗里确认，控制台如实说明。"""
    return ConsoleCheck("接线确认", CheckState.OPTIONAL, "开始时确认")


def archive_check(directory: str | None, *, configured: bool) -> ConsoleCheck:
    """归档位置是否可用；没有运行或没有 provider 时给出不同说明。"""
    if not isinstance(configured, bool):
        raise TypeError("归档配置状态必须是 bool")
    if not configured:
        return ConsoleCheck("结果归档", CheckState.WAITING, "未配置归档位置")
    if directory:
        return ConsoleCheck("结果归档", CheckState.READY, "已就绪")
    return ConsoleCheck("结果归档", CheckState.WAITING, "等待运行")


class MeasurementRunConsole(QWidget):
    """左栏控制台：只排版页面已经取好的运行事实与检查结论。"""

    #: 前置检查最多五行，超出的调用方错误直接报出来，避免静默丢行。
    MAX_CHECKS = 5

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.surface = build_surface(CONTROL_SURFACE, title="运行控制台")
        outer.addWidget(self.surface)
        content = self.surface.layout()

        self._facts = QGridLayout()
        self._facts.setContentsMargins(0, 0, 0, 0)
        self._facts.setHorizontalSpacing(10)
        self._facts.setVerticalSpacing(2)
        self._run_caption, self._run_value = self._add_fact(0, "运行标识")
        self._mode_caption, self._mode_value = self._add_fact(1, "测试模式")
        self._archive_caption, self._archive_value = self._add_fact(2, "结果归档")
        content.addLayout(self._facts)

        content.addWidget(hairline())
        content.addWidget(section_caption("前置检查"))
        self._checks_grid = QGridLayout()
        self._checks_grid.setContentsMargins(0, 0, 0, 0)
        self._checks_grid.setHorizontalSpacing(8)
        self._checks_grid.setVerticalSpacing(2)
        content.addLayout(self._checks_grid)

        self._check_rows: list[tuple[QLabel, QLabel, QLabel]] = []
        self._checks: tuple[ConsoleCheck, ...] = ()

        self.set_run_facts(run_id=None, mode=None, archive=None, archive_configured=False)
        self.set_checks(())

    # ---- 构建 ---------------------------------------------------------------

    def _add_fact(self, row: int, caption: str) -> tuple[QLabel, QLabel]:
        label = QLabel(caption, self.surface)
        label.setObjectName("consoleFactLabel")
        label.setTextFormat(Qt.TextFormat.PlainText)
        value = QLabel("", self.surface)
        value.setObjectName("consoleFactValue")
        value.setTextFormat(Qt.TextFormat.PlainText)
        value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        value.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        value.setMinimumWidth(0)
        self._facts.addWidget(label, row, 0)
        self._facts.addWidget(value, row, 1)
        return label, value

    # ---- 运行事实 -----------------------------------------------------------

    def set_run_facts(
        self,
        *,
        run_id: str | None,
        mode: str | None,
        archive: str | None,
        archive_configured: bool = True,
    ) -> None:
        """写入运行标识、测试模式与归档目录；``mode=None`` 隐藏模式行。"""
        for name, value in (("run_id", run_id), ("mode", mode), ("archive", archive)):
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{name} 必须是字符串或 None")
        if not isinstance(archive_configured, bool):
            raise TypeError("归档配置状态必须是 bool")

        cleaned_run = run_id.strip() if isinstance(run_id, str) else ""
        self._run_value.setText(_middle_elide(cleaned_run) if cleaned_run else "未连接")
        self._run_value.setToolTip(cleaned_run)

        show_mode = isinstance(mode, str) and bool(mode.strip())
        self._mode_caption.setVisible(show_mode)
        self._mode_value.setVisible(show_mode)
        self._mode_value.setText(mode.strip() if show_mode else "")
        self._mode_value.setToolTip(mode.strip() if show_mode else "")

        cleaned_archive = archive.strip() if isinstance(archive, str) else ""
        if cleaned_archive:
            self._archive_value.setText(_shorten_path(cleaned_archive))
            self._archive_value.setToolTip(cleaned_archive)
        elif archive_configured:
            self._archive_value.setText("未就绪")
            self._archive_value.setToolTip("")
        else:
            self._archive_value.setText("未配置")
            self._archive_value.setToolTip("")

    def run_id_text(self) -> str:
        """运行标识的显示文本（可能已中间省略）。"""
        return self._run_value.text()

    def run_id_tooltip(self) -> str:
        """完整运行标识；读取只读展示状态。"""
        return self._run_value.toolTip()

    def mode_text(self) -> str | None:
        """测试模式文本；模式行隐藏时返回 ``None``。"""
        return self._mode_value.text() if not self._mode_value.isHidden() else None

    def archive_text(self) -> str:
        """归档目录的显示文本（可能已缩短）。"""
        return self._archive_value.text()

    # ---- 前置检查 -----------------------------------------------------------

    def set_checks(self, checks: Sequence[ConsoleCheck]) -> None:
        """写入前置检查；行控件复用，多余行隐藏。"""
        if isinstance(checks, (str, bytes)) or not isinstance(checks, Sequence):
            raise TypeError("前置检查必须是 ConsoleCheck 序列")
        validated: list[ConsoleCheck] = []
        for check in checks:
            if not isinstance(check, ConsoleCheck):
                raise TypeError("前置检查必须是 ConsoleCheck 序列")
            validated.append(check)
        if len(validated) > self.MAX_CHECKS:
            raise ValueError(f"前置检查最多 {self.MAX_CHECKS} 行")
        self._checks = tuple(validated)
        while len(self._check_rows) < len(validated):
            self._add_check_row()
        for index, (led, label, detail) in enumerate(self._check_rows):
            if index < len(validated):
                check = validated[index]
                self._apply_led(led, check.state)
                label.setText(check.label)
                detail.setText(check.detail)
                led.show()
                label.show()
                detail.show()
            else:
                led.hide()
                label.hide()
                detail.hide()

    def checks(self) -> tuple[ConsoleCheck, ...]:
        """当前写入的前置检查（只读）。"""
        return self._checks

    def check_state(self, label: str) -> CheckState | None:
        """按标签取检查状态；不存在时返回 ``None``。"""
        for check in self._checks:
            if check.label == label:
                return check.state
        return None

    def _add_check_row(self) -> None:
        row = len(self._check_rows)
        led = QLabel(self.surface)
        led.setObjectName("consoleLed")
        led.setFixedSize(8, 8)
        led.setProperty("tone", CheckState.OPTIONAL.value)
        led.setAccessibleName("前置检查状态")
        label = QLabel("", self.surface)
        label.setObjectName("consoleCheckLabel")
        label.setTextFormat(Qt.TextFormat.PlainText)
        detail = QLabel("", self.surface)
        detail.setObjectName("consoleCheckDetail")
        detail.setTextFormat(Qt.TextFormat.PlainText)
        detail.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        detail.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        detail.setMinimumWidth(0)
        self._checks_grid.addWidget(led, row, 0)
        self._checks_grid.addWidget(label, row, 1)
        self._checks_grid.addWidget(detail, row, 2)
        self._checks_grid.setColumnStretch(1, 1)
        self._check_rows.append((led, label, detail))

    @staticmethod
    def _apply_led(led: QLabel, state: CheckState) -> None:
        if led.property("tone") != state.value:
            led.setProperty("tone", state.value)
            led.style().unpolish(led)
            led.style().polish(led)
            led.update()
