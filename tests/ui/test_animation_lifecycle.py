"""通用过渡与 Overlay 约定（技术债重构步骤 12）。

仪器卡片重构里重复出现的只有四件事：覆盖层生命周期、取消协议、完成信号、页面隐藏
时自动停止。完成信号沿用步骤 7 的 ``CardDealAnimation.finished``（由
``test_card_deal_animation.py`` 覆盖），这里锁定前两件与“宿主变化就收尾”的接线，
避免新增页面动效时又各自接事件、各自清计时器：

- :class:`presentation.qt.animation_lifecycle.OverlayLifecycle` 把“宿主尺寸/滚动/
  隐藏变化就收尾”收在一处，页面只登记一个收尾入口；
- 收尾原因取固定名称（``surface-changed`` / ``page-hidden`` / ``page-shutdown``）；
- 一次性过渡只暴露 ``is_running()`` / ``cancel(*, reveal, reason)``，每张控件由页面在
  收尾入口里统一恢复；
- 宿主隐藏时自动收尾（面板单独使用、没有页面级覆盖层的情形由面板自己接隐藏事件）。

这里只验证覆盖层与生命周期约定本身；发现页的真实接线（页面隐藏、页面关闭时收尾）在
``tests/integration/device_discovery``。
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

    def test_the_cancel_reasons_are_fixed_names(self):
        """收尾原因是对外契约：日志、诊断与测试按同一个词检索。"""
        from presentation.qt import animation_lifecycle

        self.assertEqual(animation_lifecycle.CANCEL_REASON_SURFACE_CHANGED, "surface-changed")
        self.assertEqual(animation_lifecycle.CANCEL_REASON_PAGE_HIDDEN, "page-hidden")
        self.assertEqual(animation_lifecycle.CANCEL_REASON_PAGE_SHUTDOWN, "page-shutdown")

    def test_an_animation_only_needs_is_running_and_cancel(self):
        """一次性过渡的能力只有“是否在播”与“如何收尾”，没有别的入口。"""
        from presentation.qt.animation_lifecycle import CancelableAnimation
        from presentation.qt.card_deal_animation import CardDealAnimation

        animation = CardDealAnimation()
        self.addCleanup(animation.deleteLater)

        self.assertIsInstance(animation, CancelableAnimation)
        self.assertFalse(animation.is_running())

    def test_attaching_something_else_is_rejected(self):
        _host, _overlay, lifecycle, _cancel = self._lifecycle()

        with self.assertRaises(TypeError):
            lifecycle.attach("not a callable")

    def test_without_an_entry_cancel_does_nothing(self):
        from presentation.qt.animation_lifecycle import OverlayLifecycle

        host, overlay = _host_with_overlay()
        self.addCleanup(host.close)
        lifecycle = OverlayLifecycle(overlay)

        self.assertFalse(lifecycle.cancel(reason="surface-changed"))

    def test_the_lifecycle_exposes_its_overlay(self):
        _host, overlay, lifecycle, _cancel = self._lifecycle()

        self.assertIs(lifecycle.overlay(), overlay)

    def test_a_host_resize_cancels_the_running_transition(self):
        """覆盖层尺寸变化 = 冻结的动画坐标失效：按 ``surface-changed`` 收尾。"""
        from presentation.qt.animation_lifecycle import CANCEL_REASON_SURFACE_CHANGED

        host, _overlay, _lifecycle, cancel = self._lifecycle()

        host.resize(600, 420)
        self.app.processEvents()

        self.assertEqual(
            cancel.calls, [(True, CANCEL_REASON_SURFACE_CHANGED)], "缩放只收尾一次"
        )

    def test_scrolling_a_registered_area_cancels_the_running_transition(self):
        """滚动区两个方向都算：滚动会挪动卡片在覆盖层里的位置。"""
        from presentation.qt.animation_lifecycle import (
            CANCEL_REASON_SURFACE_CHANGED,
            OverlayLifecycle,
        )

        host, overlay = _host_with_overlay()
        self.addCleanup(host.close)
        area = QScrollArea(host)
        area.resize(200, 150)
        content = QWidget()
        content.setMinimumSize(800, 800)
        area.setWidget(content)
        lifecycle = OverlayLifecycle(overlay)
        cancel = RecordingCancel()
        lifecycle.attach(cancel)
        lifecycle.watch_scrolling(area)

        area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
        area.horizontalScrollBar().setValue(area.horizontalScrollBar().maximum())

        self.assertEqual(
            cancel.calls,
            [(True, CANCEL_REASON_SURFACE_CHANGED)],
            "第一次滚动收尾之后，第二次滚动没有在播的动画可取消",
        )

    def test_hiding_the_host_cancels_and_restores(self):
        """宿主被藏起来（切页、标签页切走、窗口最小化）：按 ``page-hidden`` 收尾。"""
        from presentation.qt.animation_lifecycle import CANCEL_REASON_PAGE_HIDDEN

        host, _overlay, _lifecycle, cancel = self._lifecycle()

        host.hide()
        self.app.processEvents()

        self.assertEqual(cancel.calls, [(True, CANCEL_REASON_PAGE_HIDDEN)])

    def test_the_overlay_announces_that_its_host_was_hidden(self):
        """覆盖层只报“宿主藏起来了”；层级与几何仍由它自己保证。"""
        host, overlay, _lifecycle, _cancel = self._lifecycle()
        announced = []
        overlay.host_hidden.connect(lambda: announced.append(True))

        host.hide()
        self.app.processEvents()

        self.assertEqual(announced, [True])

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

    def test_cancel_reports_whether_anything_was_running(self):
        """取消协议返回“是否真的收尾了一次”：没有在播时页面不必再改状态。"""
        from presentation.qt.animation_lifecycle import CANCEL_REASON_SURFACE_CHANGED

        _host, _overlay, lifecycle, cancel = self._lifecycle()

        self.assertTrue(lifecycle.cancel(reason=CANCEL_REASON_SURFACE_CHANGED))
        self.assertFalse(lifecycle.cancel(reason=CANCEL_REASON_SURFACE_CHANGED))
        self.assertEqual(cancel.calls, [(True, CANCEL_REASON_SURFACE_CHANGED)])


if __name__ == "__main__":
    unittest.main()
