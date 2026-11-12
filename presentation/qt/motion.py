"""测量区的内容过渡：只让进入的页面淡入并轻移，不动常驻外壳。

测量区需要的是“状态变化的确认”，而不是配置阶段的整屏叙事：阶段条、页头与左栏在
切页时保持不动，只有内容槽里的新页面做一次 220ms 的淡入 + 10px 横移。本模块因此
只提供两件东西：

- :class:`SlideFadeEffect`：一个只负责透明度和水平偏移的 ``QGraphicsEffect``；
- :func:`reveal`：把效果临时挂到控件上、用 ``OutCubic`` 推进，结束后立即摘除。

设计约束（见 ``docs/测量工作台重构计划.md`` 步骤 2）：

- 只动画**进入的**控件。旧页面由调用方立即隐藏；这里不 ``grab()`` 旧页快照，
  因为 matplotlib 页面抓图很贵，双页快照会让切页比现在更慢。
- 只动画**看得见**的控件：窗口首次显示、页面还没切到前台时直接落到终态。看不见的
  淡入只会推迟数据显示，不符合“数据优先”。
- 动画结束必须 ``setGraphicsEffect(None)``，让控件回到正常的绘制路径；效果对象由
  Qt 随摘除一起销毁，不会长期拖慢页面。
- 控件已经装有其他 ``graphicsEffect``（页面自己的效果）时不覆盖它，直接跳过本次
  过渡；只有本模块临时挂上的效果才由 :class:`RevealHandle` 摘除。动画中途被页面
  换成自己的效果时，Qt 会同步销毁本模块的效果，句柄通过 ``destroyed`` 立即收尾。
- ``duration_ms=0`` 表示“禁用动效”或测试路径：直接落到终态，不创建效果，也不启动
  计时器。重复调用会先中止上一次过渡，同一时刻最多一个过渡在播。

实现细节：效果在**设备坐标**里合成，用 ``sourcePixmap(..., NoPad)`` 取控件快照后
手工设置透明度并平移，而不是 ``drawSource()``——控件源会绕过调用方 painter 的
透明度与坐标变换（Qt 直接在效果上下文里重画控件），那样既不会淡也不会移。
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QPoint,
    Qt,
    QVariantAnimation,
)
from PySide6.QtGui import QPainter, QTransform
from PySide6.QtWidgets import QGraphicsEffect, QWidget

from .theme import MOTION_REVEAL_MS, MOTION_SLIDE_PX

__all__ = [
    "RevealDirection",
    "RevealHandle",
    "SlideFadeEffect",
    "reveal",
]

#: 过渡句柄挂在控件上的属性名：下一次 ``reveal`` 据此中止上一条动画，不改控件类。
_HANDLE_ATTRIBUTE = "_measurement_reveal_handle"


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

    位移只改变绘制位置，不改变控件几何；效果不扩展自身边界，滑入时越过控件边缘的
    那一小条会被裁掉（页面本身有边距，淡入过程中看不出来），这样移除效果后不会在
    控件矩形外留下残影。
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._opacity = 1.0
        self._offset = 0.0

    def opacity(self) -> float:
        """当前不透明度（0–1）。"""
        return self._opacity

    def offset(self) -> float:
        """当前水平偏移（逻辑像素）；正值向右，负值向左。"""
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

    def draw(self, painter: QPainter) -> None:
        """按当前不透明度与偏移合成源控件。

        不能用 ``drawSource()``：控件源会在效果上下文里直接重画控件，调用方 painter
        的透明度与坐标变换都不生效，淡入和位移都不会落到像素上。这里按 Qt 自带透明度
        效果的写法取设备坐标快照，再手工合成。
        """
        if self._opacity <= 0.0:
            # 第一帧完全透明：不抓快照，省掉一次整页重绘。
            return
        offset = QPoint()
        pixmap = self.sourcePixmap(
            Qt.CoordinateSystem.DeviceCoordinates,
            offset,
            QGraphicsEffect.PixmapPadMode.NoPad,
        )
        if pixmap.isNull():
            return
        # 设备坐标快照：10 个逻辑像素要按屏幕倍率换算。
        shift = round(self._offset * pixmap.devicePixelRatio())
        painter.save()
        painter.setWorldTransform(QTransform())
        painter.setOpacity(self._opacity)
        painter.drawPixmap(offset + QPoint(shift, 0), pixmap)
        painter.restore()


class RevealHandle(QObject):
    """一次内容过渡的生命周期：推进效果、结束时清理、可被下一次过渡中止。

    句柄以控件为父对象，动画也以句柄为父对象；控件销毁时两者一起销毁。调用方通常
    不需要持有它，``reveal`` 的返回值只供测试或需要“立即收尾”的调用方使用。

    收尾之后句柄仍然可以安全查询：``is_running()`` 返回 ``False``，``effect()`` 与
    ``animation()`` 返回 ``None``；不会因为 Qt 已经销毁子对象而抛 ``RuntimeError``。
    """

    def __init__(
        self,
        widget: QWidget,
        effect: SlideFadeEffect,
        start_offset: float,
        duration_ms: int,
    ) -> None:
        super().__init__(widget)
        self._widget: QWidget | None = widget
        self._effect: SlideFadeEffect | None = effect
        self._start_offset = float(start_offset)
        self._active = True
        self._animation: QVariantAnimation | None = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(max(1, duration_ms))
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._apply_progress)
        self._animation.finished.connect(self._finish)
        # 页面在过渡中途换成自己的效果时，Qt 会同步 delete 本模块的效果；必须立刻
        # 停掉动画，否则下一帧会写一个已销毁的 C++ 对象。
        effect.destroyed.connect(self._on_effect_destroyed)
        widget.destroyed.connect(self._on_widget_destroyed)

    def animation(self) -> QVariantAnimation | None:
        """本次过渡的动画；收尾之后为 ``None``。"""
        return self._animation

    def effect(self) -> SlideFadeEffect | None:
        """当前挂着的效果；收尾之后为 ``None``。"""
        return self._effect

    def is_running(self) -> bool:
        """过渡是否仍在播放；收尾之后恒为 ``False``。"""
        animation = self._animation
        if not self._active or animation is None:
            return False
        return animation.state() == QAbstractAnimation.State.Running

    def start(self) -> None:
        """开始推进；调用方负责先设好起始状态。"""
        animation = self._animation
        if animation is not None:
            animation.start()

    def cancel(self) -> None:
        """立即结束过渡并摘除效果；重复调用是幂等的。"""
        if not self._active:
            return
        self._teardown()
        self.deleteLater()

    def _apply_progress(self, value: float) -> None:
        """把 0–1 的进度映射到“淡入 + 偏移归零”。"""
        effect = self._effect
        if not self._active or effect is None:
            return
        progress = max(0.0, min(1.0, float(value)))
        effect.set_opacity(progress)
        effect.set_offset(self._start_offset * (1.0 - progress))

    def _finish(self) -> None:
        """动画走完：清理效果，再排队销毁句柄与动画。"""
        if self._active:
            self._teardown()
            self.deleteLater()

    def _on_effect_destroyed(self, *_args: object) -> None:
        """效果被页面自己的效果替换：Qt 已同步销毁它，立即停止动画并释放句柄。"""
        if not self._active:
            return
        self._active = False
        self._effect = None
        animation, self._animation = self._animation, None
        if animation is not None:
            animation.stop()
        widget, self._widget = self._widget, None
        if widget is not None and getattr(widget, _HANDLE_ATTRIBUTE, None) is self:
            delattr(widget, _HANDLE_ATTRIBUTE)
        self.deleteLater()

    def _on_widget_destroyed(self, *_args: object) -> None:
        """控件先销毁：只清 Python 侧引用，不再碰任何 Qt 对象。"""
        self._active = False
        self._effect = None
        self._widget = None
        self._animation = None

    def _teardown(self) -> None:
        """停止动画、摘除本模块挂上的效果；不碰页面自己的效果。"""
        if not self._active:
            return
        self._active = False
        animation, self._animation = self._animation, None
        if animation is not None:
            animation.stop()
        effect, self._effect = self._effect, None
        widget, self._widget = self._widget, None
        if widget is None:
            return
        if effect is not None and widget.graphicsEffect() is effect:
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
    - 控件当前不可见：直接落到终态，返回 ``None``（窗口首次显示、页面还没切到前台）；
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

    if duration == 0 or not widget.isVisible():
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
