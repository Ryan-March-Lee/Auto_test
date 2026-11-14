"""覆盖层生命周期的最小契约。

保留取消协议、宿主隐藏时收尾与关停幂等；尺寸/滚动触发和逐帧动画细节不再保留。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget  # noqa: E402


def _application():
    from presentation.qt.main_window import build_application

    # 走正式启动路径：主题（样式表、调色板、字体）必须与运行时有同一份。
    return build_application([])


def _host_with_overlay(size=(400, 300)):
    """一块页面根容器：普通内容进布局，覆盖层与它同级、不进布局。"""
    from presentation.qt.animation_overlay import AnimationOverlay

    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    layout.addWidget(QWidget(host))
    overlay = AnimationOverlay(host)
    overlay.show()
    host.resize(*size)
    return host, overlay


class RecordingCancel:
    """收尾入口的替身：记录每次 ``(reveal, reason)``，``running`` 决定是否算一次收尾。"""

    def __init__(self):
        self.calls: list[tuple[bool, str]] = []
        self.running = True

    def __call__(self, *, reveal: bool = True, reason: str = "unspecified") -> bool:
        if not self.running:
            return False
        self.running = False
        self.calls.append((bool(reveal), str(reason)))
        return True


class OverlayLifecycleTests(unittest.TestCase):
    """覆盖层生命周期：取消协议与“宿主变化就收尾”的固定规则。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _application()

    def _lifecycle(self):
        from presentation.qt.animation_lifecycle import OverlayLifecycle

        host, overlay = _host_with_overlay()
        host.show()
        self.app.processEvents()
        self.addCleanup(host.close)
        lifecycle = OverlayLifecycle(overlay)
        cancel = RecordingCancel()
        lifecycle.attach(cancel)
        return host, overlay, lifecycle, cancel


    def test_an_animation_only_needs_is_running_and_cancel(self):
        """动画对象只回答“是否在播”与“如何收尾”，不承担页面状态恢复。"""
        from presentation.qt.animation_lifecycle import CancelableAnimation
        from presentation.qt.card_deal_animation import CardDealAnimation

        animation = CardDealAnimation()
        self.addCleanup(animation.deleteLater)

        self.assertIsInstance(animation, CancelableAnimation)
        self.assertFalse(animation.is_running())
        self.assertIsNone(
            animation.cancel(reason="surface-changed"),
            "动画对象的 cancel() 不返回“页面是否收尾”，那是收尾入口的职责",
        )







    def test_hiding_the_host_cancels_and_restores(self):
        """宿主被藏起来（切页、标签页切走、窗口最小化）：按 ``page-hidden`` 收尾。"""
        from presentation.qt.animation_lifecycle import CANCEL_REASON_PAGE_HIDDEN

        host, _overlay, _lifecycle, cancel = self._lifecycle()

        host.hide()
        self.app.processEvents()

        self.assertEqual(cancel.calls, [(True, CANCEL_REASON_PAGE_HIDDEN)])



    def test_shutdown_ends_without_restoring_and_is_idempotent(self):
        """页面关闭：收尾但不恢复即将销毁的控件，重复调用不再触发事件。"""
        from presentation.qt.animation_lifecycle import CANCEL_REASON_PAGE_SHUTDOWN

        host, _overlay, lifecycle, cancel = self._lifecycle()

        self.assertTrue(lifecycle.shutdown())
        self.assertEqual(cancel.calls, [(False, CANCEL_REASON_PAGE_SHUTDOWN)])
        self.assertFalse(lifecycle.shutdown(), "关闭是幂等的")

        # 关闭之后事件源已断开：宿主再缩放也不会把收尾叫第二次。
        host.resize(500, 380)
        self.app.processEvents()
        self.assertEqual(cancel.calls, [(False, CANCEL_REASON_PAGE_SHUTDOWN)])



if __name__ == "__main__":
    unittest.main()
