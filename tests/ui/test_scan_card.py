"""“查找设备”卡片按钮：按钮本体正好是那张卡片，鼠标进来时文字淡出、底卡从卡片背后旋出去。

原效果（一段 HTML/CSS 卡片）在样式表里表达不出来，因此按钮整块自绘：卡面一直亮着，正中
一行渐变标题，卡片内部的光带、扫描线与粒子自己在动；悬停动效用 Qt 复刻原 CSS 的两个伪
元素——底卡转 -90° 并横向拉伸、纵向压扁（0.6s，``cubic-bezier(0.175, 0.885, 0.32,
1.275)``），模糊的霓虹光斑同时加厚，卡面上的两行字（含副标题里的渐变高亮词）也在同一个
0.6s 里淡到看不见。要露在卡片外面的那三层（投影、光斑、底卡）画在同级的一张透明覆盖层
上，所以按钮本身只有 190×254。这里验证：点击仍然走 ``clicked``、按钮与卡片一样大、覆盖层
不进布局也不吃鼠标、悬停把底卡转出来、悬停时文字全部消失、鼠标移开后回到原样、标题落在
卡片正中，以及覆盖层跟着卡片搬家（隐藏/显示、换父控件、重排）且不改变页面层级。
"""

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

    卡片背后那层装饰（投影、光斑、旋出去的底卡）是卡片的**同级控件**，所以抓图要抓底板：
    它们是底板的孩子，抓下来的画面里才连卡片外面那一圈一起有了。底板四周比装饰层的留白
    再宽 20px，旋出去的底卡与模糊光斑才不会被底板的边裁掉。
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
    """这一帧的图像：先钉住相位、悬停进度与悬停状态，动效的进度才可比。"""
    card._hover_animation.stop()
    card._set_hovered(hover > 0.0)
    card._hover = hover
    card._ambient = 0.35
    card._particle = 0.4
    return host.grab().toImage()


def _still_frame(host, card, hover: float):
    """同样钉住动效，但把光带、扫描线与粒子停在看不见的位置：只留卡面上的字。"""
    card._hover_animation.stop()
    card._set_hovered(hover > 0.0)
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


def _outside_left(host, image, card, offset: float) -> QColor:
    """卡片左边 ``offset`` px、竖直居中的那一点：底卡转出去时会盖到它。"""
    origin = _card_origin(host, card)
    return _point(host, image, origin.x() - offset, origin.y() + card.height() / 2.0)


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
        """按钮的大小就是卡片的大小（190×254）：四周没有一圈既吃版面又吃悬停的留白。"""
        from presentation.qt.scan_card import CARD_HEIGHT, CARD_WIDTH

        host, card = _scene()
        self.assertEqual(card.sizeHint().width(), CARD_WIDTH)
        self.assertEqual(card.sizeHint().height(), CARD_HEIGHT)
        self.assertEqual(card.width(), CARD_WIDTH)
        self.assertEqual(card.height(), CARD_HEIGHT)
        # 底板只给了卡片这么大一块，装饰层不占版面。
        self.assertEqual(card.geometry().size(), card.sizeHint())

    def test_the_decorations_live_on_a_mouse_transparent_layer_behind_the_card(self):
        """投影、光斑与旋出去的底卡在同一父控件的覆盖层上：不进布局、不吃鼠标、只跟卡片走。"""
        host, card = _scene()
        backdrop = card._backdrop
        self.assertIsNotNone(backdrop, "卡片背后应有一层画投影、光斑与底卡的覆盖层")
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
        # 卡片自己仍然压在自己的装饰层上（中点是卡面，不是底卡或光斑）。
        self.assertLess(
            _card_pixel(host, image, card, 0.5, 0.5).lightness(),
            90,
            "卡片不应被自己的装饰层盖住",
        )

    def test_hovering_turns_the_bottom_card_out(self):
        """悬停到一半时，底卡转成斜的、从卡片侧面探出很远（原 CSS 的 rotate(-90deg)）。"""
        host, card = _scene()
        rest = _outside_left(host, _frame(host, card, 0.0), card, 55.0)
        turned = _outside_left(host, _frame(host, card, 0.5), card, 55.0)

        self.assertNotEqual(rest, turned, "悬停后卡片侧面应被转出来的底卡盖住")
        saturation = max(turned.red(), turned.green(), turned.blue()) - min(
            turned.red(), turned.green(), turned.blue()
        )
        self.assertGreater(
            saturation, 60, f"探出来的应是紫→蓝渐变底卡，实际 {turned.name()}"
        )
        self.assertLess(
            max(rest.red(), rest.green(), rest.blue())
            - min(rest.red(), rest.green(), rest.blue()),
            40,
            f"静止时卡片侧面只有一圈很窄的底卡，实际 {rest.name()}",
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
        """鼠标进来底卡转出去、文字收走，移开又都回来；两端画面与静止/悬停态完全一致。"""
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
        self.assertEqual(host.grab().toImage(), turned, "悬停之后就是转过头的底卡")

        _leave(card)
        QTest.qWait(800)
        self.assertAlmostEqual(card._hover, 0.0, places=3, msg="鼠标移开后应回到静止")
        card._ambient = 0.35
        card._particle = 0.4
        self.assertEqual(host.grab().toImage(), rest, "移开鼠标后应与悬停前一样")

    def test_disabling_the_card_gives_the_hover_look_back_up(self):
        """鼠标停着的时候禁用卡片：Qt 不再给它送鼠标事件，卡片自己回到静止那一格。

        被禁用的控件收不到 ``leaveEvent``，所以“悬停到一半被禁用”必须由卡片自己收尾，
        否则底卡会一直停在转出去的位置、字也一直收着。
        """
        host, card = _scene()
        _hover(card, QPointF(card.width() / 2.0, card.height() / 2.0))
        QTest.qWait(700)
        self.assertAlmostEqual(card.hover_progress, 1.0, places=3, msg="先让它悬停到底")

        card.setEnabled(False)
        _application().processEvents()
        self.assertEqual(card.hover_progress, 0.0, "禁用后底卡不该还停在转出去的位置")
        self.assertFalse(card.hovered, "禁用后柔光应回到静止那一档")

        card.setEnabled(True)
        _application().processEvents()
        self.assertGreater(
            _title_ink(host, _still_frame(host, card, 0.0), card),
            0,
            "重新启用后文字应回来",
        )

    def test_the_card_is_lit_from_the_start(self):
        """初始就是亮起的样子：卡面是深的，四周有青绿柔光与光晕。"""
        host, card = _scene()
        image = _frame(host, card, 0.0)
        face = _card_pixel(host, image, card, 0.85, 0.85)
        self.assertLess(face.lightness(), 90, f"卡面应是深色，实际 {face.name()}")
        glow = _card_pixel(host, image, card, 0.5, 0.35)
        self.assertGreater(
            glow.green(), glow.red() + 8, f"悬停态的青绿柔光应一直在，实际 {glow.name()}"
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


if __name__ == "__main__":
    unittest.main()
