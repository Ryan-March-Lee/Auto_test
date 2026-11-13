"""测量工作台的阶段条：四段导航、状态显示与滑动指示块。

阶段条取代测量区的标签栏，把“01 线损、02 驱动映射、03 功放、04 可视化”变成一组
真实可聚焦的导航项。它不是页面注册表，也不复制测量状态机：阶段元数据仍来自
:mod:`presentation.qt.measurement_stage`，当前阶段与各阶段状态由调用方经
:meth:`MeasurementStageRail.set_current` / :meth:`~MeasurementStageRail.set_state`
写入，点击只发出 :attr:`MeasurementStageRail.stage_selected`。

设计约束（见 ``docs/测量工作台重构计划.md`` 步骤 3）：

- 每个阶段都是真正的 :class:`QAbstractButton` 子类，可以用 Tab 走到、用 Enter/Space
  触发，并带有 ``accessibleName``；命中区不靠画布上临时算坐标。
- 指示块是父控件自绘的一枚圆角块，用 :class:`QVariantAnimation` 移动；阶段按钮的
  位置完全由布局决定，动画不改变任何控件的几何。
- 颜色与字号只取 :mod:`presentation.qt.theme` 的令牌/字体函数，本模块不写颜色值。
- 未启用阶段保留原编号、继续可见，但不可聚焦、不可点击；没有当前阶段（例如回到
  测试配置页）时指示块隐藏，整条阶段条降低强调。`DISABLED` 只由启用状态派生，
  不能经 `set_state` 写入。
- `visualization` 是只读视图（`StageDescriptor.kind is None`），不参与运行状态：
  不画完成/未开始标记，也不接受 `set_state`；它仍然可以作为当前阶段被导航。
- 测量区的动效只做“状态确认”：指示块 180ms（``MOTION_STAGE_MS``）移动，布局和
  文字位置都不动；窗口缩放、首次显示与“关闭动效”路径直接落到目标位置。
"""

from __future__ import annotations

from math import ceil

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEvent,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFocusEvent,
    QFont,
    QFontMetricsF,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QSizePolicy, QWidget

from .measurement_stage import MEASUREMENT_STAGES, StageDescriptor, StageState
from .theme import MOTION_STAGE_MS, TOKENS, base_font, card_status_font

__all__ = [
    "MeasurementStageButton",
    "MeasurementStageRail",
]


# ---- 几何 -------------------------------------------------------------------
# 阶段条是常驻框架的一部分，高度固定，切页、改状态和换摘要都不改变它占的位置。
_STAGE_HEIGHT = 52.0
_STAGE_MIN_WIDTH = 116.0
_STAGE_PADDING_X = 10.0
_STAGE_TEXT_GAP = 8.0
_STAGE_GAP = 8.0

# 编号徽章与两行文字（标题 / 摘要）的固定基线：标题行始终在同一高度，连接线也因此
# 不会因为某个阶段没有摘要而上下串位。
_BADGE_DIAMETER = 26.0
_BADGE_TOP = 4.0
_BADGE_CENTER_Y = _BADGE_TOP + _BADGE_DIAMETER / 2.0
_TITLE_TOP = 7.0
_TITLE_HEIGHT = 20.0
_SUMMARY_TOP = 27.0
_SUMMARY_HEIGHT = 16.0

# 状态标记的位置；即使状态为“未开始”也保留这段宽度，状态变化时标题不跳。
_STATUS_GAP = 6.0
_STATUS_AREA = 12.0

_INDICATOR_INSET = 2.0
_INDICATOR_RADIUS = 8.0

#: 可以经 ``set_state`` 写入的运行状态；``DISABLED`` 不在这里，它只表示“本次配置
#: 下不启用该阶段”，由 ``set_stage_enabled`` 派生，不能成为第二个真相源。
_RUN_STATES: tuple[StageState, ...] = (
    StageState.PENDING,
    StageState.ACTIVE,
    StageState.COMPLETED,
    StageState.STOPPED,
    StageState.FAILED,
)

_STATE_LABELS = {
    StageState.PENDING: "未开始",
    StageState.ACTIVE: "进行中",
    StageState.COMPLETED: "已完成",
    StageState.STOPPED: "已停止",
    StageState.FAILED: "失败",
    StageState.DISABLED: "未启用",
}

# 编号徽章的“填充 / 描边 / 数字”三色令牌。当前阶段仍为未开始时，徽章用白面主色边，
# 与浅主色的指示块分开；完成/停止态取更深的填充或深色数字，保证 8.5pt 数字可读。
_BADGE_TOKENS = {
    StageState.PENDING: ("surface_sunken", "border", "text_secondary"),
    StageState.ACTIVE: ("primary", "primary", "surface"),
    StageState.COMPLETED: ("success_pressed", "success_pressed", "surface"),
    StageState.STOPPED: ("warning", "warning", "text"),
    StageState.FAILED: ("danger", "danger", "surface"),
    StageState.DISABLED: ("disabled_bg", "border", "disabled_text"),
}


def _color(token: str, alpha: int | None = None) -> QColor:
    """按主题令牌取颜色；需要透明时只改 alpha，不引入额外颜色值。"""
    color = QColor(TOKENS[token])
    if alpha is not None:
        color.setAlpha(alpha)
    return color


def _coerce_state(state: StageState | str) -> StageState:
    """把状态枚举或它的字符串值统一成 ``StageState``。"""
    if isinstance(state, StageState):
        return state
    if isinstance(state, str):
        try:
            return StageState(state)
        except ValueError:
            raise ValueError(f"未知阶段状态: {state!r}") from None
    raise TypeError("state 必须是 StageState 或状态字符串")


class MeasurementStageButton(QAbstractButton):
    """一个可聚焦的阶段导航项：编号、标题、摘要和状态标记。

    按钮只负责显示与发出 ``clicked``；当前阶段、运行状态与摘要都由阶段条统一写入，
    因此阶段条可以在页面切换和运行状态变化时逐个更新它们，而不需要重建控件。
    """

    def __init__(
        self, descriptor: StageDescriptor, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._descriptor = descriptor
        self._state = StageState.PENDING
        self._summary = ""
        self._current = False
        self._rail_emphasized = True
        self._stage_enabled = True
        self._hovered = False
        self._focus_visible = False

        self._title_font = base_font()
        self._title_font.setWeight(QFont.Weight.DemiBold)
        self._summary_font = card_status_font()
        self._badge_font = card_status_font()
        self._badge_font.setWeight(QFont.Weight.Bold)

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._sync_cursor()
        self.setCheckable(False)
        self._sync_accessibility()

    # ---- 数据 ---------------------------------------------------------------

    def descriptor(self) -> StageDescriptor:
        """本按钮对应的不可变阶段元数据。"""
        return self._descriptor

    def key(self) -> str:
        """阶段 key，点击时作为 ``stage_selected`` 的载荷。"""
        return self._descriptor.key

    def state(self) -> StageState:
        """调用方写入的运行状态；未启用时也保留原值，便于重新启用后恢复。"""
        return self._state

    def stage_enabled(self) -> bool:
        """调用方通过 ``set_stage_enabled`` 写入的显式启用标志。

        它和 :meth:`isEnabled` 不是一回事：父控件被临时停用时有效状态也会是
        ``False``，但显式标志仍是 ``True``。阶段条据此判断“本次配置是否启用”。
        """
        return self._stage_enabled

    def participates_in_run(self) -> bool:
        """是否参与运行状态（``visualization`` 这类只读阶段返回 ``False``）。"""
        return self._descriptor.kind is not None

    def shows_status_mark(self) -> bool:
        """是否绘制运行状态标记；只读阶段没有完成/未开始，不画标记。"""
        return self.participates_in_run()

    def display_state(self) -> StageState:
        """实际画出来的状态：显式停用或有效停用阶段固定显示为 ``DISABLED``。"""
        if not self._stage_enabled or not self.isEnabled():
            return StageState.DISABLED
        return self._state

    def set_state(self, state: StageState | str) -> None:
        """更新运行状态；不改变当前项、摘要或是否启用。

        ``DISABLED`` 不是运行状态，只由 :meth:`set_stage_enabled` 派生；只读阶段
        （``descriptor.kind is None``）不参与运行状态。两者都直接拒绝，避免阶段条
        出现两个真相源。
        """
        resolved = _coerce_state(state)
        if resolved not in _RUN_STATES:
            raise ValueError(
                "只有五个运行状态可以写入；DISABLED 由 set_stage_enabled 派生"
            )
        if not self.participates_in_run():
            raise ValueError(
                f"只读阶段不参与运行状态: {self._descriptor.key!r}"
            )
        if resolved is self._state:
            return
        self._state = resolved
        self._sync_accessibility()
        self.update()

    def set_stage_enabled(self, enabled: bool) -> None:
        """写入显式启用标志；交互状态与显示状态都由它和父级有效状态共同决定。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        if enabled == self._stage_enabled:
            return
        self._stage_enabled = enabled
        self.setEnabled(enabled)
        self._sync_cursor()
        self._sync_accessibility()
        self.update()

    def _sync_cursor(self) -> None:
        """可用时用手型光标；停用或被父级停用时回到箭头。"""
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if self.isEnabled()
            else Qt.CursorShape.ArrowCursor
        )

    def summary(self) -> str:
        """调用方写入的摘要文本；空字符串表示不显示。"""
        return self._summary

    def set_summary(self, text: str) -> None:
        """更新摘要（例如“6 频点”）；不改变状态或当前项。"""
        if not isinstance(text, str):
            raise TypeError("summary 必须是字符串")
        if text == self._summary:
            return
        self._summary = text
        self._sync_accessibility()
        self.update()

    def is_current(self) -> bool:
        """是否是阶段条当前指向的阶段。"""
        return self._current

    def set_current(self, current: bool) -> None:
        """标记当前阶段；由阶段条统一调用，按钮本身不发导航信号。"""
        if not isinstance(current, bool):
            raise TypeError("current 必须是 bool")
        if current == self._current:
            return
        self._current = current
        self._sync_accessibility()
        self.update()

    def is_rail_emphasized(self) -> bool:
        """阶段条是否有当前项；配置页时整条降低强调。"""
        return self._rail_emphasized

    def set_rail_emphasized(self, emphasized: bool) -> None:
        """由阶段条在“有/没有当前阶段”之间切换整体强调。"""
        if not isinstance(emphasized, bool):
            raise TypeError("emphasized 必须是 bool")
        if emphasized == self._rail_emphasized:
            return
        self._rail_emphasized = emphasized
        self.update()

    # ---- 尺寸 ---------------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 覆盖方法
        """宽度只由固定的阶段标题决定；摘要变化不会让按钮和指示块重新排版。"""
        title_metrics = QFontMetricsF(self._title_font, self)
        content = title_metrics.horizontalAdvance(self._descriptor.title)
        width = ceil(
            _STAGE_PADDING_X
            + _BADGE_DIAMETER
            + _STAGE_TEXT_GAP
            + content
            + _STATUS_GAP
            + _STATUS_AREA
            + _STAGE_PADDING_X
        )
        return QSize(max(ceil(_STAGE_MIN_WIDTH), width), ceil(_STAGE_HEIGHT))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 覆盖方法
        title_metrics = QFontMetricsF(self._title_font, self)
        width = ceil(
            _STAGE_PADDING_X
            + _BADGE_DIAMETER
            + _STAGE_TEXT_GAP
            + title_metrics.horizontalAdvance(self._descriptor.title)
            + _STATUS_GAP
            + _STATUS_AREA
            + _STAGE_PADDING_X
        )
        return QSize(max(ceil(_STAGE_MIN_WIDTH), width), ceil(_STAGE_HEIGHT))

    # ---- 交互 ---------------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt 覆盖方法
        """Enter 与 Space 都激活阶段；Space 仍由 ``QAbstractButton`` 处理。"""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.isEnabled():
                self.click()
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 - Qt 覆盖方法
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt 覆盖方法
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt 覆盖方法
        if event.type() in (
            QEvent.Type.EnabledChange,
            QEvent.Type.FontChange,
        ):
            if event.type() == QEvent.Type.EnabledChange:
                self._sync_cursor()
            self._sync_accessibility()
            self.update()
        super().changeEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt 覆盖方法
        """只有 Tab/快捷键走过来的焦点才画焦点框，鼠标点出来的焦点不画。"""
        self._focus_visible = event.reason() not in (
            Qt.FocusReason.MouseFocusReason,
            Qt.FocusReason.ActiveWindowFocusReason,
            Qt.FocusReason.PopupFocusReason,
            Qt.FocusReason.OtherFocusReason,
        )
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt 覆盖方法
        self._focus_visible = False
        self.update()
        super().focusOutEvent(event)

    def shows_focus_ring(self) -> bool:
        """焦点框当前是否可见：只有键盘走过来的焦点画它。"""
        return self._focus_visible

    # ---- 绘制 ---------------------------------------------------------------

    def _title_rect(self) -> QRectF:
        left = _STAGE_PADDING_X + _BADGE_DIAMETER + _STAGE_TEXT_GAP
        width = (
            self.width()
            - left
            - _STATUS_GAP
            - _STATUS_AREA
            - _STAGE_PADDING_X
        )
        return QRectF(left, _TITLE_TOP, max(0.0, width), _TITLE_HEIGHT)

    def _summary_rect(self) -> QRectF:
        title = self._title_rect()
        return QRectF(
            title.left(),
            _SUMMARY_TOP,
            title.width(),
            _SUMMARY_HEIGHT,
        )

    def _badge_rect(self) -> QRectF:
        return QRectF(
            _STAGE_PADDING_X,
            _BADGE_TOP,
            _BADGE_DIAMETER,
            _BADGE_DIAMETER,
        )

    def _status_center(self) -> QPointF:
        title = self._title_rect()
        return QPointF(
            title.right() + _STATUS_GAP + _STATUS_AREA / 2.0,
            title.center().y(),
        )

    def _text_color(self, state: StageState) -> QColor:
        if not self._stage_enabled or not self.isEnabled():
            return _color("disabled_text")
        if not self._rail_emphasized:
            return _color("text_secondary")
        if state is StageState.PENDING and not self._current:
            return _color("text_secondary")
        return _color("text")

    def _badge_tokens(self, state: StageState) -> tuple[str, str, str]:
        if state is StageState.PENDING and self._current:
            # 白面 + 主色边：与浅主色指示块分开，“当前但未开始”仍然一眼可见。
            return ("surface", "primary", "primary")
        return _BADGE_TOKENS[state]

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 覆盖方法
        """悬停底 → 编号徽章 → 标题/摘要 → 状态标记 → 焦点框。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        state = self.display_state()
        if self._hovered and self.isEnabled() and not self._current:
            # 悬停只提示“这里可以点”，不能和浅主色的当前指示块撞脸；因此用中性
            # 浅底 + 稍深描边，当前阶段仍由指示块和徽章标记。
            painter.setPen(QPen(_color("border_strong"), 1.0))
            painter.setBrush(_color("surface_sunken"))
            painter.drawRoundedRect(
                QRectF(self.rect()).adjusted(
                    _INDICATOR_INSET,
                    _INDICATOR_INSET,
                    -_INDICATOR_INSET,
                    -_INDICATOR_INSET,
                ),
                _INDICATOR_RADIUS,
                _INDICATOR_RADIUS,
            )

        self._paint_badge(painter, state)
        self._paint_text(painter, state)
        self._paint_status_mark(painter, state)

        if self._focus_visible:
            self._paint_focus_ring(painter)

    def _paint_badge(self, painter: QPainter, state: StageState) -> None:
        fill_token, border_token, text_token = self._badge_tokens(state)
        badge = self._badge_rect()
        painter.setPen(QPen(_color(border_token), 1.0))
        painter.setBrush(_color(fill_token))
        painter.drawEllipse(badge)

        painter.save()
        painter.setFont(self._badge_font)
        painter.setPen(_color(text_token))
        painter.drawText(
            badge,
            int(Qt.AlignmentFlag.AlignCenter),
            f"{self._descriptor.order:02d}",
        )
        painter.restore()

    def _paint_text(self, painter: QPainter, state: StageState) -> None:
        title_rect = self._title_rect()
        title_metrics = QFontMetricsF(self._title_font, self)
        title = title_metrics.elidedText(
            self._descriptor.title,
            Qt.TextElideMode.ElideRight,
            title_rect.width(),
        )
        painter.save()
        painter.setFont(self._title_font)
        painter.setPen(self._text_color(state))
        painter.drawText(
            title_rect,
            int(
                Qt.AlignmentFlag.AlignLeft
                | Qt.AlignmentFlag.AlignVCenter
            ),
            title,
        )

        if self._summary:
            summary_rect = self._summary_rect()
            summary_metrics = QFontMetricsF(self._summary_font, self)
            summary = summary_metrics.elidedText(
                self._summary,
                Qt.TextElideMode.ElideRight,
                summary_rect.width(),
            )
            painter.setFont(self._summary_font)
            painter.setPen(
                _color(
                    "disabled_text"
                    if not self.isEnabled()
                    else "text_muted"
                )
            )
            painter.drawText(
                summary_rect,
                int(
                    Qt.AlignmentFlag.AlignLeft
                    | Qt.AlignmentFlag.AlignVCenter
                ),
                summary,
            )
        painter.restore()

    def _paint_status_mark(self, painter: QPainter, state: StageState) -> None:
        """把运行状态画成一个小标记：完成是勾、进行中是实心点、失败/停止是感叹与横杠。"""
        if not self.shows_status_mark():
            return
        center = self._status_center()
        radius = 5.0

        if state is StageState.DISABLED:
            painter.setPen(QPen(_color("disabled_text"), 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, 3.5, 3.5)
            return

        if state is StageState.PENDING:
            painter.setPen(QPen(_color("border_strong"), 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, 4.0, 4.0)
            return

        if state is StageState.ACTIVE:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_color("primary", 46))
            painter.drawEllipse(center, radius + 3.0, radius + 3.0)
            painter.setBrush(_color("primary"))
            painter.drawEllipse(center, radius, radius)
            return

        if state is StageState.COMPLETED:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_color("success"))
            painter.drawEllipse(center, radius, radius)
            pen = QPen(_color("surface"), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(
                QPointF(center.x() - 2.3, center.y()),
                QPointF(center.x() - 0.6, center.y() + 1.8),
            )
            painter.drawLine(
                QPointF(center.x() - 0.6, center.y() + 1.8),
                QPointF(center.x() + 2.5, center.y() - 2.0),
            )
            return

        if state is StageState.STOPPED:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_color("warning"))
            painter.drawEllipse(center, radius, radius)
            painter.setPen(QPen(_color("surface"), 1.8))
            painter.drawLine(
                QPointF(center.x() - 2.4, center.y()),
                QPointF(center.x() + 2.4, center.y()),
            )
            return

        # FAILED
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_color("danger"))
        painter.drawEllipse(center, radius, radius)
        painter.setPen(QPen(_color("surface"), 1.8))
        painter.drawLine(
            QPointF(center.x(), center.y() - 2.2),
            QPointF(center.x(), center.y() + 0.9),
        )
        painter.setBrush(_color("surface"))
        painter.drawEllipse(QPointF(center.x(), center.y() + 2.8), 1.0, 1.0)

    def _paint_focus_ring(self, painter: QPainter) -> None:
        painter.setPen(QPen(_color("primary"), 2.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5),
            _INDICATOR_RADIUS,
            _INDICATOR_RADIUS,
        )

    # ---- 可访问性 -----------------------------------------------------------

    def _sync_accessibility(self) -> None:
        # ``QWidget`` 构造期间可能收到 FontChange/EnabledChange；此时 Python 侧字段
        # 还没铺好，直接跳过，等构造末尾的显式同步补上。
        if not hasattr(self, "_descriptor"):
            return
        descriptor = self._descriptor
        summary = f"；摘要：{self._summary}" if self._summary else ""
        current_text = "当前阶段；" if self._current else ""
        if self.participates_in_run():
            status_text = _STATE_LABELS[self.display_state()]
            state_note = f"当前状态：{status_text}"
        elif not self._stage_enabled:
            status_text = "未启用"
            state_note = "未启用"
        else:
            status_text = "只读视图"
            state_note = "只读视图，不参与测量状态"
        self.setAccessibleName(
            f"阶段 {descriptor.order:02d} {descriptor.title}"
        )
        self.setAccessibleDescription(
            f"{descriptor.subtitle}；{current_text}{state_note}{summary}"
        )
        self.setToolTip(
            f"{descriptor.order:02d} {descriptor.title}："
            f"{descriptor.subtitle}（{status_text}）{summary}"
        )


class MeasurementStageRail(QWidget):
    """常驻测量区的四段导航：连接线 + 可聚焦阶段按钮 + 滑动指示块。

    调用方只负责写入“当前在哪、每个阶段什么状态、摘要是什么”，阶段条不读配置、
    不读控制器、也不切换页面；点击只发出 :attr:`stage_selected`，由窗口完成页面切换。
    """

    stage_selected = Signal(str)

    def __init__(
        self,
        stages: tuple[StageDescriptor, ...] = MEASUREMENT_STAGES,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not stages:
            raise ValueError("阶段条至少需要一个阶段")
        if len({stage.key for stage in stages}) != len(stages):
            raise ValueError("阶段条的阶段 key 不能重复")

        self._buttons: list[MeasurementStageButton] = []
        self._buttons_by_key: dict[str, MeasurementStageButton] = {}
        self._current_key: str | None = None
        self._indicator_visible = False
        self._indicator_center = 0.0
        self._indicator_width = 0.0
        self._animations_enabled = True

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(_STAGE_GAP)

        for descriptor in stages:
            button = MeasurementStageButton(descriptor, self)
            button.clicked.connect(
                lambda _checked=False, target=button: self._on_button_clicked(
                    target
                )
            )
            self._layout.addWidget(button, 1)
            self._buttons.append(button)
            self._buttons_by_key[descriptor.key] = button

        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.setFixedHeight(ceil(_STAGE_HEIGHT))
        self._set_rail_emphasis(False)

        self._indicator_animation = QVariantAnimation(self)
        self._indicator_animation.setDuration(MOTION_STAGE_MS)
        self._indicator_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._indicator_animation.valueChanged.connect(
            self._on_indicator_value
        )
        self._indicator_animation.finished.connect(
            self._on_indicator_finished
        )

    # ---- 读取 ---------------------------------------------------------------

    def stage_buttons(self) -> tuple[MeasurementStageButton, ...]:
        """按显示顺序返回阶段按钮，供测试与外壳做细粒度检查。"""
        return tuple(self._buttons)

    def stage_button(self, key: str) -> MeasurementStageButton:
        """按 key 返回阶段按钮；非字符串抛 ``TypeError``，未知 key 抛 ``KeyError``。"""
        if not isinstance(key, str):
            raise TypeError("阶段 key 必须是字符串")
        try:
            return self._buttons_by_key[key]
        except KeyError:
            raise KeyError(f"未知测量阶段: {key!r}") from None

    def current_key(self) -> str | None:
        """当前阶段的 key；配置页等没有当前阶段时为 ``None``。"""
        return self._current_key

    def state_for(self, key: str) -> StageState:
        """按钮上保存的运行状态（未启用时仍返回保存值）。"""
        return self.stage_button(key).state()

    def display_state_for(self, key: str) -> StageState:
        """实际显示的状态：未启用阶段返回 ``DISABLED``。"""
        return self.stage_button(key).display_state()

    def participates_in_run(self, key: str) -> bool:
        """该阶段是否参与运行状态；``visualization`` 这类只读阶段为 ``False``。"""
        return self.stage_button(key).participates_in_run()

    def summary_for(self, key: str) -> str:
        """按钮上的摘要文本。"""
        return self.stage_button(key).summary()

    def is_stage_enabled(self, key: str) -> bool:
        """该阶段当前是否可点击、可聚焦。"""
        return self.stage_button(key).isEnabled()

    def indicator_visible(self) -> bool:
        """指示块是否显示；没有当前阶段时为 ``False``。"""
        return self._indicator_visible and self._current_key is not None

    def indicator_rect(self) -> QRectF | None:
        """当前指示块在阶段条坐标系里的矩形；隐藏时为 ``None``。

        动画进行中返回的是插值位置，调用方可以据此做像素检查或与阶段按钮对齐。
        """
        if not self.indicator_visible():
            return None
        button = self._buttons_by_key[self._current_key]
        return QRectF(
            self._indicator_center - self._indicator_width / 2.0,
            button.y() + _INDICATOR_INSET,
            self._indicator_width,
            button.height() - 2.0 * _INDICATOR_INSET,
        )

    def indicator_animation(self) -> QVariantAnimation:
        """指示块的移动动画；测试与排障可据此推进时间。"""
        return self._indicator_animation

    def is_animating(self) -> bool:
        """指示块是否正在移动。"""
        return (
            self._indicator_animation.state()
            == QAbstractAnimation.State.Running
        )

    def animations_enabled(self) -> bool:
        """阶段条动效是否开启。"""
        return self._animations_enabled

    # ---- 写入 ---------------------------------------------------------------

    def set_current(self, key: str | None) -> None:
        """设置当前阶段；``None`` 表示没有当前阶段（例如测试配置页）。

        同一 key 重复设置是幂等的：外部页面同步不会打断已经在播的指示块动画。
        """
        if key is not None and not isinstance(key, str):
            raise TypeError("当前阶段 key 必须是字符串或 None")
        if key is not None:
            self.stage_button(key)

        previous = self._current_key
        self._current_key = key
        for button in self._buttons:
            button.set_current(button.key() == key)

        if key is None:
            self._indicator_animation.stop()
            self._indicator_visible = False
            self._indicator_center = 0.0
            self._indicator_width = 0.0
            self._set_rail_emphasis(False)
            self.update()
            return

        self._set_rail_emphasis(True)
        self._layout.activate()
        if key == previous:
            self._indicator_visible = True
            if not self.is_animating():
                self._snap_indicator_to_current()
            self.update()
            return

        self._indicator_visible = True
        target = self._target_indicator_center()
        self._indicator_width = self._target_indicator_width()

        if (
            not self._animations_enabled
            or not self.isVisible()
            or previous is None
        ):
            self._indicator_center = target
            self.update()
            return

        self._indicator_animation.stop()
        self._indicator_animation.setStartValue(float(self._indicator_center))
        self._indicator_animation.setEndValue(float(target))
        self._indicator_animation.start()

    def set_state(self, key: str, state: StageState | str) -> None:
        """更新一个阶段的运行状态；不改变当前项、摘要或是否启用。

        只接受五个运行状态；``DISABLED`` 与只读阶段都会抛出 ``ValueError``。
        """
        self.stage_button(key).set_state(state)

    def set_summary(self, key: str, text: str) -> None:
        """更新一个阶段的摘要；不改变状态或当前项。"""
        self.stage_button(key).set_summary(text)

    def set_stage_enabled(self, key: str, enabled: bool) -> None:
        """启用/停用一个阶段；停用只影响交互与显示，编号和保存的状态都不丢。

        比较显式启用标志而不是 ``isEnabled()``：父控件被临时停用时有效状态也是
        ``False``，按有效状态比较会把调用方的启用/停用意图丢掉。停用当前阶段不会
        自动改 ``current``：阶段条是视图，切到哪一页仍由窗口在切页后同步。
        """
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        button = self.stage_button(key)
        if button.stage_enabled() == enabled:
            return
        button.set_stage_enabled(enabled)
        if self._current_key == key:
            self._layout.activate()
            self._indicator_animation.stop()
            self._snap_indicator_to_current()
        self.update()

    def set_animations_enabled(self, enabled: bool) -> None:
        """关闭动效时立即落到目标位置；不改变任何布局或状态。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        if enabled == self._animations_enabled:
            return
        self._animations_enabled = enabled
        if not enabled:
            self._indicator_animation.stop()
            if self._current_key is not None:
                self._snap_indicator_to_current()
                self.update()

    # ---- 内部：动画与几何 ---------------------------------------------------

    def _on_button_clicked(self, button: MeasurementStageButton) -> None:
        """点击只发出导航请求，不写 ``current``。

        当前阶段属于窗口的页面状态：窗口接受请求、真正切页后再调用
        :meth:`set_current`。这样页面切换被拒绝或重定向时，阶段条不会先指向一个
        并没有显示的页面；同值同步仍是幂等的，不会打断指示块动画。
        """
        if not button.isEnabled():
            return
        self.stage_selected.emit(button.key())

    def _target_indicator_center(self) -> float:
        if self._current_key is None:
            return 0.0
        button = self._buttons_by_key[self._current_key]
        return QRectF(button.geometry()).center().x()

    def _target_indicator_width(self) -> float:
        if self._current_key is None:
            return 0.0
        button = self._buttons_by_key[self._current_key]
        return max(
            0.0, float(button.width()) - 2.0 * _INDICATOR_INSET
        )

    def _snap_indicator_to_current(self) -> None:
        if self._current_key is None:
            return
        self._layout.activate()
        self._indicator_center = self._target_indicator_center()
        self._indicator_width = self._target_indicator_width()
        self._indicator_visible = True

    def _set_rail_emphasis(self, emphasized: bool) -> None:
        for button in self._buttons:
            button.set_rail_emphasized(emphasized)

    def _on_indicator_value(self, value: float) -> None:
        if not self._indicator_visible:
            return
        self._indicator_center = float(value)
        self.update()

    def _on_indicator_finished(self) -> None:
        if not self._indicator_visible:
            return
        self._snap_indicator_to_current()
        self.update()

    # ---- 内部：绘制与事件 ---------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 覆盖方法
        """先画连接线与指示块，再由子控件把编号和文字覆盖上去。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        line_token = (
            "border_strong"
            if self._current_key is not None
            else "border"
        )
        pen = QPen(_color(line_token), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)

        buttons = self._buttons
        for first, second in zip(buttons, buttons[1:]):
            start = QPointF(
                float(first.geometry().right()) + 1.0,
                float(first.y()) + _BADGE_CENTER_Y,
            )
            end = QPointF(
                float(second.geometry().left()) - 1.0,
                float(second.y()) + _BADGE_CENTER_Y,
            )
            if end.x() > start.x():
                painter.drawLine(start, end)

        rect = self.indicator_rect()
        if rect is not None:
            painter.setPen(QPen(_color("primary_soft_border"), 1.0))
            painter.setBrush(_color("primary_soft"))
            painter.drawRoundedRect(
                rect, _INDICATOR_RADIUS, _INDICATOR_RADIUS
            )

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 覆盖方法
        """窗口缩放后指示块必须跟住当前按钮，不能停在旧坐标。"""
        super().resizeEvent(event)
        if self._current_key is None:
            return
        self._indicator_animation.stop()
        self._snap_indicator_to_current()
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 覆盖方法
        """首次显示时布局已经有几何，指示块直接落到当前阶段。"""
        super().showEvent(event)
        if self._current_key is None:
            return
        self._indicator_animation.stop()
        self._snap_indicator_to_current()
        self.update()
