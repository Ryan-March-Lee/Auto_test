import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import (
    QApplication,
    QProgressBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from presentation.qt.realtime_buffer import RealtimeMeasurementBuffer
from presentation.qt.status_panel import StatusPanel, StatusSlot


def _destroy(widget):
    """关闭并销毁顶层控件，再投递排队的删除（离屏用例的统一收尾方式）。"""
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


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

    def test_select_latest_frequency_resumes_following(self):
        buffer = RealtimeMeasurementBuffer()
        for frequency in (1.0, 2.0, 3.0):
            buffer.store({"frequency": frequency, "value": frequency})

        self.assertTrue(buffer.select(0))
        self.assertEqual(buffer.current()["value"], 1.0)
        self.assertTrue(buffer.user_browsing)

        self.assertTrue(buffer.select(2))
        self.assertEqual(buffer.current()["value"], 3.0)
        self.assertFalse(buffer.user_browsing)

        buffer.store({"frequency": 4.0, "value": 4.0})
        self.assertEqual(buffer.current()["value"], 4.0)

    def test_next_at_the_last_frequency_resumes_following(self):
        buffer = RealtimeMeasurementBuffer()
        for frequency in (1.0, 2.0):
            buffer.store({"frequency": frequency, "value": frequency})
        buffer.select(1)

        buffer.next()

        self.assertFalse(buffer.user_browsing, "最后一个频点上再点下一个恢复自动跟随")
        buffer.store({"frequency": 3.0, "value": 3.0})
        self.assertEqual(buffer.current()["value"], 3.0)

    def test_select_and_index_of_reject_invalid_values(self):
        buffer = RealtimeMeasurementBuffer()
        buffer.store({"frequency": 2.4})

        self.assertEqual(buffer.index_of(2.4), 0)
        self.assertEqual(buffer.index_of("2.40"), 0)
        self.assertIsNone(buffer.index_of("bad"))
        self.assertIsNone(buffer.index_of(float("inf")))
        self.assertIsNone(buffer.index_of(9.9))
        self.assertFalse(buffer.select(1))
        with self.assertRaises(TypeError):
            buffer.select(True)
        with self.assertRaises(TypeError):
            buffer.select("0")


class StatusPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _panel(self):
        panel = StatusPanel()
        self.addCleanup(_destroy, panel)
        return panel

    def test_one_line_status_shows_only_the_latest_message(self):
        panel = self._panel()

        self.assertEqual(panel.status_text(), "暂无状态")
        self.assertEqual(panel.messages(), ())
        self.assertIsNone(panel.latest_message())

        panel.add_log_message("first")
        self.assertEqual(panel.status_text(), "first")
        self.assertEqual(panel.latest_message(), "first")
        self.assertIn("first", panel.status_line.toolTip())

        panel.add_log_message("second")
        self.assertEqual(panel.status_text(), "second")
        self.assertEqual(panel.messages(), ("first", "second"))
        self.assertIn("first", panel.status_line.toolTip())
        self.assertIn("second", panel.status_line.toolTip())

    def test_multiline_message_is_compressed_on_the_line_and_kept_in_tooltip(self):
        panel = self._panel()

        panel.add_log_message("<b>错误</b> & 路径\n第二行")

        self.assertEqual(panel.status_text(), "<b>错误</b> & 路径 第二行")
        tooltip = panel.status_line.toolTip()
        self.assertTrue(tooltip.startswith("<qt>"))
        self.assertIn("&lt;b&gt;", tooltip)
        self.assertIn("&amp;", tooltip)
        self.assertIn("<br>", tooltip)

    def test_blank_message_still_counts_without_showing_an_empty_line(self):
        panel = self._panel()

        panel.add_log_message("   ")

        self.assertEqual(panel.status_text(), "—")
        self.assertEqual(panel.latest_message(), "   ")

    def test_panel_has_no_progress_bar_or_expandable_log(self):
        """进度由频点与阶段条表达；面板不再保留第二条进度或完整日志框。"""
        panel = self._panel()

        self.assertEqual(panel.findChildren(QProgressBar), [])
        self.assertEqual(panel.findChildren(QTextEdit), [])
        self.assertFalse(hasattr(panel, "progress_bar"))
        self.assertFalse(hasattr(panel, "log_text"))

    def test_clear_log_resets_the_line_and_history(self):
        panel = self._panel()
        panel.add_log_message("first")

        panel.clear_log()

        self.assertEqual(panel.status_text(), "暂无状态")
        self.assertEqual(panel.messages(), ())
        self.assertEqual(panel.status_line.toolTip(), "")

    def test_message_history_survives_remounting_into_another_page(self):
        """面板只有一份：切页只是换挂点，最近状态与历史不能途中丢失。"""
        panel = StatusPanel()
        first = StatusSlot()
        second = StatusSlot()
        # 面板最后挂进 second，销毁时先拆面板再拆容器，避免对已删除的 C++ 对象收尾。
        self.addCleanup(_destroy, second)
        self.addCleanup(_destroy, first)
        self.addCleanup(_destroy, panel)
        first.mount(panel)
        panel.add_log_message("配置页日志")

        first.unmount(panel)
        second.mount(panel)

        self.assertEqual(panel.status_text(), "配置页日志")
        self.assertIn("配置页日志", panel.messages())

    def test_one_line_panel_stays_at_the_top_of_the_slot(self):
        """面板固定一行高，多余高度留在下方，不能浮在槽中间。"""
        panel = StatusPanel()
        slot = StatusSlot()
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(slot, 1)
        # 面板最后挂在 slot 下，销毁时先拆面板，再依次拆容器。
        self.addCleanup(_destroy, host)
        self.addCleanup(_destroy, slot)
        self.addCleanup(_destroy, panel)
        slot.mount(panel)
        host.resize(400, 700)
        host.show()
        self.app.processEvents()

        self.assertEqual(panel.y(), 0)
        self.assertLess(panel.height(), slot.height())


if __name__ == "__main__":
    unittest.main()
