"""主功放用例的正式输入模型（技术债步骤 7）。

本模块定义主功放测量**唯一的正式输入表示**：测试频率、功率扫描参数、压缩目标、
衰减器设定、DUT 输入限制、驱动模式与 DUT 供电通道映射。它是应用边界的输入 DTO：

- 只表达业务语义，不复刻旧配置字典的键名（``test_frequencies`` / ``signal_source``
  / ``power_supply_assignment`` 等）；
- 单位由字段类型表达，构造时即收敛为 :mod:`domain.measurement.values` 的值对象，
  调用方不需要也不应该再解释裸数值的单位；
- 构造时校验（缺字段、类型不符、单位不符、越界、重复、路由写法错误一律
  :class:`AmplifierInputError`），因此非法输入在打开设备之前就失败；
- 不做文件读取、不接触仪器、不生成结果文档。仪器地址与传输对象由 instrument
  端口和运行上下文提供，不进测试参数 DTO（``ServicePlan`` 与 ``PreparedRun`` 的
  分工不变）。

字段来源、单位与必填性（:class:`AmplifierMeasurementInputs`）：

| 字段 | 类型（单位） | 来源 | 必填 | 非法值处理 |
| --- | --- | --- | --- | --- |
| `frequencies` | `tuple[Frequency, ...]`（内部 GHz） | 测试方案的频率列表 | 是，非空 | 空列表、重复频率、非 `Frequency` 值 → `AmplifierInputError` |
| `sweep` | `PowerSweep`（起始/终止 dBm，步进 dB） | 测试方案的信号源扫描参数 | 是 | 起始大于终止、步进非正、非值对象 → `AmplifierInputError` |
| `compression_target` | `Decibels`（dB） | 测试方案的压缩点要求（1/3/5 dB） | 是 | 不在 `AMPLIFIER_COMPRESSION_TARGETS_DB` 内 → `AmplifierInputError` |
| `attenuator` | `Decibels`（dB，非负） | 测试方案的衰减器设定 | 是 | 负值、非值对象 → `AmplifierInputError` |
| `max_dut_input_power` | `PowerDbm`（dBm） | 测试方案的 DUT 输入功率限制 | 是 | 非值对象 → `AmplifierInputError`（不再默认“不限制”） |
| `driver_enabled` | `bool` | 测试方案的驱动模式开关 | 是 | 非布尔值（含 `1`/`"yes"`）→ `AmplifierInputError` |
| `dut_power_channels` | `tuple[SupplyChannel, ...]` | 运行映射里本机的 DUT 供电通道 | 是，非空 | 空集合、重复通道、电源标识或通道名为空/占位值/含 `/` → `AmplifierInputError` |

``SupplyChannel`` 只接受显式路由（``supply_id + channel`` 或
``"<supply_id>/<channel>"``）：裸通道名（旧快照的 ``CH1``）不在这里被猜测成“唯一那台
电源”，按单电源假设的历史兼容在步骤 10 单独收敛。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from application.errors import ConfigurationError
from domain.configuration.models import PLACEHOLDER_VALUES, SUPPORTED_COMPRESSION_POINTS
from domain.measurement.values import Decibels, Frequency, PowerDbm

__all__ = [
    "AMPLIFIER_COMPRESSION_TARGETS_DB",
    "AmplifierInputError",
    "AmplifierMeasurementInputs",
    "PowerSweep",
    "SupplyChannel",
]


class AmplifierInputError(ConfigurationError):
    """主功放正式输入模型不满足业务结构。

    归类为配置失败（:class:`application.errors.ConfigurationError`），因为它在任何
    设备动作之前就表示“这次测量的输入本身不成立”。
    """


# 允许的压缩目标：与测试方案 schema 的 ``compression_point`` 允许值同源，
# 由域名层 ``SUPPORTED_COMPRESSION_POINTS``（"1dB"/"3dB"/"5dB"）解析得到，
# 不在本模块另写一份“支持的压缩点”。
AMPLIFIER_COMPRESSION_TARGETS_DB = frozenset(
    Decibels.parse(text).db for text in SUPPORTED_COMPRESSION_POINTS
)


def _text(value: Any, label: str) -> str:
    """把标识类字段收敛为去空白的非空文本；空值与配置占位值直接拒绝。"""
    if not isinstance(value, str):
        raise AmplifierInputError(
            f"{label} 必须是文本，收到 {type(value).__name__}"
        )
    text = value.strip()
    if not text:
        raise AmplifierInputError(f"{label} 不能为空")
    if text in PLACEHOLDER_VALUES:
        raise AmplifierInputError(f"{label} 不能是占位值 {text!r}，必须填写实际值")
    return text


def _require_value(value: Any, expected: type, label: str) -> Any:
    """要求字段已经是目标值对象（单位由类型表达，不接受裸数值）。"""
    if not isinstance(value, expected):
        raise AmplifierInputError(
            f"{label} 必须是 {expected.__name__}，收到 {type(value).__name__}"
        )
    return value


@dataclass(frozen=True)
class SupplyChannel:
    """DUT 供电通道的一条显式路由：哪台电源的哪个通道。

    电源标识就是运行快照 ``instruments.power_supplies`` 的集合键（``supply_id``），
    通道名是现场短名（``CH1``）。两者都按业务语义保存，不再用 ``"<supply_id>/CH1"``
    这种拼接文本充当正式表示；拼接文本只在 :meth:`from_routing` 边界解析一次。
    """

    supply_id: str
    channel: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "supply_id", _text(self.supply_id, "供电通道的电源标识"))
        object.__setattr__(self, "channel", _text(self.channel, "供电通道的通道名"))
        if "/" in self.supply_id or "/" in self.channel:
            raise AmplifierInputError(
                "供电通道的电源标识与通道名都不得包含 '/'："
                f"{self.supply_id!r} / {self.channel!r}"
            )

    @classmethod
    def from_routing(cls, token: Any) -> "SupplyChannel":
        """把运行映射里的路由写法 ``<supply_id>/<channel>`` 解析成显式两段。

        裸通道名不是合法路由：旧快照的 ``CH1`` 属于“按单电源猜测”，不在正式输入
        模型里放行。
        """
        text = _text(token, "供电路由")
        supply_id, separator, channel = text.partition("/")
        if not separator or not supply_id.strip() or not channel.strip():
            raise AmplifierInputError(
                f"供电路由必须写成 supply_id/通道名（如 'PS1/CH1'），收到 {token!r}"
            )
        return cls(supply_id=supply_id, channel=channel)

    @property
    def token(self) -> str:
        """运行映射/日志使用的路由写法；只用于展示与持久化原文，不参与解析。"""
        return f"{self.supply_id}/{self.channel}"

    def __str__(self) -> str:
        return self.token


@dataclass(frozen=True)
class PowerSweep:
    """信号源功率扫描参数：起始功率、终止功率与步进。

    起始/终止是绝对功率（dBm），步进是相对增量（dB）；三者都不接受裸数值，
    避免“把步进当成绝对功率”这类单位混用。``stop`` 是扫描上限，非整步时
    不额外插入终点。
    """

    start: PowerDbm
    stop: PowerDbm
    step: Decibels

    def __post_init__(self) -> None:
        _require_value(self.start, PowerDbm, "功率扫描起始功率")
        _require_value(self.stop, PowerDbm, "功率扫描终止功率")
        _require_value(self.step, Decibels, "功率扫描步进")
        if self.step.db <= 0:
            raise AmplifierInputError(f"功率扫描步进必须大于 0dB，收到 {self.step.db}dB")
        if self.start.dbm > self.stop.dbm:
            raise AmplifierInputError(
                "功率扫描起始功率不能大于终止功率："
                f"{self.start.dbm}dBm > {self.stop.dbm}dBm"
            )

    def points(self) -> tuple[PowerDbm, ...]:
        """返回扫描点；``stop`` 是上限，不会为凑整步长而越过它。"""
        start = self.start.dbm
        stop = self.stop.dbm
        step = self.step.db
        count = math.floor((stop - start) / step + 1e-12)
        tolerance = max(abs(start), abs(stop), abs(step), 1.0) * 1e-12
        while count >= 0 and start + count * step > stop + tolerance:
            count -= 1
        while start + (count + 1) * step <= stop + tolerance:
            count += 1
        return tuple(PowerDbm(start + index * step) for index in range(count + 1))


@dataclass(frozen=True, kw_only=True)
class AmplifierMeasurementInputs:
    """主功放测量的正式输入：频率、功率扫描、压缩目标、衰减器、DUT 限制、驱动模式与供电通道。

    全部字段必填、构造即校验：主功放用例只消费本对象，不需要 ``Mapping[str, Any]``
    作为正式配置类型。运行标识、仪器端口、阶段记录器与校准输入（线损/驱动映射）
    属于运行契约，不在这里表达。
    """

    frequencies: tuple[Frequency, ...]
    sweep: PowerSweep
    compression_target: Decibels
    attenuator: Decibels
    max_dut_input_power: PowerDbm
    driver_enabled: bool
    dut_power_channels: tuple[SupplyChannel, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "frequencies", self._frequencies(self.frequencies))
        _require_value(self.sweep, PowerSweep, "功率扫描参数")
        target = _require_value(self.compression_target, Decibels, "压缩目标")
        if target.db not in AMPLIFIER_COMPRESSION_TARGETS_DB:
            allowed = "、".join(f"{value}dB" for value in sorted(AMPLIFIER_COMPRESSION_TARGETS_DB))
            raise AmplifierInputError(
                f"压缩目标必须是 {allowed} 之一，收到 {target.db}dB"
            )
        attenuator = _require_value(self.attenuator, Decibels, "衰减器设定")
        if attenuator.db < 0:
            raise AmplifierInputError(f"衰减器设定不能为负值，收到 {attenuator.db}dB")
        _require_value(self.max_dut_input_power, PowerDbm, "DUT 最大输入功率")
        if not isinstance(self.driver_enabled, bool):
            raise AmplifierInputError(
                f"驱动模式开关必须是布尔值，收到 {type(self.driver_enabled).__name__}"
            )
        object.__setattr__(
            self, "dut_power_channels", self._routed_channels(self.dut_power_channels)
        )

    @staticmethod
    def _frequencies(values: Any) -> tuple[Frequency, ...]:
        if not isinstance(values, Iterable) or isinstance(values, (str, bytes, Mapping)):
            raise AmplifierInputError(
                f"测试频率必须是 Frequency 序列，收到 {type(values).__name__}"
            )
        frequencies = tuple(values)
        if not frequencies:
            raise AmplifierInputError("测试频率不能为空")
        seen: set[float] = set()
        for index, value in enumerate(frequencies):
            _require_value(value, Frequency, f"测试频率[{index}]")
            if value.ghz <= 0:
                raise AmplifierInputError(f"测试频率[{index}] 必须大于 0GHz，收到 {value.ghz}GHz")
            if value.ghz in seen:
                raise AmplifierInputError(f"测试频率不能重复：{value.ghz}GHz")
            seen.add(value.ghz)
        return frequencies

    @staticmethod
    def _routed_channels(values: Any) -> tuple[SupplyChannel, ...]:
        if not isinstance(values, Iterable) or isinstance(values, (str, bytes, Mapping)):
            raise AmplifierInputError(
                f"DUT 供电通道必须是 SupplyChannel 序列，收到 {type(values).__name__}"
            )
        channels = tuple(values)
        if not channels:
            raise AmplifierInputError("DUT 供电通道不能为空：主功放测量必须至少有一路 DUT 供电")
        seen: set[tuple[str, str]] = set()
        for index, value in enumerate(channels):
            _require_value(value, SupplyChannel, f"DUT 供电通道[{index}]")
            key = (value.supply_id, value.channel)
            if key in seen:
                raise AmplifierInputError(
                    f"DUT 供电通道重复分配：{value.supply_id}/{value.channel}"
                )
            seen.add(key)
        return channels

    def dut_supplies(self) -> Mapping[str, tuple[str, ...]]:
        """按电源分组的 DUT 通道：``{supply_id: (通道名, ...)}``，顺序按声明顺序。

        同一台电源可以同时供 DUT 的多个通道（例如 gate 与 drain），分组是只读投影，
        供测量逐通道读取电压/电流时使用。
        """
        grouped: dict[str, list[str]] = {}
        for item in self.dut_power_channels:
            grouped.setdefault(item.supply_id, []).append(item.channel)
        return MappingProxyType({name: tuple(channels) for name, channels in grouped.items()})
