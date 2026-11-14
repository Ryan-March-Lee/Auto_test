"""测量读数条：结构、活动态与主题选择器。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from presentation.qt.measurement_readout import MeasurementReadout, ReadoutMetric
from presentation.qt.theme import build_stylesheet


def _destroy(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class ReadoutMetricTests(unittest.TestCase):
    def test_metric_carries_label_value_unit_and_delta(self):
        metric = ReadoutMetric("线缆1", "0.44", "dB", "▼ 0.02")

        self.assertEqual(metric.label, "线缆1")
        self.assertEqual(metric.value, "0.44")
        self.assertEqual(metric.unit, "dB")
        self.assertEqual(metric.delta, "▼ 0.02")

    def test_metric_rejects_non_string_fields(self):
        with self.assertRaises(TypeError):
            ReadoutMetric("线缆1", 0.44)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            ReadoutMetric("线缆1", "0.44", None)  # type: ignore[arg-type]


class MeasurementReadoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _track(self, widget):
        self.addCleanup(_destroy, widget)
        return widget

    def test_starts_with_a_placeholder_and_no_live_badge(self):
        readout = self._track(MeasurementReadout())

        self.assertEqual(readout.frequency_text(), "")
        self.assertEqual(readout.unit_text(), "")
        self.assertEqual(readout.metrics(), ())
        self.assertEqual(readout.value_label.text(), "—")
        self.assertFalse(readout.is_active())
        self.assertTrue(readout.live_badge.isHidden())

    def test_set_reading_updates_frequency_and_chips(self):
        readout = self._track(MeasurementReadout())
        metrics = (
            ReadoutMetric("线缆1", "0.44", "dB", "▼ 0.02"),
            ReadoutMetric("线缆2", "0.53", "dB", "▲ 0.02"),
        )

        readout.set_reading("2.45", "GHz", metrics, active=False)

        self.assertEqual(readout.frequency_text(), "2.45")
        self.assertEqual(readout.unit_text(), "GHz")
        self.assertEqual(readout.metrics(), metrics)
        self.assertEqual(readout.value_label.text(), "2.45")
        self.assertEqual(readout.unit_label.text(), "GHz")
        self.assertEqual(
            [chip.label.text() for chip in readout._chips],
            ["线缆1", "线缆2"],
        )
        self.assertEqual(
            [chip.value.text() for chip in readout._chips],
            ["0.44", "0.53"],
        )
        self.assertEqual(readout._chips[0].delta.text(), "▼ 0.02")
        self.assertFalse(readout._chips[0].delta.isHidden())
        self.assertTrue(readout._chips[1].isVisibleTo(readout))

    def test_fewer_metrics_hide_the_extra_chips(self):
        readout = self._track(MeasurementReadout())
        readout.set_reading(
            "2.45",
            "GHz",
            (
                ReadoutMetric("线缆1", "0.44", "dB"),
                ReadoutMetric("线缆2", "0.53", "dB"),
            ),
        )

        readout.set_reading("2.50", "GHz", (ReadoutMetric("线缆1", "0.46", "dB"),))

        self.assertEqual(len(readout.metrics()), 1)
        self.assertFalse(readout._chips[1].isVisibleTo(readout))

    def test_active_state_controls_the_live_badge_and_animation(self):
        readout = self._track(MeasurementReadout())

        readout.set_active(True)
        self.assertTrue(readout.is_active())
        self.assertFalse(readout.live_badge.isHidden())
        self.assertTrue(readout.live_badge.dot.animations_enabled())

        readout.set_animations_enabled(False)
        self.assertFalse(readout.animations_enabled())
        self.assertFalse(readout.live_badge.dot.animations_enabled())
        self.assertEqual(readout._content_effect.opacity(), 1.0)

        readout.set_active(False)
        self.assertFalse(readout.is_active())
        self.assertTrue(readout.live_badge.isHidden())

    def test_wide_readout_puts_four_chips_on_one_row(self):
        readout = self._track(MeasurementReadout())
        readout.resize(800, 120)
        readout.set_reading(
            "2.45",
            "GHz",
            tuple(
                ReadoutMetric(f"线缆{index}", "0.44", "dB")
                for index in range(1, 5)
            ),
        )
        self.app.processEvents()

        positions = [
            readout._chip_grid.getItemPosition(index)[:2]
            for index in range(4)
        ]

        self.assertEqual(positions, [(0, 0), (0, 1), (0, 2), (0, 3)])

    def test_narrow_readout_wraps_four_chips_into_two_columns(self):
        readout = self._track(MeasurementReadout())
        readout.resize(500, 180)
        readout.set_reading(
            "2.45",
            "GHz",
            tuple(
                ReadoutMetric(f"线缆{index}", "0.44", "dB")
                for index in range(1, 5)
            ),
        )
        self.app.processEvents()

        positions = [
            readout._chip_grid.getItemPosition(index)[:2]
            for index in range(4)
        ]

        self.assertEqual(positions, [(0, 0), (0, 1), (1, 0), (1, 1)])

    def test_invalid_payloads_are_rejected(self):
        readout = self._track(MeasurementReadout())

        with self.assertRaises(TypeError):
            readout.set_reading(2.45)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            readout.set_reading("2.45", 1)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            readout.set_reading("2.45", "GHz", ("0.44",))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            readout.set_active("yes")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            readout.set_animations_enabled(1)  # type: ignore[arg-type]

    def test_grab_renders_without_errors(self):
        readout = self._track(MeasurementReadout())
        readout.resize(520, 110)
        readout.set_reading(
            "2.45",
            "GHz",
            (
                ReadoutMetric("线缆1", "0.44", "dB"),
                ReadoutMetric("线缆2", "0.53", "dB"),
                ReadoutMetric("线缆3", "0.49", "dB"),
                ReadoutMetric("线缆4", "0.57", "dB"),
            ),
            active=True,
        )
        readout.show()
        self.app.processEvents()

        image = readout.grab().toImage()

        self.assertFalse(image.isNull())
        self.assertGreater(image.width(), 0)
        self.assertGreater(image.height(), 0)


class MeasurementReadoutThemeTests(unittest.TestCase):
    def test_stylesheet_contains_readout_selectors(self):
        stylesheet = build_stylesheet()

        for selector in (
            "QFrame#measurementReadout",
            "QLabel#measurementReadoutCaption",
            "QLabel#measurementReadoutValue",
            "QLabel#measurementReadoutUnit",
            "QFrame#measurementReadoutChip",
            "QLabel#measurementReadoutChipLabel",
            "QLabel#measurementReadoutChipValue",
            "QLabel#measurementReadoutChipUnit",
            "QLabel#measurementReadoutChipDelta",
            "QFrame#measurementReadoutLive",
            "QLabel#measurementReadoutLiveText",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, stylesheet)


if __name__ == "__main__":
    unittest.main()
