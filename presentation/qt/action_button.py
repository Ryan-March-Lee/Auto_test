"""页面主操作按钮与描边胶囊按钮：从中心涌出的圆形填充 + 外圈光晕 + 悬停上浮。

复刻参考按钮（Uiverse 的 ``animated-button``）在浅色主题下的两种状态：

- **不可用**：灰色胶囊、灰字、一圈细灰边，鼠标不能点，也不响应悬停；
- **可用**：蓝色胶囊、白字与 5px 蓝色光晕。按钮第一次变为可用时，一颗蓝色圆形色块
  从中心（20px 直径）展开到盖满整枚胶囊（0.8s，``cubic-bezier(0.23, 1, 0.32, 1)``），
  外圈从 2px 细边长成 5px 光晕、文字被色块扫过之处转白——参考按钮的悬停画面在这里
  就是“可用”的终态。可用状态下鼠标移入再轻微上浮 2px，按下时整枚胶囊缩到 0.95。

样式表表达不了伪元素、``transition``、圆形裁剪与 ``transform``，所以整块自绘
（与欢迎页的 :class:`~presentation.qt.welcome_page.ModeChoiceButton` 同一做法）：
``paintEvent`` 依次画投影、胶囊、圆形填充、光晕、文字与焦点框，数值由
``QVariantAnimation`` 推进。颜色仍取自 :mod:`presentation.qt.theme` 的令牌，控件本身
不写主题之外的颜色常量。

按钮仍是 :class:`QPushButton` 的子类：``clicked``、``setEnabled``、``setToolTip`` 与键盘
操作照旧，页面只负责在“是否勾选了设备”变化时调用 :meth:`QWidget.setEnabled`。

同一模块还提供 :class:`OutlineActionButton`：胶囊高度、圆角、左右内边距与字号和上面的
主操作按钮完全一致，但只有白底黑框黑字 / 白底灰框灰字两种静止状态，没有蓝色填充。
仪表配置页的“全选”用它，因此两个按钮并排时轮廓一样高。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
)
from PySide6.QtGui import (
    QColor,
    QFocusEvent,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from .theme import FONT_FAMILIES, TOKENS

#: 胶囊本体与文字间距；控件四边另留 :data:`GLOW_MARGIN` 画光晕、上浮投影与按下缩放。
PILL_HEIGHT = 30.0
PILL_PADDING_X = 22.0

#: 光晕最宽 5px，上浮 2px，按下缩放留一点余量：四边各留 8px 才不会被控件边界裁掉。
GLOW_MARGIN = 8.0
LIFT_OFFSET = 2.0
PRESS_SCALE = 0.95

#: 参考按钮的 2px 细边与 5px 光晕；可用状态在两者之间随填充进度过渡。
RING_WIDTH_IDLE = 2.0
RING_WIDTH_ACTIVE = 5.0

#: 描边按钮（“全选”）的边框宽度：与主按钮静止时的外圈同宽，并排时轮廓一样粗。
OUTLINE_WIDTH = 2.0

#: 填充动画 0.8s，上浮 0.18s；参考按钮的 ``transition`` 是 0.6s / 0.8s，上浮是额外
#: 要求的轻量反馈，不跟着走满 0.6s。
FILL_DURATION = 800
LIFT_DURATION = 180

#: 按钮文字：主题正文略大半号、半粗，作为整页唯一的推进动作。
_POINT_SIZE = 10.0


def _activation_curve() -> QEasingCurve:
    """参考按钮的 ``cubic-bezier(0.23, 1, 0.320, 1)``：起步快、收尾缓。"""
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(
        QPointF(0.23, 1.0), QPointF(0.32, 1.0), QPointF(1.0, 1.0)
    )
    return curve


def _mix(start: QColor, end: QColor, progress: float) -> QColor:
    """两色之间按进度取值；透明通道一起过渡。"""
    progress = max(0.0, min(1.0, progress))
    return QColor(
        round(start.red() + (end.red() - start.red()) * progress),
        round(start.green() + (end.green() - start.green()) * progress),
        round(start.blue() + (end.blue() - start.blue()) * progress),
        round(start.alpha() + (end.alpha() - start.alpha()) * progress),
    )


class AnimatedActionButton(QPushButton):
    """主操作按钮：不可用时是灰色胶囊，可用时蓝色填充展开，悬停轻微上浮。

    动效状态只有两个进度值：``_fill``（0 灰白 → 1 蓝色盖满）与 ``_lift``（0 落位 →
    1 上浮）。可用后 ``_fill`` 自动走满，之后一直保持；鼠标进出只改 ``_lift``。
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self._font = QFont()
        self._font.setFamilies(list(FONT_FAMILIES))
        self._font.setPointSizeF(_POINT_SIZE)
        self._font.setWeight(QFont.DemiBold)

        #: 激活进度（圆形填充与外圈光晕）与上浮进度。
        self._fill = 0.0
        self._lift = 0.0
        #: 焦点框只在键盘走到这里时画：鼠标点出来的焦点不该在胶囊外留一圈蓝线。
        self._focus_visible = False

        self._fill_animation = QVariantAnimation(self)
        self._fill_animation.setEasingCurve(_activation_curve())
        self._fill_animation.valueChanged.connect(self._set_fill)

        self._lift_animation = QVariantAnimation(self)
        self._lift_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._lift_animation.valueChanged.connect(self._set_lift)

        self._apply_fixed_size()

    # ---- 尺寸 ---------------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 覆盖方法
        text_width = QFontMetricsF(self._font, self).horizontalAdvance(self.text())
        return QSize(
            round(text_width + 2.0 * PILL_PADDING_X + 2.0 * GLOW_MARGIN),
            round(PILL_HEIGHT + 2.0 * GLOW_MARGIN),
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 覆盖方法
        return self.sizeHint()

    def _apply_fixed_size(self) -> None:
        """钉住设计尺寸：样式表给 ``QPushButton`` 补的 ``min-height`` 不能把胶囊压扁。"""
        self.setFixedSize(self.sizeHint())

    # ---- 状态与动画 ---------------------------------------------------------

    def _set_fill(self, value: float) -> None:
        self._fill = float(value)
        self.update()

    def _set_lift(self, value: float) -> None:
        self._lift = float(value)
        self.update()

    @staticmethod
    def _start(
        animation: QVariantAnimation, current: float, target: float, duration: int
    ) -> None:
        """从当前进度走到目标进度；剩余距离越短用时越短，中途折返不会重新走满。"""
        animation.stop()
        distance = abs(target - current)
        if distance < 1e-4:
            return
        animation.setDuration(max(1, round(duration * distance)))
        animation.setStartValue(current)
        animation.setEndValue(target)
        animation.start()

    def _refresh_cursor(self) -> None:
        self.setCursor(Qt.PointingHandCursor if self.isEnabled() else Qt.ArrowCursor)

    def changeEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        if event.type() == QEvent.Type.StyleChange:
            self._apply_fixed_size()
        elif event.type() == QEvent.Type.EnabledChange:
            if self.isEnabled():
                # 可用后的第一次显示（或从灰色切到可用）自动播放参考按钮的悬停画面：
                # 圆形色块展开、光晕变宽、文字转白，之后保持这个“可以点”的样子。
                if self.isVisible():
                    self._start(self._fill_animation, self._fill, 1.0, FILL_DURATION)
                    if self.underMouse():
                        self._start(
                            self._lift_animation, self._lift, 1.0, LIFT_DURATION
                        )
            else:
                # 被禁用时鼠标正停在上面也收不到 ``leaveEvent``：这里替它回到落位。
                self._fill_animation.stop()
                self._lift_animation.stop()
                self._fill = 0.0
                self._lift = 0.0
            self._refresh_cursor()
            self.update()
        super().changeEvent(event)

    def showEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        super().showEvent(event)
        # 页面可能先 ``setEnabled(True)`` 再显示按钮：真正的展开要等用户看得见时再播。
        if self.isEnabled():
            self._start(self._fill_animation, self._fill, 1.0, FILL_DURATION)

    def hideEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """页面切走或结果清空时立刻回到静止态，避免带着半块蓝色再出现。"""
        self._fill_animation.stop()
        self._lift_animation.stop()
        self._fill = 0.0
        self._lift = 0.0
        super().hideEvent(event)

    def enterEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        if self.isEnabled():
            self._start(self._lift_animation, self._lift, 1.0, LIFT_DURATION)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        self._start(self._lift_animation, self._lift, 0.0, LIFT_DURATION)
        super().leaveEvent(event)

    def focusInEvent(self, event: QFocusEvent):  # noqa: N802 - Qt 覆盖方法
        """只有 Tab/快捷键走过来的焦点才画焦点框（与网页的 ``:focus-visible`` 同理）。"""
        self._focus_visible = event.reason() not in (
            Qt.FocusReason.MouseFocusReason,
            Qt.FocusReason.ActiveWindowFocusReason,
            Qt.FocusReason.PopupFocusReason,
            Qt.FocusReason.OtherFocusReason,
        )
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent):  # noqa: N802 - Qt 覆盖方法
        self._focus_visible = False
        self.update()
        super().focusOutEvent(event)

    def shows_focus_ring(self) -> bool:
        """焦点框当前是否可见：只有键盘走过来的焦点画它，鼠标点出来的不算。"""
        return self._focus_visible

    # ---- 绘制 ---------------------------------------------------------------

    def _pill_rect(self) -> QRectF:
        """胶囊本体：四边留出光晕与投影的余地，悬停时整枚向上抬 ``LIFT_OFFSET``。"""
        pill = QRectF(self.rect()).adjusted(
            GLOW_MARGIN, GLOW_MARGIN, -GLOW_MARGIN, -GLOW_MARGIN
        )
        pill.translate(0.0, -LIFT_OFFSET * self._lift)
        return pill

    def _fill_circle(self, pill: QRectF) -> QPainterPath:
        """当前圆形色块：从中心 20px 直径展开到足以盖住整枚胶囊。"""
        start_radius = 10.0
        target_radius = (
            (pill.width() ** 2 + pill.height() ** 2) ** 0.5 / 2.0 + 2.0
        )
        radius = start_radius + (target_radius - start_radius) * self._fill
        circle = QPainterPath()
        circle.addEllipse(pill.center(), radius, radius)
        return circle

    def _fill_opacity(self) -> float:
        """圆形填充的可见度：参考按钮的 ``opacity 0 → 1``。

        在白色胶囊上按 0 → 1 线性淡入会让中途的蓝几乎看不见，压在圆里的白字也跟着
        发灰；这里让它在进度前五分之一内就到满，形状仍从 20px 慢慢铺开，既保留“淡入
        再展开”的顺序，又保证扫过的字一开始就压在实色蓝上。
        """
        return min(1.0, 0.5 + 2.5 * self._fill)

    def _paint_shadow(self, painter: QPainter, pill: QRectF) -> None:
        """上浮时胶囊底下的一层柔影：越浮越高，影子越往下、越散。"""
        if not self.isEnabled() or self._lift <= 0.01:
            return
        radius = pill.height() / 2.0
        offset = 2.0 + 3.0 * self._lift
        shadow_color = QColor(TOKENS["tooltip_bg"])
        painter.setPen(Qt.NoPen)
        # 由外到内叠几层低透明度轮廓：不需要模糊滤镜也能得到柔边。
        for spread, alpha in ((4.0, 9), (2.5, 13), (1.0, 17)):
            tone = QColor(shadow_color)
            tone.setAlpha(round(alpha * self._lift))
            painter.setBrush(tone)
            shadow = pill.translated(0.0, offset).adjusted(
                -spread, -spread, spread, spread
            )
            painter.drawRoundedRect(
                shadow, radius + spread, radius + spread
            )

    def _paint_ring(self, painter: QPainter, pill: QRectF) -> None:
        """胶囊外圈：不可用时是细灰边，可用后随填充长成蓝色光晕。"""
        if self.isEnabled():
            width = RING_WIDTH_IDLE + (
                RING_WIDTH_ACTIVE - RING_WIDTH_IDLE
            ) * self._fill
            color = _mix(
                _with_alpha(QColor(TOKENS["primary"]), 0.22),
                _with_alpha(QColor(TOKENS["primary"]), 0.42),
                self._fill,
            )
        else:
            width = RING_WIDTH_IDLE
            color = QColor(TOKENS["border"])
        ring = pill.adjusted(-width / 2.0, -width / 2.0, width / 2.0, width / 2.0)
        painter.setPen(QPen(color, width))
        painter.setBrush(Qt.NoBrush)
        radius = ring.height() / 2.0
        painter.drawRoundedRect(ring, radius, radius)

    def paintEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """投影 → 胶囊 → 圆形填充 → 外圈光晕 → 文字 → 焦点框。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        pill = self._pill_rect()
        if self.isDown() and self.isEnabled():
            # 参考按钮的 ``:active { scale: 0.95 }``：围绕胶囊中心缩放，不动版面。
            painter.translate(pill.center())
            painter.scale(PRESS_SCALE, PRESS_SCALE)
            painter.translate(-pill.center())

        radius = pill.height() / 2.0
        capsule = QPainterPath()
        capsule.addRoundedRect(pill, radius, radius)

        self._paint_shadow(painter, pill)

        painter.setPen(Qt.NoPen)
        painter.setBrush(
            QColor(TOKENS["disabled_bg"])
            if not self.isEnabled()
            else QColor(TOKENS["surface"])
        )
        painter.drawPath(capsule)

        if self.isEnabled() and self._fill > 0.0:
            painter.save()
            painter.setClipPath(capsule)
            painter.setOpacity(self._fill_opacity())
            painter.setBrush(
                QColor(
                    TOKENS["primary_pressed"]
                    if self.isDown()
                    else TOKENS["primary"]
                )
            )
            painter.drawPath(self._fill_circle(pill))
            painter.restore()

        self._paint_ring(painter, pill)
        self._paint_text(painter, pill, capsule)

        if self._focus_visible:
            focus = pill.adjusted(
                -(RING_WIDTH_ACTIVE + 3.0),
                -(RING_WIDTH_ACTIVE + 3.0),
                RING_WIDTH_ACTIVE + 3.0,
                RING_WIDTH_ACTIVE + 3.0,
            )
            painter.setPen(QPen(QColor(TOKENS["primary"]), 2.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(focus, focus.height() / 2.0, focus.height() / 2.0)

    def _paint_text(
        self, painter: QPainter, pill: QRectF, capsule: QPainterPath
    ) -> None:
        """文字：不可用是灰字；可用时压在色块上的部分转白，扫到哪儿白到哪儿。"""
        painter.setFont(self._font)
        if not self.isEnabled():
            painter.setPen(QColor(TOKENS["disabled_text"]))
            painter.drawText(pill, int(Qt.AlignCenter), self.text())
            return

        painter.setPen(QColor(TOKENS["primary"]))
        painter.drawText(pill, int(Qt.AlignCenter), self.text())
        if self._fill <= 0.0:
            return
        swept = self._fill_circle(pill).intersected(capsule)
        painter.save()
        painter.setClipPath(swept)
        painter.setOpacity(self._fill_opacity())
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(pill, int(Qt.AlignCenter), self.text())
        painter.restore()


class OutlineActionButton(QPushButton):
    """描边胶囊按钮：可用是白底黑框黑字，不可用是白底灰框灰字。

    它与 :class:`AnimatedActionButton` 共用胶囊高度、圆角、左右内边距与字号，因此并排
    时两个按钮的轮廓一样高；但它没有蓝色填充与上浮动效，点击“全选”后直接进入不可用
    态，边框和文字同时转灰，用户取消任意一张卡片后重新变黑。白底在两种状态下都不变，
    所以禁用时不会像主操作按钮那样整块变灰。
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self._font = QFont()
        self._font.setFamilies(list(FONT_FAMILIES))
        self._font.setPointSizeF(_POINT_SIZE)
        self._font.setWeight(QFont.DemiBold)

        #: 焦点框只在键盘走到这里时画。
        self._focus_visible = False
        self._apply_fixed_size()

    # ---- 尺寸 ---------------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 覆盖方法
        text_width = QFontMetricsF(self._font, self).horizontalAdvance(self.text())
        return QSize(
            round(text_width + 2.0 * PILL_PADDING_X + 2.0 * GLOW_MARGIN),
            round(PILL_HEIGHT + 2.0 * GLOW_MARGIN),
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 覆盖方法
        return self.sizeHint()

    def _apply_fixed_size(self) -> None:
        self.setFixedSize(self.sizeHint())

    # ---- 状态 ---------------------------------------------------------------

    def changeEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        if event.type() == QEvent.Type.StyleChange:
            self._apply_fixed_size()
        elif event.type() == QEvent.Type.EnabledChange:
            self.setCursor(
                Qt.PointingHandCursor if self.isEnabled() else Qt.ArrowCursor
            )
            self.update()
        super().changeEvent(event)

    def focusInEvent(self, event: QFocusEvent):  # noqa: N802 - Qt 覆盖方法
        """只有 Tab/快捷键走过来的焦点才画焦点框。"""
        self._focus_visible = event.reason() not in (
            Qt.FocusReason.MouseFocusReason,
            Qt.FocusReason.ActiveWindowFocusReason,
            Qt.FocusReason.PopupFocusReason,
            Qt.FocusReason.OtherFocusReason,
        )
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent):  # noqa: N802 - Qt 覆盖方法
        self._focus_visible = False
        self.update()
        super().focusOutEvent(event)

    def shows_focus_ring(self) -> bool:
        """焦点框当前是否可见：只有键盘走过来的焦点画它，鼠标点出来的不算。"""
        return self._focus_visible

    # ---- 绘制 ---------------------------------------------------------------

    def _pill_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(
            GLOW_MARGIN, GLOW_MARGIN, -GLOW_MARGIN, -GLOW_MARGIN
        )

    def paintEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """白底 → 2px 边框 → 文字 → 键盘焦点框。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        pill = self._pill_rect()
        radius = pill.height() / 2.0
        capsule = QPainterPath()
        capsule.addRoundedRect(pill, radius, radius)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(TOKENS["surface"]))
        painter.drawPath(capsule)

        self._paint_outline(painter, pill)
        self._paint_text(painter, pill)
        self._paint_focus_ring(painter, pill)

    def _paint_outline(self, painter: QPainter, pill: QRectF) -> None:
        """2px 边框：可用是正文黑，不可用是灰色边框令牌。"""
        color = QColor(
            TOKENS["text"] if self.isEnabled() else TOKENS["border_strong"]
        )
        ring = pill.adjusted(
            -OUTLINE_WIDTH / 2.0,
            -OUTLINE_WIDTH / 2.0,
            OUTLINE_WIDTH / 2.0,
            OUTLINE_WIDTH / 2.0,
        )
        painter.setPen(QPen(color, OUTLINE_WIDTH))
        painter.setBrush(Qt.NoBrush)
        radius = ring.height() / 2.0
        painter.drawRoundedRect(ring, radius, radius)

    def _paint_text(self, painter: QPainter, pill: QRectF) -> None:
        painter.setFont(self._font)
        painter.setPen(
            QColor(
                TOKENS["text"] if self.isEnabled() else TOKENS["disabled_text"]
            )
        )
        painter.drawText(pill, int(Qt.AlignCenter), self.text())

    def _paint_focus_ring(self, painter: QPainter, pill: QRectF) -> None:
        if not self._focus_visible:
            return
        focus = pill.adjusted(
            -(RING_WIDTH_ACTIVE + 3.0),
            -(RING_WIDTH_ACTIVE + 3.0),
            RING_WIDTH_ACTIVE + 3.0,
            RING_WIDTH_ACTIVE + 3.0,
        )
        painter.setPen(QPen(QColor(TOKENS["primary"]), 2.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(focus, focus.height() / 2.0, focus.height() / 2.0)


def _with_alpha(color: QColor, ratio: float) -> QColor:
    """同一颜色换个透明度：外圈光晕由主色派生。"""
    faded = QColor(color)
    faded.setAlpha(round(255 * max(0.0, min(1.0, ratio))))
    return faded


__all__ = ["AnimatedActionButton", "OutlineActionButton"]
