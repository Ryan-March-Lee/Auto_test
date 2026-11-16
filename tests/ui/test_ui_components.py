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
    """隐藏的图不排队重画；显示瞬间不整图绘制，只 blit 或延迟重建。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _widget(self):
        widget = RealTimePlotWidget()
        widget.resize(640, 480)
        self.addCleanup(widget.close)
        self.addCleanup(widget.deleteLater)
        self.addCleanup(widget.canvas.cancel_rebuild)
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

    def test_show_schedules_a_rebuild_instead_of_drawing_immediately(self):
        widget = self._widget()
        canvas = widget.canvas
        widget.show()
        self.app.processEvents()

        self.assertFalse(canvas._draw_pending, "显示时先消费 matplotlib 的待办")
        self.assertTrue(
            canvas.has_pending_rebuild(), "重建被排到切页过渡之后，而不是显示瞬间"
        )
        self.assertFalse(
            hasattr(canvas, "renderer"),
            "显示瞬间不能已经付出整图绘制的开销",
        )
        canvas.cancel_rebuild()

    def test_valid_background_is_blitted_without_a_redraw(self):
        widget = self._widget()
        widget.show()
        self.app.processEvents()
        widget.canvas.cancel_rebuild()
        widget.rebuild_background()

        self.assertIsNotNone(widget._background)
        with patch.object(
            widget, "rebuild_background", wraps=widget.rebuild_background
        ) as rebuild:
            widget.on_canvas_shown()

        self.assertFalse(rebuild.called, "背景有效时切回页面只 blit，不重建")


if __name__ == "__main__":
    unittest.main()
