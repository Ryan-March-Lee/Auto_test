"""阶段摘要与结果 KPI 的纯数据契约（重构计划步骤 15）。

三个测量页各自从“已经显示出来的数据”里取一份摘要交给工作台，工作台只负责把它排版
到阶段条、页头与结果面，不重新统计频点、不读结果 DTO，也不发起新的业务查询。本模块
只定义这份摘要的形状与几个纯格式函数：

- :class:`StageMetric`：结果面 KPI 行里的一项，例如 ``StageMetric("频点", "6")``；
- :class:`StageSummary`：阶段条上的一句话摘要（``headline``）加上结果面的 KPI 行；
  两句必须一致：``headline`` 为“未开始”时 KPI 不能携带数量，有数据时至少有一项 KPI；
- :func:`frequency_headline` / :func:`frequency_text` / :func:`count_text`：把页面已有
  的数值折成展示文本，没有数据时统一返回“未开始”，不显示 0 或占位假值。

模块不导入 Qt，也不导入页面注册表，因此可以被页面、绑定层与测试直接消费。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

__all__ = [
    "NOT_STARTED",
    "StageMetric",
    "StageSummary",
    "count_text",
    "frequency_headline",
    "frequency_text",
]

#: 没有可展示数据时的统一文案：不写 0，也不留空，避免把“还没开始”误读成“测了 0 个”。
NOT_STARTED = "未开始"


def _require_text(value: Any, name: str) -> str:
    """校验展示文本；空字符串是合法值（表示不显示），非字符串直接报错。"""
    if not isinstance(value, str):
        raise TypeError(f"{name} 必须是字符串")
    return value


@dataclass(frozen=True)
class StageMetric:
    """结果面 KPI 行里的一项：标签与已格式化好的展示值。"""

    label: str
    value: str

    def __post_init__(self) -> None:
        _require_text(self.label, "KPI 标签")
        _require_text(self.value, "KPI 值")


@dataclass(frozen=True)
class StageSummary:
    """一个阶段的摘要：阶段条上的一句话，加上结果面的 KPI 行。"""

    headline: str
    metrics: tuple[StageMetric, ...] = ()

    def __post_init__(self) -> None:
        _require_text(self.headline, "阶段摘要")
        if not isinstance(self.metrics, tuple):
            raise TypeError("阶段 KPI 必须是 StageMetric 元组")
        for metric in self.metrics:
            if not isinstance(metric, StageMetric):
                raise TypeError("阶段 KPI 必须是 StageMetric 元组")
        if self.headline == NOT_STARTED:
            # 阶段条说“未开始”时，KPI 行不能报出数量：否则同一份摘要有两个真相。
            for metric in self.metrics:
                if metric.value != NOT_STARTED:
                    raise ValueError("“未开始”的摘要不能携带数量")
        elif not self.metrics:
            # 有数据就必须至少有一项 KPI；否则阶段条显示数量、结果面却是空的。
            raise ValueError("有数据的摘要必须至少包含一项 KPI")

    @property
    def has_data(self) -> bool:
        """是否已经有可展示的数据；``False`` 时只显示“未开始”。"""
        return self.headline != NOT_STARTED

    @classmethod
    def not_started(cls, *, metric_label: str = "频点") -> "StageSummary":
        """空态摘要：阶段条与结果面都只说“未开始”，不伪造数量。"""
        return cls(NOT_STARTED, (StageMetric(metric_label, NOT_STARTED),))


def frequency_headline(count: int) -> str:
    """把频点数折成阶段条摘要，例如 ``6 频点``；0 个频点显示“未开始”。"""
    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError("频点数必须是整数")
    if count < 0:
        raise ValueError("频点数不能为负")
    return f"{count} 频点" if count else NOT_STARTED


def count_text(count: int) -> str:
    """把正整数折成 KPI 值；0 与负数都显示“未开始”。"""
    if isinstance(count, bool) or not isinstance(count, int):
        raise TypeError("计数必须是整数")
    return str(count) if count > 0 else NOT_STARTED


def frequency_text(value: Any) -> str | None:
    """把页面里已有的频率值折成 ``2.4 GHz`` 这样的展示文本。

    非数值文本原样保留（它本来就是表格里显示的内容）；空值、布尔值与 ``NaN``/``±inf``
    返回 ``None``，由调用方决定显示“未开始”。
    """
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        frequency = float(text)
    except (TypeError, ValueError):
        return text
    if not math.isfinite(frequency):
        return None
    return f"{frequency:g} GHz"
