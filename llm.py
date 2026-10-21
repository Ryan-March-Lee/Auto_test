"""已弃用兼容入口：请迁移到 ``assistant.llm``。"""

from assistant.llm import LLMChat, chat

__all__ = ["LLMChat", "chat"]
