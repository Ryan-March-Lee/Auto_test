"""测试方案和单次运行资源映射的数据模型与离线校验。

本模块只定义配置对象并执行输入校验，不负责 JSON 文件读取，也不创建 VISA 或其他硬件连接。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Union

from domain.configuration.types import ConfigIssue, ConfigValidationResult


PathLike = Union[str, os.PathLike[str]]
SUPPORTED_SCHEMA_VERSION = "1.0"
SUPPORTED_COMPRESSION_POINTS = {"1dB", "3dB", "5dB"}
PLACEHOLDER_VALUES = {"", "无", "待填写", "<channel_name>", "<gate_or_drain_or_other>"}
# 驱动功放（驱放）的供电方式：由本机直流电源供电，或由驱放自带电源（市电）供电。
# 两种情况都处于驱放测试模式，区别只在驱动功放通道是否由本系统控制。
DRIVER_POWER_DC = "dc_supply"
DRIVER_POWER_EXTERNAL = "external_ac"
DRIVER_POWER_SOURCES = (DRIVER_POWER_DC, DRIVER_POWER_EXTERNAL)
# 旧运行快照里单数电源条目的键；新快照只写 ``instruments.power_supplies``。
LEGACY_POWER_SUPPLY_KEY = "power_supply"
# 运行映射里电源集合的键。电源只有一个来源：写它就不能再写旧单数条目。
POWER_SUPPLIES_KEY = "power_supplies"


def resolve_driver_power_source(source: Any, *, driver_channels: Any) -> str:
    """返回驱放供电方式；旧配置没有该字段时按是否配置驱动通道推断。"""
    if source in DRIVER_POWER_SOURCES:
        return str(source)
    return DRIVER_POWER_DC if driver_channels else DRIVER_POWER_EXTERNAL


@dataclass(frozen=True)
class PowerChannelPlan:
    """一个稳定供电角色的测试方案参数，不绑定现场物理通道名。"""

    role: Optional[str] = None
    voltage: Optional[float] = None
    current: Optional[float] = None
    voltage_protection: Optional[float] = None
    current_protection: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PowerChannelPlan":
        known = {
            "role",
            "connection",
            "voltage",
            "current",
            "voltage_protection",
            "current_protection",
            "unit_voltage",
            "unit_current",
        }
        return cls(
            role=value.get("role", value.get("connection")),
            voltage=value.get("voltage"),
            current=value.get("current"),
            voltage_protection=value.get("voltage_protection"),
            current_protection=value.get("current_protection"),
            extra={key: item for key, item in value.items() if key not in known},
        )

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "role": self.role,
            "voltage": self.voltage,
            "current": self.current,
            "voltage_protection": self.voltage_protection,
            "current_protection": self.current_protection,
            "unit_voltage": "V",
            "unit_current": "A",
        }
        result.update(self.extra)
        return result


@dataclass(frozen=True)
class TestPlan:
    """稳定的测试方法和参数。"""

    schema_version: str = SUPPORTED_SCHEMA_VERSION
    template: bool = False
    plan_id: Optional[str] = None
    frequencies: List[Any] = field(default_factory=list)
    frequency_unit: str = "GHz"
    start_power: Optional[float] = None
    stop_power: Optional[float] = None
    power_step: Optional[float] = None
    power_unit: str = "dBm"
    compression_point: Optional[str] = None
    attenuator_value: Optional[float] = None
    attenuator_unit: str = "dB"
    max_input_power: Optional[float] = None
    dut_power_channels: Dict[str, PowerChannelPlan] = field(default_factory=dict)
    driver_enabled: bool = False
    driver_power_source: Optional[str] = None
    driver_power_channels: Dict[str, PowerChannelPlan] = field(default_factory=dict)
    other_parameters: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TestPlan":
        frequencies = value.get("frequencies", {})
        signal_source = value.get("signal_source", {})
        compression = value.get("compression_point", {})
        attenuator = value.get("attenuator", {})
        dut = value.get("dut", {})
        driver = value.get("driver_mode", {})
        return cls(
            schema_version=value.get("schema_version", ""),
            template=value.get("template", False),
            plan_id=value.get("plan_id"),
            frequencies=_list_value(frequencies.get("values", [])) if isinstance(frequencies, Mapping) else [],
            frequency_unit=frequencies.get("unit", "GHz") if isinstance(frequencies, Mapping) else "GHz",
            start_power=signal_source.get("start_power") if isinstance(signal_source, Mapping) else None,
            stop_power=signal_source.get("stop_power") if isinstance(signal_source, Mapping) else None,
            power_step=signal_source.get("step") if isinstance(signal_source, Mapping) else None,
            power_unit=signal_source.get("unit", "dBm") if isinstance(signal_source, Mapping) else "dBm",
            compression_point=compression.get("type") if isinstance(compression, Mapping) else None,
            attenuator_value=attenuator.get("value") if isinstance(attenuator, Mapping) else None,
            attenuator_unit=attenuator.get("unit", "dB") if isinstance(attenuator, Mapping) else "dB",
            max_input_power=dut.get("max_input_power") if isinstance(dut, Mapping) else None,
            dut_power_channels=_channel_plans(
                _first_mapping(dut, "power_roles", "power_channels") if isinstance(dut, Mapping) else {}
            ),
            driver_enabled=driver.get("enabled", False) if isinstance(driver, Mapping) else False,
            driver_power_source=(
                driver.get("power_source") if isinstance(driver, Mapping) else None
            ),
            driver_power_channels=_channel_plans(
                _first_mapping(driver, "power_roles", "power_channels") if isinstance(driver, Mapping) else {}
            ),
            other_parameters=_mapping_value(value.get("other_parameters", {})),
            raw=dict(value),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "template": self.template,
            "plan_id": self.plan_id,
            "frequencies": {"values": list(self.frequencies), "unit": self.frequency_unit},
            "signal_source": {
                "start_power": self.start_power,
                "stop_power": self.stop_power,
                "step": self.power_step,
                "unit": self.power_unit,
            },
            "compression_point": {"type": self.compression_point},
            "attenuator": {"value": self.attenuator_value, "unit": self.attenuator_unit},
            "dut": {
                "max_input_power": self.max_input_power,
                "power_roles": {name: channel.to_dict() for name, channel in self.dut_power_channels.items()},
            },
            "driver_mode": {
                "enabled": self.driver_enabled,
                "power_source": self.driver_power_source,
                "power_roles": {name: channel.to_dict() for name, channel in self.driver_power_channels.items()},
            },
            "other_parameters": dict(self.other_parameters),
        }


@dataclass(frozen=True)
class InstrumentMapping:
    model: Optional[str] = None
    visa_address: Optional[str] = None

    @classmethod
    def from_dict(cls, value: Any) -> "InstrumentMapping":
        value = value if isinstance(value, Mapping) else {}
        return cls(model=value.get("model"), visa_address=value.get("visa_address"))

    def to_dict(self) -> Dict[str, Any]:
        return {"model": self.model, "visa_address": self.visa_address}


@dataclass(frozen=True)
class PowerSupplyMapping:
    """运行快照里的一台本机供电电源，自带身份而不是只有名称。

    集合键（``RunResourceMapping.power_supplies``）是 ``supply_id``，即现场短标签；
    ``device_key`` / ``serial`` 用于追溯，``visa_address`` 用于连接。快照冻结后不再
    回查设备注册表，历史查看与重现都以这条记录为准。
    """

    label: Optional[str] = None
    model: Optional[str] = None
    serial: Optional[str] = None
    device_key: Optional[str] = None
    visa_address: Optional[str] = None

    @classmethod
    def from_dict(cls, value: Any) -> "PowerSupplyMapping":
        value = value if isinstance(value, Mapping) else {}
        return cls(
            label=value.get("label"),
            model=value.get("model"),
            serial=value.get("serial"),
            device_key=value.get("device_key"),
            visa_address=value.get("visa_address"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "model": self.model,
            "serial": self.serial,
            "device_key": self.device_key,
            "visa_address": self.visa_address,
        }

    @classmethod
    def from_instrument_slot(
        cls, value: Any, *, address: Optional[str] = None
    ) -> "PowerSupplyMapping":
        """把旧的单数 ``instruments.power_supply`` 读成一个条目。

        旧条目只有 ``model`` / ``visa_address``；``model`` 当时存的是电源槽位名
        （``PS1`` 等），因此按槽位名回填 ``label``/``device_key``，让旧快照在
        ``power_supplies`` 里也能被唯一指到。``address`` 用于取回条目里的真实地址：
        旧运行上下文把单数条目改写成 ``power_supply: {model, visa_address}``。
        """
        slot = value if isinstance(value, Mapping) else {}
        instrument = InstrumentMapping.from_dict(slot)
        label = instrument.model or None
        return cls(
            label=label,
            model=label,
            serial=None,
            device_key=label,
            visa_address=address if address is not None else instrument.visa_address,
        )


@dataclass(frozen=True)
class ChannelMapping:
    channel: str
    role: Optional[str] = None
    connection: Optional[str] = None

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ChannelMapping":
        return cls(
            channel=value.get("channel", ""),
            role=value.get("role"),
            connection=value.get("connection"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {"channel": self.channel, "role": self.role, "connection": self.connection}


@dataclass(frozen=True)
class RunResourceMapping:
    """本次测试现场实际使用的设备和通道映射。"""

    schema_version: str = SUPPORTED_SCHEMA_VERSION
    template: bool = False
    run_id: Optional[str] = None
    run_datetime: Optional[str] = None
    operator: Optional[str] = None
    instruments: Dict[str, InstrumentMapping] = field(default_factory=dict)
    power_supplies: Dict[str, PowerSupplyMapping] = field(default_factory=dict)
    # 来源文档是否显式写了 ``instruments.power_supplies``（哪怕集合是空的）。
    # 判断“写的是新结构还是旧结构”必须看这个键在不在，不能看集合是不是非空：
    # 否则 ``power_supplies: {}`` 加上旧单数条目会被当成纯旧快照悄悄放行，序列化
    # 时也会在两种结构之间来回变形状。它只描述文档形状，不参与相等比较。
    power_supplies_declared: bool = field(default=False, compare=False, repr=False)
    driver_enabled: bool = False
    driver_power_source: Optional[str] = None
    driver_power_channels: List[ChannelMapping] = field(default_factory=list)
    dut_power_channels: List[ChannelMapping] = field(default_factory=list)
    notes: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunResourceMapping":
        instruments = value.get("instruments", {})
        instruments = instruments if isinstance(instruments, Mapping) else {}
        power_supplies = instruments.get(POWER_SUPPLIES_KEY)
        driver = value.get("driver_mode", {})
        return cls(
            schema_version=value.get("schema_version", ""),
            template=value.get("template", False),
            run_id=value.get("run_id"),
            run_datetime=value.get("run_datetime"),
            operator=value.get("operator"),
            # 电源集合有自己的一层结构，不进 ``instruments`` 的逐台仪器表，否则
            # ``instruments`` 里会多出一条空仪器记录。
            instruments={
                name: InstrumentMapping.from_dict(item)
                for name, item in instruments.items()
                if name != POWER_SUPPLIES_KEY
            },
            power_supplies=_power_supply_mappings(power_supplies),
            power_supplies_declared=POWER_SUPPLIES_KEY in instruments,
            driver_enabled=driver.get("enabled", False) if isinstance(driver, Mapping) else False,
            driver_power_source=(
                driver.get("power_source") if isinstance(driver, Mapping) else None
            ),
            driver_power_channels=_channel_mappings(driver.get("power_channels", []) if isinstance(driver, Mapping) else []),
            dut_power_channels=_channel_mappings(value.get("dut_power_channels", [])),
            notes=value.get("notes"),
            raw=dict(value),
        )

    def to_dict(self) -> Dict[str, Any]:
        """按来源的形状写回电源段：新结构只写集合，旧快照只写单数条目。

        电源只有一个来源，所以两种形状互斥。旧快照（只有 ``instruments.power_supply``、
        没有集合）读入后再写回仍是旧形状，不会被补一个空集合变成“新旧混写”的文档；
        新结构则丢掉旧单数条目，只写 ``instruments.power_supplies``。
        """
        instruments = {name: item.to_dict() for name, item in self.instruments.items()}
        if self.declares_power_supplies():
            # 电源只有一个来源：写集合时不再保留旧单数条目。
            instruments.pop(LEGACY_POWER_SUPPLY_KEY, None)
            instruments[POWER_SUPPLIES_KEY] = {
                supply_id: item.to_dict() for supply_id, item in self.power_supplies.items()
            }
        return {
            "schema_version": self.schema_version,
            "template": self.template,
            "run_id": self.run_id,
            "run_datetime": self.run_datetime,
            "operator": self.operator,
            "instruments": instruments,
            "driver_mode": {
                "enabled": self.driver_enabled,
                "power_source": self.driver_power_source,
                "power_channels": [item.to_dict() for item in self.driver_power_channels],
            },
            "dut_power_channels": [item.to_dict() for item in self.dut_power_channels],
            "notes": self.notes,
        }

    def declares_power_supplies(self) -> bool:
        """这份映射的电源段是否按新结构写。

        有集合条目，或来源文档显式写了集合键（即使集合是空的），都算新结构；程序里
        直接构造出 ``power_supplies={...}`` 的映射同样算。旧单数条目只有在“没有集合”
        时才是这个问题的唯一答案。
        """
        return bool(self.power_supplies) or self.power_supplies_declared

    def legacy_power_supply(self) -> Optional[InstrumentMapping]:
        """旧单数 ``instruments.power_supply`` 的读取兼容。

        新快照不再写这个键；历史运行的字段查看仍可以按旧名字取到“唯一那台”电源。
        恰好一台电源时用它的型号和地址回填，多台时返回 ``None``——多台电源之下
        “那台电源”没有唯一答案，调用方应改用 :attr:`power_supplies`。
        """
        instrument = self.instruments.get(LEGACY_POWER_SUPPLY_KEY)
        if instrument is not None:
            return instrument
        if len(self.power_supplies) != 1:
            return None
        item = next(iter(self.power_supplies.values()))
        return InstrumentMapping(model=item.model, visa_address=item.visa_address)


@dataclass(frozen=True)
class RunConfiguration:
    test_plan: TestPlan
    run_mapping: RunResourceMapping


def validate_test_plan(plan: Union[TestPlan, Mapping[str, Any]]) -> ConfigValidationResult:
    plan = _as_test_plan(plan)
    errors: List[ConfigIssue] = []
    warnings: List[ConfigIssue] = []
    error = lambda path, message: errors.append(ConfigIssue("error", path, message))

    _validate_version(plan.schema_version, "schema_version", error)
    for field_name, value, expected in (
        ("frequencies.unit", plan.frequency_unit, "GHz"),
        ("signal_source.unit", plan.power_unit, "dBm"),
        ("attenuator.unit", plan.attenuator_unit, "dB"),
    ):
        if value != expected:
            error(field_name, f"当前仅支持 {expected}，收到 {value!r}")
    if not isinstance(plan.template, bool):
        error("template", "必须是布尔值")
    if plan.template:
        error("template", "模板不能直接作为正式运行方案")
    if not isinstance(plan.driver_enabled, bool):
        error("driver_mode.enabled", "必须是布尔值")
    _finite_list(plan.frequencies, "frequencies.values", error, positive=True)
    if not plan.frequencies:
        error("frequencies.values", "必须是非空列表")
    else:
        numeric_frequencies = [float(item) for item in plan.frequencies if _is_finite_number(item)]
        if len(set(numeric_frequencies)) != len(numeric_frequencies):
            error("frequencies.values", "不能包含重复频率")
    start = _number(plan.start_power, "signal_source.start_power", error)
    stop = _number(plan.stop_power, "signal_source.stop_power", error)
    step = _number(plan.power_step, "signal_source.step", error, positive=True)
    if start is not None and stop is not None and start > stop:
        error("signal_source", "start_power 不能大于 stop_power")
    if step is None:
        pass
    if plan.compression_point not in SUPPORTED_COMPRESSION_POINTS:
        error("compression_point.type", "必须是 1dB、3dB 或 5dB")
    _number(plan.attenuator_value, "attenuator.value", error, non_negative=True)
    _number(plan.max_input_power, "dut.max_input_power", error)
    if not plan.dut_power_channels:
        error("dut.power_channels", "必须至少配置一个 DUT 电源通道")
    _validate_channels(plan.dut_power_channels, "dut.power_channels", error)
    _validate_channels(plan.driver_power_channels, "driver_mode.power_channels", error)
    _validate_driver_mode(
        plan.driver_enabled, plan.driver_power_source, plan.driver_power_channels, error
    )
    return ConfigValidationResult(errors=errors, warnings=warnings)


def validate_run_mapping(mapping: Union[RunResourceMapping, Mapping[str, Any]]) -> ConfigValidationResult:
    mapping = _as_run_mapping(mapping)
    errors: List[ConfigIssue] = []
    warnings: List[ConfigIssue] = []
    error = lambda path, message: errors.append(ConfigIssue("error", path, message))

    _validate_version(mapping.schema_version, "schema_version", error)
    if not isinstance(mapping.template, bool):
        error("template", "必须是布尔值")
    if mapping.template:
        error("template", "模板不能直接作为正式运行映射")
    if not isinstance(mapping.driver_enabled, bool):
        error("driver_mode.enabled", "必须是布尔值")
    # 电源是集合：不再要求单数 ``instruments.power_supply``，改为校验电源集合本身。
    for name in ("signal_generator", "spectrum_analyzer"):
        instrument = mapping.instruments.get(name)
        if instrument is None:
            error(f"instruments.{name}", "缺少仪器配置")
            continue
        _required_text(instrument.model, f"instruments.{name}.model", error)
        _required_text(instrument.visa_address, f"instruments.{name}.visa_address", error)
    _validate_power_supplies(mapping, error)
    _validate_mappings(mapping.dut_power_channels, "dut_power_channels", error)
    _validate_mappings(mapping.driver_power_channels, "driver_mode.power_channels", error)
    _validate_role_uniqueness(mapping.dut_power_channels, "dut_power_channels", error)
    dut_channels = {item.channel for item in mapping.dut_power_channels}
    driver_channels = {item.channel for item in mapping.driver_power_channels}
    if not dut_channels:
        error("dut_power_channels", "必须至少分配一个 DUT 电源通道")
    overlap = dut_channels & driver_channels
    if overlap:
        error("power_channels", f"DUT 和驱动功放不能重复使用通道: {', '.join(sorted(overlap))}")
    _validate_channel_routing(mapping, error)
    _validate_driver_mode(
        mapping.driver_enabled,
        mapping.driver_power_source,
        mapping.driver_power_channels,
        error,
    )
    return ConfigValidationResult(errors=errors, warnings=warnings)


def validate_run_configuration(configuration: RunConfiguration) -> ConfigValidationResult:
    plan_result = validate_test_plan(configuration.test_plan)
    mapping_result = validate_run_mapping(configuration.run_mapping)
    errors = plan_result.errors + mapping_result.errors
    warnings = plan_result.warnings + mapping_result.warnings
    if not configuration.test_plan.template and not configuration.run_mapping.template:
        plan_roles = set(configuration.test_plan.dut_power_channels)
        mapping_roles = {
            item.role for item in configuration.run_mapping.dut_power_channels if item.role
        }
        missing_roles = plan_roles - mapping_roles
        if missing_roles:
            errors.append(ConfigIssue(
                "error",
                "dut_power_channels",
                f"运行映射缺少测试方案供电角色: {', '.join(sorted(missing_roles))}",
            ))
    return ConfigValidationResult(
        errors=errors,
        warnings=warnings,
    )


def validate_cable_loss_configuration(configuration: RunConfiguration) -> ConfigValidationResult:
    """校验线损测量所需的最小配置。

    线损测量只使用信号源、频谱仪和衰减器，不应被
    功放测试的供电角色或驱动功放映射阻塞。完整功放运行仍使用
    ``validate_run_configuration``。
    """
    errors: List[ConfigIssue] = []
    error = lambda path, message: errors.append(ConfigIssue("error", path, message))
    plan = configuration.test_plan
    mapping = configuration.run_mapping

    _validate_version(plan.schema_version, "schema_version", error)
    _finite_list(plan.frequencies, "frequencies.values", error, positive=True)
    if not plan.frequencies:
        error("frequencies.values", "必须是非空列表")
    _number(plan.attenuator_value, "attenuator.value", error, non_negative=True)

    for name in ("signal_generator", "spectrum_analyzer"):
        instrument = mapping.instruments.get(name)
        if instrument is None:
            error(f"instruments.{name}", "缺少仪器配置")
            continue
        _required_text(instrument.visa_address, f"instruments.{name}.visa_address", error)

    return ConfigValidationResult(errors=errors, warnings=[])


def validate_driver_mapping_configuration(configuration: RunConfiguration) -> ConfigValidationResult:
    """校验驱动功放映射所需的最小配置。

    驱动映射只测量信号源到频谱仪之间的 RF 链路。驱动功放可能由
    外部电源供电，因此不能把 DUT 供电角色或驱动功放电源分配作为本
    测量的必填项。
    """
    errors: List[ConfigIssue] = []
    error = lambda path, message: errors.append(ConfigIssue("error", path, message))
    plan = configuration.test_plan
    mapping = configuration.run_mapping

    _validate_version(plan.schema_version, "schema_version", error)
    _finite_list(plan.frequencies, "frequencies.values", error, positive=True)
    if not plan.frequencies:
        error("frequencies.values", "必须是非空列表")
    _number(plan.start_power, "signal_source.start_power", error)
    _number(plan.stop_power, "signal_source.stop_power", error)
    _number(plan.power_step, "signal_source.step", error, positive=True)
    _number(plan.attenuator_value, "attenuator.value", error, non_negative=True)

    for name in ("signal_generator", "spectrum_analyzer"):
        instrument = mapping.instruments.get(name)
        if instrument is None:
            error(f"instruments.{name}", "缺少仪器配置")
            continue
        _required_text(instrument.visa_address, f"instruments.{name}.visa_address", error)

    return ConfigValidationResult(errors=errors, warnings=[])


def _as_test_plan(value: Union[TestPlan, Mapping[str, Any]]) -> TestPlan:
    return value if isinstance(value, TestPlan) else TestPlan.from_dict(value)


def _as_run_mapping(value: Union[RunResourceMapping, Mapping[str, Any]]) -> RunResourceMapping:
    return value if isinstance(value, RunResourceMapping) else RunResourceMapping.from_dict(value)


def _channel_plans(value: Any) -> Dict[str, PowerChannelPlan]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(name): PowerChannelPlan.from_dict(item) if isinstance(item, Mapping) else PowerChannelPlan()
        for name, item in value.items()
    }


def _first_mapping(value: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in value:
            return value[key]
    return {}


def _list_value(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _mapping_value(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _channel_mappings(value: Any) -> List[ChannelMapping]:
    if not isinstance(value, list):
        return []
    return [
        ChannelMapping.from_dict(item) if isinstance(item, Mapping) else ChannelMapping(channel="")
        for item in value
    ]


def _power_supply_mappings(value: Any) -> Dict[str, PowerSupplyMapping]:
    """读取 ``instruments.power_supplies``；非映射或空值时是空集合。"""
    if not isinstance(value, Mapping):
        return {}
    return {
        str(supply_id): PowerSupplyMapping.from_dict(item)
        for supply_id, item in value.items()
    }


def _validate_version(value: Any, path: str, error: Any) -> None:
    if value != SUPPORTED_SCHEMA_VERSION:
        error(path, f"当前只支持 schema_version={SUPPORTED_SCHEMA_VERSION}")


def _finite_list(values: List[Any], path: str, error: Any, *, positive: bool = False) -> None:
    for index, value in enumerate(values):
        _number(value, f"{path}[{index}]", error, positive=positive)


def _is_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _number(value: Any, path: str, error: Any, *, positive: bool = False, non_negative: bool = False) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        error(path, "必须是有限数值")
        return None
    parsed = float(value)
    if positive and parsed <= 0:
        error(path, "必须大于 0")
    if non_negative and parsed < 0:
        error(path, "必须大于或等于 0")
    return parsed


def _optional_number(value: Any, path: str, error: Any, *, positive: bool = False, non_negative: bool = False) -> Optional[float]:
    """Validate an optional numeric field without treating omission as zero."""
    if value is None:
        return None
    return _number(value, path, error, positive=positive, non_negative=non_negative)


def _validate_channels(channels: Mapping[str, PowerChannelPlan], path: str, error: Any) -> None:
    for name, channel in channels.items():
        channel_path = f"{path}.{name}"
        if not name.strip() or name in PLACEHOLDER_VALUES:
            error(channel_path, "通道名称不能是空值或占位值")
        if not isinstance(channel.role, str) or not channel.role.strip() or channel.role in PLACEHOLDER_VALUES:
            error(f"{channel_path}.role", "必须填写稳定的供电角色，不能填写现场 CH 名称")
        _optional_number(channel.voltage, f"{channel_path}.voltage", error)
        _optional_number(channel.current, f"{channel_path}.current", error, non_negative=True)
        _optional_number(channel.voltage_protection, f"{channel_path}.voltage_protection", error)
        _optional_number(channel.current_protection, f"{channel_path}.current_protection", error, non_negative=True)


def _validate_power_supplies(mapping: RunResourceMapping, error: Any) -> None:
    """校验运行映射里的电源段。

    - 没有电源时无法运行：DUT 通道必须由某台本机电源供电，报告落在
      ``instruments.power_supplies`` 上（新结构的“缺集合”）；
    - 新快照不得同时写旧单数条目，电源只有一个来源。判据是集合**键**在不在，不是
      集合非不非空——``power_supplies: {}`` 加旧条目同样是混写；
    - 旧快照的单数条目仍在时，按旧规则检查它自己的 ``model`` / ``visa_address``，
      缺字段报在旧键上，历史运行不会因为换了结构而“读得进、校验不过”；
    - 每条必须带身份：``label``、``model``、``serial``、``device_key``、``visa_address``；
    - 集合键必须是该条目的 ``label``（它同时是 ``supply_id/CHx`` 的路由前缀）；
    - 地址不能与其它仪器或另一台电源重复，``device_key`` 不能重复——同一台设备
      在快照里出现两次只能意味着写错了身份。
    """
    supplies = mapping.power_supplies
    instruments = mapping.instruments
    seen_keys: Dict[str, str] = {}
    seen_addresses: Dict[str, str] = {}
    for name in ("signal_generator", "spectrum_analyzer"):
        instrument = instruments.get(name)
        if instrument is not None and isinstance(instrument.visa_address, str):
            seen_addresses.setdefault(instrument.visa_address.strip(), f"instruments.{name}")
    legacy = instruments.get(LEGACY_POWER_SUPPLY_KEY)
    legacy_entry = legacy if isinstance(legacy, InstrumentMapping) else None
    if legacy_entry is not None and mapping.declares_power_supplies():
        error(
            f"instruments.{LEGACY_POWER_SUPPLY_KEY}",
            "新运行映射只写 instruments.power_supplies，不能同时写旧的单数电源条目",
        )
    if legacy_entry is not None:
        _required_text(
            legacy_entry.model, f"instruments.{LEGACY_POWER_SUPPLY_KEY}.model", error
        )
        _required_text(
            legacy_entry.visa_address,
            f"instruments.{LEGACY_POWER_SUPPLY_KEY}.visa_address",
            error,
        )
    if not supplies:
        if legacy_entry is None:
            error(f"instruments.{POWER_SUPPLIES_KEY}", "必须至少登记一台本机供电电源")
        return
    for supply_id, supply in supplies.items():
        path = f"instruments.{POWER_SUPPLIES_KEY}.{supply_id}"
        if not _required_text(supply_id, path, error):
            continue
        if supply_id != supply.label:
            error(path, f"集合键必须是该电源的 label: {supply_id!r} != {supply.label!r}")
        for field in ("label", "model", "serial", "device_key", "visa_address"):
            _required_text(getattr(supply, field), f"{path}.{field}", error)
        if isinstance(supply.device_key, str) and supply.device_key.strip():
            key = supply.device_key.strip()
            if key in seen_keys:
                error(
                    f"{path}.device_key",
                    f"同一设备不能登记为两台电源: {key}（另见 {seen_keys[key]}）",
                )
            else:
                seen_keys[key] = path
        if isinstance(supply.visa_address, str) and supply.visa_address.strip():
            address = supply.visa_address.strip()
            if address in seen_addresses:
                error(
                    f"{path}.visa_address",
                    f"多台仪器共用同一 VISA 地址: {address}（另见 {seen_addresses[address]}）",
                )
            else:
                seen_addresses[address] = path


def _validate_channel_routing(mapping: RunResourceMapping, error: Any) -> None:
    """通道必须写成 ``supply_id/CHx``，且前缀指向快照里的电源。

    新快照的每个通道值都是显式路由（与 ``measurement_factory`` 的 ``supply/channel``
    一致）。唯一的例外是已冻结的历史快照：它本来就写成裸通道名 ``CH1``，用旧的单数
    电源条目指代唯一电源，硬性要求加上路由会让历史运行再也打不开。因此只在旧快照
    （不带 ``instruments.power_supplies``）下放行裸通道名。
    """
    legacy_snapshot = _is_legacy_power_snapshot(mapping)
    for field in ("dut_power_channels", "driver_mode.power_channels"):
        channels = (
            mapping.dut_power_channels
            if field == "dut_power_channels"
            else mapping.driver_power_channels
        )
        for index, item in enumerate(channels):
            token = item.channel
            if not isinstance(token, str) or not token:
                continue
            if "/" not in token:
                if not legacy_snapshot:
                    error(
                        f"{field}[{index}].channel",
                        f"电源通道必须写成 supply_id/CHx: {token!r}",
                    )
                continue
            supply_id, _, channel = token.partition("/")
            if supply_id not in mapping.power_supplies:
                error(
                    f"{field}[{index}].channel",
                    f"通道路由指向快照里没有的电源: {supply_id!r}",
                )
            elif not channel.strip():
                error(f"{field}[{index}].channel", f"通道路由缺少通道名: {token!r}")


def _is_legacy_power_snapshot(mapping: RunResourceMapping) -> bool:
    """快照只带旧的单数 ``instruments.power_supply``，没有电源集合（连空集合都没有）。"""
    if mapping.declares_power_supplies():
        return False
    return isinstance(mapping.instruments.get(LEGACY_POWER_SUPPLY_KEY), InstrumentMapping)


def _validate_mappings(mappings: List[ChannelMapping], path: str, error: Any) -> None:
    seen = set()
    for index, item in enumerate(mappings):
        item_path = f"{path}[{index}]"
        if not _required_text(item.channel, f"{item_path}.channel", error):
            continue
        if item.channel in seen:
            error(f"{item_path}.channel", f"通道重复分配: {item.channel}")
        seen.add(item.channel)
        _required_text(item.role, f"{item_path}.role", error)
        _required_text(item.connection, f"{item_path}.connection", error)


def _validate_role_uniqueness(
    mappings: List[ChannelMapping], path: str, error: Any
) -> None:
    """同一个供电角色只能由一个通道提供。

    DUT 的 ``gate`` / ``drain`` 在装配时按**角色**寻址：两个通道都声称 ``gate`` 时，
    “哪一台给栅极”没有唯一答案，连接前必须失败。多电源让角色冲突不再等于通道重复——
    ``DP8B1519/CH1`` 供 ``gate``、``DP8B0885/CH1`` 也供 ``gate`` 是两条不同的通道，
    只有角色是重复的，因此不能只靠 :func:`_validate_mappings` 的通道去重发现。

    驱动功放通道不在此列：它们都是同一个 ``driver`` 角色，多通道按物理路由区分。
    """
    seen: Dict[str, int] = {}
    for index, item in enumerate(mappings):
        role = item.role
        if not isinstance(role, str) or not role.strip():
            continue
        role = role.strip()
        if role in seen:
            error(
                f"{path}[{index}].role",
                f"供电角色重复分配: {role}（另见 {path}[{seen[role]}]）",
            )
            continue
        seen[role] = index


def _validate_driver_mode(enabled: Any, source: Any, channels: Any, error: Any) -> None:
    """校验驱放模式与供电方式是否一致。

    驱放测试模式下，驱动功放既可以由本机直流电源供电（必须有驱动通道），也可以
    自带电源/市电供电（不得占用本机电源通道）。旧配置没有 ``power_source`` 时按
    “是否有驱动通道”推断，保持旧快照仍可读取。
    """
    if enabled is not True:
        if channels:
            error("driver_mode.power_channels", "驱放测试模式关闭时不能配置驱动功放通道")
        if source not in (None, ""):
            error("driver_mode.power_source", "驱放测试模式关闭时不能指定供电方式")
        return
    if source in (None, ""):
        return
    if source not in DRIVER_POWER_SOURCES:
        error("driver_mode.power_source", "必须是 dc_supply（直流电源）或 external_ac（自带供电）")
        return
    if source == DRIVER_POWER_DC and not channels:
        error("driver_mode.power_channels", "驱放使用直流电源时必须配置驱动功放通道")
    if source == DRIVER_POWER_EXTERNAL and channels:
        error("driver_mode.power_channels", "驱放自带供电时不能配置驱动功放通道")


def _required_text(value: Any, path: str, error: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or value.strip() in PLACEHOLDER_VALUES:
        error(path, "必须填写非空实际值")
        return False
    return True


# 便于调用方使用更短的通用名称。
validate_plan = validate_test_plan
validate_mapping = validate_run_mapping
