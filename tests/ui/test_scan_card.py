"""“查找设备”卡片按钮：按钮本体正好是那张卡片，鼠标进来时文字淡出。
 
原效果（一段 HTML/CSS 卡片）在样式表里表达不出来，因此按钮整块自绘：卡面一直亮着，正中
一行渐变标题，卡片内部的光带、扫描线与粒子自己在动；悬停动效复刻原 CSS 的 ``::after``
的**文字部分**——卡面上的两行字（含副标题里的渐变高亮词）在 0.6s 里淡到看不见
（``cubic-bezier(0.175, 0.885, 0.32, 1.275)``），但卡片外面的霓虹柔光已删除。原 CSS 的
``::before``（紫→蓝渐变底卡）同样不保留。要露在卡片外面的那层投影画在同级的一张透明
覆盖层上，所以按钮本身只有卡片那么大（尺寸与结果区里的仪器卡片相同）。这里验证：点击仍然
走 ``clicked``、按钮与卡片一样大、覆盖层不进布局也不吃鼠标、卡片外面没有渐变底卡也没有
霓虹柔光、悬停时文字全部消失、鼠标移开后回到原样、标题落在卡片正中，以及覆盖层跟着卡片
搬家（隐藏/显示、换父控件、重排）且不改变页面层级。
"""

import math
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QEnterEvent, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QVBoxLayout, QWidget  # noqa: E402


def _application():
    from presentation.qt.main_window import build_application

    # 走正式启动路径：主题（样式表、调色板、字体）必须与运行时有同一份。
    return build_application([])


def _blank_host() -> QWidget:
    """一块页面同色的底板：四边留给装饰层的宽度与 :func:`_scene` 里一样。"""
    from presentation.qt.scan_card import BACKDROP_MARGIN_X, BACKDROP_MARGIN_Y
    from presentation.qt.theme import TOKENS

    host = QWidget()
    host.setStyleSheet(f"background-color: {TOKENS['canvas']};")
    layout = QVBoxLayout(host)
    layout.setContentsMargins(
        BACKDROP_MARGIN_X + 20,
        BACKDROP_MARGIN_Y + 20,
        BACKDROP_MARGIN_X + 20,
        BACKDROP_MARGIN_Y + 20,
    )
    return host


def _scene():
    """把卡片放进一块页面同色的底板里，返回 ``(底板, 卡片)``。

    卡片背后那层装饰（投影）是卡片的**同级控件**，所以抓图要抓底板：它是底板的孩子，
    抓下来的画面里才连卡片外面那一圈投影一起有了。
    """
    from presentation.qt.scan_card import (
        BACKDROP_MARGIN_X,
        BACKDROP_MARGIN_Y,
        ScanPromptCard,
    )

    margin_x = BACKDROP_MARGIN_X + 20
    margin_y = BACKDROP_MARGIN_Y + 20
    card = ScanPromptCard("查找设备")
    host = _blank_host()
    host.layout().addWidget(card, 0, Qt.AlignHCenter)
    host.resize(card.width() + 2 * margin_x, card.height() + 2 * margin_y)
    host.show()
    _application().processEvents()
    return host, card


def _frame(host, card, hover: float):
    """这一帧的图像：先钉住相位与悬停进度，动效的进度才可比。"""
    card._hover_animation.stop()
    card._hover = hover
    card._ambient = 0.35
    card._particle = 0.4
    return host.grab().toImage()


def _still_frame(host, card, hover: float):
    """同样钉住动效，但把光带、扫描线与粒子停在看不见的位置：只留卡面上的字。"""
    card._hover_animation.stop()
    card._hover = hover
    card._ambient = 0.0
    card._particle = 0.0
    return host.grab().toImage()


def _point(host, image, x: float, y: float) -> QColor:
    """底板上某一点的颜色（坐标按逻辑像素给，抓图放大时按比例换算）。"""
    scale = image.width() / host.width()
    return QColor(image.pixel(int(x * scale), int(y * scale)))


def _card_origin(host, card) -> QPoint:
    """卡片左上角在底板里的位置。"""
    return card.mapTo(host, QPoint(0, 0))


def _card_pixel(host, image, card, x_ratio: float, y_ratio: float) -> QColor:
    """卡片本体上某点的颜色：比例相对卡片本体，与四周的装饰无关。"""
    origin = _card_origin(host, card)
    return _point(
        host,
        image,
        origin.x() + card.width() * x_ratio,
        origin.y() + card.height() * y_ratio,
    )


def _outside(host, image, card, offset: float) -> dict[str, QColor]:
    """卡片四边外面 ``offset`` px 的四个点：用来看卡片外面那圈装饰的颜色。"""
    origin = _card_origin(host, card)
    centre_x = origin.x() + card.width() / 2.0
    centre_y = origin.y() + card.height() / 2.0
    return {
        "left": _point(host, image, origin.x() - offset, centre_y),
        "right": _point(host, image, origin.x() + card.width() + offset, centre_y),
        "top": _point(host, image, centre_x, origin.y() - offset),
        "bottom": _point(host, image, centre_x, origin.y() + card.height() + offset),
    }


def _card_colours(host, image, card, inset: float = 8.0):
    """卡片本体上每个点实际画出来的颜色。

    ``inset`` 把四条边各让开一点：圆角之外的像素其实是页面底色（浅色），数“字有多少墨”
    时不该把它们算进来。
    """
    origin = _card_origin(host, card)
    scale = image.width() / host.width()
    left = int((origin.x() + inset) * scale)
    top = int((origin.y() + inset) * scale)
    width = int((card.width() - 2 * inset) * scale)
    height = int((card.height() - 2 * inset) * scale)
    for y in range(top, top + height):
        for x in range(left, left + width):
            yield QColor(image.pixel(x, y))


def _title_ink(host, image, card) -> int:
    """卡片上青绿亮部的点数：渐变标题（连它的青绿光晕）就是卡片上唯一这么绿的东西。"""
    return sum(1 for colour in _card_colours(host, image, card) if colour.green() > 170)


def _subtitle_ink(host, image, card) -> int:
    """卡片上“浅而不带色”的点数：底边那行小字 ``rgba(255, 255, 255, 0.6)`` 就是它。"""
    ink = 0
    for colour in _card_colours(host, image, card):
        chroma = max(colour.red(), colour.green(), colour.blue()) - min(
            colour.red(), colour.green(), colour.blue()
        )
        if colour.lightness() > 130 and chroma < 25:
            ink += 1
    return ink


def _subtitle_highlight_ink(host, image, card) -> int:
    """副标题里那个渐变高亮词的点数：靛蓝→紫的渐变，是卡片上唯一的红过绿的紫亮部。

    四角的角标也是靛蓝，但那个颜色（``#5C67FF``）红不过绿；高亮词偏紫的那一头
    （``#AD51FF``）红明显多过绿，所以「红 > 绿 + 30 且蓝很亮」只数得中这段字。
    """
    return sum(
        1
        for colour in _card_colours(host, image, card)
        if colour.red() > colour.green() + 30 and colour.blue() > 150
    )


def _backdrop_card_rect(card):
    """把覆盖层按一圈留白收回来的矩形：装饰层跟着卡片走时，它正好等于卡片的几何。"""
    from presentation.qt.scan_card import BACKDROP_MARGIN_X, BACKDROP_MARGIN_Y

    return card._backdrop.geometry().adjusted(
        BACKDROP_MARGIN_X,
        BACKDROP_MARGIN_Y,
        -BACKDROP_MARGIN_X,
        -BACKDROP_MARGIN_Y,
    )


class _Cover(QWidget):
    """一块不透明的色块：放在卡片上面，用来看同级控件谁压在谁上面。"""

    def paintEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#FF0000"))
        painter.end()


def _hover(card, around: QPointF) -> None:
    """把鼠标挪进卡片（``enterEvent``），让悬停动画自己跑。"""
    card.enterEvent(QEnterEvent(around, around, around))


def _leave(card) -> None:
    card.leaveEvent(QEvent(QEvent.Type.Leave))


class ScanPromptCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _application()

    def test_clicking_the_card_starts_a_scan(self):
        _host, card = _scene()
        clicks = []
        card.clicked.connect(lambda: clicks.append(True))
        QTest.mouseClick(card, Qt.LeftButton)
        self.assertEqual(clicks, [True])

    def test_the_button_is_exactly_the_card(self):
        """按钮的大小就是卡片的大小：四周没有一圈既吃版面又吃悬停的留白。"""
        from presentation.qt.scan_card import CARD_HEIGHT, CARD_WIDTH

        host, card = _scene()
        self.assertEqual(card.sizeHint().width(), CARD_WIDTH)
        self.assertEqual(card.sizeHint().height(), CARD_HEIGHT)
        self.assertEqual(card.width(), CARD_WIDTH)
        self.assertEqual(card.height(), CARD_HEIGHT)
        # 底板只给了卡片这么大一块，装饰层不占版面。
        self.assertEqual(card.geometry().size(), card.sizeHint())

    def test_the_button_size_always_follows_the_instrument_card(self):
        """尺寸只有一处定义：仪器卡片多大，“查找设备”按钮就多大。"""
        from presentation.qt.device_cards import CARD_HEIGHT as INSTRUMENT_CARD_HEIGHT
        from presentation.qt.device_cards import CARD_WIDTH as INSTRUMENT_CARD_WIDTH
        from presentation.qt.device_cards import InstrumentCard
        from presentation.qt.scan_card import CARD_HEIGHT, CARD_WIDTH, ScanPromptCard

        self.assertEqual(
            (CARD_WIDTH, CARD_HEIGHT),
            (INSTRUMENT_CARD_WIDTH, INSTRUMENT_CARD_HEIGHT),
            "查找设备按钮的尺寸应直接取仪器卡片的尺寸",
        )
        instrument = InstrumentCard("DP832A", "DP8B1519")
        button = ScanPromptCard("查找设备")
        self.assertEqual(button.size(), instrument.size(), "按钮要与仪器卡片一样大")

    def test_the_button_rounding_always_follows_the_instrument_card(self):
        """圆角也只有一处定义：仪器卡片样式表里的圆角就是按钮卡面的圆角。"""
        from presentation.qt.scan_card import CARD_RADIUS

        stylesheet = self.app.styleSheet()
        card_rule = stylesheet.split("QFrame#instrumentCard {", 1)[1].split("}", 1)[0]
        instrument_radius = card_rule.split("border-radius:", 1)[1].split(";", 1)[0].strip()
        self.assertEqual(
            instrument_radius,
            f"{CARD_RADIUS:g}px",
            "“查找设备”按钮与仪器卡片必须同一个圆角",
        )

    def test_the_decorations_live_on_a_mouse_transparent_layer_behind_the_card(self):
        """投影在同一父控件的覆盖层上：不进布局、不吃鼠标、只跟卡片走。"""
        host, card = _scene()
        backdrop = card._backdrop
        self.assertIsNotNone(backdrop, "卡片背后应有一层画投影的覆盖层")
        self.assertIs(backdrop.parentWidget(), host)
        self.assertTrue(
            backdrop.testAttribute(Qt.WA_TransparentForMouseEvents),
            "覆盖层不能吃鼠标：悬停与点击都属于卡片本体",
        )
        laid_out = {
            host.layout().itemAt(index).widget() for index in range(host.layout().count())
        }
        self.assertNotIn(backdrop, laid_out, "覆盖层不进布局，版面里只有卡片本身")
        self.assertEqual(
            _backdrop_card_rect(card),
            card.geometry(),
            "覆盖层只比卡片四边各大一圈留白",
        )

    def test_the_decorations_follow_the_card_through_hide_show_and_reflow(self):
        """卡片收起来装饰层一起收；再显示、再重排，装饰层都还套在卡片外面。"""
        host, card = _scene()

        card.hide()
        _application().processEvents()
        self.assertFalse(
            card._backdrop.isVisible(), "卡片收起来时，卡片外面那圈装饰也应一起收起来"
        )

        card.show()
        _application().processEvents()
        self.assertTrue(card._backdrop.isVisible(), "卡片重新显示时装饰也应回来")
        self.assertEqual(_backdrop_card_rect(card), card.geometry())

        # 重排：卡片在布局里挪了位置，装饰层要跟到新位置上。
        before = card.geometry()
        host.resize(host.width() + 40, host.height())
        _application().processEvents()
        self.assertNotEqual(card.geometry(), before, "底板变宽后卡片应换到新的位置")
        self.assertEqual(_backdrop_card_rect(card), card.geometry(), "装饰层应跟到新位置上")

    def test_the_decorations_move_to_the_new_parent_with_the_card(self):
        """换父控件：旧父控件上那层收起来，新的一层挂到新父控件上并套住卡片。"""
        _host, card = _scene()
        first = card._backdrop

        other = _blank_host()
        other.layout().addWidget(card, 0, Qt.AlignHCenter)
        # 换父控件当场就换画布，旧的那层先收起来。
        self.assertIsNot(card._backdrop, first, "换父控件后应是一层新的装饰")
        self.assertFalse(first.isVisible(), "旧父控件上那层装饰应随之收起来")

        other.resize(card.width() + 240, card.height() + 200)
        other.show()
        _application().processEvents()
        self.assertIs(card._backdrop.parentWidget(), other)
        self.assertTrue(card._backdrop.isVisible())
        self.assertEqual(_backdrop_card_rect(card), card.geometry(), "新位置上装饰层应套住卡片")

    def test_the_decorations_never_lift_the_card_above_its_siblings(self):
        """同步装饰层只把覆盖层按到卡片下面：卡片自己不越到后来出现的同级控件上面。"""
        host, card = _scene()
        cover = _Cover(host)
        origin = _card_origin(host, card)
        cover.setGeometry(origin.x() + 12, origin.y() + 12, 40, 40)
        cover.show()
        _application().processEvents()

        # 底板上重排一次：卡片自己被布局挪动，会走生产路径里的那次装饰同步。
        host.resize(host.width() + 40, host.height())
        _application().processEvents()

        image = host.grab().toImage()
        centre = cover.geometry().center()
        self.assertEqual(
            _point(host, image, centre.x(), centre.y()).name(),
            QColor("#FF0000").name(),
            "后来出现的同级控件仍应盖在卡片上面：同步装饰层不该把卡片顶上来",
        )
        # 卡片自己仍然压在自己的装饰层上（中点是卡面，不是投影）。
        self.assertLess(
            _card_pixel(host, image, card, 0.5, 0.5).lightness(),
            90,
            "卡片不应被自己的装饰层盖住",
        )

    def test_the_card_has_no_solid_gradient_card_behind_it(self):
        """卡片外面只有投影：原先那张紫→蓝渐变底卡已删除，悬停也不再长出一圈实色。"""
        # 采样点取在原底卡静止(5px)与悬停(11px)外扩之间：那时这里会是一圈鲜艳的渐变，
        # 现在只会是页面底色。
        offset = 8.0
        sides = {"左": "left", "右": "right", "上": "top", "下": "bottom"}
        host, card = _scene()
        for hover, label in ((0.0, "静止"), (1.0, "悬停")):
            colours = _outside(host, _frame(host, card, hover), card, offset)
            for name, side in sides.items():
                colour = colours[side]
                self.assertGreater(
                    colour.lightness(),
                    170,
                    f"{label}时卡片{name}侧不应再有一圈实色底卡，实际 {colour.name()}",
                )

    def test_hovering_takes_every_line_of_text_away(self):
        """悬停时卡片上的字全部淡到看不见：正中标题、底边副标题、以及副标题里的高亮词。"""
        host, card = _scene()
        rest = _still_frame(host, card, 0.0)
        turned = _still_frame(host, card, 1.0)

        self.assertGreater(_title_ink(host, rest, card), 0, "平时卡片正中应有渐变标题")
        self.assertGreater(_subtitle_ink(host, rest, card), 0, "平时底边应有副标题")
        self.assertGreater(
            _subtitle_highlight_ink(host, rest, card), 0, "平时副标题里应有渐变高亮词"
        )
        self.assertEqual(_title_ink(host, turned, card), 0, "悬停时标题应淡到看不见")
        self.assertEqual(_subtitle_ink(host, turned, card), 0, "悬停时副标题应淡到看不见")
        self.assertEqual(
            _subtitle_highlight_ink(host, turned, card),
            0,
            "悬停时副标题里的高亮词也应淡到看不见",
        )

    def test_mouse_in_and_out_replays_the_transition(self):
        """鼠标进来文字收走，移开又回来；两端画面与静止/悬停态完全一致。"""
        host, card = _scene()
        centre = QPointF(card.width() / 2.0, card.height() / 2.0)
        rest = _frame(host, card, 0.0)
        turned = _frame(host, card, 1.0)

        _hover(card, centre)
        QTest.qWait(300)
        self.assertGreater(
            card._hover, 0.6, f"0.3s 时缓冲曲线应已冲过一大半，实际 {card._hover:.2f}"
        )
        QTest.qWait(500)
        self.assertAlmostEqual(card._hover, 1.0, places=3, msg="悬停结束应停在终点")
        card._ambient = 0.35
        card._particle = 0.4
        self.assertEqual(host.grab().toImage(), turned, "悬停之后就是文字收走的画面")

        _leave(card)
        QTest.qWait(800)
        self.assertAlmostEqual(card._hover, 0.0, places=3, msg="鼠标移开后应回到静止")
        card._ambient = 0.35
        card._particle = 0.4
        self.assertEqual(host.grab().toImage(), rest, "移开鼠标后应与悬停前一样")

    def test_disabling_the_card_gives_the_hover_look_back_up(self):
        """鼠标停着的时候禁用卡片：Qt 不再给它送鼠标事件，卡片自己回到静止那一格。

        被禁用的控件收不到 ``leaveEvent``，所以“悬停到一半被禁用”必须由卡片自己收尾，
        否则卡面会一直停在悬停那一格、字也一直收着。
        """
        host, card = _scene()
        _hover(card, QPointF(card.width() / 2.0, card.height() / 2.0))
        QTest.qWait(700)
        self.assertAlmostEqual(card.hover_progress, 1.0, places=3, msg="先让它悬停到底")

        card.setEnabled(False)
        _application().processEvents()
        self.assertEqual(card.hover_progress, 0.0, "禁用后不该还停在悬停那一格")

        card.setEnabled(True)
        _application().processEvents()
        self.assertGreater(
            _title_ink(host, _still_frame(host, card, 0.0), card),
            0,
            "重新启用后文字应回来",
        )

    def test_the_card_is_lit_from_the_start(self):
        """初始就是亮起的样子：卡面是深的，内部有青绿内发光与光晕。"""
        host, card = _scene()
        image = _frame(host, card, 0.0)
        face = _card_pixel(host, image, card, 0.85, 0.85)
        self.assertLess(face.lightness(), 90, f"卡面应是深色，实际 {face.name()}")
        inner = _card_pixel(host, image, card, 0.5, 0.35)
        self.assertGreater(
            inner.green(), inner.red() + 8, f"卡面的青绿内发光应一直在，实际 {inner.name()}"
        )

    def test_the_title_sits_in_the_middle_of_the_card(self):
        """标题落在卡片正中：它的墨迹在竖直方向上以卡片中线为中心。"""
        host, card = _scene()
        origin = _card_origin(host, card)
        image = _still_frame(host, card, 0.0)
        scale = image.width() / host.width()
        left = int(origin.x() * scale)
        top = int(origin.y() * scale)
        width = int(card.width() * scale)
        height = int(card.height() * scale)

        # 渐变标题与光晕是卡片上唯一的青绿亮部：取它们的墨迹范围。
        rows = [
            y
            for y in range(top, top + height)
            for x in range(left, left + width)
            if QColor(image.pixel(x, y)).green() > 170
        ]
        self.assertTrue(rows, "卡片上应有渐变标题")
        centre = (min(rows) + max(rows)) / 2.0
        relative = (centre - top) / height
        self.assertAlmostEqual(
            relative, 0.5, delta=0.08, msg=f"标题应在卡片竖直中央，实际在 {relative:.3f}"
        )


def _stack_scene():
    """把等待动画放进一块页面同色的底板里，返回 ``(底板, 动画)``。"""
    from presentation.qt.scan_card import ScanWaitStack
    from presentation.qt.theme import TOKENS

    stack = ScanWaitStack()
    host = QWidget()
    host.setStyleSheet(f"background-color: {TOKENS['canvas']};")
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(stack, 0, Qt.AlignHCenter)
    host.resize(stack.width(), stack.height())
    host.show()
    _application().processEvents()
    return host, stack


def _stack_frame(host, stack, elapsed_ms: float, particle: float | None = None):
    """把动画钉在某一刻再抓图：每个时刻的画面才可比。"""
    stack._frame_timer.stop()
    stack._elapsed = float(elapsed_ms)
    if particle is not None:
        stack._particle = float(particle)
    stack.update()
    _application().processEvents()
    return host.grab().toImage()


def _card_point(stack, pose, x_local: float, y_local: float) -> tuple[float, float]:
    """把卡片自己的坐标（中心为原点）换算成舞台坐标。"""
    radians = math.radians(pose.angle)
    return (
        stack.width() / 2.0
        + pose.x
        + x_local * math.cos(radians)
        - y_local * math.sin(radians),
        stack.height() / 2.0
        + pose.y
        + x_local * math.sin(radians)
        + y_local * math.cos(radians),
    )


def _card_extent(pose) -> tuple[float, float]:
    """一张卡转了一个角度之后，轴对齐包围盒从舞台中心伸出去多远。"""
    from presentation.qt.device_cards import CARD_HEIGHT, CARD_WIDTH

    radians = math.radians(pose.angle)
    half_width = (
        CARD_WIDTH * abs(math.cos(radians)) + CARD_HEIGHT * abs(math.sin(radians))
    ) / 2.0
    half_height = (
        CARD_HEIGHT * abs(math.cos(radians)) + CARD_WIDTH * abs(math.sin(radians))
    ) / 2.0
    return abs(pose.x) + half_width, abs(pose.y) + half_height


class ScanWaitStackTests(unittest.TestCase):
    """扫描等待动画：7 张卡摊成一把扇形、捻过一遍，再叠回原样。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _application()

    def test_the_stage_holds_the_whole_fan_at_every_moment(self):
        from presentation.qt.scan_card import (
            WAIT_CYCLE_MS,
            WAIT_STACK_CARDS,
            WAIT_STACK_HEIGHT,
            WAIT_STACK_WIDTH,
            ScanWaitStack,
        )

        stack = ScanWaitStack()
        self.assertEqual(
            WAIT_STACK_CARDS, 7, "动画永远是 7 张卡：它只是动画，不跟设备台数走"
        )
        for elapsed in range(0, WAIT_CYCLE_MS + 1, 10):
            for index, pose in enumerate(stack.poses_at(elapsed)):
                half_width, half_height = _card_extent(pose)
                self.assertLessEqual(
                    half_width,
                    WAIT_STACK_WIDTH / 2.0 + 0.5,
                    f"{elapsed}ms 时第 {index} 张卡伸出了舞台左右边界，会被裁成硬边",
                )
                self.assertLessEqual(
                    half_height,
                    WAIT_STACK_HEIGHT / 2.0 + 0.5,
                    f"{elapsed}ms 时第 {index} 张卡伸出了舞台上下边界，会被裁成硬边",
                )
        self.assertEqual(stack.size().width(), WAIT_STACK_WIDTH)
        self.assertEqual(stack.size().height(), WAIT_STACK_HEIGHT)
        self.assertGreater(
            WAIT_STACK_WIDTH,
            WAIT_STACK_HEIGHT,
            "舞台要比卡片宽：整把扇形才摊得开",
        )
        self.assertTrue(
            stack.testAttribute(Qt.WA_TransparentForMouseEvents),
            "等待动画只是画面：点击与悬停都留给面板",
        )
        self.assertEqual(stack.focusPolicy(), Qt.NoFocus)

    def test_the_deck_fans_out_symmetrically_around_the_middle_card(self):
        from presentation.qt.scan_card import (
            WAIT_FAN_MS,
            WAIT_FAN_STEP_DEG,
            WAIT_STACK_CARDS,
            ScanWaitStack,
        )

        stack = ScanWaitStack()
        self.assertTrue(
            all(pose.is_stacked() for pose in stack.poses_at(0)),
            "开始前是一整叠",
        )
        middle = (WAIT_STACK_CARDS - 1) // 2
        reach = (WAIT_STACK_CARDS - 1) / 2.0
        fanned = stack.poses_at(WAIT_FAN_MS)
        self.assertAlmostEqual(fanned[middle].angle, 0.0, msg="中间那张始终是正的")
        self.assertAlmostEqual(fanned[middle].x, 0.0)
        self.assertAlmostEqual(fanned[0].angle, reach * WAIT_FAN_STEP_DEG)
        self.assertAlmostEqual(fanned[-1].angle, -reach * WAIT_FAN_STEP_DEG)
        self.assertAlmostEqual(fanned[0].x, -fanned[-1].x, msg="扇形左右对称")
        self.assertAlmostEqual(fanned[0].y, fanned[-1].y)
        # 摊开的幅度是有限的：不再有卡片原地转整圈。
        for pose in fanned:
            self.assertLess(abs(pose.angle), 20.0, "扇形角度要有分寸，不能转成风车")
        # 扇形确实铺得比单张卡宽：这就是“展开的面积大了一些”。
        self.assertGreater(fanned[0].x - fanned[-1].x, 100.0)

    def test_the_fan_opens_and_folds_back_within_one_cycle(self):
        from presentation.qt.scan_card import (
            WAIT_CYCLE_MS,
            WAIT_FAN_MS,
            WAIT_FAN_STAGGER_MS,
            WAIT_HOLD_MS,
            WAIT_PAUSE_MS,
            WAIT_STACK_CARDS,
            ScanWaitStack,
        )

        stack = ScanWaitStack()
        early = stack.poses_at(WAIT_FAN_STAGGER_MS // 2)
        self.assertFalse(early[0].is_stacked(), "最上面那张先摊开")
        self.assertTrue(
            early[WAIT_STACK_CARDS - 1].is_stacked(),
            "最靠后的那张要等前面几张都跟上才开始",
        )

        # 摊开之后停住，形状保持一整段（这段就是“看得清的一把扇形”）。
        fanned = stack.poses_at(WAIT_FAN_MS)
        held = stack.poses_at(WAIT_FAN_MS + WAIT_HOLD_MS - 1)
        self.assertAlmostEqual(fanned[0].angle, held[0].angle)

        closed = WAIT_FAN_MS + WAIT_HOLD_MS + WAIT_FAN_MS
        self.assertTrue(
            all(pose.is_stacked() for pose in stack.poses_at(closed)),
            "一整轮的最后要把所有卡叠回原样",
        )
        self.assertTrue(
            all(pose.is_stacked() for pose in stack.poses_at(closed + WAIT_PAUSE_MS - 1)),
            "叠回去之后要停一下才开始下一轮",
        )
        self.assertFalse(
            stack.poses_at(WAIT_CYCLE_MS + WAIT_FAN_MS)[0].is_stacked(),
            "停顿结束后应重新摊开",
        )

    def test_no_card_pokes_out_past_the_top_card_while_the_fan_opens_or_folds(self):
        """摊开与叠回的每一刻，下层卡都落在最上面那张的边上（右沿、上沿、下沿）。

        最上面那张与压在它右边的几张同步转开，左半边的牌再由内到外跟上（叠回时反过来）。
        扇形每一刻的比例都与摊满时一致，下层卡因此不会因为“转得比最上面那张慢”而从它
        的右边或下边探出去——那看起来就像下层卡压到了最上面那张，点“查找设备”后卡片
        向右展开时不该出现。扇形只在最上面那张的左边铺开（左边就是“摊开”的方向），
        所以左沿不在这里比较。
        """
        from presentation.qt.scan_card import (
            WAIT_FAN_MS,
            WAIT_FAN_STEP_DEG,
            WAIT_HOLD_MS,
            WAIT_STACK_CARDS,
            ScanWaitStack,
        )

        stack = ScanWaitStack()

        def box(pose):
            """一张卡在舞台坐标里的轴对齐包围盒。"""
            half_width, half_height = _card_extent(pose)
            return (
                pose.x - half_width,
                pose.y - half_height,
                pose.x + half_width,
                pose.y + half_height,
            )

        opening = range(0, WAIT_FAN_MS + 1, 5)
        closing = range(
            WAIT_FAN_MS + WAIT_HOLD_MS,
            WAIT_FAN_MS + WAIT_HOLD_MS + WAIT_FAN_MS + 1,
            5,
        )
        centre = (WAIT_STACK_CARDS - 1) // 2
        reach = (WAIT_STACK_CARDS - 1) / 2.0
        full_angle = reach * WAIT_FAN_STEP_DEG
        for elapsed in (*opening, *closing):
            poses = stack.poses_at(elapsed)
            _, top_edge, right_edge, bottom_edge = box(poses[0])
            # 右半边几张与最上面那张按同一个比例转开：任何时刻都是摊满那一刻的等比缩小版。
            amount = poses[0].angle / full_angle if full_angle else 0.0
            for index in range(1, centre + 1):
                expected = (reach - index) * WAIT_FAN_STEP_DEG * amount
                self.assertAlmostEqual(
                    poses[index].angle,
                    expected,
                    places=6,
                    msg=f"{elapsed}ms 时第 {index} 张卡没有跟最上面那张同步转开",
                )
            for index in range(1, WAIT_STACK_CARDS):
                _, card_top, card_right, card_bottom = box(poses[index])
                self.assertLessEqual(
                    card_right,
                    right_edge + 1.0,
                    f"{elapsed}ms 时第 {index} 张卡的右沿越过了最上面那张",
                )
                self.assertGreaterEqual(
                    card_top,
                    top_edge - 1.0,
                    f"{elapsed}ms 时第 {index} 张卡的上沿越过了最上面那张",
                )
                self.assertLessEqual(
                    card_bottom,
                    bottom_edge + 1.0,
                    f"{elapsed}ms 时第 {index} 张卡的下沿越过了最上面那张",
                )

    def test_the_hold_ripples_through_the_fan_one_card_at_a_time(self):
        from presentation.qt.scan_card import (
            WAIT_FAN_MS,
            WAIT_RIPPLE_LIFT,
            WAIT_RIPPLE_MS,
            WAIT_RIPPLE_STAGGER_MS,
            WAIT_STACK_CARDS,
            ScanWaitStack,
        )

        stack = ScanWaitStack()
        settled = stack.poses_at(WAIT_FAN_MS)
        first_peak = stack.poses_at(WAIT_FAN_MS + WAIT_RIPPLE_MS / 2)
        backmost = WAIT_STACK_CARDS - 1
        # 波纹从最靠后的那张开始：它被抬到最高时，最上面那张还没动。
        self.assertAlmostEqual(
            settled[backmost].y - first_peak[backmost].y,
            WAIT_RIPPLE_LIFT,
            delta=0.01,
        )
        self.assertAlmostEqual(settled[0].y, first_peak[0].y)

        last_peak = stack.poses_at(
            WAIT_FAN_MS + backmost * WAIT_RIPPLE_STAGGER_MS + WAIT_RIPPLE_MS / 2
        )
        self.assertAlmostEqual(
            settled[0].y - last_peak[0].y, WAIT_RIPPLE_LIFT, delta=0.01
        )

    def test_the_deck_comes_back_to_the_same_stacked_frame(self):
        from presentation.qt.scan_card import WAIT_FAN_MS, WAIT_HOLD_MS

        host, stack = _stack_scene()
        closed = WAIT_FAN_MS + WAIT_HOLD_MS + WAIT_FAN_MS
        self.assertEqual(
            _stack_frame(host, stack, 0, particle=0.0),
            _stack_frame(host, stack, closed, particle=0.0),
            "全部叠回时应与开始时一模一样",
        )

    def test_the_fanned_deck_spreads_outside_the_stacked_card(self):
        """摊开时同一块舞台上看得见卡片的边角；收拢后又只剩那一张卡。"""
        from presentation.qt.device_cards import CARD_WIDTH
        from presentation.qt.scan_card import (
            WAIT_FAN_MS,
            WAIT_STACK_HEIGHT,
            WAIT_STACK_WIDTH,
        )
        from presentation.qt.theme import TOKENS

        host, stack = _stack_scene()
        rest = _stack_frame(host, stack, 0)
        fanned = _stack_frame(host, stack, WAIT_FAN_MS)
        # 静止卡片的左沿再往外 20px（跳过卡片外那圈投影）：摊开时扇形的一角正好伸到这里。
        probe_x = (WAIT_STACK_WIDTH - CARD_WIDTH) / 2.0 - 20.0
        probe_y = WAIT_STACK_HEIGHT / 2.0

        self.assertEqual(
            _point(host, rest, probe_x, probe_y).name(),
            TOKENS["canvas"].lower(),
            "静止时卡片左沿之外是页面底色",
        )
        self.assertLess(
            _point(host, fanned, probe_x, probe_y).lightness(),
            90,
            "摊开时扇形应该伸到静止卡片的外面",
        )

    def test_every_fanned_card_keeps_its_own_bright_edge(self):
        """扇形里每张卡都有一道亮边和一道暗缝：叠得再紧也读得出是两张卡。"""
        from presentation.qt.device_cards import CARD_WIDTH
        from presentation.qt.scan_card import WAIT_FAN_MS

        host, stack = _stack_scene()
        image = _stack_frame(host, stack, WAIT_FAN_MS, particle=0.0)
        poses = stack.poses_at(WAIT_FAN_MS)
        half = CARD_WIDTH / 2.0

        def lightness_at(pose, x_local: float) -> int:
            x, y = _card_point(stack, pose, x_local, 0.0)
            return _point(host, image, x, y).lightness()

        for index, pose in enumerate(poses):
            # 亮边只有 1~2px 宽，直接取某一个采样点会踩到抗锯齿的边缘；
            # 沿左沿往里扫一小段，取最亮的那一点当作亮边。
            rim = max(lightness_at(pose, -half + inset) for inset in (0.5, 1.0, 1.5, 2.0))
            face = lightness_at(pose, -half + 8.0)
            self.assertGreater(
                rim, face + 20, f"第 {index} 张卡的亮边应与自己的卡面分得开"
            )
            if index < len(poses) - 1:
                # 亮边外面还有一道暗缝：那是它压着的下一张卡，不是同一张卡的延续。
                gap = min(lightness_at(pose, -half - outset) for outset in (1.0, 2.0, 3.0))
                self.assertLess(gap, rim - 15, f"第 {index} 张卡外侧应有暗缝")

    def test_the_whole_card_is_pre_rendered_so_a_frame_only_blits(self):
        """投影、暗缝、压暗与亮边都烘焙进一张图：每帧不再给 7 张卡各描两道圆角路径。"""
        from presentation.qt.device_cards import CARD_HEIGHT, CARD_WIDTH
        from presentation.qt.scan_card import _WAIT_SPRITE_PAD

        _host, stack = _stack_scene()
        sprites = stack._deck_sprites()
        self.assertEqual(len(sprites), 7)
        self.assertIs(sprites, stack._deck_sprites(), "同尺寸下整叠图只渲染一次")
        ratio = stack.devicePixelRatioF()
        for sprite in sprites:
            self.assertFalse(sprite.isNull())
            self.assertAlmostEqual(sprite.devicePixelRatio(), ratio)
            self.assertAlmostEqual(
                sprite.width() / ratio,
                CARD_WIDTH + 2 * _WAIT_SPRITE_PAD,
                delta=1.0,
                msg="整张图要装得下卡片四边伸出去的投影与描边",
            )
            self.assertGreaterEqual(
                sprite.height() / ratio, CARD_HEIGHT + 2 * _WAIT_SPRITE_PAD
            )

    def test_the_cards_behind_the_top_one_show_a_card_back(self):
        """后层卡面是牌背：只有纹样，不再叠正面的大光晕与角标。"""
        from presentation.qt.device_cards import CARD_HEIGHT, CARD_WIDTH
        from presentation.qt.scan_card import _render_card_back, _render_card_face

        def bright_coloured_points(pixmap) -> int:
            image = pixmap.toImage()
            count = 0
            for y in range(image.height()):
                for x in range(image.width()):
                    colour = QColor(image.pixel(x, y))
                    if colour.green() > colour.red() + 8 and colour.green() > 60:
                        count += 1
            return count

        face = _render_card_face(CARD_WIDTH, CARD_HEIGHT, 1.0)
        back = _render_card_back(CARD_WIDTH, CARD_HEIGHT, 1.0)
        face_points = bright_coloured_points(face)
        back_points = bright_coloured_points(back)
        self.assertGreater(face_points, 1000, "卡面上有青绿光晕与角标")
        self.assertLess(
            back_points * 4,
            face_points,
            "牌背只有一小圈内框与中心徽记，不能有大片光晕",
        )

    def test_the_top_card_keeps_its_particle_sparkles(self):
        """粒子只属于最上面那张卡：它照样外飘，不会让一叠卡各飘一团。"""
        from presentation.qt.scan_card import WAIT_FAN_MS

        host, stack = _stack_scene()
        still = _stack_frame(host, stack, WAIT_FAN_MS, particle=0.0)
        sparkling = _stack_frame(host, stack, WAIT_FAN_MS, particle=0.25)

        changed = sum(
            1
            for y in range(still.height())
            for x in range(still.width())
            if still.pixel(x, y) != sparkling.pixel(x, y)
        )
        self.assertGreater(changed, 20, "摊开的牌堆上也应看得到粒子")

    def test_the_deck_starts_over_every_time_it_is_shown(self):
        """扫描结束后动画收起来；下一次查找重新从一整叠、第一张卡开始。"""
        _host, stack = _stack_scene()
        self.assertTrue(stack._frame_timer.isActive(), "显示时动画应在走")

        stack.hide()
        _application().processEvents()
        self.assertFalse(stack._frame_timer.isActive(), "收起来后不该还在计时")
        self.assertTrue(
            all(pose.is_stacked() for pose in stack.current_poses),
            "收起来时牌堆归零",
        )

        stack.show()
        _application().processEvents()
        self.assertTrue(stack._frame_timer.isActive(), "再显示时应重新开始")
        self.assertEqual(stack._elapsed, 0.0, "重新开始时应从一整叠起步")

    def test_a_full_cycle_is_announced_when_the_deck_is_stacked_again(self):
        """一轮完整动画走完、牌堆复原时才通知面板；摊开中途不能提前通知。"""
        from presentation.qt.scan_card import WAIT_CYCLE_MS

        _host, stack = _stack_scene()
        announced = []
        stack.cycle_finished.connect(lambda: announced.append(True))

        QTest.qWait(300)
        self.assertEqual(announced, [], "一轮还没走完，不能通知面板换画面")

        QTest.qWait(WAIT_CYCLE_MS + 300)
        self.assertTrue(announced, "整轮走完（所有卡叠回原样）要通知面板")

    def test_finish_cycle_stacks_the_deck_and_announces_it(self):
        """收尾入口把当前这一轮直接走完：牌堆复原，并发出一次结束通知。"""
        _host, stack = _stack_scene()
        announced = []
        stack.cycle_finished.connect(lambda: announced.append(True))
        stack._elapsed = 380.0
        stack.update()

        stack.finish_cycle()

        self.assertTrue(
            all(pose.is_stacked() for pose in stack.current_poses),
            "收尾时所有卡必须叠回原样",
        )
        self.assertEqual(announced, [True])


if __name__ == "__main__":
    unittest.main()
