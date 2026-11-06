"""生产配置的只读加载和校验。

归属（见 `docs/配置领域词汇与模块归属.md`）：domain。这里登记的配置规则是正式归属：
配置页与运行创建只消费结果，不各自复制判据。
"""

import math
import os
import re
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Tuple, Union

from domain.configuration.types import ConfigIssue, ConfigValidationResult
from domain.configuration.models import MAX_INTER_CHANNEL_DELAY_S


PathLike = Union[str, os.PathLike[str]]
DB_VALUE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*dB\s*$", re.IGNORECASE)
# 设备发现写入的绑定来源：真实网络扫描为 ``scan``，离线演示为 ``simulated``。
SIMULATED_BINDING_SOURCE = "simulated"
# 上电计划的两个组：组内顺序来自 ``power_plan``，物理通道在两组之间统一比对。
# 组名沿用编辑段的历史键名，避免与 1.1 已保存的配置分叉；它不再表示“给器件固定
# 分配某台电源”，只表示“这组上电计划属于哪个器件”。
ASSIGNMENT_GROUPS = ("driver_amplifier", "dut_amplifier")


def assignment_supply_id(entry: Any) -> Optional[str]:
    """返回一条旧供电分配记录引用的电源 ``supply_id``；未绑定或无法识别时返回 ``None``。

    这是“旧分配怎样指向一台电源”的唯一实现：迁移器读取旧配置时调用这里，不另写一套。
    1.1 的器件电源由 ``power_plan`` 路由决定，新保存路径不再写分配记录。

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
    - 上电计划引用的电源必须已登记、已启用并带有连接地址，通道必须在该电源上声明
      （:func:`_validate_plan_routes` / :func:`_validate_plan_applied_supplies`）；
    - 同一个物理通道 ``supply_id/CHx`` 不能同时出现在 DUT 与驱动功放的上电计划里
      （:func:`_validate_plan_conflicts`）。

    **不**在这里重复字段完整性（通道保护值、计划形状、通道列表是否为空）：这些配置
    根本连不上，但那是配置作者要修的内容，由 :func:`validate_config` 报；若在这里也报，
    运行创建会在写下运行目录之前以“连接冲突”拒绝一份结构缺失的配置，消息会把用户引向
    错误的方向。步骤 5 起通道已没有供电角色，这里也不看任何角色字段。

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
    # 引用规则只由计划解析实现一份；它顺带给出跨组比对要用的通道表，形状与字段完整性
    # 交给正式校验。
    _validate_plan_connection_routes(assignments, power_supplies, error)
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
            _validate_supply_addresses(instruments, power_supplies, error)

    driver_plan_routes: Dict[Tuple[str, str], str] = {}
    if "power_supply_assignment" in present_sections:
        plans = get_mapping(root.get("power_supply_assignment"), "power_supply_assignment")
        driver_plan_routes = _validate_group_plan(
            plans, "driver_amplifier", power_supplies, error
        )
        _validate_group_plan(plans, "dut_amplifier", power_supplies, error)
        _validate_plan_conflicts(plans, power_supplies, error)
    if driver_enabled is False and driver_plan_routes:
        error(
            "power_supply_assignment.driver_amplifier.power_plan",
            "驱放测试模式关闭时不能配置驱动功放上电计划",
        )

    errors.extend(simulated_binding_issues(root))

    return ConfigValidationResult(errors=errors, warnings=warnings)


def validate_power_plan(config: Any) -> ConfigValidationResult:
    """校验编辑文档里的组上电计划与漏极引用（v1.1 §6.12 规则 5、7、15）。

    通道角色已不再参与通道选择、映射或上电，本函数只按**物理路由**回答这些问题：

    - 计划形状：``power_plan`` 只能是对象，必须同时包含 ``power_sequence`` 与
      ``inter_channel_delay_s``，且不能有其它字段；序列元素必须是
      ``{supply_id, channel}`` 两段对象；
    - 引用有效性：每条路由指向已登记、已启用的电源，且该电源声明了该通道；
    - 跨组冲突：同一物理通道不能同时出现在 DUT 与驱动功放的计划里；
    - 驱放一致性：驱放关闭时不得有计划；驱放开启时计划为空即自带供电，是合法状态；
    - 漏极：``dut_config.drain_channel`` 若给出，必须出现在 DUT 上电计划中。漏极是
      效率测量输入，不参与排序、间隔或启停；这里不要求每种配置都必须有漏极，主功放
      运行的必填性由运行校验负责。

    **过渡行为**：编写器尚未写出 ``power_plan`` 键时（迁移产物或旧编辑段），本次跳过
    该组的顺序检查，只保留其它检查；一旦写了 ``power_plan``（包括空计划），就按完整
    规则校验，不再“缺省即放行”。计划对象内部仍必须显式写出两个键，缺任意一个都按
    形状错误报告，不用空序列或 ``0.0`` 补全。漏极同理：字段不存在时不报缺失，出现
    非法引用时失败。

    计划本身就是“哪些电源的哪些通道给哪个器件上电”的唯一来源；不再要求另外给 DUT
    或驱动功放固定分配某台电源。
    """
    errors: List[ConfigIssue] = []

    def error(path: str, message: str) -> None:
        errors.append(ConfigIssue("error", path, message))

    if not isinstance(config, Mapping):
        return ConfigValidationResult(errors=[], warnings=[])

    instruments = config.get("instruments")
    supplies_value = instruments.get("power_supplies") if isinstance(instruments, Mapping) else None
    supplies = supplies_value if isinstance(supplies_value, Mapping) else {}
    assignments_value = config.get("power_supply_assignment")
    assignments = assignments_value if isinstance(assignments_value, Mapping) else {}

    # 只处理真正写出了 ``power_plan`` 的组；缺失键是编写器尚未生成计划的过渡状态。
    plan_entries: Dict[str, Tuple[Tuple[str, str, str], ...]] = {}
    for group in ASSIGNMENT_GROUPS:
        assignment = assignments.get(group)
        if not isinstance(assignment, Mapping) or "power_plan" not in assignment:
            continue
        path = f"power_supply_assignment.{group}.power_plan"
        entries = _parse_power_plan(assignment.get("power_plan"), path, error)
        if entries is None:
            continue
        plan_entries[group] = entries
        _validate_plan_routes(entries, supplies, error)

    # 规则 15：同一物理通道不能同时出现在两组计划里。
    dut_entries = plan_entries.get("dut_amplifier")
    driver_entries = plan_entries.get("driver_amplifier")
    if dut_entries and driver_entries:
        dut_routes = {(supply_id, channel) for supply_id, channel, _item in dut_entries}
        for supply_id, channel, item_path in driver_entries:
            route = (supply_id, channel)
            if route in dut_routes:
                error(
                    item_path,
                    f"同一物理通道不能同时用于 DUT 与驱动功放: "
                    f"{supply_id}/{channel}",
                )

    # 规则 7：驱放计划与 enabled 一致。空计划表示驱放自带供电，是合法状态。
    driver_mode = config.get("driver_mode")
    driver_mode = driver_mode if isinstance(driver_mode, Mapping) else {}
    driver_enabled = driver_mode.get("enabled")
    if "driver_amplifier" in plan_entries and driver_enabled is not True and driver_entries:
        error(
            "power_supply_assignment.driver_amplifier.power_plan",
            "驱放测试模式关闭时不能配置驱动功放上电计划",
        )

    # 漏极是测量输入，不是上电角色；字段存在时才校验引用。
    dut_config = config.get("dut_config")
    if isinstance(dut_config, Mapping) and "drain_channel" in dut_config:
        drain_value = dut_config.get("drain_channel")
        if drain_value is not None:
            drain_path = "dut_config.drain_channel"
            drain = _parse_plan_route(drain_value, drain_path, error)
            if drain is not None:
                if dut_entries is not None:
                    if drain not in {
                        (supply_id, channel)
                        for supply_id, channel, _item in dut_entries
                    }:
                        error(drain_path, "漏极必须包含在 DUT 上电顺序中")
                else:
                    # 计划尚未写出时没有可比较的顺序；运行校验仍会要求漏极属于 DUT
                    # 计划，这里只保留形状与引用检查。
                    _validate_plan_routes(((drain[0], drain[1], drain_path),), supplies, error)

    return ConfigValidationResult(errors=_unique_issues(errors), warnings=[])


def _plan_routes(
    assignment: Any, supplies: Mapping[str, Any], group: str
) -> Dict[Tuple[str, str], str]:
    """返回一组上电计划实际引用的物理路由：``(supply_id, channel) -> 字段路径``。

    只收集可解析的引用；电源不存在、通道未声明由 :func:`_validate_plan_routes`
    负责报告，这里不重复报错。
    """
    routes: Dict[Tuple[str, str], str] = {}
    plan = assignment.get("power_plan") if isinstance(assignment, Mapping) else None
    if not isinstance(plan, Mapping):
        return routes
    sequence = plan.get("power_sequence")
    if not isinstance(sequence, list):
        return routes
    for index, item in enumerate(sequence):
        if not isinstance(item, Mapping):
            continue
        supply_id = item.get("supply_id")
        channel = item.get("channel")
        if not isinstance(supply_id, str) or not isinstance(channel, str):
            continue
        route = (supply_id.strip(), channel.strip())
        if not route[0] or not route[1]:
            continue
        routes.setdefault(
            route,
            f"power_supply_assignment.{group}.power_plan.power_sequence[{index}]",
        )
    return routes


def _validate_group_plan(
    plans: Mapping[str, Any],
    group: str,
    supplies: Mapping[str, Any],
    error: Any,
) -> Dict[Tuple[str, str], str]:
    """校验一组的 ``power_plan``（形状、引用与应用状态），返回路由表。

    缺失的组或缺失的 ``power_plan`` 按“尚未生成计划”处理，不在这里报错；一旦写了
    计划，就必须满足完整形状与引用规则。
    """
    assignment = plans.get(group)
    if not isinstance(assignment, Mapping) or "power_plan" not in assignment:
        return {}
    path = f"power_supply_assignment.{group}.power_plan"
    entries = _parse_power_plan(assignment.get("power_plan"), path, error)
    if entries is None:
        return {}
    _validate_plan_routes(entries, supplies, error)
    _validate_plan_applied_supplies(entries, supplies, error)
    return _plan_routes(assignment, supplies, group)


def _validate_plan_connection_routes(
    plans: Mapping[str, Any],
    supplies: Mapping[str, Any],
    error: Any,
) -> None:
    """连接校验只报告“照这份计划去连会失败”的引用问题。

    计划形状（缺键、未知字段、重复路由）由 :func:`validate_config` 报；这里只把可解析
    的路由拿去检查电源是否存在/启用、通道是否声明、电源是否已应用，并做跨组冲突比对。
    """
    def ignore(_path: str, _message: str) -> None:
        return None

    for group in ASSIGNMENT_GROUPS:
        assignment = plans.get(group)
        if not isinstance(assignment, Mapping):
            continue
        plan = assignment.get("power_plan")
        if not isinstance(plan, Mapping):
            continue
        path = f"power_supply_assignment.{group}.power_plan"
        entries = _parse_power_plan(plan, path, ignore)
        if not entries:
            continue
        _validate_plan_routes(entries, supplies, error)
        _validate_plan_applied_supplies(entries, supplies, error)
    _validate_plan_conflicts(plans, supplies, error)


def _validate_plan_conflicts(
    plans: Mapping[str, Any],
    supplies: Mapping[str, Any],
    error: Any,
) -> None:
    """同一物理通道不能同时出现在 DUT 与驱动功放的上电计划里。

    这里不重复报告形状错误：调用方（:func:`validate_config` /
    :func:`validate_hardware_connections`）已经或另行报告引用问题；本函数只做跨组比对。
    """
    routes = {
        group: _plan_routes(plans.get(group), supplies, group)
        for group in ASSIGNMENT_GROUPS
    }
    seen: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for group in ASSIGNMENT_GROUPS:
        for route, path in routes[group].items():
            previous = seen.get(route)
            if previous is None:
                seen[route] = (group, path)
            elif previous[0] != group:
                error(
                    path,
                    f"同一物理通道不能同时用于 DUT 与驱动功放: "
                    f"{route[0]}/{route[1]}",
                )


def _parse_power_plan(
    value: Any, path: str, error: Any
) -> Optional[Tuple[Tuple[str, str, str], ...]]:
    """解析 ``power_plan``，返回 ``((supply_id, channel, 字段路径), ...)``。"""
    if not isinstance(value, Mapping):
        error(path, "必须是对象")
        return None
    unknown = {
        key for key in value if key not in {"power_sequence", "inter_channel_delay_s"}
    }
    if unknown:
        error(path, "不能包含未知字段: " + "、".join(sorted(str(key) for key in unknown)))
    # 两个键是计划的固定形状：缺失时不能用空序列 / 0.0 补全，否则一份被写坏的文档会
    # 看起来像“空计划”。形状已无效，顺序语义的比较也不再继续。
    missing = [key for key in ("power_sequence", "inter_channel_delay_s") if key not in value]
    if missing:
        for key in missing:
            error(f"{path}.{key}", "缺少必需字段")
        return None
    sequence = value["power_sequence"]
    entries: List[Tuple[str, str, str]] = []
    if not isinstance(sequence, list):
        error(f"{path}.power_sequence", "必须是列表")
    else:
        seen: set[Tuple[str, str]] = set()
        for index, item in enumerate(sequence):
            item_path = f"{path}.power_sequence[{index}]"
            route = _parse_plan_route(item, item_path, error)
            if route is None:
                continue
            if route in seen:
                error(
                    f"{path}.power_sequence",
                    f"同一物理通道不能重复: {route[0]}/{route[1]}",
                )
                continue
            seen.add(route)
            entries.append((route[0], route[1], item_path))
    delay = value["inter_channel_delay_s"]
    if not _is_finite_number(delay):
        error(f"{path}.inter_channel_delay_s", "必须是有限数值（秒）")
    elif float(delay) < 0 or float(delay) > MAX_INTER_CHANNEL_DELAY_S:
        error(
            f"{path}.inter_channel_delay_s",
            f"必须在 0 到 {MAX_INTER_CHANNEL_DELAY_S:g} 秒之间",
        )
    return tuple(entries)


def _parse_plan_route(value: Any, path: str, error: Any) -> Optional[Tuple[str, str]]:
    """解析一条两段路由；拼接串、额外字段与空白都按非法形状报告。"""
    if not isinstance(value, Mapping):
        error(path, "供电路由必须是 {supply_id, channel} 对象")
        return None
    unknown = {key for key in value if key not in {"supply_id", "channel"}}
    if unknown:
        error(path, "供电路由不能包含额外字段: " + "、".join(sorted(str(key) for key in unknown)))
        return None
    supply_id = value.get("supply_id")
    channel = value.get("channel")
    if (
        not isinstance(supply_id, str)
        or not supply_id.strip()
        or "/" in supply_id
        or not isinstance(channel, str)
        or not channel.strip()
        or "/" in channel
    ):
        error(path, "供电路由必须是非空且不含 '/' 的 supply_id 与 channel")
        return None
    return supply_id.strip(), channel.strip()


def _validate_plan_routes(
    entries: Tuple[Tuple[str, str, str], ...],
    supplies: Mapping[str, Any],
    error: Any,
) -> None:
    """规则 5：计划里的每条路由必须是已登记、已启用电源上已声明的通道。"""
    for supply_id, channel, item_path in entries:
        route = f"{supply_id}/{channel}"
        supply = supplies.get(supply_id)
        if not isinstance(supply, Mapping):
            error(item_path, f"上电顺序引用了不存在的电源 '{supply_id}'")
            continue
        if supply.get("enabled", True) is not True:
            error(item_path, f"上电顺序引用的电源 '{supply_id}' 未启用")
        channels = supply.get("channels")
        if not isinstance(channels, Mapping) or not isinstance(channels.get(channel), Mapping):
            error(item_path, f"上电顺序引用的通道 '{route}' 不存在于电源 '{supply_id}'")


def _has_applied_address(supply: Any) -> bool:
    """电源条目是否代表一台本次已应用、可连接的设备（有非空地址即视为已应用）。"""
    if not isinstance(supply, Mapping):
        return False
    address = supply.get("address")
    return isinstance(address, str) and bool(address.strip())


def _validate_plan_applied_supplies(
    entries: Tuple[Tuple[str, str, str], ...],
    supplies: Mapping[str, Any],
    error: Any,
) -> None:
    """上电计划引用的电源必须是本次已应用、可连接的设备。

    “已应用”在编写文档里的唯一证据是该电源条目带有连接地址：设备绑定写入时会为
    已绑定设备写入当前地址，并把未绑定条目的地址清空、显式标记为未启用。计划是唯一
    的电源引用来源，因此这条判定只沿着计划里的 ``supply_id`` 检查。
    """
    for supply_id, _channel, item_path in entries:
        supply = supplies.get(supply_id)
        if not isinstance(supply, Mapping):
            continue
        if not _has_applied_address(supply):
            error(
                item_path,
                f"上电计划引用的电源 '{supply_id}' 没有已应用的设备，不能保存或运行",
            )


def simulated_binding_issues(document: Any) -> List[ConfigIssue]:
    """返回“模拟扫描写入的设备绑定”问题列表。

    模拟扫描只用于没有设备网络的开发机；它产生的地址与完整设备码不是现场事实。这样的
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
                "并应用后再创建运行",
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
