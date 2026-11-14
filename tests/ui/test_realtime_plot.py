"""实时图表的 P2 编码：最新点、值芯片、目标带、节流与胶片条。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from presentation.qt.realtime_plot import PlotTargetBand, RealTimePlotWidget
from presentation.qt.theme import build_stylesheet


def _destroy(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


_SWEEP = {
    "frequency": 1.4,
    "sweep_data": {
        "input_power_dut": [-20, -15, -10, -5, 0],
        "output_power_dut": [10.0, 15.2, 20.1, 24.8, 28.3],
        "gain": [30.0, 30.2, 30.1, 29.8, 28.3],
        "efficiency": [10.0, 20.5, 33.4, 44.7, 51.2],
        "dc_power": [12.0, 18.5, 28.4, 41.2, 58.8],
    },
}


class _PlotCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _plot(self, *, nav=False):
        widget = RealTimePlotWidget(show_nav_buttons=nav)
        self.addCleanup(_destroy, widget)
        self.addCleanup(widget._draw_timer.stop)
        return widget


class LatestPointTests(_PlotCase):
    def test_each_subplot_shows_the_latest_value_in_its_legend(self):
        plot = self._plot()

        plot.update_plot(dict(_SWEEP))

        legends = [axis.get_legend().get_texts()[0].get_text() for axis in plot.figure.axes]
        self.assertEqual(
            legends,
            [
                "输出功率 28.3 dBm",
                "增益 28.3 dB",
                "效率 51.2 %",
                "DC功耗 58.80 W",
            ],
        )

    def test_latest_point_is_ringed_and_annotated(self):
        plot = self._plot()

        plot.update_plot(dict(_SWEEP))

        for axis in plot.figure.axes:
            self.assertEqual(len(axis.lines), 2, "曲线 + 最新点圆环")
            self.assertEqual(len(axis.texts), 1, "最新点旁的数值标注")
        self.assertEqual(plot.ax2.texts[0].get_text(), "28.3")
        self.assertEqual(plot.ax4.texts[0].get_text(), "58.80")

    def test_empty_panels_explain_the_waiting_state(self):
        plot = self._plot()

        plot.update_plot({"frequency": 1.0, "sweep_data": {}})

        for axis in plot.figure.axes:
            self.assertEqual([text.get_text() for text in axis.texts], ["等待数据"])

    def test_partial_sweep_only_draws_the_panels_it_can(self):
        plot = self._plot()

        plot.update_plot(
            {
                "frequency": 1.0,
                "sweep_data": {"input_power_dut": [0.0], "output_power_dut": [10.0]},
            }
        )

        self.assertEqual(len(plot.ax1.lines), 2)
        self.assertEqual([text.get_text() for text in plot.ax4.texts], ["等待数据"])

    def test_malformed_payload_does_not_raise(self):
        plot = self._plot()

        plot.update_plot({"frequency": 1.0, "sweep_data": "not-a-mapping"})
        plot.update_plot({"frequency": 1.0, "sweep_data": {"gain": ["bad"]}})

        self.assertEqual([text.get_text() for text in plot.ax1.texts], ["等待数据"])


class TargetBandTests(_PlotCase):
    def test_no_band_is_drawn_without_a_target(self):
        plot = self._plot()

        plot.update_plot(dict(_SWEEP))

        for axis in plot.figure.axes:
            self.assertEqual(len(axis.patches), 0)

    def test_band_only_appears_on_its_panel(self):
        plot = self._plot()
        plot.set_target_bands(
            [PlotTargetBand(1, 28.0, 30.0, "目标增益带")]
        )

        plot.update_plot(dict(_SWEEP))

        self.assertEqual(len(plot.ax2.patches), 1)
        self.assertEqual(len(plot.ax1.patches), 0)
        self.assertIn(
            "目标增益带", [text.get_text() for text in plot.ax2.texts]
        )

    def test_target_bands_are_readable_and_validated(self):
        plot = self._plot()
        band = PlotTargetBand(0, 10.0, 12.0)
        plot.set_target_bands([band])

        self.assertEqual(plot.target_bands(), (band,))
        plot.set_target_bands(())
        self.assertEqual(plot.target_bands(), ())

        with self.assertRaises(TypeError):
            plot.set_target_bands("band")
        with self.assertRaises(TypeError):
            plot.set_target_bands([object()])
        with self.assertRaises(ValueError):
            PlotTargetBand(0, 12.0, 10.0)
        with self.assertRaises(ValueError):
            PlotTargetBand(4, 10.0, 12.0)
        with self.assertRaises(TypeError):
            PlotTargetBand(0, "low", 12.0)
        with self.assertRaises(ValueError):
            PlotTargetBand(0, 10.0, float("nan"))
        with self.assertRaises(TypeError):
            PlotTargetBand(0, 10.0, 12.0, 3)


class DrawThrottleTests(_PlotCase):
    def test_rapid_updates_draw_at_most_once_per_interval(self):
        plot = self._plot()
        draws = []
        plot.canvas.draw_idle = lambda: draws.append(True)

        plot.update_plot(dict(_SWEEP))
        plot.update_plot(dict(_SWEEP))

        self.assertEqual(draws, [True], "第二次更新只排队，不立即重绘")

        plot._flush_draw()

        self.assertEqual(draws, [True, True], "冷却结束只补画最新一帧")

    def test_flush_without_a_pending_update_does_not_draw(self):
        plot = self._plot()
        draws = []
        plot.canvas.draw_idle = lambda: draws.append(True)
        plot.update_plot(dict(_SWEEP))

        plot._draw_timer.stop()
        plot._draw_ready = True
        plot._draw_pending = False
        plot._flush_draw()

        self.assertEqual(draws, [True])


class FilmstripOnPlotTests(_PlotCase):
    def test_navigation_plot_carries_a_filmstrip(self):
        plot = self._plot(nav=True)

        plot.set_frequencies([1.0, 1.2, 1.4], 2)

        strip = plot.frequency_filmstrip
        self.assertFalse(strip.isHidden())
        self.assertEqual(strip.frequencies(), (1.0, 1.2, 1.4))
        self.assertEqual(strip.current_index(), 2)

    def test_chip_click_is_forwarded_as_a_frequency(self):
        plot = self._plot(nav=True)
        received = []
        plot.frequency_selected.connect(received.append)
        plot.set_frequencies([1.0, 1.2], 0)

        plot.frequency_filmstrip.chips()[1].click()

        self.assertEqual(received, [1.2])

    def test_plot_without_navigation_ignores_frequencies(self):
        plot = self._plot(nav=False)

        plot.set_frequencies([1.0, 1.2], 0)

        self.assertFalse(hasattr(plot, "frequency_filmstrip"))


class PlotThemeTests(unittest.TestCase):
    def test_stylesheet_contains_filmstrip_selectors(self):
        stylesheet = build_stylesheet()

        self.assertIn("QPushButton#frequencyChip", stylesheet)
        self.assertIn('QPushButton#frequencyChip[current="true"]', stylesheet)


if __name__ == "__main__":
    unittest.main()
