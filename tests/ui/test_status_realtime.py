import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

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


class StatusPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _panel(self):
        panel = StatusPanel()
        self.addCleanup(_destroy, panel)
        return panel

    def test_log_scroll_and_clear(self):
        panel = self._panel()
        panel.add_log_message("first")
        self.assertIn("first", panel.log_text.toPlainText())
        panel.log_user_scrolling = True
        panel.add_log_message("second")
        self.assertTrue(panel.log_user_scrolling)
        panel.clear_log()
        self.assertEqual(panel.log_text.toPlainText(), "")
        self.assertFalse(panel.log_user_scrolling)

    def test_log_is_collapsed_by_default_and_still_receives_messages(self):
        """默认折叠只隐藏日志框：消息仍进入同一个控件，标题行给出摘要与条数。"""
        panel = self._panel()

        self.assertFalse(panel.is_log_expanded())
        self.assertTrue(panel.log_text.isHidden())
        self.assertEqual(panel.log_summary_label.text(), "暂无日志")
        self.assertEqual(panel.log_count_label.text(), "0 条")
        self.assertEqual(
            panel.log_toggle_btn.accessibleName(), "展开实时日志"
        )

        panel.add_log_message("first")

        self.assertIn("first", panel.log_text.toPlainText())
        self.assertEqual(panel.log_summary_label.text(), "first")
        self.assertEqual(panel.log_count_label.text(), "1 条")
        self.assertTrue(panel.log_text.isHidden())

    def test_expand_keeps_messages_and_counts(self):
        panel = self._panel()
        panel.add_log_message("first")
        panel.add_log_message("second\nline two")

        self.assertEqual(panel.log_summary_label.text(), "second line two")
        self.assertEqual(panel.log_count_label.text(), "2 条")

        panel.toggle_log()

        self.assertTrue(panel.is_log_expanded())
        self.assertFalse(panel.log_text.isHidden())
        self.assertEqual(panel.log_toggle_btn.accessibleName(), "收起实时日志")
        self.assertIn("line two", panel.log_text.toPlainText())

        panel.toggle_log()

        self.assertTrue(panel.log_text.isHidden())
        self.assertIn("line two", panel.log_text.toPlainText(), "折叠不能丢消息")

    def test_summary_tooltip_keeps_the_log_text_plain(self):
        """日志里的 HTML 片段不能被 tooltip 当成富文本；换行仍要保留。"""
        panel = self._panel()
        panel.add_log_message("<b>错误</b> & 路径\n第二行")

        self.assertEqual(
            panel.log_summary_label.text(), "<b>错误</b> & 路径 第二行"
        )
        tooltip = panel.log_summary_label.toolTip()
        self.assertTrue(tooltip.startswith("<qt>"))
        self.assertIn("&lt;b&gt;", tooltip)
        self.assertIn("&amp;", tooltip)
        self.assertIn("<br>", tooltip)

    def test_expanded_state_survives_remounting_into_another_page(self):
        """面板只有一份：切页只是换挂点，用户的展开选择不能在途中被重置。"""
        panel = StatusPanel()
        first = StatusSlot()
        second = StatusSlot()
        # 面板最后挂进 second，销毁时先拆面板再拆容器，避免对已删除的 C++ 对象收尾。
        self.addCleanup(_destroy, second)
        self.addCleanup(_destroy, first)
        self.addCleanup(_destroy, panel)
        first.mount(panel)
        panel.set_log_expanded(True)

        first.unmount(panel)
        second.mount(panel)

        self.assertTrue(panel.is_log_expanded())
        self.assertFalse(panel.log_text.isHidden())

    def test_collapsed_panel_is_shorter_than_expanded_panel(self):
        panel = self._panel()
        panel.resize(400, 700)
        panel.add_log_message("measurement finished")
        panel.show()
        self.app.processEvents()
        panel.layout().invalidate()
        collapsed_hint = panel.sizeHint().height()

        panel.set_log_expanded(True)
        self.app.processEvents()
        panel.layout().invalidate()
        expanded_hint = panel.sizeHint().height()

        self.assertGreater(
            expanded_hint,
            collapsed_hint,
            "日志展开后应明显占用更多高度",
        )

    def test_collapsed_panel_stays_at_the_top_and_gives_its_height_back(self):
        """折叠不仅变矮，还要贴在槽顶部；展开后重新填满，切回折叠仍贴顶。"""
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

        top = slot.layout().contentsMargins().top()
        collapsed_height = panel.height()
        self.assertEqual(
            panel.y(),
            top,
            "折叠时状态面板应钉在槽顶部，不能在剩余高度里居中",
        )

        panel.set_log_expanded(True)
        self.app.processEvents()
        expanded_height = panel.height()

        self.assertLess(
            collapsed_height,
            expanded_height,
            "折叠后状态面板应把高度还给页面",
        )
        self.assertEqual(panel.height(), slot.height(), "展开后应填满槽")
        self.assertEqual(panel.y(), top)

        panel.set_log_expanded(False)
        self.app.processEvents()
        self.assertEqual(
            panel.y(),
            top,
            "再次折叠后仍要贴顶，不能残留展开时的布局状态",
        )

    def test_clear_resets_summary_and_count_while_collapsed(self):
        panel = self._panel()
        panel.add_log_message("first")
        panel.add_log_message("second")

        panel.clear_log()

        self.assertEqual(panel.log_count_label.text(), "0 条")
        self.assertEqual(panel.log_summary_label.text(), "暂无日志")
        self.assertEqual(panel.log_text.toPlainText(), "")
        self.assertTrue(panel.log_text.isHidden())

    def test_toggle_rejects_non_boolean_state(self):
        panel = self._panel()
        with self.assertRaises(TypeError):
            panel.set_log_expanded("yes")


if __name__ == "__main__":
    unittest.main()
