"""测量读数条：结果面顶部的深色玻璃带与一组实时读数芯片。

测量页原本把“当前频点”散在 KPI 行和表格里，结果面顶部没有视觉主角。本模块把
当前频点和最新一组值收进一条 96px 左右的读数带：

- 左侧是当前频点的大号等宽数字与单位；
- 右侧是 2×2 的读数芯片，每项带标签、值、单位与可选的相对变化；
- 测量进行中显示一个呼吸点，空闲时整块保持克制。

读数条只排版页面已经显示的数据，不读取结果 DTO、不计算业务指标、不发测量命令。
深色背景与细网格用 :class:`QPainter` 自绘，文字和芯片由样式表按语义 objectName
着色；颜色全部取自 :data:`presentation.qt.theme.TOKENS`。

动效只有两种，且都服务于“状态确认”：新读数到达时文字从 0.55 透明度回到 1.0
（120ms 内即可读），测量进行中呼吸点按 ``MOTION_BREATH_MS`` 循环。关闭动效时两者
立即落到静态外观，读数不受影响。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import cos, pi

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme import MOTION_BREATH_MS, MOTION_DATA_MS, TOKENS

__all__ = ["MeasurementReadout", "ReadoutMetric"]


@dataclass(frozen=True)
class ReadoutMetric:
    """读数条里的一项：标签、值、单位与可选的相对变化。"""

    label: str
    value: str
    unit: str = ""
    delta: str = ""

    def __post_init__(self) -> None:
        for name, value in (
            ("label", self.label),
            ("value", self.value),
            ("unit", self.unit),
            ("delta", self.delta),
        ):
            if not isinstance(value, str):
                raise TypeError(f"读数 {name} 必须是字符串")


class _PulseDot(QWidget):
    """测量进行中的呼吸点：外圈随相位放大、变亮；空闲或关闭动效时保持静态。"""

    SIZE = 8

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._active = False
        self._animations_enabled = True
        self._phase = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(MOTION_BREATH_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.Linear)
        self._animation.setLoopCount(-1)
        self._animation.valueChanged.connect(self._on_phase)

    def is_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        if not isinstance(active, bool):
            raise TypeError("active 必须是 bool")
        if active == self._active:
            return
        self._active = active
        self._sync_animation()
        self.update()

    def set_animations_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        self._animations_enabled = enabled
        self._sync_animation()
        if not enabled:
            self._phase = 0.0
            self.update()

    def animations_enabled(self) -> bool:
        return self._animations_enabled

    def _sync_animation(self) -> None:
        if self._active and self._animations_enabled and self.isVisible():
            if self._animation.state() != QVariantAnimation.State.Running:
                self._animation.start()
        else:
            self._animation.stop()

    def _on_phase(self, value) -> None:
        self._phase = float(value)
        self.update()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt 覆盖方法
        super().showEvent(event)
        self._sync_animation()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt 覆盖方法
        super().hideEvent(event)
        self._animation.stop()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 覆盖方法
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        center = QPointF(self.SIZE / 2.0, self.SIZE / 2.0)
        if not self._active:
            color = QColor(TOKENS["readout_text_muted"])
            color.setAlpha(120)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(center, 2.0, 2.0)
            return
        breath = 0.5 - 0.5 * cos(2.0 * pi * self._phase)
        halo = QColor(TOKENS["readout_accent"])
        halo.setAlpha(int(28 + 62 * breath))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        radius = 2.5 + 2.5 * breath
        painter.drawEllipse(center, radius, radius)
        painter.setBrush(QColor(TOKENS["readout_accent"]))
        painter.drawEllipse(center, 2.0, 2.0)


class _LiveBadge(QFrame):
    """“测量中”胶囊：只在活动测量时出现，内部呼吸点与阶段条共用同一套语言。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("measurementReadoutLive")
        self.setSizePolicy(
            QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(7, 2, 8, 2)
        layout.setSpacing(5)
        self.dot = _PulseDot(self)
        layout.addWidget(self.dot)
        self.text_label = QLabel("测量中", self)
        self.text_label.setObjectName("measurementReadoutLiveText")
        self.text_label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.text_label)
        self.setAccessibleName("测量进行中")
        self.hide()

    def is_active(self) -> bool:
        return self.dot.is_active()

    def set_active(self, active: bool) -> None:
        self.dot.set_active(active)
        self.setVisible(active)

    def set_animations_enabled(self, enabled: bool) -> None:
        self.dot.set_animations_enabled(enabled)


class _ReadoutChip(QFrame):
    """一个读数芯片：标签在上，值 + 单位在下，变化量再下一行。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("measurementReadoutChip")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(0)

        self.label = QLabel(self)
        self.label.setObjectName("measurementReadoutChipLabel")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.label)

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(2)
        self.value = QLabel(self)
        self.value.setObjectName("measurementReadoutChipValue")
        self.value.setTextFormat(Qt.TextFormat.PlainText)
        value_row.addWidget(self.value)
        self.unit = QLabel(self)
        self.unit.setObjectName("measurementReadoutChipUnit")
        self.unit.setTextFormat(Qt.TextFormat.PlainText)
        value_row.addWidget(self.unit, 0, Qt.AlignmentFlag.AlignBottom)
        self.delta = QLabel(self)
        self.delta.setObjectName("measurementReadoutChipDelta")
        self.delta.setTextFormat(Qt.TextFormat.PlainText)
        value_row.addWidget(self.delta, 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addStretch(1)
        layout.addLayout(value_row)

    def set_metric(self, metric: ReadoutMetric) -> None:
        self.label.setText(metric.label)
        self.value.setText(metric.value)
        self.unit.setText(metric.unit)
        self.delta.setText(metric.delta)
        self.delta.setVisible(bool(metric.delta))
        self.setAccessibleName(
            f"{metric.label} {metric.value}{metric.unit}"
            + (f"，{metric.delta}" if metric.delta else "")
        )


class MeasurementReadout(QFrame):
    """结果面顶部的深色读数条；只展示调用方已经取好的值。"""

    #: 读数带最小高度：标题、频点、单位与芯片都要放得下。
    MINIMUM_HEIGHT = 96

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("measurementReadout")
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.setMinimumHeight(self.MINIMUM_HEIGHT)
        self._frequency = ""
        self._unit = ""
        self._metrics: tuple[ReadoutMetric, ...] = ()
        self._has_reading = False
        self._animations_enabled = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)
        outer.setSpacing(0)
        self._content = QWidget(self)
        outer.addWidget(self._content)
        self._content_effect = QGraphicsOpacityEffect(self._content)
        self._content_effect.setOpacity(1.0)
        self._content.setGraphicsEffect(self._content_effect)

        content_layout = QHBoxLayout(self._content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(14)

        identity = QWidget(self._content)
        identity_layout = QVBoxLayout(identity)
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(0)
        self.caption_label = QLabel("当前频点", identity)
        self.caption_label.setObjectName("measurementReadoutCaption")
        self.caption_label.setTextFormat(Qt.TextFormat.PlainText)
        identity_layout.addWidget(self.caption_label)

        value_row = QHBoxLayout()
        value_row.setContentsMargins(0, 0, 0, 0)
        value_row.setSpacing(4)
        self.value_label = QLabel("—", identity)
        self.value_label.setObjectName("measurementReadoutValue")
        self.value_label.setTextFormat(Qt.TextFormat.PlainText)
        self.value_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        value_row.addWidget(self.value_label)
        self.unit_label = QLabel("", identity)
        self.unit_label.setObjectName("measurementReadoutUnit")
        self.unit_label.setTextFormat(Qt.TextFormat.PlainText)
        value_row.addWidget(self.unit_label, 0, Qt.AlignmentFlag.AlignBottom)
        value_row.addStretch(1)
        identity_layout.addLayout(value_row)
        identity_layout.addSpacing(2)
        self.live_badge = _LiveBadge(identity)
        identity_layout.addWidget(
            self.live_badge, 0, Qt.AlignmentFlag.AlignLeft
        )
        identity_layout.addStretch(1)
        content_layout.addWidget(identity, 0)

        metrics_panel = QWidget(self._content)
        metrics_layout = QVBoxLayout(metrics_panel)
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.setSpacing(4)
        self.metrics_title = QLabel("最新测量值", metrics_panel)
        self.metrics_title.setObjectName("measurementReadoutChipTitle")
        self.metrics_title.setTextFormat(Qt.TextFormat.PlainText)
        metrics_layout.addWidget(self.metrics_title)
        self._chip_grid = QGridLayout()
        self._chip_grid.setContentsMargins(0, 0, 0, 0)
        self._chip_grid.setHorizontalSpacing(5)
        self._chip_grid.setVerticalSpacing(5)
        metrics_layout.addLayout(self._chip_grid)
        metrics_layout.addStretch(1)
        content_layout.addWidget(metrics_panel, 1)
        self._chips: list[_ReadoutChip] = []

        self._fade = QVariantAnimation(self)
        self._fade.setStartValue(0.55)
        self._fade.setEndValue(1.0)
        self._fade.setDuration(MOTION_DATA_MS)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.valueChanged.connect(self._on_fade_value)
        self._fade.finished.connect(
            lambda: self._content_effect.setOpacity(1.0)
        )

    # ---- 读取 ---------------------------------------------------------------

    def frequency_text(self) -> str:
        return self._frequency

    def unit_text(self) -> str:
        return self._unit

    def metrics(self) -> tuple[ReadoutMetric, ...]:
        return self._metrics

    def is_active(self) -> bool:
        return self.live_badge.is_active()

    def animations_enabled(self) -> bool:
        return self._animations_enabled

    # ---- 写入 ---------------------------------------------------------------

    def set_reading(
        self,
        frequency: str,
        unit: str = "",
        metrics: Sequence[ReadoutMetric] = (),
        *,
        active: bool | None = None,
    ) -> None:
        """写入当前频点与一组读数；``active`` 为 ``None`` 时保留现有活动状态。"""
        if not isinstance(frequency, str) or not isinstance(unit, str):
            raise TypeError("频点与单位必须是字符串")
        if isinstance(metrics, (str, bytes)) or not isinstance(metrics, Sequence):
            raise TypeError("读数必须是 ReadoutMetric 序列")
        validated = tuple(metrics)
        for metric in validated:
            if not isinstance(metric, ReadoutMetric):
                raise TypeError("读数必须是 ReadoutMetric 序列")

        changed = (
            frequency != self._frequency
            or unit != self._unit
            or validated != self._metrics
        )
        self._frequency = frequency
        self._unit = unit
        self._metrics = validated
        self.value_label.setText(frequency or "—")
        self.unit_label.setText(unit)
        self._apply_metrics(validated)
        self.setAccessibleName(
            f"当前频点 {frequency or '未开始'}{unit}"
        )
        if active is not None:
            self.set_active(active)
        if (
            changed
            and self._has_reading
            and self._animations_enabled
            and self.isVisible()
            and self._fade.state() != QVariantAnimation.State.Running
        ):
            self._play_fade()
        self._has_reading = True

    def set_active(self, active: bool) -> None:
        """显示/隐藏“测量中”呼吸点；只改变外观，不改变读数。"""
        if not isinstance(active, bool):
            raise TypeError("active 必须是 bool")
        self.live_badge.set_active(active)

    def set_animations_enabled(self, enabled: bool) -> None:
        """关闭动效时立即落到静态外观，读数与布局不变。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        self._animations_enabled = enabled
        self.live_badge.set_animations_enabled(enabled)
        if not enabled:
            self._fade.stop()
            self._content_effect.setOpacity(1.0)

    def _apply_metrics(self, metrics: tuple[ReadoutMetric, ...]) -> None:
        while len(self._chips) < len(metrics):
            self._add_chip()
        for index, chip in enumerate(self._chips):
            if index < len(metrics):
                chip.set_metric(metrics[index])
                chip.show()
            else:
                chip.hide()
        self.metrics_title.setVisible(bool(metrics))
        self._reflow_chips()

    def _add_chip(self) -> None:
        chip = _ReadoutChip(self._content)
        self._chips.append(chip)

    def _reflow_chips(self) -> None:
        """宽时一行四项，窄时两列；芯片在网格里换位，不重建控件。"""
        visible = len(getattr(self, "_metrics", ()))
        chips = getattr(self, "_chips", ())
        grid = getattr(self, "_chip_grid", None)
        if visible <= 0 or not chips or grid is None:
            return
        preferred = 4 if self.width() >= 620 else 2
        columns = min(preferred, visible)
        for index in range(visible):
            grid.addWidget(
                chips[index], index // columns, index % columns
            )

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 覆盖方法
        super().resizeEvent(event)
        self._reflow_chips()

    def _play_fade(self) -> None:
        self._fade.stop()
        self._content_effect.setOpacity(0.55)
        self._fade.start()

    def _on_fade_value(self, value) -> None:
        self._content_effect.setOpacity(float(value))

    # ---- 绘制 ---------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 覆盖方法
        """深色玻璃底、极淡网格与 1px 内高光；文字全部由子控件绘制。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 8.0, 8.0)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        gradient.setColorAt(0.0, QColor(TOKENS["readout_body"]))
        gradient.setColorAt(1.0, QColor(TOKENS["readout_body_bottom"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawPath(path)

        painter.save()
        painter.setClipPath(path)
        grid = QColor(TOKENS["readout_grid"])
        grid.setAlpha(8)
        painter.setPen(QPen(grid, 1.0))
        for x in range(0, self.width(), 22):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), 22):
            painter.drawLine(0, y, self.width(), y)
        highlight = QColor(TOKENS["readout_grid"])
        highlight.setAlpha(20)
        painter.setPen(QPen(highlight, 1.0))
        painter.drawLine(
            QPointF(rect.left() + 10, rect.top() + 1),
            QPointF(rect.right() - 10, rect.top() + 1),
        )
        painter.restore()

        painter.setPen(QPen(QColor(TOKENS["readout_border"]), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
