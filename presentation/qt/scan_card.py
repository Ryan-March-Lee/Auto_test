"""“查找设备”的卡片按钮：按钮本体就是一张与仪器卡片同大的深色卡片，正中一行渐变标题。

卡面来自一段 HTML/CSS 的 Uiverse 卡片（原效果是 190×254 的竖卡），这里用 Qt 的技术栈复刻它的
**悬停动效**：

- 卡片背后只有一层投影（``box-shadow``）。原 CSS ``::after`` 的粉→青霓虹光斑**不要**：
  卡片外面不再有一圈柔光，悬停也不会让它变亮、加厚。
- 悬停时卡面上的两行字在 0.6s 里淡到看不见，缓动
  ``cubic-bezier(0.175, 0.885, 0.32, 1.275)``：冲过终点再弹回来。鼠标移开时同一条
  时间轴倒放回去（文字一起淡回来）。
- 原 CSS 在卡片底下还有一张紫→蓝渐变底卡（``::before``），这里**不要**：卡片外面只有
  投影，悬停也不会从卡片背后转出一张色卡来。
- 投影比卡片大一圈，伸出父控件上沿的那一段会被裁掉。结果区因此在卡片上方留了
  :data:`SCAN_CARD_HEADROOM` 的一圈空白（见 ``device_discovery_panel``），让投影落得下。

**按钮的大小就是卡片的大小**：控件固定为 :data:`CARD_WIDTH` × :data:`CARD_HEIGHT`，
这份尺寸不是本模块自己定的，而是取自仪器卡片（:mod:`presentation.qt.device_cards`），
因此“查找设备”按钮始终与结果区里的仪器卡片一样大；布局里的占位、可点区域与悬停触发
范围都只有那张卡片，四周没有一圈既是空地又吃悬停的留白。投影本来要画到卡片外面去，而 Qt
会把画到控件矩形外的像素裁掉：这层因此画在同级的一张透明覆盖层上
（:class:`_CardBackdrop`），它不进布局、不吃鼠标，只跟着卡片的位置走。

卡面本身一直停在“亮起”的状态：卡面提亮、青绿柔光、三团光晕、六个外飘的粒子、斜向高光、
四角角标都亮着；“查找设备”四个字以青绿→天蓝的渐变标题落在**卡片正中**，下面一行副标题
留在底边。卡面内部的环境动效照旧：四条横向光带（3s 一轮，各自错开）、自上而下扫过的扫描线
（2s 一轮）与粒子的外飘（2s 一轮）。

实现：**整块自绘**（``paintEvent`` 依次画卡面、光带/扫描线/粒子、标题与副标题、高光），
覆盖层自己画投影。不随时间变化的那些层（卡面、柔光、角标）预算成一张位图；悬停进度由
一条 ``QVariantAnimation`` 推进，环境动效由一个 30fps 的计时器推进。样式表表达不了伪元素、
``transition`` 与 ``transform``，因此本控件不着样式表：颜色与字号都在本模块给出（尺寸
取自仪器卡片），深色卡面 + 青绿/靛蓝/紫青配色是这段效果的一部分，不并入浅色主题的令牌；
焦点框仍取 :data:`theme.TOKENS` 的主色）。

它仍是 ``QPushButton`` 的子类：``clicked``、``setEnabled``、``setToolTip``、键盘
空格/回车与悬浮提示照旧，调用方不必知道它是自绘的。
"""

from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import (
    QEasingCurve,
    QElapsedTimer,
    QEvent,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
)
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
from PySide6.QtWidgets import (
    QPushButton,
    QSizePolicy,
    QWidget,
)

from .device_cards import CARD_HEIGHT, CARD_WIDTH
from .theme import FONT_FAMILIES, TOKENS

__all__ = [
    "ScanPromptCard",
    "CARD_HEIGHT",
    "CARD_WIDTH",
    "BACKDROP_MARGIN_X",
    "BACKDROP_MARGIN_Y",
    "SCAN_CARD_HEADROOM",
]

# ---- 尺寸（原效果的 px 值） --------------------------------------------------

#: 卡片本体尺寸取自仪器卡片（:mod:`presentation.qt.device_cards`）：按钮的大小与仪器
#: 卡片完全一致（右下角那张装饰层不算在内）。这里不再单独给一套尺寸，改仪器卡片的大小
#: 就等于改按钮的大小。
CARD_RADIUS = 20.0
#: 卡片外沿那道描边的宽度：深色卡面压在浅色页面背景上，原来是 2px，线太细、轮廓几乎看不出来，
#: 这里加粗到 3px（颜色不变，仍是那层很淡的白）。
CARD_EDGE_WIDTH = 3.0
#: 四角角标的笔画宽度（原效果的 ``.corner`` 是 2px）。
CORNER_STROKE = 2.0
#: 卡片上方要留的一圈空白（结果区的上沿会把画到外面的像素裁掉）。投影伸出卡片约 7px，
#: 必须整段落在这圈空白里；12px 让投影四边都落得下，结果区上沿不会把它裁成一条硬边。
SCAN_CARD_HEADROOM = 12
#: 卡片背后那张装饰层（:class:`_CardBackdrop`）比卡片四边各大多少：Qt 会把画到控件矩形外的
#: 像素裁掉，而投影必须画在卡片外面。投影最外一层描边伸出去约 7px，留 8px 就够。这层不是
#: 按钮的一部分：它不占版面、不吃鼠标，只跟着卡片的位置走。
BACKDROP_MARGIN_X = 8
BACKDROP_MARGIN_Y = 8

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
#: ``rgba(255, 255, 255, 0.1)`` 的卡面描边（宽度见 :data:`CARD_EDGE_WIDTH`）。
_EDGE = QColor(255, 255, 255, 26)
#: ``rgba(255, 255, 255, 0.6)`` 的副标题。
_SUBTITLE_COLOR = QColor(255, 255, 255, 153)
_ACCENT = QColor("#00FFAA")
_ACCENT_BLUE = QColor("#00A2FF")
_INDIGO = QColor("#5C67FF")
_VIOLET = QColor("#AD51FF")
_WHITE = QColor("#FFFFFF")
_BLACK = QColor("#000000")

# ---- 悬停动效 ----------------------------------------------------------------

#: ``transition: all 0.6s cubic-bezier(0.175, 0.885, 0.32, 1.275)``。
#: 两个方向都是整整 0.6s（中途折返也重走满），缓动冲过终点再弹回来。
HOVER_DURATION = 600
HOVER_CURVE = (0.175, 0.885, 0.32, 1.275)

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


def _hover_curve() -> QEasingCurve:
    """``cubic-bezier(0.175, 0.885, 0.32, 1.275)``：冲过终点再弹回来的一档缓动。"""
    x1, y1, x2, y2 = HOVER_CURVE
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0))
    return curve


class _CardBackdrop(QWidget):
    """卡片背后那一层装饰：投影。

    按钮本体只有卡片那么大（:data:`CARD_WIDTH` × :data:`CARD_HEIGHT`），而投影本来就露在
    卡片外面，Qt 却会把画到控件矩形外的像素裁掉；于是把它放到这张**同级的透明覆盖层**上：
    它比卡片四边各大 :data:`BACKDROP_MARGIN_X` / :data:`BACKDROP_MARGIN_Y`，不进任何布局、
    不吃鼠标（``WA_TransparentForMouseEvents``），位置与可见性由卡片在
    :meth:`ScanPromptCard._sync_backdrop` 里钉住（并用 ``stackUnder`` 压在卡片下面），
    启用状态也从卡片上读。因此它只是卡片的一层画布：用户点不到它，悬停也不会因为落在它
    上面而触发，页面里其他控件的层级也不因它而变。

    坐标与卡片共用父控件的坐标系，卡片在这张覆盖层里的矩形由 :meth:`_card_rect` 现算。
    """

    def __init__(self, card: "ScanPromptCard", parent: QWidget) -> None:
        super().__init__(parent)
        self._card = card
        self.setObjectName("scanCardBackdrop")
        # 它只是画布：点击、悬停与键盘焦点一概不要，全都留给卡片本体。
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)

    # ---- 坐标 ---------------------------------------------------------------

    def _card_rect(self) -> QRectF:
        """卡片本体在这张覆盖层里的矩形（两者共用一个父控件，坐标只差一层留白）。"""
        card = self._card
        offset = card.pos() - self.pos()
        return QRectF(
            float(offset.x()),
            float(offset.y()),
            float(card.width()),
            float(card.height()),
        )

    # ---- 绘制 ---------------------------------------------------------------

    def paintEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """投影压在卡片底下，只有露在卡片外面的部分看得见。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if not self._card.isEnabled():
            painter.setOpacity(_DISABLED_OPACITY)
        card = self._card_rect()
        if card.isEmpty():
            return
        silhouette = QPainterPath()
        silhouette.addRoundedRect(card, CARD_RADIUS, CARD_RADIUS)
        self._paint_outer_shadow(painter, silhouette)

    def _paint_outer_shadow(self, painter: QPainter, silhouette: QPainterPath) -> None:
        """``box-shadow: 0 0 20px rgba(0, 0, 0, 0.3)``：几层由外向内收窄的深色描边。"""
        painter.setBrush(Qt.NoBrush)
        for step in range(_SHADOW_LAYERS):
            width = 2.0 + step * 3.0
            painter.setPen(QPen(_alpha(_BLACK, (26 - step * 5) / 255.0), width))
            painter.drawPath(silhouette)


class ScanPromptCard(QPushButton):
    """“查找设备”按钮：一张一直亮着的深色卡片，正中是渐变标题；悬停时文字淡出。

    卡片自上而下读：正中一行 28px 的青绿→天蓝渐变标题（带一层青绿光晕），底边一行小字
    副标题，四角是靛蓝角标。卡片内部一直有动效：四条横向光带缓慢长出又收回、一条扫描带
    自上而下扫过、六个小亮点向外飘散；卡面还压着一层柔光与一道斜向高光。

    **悬停动效**：卡面上的两行字在 0.6s 里淡到看不见，鼠标移开时同一条时间轴倒放回去。
    原 CSS 的 ``::after``（卡片背后的霓虹光斑）与 ``::before``（紫→蓝渐变底卡）都不保留：
    卡片外面只有一层投影，悬停也不会让它变亮或长出一圈色卡。

    **尺寸就是卡片本身**：控件固定为 :data:`CARD_WIDTH` × :data:`CARD_HEIGHT`，这两个值
    取自仪器卡片（:mod:`presentation.qt.device_cards`），由 :meth:`sizeHint` 交给布局，
    因此它在页面里的占位、可点区域与悬停触发范围都只有那张卡片，四周再没有一圈吃版面又
    吃悬停的空地；仪器卡片改多大，这个按钮就跟到多大。要露在卡片外面的那层投影画在同级
    的透明覆盖层上（:class:`_CardBackdrop`），它不进布局、不吃鼠标，只跟着卡片的位置走；
    动效因此也不会改变版面。
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
        #: 悬停进度：0 是静止，1 是完全悬停（缓动过的值，中途会略微冲过头）。
        self._hover = 0.0
        #: 焦点框只在键盘走到这里时画：鼠标点出来的焦点不该在卡片外留一圈线。
        self._focus_visible = False
        #: 不随时间变化的那些层（卡面、柔光、角标）的预渲染图。
        self._face = QPixmap()
        self._face_key: tuple[int, int, float] | None = None
        #: 标题光晕的预渲染图（按文字与屏幕像素比缓存）。
        self._halo = QPixmap()
        self._halo_key: tuple[str, float] | None = None
        #: 卡片背后那层装饰（投影）：有父控件时才有，跟着卡片的位置走。
        self._backdrop: _CardBackdrop | None = None
        self._clock = QElapsedTimer()
        self._clock.start()
        self._last_tick = 0

        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(_FRAME_MS)
        self._frame_timer.timeout.connect(self._advance)

        #: 悬停那 0.6s 的缓动：两个方向都走满 ``HOVER_DURATION``（与 CSS 一致）。
        self._hover_animation = QVariantAnimation(self)
        self._hover_animation.setDuration(HOVER_DURATION)
        self._hover_animation.setEasingCurve(_hover_curve())
        self._hover_animation.valueChanged.connect(self._set_hover)

    # ---- 尺寸 ---------------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 覆盖方法
        """按钮的大小就是仪器卡片的大小：布局里占的就是那张卡片的尺寸。"""
        return QSize(int(CARD_WIDTH), int(CARD_HEIGHT))

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 覆盖方法
        return self.sizeHint()

    def _apply_fixed_size(self) -> None:
        """把卡片的尺寸钉死在设计尺寸上。

        样式表里的 ``QPushButton`` 规则（内边距与 ``min-height``）会给每个按钮补一条最小
        高度：卡片按钮落在别的控件（结果框里的按钮行、提示行）旁边时会被压扁成一行高。
        样式每次重新落一遍都会改回来，因此在 :meth:`changeEvent` 里再钉一次。
        """
        self.setFixedSize(self.sizeHint())

    def changeEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        if event.type() == QEvent.Type.StyleChange:
            self._apply_fixed_size()
            self._sync_backdrop()
        elif event.type() == QEvent.Type.EnabledChange:
            # 灰掉的卡片不该还停在悬停那一格：Qt 不给被禁用的控件送鼠标进出事件，
            # 鼠标正好停在上面时禁用它就收不到 ``leaveEvent``，这里替它回到静止。
            if not self.isEnabled():
                self._hover_animation.stop()
                self._hover = 0.0
            # 整块的明暗换了：背后那层装饰跟着一起淡。
            self._refresh_backdrop()
            self.update()
        super().changeEvent(event)

    def _card_rect(self) -> QRectF:
        """卡片本体在控件里的矩形：按钮就是卡片，所以是整块控件矩形。"""
        return QRectF(self.rect())

    # ---- 卡片背后的装饰层 ---------------------------------------------------

    def _sync_backdrop(self) -> None:
        """让背后那层装饰跟着卡片：换父控件时重建，随后把位置、大小与可见性钉住。

        装饰层与卡片同级（在同一个父控件里），所以两者的坐标系只差一层留白；卡片在布局里
        挪动或父控件重排（:meth:`moveEvent` / :meth:`resizeEvent`）时都要再对一次。
        """
        parent = self.parentWidget()
        if parent is None:
            self._drop_backdrop()
            return
        backdrop = self._backdrop
        if backdrop is None or backdrop.parentWidget() is not parent:
            self._drop_backdrop()
            backdrop = _CardBackdrop(self, parent)
            self._backdrop = backdrop
        backdrop.setGeometry(
            self.geometry().adjusted(
                -BACKDROP_MARGIN_X, -BACKDROP_MARGIN_Y, BACKDROP_MARGIN_X, BACKDROP_MARGIN_Y
            )
        )
        backdrop.setVisible(self.isVisible())
        # 装饰层压在卡片底下：两个同级控件谁在上面，靠这一句定下来。这里只把装饰层按下去，
        # 卡片自己不越到任何同级控件上面（``raise_()`` 会把卡片顶到后来出现的同级控件
        # 之上，装饰层的位置本来不该改变页面的层级）。
        backdrop.stackUnder(self)

    def _drop_backdrop(self) -> None:
        """扔掉背后那层装饰（换父控件、控件还没进界面时）。"""
        backdrop = self._backdrop
        if backdrop is None:
            return
        self._backdrop = None
        backdrop.hide()
        backdrop.deleteLater()

    def _refresh_backdrop(self) -> None:
        """装饰层重画：整块的明暗（启用状态）变了。"""
        backdrop = self._backdrop
        if backdrop is None:
            return
        backdrop.update()

    def moveEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        self._sync_backdrop()
        super().moveEvent(event)

    def resizeEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        self._sync_backdrop()
        super().resizeEvent(event)

    def event(self, event):  # noqa: N802 - Qt 覆盖方法
        if event.type() == QEvent.Type.ParentChange:
            # 换了父控件：装饰层要跟着搬过去（真正的几何在下一轮 move/resize 里对）。
            self._sync_backdrop()
        return super().event(event)

    @property
    def hover_progress(self) -> float:
        """悬停进度（0 静止、1 完全悬停）：卡面上的两行字按它算淡出。"""
        return self._hover

    # ---- 动画 ---------------------------------------------------------------

    def _advance(self) -> None:
        """推进光带、扫描线与粒子（悬停那 0.6s 由另一条动画自己走）。"""
        now = self._clock.elapsed()
        delta = max(0, min(now - self._last_tick, 100))
        self._last_tick = now
        self._ambient = (self._ambient + delta / _CYBER_PERIOD_MS) % 1.0
        self._particle = (self._particle + delta / _PARTICLE_PERIOD_MS) % 1.0
        self.update()

    def _set_hover(self, value: float) -> None:
        """悬停进度（0..1，缓动过的值）变了：卡面上的两行字按它算淡出，重画卡片。"""
        self._hover = float(value)
        self.update()

    def _animate_hover(self, target: float) -> None:
        """让悬停进度走到 ``target``：整个过渡 0.6s，中途折返也重走满 0.6s（与 CSS 一致）。"""
        animation = self._hover_animation
        animation.stop()
        if abs(target - self._hover) < 1e-3:
            self._set_hover(target)
            return
        animation.setStartValue(self._hover)
        animation.setEndValue(target)
        animation.start()

    def enterEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """鼠标进来：卡面上的两行字开始淡出。"""
        self._animate_hover(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """鼠标移开：同一条时间轴倒放回去，文字一起淡回来。"""
        self._animate_hover(0.0)
        super().leaveEvent(event)

    def showEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        # 样式表给 QPushButton 补的 min-height 会在显示时重新落一遍，尺寸要再钉一次。
        self._apply_fixed_size()
        self._last_tick = self._clock.elapsed()
        self._frame_timer.start()
        self._sync_backdrop()
        super().showEvent(event)

    def hideEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """收起时停下计时器：看不见的卡片不必重绘；悬停动效也直接落到终点。"""
        self._frame_timer.stop()
        self._hover_animation.stop()
        self._hover = 0.0
        # 背后那层装饰跟着收起来。
        self._sync_backdrop()
        self._refresh_backdrop()
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
        """预渲染的卡面 → 光带/扫描线/粒子 → 标题与副标题 → 高光 → 描边。

        投影要露在卡片外面，画在同级的装饰层上（:class:`_CardBackdrop`），不在这里：
        按钮本体只有卡片那么大。
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        if not self.isEnabled():
            painter.setOpacity(_DISABLED_OPACITY)
        card = self._card_rect()
        if card.isEmpty():
            return
        silhouette = QPainterPath()
        silhouette.addRoundedRect(card, CARD_RADIUS, CARD_RADIUS)

        # 卡面、柔光与角标不随时间变化：预渲染成一张位图，逐帧只贴一次。
        painter.drawPixmap(QPointF(0.0, 0.0), self._card_face())

        painter.save()
        # ``overflow: hidden``：卡片里的装饰一律裁在圆角内。
        painter.setClipPath(silhouette)
        self._paint_cyber_lines(painter, card)
        self._paint_scan_line(painter, card)
        self._paint_particles(painter, card)
        self._paint_text(painter, card)
        self._paint_glare(painter, card)
        painter.restore()

        painter.setPen(QPen(_EDGE, CARD_EDGE_WIDTH))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(silhouette)

        if self._focus_visible:
            # 焦点框画在卡片里侧：按钮就是卡片，没有外面那一圈可以落笔。
            ring = card.adjusted(1.5, 1.5, -1.5, -1.5)
            painter.setPen(QPen(QColor(TOKENS["primary"]), 2.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(ring, CARD_RADIUS - 1.5, CARD_RADIUS - 1.5)

    def _card_face(self) -> QPixmap:
        """卡面那一叠的预渲染图：只在尺寸或屏幕像素比变化时重建。"""
        ratio = float(self.devicePixelRatioF())
        key = (self.width(), self.height(), round(ratio, 3))
        if self._face_key != key:
            self._face = self._render_face(ratio)
            self._face_key = key
        return self._face

    def _render_face(self, ratio: float) -> QPixmap:
        """渐变卡面、提亮、柔光与四角角标：都不动，画进位图（投影在装饰层上）。"""
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
        painter.setClipPath(silhouette)
        self._paint_face(painter, card)
        self._paint_card_glow(painter, card)
        self._paint_blobs(painter, card)
        self._paint_corners(painter, card)
        painter.end()
        return pixmap

    def _text_fade(self) -> float:
        """悬停时两行字淡到看不见的程度：0 是完全不透明，1 是完全看不见。

        悬停那 0.6s 的缓动会冲过终点（``cubic-bezier(…, 1.275)``），因此进度先夹到 0..1
        再算淡出，字不会反着亮回来。
        """
        return max(0.0, min(1.0, self._hover))

    def _paint_text(self, painter: QPainter, card: QRectF) -> None:
        """卡片上的两行字：悬停时淡出（鼠标移开再一起淡回来）。"""
        # 灰掉的卡片整块已经淡过一档，这里只在当前不透明度上再乘一次淡出。
        opacity = painter.opacity() * (1.0 - self._text_fade())
        if opacity <= 0.0:
            return
        painter.save()
        painter.setOpacity(opacity)
        self._paint_title(painter, card)
        self._paint_subtitle(painter, card)
        painter.restore()

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
        """卡面自己的一团青绿→天蓝柔光（压深色卡面用的内发光，不是背后的霓虹光斑）。"""
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
        solid = _glow_pen(color, CORNER_STROKE)
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
