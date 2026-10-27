"""步骤 12：可选助手面板的行为与失败边界。

助手是可选能力：缺失可选依赖时必须退化为“不可用面板”，且失败要呈现给用户、
写入日志，不能被静默吞掉；面板不写回窗口状态，也不影响测量链路。
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from presentation.qt.assistant.chat_panel import ChatPanel, create_assistant
from presentation.qt.assistant.chat_worker import UnavailableAssistant


class _FakeSignal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)


class _FakeWorker:
    """替身 worker：记录是否启动，并保留信号以便手动触发回调。"""

    instances = []

    def __init__(self, llm_chat, message, context):
        self.llm_chat = llm_chat
        self.message = message
        self.context = context
        self.response_ready = _FakeSignal()
        self.error_occurred = _FakeSignal()
        self.finished = _FakeSignal()
        self.running = False
        self.deleted = False
        _FakeWorker.instances.append(self)

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def deleteLater(self):
        self.deleted = True


class _FakeAssistant:
    """完整的助手替身：字段与 ``assistant.llm.LLMChat`` 的公开接口一致。"""

    available = True
    server_url = "http://localhost:11434/api/chat"
    temperature = 0.7
    max_tokens = 2000
    auto_save = False
    history_limit = 20
    enable_web_search = False
    function_handler = None
    model_name = "qwen3:8b"

    def __init__(self):
        self.conversation_history = []
        self.saved = 0

    def chat(self, message, context=None):
        return f"echo:{message}"

    def update_settings(self, **_kwargs):
        return None

    def set_web_search(self, _enabled):
        return None

    def clear_history(self):
        self.conversation_history = []

    def save_history(self):
        self.saved += 1


def _application():
    return QApplication.instance() or QApplication([])


def _panel(**window_attributes):
    _application()
    assistant = _FakeAssistant()
    panel = ChatPanel(None, assistant_factory=lambda _paths: (assistant, None))
    window = SimpleNamespace(**window_attributes) if window_attributes else None
    # 窗口本身是 Qt 父控件；测试用纯 Python 替身承载只读上下文属性。
    panel.main_window = window
    return panel, window, assistant


class AssistantFactoryTests(unittest.TestCase):
    def test_missing_optional_dependency_becomes_an_explained_unavailable_state(self):
        with patch.dict(sys.modules, {"assistant.llm": None}):
            assistant, reason = create_assistant()

        self.assertIsNone(assistant)
        self.assertIn("可选依赖", reason)

    def test_factory_never_raises_for_a_readable_configuration_failure(self):
        try:
            import assistant.llm  # noqa: F401 - 缺少可选依赖时整条用例跳过
        except ImportError as error:
            self.skipTest(f"可选依赖缺失: {error}")
        with patch("assistant.llm.LLMChat", side_effect=OSError("配置不可读")):
            assistant, reason = create_assistant()

        self.assertIsNone(assistant)
        self.assertIn("配置不可读", reason)


class ChatPanelBehaviourTests(unittest.TestCase):
    def test_welcome_message_is_shown_once_and_names_the_model(self):
        panel, _window, _assistant = _panel()

        panel.show_welcome_message()
        first = panel.chat_display.toPlainText()
        panel.show_welcome_message()

        self.assertIn("Qwen", first)
        self.assertEqual(panel.chat_display.toPlainText(), first)

    def test_sending_with_an_unavailable_assistant_reports_instead_of_silence(self):
        _application()
        panel = ChatPanel(
            None, assistant_factory=lambda _paths: (None, "缺少可选依赖: requests")
        )
        self.assertIsInstance(panel.llm_chat, UnavailableAssistant)
        with patch("presentation.qt.assistant.chat_panel.ChatWorker", _FakeWorker):
            panel.message_input.setPlainText("你好")
            panel.send_message()

        self.assertIn("AI 助手不可用", panel.chat_display.toPlainText())
        self.assertIsNone(panel.chat_worker)

    def test_send_message_runs_the_worker_and_clears_the_thinking_state(self):
        panel, _window, _assistant = _panel()
        with patch("presentation.qt.assistant.chat_panel.ChatWorker", _FakeWorker):
            panel.message_input.setPlainText("分析一下")
            panel.send_message()

            worker = panel.chat_worker
            self.assertIsNotNone(worker)
            self.assertTrue(worker.running)
            self.assertTrue(panel.thinking_label.isVisibleTo(panel))
            self.assertEqual(worker.message, "分析一下")

            panel.on_ai_response_ready("结果")
            self.assertFalse(panel.thinking_label.isVisibleTo(panel))
            self.assertIn("结果", panel.chat_display.toPlainText())

            panel.on_ai_finished()
        self.assertTrue(worker.deleted)

    def test_second_message_is_refused_while_a_reply_is_pending(self):
        panel, _window, _assistant = _panel()
        with patch("presentation.qt.assistant.chat_panel.ChatWorker", _FakeWorker):
            panel.message_input.setPlainText("第一条")
            panel.send_message()
            panel.message_input.setPlainText("第二条")
            panel.send_message()

        self.assertEqual(len(_FakeWorker.instances), 1)
        self.assertIn("请稍候", panel.chat_display.toPlainText())

    def test_new_conversation_reports_a_history_save_failure(self):
        from PySide6.QtWidgets import QMessageBox

        panel, _window, assistant = _panel()
        assistant.conversation_history = [{"role": "user", "content": "历史"}]

        def _failing_save():
            raise OSError("磁盘只读")

        assistant.save_history = _failing_save

        with patch(
            "presentation.qt.assistant.chat_panel.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ), patch(
            "presentation.qt.assistant.chat_panel.QMessageBox.warning"
        ) as warning:
            panel.start_new_conversation()

        # 旧实现用 ``except Exception: pass`` 静默丢弃，这里必须让用户看到。
        warning.assert_called_once()
        self.assertEqual(
            assistant.conversation_history, [{"role": "user", "content": "历史"}]
        )


class ChatPanelContextTests(unittest.TestCase):
    def test_context_uses_the_page_key_instead_of_a_tab_position(self):
        panel, _window, _assistant = _panel(
            config={"test_frequencies": [2.4], "driver_mode": {"enabled": True}},
            pages={"configuration": object(), "visualization": object()},
            tab_widget=SimpleNamespace(currentIndex=lambda: 1),
            loaded_data={"2.4": {"sweep_data": {"gain": [1.234, 2.0]}}},
            frequency_list=[2.4],
            current_freq_index=0,
            real_time_data={},
            rt_frequency_list=[],
            rt_current_freq_index=0,
        )

        context = panel.get_test_context()

        self.assertIn("当前页面: visualization", context)
        self.assertIn("当前频率: 2.4 GHz", context)
        self.assertIn("增益(Gain)数组: [1.23, 2.0]", context)
        self.assertIn("最大增益(Gain): 2.00", context)

    def test_context_falls_back_to_realtime_data_on_other_pages(self):
        panel, _window, _assistant = _panel(
            pages={"configuration": object(), "driver_mapping": object()},
            tab_widget=SimpleNamespace(currentIndex=lambda: 1),
            loaded_data={},
            frequency_list=[],
            current_freq_index=0,
            real_time_data={"3.5": {"sweep_data": {"output_power_dut": [10.0]}}},
            rt_frequency_list=[3.5],
            rt_current_freq_index=0,
        )

        context = panel.get_test_context()

        self.assertIn("当前页面: driver_mapping", context)
        self.assertIn("输出功率(Pout)数组: [10.0]", context)

    def test_page_key_is_unknown_without_pages(self):
        panel, _window, _assistant = _panel()

        self.assertIsNone(panel._current_page_key())
        self.assertEqual(panel.get_test_context(), "功放测试系统")


if __name__ == "__main__":
    unittest.main()
