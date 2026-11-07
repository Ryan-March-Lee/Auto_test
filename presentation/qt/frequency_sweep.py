"""频率扫描参数与频点列表之间的纯 Python 转换。

配置页不再让用户逐点输入频率，而是编辑“起始频率 / 结束频率 / 频率步长 / 增补频点”。
本模块只负责把这四个读数转换成配置文档仍在使用的 ``test_frequencies: list[float]``
（GHz），以及把已有列表反向拆成扫描参数。它不依赖 Qt，也不判断 domain 的业务规则。

口径（与 ``application.dto.amplifier_inputs.PowerSweep`` 保持一致）：

- 结束频率是扫描上限，且必须落在步长网格上；不整步时直接报错，不自动补终点。
- 不在网格上的频点由“增补频点”显式补充；增补允许落在起始/结束区间之外。
- 生成结果升序、去重；增补点与网格点重复时只保留一个。

加载旧配置时若频率列表不是严格等步长，不尝试兼容拆分：调用方应保留原列表并报告
“无法用起始/结束/步长表示”，要求用户重新输入扫描参数。
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

__all__ = [
    "DEFAULT_FREQUENCY_EXTRA_TEXT",
    "DEFAULT_FREQUENCY_START",
    "DEFAULT_FREQUENCY_STEP",
    "DEFAULT_FREQUENCY_STOP",
    "DEFAULT_TEST_FREQUENCIES",
    "FREQUENCY_DECIMALS",
    "FrequencySweepError",
    "best_effort_sweep",
    "format_frequency",
    "frequencies_are_uniform",
    "generate_frequencies",
    "parse_extra_frequencies",
    "sweep_from_frequencies",
]

# 频率数值框的显示精度：GHz 下的 1e-6 足够覆盖常见的 kHz 级步长，也能把
# ``4.0 + 2 * 0.4`` 这类浮点误差收敛成稳定的十进制文本。
FREQUENCY_DECIMALS = 6

# 防止误输入极小步长把界面卡死；超过上限时在页面上给出明确错误。
MAX_GENERATED_FREQUENCIES = 10000

# 配置页空配置时的默认扫描参数：生成结果与原来的默认频率列表一致。
DEFAULT_FREQUENCY_START = 4.0
DEFAULT_FREQUENCY_STOP = 5.6
DEFAULT_FREQUENCY_STEP = 0.4
DEFAULT_FREQUENCY_EXTRA_TEXT = "5.8"

#: ``test_frequencies`` 的默认值在模块末尾由默认扫描参数生成；这里先声明名称，避免
#: 默认值与生成规则出现两份来源。


class FrequencySweepError(ValueError):
    """频率扫描参数无法生成合法频点。"""


def _round(value: float) -> float:
    return round(float(value), FREQUENCY_DECIMALS)


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FrequencySweepError(f"{label}必须是数字")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise FrequencySweepError(f"{label}必须大于 0")
    return _round(number)


def _finite_positive(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    return math.isfinite(number) and number > 0


def parse_extra_frequencies(text: Any) -> tuple[float, ...]:
    """解析“增补频点”文本：逗号、中文逗号、分号或空白分隔，允许为空。

    非法文本抛出 :class:`FrequencySweepError`，由页面显示为用户错误；不做任何静默
    丢弃，避免“看起来加了频点、实际没加上”。
    """
    if text is None:
        return ()
    if not isinstance(text, str):
        raise FrequencySweepError("增补频点必须是以逗号或空格分隔的数字")
    tokens = [token for token in re.split(r"[,，;；\s]+", text.strip()) if token]
    values: list[float] = []
    for token in tokens:
        try:
            number = float(token)
        except ValueError as failure:
            raise FrequencySweepError(f"增补频点“{token}”不是数字") from failure
        if not math.isfinite(number) or number <= 0:
            raise FrequencySweepError(f"增补频点“{token}”必须大于 0")
        values.append(_round(number))
    return tuple(values)


def generate_frequencies(
    start: Any,
    stop: Any,
    step: Any,
    extras: Iterable[Any] = (),
) -> tuple[float, ...]:
    """按起始/结束/步长生成升序频点，再合并增补频点。

    结束频率必须满足 ``stop = start + 整数 × step``；否则抛出
    :class:`FrequencySweepError`，不自动补终点，也不悄悄改变扫描范围。
    """
    first = _positive_number(start, "起始频率")
    last = _positive_number(stop, "结束频率")
    increment = _positive_number(step, "频率步长")
    tolerance = max(abs(first), abs(last), abs(increment), 1.0) * 1e-9

    if last < first - tolerance:
        raise FrequencySweepError("结束频率不能小于起始频率")

    count = round((last - first) / increment)
    if count < 0:
        raise FrequencySweepError("结束频率不能小于起始频率")
    endpoint = first + count * increment
    if abs(endpoint - last) > tolerance:
        raise FrequencySweepError(
            "结束频率必须落在步长上：结束频率 = 起始频率 + 整数 × 步长；"
            "不在网格上的频点请写入增补"
        )
    if count + 1 > MAX_GENERATED_FREQUENCIES:
        raise FrequencySweepError(
            f"频点数量过多（{count + 1} 个），请增大频率步长"
        )

    grid = [_round(first + index * increment) for index in range(count + 1)]
    extra_values = [_positive_number(value, "增补频点") for value in extras]
    return tuple(sorted(set(grid + extra_values)))


def frequencies_are_uniform(values: Any) -> bool:
    """频率列表是否为严格递增的等步长序列（单点视为等步长）。"""
    if not isinstance(values, (list, tuple)) or not values:
        return False
    numbers: list[float] = []
    for value in values:
        if not _finite_positive(value):
            return False
        numbers.append(_round(value))
    if len(numbers) == 1:
        return True
    step = _round(numbers[1] - numbers[0])
    if step <= 0:
        return False
    tolerance = max(abs(numbers[0]), abs(numbers[-1]), abs(step), 1.0) * 1e-9
    return all(
        abs((right - left) - step) <= tolerance
        for left, right in zip(numbers, numbers[1:])
    )


def sweep_from_frequencies(
    values: Any,
    *,
    default_step: float = DEFAULT_FREQUENCY_STEP,
) -> tuple[float, float, float]:
    """把严格等步长的频率列表拆成 ``(start, stop, step)``。

    非等步长、空列表或非法元素都抛出 :class:`FrequencySweepError`；调用方不得把
    失败当成“可以猜一个步长”的信号。单点列表用 ``default_step`` 占位，生成结果
    仍是该单点。
    """
    if not frequencies_are_uniform(values):
        raise FrequencySweepError("测试频率不是严格等步长序列")
    numbers = [_round(value) for value in values]
    if len(numbers) == 1:
        return numbers[0], numbers[0], _round(default_step)
    return numbers[0], numbers[-1], _round(numbers[1] - numbers[0])


def best_effort_sweep(
    values: Any,
    *,
    default_step: float = DEFAULT_FREQUENCY_STEP,
) -> tuple[float, float, float]:
    """非等步长列表的只读展示用参数：首点、末点与第一个正间隔。

    该结果只用于把控件放到一个接近原列表的位置；页面必须同时保留错误状态并禁止
    保存，不能把这里猜出的步长当成对原列表的合法解释。
    """
    if not isinstance(values, (list, tuple)) or not values:
        return (
            DEFAULT_FREQUENCY_START,
            DEFAULT_FREQUENCY_STOP,
            _round(default_step),
        )
    numbers = [
        _round(float(value))
        for value in values
        if _finite_positive(value)
    ]
    if not numbers:
        return (
            DEFAULT_FREQUENCY_START,
            DEFAULT_FREQUENCY_STOP,
            _round(default_step),
        )
    start, stop = numbers[0], numbers[-1]
    positive_gaps = [
        _round(right - left)
        for left, right in zip(numbers, numbers[1:])
        if right > left
    ]
    step = positive_gaps[0] if positive_gaps else _round(default_step)
    if step <= 0:
        step = _round(default_step)
    return start, stop, step


def format_frequency(value: Any) -> str:
    """把频率显示成不带多余尾零的十进制文本。"""
    number = float(value)
    text = f"{number:.{FREQUENCY_DECIMALS}f}".rstrip("0").rstrip(".")
    return text or "0"


# 空配置基线的频点列表：由默认扫描参数生成，保证“默认控件值”与“默认文档值”同源。
DEFAULT_TEST_FREQUENCIES = generate_frequencies(
    DEFAULT_FREQUENCY_START,
    DEFAULT_FREQUENCY_STOP,
    DEFAULT_FREQUENCY_STEP,
    parse_extra_frequencies(DEFAULT_FREQUENCY_EXTRA_TEXT),
)
