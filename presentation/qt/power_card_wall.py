"""电源配置页的卡片视图（``PowerCardWall``）。

电源配置页以通道卡片为主视图：每台电源一张 440×232 的通道卡片（左 CH1 浅蓝、
右 CH2 浅琥珀），卡片按仪器排布页九宫格里的相对位置摆放在页面中央，参数表单
接在卡片下方。这里只负责卡片的排版与绘制，卡片数据来自页面读取的配置片段。

**相对位置**：仪器排布页把电源拖进 3×3 格子，格位下标从左上开始、先行后列。
本控件保留这些格位的行列关系：卡片只按被占用格子的外接矩形排版，空行/空列被
收掉，因此“左上角那台电源”在卡片视图里仍然在左上角。卡片数量多到一屏放不下时，
整块按可用宽度等比缩小，相对位置不变。

**绘制**：卡片没有子控件，全部由 :func:`presentation.qt.power_card.render_power_card`
出图后缩放到格子里；这样它和导出预览、以后的交互卡片共用同一份卡面。控件本身
必须创建在 GUI 线程、``QApplication`` 之后。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from .config_form_state import protection_threshold
from .power_card import (
    CHANNEL_NAMES,
    POWER_CARD_HEIGHT,
    POWER_CARD_WIDTH,
    PowerChannelData,
    PowerSupplyCardData,
    render_power_card,
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
    voltage_set = _number(voltage.get("value"))
    current_set = _number(current.get("value"))
    return PowerChannelData(
        channel=name,
        # 配置里只有实际写出的通道才参与供电，另一路在卡面上保持关闭态。
        enabled=name in channels,
        # 保护阈值由工作值按 110% 推导，与电源配置页保存的值保持一致。
        voltage_set=voltage_set,
        voltage_protection=protection_threshold(voltage_set),
        current_set=current_set,
        current_protection=protection_threshold(current_set),
    )


def power_card_data(entry: Mapping[str, Any], supply_id: str) -> PowerSupplyCardData:
    """把一条电源配置读成通道卡片数据；缺字段按关闭态与 0 显示。

    保护阈值不再读取配置里的旧值，而是按工作值 × 110% 推导，保证卡片与页面保存
    出的配置一致。
    """
    label = entry.get("label")
    code = label.strip() if isinstance(label, str) and label.strip() else supply_id
    return PowerSupplyCardData(
        code=code,
        channels=tuple(_channel_data(name, entry) for name in CHANNEL_NAMES),
    )


class PowerCardWall(QWidget):
    """按九宫格相对位置摆放通道卡片的画布。

    ``set_cards`` 接收 ``(supply_id, data)`` 与可选的 ``slots`` 映射；没有格位信息
    的电源按传入顺序补进空位。控件没有子控件，卡片由 :meth:`paintEvent` 直接从
    位图绘制，因此在任何缩放比例下都不会出现控件自身背景透出来的缝。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("powerCardWall")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._cells: list[PowerCardCell] = []
        self._rects: dict[str, QRectF] = {}
        self._pixmaps: dict[tuple[str, PowerSupplyCardData], QPixmap] = {}
        self._paint_enabled = True

    # ---- 内容 ---------------------------------------------------------------

    def set_cards(
        self,
        cards: Iterable[tuple[str, PowerSupplyCardData]],
        slots: Mapping[str, int] | None = None,
    ) -> None:
        """替换整墙卡片；``slots`` 缺省时按九宫格顺序补齐。"""
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
        self._cells = [
            PowerCardCell(
                supply_id=supply_id,
                data=data,
                slot=int(slot_of[supply_id]) if slot_of[supply_id] is not None else 0,
            )
            for supply_id, data in entries
        ]
        self._pixmaps = {
            key: value
            for key, value in self._pixmaps.items()
            if key[0] in {cell.supply_id for cell in self._cells}
        }
        self.refresh_layout()

    def cards(self) -> tuple[PowerCardCell, ...]:
        return tuple(self._cells)

    def is_empty(self) -> bool:
        return not self._cells

    def card_rect(self, supply_id: str) -> QRectF | None:
        """卡片在墙坐标里的矩形；没有这张卡片时返回 ``None``。"""
        return self._rects.get(supply_id)

    def card_rects(self) -> dict[str, QRectF]:
        return dict(self._rects)

    def paint_enabled(self) -> bool:
        return self._paint_enabled

    def set_paint_enabled(self, enabled: bool) -> None:
        """动画覆盖层接管卡面时暂停自绘；布局占位保持不变。"""
        enabled = bool(enabled)
        if enabled == self._paint_enabled:
            return
        self._paint_enabled = enabled
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

    def _pixmap(self, cell: PowerCardCell) -> QPixmap:
        key = (cell.supply_id, cell.data)
        pixmap = self._pixmaps.get(key)
        if pixmap is None:
            pixmap = render_power_card(
                cell.data,
                device_pixel_ratio=float(self.devicePixelRatioF()) or 1.0,
            )
            self._pixmaps[key] = pixmap
        return pixmap

    def card_pixmap(self, supply_id: str) -> QPixmap:
        """某台电源的通道卡面；没有这张卡片时返回空位图。"""
        for cell in self._cells:
            if cell.supply_id == supply_id:
                return self._pixmap(cell)
        return QPixmap()

    @staticmethod
    def _draw_pixmap(painter: QPainter, pixmap: QPixmap, target: QRectF) -> None:
        ratio = float(pixmap.devicePixelRatio()) or 1.0
        width = pixmap.width() / ratio
        height = pixmap.height() / ratio
        if width <= 0.0 or height <= 0.0 or target.isEmpty():
            return
        painter.save()
        painter.translate(target.topLeft())
        painter.scale(target.width() / width, target.height() / height)
        painter.drawPixmap(QPointF(0.0, 0.0), pixmap)
        painter.restore()

    def paintEvent(self, event) -> None:
        if not self._paint_enabled or not self._cells:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            for cell in self._cells:
                rect = self._rects.get(cell.supply_id)
                if rect is None or rect.isEmpty():
                    continue
                self._draw_pixmap(painter, self._pixmap(cell), rect)
        finally:
            painter.end()
