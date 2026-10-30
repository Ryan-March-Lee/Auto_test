"""欢迎页：两个模式按钮的样式表解放（自绘）与悬停动效。

按钮的悬停效果在样式表里表达不出来（伪元素、``transition``、``transform``），因此整块
自绘：一块与按钮等宽的圆形色块从左侧滑入并放大到 1.5 倍，被胶囊裁剪后看起来像一块扫过
按钮的色块；箭头同时从 45° 转到 90°。这里验证三件事：模式信号照旧发出、动效在两个方向
上都走满全程、以及静止/扫过时胶囊上的颜色确实变了（不是只有进度数值在动）。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF  # noqa: E402
from PySide6.QtGui import QColor, QEnterEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402


def _application():
    from presentation.qt.main_window import build_application

    # 走正式启动路径：主题（样式表、调色板、字体）必须与运行时有同一份。
    return build_application([])


def _page():
    from presentation.qt.welcome_page import ModeChoiceButton, WelcomePage

    page = WelcomePage()
    page.resize(900, 600)
    page.show()
    _application().processEvents()
    return page, page.findChildren(ModeChoiceButton)


def _pixel(button, x_ratio: float, y_ratio: float) -> QColor:
    """按钮胶囊内某点的实际颜色；比例相对胶囊本体，跳过四周画阴影的留白。"""
    from presentation.qt.welcome_page import SHADOW_MARGIN

    image = button.grab().toImage()
    scale = image.width() / button.width()
    width = button.width() - 2 * SHADOW_MARGIN
    height = button.height() - 2 * SHADOW_MARGIN
    x = int((SHADOW_MARGIN + width * x_ratio) * scale)
    y = int((SHADOW_MARGIN + height * y_ratio) * scale)
    return QColor(image.pixel(x, y))


class WelcomePageModeButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _application()

    def test_two_mode_buttons_report_the_choice(self):
        page, buttons = _page()

        self.assertEqual([b.text() for b in buttons], ["无驱放测试", "有驱放测试"])

        choices = []
        page.mode_selected.connect(choices.append)
        buttons[0].click()
        buttons[1].click()
        self.assertEqual(choices, [False, True])

    def test_hover_sweeps_the_fill_in_and_back_out(self):
        _, buttons = _page()
        button = buttons[0]
        self.assertEqual(button._fill, 0.0)

        button.enterEvent(
            QEnterEvent(QPointF(5.0, 5.0), QPointF(5.0, 5.0), QPointF(5.0, 5.0))
        )
        QTest.qWait(1100)
        self.assertAlmostEqual(button._fill, 1.0, places=3)
        self.assertAlmostEqual(button._foreground, 1.0, places=3)

        button.leaveEvent(QEvent(QEvent.Type.Leave))
        QTest.qWait(1100)
        self.assertAlmostEqual(button._fill, 0.0, places=3)
        self.assertAlmostEqual(button._foreground, 0.0, places=3)

    def test_fill_starts_off_the_capsule_and_ends_covering_it(self):
        """色块静止时整块停在左边界外（看不见），走完之后整枚胶囊都是强调色。"""
        _, buttons = _page()
        button = buttons[0]

        rest = _pixel(button, 0.25, 0.25)
        self.assertGreater(rest.lightness(), 230, f"静止时应是白色胶囊面，实际 {rest.name()}")

        button._fill = 0.5
        self.assertGreater(
            _pixel(button, 0.85, 0.25).lightness(),
            230,
            "扫到一半时右端应仍是白色胶囊面",
        )
        self.assertLessEqual(
            _pixel(button, 0.15, 0.25).green(),
            200,
            "扫到一半时左端应已被色块压住",
        )

        button._fill = 1.0
        swept = _pixel(button, 0.85, 0.25)
        self.assertLess(swept.red(), 90, f"走完之后应是强调色，实际 {swept.name()}")
        self.assertGreater(swept.green(), 150, f"走完之后应是强调色，实际 {swept.name()}")


if __name__ == "__main__":
    unittest.main()
