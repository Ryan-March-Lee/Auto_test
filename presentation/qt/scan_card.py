"""“查找设备”的卡片按钮：一张深色卡片，正中一行渐变标题。

原效果是一段 HTML/CSS 的 Uiverse 卡片（190×254 深色卡片，悬停时才亮起柔光、光晕、粒子、
高光与角标）。这里用 Qt 的技术栈复刻，但**只取它悬停之后的样子**：

- 卡片一直停在“亮起”的状态：卡面提亮、青绿柔光、三团光晕、六个外飘的粒子、斜向高光、
  四角角标都亮着；
- **鼠标悬浮不再有任何动效**（原效果的过渡不做，按下缩小与 25 格鼠标跟踪的 3D 倾斜也不做），
  卡片在鼠标进出前后长得一模一样；
- “查找设备”四个字以青绿→天蓝的渐变标题落在**卡片正中**，下面一行副标题留在底边。

仍在动的只有卡片自身的环境动效：四条横向光带（3s 一轮，各自错开）、自上而下扫过的扫描线
（2s 一轮）与粒子的外飘（2s 一轮）。

实现：**整块自绘**（``paintEvent`` 依次贴卡面、画光带/扫描线/粒子、写标题与副标题、扫高光），
不随鼠标变化的那些层（投影、卡面、柔光、角标）预算成一张位图，逐帧只画会动的那几层；
数值由一个 30fps 的计时器推进。样式表表达不了伪元素、``transition`` 与 ``transform``，
因此本控件不着样式表：颜色、字号与尺寸都在本模块给出（深色卡面 + 青绿/靛蓝配色是这段
效果的一部分，不并入浅色主题的令牌；焦点框仍取 :data:`theme.TOKENS` 的主色）。

它仍是 ``QPushButton`` 的子类：``clicked``、``setEnabled``、``setToolTip``、键盘
空格/回车与悬浮提示照旧，调用方不必知道它是自绘的。
"""

from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import QElapsedTimer, QEvent, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from .theme import FONT_FAMILIES, TOKENS

__all__ = ["ScanPromptCard", "CARD_HEIGHT", "CARD_WIDTH", "GLOW_MARGIN"]

# ---- 尺寸（原效果的 px 值） --------------------------------------------------

#: 卡片本体尺寸：原效果是 190×254 的竖卡。
CARD_WIDTH = 190
CARD_HEIGHT = 254
CARD_RADIUS = 20.0
CARD_BORDER = 2.0
#: 四周留白：Qt 会把画到控件矩形外的像素裁掉，投影与外发光必须画在控件里面。
GLOW_MARGIN = 18

#: 卡片上的文案：正中是渐变大字，底边一行副标题（高亮词单独上渐变）。
CARD_TEXT = "查找设备"
SUBTITLE_TEXT = "网段内"
SUBTITLE_HIGHLIGHT = "全部仪器"

TITLE_PIXEL_SIZE = 28
SUBTITLE_PIXEL_SIZE = 12
TITLE_LETTER_SPACING = 4.0
SUBTITLE_LETTER_SPACING = 2.0
#: ``.highlight`` 的 ``margin-left: 5px``。
HIGHLIGHT_GAP = 5.0

# ---- 配色（原效果的配色，集中在模块里一次） ----------------------------------

_FACE_START = QColor("#1A1A1A")
_FACE_END = QColor("#262626")
#: 2px ``rgba(255, 255, 255, 0.1)`` 的卡面描边。
_EDGE = QColor(255, 255, 255, 26)
#: ``rgba(255, 255, 255, 0.6)`` 的副标题。
_SUBTITLE_COLOR = QColor(255, 255, 255, 153)
_ACCENT = QColor("#00FFAA")
_ACCENT_BLUE = QColor("#00A2FF")
_INDIGO = QColor("#5C67FF")
_VIOLET = QColor("#AD51FF")
_WHITE = QColor("#FFFFFF")
_BLACK = QColor("#000000")

# ---- 时间轴 ------------------------------------------------------------------

#: 30fps：光带、扫描线与粒子一直在走。
_FRAME_MS = 33
#: ``lineGrow`` 3s、``scanMove`` 2s、``particleFloat`` 2s。
_CYBER_PERIOD_MS = 3000
_SCAN_PERIOD_MS = 2000
_PARTICLE_PERIOD_MS = 2000

#: 横向光带：(纵向位置占比, 是否从左端起, 动画延迟秒数)，对应四个 ``.cyber-lines span``。
_CYBER_LINES = (
    (0.20, True, 0.0),
    (0.40, False, 1.0),
    (0.60, True, 2.0),
    (0.80, False, 1.5),
)
#: 粒子：(横向占比, 纵向占比, 外飘方向 x, 外飘方向 y)，对应六个 ``.card-particles span``。
_PARTICLES = (
    (0.20, 0.40, 1.0, -1.0),
    (0.80, 0.60, -1.0, -1.0),
    (0.40, 0.20, 0.5, 1.0),
    (0.60, 0.80, -0.5, 1.0),
    (0.60, 0.30, 1.0, 0.5),
    (0.40, 0.70, -1.0, 0.5),
)
#: 三团光晕的直径：原效果的 ``.glow-1..3`` 是 100×100 的圆形色斑。
_BLOB_SIZE = 100.0
_PARTICLE_SIZE = 3.0
#: 粒子外飘的最大位移（原效果 ``--x/--y`` 乘 30px）。
_PARTICLE_TRAVEL = 30.0
#: 外发光（``0 0 20px rgba(0, 0, 0, 0.3)``）用几层逐渐收窄的描边叠出来。
_SHADOW_LAYERS = 5
#: 卡面提亮那一层白的透明度：近黑卡面上约等于 ``filter: brightness(1.1)``。
_BRIGHTNESS_WASH = 0.02
#: 不可点时整块淡下去：浅色页面上“灰掉”比另配一套深色配色更清楚。
_DISABLED_OPACITY = 0.55
#: 标题光晕的预渲染图四周多留的像素：9px 描边会溢出字形包围盒。
_HALO_PAD = 10


def _alpha(color: QColor, ratio: float) -> QColor:
    """同色不同透明度：``ratio`` 是相对原 alpha 的比例。"""
    faded = QColor(color)
    faded.setAlpha(round(color.alpha() * max(0.0, min(1.0, ratio))))
    return faded


def _triangle(phase: float) -> float:
    """``0 → 1 → 0`` 的三角波：``lineGrow`` 与 ``particleFloat`` 的关键帧就是它。"""
    phase = phase % 1.0
    return phase * 2.0 if phase < 0.5 else (1.0 - phase) * 2.0


def _glow_pen(color: QColor, width: float) -> QPen:
    """圆头圆角的描边笔：文字外发光与角标光晕都用它，描边没有断口。"""
    pen = QPen(color, float(width))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


@lru_cache(maxsize=6)
def _card_font(pixel_size: int, weight: int, letter_spacing: float) -> QFont:
    """卡片上的一档字体：字号按原效果的 px 给，字距用绝对间距（``letter-spacing``）。"""
    font = QFont()
    font.setFamilies(list(FONT_FAMILIES))
    font.setPixelSize(int(pixel_size))
    font.setWeight(QFont.Weight(weight))
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, float(letter_spacing))
    return font


@lru_cache(maxsize=8)
def _text_path(text: str, pixel_size: int, weight: int, spacing: float) -> QPainterPath:
    """一行文字的填充路径（左边界与基线为原点）：排一次版，之后按需平移着画。

    每帧都调 ``QPainterPath.addText`` 等于每帧重排一次版，光是卡片上的两行字就够把
    30fps 吃掉；路径只跟文字与字体有关，因此缓存下来。
    """
    font = _card_font(pixel_size, weight, spacing)
    path = QPainterPath()
    path.addText(0.0, QFontMetricsF(font).ascent(), font, text)
    return path


def _render_title_halo(text: str, ratio: float) -> QPixmap:
    """把标题的外发光预渲染成一张位图（四周留出描边溢出的余量）。

    光晕是拿粗描边笔「描」字形轮廓画出来的，而描轮廓是这条绘制链里最贵的一步：每帧都描
    两遍会占掉一半的帧时间。它只跟文字与字体有关，于是画一次、之后整张贴上去。
    """
    path = _text_path(
        text, TITLE_PIXEL_SIZE, QFont.Weight.ExtraBold.value, TITLE_LETTER_SPACING
    )
    box = path.boundingRect()
    width = int(math.ceil(box.width())) + 2 * _HALO_PAD
    height = int(math.ceil(box.height())) + 2 * _HALO_PAD
    pixmap = QPixmap(
        QSize(max(1, round(width * ratio)), max(1, round(height * ratio)))
    )
    pixmap.fill(Qt.transparent)
    # 带 dpr 的画布由 Qt 换算成逻辑坐标，这里不再自己缩放一次。
    pixmap.setDevicePixelRatio(ratio)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.translate(_HALO_PAD - box.left(), _HALO_PAD - box.top())
    painter.setBrush(Qt.NoBrush)
    painter.setPen(_glow_pen(_alpha(_ACCENT, 0.10), 9.0))
    painter.drawPath(path)
    painter.setPen(_glow_pen(_alpha(_ACCENT, 0.22), 4.0))
    painter.drawPath(path)
    painter.end()
    return pixmap


def _diagonal_gradient(box: QRectF) -> QLinearGradient:
    """45° 渐变（``linear-gradient(45deg, …)``）：从左下往右上。"""
    return QLinearGradient(box.bottomLeft(), box.topRight())


def _horizontal_gradient(box: QRectF) -> QLinearGradient:
    """90° 渐变（``linear-gradient(90deg, …)``）：从左往右。"""
    return QLinearGradient(box.topLeft(), box.topRight())


class ScanPromptCard(QPushButton):
    """“查找设备”按钮：一张一直亮着的深色卡片，正中是渐变标题。

    卡片自上而下读：正中一行 28px 的青绿→天蓝渐变标题（带一层青绿光晕），底边一行小字
    副标题，四角是靛蓝角标。卡片内部一直有动效：四条横向光带缓慢长出又收回、一条扫描带
    自上而下扫过、六个小亮点向外飘散；卡面还压着一层柔光与一道斜向高光。

    **鼠标悬浮不改变任何东西**：原效果里“悬停才亮起”的那段过渡整段拿掉，卡片在鼠标进出
    前后一模一样，只有上面那几条环境动效自己在动。

    尺寸固定在 :data:`CARD_WIDTH` × :data:`CARD_HEIGHT`（四周另有 :data:`GLOW_MARGIN`
    的留白用来画投影），由 :meth:`sizeHint` 交给布局，因此页面里它的占位与一张设备卡片
    相当；动效不会改变版面。
    """

    def __init__(self, text: str = CARD_TEXT, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("scanPromptCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._apply_fixed_size()

        #: 环境相位（光带与扫描线）与粒子相位，都在 0..1 之间循环。
        self._ambient = 0.0
        self._particle = 0.0
        #: 焦点框只在键盘走到这里时画：鼠标点出来的焦点不该在卡片外留一圈线。
        self._focus_visible = False
        #: 不随鼠标变化的那些层（投影、卡面、柔光、角标）的预渲染图。
        self._face = QPixmap()
        self._face_key: tuple[int, int, float] | None = None
        #: 标题光晕的预渲染图（按文字与屏幕像素比缓存）。
        self._halo = QPixmap()
        self._halo_key: tuple[str, float] | None = None
        self._clock = QElapsedTimer()
        self._clock.start()
        self._last_tick = 0

        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(_FRAME_MS)
        self._frame_timer.timeout.connect(self._advance)

    # ---- 尺寸 ---------------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 覆盖方法
        return QSize(
            int(CARD_WIDTH + 2 * GLOW_MARGIN), int(CARD_HEIGHT + 2 * GLOW_MARGIN)
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 覆盖方法
        return self.sizeHint()

    def _apply_fixed_size(self) -> None:
        """把卡片的尺寸钉死在设计尺寸上。

        样式表里的 ``QPushButton`` 规则（内边距与 ``min-height``）会给每个按钮补一条最小
        高度：卡片按钮与别的按钮并排（收起结果后的那一行）时会被压扁成一行高。样式每次
        重新落一遍都会改回来，因此在 :meth:`changeEvent` 里再钉一次。
        """
        self.setFixedSize(self.sizeHint())

    def changeEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        if event.type() == QEvent.Type.StyleChange:
            self._apply_fixed_size()
        super().changeEvent(event)

    def _card_rect(self) -> QRectF:
        """卡片本体在控件里的矩形：四边留出画投影与外发光的余地。"""
        return QRectF(self.rect()).adjusted(
            GLOW_MARGIN, GLOW_MARGIN, -GLOW_MARGIN, -GLOW_MARGIN
        )

    def hitButton(self, position) -> bool:  # noqa: N802 - Qt 覆盖方法
        """只有点在卡片本体上才算点中：四周留白只是给投影的。"""
        return self._card_rect().contains(QPointF(position))

    # ---- 动画 ---------------------------------------------------------------

    def _advance(self) -> None:
        """推进光带、扫描线与粒子：卡片上没有别的动效，鼠标进出都不参与。"""
        now = self._clock.elapsed()
        delta = max(0, min(now - self._last_tick, 100))
        self._last_tick = now
        self._ambient = (self._ambient + delta / _CYBER_PERIOD_MS) % 1.0
        self._particle = (self._particle + delta / _PARTICLE_PERIOD_MS) % 1.0
        self.update()

    def showEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        # 样式表给 QPushButton 补的 min-height 会在显示时重新落一遍，尺寸要再钉一次。
        self._apply_fixed_size()
        self._last_tick = self._clock.elapsed()
        self._frame_timer.start()
        super().showEvent(event)

    def hideEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """收起时停下计时器：看不见的卡片不必重绘。"""
        self._frame_timer.stop()
        super().hideEvent(event)

    def focusInEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """只有 Tab/键盘走过来的焦点才画焦点框（与网页的 ``:focus-visible`` 同理）。"""
        self._focus_visible = event.reason() not in (
            Qt.FocusReason.MouseFocusReason,
            Qt.FocusReason.ActiveWindowFocusReason,
            Qt.FocusReason.PopupFocusReason,
            Qt.FocusReason.OtherFocusReason,
        )
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        self._focus_visible = False
        self.update()
        super().focusOutEvent(event)

    # ---- 绘制 ---------------------------------------------------------------

    def paintEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """预渲染的卡面 → 光带/扫描线/粒子 → 标题与副标题 → 高光 → 描边。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if not self.isEnabled():
            painter.setOpacity(_DISABLED_OPACITY)
        card = self._card_rect()
        if card.isEmpty():
            return
        silhouette = QPainterPath()
        silhouette.addRoundedRect(card, CARD_RADIUS, CARD_RADIUS)

        # 投影、卡面、柔光与角标不随鼠标或时间变化：预渲染成一张位图，逐帧只贴一次。
        painter.drawPixmap(QPointF(0.0, 0.0), self._card_face())

        painter.save()
        # ``overflow: hidden``：卡片里的装饰一律裁在圆角内。
        painter.setClipPath(silhouette)
        self._paint_cyber_lines(painter, card)
        self._paint_scan_line(painter, card)
        self._paint_particles(painter, card)
        self._paint_title(painter, card)
        self._paint_subtitle(painter, card)
        self._paint_glare(painter, card)
        painter.restore()

        painter.setPen(QPen(_EDGE, CARD_BORDER))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(silhouette)

        if self._focus_visible:
            ring = card.adjusted(-3.0, -3.0, 3.0, 3.0)
            painter.setPen(QPen(QColor(TOKENS["primary"]), 2.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(ring, CARD_RADIUS + 3.0, CARD_RADIUS + 3.0)

    def _card_face(self) -> QPixmap:
        """卡面那一叠的预渲染图：只在尺寸或屏幕像素比变化时重建。"""
        ratio = float(self.devicePixelRatioF())
        key = (self.width(), self.height(), round(ratio, 3))
        if self._face_key != key:
            self._face = self._render_face(ratio)
            self._face_key = key
        return self._face

    def _render_face(self, ratio: float) -> QPixmap:
        """投影、渐变卡面、提亮、柔光与四角角标：都不动，画进位图。"""
        size = QSize(
            max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio))
        )
        pixmap = QPixmap(size)
        pixmap.fill(Qt.transparent)
        # 带 dpr 的画布由 Qt 换算成逻辑坐标，这里不再自己缩放一次。
        pixmap.setDevicePixelRatio(ratio)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        card = self._card_rect()
        silhouette = QPainterPath()
        silhouette.addRoundedRect(card, CARD_RADIUS, CARD_RADIUS)
        self._paint_outer_shadow(painter, silhouette)
        painter.setClipPath(silhouette)
        self._paint_face(painter, card)
        self._paint_card_glow(painter, card)
        self._paint_blobs(painter, card)
        self._paint_corners(painter, card)
        painter.end()
        return pixmap

    def _paint_outer_shadow(self, painter: QPainter, silhouette: QPainterPath) -> None:
        """``box-shadow: 0 0 20px rgba(0, 0, 0, 0.3)``：几层由外向内收窄的深色描边。"""
        painter.setBrush(Qt.NoBrush)
        for step in range(_SHADOW_LAYERS):
            width = 2.0 + step * 3.0
            painter.setPen(QPen(_alpha(_BLACK, (26 - step * 5) / 255.0), width))
            painter.drawPath(silhouette)

    def _paint_face(self, painter: QPainter, card: QRectF) -> None:
        """45° 的近黑渐变卡面 + 四边内阴影 + 一层提亮（``filter: brightness(1.1)``）。"""
        gradient = _diagonal_gradient(card)
        gradient.setColorAt(0.0, _FACE_START)
        gradient.setColorAt(1.0, _FACE_END)
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawRect(card)

        # ``inset 0 0 20px rgba(0, 0, 0, 0.2)``：沿四边往里画几圈很淡的暗色。
        painter.setBrush(Qt.NoBrush)
        for step in range(4):
            inset = 1.0 + step * 4.0
            painter.setPen(QPen(_alpha(_BLACK, 10 / 255.0), 4.0))
            painter.drawRoundedRect(
                card.adjusted(inset, inset, -inset, -inset),
                max(1.0, CARD_RADIUS - inset),
                max(1.0, CARD_RADIUS - inset),
            )

        painter.setPen(Qt.NoPen)
        painter.setBrush(_alpha(_WHITE, _BRIGHTNESS_WASH))
        painter.drawRect(card)

    def _paint_card_glow(self, painter: QPainter, card: QRectF) -> None:
        """``#card::before``：压在卡面下面的一团青绿→天蓝柔光。"""
        center = card.center()
        reach = 0.75 * math.hypot(card.width(), card.height())
        gradient = QRadialGradient(center, reach)
        gradient.setColorAt(0.0, _alpha(_ACCENT, 0.08))
        gradient.setColorAt(0.5, _alpha(_ACCENT_BLUE, 0.04))
        gradient.setColorAt(1.0, _alpha(_ACCENT_BLUE, 0.0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(center, reach, reach)

    def _paint_blobs(self, painter: QPainter, card: QRectF) -> None:
        """三团光晕（``.glow-1..3``）：左上角外、右侧外、底部偏左。"""
        radius = _BLOB_SIZE / 2.0
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
            gradient.setColorAt(0.0, _alpha(_ACCENT, 0.3))
            gradient.setColorAt(0.7, _alpha(_ACCENT, 0.0))
            painter.setBrush(gradient)
            painter.drawEllipse(center, reach, reach)

    def _paint_corners(self, painter: QPainter, card: QRectF) -> None:
        """四角的直角角标：靛蓝描边 + 一圈外发光。"""
        color = _alpha(_INDIGO, 0.8)
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
        glow = _glow_pen(_alpha(_INDIGO, 0.35), 5.0)
        glow.setCapStyle(Qt.FlatCap)
        painter.setPen(glow)
        for start, end in strokes:
            painter.drawLine(QPointF(*start), QPointF(*end))
        solid = _glow_pen(color, CARD_BORDER)
        solid.setCapStyle(Qt.FlatCap)
        painter.setPen(solid)
        for start, end in strokes:
            painter.drawLine(QPointF(*start), QPointF(*end))

    def _paint_cyber_lines(self, painter: QPainter, card: QRectF) -> None:
        """四条横向光带：``scaleX`` 从中间向两端长出去再收回来（``lineGrow``）。"""
        painter.setPen(Qt.NoPen)
        period = _CYBER_PERIOD_MS / 1000.0
        for ratio, from_left, delay in _CYBER_LINES:
            # 每条光带都有自己的延迟：同一条 3s 时间轴错开起步。
            phase = ((self._ambient * period - delay) % period) / period
            grow = _triangle(phase)
            if grow <= 0.0:
                continue
            width = card.width() * grow
            y = card.top() + card.height() * ratio
            left = card.left() if from_left else card.right() - width
            gradient = QLinearGradient(left, y, left + width, y)
            gradient.setColorAt(0.0, _alpha(_INDIGO, 0.0))
            gradient.setColorAt(0.5, _alpha(_INDIGO, 0.2 * grow))
            gradient.setColorAt(1.0, _alpha(_INDIGO, 0.0))
            painter.setBrush(gradient)
            painter.drawRect(QRectF(left, y, width, 1.0))

    def _paint_scan_line(self, painter: QPainter, card: QRectF) -> None:
        """一条自上而下扫过的扫描线（``scanMove`` 2s 一轮）。"""
        phase = (self._ambient * _CYBER_PERIOD_MS / _SCAN_PERIOD_MS) % 1.0
        offset = (phase * 2.0 - 1.0) * card.height()
        band = QRectF(card.left(), card.top() + offset, card.width(), card.height())
        gradient = QLinearGradient(band.topLeft(), band.bottomLeft())
        gradient.setColorAt(0.0, _alpha(_INDIGO, 0.0))
        gradient.setColorAt(0.5, _alpha(_INDIGO, 0.1))
        gradient.setColorAt(1.0, _alpha(_INDIGO, 0.0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawRect(band)

    def _paint_particles(self, painter: QPainter, card: QRectF) -> None:
        """六个小亮点向外飘散：``0 → 1 → 0`` 的透明度，位移最远 30px。"""
        opacity = _triangle(self._particle)
        if opacity <= 0.0:
            return
        painter.setPen(Qt.NoPen)
        painter.setBrush(_alpha(_ACCENT, opacity))
        half = _PARTICLE_SIZE / 2.0
        travel = _PARTICLE_TRAVEL * self._particle
        for x_ratio, y_ratio, dx, dy in _PARTICLES:
            center = QPointF(
                card.left() + card.width() * x_ratio + half + dx * travel,
                card.top() + card.height() * y_ratio + half + dy * travel,
            )
            painter.drawEllipse(center, half, half)

    def _paint_title(self, painter: QPainter, card: QRectF) -> None:
        """卡片正中的渐变大字：字形墨迹盒的重心就是卡片中心，带一层青绿光晕。"""
        text = self.text() or CARD_TEXT
        path = _text_path(
            text, TITLE_PIXEL_SIZE, QFont.Weight.ExtraBold.value, TITLE_LETTER_SPACING
        )
        box = path.boundingRect()
        left = card.center().x() - box.center().x()
        top = card.center().y() - box.center().y()

        # ``drop-shadow(0 0 15px rgba(0, 255, 170, 0.3))``：预渲染好的光晕垫在字下面。
        painter.drawPixmap(
            QPointF(left - _HALO_PAD, top - _HALO_PAD), self._title_halo()
        )

        painter.save()
        painter.translate(left, top)
        gradient = _diagonal_gradient(box)
        gradient.setColorAt(0.0, _ACCENT)
        gradient.setColorAt(1.0, _ACCENT_BLUE)
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawPath(path)
        painter.restore()

    def _title_halo(self) -> QPixmap:
        """标题光晕的预渲染图：按文字与屏幕像素比缓存，尺寸变化时才重建。"""
        ratio = float(self.devicePixelRatioF())
        key = (self.text() or CARD_TEXT, round(ratio, 3))
        if self._halo_key != key:
            self._halo = _render_title_halo(key[0], key[1])
            self._halo_key = key
        return self._halo

    def _paint_subtitle(self, painter: QPainter, card: QRectF) -> None:
        """底边一行小字与它的高亮词：``bottom: 40px`` 再下移 30px。"""
        spacing = SUBTITLE_LETTER_SPACING
        plain = _card_font(SUBTITLE_PIXEL_SIZE, QFont.Weight.Normal.value, spacing)
        plain_path = _text_path(
            SUBTITLE_TEXT, SUBTITLE_PIXEL_SIZE, QFont.Weight.Normal.value, spacing
        )
        highlight_path = _text_path(
            SUBTITLE_HIGHLIGHT, SUBTITLE_PIXEL_SIZE, QFont.Weight.Bold.value, spacing
        )
        plain_width = QFontMetricsF(plain).horizontalAdvance(SUBTITLE_TEXT)
        highlight_width = QFontMetricsF(plain).horizontalAdvance(SUBTITLE_HIGHLIGHT)
        total = plain_width + HIGHLIGHT_GAP + highlight_width
        left = card.center().x() - total / 2.0
        top = card.bottom() - 40.0 + 30.0 - QFontMetricsF(plain).height()

        painter.save()
        painter.translate(left, top)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_SUBTITLE_COLOR)
        painter.drawPath(plain_path)
        painter.translate(plain_width + HIGHLIGHT_GAP, 0.0)
        gradient = _horizontal_gradient(highlight_path.boundingRect())
        gradient.setColorAt(0.0, _INDIGO)
        gradient.setColorAt(1.0, _VIOLET)
        painter.setBrush(gradient)
        painter.drawPath(highlight_path)
        painter.restore()

    def _paint_glare(self, painter: QPainter, card: QRectF) -> None:
        """斜向扫过的一道镜面高光（``linear-gradient(125deg, …)``）。"""
        radians = math.radians(125.0)
        dx, dy = math.sin(radians), -math.cos(radians)
        half = (abs(card.width() * dx) + abs(card.height() * dy)) / 2.0
        center = card.center()
        start = QPointF(center.x() - dx * half, center.y() - dy * half)
        end = QPointF(center.x() + dx * half, center.y() + dy * half)
        gradient = QLinearGradient(start, end)
        for position, alpha in (
            (0.00, 0.00),
            (0.45, 0.05),
            (0.50, 0.10),
            (0.55, 0.05),
            (1.00, 0.00),
        ):
            gradient.setColorAt(position, _alpha(_WHITE, alpha))
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawRect(card)
