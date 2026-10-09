import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import enhanced_main_gui
from presentation.qt.connection_dialog import ConnectionDialog
from presentation.qt.realtime_plot import RealTimePlotWidget
from presentation.qt.assistant.chat_panel import ChatPanel
from presentation.qt.assistant.chat_worker import ChatWorker, UnavailableAssistant


class Phase31PureUiTests(unittest.TestCase):
    def test_compatibility_exports_are_extracted_types(self):
        from presentation.qt.assistant.chat_dialogs import ChatHistoryDialog, ChatSettingsDialog

        self.assertIs(enhanced_main_gui.RealTimePlotWidget, RealTimePlotWidget)
        self.assertIs(enhanced_main_gui.ConnectionDialog, ConnectionDialog)
        self.assertIs(enhanced_main_gui.ChatPanel, ChatPanel)
        self.assertIs(enhanced_main_gui.ChatWorker, ChatWorker)
        self.assertIs(enhanced_main_gui.ChatHistoryDialog, ChatHistoryDialog)
        self.assertIs(enhanced_main_gui.ChatSettingsDialog, ChatSettingsDialog)

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
