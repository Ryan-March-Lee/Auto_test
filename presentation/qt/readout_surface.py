"""读数面底色：测量结果顶部深色玻璃带的自绘背景。

线损页的监控台与功放/驱动映射页的读数条共用同一种视觉语言：深海军蓝渐变、
极淡网格、1px 内高光与描边。底色不能交给样式表：圆角裁剪与网格必须和控件矩形
对齐，QSS 再铺一层会把自绘的渐变盖掉。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .theme import TOKENS

__all__ = ["paint_readout_surface"]

#: 网格间距（像素）：固定值让不同宽度的读数面保持同一密度。
_GRID_STEP = 22
_GRID_ALPHA = 8
_HIGHLIGHT_ALPHA = 20
_CORNER_RADIUS = 8.0


def paint_readout_surface(painter: QPainter, widget: QWidget) -> None:
    """在 ``widget`` 的矩形里铺满读数面底色（调用方负责创建并结束 ``painter``）。"""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    rect = QRectF(widget.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
    path = QPainterPath()
    path.addRoundedRect(rect, _CORNER_RADIUS, _CORNER_RADIUS)

    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gradient.setColorAt(0.0, QColor(TOKENS["readout_body"]))
    gradient.setColorAt(1.0, QColor(TOKENS["readout_body_bottom"]))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawPath(path)

    painter.save()
    painter.setClipPath(path)
    grid = QColor(TOKENS["readout_grid"])
    grid.setAlpha(_GRID_ALPHA)
    painter.setPen(QPen(grid, 1.0))
    for x in range(0, widget.width(), _GRID_STEP):
        painter.drawLine(x, 0, x, widget.height())
    for y in range(0, widget.height(), _GRID_STEP):
        painter.drawLine(0, y, widget.width(), y)
    highlight = QColor(TOKENS["readout_grid"])
    highlight.setAlpha(_HIGHLIGHT_ALPHA)
    painter.setPen(QPen(highlight, 1.0))
    painter.drawLine(
        QPointF(rect.left() + 10, rect.top() + 1),
        QPointF(rect.right() - 10, rect.top() + 1),
    )
    painter.restore()

    painter.setPen(QPen(QColor(TOKENS["readout_border"]), 1.0))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
