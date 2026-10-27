"""把配置页表单直接编写为正式运行配置。

配置页仍然只编辑一份面向现场硬件的组合文档；本模块用当前表单值生成同一文档中的
正式 ``test_plan`` / ``run_mapping`` 段，使保存出的文件可以被
:class:`infrastructure.config.json_config_repository.JsonConfigurationRepository`
直接读取，而无需在加载时再做旧配置推断。

约定：

- 表单缺失或非法的字段保持 ``None`` / 空列表，由正式校验在连接仪器前明确失败，
  不生成“待人工确认”的半成品。
- 现场固定接线的 DUT 通道角色（``CH1`` 栅极、``CH2`` 漏极）在这里作为**编辑约定**
  写入正式角色；不从旧配置文件反推，也不猜测未知通道的角色。
- 正式 ``run_mapping`` 只登记**一个**本机供电电源，因此表单里被供电分配引用的电源必须
  唯一。多电源指派无法用当前正式 schema 如实表达，此时抛出
  :class:`ConfigurationAuthoringError` 明确拒绝，而不是写出一份看不出问题、却永远
  无法创建运行的半成品配置。
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any, Mapping

OFFICIAL_SCHEMA_VERSION = "1.0"
# 现场固定接线的编辑约定：DUT 的 CH1 是栅极、CH2 是漏极。未知通道不猜测角色。
DUT_CHANNEL_ROLES = {"CH1": "gate", "CH2": "drain"}
DRIVER_CHANNEL_ROLE = "driver"
_DB_VALUE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*dB\s*$", re.IGNORECASE)
_ASSIGNMENT_GROUPS = ("dut_amplifier", "driver_amplifier")


class ConfigurationAuthoringError(ValueError):
    """当前表单无法如实表达为一份正式运行配置。

    这只用于“正式 schema 根本表达不了”的情况；缺失或非法字段仍写成 ``None`` /
    空列表，交给正式校验报告，不在这里提前猜测或补默认值。
    """


def parse_db_value(value: Any) -> float | None:
    """把 ``30dB`` 形式的表单值转为数值；非法值返回 ``None``。"""
    if not isinstance(value, str):
        return None
    match = _DB_VALUE_PATTERN.fullmatch(value)
    if not match:
        return None
    parsed = float(match.group(1))
    return parsed if math.isfinite(parsed) else None


def author_official_configuration(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """返回带正式 ``test_plan`` / ``run_mapping`` 段的配置文档副本。

    函数不修改调用方对象；输入可以是任意（含不完整）的表单配置，缺失或非法字段只会
    导致正式校验失败，不会抛出异常。唯一例外是正式 schema 无法表达的形态（多个本机
    供电电源同时被分配），此时抛出 :class:`ConfigurationAuthoringError`。
    """
    document = copy.deepcopy(dict(config)) if isinstance(config, Mapping) else {}
    document["schema_version"] = OFFICIAL_SCHEMA_VERSION
    document["test_plan"] = _author_test_plan(document)
    document["run_mapping"] = _author_run_mapping(document)
    if "instruments" in document:
        document["instruments"] = _author_instrument_power_roles(document["instruments"])
    return document


def _author_instrument_power_roles(instruments: Any) -> Any:
    """给本机电源通道写入显式角色，供生产装配直接使用。

    配置页固定按 ``CH1`` 栅极、``CH2`` 漏极接线；已有合法显式角色保持不变，
    未知通道不猜测角色。返回值是独立副本，不修改调用方的配置对象。
    """
    if not isinstance(instruments, Mapping):
        return instruments
    result = copy.deepcopy(dict(instruments))
    supplies = result.get("power_supplies")
    if not isinstance(supplies, dict):
        return result
    for supply in supplies.values():
        channels = supply.get("channels") if isinstance(supply, dict) else None
        if not isinstance(channels, dict):
            continue
        for channel, channel_config in channels.items():
            role = DUT_CHANNEL_ROLES.get(channel)
            if role is None or not isinstance(channel_config, dict):
                continue
            if channel_config.get("role") not in ("gate", "drain"):
                channel_config["role"] = role
    return result


def _author_test_plan(document: Mapping[str, Any]) -> dict[str, Any]:
    signal_source = _mapping(document.get("signal_source"))
    compression = _mapping(document.get("compression_point"))
    attenuator = _mapping(document.get("attenuator"))
    dut_config = _mapping(document.get("dut_config"))
    driver_mode = _mapping(document.get("driver_mode"))
    driver_channels = _author_driver_role_plans(document)
    return {
        "schema_version": OFFICIAL_SCHEMA_VERSION,
        "template": False,
        "plan_id": "gui-configuration",
        "frequencies": {
            "values": _frequencies(document.get("test_frequencies")),
            "unit": "GHz",
        },
        "signal_source": {
            "start_power": signal_source.get("start_power"),
            "stop_power": signal_source.get("stop_power"),
            "step": signal_source.get("step"),
            "unit": "dBm",
        },
        "compression_point": {"type": compression.get("type")},
        "attenuator": {
            "value": parse_db_value(attenuator.get("type")),
            "unit": "dB",
        },
        "dut": {
            "max_input_power": dut_config.get("max_input_power"),
            "power_roles": _author_dut_role_plans(document),
        },
        # 驱动功放由外部供电时（没有本机供电分配）不进入本机驱动模式，与配置页
        # “启用驱动功放电源”开关的含义一致；不根据旧配置推断。
        "driver_mode": {
            "enabled": bool(driver_mode.get("enabled")) and bool(driver_channels),
            "power_roles": driver_channels,
        },
        "other_parameters": {},
    }


def _author_run_mapping(document: Mapping[str, Any]) -> dict[str, Any]:
    instruments = _mapping(document.get("instruments"))
    signal_generator = _mapping(instruments.get("signal_generator"))
    spectrum_analyzer = _mapping(instruments.get("spectrum_analyzer"))
    supplies = _mapping(instruments.get("power_supplies"))
    selected_supply = _selected_supply(document)
    selected_config = _mapping(supplies.get(selected_supply)) if selected_supply else {}
    wiring = _mapping(document.get("wiring"))
    driver_channels = _author_driver_channel_mappings(document)
    return {
        "schema_version": OFFICIAL_SCHEMA_VERSION,
        "template": False,
        "run_id": None,
        "run_datetime": None,
        "operator": None,
        "instruments": {
            "signal_generator": {
                "model": signal_generator.get("model"),
                "visa_address": (
                    signal_generator.get("address")
                    if signal_generator.get("enabled", True)
                    else None
                ),
            },
            "spectrum_analyzer": {
                "model": spectrum_analyzer.get("model"),
                "visa_address": (
                    spectrum_analyzer.get("address")
                    if spectrum_analyzer.get("enabled", True)
                    else None
                ),
            },
            "power_supply": {
                "model": selected_supply,
                "visa_address": selected_config.get("address"),
            },
        },
        "driver_mode": {
            "enabled": bool(_mapping(document.get("driver_mode")).get("enabled"))
            and bool(driver_channels),
            "power_channels": driver_channels,
        },
        "dut_power_channels": _author_dut_channel_mappings(document),
        "wiring": {
            "confirmed": wiring.get("confirmed") is True,
            "connection_note": wiring.get("connection_note"),
            "confirmed_at": wiring.get("confirmed_at"),
            "confirmation_source": wiring.get("confirmation_source"),
        },
        "notes": None,
    }


def _author_dut_role_plans(document: Mapping[str, Any]) -> dict[str, Any]:
    plans: dict[str, Any] = {}
    for assignment in _assignment_supplies(document, "dut_amplifier").values():
        supply_name = assignment.get("name")
        channels = _supply_channels(document, supply_name)
        for channel in _channel_list(assignment.get("channel")):
            role = DUT_CHANNEL_ROLES.get(channel)
            plans.setdefault(role or channel, _channel_plan(role, _mapping(channels.get(channel))))
    return plans


def _author_driver_role_plans(document: Mapping[str, Any]) -> dict[str, Any]:
    plans: dict[str, Any] = {}
    for assignment in _assignment_supplies(document, "driver_amplifier").values():
        supply_name = assignment.get("name")
        channels = _supply_channels(document, supply_name)
        for channel in _channel_list(assignment.get("channel")):
            plans.setdefault(
                DRIVER_CHANNEL_ROLE,
                _channel_plan(DRIVER_CHANNEL_ROLE, _mapping(channels.get(channel))),
            )
    return plans


def _author_dut_channel_mappings(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for assignment in _assignment_supplies(document, "dut_amplifier").values():
        supply_name = assignment.get("name")
        for channel in _channel_list(assignment.get("channel")):
            if channel in seen:
                continue
            seen.add(channel)
            role = DUT_CHANNEL_ROLES.get(channel)
            connection = f"{supply_name or 'power-supply'}-{channel}"
            if role:
                connection = f"{connection} -> DUT {'栅极' if role == 'gate' else '漏极'}"
            mappings.append(
                {"channel": channel, "role": role, "connection": connection}
            )
    return mappings


def _author_driver_channel_mappings(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for assignment in _assignment_supplies(document, "driver_amplifier").values():
        supply_name = assignment.get("name")
        for channel in _channel_list(assignment.get("channel")):
            if channel in seen:
                continue
            seen.add(channel)
            mappings.append(
                {
                    "channel": channel,
                    "role": DRIVER_CHANNEL_ROLE,
                    "connection": f"{supply_name or 'power-supply'}-{channel} -> 驱动功放",
                }
            )
    return mappings


def _channel_plan(role: str | None, settings: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "role": role,
        "voltage": _setting(settings, "voltage", "value"),
        "current": _setting(settings, "current", "value"),
        "voltage_protection": _setting(settings, "voltage", "protection"),
        "current_protection": _setting(settings, "current", "protection"),
    }


def _setting(channel: Mapping[str, Any], section: str, key: str) -> Any:
    value = _mapping(channel.get(section)).get(key)
    return value if _is_finite_number(value) else None


def _assignment_supplies(document: Mapping[str, Any], assignment_name: str) -> Mapping[str, Any]:
    assignments = _mapping(document.get("power_supply_assignment"))
    return _mapping(_mapping(assignments.get(assignment_name)).get("supplies"))


def _supply_channels(document: Mapping[str, Any], supply_name: Any) -> Mapping[str, Any]:
    if not isinstance(supply_name, str):
        return {}
    supplies = _mapping(_mapping(document.get("instruments")).get("power_supplies"))
    return _mapping(_mapping(supplies.get(supply_name)).get("channels"))


def _selected_supply(document: Mapping[str, Any]) -> str | None:
    """返回正式 ``run_mapping`` 要登记的本机供电电源名。

    依据是**供电分配**（``power_supply_assignment``）实际引用的电源，而不是“恰好只有
    一个电源被启用”这类隐式条件；分配为空时返回 ``None``，由正式校验报告缺少电源，
    不做任何推断。被多个电源同时引用时无法如实表达，直接拒绝。
    """
    names: list[str] = []
    for group in _ASSIGNMENT_GROUPS:
        for assignment in _assignment_supplies(document, group).values():
            name = assignment.get("name")
            if isinstance(name, str) and name.strip() and name not in names:
                names.append(name)
    if len(names) > 1:
        raise ConfigurationAuthoringError(
            "配置把供电分配给了多个本机电源（"
            + "、".join(names)
            + "），但正式 run_mapping 只登记一个本机供电电源："
            "请只保留一组电源分配，或把驱动功放改为外部供电（取消其本机供电分配）后再保存。"
        )
    return names[0] if names else None


def _channel_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _frequencies(value: Any) -> list[Any]:
    # 保留原始元素，让正式校验发现非法频率；不静默丢弃表单/文件中的坏值。
    return list(value) if isinstance(value, list) else []


def _is_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


__all__ = [
    "ConfigurationAuthoringError",
    "DUT_CHANNEL_ROLES",
    "OFFICIAL_SCHEMA_VERSION",
    "author_official_configuration",
    "parse_db_value",
]
