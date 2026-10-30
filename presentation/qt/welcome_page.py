"""启动欢迎页与测试模式选择。"""

from functools import lru_cache

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme import FONT_FAMILIES, TOKENS, WELCOME_BACKGROUND_FILE


@lru_cache(maxsize=1)
def _welcome_background() -> QPixmap:
    """欢迎页背景图；只解码一次，缩放交给绘制时完成。"""
    return QPixmap(str(WELCOME_BACKGROUND_FILE))


def apply_test_mode(config: dict, driver_enabled: bool) -> dict:
    """把欢迎页选择的测试模式写进配置副本的 ``driver_mode``。"""
    mode = config.get("driver_mode")
    mode = dict(mode) if isinstance(mode, dict) else {}
    mode["enabled"] = bool(driver_enabled)
    config["driver_mode"] = mode
    return config


def confirm_leave_configuration(parent, is_dirty: bool) -> bool:
    """返回欢迎页前的确认：只有配置被改动过才询问，避免丢失未保存的修改。"""
    if not is_dirty:
        return True
    reply = QMessageBox.question(
        parent,
        "返回欢迎页",
        "返回欢迎页将丢失当前未保存的配置，是否返回？",
        QMessageBox.Yes | QMessageBox.No,
    )
    return reply == QMessageBox.Yes


# ---- 随窗口缩放的版式基准 ----------------------------------------------------

#: 欢迎页按这个窗口尺寸设计排版，是 :data:`TITLE_POINT_SIZE` 等字号的基准：
#: 与主窗口的期望尺寸一致，大屏上全屏/最大化时正好是这个版面。
REFERENCE_SIZE = QSize(1600, 1000)
#: 缩放上下限：窗口化后字号不能小到读不清，超大屏上也不能把版心撑出画面。
SCALE_MIN = 0.6
SCALE_MAX = 1.5

#: 基准字号（缩放为 1 时的 pt 值）；实际字号 = 基准字号 × 当前缩放。
TITLE_POINT_SIZE = 56.0
SUBTITLE_POINT_SIZE = 13.0
MODE_BUTTON_POINT_SIZE = 13.0


# ---- 模式按钮（自绘 + 悬停动效） --------------------------------------------

#: 胶囊本体尺寸与内容间距；这些都是缩放为 1 时的值，绘制时乘以控件的当前缩放。
#: 阴影另占用控件四边留白，见 :data:`SHADOW_MARGIN`（留白固定，阴影不随字号变）。
PILL_HEIGHT = 52
PILL_PADDING_X = 26
CONTENT_GAP = 12
ARROW_SIZE = 32
#: 箭头字形（16x19 视图框里的描边箭头）在当前控件里的高度。
ARROW_GLYPH_HEIGHT = 15

#: 投影画在控件矩形内部：Qt 不会给控件留出画阴影的空间，画到外面会被裁掉。
SHADOW_MARGIN = 18
SHADOW_OFFSET = 5
SHADOW_SPREAD = 12
SHADOW_LAYERS = 10
SHADOW_ALPHA = 4

#: 两段时间轴：色块扫入 700ms（原效果的 ``duration-700``），前景文字与箭头 300ms
#: （``duration-300``，线性缓动）。
FILL_DURATION = 700
FOREGROUND_DURATION = 300

#: 胶囊底色：``bg-gray-50`` 加 ``backdrop-blur-md`` 的近似——半透明白，压得住照片又不死白。
_PILL_FACE = QColor("#F9FAFB")
_PILL_FACE.setAlpha(236)
#: ``border-2 border-gray-50``：白描边，色块扫过后留下的一圈白边就是它。
_PILL_BORDER = QColor("#F9FAFB")
#: 圆形填充色 ``before:bg-emerald-500``。
_ACCENT = QColor(TOKENS["accent"])
_ACCENT_PRESSED = QColor(TOKENS["accent_pressed"])
#: 文字 ``hover:text-gray-50``：静止时沿用主题的主色深调，悬停转白。
_TEXT_REST = QColor(TOKENS["primary_pressed"])
_TEXT_HOVER = QColor("#F9FAFB")
#: 箭头 ``border-gray-700`` / ``group-hover:bg-gray-50`` / ``fill-gray-800``。
_ARROW_BORDER = QColor("#374151")
_ARROW_FACE = QColor("#F9FAFB")
_ARROW_GLYPH = QColor("#1F2937")


def _with_alpha(color: QColor, ratio: float) -> QColor:
    """同色不同透明度：用于箭头描边与白底互相淡入淡出。"""
    faded = QColor(color)
    faded.setAlpha(round(color.alpha() * max(0.0, min(1.0, ratio))))
    return faded


@lru_cache(maxsize=8)
def _mode_button_font(point_size: float = MODE_BUTTON_POINT_SIZE) -> QFont:
    """模式按钮的文字字体：半粗，字号随窗口缩放（基准 13pt）。

    样式表给全局文字定的是 9.5pt，而这两个按钮是整页唯一的操作入口；字体在这里显式
    给出（并用于绘制与量宽），不再依赖样式表，于是按钮无论被谁 polish 都保持同一尺寸。
    """
    font = QFont()
    font.setFamilies(list(FONT_FAMILIES))
    font.setPointSizeF(point_size)
    font.setWeight(QFont.DemiBold)
    return font


@lru_cache(maxsize=1)
def _arrow_glyph() -> QPainterPath:
    """箭头的描边字形：一个上指的 V 加一条竖线，坐标取自原 SVG 的 16x19 视图框。

    原图是 2px 描边的 ``^`` 加竖杆（不是实心三角），因此这里只给路径，描边由画笔给出。
    """
    path = QPainterPath()
    path.moveTo(2.6, 7.6)
    path.lineTo(8.0, 2.2)
    path.lineTo(13.4, 7.6)
    path.moveTo(8.0, 2.2)
    path.lineTo(8.0, 16.8)
    return path


class ModeChoiceButton(QPushButton):
    """欢迎页的模式选择按钮：白色胶囊 + 从左侧涌入并放大的圆形色块。

    复现某按钮动效：静止时按钮是一枚白胶囊，右侧一个描边圆里放着 45° 的上箭头；悬停时
    与按钮等宽的圆形色块从左侧画布外滑入并放大到 1.5 倍，被胶囊的裁剪收成一块扫过去的
    色块（700ms）；文字随着色块扫过之处转白，箭头从 45° 转到 90°、描边圆淡成实心白圆
    （300ms，线性）。

    QSS 表达不了这段效果（伪元素、``transition`` 与 ``transform`` 都不在样式表能力里），
    所以按钮整块自绘：``paintEvent`` 依次画投影、胶囊底、圆形色块、白描边、文字与箭头，
    数值由 ``QVariantAnimation`` 推进。颜色仍取自 :data:`theme.TOKENS`，与全局主题同源。

    胶囊与字号按 :meth:`set_scale` 给出的比例整体缩放（由 :class:`WelcomePage` 按窗口
    尺寸算出），于是窗口化与全屏下按钮和标题的相对大小一致；四周画阴影的留白固定不变。
    """

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("welcomeChoice")
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        #: 当前缩放：1 表示按设计基准尺寸绘制。
        self._scale = 1.0
        self._font = _mode_button_font(MODE_BUTTON_POINT_SIZE)

        #: 色块扫入进度与前景（文字/箭头）进度，各自一条时间轴。
        self._fill = 0.0
        self._foreground = 0.0
        #: 焦点框只在键盘走到这里时画：鼠标点出来的焦点不该在胶囊外留一圈蓝线。
        self._focus_visible = False
        self._shadow_paths: list[QPainterPath] = []

        self._fill_animation = QVariantAnimation(self)
        self._fill_animation.setEasingCurve(self._sweep_curve())
        self._fill_animation.valueChanged.connect(self._set_fill)

        self._foreground_animation = QVariantAnimation(self)
        self._foreground_animation.setEasingCurve(QEasingCurve.Type.Linear)
        self._foreground_animation.valueChanged.connect(self._set_foreground)

    @staticmethod
    def _sweep_curve() -> QEasingCurve:
        """原效果里 ``transition-all`` 的默认缓动 ``cubic-bezier(0.4, 0, 0.2, 1)``。"""
        curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
        curve.addCubicBezierSegment(
            QPointF(0.4, 0.0), QPointF(0.2, 1.0), QPointF(1.0, 1.0)
        )
        return curve

    # ---- 尺寸 ---------------------------------------------------------------

    def set_scale(self, scale: float) -> None:
        """按窗口尺寸等比缩放胶囊与字号；留白固定，投影轮廓下次布局时重建。"""
        scale = max(0.1, float(scale))
        if abs(scale - self._scale) < 1e-3:
            return
        self._scale = scale
        self._font = _mode_button_font(MODE_BUTTON_POINT_SIZE * scale)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 覆盖方法
        content = (
            2.0 * PILL_PADDING_X * self._scale
            + self._text_width()
            + CONTENT_GAP * self._scale
            + ARROW_SIZE * self._scale
        )
        return QSize(
            int(content) + 2 * SHADOW_MARGIN,
            int(PILL_HEIGHT * self._scale) + 2 * SHADOW_MARGIN,
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt 覆盖方法
        return self.sizeHint()

    def _text_width(self) -> float:
        """文字宽度：按按钮自己的字体量，不取样式表覆盖过的控件字体。"""
        return QFontMetricsF(self._font, self).horizontalAdvance(self.text())

    def _pill_rect(self) -> QRectF:
        """胶囊本体在控件里的矩形：四边留出画投影的余地。"""
        return QRectF(self.rect()).adjusted(
            SHADOW_MARGIN, SHADOW_MARGIN, -SHADOW_MARGIN, -SHADOW_MARGIN
        )

    # ---- 动画 ---------------------------------------------------------------

    def _set_fill(self, value: float) -> None:
        self._fill = float(value)
        self.update()

    def _set_foreground(self, value: float) -> None:
        self._foreground = float(value)
        self.update()

    @staticmethod
    def _start(
        animation: QVariantAnimation, current: float, target: float, duration: int
    ) -> None:
        """从当前进度走到目标进度；剩余距离越短用时越短，中途折返不会重新走满。"""
        animation.stop()
        distance = abs(target - current)
        if distance < 1e-4:
            return
        animation.setDuration(max(1, round(duration * distance)))
        animation.setStartValue(current)
        animation.setEndValue(target)
        animation.start()

    def _animate_to(self, target: float) -> None:
        self._start(self._fill_animation, self._fill, target, FILL_DURATION)
        self._start(
            self._foreground_animation, self._foreground, target, FOREGROUND_DURATION
        )

    def enterEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        self._animate_to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        self._animate_to(0.0)
        super().leaveEvent(event)

    def focusInEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """只有 Tab/快捷键走过来的焦点才画焦点框（与网页的 ``:focus-visible`` 同理）。"""
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

    def hideEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """页面切走时立刻回到静止态，避免带着半块色块再回到欢迎页。"""
        self._fill_animation.stop()
        self._foreground_animation.stop()
        self._fill = 0.0
        self._foreground = 0.0
        super().hideEvent(event)

    # ---- 绘制 ---------------------------------------------------------------

    def resizeEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        super().resizeEvent(event)
        self._rebuild_shadow()

    def _rebuild_shadow(self) -> None:
        """预算投影轮廓：一串由外向内收窄的形状，绘制时按由外到内叠半透明色即可。

        只在尺寸变化时算一次，动画每帧只画几个现成的路径。
        """
        pill = self._pill_rect()
        self._shadow_paths = []
        if pill.isEmpty():
            return
        radius = pill.height() / 2.0
        silhouette = QPainterPath()
        silhouette.addRoundedRect(
            pill.translated(0.0, float(SHADOW_OFFSET)), radius, radius
        )
        for step in range(SHADOW_LAYERS):
            stroker = QPainterPathStroker()
            stroker.setWidth(2.0 * SHADOW_SPREAD * (step + 1) / SHADOW_LAYERS)
            grown = stroker.createStroke(silhouette).united(silhouette)
            self._shadow_paths.append(grown)

    def paintEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        """投影 → 胶囊底 → 扫入的圆形色块 → 白描边 → 文字与箭头。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        pill = self._pill_rect()
        radius = pill.height() / 2.0
        capsule = QPainterPath()
        capsule.addRoundedRect(pill, radius, radius)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(15, 23, 42, SHADOW_ALPHA))
        for silhouette in self._shadow_paths:
            painter.drawPath(silhouette)

        painter.setBrush(_PILL_FACE)
        painter.drawPath(capsule)

        if self._fill > 0.0:
            painter.save()
            painter.setClipPath(capsule)
            painter.setBrush(_ACCENT_PRESSED if self.isDown() else _ACCENT)
            painter.drawPath(self._sweep_circle(pill))
            painter.restore()

        painter.setPen(QPen(_PILL_BORDER, 2.0 * self._scale))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(capsule)

        self._paint_content(painter, pill, capsule)

        if self._focus_visible:
            ring = pill.adjusted(-3.0, -3.0, 3.0, 3.0)
            painter.setPen(QPen(QColor(TOKENS["primary"]), 2.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(ring, ring.height() / 2.0, ring.height() / 2.0)

    def _sweep_circle(self, pill: QRectF) -> QPainterPath:
        """色块当前占据的圆形区域：与按钮等宽，静止时整个停在左边界外。

        左移到位的位移与放大到 1.5 倍在同一段时间里完成，因此圆心与半径都随进度插值。
        """
        progress = self._fill
        center = QPointF(
            pill.left() - pill.width() / 2.0 + progress * pill.width(), pill.center().y()
        )
        reach = pill.width() * (1.0 + 0.5 * progress) / 2.0
        circle = QPainterPath()
        circle.addEllipse(center, reach, reach)
        return circle

    def _paint_content(
        self, painter: QPainter, pill: QRectF, capsule: QPainterPath
    ) -> None:
        """文字与箭头整组水平居中，按 ``gap`` 分开，与原效果的 ``justify-center gap-2`` 一致。"""
        text_width = self._text_width()
        gap = CONTENT_GAP * self._scale
        arrow_size = ARROW_SIZE * self._scale
        left = pill.center().x() - (text_width + gap + arrow_size) / 2.0
        # 量宽只给排版用，画字时右侧留一点余量，避免末字的右边距被矩形裁掉。
        text_rect = QRectF(left, pill.top(), text_width + 4.0, pill.height())
        flags = int(Qt.AlignLeft | Qt.AlignVCenter)

        painter.setFont(self._font)
        painter.setPen(_TEXT_REST)
        painter.drawText(text_rect, flags, self.text())
        if self._fill > 0.0:
            # 在色块扫过的地方把字重画成白色：字色始终跟着底色走，扫到哪儿白到哪儿，
            # 不会出现“白字压在还没扫到的白底上”的闪失。
            swept = self._sweep_circle(pill).intersected(capsule)
            painter.save()
            painter.setClipPath(swept)
            painter.setPen(_TEXT_HOVER)
            painter.drawText(text_rect, flags, self.text())
            painter.restore()

        arrow = QRectF(
            left + text_width + gap,
            pill.center().y() - arrow_size / 2.0,
            arrow_size,
            arrow_size,
        )
        self._paint_arrow(painter, arrow)

    def _paint_arrow(self, painter: QPainter, arrow: QRectF) -> None:
        """描边圆在悬停时淡出为白底，箭头同时从 45° 转到 90°。"""
        progress = self._foreground
        if progress > 0.0:
            painter.setPen(Qt.NoPen)
            painter.setBrush(_with_alpha(_ARROW_FACE, progress))
            painter.drawEllipse(arrow)
        if progress < 1.0:
            painter.setPen(
                QPen(_with_alpha(_ARROW_BORDER, 1.0 - progress), max(1.0, self._scale))
            )
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(arrow.adjusted(0.5, 0.5, -0.5, -0.5))

        painter.save()
        painter.translate(arrow.center())
        painter.rotate(45.0 + 45.0 * progress)
        scale = ARROW_GLYPH_HEIGHT * self._scale / 19.0
        painter.scale(scale, scale)
        painter.translate(-8.0, -9.5)
        pen = QPen(_ARROW_GLYPH, 2.0)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(_arrow_glyph())
        painter.restore()


class WelcomePage(QWidget):
    """每次启动首先展示的测试模式入口。

    整页只有背景图、标题与两个模式按钮：背景图由本页自绘铺满，标题与按钮摆在图中
    偏上的天空区域，避免压住下方的仪器场景；图上再叠一层自上而下变淡的黑色蒙版，
    让白字在明暗不定的照片底上依然醒目。两个按钮的悬停动效见
    :class:`ModeChoiceButton`。

    版式按 :data:`REFERENCE_SIZE` 设计，文字与按钮随窗口尺寸整体缩放（见
    :meth:`_layout_scale`）：全屏与窗口化都保持同一版面比例，窗口变小时字不会显得
    顶破画面，变大时也不会缩成一小团。
    """

    mode_selected = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("welcomePage")
        #: 当前缩放，由 :meth:`_apply_scale` 按窗口尺寸维护。
        self._scale = 0.0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 40)
        layout.setSpacing(0)
        layout.addStretch(2)

        title = QLabel("功放自动测试系统")
        title.setObjectName("welcomeTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("请选择本次测试模式")
        subtitle.setObjectName("welcomeSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addSpacing(6)
        layout.addWidget(subtitle)
        layout.addSpacing(28)

        no_driver = ModeChoiceButton("无驱放测试")
        with_driver = ModeChoiceButton("有驱放测试")
        no_driver.clicked.connect(lambda: self.mode_selected.emit(False))
        with_driver.clicked.connect(lambda: self.mode_selected.emit(True))

        buttons = QHBoxLayout()
        buttons.setSpacing(16)
        buttons.addStretch(1)
        buttons.addWidget(no_driver)
        buttons.addWidget(with_driver)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        layout.addStretch(5)

        self._title = title
        self._subtitle = subtitle
        self._buttons = (no_driver, with_driver)
        # 构造时窗口还没有实际尺寸，先按缩放 1 落一遍字号，避免露出样式表的通用字号。
        self._apply_scale(force=True)

    # ---- 随窗口缩放 ---------------------------------------------------------

    def _layout_scale(self) -> float:
        """当前窗口相对设计基准的缩放：取宽高两个比例中较小的一个，再夹到上下限内。"""
        if self.width() <= 0 or self.height() <= 0:
            return 1.0
        ratio = min(
            self.width() / max(1, REFERENCE_SIZE.width()),
            self.height() / max(1, REFERENCE_SIZE.height()),
        )
        return max(SCALE_MIN, min(SCALE_MAX, ratio))

    def _apply_scale(self, force: bool = False) -> None:
        """按当前窗口尺寸更新标题、副标题与模式按钮的字号（及按钮胶囊）。"""
        scale = self._layout_scale()
        if not force and abs(scale - self._scale) < 0.005:
            return
        self._scale = scale
        self._apply_font_size(self._title, TITLE_POINT_SIZE * scale)
        self._apply_font_size(self._subtitle, SUBTITLE_POINT_SIZE * scale)
        for button in self._buttons:
            button.set_scale(scale)

    @staticmethod
    def _apply_font_size(label: QLabel, point_size: float) -> None:
        """把字号写进控件自己的样式表。

        全局样式表里的 ``QWidget { font-size }`` 会盖掉 ``setFont``，而控件自己的
        样式表优先于应用样式表，因此这里是唯一能让字号随窗口变化、又不失其他规则的
        写法；``QLabel#welcomeTitle`` 里的颜色、字重与内边距仍然照旧生效。
        """
        label.setStyleSheet(f"font-size: {point_size:.1f}pt;")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_scale()

    def paintEvent(self, event):
        """背景图 + 覆盖其上的黑色渐变蒙版。

        样式表的 ``background-image`` 在这里不可靠：页面里放了按钮（子控件），且页面上方
        有 ``addStretch`` 时，Qt 只在按钮与边距附近画这层背景，中间大片区域退回窗口底色
        （实测如此，与布局参数组合有关）。自绘则不受布局结构影响。

        蒙版自上而下逐渐变淡（顶部最深），标题与按钮所在的偏上区域因此有足够对比度，
        下方的仪器与桌面保留原图亮度；子控件在这些之后再叠上去，不受蒙版影响。
        """
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().window())
        background = _welcome_background()
        if not background.isNull():
            painter.drawPixmap(self.rect(), background, background.rect())
        mask = QLinearGradient(0.0, 0.0, 0.0, float(self.height()))
        for stop, alpha in _MASK_STOPS:
            mask.setColorAt(stop, QColor(0, 0, 0, alpha))
        painter.fillRect(self.rect(), QBrush(mask))


#: 蒙版沿页面高度（相对位置, 黑色不透明度 0..255）：顶部最深，到底部完全透出原图。
_MASK_STOPS = ((0.0, 130), (0.28, 96), (0.55, 40), (0.78, 0), (1.0, 0))


__all__ = ["ModeChoiceButton", "WelcomePage"]
