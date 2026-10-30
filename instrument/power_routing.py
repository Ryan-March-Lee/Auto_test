"""供电分配解析：把配置里的电源引用解析成显式的 ``supply_id/CHx`` 路由。

本模块只**读配置**、只**算路由**，是纯计算：不打开 VISA 资源、不构造驱动、不认识
会话；仪器地址、启用状态与仪器身份由 :mod:`instrument.assembly_plan` 在同一份配置上
校验，本模块只在“已启用的电源键”这一前提上工作。它回答两个问题：

- 分配里写的那台电源到底是谁（:func:`resolve_supply_keys`）：``supply_id`` 就是
  ``instruments.power_supplies`` 的集合键；旧文件仍可能写 ``label``（迁移前的显示名）
  或残留的 ``visual`` 槽位（``PS1`` 等），别名必须唯一指向一台**已启用**的电源。
  任何歧义都明确失败——按位置或按数量挑一台，等于把电接到不知道哪台设备上。
- 被引用的通道承什么角色、走哪个物理 token（:func:`resolve_channel_routing`）：
  角色只认通道上显式声明的 ``gate``/``drain``；物理 token 只由集合键拼成，短标签是
  显示文本，可能被改动或与键不同，绝不进入被路由的通道值。

两个阶段都是纯函数：同一份配置总得到同一份路由，因此可以在连接任何仪器之前单独
测试，也可以在任何资源被创建之前失败。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from domain.configuration.rules import assignment_supply_id
from domain.instruments.labels import supply_channel_text

# 生产供电只接受配置中显式声明的角色；不再按 CH1/CH2 推导。
POWER_ROLES = ("gate", "drain")


@dataclass(frozen=True)
class SupplyKeyResolution:
    """电源引用解析结果：引用到集合键、参与装配的电源键与它们的短标签。"""

    supply_keys: Mapping[str, str]
    supply_ids: tuple[str, ...]
    supply_labels: Mapping[str, str]


@dataclass(frozen=True)
class ChannelRouting:
    """被引用通道的路由：角色到 token、电压/电流设置、日志写法与急停顺序。"""

    dut_channels: Mapping[str, str]
    driver_channels: Mapping[str, str]
    power_settings: Mapping[str, Mapping[str, float]]
    all_channels: Mapping[str, str]
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
    """一台电源在配置里声明的通道（角色校验与初始输出检查共用这一份判据）。"""
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
    """把 DUT 与驱动功放的分配解析成角色 token、设置、日志写法与急停顺序。"""
    dut_channels, dut_settings, dut_channel_labels = _assigned_channels(
        dut_assignment, power_configs, resolution.supply_keys, resolution.supply_labels, "DUT"
    )
    driver_channels, driver_settings, driver_channel_labels = _assigned_channels(
        driver_assignment, power_configs, resolution.supply_keys, resolution.supply_labels, "驱动"
    )
    overlap = set(dut_channels.values()) & set(driver_channels.values())
    if overlap:
        raise ValueError(
            "DUT 与驱动功放占用了同一个电源通道: " + ", ".join(sorted(overlap))
        )
    return ChannelRouting(
        dut_channels=dut_channels,
        driver_channels=driver_channels,
        power_settings={**driver_settings, **dut_settings},
        all_channels={**driver_channels, **dut_channels},
        # 路由 token -> 日志里的 ``短标签/CHx``：路由值不变，只是给日志与错误一份可读写法。
        channel_labels={**driver_channel_labels, **dut_channel_labels},
        emergency_channels=_emergency_channels(
            resolution.supply_ids, driver_channels, dut_channels
        ),
    )


def _supply_assignments(assignment: Mapping[str, Any]) -> Mapping[str, Any]:
    supplies = assignment.get("supplies", {})
    if not isinstance(supplies, Mapping):
        raise ValueError("供电分配必须提供 supplies 对象")
    return supplies


def _assigned_channels(
    assignment: Mapping[str, Any],
    power_configs: Mapping[str, Any],
    supply_keys: Mapping[str, str],
    supply_labels: Mapping[str, str],
    label: str,
) -> tuple[dict[str, str], dict[str, dict[str, float]], dict[str, str]]:
    """Resolve assigned logical roles to explicit ``supply_id/channel`` tokens.

    ``supply_keys`` 把分配里的引用（``supply_id`` / ``label`` / 旧槽位）解析成
    ``power_supplies`` 的集合键。物理路由的 token 只由集合键拼成：短标签是显示
    文本，可能被改动或与键不同，绝不能出现在被路由的通道值里。错误消息用短标签。

    第三个返回值是同一批 token 的**日志写法**（``短标签/CHx``）。它在拼 token 的同一处
    生成，因为这里同时握着集合键与短标签两个来源；调用方不必、也不许从 token 反解析。
    """
    channels: dict[str, str] = {}
    settings: dict[str, dict[str, float]] = {}
    channel_labels: dict[str, str] = {}
    for supply in _supply_assignments(assignment).values():
        if not isinstance(supply, Mapping):
            raise ValueError(f"{label} 供电分配条目必须是对象")
        reference = _assigned_supply_name(supply)
        power_name = supply_keys.get(reference)
        power_config = power_configs.get(power_name)
        if not isinstance(power_config, Mapping):
            raise ValueError(f"供电分配引用了不存在的电源: {reference}")
        power_label = supply_labels.get(power_name, str(power_name))
        configured = declared_channels(power_config, power_name)
        for channel in _channel_list(supply, power_label):
            channel_config = configured.get(channel)
            if not isinstance(channel_config, Mapping):
                raise ValueError(f"供电分配引用了不存在的通道: {power_label}/{channel}")
            token = f"{power_name}/{channel}"
            channel_labels[token] = supply_channel_text(power_name, channel, power_label)
            role = _explicit_role(channel_config, token)
            if role in channels and channels[role] != token:
                raise ValueError(
                    f"供电角色重复配置: {role} 同时出现在 {channels[role]} 和 {token}"
                )
            channels[role] = token
            settings[role] = _channel_settings(channel_config, token)
    if channels and set(channels) != {"gate", "drain"}:
        raise ValueError(f"{label} 电源必须同时配置 gate 和 drain 通道")
    return channels, settings, channel_labels


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


def _explicit_role(channel_config: Mapping[str, Any], token: str) -> str:
    role = channel_config.get("role")
    normalized = role.strip().lower() if isinstance(role, str) else ""
    if normalized not in POWER_ROLES:
        raise ValueError(f"电源通道未配置显式 gate/drain 角色: {token}")
    return normalized


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
    *role_groups: Mapping[str, str],
) -> tuple[tuple[int, str], ...]:
    """Return the outputs a shutdown must switch off, drain before gate.

    Only tokens assigned to a declared role are returned: those are the outputs
    the port can energize, and they are the only channels the power drivers
    support.  Order follows the safe off sequence (``drain`` before ``gate``)
    instead of the order channels appear in the configuration.  The first tuple
    item is the supply index, matching the order drivers are created in.
    """
    positions = {name: index for index, name in enumerate(supply_ids)}
    ordered: list[str] = []
    for role in ("drain", "gate"):
        for group in role_groups:
            token = group.get(role)
            if token is not None and token not in ordered:
                ordered.append(token)
    return tuple((positions[token.split("/", 1)[0]], token) for token in ordered)


__all__ = [
    "POWER_ROLES",
    "ChannelRouting",
    "SupplyKeyResolution",
    "assigned_supply_references",
    "declared_channels",
    "resolve_channel_routing",
    "resolve_supply_keys",
    "supply_short_label",
]
