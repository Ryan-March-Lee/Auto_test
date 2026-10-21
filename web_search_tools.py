"""已弃用兼容入口：请迁移到 ``assistant.web_search_tools``。"""

from assistant.web_search_tools import FunctionCallHandler, WebSearchAPI

__all__ = ["FunctionCallHandler", "WebSearchAPI"]
