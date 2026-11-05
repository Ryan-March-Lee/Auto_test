"""供电分配解析：把配置里的电源引用解析成有序的物理通道计划。

本模块只**读配置**、只**算路由**，是纯计算：不打开 VISA 资源、不构造驱动、不认识
会话；仪器地址、启用状态与仪器身份由 :mod:`instrument.assembly_plan` 在同一份配置上
校验。它回答两个问题：

- 分配里写的那台电源到底是谁（:func:`resolve_supply_keys`）：``supply_id`` 就是
  ``instruments.power_supplies`` 的集合键；旧文件仍可能写 ``label``（迁移前的显示名）
  或残留的 ``visual`` 槽位（``PS1`` 等），别名必须唯一指向一台**已启用**的电源。
  任何歧义都明确失败——按位置或按数量挑一台，等于把电接到不知道哪台设备上。
- 被引用的通道按什么顺序上电（:func:`resolve_channel_routing`）：顺序只来自用户
  计划 ``power_plan.power_sequence``；没有计划键的旧文件按分配里 ``channel`` 列表的
  声明顺序补齐。通道上残留的 ``role`` 字段既不是顺序来源，也不是选择条件。

两个阶段都是纯函数：同一份配置总得到同一份计划，因此可以在连接任何仪器之前单独
测试，也可以在任何资源被创建之前失败。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from domain.configuration.rules import assignment_supply_id
from domain.instruments.labels import supply_channel_text

from .power_control import PowerChannelStep, PowerGroupPlan


@dataclass(frozen=True)
class SupplyKeyResolution:
    """电源引用解析结果：引用到集合键、参与装配的电源键与它们的短标签。"""

    supply_keys: Mapping[str, str]
    supply_ids: tuple[str, ...]
    supply_labels: Mapping[str, str]


@dataclass(frozen=True)
class ChannelRouting:
    """两组通道计划、日志写法与急停顺序。"""

    dut_power_plan: PowerGroupPlan
    driver_power_plan: PowerGroupPlan
    all_channels: tuple[str, ...]
    channel_labels: Mapping[str, str]
    emergency_channels: tuple[tuple[int, str], ...]


def supply_short_label(label: Any, fallback: Any) -> str:
    """电源的现场短标签：配置里的 ``label`` 优先，没有时才用集合键。

    短标签只用于显示与错误消息（装配计划与日志都从这里取），不参与路由；旧文件的
    引用别名也按它比对。
    """
    if isinstance(label, str) and label.strip():
        return label.strip()
    return str(fallback)


def assigned_supply_references(*assignments: Mapping[str, Any]) -> tuple[str, ...]:
    """分配里出现的电源引用，原样保留、按字典序去重。

    引用判据与配置页、文件校验共用 ``domain.configuration.rules.assignment_supply_id``：
    ``supply_id`` 为准，只有完全没有该字段的旧记录才回落读一次 ``name``。
    """
    references: set[str] = set()
    for assignment in assignments:
        for supply in _supply_assignments(assignment).values():
            if not isinstance(supply, Mapping):
                raise ValueError("供电分配条目必须是对象")
            name = _assigned_supply_name(supply)
            if name is None:
                continue
            references.add(name)
    return tuple(sorted(references))


def declared_channels(power_config: Mapping[str, Any], power_name: str) -> Mapping[str, Any]:
    """一台电源在配置里声明的通道（校验与初始输出检查共用这一份判据）。"""
    channels = power_config.get("channels")
    if not isinstance(channels, Mapping) or not channels:
        raise ValueError(f"生产电源 {power_name} 必须配置非空通道对象")
    return channels


def resolve_supply_keys(
    power_configs: Mapping[str, Any],
    references: Iterable[str],
    enabled_supply_ids: Sequence[str],
) -> SupplyKeyResolution:
    """把分配里的电源引用解析成 ``power_supplies`` 的集合键，并给出参与装配的电源。

    ``supply_ids`` 按字典序排列：装配方按这个顺序逐台打开电源，急停顺序里的电源
    下标也以它为准，计划里因此不必再排一次。
    """
    visual_names = _supply_visual_names(power_configs)
    supply_keys: dict[str, str] = {}
    supply_labels: dict[str, str] = {}
    for reference in sorted(references):
        supply_id = _resolve_supply_key(reference, enabled_supply_ids, power_configs, visual_names)
        supply_keys[reference] = supply_id
        supply_labels[supply_id] = supply_short_label(
            power_configs[supply_id].get("label"), supply_id
        )
    return SupplyKeyResolution(
        supply_keys=supply_keys,
        supply_ids=tuple(sorted(supply_labels)),
        supply_labels=supply_labels,
    )


def resolve_channel_routing(
    *,
    dut_assignment: Mapping[str, Any],
    driver_assignment: Mapping[str, Any],
    power_configs: Mapping[str, Any],
    resolution: SupplyKeyResolution,
) -> ChannelRouting:
    """把 DUT 与驱动功放的分配解析成有序通道计划、日志写法与急停顺序。"""
    dut_plan, dut_channel_labels = _group_plan(
        dut_assignment, power_configs, resolution, "DUT"
    )
    driver_plan, driver_channel_labels = _group_plan(
        driver_assignment, power_configs, resolution, "驱动"
    )
    overlap = set(dut_plan.tokens) & set(driver_plan.tokens)
    if overlap:
        raise ValueError(
            "DUT 与驱动功放占用了同一个电源通道: " + ", ".join(sorted(overlap))
        )
    # 全局上电顺序：先驱动功放、后 DUT；急停与收尾按这个顺序的逆序执行。
    all_channels = driver_plan.tokens + dut_plan.tokens
    return ChannelRouting(
        dut_power_plan=dut_plan,
        driver_power_plan=driver_plan,
        all_channels=all_channels,
        # 路由 token -> 日志里的 ``短标签/CHx``：路由值不变，只是给日志与错误一份可读写法。
        channel_labels={**driver_channel_labels, **dut_channel_labels},
        emergency_channels=_emergency_channels(
            resolution.supply_ids, driver_plan, dut_plan
        ),
    )


def _group_plan(
    assignment: Mapping[str, Any],
    power_configs: Mapping[str, Any],
    resolution: SupplyKeyResolution,
    label: str,
) -> tuple[PowerGroupPlan, dict[str, str]]:
    """解析一组的计划；有 ``power_plan`` 用用户顺序，没有才按声明顺序补齐。"""
    assignment = assignment if isinstance(assignment, Mapping) else {}
    labels: dict[str, str] = {}
    declared_entries = _assignment_entries(
        assignment, power_configs, resolution, label, labels
    )
    if "power_plan" in assignment:
        entries, delay = _planned_entries(
            assignment.get("power_plan"), power_configs, resolution, label, labels
        )
        declared_routes = {(name, channel) for name, channel, _settings in declared_entries}
        planned_routes = {(name, channel) for name, channel, _settings in entries}
        if planned_routes != declared_routes:
            raise ValueError(
                f"{label} 上电顺序必须恰好包含该组分配的全部物理通道"
            )
    else:
        # 旧配置没有计划键：按分配里 channel 列表的声明顺序建立计划。角色字段不参与。
        entries, delay = declared_entries, 0.0
    steps = tuple(
        PowerChannelStep(channel=f"{name}/{channel}", **settings)
        for name, channel, settings in entries
    )
    return PowerGroupPlan(channels=steps, inter_channel_delay_s=delay), labels


def _assignment_entries(
    assignment: Mapping[str, Any],
    power_configs: Mapping[str, Any],
    resolution: SupplyKeyResolution,
    label: str,
    labels: dict[str, str],
) -> list[tuple[str, str, dict[str, float]]]:
    """按分配声明顺序展开通道，作为旧文件顺序与计划完整性校验的共同判据。"""
    entries: list[tuple[str, str, dict[str, float]]] = []
    for supply in _supply_assignments(assignment).values():
        if not isinstance(supply, Mapping):
            raise ValueError(f"{label} 供电分配条目必须是对象")
        reference = _assigned_supply_name(supply)
        power_name = resolution.supply_keys.get(reference)
        if power_name is None:
            raise ValueError(f"供电分配引用了不存在的电源: {reference}")
        power_config = power_configs.get(power_name)
        if not isinstance(power_config, Mapping):
            raise ValueError(f"供电分配引用了不存在的电源: {reference}")
        power_label = resolution.supply_labels.get(power_name, str(power_name))
        configured = declared_channels(power_config, power_name)
        for channel in _channel_list(supply, power_label):
            entries.append(
                _entry(
                    power_name,
                    power_label,
                    channel,
                    configured,
                    labels,
                )
            )
    return entries


def _planned_entries(
    plan: Any,
    power_configs: Mapping[str, Any],
    resolution: SupplyKeyResolution,
    label: str,
    labels: dict[str, str],
) -> tuple[list[tuple[str, str, dict[str, float]]], float]:
    """读取用户计划：``power_sequence`` 的顺序就是上电顺序。"""
    if not isinstance(plan, Mapping):
        raise ValueError(f"{label} 上电计划必须是对象")
    missing = [key for key in ("power_sequence", "inter_channel_delay_s") if key not in plan]
    if missing:
        raise ValueError(f"{label} 上电计划缺少字段: " + ", ".join(missing))
    sequence = plan.get("power_sequence")
    if not isinstance(sequence, list):
        raise ValueError(f"{label} 上电计划的 power_sequence 必须是列表")
    entries: list[tuple[str, str, dict[str, float]]] = []
    for index, item in enumerate(sequence):
        if not isinstance(item, Mapping):
            raise ValueError(f"{label} 上电计划[{index}] 必须是 {{supply_id, channel}} 对象")
        reference = item.get("supply_id")
        channel = item.get("channel")
        if not isinstance(reference, str) or not reference.strip():
            raise ValueError(f"{label} 上电计划[{index}] 缺少 supply_id")
        if not isinstance(channel, str) or not channel.strip():
            raise ValueError(f"{label} 上电计划[{index}] 缺少 channel")
        power_name = _plan_supply_key(reference, resolution, label, index)
        power_config = power_configs.get(power_name)
        if not isinstance(power_config, Mapping):
            raise ValueError(f"上电计划引用了不存在的电源: {reference}")
        power_label = resolution.supply_labels.get(power_name, str(power_name))
        configured = declared_channels(power_config, power_name)
        entries.append(
            _entry(
                power_name,
                power_label,
                channel.strip(),
                configured,
                labels,
            )
        )
    delay = plan.get("inter_channel_delay_s")
    return entries, delay


def _plan_supply_key(
    reference: str,
    resolution: SupplyKeyResolution,
    label: str,
    index: int,
) -> str:
    """计划里的 ``supply_id`` 必须是本次参与装配的电源键；别名同样唯一解析。"""
    if reference in resolution.supply_keys:
        return resolution.supply_keys[reference]
    if reference in resolution.supply_ids:
        return reference
    raise ValueError(
        f"{label} 上电计划[{index}] 引用了未参与本次分配的电源: {reference}"
    )


def _entry(
    power_name: str,
    power_label: str,
    channel: str,
    configured: Mapping[str, Any],
    labels: dict[str, str],
) -> tuple[str, str, dict[str, float]]:
    channel_config = configured.get(channel)
    if not isinstance(channel_config, Mapping):
        raise ValueError(f"供电分配引用了不存在的通道: {power_label}/{channel}")
    token = f"{power_name}/{channel}"
    labels[token] = supply_channel_text(power_name, channel, power_label)
    return power_name, channel, _channel_settings(channel_config, token)


def _supply_assignments(assignment: Mapping[str, Any]) -> Mapping[str, Any]:
    supplies = assignment.get("supplies", {})
    if not isinstance(supplies, Mapping):
        raise ValueError("供电分配必须提供 supplies 对象")
    return supplies


def _assigned_supply_name(supply: Mapping[str, Any]) -> str | None:
    """供电分配引用的电源名，原样返回（不裁剪、不猜测）。

    ``domain.configuration.rules.assignment_supply_id`` 是引用判据的唯一实现，
    这里只把它返回值再次收紧成“非空字符串”，避免非字符串进入集合键。
    """
    name = assignment_supply_id(supply)
    if not isinstance(name, str) or not name.strip():
        return None
    return name


def _resolve_supply_key(
    reference: str,
    enabled_ids: Sequence[str],
    power_configs: Mapping[str, Any],
    visual_names: Mapping[str, list[str]],
) -> str:
    """把一条分配引用解析成 ``power_supplies`` 的集合键。

    ``supply_id`` 就是集合键，直接命中。旧文件仍可能写 ``label``（迁移前的显示名）
    或 ``visual`` 槽位（``PS1`` 等）：它们必须唯一指向一台电源，且这台电源必须已
    启用、身份自洽。

    歧义判定不只看“已启用的候选”：一个名字既是某台的集合键、又是另一台的
    ``label`` 时，两台都没启用也不能悄悄挑一台。因此命中集合键时仍要求名字类
    别名不再指向**其它**电源。
    """
    if reference in power_configs:
        aliases = [
            name
            for name in _alias_supply_keys(reference, power_configs, visual_names)
            if name != reference
        ]
        if aliases:
            raise ValueError(
                f"供电分配引用的电源名 {reference} 同时是另一台电源的 label/visual: "
                + ", ".join(
                    supply_short_label(power_configs[name].get("label"), name) for name in aliases
                )
                + f"；请改写成 supply_id {reference} 之外的唯一写法"
            )
        key = reference
    else:
        candidates = _alias_supply_keys(reference, power_configs, visual_names)
        if not candidates:
            raise ValueError(f"供电分配引用了不存在的电源: {reference}")
        if len(candidates) > 1:
            raise ValueError(
                f"供电分配引用的电源名 {reference} 对应多台电源: "
                + ", ".join(sorted(candidates))
                + "；请改写成各自的 supply_id"
            )
        key = candidates[0]
    if key not in enabled_ids:
        raise ValueError(f"供电分配引用了未启用的电源: {reference}")
    return key


def _alias_supply_keys(
    reference: str,
    power_configs: Mapping[str, Any],
    visual_names: Mapping[str, list[str]],
) -> list[str]:
    """按 ``label`` 与旧 ``visual`` 槽位找出引用可能指向的电源键。"""
    matched = [
        name
        for name, config in power_configs.items()
        if isinstance(config, Mapping)
        and supply_short_label(config.get("label"), name) == reference
    ]
    matched.extend(visual_names.get(reference, ()))
    return list(dict.fromkeys(matched))


def _supply_visual_names(power_configs: Mapping[str, Any]) -> dict[str, list[str]]:
    """旧文件里残留的 ``visual`` 槽位名到电源键的索引（只用于引用解析）。"""
    index: dict[str, list[str]] = {}
    for name, config in power_configs.items():
        if not isinstance(config, Mapping):
            continue
        visual = config.get("visual")
        if isinstance(visual, str) and visual.strip():
            index.setdefault(visual.strip(), []).append(name)
    return index


def _channel_list(supply: Mapping[str, Any], power_label: str) -> list[str]:
    channels = supply.get("channel")
    if not isinstance(channels, list) or not channels:
        raise ValueError(f"供电分配必须提供非空通道列表: {power_label}")
    names = [channel for channel in channels if isinstance(channel, str)]
    if len(names) != len(channels):
        raise ValueError(f"供电分配的通道必须是字符串: {power_label}")
    return names


def _channel_settings(channel_config: Mapping[str, Any], token: str) -> dict[str, float]:
    try:
        return {
            "voltage_v": float(channel_config["voltage"]["value"]),
            "current_a": float(channel_config["current"]["value"]),
        }
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"电源通道缺少有效的电压/电流设置: {token}") from error


def _emergency_channels(
    supply_ids: Sequence[str],
    driver_plan: PowerGroupPlan,
    dut_plan: PowerGroupPlan,
) -> tuple[tuple[int, str], ...]:
    """Return the outputs an emergency shutdown must switch off.

    顺序是全局上电顺序的逆序（先 DUT、再驱动功放；组内也逆序），只覆盖计划里的
    通道。第一个元素是电源在装配顺序里的下标，与驱动创建顺序一致。
    """
    positions = {name: index for index, name in enumerate(supply_ids)}
    ordered: list[str] = []
    # 全局上电顺序是驱动功放 -> DUT；逆序因此是 DUT 逆序 -> 驱动功放逆序。
    for token in reversed(driver_plan.tokens + dut_plan.tokens):
        if token not in ordered:
            ordered.append(token)
    return tuple((positions[token.split("/", 1)[0]], token) for token in ordered)


__all__ = [
    "ChannelRouting",
    "SupplyKeyResolution",
    "assigned_supply_references",
    "declared_channels",
    "resolve_channel_routing",
    "resolve_supply_keys",
    "supply_short_label",
]
