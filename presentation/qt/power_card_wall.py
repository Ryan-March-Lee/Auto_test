"""电源配置页的卡片视图（``PowerCardWall``）。

电源配置页以通道卡片为主视图：每台电源一张 440×232 的通道卡片（左 CH1 浅蓝、
右 CH2 浅琥珀），卡片按仪器排布页九宫格里的相对位置摆放在页面中央。上电计划与
漏极表单接在卡片下方；通道参数本身只在这一张卡上编辑。

**真实控件**：卡片墙直接承载 :class:`presentation.qt.power_card.PowerSupplyCard`
子控件（不是位图 + 坐标命中）。通道开关、VS/IS 输入框与保护勾选框都由 Qt 处理
鼠标、键盘与焦点，卡片墙只负责按格位摆位、按可用宽度等比缩放并转发信号。

**相对位置**：仪器排布页把电源拖进 3×3 格子，格位下标从左上开始、先行后列。
本控件保留这些格位的行列关系：卡片只按被占用格子的外接矩形排版，空行/空列被
收掉，因此“左上角那台电源”在卡片视图里仍然在左上角。卡片数量多到一屏放不下时，
整块按可用宽度等比缩小，相对位置不变；卡片缩放时子控件几何与字号一起调整。

**编辑状态**：通道开关只表示本次停留本页的编辑启用状态，不写配置。页面重建卡片
（应用绑定、回填配置）时，``preserve_edit_state=True`` 会保留同一台电源当前的
开关、数值与保护状态；``False`` 则完全按传入数据重建。控件本身不读写配置文档。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from PySide6.QtCore import QRectF, QSize, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from .power_card import (
    CHANNEL_NAMES,
    POWER_CARD_HEIGHT,
    POWER_CARD_WIDTH,
    PROTECTION_KINDS,
    VALUE_KINDS,
    PowerChannelData,
    PowerSupplyCard,
    PowerSupplyCardData,
)

__all__ = ["PowerCardCell", "PowerCardWall", "power_card_data"]

#: 卡片之间的间距（逻辑像素）。卡片本身缩小时间距不缩，保持卡片轮廓清晰。
_CARD_GAP = 18.0

#: 卡片墙左右留白：不贴住内容列边缘。
_SIDE_MARGIN = 16.0

#: 九宫格列数，与仪器排布页一致。
_SLOT_COLUMNS = 3


@dataclass(frozen=True)
class PowerCardCell:
    """一张通道卡片在墙上的位置：电源键、数据与格位下标。"""

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
    return PowerChannelData(
        channel=name,
        # 两个通道始终保留；配置里缺少这条通道时按 0 显示，交给页面决定编辑状态。
        enabled=True,
        voltage_set=_number(voltage.get("value")),
        current_set=_number(current.get("value")),
        # 卡面只保留“是否启用保护”；阈值仍由配置读取按 110% 推导，不在这里显示。
        voltage_protection_enabled=voltage.get("protection_enabled") is True,
        current_protection_enabled=current.get("protection_enabled") is True,
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
        merged = merged.with_channel_enabled(name, old.enabled)
        for kind, value in (
            ("voltage", old.voltage_set),
            ("current", old.current_set),
        ):
            merged = merged.with_channel_value(name, kind, value)
        for kind in PROTECTION_KINDS:
            merged = merged.with_channel_protection(
                name, kind, old.protection_enabled(kind)
            )
    return merged


class PowerCardWall(QWidget):
    """按九宫格相对位置摆放交互式电源卡片的画布。

    ``set_cards`` 接收 ``(supply_id, data)`` 与可选的 ``slots`` 映射；没有格位信息
    的电源按传入顺序补进空位。卡片是真实子控件，缩放由 :meth:`PowerSupplyCard.set_scale`
    完成，因此三列放不下时点击位置、输入焦点与字号都跟着缩放。

    卡片上的通道开关、工作值与保护变化通过信号转给页面；本控件只同步自己的卡片
    快照，不写配置。
    """

    #: ``(supply_id, channel, enabled)``：本次编辑的通道启用状态。
    channel_toggled = Signal(str, str, bool)
    #: ``(supply_id, channel, kind, enabled)``；``kind`` 是 ``voltage`` / ``current``。
    protection_toggled = Signal(str, str, str, bool)
    #: ``(supply_id, channel, kind, value)``：VS / IS 输入。
    value_changed = Signal(str, str, str, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("powerCardWall")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._cells: list[PowerCardCell] = []
        self._rects: dict[str, QRectF] = {}
        self._cards: dict[str, PowerSupplyCard] = {}
        self._paint_enabled = True

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

        for cell in cells:
            card = self._cards.get(cell.supply_id)
            if card is None:
                card = PowerSupplyCard(cell.data, self)
                card.channel_toggled.connect(
                    lambda channel, enabled, supply=cell.supply_id: (
                        self._on_card_channel(supply, channel, enabled)
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
                self._cards[cell.supply_id] = card
            else:
                card.set_data(cell.data)
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

    def reset_channel_switches(self) -> None:
        """把所有卡片的通道开关回到未启用；不发配置变更信号。"""
        for supply_id, card in self._cards.items():
            card.reset_channel_switches()
            self._sync_cell(supply_id, card.data)

    def _sync_cell(self, supply_id: str, data: PowerSupplyCardData) -> None:
        for index, cell in enumerate(self._cells):
            if cell.supply_id == supply_id:
                self._cells[index] = replace(cell, data=data)
                return

    def _on_card_channel(self, supply_id: str, channel: str, enabled: bool) -> None:
        card = self._cards.get(supply_id)
        if card is not None:
            self._sync_cell(supply_id, card.data)
        self.channel_toggled.emit(supply_id, channel, bool(enabled))

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
        total_height = row_count * card_height + (row_count - 1) * _CARD_GAP
        left = (float(width) - total_width) / 2.0
        rects: dict[str, QRectF] = {}
        for cell in self._cells:
            row = cell.slot // _SLOT_COLUMNS - min_row
            column = cell.slot % _SLOT_COLUMNS - min_col
            rects[cell.supply_id] = QRectF(
                left + column * (card_width + _CARD_GAP),
                row * (card_height + _CARD_GAP),
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
