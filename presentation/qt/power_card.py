"""电源通道卡片：一张横卡表示一台电源的两个通道。

这张卡是配置页「电源详细配置」的卡面资产，只有正面，不参与设备发现的发牌动画，
因此没有牌背。卡面内容自上而下：

- **设备码缩写**：整张卡最上面的一行，等宽加粗字，只写 ``label``（``DP8B1519``）；
- **两个通道**：左右各一半，每个通道顶部是一行“圆形电源键 + ``CH1`` / ``CH2``”，
  开关在左、标题在右，两者共用同一条基线；
- **两个只读值**：每个通道第一行左右各一个，``VS``（电压设定）、``IS``（电流设定）；
  设定值来自配置里的 ``voltage.value`` / ``current.value``；
- **两个保护勾选框**：每个通道第二行左右各一个，左边电压保护、右边电流保护，
  勾选状态来自配置里的 ``voltage.protection_enabled`` /
  ``current.protection_enabled``；卡面不再显示保护数值，阈值仍由表单按工作值的
  110% 推导；
- **自动阈值提示**：通道底部一行小字说明“勾选后保护阈值自动 +10%”；
- **通道开关**：关闭时该通道整块降为灰色（``disabled_bg``），数值框、勾选框、标签
  与开关一起变灰；打开时恢复通道底色（左浅蓝、右浅琥珀），电源键同时点亮：边框转白、
  底色转绿、四周泛起绿色辉光，图标描边加粗并让圆环带一圈青色光。

数值在本卡上只读：编辑仍走现有表单，卡片只显示一份快照。两个保护勾选框是可点
控件（:class:`ProtectionCheck`），点击只切换“是否启用保护”，不填阈值、不碰硬件；
页面通过 :attr:`PowerSupplyCard.protection_toggled` 决定之后怎么处理这个动作。
通道开关仍只控制卡面显示，不改配置模型；页面通过 :attr:`PowerSupplyCard.channel_toggled`
决定之后要不要把它接到配置或硬件输出。

**卡面与交互共用一份绘制**：:func:`render_power_card` 把同一个
:func:`_paint_power_card` 画到透明位图上，所以导出的 PNG 预览与页面上的真实卡片
逐像素同源；开关的悬停、按下与键盘焦点也由这份绘制按状态参数画出来，不看控件
截图。开关的点亮/熄灭由 0–1 的进度值驱动，控件在 500 ms 内插值重绘，导出位图
则直接取终态。

**尺寸与字体**：卡片固定为 :data:`POWER_CARD_WIDTH` × :data:`POWER_CARD_HEIGHT`
（440×232，约 1.9:1），圆角取 :data:`presentation.qt.theme.CARD_RADIUS`，与设备
发现卡片同值；圆形电源键的直径按卡片高度取比例（:data:`_SWITCH_DIAMETER`），
卡片改尺寸时开关跟着缩放。字体系列取自 :func:`presentation.qt.theme.card_code_font`，
只改字号，不再另起一套字体。
颜色全部来自 :data:`presentation.qt.theme.TOKENS`，其中包括为两个通道新增的
``channel_cool`` / ``channel_warm`` 两个浅底色与 ``power_switch_*`` 一组开关色。

**运行约束**：位图用 ``QPixmap`` 绘制，必须在创建 ``QApplication`` 之后、在 GUI
线程里调用。卡片控件本身也只在 GUI 线程中使用。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Mapping

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QAbstractButton, QSizePolicy, QWidget

from .theme import (
    CARD_RADIUS,
    TOKENS,
    card_code_font,
    card_status_font,
)

__all__ = [
    "CHANNEL_NAMES",
    "POWER_CARD_HEIGHT",
    "POWER_CARD_SIZE",
    "POWER_CARD_WIDTH",
    "PROTECTION_KINDS",
    "PowerCardLayout",
    "PowerChannelData",
    "PowerChannelLayout",
    "PowerSupplyCard",
    "PowerSupplyCardData",
    "ProtectionCheck",
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
# 圆形电源键：直径按卡片高度取 18%，与参考实现里 70px 按钮 / 约 390px 卡面的比例接近；
# 卡片尺寸变化时开关跟着缩放，不另写死像素。开关与通道标题同处一行，开关在左。
_SWITCH_DIAMETER = round(POWER_CARD_HEIGHT * 0.18, 1)
_GRID_TOP = 130.0
_SWITCH_CENTER_Y = (_TITLE_HEIGHT + _GRID_TOP) / 2.0
_SWITCH_LABEL_GAP = 8.0
_SWITCH_LABEL_WIDTH = 46.0
_ROW_HEIGHT = 38.0
_ROW_GAP = 10.0
_BOX_HEIGHT = 28.0
_LABEL_WIDTH = 24.0
_LABEL_GAP = 4.0
_CELL_WIDTH = (_HALF_WIDTH - 2.0 * _PADDING - _ROW_GAP) / 2.0
_BOX_WIDTH = _CELL_WIDTH - _LABEL_WIDTH - _LABEL_GAP
#: 第一行只剩工作值：VS（电压设定）、IS（电流设定）。
_VALUE_TEXTS = ("VS", "IS")
#: 第二行是保护勾选框，顺序也是卡面上的左右顺序：左电压、右电流。
PROTECTION_KINDS = ("voltage", "current")
_PROTECTION_TEXTS = ("电压保护", "电流保护")
#: 勾选框方块固定 14 逻辑像素，并与上一行的 ``VS`` / ``IS`` 标签共用同一条中线；
#: 文字从数值框的左边缘开始，因此“电压保护”与“12.00”左对齐。
_INDICATOR_SIZE = 14.0
_INDICATOR_INSET = (_LABEL_WIDTH - _INDICATOR_SIZE) / 2.0
_HINT_TEXT = "勾选后保护阈值自动 +10%"
_HINT_TOP = _GRID_TOP + 2.0 * _ROW_HEIGHT + _ROW_GAP + 2.0
_HINT_HEIGHT = 12.0

# ---- 配色：全部来自 TOKENS，关闭态只换 token，不另造一套灰 ----------------------
_SURFACE = QColor(TOKENS["surface"])
_SURFACE_ALT = QColor(TOKENS["surface_alt"])
_COOL = QColor(TOKENS["channel_cool"])
_WARM = QColor(TOKENS["channel_warm"])
_OFF = QColor(TOKENS["disabled_bg"])
_BORDER = QColor(TOKENS["border"])
_BORDER_STRONG = QColor(TOKENS["border_strong"])
_TEXT = QColor(TOKENS["text"])
_TEXT_MUTED = QColor(TOKENS["text_muted"])
_DISABLED_TEXT = QColor(TOKENS["disabled_text"])
_PRIMARY = QColor(TOKENS["primary"])
_WARNING = QColor(TOKENS["warning"])
# 电源键：关闭是深灰金属面，点亮后转绿并向外发光；配色逐项来自 TOKENS。
_SWITCH_BODY_OFF = QColor(TOKENS["power_switch_off"])
_SWITCH_BORDER_OFF = QColor(TOKENS["power_switch_off_border"])
_SWITCH_ICON_OFF = QColor(TOKENS["power_switch_icon_off"])
_SWITCH_BODY_ON = QColor(TOKENS["power_switch_on"])
_SWITCH_BORDER_ON = QColor(TOKENS["power_switch_on_border"])
_SWITCH_ICON_ON = QColor(TOKENS["power_switch_icon_on"])
_SWITCH_GLOW = QColor(TOKENS["power_switch_glow"])
_SWITCH_ICON_GLOW = QColor(TOKENS["power_switch_icon_glow"])
_SWITCH_INSET = QColor(TOKENS["power_switch_inset"])


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
    """一个电源通道在卡面上的快照：开关状态 + 两个工作值 + 两个保护开关。

    ``channel`` 只能是 ``CH1`` / ``CH2``；两个数值依次是电压设定、电流设定，单位
    分别是 V、A。保护阈值不再出现在卡面上，只保留“是否启用电压/电流保护”两个
    状态，阈值仍由表单按工作值的 110% 推导。数值只做“有限且非负”的显示校验，
    不在这里套用配置页的 0–50 V / 0–10 A 输入范围：那是输入控件的边界，不是卡面
    资产的责任。
    """

    channel: str
    enabled: bool = True
    voltage_set: float = 0.0
    current_set: float = 0.0
    voltage_protection_enabled: bool = False
    current_protection_enabled: bool = False

    def __post_init__(self) -> None:
        if self.channel not in CHANNEL_NAMES:
            raise ValueError(
                f"通道必须是 {' / '.join(CHANNEL_NAMES)}，收到 {self.channel!r}"
            )
        for field, label in (
            ("enabled", "enabled"),
            ("voltage_protection_enabled", "电压保护"),
            ("current_protection_enabled", "电流保护"),
        ):
            if not isinstance(getattr(self, field), bool):
                raise TypeError(f"通道 {self.channel} 的 {label} 必须是 bool")
        for field, label in (
            ("voltage_set", "电压设定"),
            ("current_set", "电流设定"),
        ):
            object.__setattr__(
                self,
                field,
                _finite_non_negative(getattr(self, field), f"{self.channel} {label}"),
            )

    def protection_enabled(self, kind: str) -> bool:
        """某种保护的启用状态；``kind`` 只能是 ``voltage`` / ``current``。"""
        if kind == "voltage":
            return self.voltage_protection_enabled
        if kind == "current":
            return self.current_protection_enabled
        raise KeyError(f"保护类型必须是 voltage / current，收到 {kind!r}")

    def with_protection(self, kind: str, enabled: bool) -> "PowerChannelData":
        """返回一份只改某种保护开关的新快照。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        self.protection_enabled(kind)
        return replace(self, **{f"{kind}_protection_enabled": enabled})


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

    def channel_protection(self, name: str, kind: str) -> bool:
        """某个通道某种保护的启用状态；名称/类型不合法时抛 ``KeyError``。"""
        return self.channel(name).protection_enabled(kind)

    def with_channel_protection(
        self, name: str, kind: str, enabled: bool
    ) -> "PowerSupplyCardData":
        """返回一份只改某个通道某种保护开关的新快照（其余值原样保留）。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        self.channel(name)
        return replace(
            self,
            channels=tuple(
                item.with_protection(kind, enabled) if item.channel == name else item
                for item in self.channels
            ),
        )


# ---- 几何 ------------------------------------------------------------------


@dataclass(frozen=True)
class PowerChannelLayout:
    """一个通道在卡面坐标系里的位置：面板、标题、开关、两个值、两个勾选框与提示。"""

    channel: str
    panel: QRectF
    label: QRectF
    switch: QRectF
    value_labels: tuple[QRectF, ...]
    value_boxes: tuple[QRectF, ...]
    #: 两个保护勾选框的整块命中区（方块 + 文字），顺序是电压、电流。
    protection_checks: tuple[QRectF, ...]
    #: 两个保护勾选框的方块本身，绘制与焦点框用它。
    protection_indicators: tuple[QRectF, ...]
    #: 两个保护勾选框的文字区，左边缘与上一行数值框对齐。
    protection_labels: tuple[QRectF, ...]
    #: 通道底部“勾选后保护阈值自动 +10%”的一行小字。
    hint: QRectF


@dataclass(frozen=True)
class PowerCardLayout:
    """整张卡片的几何：卡片、标题带与左右两个通道。"""

    card: QRectF
    title: QRectF
    channels: tuple[PowerChannelLayout, ...]


def power_card_layout() -> PowerCardLayout:
    """算出整张卡片的几何；绘制、命中区与测试都读这一份结果。

    卡片尺寸固定，因此不需要宽高参数。左右通道按 :data:`CHANNEL_NAMES` 的顺序
    排列；每个通道顶部是“开关在左、标题在右”的一行，第一行是 ``VS / IS`` 两个
    工作值，第二行是电压保护、电流保护两个勾选框，底部一行小字提示自动阈值。
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
        # 开关 + 标题当作一整组在通道里居中；标题框只放“小色点 + CH1/CH2”，
        # 宽度按最宽的三个等宽字预留，绘制时再按实际字宽在框内居中。
        row_width = _SWITCH_DIAMETER + _SWITCH_LABEL_GAP + _SWITCH_LABEL_WIDTH
        switch = QRectF(
            left + (_HALF_WIDTH - row_width) / 2.0,
            _SWITCH_CENTER_Y - _SWITCH_DIAMETER / 2.0,
            _SWITCH_DIAMETER,
            _SWITCH_DIAMETER,
        )
        label = QRectF(
            switch.right() + _SWITCH_LABEL_GAP,
            switch.top(),
            _SWITCH_LABEL_WIDTH,
            _SWITCH_DIAMETER,
        )
        value_labels: list[QRectF] = []
        value_boxes: list[QRectF] = []
        for value_index in range(len(_VALUE_TEXTS)):
            cell_x = left + _PADDING + value_index * (_CELL_WIDTH + _ROW_GAP)
            cell_y = _GRID_TOP
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
        protection_checks: list[QRectF] = []
        protection_indicators: list[QRectF] = []
        protection_labels: list[QRectF] = []
        for protection_index in range(len(PROTECTION_KINDS)):
            cell_x = left + _PADDING + protection_index * (_CELL_WIDTH + _ROW_GAP)
            cell_y = _GRID_TOP + _ROW_HEIGHT + _ROW_GAP
            protection_checks.append(
                QRectF(cell_x, cell_y, _CELL_WIDTH, _ROW_HEIGHT)
            )
            # 方块与上一行 ``VS`` / ``IS`` 标签同一条中线；文字与数值框左对齐。
            protection_indicators.append(
                QRectF(
                    cell_x + _INDICATOR_INSET,
                    cell_y + (_ROW_HEIGHT - _INDICATOR_SIZE) / 2.0,
                    _INDICATOR_SIZE,
                    _INDICATOR_SIZE,
                )
            )
            protection_labels.append(
                QRectF(
                    cell_x + _LABEL_WIDTH + _LABEL_GAP,
                    cell_y,
                    _BOX_WIDTH,
                    _ROW_HEIGHT,
                )
            )
        hint = QRectF(
            left + _PADDING,
            _HINT_TOP,
            _HALF_WIDTH - 2.0 * _PADDING,
            _HINT_HEIGHT,
        )
        channels.append(
            PowerChannelLayout(
                channel=name,
                panel=panel,
                label=label,
                switch=switch,
                value_labels=tuple(value_labels),
                value_boxes=tuple(value_boxes),
                protection_checks=tuple(protection_checks),
                protection_indicators=tuple(protection_indicators),
                protection_labels=tuple(protection_labels),
                hint=hint,
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
    """通道标题 ``CH1`` / ``CH2``：比设备码小一号，仍用等宽加粗。"""
    font = QFont(card_code_font())
    font.setPointSizeF(10.5)
    return font


def _value_label_font() -> QFont:
    """两个值的缩写 ``VS`` / ``IS``：10pt 等宽加粗。"""
    font = QFont(card_code_font())
    font.setPointSizeF(10.0)
    return font


def _value_font() -> QFont:
    """数值本身：12pt 等宽加粗，保证 ``50.00`` 这类五位数也放得下。"""
    font = QFont(card_code_font())
    font.setPointSizeF(12.0)
    return font


def _protection_font() -> QFont:
    """勾选框文字“电压保护”“电流保护”：9pt 中等字重，用界面字体保证中文清晰。"""
    font = QFont(card_status_font())
    font.setPointSizeF(9.0)
    font.setWeight(QFont.Weight.DemiBold)
    return font


def _hint_font() -> QFont:
    """通道底部的自动阈值提示：8pt 常规字重。"""
    font = QFont(card_status_font())
    font.setPointSizeF(8.0)
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
    """通道标题：一个小色点 + ``CH1`` / ``CH2``，与开关并排、在标题框内居中。"""
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


def _paint_protection(
    painter: QPainter,
    label_rect: QRectF,
    indicator_rect: QRectF,
    label: str,
    checked: bool,
    index: int,
    enabled: bool,
    *,
    hovered: bool,
    focused: bool,
) -> None:
    """一个保护勾选框：左侧方块 + 右侧文字；关闭通道时整组降到禁用色。

    方块用通道强调色填满表示已勾选，未勾选只画描边；悬停只提亮描边，焦点在方块
    外侧画一圈主色框，键盘用户能看出当前停在哪个勾选框上。
    """
    painter.save()
    accent = _accent(index)
    text_color = _TEXT if enabled else _DISABLED_TEXT
    if checked:
        fill = accent if enabled else _BORDER_STRONG
        border = fill
    else:
        fill = _SURFACE if enabled else _SURFACE_ALT
        border = accent if hovered and enabled else _BORDER_STRONG
    pen_width = 1.5 if enabled else 1.0
    inset = pen_width / 2.0
    box = indicator_rect.adjusted(inset, inset, -inset, -inset)
    painter.setBrush(fill)
    painter.setPen(QPen(border, pen_width))
    painter.drawRoundedRect(box, 3.0, 3.0)

    if checked:
        tick = QPainterPath(
            QPointF(
                indicator_rect.left() + indicator_rect.width() * 0.26,
                indicator_rect.top() + indicator_rect.height() * 0.52,
            )
        )
        tick.lineTo(
            QPointF(
                indicator_rect.left() + indicator_rect.width() * 0.43,
                indicator_rect.top() + indicator_rect.height() * 0.70,
            )
        )
        tick.lineTo(
            QPointF(
                indicator_rect.left() + indicator_rect.width() * 0.75,
                indicator_rect.top() + indicator_rect.height() * 0.33,
            )
        )
        painter.setBrush(Qt.NoBrush)
        painter.setPen(
            QPen(
                QColor("#FFFFFF") if enabled else _SURFACE,
                1.9,
                Qt.SolidLine,
                Qt.RoundCap,
                Qt.RoundJoin,
            )
        )
        painter.drawPath(tick)

    if focused:
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_PRIMARY, 1.5))
        painter.drawRoundedRect(
            indicator_rect.adjusted(-2.5, -2.5, 2.5, 2.5), 4.0, 4.0
        )

    painter.setFont(_protection_font())
    painter.setPen(text_color)
    painter.drawText(
        label_rect,
        int(Qt.AlignLeft | Qt.AlignVCenter),
        label,
    )
    painter.restore()


def _paint_hint(painter: QPainter, rect: QRectF, enabled: bool) -> None:
    """通道底部的自动阈值提示：一行灰色小字，关闭通道时同样降到禁用色。"""
    painter.save()
    painter.setFont(_hint_font())
    painter.setPen(_TEXT_MUTED if enabled else _DISABLED_TEXT)
    painter.drawText(rect, int(Qt.AlignCenter), _HINT_TEXT)
    painter.restore()


def _mix_color(start: QColor, end: QColor, amount: float) -> QColor:
    """按 0–1 的比例混合两种颜色；比例越界时夹到端点。"""
    ratio = min(1.0, max(0.0, float(amount)))
    return QColor.fromRgbF(
        start.redF() + (end.redF() - start.redF()) * ratio,
        start.greenF() + (end.greenF() - start.greenF()) * ratio,
        start.blueF() + (end.blueF() - start.blueF()) * ratio,
        start.alphaF() + (end.alphaF() - start.alphaF()) * ratio,
    )


def _with_alpha(color: QColor, alpha: float) -> QColor:
    """同一颜色换一个 0–1 的透明度，用于逐层叠出辉光与内阴影。"""
    tinted = QColor(color)
    tinted.setAlphaF(min(1.0, max(0.0, float(alpha))))
    return tinted


def _draw_power_icon(
    painter: QPainter,
    center: QPointF,
    size: float,
    ring_pen: QPen,
    stem_pen: QPen | None = None,
) -> None:
    """画一个电源符号：顶部留口的圆环 + 从缺口伸进圆心的竖线。

    圆环与竖线各用一支笔，便于分别控制粗细；只描边不填充，离屏环境里不依赖字体。
    ``stem_pen`` 为 ``None`` 时只画圆环，用于只让圆环带光晕的场合。
    """
    radius = size / 2.0
    ring = QRectF(center.x() - radius, center.y() - radius, size, size)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(ring_pen)
    painter.drawArc(ring, 120 * 16, 300 * 16)
    if stem_pen is None:
        return
    painter.setPen(stem_pen)
    painter.drawLine(
        QPointF(center.x(), center.y() - radius * 1.32),
        QPointF(center.x(), center.y() - radius * 0.08),
    )


def _paint_switch_glow(
    painter: QPainter,
    rect: QRectF,
    strength: float,
) -> None:
    """按钮外侧的绿色辉光：三层径向渐变由近及远淡出。

    参考实现用多条 ``box-shadow`` 叠出 5/40/100px 的光晕；QPainter 里用不同半径、
    不同透明度的同心径向渐变等效表达，因此不依赖模糊滤镜，离屏渲染也一致。
    """
    if strength <= 0.001:
        return
    center = rect.center()
    radius = rect.width() / 2.0
    painter.save()
    painter.setPen(Qt.NoPen)
    for spread, alpha in ((1.5, 0.34), (2.3, 0.16), (3.3, 0.08)):
        outer = radius * spread
        edge = radius / outer
        gradient = QRadialGradient(center, outer)
        gradient.setColorAt(0.0, _with_alpha(_SWITCH_GLOW, 0.0))
        gradient.setColorAt(max(0.0, edge - 0.02), _with_alpha(_SWITCH_GLOW, 0.0))
        gradient.setColorAt(edge, _with_alpha(_SWITCH_GLOW, alpha * strength))
        gradient.setColorAt(1.0, _with_alpha(_SWITCH_GLOW, 0.0))
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(center, outer, outer)
    painter.restore()


def _paint_switch(
    painter: QPainter,
    rect: QRectF,
    *,
    lit: float,
    hovered: bool,
    focused: bool,
    pressed: bool,
) -> None:
    """圆形电源键：关闭是深灰金属按钮，点亮后转绿并向外发光。

    ``lit`` 是 0–1 的点亮进度，由控件在 500 ms 内插值，导出位图直接取 0 / 1；
    悬停只轻微提亮，按下沿用参考实现的行为——按键瞬间点亮、整体下移一点，
    焦点环画在按钮外侧，键盘用户能看出当前在哪个通道上。
    """
    lit = 1.0 if pressed else min(1.0, max(0.0, float(lit)))
    if pressed:
        rect = rect.translated(0.0, rect.height() * 0.05)
    glow_strength = lit
    if hovered and lit > 0.0:
        glow_strength = min(1.0, lit + 0.12)

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    _paint_switch_glow(painter, rect, glow_strength)

    border = max(1.5, rect.width() * 0.05)
    body = rect.adjusted(border / 2.0, border / 2.0, -border / 2.0, -border / 2.0)
    body_path = QPainterPath()
    body_path.addEllipse(body)

    fill = _mix_color(_SWITCH_BODY_OFF, _SWITCH_BODY_ON, lit)
    if hovered:
        fill = fill.lighter(107)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QBrush(fill))
    painter.drawPath(body_path)

    # 内阴影与内发光都裁在按钮圆形里：关闭时边缘压暗（inset 阴影），
    # 点亮时边缘泛青（inset 光晕），与参考实现的多层 inset box-shadow 对应。
    painter.save()
    painter.setClipPath(body_path)
    if lit < 0.999:
        inset = QRadialGradient(body.center(), body.width() / 2.0)
        inset.setColorAt(0.0, _with_alpha(_SWITCH_INSET, 0.0))
        inset.setColorAt(0.70, _with_alpha(_SWITCH_INSET, 0.0))
        inset.setColorAt(1.0, _with_alpha(_SWITCH_INSET, 0.55 * (1.0 - lit)))
        painter.fillPath(body_path, QBrush(inset))
    if lit > 0.001:
        inner = QRadialGradient(body.center(), body.width() / 2.0)
        inner.setColorAt(0.0, _with_alpha(_SWITCH_GLOW, 0.0))
        inner.setColorAt(0.52, _with_alpha(_SWITCH_GLOW, 0.0))
        inner.setColorAt(0.88, _with_alpha(_SWITCH_GLOW, 0.30 * lit))
        inner.setColorAt(1.0, _with_alpha(_SWITCH_GLOW, 0.62 * lit))
        painter.fillPath(body_path, QBrush(inner))
    painter.restore()

    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(_mix_color(_SWITCH_BORDER_OFF, _SWITCH_BORDER_ON, lit), border))
    painter.drawEllipse(body)

    icon_size = rect.width() * 0.36
    # 圆环按参考比例取按钮直径的 5.7%（70px 按钮上的 4px）；竖线在参考里是圆环的两倍宽，
    # 但在卡片尺寸下显得笨重，这里收细到与圆环同宽，保留清晰的电源符号轮廓。
    ring_width = max(1.6, rect.width() * 0.057)
    stem_width = ring_width
    if glow_strength > 0.001:
        for width_scale, alpha in ((2.8, 0.10), (1.8, 0.24)):
            halo = QPen(
                _with_alpha(_SWITCH_ICON_GLOW, alpha * glow_strength),
                ring_width * width_scale,
            )
            halo.setCapStyle(Qt.RoundCap)
            _draw_power_icon(painter, rect.center(), icon_size, halo)
    icon_color = _mix_color(_SWITCH_ICON_OFF, _SWITCH_ICON_ON, lit)
    ring = QPen(icon_color, ring_width)
    ring.setCapStyle(Qt.RoundCap)
    stem = QPen(icon_color, stem_width)
    stem.setCapStyle(Qt.RoundCap)
    _draw_power_icon(painter, rect.center(), icon_size, ring, stem)

    if focused:
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_PRIMARY, 2.0))
        painter.drawEllipse(rect.adjusted(-3.5, -3.5, 3.5, 3.5))
    painter.restore()


def _paint_power_card(
    painter: QPainter,
    data: PowerSupplyCardData,
    *,
    hover_channel: str | None = None,
    focus_channel: str | None = None,
    pressed_channel: str | None = None,
    hover_protection: tuple[str, str] | None = None,
    focus_protection: tuple[str, str] | None = None,
    glow: Mapping[str, float] | None = None,
) -> None:
    """把整张卡画到 ``painter`` 上；控件与导出位图共用这一份绘制。

    ``glow`` 是“通道 -> 点亮进度（0–1）”，控件用它播 500 ms 的亮灭过渡；不传时
    取数据的终态（开 = 1、关 = 0），导出位图因此总是画最终样子。
    """
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
        # 先画开关再画文字与数值：按钮的辉光是装饰，数据与标题必须压在最上层，
        # 否则绿色光晕会把数值框和标题一起染色（参考实现里按钮 z-index 高于同级内容）。
        _paint_switch(
            painter,
            channel_layout.switch,
            lit=(glow or {}).get(
                channel.channel,
                1.0 if channel.enabled else 0.0,
            ),
            hovered=hover_channel == channel.channel,
            focused=focus_channel == channel.channel,
            pressed=pressed_channel == channel.channel,
        )
        _paint_channel_label(painter, channel_layout, index, channel.enabled)
        values = (channel.voltage_set, channel.current_set)
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
        for kind_index, kind in enumerate(PROTECTION_KINDS):
            _paint_protection(
                painter,
                channel_layout.protection_labels[kind_index],
                channel_layout.protection_indicators[kind_index],
                _PROTECTION_TEXTS[kind_index],
                channel.protection_enabled(kind),
                index,
                channel.enabled,
                hovered=hover_protection == (channel.channel, kind),
                focused=focus_protection == (channel.channel, kind),
            )
        _paint_hint(painter, channel_layout.hint, channel.enabled)

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
        return QSize(round(_SWITCH_DIAMETER), round(_SWITCH_DIAMETER))

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


class ProtectionCheck(QAbstractButton):
    """保护勾选框的透明命中区：外观由父卡片按勾选状态绘制。

    与 :class:`ChannelSwitch` 一样保留 Qt 的键盘焦点、``Space`` 激活与无障碍语义。
    ``kind`` 只能是 ``voltage`` / ``current``；控件本身不画像素。
    """

    hover_changed = Signal(str, str, bool)
    focus_changed = Signal(str, str, bool)

    def __init__(
        self, channel: str, kind: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        if channel not in CHANNEL_NAMES:
            raise ValueError(f"通道必须是 {' / '.join(CHANNEL_NAMES)}，收到 {channel!r}")
        if kind not in PROTECTION_KINDS:
            raise ValueError(
                f"保护类型必须是 {' / '.join(PROTECTION_KINDS)}，收到 {kind!r}"
            )
        self.channel = channel
        self.kind = kind
        self.setCheckable(True)
        self.setAutoExclusive(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        label = "电压保护" if kind == "voltage" else "电流保护"
        self.setAccessibleName(f"通道 {channel} {label}")
        self.setAccessibleDescription("勾选后保护阈值自动取比设定值高 10% 的值")

    def sizeHint(self) -> QSize:
        return QSize(round(_CELL_WIDTH), round(_ROW_HEIGHT))

    def paintEvent(self, event) -> None:
        # 外观由父卡片统一画；这里只接受事件，不留下任何控件自己的底色。
        event.accept()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.hover_changed.emit(self.channel, self.kind, True)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.hover_changed.emit(self.channel, self.kind, False)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.focus_changed.emit(self.channel, self.kind, True)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.focus_changed.emit(self.channel, self.kind, False)


class PowerSupplyCard(QWidget):
    """配置页的电源通道卡片：只读展示两个通道，开关与保护勾选框只改卡面状态。

    ``data`` 是构造时的快照；用户拨动通道开关或保护勾选框只更新本卡内部的快照，
    不写配置、不碰硬件。页面通过 :attr:`channel_toggled` / :attr:`protection_toggled`
    决定之后怎么处理这些动作。

    通道开关与四个保护勾选框都是可聚焦控件：``Tab`` 依次经过它们，``Space`` /
    ``Enter`` 切换状态，鼠标点击同样有效。数值是只读的，不创建输入控件。
    """

    channel_toggled = Signal(str, bool)
    protection_toggled = Signal(str, str, bool)

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
        self._hover_protection: tuple[str, str] | None = None
        self._focus_protection: tuple[str, str] | None = None
        # 每个通道的点亮进度（0–1）：控件用它播过渡，导出的位图则只取终态。
        self._glow: dict[str, float] = {
            name: 1.0 if data.channel(name).enabled else 0.0
            for name in CHANNEL_NAMES
        }
        self._glow_animations: dict[str, QVariantAnimation] = {}

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

        self._protection_checks: dict[tuple[str, str], ProtectionCheck] = {}
        for channel_layout in self._layout.channels:
            name = channel_layout.channel
            channel_data = data.channel(name)
            for kind, check_rect in zip(
                PROTECTION_KINDS, channel_layout.protection_checks
            ):
                check = ProtectionCheck(name, kind, self)
                check.setChecked(channel_data.protection_enabled(kind))
                check.setEnabled(channel_data.enabled)
                check.setGeometry(check_rect.toRect())
                check.toggled.connect(
                    lambda checked, channel=name, kind=kind: (
                        self._on_protection_toggled(channel, kind, checked)
                    )
                )
                check.hover_changed.connect(self._on_protection_hover)
                check.focus_changed.connect(self._on_protection_focus)
                self._protection_checks[(name, kind)] = check

        # 焦点顺序按“通道开关 → 电压保护 → 电流保护”逐通道向后，鼠标与键盘一致。
        order = []
        for name in CHANNEL_NAMES:
            order.append(self._switches[name])
            order.extend(
                self._protection_checks[(name, kind)] for kind in PROTECTION_KINDS
            )
        for previous, following in zip(order, order[1:]):
            self.setTabOrder(previous, following)
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

    def protection_check(self, channel: str, kind: str) -> ProtectionCheck:
        """取某个通道某种保护的透明命中控件；不合法时抛 ``KeyError``。"""
        check = self._protection_checks.get((channel, kind))
        if check is None:
            raise KeyError(f"卡片里没有通道 {channel!r} 的 {kind!r} 保护勾选框")
        return check

    def channel_protection(self, channel: str, kind: str) -> bool:
        """某个通道某种保护此刻是否启用。"""
        return self.protection_check(channel, kind).isChecked()

    # ---------- 写入 ----------

    def set_data(self, data: PowerSupplyCardData) -> None:
        """整张卡换一份快照；不触发 :attr:`channel_toggled` 或保护信号。"""
        if not isinstance(data, PowerSupplyCardData):
            raise TypeError(f"data 必须是 PowerSupplyCardData，收到 {data!r}")
        self._data = data
        for channel_layout in self._layout.channels:
            name = channel_layout.channel
            channel = data.channel(name)
            switch = self._switches[name]
            if switch.isChecked() != channel.enabled:
                switch.blockSignals(True)
                switch.setChecked(channel.enabled)
                switch.blockSignals(False)
            for kind in PROTECTION_KINDS:
                check = self._protection_checks[(name, kind)]
                checked = channel.protection_enabled(kind)
                if check.isChecked() != checked:
                    check.blockSignals(True)
                    check.setChecked(checked)
                    check.blockSignals(False)
                check.setEnabled(channel.enabled)
            self._settle_glow(name, 1.0 if channel.enabled else 0.0)
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
            for kind in PROTECTION_KINDS:
                self._protection_checks[(channel, kind)].setEnabled(enabled)
            self._settle_glow(channel, 1.0 if enabled else 0.0)
            self._update_tooltips()
            self.update()
        if emit:
            self.channel_toggled.emit(channel, enabled)

    def set_protection_enabled(
        self,
        channel: str,
        kind: str,
        enabled: bool,
        *,
        emit: bool = False,
    ) -> None:
        """程序化设置某个保护勾选框；``emit=True`` 时仍发一次信号。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        check = self.protection_check(channel, kind)
        if check.isChecked() != enabled:
            check.blockSignals(True)
            check.setChecked(enabled)
            check.blockSignals(False)
            self._data = self._data.with_channel_protection(
                channel, kind, enabled
            )
            self._update_tooltips()
            self.update()
        if emit:
            self.protection_toggled.emit(channel, kind, enabled)

    # ---------- 点亮过渡 ----------

    def _settle_glow(self, channel: str, value: float) -> None:
        """把某个通道的点亮进度直接定到终态；程序化路径不播过渡动画。"""
        animation = self._glow_animations.get(channel)
        if animation is not None:
            animation.stop()
        self._glow[channel] = min(1.0, max(0.0, float(value)))
        self.update()

    def _animate_glow(self, channel: str, enabled: bool) -> None:
        """用户拨动后播放 500 ms 的点亮/熄灭，与参考实现的 ``transition`` 同长。"""
        animation = self._glow_animations.get(channel)
        if animation is None:
            animation = QVariantAnimation(self)
            animation.setDuration(500)
            animation.setEasingCurve(QEasingCurve.InOutQuad)
            animation.valueChanged.connect(
                lambda value, name=channel: self._on_glow_changed(name, value)
            )
            self._glow_animations[channel] = animation
        animation.stop()
        # 按下时按钮已经点亮（参考实现的 :active 状态），所以从 1 开始：
        # 打开时不会先灭再亮，关闭时才走完整的 1 → 0 过渡。
        animation.setStartValue(1.0)
        animation.setEndValue(1.0 if enabled else 0.0)
        animation.start()

    def _on_glow_changed(self, channel: str, value: Any) -> None:
        self._glow[channel] = min(1.0, max(0.0, float(value)))
        self.update()

    # ---------- 开关事件 ----------

    def _on_switch_toggled(self, channel: str, enabled: bool) -> None:
        """用户拨动开关：只更新卡面快照，不写配置、不连硬件。"""
        self._data = self._data.with_channel_enabled(channel, bool(enabled))
        for kind in PROTECTION_KINDS:
            self._protection_checks[(channel, kind)].setEnabled(bool(enabled))
        self._animate_glow(channel, bool(enabled))
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

    def _on_protection_toggled(
        self, channel: str, kind: str, enabled: bool
    ) -> None:
        """用户勾选/取消保护：只更新卡面快照，不写配置、不碰硬件。"""
        self._data = self._data.with_channel_protection(
            channel, kind, bool(enabled)
        )
        self._update_tooltips()
        self.update()
        self.protection_toggled.emit(channel, kind, bool(enabled))

    def _on_protection_hover(
        self, channel: str, kind: str, hovered: bool
    ) -> None:
        self._hover_protection = (channel, kind) if hovered else None
        self.update()

    def _on_protection_focus(
        self, channel: str, kind: str, focused: bool
    ) -> None:
        self._focus_protection = (channel, kind) if focused else None
        self.update()

    # ---------- 提示与绘制 ----------

    def _update_tooltips(self) -> None:
        """悬浮提示补上卡面放不下的信息：每个缩写的含义、单位与通道状态。"""
        lines = [
            self._data.code or "未识别电源",
            "VS 电压设定 · IS 电流设定",
            "勾选电压/电流保护后，阈值自动取比设定值高 10% 的值",
        ]
        for channel in self._data.channels:
            lines.append(
                f"{channel.channel} {'开' if channel.enabled else '关'}："
                f"VS {channel.voltage_set:.2f} V · IS {channel.current_set:.2f} A · "
                f"电压保护 {'开' if channel.voltage_protection_enabled else '关'} · "
                f"电流保护 {'开' if channel.current_protection_enabled else '关'}"
            )
        text = "\n".join(lines)
        self.setToolTip(text)
        for channel in self._data.channels:
            switch = self._switches[channel.channel]
            switch.setToolTip(
                f"{channel.channel} {'开' if channel.enabled else '关'}\n"
                f"点击切换卡面显示状态（暂不写入配置）\n{text}"
            )
            for kind in PROTECTION_KINDS:
                label = "电压保护" if kind == "voltage" else "电流保护"
                check = self._protection_checks[(channel.channel, kind)]
                check.setToolTip(
                    f"{channel.channel} {label}\n"
                    "勾选后保护阈值自动取比设定值高 10% 的值，无需手工填写。\n"
                    f"{'点击取消' if channel.protection_enabled(kind) else '点击启用'}"
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
                hover_protection=self._hover_protection,
                focus_protection=self._focus_protection,
                glow=self._glow,
            )
        finally:
            painter.end()
