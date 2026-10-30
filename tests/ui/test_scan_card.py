"""“查找设备”卡片按钮：一直亮着的自绘卡片，鼠标进来时底卡从卡片背后旋出去。

原效果（一段 HTML/CSS 卡片）在样式表里表达不出来，因此按钮整块自绘：卡面一直亮着，正中
一行渐变标题，卡片内部的光带、扫描线与粒子自己在动；悬停动效用 Qt 复刻原 CSS 的两个伪
元素——底卡转 -90° 并横向拉伸、纵向压扁（0.6s，``cubic-bezier(0.175, 0.885, 0.32,
1.275)``），模糊的霓虹光斑同时加厚。这里验证五件事：点击仍然走 ``clicked``、尺寸照旧、
悬停确实把底卡转出来、鼠标移开后回到原样、以及标题确实落在卡片正中。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QEnterEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402


def _application():
    from presentation.qt.main_window import build_application

    # 走正式启动路径：主题（样式表、调色板、字体）必须与运行时有同一份。
    return build_application([])


def _card():
    from presentation.qt.scan_card import ScanPromptCard

    card = ScanPromptCard("查找设备")
    card.show()
    _application().processEvents()
    return card


def _frame(card, hover: float = 0.0):
    """卡片当前这一帧的图像；先钉住相位、悬停进度与悬停状态，动效的进度才可比。"""
    card._hover_animation.stop()
    card._set_hovered(hover > 0.0)
    card._hover = hover
    card._ambient = 0.35
    card._particle = 0.4
    return card.grab().toImage()


def _pixel(card, x_ratio: float, y_ratio: float) -> QColor:
    """卡片本体上某点的实际颜色；比例相对卡片本体，跳过四周画投影与动效的留白。"""
    from presentation.qt.scan_card import GLOW_MARGIN_X, GLOW_MARGIN_Y

    image = card.grab().toImage()
    scale = image.width() / card.width()
    width = card.width() - 2 * GLOW_MARGIN_X
    height = card.height() - 2 * GLOW_MARGIN_Y
    x = int((GLOW_MARGIN_X + width * x_ratio) * scale)
    y = int((GLOW_MARGIN_Y + height * y_ratio) * scale)
    return QColor(image.pixel(x, y))


def _outside_left(card, offset: float) -> QColor:
    """卡片左边 ``offset`` px、竖直居中的那一点：底卡转出去时会盖到它。"""
    from presentation.qt.scan_card import CARD_HEIGHT, GLOW_MARGIN_X, GLOW_MARGIN_Y

    image = card.grab().toImage()
    scale = image.width() / card.width()
    x = int((GLOW_MARGIN_X - offset) * scale)
    y = int((GLOW_MARGIN_Y + CARD_HEIGHT / 2.0) * scale)
    return QColor(image.pixel(x, y))


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
        card = _card()
        clicks = []
        card.clicked.connect(lambda: clicks.append(True))
        QTest.mouseClick(card, Qt.LeftButton)
        self.assertEqual(clicks, [True])

    def test_the_card_keeps_its_design_size(self):
        """尺寸固定在 190×254（外加画投影与旋出去的底卡的留白）：样式表不能把它压扁。"""
        from presentation.qt.scan_card import (
            CARD_HEIGHT,
            CARD_WIDTH,
            GLOW_MARGIN_X,
            GLOW_MARGIN_Y,
        )

        card = _card()
        self.assertEqual(card.width(), CARD_WIDTH + 2 * GLOW_MARGIN_X)
        self.assertEqual(card.height(), CARD_HEIGHT + 2 * GLOW_MARGIN_Y)

    def test_hovering_turns_the_bottom_card_out(self):
        """悬停到一半时，底卡转成斜的、从卡片侧面探出很远（原 CSS 的 rotate(-90deg)）。"""
        card = _card()
        _frame(card, 0.0)
        rest = _outside_left(card, 55.0)
        _frame(card, 0.5)
        turned = _outside_left(card, 55.0)

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

    def test_mouse_in_and_out_replays_the_transition(self):
        """鼠标进来底卡转出去、移开又转回来；两端画面与静止/悬停态完全一致。"""
        card = _card()
        centre = QPointF(card.width() / 2.0, card.height() / 2.0)
        rest = _frame(card, 0.0)
        turned = _frame(card, 1.0)

        _hover(card, centre)
        QTest.qWait(300)
        self.assertGreater(
            card._hover, 0.6, f"0.3s 时缓冲曲线应已冲过一大半，实际 {card._hover:.2f}"
        )
        QTest.qWait(500)
        self.assertAlmostEqual(card._hover, 1.0, places=3, msg="悬停结束应停在终点")
        card._ambient = 0.35
        card._particle = 0.4
        self.assertEqual(card.grab().toImage(), turned, "悬停之后就是转过头的底卡")

        _leave(card)
        QTest.qWait(800)
        self.assertAlmostEqual(card._hover, 0.0, places=3, msg="鼠标移开后应回到静止")
        card._ambient = 0.35
        card._particle = 0.4
        self.assertEqual(card.grab().toImage(), rest, "移开鼠标后应与悬停前一样")

    def test_the_card_is_lit_from_the_start(self):
        """初始就是亮起的样子：卡面是深的，四周有青绿柔光与光晕。"""
        card = _card()
        face = _pixel(card, 0.85, 0.85)
        self.assertLess(face.lightness(), 90, f"卡面应是深色，实际 {face.name()}")
        glow = _pixel(card, 0.5, 0.35)
        self.assertGreater(
            glow.green(), glow.red() + 8, f"悬停态的青绿柔光应一直在，实际 {glow.name()}"
        )

    def test_the_title_sits_in_the_middle_of_the_card(self):
        """标题落在卡片正中：它的墨迹在竖直方向上以卡片中线为中心。"""
        from presentation.qt.scan_card import (
            CARD_HEIGHT,
            CARD_WIDTH,
            GLOW_MARGIN_X,
            GLOW_MARGIN_Y,
        )

        card = _card()
        # 光带与粒子不参与判断：先把它们的相位留在看不见的位置。
        card._ambient = 0.0
        card._particle = 0.0
        image = card.grab().toImage()
        scale = image.width() / card.width()
        left = GLOW_MARGIN_X * scale
        top = GLOW_MARGIN_Y * scale
        width = CARD_WIDTH * scale
        height = CARD_HEIGHT * scale

        # 渐变标题与光晕是卡片上唯一的青绿亮部：取它们的墨迹范围。
        rows = [
            y
            for y in range(int(top), int(top + height))
            for x in range(int(left), int(left + width))
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
