from datetime import datetime
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QTextEdit, QVBoxLayout, QWidget)
from PySide6.QtCore import QEvent, Qt

from infrastructure.filesystem.paths import ICONS_DIR
from .chat_dialogs import ChatHistoryDialog, ChatSettingsDialog
from .chat_worker import ChatWorker, UnavailableAssistant


class ChatPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        try:
            from assistant.llm import LLMChat
            self.llm_chat = LLMChat()
        except Exception as error:
            print(f"AI 助手未启用: {error}")
            self.llm_chat = UnavailableAssistant()
        self.main_window = parent
        self.chat_worker = None
        self.thinking_message_visible = False
        layout = QVBoxLayout(self)
        header = QHBoxLayout(); header.addWidget(QLabel("CHAT")); header.addStretch()
        self.new_chat_btn = QPushButton("+"); self.history_btn = QPushButton("历史"); self.settings_btn = QPushButton("设置"); self.close_btn = QPushButton("关闭")
        self.new_chat_btn.clicked.connect(self.start_new_conversation); self.history_btn.clicked.connect(self.show_history_dialog); self.settings_btn.clicked.connect(self.show_settings_dialog); self.close_btn.clicked.connect(self.hide_chat_panel)
        for button in (self.new_chat_btn, self.history_btn, self.settings_btn, self.close_btn): header.addWidget(button)
        layout.addLayout(header)
        self.web_search_checkbox = QCheckBox("联网搜索"); self.web_search_checkbox.stateChanged.connect(self.on_web_search_changed); layout.addWidget(self.web_search_checkbox)
        self.chat_display = QTextEdit(); self.chat_display.setReadOnly(True); layout.addWidget(self.chat_display)
        shortcuts = QHBoxLayout()
        self.analyze_test_btn = QPushButton("分析测试数据"); self.troubleshoot_btn = QPushButton("故障诊断"); self.clear_btn = QPushButton("清除历史")
        self.analyze_test_btn.clicked.connect(self.analyze_test_data); self.troubleshoot_btn.clicked.connect(self.troubleshoot_issues); self.clear_btn.clicked.connect(self.clear_chat_history)
        for button in (self.analyze_test_btn, self.troubleshoot_btn, self.clear_btn): shortcuts.addWidget(button)
        layout.addLayout(shortcuts)
        self.message_input = QTextEdit(); self.message_input.setMaximumHeight(80); layout.addWidget(self.message_input)
        self.message_input.installEventFilter(self)
        self.send_btn = QPushButton("发送 (Ctrl+Enter)"); self.send_btn.clicked.connect(self.send_message); layout.addWidget(self.send_btn)
        self.web_search_checkbox.setChecked(self.llm_chat.enable_web_search)
        self.load_chat_history()

    def on_web_search_changed(self, state): self.llm_chat.set_web_search(state == Qt.CheckState.Checked.value)
    def add_system_message(self, message): self.chat_display.append(f"[{datetime.now():%H:%M:%S}] {message}")
    def add_user_message(self, message): self.chat_display.append(f"我: {message}")
    def add_assistant_message(self, message):
        try:
            import markdown
            self.chat_display.append(f"AI: {markdown.markdown(message, extensions=['fenced_code', 'tables', 'nl2br'])}")
        except Exception:
            self.chat_display.append(f"AI: {message}")
    def get_test_context(self):
        if not self.main_window:
            return "功放测试系统"
        context = ["功放测试系统环境"]
        config = getattr(self.main_window, "config", None)
        if config:
            context.append(f"测试频率: {config.get('test_frequencies', 'N/A')}")
            context.append(f"驱动模式: {'启用' if config.get('driver_mode', {}).get('enabled', False) else '禁用'}")
            context.append(f"衰减器: {config.get('attenuator', {}).get('type', 'N/A')}")
        tab_widget = getattr(self.main_window, "tab_widget", None)
        current_tab = tab_widget.currentIndex() if tab_widget is not None else 0
        context.append(f"当前页面索引: {current_tab}")
        data_to_analyze = None
        current_frequency = None
        if current_tab == 4 and getattr(self.main_window, "loaded_data", None):
            frequencies = getattr(self.main_window, "frequency_list", [])
            index = getattr(self.main_window, "current_freq_index", 0)
            if 0 <= index < len(frequencies):
                current_frequency = str(frequencies[index])
                data_to_analyze = self.main_window.loaded_data.get(current_frequency)
        if not data_to_analyze and getattr(self.main_window, "real_time_data", None):
            frequencies = getattr(self.main_window, "rt_frequency_list", [])
            index = getattr(self.main_window, "rt_current_freq_index", 0)
            if 0 <= index < len(frequencies):
                current_frequency = str(frequencies[index])
                data_to_analyze = self.main_window.real_time_data.get(current_frequency)
        if data_to_analyze:
            context.append(f"当前频率: {current_frequency} GHz")
            sweep = data_to_analyze.get("sweep_data", {})
            for key, label in (("input_power_dut", "输入功率(Pin)"), ("output_power_dut", "输出功率(Pout)"), ("input_power_sg", "信号源输入(Pin)"), ("output_power_driver", "驱动输出(Pout)"), ("gain", "增益(Gain)"), ("efficiency", "效率(PAE)")):
                values = sweep.get(key)
                if isinstance(values, list):
                    rounded = [round(value, 2) for value in values]
                    context.append(f"{label}数组: {rounded}")
                    if rounded and key in {"gain", "efficiency"}:
                        context.append(f"最大{label}: {max(rounded):.2f}")
        return "\n".join(context)
    def send_message(self):
        message = self.message_input.toPlainText().strip()
        if message and not (self.chat_worker and self.chat_worker.isRunning()):
            self.add_user_message(message)
            self.message_input.clear()
            self.add_system_message("🤔 AI正在思考...")
            self.get_ai_response(message, self.get_test_context())
    def get_ai_response(self, message, context):
        self.chat_worker = ChatWorker(self.llm_chat, message, context)
        self.chat_worker.response_ready.connect(self.on_ai_response_ready)
        self.chat_worker.error_occurred.connect(self.on_ai_error)
        self.chat_worker.finished.connect(self.on_ai_finished)
        self.chat_worker.start()
        self.thinking_message_visible = True
    def on_ai_finished(self):
        self.thinking_message_visible = False
        if self.chat_worker:
            self.chat_worker.deleteLater()
            self.chat_worker = None

    def on_ai_response_ready(self, response):
        self.remove_thinking_message()
        self.add_assistant_message(response)
    def on_ai_error(self, error_message):
        self.remove_thinking_message()
        self.add_system_message(f"错误: {error_message}")
    def remove_thinking_message(self): self.thinking_message_visible = False
    def eventFilter(self, obj, event):
        if obj == self.message_input and event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key_Return and event.modifiers() == Qt.ControlModifier:
            self.send_message(); return True
        return super().eventFilter(obj, event)
    def analyze_test_data(self):
        self.message_input.setPlainText("请帮我分析当前的测试数据，包括增益、效率、压缩点等关键指标，并指出可能存在的问题。"); self.send_message()
    def troubleshoot_issues(self):
        self.message_input.setPlainText("我在测试过程中遇到了问题，请帮我进行故障诊断和排查。"); self.send_message()
    def clear_chat_history(self):
        if QMessageBox.question(self, "确认", "确定要清除所有聊天历史吗？", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.llm_chat.clear_history(); self.chat_display.clear(); self.add_system_message("聊天历史已清除")
    def load_selected_history(self, history_data):
        self.llm_chat.conversation_history = history_data; self.chat_display.clear(); self.load_chat_history(); self.add_system_message("已加载历史对话")
    def load_chat_history(self):
        for message in getattr(self.llm_chat, "conversation_history", [])[-20:]:
            if message.get("role") == "user": self.add_user_message(message.get("content", ""))
            elif message.get("role") == "assistant": self.add_assistant_message(message.get("content", ""))
    def show_history_dialog(self):
        dialog = ChatHistoryDialog(self)
        if dialog.exec() == QDialog.Accepted:
            selected_history = dialog.get_selected_history()
            if selected_history:
                self.load_selected_history(selected_history)
    def show_settings_dialog(self): ChatSettingsDialog(self.llm_chat, self).exec()
    def start_new_conversation(self):
        if self.llm_chat.conversation_history:
            if QMessageBox.question(self, "新对话", "确定要开始新对话吗？当前对话将被保存。", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
                return
            try:
                self.llm_chat.save_history()
            except Exception:
                pass
        self.llm_chat.conversation_history = []
        self.chat_display.clear()
    def hide_chat_panel(self):
        if self.main_window: self.main_window.toggle_chat_panel()
    def closeEvent(self, event):
        if self.chat_worker and self.chat_worker.isRunning(): self.chat_worker.terminate(); self.chat_worker.wait()
        super().closeEvent(event)
