"""频率扫描的灰字预览与汇总（纯展示计算）。

与 :mod:`presentation.qt.power_scan_preview` 成对：功率计划按行给出“这一段会扫出哪些
功率点”，再给一行合并汇总；频率扫描只有一段，这里把同一套结构套上去——行内灰字显示
该段会生成的频点，汇总灰字显示段数、点数与首尾范围，非法读数在同一个位置转成问题描述。

频点生成、增补解析与加载错误都复用 :mod:`presentation.qt.frequency_sweep`，本模块不
重新实现步长网格、端点容差或增补合并，只决定文本、语义色和 tooltip。预览作为 Qt 槽函数
的实现，不把异常抛回信号处理：任何读不出来的参数都收敛为用户文案。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .frequency_sweep import (
    FrequencySweepError,
    format_frequency,
    generate_frequencies,
    parse_extra_frequencies,
)

__all__ = [
    "FrequencyScanPreview",
    "preview_frequency_scan",
]

#: 行内预览最多显示的频点数；超过后只显示前 4 个和后 3 个，完整点列放进 tooltip。
#: 与功率计划行取同一组值，两块灰字的截断口径才不会各有一套。
_PREVIEW_LIMIT = 8
_PREVIEW_HEAD = 4
_PREVIEW_TAIL = 3

#: 频率扫描在界面上固定只有一段；汇总沿用功率计划的“N 段”句式。
_SEGMENT_COUNT = 1


@dataclass(frozen=True)
class FrequencyScanPreview:
    """频率扫描的展示结果：行内灰字、汇总灰字与生成出的频点。

    ``row_tone`` / ``summary_tone`` 只决定语义色，不参与业务判断；``error`` 是当前
    读数或加载状态的问题描述，合法时为 ``None``。
    """

    row_text: str
    row_tone: str = "muted"
    row_tooltip: str = ""
    summary: str = ""
    summary_tone: str = "muted"
    summary_tooltip: str = ""
    frequencies: tuple[float, ...] = ()
    error: str | None = None


def _compact_frequencies(values: Sequence[float]) -> str:
    """行内频点文本：不多于 8 点时全列，否则只保留首 4 尾 3 并注明总数。"""
    texts = [format_frequency(value) for value in values]
    if len(texts) <= _PREVIEW_LIMIT:
        body = "，".join(texts)
    else:
        body = (
            "，".join(texts[:_PREVIEW_HEAD])
            + "，…，"
            + "，".join(texts[-_PREVIEW_TAIL:])
        )
    return f"共 {len(texts)} 个频点：{body} GHz"


def _full_frequencies(values: Sequence[float]) -> str:
    """tooltip 用的完整点列，不受行内截断限制。"""
    return "，".join(format_frequency(value) for value in values) + " GHz"


def _row_tooltip(
    start: float,
    step: float,
    stop: float,
    values: Sequence[float],
    error: str | None = None,
) -> str:
    header = (
        f"第 {_SEGMENT_COUNT} 段：起始 {format_frequency(start)} GHz，"
        f"步长 {format_frequency(step)} GHz，终止 {format_frequency(stop)} GHz"
    )
    if error:
        return f"{header}\n{error}"
    if not values:
        return header
    return f"{header}\n共 {len(values)} 个频点：{_full_frequencies(values)}"


def _problem(
    message: str, start: float, step: float, stop: float
) -> FrequencyScanPreview:
    """读数或加载状态只有一个问题时使用：不伪造频点，行内与汇总给出同一原因。"""
    return FrequencyScanPreview(
        row_text=message,
        row_tone="warning",
        row_tooltip=_row_tooltip(start, step, stop, (), message),
        summary=f"DUT 频率扫描：{_SEGMENT_COUNT} 段，1 个问题；{message}",
        summary_tone="warning",
        summary_tooltip=message,
        error=message,
    )


def preview_frequency_scan(
    start,
    stop,
    step,
    extra_text,
    *,
    import_error: str | None = None,
) -> FrequencyScanPreview:
    """把频率扫描控件读数整理成行内预览与汇总。

    ``import_error`` 是读入配置时留下的错误（例如旧列表非等步长）：它优先于控件读数，
    在用户重新输入扫描参数之前，控件里的值只是“尽力摆放”，不能当成可保存的频点。

    生成与增补合并都经 :mod:`presentation.qt.frequency_sweep` 的共享入口；本函数只负责
    排版成文本并决定语义色。读不出来时行内与汇总都显示同一原因，不保留旧的点列。
    """
    imported = (str(import_error).strip() if import_error else "") or None
    if imported:
        return _problem(imported, start, step, stop)

    try:
        extras = parse_extra_frequencies(extra_text)
        values = generate_frequencies(start, stop, step, extras)
    except FrequencySweepError as failure:
        return _problem(str(failure), start, step, stop)

    row_text = _compact_frequencies(values)
    if extras:
        row_text += (
            "（含增补 "
            + "，".join(format_frequency(value) for value in extras)
            + " GHz）"
        )
    summary = (
        f"DUT 频率扫描：{_SEGMENT_COUNT} 段，共 {len(values)} 个频点"
        f"（{format_frequency(values[0])} → {format_frequency(values[-1])} GHz）"
    )
    return FrequencyScanPreview(
        row_text=row_text,
        row_tone="muted",
        row_tooltip=_row_tooltip(start, step, stop, values),
        summary=summary,
        summary_tone="muted",
        summary_tooltip=f"共 {len(values)} 个频点：{_full_frequencies(values)}",
        frequencies=tuple(values),
    )
