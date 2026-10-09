from PySide6.QtCore import QThread, Signal


class UnavailableAssistant:
    available = False
    server_url = ""
    model_name = "AI unavailable"
    temperature = 0.7
    max_tokens = 2000
    auto_save = False
    history_limit = 0
    enable_web_search = False
    function_handler = None

    def __init__(self):
        self.conversation_history = []

    def chat(self, *_args, **_kwargs):
        return "AI 助手当前不可用，请检查可选依赖和服务配置。"

    def update_settings(self, **_kwargs):
        return None

    def set_web_search(self, _enabled):
        return None

    def clear_history(self):
        self.conversation_history = []

    def save_history(self):
        return None


class ChatWorker(QThread):
    response_ready = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, llm_chat, message, context):
        super().__init__()
        self.llm_chat, self.message, self.context = llm_chat, message, context

    def run(self):
        try:
            self.response_ready.emit(self.llm_chat.chat(self.message, self.context))
        except Exception as error:
            self.error_occurred.emit(str(error))
