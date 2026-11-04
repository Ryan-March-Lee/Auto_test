"""仪表配置页的页面动作按钮：主按钮的蓝色填充，以及“全选”的黑白描边状态。

参考按钮的悬停画面（伪元素圆形色块、``transition``、``box-shadow`` 扩散与
``transform``）样式表表达不了，因此按钮整块自绘。这里验证用户看得见的三件事：

- 没有选择设备时“下一步”是灰色、不可点，鼠标移上去也不动；选中设备后变为可用，
  并播放一次从中心展开的蓝色填充，之后保持蓝色；可用状态下鼠标移入会轻微上浮；
- “全选”初始是白底黑框黑字，全部选中后变成白底灰框灰字且不可点，占位不随状态变化；
- 两个按钮共用同一份胶囊几何，并排时高度一致。

颜色断言只取胶囊表面与边框（跳过文字与四周光晕），避免把字形抗锯齿算进去；文字颜色
取中间区域最深的像素。
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


def _button(*, enabled: bool):
    from presentation.qt.action_button import AnimatedActionButton

    button = AnimatedActionButton("下一步")
    button.resize(button.sizeHint())
    button.show()
    button.setEnabled(enabled)
    _application().processEvents()
    return button


def _enter(button):
    button.enterEvent(
        QEnterEvent(QPointF(5.0, 5.0), QPointF(5.0, 5.0), QPointF(5.0, 5.0))
    )


def _leave(button):
    button.leaveEvent(QEvent(QEvent.Type.Leave))


def _pill_pixel(button, x_ratio: float, y_ratio: float) -> QColor:
    """胶囊表面上某点的颜色；比例相对胶囊本体，跳过文字所在的中间区域。"""
    from presentation.qt.action_button import GLOW_MARGIN, PILL_HEIGHT

    image = button.grab().toImage()
    scale = image.width() / button.width()
    pill_width = button.width() - 2 * GLOW_MARGIN
    x = int((GLOW_MARGIN + pill_width * x_ratio) * scale)
    y = int((GLOW_MARGIN + PILL_HEIGHT * y_ratio) * scale)
    return QColor(image.pixel(x, y))


def _outline_button(*, enabled: bool):
    from presentation.qt.action_button import OutlineActionButton

    button = OutlineActionButton("全选")
    button.resize(button.sizeHint())
    button.show()
    button.setEnabled(enabled)
    _application().processEvents()
    return button


def _top_border_pixel(button) -> QColor:
    """胶囊上边框正中那一点的颜色；避开圆角与中间的文字。"""
    from presentation.qt.action_button import GLOW_MARGIN, OUTLINE_WIDTH

    image = button.grab().toImage()
    scale_x = image.width() / button.width()
    scale_y = image.height() / button.height()
    x = int(button.width() * 0.5 * scale_x)
    y = int((GLOW_MARGIN - OUTLINE_WIDTH / 2.0) * scale_y)
    return QColor(image.pixel(x, y))


def _darkest_text_pixel(button) -> QColor:
    """胶囊中间区域最深的像素：字形笔画总有一处落在实色文字上。"""
    image = button.grab().toImage()
    scale_x = image.width() / button.width()
    scale_y = image.height() / button.height()
    darkest = QColor(255, 255, 255)
    for x in range(int(button.width() * 0.30), int(button.width() * 0.70)):
        for y in range(int(button.height() * 0.35), int(button.height() * 0.65)):
            colour = QColor(image.pixel(int(x * scale_x), int(y * scale_y)))
            if colour.lightness() < darkest.lightness():
                darkest = colour
    return darkest


class AnimatedActionButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _application()

    def test_disabled_button_is_grey_and_ignores_hover(self):
        from presentation.qt.action_button import GLOW_MARGIN, PILL_HEIGHT
        from presentation.qt.theme import TOKENS

        button = _button(enabled=False)
        self.addCleanup(button.close)

        self.assertEqual(
            _pill_pixel(button, 0.18, 0.35).name(),
            TOKENS["disabled_bg"].lower(),
            "不可用时胶囊应是禁用灰",
        )
        _enter(button)
        QTest.qWait(250)
        self.assertEqual(button._fill, 0.0, "不可用时不应播放填充")
        self.assertEqual(button._lift, 0.0, "不可用时不应上浮")
        self.assertEqual(button.size().height(), button.sizeHint().height())
        self.assertEqual(
            button.sizeHint().height(), int(PILL_HEIGHT + 2 * GLOW_MARGIN)
        )

    def test_selecting_starts_the_fill_and_keeps_the_button_blue(self):
        from presentation.qt.theme import TOKENS

        button = _button(enabled=False)
        self.addCleanup(button.close)
        self.assertEqual(button._fill, 0.0)

        button.setEnabled(True)
        QTest.qWait(1000)

        self.assertAlmostEqual(button._fill, 1.0, places=3, msg="可用后填充应走满")
        self.assertEqual(
            _pill_pixel(button, 0.18, 0.35).name(),
            TOKENS["primary"].lower(),
            "可用后胶囊应保持主色蓝",
        )

    def test_hover_lifts_the_enabled_button_and_back(self):
        button = _button(enabled=True)
        self.addCleanup(button.close)
        QTest.qWait(1000)
        # 顶层控件可能正好落在系统光标下（离屏运行时是左上角）：先钉回落位态，
        # 再验证一次真正的“移入 → 上浮 → 移出 → 落回”。
        button._lift_animation.stop()
        button._lift = 0.0
        button.update()
        rest_top = button._pill_rect().top()

        _enter(button)
        QTest.qWait(300)
        self.assertAlmostEqual(button._lift, 1.0, places=2, msg="悬停应上浮")
        self.assertLess(button._pill_rect().top(), rest_top, "上浮时胶囊应离原位更高")

        _leave(button)
        QTest.qWait(300)
        self.assertAlmostEqual(button._lift, 0.0, places=2, msg="移开后应落回原位")
        self.assertAlmostEqual(button._pill_rect().top(), rest_top, places=2)

    def test_only_the_enabled_button_reports_clicks(self):
        button = _button(enabled=False)
        self.addCleanup(button.close)
        clicks = []
        button.clicked.connect(lambda: clicks.append(True))

        button.click()
        self.assertEqual(clicks, [], "不可用时点击不能触发任何动作")

        button.setEnabled(True)
        button.click()
        self.assertEqual(clicks, [True])

    def test_size_stays_fixed_when_the_state_changes(self):
        button = _button(enabled=False)
        self.addCleanup(button.close)
        before = button.size()

        button.setEnabled(True)
        QTest.qWait(1000)
        _enter(button)
        QTest.qWait(300)

        self.assertEqual(button.size(), before, "动效不能改变按钮占位")


class OutlineActionButtonTests(unittest.TestCase):
    """“全选”：白底黑框黑字 ↔ 白底灰框灰字，形状与“下一步”一致。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _application()

    def test_initial_state_is_white_with_a_black_outline_and_text(self):
        from presentation.qt.theme import TOKENS

        button = _outline_button(enabled=True)
        self.addCleanup(button.close)

        self.assertTrue(button.isEnabled())
        self.assertEqual(
            _pill_pixel(button, 0.18, 0.35).name(),
            TOKENS["surface"].lower(),
            "初始状态应为白底",
        )
        self.assertEqual(
            _top_border_pixel(button).name(),
            TOKENS["text"].lower(),
            "初始状态应为黑色边框",
        )
        self.assertEqual(
            _darkest_text_pixel(button).name(),
            TOKENS["text"].lower(),
            "初始状态应为黑色文字",
        )

    def test_all_selected_state_turns_grey_and_stays_white_inside(self):
        from presentation.qt.theme import TOKENS

        button = _outline_button(enabled=True)
        self.addCleanup(button.close)
        size = button.size()

        button.setEnabled(False)
        self.app.processEvents()

        self.assertEqual(button.size(), size, "状态切换不能改变按钮占位")
        self.assertEqual(
            _pill_pixel(button, 0.18, 0.35).name(),
            TOKENS["surface"].lower(),
            "不可用时仍应是白底",
        )
        self.assertEqual(
            _top_border_pixel(button).name(),
            TOKENS["border_strong"].lower(),
            "不可用时边框转灰",
        )
        self.assertEqual(
            _darkest_text_pixel(button).name(),
            TOKENS["disabled_text"].lower(),
            "不可用时文字转灰",
        )

    def test_outline_button_matches_the_next_button_shape(self):
        from presentation.qt.action_button import AnimatedActionButton

        outline = _outline_button(enabled=True)
        self.addCleanup(outline.close)
        next_btn = AnimatedActionButton("下一步")
        self.addCleanup(next_btn.close)
        next_btn.resize(next_btn.sizeHint())

        self.assertEqual(
            outline.height(), next_btn.height(), "两个按钮的胶囊高度必须一致"
        )
        self.assertEqual(
            outline._pill_rect().height(), next_btn._pill_rect().height()
        )

    def test_disabled_outline_button_does_not_report_clicks(self):
        button = _outline_button(enabled=False)
        self.addCleanup(button.close)
        clicks = []
        button.clicked.connect(lambda: clicks.append(True))

        button.click()

        self.assertEqual(clicks, [], "全部选中后不能再触发全选")


if __name__ == "__main__":
    unittest.main()
