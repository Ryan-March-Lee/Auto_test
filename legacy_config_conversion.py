"""旧版 ``config.json`` 到新配置模型的只读转换。

转换器只处理配置数据，不连接 VISA、不修改旧配置，也不执行任何测量或上电。
旧配置中的物理通道不能自动推断为稳定供电角色，因此不完整的转换结果会
通过 ``unresolved_fields`` 标记为需要现场确认。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from config_models import (
    ChannelMapping,
    InstrumentMapping,
    PowerChannelPlan,
    RunResourceMapping,
    TestPlan,
)
from config_validation import ConfigIssue


PathLike = Union[str, Path]
_DB_VALUE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*dB\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class LegacyConfigConversionResult:
    """旧配置转换结果及其人工确认信息。"""

    test_plan: TestPlan
    run_mapping: RunResourceMapping
    warnings: List[ConfigIssue] = field(default_factory=list)
    unresolved_fields: List[str] = field(default_factory=list)
    errors: List[ConfigIssue] = field(default_factory=list)
    selected_supply: Optional[str] = None

    @property
    def status(self) -> str:
        if self.errors:
            return "invalid"
        if self.unresolved_fields or self.warnings:
            return "needs_review"
        return "converted"


def parse_legacy_attenuator(value: Any) -> Optional[float]:
    """将旧格式 ``30dB`` 转为数值；非法值返回 ``None``。"""
    if not isinstance(value, str):
        return None
    match = _DB_VALUE_PATTERN.fullmatch(value)
    if not match:
        return None
    parsed = float(match.group(1))
    return parsed if math.isfinite(parsed) else None


def load_legacy_config(path: PathLike) -> Dict[str, Any]:
    """只读加载旧配置文件。"""
    with Path(path).open("r", encoding="utf-8") as config_file:
        value = json.load(config_file)
    if not isinstance(value, dict):
        raise ValueError("旧配置根节点必须是 JSON 对象")
    return value


def legacy_config_to_test_plan(config: Mapping[str, Any]) -> TestPlan:
    """将旧配置中的测试方法参数转换为新 ``TestPlan``。

    旧配置只有物理通道名时，通道会暂时以旧名称保留，供转换报告追踪；
    其 ``role`` 和电气参数是否可用于新方案由组合转换结果标记为未决。
    """
    _require_mapping(config, "config")
    signal_source = _mapping(config.get("signal_source"), "signal_source")
    compression = _mapping(config.get("compression_point"), "compression_point")
    attenuator = _mapping(config.get("attenuator"), "attenuator")
    dut_config = _mapping(config.get("dut_config"), "dut_config")
    driver_mode = _mapping(config.get("driver_mode"), "driver_mode")

    power_channels: Dict[str, PowerChannelPlan] = {}
    legacy_channel_candidates: List[Dict[str, Any]] = []
    assignments = _mapping(config.get("power_supply_assignment"), "power_supply_assignment")
    dut_assignment = _mapping(assignments.get("dut_amplifier"), "power_supply_assignment.dut_amplifier")
    supply_configs = _mapping(_mapping(config.get("instruments"), "instruments").get("power_supplies"), "instruments.power_supplies")
    for supply_assignment in _mapping(dut_assignment.get("supplies"), "power_supply_assignment.dut_amplifier.supplies").values():
        if not isinstance(supply_assignment, Mapping):
            continue
        supply_name = supply_assignment.get("name")
        supply_config = supply_configs.get(supply_name, {})
        supply_channels = supply_config.get("channels", {}) if isinstance(supply_config, Mapping) else {}
        for channel in supply_assignment.get("channel", []):
            if isinstance(channel, str):
                power_channels.setdefault(channel, PowerChannelPlan(role=None))
                channel_config = supply_channels.get(channel, {}) if isinstance(supply_channels, Mapping) else {}
                legacy_channel_candidates.append({
                    "supply": supply_name,
                    "channel": channel,
                    "enabled": supply_config.get("enabled") if isinstance(supply_config, Mapping) else None,
                    "settings": dict(channel_config) if isinstance(channel_config, Mapping) else {},
                })

    attenuator_value = parse_legacy_attenuator(attenuator.get("type"))
    return TestPlan(
        schema_version="1.0",
        template=False,
        plan_id="legacy-config",
        frequencies=list(config.get("test_frequencies", [])) if isinstance(config.get("test_frequencies"), list) else [],
        frequency_unit="GHz",
        start_power=signal_source.get("start_power"),
        stop_power=signal_source.get("stop_power"),
        power_step=signal_source.get("step"),
        power_unit="dBm",
        compression_point=compression.get("type"),
        attenuator_value=attenuator_value,
        attenuator_unit="dB",
        max_input_power=dut_config.get("max_input_power"),
        dut_power_channels=power_channels,
        driver_enabled=driver_mode.get("enabled", False),
        driver_power_channels={},
        other_parameters={
            "legacy_dut_power_supply_count": dut_config.get("power_supply_count"),
            "legacy_power_channel_candidates": legacy_channel_candidates,
        },
        raw=dict(config),
    )


def legacy_config_to_run_mapping(
    config: Mapping[str, Any], *, selected_supply: Optional[str] = None
) -> RunResourceMapping:
    """将旧配置转换为历史默认运行映射，不猜测现场接线。"""
    _require_mapping(config, "config")
    instruments = _mapping(config.get("instruments"), "instruments")
    signal_generator = _instrument(instruments, "signal_generator")
    spectrum_analyzer = _instrument(instruments, "spectrum_analyzer")
    supplies = _mapping(instruments.get("power_supplies"), "instruments.power_supplies")
    selected_name = selected_supply or _unique_enabled_supply(supplies)
    selected = supplies.get(selected_name) if selected_name else None

    assignments = _mapping(config.get("power_supply_assignment"), "power_supply_assignment")
    dut_assignment = _mapping(assignments.get("dut_amplifier"), "power_supply_assignment.dut_amplifier")
    driver_assignment = _mapping(assignments.get("driver_amplifier"), "power_supply_assignment.driver_amplifier")

    dut_channels = _legacy_channel_mappings(dut_assignment)
    driver_channels = _legacy_channel_mappings(driver_assignment)
    return RunResourceMapping(
        schema_version="1.0",
        template=False,
        run_id=None,
        run_datetime=None,
        operator=None,
        instruments={
            "signal_generator": InstrumentMapping(
                model=None,
                visa_address=signal_generator.get("address") if signal_generator.get("enabled", True) else None,
            ),
            "spectrum_analyzer": InstrumentMapping(
                model=None,
                visa_address=spectrum_analyzer.get("address") if spectrum_analyzer.get("enabled", True) else None,
            ),
            "power_supply": InstrumentMapping(
                model=selected_name,
                visa_address=selected.get("address") if isinstance(selected, Mapping) else None,
            ),
        },
        driver_enabled=config.get("driver_mode", {}).get("enabled", False)
        if isinstance(config.get("driver_mode"), Mapping)
        else False,
        driver_power_channels=driver_channels,
        dut_power_channels=dut_channels,
        wiring_confirmed=False,
        connection_note=None,
        notes="由旧 config.json 转换；需要现场确认设备、角色和接线",
        raw={
            "source": "legacy_config",
            "selected_supply": selected_name,
            "candidate_supplies": sorted(supplies),
            "legacy_config": dict(config),
        },
    )


def convert_legacy_config(
    config: Mapping[str, Any], *, selected_supply: Optional[str] = None
) -> LegacyConfigConversionResult:
    """转换旧配置并汇总错误、警告和需要人工确认的字段。"""
    errors: List[ConfigIssue] = []
    warnings: List[ConfigIssue] = []
    unresolved: List[str] = []
    _require_mapping(config, "config")

    try:
        plan = legacy_config_to_test_plan(config)
        mapping = legacy_config_to_run_mapping(config, selected_supply=selected_supply)
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(ConfigIssue("error", "config", str(exc)))
        return LegacyConfigConversionResult(
            test_plan=TestPlan(template=False),
            run_mapping=RunResourceMapping(template=False),
            errors=errors,
            selected_supply=selected_supply,
        )

    supplies = _mapping(_mapping(config.get("instruments"), "instruments").get("power_supplies"), "instruments.power_supplies")
    if selected_supply is not None and selected_supply not in supplies:
        errors.append(ConfigIssue("error", "selected_supply", f"旧配置中不存在电源: {selected_supply}"))
    if selected_supply is None and len(_enabled_supplies(supplies)) != 1:
        unresolved.append("run_mapping.instruments.power_supply")
        warnings.append(ConfigIssue("warning", "instruments.power_supplies", "存在多个或零个启用电源，未自动选择当前运行电源"))
    if selected_supply is not None and isinstance(supplies.get(selected_supply), Mapping) and not supplies[selected_supply].get("enabled", True):
        warnings.append(ConfigIssue("warning", f"instruments.power_supplies.{selected_supply}.enabled", "显式选择的电源未启用"))

    if plan.attenuator_value is None:
        errors.append(ConfigIssue("error", "attenuator.type", "旧衰减器值无法解析"))
    if not plan.dut_power_channels:
        unresolved.append("run_mapping.dut_power_channels")
        warnings.append(ConfigIssue("warning", "power_supply_assignment.dut_amplifier", "旧配置没有可转换的 DUT 通道分配"))
    else:
        unresolved.extend(f"test_plan.dut.power_roles.{name}" for name in plan.dut_power_channels)
        unresolved.extend(f"run_mapping.dut_power_channels[{index}].role" for index, _ in enumerate(mapping.dut_power_channels))
        warnings.append(ConfigIssue("warning", "power_supply_assignment.dut_amplifier", "旧配置只有物理通道名，未自动推断 gate/drain 角色"))
    if plan.driver_enabled and not mapping.driver_power_channels:
        unresolved.append("run_mapping.driver_mode.power_channels")
        warnings.append(ConfigIssue("warning", "driver_mode.enabled", "驱动模式已启用但旧配置没有驱动功放通道"))
    unresolved.append("run_mapping.wiring.confirmed")
    warnings.append(ConfigIssue("warning", "wiring.confirmed", "旧配置没有现场接线确认，转换结果不能直接连接或上电"))

    return LegacyConfigConversionResult(
        test_plan=plan,
        run_mapping=mapping,
        warnings=warnings,
        unresolved_fields=sorted(set(unresolved)),
        errors=errors,
        selected_supply=selected_supply or _unique_enabled_supply(supplies),
    )


def convert_legacy_config_file(
    path: PathLike, *, selected_supply: Optional[str] = None
) -> LegacyConfigConversionResult:
    return convert_legacy_config(load_legacy_config(path), selected_supply=selected_supply)


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{path} 必须是对象")
    return value


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    return _require_mapping(value, path) if value is not None else {}


def _instrument(instruments: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = instruments.get(name, {})
    return _mapping(value, f"instruments.{name}")


def _enabled_supplies(supplies: Mapping[str, Any]) -> List[str]:
    return [name for name, value in supplies.items() if isinstance(value, Mapping) and value.get("enabled", True) is True]


def _unique_enabled_supply(supplies: Mapping[str, Any]) -> Optional[str]:
    enabled = _enabled_supplies(supplies)
    return enabled[0] if len(enabled) == 1 else None


def _legacy_channel_mappings(assignment: Mapping[str, Any]) -> List[ChannelMapping]:
    mappings: List[ChannelMapping] = []
    supplies = assignment.get("supplies", {})
    if not isinstance(supplies, Mapping):
        return mappings
    for supply_assignment in supplies.values():
        if not isinstance(supply_assignment, Mapping):
            continue
        channels = supply_assignment.get("channel", [])
        if not isinstance(channels, list):
            continue
        for channel in channels:
            if isinstance(channel, str):
                mappings.append(ChannelMapping(channel=channel, role=None, connection=None))
    return mappings
