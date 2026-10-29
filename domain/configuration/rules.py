"""生产配置的只读加载和校验。"""

import math
import os
import re
from typing import Any, Dict, List, Optional, Union

from domain.configuration.types import ConfigIssue, ConfigValidationResult
from domain.configuration.models import DRIVER_POWER_DC, DRIVER_POWER_EXTERNAL, DRIVER_POWER_SOURCES


PathLike = Union[str, os.PathLike[str]]
DB_VALUE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*dB\s*$", re.IGNORECASE)
# 设备发现写入的绑定来源：真实网络扫描为 ``scan``，离线演示为 ``simulated``。
SIMULATED_BINDING_SOURCE = "simulated"


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
    driver_power_source = None
    if "driver_mode" in present_sections:
        driver_mode = get_mapping(root.get("driver_mode"), "driver_mode")
        driver_enabled = require(driver_mode, "enabled", "driver_mode.enabled")
        driver_power_source = driver_mode.get("power_source")
        if driver_power_source not in (None, "") and driver_power_source not in DRIVER_POWER_SOURCES:
            error(
                "driver_mode.power_source",
                "必须是 dc_supply（直流电源）或 external_ac（自带供电）",
            )
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
    if driver_enabled is True:
        _validate_driver_power_source(
            driver_power_source,
            driver_assignment,
            "power_supply_assignment.driver_amplifier",
            error,
            warning,
        )
    elif driver_enabled is False and driver_assignment:
        error(
            "power_supply_assignment.driver_amplifier.supplies",
            "驱放测试模式关闭时不能配置驱动功放供电分配",
        )

    errors.extend(simulated_binding_issues(root))

    return ConfigValidationResult(errors=errors, warnings=warnings)


def simulated_binding_issues(document: Any) -> List[ConfigIssue]:
    """返回“模拟扫描写入的设备绑定”问题列表。

    模拟扫描只用于没有设备网络的开发机；它产生的地址与序列号不是现场事实。这样的
    绑定可以被保存用于演示，但绝不能进入真实运行：否则一次演示点击就会让测量对着
    演示地址（或干脆连不上）跑出看似有效的结果。

    这里只判断绑定记录的来源标记，不猜地址是否真实存在——真实性无法离线判断，
    而来源标记是写入时的事实。
    """
    if not isinstance(document, dict):
        return []
    bindings = document.get("device_bindings")
    if not isinstance(bindings, dict):
        return []
    issues: List[ConfigIssue] = []
    for role, record in bindings.items():
        if not isinstance(record, dict):
            continue
        source = str(record.get("source") or "").strip().casefold()
        if source != SIMULATED_BINDING_SOURCE:
            continue
        issues.append(
            ConfigIssue(
                "error",
                f"device_bindings.{role}.source",
                "该设备绑定来自模拟扫描，只能用于离线演示：请用“真实网络扫描”重新获取设备"
                "并应用，或清除设备绑定后再创建运行",
            )
        )
    return issues


def _validate_driver_power_source(
    source: Any,
    driver_assignment: Dict[str, Any],
    path: str,
    error: Any,
    warning: Any,
) -> None:
    """校验驱放测试模式下的供电方式与驱动功放供电分配是否一致。"""
    if source == DRIVER_POWER_DC:
        if not driver_assignment:
            error(
                f"{path}.supplies",
                "驱放使用直流电源时必须选择驱动功放电源和通道",
            )
        return
    if source == DRIVER_POWER_EXTERNAL:
        if driver_assignment:
            error(
                f"{path}.supplies",
                "驱放自带供电时不能配置本机直流电源分配",
            )
        return
    # 旧配置没有声明供电方式：有分配按直流处理，没有分配按自带供电处理，
    # 只提示用户重新保存以把它写成显式字段。
    if not driver_assignment:
        warning(
            "driver_mode.power_source",
            "驱放测试模式未声明供电方式，按自带供电处理；重新保存配置即可记录该选择",
        )
    else:
        warning(
            "driver_mode.power_source",
            "驱放测试模式未声明供电方式，按直流电源处理；重新保存配置即可记录该选择",
        )


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
    # 生产硬件装配只使用配置中显式声明的地址、角色和物理通道；地址与角色
    # 的实际装配校验在 instrument.measurement_factory 中重复执行。
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
    # 角色唯一性作用于整个 assignment：不同电源分别提供 gate 也是重复配置，
    # 生产装配只按角色寻址，无法把两个 gate 分派出去。
    resolved_roles: Dict[str, str] = {}
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
            channel_config = configured_channels.get(channel)
            if not isinstance(channel_config, dict):
                error(f"{role_path}.channel", f"通道 '{channel}' 不存在于电源 '{supply_name}'")
                continue
            channel_role = channel_config.get("role")
            token = f"{supply_name}/{channel}"
            if channel_role not in ("gate", "drain"):
                error(
                    f"{role_path}.channel",
                    f"通道 '{channel}' 必须显式声明 role 为 gate 或 drain，"
                    "不再按 CH1/CH2 推导供电角色",
                )
            elif channel_role in resolved_roles:
                error(
                    f"{role_path}.channel",
                    f"供电角色重复配置: {channel_role} 同时出现在 "
                    f"'{resolved_roles[channel_role]}' 和 '{token}'",
                )
            else:
                resolved_roles[channel_role] = token
    if resolved_roles and set(resolved_roles) != {"gate", "drain"}:
        missing = sorted({"gate", "drain"} - set(resolved_roles))
        error(
            f"{path}.supplies",
            "供电分配必须同时提供 gate 和 drain 通道，缺少: " + "、".join(missing),
        )
    return supplies
