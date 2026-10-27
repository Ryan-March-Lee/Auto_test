"""助手后台 worker：把一次问答放到 QThread 里执行。

这里是线程边界：助手侧的任何异常都在 ``run()`` 内被转成 ``error_occurred`` 信号，
由面板显示给用户。异常不会返回主窗口，也不会影响测量线程。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QThread, Signal

from infrastructure.logging.app_logging import get_logger

logger = get_logger(__name__)


class UnavailableAssistant:
    """助手不可用时的占位实现：接口齐全、只返回固定说明。

    面板因此不需要为“缺少可选依赖”写第二套代码路径。
    """

    available = False
    server_url = ""
    model_name = "AI unavailable"
    temperature = 0.7
    max_tokens = 2000
    auto_save = False
    history_limit = 0
    enable_web_search = False
    function_handler = None

    def __init__(self) -> None:
        self.conversation_history: list = []

    def chat(self, *_args, **_kwargs) -> str:
        return "AI 助手当前不可用，请检查可选依赖和服务配置。"

    def update_settings(self, **_kwargs) -> None:
        return None

    def set_web_search(self, _enabled) -> None:
        return None

    def clear_history(self) -> None:
        self.conversation_history = []

    def save_history(self) -> None:
        return None


class ChatWorker(QThread):
    """在线程里调用一次 ``llm_chat.chat()``，用信号返回结果或错误。

    取消是协作式的：面板关闭时先 ``requestInterruption()``，线程在开始前和拿到结果后
    检查中断标记，因此被中断的回复不会再把消息推给正在关闭的面板。
    """

    response_ready = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, llm_chat: Any, message: str, context: str) -> None:
        super().__init__()
        self.llm_chat = llm_chat
        self.message = message
        self.context = context

    def run(self) -> None:
        if self.isInterruptionRequested():
            return
        # 线程边界：任何助手失败都转成 error 信号，由面板呈现，不静默丢弃。
        try:
            response = self.llm_chat.chat(self.message, self.context)
        except Exception as error:  # noqa: BLE001 - 助手侧异常一律转成用户可见错误
            logger.warning("AI 回复失败: %s", error)
            if not self.isInterruptionRequested():
                self.error_occurred.emit(str(error) or type(error).__name__)
            return
        if self.isInterruptionRequested():
            return
        self.response_ready.emit(response)


__all__ = ["ChatWorker", "UnavailableAssistant"]
