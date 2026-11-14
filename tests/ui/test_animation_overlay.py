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








if __name__ == "__main__":
    unittest.main()
