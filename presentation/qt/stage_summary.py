"""阶段摘要的纯数据契约（重构计划步骤 15）。

三个测量页各自从“已经显示出来的数据”里取一句话摘要交给工作台，工作台只负责把它
排版到阶段条，不重新统计频点、不读结果 DTO，也不发起新的业务查询。本模块只定义
这份摘要的形状与一个纯格式函数：

- :class:`StageSummary`：阶段条上的一句话摘要（``headline``）；有数据时写频点数，
  没有数据时统一写“未开始”，不显示 0 或占位假值；
- :func:`frequency_headline`：把页面已有的频点数折成展示文本。

模块不导入 Qt，也不导入页面注册表，因此可以被页面、绑定层与测试直接消费。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "NOT_STARTED",
    "StageSummary",
    "frequency_headline",
]

#: 没有可展示数据时的统一文案：不写 0，也不留空，避免把“还没开始”误读成“测了 0 个”。
NOT_STARTED = "未开始"


def _require_text(value: Any, name: str) -> str:
    """校验展示文本；空字符串是合法值（表示不显示），非字符串直接报错。"""
    if not isinstance(value, str):
        raise TypeError(f"{name} 必须是字符串")
    return value


@dataclass(frozen=True)
class StageSummary:
    """一个阶段的摘要：阶段条上的一句话。"""

    headline: str

    def __post_init__(self) -> None:
        _require_text(self.headline, "阶段摘要")

    @property
    def has_data(self) -> bool:
        """是否已经有可展示的数据；``False`` 时只显示“未开始”。"""
        return self.headline != NOT_STARTED

    @classmethod
    def not_started(cls) -> "StageSummary":
        """空态摘要：阶段条只说“未开始”，不伪造数量。"""
        return cls(NOT_STARTED)


def frequency_headline(count: int) -> str:
    """把频点数折成阶段条摘要，例如 ``6 频点``；0 个频点显示“未开始”。"""
    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError("频点数必须是整数")
    if count < 0:
        raise ValueError("频点数不能为负")
    return f"{count} 频点" if count else NOT_STARTED
