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
电源”。运行配置转换也只接受显式路由，不按单电源假设猜测设备。

``PowerPlan`` / ``DrainChannel`` 是 v1.1 通道计划的值对象（步骤 4）：前者表达一组
通道的有序上电顺序与组内间隔，后者表达可选的漏极测量路由。两者共用同一个
``SupplyChannel`` 路由类型，不引入第二份路由实现；计划本身不判断绑定关系，也不强制
DUT 必须有漏极。

解析与业务校验在这里是分开的两件事：``from_document`` 只负责把规范文档形状读成值
对象（路由必须写成 ``{supply_id, channel}`` 两段对象），``DrainChannel`` 的成员判定
由显式调用 ``require_in_routes`` 完成，不塞进解析入口。归属 DUT、是否在启用序列内、
是否已绑定等整体规则属于配置/运行校验（步骤 5），不在本模块重复实现。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from application.errors import ConfigurationError
from domain.configuration.models import (
    MAX_INTER_CHANNEL_DELAY_S,
    PLACEHOLDER_VALUES,
    SUPPORTED_COMPRESSION_POINTS,
)
from domain.measurement.values import Decibels, Frequency, PowerDbm

__all__ = [
    "AMPLIFIER_COMPRESSION_TARGETS_DB",
    "AmplifierInputError",
    "AmplifierMeasurementInputs",
    "DrainChannel",
    "MAX_INTER_CHANNEL_DELAY_S",
    "PowerPlan",
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

# 组内相邻两次通道开启之间的最大等待秒数。唯一来源是 domain 的配置模型
# （``domain.configuration.models.MAX_INTER_CHANNEL_DELAY_S``），这样步骤 5 的
# ``validate_power_plan`` 与这里的值对象共用同一个上限，domain 也不必反向依赖
# application。本模块只做再导出，不另写一份数值。
# 范围由组计划值对象在构造时判定，超界（例如把毫秒误填成 2000）必须显式失败，
# 不能裁剪成允许值。


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

        这是唯一接受拼接文本的入口；规范文档解析（:meth:`from_document`）不接受
        它。裸通道名不是合法路由：旧快照的 ``CH1`` 属于“按单电源猜测”，不在正式
        输入模型里放行。
        """
        text = _text(token, "供电路由")
        supply_id, separator, channel = text.partition("/")
        if not separator or not supply_id.strip() or not channel.strip():
            raise AmplifierInputError(
                f"供电路由必须写成 supply_id/通道名（如 'PS1/CH1'），收到 {token!r}"
            )
        return cls(supply_id=supply_id, channel=channel)

    @classmethod
    def from_document(cls, value: Any) -> "SupplyChannel":
        """把 v1.1 文档里的路由读成显式两段。

        正式形状是两个平铺键 ``{"supply_id", "channel"}``；也接受已经构造好的
        :class:`SupplyChannel`。未知键、缺字段与拼接文本都拒绝：``"supply_id/CHx"``
        只属于运行映射的历史写法，必须由调用方显式交给
        :meth:`from_routing`，不在文档解析里隐式兼容，否则“新格式写入、旧格式读入”
        会在同一入口混用。
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            unknown = {
                key
                for key in value
                if key not in {"supply_id", "channel"}
            }
            if unknown:
                names = "、".join(sorted(str(key) for key in unknown))
                raise AmplifierInputError(f"供电路由不能包含额外字段：{names}")
            if "supply_id" not in value or "channel" not in value:
                raise AmplifierInputError(
                    "供电路由必须写成 {supply_id, channel} 两个字段"
                )
            return cls(supply_id=value["supply_id"], channel=value["channel"])
        if isinstance(value, str):
            raise AmplifierInputError(
                "供电路由必须写成 {supply_id, channel} 对象；"
                f"拼接文本 {value!r} 只在运行映射兼容读取处由 from_routing 解析"
            )
        raise AmplifierInputError(
            "供电路由必须是 {supply_id, channel} 对象，"
            f"收到 {type(value).__name__}"
        )

    def to_document(self) -> dict[str, str]:
        """输出 v1.1 文档里的两段路由对象；不写 ``supply_id/CHx`` 拼接串。"""
        return {"supply_id": self.supply_id, "channel": self.channel}

    @property
    def token(self) -> str:
        """运行映射/日志使用的路由写法；只用于展示与持久化原文，不参与解析。"""
        return f"{self.supply_id}/{self.channel}"

    def __str__(self) -> str:
        return self.token


def _route_members(values: Any, label: str) -> tuple[SupplyChannel, ...]:
    """把通道集合收敛为受控 tuple，只检查元素类型，不要求去重。"""
    if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Iterable):
        raise AmplifierInputError(
            f"{label}必须是 SupplyChannel 序列，收到 {type(values).__name__}"
        )
    routes = tuple(values)
    for index, route in enumerate(routes):
        if not isinstance(route, SupplyChannel):
            raise AmplifierInputError(
                f"{label}[{index}] 必须是 SupplyChannel，"
                f"收到 {type(route).__name__}"
            )
    return routes


def _ordered_routes(values: Any, label: str) -> tuple[SupplyChannel, ...]:
    """上电顺序：只接受 ``list``/``tuple``，并拒绝重复的物理路由。

    白名单是刻意的：顺序就是上电顺序，集合与生成器不能在这里被悄悄展开成某个
    顺序。调用方若持有其它有序容器（``deque`` 等），需要自己显式转换。
    """
    if not isinstance(values, (list, tuple)):
        raise AmplifierInputError(
            f"{label}必须是有序序列（列表或元组），"
            f"收到 {type(values).__name__}"
        )
    routes = _route_members(values, label)
    seen: set[tuple[str, str]] = set()
    for index, route in enumerate(routes):
        key = (route.supply_id, route.channel)
        if key in seen:
            raise AmplifierInputError(
                f"{label}不能重复：{route.token}（重复项见 [{index}]）"
            )
        seen.add(key)
    return routes


def _inter_channel_delay(value: Any) -> float:
    """把组间隔收敛为有限的非负秒数；超界不裁剪，直接失败。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AmplifierInputError(
            "通道间隔必须是有限数值（秒），"
            f"收到 {type(value).__name__}"
        )
    seconds = float(value)
    if not math.isfinite(seconds):
        raise AmplifierInputError(f"通道间隔必须是有限数值（秒），收到 {value!r}")
    if seconds < 0:
        raise AmplifierInputError(f"通道间隔不能为负值，收到 {seconds}s")
    if seconds > MAX_INTER_CHANNEL_DELAY_S:
        raise AmplifierInputError(
            f"通道间隔不能超过 {MAX_INTER_CHANNEL_DELAY_S:g}s，收到 {seconds}s"
        )
    return seconds


@dataclass(frozen=True)
class PowerPlan:
    """一组电源通道的有序上电计划。

    ``power_sequence`` 的**列表顺序就是上电顺序**；每条元素是显式物理路由
    （``supply_id + channel``）。``inter_channel_delay_s`` 是同组相邻两次成功开启
    之间的等待秒数，单通道计划不生效但仍须在 ``0..MAX_INTER_CHANNEL_DELAY_S`` 内。

    构造只接受 ``list``/``tuple``（其它容器需显式转换）；值对象只回答计划本身是否
    成立：拒绝重复物理路由、非法路由与越界间隔；不判断路由是否属于已绑定电源，
    也不强制 DUT 必须配置漏极——前者是文档/绑定校验，后者只在需要效率计算的测量
    模式下由运行校验要求。
    """

    power_sequence: tuple[SupplyChannel, ...] = ()
    inter_channel_delay_s: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "power_sequence",
            _ordered_routes(self.power_sequence, "通道上电顺序"),
        )
        object.__setattr__(
            self,
            "inter_channel_delay_s",
            _inter_channel_delay(self.inter_channel_delay_s),
        )

    @classmethod
    def empty(cls) -> "PowerPlan":
        """空计划：没有任何通道要开启，间隔固定写成规范值 ``0.0``。"""
        return cls()

    @property
    def is_empty(self) -> bool:
        return not self.power_sequence

    @property
    def tokens(self) -> tuple[str, ...]:
        """按上电顺序返回 ``supply_id/channel``；只用于展示、比较与日志。"""
        return tuple(route.token for route in self.power_sequence)

    @classmethod
    def from_document(cls, value: Any) -> "PowerPlan":
        """读取 v1.1 的 ``power_plan`` 对象。

        ``None`` 或缺失的整个 ``power_plan`` 按空计划处理（迁移产物与尚未配置的
        编辑段都属于这种状态）；对象里的未知键、非法路由与越界间隔仍然拒绝。
        缺失的 ``power_sequence`` / ``inter_channel_delay_s`` 分别按空序列与
        ``0.0`` 归一，写出时两个键永远同时存在。
        """
        if value is None:
            return cls()
        if not isinstance(value, Mapping):
            raise AmplifierInputError(
                "power_plan 必须是对象，"
                f"收到 {type(value).__name__}"
            )
        unknown = {
            key
            for key in value
            if key not in {"power_sequence", "inter_channel_delay_s"}
        }
        if unknown:
            names = "、".join(sorted(str(key) for key in unknown))
            raise AmplifierInputError(f"power_plan 不能包含未知字段：{names}")
        raw_sequence = value.get("power_sequence", ())
        if not isinstance(raw_sequence, (list, tuple)):
            raise AmplifierInputError(
                "power_sequence 必须是有序序列（列表或元组），"
                f"收到 {type(raw_sequence).__name__}"
            )
        routes = tuple(
            SupplyChannel.from_document(item) for item in raw_sequence
        )
        delay = value.get("inter_channel_delay_s", 0.0)
        return cls(power_sequence=routes, inter_channel_delay_s=delay)

    def to_document(self) -> dict[str, Any]:
        """输出 v1.1 的 ``power_plan``；顺序、两段路由与 float 间隔。"""
        return {
            "power_sequence": [
                route.to_document() for route in self.power_sequence
            ],
            "inter_channel_delay_s": self.inter_channel_delay_s,
        }


@dataclass(frozen=True)
class DrainChannel:
    """效率计算使用的漏极路由；它不是上电计划的一部分。

    漏极是**可选**测量输入：线损、驱动映射等不需要效率的测量不配置它（``None``）。
    只要配置了，就必须指向本次配置里真实存在的物理通道；值对象只提供显式的
    :meth:`require_in_routes` 成员判定，完整的有效性判定链（属于 DUT、在启用序列
    与 ``channel_settings`` 中、电源已登记并启用）属于步骤 5 的配置/运行校验。是否
    必须配置属于“主功放运行”的校验，不在值对象里强制。漏极不参与排序、间隔、启停
    或安全关断。
    """

    route: SupplyChannel | None = None

    def __post_init__(self) -> None:
        if self.route is not None and not isinstance(self.route, SupplyChannel):
            raise AmplifierInputError(
                "漏极路由必须是 SupplyChannel 或 None，"
                f"收到 {type(self.route).__name__}"
            )

    @property
    def is_configured(self) -> bool:
        return self.route is not None

    @classmethod
    def from_document(
        cls,
        value: Any,
        *,
        available: Iterable[SupplyChannel] | None = None,
    ) -> "DrainChannel":
        """读取 ``drain_channel``；``None`` 表示未配置。

        只做形状解析：漏极是否真属于“本次配置的通道”需要完整配置（分组、序列、
        ``channel_settings`` 与绑定），属于步骤 5 的配置/运行校验，不在这里猜。

        ``available`` 是显式可选的便捷成员判定，语义严格等于
        ``require_in_routes(available)``：调用方给出的集合是什么，就只检查是否在
        其中，不隐含“属于 DUT/已启用/在序列内”。
        """
        drain = cls(
            route=None if value is None else SupplyChannel.from_document(value)
        )
        if available is not None:
            drain.require_in_routes(available)
        return drain

    def to_document(self) -> dict[str, str] | None:
        """未配置写 ``None``；已配置写两段路由对象，不写拼接串。"""
        return None if self.route is None else self.route.to_document()

    def require_in_routes(
        self, routes: Iterable[SupplyChannel]
    ) -> "DrainChannel":
        """已配置的漏极必须出现在给定路由集合里；本方法只回答“在不在”。

        它不判断该集合是否属于 DUT、是否已绑定、是否在 ``power_sequence`` 或
        ``channel_settings`` 中——那些是完整配置校验的职责（§6.9 判定链）。未配置
        漏极时直接返回，不做任何要求。
        """
        if self.route is None:
            return self
        members = _route_members(routes, "给定通道集合")
        if self.route not in members:
            raise AmplifierInputError(
                f"漏极通道 {self.route.token} 不在给定的通道集合中"
            )
        return self


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

    @classmethod
    def from_run_configuration(cls, configuration: Any) -> "AmplifierMeasurementInputs":
        """从已冻结的运行配置构造正式输入（应用边界唯一转换入口）。

        只读取领域运行配置的**类型化字段**（``TestPlan`` / ``RunResourceMapping``），
        不读取历史测量字典的键名：测试方案提供频率、扫描、压缩目标、衰减器、DUT
        输入限制与驱动模式，运行映射提供本机实际接线的 DUT 供电通道。

        非法或缺失字段统一收敛为 :class:`AmplifierInputError`，因此调用方可以在连接
        任何仪器之前失败。运行映射只接受显式 ``supply_id/CHx`` 路由。
        """
        plan = getattr(configuration, "test_plan", None)
        mapping = getattr(configuration, "run_mapping", None)
        if plan is None or mapping is None:
            raise AmplifierInputError(
                "主功放正式输入必须来自运行配置：缺少 test_plan 或 run_mapping"
            )
        try:
            return cls(
                frequencies=tuple(
                    Frequency.from_ghz(value) for value in plan.frequencies
                ),
                sweep=PowerSweep(
                    start=PowerDbm(plan.start_power),
                    stop=PowerDbm(plan.stop_power),
                    step=Decibels(plan.power_step),
                ),
                compression_target=Decibels.parse(plan.compression_point),
                attenuator=Decibels(plan.attenuator_value),
                max_dut_input_power=PowerDbm(plan.max_input_power),
                driver_enabled=plan.driver_enabled,
                dut_power_channels=_dut_power_channels(mapping),
            )
        except AmplifierInputError:
            raise
        except (AttributeError, TypeError, ValueError) as error:
            raise AmplifierInputError(
                f"主功放运行配置无法转换为正式输入: {error}"
            ) from error

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


def _dut_power_channels(mapping: Any) -> tuple[SupplyChannel, ...]:
    """把运行映射中的显式路由解析为供电通道。"""
    return tuple(
        SupplyChannel.from_routing(getattr(item, "channel", None))
        for item in (getattr(mapping, "dut_power_channels", ()) or ())
    )


