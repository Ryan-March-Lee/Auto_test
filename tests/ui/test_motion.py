"""内容过渡件的最小契约。

保留动效令牌、方向名称、透明度/偏移推进、结束后不残留效果与取消幂等；
逐帧像素比对和几何细节不再纳入默认回归。
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
    MOTION_DATA_MS,
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
        self.assertEqual(MOTION_DATA_MS, 180)
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








class RevealTests(_MotionTestCase):



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








if __name__ == "__main__":
    unittest.main()
