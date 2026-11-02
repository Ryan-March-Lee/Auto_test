"""“查找设备”的卡片按钮：按钮本体就是一张与仪器卡片同形同大的深色卡片，正中一行渐变标题。

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

**按钮的形状大小就是卡片的形状大小**：控件固定为 :data:`CARD_WIDTH` × :data:`CARD_HEIGHT`、
圆角取 :data:`presentation.qt.theme.CARD_RADIUS`，这两份都不是本模块自己定的，而是与仪器
卡片（:mod:`presentation.qt.device_cards`）共用，因此“查找设备”按钮始终与结果区里的仪器
卡片同形同大；布局里的占位、可点区域与悬停触发
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

同一个模块里还有扫描期间的等待动画（:class:`ScanWaitStack`）：点下去之后按钮收走，
同一位置换成 7 张与按钮同尺寸的卡叠成的一叠（7 张只是动画，与这次查到几台设备无关）。
整叠牌以中间那张为准对称摊成一把扇形，再像捻牌一样从一侧到另一侧轻轻拨一遍，最后叠回
原样；停顿一下再来一轮。摊开时最上面那张与压在它右边的几张同步转开，左半边的牌由内到外
一张接一张跟上，叠回时反过来：扇形每一刻的比例都与摊满时一致，下层卡不会因为转得慢而
抢在最上面那张前面露出来。最上面那张是“查找设备”的完整卡面，下面几张都是牌背（渐变 + 斜纹
+ 内框 + 小菱形）、越往后压得越暗；每张卡边缘还有一道贴边的暗缝和一圈亮边，因此叠在一起
时读得出“这是一叠牌”，不会糊成一团。原来的等待动画是几张卡各绕自己的中心转满一整圈，
同时有 3、4 张卡转到不同角度，光晕与角标搅在一起——那是“乱”的来源。控件本身不吃鼠标、
不占焦点，也不改变版面。每走完一整轮（牌堆复原）它发出一次
:attr:`ScanWaitStack.cycle_finished`：设备发现面板据此保证“查找设备”至少完整播放一轮
动画，才把结果换进结果区；结果还没回来就继续下一轮。

等待动画收拢之后，结果卡片不是瞬间出现：:class:`DealRevealLayer` 从按钮牌堆原来的位置
往结果位一张张发牌（背面朝上，背面取按钮卡面的纹样，不带正中文字），全部落定后停一下，
再从左到右逐张翻开，把真正的仪器卡片露出来。落点就是卡片最终所在的位置：扫到几台仪器
就有几张卡片，发牌前结果区已经按这个数量排定版面，发牌与翻牌因此都落在同一处。它覆盖在
结果区上，只负责画面；面板把卡片先藏起来（仍占着版面），翻到哪张才恢复哪张。

**每帧只贴图，不重新描边**：投影、临边暗缝、卡面、压暗与亮边相对卡片都是静止的，只有整张
卡在动；每帧给 7 张卡各描两道抗锯齿圆角路径曾经是这条动画最大的一笔开销（高分屏上单帧
描边就超过一帧预算，动画因此发卡）。这些层现在按“第几张卡”预渲染成一整张图
（:func:`_render_wait_sprite`），每帧只把露得出来的那几张旋转着贴上去；完全叠在上一张下面、
一点也看不见的卡直接跳过。帧间隔因此从 30fps 提到约 60fps（:data:`WAIT_FRAME_MS`）。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

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
    Signal,
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

from .animation_diagnostics import log_event, timestamp, widget_label
from .device_cards import CARD_HEIGHT, CARD_WIDTH
from .theme import CARD_RADIUS, FONT_FAMILIES, TOKENS

__all__ = [
    "DealRevealLayer",
    "ScanPromptCard",
    "ScanWaitStack",
    "CARD_HEIGHT",
    "CARD_WIDTH",
    "BACKDROP_MARGIN_X",
    "BACKDROP_MARGIN_Y",
    "SCAN_CARD_HEADROOM",
    "WAIT_CYCLE_MS",
    "WAIT_STACK_CARDS",
    "WAIT_STACK_WIDTH",
    "WAIT_STACK_HEIGHT",
    "DEAL_FLY_MS",
    "DEAL_STAGGER_MS",
    "DEAL_SETTLE_MS",
    "FLIP_MS",
    "FLIP_STAGGER_MS",
]

# ---- 尺寸（原效果的 px 值） --------------------------------------------------

#: 卡片本体尺寸取自仪器卡片（:mod:`presentation.qt.device_cards`），圆角与仪器卡片共用
#: 一个值（:data:`presentation.qt.theme.CARD_RADIUS`）：按钮的形状、大小始终与仪器卡片
#: 一致（右下角那张装饰层不算在内），改仪器卡片就等于改这张按钮。

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

# ---- 等待动画 ----------------------------------------------------------------

#: 扫描期间那一叠卡的张数：只是动画，与这次真正查到几台设备无关。
WAIT_STACK_CARDS = 7
#: 扇形展开时相邻两张卡相差的角度（度）。7 张卡因此摊开约 ±15.6°，每张之间留得开，
#: 又不至于把两边的卡推出舞台。
WAIT_FAN_STEP_DEG = 5.2
#: 扇形的转轴在卡片中心正下方多远（px）：卡片绕它转，于是横向错开、上沿略低，
#: 像手里摊开的一把牌。转轴越远，同样的角度错得越多。
WAIT_FAN_PIVOT = 260.0
#: 相邻两张开始摊开的间隔：最上面那张所在的右半边与它同步转开，左半边的牌由内到外
#: 一张接一张跟上（叠回时反过来，由外向内先收）。右半边必须同步：它们压在最上面那张
#: 下面，转开的比例与摊满时一致，才不会有一张从它的右边或上边探出去。
WAIT_FAN_STAGGER_MS = 45
#: 整段摊开（或叠回）的时间；一张卡自己的时长，是它减去最靠外那张晚起步的错开量。
WAIT_FAN_MS = 520
#: 摊开之后停一下，让“扇形”这个形状被看清。
WAIT_HOLD_MS = 720
#: 停顿里那道“捻牌”的波纹：每张卡按顺序从扇面上抬起来一点再落回去，从一侧传到另一侧
#: （只上下抬 :data:`WAIT_RIPPLE_LIFT`，不改扇形角度，才不会撑出舞台）。
WAIT_RIPPLE_MS = 240
WAIT_RIPPLE_STAGGER_MS = 70
WAIT_RIPPLE_LIFT = 8.0
#: 叠回原样之后再停一下，然后开始下一轮。
WAIT_PAUSE_MS = 380
#: 一轮完整动画：摊开 → 停一下并捻过一遍 → 叠回 → 停一下。
WAIT_CYCLE_MS = (
    WAIT_FAN_MS + WAIT_HOLD_MS + WAIT_FAN_MS + WAIT_PAUSE_MS
)

# ---- 发牌与翻牌 --------------------------------------------------------------

#: 每张牌从按钮牌堆飞到结果位的时长。
DEAL_FLY_MS = 320
#: 相邻两张开始发牌的间隔：牌是一张张出去的，不是整把一起飞。
DEAL_STAGGER_MS = 70
#: 飞行弧线在起点与落点连线中点之上再抬多高（px）：从下方牌堆抛上来，有一点弧。
DEAL_ARC_LIFT = 36.0
#: 起飞时的缩放与角度：牌从牌堆里被抽出来时略小、略歪。
DEAL_START_SCALE = 0.84
DEAL_START_ANGLE = 7.0
#: 最后一张落定后停一下，再开始翻第一张。
DEAL_SETTLE_MS = 140
#: 一张牌翻面的时长与相邻两张开始翻的间隔。
FLIP_MS = 260
FLIP_STAGGER_MS = 105
#: 发牌动画的帧间隔（约 60fps）：每帧只旋转/缩放着贴图。
DEAL_FRAME_MS = 16
#: 发牌/翻牌阶段名：运行时日志与 :meth:`DealRevealLayer.diagnostics` 使用固定名称。
DEAL_PHASE_IDLE = "idle"
DEAL_PHASE_DEALING = "dealing"
DEAL_PHASE_SETTLING = "settling"
DEAL_PHASE_FLIPPING = "flipping"
DEAL_PHASE_FINISHED = "finished"
#: 等待动画的舞台：比卡片宽不少，整把扇形才摊得开。最外那张卡转到自己的角度时，
#: 轴对齐包围盒不能超出舞台（舞台之外会被结果区上沿裁掉）；各边再留一点给抗锯齿。
#: 高度按扇形竖直范围（约 ±168）收在 340：舞台下方多出来的空白越少，“正在查找设备”
#: 那行灰字离按钮越近，页面也给下方的结果卡片留出更多位置。
WAIT_STACK_WIDTH = 420
WAIT_STACK_HEIGHT = 340
#: 越靠后的卡压得越暗：一叠牌因此有前亮后暗的层次。
_WAIT_DIM = (0, 20, 36, 52, 66, 78, 90)
#: 卡与卡之间的投影（预渲染的那张图四边各留出的空白，以及向下偏移的像素）。
_WAIT_SHADOW_PAD = 12
_WAIT_SHADOW_DROP = 3
#: 整张卡的预渲染图（:func:`_render_wait_sprite`）四边各留多少：与投影那张图一致，
#: 暗缝、亮边与投影都画在里面，旋转着整张贴上去时不会被裁掉。
_WAIT_SPRITE_PAD = _WAIT_SHADOW_PAD
#: 等待动画里每张卡的“亮边 + 暗缝”：外圈一道贴边的暗缝负责把叠在一起的深色卡分开，
#: 里圈一道更亮的白边勾出卡片轮廓（沿轮廓往里收 :data:`_WAIT_RIM_INSET` 再描）。
#: 两道加起来不到 3px，不改变卡片的大小。
_WAIT_CREASE_WIDTH = 2.0
_WAIT_CREASE_ALPHA = 120
_WAIT_RIM_INSET = 1.0
_WAIT_EDGE_WIDTH = 1.4
_WAIT_EDGE_ALPHA = 0.30

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

#: 30fps：光带、扫描线与粒子一直在走（“查找设备”按钮的环境装饰）。
_FRAME_MS = 33
#: 等待动画的帧间隔（约 60fps）。静态层已经预渲染成一张图，每帧只剩旋转着贴，
#: 60fps 才吃得下；扫描等待时用户一直盯着这一叠牌，30fps 本身也读得出顿挫。
WAIT_FRAME_MS = 16
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


def _ease_in_out_cubic(phase: float) -> float:
    """``cubic-bezier`` 那一档缓入缓出：一张卡起步与收尾都放慢，转起来才不像急停。"""
    phase = max(0.0, min(1.0, phase))
    if phase < 0.5:
        return 4.0 * phase * phase * phase
    return 1.0 - ((-2.0 * phase + 2.0) ** 3) / 2.0


def _glow_pen(color: QColor, width: float) -> QPen:
    """圆头圆角的描边笔：文字外发光与角标光晕都用它，描边没有断口。"""
    pen = QPen(color, float(width))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


@dataclass(frozen=True)
class _CardPose:
    """等待动画里一张卡的位置：相对舞台中心的偏移，以及绕自己中心转过的角度（度）。"""

    x: float
    y: float
    angle: float

    def is_stacked(self) -> bool:
        """是否就是“叠在牌堆里”的那一格（扇形收拢、整叠复原）。"""
        return self.x == 0.0 and self.y == 0.0 and self.angle == 0.0


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


# ---- 卡面（不随时间变化的那一层）：按钮与等待动画里每一张卡共用 -------------------


def _paint_card_face(painter: QPainter, card: QRectF) -> None:
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


def _paint_card_glow(painter: QPainter, card: QRectF) -> None:
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


def _paint_card_blobs(painter: QPainter, card: QRectF) -> None:
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


def _paint_card_corners(painter: QPainter, card: QRectF) -> None:
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


def _paint_card_back(painter: QPainter, card: QRectF) -> None:
    """牌背：斜向细纹 + 一圈内框 + 正中一个小菱形，全是压得很低的装饰。

    等待动画里只有最上面那张是“查找设备”的卡面，下面几张都是牌背。牌背要能一眼看出
    是另一张牌，但又不能比正面还热闹：这里只给一层几乎看不见的斜纹、一道细内框和一个
    小菱形，叠成扇形时靠 :data:`_WAIT_DIM` 越压越暗。调用方负责先按圆角裁好。
    """
    painter.save()
    # 45° 斜纹：剪在卡片圆角里，间距 16px、白 7% 左右，只当纹理看。
    painter.setPen(QPen(QColor(255, 255, 255, 18), 1.0))
    step = 16.0
    height = card.height()
    x = card.left() - height
    while x < card.right() + height:
        painter.drawLine(QPointF(x, card.bottom()), QPointF(x + height, card.top()))
        x += step
    # 内框：与正面四角的角标呼应，是一条完整的细边。
    frame = card.adjusted(10.0, 10.0, -10.0, -10.0)
    painter.setPen(QPen(_alpha(_ACCENT_BLUE, 0.30), 1.2))
    painter.setBrush(Qt.NoBrush)
    radius = max(1.0, CARD_RADIUS - 6.0)
    painter.drawRoundedRect(frame, radius, radius)
    # 正中一个小菱形，给牌背一个可读的中心。
    centre = card.center()
    reach = 13.0
    diamond = QPainterPath()
    diamond.moveTo(centre.x(), centre.y() - reach)
    diamond.lineTo(centre.x() + reach, centre.y())
    diamond.lineTo(centre.x(), centre.y() + reach)
    diamond.lineTo(centre.x() - reach, centre.y())
    diamond.closeSubpath()
    painter.setPen(QPen(_alpha(_ACCENT, 0.45), 1.4))
    painter.drawPath(diamond)
    painter.setPen(Qt.NoPen)
    painter.setBrush(_alpha(_ACCENT, 0.55))
    painter.drawEllipse(centre, 2.2, 2.2)
    painter.restore()


def _paint_card_particles(painter: QPainter, card: QRectF, phase: float) -> None:
    """六个小亮点向外飘散：``0 → 1 → 0`` 的透明度，位移最远 30px。

    “查找设备”按钮与扫描期间摊开的牌堆共用这一份画法：粒子是卡面内部的动效，画在卡片
    自己的坐标系里。调用方负责先把坐标系放到卡片上、并按圆角裁好。
    """
    opacity = _triangle(phase)
    if opacity <= 0.0:
        return
    painter.setPen(Qt.NoPen)
    painter.setBrush(_alpha(_ACCENT, opacity))
    half = _PARTICLE_SIZE / 2.0
    travel = _PARTICLE_TRAVEL * phase
    for x_ratio, y_ratio, dx, dy in _PARTICLES:
        center = QPointF(
            card.left() + card.width() * x_ratio + half + dx * travel,
            card.top() + card.height() * y_ratio + half + dy * travel,
        )
        painter.drawEllipse(center, half, half)


@lru_cache(maxsize=4)
def _render_card_face(width: int, height: int, ratio: float) -> QPixmap:
    """卡面、柔光、光晕与四角角标的预渲染图：尺寸与屏幕像素比不变就整张复用。

    按钮本体与等待动画里的卡画的是同一张卡面，因此都从这里取图：摊开只是把这张
    位图平移、转一个角度，不必每帧重画卡面。
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
    _paint_card_face(painter, card)
    _paint_card_glow(painter, card)
    _paint_card_blobs(painter, card)
    _paint_card_corners(painter, card)
    painter.end()
    return pixmap


@lru_cache(maxsize=4)
def _render_card_back(width: int, height: int, ratio: float) -> QPixmap:
    """牌背的预渲染图：渐变卡面 + 斜纹、内框与小菱形，不带正面的光晕与角标。

    等待动画里压在最上面那张下面的卡只露出窄窄一条；如果它们也带着完整的卡面动效，
    7 张叠在一起时那些重复的光晕就会连成一片，看着像糊在一起。牌背只留卡片本身的
    形状、明暗与一层很安静的纹样，边缘由亮边和暗缝交代。
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
    _paint_card_face(painter, card)
    _paint_card_back(painter, card)
    painter.end()
    return pixmap


@lru_cache(maxsize=4)
def _render_card_shadow(width: int, height: int, ratio: float) -> QPixmap:
    """等待动画里卡与卡之间的那层投影：预渲染一次，每帧只平移着贴。

    投影本来就画在卡片外面，而舞台上的卡是自由摆放的，所以这张图比卡面四边各大
    :data:`_WAIT_SHADOW_PAD`。几层由内向外收窄的深色描边叠出一圈柔边（Qt 没有
    便宜的模糊），比逐帧现画省掉一大截时间。
    """
    pad = _WAIT_SHADOW_PAD
    size = QSize(
        max(1, round((width + 2 * pad) * ratio)),
        max(1, round((height + 2 * pad) * ratio)),
    )
    pixmap = QPixmap(size)
    pixmap.fill(Qt.transparent)
    # 带 dpr 的画布由 Qt 换算成逻辑坐标，这里不再自己缩放一次。
    pixmap.setDevicePixelRatio(ratio)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.translate(float(pad), float(pad))
    card = QRectF(0.0, 0.0, float(width), float(height))
    painter.setBrush(Qt.NoBrush)
    for step in range(_SHADOW_LAYERS):
        width_px = 3.0 + step * 3.5
        painter.setPen(QPen(QColor(0, 0, 0, 30 - step * 6), width_px))
        painter.drawRoundedRect(card, CARD_RADIUS, CARD_RADIUS)
    painter.end()
    return pixmap


@lru_cache(maxsize=16)
def _render_wait_sprite(index: int, width: int, height: int, ratio: float) -> QPixmap:
    """等待动画里第 ``index`` 张卡的整张图：投影、暗缝、卡面、压暗与亮边一次画好。

    这些层相对卡片都是静止的，动的只是整张卡。逐帧给 7 张卡各描两道抗锯齿圆角路径
    （临边暗缝与里圈亮边）是这条动画最大的一笔开销：高分屏上单帧就超过一帧预算，
    牌堆摊开时明显发卡。这里把它们与投影、卡面一起预渲染成一张位图，每帧只旋转着贴一次；
    第 0 张用正面卡面，其余都是牌背，并按 :data:`_WAIT_DIM` 逐张压暗。

    图比卡片四边各大 :data:`_WAIT_SPRITE_PAD`（下沿再多 :data:`_WAIT_SHADOW_DROP`）：
    投影与描边都伸出卡片轮廓之外，留白让它们整段落在这张图里。
    """
    pad = _WAIT_SPRITE_PAD
    drop = _WAIT_SHADOW_DROP
    size = QSize(
        max(1, round((width + 2 * pad) * ratio)),
        max(1, round((height + 2 * pad + drop) * ratio)),
    )
    pixmap = QPixmap(size)
    pixmap.fill(Qt.transparent)
    # 带 dpr 的画布由 Qt 换算成逻辑坐标，这里不再自己缩放一次。
    pixmap.setDevicePixelRatio(ratio)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    # 卡片在这张图里的位置：左右与上方是 pad，下方是 pad + drop。
    card = QRectF(float(pad), float(pad), float(width), float(height))
    painter.drawPixmap(
        QPointF(0.0, float(drop)), _render_card_shadow(width, height, ratio)
    )
    silhouette = QPainterPath()
    silhouette.addRoundedRect(card, CARD_RADIUS, CARD_RADIUS)
    # 贴边的一道暗缝：两张深色卡叠在一起时，这条缝把上面那张的轮廓压出来。
    painter.setPen(QPen(QColor(0, 0, 0, _WAIT_CREASE_ALPHA), _WAIT_CREASE_WIDTH))
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(silhouette)
    painter.drawPixmap(
        QPointF(card.left(), card.top()),
        _render_card_face(width, height, ratio)
        if index == 0
        else _render_card_back(width, height, ratio),
    )
    # 越靠后的卡压得越暗：一叠牌因此有前亮后暗的层次。
    dim = _WAIT_DIM[min(index, len(_WAIT_DIM) - 1)]
    if dim:
        painter.fillPath(silhouette, QColor(0, 0, 0, dim))
    # 里圈一道亮边：沿轮廓往里收 1px 再描一条细线，卡片边缘因此有一条清晰的亮线，
    # 不会和下面那张卡的黑边糊成一片。
    inset = _WAIT_RIM_INSET
    rim_path = QPainterPath()
    rim_path.addRoundedRect(
        card.adjusted(inset, inset, -inset, -inset),
        max(1.0, CARD_RADIUS - inset),
        max(1.0, CARD_RADIUS - inset),
    )
    painter.setPen(
        QPen(QColor(255, 255, 255, round(255 * _WAIT_EDGE_ALPHA)), _WAIT_EDGE_WIDTH)
    )
    painter.setBrush(Qt.NoBrush)
    painter.drawPath(rim_path)
    painter.end()
    return pixmap


@lru_cache(maxsize=4)
def _render_wait_sprites(
    width: int, height: int, ratio: float
) -> tuple[QPixmap, ...]:
    """一整叠牌的预渲染图（第 0 张在最上面）；尺寸与屏幕像素比不变就一直复用。"""
    return tuple(
        _render_wait_sprite(index, width, height, ratio)
        for index in range(WAIT_STACK_CARDS)
    )


@lru_cache(maxsize=8)
def _render_deal_back(width: int, height: int, ratio: float) -> QPixmap:
    """发牌时每张牌的背面：按钮那张卡面的纹样 + 描边 + 投影。

    背面不再另画一套图案：用户点下去时牌堆最上面那张就是“查找设备”的卡面，发出去的
    牌因此把这张卡面（不带正中文字）当作背面——牌是从同一副牌里发出来的。投影与描边
    和等待动画一样烘焙进这张图，发牌/翻牌每帧只缩放、旋转着贴。
    """
    pad = _WAIT_SHADOW_PAD
    drop = _WAIT_SHADOW_DROP
    size = QSize(
        max(1, round((width + 2 * pad) * ratio)),
        max(1, round((height + 2 * pad + drop) * ratio)),
    )
    pixmap = QPixmap(size)
    pixmap.fill(Qt.transparent)
    pixmap.setDevicePixelRatio(ratio)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.drawPixmap(
        QPointF(0.0, float(drop)), _render_card_shadow(width, height, ratio)
    )
    card = QRectF(float(pad), float(pad), float(width), float(height))
    painter.drawPixmap(card.topLeft(), _render_card_face(width, height, ratio))
    painter.setPen(QPen(_EDGE, CARD_EDGE_WIDTH))
    painter.setBrush(Qt.NoBrush)
    painter.drawRoundedRect(card, CARD_RADIUS, CARD_RADIUS)
    painter.end()
    return pixmap


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
        """渐变卡面、提亮、柔光与四角角标：都不动，整张预渲染（投影在装饰层上）。"""
        return _render_card_face(self.width(), self.height(), ratio)

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
        _paint_card_particles(painter, card, self._particle)

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


class ScanWaitStack(QWidget):
    """扫描进行中的等待动画：一叠 7 张卡摊成扇形、轻轻捻过一遍，再叠回原样。

    点“查找设备”之后，面板把按钮换成这个控件：7 张与按钮同尺寸的卡叠在一起（7 张只是
    动画，与这次真正查到几台设备无关）。整叠牌以中间那张为准对称摊成一把扇形
    （:data:`WAIT_FAN_STEP_DEG` / :data:`WAIT_FAN_PIVOT`），停一下，再从一侧到另一侧
    “捻”一遍（每张按顺序抬起来一点再落回去），最后叠回原样，停 :data:`WAIT_PAUSE_MS`
    再来一轮（:data:`WAIT_CYCLE_MS` 是这一整轮的时长）。摊开时最上面那张与压在它右边的
    几张同步转开，左半边的牌由内到外一张接一张跟上（叠回时反过来）：扇形每一刻的比例
    都与摊满时一致，下层卡不会因为转得慢而抢在最上面那张前面露出边角。

    动作是这样收敛的：同一时刻只有一两张卡在动；最上面那张是“查找设备”的卡面，下面
    几张都是牌背（:func:`_render_card_back`：渐变 + 斜纹 + 内框 + 小菱形），越靠后压得
    越暗（:data:`_WAIT_DIM`）；每张卡还带一道贴边的暗缝与一圈亮边
    （:data:`_WAIT_CREASE_ALPHA` / :data:`_WAIT_EDGE_ALPHA`），卡与卡之间因此有明确的
    轮廓，叠在一起时读得出“这是一叠牌”，不会糊成一片。原来的等待动画是几张卡各绕自己
    的中心转满一整圈，同时有 3、4 张卡转到不同角度，光晕与角标搅在一起——那是“乱”的
    来源。

    它只是动画：不吃鼠标（``WA_TransparentForMouseEvents``）、不占键盘焦点，也不改变
    版面。控件固定为 :data:`WAIT_STACK_WIDTH` × :data:`WAIT_STACK_HEIGHT` 的舞台（比
    卡片宽），扇形最外那张卡也落在自己的矩形里，不会被结果区上沿裁成一条硬边。卡面与
    按钮是同一张预渲染图（:func:`_render_card_face`）；整个“一张牌”的投影、暗缝、卡面、
    压暗与亮边则一起烘焙进 :func:`_render_wait_sprite` 的一张图里，每帧只旋转着贴一次
    （逐帧重描两道抗锯齿圆角路径在高分屏上会直接吃掉一帧预算），帧间隔约 60fps
    （:data:`WAIT_FRAME_MS`）；粒子只画在最上面那张卡上，不会让一叠卡各飘一团。

    每走完一整轮（7 张全部叠回原样）发出一次 :attr:`cycle_finished`：设备发现面板等到
    这一刻才把扫描结果换进结果区，因此一次查找至少完整播完一轮动画；结果还没回来就继续
    下一轮。牌的摊开与叠回中途从不被打断。
    """

    #: 一整轮动画走完（牌堆复原）时发出；面板据此决定什么时候进入下一步。
    cycle_finished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("scanWaitStack")
        # 这是动画，不是按钮：点击、悬停与键盘焦点都留在面板上。
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFixedSize(self.sizeHint())

        #: 本轮动画已经走到哪一刻（0..WAIT_CYCLE_MS），每帧按它算每张卡的位置。
        self._elapsed = 0.0
        #: 从显示到现在已经走完的整轮数：跨过一轮就发一次 ``cycle_finished``。
        self._completed_cycles = 0
        #: 粒子相位（0..1，2s 一轮）：与“查找设备”按钮同一条时间轴，只画在最上面那张卡上。
        self._particle = 0.0
        #: 整叠牌的预渲染图（第 0 张在最上面）：每帧只旋转着贴，不重描边。
        self._sprites: tuple[QPixmap, ...] = ()
        self._sprites_key: tuple[int, int, float] | None = None
        self._clock = QElapsedTimer()
        self._clock.start()

        self._frame_timer = QTimer(self)
        self._frame_timer.setInterval(WAIT_FRAME_MS)
        self._frame_timer.timeout.connect(self._advance)

    # ---- 尺寸 ---------------------------------------------------------------

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 覆盖方法
        """舞台正好装得下摊开的扇形：比卡片宽，卡片本身在舞台正中。"""
        return QSize(WAIT_STACK_WIDTH, WAIT_STACK_HEIGHT)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 覆盖方法
        return self.sizeHint()

    # ---- 时间轴 -------------------------------------------------------------

    def open_amounts_at(self, elapsed_ms: float) -> tuple[float, ...]:
        """这一刻每张卡各自摊开了多少（0 是叠在牌堆里，1 是扇形上自己的位置）。

        第 0 项是最上面那张（落笔最晚）。它和压在它下面的右半边几张一起转开：这几张
        本来就叠在它的边上，比例与摊满时一致，任何时刻都不会有一张从它的右边或上边
        探出去（那看起来就像下层卡压到了最上面那张）。正中间那张角度始终是 0，跟谁同步
        都一样；左半边的牌按离中间那张的远近依次错开，牌因此还是一张张摊开的。
        叠回时反过来：左半边由外向内先收，最上面那一组最后收起。整轮的最后一段是停顿，
        所有项都回到 0。
        """
        elapsed = float(elapsed_ms) % WAIT_CYCLE_MS
        centre_slot = (WAIT_STACK_CARDS - 1) // 2
        outer_count = WAIT_STACK_CARDS - 1 - centre_slot
        # 每张卡自己走完的时长一样长，只有起步时刻错开，牌才是一张张摊开、一张张收起的；
        # 最靠外那张晚起步多少，每张卡就走短多少，整段摊开 / 叠回仍正好是 WAIT_FAN_MS。
        span = WAIT_FAN_MS - outer_count * WAIT_FAN_STAGGER_MS
        close_start = WAIT_FAN_MS + WAIT_HOLD_MS
        amounts = []
        for index in range(WAIT_STACK_CARDS):
            # 右半边（含最上面那张与中间那张）的 rank 是 0，永远走在最前面；左半边的
            # 牌由内到外 rank 1、2、3，摊开时依次晚一点起步，叠回时依次早一点收。
            rank = max(0, index - centre_slot)
            open_delay = rank * WAIT_FAN_STAGGER_MS
            close_delay = (outer_count - rank) * WAIT_FAN_STAGGER_MS
            if elapsed < WAIT_FAN_MS:
                local = elapsed - open_delay
                amount = _ease_in_out_cubic(local / span) if local > 0.0 else 0.0
            elif elapsed < close_start:
                amount = 1.0
            elif elapsed < close_start + WAIT_FAN_MS:
                local = elapsed - close_start - close_delay
                amount = 1.0 if local <= 0.0 else 1.0 - _ease_in_out_cubic(local / span)
            else:
                amount = 0.0
            amounts.append(max(0.0, min(1.0, amount)))
        return tuple(amounts)

    def poses_at(self, elapsed_ms: float) -> tuple[_CardPose, ...]:
        """这一刻每张卡的位置与角度（第 0 项是最上面那张，落笔时压在其他卡上）。

        扇形以中间那张（角度始终为 0）为准对称摊开：卡片绕
        :data:`WAIT_FAN_PIVOT` 下方的转轴转，因此左右错开、上沿略低，像手里摊开的一把牌。
        停顿里的“捻牌”波纹只把卡向上抬一点，不改变扇形角度（再转就撑出舞台了）。
        """
        elapsed = float(elapsed_ms) % WAIT_CYCLE_MS
        poses = []
        # 整叠牌以中间那张为准对称摊开：扇形中心正对最上面那张的落点，两边各露出几张，
        # 不会整把牌都歪向一侧。
        centre_slot = (WAIT_STACK_CARDS - 1) / 2.0
        for index, amount in enumerate(self.open_amounts_at(elapsed)):
            angle = (centre_slot - index) * WAIT_FAN_STEP_DEG * amount
            radians = math.radians(angle)
            poses.append(
                _CardPose(
                    x=WAIT_FAN_PIVOT * math.sin(radians),
                    y=WAIT_FAN_PIVOT * (1.0 - math.cos(radians))
                    - self._ripple_lift(index, elapsed),
                    angle=angle,
                )
            )
        return tuple(poses)

    def _ripple_lift(self, index: int, elapsed: float) -> float:
        """这一步这张卡被“捻”起来多高（px）：0 是贴在扇面上。

        波纹在停顿里从一侧传到另一侧（索引 4 → 0）：每张卡抬起
        :data:`WAIT_RIPPLE_LIFT` 再落回去，一张接一张，像拇指从牌堆一侧拨过去。
        """
        start = WAIT_FAN_MS + (WAIT_STACK_CARDS - 1 - index) * WAIT_RIPPLE_STAGGER_MS
        local = (elapsed - start) / WAIT_RIPPLE_MS
        if local <= 0.0 or local >= 1.0:
            return 0.0
        return WAIT_RIPPLE_LIFT * math.sin(math.pi * local)

    @property
    def current_poses(self) -> tuple[_CardPose, ...]:
        """当前画在屏幕上的每张卡的位置与角度（第 0 项是最上面那张）。"""
        return self.poses_at(self._elapsed)

    def _advance(self) -> None:
        """推进到这一刻并重画；跨过一整轮就通知面板“牌堆复原了”。

        计时从显示那一刻开始累加，因此跨过的轮数按 ``WAIT_CYCLE_MS`` 整除算；停顿期间
        也在计时，跨过整轮边界时所有卡刚刚叠回原样（新的一轮从这一刻起步）。
        """
        total = self._clock.elapsed()
        self._elapsed = float(total % WAIT_CYCLE_MS)
        self._particle = (total / _PARTICLE_PERIOD_MS) % 1.0
        self.update()
        completed = int(total // WAIT_CYCLE_MS)
        if completed > self._completed_cycles:
            self._completed_cycles = completed
            self.cycle_finished.emit()

    def finish_cycle(self) -> None:
        """立刻把当前这一轮走完：牌堆复原，并发出 :attr:`cycle_finished`。

        动画本身按时间走；只有已经拿到结果、却还在等这一轮走完的调用方（测试）需要这
        个确定性的收尾：所有卡都叠回原样，计时从头开始。
        """
        self._elapsed = 0.0
        self._particle = 0.0
        self._completed_cycles = 0
        self._clock.start()
        self.update()
        self.cycle_finished.emit()

    def showEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        # 每次出现都从“一整叠”开始，再摊开。
        self._elapsed = 0.0
        self._particle = 0.0
        self._completed_cycles = 0
        self._clock.start()
        self._frame_timer.start()
        super().showEvent(event)

    def hideEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """收起来就停下计时器，并把位置归零：下一次查找重新从一整叠开始。"""
        self._frame_timer.stop()
        self._elapsed = 0.0
        self._particle = 0.0
        self._completed_cycles = 0
        super().hideEvent(event)

    # ---- 绘制 ---------------------------------------------------------------

    def paintEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """每帧只把预渲染好的整张卡旋转着贴上去；最上面那张最后落笔。

        卡上的投影、暗缝、压暗与亮边都不随动画变化，已经烘焙进
        :func:`_render_wait_sprite` 的一张图里；这里不再逐帧描圆角路径（那是原来单帧
        的主要开销）。完全叠在上方那张下面、一点也看不见的卡直接跳过，粒子只画在
        最上面那张卡上。
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        sprites = self._deck_sprites()
        centre = QPointF(self.width() / 2.0, self.height() / 2.0)
        half_width = float(CARD_WIDTH) / 2.0
        half_height = float(CARD_HEIGHT) / 2.0
        pad = float(_WAIT_SPRITE_PAD)
        poses = self.poses_at(self._elapsed)
        # 卡片按 0（最上面）到 6（最底下）叠放：第 i 张与第 i-1 张姿态完全相同时，
        # 它被上一张严丝合缝地盖住，画了也看不见。反过来从底下往上画，只画看得见的。
        visible = [
            index
            for index in range(WAIT_STACK_CARDS)
            if index == 0 or poses[index] != poses[index - 1]
        ]
        for index in reversed(visible):
            pose = poses[index]
            painter.save()
            painter.translate(centre + QPointF(pose.x, pose.y))
            painter.rotate(pose.angle)
            painter.drawPixmap(
                QPointF(-half_width - pad, -half_height - pad), sprites[index]
            )
            painter.restore()
        # 粒子只属于最上面那张卡：一叠卡各飘一团就又把画面弄花了。
        top = poses[0]
        painter.save()
        painter.translate(centre + QPointF(top.x, top.y))
        painter.rotate(top.angle)
        card = QRectF(-half_width, -half_height, float(CARD_WIDTH), float(CARD_HEIGHT))
        silhouette = QPainterPath()
        silhouette.addRoundedRect(card, CARD_RADIUS, CARD_RADIUS)
        painter.setClipPath(silhouette)
        _paint_card_particles(painter, card, self._particle)
        painter.restore()

    def _deck_sprites(self) -> tuple[QPixmap, ...]:
        """这一叠的预渲染图：尺寸或屏幕像素比变化时才重建。"""
        ratio = float(self.devicePixelRatioF())
        key = (int(CARD_WIDTH), int(CARD_HEIGHT), round(ratio, 3))
        if self._sprites_key != key:
            self._sprites = _render_wait_sprites(*key)
            self._sprites_key = key
        return self._sprites


def _ease_out_cubic(phase: float) -> float:
    """发牌飞行的缓动：起步快、落点慢，牌是“放”到结果位上的。"""
    phase = max(0.0, min(1.0, float(phase)))
    return 1.0 - (1.0 - phase) ** 3


class DealRevealLayer(QWidget):
    """发牌与翻牌：从“查找设备”牌堆往结果位发牌，背面朝上，再逐张翻开。

    扫描结果回来、等待动画走完一整轮之后，面板不直接把卡片摆出来，而是让这个覆盖层
    接住这一段：牌从按钮牌堆原来的位置一张张飞向结果区里的卡片位，飞行中背面朝上；
    所有牌都落定后停一下，再从左到右逐张翻面，把仪器卡片（正面）露出来。

    **背面就是按钮的卡面**（:func:`_render_deal_back` 用 :func:`_render_card_face`
    的纹样，不带正中文字）：牌是从按钮那一叠里发出来的，背面因此与牌堆最上面那张一致。
    正面由调用方在动画开始前截好（:class:`presentation.qt.device_cards.InstrumentCard`
    的整张图），翻到后半程时贴的是这张正面；翻完一张才把真正的卡片控件恢复显示，用户
    因此不会看到“卡片背后藏着一张已经能点的卡片”。

    它只是画面：不吃鼠标（``WA_TransparentForMouseEvents``）、不占焦点、不进布局；
    覆盖在结果区上，目标的矩形每帧从卡片控件现取，窗口缩放时也能跟到新位置。
    全部翻完后发出 :attr:`finished`，面板据此恢复状态行与卡片交互。
    """

    #: 所有牌都翻开、覆盖层收起时发出。
    finished = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("dealRevealLayer")
        # 动画不是控件：点击、悬停与键盘焦点都留给下面的卡片与按钮。
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        #: 本次要发的牌（按结果区里的显示顺序）与它们的正面截图。
        self._cards: list[QWidget] = []
        self._fronts: list[QPixmap] = []
        #: 已经翻开的牌：翻完就恢复真正的卡片控件，不再由覆盖层画。
        self._revealed: list[bool] = []
        #: 牌堆在覆盖层坐标里的中心（发牌起点）。
        self._deck = QPointF()
        #: 只让卡片画在卡片区视口里：结果多到要滚动时，看不见的行不会画到状态行/动作行上。
        self._clip_widget: QWidget | None = None
        self._elapsed = 0.0
        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(DEAL_FRAME_MS)
        self._timer.timeout.connect(self._advance)
        #: 运行时诊断（步骤 2）：开始/结束时间、阶段、绘制帧数与取消原因。
        #: 只写日志与 :meth:`diagnostics` 快照，不显示给最终用户。
        self._started_at: str | None = None
        self._ended_at: str | None = None
        self._phase = DEAL_PHASE_IDLE
        self._paint_frames = 0
        self._first_paint_ms: float | None = None
        self._drawn_cards = 0
        self._cancel_reason: str | None = None
        self._restore_result: str | None = None
        self._last_event = DEAL_PHASE_IDLE
        #: 最近一次动画收尾时的快照：取消/完成后卡片与时长会被清空，
        #: 快照保证 :meth:`diagnostics` 仍能给出同一时刻的完整数据。
        self._last_run: dict[str, Any] | None = None

    # ---- 时间轴 -------------------------------------------------------------

    def is_running(self) -> bool:
        """发牌/翻牌是否正在走。"""
        return self._timer.isActive()

    @property
    def revealed_count(self) -> int:
        """已经翻开的张数（测试与收尾用）。"""
        return sum(1 for revealed in self._revealed if revealed)

    def duration_ms(self) -> float:
        """这一次发牌 + 翻牌的整段时长；没有牌时为 0。"""
        count = len(self._cards)
        if count <= 0:
            return 0.0
        deal_end = (count - 1) * DEAL_STAGGER_MS + DEAL_FLY_MS
        return float(
            deal_end + DEAL_SETTLE_MS + (count - 1) * FLIP_STAGGER_MS + FLIP_MS
        )

    def phase_at(self, elapsed_ms: float | None = None) -> str:
        """按已走时长判断阶段：``dealing`` → ``settling`` → ``flipping`` → ``finished``。"""
        count = len(self._cards)
        if count <= 0:
            return DEAL_PHASE_IDLE
        elapsed = self._elapsed if elapsed_ms is None else float(elapsed_ms)
        deal_end = (count - 1) * DEAL_STAGGER_MS + DEAL_FLY_MS
        if elapsed < deal_end:
            return DEAL_PHASE_DEALING
        if elapsed < deal_end + DEAL_SETTLE_MS:
            return DEAL_PHASE_SETTLING
        if elapsed < self.duration_ms():
            return DEAL_PHASE_FLIPPING
        return DEAL_PHASE_FINISHED

    def diagnostics(self) -> dict[str, Any]:
        """最近一次动画的诊断快照（字段名见 :mod:`presentation.qt.animation_diagnostics`）。

        运行中返回当前值；结束或取消后返回那次动画收尾时的快照，因此 ``card_count``、
        ``elapsed_ms``、``phase`` 与 ``event=cancel`` / ``event=finish`` 是同一时刻的
        数据（不会出现“卡片已清空却还留着取消原因”的矛盾）。快照只给日志与测试看，
        不进入用户界面。
        """
        if self._last_run is not None:
            return dict(self._last_run)
        return self._live_snapshot()

    def _live_snapshot(self) -> dict[str, Any]:
        """当场读出运行中的诊断字段。"""
        return {
            "event": self._last_event,
            "running": self.is_running(),
            "phase": self.phase_at(),
            "started_at": self._started_at,
            "ended_at": self._ended_at,
            "elapsed_ms": float(self._elapsed),
            "duration_ms": self.duration_ms(),
            "card_count": len(self._cards),
            "deck_x": float(self._deck.x()),
            "deck_y": float(self._deck.y()),
            "target_rects": self._target_labels(),
            "overlay_visible": self.isVisible(),
            "overlay_width": int(self.width()),
            "overlay_height": int(self.height()),
            "overlay_parent": widget_label(self.parentWidget()),
            "paint_frames": self._paint_frames,
            "first_paint_ms": self._first_paint_ms,
            "drawn_cards": self._drawn_cards,
            "revealed_count": self.revealed_count,
            "cancel_reason": self._cancel_reason,
            "restore_result": self._restore_result,
        }

    def _capture_run_end(self, event: str) -> None:
        """在清空本次动画状态之前留下最终快照，供 :meth:`diagnostics` 读取。"""
        self._last_event = event
        snapshot = self._live_snapshot()
        snapshot["event"] = event
        self._last_run = snapshot

    def _record(self, event: str, **fields: Any) -> None:
        """更新内部快照并写一条固定字段的诊断日志。"""
        self._last_event = event
        log_event(event, **fields)

    def _target_labels(self) -> tuple[str, ...]:
        """每张卡的落点 ``(x,y,w,h)``，供日志与快照使用（不参与绘制）。"""
        labels = []
        for index in range(len(self._cards)):
            rect = self._target_rect(index)
            labels.append(
                f"({rect.x():.1f},{rect.y():.1f},"
                f"{rect.width():.1f},{rect.height():.1f})"
            )
        return tuple(labels)

    def _sync_phase(self) -> None:
        """阶段变了就记一条日志：``dealing`` / ``settling`` / ``flipping``。"""
        phase = self.phase_at()
        if phase == self._phase:
            return
        self._phase = phase
        self._record(
            "phase",
            phase=phase,
            elapsed_ms=float(self._elapsed),
            card_count=len(self._cards),
        )

    def _record_paint(self, drawn_cards: int) -> None:
        """每一帧真的进入绘制就计数；第一帧记 ``first_paint``。

        ``drawn_cards`` 是这一帧里实际画到可见区域（覆盖层与卡片裁剪区之内）的牌面数，
        不是“调用了绘制函数”的次数：没有它说明覆盖层没有画出来，它为 0 说明画了但
        画面是空的。
        """
        if self._phase in (DEAL_PHASE_IDLE, DEAL_PHASE_FINISHED):
            return
        self._paint_frames += 1
        if self._first_paint_ms is not None:
            return
        self._first_paint_ms = (
            float(self._clock.elapsed()) if self._clock.isValid() else 0.0
        )
        self._drawn_cards = int(drawn_cards)
        self._record(
            "first_paint",
            first_paint_ms=self._first_paint_ms,
            drawn_cards=self._drawn_cards,
            card_count=len(self._cards),
            overlay_visible=self.isVisible(),
            overlay_width=int(self.width()),
            overlay_height=int(self.height()),
            overlay_parent=widget_label(self.parentWidget()),
            phase=self._phase,
        )

    def deal_progress(self, index: int, elapsed_ms: float | None = None) -> float:
        """第 ``index`` 张牌飞到了哪里（0 刚离开牌堆，1 已落定）。"""
        if index < 0 or index >= len(self._cards):
            return 0.0
        elapsed = self._elapsed if elapsed_ms is None else float(elapsed_ms)
        local = elapsed - index * DEAL_STAGGER_MS
        return max(0.0, min(1.0, local / DEAL_FLY_MS))

    def flip_progress(self, index: int, elapsed_ms: float | None = None) -> float:
        """第 ``index`` 张牌翻到了哪里（0 还没动，0.5 侧对屏幕，1 已翻开）。"""
        if index < 0 or index >= len(self._cards):
            return 0.0
        elapsed = self._elapsed if elapsed_ms is None else float(elapsed_ms)
        count = len(self._cards)
        deal_end = (count - 1) * DEAL_STAGGER_MS + DEAL_FLY_MS
        start = deal_end + DEAL_SETTLE_MS + index * FLIP_STAGGER_MS
        return max(0.0, min(1.0, (elapsed - start) / FLIP_MS))

    def start(
        self,
        cards: Sequence[QWidget],
        fronts: Sequence[QPixmap],
        deck_origin: QPointF,
        clip_widget: QWidget | None = None,
    ) -> None:
        """从 ``deck_origin`` 开始发 ``cards``；``fronts`` 是每张卡的正面截图。

        ``clip_widget`` 是卡片区的可见视口：结果多到要滚动时，没滚到的行不该被画到
        状态行或动作行上。
        """
        if not cards:
            self.skip(reason="no-cards")
            return
        self._timer.stop()
        self._cards = list(cards)
        self._fronts = list(fronts)
        self._revealed = [False] * len(self._cards)
        self._deck = QPointF(deck_origin)
        self._clip_widget = clip_widget
        self._elapsed = 0.0
        self._started_at = None
        self._ended_at = None
        self._phase = DEAL_PHASE_IDLE
        self._paint_frames = 0
        self._first_paint_ms = None
        self._drawn_cards = 0
        self._cancel_reason = None
        self._restore_result = None
        self._last_run = None
        self._started_at = timestamp()
        self._clock.start()
        self.setVisible(True)
        self.raise_()
        self._phase = self.phase_at(0.0)
        self._record(
            "start",
            started_at=self._started_at,
            card_count=len(self._cards),
            duration_ms=self.duration_ms(),
            deck_x=float(self._deck.x()),
            deck_y=float(self._deck.y()),
            target_rects=self._target_labels(),
            overlay_visible=self.isVisible(),
            overlay_width=int(self.width()),
            overlay_height=int(self.height()),
            overlay_parent=widget_label(self.parentWidget()),
            phase=self._phase,
        )
        self.update()
        self._timer.start()

    def skip(self, *, reason: str, card_count: int = 0) -> None:
        """记录一次没有启动的动画：只写 ``skip`` 事件并收好覆盖层。

        ``reason`` 由调用方给出业务原因（例如 ``no-cards``）；这是“动画没有启动”
        的唯一记录点，面板不再自己拼一条日志。
        """
        self._timer.stop()
        self.hide()
        self._cards = []
        self._fronts = []
        self._revealed = []
        self._clip_widget = None
        self._elapsed = 0.0
        self._phase = DEAL_PHASE_IDLE
        self._paint_frames = 0
        self._first_paint_ms = None
        self._drawn_cards = 0
        self._started_at = None
        self._ended_at = None
        self._cancel_reason = None
        self._restore_result = None
        self._last_run = None
        self._record(
            "skip",
            skip_reason=reason,
            card_count=int(card_count),
            phase=DEAL_PHASE_IDLE,
            overlay_visible=self.isVisible(),
            overlay_width=int(self.width()),
            overlay_height=int(self.height()),
            overlay_parent=widget_label(self.parentWidget()),
        )

    def finish(self) -> None:
        """立刻走完这一段：所有牌翻开，收起覆盖层并发出 :attr:`finished`。"""
        if not self._cards:
            return
        self._complete()

    def cancel(self, *, reveal: bool = True, reason: str = "unspecified") -> None:
        """中途取消；``reason`` 写进诊断日志，``reveal=True`` 时把卡片恢复显示。

        已经自然播完的动画只做清理，不再记 ``cancel``：否则“播完后换一批结果”会被
        误读成一次中断，也会盖掉 ``finish`` 快照。
        """
        had_cards = bool(self._cards)
        was_running = self.is_running()
        interrupted = had_cards and self._phase != DEAL_PHASE_FINISHED
        if interrupted:
            self._cancel_reason = reason
            self._restore_result = "revealed" if reveal else "hidden"
            self._ended_at = timestamp()
        self._stop()
        if reveal:
            self._reveal_all()
        if interrupted:
            self._record(
                "cancel",
                started_at=self._started_at,
                ended_at=self._ended_at,
                elapsed_ms=float(self._elapsed),
                duration_ms=self.duration_ms(),
                card_count=len(self._cards),
                revealed_count=self.revealed_count,
                paint_frames=self._paint_frames,
                first_paint_ms=self._first_paint_ms,
                cancel_reason=self._cancel_reason,
                restore_result=self._restore_result,
                was_running=was_running,
                phase=self.phase_at(),
            )
            # 取消后卡片与时长会被清空：先把同一时刻的完整快照留下来。
            self._capture_run_end("cancel")
        self._cards = []
        self._fronts = []
        self._revealed = []
        self._clip_widget = None
        self._elapsed = 0.0
        self._phase = DEAL_PHASE_IDLE

    def _stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _complete(self) -> None:
        """自然走完或 ``finish()`` 的收尾：恢复卡片、记录结束诊断并通知面板。"""
        self._elapsed = self.duration_ms()
        self._reveal_all()
        self._stop()
        self._phase = DEAL_PHASE_FINISHED
        self._ended_at = timestamp()
        self._record(
            "finish",
            started_at=self._started_at,
            ended_at=self._ended_at,
            elapsed_ms=float(self._elapsed),
            duration_ms=self.duration_ms(),
            card_count=len(self._cards),
            revealed_count=self.revealed_count,
            paint_frames=self._paint_frames,
            first_paint_ms=self._first_paint_ms,
            phase=self._phase,
        )
        self._capture_run_end("finish")
        self.finished.emit()

    def _advance(self) -> None:
        total = self.duration_ms()
        self._elapsed = float(min(total, self._clock.elapsed()))
        self._sync_phase()
        for index in range(len(self._cards)):
            if not self._revealed[index] and self.flip_progress(index) >= 1.0:
                self._reveal(index)
        self.update()
        if self._elapsed >= total:
            self._complete()

    def _reveal(self, index: int) -> None:
        """翻完一张：撤掉透明度效果，让真正的卡片控件露出来。"""
        if index < 0 or index >= len(self._cards) or self._revealed[index]:
            return
        self._revealed[index] = True
        self._cards[index].setGraphicsEffect(None)

    def _reveal_all(self) -> None:
        for index in range(len(self._cards)):
            self._reveal(index)
        self.update()

    # ---- 几何 ---------------------------------------------------------------

    def _target_rect(self, index: int) -> QRectF:
        """第 ``index`` 张卡在覆盖层里的落点：每帧从卡片控件现取。"""
        card = self._cards[index]
        origin = card.mapTo(self, card.rect().topLeft())
        return QRectF(
            float(origin.x()),
            float(origin.y()),
            float(card.width()),
            float(card.height()),
        )

    def _flight_center(self, index: int, progress: float) -> QPointF:
        """飞行中的牌中心：从牌堆到落点的一条向上拱起的二次曲线。"""
        target = self._target_rect(index).center()
        start = self._deck
        control = QPointF(
            (start.x() + target.x()) / 2.0,
            min(start.y(), target.y()) - DEAL_ARC_LIFT,
        )
        eased = _ease_out_cubic(progress)
        inverse = 1.0 - eased
        return QPointF(
            start.x() * inverse * inverse
            + control.x() * 2.0 * inverse * eased
            + target.x() * eased * eased,
            start.y() * inverse * inverse
            + control.y() * 2.0 * inverse * eased
            + target.y() * eased * eased,
        )

    # ---- 绘制 ---------------------------------------------------------------

    def _deal_back(self) -> QPixmap:
        """本次发牌共用的背面图：尺寸与屏幕像素比不变就一直复用。"""
        ratio = float(self.devicePixelRatioF())
        return _render_deal_back(int(CARD_WIDTH), int(CARD_HEIGHT), ratio)

    def _draw_back(self, painter: QPainter, rect: QRectF, angle: float = 0.0) -> bool:
        """把背面图按 ``rect`` 缩放、旋转着贴上去（投影已在图里）。

        返回是否真的画到了可见区域：``False`` 表示这一笔要么没有贴图，要么整块落在
        覆盖层/裁剪区之外，不能算作“卡片已画出”。
        """
        sprite = self._deal_back()
        if sprite.isNull() or not self._is_visible_draw(painter, rect):
            return False
        painter.save()
        painter.translate(rect.center())
        painter.rotate(float(angle))
        painter.scale(rect.width() / CARD_WIDTH, rect.height() / CARD_HEIGHT)
        painter.translate(-CARD_WIDTH / 2.0, -CARD_HEIGHT / 2.0)
        painter.drawPixmap(
            QPointF(-float(_WAIT_SHADOW_PAD), -float(_WAIT_SHADOW_PAD)), sprite
        )
        painter.restore()
        return True

    def _draw_front(self, painter: QPainter, rect: QRectF, index: int) -> bool:
        """把卡片控件的截图按 ``rect`` 缩放贴上去，并在下面补一层同样的投影。"""
        if index < 0 or index >= len(self._fronts):
            return False
        front = self._fronts[index]
        if front.isNull() or not self._is_visible_draw(painter, rect):
            return False
        ratio = float(self.devicePixelRatioF())
        pad = float(_WAIT_SHADOW_PAD)
        shadow = _render_card_shadow(int(CARD_WIDTH), int(CARD_HEIGHT), ratio)
        # 投影与背面共用同一张预渲染图：翻面时只缩放它，不按每一帧的新宽度重画。
        painter.save()
        painter.translate(rect.center())
        painter.scale(rect.width() / CARD_WIDTH, rect.height() / CARD_HEIGHT)
        painter.translate(-CARD_WIDTH / 2.0, -CARD_HEIGHT / 2.0)
        painter.drawPixmap(
            QPointF(-pad, -pad + _WAIT_SHADOW_DROP),
            shadow,
        )
        painter.restore()
        # 整张截图（物理像素）映射进落点矩形：不依赖 grab() 报告的设备像素比，
        # 高分屏上也不会因为 dpr 与逻辑尺寸对不上而把卡片缩小/放大。
        painter.drawPixmap(rect, front, QRectF(front.rect()))
        return True

    def _is_visible_draw(self, painter: QPainter, rect: QRectF) -> bool:
        """``rect``（含投影边距）是否与覆盖层当前的可见区域相交。"""
        if rect.width() <= 0.0 or rect.height() <= 0.0:
            return False
        pad = float(_WAIT_SHADOW_PAD)
        padded = rect.adjusted(-pad, -pad, pad, pad)
        visible = painter.clipBoundingRect()
        if visible.isNull():
            visible = QRectF(self.rect())
        return padded.intersects(visible)

    def paintEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """先画剩下的牌堆，再画飞出去的牌 / 落定的背面；翻到后半程贴正面。"""
        if not self._cards:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        count = len(self._cards)
        drawn = 0
        dealt = sum(
            1 for index in range(count) if self.deal_progress(index) > 0.0
        )
        remaining = count - dealt
        if remaining > 0:
            # 牌堆最多叠三张：发走一张，剩下的少一层，看得出“牌是从这叠里出去的”。
            for depth in range(min(3, remaining) - 1, -1, -1):
                rect = QRectF(
                    self._deck.x() - CARD_WIDTH / 2.0,
                    self._deck.y() - CARD_HEIGHT / 2.0 - depth * 2.0,
                    float(CARD_WIDTH),
                    float(CARD_HEIGHT),
                )
                if self._draw_back(painter, rect):
                    drawn += 1
        clip = self._cards_clip_rect()
        if clip is not None:
            painter.save()
            painter.setClipRect(clip, Qt.IntersectClip)
        for index in range(count):
            if self._revealed[index]:
                continue
            deal = self.deal_progress(index)
            if deal <= 0.0:
                continue
            target = self._target_rect(index)
            if deal < 1.0:
                eased = _ease_out_cubic(deal)
                scale = DEAL_START_SCALE + (1.0 - DEAL_START_SCALE) * eased
                angle = (
                    DEAL_START_ANGLE
                    * (1.0 - eased)
                    * (1.0 if index % 2 == 0 else -1.0)
                )
                width = target.width() * scale
                height = target.height() * scale
                center = self._flight_center(index, deal)
                rect = QRectF(
                    center.x() - width / 2.0,
                    center.y() - height / 2.0,
                    width,
                    height,
                )
                if self._draw_back(painter, rect, angle):
                    drawn += 1
                continue
            flip = self.flip_progress(index)
            if flip <= 0.0:
                painted = self._draw_back(painter, target)
            elif flip < 1.0:
                painted = self._paint_flip(painter, target, index, flip)
            else:
                # 翻完了但收尾信号还没到：直接贴正面，下一帧就会恢复真正的卡片。
                painted = self._draw_front(painter, target, index)
            if painted:
                drawn += 1
        if clip is not None:
            painter.restore()
        self._record_paint(drawn)

    def _cards_clip_rect(self) -> QRectF | None:
        """卡片区的可见矩形（覆盖层坐标）：看不见的行不画到状态行与动作行上。"""
        clip = self._clip_widget
        if clip is None or not clip.isVisible():
            return None
        origin = clip.mapTo(self, clip.rect().topLeft())
        return QRectF(
            float(origin.x()),
            float(origin.y()),
            float(clip.width()),
            float(clip.height()),
        )

    def _paint_flip(
        self, painter: QPainter, target: QRectF, index: int, progress: float
    ) -> bool:
        """绕竖直轴翻面：前半程背面收窄，后半程正面展开；返回是否画到可见区域。"""
        squeeze = abs(math.cos(math.pi * progress))
        squeeze = max(0.035, squeeze)
        grow = 1.0 + 0.04 * math.sin(math.pi * progress)
        width = target.width() * squeeze
        height = target.height() * grow
        rect = QRectF(
            target.center().x() - width / 2.0,
            target.center().y() - height / 2.0,
            width,
            height,
        )
        if progress < 0.5:
            painted = self._draw_back(painter, rect)
        else:
            painted = self._draw_front(painter, rect, index)
        shade = round(70 * math.sin(math.pi * progress))
        if shade:
            path = QPainterPath()
            radius = max(1.0, CARD_RADIUS * (rect.width() / CARD_WIDTH))
            path.addRoundedRect(rect, radius, radius)
            painter.fillPath(path, QColor(0, 0, 0, shade))
        return painted

