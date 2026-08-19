"""生产配置的只读加载和校验。"""

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from project_paths import CONFIG_FILE, resolve_path


PathLike = Union[str, Path]
DB_VALUE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*dB\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class ConfigIssue:
    """单个配置问题。"""

    level: str
    path: str
    message: str


@dataclass(frozen=True)
class ConfigValidationResult:
    """配置校验结果。"""

    errors: List[ConfigIssue]
    warnings: List[ConfigIssue]

    @property
    def valid(self) -> bool:
        return not self.errors


def load_config(config_path: Optional[PathLike] = None) -> Dict[str, Any]:
    """加载 JSON 配置，不创建仪器连接，也不修改文件。"""
    path = resolve_path(config_path, CONFIG_FILE)
    with path.open("r", encoding="utf-8") as config_file:
        return json.load(config_file)


def validate_config(config: Dict[str, Any]) -> ConfigValidationResult:
    """校验当前生产配置支持的最小安全约束。"""
    errors: List[ConfigIssue] = []
    warnings: List[ConfigIssue] = []

    def error(path: str, message: str) -> None:
        errors.append(ConfigIssue("error", path, message))

    def warning(path: str, message: str) -> None:
        warnings.append(ConfigIssue("warning", path, message))

    def get_mapping(value: Any, path: str) -> Dict[str, Any]:
        if not isinstance(value, dict):
            error(path, "必须是对象")
            return {}
        return value

    def require(mapping: Dict[str, Any], key: str, path: str) -> Any:
        if key not in mapping:
            error(path, "缺少必需字段")
            return None
        return mapping[key]

    def number(value: Any, path: str, *, positive: bool = False, non_negative: bool = False) -> Optional[float]:
        if not _is_finite_number(value):
            error(path, "必须是有限数值")
            return None
        parsed = float(value)
        if positive and parsed <= 0:
            error(path, "必须大于 0")
        if non_negative and parsed < 0:
            error(path, "必须大于或等于 0")
        return parsed

    root = get_mapping(config, "$")
    required_sections = (
        "test_frequencies",
        "signal_source",
        "compression_point",
        "driver_mode",
        "attenuator",
        "dut_config",
        "instruments",
        "power_supply_assignment",
    )
    present_sections = {section for section in required_sections if section in root}
    for section in required_sections:
        require(root, section, section)

    frequencies = root.get("test_frequencies")
    if "test_frequencies" not in present_sections:
        frequencies = None
    elif not isinstance(frequencies, list) or not frequencies:
        error("test_frequencies", "必须是非空列表")
    elif len(set(map(str, frequencies))) != len(frequencies):
        error("test_frequencies", "不能包含重复频率")
    else:
        for index, frequency in enumerate(frequencies):
            number(frequency, f"test_frequencies[{index}]", positive=True)

    start_power = stop_power = None
    if "signal_source" in present_sections:
        signal_source = get_mapping(root.get("signal_source"), "signal_source")
        start_power = number(require(signal_source, "start_power", "signal_source.start_power"), "signal_source.start_power")
        stop_power = number(require(signal_source, "stop_power", "signal_source.stop_power"), "signal_source.stop_power")
        number(require(signal_source, "step", "signal_source.step"), "signal_source.step", positive=True)
        if start_power is not None and stop_power is not None and start_power > stop_power:
            error("signal_source", "start_power 不能大于 stop_power")

    compression_value = None
    if "compression_point" in present_sections:
        compression_point = get_mapping(root.get("compression_point"), "compression_point")
        compression_value = _parse_db_value(
            require(compression_point, "type", "compression_point.type"),
            "compression_point.type",
            error,
            positive=True,
        )

    if "attenuator" in present_sections:
        attenuator = get_mapping(root.get("attenuator"), "attenuator")
        _parse_db_value(require(attenuator, "type", "attenuator.type"), "attenuator.type", error, non_negative=True)

    driver_enabled = None
    if "driver_mode" in present_sections:
        driver_mode = get_mapping(root.get("driver_mode"), "driver_mode")
        driver_enabled = require(driver_mode, "enabled", "driver_mode.enabled")
    if not isinstance(driver_enabled, bool):
        if "driver_mode" in present_sections:
            error("driver_mode.enabled", "必须是布尔值")

    if "dut_config" in present_sections:
        dut_config = get_mapping(root.get("dut_config"), "dut_config")
        number(require(dut_config, "max_input_power", "dut_config.max_input_power"), "dut_config.max_input_power")

    power_supplies: Dict[str, Any] = {}
    if "instruments" in present_sections:
        instruments = get_mapping(root.get("instruments"), "instruments")
        _validate_instrument(instruments, "signal_generator", error)
        _validate_instrument(instruments, "spectrum_analyzer", error)
        if "power_supplies" in instruments:
            power_supplies = get_mapping(instruments["power_supplies"], "instruments.power_supplies")
            for supply_name, supply_config in power_supplies.items():
                _validate_power_supply(supply_name, supply_config, error, warning)

    driver_assignment: Dict[str, Any] = {}
    if "power_supply_assignment" in present_sections:
        assignments = get_mapping(root.get("power_supply_assignment"), "power_supply_assignment")
        driver_assignment = _validate_assignment(assignments, "driver_amplifier", power_supplies, error, warning)
        _validate_assignment(assignments, "dut_amplifier", power_supplies, error, warning)
    if driver_enabled is True and not driver_assignment:
        warning(
            "power_supply_assignment.driver_amplifier.supplies",
            "驱动模式已启用但未配置驱动功放供电分配；请确认驱动功放由外部供电。",
        )

    return ConfigValidationResult(errors=errors, warnings=warnings)


def validate_config_file(config_path: Optional[PathLike] = None) -> ConfigValidationResult:
    """加载并校验指定配置文件。"""
    return validate_config(load_config(config_path))


def _is_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _parse_db_value(
    value: Any,
    path: str,
    error: Any,
    *,
    positive: bool = False,
    non_negative: bool = False,
) -> Optional[float]:
    if not isinstance(value, str):
        error(path, "必须是带 dB 单位的字符串")
        return None
    match = DB_VALUE_PATTERN.match(value)
    if not match:
        error(path, "必须是类似 '30dB' 的有限数值字符串")
        return None
    parsed = float(match.group(1))
    if positive and parsed <= 0:
        error(path, "必须大于 0 dB")
    if non_negative and parsed < 0:
        error(path, "必须大于或等于 0 dB")
    return parsed


def _validate_instrument(instruments: Dict[str, Any], name: str, error: Any) -> None:
    path = f"instruments.{name}"
    instrument = instruments.get(name)
    if not isinstance(instrument, dict):
        error(path, "必须是对象")
        return
    enabled = instrument.get("enabled", True)
    if not isinstance(enabled, bool):
        error(f"{path}.enabled", "必须是布尔值")
    if enabled and (not isinstance(instrument.get("address"), str) or not instrument["address"].strip()):
        error(f"{path}.address", "启用的仪器必须配置非空地址")


def _validate_power_supply(name: str, supply_config: Any, error: Any, warning: Any) -> None:
    path = f"instruments.power_supplies.{name}"
    if not isinstance(supply_config, dict):
        error(path, "必须是对象")
        return
    enabled = supply_config.get("enabled", True)
    if not isinstance(enabled, bool):
        error(f"{path}.enabled", "必须是布尔值")
    if enabled and (not isinstance(supply_config.get("address"), str) or not supply_config["address"].strip()):
        error(f"{path}.address", "启用的电源必须配置非空地址")
    channels = supply_config.get("channels")
    if not isinstance(channels, dict) or not channels:
        error(f"{path}.channels", "必须是非空对象")
        return
    for channel_name, channel_config in channels.items():
        channel_path = f"{path}.channels.{channel_name}"
        if not isinstance(channel_config, dict):
            error(channel_path, "必须是对象")
            continue
        for setting_name in ("voltage", "current"):
            setting_path = f"{channel_path}.{setting_name}"
            setting = channel_config.get(setting_name)
            if not isinstance(setting, dict):
                error(setting_path, "必须是对象")
                continue
            value = setting.get("value")
            protection = setting.get("protection")
            protection_enabled = setting.get("protection_enabled")
            value_number = _validate_finite_number(value, f"{setting_path}.value", error)
            protection_number = _validate_finite_number(protection, f"{setting_path}.protection", error, non_negative=True)
            if not isinstance(protection_enabled, bool):
                error(f"{setting_path}.protection_enabled", "必须是布尔值")
            elif (
                enabled is True
                and protection_enabled
                and value_number is not None
                and protection_number is not None
                and value_number > protection_number
            ):
                warning(
                    setting_path,
                    "保护已启用但工作值大于保护值；请确认该仪器保护参数的单位和语义。",
                )


def _validate_finite_number(value: Any, path: str, error: Any, *, non_negative: bool = False) -> Optional[float]:
    if not _is_finite_number(value):
        error(path, "必须是有限数值")
        return None
    parsed = float(value)
    if non_negative and parsed < 0:
        error(path, "必须大于或等于 0")
    return parsed


def _validate_assignment(
    assignments: Dict[str, Any],
    assignment_name: str,
    power_supplies: Dict[str, Any],
    error: Any,
    warning: Any,
) -> Dict[str, Any]:
    path = f"power_supply_assignment.{assignment_name}"
    assignment = assignments.get(assignment_name)
    if not isinstance(assignment, dict):
        error(path, "必须是对象")
        return {}
    supplies = assignment.get("supplies")
    if not isinstance(supplies, dict):
        error(f"{path}.supplies", "必须是对象")
        return {}
    configured_count = assignment.get("power_supply_count")
    if isinstance(configured_count, bool) or not isinstance(configured_count, int) or configured_count < 0:
        error(f"{path}.power_supply_count", "必须是非负整数")
    elif configured_count != len(supplies):
        warning(f"{path}.power_supply_count", f"配置值为 {configured_count}，实际分配数量为 {len(supplies)}")
    for role, supply_assignment in supplies.items():
        role_path = f"{path}.supplies.{role}"
        if not isinstance(supply_assignment, dict):
            error(role_path, "必须是对象")
            continue
        supply_name = supply_assignment.get("name")
        if not isinstance(supply_name, str) or not supply_name.strip():
            error(f"{role_path}.name", "必须是非空电源名称")
            continue
        supply_config = power_supplies.get(supply_name)
        if not isinstance(supply_config, dict):
            error(f"{role_path}.name", f"引用了不存在的电源 '{supply_name}'")
            continue
        if supply_config.get("enabled", True) is not True:
            error(f"{role_path}.name", f"引用的电源 '{supply_name}' 未启用")
        channels = supply_assignment.get("channel")
        if not isinstance(channels, list) or not channels:
            error(f"{role_path}.channel", "必须是非空通道列表")
            continue
        configured_channels = supply_config.get("channels", {})
        for channel in channels:
            if channel not in configured_channels:
                error(f"{role_path}.channel", f"通道 '{channel}' 不存在于电源 '{supply_name}'")
    return supplies
