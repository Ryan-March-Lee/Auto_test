"""配置编辑阶段的唯一数据模型（GUI 重构步骤 3）。

本模块定义“用户正在编辑的配置” :class:`ConfigurationDraft`。它既不是正式的
``RunConfiguration``（``test_plan`` + ``run_mapping``），也不是冻结运行对象
``PreparedRun``：草稿只存在于内存，不含 Qt 控件、文件路径、仪器对象或运行标识。

设计约束：

- 频率、功率扫描、压缩点、衰减器与 DUT 输入限制优先复用 domain 值对象
  （``Frequency`` / ``PowerSweep`` / ``Decibels`` / ``PowerDbm``），单位由类型表达；
- 起止功率表示**主功放输入功率**：``power_reference`` 只接受 ``None``（旧配置）或
  ``"dut_input"``，写出时统一写新语义；``driver_gain_estimate`` 是驱动功放全局预估
  增益（``Decibels | None``），缺失不折叠成 0；
- 集合字段使用 ``tuple``，设备绑定按“角色 + 名称到记录的键值对”保存受控副本，
  外部不能通过属性随意改写内部状态；
- 缺失值用 ``None`` / 空元组明确表示，非法类型与无法解析的文本由
  :class:`ConfigurationDraftResult` 的结构化问题报告；布尔开关也是三态
  （``None`` 未填写 / ``True`` / ``False``），读取时不补默认值，模型不会替用户挑一台
  默认设备，也不会把空引用补成某台电源；
- :meth:`ConfigurationDraft.from_document` / :meth:`ConfigurationDraft.to_document`
  只转换配置编辑段（测试参数、电源通道参数、组上电计划、漏极与设备绑定），
  不生成正式 ``test_plan`` / ``run_mapping``——那属于配置编写服务（步骤 4）。
  ``schema_version`` 同样由配置编写服务补齐，草稿不持有文件元数据。上电计划与漏极
  只原样读写，不推断顺序，也不按通道名猜漏极。

设备绑定的读取边界：本模块只读取当前结构 ``device_bindings.{signal_generator,
spectrum_analyzer,power_supplies}``。旧槽位绑定（``PS1`` 等）会留下警告，只有地址的
旧配置不会被当作可连接设备；两者的迁移都归设备绑定服务（步骤 5），这里不猜成某台设备。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from application.configuration_editing import ValidationIssue, ValidationResult
from application.dto.amplifier_inputs import (
    AmplifierInputError,
    INPUT_POWER_REFERENCE,
    MAX_INTER_CHANNEL_DELAY_S,
    PowerPlan,
    PowerSweep,
    SupplyChannel,
)
from domain.configuration.models import LEGACY_POWER_SCAN_FIELDS
from domain.instruments.models import INSTRUMENT_ROLES
from domain.measurement.values import Decibels, Frequency, PowerDbm

__all__ = [
    "ConfigurationDraft",
    "ConfigurationDraftResult",
    "DeviceBindingDraft",
    "DeviceBindingsDraft",
    "PowerChannelDraft",
    "PowerSupplyDraft",
]

# 文档转换问题的稳定错误码。文案可以改，错误码不随文案变化。
_CODE_INVALID_DOCUMENT = "configuration.invalid_document"
_CODE_INVALID_TYPE = "configuration.invalid_type"
_CODE_INVALID_NUMBER = "configuration.invalid_number"
_CODE_INVALID_FREQUENCY = "configuration.invalid_frequency"
_CODE_INVALID_SWEEP = "configuration.invalid_sweep"
_CODE_INVALID_DECIBELS = "configuration.invalid_decibels"
_CODE_INVALID_DUT_SUPPLY_COUNT = "configuration.invalid_dut_supply_count"
_CODE_INVALID_BINDING = "configuration.invalid_binding"
_CODE_LEGACY_BINDING = "configuration.legacy_binding"
_CODE_INVALID_ASSIGNMENT = "configuration.invalid_power_plan"

_POWER_ASSIGNMENT_GROUPS = ("dut_amplifier", "driver_amplifier")


def _text(value: Any, label: str) -> str:
    """要求字段是文本；不裁剪、不改写，空文本由校验报告而不是这里悄悄修正。"""
    if not isinstance(value, str):
        raise TypeError(f"{label} 必须是字符串，收到 {type(value).__name__}")
    return value


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _optional_number(value: Any, label: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"{label} 必须是数值或 None，收到 {type(value).__name__}"
        )
    number = float(value)
    # 直接构造与文档读取对数值有效性的要求必须一致：NaN/Infinity 不能进草稿，
    # 否则它们会一路流到测量参数里，直到别的层才被发现。
    if not math.isfinite(number):
        raise ValueError(f"{label} 必须是有限数值，收到 {number!r}")
    return number


def _optional_bool(value: Any, label: str) -> bool | None:
    """可空布尔：``None`` 表示“还没填”，不折叠成 False。"""
    if value is None:
        return None
    if not isinstance(value, bool):
        raise TypeError(
            f"{label} 必须是布尔值或 None，收到 {type(value).__name__}"
        )
    return value


def _optional_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{label} 必须是整数或 None，收到 {type(value).__name__}"
        )
    return value


def _tuple_of(values: Any, expected: type, label: str) -> tuple[Any, ...]:
    """把可迭代读数收敛为受控 tuple，并逐项检查元素类型。"""
    if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Iterable):
        raise TypeError(
            f"{label} 必须是 {expected.__name__} 序列，收到 {type(values).__name__}"
        )
    items = tuple(values)
    for index, item in enumerate(items):
        if not isinstance(item, expected):
            raise TypeError(
                f"{label}[{index}] 必须是 {expected.__name__}，"
                f"收到 {type(item).__name__}"
            )
    return items


def _text_tuple(values: Any, label: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Iterable):
        raise TypeError(f"{label} 必须是字符串序列，收到 {type(values).__name__}")
    items = tuple(values)
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise TypeError(
                f"{label}[{index}] 必须是字符串，收到 {type(item).__name__}"
            )
    return items


def _require_optional(value: Any, expected: type, label: str) -> None:
    if value is not None and not isinstance(value, expected):
        raise TypeError(
            f"{label} 必须是 {expected.__name__} 或 None，"
            f"收到 {type(value).__name__}"
        )


def _require_unique(values: Iterable[str], label: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"{label} 不能重复：{value!r}")
        seen.add(value)


def _require_unique_objects(values: Iterable[Any], label: str) -> None:
    seen: list[Any] = []
    for value in values:
        if value in seen:
            token = getattr(value, "token", value)
            raise ValueError(f"{label} 不能重复：{token!r}")
        seen.append(value)


def _binding_pairs(
    values: Any,
) -> tuple[tuple[str, "DeviceBindingDraft"], ...]:
    """把映射或二元组序列收敛成有序、去重后的绑定键值对。"""
    if isinstance(values, Mapping):
        items = tuple(values.items())
    elif isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise TypeError(
            f"电源绑定必须是映射或 (supply_id, 记录) 序列，"
            f"收到 {type(values).__name__}"
        )
    else:
        items = tuple(values)
    result: list[tuple[str, DeviceBindingDraft]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise TypeError(
                f"电源绑定[{index}] 必须是 (supply_id, 记录) 二元组，"
                f"收到 {type(item).__name__}"
            )
        supply_id, record = item
        if not isinstance(supply_id, str):
            raise TypeError(
                f"电源绑定[{index}] 的 supply_id 必须是字符串，"
                f"收到 {type(supply_id).__name__}"
            )
        if not isinstance(record, DeviceBindingDraft):
            raise TypeError(
                f"电源绑定[{index}] 的记录必须是 DeviceBindingDraft，"
                f"收到 {type(record).__name__}"
            )
        if supply_id in seen:
            raise ValueError(f"电源绑定不能重复：{supply_id!r}")
        seen.add(supply_id)
        result.append((supply_id, record))
    return tuple(result)


@dataclass(frozen=True)
class DeviceBindingDraft:
    """一条设备绑定记录（当前 ``device_bindings`` 结构里的记录本身）。

    身份是 ``device_key``（``厂商:完整设备码``）；``visa_address`` 只是连接地址，
    不是身份。字段全部可选，缺失写成 ``None``，不在读取时按型号或地址猜设备。
    ``supply_id`` 不在记录内重复：电源绑定的键就是 ``supply_id``。
    """

    vendor: str | None = None
    model: str | None = None
    label: str | None = None
    full_code: str | None = None
    device_key: str | None = None
    visa_address: str | None = None
    last_ip: str | None = None
    last_port: int | str | None = None
    known: bool = False
    source: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "vendor",
            "model",
            "label",
            "full_code",
            "device_key",
            "visa_address",
            "last_ip",
        ):
            object.__setattr__(self, name, _optional_text(getattr(self, name), name))
        port = self.last_port
        if port is not None and (
            isinstance(port, bool) or not isinstance(port, (int, str))
        ):
            raise TypeError(
                f"last_port 必须是整数、字符串或 None，收到 {type(port).__name__}"
            )
        if not isinstance(self.known, bool):
            raise TypeError(
                f"known 必须是布尔值，收到 {type(self.known).__name__}"
            )
        object.__setattr__(self, "source", _optional_text(self.source, "source"))

    def to_mapping(self) -> dict[str, Any]:
        """输出当前绑定记录形状；缺失字段保留为 ``None``，不删除键。"""
        return {
            "vendor": self.vendor,
            "model": self.model,
            "label": self.label,
            "full_code": self.full_code,
            "device_key": self.device_key,
            "visa_address": self.visa_address,
            "last_ip": self.last_ip,
            "last_port": self.last_port,
            "known": self.known,
            "source": self.source,
        }


@dataclass(frozen=True)
class DeviceBindingsDraft:
    """配置编辑阶段的一组设备绑定：两个唯一角色 + 按 ``supply_id`` 的电源集合。"""

    signal_generator: DeviceBindingDraft | None = None
    spectrum_analyzer: DeviceBindingDraft | None = None
    power_supply_bindings: tuple[tuple[str, DeviceBindingDraft], ...] = field(
        default_factory=tuple
    )

    def __post_init__(self) -> None:
        for name in ("signal_generator", "spectrum_analyzer"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, DeviceBindingDraft):
                raise TypeError(
                    f"{name} 必须是 DeviceBindingDraft 或 None，"
                    f"收到 {type(value).__name__}"
                )
        object.__setattr__(
            self, "power_supply_bindings", _binding_pairs(self.power_supply_bindings)
        )

    @property
    def power_supplies(self) -> Mapping[str, DeviceBindingDraft]:
        """``supply_id -> 绑定记录`` 的只读投影；每次返回新的只读视图。"""
        return MappingProxyType(dict(self.power_supply_bindings))

    def power_supply(self, supply_id: str) -> DeviceBindingDraft | None:
        for key, record in self.power_supply_bindings:
            if key == supply_id:
                return record
        return None

    def is_empty(self) -> bool:
        return (
            self.signal_generator is None
            and self.spectrum_analyzer is None
            and not self.power_supply_bindings
        )

    def to_mapping(self) -> dict[str, Any]:
        """输出当前 ``device_bindings`` 结构；没有电源绑定时不写空集合。"""
        result: dict[str, Any] = {}
        if self.signal_generator is not None:
            result["signal_generator"] = self.signal_generator.to_mapping()
        if self.spectrum_analyzer is not None:
            result["spectrum_analyzer"] = self.spectrum_analyzer.to_mapping()
        if self.power_supply_bindings:
            result["power_supplies"] = {
                supply_id: record.to_mapping()
                for supply_id, record in self.power_supply_bindings
            }
        return result


@dataclass(frozen=True)
class PowerChannelDraft:
    """一台电源的单个通道参数。

    通道身份只有通道名；供电角色（``gate`` / ``drain``）不再属于草稿，旧文档里的
    ``role`` 在读入时忽略、写出时不输出。漏极是独立的路由引用，后续加入上电计划
    模型，不在这里按通道名推断。电压/电流的工作值与保护值缺失时保持 ``None``，
    不填入显示初值。保护开关同样区分“未填写”（``None``）与“明确关闭”（``False``），
    不在读取时补默认值。
    """

    channel: str
    voltage: float | None = None
    voltage_protection: float | None = None
    voltage_protection_enabled: bool | None = None
    current: float | None = None
    current_protection: float | None = None
    current_protection_enabled: bool | None = None
    #: 本通道开启后到下一条上电命令的等待秒数；``None`` 表示文档没有写。
    power_on_delay_s: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "channel", _text(self.channel, "通道名"))
        for name in (
            "voltage",
            "voltage_protection",
            "current",
            "current_protection",
            "power_on_delay_s",
        ):
            object.__setattr__(self, name, _optional_number(getattr(self, name), name))
        for name in ("voltage_protection_enabled", "current_protection_enabled"):
            object.__setattr__(
                self, name, _optional_bool(getattr(self, name), name)
            )
        delay = self.power_on_delay_s
        if delay is not None and (delay < 0 or delay > MAX_INTER_CHANNEL_DELAY_S):
            raise ValueError(
                f"上电间隔必须在 0 到 {MAX_INTER_CHANNEL_DELAY_S:g} 秒之间，"
                f"收到 {delay}s"
            )


@dataclass(frozen=True)
class PowerSupplyDraft:
    """一台电源在草稿里的参数集合；连接地址与身份来自设备绑定，不在这里重复。"""

    supply_id: str
    channels: tuple[PowerChannelDraft, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "supply_id", _text(self.supply_id, "电源引用"))
        object.__setattr__(
            self, "channels", _tuple_of(self.channels, PowerChannelDraft, "电源通道")
        )
        _require_unique(
            (channel.channel for channel in self.channels), "电源的通道名"
        )

    def channel(self, name: str) -> PowerChannelDraft | None:
        for channel in self.channels:
            if channel.channel == name:
                return channel
        return None


@dataclass(frozen=True)
class ConfigurationDraft:
    """用户正在编辑的配置；配置页状态的唯一业务数据模型。"""

    frequencies: tuple[Frequency, ...] = ()
    # 单段兼容属性：只给一个扫描段时与 ``sweeps`` 同值；多段计划下保持 ``None``，
    # 调用方必须改读 ``sweeps``。新代码只写 ``sweeps``。
    sweep: PowerSweep | None = None
    # 1.2 的功率扫描计划集合：顺序即执行顺序；空元组表示文档没有计划。
    sweeps: tuple[PowerSweep, ...] = ()
    # 起止功率的语义标记；None 表示来源文档没有声明（旧配置），写回时固定写新语义。
    power_reference: str | None = None
    compression_target: Decibels | None = None
    attenuator: Decibels | None = None
    max_dut_input_power: PowerDbm | None = None
    driver_enabled: bool | None = None
    # 驱动功放全局预估增益（dB）；None 表示尚未填写，不折叠成 0。
    driver_gain_estimate: Decibels | None = None
    # 测试方案现场参数（页面下拉框），不驱动电源分配；缺失时写出阶段按 DUT 计划路由数回退。
    dut_supply_count: int | None = None
    # 漏极只供效率计算读取，不参与排序/间隔/启停；空元组表示未配置。
    # 可以有多条：运行阶段把每条漏极的直流功率相加。
    drain_channels: tuple[SupplyChannel, ...] = ()
    # 组上电计划：None 表示来源文档没有该键；写出时按空计划规范形状补齐。
    # 计划同时是“哪些电源的哪些通道给哪个器件上电”的唯一来源，不再另存固定分配表。
    dut_power_plan: PowerPlan | None = None
    driver_power_plan: PowerPlan | None = None
    power_supplies: tuple[PowerSupplyDraft, ...] = ()
    bindings: DeviceBindingsDraft = field(default_factory=DeviceBindingsDraft)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frequencies", _tuple_of(self.frequencies, Frequency, "测试频率")
        )
        _require_optional(self.sweep, PowerSweep, "功率扫描参数")
        sweeps = _tuple_of(self.sweeps, PowerSweep, "功率扫描计划")
        if sweeps and self.sweep is not None and sweeps != (self.sweep,):
            raise ValueError("sweep 与 sweeps 不能同时提供：功率扫描只有 sweeps 一个来源")
        if not sweeps and self.sweep is not None:
            sweeps = (self.sweep,)
        object.__setattr__(self, "sweeps", sweeps)
        if self.sweep is None and len(sweeps) == 1:
            object.__setattr__(self, "sweep", sweeps[0])
        if self.power_reference is not None:
            if not isinstance(self.power_reference, str):
                raise TypeError(
                    f"功率范围语义标记必须是字符串或 None，"
                    f"收到 {type(self.power_reference).__name__}"
                )
            if self.power_reference != INPUT_POWER_REFERENCE:
                raise ValueError(
                    f"功率范围语义标记必须是 {INPUT_POWER_REFERENCE!r}，"
                    f"收到 {self.power_reference!r}"
                )
        _require_optional(self.compression_target, Decibels, "压缩点")
        _require_optional(self.attenuator, Decibels, "衰减器")
        _require_optional(self.max_dut_input_power, PowerDbm, "DUT 最大输入功率")
        object.__setattr__(
            self, "driver_enabled", _optional_bool(self.driver_enabled, "驱放模式开关")
        )
        _require_optional(
            self.driver_gain_estimate, Decibels, "驱动功放预估增益"
        )
        # 数量只做结构校验：页面提供 1–3 的下拉框，但旧配置可能记录更多电源，
        # 草稿不在这里复制范围规则，也不把旧值改写成页面默认值。
        object.__setattr__(
            self,
            "dut_supply_count",
            _optional_int(self.dut_supply_count, "PA 单元数量"),
        )
        drain_channels = _tuple_of(self.drain_channels, SupplyChannel, "漏极通道")
        object.__setattr__(self, "drain_channels", drain_channels)
        _require_unique_objects(drain_channels, "漏极通道")
        _require_optional(self.dut_power_plan, PowerPlan, "DUT 上电计划")
        _require_optional(self.driver_power_plan, PowerPlan, "驱动功放上电计划")
        object.__setattr__(
            self,
            "power_supplies",
            _tuple_of(self.power_supplies, PowerSupplyDraft, "电源参数"),
        )
        _require_unique(
            (supply.supply_id for supply in self.power_supplies),
            "电源引用",
        )
        if not isinstance(self.bindings, DeviceBindingsDraft):
            raise TypeError(
                f"bindings 必须是 DeviceBindingsDraft，"
                f"收到 {type(self.bindings).__name__}"
            )

    @property
    def dut_channels(self) -> tuple[SupplyChannel, ...]:
        """DUT 上电计划里的物理路由（计划是唯一来源）。"""
        return (
            tuple(self.dut_power_plan.power_sequence)
            if self.dut_power_plan is not None
            else ()
        )

    @property
    def driver_channels(self) -> tuple[SupplyChannel, ...]:
        """驱动功放上电计划里的物理路由。"""
        return (
            tuple(self.driver_power_plan.power_sequence)
            if self.driver_power_plan is not None
            else ()
        )

    @classmethod
    def from_document(
        cls,
        document: Mapping[str, Any] | None,
        *,
        bindings: DeviceBindingsDraft | Mapping[str, Any] | None = None,
    ) -> "ConfigurationDraftResult":
        """把配置编写文档转换成草稿，并用结构化问题报告无法表达的内容。

        ``bindings`` 允许调用方传入**已经规范化**的设备绑定（例如 presentation 适配器
        从 ``effective_device_bindings`` 得到的结构）。不传时只读取文档里当前的
        ``device_bindings`` 结构；旧槽位折叠与只有地址的旧绑定由设备绑定服务负责
        （步骤 5），这里不会猜成某台设备。

        缺失字段返回 ``None`` / 空元组，不产生问题；**出现但类型或语法非法**的字段
        产生错误级别的问题，正文仍保持缺失状态，方便页面展示并让用户修正。布尔字段
        （``driver_mode.enabled``、通道的 ``protection_enabled``）缺失时同样是 ``None``，
        不会折叠成“明确关闭”。
        """
        issues: list[ValidationIssue] = []
        if document is None:
            document = {}
        if not isinstance(document, Mapping):
            issues.append(
                _issue("$", _CODE_INVALID_DOCUMENT, "配置文档必须是对象")
            )
            document = {}

        frequencies = _read_frequencies(document.get("test_frequencies"), issues)
        sweeps = _read_sweeps(document.get("signal_source"), issues)
        power_reference, reference_valid = _read_power_reference(
            document.get("signal_source"), issues
        )
        if not reference_valid:
            # 声明了不认识的功率语义时不能保留扫描参数：那会把旧数值静默当成新语义。
            sweeps = ()
        compression_target = _read_decibels(
            document.get("compression_point"), "compression_point.type", issues
        )
        attenuator = _read_decibels(
            document.get("attenuator"), "attenuator.type", issues
        )
        max_dut_input_power = _read_max_input_power(document.get("dut_config"), issues)
        dut_supply_count = _read_dut_supply_count(document.get("dut_config"), issues)
        drain_channels = _read_drain_channels(document.get("dut_config"), issues)
        driver_enabled = _read_driver_mode(document.get("driver_mode"), issues)
        driver_gain_estimate = _read_driver_gain(
            document.get("driver_mode"), issues
        )
        power_supplies = _read_power_supplies(document.get("instruments"), issues)
        assignments = document.get("power_supply_assignment")
        dut_power_plan = _read_power_plan(assignments, "dut_amplifier", issues)
        driver_power_plan = _read_power_plan(assignments, "driver_amplifier", issues)
        device_bindings = _read_device_bindings(document, bindings, issues)
        unique_issues = _unique_issues(issues)

        draft = cls(
            frequencies=frequencies,
            sweeps=sweeps,
            power_reference=power_reference,
            compression_target=compression_target,
            attenuator=attenuator,
            max_dut_input_power=max_dut_input_power,
            driver_enabled=driver_enabled,
            driver_gain_estimate=driver_gain_estimate,
            dut_supply_count=dut_supply_count,
            drain_channels=drain_channels,
            dut_power_plan=dut_power_plan,
            driver_power_plan=driver_power_plan,
            power_supplies=power_supplies,
            bindings=device_bindings,
        )
        return ConfigurationDraftResult(
            draft=draft, validation=ValidationResult(issues=unique_issues)
        )

    def to_document(self) -> dict[str, Any]:
        """输出配置编辑段的编写文档，键形状与当前配置格式一致。

        不写 ``schema_version``、``test_plan``、``run_mapping`` 与 ``wiring``：前者和
        正式段由配置编写服务补齐，现场接线由阶段开始前的连接确认负责。缺失值写成
        ``None`` / 空集合，让后续校验明确报告，而不是在这里补默认值。
        """
        count = (
            self.dut_supply_count
            if self.dut_supply_count is not None
            else len(self.dut_channels)
        )
        # ``power_supply_count`` 只是测试方案现场参数：固定分配表删除后没有“PA 单元槽位”
        # 可数，缺失时按 DUT 计划路由数回退，仅供页面下拉框显示，不参与校验或正式 schema。
        dut_config = {
            "max_input_power": (
                self.max_dut_input_power.dbm
                if self.max_dut_input_power is not None
                else None
            ),
            "power_supply_count": count,
        }
        if self.drain_channels:
            documents = [route.to_document() for route in self.drain_channels]
            # 单漏极继续写旧文档的单条对象；多漏极才写列表。
            dut_config["drain_channel"] = (
                documents[0] if len(documents) == 1 else documents
            )
        return {
            "test_frequencies": [frequency.ghz for frequency in self.frequencies],
            "signal_source": _sweep_document(self.sweeps, self.power_reference),
            "compression_point": {
                "type": _decibels_text(self.compression_target)
            },
            "attenuator": {"type": _decibels_text(self.attenuator)},
            "driver_mode": _driver_mode_document(self),
            "dut_config": dut_config,
            "instruments": self._instrument_document(),
            "power_supply_assignment": self._assignment_document(),
            "device_bindings": self.bindings.to_mapping(),
        }

    def _instrument_document(self) -> dict[str, Any]:
        supply_ids: list[str] = []
        for supply in self.power_supplies:
            if supply.supply_id not in supply_ids:
                supply_ids.append(supply.supply_id)
        for supply_id, _record in self.bindings.power_supply_bindings:
            if supply_id not in supply_ids:
                supply_ids.append(supply_id)
        supplies_by_id = {supply.supply_id: supply for supply in self.power_supplies}
        return {
            "signal_generator": _instrument_entry(self.bindings.signal_generator),
            "spectrum_analyzer": _instrument_entry(self.bindings.spectrum_analyzer),
            "power_supplies": {
                supply_id: _power_supply_entry(
                    supply_id,
                    self.bindings.power_supply(supply_id),
                    supplies_by_id.get(supply_id),
                )
                for supply_id in supply_ids
            },
        }

    def _assignment_document(self) -> dict[str, Any]:
        driver: dict[str, Any] = {}
        dut: dict[str, Any] = {}
        # 缺失的计划保持缺失：不把“还没有排序”伪装成一份空计划写回文档。
        if self.driver_power_plan is not None:
            driver["power_plan"] = self.driver_power_plan.to_document()
        if self.dut_power_plan is not None:
            dut["power_plan"] = self.dut_power_plan.to_document()
        return {"driver_amplifier": driver, "dut_amplifier": dut}


@dataclass(frozen=True)
class ConfigurationDraftResult:
    """草稿转换结果：草稿本身 + 转换阶段发现的结构化问题。

    ``valid`` 只表示**转换**没有错误（例如没有非法类型），不代表配置已完整或可以运行；
    频率重复、供电角色冲突等业务校验由后续的配置编写/校验服务负责。
    """

    draft: ConfigurationDraft
    validation: ValidationResult = field(default_factory=ValidationResult)

    def __post_init__(self) -> None:
        if not isinstance(self.draft, ConfigurationDraft):
            raise TypeError(
                f"draft 必须是 ConfigurationDraft，收到 {type(self.draft).__name__}"
            )
        if not isinstance(self.validation, ValidationResult):
            raise TypeError(
                f"validation 必须是 ValidationResult，"
                f"收到 {type(self.validation).__name__}"
            )

    @property
    def valid(self) -> bool:
        """转换阶段没有错误；警告不改变结果。"""
        return self.validation.valid

    @property
    def issues(self) -> tuple[ValidationIssue, ...]:
        return self.validation.issues

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        return self.validation.errors

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        return self.validation.warnings

    def messages(self) -> tuple[str, ...]:
        return self.validation.messages()


def _issue(
    path: str,
    code: str,
    message: str,
    severity: str = "error",
) -> ValidationIssue:
    return ValidationIssue(
        path=path, code=code, message=message, severity=severity
    )


def _unique_issues(
    issues: Iterable[ValidationIssue],
) -> tuple[ValidationIssue, ...]:
    """按出现顺序去重：同一字段被多个读取分支检查时不重复展示同一句话。"""
    result: list[ValidationIssue] = []
    seen: set[tuple[str, str, str, str]] = set()
    for issue in issues:
        key = (issue.path, issue.code, issue.message, issue.severity)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return tuple(result)


def _mapping(value: Any, path: str, issues: list[ValidationIssue]) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if value is not None:
        issues.append(_issue(path, _CODE_INVALID_TYPE, "必须是对象"))
    return {}


def _finite_number(
    value: Any, path: str, issues: list[ValidationIssue]
) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        issues.append(_issue(path, _CODE_INVALID_NUMBER, "必须是有限数值"))
        return None
    number = float(value)
    if not math.isfinite(number):
        issues.append(_issue(path, _CODE_INVALID_NUMBER, "必须是有限数值"))
        return None
    return number


def _optional_text_field(
    value: Any, path: str, issues: list[ValidationIssue]
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        issues.append(_issue(path, _CODE_INVALID_TYPE, "必须是字符串"))
        return None
    return value


def _optional_bool_field(
    value: Any, path: str, issues: list[ValidationIssue], *, default: bool | None = None
) -> bool | None:
    """读取可空布尔；``None`` 表示文档没有给出，非布尔值报问题并保持缺失。"""
    if value is None:
        return default
    if not isinstance(value, bool):
        issues.append(_issue(path, _CODE_INVALID_TYPE, "必须是布尔值"))
        return default
    return value


def _read_frequencies(
    value: Any, issues: list[ValidationIssue]
) -> tuple[Frequency, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        issues.append(
            _issue("test_frequencies", _CODE_INVALID_TYPE, "必须是频率列表")
        )
        return ()
    frequencies: list[Frequency] = []
    for index, item in enumerate(value):
        path = f"test_frequencies[{index}]"
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            issues.append(
                _issue(path, _CODE_INVALID_FREQUENCY, "必须是有限数值（GHz）")
            )
            continue
        number = float(item)
        if not math.isfinite(number):
            issues.append(
                _issue(path, _CODE_INVALID_FREQUENCY, "必须是有限数值（GHz）")
            )
            continue
        try:
            frequencies.append(Frequency.from_ghz(number))
        except (TypeError, ValueError) as error:
            issues.append(
                _issue(path, _CODE_INVALID_FREQUENCY, f"无法解析频率：{error}")
            )
    return tuple(frequencies)


def _read_sweeps(
    value: Any, issues: list[ValidationIssue]
) -> tuple[PowerSweep, ...]:
    """读取 ``signal_source`` 的功率扫描：1.2 计划列表或 1.1 单段标量。

    1.2 的 ``power_scan_plans`` 是唯一来源；旧文件的 ``start_power`` /
    ``stop_power`` / ``step`` 只在读取边界包装成一段计划，随后草稿保存为新列表。
    两者同时出现说明文件存在两个来源，直接报结构冲突，不选择其中一个继续。
    列表元素保持原始顺序；非法元素按索引报告并跳过，剩下的合法段仍可编辑。
    """
    if value is None:
        return ()
    section = _mapping(value, "signal_source", issues)
    if not section:
        return ()
    plans = section.get("power_scan_plans")
    scalar_keys = [key for key in LEGACY_POWER_SCAN_FIELDS if key in section]
    if plans is not None:
        if scalar_keys:
            issues.append(
                _issue(
                    "signal_source",
                    _CODE_INVALID_SWEEP,
                    "power_scan_plans 与 start_power/stop_power/step 不能同时出现："
                    "功率扫描只能有一个来源",
                )
            )
            return ()
        if not isinstance(plans, (list, tuple)):
            issues.append(
                _issue(
                    "signal_source.power_scan_plans",
                    _CODE_INVALID_TYPE,
                    "必须是功率扫描计划列表",
                )
            )
            return ()
        if not plans:
            issues.append(
                _issue(
                    "signal_source.power_scan_plans",
                    _CODE_INVALID_SWEEP,
                    "至少需要一段功率扫描计划",
                )
            )
            return ()
        sweeps: list[PowerSweep] = []
        for index, item in enumerate(plans):
            path = f"signal_source.power_scan_plans[{index}]"
            if not isinstance(item, Mapping):
                issues.append(
                    _issue(
                        path,
                        _CODE_INVALID_TYPE,
                        "功率扫描计划必须是 {start_power, step, stop_power} 对象",
                    )
                )
                continue
            start = _finite_number(item.get("start_power"), f"{path}.start_power", issues)
            step = _finite_number(item.get("step"), f"{path}.step", issues)
            stop = _finite_number(item.get("stop_power"), f"{path}.stop_power", issues)
            if start is None or step is None or stop is None:
                continue
            try:
                sweeps.append(
                    PowerSweep(
                        start=PowerDbm(start), stop=PowerDbm(stop), step=Decibels(step)
                    )
                )
            except (TypeError, ValueError) as error:
                issues.append(_issue(path, _CODE_INVALID_SWEEP, str(error)))
        return tuple(sweeps)
    if not scalar_keys:
        return ()
    start = _finite_number(section.get("start_power"), "signal_source.start_power", issues)
    stop = _finite_number(section.get("stop_power"), "signal_source.stop_power", issues)
    step = _finite_number(section.get("step"), "signal_source.step", issues)
    if start is None or stop is None or step is None:
        return ()
    try:
        return (
            PowerSweep(
                start=PowerDbm(start), stop=PowerDbm(stop), step=Decibels(step)
            ),
        )
    except (TypeError, ValueError) as error:
        issues.append(_issue("signal_source", _CODE_INVALID_SWEEP, str(error)))
        return ()


def _read_power_reference(
    value: Any, issues: list[ValidationIssue]
) -> tuple[str | None, bool]:
    """读取起止功率的语义标记，返回 ``(标记, 是否合法)``。

    缺失表示旧配置（标记为 ``None``，视为可继续读取）；出现但无法识别时返回
    ``(None, False)``，调用方必须丢弃扫描参数，不能把历史数值按新语义重写。
    """
    if value is None:
        return None, True
    section = _mapping(value, "signal_source", issues)
    raw = section.get("power_reference")
    if raw is None:
        return None, True
    if not isinstance(raw, str):
        issues.append(
            _issue(
                "signal_source.power_reference",
                _CODE_INVALID_TYPE,
                "功率范围语义标记必须是字符串",
            )
        )
        return None, False
    if raw != INPUT_POWER_REFERENCE:
        issues.append(
            _issue(
                "signal_source.power_reference",
                _CODE_INVALID_SWEEP,
                f"功率范围语义必须是 {INPUT_POWER_REFERENCE!r}（主功放输入功率），"
                f"收到 {raw!r}",
            )
        )
        return None, False
    return raw, True


def _read_driver_gain(
    value: Any, issues: list[ValidationIssue]
) -> Decibels | None:
    """读取 ``driver_mode.estimated_gain_db``；缺失保持 None，非法值报问题。"""
    if value is None:
        return None
    section = _mapping(value, "driver_mode", issues)
    if "estimated_gain_db" not in section:
        return None
    number = _finite_number(
        section.get("estimated_gain_db"),
        "driver_mode.estimated_gain_db",
        issues,
    )
    if number is None:
        return None
    try:
        return Decibels(number)
    except (TypeError, ValueError) as error:
        issues.append(
            _issue(
                "driver_mode.estimated_gain_db",
                _CODE_INVALID_NUMBER,
                str(error),
            )
        )
        return None


def _read_decibels(
    value: Any, path: str, issues: list[ValidationIssue]
) -> Decibels | None:
    if value is None:
        return None
    section = _mapping(value, path.rsplit(".", 1)[0], issues)
    raw = section.get("type")
    if raw is None:
        return None
    if not isinstance(raw, str):
        issues.append(_issue(path, _CODE_INVALID_TYPE, "必须是带 dB 单位的字符串"))
        return None
    try:
        return Decibels.parse(raw)
    except ValueError as error:
        issues.append(_issue(path, _CODE_INVALID_DECIBELS, str(error)))
        return None


def _read_max_input_power(
    value: Any, issues: list[ValidationIssue]
) -> PowerDbm | None:
    if value is None:
        return None
    section = _mapping(value, "dut_config", issues)
    if not section:
        return None
    number = _finite_number(
        section.get("max_input_power"), "dut_config.max_input_power", issues
    )
    if number is None:
        return None
    try:
        return PowerDbm(number)
    except (TypeError, ValueError) as error:
        issues.append(
            _issue("dut_config.max_input_power", _CODE_INVALID_NUMBER, str(error))
        )
        return None


def _read_dut_supply_count(
    value: Any, issues: list[ValidationIssue]
) -> int | None:
    if value is None:
        return None
    section = _mapping(value, "dut_config", issues)
    if not section:
        return None
    raw = section.get("power_supply_count")
    if raw is None:
        return None
    # 只接受整数：``5.0`` 或 ``"5"`` 说明写入方的类型已经错了，草稿不能顺手把它
    # 修成看起来合法的值。范围规则（页面的 1–3）仍归正式校验，不在这里复制。
    if isinstance(raw, bool) or not isinstance(raw, int):
        issues.append(
            _issue(
                "dut_config.power_supply_count",
                _CODE_INVALID_DUT_SUPPLY_COUNT,
                "PA 单元数量必须是整数",
            )
        )
        return None
    return raw


def _read_drain_channels(
    value: Any, issues: list[ValidationIssue]
) -> tuple[SupplyChannel, ...]:
    """读取 ``dut_config.drain_channel``；缺失、``null`` 与空列表都表示未配置。

    文档兼容单条两段路由与路由列表两种形状；多漏极按书写顺序保存，运行阶段把
    各自直流功率相加。任何一项形状非法都记录问题并跳过该项，不让整份草稿失败。
    """
    if value is None:
        return ()
    section = _mapping(value, "dut_config", issues)
    if "drain_channel" not in section:
        return ()
    raw = section.get("drain_channel")
    if raw is None:
        return ()
    if isinstance(raw, Mapping):
        items = ((raw, "dut_config.drain_channel"),)
    elif isinstance(raw, (list, tuple)):
        items = tuple(
            (item, f"dut_config.drain_channel[{index}]")
            for index, item in enumerate(raw)
        )
    else:
        issues.append(
            _issue(
                "dut_config.drain_channel",
                _CODE_INVALID_ASSIGNMENT,
                "漏极通道必须是 {supply_id, channel} 对象或对象列表",
            )
        )
        return ()
    routes: list[SupplyChannel] = []
    for item, path in items:
        try:
            route = SupplyChannel.from_document(item)
        except (AmplifierInputError, TypeError, ValueError) as error:
            issues.append(_issue(path, _CODE_INVALID_ASSIGNMENT, str(error)))
            continue
        if route in routes:
            issues.append(
                _issue(path, _CODE_INVALID_ASSIGNMENT, f"漏极通道不能重复：{route.token}")
            )
            continue
        routes.append(route)
    return tuple(routes)


def _read_power_plan(
    assignments: Any, group: str, issues: list[ValidationIssue]
) -> PowerPlan | None:
    """读取一组的 ``power_plan``；键缺失时返回 ``None``（不推断顺序）。"""
    section = _mapping(assignments, "power_supply_assignment", issues)
    group_section = _mapping(section.get(group), f"power_supply_assignment.{group}", issues)
    if "power_plan" not in group_section:
        return None
    path = f"power_supply_assignment.{group}.power_plan"
    raw = group_section.get("power_plan")
    if not isinstance(raw, Mapping):
        issues.append(_issue(path, _CODE_INVALID_ASSIGNMENT, "上电计划必须是对象"))
        return None
    missing = [
        key for key in ("power_sequence", "inter_channel_delay_s") if key not in raw
    ]
    if missing:
        for key in missing:
            issues.append(
                _issue(f"{path}.{key}", _CODE_INVALID_ASSIGNMENT, "缺少必需字段")
            )
        return None
    try:
        return PowerPlan.from_document(raw)
    except (AmplifierInputError, TypeError, ValueError) as error:
        issues.append(_issue(path, _CODE_INVALID_ASSIGNMENT, str(error)))
        return None


def _read_driver_mode(value: Any, issues: list[ValidationIssue]) -> bool | None:
    if value is None:
        return None
    section = _mapping(value, "driver_mode", issues)
    if not section:
        return None
    return _optional_bool_field(
        section.get("enabled"), "driver_mode.enabled", issues
    )


def _read_power_supplies(
    value: Any, issues: list[ValidationIssue]
) -> tuple[PowerSupplyDraft, ...]:
    if value is None:
        return ()
    instruments = _mapping(value, "instruments", issues)
    raw_supplies = instruments.get("power_supplies")
    if raw_supplies is None:
        return ()
    if not isinstance(raw_supplies, Mapping):
        issues.append(
            _issue("instruments.power_supplies", _CODE_INVALID_TYPE, "必须是对象")
        )
        return ()
    supplies: list[PowerSupplyDraft] = []
    for supply_id, entry in raw_supplies.items():
        path = f"instruments.power_supplies.{supply_id}"
        if not isinstance(supply_id, str):
            issues.append(
                _issue(path, _CODE_INVALID_TYPE, "电源引用必须是字符串")
            )
            continue
        supply = _mapping(entry, path, issues)
        channels = _read_channels(supply.get("channels"), f"{path}.channels", issues)
        supplies.append(PowerSupplyDraft(supply_id=supply_id, channels=channels))
    return tuple(supplies)


def _read_channels(
    value: Any, path: str, issues: list[ValidationIssue]
) -> tuple[PowerChannelDraft, ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        issues.append(_issue(path, _CODE_INVALID_TYPE, "必须是对象"))
        return ()
    channels: list[PowerChannelDraft] = []
    for name, entry in value.items():
        channel_path = f"{path}.{name}"
        if not isinstance(name, str):
            issues.append(
                _issue(channel_path, _CODE_INVALID_TYPE, "通道名必须是字符串")
            )
            continue
        channel = _mapping(entry, channel_path, issues)
        # 旧文档可能带 ``role``：它已不参与通道身份、映射或上电，读取时直接忽略，
        # 不报问题、也不保留到草稿里；写出时由 ``_channel_document`` 不再输出。
        voltage_value, voltage_protection, voltage_enabled = _read_channel_setting(
            channel.get("voltage"), f"{channel_path}.voltage", issues
        )
        current_value, current_protection, current_enabled = _read_channel_setting(
            channel.get("current"), f"{channel_path}.current", issues
        )
        channels.append(
            PowerChannelDraft(
                channel=name,
                voltage=voltage_value,
                voltage_protection=voltage_protection,
                voltage_protection_enabled=voltage_enabled,
                current=current_value,
                current_protection=current_protection,
                current_protection_enabled=current_enabled,
                power_on_delay_s=_read_channel_delay(
                    channel.get("power_on_delay_s"),
                    f"{channel_path}.power_on_delay_s",
                    issues,
                ),
            )
        )
    return tuple(channels)


def _read_channel_delay(
    value: Any, path: str, issues: list[ValidationIssue]
) -> float | None:
    """读取通道上电间隔；缺失保持 ``None``，非法值记问题后按缺失处理。"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        issues.append(_issue(path, _CODE_INVALID_NUMBER, "必须是有限数值（秒）"))
        return None
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0 or seconds > MAX_INTER_CHANNEL_DELAY_S:
        issues.append(
            _issue(
                path,
                _CODE_INVALID_NUMBER,
                f"必须在 0 到 {MAX_INTER_CHANNEL_DELAY_S:g} 秒之间",
            )
        )
        return None
    return seconds


def _read_channel_setting(
    value: Any, path: str, issues: list[ValidationIssue]
) -> tuple[float | None, float | None, bool | None]:
    if value is None:
        return None, None, None
    section = _mapping(value, path, issues)
    if not section:
        return None, None, None
    work = _finite_number(section.get("value"), f"{path}.value", issues)
    protection = _finite_number(
        section.get("protection"), f"{path}.protection", issues
    )
    enabled = _optional_bool_field(
        section.get("protection_enabled"), f"{path}.protection_enabled", issues
    )
    return work, protection, enabled


def _read_device_bindings(
    document: Mapping[str, Any],
    bindings: DeviceBindingsDraft | Mapping[str, Any] | None,
    issues: list[ValidationIssue],
) -> DeviceBindingsDraft:
    if bindings is not None:
        if isinstance(bindings, DeviceBindingsDraft):
            return bindings
        if isinstance(bindings, Mapping):
            return _bindings_from_mapping(bindings, "device_bindings", issues)
        issues.append(
            _issue(
                "device_bindings",
                _CODE_INVALID_TYPE,
                "设备绑定必须是 DeviceBindingsDraft 或对象",
            )
        )
        return DeviceBindingsDraft()
    value = document.get("device_bindings")
    if value is None:
        return DeviceBindingsDraft()
    if not isinstance(value, Mapping):
        issues.append(_issue("device_bindings", _CODE_INVALID_TYPE, "必须是对象"))
        return DeviceBindingsDraft()
    return _bindings_from_mapping(value, "device_bindings", issues)


def _bindings_from_mapping(
    value: Mapping[str, Any], path: str, issues: list[ValidationIssue]
) -> DeviceBindingsDraft:
    signal_generator = _read_binding(
        value.get("signal_generator"), f"{path}.signal_generator", issues
    )
    spectrum_analyzer = _read_binding(
        value.get("spectrum_analyzer"), f"{path}.spectrum_analyzer", issues
    )
    power_pairs: list[tuple[str, DeviceBindingDraft]] = []
    raw_power = value.get("power_supplies")
    if raw_power is not None:
        if not isinstance(raw_power, Mapping):
            issues.append(
                _issue(f"{path}.power_supplies", _CODE_INVALID_TYPE, "必须是对象")
            )
        else:
            for supply_id, record in raw_power.items():
                record_path = f"{path}.power_supplies.{supply_id}"
                if not isinstance(supply_id, str):
                    issues.append(
                        _issue(
                            record_path,
                            _CODE_INVALID_BINDING,
                            "电源绑定键必须是字符串",
                        )
                    )
                    continue
                parsed = _read_binding(record, record_path, issues)
                if parsed is not None:
                    power_pairs.append((supply_id, parsed))
    for key in value:
        if key in INSTRUMENT_ROLES or key == "power_supplies":
            continue
        issues.append(
            _issue(
                f"{path}.{key}",
                _CODE_LEGACY_BINDING,
                "草稿只读取当前设备绑定结构；旧槽位绑定需由设备绑定服务迁移",
                severity="warning",
            )
        )
    return DeviceBindingsDraft(
        signal_generator=signal_generator,
        spectrum_analyzer=spectrum_analyzer,
        power_supply_bindings=tuple(power_pairs),
    )


def _read_binding(
    value: Any, path: str, issues: list[ValidationIssue]
) -> DeviceBindingDraft | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        issues.append(_issue(path, _CODE_INVALID_BINDING, "必须是绑定记录对象"))
        return None
    fields = {
        name: _optional_text_field(value.get(name), f"{path}.{name}", issues)
        for name in (
            "vendor",
            "model",
            "label",
            "full_code",
            "device_key",
            "visa_address",
            "last_ip",
        )
    }
    port = value.get("last_port")
    if port is not None and (
        isinstance(port, bool) or not isinstance(port, (int, str))
    ):
        issues.append(
            _issue(f"{path}.last_port", _CODE_INVALID_TYPE, "必须是整数或字符串")
        )
        port = None
    known = _optional_bool_field(
        value.get("known"), f"{path}.known", issues, default=False
    )
    source = _optional_text_field(value.get("source"), f"{path}.source", issues)
    return DeviceBindingDraft(
        vendor=fields["vendor"],
        model=fields["model"],
        label=fields["label"],
        full_code=fields["full_code"],
        device_key=fields["device_key"],
        visa_address=fields["visa_address"],
        last_ip=fields["last_ip"],
        last_port=port,
        known=known,
        source=source,
    )


def _decibels_text(value: Decibels | None) -> str | None:
    if value is None:
        return None
    return f"{value.db:g}dB"


def _sweep_document(
    values: tuple[PowerSweep, ...], power_reference: str | None
) -> dict[str, Any]:
    """写出当前 1.2 编辑段的功率扫描：只用 ``power_scan_plans``，不写旧标量。

    缺失计划保持 ``None``（不伪造一组默认计划掩盖配置问题）；新保存的编辑段一律
    声明主功放输入功率语义，旧文档被读取后重新保存时也写新语义，不根据历史数值
    猜测或换算原功率范围。
    """
    return {
        "power_scan_plans": (
            [
                {
                    "start_power": sweep.start.dbm,
                    "step": sweep.step.db,
                    "stop_power": sweep.stop.dbm,
                }
                for sweep in values
            ]
            if values
            else None
        ),
        "power_reference": (
            power_reference if power_reference is not None else INPUT_POWER_REFERENCE
        ),
    }


def _driver_mode_document(draft: ConfigurationDraft) -> dict[str, Any]:
    return {
        "enabled": draft.driver_enabled,
        "estimated_gain_db": (
            draft.driver_gain_estimate.db
            if draft.driver_gain_estimate is not None
            else None
        ),
    }


def _instrument_entry(binding: DeviceBindingDraft | None) -> dict[str, Any]:
    if binding is None:
        return {"enabled": False}
    address = (
        binding.visa_address.strip()
        if isinstance(binding.visa_address, str)
        else ""
    )
    entry: dict[str, Any] = {
        "enabled": bool(address),
        "label": binding.label,
        "model": binding.model,
        "full_code": binding.full_code,
        "device_key": binding.device_key,
    }
    if address:
        entry["address"] = binding.visa_address
    return entry


def _channel_document(channel: PowerChannelDraft) -> dict[str, Any]:
    document: dict[str, Any] = {
        "voltage": {
            "value": channel.voltage,
            "protection": channel.voltage_protection,
            "protection_enabled": channel.voltage_protection_enabled,
        },
        "current": {
            "value": channel.current,
            "protection": channel.current_protection,
            "protection_enabled": channel.current_protection_enabled,
        },
    }
    if channel.power_on_delay_s is not None:
        document["power_on_delay_s"] = channel.power_on_delay_s
    return document


def _power_supply_entry(
    supply_id: str,
    binding: DeviceBindingDraft | None,
    supply: PowerSupplyDraft | None,
) -> dict[str, Any]:
    entry = _instrument_entry(binding)
    entry["label"] = supply_id
    channels = supply.channels if supply is not None else ()
    entry["channels"] = {
        channel.channel: _channel_document(channel) for channel in channels
    }
    return entry
