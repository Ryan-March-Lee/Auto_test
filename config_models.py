"""测试方案和单次运行资源映射的数据模型与离线校验。

本模块只读取 JSON、构造数据对象并执行输入校验，不创建 VISA 或其他硬件连接。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from config_validation import ConfigIssue, ConfigValidationResult


PathLike = Union[str, Path]
SUPPORTED_SCHEMA_VERSION = "1.0"
SUPPORTED_COMPRESSION_POINTS = {"1dB", "3dB", "5dB"}
PLACEHOLDER_VALUES = {"", "无", "待填写", "<channel_name>", "<gate_or_drain_or_other>"}


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
    driver_enabled: bool = False
    driver_power_channels: List[ChannelMapping] = field(default_factory=list)
    dut_power_channels: List[ChannelMapping] = field(default_factory=list)
    wiring_confirmed: bool = False
    connection_note: Optional[str] = None
    notes: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RunResourceMapping":
        instruments = value.get("instruments", {})
        driver = value.get("driver_mode", {})
        wiring = value.get("wiring", {})
        return cls(
            schema_version=value.get("schema_version", ""),
            template=value.get("template", False),
            run_id=value.get("run_id"),
            run_datetime=value.get("run_datetime"),
            operator=value.get("operator"),
            instruments={
                name: InstrumentMapping.from_dict(item)
                for name, item in instruments.items()
            } if isinstance(instruments, Mapping) else {},
            driver_enabled=driver.get("enabled", False) if isinstance(driver, Mapping) else False,
            driver_power_channels=_channel_mappings(driver.get("power_channels", []) if isinstance(driver, Mapping) else []),
            dut_power_channels=_channel_mappings(value.get("dut_power_channels", [])),
            wiring_confirmed=wiring.get("confirmed", False) if isinstance(wiring, Mapping) else False,
            connection_note=wiring.get("connection_note") if isinstance(wiring, Mapping) else None,
            notes=value.get("notes"),
            raw=dict(value),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "template": self.template,
            "run_id": self.run_id,
            "run_datetime": self.run_datetime,
            "operator": self.operator,
            "instruments": {name: item.to_dict() for name, item in self.instruments.items()},
            "driver_mode": {
                "enabled": self.driver_enabled,
                "power_channels": [item.to_dict() for item in self.driver_power_channels],
            },
            "dut_power_channels": [item.to_dict() for item in self.dut_power_channels],
            "wiring": {"confirmed": self.wiring_confirmed, "connection_note": self.connection_note},
            "notes": self.notes,
        }


@dataclass(frozen=True)
class RunConfiguration:
    test_plan: TestPlan
    run_mapping: RunResourceMapping


def load_json(path: PathLike) -> Dict[str, Any]:
    """读取 JSON 文件，不执行任何硬件操作。"""
    with Path(path).open("r", encoding="utf-8") as config_file:
        value = json.load(config_file)
    if not isinstance(value, dict):
        raise ValueError("配置根节点必须是 JSON 对象")
    return value


def load_test_plan(path: PathLike) -> TestPlan:
    return TestPlan.from_dict(load_json(path))


def load_run_mapping(path: PathLike) -> RunResourceMapping:
    return RunResourceMapping.from_dict(load_json(path))


def load_run_configuration(plan_path: PathLike, mapping_path: PathLike) -> RunConfiguration:
    return RunConfiguration(load_test_plan(plan_path), load_run_mapping(mapping_path))


def validate_test_plan(plan: Union[TestPlan, Mapping[str, Any]]) -> ConfigValidationResult:
    plan = _as_test_plan(plan)
    errors: List[ConfigIssue] = []
    warnings: List[ConfigIssue] = []
    error = lambda path, message: errors.append(ConfigIssue("error", path, message))

    _validate_version(plan.schema_version, "schema_version", error)
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
    if plan.driver_enabled and not plan.driver_power_channels:
        error("driver_mode.power_channels", "驱动模式开启时必须配置驱动功放通道")
    _validate_channels(plan.driver_power_channels, "driver_mode.power_channels", error)
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
    for name in ("signal_generator", "spectrum_analyzer", "power_supply"):
        instrument = mapping.instruments.get(name)
        if instrument is None:
            error(f"instruments.{name}", "缺少仪器配置")
            continue
        _required_text(instrument.model, f"instruments.{name}.model", error)
        _required_text(instrument.visa_address, f"instruments.{name}.visa_address", error)
    _validate_mappings(mapping.dut_power_channels, "dut_power_channels", error)
    _validate_mappings(mapping.driver_power_channels, "driver_mode.power_channels", error)
    dut_channels = {item.channel for item in mapping.dut_power_channels}
    driver_channels = {item.channel for item in mapping.driver_power_channels}
    if not dut_channels:
        error("dut_power_channels", "必须至少分配一个 DUT 电源通道")
    overlap = dut_channels & driver_channels
    if overlap:
        error("power_channels", f"DUT 和驱动功放不能重复使用通道: {', '.join(sorted(overlap))}")
    if not mapping.driver_enabled and driver_channels:
        error("driver_mode.power_channels", "驱动模式关闭时不能配置驱动功放通道")
    if mapping.driver_enabled and not driver_channels:
        error("driver_mode.power_channels", "驱动模式开启时必须配置驱动功放通道")
    if not mapping.wiring_confirmed:
        error("wiring.confirmed", "连接或上电前必须确认现场接线")
    _required_text(mapping.connection_note, "wiring.connection_note", error)
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


def _validate_channels(channels: Mapping[str, PowerChannelPlan], path: str, error: Any) -> None:
    for name, channel in channels.items():
        channel_path = f"{path}.{name}"
        if not name.strip() or name in PLACEHOLDER_VALUES:
            error(channel_path, "通道名称不能是空值或占位值")
        if not isinstance(channel.role, str) or not channel.role.strip() or channel.role in PLACEHOLDER_VALUES:
            error(f"{channel_path}.role", "必须填写稳定的供电角色，不能填写现场 CH 名称")
        _number(channel.voltage, f"{channel_path}.voltage", error)
        _number(channel.current, f"{channel_path}.current", error, non_negative=True)
        _number(channel.voltage_protection, f"{channel_path}.voltage_protection", error)
        _number(channel.current_protection, f"{channel_path}.current_protection", error, non_negative=True)


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


def _required_text(value: Any, path: str, error: Any) -> bool:
    if not isinstance(value, str) or not value.strip() or value.strip() in PLACEHOLDER_VALUES:
        error(path, "必须填写非空实际值")
        return False
    return True


# 便于调用方使用更短的通用名称。
validate_plan = validate_test_plan
validate_mapping = validate_run_mapping
