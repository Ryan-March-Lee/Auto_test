"""欢迎页模式选择与配置入口。

覆盖两个模式按钮、加载配置链接与模式选择对其它配置键的保留；悬停动画和像素比对不再保留。
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

    def test_load_configuration_link_sits_below_the_mode_buttons(self):
        """加载配置是按钮下一行居中的白色下划线小字，点击只发出加载意图。"""
        page, buttons = _page()
        link = page._load_link

        self.assertEqual(link.text(), "加载配置")
        self.assertTrue(link.font().underline(), "加载配置应显示下划线")
        self.assertLess(
            link.font().pointSizeF(),
            page._subtitle.font().pointSizeF(),
            "加载配置应比副标题更小",
        )
        self.assertGreater(
            link.geometry().top(),
            buttons[0].geometry().bottom(),
            "加载配置应位于两个模式按钮的下一行",
        )

        requests = []
        page.load_config_requested.connect(lambda: requests.append(True))
        link.click()
        self.assertEqual(requests, [True])







class TestModeSelectionTests(unittest.TestCase):
    """欢迎页选择的测试模式只改写 ``driver_mode`` 的开关，不碰其它配置。

    这两条只调用 ``apply_test_mode`` 这个纯函数：不建 ``QApplication``、不触碰控件
    （本模块其余用例会导入 PySide6 并绘制按钮，但这里不需要）。
    """

    def test_mode_selection_keeps_other_driver_mode_keys(self):
        from presentation.qt.welcome_page import apply_test_mode

        config = {
            "driver_mode": {"enabled": False, "note": "keep"},
            "test_frequencies": [2.4],
        }

        result = apply_test_mode(config, True)

        # 返回并原地更新同一份配置副本：调用方随后交给配置页回填。
        self.assertIs(result, config)
        self.assertEqual(
            config["driver_mode"], {"enabled": True, "note": "keep"}
        )
        self.assertEqual(config["test_frequencies"], [2.4])

    def test_mode_selection_rebuilds_a_malformed_driver_block(self):
        from presentation.qt.welcome_page import apply_test_mode

        config = {"driver_mode": "enabled"}

        apply_test_mode(config, 0)

        self.assertEqual(config["driver_mode"], {"enabled": False})


if __name__ == "__main__":
    unittest.main()
