import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from presentation.qt.realtime_buffer import RealtimeMeasurementBuffer
from presentation.qt.status_panel import StatusPanel


class RealtimeBufferTests(unittest.TestCase):
    def test_sorts_frequencies_and_follows_latest_until_user_browses(self):
        buffer = RealtimeMeasurementBuffer()
        buffer.store({"frequency": "5.8", "value": 1})
        buffer.store({"frequency": "2.4", "value": 2})
        self.assertEqual(buffer.frequencies, [2.4, 5.8])
        self.assertEqual(buffer.current()["value"], 1)
        buffer.previous()
        self.assertTrue(buffer.user_browsing)
        buffer.store({"frequency": "6.0", "value": 3})
        self.assertEqual(buffer.current()["value"], 2)
        buffer.next()
        buffer.next()
        self.assertFalse(buffer.user_browsing)
        buffer.store({"frequency": "6.2", "value": 4})
        self.assertEqual(buffer.current()["value"], 4)

    def test_invalid_and_clear(self):
        buffer = RealtimeMeasurementBuffer()
        self.assertFalse(buffer.store({"frequency": "bad"}))
        self.assertFalse(buffer.store({"frequency": float("nan")}))
        buffer.store({"frequency": "2.40", "value": 1})
        buffer.store({"frequency": 2.4, "value": 2})
        self.assertEqual(buffer.frequencies, [2.4])
        self.assertEqual(buffer.current()["value"], 2)
        frequencies = buffer.frequencies
        frequencies.append(9.9)
        data = buffer.data
        data["2.4"]["value"] = 99
        self.assertEqual(buffer.frequencies, [2.4])
        self.assertEqual(buffer.current()["value"], 2)
        buffer.store({"frequency": 1.0})
        buffer.clear()
        self.assertEqual(buffer.data, {})
        self.assertEqual(buffer.frequencies, [])
        self.assertFalse(buffer.user_browsing)


class StatusPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_log_scroll_and_clear(self):
        panel = StatusPanel()
        panel.add_log_message("first")
        self.assertIn("first", panel.log_text.toPlainText())
        panel.log_user_scrolling = True
        panel.add_log_message("second")
        self.assertTrue(panel.log_user_scrolling)
        panel.clear_log()
        self.assertEqual(panel.log_text.toPlainText(), "")
        self.assertFalse(panel.log_user_scrolling)


if __name__ == "__main__":
    unittest.main()
