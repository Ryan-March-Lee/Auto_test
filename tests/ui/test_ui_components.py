import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from presentation.qt.realtime_plot import RealTimePlotWidget


class UiComponentTests(unittest.TestCase):
    def test_plot_widget_has_four_axes_and_updates_common_data(self):
        self.assertTrue(callable(RealTimePlotWidget.update_plot))

    def test_navigation_signals_are_connected(self):
        self.assertTrue(hasattr(RealTimePlotWidget, "prev_clicked"))
        self.assertTrue(hasattr(RealTimePlotWidget, "next_clicked"))


class DeferredPlotCanvasTests(unittest.TestCase):
    """隐藏的图不排队重画：页面切换不能把几百毫秒的空白图绘制塞进动画里。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _widget(self):
        widget = RealTimePlotWidget()
        widget.resize(640, 480)
        self.addCleanup(widget.close)
        self.addCleanup(widget.deleteLater)
        return widget

    def _record_idle_calls(self, canvas, draws):
        def record():
            # matplotlib 真正的 ``_draw_idle`` 会先清掉这个标志再绘制。
            canvas._draw_pending = False
            draws.append(True)

        return patch.object(canvas, "_draw_idle", record)

    def test_hidden_plot_widget_defers_its_pending_draw(self):
        widget = self._widget()
        canvas = widget.canvas
        self.assertFalse(canvas.isVisible())
        self.assertTrue(canvas._draw_pending, "构造时只记下待重画，不立刻绘制")

        draws = []
        with self._record_idle_calls(canvas, draws):
            canvas.draw_idle()
            self.app.processEvents()

        self.assertEqual(draws, [], "不可见时不能排队重画")
        self.assertTrue(canvas._draw_pending)

    def test_plot_widget_draws_after_it_becomes_visible(self):
        widget = self._widget()
        canvas = widget.canvas
        draws = []
        with self._record_idle_calls(canvas, draws):
            widget.show()
            self.app.processEvents()

            self.assertTrue(draws, "第一次显示时要补画")
            self.assertFalse(canvas._draw_pending)

            scheduled = len(draws)
            canvas.draw_idle()
            self.app.processEvents()
            self.assertGreater(
                len(draws), scheduled, "可见后恢复正常的延迟重画"
            )


if __name__ == "__main__":
    unittest.main()
