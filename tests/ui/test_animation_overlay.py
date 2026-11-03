"""页面级动画覆盖层（技术债重构步骤 6）：铺满页面根容器、不进布局、不吃鼠标。

发牌/翻牌原先画在结果区里，坐标要跟着 ``QScrollArea`` 的滚动、页头收放与结果区布局
一起换算。覆盖层因此挂到页面根容器上，与普通内容同级：它只负责覆盖绘制和鼠标穿透，
几何始终等于宿主矩形，宿主尺寸变化时发出 :attr:`AnimationOverlay.resized`（页面据此
取消已经开始的动画，而不是在动画中途追踪布局）。

这里只验证这一层本身；发牌层的落点在开始时冻结、以及页面接线由
``tests/integration/device_discovery`` 里的用例覆盖。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QVBoxLayout, QWidget  # noqa: E402


def _application():
    from presentation.qt.main_window import build_application

    # 走正式启动路径：主题（样式表、调色板、字体）必须与运行时有同一份。
    return build_application([])


def _host_with_content():
    """一个页面根容器：普通内容进布局，覆盖层与它同级、不进布局。"""
    from presentation.qt.animation_overlay import AnimationOverlay

    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    content = QWidget(host)
    content.setObjectName("normalContent")
    layout.addWidget(content)
    overlay = AnimationOverlay(host)
    overlay.show()
    host.resize(640, 480)
    host.show()
    _application().processEvents()
    return host, content, overlay, layout


class AnimationOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _application()

    def test_the_overlay_covers_the_page_root_and_is_only_a_picture(self):
        """覆盖层铺满页面根容器；点击、悬停与键盘焦点都留给下面的普通内容。"""
        host, _content, overlay, _layout = _host_with_content()
        self.addCleanup(host.close)

        self.assertEqual(overlay.objectName(), "pageAnimationOverlay")
        self.assertEqual(overlay.geometry(), host.rect(), "覆盖层要铺满页面根容器")
        self.assertTrue(
            overlay.testAttribute(Qt.WA_TransparentForMouseEvents),
            "覆盖层不能吃鼠标：下面还有滚动区与卡片",
        )
        self.assertEqual(overlay.focusPolicy(), Qt.NoFocus)

    def test_the_overlay_does_not_take_part_in_the_page_layout(self):
        """覆盖层不进布局：它出现与否都不推动页面里的控件。"""
        host, content, overlay, layout = _host_with_content()
        self.addCleanup(host.close)

        self.assertEqual(layout.count(), 1, "布局里只应有普通内容")
        self.assertIs(layout.itemAt(0).widget(), content)
        laid_out = {
            layout.itemAt(index).widget() for index in range(layout.count())
        }
        self.assertNotIn(overlay, laid_out)
        self.assertIs(overlay.host(), host)

    def test_the_overlay_follows_the_host_and_announces_the_change(self):
        """宿主尺寸一变，覆盖层立刻铺满新矩形并发出 ``resized``。"""
        host, _content, overlay, _layout = _host_with_content()
        self.addCleanup(host.close)
        announced = []
        overlay.resized.connect(lambda: announced.append(True))

        host.resize(900, 520)
        self.app.processEvents()

        self.assertEqual(overlay.geometry(), host.rect())
        self.assertEqual(announced, [True], "宿主尺寸变化要通知页面：动画坐标已失效")

    def test_the_overlay_keeps_itself_above_the_normal_content(self):
        """显示时压在普通内容之上：动画画面不会被页面内容盖住。"""
        from PySide6.QtGui import QColor, QPainter

        from presentation.qt.animation_overlay import AnimationOverlay

        class _PaintedOverlay(AnimationOverlay):
            """只在这条用例里替覆盖层上一块颜色：真实覆盖层是透明的。"""

            def paintEvent(self, event):  # noqa: N802 - Qt 覆盖方法
                painter = QPainter(self)
                painter.fillRect(self.rect(), QColor("#FF0000"))
                painter.end()

        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        content = QWidget(host)
        content.setAttribute(Qt.WA_StyledBackground, True)
        content.setStyleSheet("background-color: #0000FF;")
        layout.addWidget(content)
        overlay = _PaintedOverlay(host)
        overlay.show()
        host.resize(320, 240)
        host.show()
        self.addCleanup(host.close)
        self.app.processEvents()

        image = host.grab().toImage()
        centre = image.pixelColor(host.width() // 2, host.height() // 2)
        self.assertEqual(
            centre.name(), QColor("#FF0000").name(), "覆盖层应盖在普通内容上面"
        )

    def test_the_overlay_is_transparent_while_no_animation_is_playing(self):
        """没有动画时覆盖层不涂任何像素：页面内容原样透出来。"""
        from PySide6.QtGui import QColor

        from presentation.qt.animation_overlay import AnimationOverlay

        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        content = QWidget(host)
        content.setAttribute(Qt.WA_StyledBackground, True)
        content.setStyleSheet("background-color: #0000FF;")
        layout.addWidget(content)
        overlay = AnimationOverlay(host)
        overlay.show()
        host.resize(320, 240)
        host.show()
        self.addCleanup(host.close)
        self.app.processEvents()

        image = host.grab().toImage()
        centre = image.pixelColor(host.width() // 2, host.height() // 2)
        self.assertEqual(
            centre.name(),
            QColor("#0000FF").name(),
            "空闲时覆盖层应完全透明，不能盖住页面内容",
        )


    def test_widget_rect_in_maps_across_branches_through_the_screen(self):
        """跨分支换算不依赖 ``mapTo``：窗口挪到桌面别处也仍是覆盖层坐标。"""
        from PySide6.QtCore import QRectF

        from presentation.qt.animation_overlay import (
            AnimationOverlay,
            widget_rect_in,
        )

        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        content = QWidget(host)
        layout.addWidget(content)
        card = QWidget(content)
        card.setGeometry(30, 40, 100, 60)
        overlay = AnimationOverlay(host)
        overlay.show()
        host.move(140, 90)
        host.resize(640, 480)
        host.show()
        self.addCleanup(host.close)
        self.app.processEvents()

        self.assertEqual(
            widget_rect_in(card, overlay),
            QRectF(30.0, 40.0, 100.0, 60.0),
            "窗口位置不能混进覆盖层坐标（mapTo 对兄弟分支返回的是屏幕坐标）",
        )


if __name__ == "__main__":
    unittest.main()
