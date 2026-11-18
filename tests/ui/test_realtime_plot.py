"""实时图表的 P2 编码：最新点、图例、目标带、节流、blit 与胶片条。"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from presentation.qt.realtime_plot import (
    ALL_PANELS,
    DRIVER_MAPPING_PANELS,
    PlotTargetBand,
    RealTimePlotWidget,
)
from presentation.qt.realtime_buffer import RealtimeMeasurementBuffer
from presentation.qt.realtime_page import RealtimePageMixin
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
    def test_each_subplot_shows_a_static_metric_legend(self):
        plot = self._plot()

        plot.update_plot(dict(_SWEEP))

        # 图例只显示指标与单位，属于背景缓存；最新值在点旁标注与页面读数条里。
        legends = [axis.get_legend().get_texts()[0].get_text() for axis in plot.figure.axes]
        self.assertEqual(
            legends,
            [
                "输出功率 — dBm",
                "增益 — dB",
                "效率 — %",
                "DC功耗 — W",
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


class PanelSelectionTests(_PlotCase):
    def test_default_panels_keep_the_two_by_two_grid(self):
        plot = self._plot()

        self.assertEqual(plot.panel_keys(), ALL_PANELS)
        self.assertEqual(len(plot.figure.axes), 4)
        self.assertEqual(
            plot.figure.axes[0].get_subplotspec().get_gridspec().get_geometry(),
            (2, 2),
        )

    def test_driver_panels_are_one_row_of_two(self):
        widget = RealTimePlotWidget(panels=DRIVER_MAPPING_PANELS)
        self.addCleanup(_destroy, widget)
        self.addCleanup(widget._draw_timer.stop)
        widget.update_plot(dict(_SWEEP))

        self.assertEqual(widget.panel_keys(), ("input_output", "gain"))
        self.assertEqual(len(widget.figure.axes), 2)
        self.assertEqual(
            widget.figure.axes[0].get_subplotspec().get_gridspec().get_geometry(),
            (1, 2),
        )
        legends = [
            axis.get_legend().get_texts()[0].get_text()
            for axis in widget.figure.axes
        ]
        self.assertEqual(legends, ["输出功率 — dBm", "增益 — dB"])
        self.assertFalse(hasattr(widget, "ax3"), "收窄后不保留多余坐标轴")

    def test_switching_panels_rebuilds_the_figure_and_waits_for_new_data(self):
        plot = self._plot()
        plot.update_plot(dict(_SWEEP))

        plot.set_panels(DRIVER_MAPPING_PANELS)

        self.assertEqual(plot.panel_keys(), ("input_output", "gain"))
        self.assertEqual(len(plot.figure.axes), 2)
        self.assertEqual([text.get_text() for text in plot.ax1.texts], ["等待数据"])

        plot.set_panels(ALL_PANELS)
        self.assertEqual(len(plot.figure.axes), 4)
        self.assertTrue(hasattr(plot, "ax4"))

    def test_panel_selection_is_validated(self):
        with self.assertRaises(TypeError):
            RealTimePlotWidget(panels="input_output")
        with self.assertRaises(ValueError):
            RealTimePlotWidget(panels=())
        with self.assertRaises(ValueError):
            RealTimePlotWidget(panels=("nope",))
        with self.assertRaises(ValueError):
            RealTimePlotWidget(panels=("gain", "gain"))

    def test_switching_panels_on_a_visible_widget_keeps_rendering(self):
        plot = self._plot()
        plot.resize(640, 480)
        plot.show()
        self.app.processEvents()
        plot.canvas.cancel_rebuild()
        plot.rebuild_background()

        plot.set_panels(DRIVER_MAPPING_PANELS)
        plot.update_plot(dict(_SWEEP))
        self.app.processEvents()
        plot.canvas.cancel_rebuild()
        plot.rebuild_background()

        self.assertIsNotNone(plot._background)
        self.assertEqual(len(plot.figure.axes), 2)
        self.assertTrue(all(len(axis.lines) == 2 for axis in plot.figure.axes))


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
    def test_rapid_updates_render_at_most_once_per_interval(self):
        plot = self._plot()
        renders = []
        plot._render = lambda: renders.append(True)

        plot.update_plot(dict(_SWEEP))
        plot.update_plot(dict(_SWEEP))

        self.assertEqual(renders, [True], "第二次更新只排队，不立即渲染")

        plot._flush_draw()

        self.assertEqual(renders, [True, True], "冷却结束只补画最新一帧")

    def test_flush_without_a_pending_update_does_not_render(self):
        plot = self._plot()
        renders = []
        plot._render = lambda: renders.append(True)
        plot.update_plot(dict(_SWEEP))

        plot._draw_timer.stop()
        plot._draw_ready = True
        plot._draw_pending = False
        plot._flush_draw()

        self.assertEqual(renders, [True])


class BlitRenderTests(_PlotCase):
    """背景缓存 + blit：稳态更新不重画整图，只有量程扩展才重建一次。"""

    def _shown_plot(self):
        plot = self._plot()
        plot.resize(700, 440)
        plot.show()
        self.app.processEvents()
        plot.canvas.cancel_rebuild()
        plot.rebuild_background()
        return plot

    def test_steady_updates_within_the_view_only_blit(self):
        plot = self._shown_plot()
        plot.update_plot(dict(_SWEEP))
        plot._render()  # 首次数据：移除等待提示、扩展量程，允许一次重建

        with patch.object(
            plot, "rebuild_background", wraps=plot.rebuild_background
        ) as rebuild:
            for _ in range(20):
                plot.update_plot(dict(_SWEEP))
                plot._render()

        self.assertEqual(rebuild.call_count, 0, "同一量程内的更新只 blit")

    def test_range_expansion_rebuilds_once(self):
        plot = self._shown_plot()
        plot.update_plot(dict(_SWEEP))
        plot._render()

        bigger = dict(_SWEEP)
        bigger["sweep_data"] = dict(_SWEEP["sweep_data"])
        bigger["sweep_data"]["dc_power"] = [500.0] * 5

        with patch.object(
            plot, "rebuild_background", wraps=plot.rebuild_background
        ) as rebuild:
            plot.update_plot(bigger)
            plot._render()

        self.assertEqual(rebuild.call_count, 1)

    def test_hidden_updates_do_not_rebuild(self):
        plot = self._shown_plot()
        plot.update_plot(dict(_SWEEP))
        plot._render()
        plot.hide()
        self.app.processEvents()

        with patch.object(
            plot, "rebuild_background", wraps=plot.rebuild_background
        ) as rebuild:
            for _ in range(5):
                plot.update_plot(dict(_SWEEP))
                plot._render()

        self.assertEqual(rebuild.call_count, 0)
        self.assertTrue(plot._render_pending, "隐藏页只记待办，等显示后再画")

    def test_blit_matches_full_redraw(self):
        import numpy as np

        plot = self._shown_plot()
        plot.update_plot(dict(_SWEEP))
        plot._render()
        plot._render()

        blitted = np.asarray(plot.canvas.buffer_rgba()).copy()
        plot.canvas.draw()
        full = np.asarray(plot.canvas.buffer_rgba())

        self.assertLess(float(np.mean(blitted != full)), 0.001)

    def test_growing_sweep_settles_instead_of_rebuilding_per_point(self):
        """一条扫频曲线越扫越长时只扩界几次，不每个点都整图重绘一遍。"""
        plot = self._shown_plot()
        powers = [-30, -24, -20.5, -17.4, -14.8, -12.6, -10.7, -9.1, -7.7, -6.5]

        with patch.object(
            plot, "rebuild_background", wraps=plot.rebuild_background
        ) as rebuild:
            for index in range(1, len(powers) + 1):
                points = powers[:index]
                plot.update_plot(
                    {
                        "frequency": 1.0,
                        "sweep_data": {
                            "input_power_sg": points,
                            "output_power_driver": [value + 11 for value in points],
                            "gain": [11.0] * index,
                        },
                    }
                )
                plot._render()

        self.assertLessEqual(
            rebuild.call_count, 4, "扩界要为后续扫描点留余量，不能逐点重建"
        )

    def test_new_frequency_does_not_shrink_the_view(self):
        plot = self._shown_plot()
        plot.update_plot(
            {
                "frequency": 1.0,
                "sweep_data": {
                    "input_power_sg": [-30.0, -10.0],
                    "output_power_driver": [-19.0, 1.0],
                },
            }
        )
        plot._render()
        wide = plot.ax1.get_xlim()

        plot.update_plot(
            {
                "frequency": 1.1,
                "sweep_data": {
                    "input_power_sg": [-12.0],
                    "output_power_driver": [-1.0],
                },
            }
        )
        plot._render()

        low, high = plot.ax1.get_xlim()
        self.assertLessEqual(low, wide[0])
        self.assertGreaterEqual(high, wide[1])


class NavigationStateTests(_PlotCase):
    """频点浏览与“下一个”按钮：最后一个频点上仍可回到自动跟随。"""

    @staticmethod
    def _host(plot):
        class Host(RealtimePageMixin):
            def __init__(self, widget):
                self.realtime_buffer = RealtimeMeasurementBuffer()
                self._realtime_plot_widget = widget

            def _update_plot(self, data):
                return None

        return Host(plot)

    def test_pinned_last_frequency_keeps_next_enabled_to_resume_following(self):
        plot = self._plot(nav=True)
        host = self._host(plot)
        for frequency in (1.0, 1.1):
            host.realtime_buffer.store({"frequency": frequency})

        host.realtime_buffer.select(1)
        host._update_navigation()

        self.assertTrue(plot.nav_next_btn.isEnabled())
        self.assertIn("跟随", plot.nav_next_btn.toolTip())

        host.next_frequency()

        self.assertFalse(host.realtime_buffer.user_browsing)
        self.assertFalse(plot.nav_next_btn.isEnabled())
        self.assertEqual(plot.nav_next_btn.toolTip(), "下一个频点")


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

    def test_appending_a_frequency_keeps_the_existing_chips(self):
        plot = self._plot(nav=True)
        plot.set_frequencies([1.0, 1.2], 1)
        strip = plot.frequency_filmstrip
        first = strip.chips()[0]

        plot.set_frequencies([1.0, 1.2, 1.4], 2)

        self.assertEqual(len(strip.chips()), 3)
        self.assertIs(strip.chips()[0], first, "新频点只补建自己的 chip")
        self.assertEqual(strip.current_index(), 2)
        self.assertEqual(
            [chip.text() for chip in strip.chips()],
            ["✓ 1", "✓ 1.2", "✓ 1.4"],
        )

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
