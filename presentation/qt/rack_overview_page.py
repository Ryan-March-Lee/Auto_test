"""仪器排布页（``RackOverviewPage``）：把本次勾选的仪器排成现场测试链路。

流程位置：仪表配置页点“下一步”后不再直接进入参数页，而是先到这里。页面把本次勾选的
仪器卡片从它们在结果区里的屏幕位置连续变形到最终布局：

- 电源卡片上边保持不动、下边向上收拢，最后只剩一行缩写设备码；
- 信号源与频谱仪卡片滑到页面左右下角，保持设备发现页的卡片样式；
- 中间的线缆、箭头、九宫格与功放示意作为一个整体从页面下方滑入。

动画约 700 ms，点击示意图可立即结束。九宫格是现场电源架的 3×3 摆位：把每台
电源拖到与实际一致的格子里，全部放好后“下一步”才可用。格位只属于本页，不写入配置、
也不改变“电源配置”页的标签页顺序——它只帮助操作者在进入电源参数前把软件画面与现场
对上。拖到已占用的格子会交换两台电源；拖回顶部托盘会把电源从格子里取出。

页面只依赖表现层：卡片正面由
:func:`presentation.qt.card_assets.render_card_front` 按同一份
``InstrumentCardData`` 渲染，本页不截取控件，也不读写配置或连接仪器。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Sequence

from PySide6.QtCore import (
    QEasingCurve,
    QMimeData,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDrag,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from domain.instruments.models import POWER_SUPPLY_ROLE

from .card_assets import render_card_front
from .device_cards import CARD_HEIGHT, CARD_WIDTH, InstrumentCardData
from .theme import (
    CARD_RADIUS,
    PAGE_CONTENT_MAX_WIDTH,
    TOKENS,
    card_code_font,
    set_button_variant,
)

__all__ = [
    "RackCardEntry",
    "RackOverviewPage",
]

#: 拖放电源卡片使用的私有 MIME 类型：只在本页内部使用，不写剪贴板、不跨进程。
RACK_SUPPLY_MIME = "application/x-auto-test-rack-supply"

# ---- 设计坐标：整张示意图按固定画布排版，再等比缩放到页面可用区域 ---------------
# 画布尺寸与效果图一致（约 1.7:1）。所有绘制都用设计坐标，缩放与居中只在画布外做，
# 因此文字、圆角与线宽的比例不会随窗口变化而走样。
_DESIGN_WIDTH = 1120.0
_DESIGN_HEIGHT = 664.0

#: 顶部电源托盘：三张窄卡；三台电源时与九宫格的三列同宽同列，向下拖正好落到同一列。
_CHIP_WIDTH = 170.0
_CHIP_HEIGHT = 80.0
_CHIP_GAP = 22.0
_TRAY_TOP = 4.0

#: 九宫格：现场电源架的 3×3 摆位，每格最多一台电源。
_SLOT_COLUMNS = 3
_SLOT_ROWS = 3
_SLOT_COUNT = _SLOT_COLUMNS * _SLOT_ROWS
_SLOT_HEIGHT = 68.0
_SLOT_H_GAP = _CHIP_GAP
_SLOT_V_GAP = 12.0
_GRID_TOP = _TRAY_TOP + _CHIP_HEIGHT + 26.0
_GRID_WIDTH = _SLOT_COLUMNS * _CHIP_WIDTH + (_SLOT_COLUMNS - 1) * _SLOT_H_GAP
_GRID_LEFT = (_DESIGN_WIDTH - _GRID_WIDTH) / 2.0

#: 向下箭头：从九宫格指向 DUT，表示架子给功放供电。
_ARROW_WIDTH = 34.0
_ARROW_HEIGHT = 40.0
_ARROW_TOP = _GRID_TOP + _SLOT_ROWS * _SLOT_HEIGHT + (
    _SLOT_ROWS - 1
) * _SLOT_V_GAP + 16.0

#: 左右两张仪器卡片：保持设备发现页的卡片尺寸（190×252）。
_CARD_MARGIN = 24.0
_CARD_TOP = _DESIGN_HEIGHT - 8.0 - CARD_HEIGHT
_CHAIN_Y = _CARD_TOP + CARD_HEIGHT / 2.0

#: 两个功放三角：Driver 在左、DUT 在右，成对居中；中间留一段短电缆。
_AMP_WIDTH = 116.0
_AMP_HEIGHT = 86.0
_AMP_GAP = 20.0

_CENTER_X = _DESIGN_WIDTH / 2.0
_CHAIN_LEFT = _CARD_MARGIN + CARD_WIDTH
_CHAIN_RIGHT = _DESIGN_WIDTH - _CARD_MARGIN - CARD_WIDTH
_AMP_PAIR_LEFT = _CENTER_X - (_AMP_WIDTH * 2.0 + _AMP_GAP) / 2.0
_DRIVER_RECT = QRectF(
    _AMP_PAIR_LEFT, _CHAIN_Y - _AMP_HEIGHT / 2.0, _AMP_WIDTH, _AMP_HEIGHT
)
_DUT_RECT = QRectF(
    _AMP_PAIR_LEFT + _AMP_WIDTH + _AMP_GAP,
    _CHAIN_Y - _AMP_HEIGHT / 2.0,
    _AMP_WIDTH,
    _AMP_HEIGHT,
)

#: 卡片整体上滑的起点：至少让九宫格顶边落到画布下方。
_GROUP_SLIDE = _DESIGN_HEIGHT - _GRID_TOP + 24.0

#: 动画时长：与效果图一致的轻快节奏；点击可立即结束。
_TRANSITION_MS = 700

# ---- 配色与字体：全部来自统一主题令牌 -----------------------------------------
_CANVAS = QColor(TOKENS["canvas"])
_SURFACE = QColor(TOKENS["surface"])
_INK = QColor(TOKENS["text"])
_PRIMARY = QColor(TOKENS["primary"])
_PRIMARY_SOFT = QColor(TOKENS["primary_soft"])

#: 九宫格空位的虚线：与效果图一致的深色细虚线。
_SLOT_PEN = QPen(_INK, 2.0, Qt.DashLine, Qt.RoundCap, Qt.RoundJoin)
_SLOT_PEN.setDashPattern([5.0, 4.0])


def _slot_font() -> QFont:
    """格子里的设备码：比卡片略小，保证九宫格三列在画布内排得下。"""
    font = QFont(card_code_font())
    font.setPointSizeF(14.0)
    return font


def _elided(font: QFont, text: str, width: float) -> str:
    """按可用宽度裁掉过长的设备码；单行显示，不改变格子高度。"""
    if not text:
        return ""
    metrics = QFontMetricsF(font)
    return metrics.elidedText(text, Qt.ElideRight, max(1.0, float(width)))


def _rounded(rect: QRectF, radius: float = CARD_RADIUS) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _lerp(start: float, end: float, t: float) -> float:
    return start + (end - start) * t


def _lerp_rect(start: QRectF, end: QRectF, t: float) -> QRectF:
    return QRectF(
        _lerp(start.x(), end.x(), t),
        _lerp(start.y(), end.y(), t),
        _lerp(start.width(), end.width(), t),
        _lerp(start.height(), end.height(), t),
    )


def _draw_pixmap(painter: QPainter, pixmap: QPixmap, target: QRectF) -> None:
    """把整张位图按逻辑尺寸缩放进 ``target``；保留位图自己的屏幕像素比。"""
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


@dataclass(frozen=True)
class RackCardEntry:
    """一张要进入本页的仪器卡片：角色、缩写设备码、正面位图与动画起点。

    ``start_rect_global`` 是点击“下一步”时该卡片在屏幕上的矩形（全局坐标）。页面在
    第一次绘制时把它换算到画布坐标，因此动画起点与结果区里看到的位置一致，不需要
    截取控件、也不依赖上一页的布局对象。
    """

    role_key: str
    code: str
    front: QPixmap
    start_rect_global: QRect


def _cable_source() -> tuple[QPixmap, QRectF] | None:
    """读取电缆照片并按内容包围盒裁掉透明留白；读不到时返回 ``None``（改画直线）。"""
    path = Path(__file__).resolve().parents[2] / "assets" / "cable.png"
    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return None
    image = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
    width, height = image.width(), image.height()
    stride = image.bytesPerLine()
    buffer = bytes(image.constBits())
    columns: list[int] = []
    rows: list[int] = []
    for y in range(height):
        row = buffer[y * stride: (y + 1) * stride]
        for x in range(width):
            if row[x * 4 + 3] > 10:
                columns.append(x)
                rows.append(y)
    if not columns or not rows:
        return pixmap, QRectF(0, 0, pixmap.width(), pixmap.height())
    left, right = min(columns), max(columns) + 1
    top, bottom = min(rows), max(rows) + 1
    return pixmap, QRectF(left, top, right - left, bottom - top)


_CABLE_ASSET: tuple[QPixmap, QRectF] | None = None


def _cable_asset() -> tuple[QPixmap, QRectF] | None:
    global _CABLE_ASSET
    if _CABLE_ASSET is None:
        _CABLE_ASSET = _cable_source()
    return _CABLE_ASSET


class _RackBoard(QWidget):
    """整张示意图的画布：变形动画、九宫格拖放与静态布局都在这里。

    画布按 :data:`_DESIGN_WIDTH` × :data:`_DESIGN_HEIGHT` 设计坐标排版，再等比缩放
    居中到控件内。鼠标、拖放坐标都先换算回设计坐标，因此缩放不影响命中区。
    """

    state_changed = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setObjectName("rackOverviewBoard")
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self._entries: list[RackCardEntry] = []
        self._power_ids: list[str] = []
        self._signal: RackCardEntry | None = None
        self._spectrum: RackCardEntry | None = None
        self._slot_of: dict[str, int] = {}
        self._progress = 1.0
        self._running = False
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(_TRANSITION_MS)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)
        self._animation.valueChanged.connect(self._on_animation_value)
        self._animation.finished.connect(self._on_animation_finished)
        self._press: tuple[str, QPoint] | None = None
        self._hover_target: int | None = None
        self._cable = _cable_asset()

    # ---- 进入与动画 -------------------------------------------------------

    def enter(self, entries: Iterable[RackCardEntry]) -> None:
        """接收本次勾选的卡片并播放一次入场变形；每次进入都从托盘重新摆起。"""
        self._animation.stop()
        self._entries = list(entries)
        self._power_ids = []
        self._signal = None
        self._spectrum = None
        for entry in self._entries:
            if entry.role_key == POWER_SUPPLY_ROLE:
                if entry.code and entry.code not in self._power_ids:
                    self._power_ids.append(entry.code)
            elif entry.role_key == "signal_generator" and self._signal is None:
                self._signal = entry
            elif entry.role_key == "spectrum_analyzer" and self._spectrum is None:
                self._spectrum = entry
        self._slot_of.clear()
        self._press = None
        self._hover_target = None
        self._progress = 0.0
        self._running = bool(self._entries)
        if self._running:
            self._animation.start()
        else:
            self._progress = 1.0
        self._notify_state()
        self.update()

    def finish_transition(self) -> None:
        """立即结束入场动画，直接呈现最终布局（点击跳过与测试都用这个入口）。"""
        if self._animation.state() == QVariantAnimation.Running:
            self._animation.stop()
        self._set_progress(1.0)

    def transition_running(self) -> bool:
        return self._running

    def _on_animation_value(self, value: Any) -> None:
        self._set_progress(float(value))

    def _on_animation_finished(self) -> None:
        self._set_progress(1.0)

    def _set_progress(self, value: float) -> None:
        progress = max(0.0, min(1.0, float(value)))
        running = progress < 1.0 and bool(self._entries)
        changed = running != self._running or progress != self._progress
        self._progress = progress
        self._running = running
        if changed:
            self._notify_state()
            self.update()

    def _notify_state(self) -> None:
        self.state_changed.emit()

    # ---- 摆位状态 ---------------------------------------------------------

    def power_supply_ids(self) -> tuple[str, ...]:
        return tuple(self._power_ids)

    def slot_of(self, supply_id: str) -> int | None:
        return self._slot_of.get(supply_id)

    def slot_assignments(self) -> dict[str, int]:
        """``supply_id -> 格位下标`` 的只读副本（只在本页内有效）。"""
        return dict(self._slot_of)

    def placed_count(self) -> int:
        return len(self._slot_of)

    def all_supplies_placed(self) -> bool:
        return len(self._slot_of) == len(self._power_ids)

    def place_supply(self, supply_id: str, slot_index: int) -> None:
        """把一台电源放进指定格子；目标已有电源时两台交换。

        电源原本在托盘里、目标格已占用时，被占用的一台退回托盘；原本在别的格子里时，
        两台互换。电格位下标从左上开始、先行后列，与 :data:`_SLOT_COUNT` 一致。
        """
        if supply_id not in self._power_ids:
            raise ValueError(f"不是本页的电源：{supply_id!r}")
        index = int(slot_index)
        if not 0 <= index < _SLOT_COUNT:
            raise ValueError(f"格位下标必须在 0..{_SLOT_COUNT - 1}：{slot_index!r}")
        previous = self._slot_of.get(supply_id)
        if previous == index:
            return
        occupant = self._occupant(index)
        if occupant is not None:
            if previous is None:
                self._slot_of.pop(occupant, None)
            else:
                self._slot_of[occupant] = previous
        self._slot_of[supply_id] = index
        self._notify_state()
        self.update()

    def unplace_supply(self, supply_id: str) -> None:
        """把电源从格子里取回顶部托盘；不在格子里时什么也不做。"""
        if self._slot_of.pop(supply_id, None) is not None:
            self._notify_state()
            self.update()

    def drop_supply(self, supply_id: str, slot_index: int | None) -> bool:
        """拖放落点：``0..8`` 放进格子，``-1`` 退回托盘，``None`` 取消。"""
        if supply_id not in self._power_ids:
            return False
        if slot_index is None:
            return False
        if slot_index < 0:
            self.unplace_supply(supply_id)
        else:
            self.place_supply(supply_id, slot_index)
        return True

    def _occupant(self, slot_index: int) -> str | None:
        for supply_id, index in self._slot_of.items():
            if index == slot_index:
                return supply_id
        return None

    def _code_of(self, supply_id: str) -> str:
        return supply_id

    # ---- 坐标换算 ---------------------------------------------------------

    def _transform(self) -> tuple[float, QPointF]:
        width = float(self.width())
        height = float(self.height())
        if width <= 0.0 or height <= 0.0:
            return 1.0, QPointF(0.0, 0.0)
        scale = min(width / _DESIGN_WIDTH, height / _DESIGN_HEIGHT)
        return scale, QPointF(
            (width - _DESIGN_WIDTH * scale) / 2.0,
            (height - _DESIGN_HEIGHT * scale) / 2.0,
        )

    def _to_design(self, pos: QPoint) -> QPointF:
        scale, offset = self._transform()
        return QPointF(
            (pos.x() - offset.x()) / scale,
            (pos.y() - offset.y()) / scale,
        )

    def _design_rect_from_global(self, rect: QRect) -> QRectF:
        """把上一页卡片的全局矩形换算成画布坐标；拿不到位置时返回空矩形。"""
        if rect.isNull() or rect.isEmpty():
            return QRectF()
        scale, offset = self._transform()
        top_left = self.mapFromGlobal(rect.topLeft())
        return QRectF(
            (top_left.x() - offset.x()) / scale,
            (top_left.y() - offset.y()) / scale,
            rect.width() / scale,
            rect.height() / scale,
        )

    def _slot_rects(self) -> tuple[QRectF, ...]:
        return tuple(
            QRectF(
                _GRID_LEFT + column * (_CHIP_WIDTH + _SLOT_H_GAP),
                _GRID_TOP + row * (_SLOT_HEIGHT + _SLOT_V_GAP),
                _CHIP_WIDTH,
                _SLOT_HEIGHT,
            )
            for row in range(_SLOT_ROWS)
            for column in range(_SLOT_COLUMNS)
        )

    def _tray_layout(self) -> dict[str, QRectF]:
        """当前未放好的电源在托盘里的位置：放走一台后其余自动收拢居中。"""
        unplaced = [key for key in self._power_ids if key not in self._slot_of]
        if not unplaced:
            return {}
        total = len(unplaced) * _CHIP_WIDTH + (len(unplaced) - 1) * _CHIP_GAP
        left = (_DESIGN_WIDTH - total) / 2.0
        return {
            key: QRectF(
                left + index * (_CHIP_WIDTH + _CHIP_GAP),
                _TRAY_TOP,
                _CHIP_WIDTH,
                _CHIP_HEIGHT,
            )
            for index, key in enumerate(unplaced)
        }

    def _chip_rect(self, supply_id: str) -> QRectF | None:
        index = self._slot_of.get(supply_id)
        if index is not None:
            return self._slot_rects()[index]
        return self._tray_layout().get(supply_id)

    def _tray_band(self) -> QRectF:
        return QRectF(8.0, 0.0, _DESIGN_WIDTH - 16.0, _GRID_TOP - 8.0)

    # ---- 拖放 -------------------------------------------------------------

    def mousePressEvent(self, event: Any) -> None:
        if self._running:
            self.finish_transition()
            event.accept()
            return
        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return
        supply_id = self._chip_at(event.position().toPoint())
        if supply_id is None:
            super().mousePressEvent(event)
            return
        self._press = (supply_id, event.position().toPoint())
        event.accept()

    def mouseMoveEvent(self, event: Any) -> None:
        if self._press is None:
            hovering = (
                not self._running
                and self._chip_at(event.position().toPoint()) is not None
            )
            self.setCursor(Qt.OpenHandCursor if hovering else Qt.ArrowCursor)
            super().mouseMoveEvent(event)
            return
        supply_id, origin = self._press
        current = event.position().toPoint()
        if (
            current - origin
        ).manhattanLength() < QApplication.startDragDistance():
            return
        self._press = None
        self._begin_drag(supply_id, current)

    def mouseReleaseEvent(self, event: Any) -> None:
        self._press = None
        super().mouseReleaseEvent(event)

    def _chip_at(self, pos: QPoint) -> str | None:
        point = self._to_design(pos)
        for supply_id in self._power_ids:
            rect = self._chip_rect(supply_id)
            if rect is not None and rect.contains(point):
                return supply_id
        return None

    def _slot_at(self, point: QPointF) -> int | None:
        for index, rect in enumerate(self._slot_rects()):
            if rect.contains(point):
                return index
        return None

    def _begin_drag(self, supply_id: str, pos: QPoint) -> None:
        rect = self._chip_rect(supply_id)
        if rect is None:
            return
        scale, offset = self._transform()
        widget_rect = QRectF(
            offset.x() + rect.x() * scale,
            offset.y() + rect.y() * scale,
            rect.width() * scale,
            rect.height() * scale,
        )
        pixmap = self._render_chip(supply_id, rect)
        mime = QMimeData()
        mime.setData(RACK_SUPPLY_MIME, supply_id.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        if not pixmap.isNull():
            drag.setPixmap(pixmap)
            drag.setHotSpot(
                QPoint(
                    max(0, int(pos.x() - widget_rect.x())),
                    max(0, int(pos.y() - widget_rect.y())),
                )
            )
        drag.exec(Qt.MoveAction)

    def _render_chip(self, supply_id: str, rect: QRectF) -> QPixmap:
        """把当前状态下的电源卡画成拖拽跟随位图；尺寸与屏幕上的卡一致。"""
        scale, _offset = self._transform()
        ratio = float(self.devicePixelRatioF()) or 1.0
        width = max(1, int(round(rect.width() * scale * ratio)))
        height = max(1, int(round(rect.height() * scale * ratio)))
        pixmap = QPixmap(width, height)
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.TextAntialiasing, True)
            painter.scale(scale, scale)
            font = _slot_font() if supply_id in self._slot_of else card_code_font()
            self._paint_chip(painter, QRectF(0.0, 0.0, rect.width(), rect.height()),
                             self._code_of(supply_id), font)
        finally:
            painter.end()
        return pixmap

    def dragEnterEvent(self, event: Any) -> None:
        if event.mimeData().hasFormat(RACK_SUPPLY_MIME) and not self._running:
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event: Any) -> None:
        if not event.mimeData().hasFormat(RACK_SUPPLY_MIME) or self._running:
            event.ignore()
            return
        point = self._to_design(event.position().toPoint())
        slot = self._slot_at(point)
        if slot is not None:
            self._hover_target = slot
        elif self._tray_band().contains(point):
            self._hover_target = -1
        else:
            self._hover_target = None
        self.update()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: Any) -> None:
        self._hover_target = None
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: Any) -> None:
        self._hover_target = None
        if not event.mimeData().hasFormat(RACK_SUPPLY_MIME) or self._running:
            event.ignore()
            return
        supply_id = bytes(event.mimeData().data(RACK_SUPPLY_MIME)).decode("utf-8")
        point = self._to_design(event.position().toPoint())
        slot = self._slot_at(point)
        target = slot if slot is not None else (-1 if self._tray_band().contains(point) else None)
        if self.drop_supply(supply_id, target):
            event.acceptProposedAction()
        else:
            event.ignore()

    # ---- 绘制 -------------------------------------------------------------

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.TextAntialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.fillRect(self.rect(), _CANVAS)
            scale, offset = self._transform()
            painter.save()
            painter.translate(offset)
            painter.scale(scale, scale)
            if self._running or self._progress < 1.0:
                self._paint_transition(painter)
            else:
                self._paint_board(painter)
            painter.restore()
        finally:
            painter.end()

    def _paint_board(self, painter: QPainter) -> None:
        for index, rect in enumerate(self._slot_rects()):
            occupant = self._occupant(index)
            if occupant is None:
                self._paint_empty_slot(
                    painter, rect, highlighted=self._hover_target == index
                )
            else:
                self._paint_chip(
                    painter, rect, self._code_of(occupant), _slot_font()
                )
        for supply_id, rect in self._tray_layout().items():
            self._paint_chip(
                painter, rect, self._code_of(supply_id), card_code_font()
            )
        if self._hover_target == -1:
            self._paint_tray_highlight(painter)
        self._paint_arrow(painter)
        self._paint_chain(painter)
        self._paint_side_cards(painter)

    def _paint_transition(self, painter: QPainter) -> None:
        progress = self._progress
        self._paint_middle(
            painter, QPointF(0.0, (1.0 - progress) * _GROUP_SLIDE)
        )
        self._paint_side_cards(
            painter,
            progress,
        )
        tray = self._tray_layout()
        for supply_id in self._power_ids:
            entry = self._power_entry(supply_id)
            end = tray.get(supply_id)
            if entry is None or end is None:
                continue
            start = self._design_rect_from_global(entry.start_rect_global)
            if start.isEmpty():
                self._paint_chip(
                    painter, end, self._code_of(supply_id), card_code_font()
                )
                continue
            self._paint_power_morph(painter, entry, start, end, progress)

    def _power_entry(self, supply_id: str) -> RackCardEntry | None:
        for entry in self._entries:
            if entry.role_key == POWER_SUPPLY_ROLE and entry.code == supply_id:
                return entry
        return None

    def _paint_power_morph(
        self,
        painter: QPainter,
        entry: RackCardEntry,
        start: QRectF,
        end: QRectF,
        progress: float,
    ) -> None:
        """电源卡片：上边保持、下边随进度向上收拢，最后只剩设备码那一行。"""
        rect = _lerp_rect(start, end, progress)
        if rect.width() <= 0.0 or rect.height() <= 0.0:
            return
        painter.save()
        painter.setClipPath(_rounded(rect))
        ratio = float(entry.front.devicePixelRatio()) or 1.0
        natural_width = entry.front.width() / ratio
        natural_height = entry.front.height() / ratio
        if natural_width > 0.0 and natural_height > 0.0:
            factor = rect.width() / natural_width
            draw = QRectF(
                rect.x(),
                rect.y(),
                rect.width(),
                natural_height * factor,
            )
            _draw_pixmap(painter, entry.front, draw)
        painter.restore()
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_INK, 2.0))
        painter.drawRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)

    def _paint_middle(self, painter: QPainter, offset: QPointF) -> None:
        painter.save()
        painter.translate(offset)
        for index, rect in enumerate(self._slot_rects()):
            self._paint_empty_slot(
                painter, rect, highlighted=self._hover_target == index
            )
        self._paint_arrow(painter)
        self._paint_chain(painter)
        painter.restore()

    def _paint_side_cards(
        self, painter: QPainter, progress: float | None = None
    ) -> None:
        for entry, target in (
            (self._signal, QRectF(_CARD_MARGIN, _CARD_TOP, CARD_WIDTH, CARD_HEIGHT)),
            (
                self._spectrum,
                QRectF(
                    _DESIGN_WIDTH - _CARD_MARGIN - CARD_WIDTH,
                    _CARD_TOP,
                    CARD_WIDTH,
                    CARD_HEIGHT,
                ),
            ),
        ):
            if entry is None:
                continue
            rect = target
            if progress is not None:
                start = self._design_rect_from_global(entry.start_rect_global)
                if not start.isEmpty():
                    rect = _lerp_rect(start, target, progress)
            _draw_pixmap(painter, entry.front, rect)

    def _paint_chip(
        self,
        painter: QPainter,
        rect: QRectF,
        text: str,
        font: QFont,
    ) -> None:
        painter.setPen(QPen(_INK, 2.0))
        painter.setBrush(_SURFACE)
        painter.drawRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)
        painter.setPen(_INK)
        painter.setFont(font)
        inner = rect.adjusted(10.0, 0.0, -10.0, 0.0)
        painter.drawText(
            inner,
            int(Qt.AlignCenter),
            _elided(font, text, inner.width()),
        )

    def _paint_empty_slot(
        self, painter: QPainter, rect: QRectF, *, highlighted: bool
    ) -> None:
        if highlighted:
            painter.setBrush(_PRIMARY_SOFT)
            painter.setPen(QPen(_PRIMARY, 2.0))
            painter.drawRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)
            return
        painter.setBrush(Qt.NoBrush)
        painter.setPen(_SLOT_PEN)
        painter.drawRoundedRect(rect, CARD_RADIUS, CARD_RADIUS)

    def _paint_tray_highlight(self, painter: QPainter) -> None:
        band = self._tray_band()
        pen = QPen(_PRIMARY, 2.0, Qt.DashLine)
        pen.setDashPattern([5.0, 4.0])
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(band, CARD_RADIUS, CARD_RADIUS)

    def _paint_arrow(self, painter: QPainter) -> None:
        x = _CENTER_X
        top = _ARROW_TOP
        shaft_half = 6.0
        head_top = top + _ARROW_HEIGHT * 0.45
        path = QPainterPath()
        path.moveTo(x - shaft_half, top)
        path.lineTo(x + shaft_half, top)
        path.lineTo(x + shaft_half, head_top)
        path.lineTo(x + _ARROW_WIDTH / 2.0, head_top)
        path.lineTo(x, top + _ARROW_HEIGHT)
        path.lineTo(x - _ARROW_WIDTH / 2.0, head_top)
        path.lineTo(x - shaft_half, head_top)
        path.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(_INK)
        painter.drawPath(path)

    def _paint_chain(self, painter: QPainter) -> None:
        self._paint_cable(painter, _CHAIN_LEFT, _DRIVER_RECT.left())
        self._paint_cable(painter, _DRIVER_RECT.right(), _DUT_RECT.left())
        self._paint_cable(painter, _DUT_RECT.right(), _CHAIN_RIGHT)
        self._paint_amplifier(painter, _DRIVER_RECT, "Driver")
        self._paint_amplifier(painter, _DUT_RECT, "DUT")

    def _paint_cable(self, painter: QPainter, start_x: float, end_x: float) -> None:
        span = float(end_x) - float(start_x)
        if span <= 1.0:
            return
        asset = self._cable
        if asset is None:
            painter.setPen(QPen(_INK, 3.0, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QPointF(start_x, _CHAIN_Y), QPointF(end_x, _CHAIN_Y))
            return
        pixmap, source = asset
        height = 24.0
        top = _CHAIN_Y - height / 2.0
        connector = source.width() * 0.18
        connector_target = connector * height / source.height()
        if span <= connector_target * 2.0:
            painter.drawPixmap(
                QRectF(start_x, top, span, height), pixmap, source
            )
            return
        left = QRectF(source.left(), source.top(), connector, source.height())
        middle = QRectF(
            source.left() + connector,
            source.top(),
            source.width() - connector * 2.0,
            source.height(),
        )
        right = QRectF(
            source.right() - connector,
            source.top(),
            connector,
            source.height(),
        )
        painter.drawPixmap(
            QRectF(start_x, top, connector_target, height), pixmap, left
        )
        painter.drawPixmap(
            QRectF(
                start_x + connector_target,
                top,
                span - connector_target * 2.0,
                height,
            ),
            pixmap,
            middle,
        )
        painter.drawPixmap(
            QRectF(
                start_x + span - connector_target, top, connector_target, height
            ),
            pixmap,
            right,
        )

    def _paint_amplifier(self, painter: QPainter, rect: QRectF, label: str) -> None:
        path = QPainterPath()
        path.moveTo(rect.left(), rect.top())
        path.lineTo(rect.left(), rect.bottom())
        path.lineTo(rect.right(), rect.center().y())
        path.closeSubpath()
        painter.setBrush(_SURFACE)
        outline = QPen(_INK, 2.4)
        outline.setJoinStyle(Qt.RoundJoin)
        painter.setPen(outline)
        painter.drawPath(path)
        painter.setPen(_INK)
        font = QFont(card_code_font())
        font.setPointSizeF(13.0)
        font.setBold(True)
        painter.setFont(font)
        text_rect = QRectF(
            rect.left() + 10.0,
            rect.top(),
            rect.width() - 16.0,
            rect.height(),
        )
        painter.drawText(text_rect, int(Qt.AlignCenter), label)


class RackOverviewPage(QWidget):
    """整屏“仪器排布”页：入场动画 + 电源架位拖放。

    :meth:`enter` 接收本次勾选的卡片（含它们在屏幕上的起始矩形），随后播放 700 ms
    动画；动画结束后电源卡停在顶部托盘，拖进九宫格才允许点“下一步”。格位状态只在
    本页内存里保存，不写配置、不影响电源配置页的标签页顺序。
    """

    next_requested = Signal()
    back_requested = Signal()

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setObjectName("rackOverviewPage")
        self._build_ui()
        self._connect_signals()
        self._sync_actions()

    # ---- 构建 -------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        content.setMaximumWidth(PAGE_CONTENT_MAX_WIDTH)
        column = QVBoxLayout(content)
        column.setContentsMargins(24, 12, 24, 16)
        column.setSpacing(10)
        # 内容列吃满可用宽度（到版心上限为止）；两侧只分到剩下的余量，窗口变宽时
        # 示意图仍然居中，而不是被挤成窄窄的一条。
        root.addStretch(1)
        root.addWidget(content, 100)
        root.addStretch(1)

        header = QHBoxLayout()
        header.setSpacing(16)
        self.title_label = QLabel("仪器排布")
        self.title_label.setProperty("role", "pageTitle")
        self.hint_label = QLabel()
        self.hint_label.setProperty("role", "hint")
        self.hint_label.setWordWrap(True)
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(self.hint_label, 1)
        column.addLayout(header)

        self.board = _RackBoard(self)
        column.addWidget(self.board, 1)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.back_btn = QPushButton("上一步：仪表配置")
        self.back_btn.setToolTip("回到仪表配置页，重新扫描并选择本次测试使用的设备")
        set_button_variant(self.back_btn, "secondary")
        self.next_btn = QPushButton("下一步")
        self.next_btn.setToolTip("进入仪器配置，配置测试参数并完成仪器连接")
        set_button_variant(self.next_btn, "primary")
        actions.addWidget(self.back_btn)
        actions.addStretch(1)
        actions.addWidget(self.next_btn)
        column.addLayout(actions)

    def _connect_signals(self) -> None:
        self.board.state_changed.connect(self._sync_actions)
        self.back_btn.clicked.connect(self.back_requested.emit)
        self.next_btn.clicked.connect(self.request_next)

    # ---- 进入 -------------------------------------------------------------

    def enter(self, entries: Iterable[RackCardEntry]) -> None:
        """按本次勾选的卡片重建页面并播放一次入场动画。"""
        self.board.enter(entries)
        self._sync_actions()

    def finish_transition(self) -> None:
        self.board.finish_transition()

    def transition_running(self) -> bool:
        return self.board.transition_running()

    # ---- 摆位 -------------------------------------------------------------

    def power_supply_ids(self) -> tuple[str, ...]:
        return self.board.power_supply_ids()

    def slot_of(self, supply_id: str) -> int | None:
        return self.board.slot_of(supply_id)

    def slot_assignments(self) -> dict[str, int]:
        return self.board.slot_assignments()

    def place_supply(self, supply_id: str, slot_index: int) -> None:
        self.board.place_supply(supply_id, slot_index)

    def unplace_supply(self, supply_id: str) -> None:
        self.board.unplace_supply(supply_id)

    def drop_supply(self, supply_id: str, slot_index: int | None) -> bool:
        return self.board.drop_supply(supply_id, slot_index)

    def all_supplies_placed(self) -> bool:
        return self.board.all_supplies_placed()

    # ---- 动作 -------------------------------------------------------------

    def request_next(self) -> None:
        if self.next_btn.isEnabled():
            self.next_requested.emit()

    def _sync_actions(self) -> None:
        total = len(self.board.power_supply_ids())
        placed = self.board.placed_count()
        ready = not self.board.transition_running() and placed == total
        self.next_btn.setEnabled(ready)
        if total == 0:
            self.hint_label.setText("本次没有电源需要排布，可以直接进入下一步。")
        elif placed == total:
            self.hint_label.setText("电源已全部放到架子上，可以进入下一步。")
        else:
            self.hint_label.setText(
                "把电源拖到九宫格中与现场一致的位置；"
                f"还差 {total - placed} 台电源。"
            )
        self.next_btn.setToolTip(
            "进入仪器配置，配置测试参数并完成仪器连接"
            if ready
            else "请先把全部电源拖入九宫格"
        )

    # ---- 卡片入口 ---------------------------------------------------------

    @classmethod
    def entries_from_cards(cls, cards: Sequence[Any]) -> list[RackCardEntry]:
        """把设备发现页勾选的卡片转成本页的进入条目。

        卡片正面用与真实卡片同源的 :func:`render_card_front` 渲染（去掉选中态的高亮，
        保留“可应用”的白底样式，与效果图里白底灰边的卡片一致），起始矩形取卡片当前的
        全局位置。这样页面不需要截取控件，也不依赖结果区的布局对象。
        """
        entries: list[RackCardEntry] = []
        for card in cards:
            data = getattr(card, "data", None)
            if not isinstance(data, InstrumentCardData):
                continue
            front = render_card_front(
                replace(data, checked=False),
                device_pixel_ratio=float(card.devicePixelRatioF()) or 1.0,
            )
            top_left = card.mapToGlobal(QPoint(0, 0))
            entries.append(
                RackCardEntry(
                    role_key=data.role_key,
                    code=data.code or data.heading,
                    front=front,
                    start_rect_global=QRect(top_left, card.size()),
                )
            )
        return entries
