"""可选的助手界面组件（聊天面板、对话框与后台 worker）。"""

from .chat_dialogs import ChatHistoryDialog, ChatSettingsDialog
from .chat_panel import ChatPanel, create_assistant
from .chat_worker import ChatWorker, UnavailableAssistant

__all__ = [
    "ChatPanel",
    "ChatWorker",
    "ChatHistoryDialog",
    "ChatSettingsDialog",
    "UnavailableAssistant",
    "create_assistant",
]
