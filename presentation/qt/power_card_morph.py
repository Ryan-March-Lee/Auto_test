"""排布页电源卡片 → 电源配置页通道卡片的过渡覆盖层。

离开“仪器排布”页时，页面先把除电源卡片以外的内容淡出；切到“电源配置”页后，
本覆盖层接管画面：每张卡片从它在排布页里的屏幕位置出发，边移动到卡片墙上的
最终格位、边展开成带黄蓝通道的通道卡片，其余区域保持画布底色，最后淡出并交给
页面真实的卡片墙。两张卡片的外接矩形来自同一个 :class:`PowerCardWall`，所以
覆盖层最后一帧与卡片墙的静止画面逐像素一致，不会在交接时跳一下。

覆盖层只在 GUI 线程创建，必须在父页面已经完成布局之后调用 :meth:`start`。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QWidget

from .theme import CARD_RADIUS, TOKENS, card_code_font

__all__ = ["PowerCardMorphItem", "PowerCardMorphOverlay"]

_CANVAS = QColor(TOKENS["canvas"])
_SURFACE = QColor(TOKENS["surface"])
_BORDER = QColor(TOKENS["border"])
_INK = QColor(TOKENS["text"])
_TEXT_INSET = 10.0

#: 卡片展开的时长：比排布页入场短一些，落位后立即交给真实页面。
_MORPH_MS = 520


@dataclass(frozen=True)
class PowerCardMorphItem:
    """一张正在变形的电源卡：起点、终点、卡面位图与设备码。"""

    supply_id: str
    code: str
    start: QRectF
    target: QRectF
    pixmap: QPixmap


def _lerp(start: float, end: float, t: float) -> float:
    return start + (end - start) * t


def _lerp_rect(start: QRectF, end: QRectF, t: float) -> QRectF:
    return QRectF(
        _lerp(start.x(), end.x(), t),
        _lerp(start.y(), end.y(), t),
        _lerp(start.width(), end.width(), t),
        _lerp(start.height(), end.height(), t),
    )


def _rounded(rect: QRectF, inset: float = 0.0) -> QPainterPath:
    adjusted = rect.adjusted(inset, inset, -inset, -inset)
    radius = max(1.0, CARD_RADIUS - inset)
    path = QPainterPath()
    path.addRoundedRect(adjusted, radius, radius)
    return path


def _elided(font: QFont, text: str, width: float) -> str:
    return QFontMetricsF(font).elidedText(text, Qt.ElideRight, max(1.0, width))


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


class PowerCardMorphOverlay(QWidget):
    """铺满父页面的覆盖层：画布底色 + 逐张展开的电源卡片。"""

    finished = Signal()

    def __init__(
        self,
        parent: QWidget,
        items: Iterable[PowerCardMorphItem],
        *,
        duration_ms: int = _MORPH_MS,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("powerCardMorphOverlay")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._items = [item for item in items if not item.target.isEmpty()]
        self._progress = 0.0
        self._done = False
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(max(1, int(duration_ms)))
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setEasingCurve(QEasingCurve.InOutCubic)
        self._animation.valueChanged.connect(self._on_value)
        self._animation.finished.connect(self._finish)

    # ---- 状态 -------------------------------------------------------------

    def items(self) -> tuple[PowerCardMorphItem, ...]:
        return tuple(self._items)

    def progress(self) -> float:
        return float(self._progress)

    def start(self) -> None:
        self.show()
        self.raise_()
        self._animation.start()

    def finish_now(self) -> None:
        """立即落到终态并只发一次 ``finished``（测试与点击跳过使用）。"""
        self._animation.stop()
        self._progress = 1.0
        self.update()
        self._finish()

    def _on_value(self, value: object) -> None:
        self._progress = max(0.0, min(1.0, float(value)))
        self.update()

    def _finish(self) -> None:
        if self._done:
            return
        self._done = True
        self._progress = 1.0
        self.update()
        self.finished.emit()

    # ---- 绘制 -------------------------------------------------------------

    def _tile_opacity(self, progress: float) -> float:
        """卡片在前 60% 里淡出，卡片同时淡入。"""
        return max(0.0, 1.0 - min(1.0, progress / 0.6))

    def _backdrop_opacity(self, progress: float) -> float:
        """画布底色到后 40% 才淡出，露出电源配置页本身。"""
        if progress <= 0.6:
            return 1.0
        return max(0.0, (1.0 - progress) / 0.4)

    def _paint_tile(self, painter: QPainter, rect: QRectF, code: str, opacity: float) -> None:
        if opacity <= 0.001 or rect.isEmpty():
            return
        font = card_code_font()
        painter.save()
        painter.setOpacity(opacity)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillPath(_rounded(rect), _SURFACE)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_BORDER, 1.0))
        painter.drawPath(_rounded(rect, 0.5))
        painter.setFont(font)
        painter.setPen(_INK)
        inner = rect.adjusted(_TEXT_INSET, 0.0, -_TEXT_INSET, 0.0)
        painter.drawText(
            inner,
            int(Qt.AlignCenter),
            _elided(font, code, inner.width()),
        )
        painter.restore()

    def paintEvent(self, event) -> None:
        if not self._items:
            return
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setRenderHint(QPainter.TextAntialiasing, True)
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            backdrop = self._backdrop_opacity(self._progress)
            if backdrop > 0.0:
                painter.setOpacity(backdrop)
                painter.fillRect(self.rect(), _CANVAS)
                painter.setOpacity(1.0)
            tile_opacity = self._tile_opacity(self._progress)
            card_opacity = 1.0 - tile_opacity
            for item in self._items:
                rect = _lerp_rect(item.start, item.target, self._progress)
                self._paint_tile(painter, rect, item.code, tile_opacity)
                if card_opacity > 0.001:
                    painter.save()
                    painter.setOpacity(card_opacity)
                    _draw_pixmap(painter, item.pixmap, rect)
                    painter.restore()
        finally:
            painter.end()
