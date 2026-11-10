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

除了输入 DTO，本模块还提供 DUT 功率扫描计划的不可变值对象
:class:`PowerScanSchedule`，以及三个纯计算入口：:func:`validate_power_scan_schedule`
校验相邻计划首尾相接，:meth:`PowerSweep.points` 生成单段预览点，
:func:`target_input_power_grid` 生成预览与测量共用的合并目标点。三者都不读取配置、
不接触仪器。``PowerScanSchedule`` 目前是独立的计划集合值对象，尚未替换
:class:`AmplifierMeasurementInputs` 的单段字段；正式测量链路的接入属于后续阶段。

点位只由一处算法产出：单段点位（:meth:`PowerSweep.points`）与合并点位
（:func:`target_input_power_grid`）共用 ``_step_lattice`` / ``_segment_points``，
因此终止点始终可见，不在步长网格上的终止点作为额外最后一点补入。多段合并由
:func:`target_input_power_grid` 完成：先校验相邻计划首尾相接，再让公共边界只保留
一次；DUT 最大输入也在同一个入口截断，调用方不需要各自再补点、去重或截断。

功率扫描的非法输入按层报错，四处边界各自负责一件事，不互相复制校验：

- 非有限数值（``NaN``/``inf``）在 :class:`PowerDbm` / :class:`Decibels` 构造期由
  ``MeasurementValueError`` 拒绝，不会等到计划集合构造才失败；
- 非正步长与“起点大于终点”由 :class:`PowerSweep` 以 :class:`AmplifierInputError`
  拒绝，因为问题出在单段参数关系；
- 空集合、非 ``list``/``tuple`` 容器和非法元素由 :class:`PowerScanSchedule` 以
  :class:`AmplifierInputError` 拒绝，错误信息带元素下标，便于映射到配置行；
- 相邻计划的空档/重叠由 :func:`validate_power_scan_schedule` 以
  :class:`AmplifierInputError` 拒绝，错误信息带相邻两段的编号和功率值。

值对象与点位生成都保持“不排序、不修补”：连续性校验只报告问题，不会静默调整用户
填写的计划顺序或端点。

字段来源、单位与必填性（:class:`AmplifierMeasurementInputs`）：

| 字段 | 类型（单位） | 来源 | 必填 | 非法值处理 |
| --- | --- | --- | --- | --- |
| `frequencies` | `tuple[Frequency, ...]`（内部 GHz） | 测试方案的频率列表 | 是，非空 | 空列表、重复频率、非 `Frequency` 值 → `AmplifierInputError` |
| `input_power_sweep` | `PowerSweep`（起始/终止 dBm，步进 dB） | 测试方案的**主功放输入功率**扫描参数 | 是 | 起始大于终止、步进非正、非值对象 → `AmplifierInputError`（当前仍为单段，预览与测量已共用 `target_input_power_grid`；多段集合见 `PowerScanSchedule`，替换本字段属于阶段四） |
| `compression_target` | `Decibels`（dB） | 测试方案的压缩点要求（1/3/5 dB） | 是 | 不在 `AMPLIFIER_COMPRESSION_TARGETS_DB` 内 → `AmplifierInputError` |
| `attenuator` | `Decibels`（dB，非负） | 测试方案的衰减器设定 | 是 | 负值、非值对象 → `AmplifierInputError` |
| `max_dut_input_power` | `PowerDbm`（dBm） | 测试方案的 DUT 输入功率限制 | 是 | 非值对象 → `AmplifierInputError`（不再默认“不限制”） |
| `driver_enabled` | `bool` | 测试方案的驱动模式开关 | 是 | 非布尔值（含 `1`/`"yes"`）→ `AmplifierInputError` |
| `driver_gain_estimate` | `Decibels | None`（dB） | 测试方案的驱动功放全局预估增益 | 否 | 非 `Decibels` 值 → `AmplifierInputError` |
| `dut_power_plan` | `PowerPlan` | 运行映射里冻结的 DUT 上电顺序与组间隔 | 是，非空 | 空序列、重复路由、非法路由、越界间隔 → `AmplifierInputError` |
| `channel_settings` | `Mapping[SupplyChannel, ChannelSetting]` | 测试方案里每条 DUT 路由的工作值/保护值 | 是，且与序列集合完全相等 | 缺设置、多设置、非路由键、缺工作值 → `AmplifierInputError` |
| `drain_channel` | `DrainChannel` | 测试方案的漏极测量路由集合（可选，可多条） | 可选，但主功放测量服务构造时要求至少一条 | 已配置但不在上电序列里、重复路由 → `AmplifierInputError` |

``SupplyChannel`` 只接受显式路由（``supply_id + channel`` 或
``"<supply_id>/<channel>"``）：裸通道名（旧快照的 ``CH1``）不在这里被猜测成“唯一那台
电源”。运行配置转换也只接受显式路由，不按单电源假设猜测设备。

``PowerPlan`` / ``DrainChannel`` / ``ChannelSetting`` 是 v1.1 通道计划的值对象
（步骤 4/7）：计划表达一组通道的有序上电顺序与组内间隔，``ChannelSetting`` 为每条
物理路由携带工作值/保护值，漏极表达可选的效率测量路由集合。它们共用同一个
``SupplyChannel`` 路由类型，不引入第二份路由实现；计划本身不判断绑定关系，也不强制
DUT 必须有漏极。

解析与业务校验在这里是分开的两件事：``from_document`` 只负责把规范文档形状读成值
对象（路由必须写成 ``{supply_id, channel}`` 两段对象），``DrainChannel`` 的成员判定
由显式调用 ``require_in_routes`` 完成，不塞进解析入口。归属 DUT、是否在启用序列内、
是否已绑定等整体规则属于配置/运行校验（步骤 5），不在本模块重复实现。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from application.errors import ConfigurationError
from domain.configuration.models import (
    CHANNEL_SETTING_FIELDS,
    INPUT_POWER_REFERENCE,
    MAX_INTER_CHANNEL_DELAY_S,
    PLACEHOLDER_VALUES,
    SUPPORTED_COMPRESSION_POINTS,
    power_reference_confirmation_error,
    power_scan_boundary_tolerance,
)
from domain.measurement.values import Decibels, Frequency, PowerDbm

__all__ = [
    "AMPLIFIER_COMPRESSION_TARGETS_DB",
    "AmplifierInputError",
    "AmplifierMeasurementInputs",
    "ChannelSetting",
    "DEFAULT_POWER_ON_DELAY_S",
    "DrainChannel",
    "INPUT_POWER_REFERENCE",
    "MAX_INTER_CHANNEL_DELAY_S",
    "PowerPlan",
    "PowerScanSchedule",
    "PowerSweep",
    "SupplyChannel",
    "target_input_power_grid",
]


#: 卡面/编辑文档没有单独设置通道上电间隔时的缺省值（秒）。
#: 旧配置仍可用组级 ``inter_channel_delay_s`` 覆盖它；通道显式值优先。
DEFAULT_POWER_ON_DELAY_S = 2.0


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


def _finite_number(value: Any, label: str, *, non_negative: bool = False) -> float:
    """把工作值/保护值收敛为有限浮点数；布尔值与文本一律拒绝。

    ``non_negative`` 与领域校验的电流规则（``models.py`` 的
    ``_validate_channels``）一致：电流可以是 0，但不能为负。电压没有这条限制，
    因为领域契约没有要求电压非负。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AmplifierInputError(
            f"{label} 必须是有限数值，收到 {type(value).__name__}"
        )
    number = float(value)
    if not math.isfinite(number):
        raise AmplifierInputError(f"{label} 必须是有限数值，收到 {value!r}")
    if non_negative and number < 0:
        raise AmplifierInputError(f"{label} 不能为负值，收到 {number:g}")
    return number


def _optional_finite_number(
    value: Any, label: str, *, non_negative: bool = False
) -> float | None:
    """可空保护值：``None`` 保持未配置，其余按有限数值收敛。"""
    if value is None:
        return None
    return _finite_number(value, label, non_negative=non_negative)


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
class ChannelSetting:
    """一条物理路由的工作值/保护值；只承载参数，不承载顺序或角色。

    ``channel_settings`` 按 ``SupplyChannel`` 索引，因此本对象不必再存路由。上电顺序
    只由 :class:`PowerPlan` 决定；漏极也只是一条测量路由，不会因为某条设置“像漏极”
    而改变启停。工作值与保护值必须是有限数值，电流（含电流保护值）按领域规则允许
    0 但不能为负；保护值当前只保存、不下发（契约 §6.13），这里仍原样保留，避免 DTO
    丢掉配置里已有的数值。
    """

    voltage_v: float
    current_a: float
    voltage_protection_v: float | None = None
    current_protection_a: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "voltage_v", _finite_number(self.voltage_v, "通道工作电压")
        )
        object.__setattr__(
            self,
            "current_a",
            _finite_number(self.current_a, "通道工作电流", non_negative=True),
        )
        object.__setattr__(
            self,
            "voltage_protection_v",
            _optional_finite_number(self.voltage_protection_v, "通道电压保护值"),
        )
        object.__setattr__(
            self,
            "current_protection_a",
            _optional_finite_number(
                self.current_protection_a, "通道电流保护值", non_negative=True
            ),
        )

    @classmethod
    def from_document(cls, value: Any) -> "ChannelSetting":
        """读取 ``channel_settings`` 的一条平铺记录（路由两段 + 参数）。

        路由由调用方用 :meth:`SupplyChannel` 解析；这里只读参数并拒绝旧角色字段与
        未知字段。``voltage`` / ``current`` 是必填工作值，保护值与单位可省略。
        """
        if not isinstance(value, Mapping):
            raise AmplifierInputError(
                f"通道参数必须是对象，收到 {type(value).__name__}"
            )
        unknown = {
            key
            for key in value
            if key not in CHANNEL_SETTING_FIELDS
        }
        if unknown:
            names = "、".join(sorted(str(key) for key in unknown))
            raise AmplifierInputError(f"通道参数不能包含额外字段：{names}")
        for field in ("voltage", "current"):
            if field not in value:
                raise AmplifierInputError(f"通道参数缺少 {field}")
        for field, expected in (("unit_voltage", "V"), ("unit_current", "A")):
            unit = value.get(field)
            if unit is not None and unit != expected:
                raise AmplifierInputError(
                    f"通道参数的 {field} 必须是 {expected}，收到 {unit!r}"
                )
        return cls(
            voltage_v=value["voltage"],
            current_a=value["current"],
            voltage_protection_v=value.get("voltage_protection"),
            current_protection_a=value.get("current_protection"),
        )


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
        if isinstance(value, cls):
            return value
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
    """效率计算使用的漏极路由集合；它不是上电计划的一部分。

    漏极是**可选**测量输入：线损、驱动映射等不需要效率的测量不配置它（空集合）。
    可以配置一条或多条物理通道，效率计算把它们各自的直流功率相加。只要配置了，
    每条路由都必须指向本次配置里真实存在的物理通道；值对象只提供显式的
    :meth:`require_in_routes` 成员判定，完整的有效性判定链（属于 DUT、在启用序列
    与 ``channel_settings`` 中、电源已登记并启用）属于步骤 5 的配置/运行校验。是否
    必须配置属于“主功放运行”的校验，不在值对象里强制：DTO 可被不需要效率的场景
    复用，而 :class:`AmplifierMeasurementService` 在构造时要求至少有一条漏极，缺失
    即抛 ``AmplifierInputError``。漏极不参与排序、间隔、启停或安全关断。

    路由集合的顺序就是配置里的书写顺序。
    """

    routes: tuple[SupplyChannel, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.routes, (str, bytes)) or not isinstance(
            self.routes, Iterable
        ):
            raise AmplifierInputError(
                "漏极路由集合必须是 SupplyChannel 序列，"
                f"收到 {type(self.routes).__name__}"
            )
        normalized: list[SupplyChannel] = []
        for item in self.routes:
            if not isinstance(item, SupplyChannel):
                raise AmplifierInputError(
                    "漏极路由集合只能包含 SupplyChannel，"
                    f"收到 {type(item).__name__}"
                )
            if item in normalized:
                raise AmplifierInputError(f"漏极通道不能重复：{item.token}")
            normalized.append(item)
        object.__setattr__(self, "routes", tuple(normalized))

    @property
    def is_configured(self) -> bool:
        return bool(self.routes)

    @classmethod
    def from_document(
        cls,
        value: Any,
        *,
        available: Iterable[SupplyChannel] | None = None,
    ) -> "DrainChannel":
        """读取 ``drain_channel``；``None`` 或空列表表示未配置。

        文档既可以写单条两段路由对象，也可以写路由对象列表（配置页多选 ``Cal.``
        时写出列表）；两种形状都收敛为同一条 :attr:`routes` 集合。

        只做形状解析：漏极是否真属于“本次配置的通道”需要完整配置（分组、序列、
        ``channel_settings`` 与绑定），属于步骤 5 的配置/运行校验，不在这里猜。

        ``available`` 是显式可选的便捷成员判定，语义严格等于
        ``require_in_routes(available)``：调用方给出的集合是什么，就只检查是否在
        其中，不隐含“属于 DUT/已启用/在序列内”。
        """
        if isinstance(value, cls):
            drain = value
        elif value is None:
            drain = cls()
        elif isinstance(value, (list, tuple)):
            drain = cls(
                routes=tuple(SupplyChannel.from_document(item) for item in value)
            )
        else:
            drain = cls(routes=(SupplyChannel.from_document(value),))
        if available is not None:
            drain.require_in_routes(available)
        return drain

    def to_document(self) -> dict[str, str] | list[dict[str, str]] | None:
        """未配置写 ``None``；单漏极写两段路由对象，多漏极写对象列表。"""
        if not self.routes:
            return None
        documents = [route.to_document() for route in self.routes]
        return documents[0] if len(documents) == 1 else documents

    def require_in_routes(
        self, routes: Iterable[SupplyChannel]
    ) -> "DrainChannel":
        """已配置的每条漏极都必须出现在给定路由集合里；本方法只回答“在不在”。

        它不判断该集合是否属于 DUT、是否已绑定、是否在 ``power_sequence`` 或
        ``channel_settings`` 中——那些是完整配置校验的职责（§6.9 判定链）。未配置
        漏极时直接返回，不做任何要求。
        """
        if not self.routes:
            return self
        members = _route_members(routes, "给定通道集合")
        for route in self.routes:
            if route not in members:
                raise AmplifierInputError(
                    f"漏极通道 {route.token} 不在给定的通道集合中"
                )
        return self


_RELATIVE_TOLERANCE = 1e-12


def _boundary_tolerance(left: float, right: float) -> float:
    """两段功率端点相等判断的共享相对容差（dB）。

    相邻计划的“首尾相接”是端点与端点的比较，与步长无关，因此步长不参与这里的
    容差；浮点表述误差（1e-12 量级）之外的差异都是用户真实填写的空档或重叠。
    容差本身由 domain 配置模型定义，配置校验与点位生成共用同一个数值。
    """
    return power_scan_boundary_tolerance(left, right)


def _sweep_endpoint_tolerance(start: float, stop: float, step: float) -> float:
    """功率网格端点比较的共享相对容差（dB）。

    单段点位（:meth:`PowerSweep.points`）、合并点位
    （:func:`target_input_power_grid`）与相邻计划边界去重必须用同一个规则，否则
    同一次配置可能出现“扫描点含终点、目标点不含终点”的漂移。容差只吸收浮点表述
    误差（1e-12 量级），不会放过用户真实填写的越界值。
    """
    return max(_boundary_tolerance(start, stop), abs(step) * _RELATIVE_TOLERANCE)


def _step_lattice(start: float, stop: float, step: float) -> list[float]:
    """按步长从 ``start`` 推进到不超过 ``stop`` 的网格点（不含额外补入的终点）。

    只回答“哪些点落在步长网格上”：末端不会被推过 ``stop``，落在浮点容差内的末点
    由调用方按需求吸附到端点。单段点位与多段合并共用这一处容差循环。
    """
    tolerance = _sweep_endpoint_tolerance(start, stop, step)
    count = math.floor((stop - start) / step + 1e-12)
    while count >= 0 and start + count * step > stop + tolerance:
        count -= 1
    while start + (count + 1) * step <= stop + tolerance:
        count += 1
    return [start + index * step for index in range(count + 1)]


def _segment_points(start: float, stop: float, step: float) -> list[float]:
    """单段点位：步长网格点 + 始终可见的终止点。

    ``start == stop`` 时只有一个点。终止点不在网格上时作为额外最后一点补入；
    网格末点与终止点只差浮点误差时吸附到终止点本身，避免出现两个几乎相同的值。
    """
    tolerance = _sweep_endpoint_tolerance(start, stop, step)
    points = _step_lattice(start, stop, step)
    if abs(points[-1] - stop) <= tolerance:
        points[-1] = stop
    else:
        points.append(stop)
    return points


@dataclass(frozen=True)
class PowerSweep:
    """主功放输入功率扫描参数：起始功率、终止功率与步进。

    起始/终止是**主功放（DUT）输入功率**的绝对功率（dBm），步进是目标输入功率
    网格的相对增量（dB）；三者都不接受裸数值，避免“把步进当成绝对功率”这类单位
    混用。``stop`` 是扫描上限，但始终作为最后一点可见：不在步长网格上时作为额外
    终点补入。
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
        """返回本段扫描点；不会越过 ``stop``，且 ``stop`` 始终是最后一点。

        终止点正好落在步长网格上时只出现一次；不在网格上时作为额外最后一点补入，
        因此单段预览与实际目标点不会出现“少一个终点”的差异。
        """
        return tuple(
            PowerDbm(point)
            for point in _segment_points(self.start.dbm, self.stop.dbm, self.step.db)
        )


def _ordered_sweeps(values: Any, label: str) -> tuple[PowerSweep, ...]:
    """把功率扫描计划收敛为受控 tuple，并拒绝空集合与非法元素。

    顺序就是执行顺序，因此只接受 ``list``/``tuple``：集合与生成器没有稳定顺序，
    不能在这里被悄悄展开成某种“刚好可用”的序列。其他有序容器（如 ``deque``）也
    不例外，避免同一份公共契约出现“有些调用方给序列表、有些给别的容器”的分叉。
    多段至少需要一段计划，空集合直接失败；元素必须是已构造好的
    :class:`PowerSweep`，错误信息带下标，便于定位到具体计划行。各段之间的连续性
    （空档/重叠）属于独立校验，不在本函数重复。构造时复制为新 tuple，调用方之后
    修改自己持有的列表不会改变计划集合。
    """
    if not isinstance(values, (list, tuple)):
        raise AmplifierInputError(
            f"{label}必须是有序序列（列表或元组），"
            f"收到 {type(values).__name__}"
        )
    sweeps = tuple(values)
    if not sweeps:
        raise AmplifierInputError(
            f"{label}不能为空：功率扫描至少需要一段有效计划"
        )
    for index, sweep in enumerate(sweeps):
        if not isinstance(sweep, PowerSweep):
            raise AmplifierInputError(
                f"{label}[{index}] 必须是 PowerSweep，"
                f"收到 {type(sweep).__name__}"
            )
    return sweeps


@dataclass(frozen=True)
class PowerScanSchedule:
    """按用户填写顺序执行的 DUT 功率扫描计划集合。

    每个元素是一段 :class:`PowerSweep`（起点、步长、终点）；``plans`` 的
    **元组顺序就是执行顺序**，不做按起点排序，也不允许用无序容器顶替。构造时只
    接受 ``list``/``tuple`` 并复制为新 tuple：其他容器一律拒绝，调用方之后修改自己
    的列表不会影响已构造的计划集合。

    值对象只回答“计划集合本身是否成立”：至少一段、元素类型正确、容器形状受控。
    非法输入按层报错，避免把不同原因混成一种异常：

    - 非有限数值（``NaN``/``inf``）已在 ``PowerDbm`` / ``Decibels`` 构造期由
      ``MeasurementValueError`` 拒绝，不会进入本对象；
    - 非正步长与起点大于终点由 :class:`PowerSweep` 以 :class:`AmplifierInputError`
      拒绝；
    - 空集合、非 ``list``/``tuple`` 容器和非法元素由本对象以
      :class:`AmplifierInputError` 拒绝，元素错误带下标。

    相邻计划的衔接、DUT 上限与点位合并由后续纯计算入口负责（点位合并见
    :func:`target_input_power_grid`）；构造期既不排序，也不补点，也不校验空档/重叠。

    单段扫描允许 ``start == stop``（单点扫描）；多段计划的最小合法性只要求至少
    一段，而“多段”与“单段”由列表长度自然表达。对象不可变，测量过程中不会被 UI
    状态改变。
    """

    plans: tuple[PowerSweep, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "plans", _ordered_sweeps(self.plans, "功率扫描计划")
        )

    def __len__(self) -> int:
        return len(self.plans)

    def __iter__(self) -> Iterator[PowerSweep]:
        return iter(self.plans)


def _as_power_scan_schedule(scan: Any) -> PowerScanSchedule:
    """允许单段以 :class:`PowerSweep` 传入，统一收敛为计划集合。"""
    if isinstance(scan, PowerScanSchedule):
        return scan
    if isinstance(scan, PowerSweep):
        return PowerScanSchedule(plans=(scan,))
    raise AmplifierInputError(
        "功率扫描计划必须是 PowerScanSchedule（多段）或 PowerSweep（单段），"
        f"收到 {type(scan).__name__}"
    )


def validate_power_scan_schedule(
    scan: PowerScanSchedule | PowerSweep,
    *,
    first_plan_number: int = 1,
) -> PowerScanSchedule:
    """校验相邻计划首尾相接，并把合法计划原样返回（纯计算）。

    只回答“计划顺序是否连续”：第 N 段的终止功率必须等于第 N+1 段的起始功率，比较
    使用 :func:`_boundary_tolerance` 的相对容差，因此浮点运算造成的 1e-12 级差异不会
    误报，真实填写的空档或重叠也不会被放过。

    - 后一段起点更高：空档；
    - 后一段起点更低：重叠（只允许相邻计划共享一个边界点）；
    - 只按用户填写顺序比较相邻两项，不排序、不修补，因此错误里的编号就是配置里的
      计划行号。

    ``first_plan_number`` 只影响错误文案里的编号：调用方只把相邻两项交给本函数校验
    时（例如页面逐行预览），传入后一项在完整列表里的 1 基编号，错误仍指向真实的
    计划行，不需要自己改写共享校验的文案。

    错误信息同时给出两段编号和两个功率值，便于映射到
    ``signal_source.power_scan_plans[N]``。单段计划没有相邻关系，直接通过。
    """
    if not isinstance(first_plan_number, int) or isinstance(
        first_plan_number, bool
    ):
        raise AmplifierInputError(
            "first_plan_number 必须是整数，"
            f"收到 {type(first_plan_number).__name__}"
        )
    if first_plan_number < 1:
        raise AmplifierInputError(
            f"first_plan_number 必须从 1 开始，收到 {first_plan_number}"
        )
    schedule = _as_power_scan_schedule(scan)
    plans = schedule.plans
    for index in range(len(plans) - 1):
        previous_stop = plans[index].stop.dbm
        following_start = plans[index + 1].start.dbm
        previous_number = first_plan_number + index
        following_number = previous_number + 1
        tolerance = _boundary_tolerance(previous_stop, following_start)
        if following_start > previous_stop + tolerance:
            raise AmplifierInputError(
                f"第 {previous_number} 段终止功率 {previous_stop}dBm 与 "
                f"第 {following_number} 段起始功率 {following_start}dBm 之间存在空档："
                "相邻计划必须首尾相接"
            )
        if following_start < previous_stop - tolerance:
            raise AmplifierInputError(
                f"第 {previous_number} 段终止功率 {previous_stop}dBm 与 "
                f"第 {following_number} 段起始功率 {following_start}dBm 重叠："
                "只允许相邻计划共享一个边界点"
            )
    return schedule


def target_input_power_grid(
    scan: PowerScanSchedule | PowerSweep,
    max_dut_input_power: PowerDbm,
) -> tuple[PowerDbm, ...]:
    """生成正式测试的合并目标 DUT 输入功率网格（纯计算，不接触配置或仪器）。

    ``scan`` 可以是单段 :class:`PowerSweep`（兼容现有单计划调用方），也可以是
    有序的 :class:`PowerScanSchedule`。这是预览与实际测量共用的唯一入口：各段按
    列表顺序生成点位，``stop`` 始终可见，不在步长网格上的终止点作为额外最后一点
    补入。有效上限取最后一段结束功率与 DUT 最大输入功率的较小值，DUT 限制在同一个
    入口截断，调用方不需要再自行补点或去重：

    - 进入合并前先用 :func:`validate_power_scan_schedule` 校验相邻计划首尾相接，
      因此空档或重叠在这里直接拒绝，不会生成一份“看起来能用”的点列；
    - 相邻计划的公共边界只保留一次；
    - 上限落在某段中间时，该段在达到上限后停止，后续整段跳过；
    - 上限正好落在网格上时作为普通点只出现一次；不在网格上时补入为最后一点；
    - 首个目标点高于有效上限时拒绝配置；起点与上限只差浮点误差时视为同一点，
      只返回上限本身；
    - 输出按升序排列且没有重复点。

    端点比较沿用 :meth:`PowerSweep.points` 的相对容差（共用
    :func:`_sweep_endpoint_tolerance`）：浮点误差既不会造出重复点，也不会让最后
    一个点越过上限。返回值只含 :class:`PowerDbm`，调用方不需要再解释单位。
    """
    schedule = validate_power_scan_schedule(scan)
    limit = _require_value(max_dut_input_power, PowerDbm, "DUT 最大输入功率")
    plans = schedule.plans
    last_stop = plans[-1].stop.dbm
    upper = min(last_stop, limit.dbm)
    first = plans[0]
    first_tolerance = _sweep_endpoint_tolerance(
        first.start.dbm, upper, first.step.db
    )
    if first.start.dbm > upper + first_tolerance:
        raise AmplifierInputError(
            "目标输入功率起始值高于有效上限："
            f"{first.start.dbm}dBm > {upper}dBm（最后一段结束 {last_stop}dBm 与 "
            f"DUT 最大输入 {limit.dbm}dBm 取较小值）"
        )

    merged: list[float] = []
    for sweep in plans:
        start = sweep.start.dbm
        step = sweep.step.db
        tolerance = _sweep_endpoint_tolerance(start, upper, step)
        if start > upper + tolerance:
            # 有效上限已经在前面的计划里到达，后续计划完全在上限之上，不产生目标点。
            continue
        segment_stop = min(sweep.stop.dbm, upper)
        if start >= segment_stop - tolerance:
            # 起点与这段的有效终点只差浮点误差（略低或略高）：只保留上限本身。
            points = [segment_stop]
        else:
            points = _segment_points(start, segment_stop, step)
        for point in points:
            if not merged:
                merged.append(point)
                continue
            previous = merged[-1]
            if abs(point - previous) <= _boundary_tolerance(previous, point):
                # 相邻计划共享边界，总点列只保留一次。
                continue
            if point < previous:
                # 连续性校验后不应出现；保留为内部不变量，避免另一段代码改动后
                # 悄悄产出一份乱序点列。
                raise AmplifierInputError(
                    "目标输入功率点列必须按升序排列："
                    f"{point}dBm 小于前一点 {previous}dBm"
                )
            merged.append(point)
    return tuple(PowerDbm(point) for point in merged)


@dataclass(frozen=True, kw_only=True)
class AmplifierMeasurementInputs:
    """主功放测量的正式输入：频率、功率扫描、压缩目标、衰减器、DUT 限制、驱动模式与供电计划。

    除可选的驱动功放预估增益外，全部字段必填、构造即校验：主功放用例只消费本对象，
    不需要 ``Mapping[str, Any]`` 作为正式配置类型。运行标识、仪器端口、阶段记录器与
    校准输入（线损/驱动映射）属于运行契约，不在这里表达。

    ``input_power_sweep`` 表示**主功放（DUT）输入功率**的目标范围，不是信号源输出
    功率；``driver_gain_estimate`` 是驱动功放的全局预估增益（dB），驱动关闭时允许为空。

    DUT 供电由三项组成，职责互不重叠：

    - ``dut_power_plan``：**有序**物理路由与组间隔，顺序就是上电顺序；
    - ``channel_settings``：按物理路由索引的工作值/保护值，是**无序集合**；
    - ``drain_channel``：可选的漏极测量路由集合，只供效率计算读取，不参与排序或启停；
      可以配置多条，运行阶段把各自的直流功率相加；主功放测量服务在构造时另行要求
      至少配置一条。

    构造时要求两个集合完全一致：序列里的每条路由都必须有设置，设置里也不能出现
    未列入序列的通道。这是 DTO 能安全消费“已校验计划”的最低保证；归属 DUT、绑定
    与漏极必填等配置/运行规则仍由 domain 的正式校验负责。
    """

    frequencies: tuple[Frequency, ...]
    input_power_sweep: PowerSweep
    compression_target: Decibels
    attenuator: Decibels
    max_dut_input_power: PowerDbm
    driver_enabled: bool
    dut_power_plan: PowerPlan
    channel_settings: Mapping[SupplyChannel, ChannelSetting]
    drain_channel: DrainChannel
    # 驱动功放全局预估增益（dB）；驱动关闭时可以为空。是否必填由运行前置校验决定。
    driver_gain_estimate: Decibels | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "frequencies", self._frequencies(self.frequencies))
        _require_value(self.input_power_sweep, PowerSweep, "主功放输入功率扫描参数")
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
        if self.driver_gain_estimate is not None:
            _require_value(
                self.driver_gain_estimate, Decibels, "驱动功放预估增益"
            )
        plan = _require_value(self.dut_power_plan, PowerPlan, "DUT 上电计划")
        if plan.is_empty:
            raise AmplifierInputError(
                "DUT 供电通道不能为空：主功放测量必须至少有一路 DUT 供电"
            )
        settings = _channel_settings(self.channel_settings)
        sequence = plan.power_sequence
        missing = [route for route in sequence if route not in settings]
        if missing:
            names = "、".join(route.token for route in missing)
            raise AmplifierInputError(f"上电顺序中的通道缺少电压/电流设置：{names}")
        extra = [route for route in settings if route not in sequence]
        if extra:
            names = "、".join(route.token for route in extra)
            raise AmplifierInputError(f"通道参数包含未列入上电顺序的通道：{names}")
        object.__setattr__(self, "channel_settings", settings)
        drain = _require_value(self.drain_channel, DrainChannel, "漏极通道")
        # 漏极只做成员判定：配置了就必须是本次上电序列里的物理通道。是否必须配置
        # 属于主功放运行的校验（domain 规则 11），不在 DTO 里强制。
        drain.require_in_routes(sequence)

    @classmethod
    def from_run_configuration(cls, configuration: Any) -> "AmplifierMeasurementInputs":
        """从已冻结的运行配置构造正式输入（应用边界唯一转换入口）。

        只读取领域运行配置的**类型化字段**（``TestPlan`` / ``RunResourceMapping``），
        不读取历史测量字典的键名：测试方案提供频率、扫描、压缩目标、衰减器、DUT
        输入限制、通道参数与漏极，运行映射提供本机实际接线的有序 DUT 上电计划。

        非法或缺失字段统一收敛为 :class:`AmplifierInputError`，因此调用方可以在连接
        任何仪器之前失败。计划与通道参数只接受 v1.1 的两段路由对象；1.0 的
        ``dut_power_channels`` / 角色字段不会被用来推断顺序。
        """
        plan = getattr(configuration, "test_plan", None)
        mapping = getattr(configuration, "run_mapping", None)
        if plan is None or mapping is None:
            raise AmplifierInputError(
                "主功放正式输入必须来自运行配置：缺少 test_plan 或 run_mapping"
            )
        # 未确认功率范围语义的配置不能进入正式测量：旧数值可能是信号源输出功率，
        # 直接当作 DUT 输入功率会改变实际驱动电平，必须在硬件动作前失败。
        unconfirmed = power_reference_confirmation_error(plan)
        if unconfirmed is not None:
            raise AmplifierInputError(unconfirmed)
        plans = tuple(getattr(plan, "power_scan_plans", ()) or ())
        if len(plans) != 1:
            # 多段计划的运行链路属于阶段四；在这里失败比“只跑第一段”安全。
            raise AmplifierInputError(
                "主功放正式输入目前只支持单段 DUT 功率扫描计划；"
                f"当前计划为 {len(plans)} 段，多段运行链路尚未接入"
            )
        sweep = plans[0]
        try:
            return cls(
                frequencies=tuple(
                    Frequency.from_ghz(value) for value in plan.frequencies
                ),
                input_power_sweep=PowerSweep(
                    start=PowerDbm(sweep.start_power),
                    stop=PowerDbm(sweep.stop_power),
                    step=Decibels(sweep.step),
                ),
                compression_target=Decibels.parse(plan.compression_point),
                attenuator=Decibels(plan.attenuator_value),
                max_dut_input_power=PowerDbm(plan.max_input_power),
                driver_enabled=plan.driver_enabled,
                driver_gain_estimate=(
                    Decibels(plan.driver_gain_estimate_db)
                    if plan.driver_gain_estimate_db is not None
                    else None
                ),
                dut_power_plan=PowerPlan.from_document(
                    getattr(mapping, "dut_power_plan", None)
                ),
                channel_settings=_channel_settings_from_document(
                    getattr(plan, "channel_settings", None)
                ),
                drain_channel=DrainChannel.from_document(
                    getattr(plan, "drain_channel", None)
                ),
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

    def dut_supplies(self) -> Mapping[str, tuple[str, ...]]:
        """按电源分组的 DUT 通道：``{supply_id: (通道名, ...)}``，顺序按上电顺序。

        同一台电源可以同时供 DUT 的多个通道；分组是只读投影，供测量逐通道读取
        电压/电流时使用。步骤 10 已完成：效率只读 ``drain_channel``，分组顺序不再
        影响 ``dc_power``。

        该投影只是采样清单，不是路由身份：路由身份由 :class:`SupplyChannel` 表达。
        测量结果仍以 ``f"{supply_id}_{channel}"`` 作为兼容键，因此无法仅凭结果键
        还原物理路由（两段都允许下划线），选择漏极时必须比较路由对象。
        """
        grouped: dict[str, list[str]] = {}
        for item in self.dut_power_plan.power_sequence:
            grouped.setdefault(item.supply_id, []).append(item.channel)
        return MappingProxyType({name: tuple(channels) for name, channels in grouped.items()})


def _channel_settings(value: Any) -> Mapping[SupplyChannel, ChannelSetting]:
    """把构造函数收到的 ``{SupplyChannel: ChannelSetting}`` 冻结成只读映射。"""
    if not isinstance(value, Mapping):
        raise AmplifierInputError(
            "通道参数必须是 SupplyChannel 到 ChannelSetting 的映射，"
            f"收到 {type(value).__name__}"
        )
    settings: dict[SupplyChannel, ChannelSetting] = {}
    for route, setting in value.items():
        if not isinstance(route, SupplyChannel):
            raise AmplifierInputError(
                "通道参数的键必须是 SupplyChannel，"
                f"收到 {type(route).__name__}"
            )
        if not isinstance(setting, ChannelSetting):
            raise AmplifierInputError(
                f"通道 {route.token} 的参数必须是 ChannelSetting，"
                f"收到 {type(setting).__name__}"
            )
        settings[route] = setting
    return MappingProxyType(settings)


def _channel_settings_from_document(values: Any) -> Mapping[SupplyChannel, ChannelSetting]:
    """把 ``test_plan.dut.channel_settings`` 读成按路由索引的只读映射。"""
    if not isinstance(values, (list, tuple)):
        raise AmplifierInputError(
            f"通道参数（channel_settings）必须是列表，收到 {type(values).__name__}"
        )
    settings: dict[SupplyChannel, ChannelSetting] = {}
    for index, item in enumerate(values):
        if not isinstance(item, Mapping):
            raise AmplifierInputError(
                f"channel_settings[{index}] 必须是对象，收到 {type(item).__name__}"
            )
        supply_id = item.get("supply_id")
        channel = item.get("channel")
        if not isinstance(supply_id, str) or not isinstance(channel, str):
            raise AmplifierInputError(
                f"channel_settings[{index}] 必须提供 supply_id 与 channel 两段路由"
            )
        route = SupplyChannel(supply_id=supply_id, channel=channel)
        if route in settings:
            raise AmplifierInputError(
                f"channel_settings 不能重复：{route.token}（重复项见 [{index}]）"
            )
        settings[route] = ChannelSetting.from_document(item)
    return MappingProxyType(settings)

