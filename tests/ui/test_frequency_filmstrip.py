"""频点胶片条：chip 构建、当前项高亮、点击与空态。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from presentation.qt.frequency_filmstrip import FrequencyFilmstrip, format_frequency
from presentation.qt.theme import build_stylesheet


def _destroy(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class FrequencyFormatTests(unittest.TestCase):
    def test_format_strips_trailing_zeros(self):
        self.assertEqual(format_frequency(2.4), "2.4")
        self.assertEqual(format_frequency(2.0), "2")
        self.assertEqual(format_frequency(2.450), "2.45")
        self.assertEqual(format_frequency(0.0), "0")


class FrequencyFilmstripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _strip(self):
        strip = FrequencyFilmstrip()
        self.addCleanup(_destroy, strip)
        return strip

    def test_starts_hidden_without_frequencies(self):
        strip = self._strip()

        self.assertTrue(strip.isHidden())
        self.assertEqual(strip.frequencies(), ())
        self.assertEqual(strip.current_index(), -1)
        self.assertEqual(strip.chips(), ())

    def test_set_frequencies_builds_chips_and_highlights_the_current_one(self):
        strip = self._strip()

        strip.set_frequencies([2.4, 2.45, 2.5], 1)

        self.assertFalse(strip.isHidden())
        self.assertEqual(strip.frequencies(), (2.4, 2.45, 2.5))
        self.assertEqual(strip.current_index(), 1)
        self.assertEqual(
            [chip.text() for chip in strip.chips()],
            ["✓ 2.4", "✓ 2.45", "✓ 2.5"],
        )
        self.assertEqual(
            [bool(chip.property("current")) for chip in strip.chips()],
            [False, True, False],
        )
        self.assertIn("当前", strip.chips()[1].toolTip())

    def test_clicking_a_chip_emits_the_frequency(self):
        strip = self._strip()
        strip.set_frequencies([2.4, 2.45, 2.5], 0)
        received = []
        strip.frequency_selected.connect(received.append)

        strip.chips()[2].click()

        self.assertEqual(received, [2.5])

    def test_moving_the_current_index_reuses_the_existing_chips(self):
        strip = self._strip()
        strip.set_frequencies([2.4, 2.45, 2.5], 0)
        chips = strip.chips()

        strip.set_frequencies([2.4, 2.45, 2.5], 2)

        self.assertEqual(strip.chips(), chips, "频点没变时不能重建控件")
        self.assertEqual(
            [bool(chip.property("current")) for chip in strip.chips()],
            [False, False, True],
        )

    def test_out_of_range_current_index_is_clamped(self):
        strip = self._strip()

        strip.set_frequencies([2.4, 2.45], 9)

        self.assertEqual(strip.current_index(), 1)

    def test_clear_hides_the_strip(self):
        strip = self._strip()
        strip.set_frequencies([2.4], 0)

        strip.clear()

        self.assertTrue(strip.isHidden())
        self.assertEqual(strip.chips(), ())
        self.assertEqual(strip.current_index(), -1)
        strip.clear()  # 幂等

    def test_invalid_payloads_are_rejected(self):
        strip = self._strip()

        with self.assertRaises(TypeError):
            strip.set_frequencies("2.4")
        with self.assertRaises(TypeError):
            strip.set_frequencies([True])
        with self.assertRaises(TypeError):
            strip.set_frequencies(["bad"])
        with self.assertRaises(ValueError):
            strip.set_frequencies([float("nan")])

    def test_grab_renders_without_errors(self):
        strip = self._strip()
        strip.resize(520, 30)
        strip.set_frequencies([2.4, 2.45, 2.5, 2.55], 1)
        strip.show()
        self.app.processEvents()

        image = strip.grab().toImage()

        self.assertFalse(image.isNull())
        self.assertGreater(image.width(), 0)


class FrequencyFilmstripThemeTests(unittest.TestCase):
    def test_stylesheet_contains_filmstrip_selectors(self):
        stylesheet = build_stylesheet()

        for selector in (
            "QWidget#frequencyFilmstrip",
            "QScrollArea#frequencyFilmstripScroll",
            "QPushButton#frequencyChip",
            'QPushButton#frequencyChip[current="true"]',
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, stylesheet)


if __name__ == "__main__":
    unittest.main()
