"""绿色光子卡面：等待动画最上面那张牌与仪器卡片牌背共用的一份画法。

“查找设备”的按钮卡面、等待牌堆最上面那张牌、发牌时背面朝上的仪器卡片，三处用的是
同一张卡：

- 45° 近黑渐变（``#1A1A1A`` → ``#262626``）与四边内阴影，再压一层很淡的提亮；
- 卡面正中一团青绿→天蓝的柔光，外面三团 ``#00FFAA`` 的光斑（左上、右侧、底部偏左）；
- 四角靛蓝角标，每道角标带一圈外发光。

按钮在它上面再画标题与副标题；等待牌堆与发牌牌背不加字，于是同一张位图在两边都读作
“牌背”。卡面不随时间变化的那一层由 :func:`render_photon_card` 预渲染成一张位图，
六个外飘的绿色粒子由 :func:`paint_photon_particles` 每帧按相位画。

**裁切**：:func:`render_photon_card` 自己按卡片圆角裁好；直接调用
:func:`paint_photon_face` / :func:`paint_photon_particles` 时，调用方负责按
:data:`~presentation.qt.theme.CARD_RADIUS` 建立轮廓并 ``setClipPath``。位图按逻辑尺寸
渲染、按屏幕像素比放大，因此高 DPI 只增加清晰度，不改变版面。

**运行约束**：``QPixmap`` 必须在 GUI 线程、创建 ``QApplication`` 之后创建与读取。
"""

from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)

from .theme import CARD_RADIUS

__all__ = [
    "ACCENT",
    "ACCENT_BLUE",
    "BLACK",
    "BLOB_SIZE",
    "BRIGHTNESS_WASH",
    "CORNER_STROKE",
    "EDGE",
    "FACE_END",
    "FACE_START",
    "INDIGO",
    "PARTICLE_PERIOD_MS",
    "PARTICLE_SIZE",
    "PARTICLE_TRAVEL",
    "PARTICLES",
    "VIOLET",
    "WHITE",
    "alpha",
    "glow_pen",
    "paint_photon_face",
    "paint_photon_particles",
    "render_photon_card",
    "triangle",
]

# ---- 配色：这段深色卡面 + 青绿/靛蓝/紫青配色是效果本身的一部分，不并入浅色主题 ----

FACE_START = QColor("#1A1A1A")
FACE_END = QColor("#262626")
#: ``rgba(255, 255, 255, 0.1)`` 的卡片描边（按钮在卡面外再描一道）。
EDGE = QColor(255, 255, 255, 26)
ACCENT = QColor("#00FFAA")
ACCENT_BLUE = QColor("#00A2FF")
INDIGO = QColor("#5C67FF")
VIOLET = QColor("#AD51FF")
WHITE = QColor("#FFFFFF")
BLACK = QColor("#000000")

# ---- 卡面装饰 ----------------------------------------------------------------

#: 卡面提亮那一层白的透明度：近黑卡面上约等于 ``filter: brightness(1.1)``。
BRIGHTNESS_WASH = 0.02
#: 四角角标的笔画宽度（原效果的 ``.corner`` 是 2px）。
CORNER_STROKE = 2.0
#: 三团光晕的直径：原效果的 ``.glow-1..3`` 是 100×100 的圆形色斑。
BLOB_SIZE = 100.0

# ---- 粒子 --------------------------------------------------------------------

#: 粒子相位一轮的时长（``particleFloat`` 2s）。
PARTICLE_PERIOD_MS = 2000
#: 粒子：(横向占比, 纵向占比, 外飘方向 x, 外飘方向 y)，对应六个 ``.card-particles span``。
PARTICLES = (
    (0.20, 0.40, 1.0, -1.0),
    (0.80, 0.60, -1.0, -1.0),
    (0.40, 0.20, 0.5, 1.0),
    (0.60, 0.80, -0.5, 1.0),
    (0.60, 0.30, 1.0, 0.5),
    (0.40, 0.70, -1.0, 0.5),
)
PARTICLE_SIZE = 3.0
#: 粒子外飘的最大位移（原效果 ``--x/--y`` 乘 30px）。
PARTICLE_TRAVEL = 30.0


def alpha(color: QColor, ratio: float) -> QColor:
    """同色不同透明度：``ratio`` 是相对原 alpha 的比例。"""
    faded = QColor(color)
    faded.setAlpha(round(color.alpha() * max(0.0, min(1.0, ratio))))
    return faded


def triangle(phase: float) -> float:
    """``0 → 1 → 0`` 的三角波：``particleFloat`` 的关键帧就是它。"""
    phase = phase % 1.0
    return phase * 2.0 if phase < 0.5 else (1.0 - phase) * 2.0


def glow_pen(color: QColor, width: float) -> QPen:
    """圆头圆角的描边笔：文字外发光与角标光晕都用它，描边没有断口。"""
    pen = QPen(color, float(width))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _diagonal_gradient(box: QRectF) -> QLinearGradient:
    """45° 渐变（``linear-gradient(45deg, …)``）：从左下往右上。"""
    return QLinearGradient(box.bottomLeft(), box.topRight())


# ---- 卡面（不随时间变化的那一层） --------------------------------------------


def paint_photon_face(painter: QPainter, card: QRectF) -> None:
    """45° 的近黑渐变卡面 + 四边内阴影 + 一层提亮（``filter: brightness(1.1)``）。

    等待动画下层的斜纹牌背也先铺这一层，保证一叠牌共用同一种深色底。
    """
    gradient = _diagonal_gradient(card)
    gradient.setColorAt(0.0, FACE_START)
    gradient.setColorAt(1.0, FACE_END)
    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawRect(card)

    # ``inset 0 0 20px rgba(0, 0, 0, 0.2)``：沿四边往里画几圈很淡的暗色。
    painter.setBrush(Qt.NoBrush)
    for step in range(4):
        inset = 1.0 + step * 4.0
        painter.setPen(QPen(alpha(BLACK, 10 / 255.0), 4.0))
        painter.drawRoundedRect(
            card.adjusted(inset, inset, -inset, -inset),
            max(1.0, CARD_RADIUS - inset),
            max(1.0, CARD_RADIUS - inset),
        )

    painter.setPen(Qt.NoPen)
    painter.setBrush(alpha(WHITE, BRIGHTNESS_WASH))
    painter.drawRect(card)


def _paint_card_glow(painter: QPainter, card: QRectF) -> None:
    """卡面自己的一团青绿→天蓝柔光（压深色卡面用的内发光，不是背后的霓虹光斑）。"""
    center = card.center()
    reach = 0.75 * math.hypot(card.width(), card.height())
    gradient = QRadialGradient(center, reach)
    gradient.setColorAt(0.0, alpha(ACCENT, 0.08))
    gradient.setColorAt(0.5, alpha(ACCENT_BLUE, 0.04))
    gradient.setColorAt(1.0, alpha(ACCENT_BLUE, 0.0))
    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(center, reach, reach)


def _paint_card_blobs(painter: QPainter, card: QRectF) -> None:
    """三团光晕（``.glow-1..3``）：左上角外、右侧外、底部偏左。"""
    radius = BLOB_SIZE / 2.0
    centers = (
        # glow-1：``top: -20px; left: -20px``。
        QPointF(card.left() + 30.0, card.top() + 30.0),
        # glow-2：``top: 50%; right: -30px``，竖直居中。
        QPointF(card.right() - 20.0, card.center().y()),
        # glow-3：``bottom: -20px; left: 30%``。
        QPointF(card.left() + card.width() * 0.30 + radius, card.bottom() - 30.0),
    )
    painter.setPen(Qt.NoPen)
    for center in centers:
        # ``filter: blur(15px)`` 的近似：把半径放大一圈，让它自己就散开。
        reach = radius * 1.3
        gradient = QRadialGradient(center, reach)
        gradient.setColorAt(0.0, alpha(ACCENT, 0.3))
        gradient.setColorAt(0.7, alpha(ACCENT, 0.0))
        painter.setBrush(gradient)
        painter.drawEllipse(center, reach, reach)


def _paint_card_corners(painter: QPainter, card: QRectF) -> None:
    """四角的直角角标：靛蓝描边 + 一圈外发光。"""
    color = alpha(INDIGO, 0.8)
    size = 15.0
    gap = 10.0
    left = card.left() + gap
    right = card.right() - gap
    top = card.top() + gap
    bottom = card.bottom() - gap
    strokes = (
        ((left, top), (left + size, top)),
        ((left, top), (left, top + size)),
        ((right - size, top), (right, top)),
        ((right, top), (right, top + size)),
        ((left, bottom), (left + size, bottom)),
        ((left, bottom - size), (left, bottom)),
        ((right - size, bottom), (right, bottom)),
        ((right, bottom - size), (right, bottom)),
    )
    # ``box-shadow: 0 0 10px rgba(92, 103, 255, 0.5)``：先描一圈更淡更粗的底。
    painter.setBrush(Qt.NoBrush)
    glow = glow_pen(alpha(INDIGO, 0.35), 5.0)
    glow.setCapStyle(Qt.FlatCap)
    painter.setPen(glow)
    for start, end in strokes:
        painter.drawLine(QPointF(*start), QPointF(*end))
    solid = glow_pen(color, CORNER_STROKE)
    solid.setCapStyle(Qt.FlatCap)
    painter.setPen(solid)
    for start, end in strokes:
        painter.drawLine(QPointF(*start), QPointF(*end))


def paint_photon_particles(painter: QPainter, card: QRectF, phase: float) -> None:
    """六个小亮点向外飘散：``0 → 1 → 0`` 的透明度，位移最远 30px。

    等待动画最上面那张牌与发牌时背面朝上的卡共用这一份画法：粒子是卡面内部的动效，
    画在卡片自己的坐标系里。调用方负责先把坐标系放到卡片上、并按圆角裁好。
    """
    opacity = triangle(phase)
    if opacity <= 0.0:
        return
    painter.setPen(Qt.NoPen)
    painter.setBrush(alpha(ACCENT, opacity))
    half = PARTICLE_SIZE / 2.0
    travel = PARTICLE_TRAVEL * phase
    for x_ratio, y_ratio, dx, dy in PARTICLES:
        center = QPointF(
            card.left() + card.width() * x_ratio + half + dx * travel,
            card.top() + card.height() * y_ratio + half + dy * travel,
        )
        painter.drawEllipse(center, half, half)


@lru_cache(maxsize=4)
def render_photon_card(width: int, height: int, ratio: float) -> QPixmap:
    """绿色光子卡面的预渲染图：尺寸与屏幕像素比不变就整张复用。

    “查找设备”按钮、等待动画最上面那张牌与发牌牌背都从这里取图：摊开/发牌只是把这张
    位图平移、旋转、缩放，不必每帧重画卡面。
    """
    size = QSize(max(1, round(width * ratio)), max(1, round(height * ratio)))
    pixmap = QPixmap(size)
    pixmap.fill(Qt.transparent)
    # 带 dpr 的画布由 Qt 换算成逻辑坐标，这里不再自己缩放一次。
    pixmap.setDevicePixelRatio(ratio)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    card = QRectF(0.0, 0.0, float(width), float(height))
    silhouette = QPainterPath()
    silhouette.addRoundedRect(card, CARD_RADIUS, CARD_RADIUS)
    painter.setClipPath(silhouette)
    paint_photon_face(painter, card)
    _paint_card_glow(painter, card)
    _paint_card_blobs(painter, card)
    _paint_card_corners(painter, card)
    painter.end()
    return pixmap
