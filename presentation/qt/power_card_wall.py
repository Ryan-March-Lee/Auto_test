"""电源配置页的卡片视图（``PowerCardWall``）。

电源配置页以通道卡片为主视图：每台电源一张 440×186 的通道卡片（左 CH1 浅蓝、
右 CH2 浅琥珀；两个通道的 OFF / DUT / Driver 竖直滚筒分别贴住左右上角，设备码在
顶部正中的白色铭牌上；最下面一行是上电顺序与间隔），卡片按仪器排布页九宫格里的
相对位置摆放在页面中央。卡片下方只剩效率测量的漏极选择；通道参数、上电角色与
顺序本身只在这一张卡上编辑。

**真实控件**：卡片墙直接承载 :class:`presentation.qt.power_card.PowerSupplyCard`
子控件（不是位图 + 坐标命中）。角色滚筒、VS/IS 输入框与保护勾选框都由 Qt 处理
鼠标、键盘与焦点，卡片墙只负责按格位摆位、按可用宽度等比缩放并转发信号。

**相对位置**：仪器排布页把电源拖进 3×3 格子，格位下标从左上开始、先行后列。
本控件保留这些格位的行列关系：卡片只按被占用格子的外接矩形排版，空行/空列被
收掉，因此“左上角那台电源”在卡片视图里仍然在左上角。卡片数量多到一屏放不下时，
整块按可用宽度等比缩小，相对位置不变；卡片缩放时子控件几何与字号一起调整。

**间距与投影**：同排卡片之间留 22 的逻辑间距，多张卡并排时不挤在一起。卡片是
真实子控件，投影只能由本控件在卡片下面画（:meth:`PowerCardWall.paintEvent`）：
三层圆角矩形由内到外叠出柔和阴影，墙上下各留 8 的投影空间，避免阴影被边界裁掉。
展开动画接管卡面时（``set_paint_enabled(False)``）不再画静态投影。

**角色状态**：滚筒档位由页面按两份 ``power_plan`` 回填，换档后页面再把路由写回
计划；本控件只保存卡片快照并发 ``channel_mode_changed``，不写配置。页面重建卡片
（应用绑定、回填配置）时，``preserve_edit_state=True`` 会保留同一台电源当前的
角色、数值、保护与上电间隔；``False`` 则完全按传入数据重建。控件本身不读写配置。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from PySide6.QtCore import QRectF, QSize, Signal, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from .power_card import (
    CHANNEL_NAMES,
    DEFAULT_POWER_ON_DELAY_S,
    POWER_CARD_HEIGHT,
    POWER_CARD_RADIUS,
    POWER_CARD_WIDTH,
    PROTECTION_KINDS,
    VALUE_KINDS,
    PowerChannelData,
    PowerSupplyCard,
    PowerSupplyCardData,
)

__all__ = ["PowerCardCell", "PowerCardWall", "power_card_data"]

#: 卡片之间的间距（逻辑像素）。卡片本身缩小时间距不缩，保持卡片轮廓清晰。
_CARD_GAP = 22.0

#: 卡片墙左右留白：不贴住内容列边缘。
_SIDE_MARGIN = 16.0

#: 卡片投影在墙坐标里占的额外留白：卡片控件是子控件，投影只能由墙自己画，
#: 上下留一点空间，柔和阴影才不会被墙的边界裁掉。
_SHADOW_PAD = 8.0

#: 九宫格列数，与仪器排布页一致。
_SLOT_COLUMNS = 3


@dataclass(frozen=True)
class PowerCardCell:
    """一张通道卡片在墙上的位置：供电角色、数据与格位下标。"""

    supply_id: str
    data: PowerSupplyCardData
    slot: int


def _number(value: Any, default: float = 0.0) -> float:
    """卡片显示的数值：只接受非 bool 的有限非负数，其余显示为缺省值。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        return default
    return number


def _channel_data(name: str, entry: Mapping[str, Any]) -> PowerChannelData:
    channels = entry.get("channels")
    channels = channels if isinstance(channels, Mapping) else {}
    channel = channels.get(name)
    channel = channel if isinstance(channel, Mapping) else {}
    voltage = channel.get("voltage")
    voltage = voltage if isinstance(voltage, Mapping) else {}
    current = channel.get("current")
    current = current if isinstance(current, Mapping) else {}
    raw_delay = channel.get("power_on_delay_s")
    delay = (
        DEFAULT_POWER_ON_DELAY_S
        if raw_delay is None
        else _number(raw_delay, DEFAULT_POWER_ON_DELAY_S)
    )
    return PowerChannelData(
        channel=name,
        # 两个通道始终保留；角色由页面按上电计划回填，缺省是 OFF。
        mode="off",
        voltage_set=_number(voltage.get("value")),
        current_set=_number(current.get("value")),
        # 卡面只保留“是否启用保护”；阈值仍由配置读取按 110% 推导，不在这里显示。
        voltage_protection_enabled=voltage.get("protection_enabled") is True,
        current_protection_enabled=current.get("protection_enabled") is True,
        power_delay_s=delay,
    )


def power_card_data(entry: Mapping[str, Any], supply_id: str) -> PowerSupplyCardData:
    """把一条电源配置读成通道卡片数据；缺字段按 0 与未勾选保护显示。

    两个通道都会出现在卡面上（配置里没有的通道显示 0），通道是否可编辑由页面在
    重建卡片时统一决定的“本次启用状态”控制，不从配置里的键是否存在推导。
    """
    label = entry.get("label")
    code = label.strip() if isinstance(label, str) and label.strip() else supply_id
    model = entry.get("model")
    full_code = entry.get("full_code")
    device_key = entry.get("device_key")
    return PowerSupplyCardData(
        code=code,
        model=model if isinstance(model, str) else None,
        full_code=full_code if isinstance(full_code, str) else None,
        device_key=device_key if isinstance(device_key, str) else None,
        channels=tuple(_channel_data(name, entry) for name in CHANNEL_NAMES),
    )


def _merge_edit_state(
    incoming: PowerSupplyCardData, current: PowerSupplyCardData
) -> PowerSupplyCardData:
    """把卡片上尚未回填的编辑状态覆盖到新数据上（重建卡片时保留用户输入）。"""
    merged = incoming
    for name in CHANNEL_NAMES:
        old = current.channel(name)
        merged = merged.with_channel_mode(name, old.mode)
        for kind, value in (
            ("voltage", old.voltage_set),
            ("current", old.current_set),
        ):
            merged = merged.with_channel_value(name, kind, value)
        for kind in PROTECTION_KINDS:
            merged = merged.with_channel_protection(
                name, kind, old.protection_enabled(kind)
            )
        # 顺序由页面按计划重算，只有间隔是卡面上尚未回填的编辑值。
        setting = merged.channel(name)
        merged = merged.with_channel_power(
            name,
            setting.power_order,
            setting.power_order_count,
            old.power_delay_s,
        )
    return merged


class PowerCardWall(QWidget):
    """按九宫格相对位置摆放交互式电源卡片的画布。

    ``set_cards`` 接收 ``(supply_id, data)`` 与可选的 ``slots`` 映射；没有格位信息
    的电源按传入顺序补进空位。卡片是真实子控件，缩放由 :meth:`PowerSupplyCard.set_scale`
    完成，因此三列放不下时点击位置、输入焦点与字号都跟着缩放。

    卡片上的供电角色、工作值与保护变化通过信号转给页面；本控件只同步自己的卡片
    快照，不写配置。
    """

    #: ``(supply_id, channel, mode)``：供电角色，``mode`` 是 ``off`` / ``driver`` / ``dut``。
    channel_mode_changed = Signal(str, str, str)
    #: ``(supply_id, channel, kind, enabled)``；``kind`` 是 ``voltage`` / ``current``。
    protection_toggled = Signal(str, str, str, bool)
    #: ``(supply_id, channel, kind, value)``：VS / IS 输入。
    value_changed = Signal(str, str, str, float)
    #: ``(supply_id, channel, order)``：卡面上电顺序下拉框的新位置。
    channel_order_changed = Signal(str, str, int)
    #: ``(supply_id, channel, seconds)``：卡面上电间隔。
    channel_delay_changed = Signal(str, str, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("powerCardWall")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._cells: list[PowerCardCell] = []
        self._rects: dict[str, QRectF] = {}
        self._cards: dict[str, PowerSupplyCard] = {}
        self._paint_enabled = True
        self._driver_available = True

    # ---- 内容 ---------------------------------------------------------------

    def set_cards(
        self,
        cards: Iterable[tuple[str, PowerSupplyCardData]],
        slots: Mapping[str, int] | None = None,
        *,
        preserve_edit_state: bool = False,
    ) -> None:
        """替换整墙卡片；``slots`` 缺省时按九宫格顺序补齐。

        ``preserve_edit_state=True`` 用于绑定/格位变化后的重建：同一台电源的本次
        启用状态、VS/IS 与保护勾选覆盖到新数据上，未提交的编辑不会丢。回填配置时
        用 ``False``，以传入数据为准。
        """
        entries = list(cards)
        slot_of = dict(slots or {})
        used = {slot_of.get(supply_id) for supply_id, _data in entries}
        next_slot = 0
        for supply_id, _data in entries:
            if slot_of.get(supply_id) is not None:
                continue
            while next_slot in used:
                next_slot += 1
            slot_of[supply_id] = next_slot
            used.add(next_slot)

        current = {
            supply_id: card.data for supply_id, card in self._cards.items()
        }
        cells: list[PowerCardCell] = []
        active: set[str] = set()
        for supply_id, data in entries:
            if preserve_edit_state and supply_id in current:
                data = _merge_edit_state(data, current[supply_id])
            index = slot_of.get(supply_id)
            cells.append(
                PowerCardCell(
                    supply_id=supply_id,
                    data=data,
                    slot=int(index) if index is not None else 0,
                )
            )
            active.add(supply_id)

        for supply_id in set(self._cards) - active:
            card = self._cards.pop(supply_id)
            card.hide()
            card.deleteLater()

        for index, cell in enumerate(cells):
            card = self._cards.get(cell.supply_id)
            if card is None:
                card = PowerSupplyCard(cell.data, self)
                card.channel_mode_changed.connect(
                    lambda channel, mode, supply=cell.supply_id: (
                        self._on_card_mode(supply, channel, mode)
                    )
                )
                card.protection_toggled.connect(
                    lambda channel, kind, enabled, supply=cell.supply_id: (
                        self._on_card_protection(supply, channel, kind, enabled)
                    )
                )
                card.value_changed.connect(
                    lambda channel, kind, value, supply=cell.supply_id: (
                        self._on_card_value(supply, channel, kind, value)
                    )
                )
                card.power_order_changed.connect(
                    lambda channel, order, supply=cell.supply_id: (
                        self._on_card_order(supply, channel, order)
                    )
                )
                card.power_delay_changed.connect(
                    lambda channel, delay, supply=cell.supply_id: (
                        self._on_card_delay(supply, channel, delay)
                    )
                )
                self._cards[cell.supply_id] = card
            else:
                card.set_data(cell.data)
            card.set_driver_available(self._driver_available)
            # Driver 不可用时卡片会把 driver 规整成 off；墙面快照跟上，避免
            # ``cards()`` 与真实控件各说一套。
            if card.data != cell.data:
                cells[index] = replace(cell, data=card.data)
            card.setVisible(self._paint_enabled)
        self._cells = cells
        self.refresh_layout()

    def cards(self) -> tuple[PowerCardCell, ...]:
        return tuple(self._cells)

    def supply_card(self, supply_id: str) -> PowerSupplyCard | None:
        """取某台电源的卡片控件；没有这张卡时返回 ``None``。"""
        return self._cards.get(supply_id)

    def control_records(self) -> dict[str, dict[str, Any]]:
        """按 ``ConfigFormState`` 的读取形状给出每台电源的通道控件。"""
        records: dict[str, dict[str, Any]] = {}
        for supply_id, card in self._cards.items():
            records[supply_id] = {
                "channels": {
                    name: card.channel_controls(name) for name in CHANNEL_NAMES
                }
            }
        return records

    def set_protection(
        self,
        supply_id: str,
        channel: str,
        kind: str,
        enabled: bool,
    ) -> bool:
        """只改某张卡某个通道的保护开关；没有这张卡或状态未变时返回 ``False``。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        card = self._cards.get(supply_id)
        if card is None:
            return False
        if card.channel_protection(channel, kind) == enabled:
            return False
        card.set_protection_enabled(channel, kind, enabled)
        self._sync_cell(supply_id, card.data)
        return True

    def set_channel_mode(self, supply_id: str, channel: str, mode: str) -> bool:
        """只改某张卡某个通道的供电角色；没有这张卡或状态未变时返回 ``False``。"""
        card = self._cards.get(supply_id)
        if card is None:
            return False
        if mode == "driver" and not self._driver_available:
            mode = "off"
        if card.channel_mode(channel) == mode:
            return False
        card.set_channel_mode(channel, mode, animate=False)
        self._sync_cell(supply_id, card.data)
        return True

    def set_channel_modes(self, modes: Mapping[tuple[str, str], str]) -> None:
        """按 ``(supply_id, channel) -> mode`` 批量同步卡片；不发信号。"""
        for supply_id, card in self._cards.items():
            for name in CHANNEL_NAMES:
                mode = modes.get((supply_id, name), "off")
                if mode == "driver" and not self._driver_available:
                    mode = "off"
                if card.channel_mode(name) != mode:
                    card.set_channel_mode(name, mode, animate=False)
            self._sync_cell(supply_id, card.data)

    def set_channel_power_settings(
        self,
        settings: Mapping[tuple[str, str], tuple[int | None, int, float]],
    ) -> None:
        """按 ``(supply_id, channel) -> (order, count, delay)`` 批量同步卡片。"""
        for supply_id, card in self._cards.items():
            for name in CHANNEL_NAMES:
                order, count, delay = settings.get(
                    (supply_id, name),
                    (None, 0, DEFAULT_POWER_ON_DELAY_S),
                )
                card.set_channel_power(name, order, count, delay)
            self._sync_cell(supply_id, card.data)

    def set_driver_available(self, available: bool) -> None:
        """驱放测试模式开关：Driver 档是否可选；不发配置变更信号。"""
        available = bool(available)
        if available == self._driver_available:
            return
        self._driver_available = available
        for supply_id, card in self._cards.items():
            card.set_driver_available(available)
            self._sync_cell(supply_id, card.data)

    def reset_channel_modes(self) -> None:
        """把所有卡片的通道角色回到 OFF；不发配置变更信号。"""
        for supply_id, card in self._cards.items():
            card.reset_channel_modes()
            self._sync_cell(supply_id, card.data)

    def reset_channel_switches(self) -> None:
        """兼容旧名字：把所有卡片的通道角色回到 OFF。"""
        self.reset_channel_modes()

    def _sync_cell(self, supply_id: str, data: PowerSupplyCardData) -> None:
        for index, cell in enumerate(self._cells):
            if cell.supply_id == supply_id:
                self._cells[index] = replace(cell, data=data)
                return

    def _on_card_mode(self, supply_id: str, channel: str, mode: str) -> None:
        card = self._cards.get(supply_id)
        if card is not None:
            self._sync_cell(supply_id, card.data)
        self.channel_mode_changed.emit(supply_id, channel, mode)

    def _on_card_protection(
        self, supply_id: str, channel: str, kind: str, enabled: bool
    ) -> None:
        card = self._cards.get(supply_id)
        if card is not None:
            self._sync_cell(supply_id, card.data)
        self.protection_toggled.emit(supply_id, channel, kind, bool(enabled))

    def _on_card_value(
        self, supply_id: str, channel: str, kind: str, value: float
    ) -> None:
        card = self._cards.get(supply_id)
        if card is not None:
            self._sync_cell(supply_id, card.data)
        self.value_changed.emit(supply_id, channel, kind, float(value))

    def _on_card_order(
        self, supply_id: str, channel: str, order: int
    ) -> None:
        card = self._cards.get(supply_id)
        if card is not None:
            self._sync_cell(supply_id, card.data)
        self.channel_order_changed.emit(supply_id, channel, int(order))

    def _on_card_delay(
        self, supply_id: str, channel: str, delay: float
    ) -> None:
        card = self._cards.get(supply_id)
        if card is not None:
            self._sync_cell(supply_id, card.data)
        self.channel_delay_changed.emit(supply_id, channel, float(delay))

    def is_empty(self) -> bool:
        return not self._cells

    def card_rect(self, supply_id: str) -> QRectF | None:
        """卡片在墙坐标里的矩形；没有这张卡片时返回 ``None``。"""
        return self._rects.get(supply_id)

    def card_widget_rect(self, supply_id: str) -> QRectF | None:
        """卡片控件的实际整数几何；展开动画用它作为终点，避免亚像素错位。"""
        card = self._cards.get(supply_id)
        if card is None:
            return None
        return QRectF(card.geometry())

    def card_rects(self) -> dict[str, QRectF]:
        return dict(self._rects)

    def paint_enabled(self) -> bool:
        return self._paint_enabled

    def set_paint_enabled(self, enabled: bool) -> None:
        """动画覆盖层接管卡面时暂停显示卡片；布局占位保持不变。"""
        enabled = bool(enabled)
        if enabled == self._paint_enabled:
            return
        self._paint_enabled = enabled
        for card in self._cards.values():
            card.setVisible(enabled)
        self.update()

    # ---- 布局 ---------------------------------------------------------------

    def _bounds(self) -> tuple[int, int, int, int]:
        """被占用格位的行列外接矩形：``(min_row, max_row, min_col, max_col)``。"""
        rows = [cell.slot // _SLOT_COLUMNS for cell in self._cells]
        columns = [cell.slot % _SLOT_COLUMNS for cell in self._cells]
        return min(rows), max(rows), min(columns), max(columns)

    def _layout_for_width(self, width: float) -> tuple[dict[str, QRectF], float]:
        """按可用宽度算出每张卡片的位置与整墙高度。"""
        if not self._cells:
            return {}, 0.0
        min_row, max_row, min_col, max_col = self._bounds()
        column_count = max_col - min_col + 1
        row_count = max_row - min_row + 1
        available = max(1.0, float(width) - 2.0 * _SIDE_MARGIN)
        gap_total = (column_count - 1) * _CARD_GAP
        available_for_cards = max(1.0, available - gap_total)
        natural = column_count * POWER_CARD_WIDTH
        scale = min(1.0, available_for_cards / natural) if natural > 0.0 else 1.0
        card_width = POWER_CARD_WIDTH * scale
        card_height = POWER_CARD_HEIGHT * scale
        total_width = (
            column_count * card_width + (column_count - 1) * _CARD_GAP
        )
        content_height = row_count * card_height + (row_count - 1) * _CARD_GAP
        total_height = content_height + 2.0 * _SHADOW_PAD
        left = (float(width) - total_width) / 2.0
        rects: dict[str, QRectF] = {}
        for cell in self._cells:
            row = cell.slot // _SLOT_COLUMNS - min_row
            column = cell.slot % _SLOT_COLUMNS - min_col
            rects[cell.supply_id] = QRectF(
                left + column * (card_width + _CARD_GAP),
                _SHADOW_PAD + row * (card_height + _CARD_GAP),
                card_width,
                card_height,
            )
        return rects, total_height

    def refresh_layout(self) -> None:
        """按当前宽度重排卡片；宽度未知时以卡片自然宽度排版。"""
        width = float(self.width())
        if width <= 0.0:
            width = float(self.parentWidget().width()) if self.parentWidget() else 0.0
        if width <= 0.0:
            width = _SIDE_MARGIN * 2.0 + POWER_CARD_WIDTH
        self._rects, height = self._layout_for_width(width)
        pixels = int(round(height))
        if pixels != self.minimumHeight() or pixels != self.maximumHeight():
            self.setMinimumHeight(pixels)
            self.setMaximumHeight(pixels)
        for supply_id, rect in self._rects.items():
            card = self._cards.get(supply_id)
            if card is None or rect.isEmpty():
                continue
            card.set_scale(rect.width() / POWER_CARD_WIDTH)
            card.setGeometry(rect.toRect())
            card.setVisible(self._paint_enabled)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        width = max(1, self.width())
        _rects, height = self._layout_for_width(float(width))
        return QSize(width, int(round(height)))

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self.minimumHeight())

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        _rects, height = self._layout_for_width(float(width))
        return int(round(height))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.refresh_layout()

    # ---- 绘制 ---------------------------------------------------------------

    def card_pixmap(self, supply_id: str) -> QPixmap:
        """某台电源的通道卡面快照；没有这张卡片时返回空位图。

        展开动画从当前真实控件取图，因此动画最后一帧与页面上的卡片（包括输入框、
        开关和勾选状态）逐像素一致。
        """
        card = self._cards.get(supply_id)
        if card is None:
            return QPixmap()
        return card.grab()

    def paintEvent(self, event) -> None:
        """先给每张卡片画一层柔和投影，卡片控件随后盖在上面。

        卡片是真实子控件，投影不能由卡片自己画（会被卡片矩形裁掉），也不能用
        ``QGraphicsDropShadowEffect``（每张卡一份离屏渲染，缩放和重排时很贵）。这里
        用三层圆角矩形由内到外叠出投影，颜色与页面阴影同族，代价只有几次填充。
        """
        if not self._paint_enabled or not self._rects:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(Qt.NoPen)
            for rect in self._rects.values():
                self._paint_card_shadow(painter, rect)
        finally:
            painter.end()

    def _paint_card_shadow(self, painter: QPainter, rect: QRectF) -> None:
        radius = POWER_CARD_RADIUS * (rect.width() / POWER_CARD_WIDTH)
        shadow = QColor(15, 23, 42)
        for grow, drop, alpha in (
            (1.5, 1.0, 0.05),
            (3.0, 2.0, 0.04),
            (4.5, 3.0, 0.03),
        ):
            shadow.setAlphaF(alpha)
            spread = rect.adjusted(-grow, -grow + drop, grow, grow + drop)
            path = QPainterPath()
            path.addRoundedRect(spread, radius + grow, radius + grow)
            painter.fillPath(path, shadow)
