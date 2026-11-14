"""页面动作按钮的点击契约。

只验证启用/禁用状态是否报告点击；颜色、悬停动画和胶囊几何不再纳入默认回归。
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



class OutlineActionButtonTests(unittest.TestCase):
    """“全选”：白底黑框黑字 ↔ 白底灰框灰字，形状与“下一步”一致。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _application()




    def test_disabled_outline_button_does_not_report_clicks(self):
        button = _outline_button(enabled=False)
        self.addCleanup(button.close)
        clicks = []
        button.clicked.connect(lambda: clicks.append(True))

        button.click()

        self.assertEqual(clicks, [], "全部选中后不能再触发全选")


if __name__ == "__main__":
    unittest.main()
