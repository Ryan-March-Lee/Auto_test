"""测量区的内容过渡：只让进入的页面淡入并轻移，不动常驻外壳。

测量区需要的是“状态变化的确认”，而不是配置阶段的整屏叙事：阶段条、页头与左栏在
切页时保持不动，只有内容槽里的新页面做一次 220ms 的淡入 + 10px 横移。本模块因此
只提供两件东西：

- :class:`SlideFadeEffect`：一个只负责透明度和水平偏移的 ``QGraphicsEffect``；
- :func:`reveal`：把效果临时挂到控件上、用 ``OutCubic`` 推进，结束后立即摘除。

设计约束（见 ``docs/测量工作台重构计划.md`` 步骤 2）：

- 只动画**进入的**控件。旧页面由调用方立即隐藏；这里不 ``grab()`` 旧页快照，
  因为 matplotlib 页面抓图很贵，双页快照会让切页比现在更慢。
- 动画结束必须 ``setGraphicsEffect(None)``，让控件回到正常的绘制路径；效果对象由
  Qt 随摘除一起销毁，不会长期拖慢页面。
- 控件已经装有其他 ``graphicsEffect``（页面自己的效果）时不覆盖它，直接跳过本次
  过渡；只有本模块临时挂上的效果才由 :class:`RevealHandle` 摘除。
- ``duration_ms=0`` 表示“禁用动效”或测试路径：直接落到终态，不创建效果，也不启动
  计时器。重复调用会先中止上一次过渡，同一时刻最多一个过渡在播。
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QRectF,
    QVariantAnimation,
)
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QGraphicsEffect, QWidget

from .theme import MOTION_REVEAL_MS, MOTION_SLIDE_PX

__all__ = [
    "RevealDirection",
    "RevealHandle",
    "SlideFadeEffect",
    "reveal",
]

#: 过渡句柄挂在控件上的属性名：下一次 ``reveal`` 据此中止上一条动画，不改控件类。
_HANDLE_ATTRIBUTE = "_measurementRevealHandle"


class RevealDirection(str, Enum):
    """内容进入方向：``FROM_RIGHT`` 表示从右侧轻移进来，``FROM_LEFT`` 反之。"""

    FROM_LEFT = "left"
    FROM_RIGHT = "right"


def _resolved_direction(direction: RevealDirection | str) -> RevealDirection:
    """把字符串方向解析成枚举；未知取值报错，避免静默落到默认方向。"""
    if isinstance(direction, RevealDirection):
        return direction
    if isinstance(direction, str):
        try:
            return RevealDirection(direction)
        except ValueError:
            raise ValueError(f"未知的内容过渡方向: {direction!r}") from None
    raise TypeError("direction 必须是 RevealDirection 或 'left'/'right'")


def _validated_duration(duration_ms: int) -> int:
    """时长必须是非负整数；``bool`` 是 ``int`` 子类，要单独排除。"""
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int):
        raise TypeError("duration_ms 必须是整数")
    if duration_ms < 0:
        raise ValueError("duration_ms 不能为负数")
    return duration_ms


class SlideFadeEffect(QGraphicsEffect):
    """透明度 + 水平偏移的过渡效果。

    它与主题里的 :data:`presentation.qt.theme.MOTION_SLIDE_PX` 配套：起始状态是
    “透明、向进入方向偏 10px”，终态是“不透明、回到原位”。效果本身不启动动画，
    由 :class:`RevealHandle` 推进两个属性，测试也可以直接设置终态做断言。
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._opacity = 1.0
        self._offset = 0.0

    def opacity(self) -> float:
        """当前不透明度（0–1）。"""
        return self._opacity

    def offset(self) -> float:
        """当前水平偏移（像素）；正值向右，负值向左。"""
        return self._offset

    def set_opacity(self, opacity: float) -> None:
        """设置不透明度；超出 0–1 的值会被夹紧。"""
        value = max(0.0, min(1.0, float(opacity)))
        if abs(value - self._opacity) < 1e-6:
            return
        self._opacity = value
        self.update()

    def set_offset(self, offset: float) -> None:
        """设置水平偏移；偏移只影响绘制，不改变控件几何或布局。"""
        value = float(offset)
        if abs(value - self._offset) < 1e-6:
            return
        self._offset = value
        self.update()

    def boundingRectFor(self, source_rect: QRectF) -> QRectF:  # noqa: N802 - Qt 覆盖方法
        """把偏移量算进效果边界，避免滑入的那一侧被效果缓冲区裁掉。"""
        if self._offset > 0.0:
            return source_rect.adjusted(0.0, 0.0, self._offset, 0.0)
        if self._offset < 0.0:
            return source_rect.adjusted(self._offset, 0.0, 0.0, 0.0)
        return source_rect

    def draw(self, painter: QPainter) -> None:
        """先按当前偏移平移，再按当前不透明度画源控件。"""
        painter.save()
        painter.setOpacity(self._opacity)
        painter.translate(self._offset, 0.0)
        self.drawSource(painter)
        painter.restore()


class RevealHandle(QObject):
    """一次内容过渡的生命周期：推进效果、结束时清理、可被下一次过渡中止。

    句柄以控件为父对象，动画也以句柄为父对象；控件销毁时两者一起销毁。调用方通常
    不需要持有它，``reveal`` 的返回值只供测试或需要“立即收尾”的调用方使用。
    """

    def __init__(
        self,
        widget: QWidget,
        effect: SlideFadeEffect,
        start_offset: float,
        duration_ms: int,
    ) -> None:
        super().__init__(widget)
        self._widget = widget
        self._effect = effect
        self._start_offset = float(start_offset)
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(max(1, duration_ms))
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._apply_progress)
        self._animation.finished.connect(self._finish)

    def animation(self) -> QVariantAnimation:
        """本次过渡的动画；时长由 ``duration_ms`` 决定。"""
        return self._animation

    def effect(self) -> SlideFadeEffect | None:
        """当前挂着的效果；收尾之后为 ``None``。"""
        return self._effect

    def is_running(self) -> bool:
        """过渡是否仍在播放。"""
        return self._animation.state() == QAbstractAnimation.State.Running

    def start(self) -> None:
        """开始推进；调用方负责先设好起始状态。"""
        self._animation.start()

    def cancel(self) -> None:
        """立即结束过渡并摘除效果；重复调用是幂等的。"""
        self._teardown()
        self.deleteLater()

    def _apply_progress(self, value: float) -> None:
        """把 0–1 的进度映射到“淡入 + 偏移归零”。"""
        effect = self._effect
        if effect is None:
            return
        progress = max(0.0, min(1.0, float(value)))
        effect.set_opacity(progress)
        effect.set_offset(self._start_offset * (1.0 - progress))

    def _finish(self) -> None:
        """动画走完：清理效果，再排队销毁句柄与动画。"""
        self._teardown()
        self.deleteLater()

    def _teardown(self) -> None:
        """只摘除本模块挂上的效果，并停止动画；不碰页面自己的效果。"""
        self._animation.stop()
        effect = self._effect
        self._effect = None
        widget = self._widget
        self._widget = None
        if widget is not None:
            if widget.graphicsEffect() is effect:
                # ``setGraphicsEffect(None)`` 会同步销毁效果对象。
                widget.setGraphicsEffect(None)
            if getattr(widget, _HANDLE_ATTRIBUTE, None) is self:
                delattr(widget, _HANDLE_ATTRIBUTE)


def reveal(
    widget: QWidget,
    direction: RevealDirection | str = RevealDirection.FROM_RIGHT,
    duration_ms: int = MOTION_REVEAL_MS,
) -> RevealHandle | None:
    """让 ``widget`` 从 ``direction`` 一侧淡入，返回本次过渡句柄。

    - 控件已装有页面自己的 ``graphicsEffect``：不覆盖，返回 ``None``；
    - ``duration_ms=0``：直接落到终态，不创建效果，返回 ``None``；
    - 已有本模块的过渡在播：先停止并摘除，再开始新的一次；
    - 效果在动画结束时自动摘除，控件不会带着 ``QGraphicsEffect`` 继续绘制。
    """
    if not isinstance(widget, QWidget):
        raise TypeError("widget 必须是 QWidget")
    resolved = _resolved_direction(direction)
    duration = _validated_duration(duration_ms)

    previous = getattr(widget, _HANDLE_ATTRIBUTE, None)
    if previous is not None:
        previous.cancel()

    if duration == 0:
        return None
    if widget.graphicsEffect() is not None:
        return None

    start_offset = float(MOTION_SLIDE_PX)
    if resolved is RevealDirection.FROM_LEFT:
        start_offset = -start_offset

    effect = SlideFadeEffect()
    effect.set_opacity(0.0)
    effect.set_offset(start_offset)
    widget.setGraphicsEffect(effect)

    handle = RevealHandle(widget, effect, start_offset, duration)
    setattr(widget, _HANDLE_ATTRIBUTE, handle)
    handle.start()
    return handle
