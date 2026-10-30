"""“查找设备”卡片按钮：一直亮着的自绘卡片，悬停不再改变它。

原效果（一段 HTML/CSS 卡片）在样式表里表达不出来，因此按钮整块自绘；这里只取它悬停之后
的样子：正中一行渐变标题、四周柔光与角标都亮着，卡片内部的光带、扫描线与粒子自己在动。
这里验证四件事：点击仍然走 ``clicked``、尺寸照旧、鼠标进出前后画面一模一样、以及标题确实
落在卡片正中。
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


def _frame(card):
    """卡片当前这一帧的图像；先钉住相位，动效的进度才可比。"""
    card._ambient = 0.35
    card._particle = 0.4
    return card.grab().toImage()


def _pixel(card, x_ratio: float, y_ratio: float) -> QColor:
    """卡片本体上某点的实际颜色；比例相对卡片本体，跳过四周画投影的留白。"""
    from presentation.qt.scan_card import GLOW_MARGIN

    image = card.grab().toImage()
    scale = image.width() / card.width()
    width = card.width() - 2 * GLOW_MARGIN
    height = card.height() - 2 * GLOW_MARGIN
    x = int((GLOW_MARGIN + width * x_ratio) * scale)
    y = int((GLOW_MARGIN + height * y_ratio) * scale)
    return QColor(image.pixel(x, y))


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
        """尺寸固定在 190×254（外加画投影的留白）：样式表的内边距不能把它压扁。"""
        from presentation.qt.scan_card import CARD_HEIGHT, CARD_WIDTH, GLOW_MARGIN

        card = _card()
        self.assertEqual(card.width(), CARD_WIDTH + 2 * GLOW_MARGIN)
        self.assertEqual(card.height(), CARD_HEIGHT + 2 * GLOW_MARGIN)

    def test_hovering_changes_nothing(self):
        """鼠标进出不留痕迹：卡片一直亮着，没有悬停过渡。"""
        card = _card()
        before = _frame(card)

        centre = QPointF(card.width() / 2.0, card.height() / 2.0)
        card.enterEvent(QEnterEvent(centre, centre, centre))
        QTest.qWait(400)
        self.assertEqual(_frame(card), before, "悬停不该改变卡片")

        card.leaveEvent(QEvent(QEvent.Type.Leave))
        QTest.qWait(400)
        self.assertEqual(_frame(card), before, "移开鼠标也不该改变卡片")

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
        from presentation.qt.scan_card import CARD_HEIGHT, CARD_WIDTH, GLOW_MARGIN

        card = _card()
        # 光带与粒子不参与判断：先把它们的相位留在看不见的位置。
        card._ambient = 0.0
        card._particle = 0.0
        image = card.grab().toImage()
        scale = image.width() / card.width()
        left = GLOW_MARGIN * scale
        top = GLOW_MARGIN * scale
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
