"""电源通道卡片：一张横卡表示一台电源的两个通道。

这张卡是配置页「电源详细配置」的卡面资产，只有正面，不参与设备发现的发牌动画，
因此没有牌背。卡面内容自上而下：

- **设备码缩写**：整张卡最上面的一行，等宽加粗字，只写 ``label``（``DP8B1519``）；
- **两个通道**：左右各一半，标题是 ``CH1`` / ``CH2``，下面是该通道的拨动开关；
- **四个只读值**：每个通道两行两列，``VS``（电压设定）、``VL``（电压保护）、
  ``IS``（电流设定）、``IL``（电流保护），与配置里的
  ``voltage.value`` / ``voltage.protection`` / ``current.value`` /
  ``current.protection`` 一一对应；
- **通道开关**：关闭时该通道整块降为灰色（``disabled_bg``），数值框、标签与开关
  一起变灰；打开时恢复通道底色（左浅蓝、右浅琥珀）。

数值在本卡上只读：编辑仍走现有表单，卡片只显示一份快照。通道开关目前也只控制
卡面显示，不改配置模型；页面通过 :attr:`PowerSupplyCard.channel_toggled` 决定之后
要不要把它接到配置或硬件输出。

**卡面与交互共用一份绘制**：:func:`render_power_card` 把同一个
:func:`_paint_power_card` 画到透明位图上，所以导出的 PNG 预览与页面上的真实卡片
逐像素同源；开关的悬停、按下与键盘焦点也由这份绘制按状态参数画出来，不看控件
截图。

**尺寸与字体**：卡片固定为 :data:`POWER_CARD_WIDTH` × :data:`POWER_CARD_HEIGHT`
（440×232，约 1.9:1），圆角取 :data:`presentation.qt.theme.CARD_RADIUS`，与设备
发现卡片同值；字体系列取自 :func:`presentation.qt.theme.card_code_font` 与
:func:`presentation.qt.theme.card_caption_font`，只改字号，不再另起一套字体。
颜色全部来自 :data:`presentation.qt.theme.TOKENS`，其中包括为两个通道新增的
``channel_cool`` / ``channel_warm`` 两个浅底色。

**运行约束**：位图用 ``QPixmap`` 绘制，必须在创建 ``QApplication`` 之后、在 GUI
线程里调用。卡片控件本身也只在 GUI 线程中使用。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from .theme import (
    CARD_RADIUS,
    TOKENS,
    card_caption_font,
    card_code_font,
)

__all__ = [
    "CHANNEL_NAMES",
    "POWER_CARD_HEIGHT",
    "POWER_CARD_SIZE",
    "POWER_CARD_WIDTH",
    "PowerCardLayout",
    "PowerChannelData",
    "PowerChannelLayout",
    "PowerSupplyCard",
    "PowerSupplyCardData",
    "power_card_layout",
    "render_power_card",
]

#: 电源固定两个通道，顺序也是卡面上的左右顺序：左边 CH1，右边 CH2。
CHANNEL_NAMES = ("CH1", "CH2")

#: 卡片逻辑尺寸：440×232，约 1.9:1，正好放进配置页右栏。
POWER_CARD_WIDTH = 440
POWER_CARD_HEIGHT = 232
POWER_CARD_SIZE = QSize(POWER_CARD_WIDTH, POWER_CARD_HEIGHT)

# ---- 卡面几何：所有位置都由这一组常量算出，绘制与控件命中区共用 -----------------
_TITLE_HEIGHT = 64.0
_PADDING = 14.0
_HALF_WIDTH = POWER_CARD_WIDTH / 2.0
_CHANNEL_LABEL_TOP = 72.0
_CHANNEL_LABEL_HEIGHT = 14.0
_SWITCH_TOP = 90.0
_SWITCH_WIDTH = 112.0
_SWITCH_HEIGHT = 30.0
_GRID_TOP = 130.0
_ROW_HEIGHT = 38.0
_ROW_GAP = 10.0
_BOX_HEIGHT = 28.0
_LABEL_WIDTH = 24.0
_LABEL_GAP = 4.0
_CELL_WIDTH = (_HALF_WIDTH - 2.0 * _PADDING - _ROW_GAP) / 2.0
_BOX_WIDTH = _CELL_WIDTH - _LABEL_WIDTH - _LABEL_GAP
_VALUE_TEXTS = ("VS", "VL", "IS", "IL")

# ---- 配色：全部来自 TOKENS，关闭态只换 token，不另造一套灰 ----------------------
_SURFACE = QColor(TOKENS["surface"])
_SURFACE_ALT = QColor(TOKENS["surface_alt"])
_COOL = QColor(TOKENS["channel_cool"])
_WARM = QColor(TOKENS["channel_warm"])
_OFF = QColor(TOKENS["disabled_bg"])
_BORDER = QColor(TOKENS["border"])
_BORDER_STRONG = QColor(TOKENS["border_strong"])
_TEXT = QColor(TOKENS["text"])
_TEXT_SECONDARY = QColor(TOKENS["text_secondary"])
_TEXT_MUTED = QColor(TOKENS["text_muted"])
_DISABLED_TEXT = QColor(TOKENS["disabled_text"])
_PRIMARY = QColor(TOKENS["primary"])
_WARNING = QColor(TOKENS["warning"])
_SWITCH_ON = QColor(TOKENS["tooltip_bg"])
_SWITCH_ON_HOVER = _SWITCH_ON.lighter(125)
_SWITCH_ON_PRESSED = _SWITCH_ON.darker(120)
_SWITCH_OFF = _SURFACE
_SWITCH_OFF_HOVER = _SURFACE_ALT
_SWITCH_OFF_PRESSED = _BORDER
_KNOB = _SURFACE


# ---- 数据 ------------------------------------------------------------------


def _finite_non_negative(value: Any, name: str) -> float:
    """通道数值的显示口径：非 bool 的有限非负数；其余输入直接报错。

    卡片是只读资产，不替调用方把非法值静默换成 0：错误值应该停在构造阶段，
    而不是在卡面上显示成看起来正常的 ``0.00``。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} 必须是数字，收到 {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须是有限数字，收到 {value!r}")
    if number < 0.0:
        raise ValueError(f"{name} 不能为负数，收到 {value!r}")
    return number


@dataclass(frozen=True)
class PowerChannelData:
    """一个电源通道在卡面上的快照：开关状态 + 四个只读值。

    ``channel`` 只能是 ``CH1`` / ``CH2``；四个值对应配置里的
    ``voltage.value`` / ``voltage.protection`` / ``current.value`` /
    ``current.protection``，单位分别是 V、V、A、A。数值只做“有限且非负”的显示
    校验，不在这里套用配置页的 0–50 V / 0–10 A 输入范围：那是输入控件的边界，
    不是卡面资产的责任。
    """

    channel: str
    enabled: bool = True
    voltage_set: float = 0.0
    voltage_protection: float = 0.0
    current_set: float = 0.0
    current_protection: float = 0.0

    def __post_init__(self) -> None:
        if self.channel not in CHANNEL_NAMES:
            raise ValueError(
                f"通道必须是 {' / '.join(CHANNEL_NAMES)}，收到 {self.channel!r}"
            )
        if not isinstance(self.enabled, bool):
            raise TypeError(f"通道 {self.channel} 的 enabled 必须是 bool")
        for field, label in (
            ("voltage_set", "电压设定"),
            ("voltage_protection", "电压保护"),
            ("current_set", "电流设定"),
            ("current_protection", "电流保护"),
        ):
            object.__setattr__(
                self,
                field,
                _finite_non_negative(getattr(self, field), f"{self.channel} {label}"),
            )


@dataclass(frozen=True)
class PowerSupplyCardData:
    """一张电源通道卡片的全部内容：设备码缩写 + 两个通道。

    ``channels`` 可以按任意顺序传入，构造时会规整成 ``CH1``、``CH2`` 的左右
    顺序，因此调用方不必关心卡面从哪里开始画。两个通道缺一个、重复或名称不合法
    都会在这里报错，而不是画出一张缺一半的卡。
    """

    code: str
    channels: tuple[PowerChannelData, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.code, str):
            raise TypeError(f"设备码缩写必须是字符串，收到 {self.code!r}")
        object.__setattr__(self, "code", self.code.strip())
        channels = tuple(self.channels)
        if len(channels) != len(CHANNEL_NAMES):
            raise ValueError(
                f"电源卡片必须正好有 {len(CHANNEL_NAMES)} 个通道，收到 {len(channels)} 个"
            )
        for item in channels:
            if not isinstance(item, PowerChannelData):
                raise TypeError(f"通道数据必须是 PowerChannelData，收到 {item!r}")
        by_name = {item.channel: item for item in channels}
        if set(by_name) != set(CHANNEL_NAMES):
            raise ValueError(
                f"通道必须正好是 {' / '.join(CHANNEL_NAMES)}，收到 "
                f"{sorted(by_name)}"
            )
        object.__setattr__(
            self,
            "channels",
            tuple(by_name[name] for name in CHANNEL_NAMES),
        )

    def channel(self, name: str) -> PowerChannelData:
        """按通道名取快照；名称不合法时抛 ``KeyError``。"""
        for item in self.channels:
            if item.channel == name:
                return item
        raise KeyError(f"卡片里没有通道 {name!r}")

    def with_channel_enabled(self, name: str, enabled: bool) -> "PowerSupplyCardData":
        """返回一份只改某个通道开关状态的新快照（其余值原样保留）。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        self.channel(name)
        return replace(
            self,
            channels=tuple(
                replace(item, enabled=enabled) if item.channel == name else item
                for item in self.channels
            ),
        )


# ---- 几何 ------------------------------------------------------------------


@dataclass(frozen=True)
class PowerChannelLayout:
    """一个通道在卡面坐标系里的位置：面板、标题、开关、四个标签与数值框。"""

    channel: str
    panel: QRectF
    label: QRectF
    switch: QRectF
    value_labels: tuple[QRectF, ...]
    value_boxes: tuple[QRectF, ...]


@dataclass(frozen=True)
class PowerCardLayout:
    """整张卡片的几何：卡片、标题带与左右两个通道。"""

    card: QRectF
    title: QRectF
    channels: tuple[PowerChannelLayout, ...]


def power_card_layout() -> PowerCardLayout:
    """算出整张卡片的几何；绘制、命中区与测试都读这一份结果。

    卡片尺寸固定，因此不需要宽高参数。左右通道按 :data:`CHANNEL_NAMES` 的顺序
    排列；每个通道的四个值按 ``VS / VL / IS / IL`` 的顺序排成两行两列。
    """
    card = QRectF(0.0, 0.0, float(POWER_CARD_WIDTH), float(POWER_CARD_HEIGHT))
    title = QRectF(0.0, 0.0, float(POWER_CARD_WIDTH), _TITLE_HEIGHT)
    channels: list[PowerChannelLayout] = []
    for index, name in enumerate(CHANNEL_NAMES):
        left = index * _HALF_WIDTH
        panel = QRectF(
            left,
            _TITLE_HEIGHT,
            _HALF_WIDTH,
            float(POWER_CARD_HEIGHT) - _TITLE_HEIGHT,
        )
        label = QRectF(
            left,
            _CHANNEL_LABEL_TOP,
            _HALF_WIDTH,
            _CHANNEL_LABEL_HEIGHT,
        )
        switch = QRectF(
            left + (_HALF_WIDTH - _SWITCH_WIDTH) / 2.0,
            _SWITCH_TOP,
            _SWITCH_WIDTH,
            _SWITCH_HEIGHT,
        )
        value_labels: list[QRectF] = []
        value_boxes: list[QRectF] = []
        for value_index in range(len(_VALUE_TEXTS)):
            column = value_index % 2
            row = value_index // 2
            cell_x = left + _PADDING + column * (_CELL_WIDTH + _ROW_GAP)
            cell_y = _GRID_TOP + row * (_ROW_HEIGHT + _ROW_GAP)
            value_labels.append(
                QRectF(cell_x, cell_y, _LABEL_WIDTH, _ROW_HEIGHT)
            )
            value_boxes.append(
                QRectF(
                    cell_x + _LABEL_WIDTH + _LABEL_GAP,
                    cell_y + (_ROW_HEIGHT - _BOX_HEIGHT) / 2.0,
                    _BOX_WIDTH,
                    _BOX_HEIGHT,
                )
            )
        channels.append(
            PowerChannelLayout(
                channel=name,
                panel=panel,
                label=label,
                switch=switch,
                value_labels=tuple(value_labels),
                value_boxes=tuple(value_boxes),
            )
        )
    return PowerCardLayout(card=card, title=title, channels=tuple(channels))


# ---- 绘制 ------------------------------------------------------------------


def _rounded(rect: QRectF, inset: float = 0.0) -> QPainterPath:
    """卡片轮廓：圆角与设备发现卡片同值；``inset`` 用于把描边画在轮廓以内。"""
    adjusted = rect.adjusted(inset, inset, -inset, -inset)
    radius = max(1.0, CARD_RADIUS - inset)
    path = QPainterPath()
    path.addRoundedRect(adjusted, radius, radius)
    return path


def _channel_label_font() -> QFont:
    """通道标题 ``CH1`` / ``CH2``：比设备码小两号，仍用等宽加粗。"""
    font = QFont(card_code_font())
    font.setPointSizeF(9.5)
    return font


def _value_label_font() -> QFont:
    """四个值的缩写 ``VS`` / ``VL`` / ``IS`` / ``IL``：10pt 等宽加粗。"""
    font = QFont(card_code_font())
    font.setPointSizeF(10.0)
    return font


def _value_font() -> QFont:
    """数值本身：12pt 等宽加粗，保证 ``50.00`` 这类五位数也放得下。"""
    font = QFont(card_code_font())
    font.setPointSizeF(12.0)
    return font


def _switch_font() -> QFont:
    """开关状态文字：与卡片说明同一份无衬线字体，避免中文落在等宽字上。"""
    font = QFont(card_caption_font())
    font.setPointSizeF(10.0)
    return font


def _accent(index: int) -> QColor:
    """通道强调色：左边主色，右边警告色。"""
    return _PRIMARY if index == 0 else _WARNING


def _panel_fill(index: int, enabled: bool) -> QColor:
    """通道底色：开启时左浅蓝/右浅琥珀，关闭时统一降到禁用灰。"""
    if not enabled:
        return _OFF
    return _COOL if index == 0 else _WARM


def _paint_title(painter: QPainter, data: PowerSupplyCardData, rect: QRectF) -> None:
    """设备码缩写：一行等宽加粗字，居中；过长时右侧省略。"""
    text = data.code or "—"
    painter.save()
    painter.setFont(card_code_font())
    painter.setPen(_TEXT if data.code else _TEXT_MUTED)
    metrics = QFontMetricsF(card_code_font())
    available = max(0.0, rect.width() - 2.0 * _PADDING)
    elided = metrics.elidedText(text, Qt.ElideRight, available)
    painter.drawText(
        rect.adjusted(_PADDING, 0.0, -_PADDING, 0.0),
        int(Qt.AlignCenter),
        elided,
    )
    painter.restore()


def _paint_channel_panel(
    painter: QPainter,
    layout: PowerChannelLayout,
    index: int,
    enabled: bool,
) -> None:
    """通道底色与顶部强调线：关闭时整块灰化。"""
    painter.save()
    painter.fillRect(layout.panel, _panel_fill(index, enabled))
    accent = _accent(index) if enabled else _BORDER_STRONG
    painter.fillRect(
        QRectF(layout.panel.left(), layout.panel.top(), layout.panel.width(), 2.0),
        accent,
    )
    painter.restore()


def _paint_channel_label(
    painter: QPainter,
    layout: PowerChannelLayout,
    index: int,
    enabled: bool,
) -> None:
    """通道标题：一个小色点 + ``CH1`` / ``CH2``，在通道顶部居中。"""
    font = _channel_label_font()
    metrics = QFontMetricsF(font)
    text = layout.channel
    dot = 6.0
    gap = 6.0
    text_width = metrics.horizontalAdvance(text)
    total = dot + gap + text_width
    start = layout.label.center().x() - total / 2.0
    color = _accent(index) if enabled else _TEXT_MUTED
    painter.save()
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(
        QRectF(
            start,
            layout.label.center().y() - dot / 2.0,
            dot,
            dot,
        )
    )
    painter.setFont(font)
    painter.setPen(color)
    painter.drawText(
        QRectF(
            start + dot + gap,
            layout.label.top(),
            text_width + 2.0,
            layout.label.height(),
        ),
        int(Qt.AlignLeft | Qt.AlignVCenter),
        text,
    )
    painter.restore()


def _paint_value(
    painter: QPainter,
    label_rect: QRectF,
    box_rect: QRectF,
    label: str,
    value: float,
    index: int,
    enabled: bool,
) -> None:
    """一个值：左侧缩写 + 右侧数值框；关闭时标签与数值一起降到禁用色。"""
    painter.save()
    text_color = _TEXT if enabled else _DISABLED_TEXT
    painter.setFont(_value_label_font())
    painter.setPen(text_color)
    painter.drawText(label_rect, int(Qt.AlignCenter), label)

    pen_width = 1.5 if enabled else 1.0
    inset = pen_width / 2.0
    box = box_rect.adjusted(inset, inset, -inset, -inset)
    painter.setBrush(_SURFACE if enabled else _SURFACE_ALT)
    painter.setPen(
        QPen(_accent(index) if enabled else _BORDER_STRONG, pen_width)
    )
    painter.drawRoundedRect(box, 6.0, 6.0)

    font = _value_font()
    painter.setFont(font)
    painter.setPen(text_color)
    available = max(0.0, box_rect.width() - 12.0)
    text = QFontMetricsF(font).elidedText(
        f"{value:.2f}",
        Qt.ElideRight,
        available,
    )
    painter.drawText(
        box_rect.adjusted(6.0, 0.0, -6.0, 0.0),
        int(Qt.AlignCenter),
        text,
    )
    painter.restore()


def _paint_switch(
    painter: QPainter,
    rect: QRectF,
    enabled: bool,
    *,
    hovered: bool,
    focused: bool,
    pressed: bool,
) -> None:
    """拨动开关：开启是深色胶囊 + 右侧圆钮，关闭是白底灰边 + 左侧圆钮。

    开关的悬停、按下与焦点只改变这一块的绘制，不影响卡片其他位置；焦点环画在
    胶囊外侧，键盘用户能看出当前在哪个通道上。
    """
    radius = rect.height() / 2.0
    painter.save()
    if enabled:
        fill = _SWITCH_ON_PRESSED if pressed else _SWITCH_ON_HOVER if hovered else _SWITCH_ON
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, radius, radius)
    else:
        fill = _SWITCH_OFF_PRESSED if pressed else _SWITCH_OFF_HOVER if hovered else _SWITCH_OFF
        painter.setPen(QPen(_BORDER_STRONG, 1.0))
        painter.setBrush(fill)
        painter.drawRoundedRect(
            rect.adjusted(0.5, 0.5, -0.5, -0.5),
            radius - 0.5,
            radius - 0.5,
        )

    knob_size = rect.height() - 8.0
    knob_y = rect.center().y() - knob_size / 2.0
    knob_x = (
        rect.right() - 4.0 - knob_size
        if enabled
        else rect.left() + 4.0
    )
    knob = QRectF(knob_x, knob_y, knob_size, knob_size)
    painter.setBrush(_KNOB)
    painter.setPen(QPen(_BORDER_STRONG, 1.0) if not enabled else Qt.NoPen)
    painter.drawEllipse(knob)

    painter.setFont(_switch_font())
    if enabled:
        text_rect = QRectF(
            rect.left() + 10.0,
            rect.top(),
            max(0.0, knob.left() - rect.left() - 14.0),
            rect.height(),
        )
        painter.setPen(_SURFACE)
    else:
        text_rect = QRectF(
            knob.right() + 6.0,
            rect.top(),
            max(0.0, rect.right() - knob.right() - 12.0),
            rect.height(),
        )
        painter.setPen(_TEXT_SECONDARY)
    painter.drawText(
        text_rect,
        int(Qt.AlignCenter),
        "开" if enabled else "关",
    )

    if focused:
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_PRIMARY, 2.0))
        painter.drawRoundedRect(
            rect.adjusted(-2.5, -2.5, 2.5, 2.5),
            radius + 2.5,
            radius + 2.5,
        )
    painter.restore()


def _paint_power_card(
    painter: QPainter,
    data: PowerSupplyCardData,
    *,
    hover_channel: str | None = None,
    focus_channel: str | None = None,
    pressed_channel: str | None = None,
) -> None:
    """把整张卡画到 ``painter`` 上；控件与导出位图共用这一份绘制。"""
    layout = power_card_layout()
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.setClipPath(_rounded(layout.card))
    painter.fillPath(_rounded(layout.card), _SURFACE)

    for index, channel_layout in enumerate(layout.channels):
        channel = data.channels[index]
        _paint_channel_panel(painter, channel_layout, index, channel.enabled)

    # 标题带下边线与左右通道分隔线：两条都是 1px，压在通道底色上。
    painter.fillRect(
        QRectF(0.0, _TITLE_HEIGHT - 1.0, float(POWER_CARD_WIDTH), 1.0),
        _BORDER,
    )
    painter.fillRect(
        QRectF(
            _HALF_WIDTH,
            _TITLE_HEIGHT,
            1.0,
            float(POWER_CARD_HEIGHT) - _TITLE_HEIGHT,
        ),
        _BORDER,
    )

    _paint_title(painter, data, layout.title)

    for index, channel_layout in enumerate(layout.channels):
        channel = data.channels[index]
        _paint_channel_label(painter, channel_layout, index, channel.enabled)
        values = (
            channel.voltage_set,
            channel.voltage_protection,
            channel.current_set,
            channel.current_protection,
        )
        for value_index, (label_rect, box_rect) in enumerate(
            zip(channel_layout.value_labels, channel_layout.value_boxes)
        ):
            _paint_value(
                painter,
                label_rect,
                box_rect,
                _VALUE_TEXTS[value_index],
                values[value_index],
                index,
                channel.enabled,
            )
        _paint_switch(
            painter,
            channel_layout.switch,
            channel.enabled,
            hovered=hover_channel == channel.channel,
            focused=focus_channel == channel.channel,
            pressed=pressed_channel == channel.channel,
        )

    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(_BORDER, 1.0))
    painter.drawPath(_rounded(layout.card, 0.5))
    painter.restore()


def _device_pixel_ratio(value: float) -> float:
    """屏幕像素比规整成正的有限数；拿不到时按 1.0 处理（只影响清晰度）。"""
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(ratio) or ratio <= 0.0:
        return 1.0
    return ratio


def render_power_card(
    data: PowerSupplyCardData,
    *,
    device_pixel_ratio: float = 1.0,
) -> QPixmap:
    """按一份卡片数据渲染完整正面（透明底，不含投影），用于预览或静态导出。

    逻辑尺寸始终是 :data:`POWER_CARD_WIDTH` × :data:`POWER_CARD_HEIGHT`；高 DPI 只
    放大物理像素。必须在 GUI 线程、创建 ``QApplication`` 之后调用。
    """
    if not isinstance(data, PowerSupplyCardData):
        raise TypeError(f"data 必须是 PowerSupplyCardData，收到 {data!r}")
    ratio = _device_pixel_ratio(device_pixel_ratio)
    pixmap = QPixmap(
        max(1, round(POWER_CARD_WIDTH * ratio)),
        max(1, round(POWER_CARD_HEIGHT * ratio)),
    )
    pixmap.fill(Qt.transparent)
    pixmap.setDevicePixelRatio(ratio)
    painter = QPainter(pixmap)
    try:
        _paint_power_card(painter, data)
    finally:
        painter.end()
    return pixmap


# ---- 控件 ------------------------------------------------------------------


class ChannelSwitch(QAbstractButton):
    """通道开关的透明命中区：真正的外观由父卡片按状态绘制。

    用 ``QAbstractButton`` 而不是自绘命中矩形，是为了保留 Qt 的键盘焦点、
    ``Space`` 激活、``pressed`` / ``released`` 与无障碍语义；控件本身不画任何
    像素，卡面因此仍由 :func:`_paint_power_card` 这一份绘制决定。
    """

    hover_changed = Signal(str, bool)
    focus_changed = Signal(str, bool)

    def __init__(self, channel: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if channel not in CHANNEL_NAMES:
            raise ValueError(f"通道必须是 {' / '.join(CHANNEL_NAMES)}，收到 {channel!r}")
        self.channel = channel
        self.setCheckable(True)
        self.setAutoExclusive(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(f"通道 {channel} 开关")
        self.setAccessibleDescription("切换该通道在卡面上的启用状态")

    def sizeHint(self) -> QSize:
        return QSize(round(_SWITCH_WIDTH), round(_SWITCH_HEIGHT))

    def paintEvent(self, event) -> None:
        # 外观由父卡片统一画；这里只接受事件，不留下任何控件自己的底色。
        event.accept()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.hover_changed.emit(self.channel, True)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.hover_changed.emit(self.channel, False)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.focus_changed.emit(self.channel, True)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.focus_changed.emit(self.channel, False)


class PowerSupplyCard(QWidget):
    """配置页的电源通道卡片：只读展示两个通道，开关只改变卡面状态。

    ``data`` 是构造时的快照；用户拨动开关只更新本卡内部的 enabled 状态，不写配置、
    不碰硬件。页面通过 :attr:`channel_toggled` 决定之后怎么处理这个动作。

    两个开关是真正的可聚焦控件：``Tab`` 在两个通道之间切换，``Space`` / ``Enter``
    切换状态，鼠标点击同样有效。数值是只读的，不创建输入控件。
    """

    channel_toggled = Signal(str, bool)

    def __init__(
        self,
        data: PowerSupplyCardData,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if not isinstance(data, PowerSupplyCardData):
            raise TypeError(f"data 必须是 PowerSupplyCardData，收到 {data!r}")
        self.setObjectName("powerSupplyCard")
        self.setFixedSize(POWER_CARD_SIZE)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.NoFocus)
        self._data = data
        self._layout = power_card_layout()
        self._hover_channel: str | None = None
        self._focus_channel: str | None = None
        self._pressed_channel: str | None = None

        self._switches: dict[str, ChannelSwitch] = {}
        for channel_layout in self._layout.channels:
            switch = ChannelSwitch(channel_layout.channel, self)
            switch.setChecked(data.channel(channel_layout.channel).enabled)
            switch.setGeometry(channel_layout.switch.toRect())
            switch.toggled.connect(
                lambda checked, name=channel_layout.channel: self._on_switch_toggled(
                    name, checked
                )
            )
            switch.hover_changed.connect(self._on_switch_hover)
            switch.focus_changed.connect(self._on_switch_focus)
            switch.pressed.connect(
                lambda name=channel_layout.channel: self._on_switch_pressed(name)
            )
            switch.released.connect(
                lambda name=channel_layout.channel: self._on_switch_released(name)
            )
            self._switches[channel_layout.channel] = switch
        self.setTabOrder(self._switches["CH1"], self._switches["CH2"])
        self._update_tooltips()

    # ---------- 读取 ----------

    def sizeHint(self) -> QSize:
        return QSize(POWER_CARD_WIDTH, POWER_CARD_HEIGHT)

    @property
    def data(self) -> PowerSupplyCardData:
        """卡片此刻显示的快照（开关状态已随用户操作更新）。"""
        return self._data

    def channel_switch(self, channel: str) -> ChannelSwitch:
        """取某个通道的透明命中控件；名称不合法时抛 ``KeyError``。"""
        switch = self._switches.get(channel)
        if switch is None:
            raise KeyError(f"卡片里没有通道 {channel!r}")
        return switch

    def channel_enabled(self, channel: str) -> bool:
        """某个通道此刻是否处于开启状态。"""
        return self.channel_switch(channel).isChecked()

    # ---------- 写入 ----------

    def set_data(self, data: PowerSupplyCardData) -> None:
        """整张卡换一份快照；不触发 :attr:`channel_toggled`。"""
        if not isinstance(data, PowerSupplyCardData):
            raise TypeError(f"data 必须是 PowerSupplyCardData，收到 {data!r}")
        self._data = data
        for channel_layout in self._layout.channels:
            switch = self._switches[channel_layout.channel]
            enabled = data.channel(channel_layout.channel).enabled
            if switch.isChecked() != enabled:
                switch.blockSignals(True)
                switch.setChecked(enabled)
                switch.blockSignals(False)
        self._update_tooltips()
        self.update()

    def set_channel_enabled(
        self,
        channel: str,
        enabled: bool,
        *,
        emit: bool = False,
    ) -> None:
        """程序化设置某个通道的开关；``emit=True`` 时仍发一次信号。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        switch = self.channel_switch(channel)
        if switch.isChecked() != enabled:
            switch.blockSignals(True)
            switch.setChecked(enabled)
            switch.blockSignals(False)
            self._data = self._data.with_channel_enabled(channel, enabled)
            self._update_tooltips()
            self.update()
        if emit:
            self.channel_toggled.emit(channel, enabled)

    # ---------- 开关事件 ----------

    def _on_switch_toggled(self, channel: str, enabled: bool) -> None:
        """用户拨动开关：只更新卡面快照，不写配置、不连硬件。"""
        self._data = self._data.with_channel_enabled(channel, bool(enabled))
        self._update_tooltips()
        self.update()
        self.channel_toggled.emit(channel, bool(enabled))

    def _on_switch_hover(self, channel: str, hovered: bool) -> None:
        self._hover_channel = channel if hovered else None
        self.update()

    def _on_switch_focus(self, channel: str, focused: bool) -> None:
        self._focus_channel = channel if focused else None
        self.update()

    def _on_switch_pressed(self, channel: str) -> None:
        self._pressed_channel = channel
        self.update()

    def _on_switch_released(self, channel: str) -> None:
        if self._pressed_channel == channel:
            self._pressed_channel = None
        self.update()

    # ---------- 提示与绘制 ----------

    def _update_tooltips(self) -> None:
        """悬浮提示补上卡面放不下的信息：每个缩写的含义、单位与通道状态。"""
        lines = [
            self._data.code or "未识别电源",
            "VS 电压设定 · VL 电压保护 · IS 电流设定 · IL 电流保护",
        ]
        for channel in self._data.channels:
            lines.append(
                f"{channel.channel} {'开' if channel.enabled else '关'}："
                f"VS {channel.voltage_set:.2f} V · VL {channel.voltage_protection:.2f} V · "
                f"IS {channel.current_set:.2f} A · IL {channel.current_protection:.2f} A"
            )
        text = "\n".join(lines)
        self.setToolTip(text)
        for channel in self._data.channels:
            switch = self._switches[channel.channel]
            switch.setToolTip(
                f"{channel.channel} {'开' if channel.enabled else '关'}\n"
                f"点击切换卡面显示状态（暂不写入配置）\n{text}"
            )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            _paint_power_card(
                painter,
                self._data,
                hover_channel=self._hover_channel,
                focus_channel=self._focus_channel,
                pressed_channel=self._pressed_channel,
            )
        finally:
            painter.end()
