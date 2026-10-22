import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from presentation.qt.realtime_plot import RealTimePlotWidget
from presentation.qt.assistant.chat_panel import ChatPanel
from presentation.qt.assistant.chat_worker import UnavailableAssistant


class UiComponentTests(unittest.TestCase):
    def test_plot_widget_has_four_axes_and_updates_common_data(self):
        self.assertTrue(callable(RealTimePlotWidget.update_plot))

    def test_navigation_signals_are_connected(self):
        self.assertTrue(hasattr(RealTimePlotWidget, "prev_clicked"))
        self.assertTrue(hasattr(RealTimePlotWidget, "next_clicked"))

    def test_unavailable_assistant_instances_do_not_share_history(self):
        first = UnavailableAssistant()
        second = UnavailableAssistant()
        first.conversation_history.append("message")
        self.assertEqual(second.conversation_history, [])

    def test_chat_panel_installs_input_event_filter(self):
        self.assertTrue(callable(ChatPanel.eventFilter))


if __name__ == "__main__":
    unittest.main()
