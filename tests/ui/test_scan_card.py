"""“查找设备”卡片的交互冒烟。

覆盖点击发起扫描、按钮与卡片同形、悬停隐藏文字、禁用状态恢复与发牌循环收尾；
覆盖层像素细节不再纳入默认回归。
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

if __name__ == '__main__':
    unittest.main()
