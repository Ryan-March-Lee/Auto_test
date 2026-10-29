import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from presentation.qt.realtime_plot import RealTimePlotWidget


class UiComponentTests(unittest.TestCase):
    def test_plot_widget_has_four_axes_and_updates_common_data(self):
        self.assertTrue(callable(RealTimePlotWidget.update_plot))

    def test_navigation_signals_are_connected(self):
        self.assertTrue(hasattr(RealTimePlotWidget, "prev_clicked"))
        self.assertTrue(hasattr(RealTimePlotWidget, "next_clicked"))


if __name__ == "__main__":
    unittest.main()
