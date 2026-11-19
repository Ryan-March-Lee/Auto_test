"""线损监控台：四等分结构、统计写入、占位态与主题选择器。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from presentation.qt.cable_loss_monitor import CableLossMonitor, CableLossStats
from presentation.qt.theme import build_stylesheet

CABLE_LABELS = ("线缆1", "线缆2", "线缆3", "线缆4")


def _destroy(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class CableLossStatsTests(unittest.TestCase):
    def test_empty_values_stay_empty(self):
        stats = CableLossStats.from_values([])

        self.assertIsNone(stats.maximum)
        self.assertIsNone(stats.minimum)
        self.assertIsNone(stats.average)

    def test_values_produce_maximum_minimum_and_mean(self):
        stats = CableLossStats.from_values([1.0, 1.2, 1.1])

        self.assertAlmostEqual(stats.maximum, 1.2)
        self.assertAlmostEqual(stats.minimum, 1.0)
        self.assertAlmostEqual(stats.average, 1.1)

    def test_single_value_is_its_own_min_max_and_average(self):
        stats = CableLossStats.from_values([1.234])

        self.assertAlmostEqual(stats.maximum, 1.234)
        self.assertAlmostEqual(stats.minimum, 1.234)
        self.assertAlmostEqual(stats.average, 1.234)

    def test_rejects_values_that_are_not_finite_numbers(self):
        with self.assertRaises(TypeError):
            CableLossStats.from_values(["1.0"])  # type: ignore[list-item]
        with self.assertRaises(TypeError):
            CableLossStats.from_values([True])  # type: ignore[list-item]
        with self.assertRaises(ValueError):
            CableLossStats.from_values([float("nan")])
        with self.assertRaises(ValueError):
            CableLossStats.from_values([float("inf")])

    def test_rejects_an_incomplete_or_misordered_triplet(self):
        with self.assertRaises(ValueError):
            CableLossStats(1.0, None, 0.5)
        with self.assertRaises(ValueError):
            CableLossStats(1.0, 0.2, 1.5)
        with self.assertRaises(TypeError):
            CableLossStats("1.0", 0.5, 0.7)  # type: ignore[arg-type]


class CableLossMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _track(self, widget):
        self.addCleanup(_destroy, widget)
        return widget

    def test_starts_with_four_equal_cells_and_placeholders(self):
        monitor = self._track(CableLossMonitor(CABLE_LABELS))

        self.assertEqual(monitor.cell_count(), 4)
        self.assertEqual(
            [monitor.cell(index).name_text() for index in range(4)],
            list(CABLE_LABELS),
        )
        self.assertEqual(
            [monitor.layout().stretch(index) for index in range(4)], [1, 1, 1, 1]
        )
        for index in range(4):
            for field in ("maximum", "minimum", "average"):
                self.assertEqual(monitor.value_text(index, field), "—")

    def test_set_stats_writes_each_cable_cell(self):
        monitor = self._track(CableLossMonitor(CABLE_LABELS))
        monitor.set_stats(
            (
                CableLossStats.from_values([1.0, 1.2]),
                CableLossStats.from_values([2.0]),
                CableLossStats(),
                CableLossStats(),
            )
        )

        self.assertEqual(monitor.value_text(0, "maximum"), "1.200")
        self.assertEqual(monitor.value_text(0, "minimum"), "1.000")
        self.assertEqual(monitor.value_text(0, "average"), "1.100")
        self.assertEqual(monitor.value_text(1, "maximum"), "2.000")
        self.assertEqual(monitor.value_text(2, "maximum"), "—")
        self.assertEqual(monitor.stats()[0].maximum, 1.2)

    def test_clear_returns_every_cell_to_the_placeholder(self):
        monitor = self._track(CableLossMonitor(CABLE_LABELS))
        monitor.set_stats(tuple(CableLossStats.from_values([1.0]) for _ in range(4)))

        monitor.clear()

        for index in range(4):
            self.assertEqual(monitor.value_text(index, "average"), "—")
        self.assertEqual(monitor.stats(), tuple(CableLossStats() for _ in range(4)))

    def test_accessibility_text_names_every_statistic(self):
        monitor = self._track(CableLossMonitor(CABLE_LABELS))
        monitor.set_stats((CableLossStats.from_values([1.0, 1.2]),) * 4)

        text = monitor.cell(0).accessibleName()

        self.assertIn("线缆1", text)
        self.assertIn("Max 1.200", text)
        self.assertIn("Min 1.000", text)
        self.assertIn("Avg 1.100", text)

    def test_invalid_payloads_are_rejected(self):
        monitor = self._track(CableLossMonitor(CABLE_LABELS))

        with self.assertRaises(TypeError):
            monitor.set_stats("0.44")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            monitor.set_stats(({"maximum": 1.0},))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            monitor.set_stats((CableLossStats(),))
        with self.assertRaises(TypeError):
            CableLossMonitor("线缆1")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            CableLossMonitor(())
        with self.assertRaises(ValueError):
            CableLossMonitor(("",))
        with self.assertRaises(ValueError):
            monitor.value_text(0, "median")

    def test_grab_renders_without_errors(self):
        monitor = self._track(CableLossMonitor(CABLE_LABELS))
        monitor.resize(760, 108)
        monitor.set_stats(
            (
                CableLossStats.from_values([1.180, 1.234, 1.207]),
                CableLossStats.from_values([1.190, 1.240, 1.210]),
                CableLossStats.from_values([0.943, 0.955, 0.949]),
                CableLossStats.from_values([0.938, 0.951, 0.945]),
            )
        )
        monitor.show()
        self.app.processEvents()

        image = monitor.grab().toImage()

        self.assertFalse(image.isNull())
        self.assertGreater(image.width(), 0)
        self.assertGreater(image.height(), 0)


class CableLossMonitorThemeTests(unittest.TestCase):
    def test_stylesheet_contains_monitor_selectors(self):
        stylesheet = build_stylesheet()

        for selector in (
            "QFrame#cableLossMonitor",
            "QFrame#cableLossMonitorCell",
            "QLabel#cableLossMonitorCable",
            "QLabel#cableLossMonitorUnit",
            "QFrame#cableLossMonitorHairline",
            "QLabel#cableLossMonitorMetricLabel",
            "QLabel#cableLossMonitorValue",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, stylesheet)


if __name__ == "__main__":
    unittest.main()
