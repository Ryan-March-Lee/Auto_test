"""内容过渡件的契约（重构计划步骤 2）。

这里锁定用户能看见、后续步骤会依赖的四件事：

- 动效令牌集中在 :mod:`presentation.qt.theme`，页面不再各写时长；
- 效果只做透明度和水平偏移，进度从 0 走到 1 时偏移归零；
- 过渡结束后控件上不再残留 ``QGraphicsEffect``，页面自己的效果不会被覆盖；
- ``duration_ms=0`` 直接落到终态，不创建效果也不启动计时器；重复调用先中止上一条。

动画等待使用很短的时长（30ms），避免离屏用例为 220ms 的真实时长排队。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (  # noqa: E402
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QRectF,
)
from PySide6.QtGui import QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QGraphicsEffect, QWidget  # noqa: E402

from presentation.qt.motion import (  # noqa: E402
    RevealDirection,
    SlideFadeEffect,
    reveal,
)
from presentation.qt.theme import (  # noqa: E402
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


class MotionTokenTests(unittest.TestCase):
    def test_tokens_match_the_reconstruction_plan(self):
        self.assertEqual(MOTION_REVEAL_MS, 220)
        self.assertEqual(MOTION_STAGE_MS, 180)
        self.assertEqual(MOTION_COMPLETE_MS, 260)
        self.assertEqual(MOTION_SLIDE_PX, 10)

    def test_direction_names_are_stable_strings(self):
        self.assertEqual(RevealDirection.FROM_LEFT.value, "left")
        self.assertEqual(RevealDirection.FROM_RIGHT.value, "right")


class SlideFadeEffectTests(unittest.TestCase):
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

    def test_bounding_rect_covers_the_slid_side(self):
        effect = SlideFadeEffect()
        self.addCleanup(effect.deleteLater)
        source = QRectF(0.0, 0.0, 100.0, 50.0)

        effect.set_offset(MOTION_SLIDE_PX)
        self.assertEqual(effect.boundingRectFor(source), QRectF(0.0, 0.0, 110.0, 50.0))
        effect.set_offset(-MOTION_SLIDE_PX)
        self.assertEqual(
            effect.boundingRectFor(source), QRectF(-10.0, 0.0, 110.0, 50.0)
        )
        effect.set_offset(0.0)
        self.assertEqual(effect.boundingRectFor(source), source)


class RevealTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _widget(self):
        widget = QWidget()
        widget.resize(160, 80)
        widget.show()
        self.addCleanup(_destroy, widget)
        self.app.processEvents()
        return widget

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

        handle.animation().setCurrentTime(MOTION_REVEAL_MS // 2)
        self.app.processEvents()
        effect = widget.graphicsEffect()
        self.assertGreater(effect.opacity(), 0.0)
        self.assertLess(effect.opacity(), 1.0)
        self.assertGreater(effect.offset(), 0.0)
        self.assertLess(effect.offset(), float(MOTION_SLIDE_PX))

    def test_animation_finishes_without_residual_effect(self):
        widget = self._widget()
        reveal(widget, RevealDirection.FROM_LEFT, 30)
        self.assertIsNotNone(widget.graphicsEffect())

        QTest.qWait(80)

        self.assertIsNone(
            widget.graphicsEffect(), "过渡结束后必须摘除效果，不能长期拖慢页面"
        )

    def test_zero_duration_lands_directly_without_a_timer(self):
        widget = self._widget()
        self.assertIsNone(reveal(widget, duration_ms=0))
        self.assertIsNone(widget.graphicsEffect())

        running = reveal(widget, RevealDirection.FROM_RIGHT, 1000)
        self.assertTrue(running.is_running())
        self.assertIsNone(reveal(widget, duration_ms=0))
        self.assertFalse(running.is_running())
        self.assertIsNone(widget.graphicsEffect())

    def test_a_second_reveal_replaces_the_first(self):
        widget = self._widget()
        first = reveal(widget, RevealDirection.FROM_RIGHT, 1000)
        first_effect = first.effect()

        second = reveal(widget, RevealDirection.FROM_LEFT, 1000)

        self.assertFalse(first.is_running(), "新过渡开始前必须停止上一条")
        self.assertIsNot(widget.graphicsEffect(), first_effect)
        self.assertIs(widget.graphicsEffect(), second.effect())
        self.assertEqual(second.effect().offset(), -float(MOTION_SLIDE_PX))

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

    def test_effect_can_be_drawn_without_changing_widget_geometry(self):
        widget = self._widget()
        before = widget.geometry()
        handle = reveal(widget, RevealDirection.FROM_RIGHT, MOTION_REVEAL_MS)
        handle.animation().setCurrentTime(MOTION_REVEAL_MS // 3)

        image = widget.grab()

        self.assertFalse(image.isNull())
        self.assertEqual(widget.geometry(), before, "过渡只影响绘制，不改变布局")


if __name__ == "__main__":
    unittest.main()
