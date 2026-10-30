"""生产配置的只读加载和校验。"""

import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

from domain.configuration.types import ConfigIssue, ConfigValidationResult
from domain.configuration.models import DRIVER_POWER_DC, DRIVER_POWER_EXTERNAL, DRIVER_POWER_SOURCES


PathLike = Union[str, os.PathLike[str]]
DB_VALUE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*dB\s*$", re.IGNORECASE)
# 设备发现写入的绑定来源：真实网络扫描为 ``scan``，离线演示为 ``simulated``。
SIMULATED_BINDING_SOURCE = "simulated"
# 供电分配的两个组：电源引用按组各自校验，物理通道在两组之间统一比对。
ASSIGNMENT_GROUPS = ("driver_amplifier", "dut_amplifier")


def assignment_supply_id(entry: Any) -> Optional[str]:
    """返回一条供电分配记录引用的电源 ``supply_id``；未绑定或无法识别时返回 ``None``。

    这是“分配怎样指向一台电源”的唯一实现：配置页读取
    （``presentation.qt.config_form_state``）与正式运行映射的编写都调用这里，不各写一套。

    新配置只认 ``supply_id``：记录里出现该字段就以它为准（``null`` / 空白表示“引用
    未绑定”），只有完全没有该字段的旧记录才回落读一次过渡别名 ``name``。``name`` 不是
    第二个可用性来源，也不参与“猜一台电源”。

    返回值只判断“是不是非空字符串”，不裁剪、不改写：写错的引用要留给校验报告，不能在
    校验前被悄悄修成看起来合法的值。
    """
    if not isinstance(entry, Mapping):
        return None
    field = "supply_id" if "supply_id" in entry else "name"
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def validate_hardware_connections(config: Dict[str, Any]) -> ConfigValidationResult:
    """只校验硬件装配段的**连接冲突**（字段完整性由 :func:`validate_config` 负责）。

    这里回答的是“照这份配置去连，会不会连错设备、抢同一个资源”，因此只保留满足
    “两台设备会被同时连接”前提的规则，并且只看启用的条目：

    - 启用的电源之间、电源与信号源/频谱仪之间不能共用同一个 VISA 地址：地址决定物理
      资源，重复地址意味着一次连接可能落到另一台设备上（:func:`_validate_supply_addresses`）；
    - 供电分配引用必须指向配置里存在且已启用的电源，过渡别名 ``name`` 必须与
      ``supply_id`` 同值（:func:`_collect_assignment_channels` 的引用检查，与
      :func:`_validate_assignment` 共用同一段实现）；
    - 同一个物理通道 ``supply_id/CHx`` 不能同时供 DUT 与驱动功放
      （:func:`_validate_cross_group_channels`）。

    **不**在这里重复字段完整性（通道保护值、``power_supply_count``、通道是否声明
    ``role``、gate/drain 是否齐全）：这些配置根本连不上，但那是配置作者要修的内容，
    由 :func:`validate_config` 报；若在这里也报，运行创建会在写下运行目录之前以“连接
    冲突”拒绝一份结构缺失的配置，消息会把用户引向错误的方向。

    调用方：``app.run_context.create_prepared_run``（写运行目录前）与配置页的正式校验。
    """
    errors: List[ConfigIssue] = []

    def error(path: str, message: str) -> None:
        errors.append(ConfigIssue("error", path, message))

    if not isinstance(config, Mapping):
        return ConfigValidationResult(errors=[], warnings=[])
    instruments = config.get("instruments")
    instruments = instruments if isinstance(instruments, Mapping) else {}
    supplies_value = instruments.get("power_supplies")
    power_supplies = (
        {str(name): value for name, value in supplies_value.items()}
        if isinstance(supplies_value, Mapping)
        else {}
    )
    assignments_value = config.get("power_supply_assignment")
    assignments = (
        {str(name): value for name, value in assignments_value.items()}
        if isinstance(assignments_value, Mapping)
        else {}
    )
    _validate_supply_addresses(
        {str(name): value for name, value in instruments.items()}, power_supplies, error
    )
    # 引用规则只由 ``_collect_assignment_channels`` 实现一份；它顺带给出跨组比对要用的
    # 通道表，组内重复与角色缺失交给正式校验。
    _validate_cross_group_channels(assignments, power_supplies, error)
    return ConfigValidationResult(errors=_unique_issues(errors), warnings=[])


def _unique_issues(issues: List[ConfigIssue]) -> List[ConfigIssue]:
    seen = set()
    result: List[ConfigIssue] = []
    for issue in issues:
        key = (issue.path, issue.message)
        if key in seen:
            continue
        seen.add(key)
        result.append(issue)
    return result


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
            _validate_supply_addresses(instruments, power_supplies, error)

    driver_assignment: Dict[str, Any] = {}
    if "power_supply_assignment" in present_sections:
        assignments = get_mapping(root.get("power_supply_assignment"), "power_supply_assignment")
        driver_assignment = _validate_assignment(assignments, "driver_amplifier", power_supplies, error, warning)
        _validate_assignment(assignments, "dut_amplifier", power_supplies, error, warning)
        _validate_cross_group_channels(assignments, power_supplies, error)
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

    绑定结构是“信号源 / 频谱仪各一条 + ``power_supplies`` 里按 ``supply_id`` 的多条”，
    两类都要检查：只查顶层键会让电源绑定绕过这条门禁。

    这里只判断绑定记录的来源标记，不猜地址是否真实存在——真实性无法离线判断，
    而来源标记是写入时的事实。
    """
    if not isinstance(document, dict):
        return []
    bindings = document.get("device_bindings")
    if not isinstance(bindings, dict):
        return []
    issues: List[ConfigIssue] = []
    for path, record in _binding_entries(bindings):
        if not isinstance(record, dict):
            continue
        source = str(record.get("source") or "").strip().casefold()
        if source != SIMULATED_BINDING_SOURCE:
            continue
        issues.append(
            ConfigIssue(
                "error",
                f"{path}.source",
                "该设备绑定来自模拟扫描，只能用于离线演示：请用“真实网络扫描”重新获取设备"
                "并应用，或清除设备绑定后再创建运行",
            )
        )
    return issues


def _binding_entries(bindings: dict) -> List[Tuple[str, Any]]:
    """展开 ``device_bindings``：顶层角色键 + ``power_supplies`` 里的每台电源。

    电源是同一类别下的多台设备，集合的键是 ``supply_id``；逐条展开才能让每条电源
    绑定都受同一条门禁约束，而不是被当成一个“记录”整体跳过。
    """
    entries: List[Tuple[str, Any]] = []
    for role, record in bindings.items():
        if role == "power_supplies" and isinstance(record, dict):
            for supply_id, supply_record in record.items():
                entries.append((f"device_bindings.power_supplies.{supply_id}", supply_record))
            continue
        entries.append((f"device_bindings.{role}", record))
    return entries


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
    for role, supply_assignment in supplies.items():
        if not isinstance(supply_assignment, dict):
            error(f"{path}.supplies.{role}", "必须是对象")
            continue
        channels = supply_assignment.get("channel")
        if not isinstance(channels, list) or not channels:
            # 结构问题必须由这里报出：下面的通道解析会跳过它，不会产生任何通道。
            error(f"{path}.supplies.{role}.channel", "必须是非空通道列表")
    # 角色唯一性作用于整个 assignment：不同电源分别提供 gate 也是重复配置，
    # 生产装配只按角色寻址，无法把两个 gate 分派出去。
    resolved_roles: Dict[str, str] = {}
    # 引用与通道解析只在这里做一次；下面的角色规则叠加在同一个通道表上。
    channels = _collect_assignment_channels(
        assignments, power_supplies, error, group=assignment_name
    )
    # 未声明角色的通道按分配归组：每个分配只报一条，逐通道重复同一条说明只是噪声。
    undecided: Dict[str, List[str]] = {}
    for item in channels:
        if item.role is None:
            undecided.setdefault(item.role_owner, []).append(item.name)
    for role_owner, names in undecided.items():
        error(
            f"{path}.supplies.{role_owner}.channel",
            f"通道 {', '.join(names)} 必须显式声明 role 为 gate 或 drain，"
            "不再按 CH1/CH2 推导供电角色",
        )
    for item in channels:
        if item.role is None:
            continue
        if item.role in resolved_roles:
            error(
                f"{path}.supplies.{item.role_owner}.channel",
                f"供电角色重复配置: {item.role} 同时出现在 "
                f"'{resolved_roles[item.role]}' 和 '{item.token}'",
            )
        else:
            resolved_roles[item.role] = item.token
    if resolved_roles and set(resolved_roles) != {"gate", "drain"}:
        missing = sorted({"gate", "drain"} - set(resolved_roles))
        error(
            f"{path}.supplies",
            "供电分配必须同时提供 gate 和 drain 通道，缺少: " + "、".join(missing),
        )
    return supplies


@dataclass(frozen=True)
class _AssignedChannel:
    """一条供电分配里给出的一个物理通道，以及它在配置里的位置。"""

    group: str
    role_owner: str
    name: str
    supply_id: str
    token: str
    # 通道在电源自己的 ``channels`` 里声明的供电角色；未声明或名称不合法时为 ``None``。
    role: Optional[str]

    @property
    def path(self) -> str:
        return f"power_supply_assignment.{self.group}.supplies.{self.role_owner}.channel"


def _collect_assignment_channels(
    assignments: Dict[str, Any],
    power_supplies: Dict[str, Any],
    error: Any,
    *,
    check_roles: bool = True,
    group: Optional[str] = None,
) -> List[_AssignedChannel]:
    """把供电分配解析成物理通道列表，并报告引用层面的问题。

    这是“分配指向哪台电源、哪些物理通道”的唯一实现：完整校验
    （:func:`_validate_assignment`）在它之上再叠加角色与数量规则，连接冲突校验
    （:func:`validate_hardware_connections`）只取通道表与引用错误。

    报告的错误都关于**引用**，且都会让连接无法成立：引用缺失、引用了不存在的电源、
    引用了未启用的电源、通道不在该电源上、过渡别名 ``name`` 与 ``supply_id`` 不一致。
    “通道列表为空”与“通道没有声明 role”是字段完整性问题，仅在 ``check_roles``
    为真时由 :func:`_validate_assignment` 报（它拿到的是同一个列表）。

    ``group`` 不为空时只解析这一组，其余组忽略。``check_roles`` 为假时通道表只保留
    连接判据（``token`` / ``path``），``role`` 一律为空——连接冲突与供电角色无关，
    角色缺失不该让连接校验跟着失败。
    """
    collected: List[_AssignedChannel] = []
    for group_name in ASSIGNMENT_GROUPS:
        if group is not None and group_name != group:
            continue
        assignment = assignments.get(group_name)
        if not isinstance(assignment, Mapping):
            continue
        supplies = assignment.get("supplies")
        if not isinstance(supplies, Mapping):
            continue
        for role_owner, supply_assignment in supplies.items():
            if not isinstance(supply_assignment, Mapping):
                # 结构问题：交给完整校验报“必须是对象”。
                continue
            owner = str(role_owner)
            role_path = f"power_supply_assignment.{group_name}.supplies.{owner}"
            reference_path = (
                f"{role_path}.supply_id"
                if "supply_id" in supply_assignment
                else f"{role_path}.name"
            )
            _require_reference_alias(supply_assignment, role_path, error)
            supply_id = assignment_supply_id(supply_assignment)
            if supply_id is None:
                error(reference_path, "必须填写已绑定的电源引用 supply_id")
                continue
            supply_config = power_supplies.get(supply_id)
            if not isinstance(supply_config, Mapping):
                error(reference_path, f"引用了不存在的电源 '{supply_id}'")
                continue
            if supply_config.get("enabled", True) is not True:
                error(reference_path, f"引用的电源 '{supply_id}' 未启用")
            configured_channels = supply_config.get("channels")
            configured_channels = (
                configured_channels if isinstance(configured_channels, Mapping) else {}
            )
            value = supply_assignment.get("channel")
            if not isinstance(value, list) or not value:
                # 结构问题（“必须是非空通道列表”）：交给完整校验，连接无从谈起。
                # 注意字符串（``"CH1"``）也是“不是列表”，不能按字符逐个解析成通道。
                continue
            for channel in value:
                channel_config = configured_channels.get(channel)
                if not isinstance(channel_config, Mapping):
                    error(
                        f"{role_path}.channel",
                        f"通道 '{channel}' 不存在于电源 '{supply_id}'",
                    )
                    continue
                role = channel_config.get("role")
                collected.append(
                    _AssignedChannel(
                        group=group_name,
                        role_owner=owner,
                        name=str(channel),
                        supply_id=supply_id,
                        token=f"{supply_id}/{channel}",
                        role=role if check_roles and role in ("gate", "drain") else None,
                    )
                )
    return collected


def _require_reference_alias(entry: Dict[str, Any], path: str, error: Any) -> None:
    """过渡别名 ``name`` 若同时出现，必须与主字段 ``supply_id`` 同值。

    保存时两个字段写同值、读取时只认 ``supply_id``；一旦被手工改成两个值，回填的与
    运行用的就不是同一台电源——那就是“第二套编号”，必须在连接前报错。
    """
    if "supply_id" not in entry or "name" not in entry:
        return
    primary = entry.get("supply_id")
    alias = entry.get("name")
    if not isinstance(primary, str) or not primary.strip():
        return
    if not isinstance(alias, str) or not alias.strip():
        return
    if primary != alias:
        error(f"{path}.name", f"过渡字段 name 必须与 supply_id 同值: {alias!r} != {primary!r}")


def _validate_supply_addresses(
    instruments: Dict[str, Any],
    power_supplies: Dict[str, Any],
    error: Any,
) -> None:
    """启用的仪器之间不能共用同一个 VISA 地址。

    地址重复意味着“以为连的是这台、实际打开的是那台”。装配阶段
    （``instrument.measurement_factory``）会拒绝这种配置，这里用同一判据在连接前报出
    具体是两个字段在抢同一个地址，而不是等界面点了“开始”才失败。比对忽略大小写与
    首尾空白：VISA 资源名不区分大小写，只差大小写仍是同一个物理资源。

    只有启用的条目参与比对：未启用的电源不会连接（未绑定设备的电源地址为空或已失效），
    它的历史地址不是连接冲突。
    """
    seen: Dict[str, str] = {}
    for name in ("signal_generator", "spectrum_analyzer"):
        instrument = instruments.get(name)
        if not isinstance(instrument, dict) or instrument.get("enabled", True) is not True:
            continue
        address = instrument.get("address")
        if isinstance(address, str) and address.strip():
            seen.setdefault(_address_key(address), f"instruments.{name}.address")
    for supply_name, supply_config in power_supplies.items():
        if not isinstance(supply_config, dict) or supply_config.get("enabled", True) is not True:
            continue
        address = supply_config.get("address")
        if not isinstance(address, str) or not address.strip():
            continue
        path = f"instruments.power_supplies.{supply_name}.address"
        key = _address_key(address)
        if key in seen:
            error(path, f"多台仪器共用同一 VISA 地址: {address.strip()}（另见 {seen[key]}）")
            continue
        seen[key] = path


def _address_key(address: str) -> str:
    return " ".join(address.split()).casefold()


def _validate_cross_group_channels(
    assignments: Dict[str, Any],
    power_supplies: Dict[str, Any],
    error: Any,
) -> None:
    """同一个物理通道不能同时供 DUT 与驱动功放。

    通道是 ``supply_id/CHx``：一台电源的一个通道只有一个物理输出，“既接 DUT 栅极、
    又接驱动功放”不可能同时成立。两条记录各自看都合法，只有放在一起才矛盾，因此必须
    按“物理通道”跨组统一比对（组内重复由 :func:`_validate_assignment` 的供电角色
    唯一性报出）。

    “分配里写了哪些物理通道”只在 :func:`_collect_assignment_channels` 解析一次；这里
    不重复解释引用（引用问题已由同一次调用报出），只比较 token。token 与正式运行映射里
    ``DUT 和驱动功放不能重复使用通道`` 用的 ``supply_id/CHx`` 同一写法。
    """
    uses: Dict[str, Tuple[str, str]] = {}
    for item in _collect_assignment_channels(
        assignments, power_supplies, error, check_roles=False
    ):
        previous = uses.get(item.token)
        if previous is None:
            uses[item.token] = (item.group, item.path)
        elif previous[0] != item.group:
            error(
                item.path,
                f"同一物理通道不能同时用于 DUT 与驱动功放: {item.token}"
                f"（另见 {previous[1]}）",
            )
