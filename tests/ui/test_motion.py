"""内容过渡件的契约（重构计划步骤 2）。

这里锁定用户能看见、后续步骤会依赖的五件事：

- 动效令牌集中在 :mod:`presentation.qt.theme`，页面不再各写时长；
- 效果真的把透明度和水平偏移画到像素上（不是只改内部属性）；
- 过渡结束后控件上不再残留 ``QGraphicsEffect``，页面自己的效果不会被覆盖；
- ``duration_ms=0`` 直接落到终态，不创建效果也不启动计时器；重复调用先中止上一条；
- 收尾之后（包括控件先被销毁）句柄仍可安全查询，不抛 ``RuntimeError``。

像素断言抓取的是**父容器**而不是控件自己：Qt 在 ``QWidget.grab()`` 时不应用控件
自身的效果，只应用子控件的效果。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QEasingCurve  # noqa: E402
from PySide6.QtGui import QColor, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsEffect, QWidget  # noqa: E402

from presentation.qt.motion import (  # noqa: E402
    RevealDirection,
    SlideFadeEffect,
    reveal,
)
from presentation.qt.theme import (  # noqa: E402
    MOTION_BREATH_MS,
    MOTION_COMPLETE_MS,
    MOTION_REVEAL_MS,
    MOTION_SLIDE_PX,
    MOTION_STAGE_MS,
)


def _destroy(widget):
    """关闭并销毁顶层控件，再投递排队的删除（离屏用例的统一收尾方式）。"""
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class _ForeignEffect(QGraphicsEffect):
    """页面自己的效果：过渡件不得覆盖它。"""

    def draw(self, painter: QPainter) -> None:
        self.drawSource(painter)


class _Strip(QWidget):
    """红底 + 左侧 10px 蓝条：像素位置能直接看出偏移与透明度。"""

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(255, 0, 0))
        painter.fillRect(0, 0, 10, self.height(), QColor(0, 0, 255))
        painter.end()


class _MotionTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _widget(self, *, visible=True):
        widget = QWidget()
        widget.resize(160, 80)
        if visible:
            widget.show()
        self.addCleanup(_destroy, widget)
        self.app.processEvents()
        return widget

    def _flush_deferred_deletes(self):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def assert_color_close(self, actual, expected, tolerance=3):
        for channel in ("red", "green", "blue"):
            self.assertAlmostEqual(
                getattr(actual, channel)(),
                getattr(expected, channel)(),
                delta=tolerance,
                msg=f"{actual.name()} 与 {expected.name()} 的 {channel} 通道不一致",
            )


class MotionTokenTests(unittest.TestCase):
    def test_tokens_match_the_reconstruction_plan(self):
        self.assertEqual(MOTION_REVEAL_MS, 220)
        self.assertEqual(MOTION_STAGE_MS, 180)
        self.assertEqual(MOTION_COMPLETE_MS, 260)
        self.assertEqual(MOTION_BREATH_MS, 1600)
        self.assertEqual(MOTION_SLIDE_PX, 10)

    def test_direction_names_are_stable_strings(self):
        self.assertEqual(RevealDirection.FROM_LEFT.value, "left")
        self.assertEqual(RevealDirection.FROM_RIGHT.value, "right")


class SlideFadeEffectTests(_MotionTestCase):
    def test_progress_moves_opacity_and_offset(self):
        effect = SlideFadeEffect()
        self.addCleanup(effect.deleteLater)

        effect.set_opacity(0.0)
        effect.set_offset(MOTION_SLIDE_PX)
        self.assertEqual(effect.opacity(), 0.0)
        self.assertEqual(effect.offset(), float(MOTION_SLIDE_PX))

        effect.set_opacity(0.5)
        effect.set_offset(MOTION_SLIDE_PX / 2)
        self.assertEqual(effect.opacity(), 0.5)
        self.assertEqual(effect.offset(), MOTION_SLIDE_PX / 2)

        effect.set_opacity(1.0)
        effect.set_offset(0.0)
        self.assertEqual(effect.opacity(), 1.0)
        self.assertEqual(effect.offset(), 0.0)

    def test_opacity_is_clamped_to_the_visible_range(self):
        effect = SlideFadeEffect()
        self.addCleanup(effect.deleteLater)

        effect.set_opacity(-0.5)
        self.assertEqual(effect.opacity(), 0.0)
        effect.set_opacity(2.0)
        self.assertEqual(effect.opacity(), 1.0)

    def test_the_effect_is_actually_painted_as_a_fade_and_a_slide(self):
        """锁定像素级契约：只用属性断言抓不到“效果没画出来”的回归。"""
        container = QWidget()
        container.resize(300, 200)
        container.setStyleSheet("background: #FFFFFF;")
        self.addCleanup(_destroy, container)
        strip = _Strip(container)
        strip.setGeometry(50, 50, 100, 50)
        container.show()
        self.app.processEvents()

        effect = SlideFadeEffect()
        effect.set_opacity(0.5)
        effect.set_offset(float(MOTION_SLIDE_PX))
        strip.setGraphicsEffect(effect)
        self.app.processEvents()

        image = container.grab().toImage()

        def pixel(x, y=60):
            return image.pixelColor(x, y)

        # 内容整体右移 10px：原来的左边缘让出来，露出父容器底色。
        self.assert_color_close(pixel(52), QColor(255, 255, 255))
        # 蓝条从 50–59 挪到 60–69，并且以 50% 不透明度混在白底上。
        self.assert_color_close(pixel(65), QColor(128, 128, 255))
        # 红底同样整体右移并淡入。
        self.assert_color_close(pixel(80), QColor(255, 128, 128))
        self.assert_color_close(pixel(145), QColor(255, 128, 128))


    def test_background_fill_replaces_the_transparent_destination(self):
        """给了底色就先铺满控件矩形：原生子窗口（matplotlib 画布）不会透出黑底。"""
        container = QWidget()
        container.resize(300, 200)
        container.setStyleSheet("background: #FFFFFF;")
        self.addCleanup(_destroy, container)
        strip = _Strip(container)
        strip.setGeometry(50, 50, 100, 50)
        container.show()
        self.app.processEvents()

        effect = SlideFadeEffect(background=QColor(0, 255, 0))
        effect.set_opacity(0.0)
        effect.set_offset(0.0)
        strip.setGraphicsEffect(effect)
        self.app.processEvents()

        image = container.grab().toImage()

        # 完全透明也仍然铺底：整块控件矩形变成底色，而不是透出父容器。
        self.assert_color_close(image.pixelColor(80, 60), QColor(0, 255, 0))
        self.assert_color_close(image.pixelColor(52, 60), QColor(0, 255, 0))

    def test_background_fill_blends_only_with_the_source(self):
        container = QWidget()
        container.resize(300, 200)
        container.setStyleSheet("background: #FFFFFF;")
        self.addCleanup(_destroy, container)
        strip = _Strip(container)
        strip.setGeometry(50, 50, 100, 50)
        container.show()
        self.app.processEvents()

        effect = SlideFadeEffect(background=QColor(0, 255, 0))
        effect.set_opacity(0.5)
        effect.set_offset(0.0)
        strip.setGraphicsEffect(effect)
        self.app.processEvents()

        image = container.grab().toImage()

        # 红底以 50% 合成在绿底色上；若没有铺底会得到 127,127,127 的灰粉。
        self.assert_color_close(image.pixelColor(80, 60), QColor(127, 127, 0))

    def test_background_is_validated(self):
        with self.assertRaises(TypeError):
            SlideFadeEffect(background=123)
        with self.assertRaises(ValueError):
            SlideFadeEffect(background="not-a-colour")


class RevealTests(_MotionTestCase):
    def test_reveal_passes_the_background_to_the_effect(self):
        widget = self._widget()
        handle = reveal(
            widget,
            RevealDirection.FROM_RIGHT,
            MOTION_REVEAL_MS,
            background="#f5f7fa",
        )

        self.assertEqual(handle.effect().background().name(), "#f5f7fa")

    def test_reveal_starts_transparent_from_the_requested_edge(self):
        widget = self._widget()
        handle = reveal(widget, RevealDirection.FROM_RIGHT, MOTION_REVEAL_MS)

        effect = widget.graphicsEffect()
        self.assertIsInstance(effect, SlideFadeEffect)
        self.assertIs(handle.effect(), effect)
        self.assertEqual(effect.opacity(), 0.0)
        self.assertEqual(effect.offset(), float(MOTION_SLIDE_PX))
        self.assertTrue(handle.is_running())
        self.assertEqual(
            handle.animation().easingCurve().type(),
            QEasingCurve.Type.OutCubic,
            "测量区过渡必须与配置页同一缓动语气",
        )

    def test_progress_animates_opacity_and_offset_together(self):
        widget = self._widget()
        handle = reveal(widget, RevealDirection.FROM_RIGHT, MOTION_REVEAL_MS)
        animation = handle.animation()
        # 停掉计时器再手动推进，避免真实计时与断言赛跑。
        animation.stop()
        animation.setCurrentTime(MOTION_REVEAL_MS // 2)

        effect = widget.graphicsEffect()
        self.assertGreater(effect.opacity(), 0.0)
        self.assertLess(effect.opacity(), 1.0)
        self.assertGreater(effect.offset(), 0.0)
        self.assertLess(effect.offset(), float(MOTION_SLIDE_PX))

    def test_animation_finishes_without_residual_effect(self):
        widget = self._widget()
        handle = reveal(widget, RevealDirection.FROM_LEFT, MOTION_REVEAL_MS)
        animation = handle.animation()
        self.assertIsNotNone(widget.graphicsEffect())

        animation.setCurrentTime(animation.duration())

        self.assertIsNone(
            widget.graphicsEffect(), "过渡结束后必须摘除效果，不能长期拖慢页面"
        )
        self.assertFalse(handle.is_running())
        self.assertIsNone(handle.effect())
        self.assertIsNone(handle.animation())

        # 句柄的 deleteLater 由事件循环处理；之后查询仍必须是安全的。
        self._flush_deferred_deletes()
        self.assertFalse(handle.is_running())
        self.assertIsNone(handle.effect())
        self.assertIsNone(handle.animation())

    def test_zero_duration_lands_directly_without_a_timer(self):
        widget = self._widget()
        self.assertIsNone(reveal(widget, duration_ms=0))
        self.assertIsNone(widget.graphicsEffect())

        running = reveal(widget, RevealDirection.FROM_RIGHT, 1000)
        self.assertTrue(running.is_running())
        self.assertIsNone(reveal(widget, duration_ms=0))
        self.assertFalse(running.is_running())
        self.assertIsNone(widget.graphicsEffect())

        self._flush_deferred_deletes()
        self.assertFalse(running.is_running())
        self.assertIsNone(running.effect())
        self.assertIsNone(running.animation())
        running.cancel()

    def test_a_second_reveal_replaces_the_first(self):
        widget = self._widget()
        first = reveal(widget, RevealDirection.FROM_RIGHT, 1000)
        first_effect = first.effect()

        second = reveal(widget, RevealDirection.FROM_LEFT, 1000)

        self.assertFalse(first.is_running(), "新过渡开始前必须停止上一条")
        self.assertIsNot(widget.graphicsEffect(), first_effect)
        self.assertIs(widget.graphicsEffect(), second.effect())
        self.assertEqual(second.effect().offset(), -float(MOTION_SLIDE_PX))

        self._flush_deferred_deletes()
        self.assertFalse(first.is_running())
        self.assertIsNone(first.effect())

    def test_direction_accepts_left_right_strings(self):
        widget = self._widget()
        left = reveal(widget, "left", 1000)
        self.assertEqual(left.effect().offset(), -float(MOTION_SLIDE_PX))

        right = reveal(widget, "right", 1000)
        self.assertEqual(right.effect().offset(), float(MOTION_SLIDE_PX))

    def test_an_existing_page_effect_is_not_overridden(self):
        widget = self._widget()
        foreign = _ForeignEffect()
        widget.setGraphicsEffect(foreign)

        self.assertIsNone(reveal(widget, RevealDirection.FROM_RIGHT, 220))
        self.assertIs(widget.graphicsEffect(), foreign)

    def test_a_page_effect_installed_mid_transition_stops_the_transition(self):
        """Qt 会同步销毁被替换的效果，动画不能继续写已删除的对象。"""
        widget = self._widget()
        handle = reveal(widget, RevealDirection.FROM_RIGHT, 1000)
        foreign = _ForeignEffect()

        widget.setGraphicsEffect(foreign)

        self.assertIs(widget.graphicsEffect(), foreign)
        self.assertFalse(handle.is_running())
        self.assertIsNone(handle.effect())
        self.assertIsNone(handle.animation())
        self._flush_deferred_deletes()
        handle.cancel()

    def test_cancel_removes_the_effect_and_is_idempotent(self):
        widget = self._widget()
        handle = reveal(widget, RevealDirection.FROM_RIGHT, 1000)

        handle.cancel()
        self.assertIsNone(widget.graphicsEffect())
        handle.cancel()

        self._flush_deferred_deletes()
        self.assertFalse(handle.is_running())
        self.assertIsNone(handle.effect())
        self.assertIsNone(handle.animation())

    def test_widget_destroyed_mid_transition_leaves_a_safe_handle(self):
        widget = QWidget()
        widget.resize(160, 80)
        widget.show()
        self.app.processEvents()
        handle = reveal(widget, RevealDirection.FROM_RIGHT, 1000)

        widget.deleteLater()
        self._flush_deferred_deletes()

        self.assertFalse(handle.is_running())
        self.assertIsNone(handle.effect())
        self.assertIsNone(handle.animation())
        handle.cancel()

    def test_hidden_widget_lands_directly(self):
        widget = self._widget(visible=False)
        self.assertIsNone(reveal(widget, RevealDirection.FROM_RIGHT, 1000))
        self.assertIsNone(widget.graphicsEffect())

    def test_reveal_does_not_change_widget_geometry(self):
        widget = self._widget()
        before = widget.geometry()
        reveal(widget, RevealDirection.FROM_RIGHT, MOTION_REVEAL_MS)
        self.assertEqual(widget.geometry(), before, "过渡只影响绘制，不改变布局")

    def test_widget_must_be_a_widget(self):
        with self.assertRaises(TypeError):
            reveal(object())

    def test_direction_and_duration_are_validated(self):
        widget = self._widget()

        with self.assertRaises(ValueError):
            reveal(widget, "up", 220)
        with self.assertRaises(TypeError):
            reveal(widget, None, 220)

        with self.assertRaises(TypeError):
            reveal(widget, RevealDirection.FROM_RIGHT, True)
        with self.assertRaises(TypeError):
            reveal(widget, RevealDirection.FROM_RIGHT, 220.0)
        with self.assertRaises(ValueError):
            reveal(widget, RevealDirection.FROM_RIGHT, -1)

    def test_invalid_arguments_do_not_disturb_a_running_transition(self):
        widget = self._widget()
        handle = reveal(widget, RevealDirection.FROM_RIGHT, 1000)

        with self.assertRaises(ValueError):
            reveal(widget, "up", 220)

        self.assertTrue(handle.is_running())


if __name__ == "__main__":
    unittest.main()
