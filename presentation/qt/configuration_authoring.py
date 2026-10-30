"""把配置页表单直接编写为正式运行配置。

配置页仍然只编辑一份面向现场硬件的组合文档；本模块用当前表单值生成同一文档中的
正式 ``test_plan`` / ``run_mapping`` 段，使保存出的文件可以被
:class:`infrastructure.config.json_config_repository.JsonConfigurationRepository`
直接读取，而无需在加载时再做旧配置推断。

约定：

- 表单缺失或非法的字段保持 ``None`` / 空列表，由正式校验在连接仪器前明确失败，
  不生成“待人工确认”的半成品。
- 现场固定接线的 DUT 通道角色（``CH1`` 栅极、``CH2`` 漏极）在这里作为**编辑约定**
  写入正式 ``test_plan`` / ``run_mapping`` 角色；不从旧配置文件反推，也不猜测未知
  通道的角色。
- ``instruments.power_supplies.<name>.channels.<channel>.role`` 属于接线事实，由配置页
  控件显式选择后随表单写入；本模块不改写它，也不为缺失角色补默认值。
- 正式 ``run_mapping`` 的电源段是**集合** ``instruments.power_supplies.<supply_id>``：
  每台被供电分配实际引用的电源各占一条，条目自带 ``label`` / ``model`` / ``serial`` /
  ``device_key`` / ``visa_address``。快照只登记实际使用的电源，绑定了但没被分配的电源
  不会进快照。集合大小由分配与绑定共同决定，1 台和多台走同一条路径，不再有“只能登记
  一个本机电源”的限制。
- 通道连接统一写成 ``supply_id/CHx``（与 ``instrument.measurement_factory`` 的
  ``supply/channel`` 路由一致），因此 DUT 的 gate 与 drain 可以来自不同电源。同一条
  物理通道被分配两次时**不在这里去重**：重复条目原样写进映射，由正式校验
  （``通道重复分配`` / ``供电角色重复分配``）在连接前失败，而不是悄悄只留一条。
- 身份字段（``label`` / ``serial`` / ``device_key``）只从该电源自己的配置条目读取；
  条目缺少身份时写空并在正式校验中报错，不按地址或型号去猜设备。
"""

from __future__ import annotations

import copy
import math
import re
from typing import Any, Mapping

from application.errors import ConfigurationError
from domain.configuration.rules import assignment_supply_id
from domain.instruments.models import is_legacy_power_supply_slot

OFFICIAL_SCHEMA_VERSION = "1.0"
# 现场固定接线的编辑约定：DUT 的 CH1 是栅极、CH2 是漏极。未知通道不猜测角色。
DUT_CHANNEL_ROLES = {"CH1": "gate", "CH2": "drain"}
DRIVER_CHANNEL_ROLE = "driver"
_DB_VALUE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*dB\s*$", re.IGNORECASE)
_ASSIGNMENT_GROUPS = ("dut_amplifier", "driver_amplifier")


class ConfigurationAuthoringError(ConfigurationError):
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
    导致正式校验失败，不会抛出异常。电源数量不再受“只能有一台”限制：分配引用了两台
    就生成两条电源条目。
    """
    document = copy.deepcopy(dict(config)) if isinstance(config, Mapping) else {}
    document["schema_version"] = OFFICIAL_SCHEMA_VERSION
    document["test_plan"] = _author_test_plan(document)
    document["run_mapping"] = _author_run_mapping(document)
    return document


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
        "driver_mode": {
            "enabled": bool(driver_mode.get("enabled")),
            "power_source": _driver_power_source(document, driver_channels),
            "power_roles": driver_channels,
        },
        "other_parameters": {},
    }


def _author_run_mapping(document: Mapping[str, Any]) -> dict[str, Any]:
    instruments = _mapping(document.get("instruments"))
    signal_generator = _mapping(instruments.get("signal_generator"))
    spectrum_analyzer = _mapping(instruments.get("spectrum_analyzer"))
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
            "power_supplies": _author_power_supply_entries(document),
        },
        "driver_mode": {
            "enabled": bool(_mapping(document.get("driver_mode")).get("enabled")),
            "power_source": _driver_power_source(document, driver_channels),
            "power_channels": driver_channels,
        },
        "dut_power_channels": _author_dut_channel_mappings(document),
        "notes": None,
    }


def _driver_power_source(document: Mapping[str, Any], driver_channels: Any) -> str | None:
    """显式供电方式优先；旧配置按驱动通道是否存在推断。"""
    mode = _mapping(document.get("driver_mode"))
    if not mode.get("enabled"):
        return None
    source = mode.get("power_source")
    if source in ("dc_supply", "external_ac"):
        return source
    return "dc_supply" if driver_channels else "external_ac"


def _author_dut_role_plans(document: Mapping[str, Any]) -> dict[str, Any]:
    plans: dict[str, Any] = {}
    for assignment in _assignment_supplies(document, "dut_amplifier").values():
        supply_name = _assigned_supply_id(assignment)
        channels = _supply_channels(document, supply_name)
        for channel in _channel_list(assignment.get("channel")):
            role = DUT_CHANNEL_ROLES.get(channel)
            plans.setdefault(role or channel, _channel_plan(role, _mapping(channels.get(channel))))
    return plans


def _author_driver_role_plans(document: Mapping[str, Any]) -> dict[str, Any]:
    plans: dict[str, Any] = {}
    for assignment in _assignment_supplies(document, "driver_amplifier").values():
        supply_name = _assigned_supply_id(assignment)
        channels = _supply_channels(document, supply_name)
        for channel in _channel_list(assignment.get("channel")):
            plans.setdefault(
                DRIVER_CHANNEL_ROLE,
                _channel_plan(DRIVER_CHANNEL_ROLE, _mapping(channels.get(channel))),
            )
    return plans


def _author_dut_channel_mappings(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    for assignment in _assignment_supplies(document, "dut_amplifier").values():
        supply_name = _assigned_supply_id(assignment)
        for channel in _channel_list(assignment.get("channel")):
            token = _channel_token(supply_name, channel)
            role = DUT_CHANNEL_ROLES.get(channel)
            connection = token
            if role:
                connection = f"{connection} -> DUT {'栅极' if role == 'gate' else '漏极'}"
            mappings.append(
                {"channel": token, "role": role, "connection": connection}
            )
    return mappings


def _author_driver_channel_mappings(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    for assignment in _assignment_supplies(document, "driver_amplifier").values():
        supply_name = _assigned_supply_id(assignment)
        for channel in _channel_list(assignment.get("channel")):
            token = _channel_token(supply_name, channel)
            mappings.append(
                {
                    "channel": token,
                    "role": DRIVER_CHANNEL_ROLE,
                    "connection": f"{token} -> 驱动功放",
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


def _used_supply_ids(document: Mapping[str, Any]) -> list[str]:
    """按出现顺序返回供电分配实际引用的 ``supply_id``（去重）。

    只认 ``supply_id``：它才是电源引用。旧记录没有该字段时回落读一次同名
    ``name``（过渡期两者同值），完全未绑定的引用（``supply_id: null``）不计入——
    它交给正式校验报告，不能被当成一台电源写进快照。
    """
    names: list[str] = []
    for group in _ASSIGNMENT_GROUPS:
        for assignment in _assignment_supplies(document, group).values():
            name = _assigned_supply_id(assignment)
            # 没有引用（``supply_id: null``、空白）的分配不算一台电源：它交给正式校验
            # 报告，不能被当成某台设备写进快照。
            if name is not None and name not in names:
                names.append(name)
    return names


def _assigned_supply_id(assignment: Mapping[str, Any]) -> str | None:
    """分配条目引用的电源 ``supply_id``。

    读引用规则只有一处实现（:func:`domain.configuration.rules.assignment_supply_id`）：
    ``supply_id`` 优先，只有完全没有该字段的旧记录才回落读一次 ``name``。配置页、正式
    运行映射与文件级校验都用它，不存在第二套编号。
    """
    return assignment_supply_id(assignment)


def _author_power_supply_entries(document: Mapping[str, Any]) -> dict[str, Any]:
    """生成正式 ``run_mapping.instruments.power_supplies``：每台**实际使用**的电源一条。

    条目自带身份（``label`` / ``model`` / ``serial`` / ``device_key``）和地址，历史查看
    与重现不再回查设备注册表。身份只从该电源自己的配置条目读取，不按地址或型号推断。
    """
    supplies = _mapping(_mapping(document.get("instruments")).get("power_supplies"))
    entries: dict[str, Any] = {}
    for supply_id in _used_supply_ids(document):
        supply = _mapping(supplies.get(supply_id))
        entries[supply_id] = {
            "label": supply.get("label"),
            # 旧槽位条目的 ``model`` 里存的是槽位名（``PS1``），那不是型号；旧写法没有
            # 型号信息，宁可留空让正式校验报告，也不把编号当型号写进快照。槽位名的识别
            # 只有领域层一处实现（``is_legacy_power_supply_slot``），页面不再自带槽位清单。
            "model": None
            if is_legacy_power_supply_slot(supply.get("model"))
            else supply.get("model"),
            "serial": supply.get("serial"),
            "device_key": supply.get("device_key"),
            "visa_address": supply.get("address"),
        }
    return entries


def _channel_token(supply_id: str | None, channel: str) -> str:
    """通道的显式路由：``supply_id/CHx``。

    未选择电源时只写通道名，由正式校验报“通道路由指向快照里没有的电源”，
    而不是凭通道名猜一台电源。
    """
    return f"{supply_id}/{channel}" if supply_id else channel


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
