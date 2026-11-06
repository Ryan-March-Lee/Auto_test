"""电源配置页的卡片视图（``PowerCardWall``）。

电源配置页以通道卡片为主视图：每台电源一张 440×232 的通道卡片（左 CH1 浅蓝、
右 CH2 浅琥珀），卡片按仪器排布页九宫格里的相对位置摆放在页面中央，参数表单
接在卡片下方。这里只负责卡片的排版与绘制，卡片数据来自页面读取的配置片段。

**相对位置**：仪器排布页把电源拖进 3×3 格子，格位下标从左上开始、先行后列。
本控件保留这些格位的行列关系：卡片只按被占用格子的外接矩形排版，空行/空列被
收掉，因此“左上角那台电源”在卡片视图里仍然在左上角。卡片数量多到一屏放不下时，
整块按可用宽度等比缩小，相对位置不变。

**绘制与交互**：卡片没有子控件，全部由
:func:`presentation.qt.power_card.render_power_card` 出图后缩放到格子里；这样它和
导出预览、交互卡片共用同一份卡面。两个保护勾选框没有真实子控件，点击由本控件把
鼠标位置换算回卡面逻辑坐标后命中，再发出 ``protection_toggled``。控件本身必须创建
在 GUI 线程、``QApplication`` 之后。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from .power_card import (
    CHANNEL_NAMES,
    POWER_CARD_HEIGHT,
    POWER_CARD_WIDTH,
    PROTECTION_KINDS,
    PowerChannelData,
    PowerSupplyCardData,
    power_card_layout,
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
    return PowerChannelData(
        channel=name,
        # 配置里只有实际写出的通道才参与供电，另一路在卡面上保持关闭态。
        enabled=name in channels,
        voltage_set=_number(voltage.get("value")),
        current_set=_number(current.get("value")),
        # 卡面只保留“是否启用保护”；阈值仍由表单按 110% 推导，不在这里显示。
        voltage_protection_enabled=voltage.get("protection_enabled") is True,
        current_protection_enabled=current.get("protection_enabled") is True,
    )


def power_card_data(entry: Mapping[str, Any], supply_id: str) -> PowerSupplyCardData:
    """把一条电源配置读成通道卡片数据；缺字段按关闭态与 0 显示。

    卡面只读工作值与两个保护开关，不再显示保护阈值；阈值仍由表单按 110% 推导，
    因此这里只判断 ``protection_enabled`` 是否为 ``True``。
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

    卡面仍是位图，只有两个保护勾选框可点：按下时按卡面布局把鼠标位置换算回逻辑
    坐标，命中后发出 :attr:`protection_toggled`，由页面决定是否写回表单。卡片被
    等比缩小后命中区跟着缩放，因此三列放不下时点击位置仍然准确。
    """

    #: ``(supply_id, channel, kind, enabled)``；``kind`` 是 ``voltage`` / ``current``。
    protection_toggled = Signal(str, str, str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("powerCardWall")
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._cells: list[PowerCardCell] = []
        self._rects: dict[str, QRectF] = {}
        self._pixmaps: dict[tuple[str, PowerSupplyCardData], QPixmap] = {}
        self._paint_enabled = True
        self._card_layout = power_card_layout()

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

    def set_protection(
        self,
        supply_id: str,
        channel: str,
        kind: str,
        enabled: bool,
    ) -> bool:
        """只改某张卡某个通道的保护开关；没有这张卡或状态未变时返回 ``False``。

        页面在用户点击卡面勾选框（或下方表单勾选框）时调用，避免整墙重读配置。
        卡片的保护阈值不在卡面上，因此只需失效这一张卡的位图缓存。
        """
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        for index, cell in enumerate(self._cells):
            if cell.supply_id != supply_id:
                continue
            current = cell.data.channel(channel).protection_enabled(kind)
            if current == enabled:
                return False
            self._pixmaps.pop((supply_id, cell.data), None)
            self._cells[index] = replace(
                cell,
                data=cell.data.with_channel_protection(channel, kind, enabled),
            )
            self.update()
            return True
        return False

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

    # ---- 交互 ---------------------------------------------------------------

    def _logical_point(self, target: QRectF, position: QPointF) -> QPointF | None:
        """把墙坐标换算成 440×232 卡面坐标；位置不在这张卡里时返回 ``None``。"""
        if target.isEmpty() or not target.contains(position):
            return None
        scale_x = target.width() / POWER_CARD_WIDTH
        scale_y = target.height() / POWER_CARD_HEIGHT
        if scale_x <= 0.0 or scale_y <= 0.0:
            return None
        return QPointF(
            (position.x() - target.x()) / scale_x,
            (position.y() - target.y()) / scale_y,
        )

    def _hit_protection(
        self, position: QPointF
    ) -> tuple[PowerCardCell, str, str] | None:
        """命中哪张卡、哪个通道、哪种保护的勾选框；没命中时返回 ``None``。

        关闭的通道不接收点击：卡面上它的勾选框整组是灰的，点击不应该悄悄改配置。
        """
        for cell in self._cells:
            target = self._rects.get(cell.supply_id)
            if target is None:
                continue
            logical = self._logical_point(target, position)
            if logical is None:
                continue
            for channel_layout in self._card_layout.channels:
                channel = cell.data.channel(channel_layout.channel)
                if not channel.enabled:
                    continue
                for kind, check_rect in zip(
                    PROTECTION_KINDS, channel_layout.protection_checks
                ):
                    if check_rect.contains(logical):
                        return cell, channel_layout.channel, kind
            return None
        return None

    def mouseMoveEvent(self, event) -> None:
        hit = self._hit_protection(event.position())
        if hit is None:
            self.setCursor(Qt.ArrowCursor)
            self.setToolTip("")
        else:
            cell, channel, kind = hit
            label = "电压保护" if kind == "voltage" else "电流保护"
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip(
                f"{cell.supply_id} {channel} {label}："
                "勾选后保护阈值自动取比设定值高 10% 的值。"
            )
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self.setCursor(Qt.ArrowCursor)
        self.setToolTip("")
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            hit = self._hit_protection(event.position())
            if hit is not None:
                cell, channel, kind = hit
                enabled = not cell.data.channel(channel).protection_enabled(kind)
                self.protection_toggled.emit(
                    cell.supply_id, channel, kind, enabled
                )
                event.accept()
                return
        super().mousePressEvent(event)

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
