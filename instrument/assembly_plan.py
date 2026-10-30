"""硬件装配计划：把配置解析成一份显式的设备构造计划。

本模块只**读配置**、只**做校验**，不打开任何 VISA 资源、不构造驱动、不认识会话：
地址重复、角色缺失/重复、通道不存在、引用歧义都在这里失败，因此调用方可以在连接
任何仪器之前拿到一份完整的构造计划（:func:`hardware_assembly_plan` 的返回值），
再由 :mod:`instrument.measurement_factory` 按计划逐台打开设备。

硬件路径只使用配置里显式声明的仪器地址、电源角色与物理通道：每个被分配的电源通道
都必须声明自己的 ``gate``/``drain`` 角色。自动发现电源留在硬件 smoke 入口
（:mod:`hardware.power_supply_discovery`），生产装配永不使用。

电源是**动态集合**：``instruments.power_supplies`` 以 ``supply_id`` 为键（现场短标签，
如 ``DP8B1519``），每条物理路由都是显式的 ``supply_id/CHx`` token。计划按实际被引用的
电源逐台解析，1 台、2 台或 5 台都不需要改代码；``test_instrument_routing`` 禁止把
``CH1``/``CH2`` 写成装配常量，因此电源驱动型号与通道查询写法留在构造计划的使用方，
不在逐台电源的循环里。

计划里同时带三类文本，各司其职、互不参与路由：

- ``supply_labels``：路由键（``supply_id``）到现场短标签的映射，只用于显示与错误消息；
- ``channel_labels``：每个路由 token（``supply_id/CHx``）到日志写法（``短标签/CHx``）
  的映射，在拼 token 的同一处生成，调用方不必也不许从 token 反解析；
- ``supply_details``：同一台电源的详情/审计文本（短标签 · 型号 · 完整序列号 · 设备键），
  逐台电源的身份核对日志直接用它，不再从配置里各挑各的字段。
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from domain.configuration.rules import assignment_supply_id
from domain.instruments.labels import supply_channel_text, supply_identity_text

# 生产供电只接受配置中显式声明的角色；不再按 CH1/CH2 推导。
POWER_ROLES = ("gate", "drain")


def hardware_assembly_plan(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the hardware assembly and return the explicit construction plan.

    该函数只读取配置，不创建 VISA 资源；地址重复、角色缺失/重复、通道不存在
    都在连接任何仪器之前失败。

    返回的 ``supply_labels`` 是路由键（``supply_id``）到现场短标签的映射，只用于显示与
    错误消息；``supply_details`` 是同一台电源的详情/审计文本（短标签 · 型号 · 完整序列号 ·
    设备键），逐台电源的身份核对日志直接用它，不再从配置里各挑各的字段；
    ``channel_labels`` 把每个路由 token（``supply_id/CHx``）映射到日志用的
    ``短标签/CHx``，通道动作日志因此说人话，而路由仍只认 token。
    """
    instruments = _mapping_section(config, "instruments")
    signal_config = _mapping_section(instruments, "signal_generator", "instruments")
    spectrum_config = _mapping_section(instruments, "spectrum_analyzer", "instruments")
    power_configs = _mapping_section(instruments, "power_supplies", "instruments", optional=True)
    if not signal_config.get("enabled", True) or not spectrum_config.get("enabled", True):
        raise ValueError("硬件测量路径要求信号源和频谱仪均已启用")

    signal_address = _required_address(signal_config, "信号源")
    spectrum_address = _required_address(spectrum_config, "频谱仪")
    if signal_address == spectrum_address:
        raise ValueError(f"信号源与频谱仪的 VISA 地址重复: {signal_address}")
    enabled_names = []
    for name, item in power_configs.items():
        if not isinstance(item, Mapping):
            raise ValueError(f"生产电源 {name} 必须配置对象")
        if item.get("enabled", True):
            enabled_names.append(name)
    assignments = _mapping_section(config, "power_supply_assignment")
    dut_assignment = _mapping_section(
        assignments, "dut_amplifier", "power_supply_assignment", optional=True
    )
    driver_assignment = _mapping_section(
        assignments, "driver_amplifier", "power_supply_assignment", optional=True
    )
    assigned_names = set()
    for group in (dut_assignment, driver_assignment):
        for supply in _supply_assignments(group).values():
            if not isinstance(supply, Mapping):
                raise ValueError("供电分配条目必须是对象")
            # 分配引用与配置页、文件校验共用同一个判据：``supply_id`` 为准，只有
            # 完全没有该字段的旧记录才回落读一次 ``name``。返回值原样保留，供步骤 13
            # 的迁移器区分“旧槽位名”和“未迁移的引用”。
            name = _assigned_supply_name(supply)
            if name is None:
                continue
            assigned_names.add(name)
    if not enabled_names and not assigned_names:
        raise ValueError("硬件测量路径至少需要一个电源模板配置")
    if not assigned_names:
        raise ValueError("生产硬件路径必须显式配置电源角色和通道")

    # 分组时按 ``supply_id`` 匹配 ``power_supplies`` 的键；同时接受该电源自己的
    # ``label`` 和旧文件残留的 ``visual`` 槽位（``PS1`` 等）。两个来源都必须是
    # 唯一的一台电源且身份一致，绝不按顺序或长度猜。解析在“已启用”检查之前：
    # 别名先落到一台确定的设备上，再判断它有没有被启用。短标签只用于显示，
    # 不参与路由。
    visual_names = _supply_visual_names(power_configs)
    supply_keys = {}
    supply_labels = {}
    for reference in sorted(assigned_names):
        supply_key = _resolve_supply_key(reference, enabled_names, power_configs, visual_names)
        supply_keys[reference] = supply_key
        supply_labels[supply_key] = supply_short_label(
            power_configs[supply_key].get("label"), supply_key
        )
    duplicate_labels = _duplicate_labels(supply_labels)
    if duplicate_labels:
        # 同一个显示名对应两台电源时，日志与错误会指向错误的设备编号，而路由
        # 本身仍然正确。这里直接拒绝，而不是冒险让操作者认错电源。
        raise ValueError(
            "生产电源短标签重复: " + ", ".join(sorted(duplicate_labels))
        )

    # 身份核对日志用的详情文本（短标签 · 型号 · 完整序列号 · 设备键）：在装配计划里算一次，
    # 逐台电源的日志与错误因此共用同一份事实，不各自从配置里挑字段（步骤 14）。
    supply_details = {
        name: supply_identity_text(
            supply_labels[name],
            power_configs[name].get("model"),
            power_configs[name].get("serial"),
            power_configs[name].get("device_key"),
        )
        for name in supply_labels
    }

    supply_addresses = {}
    for reference in sorted(assigned_names):
        power_name = supply_keys[reference]
        power_label = supply_labels[power_name]
        power_config = power_configs[power_name]
        address = _required_address(power_config, f"生产电源 {power_label}")
        if address == signal_address:
            raise ValueError(f"生产电源 {power_label} 与信号源地址重复: {address}")
        if address == spectrum_address:
            raise ValueError(f"生产电源 {power_label} 与频谱仪地址重复: {address}")
        for other_name, other_address in supply_addresses.items():
            if address == other_address:
                raise ValueError(
                    f"生产电源 {power_label} 与"
                    f"生产电源 {supply_labels[other_name]} 地址重复: {address}"
                )
        supply_addresses[power_name] = address

    dut_channels, dut_settings, dut_channel_labels = _assigned_channels(
        dut_assignment, power_configs, supply_keys, supply_labels, "DUT"
    )
    driver_channels, driver_settings, driver_channel_labels = _assigned_channels(
        driver_assignment, power_configs, supply_keys, supply_labels, "驱动"
    )
    overlap = set(dut_channels.values()) & set(driver_channels.values())
    if overlap:
        raise ValueError(
            "DUT 与驱动功放占用了同一个电源通道: " + ", ".join(sorted(overlap))
        )
    supply_names = sorted(supply_labels)
    return {
        "signal_config": signal_config,
        "signal_address": signal_address,
        "spectrum_address": spectrum_address,
        "power_configs": power_configs,
        "supply_names": supply_names,
        "supply_labels": supply_labels,
        "supply_details": supply_details,
        "dut_channels": dut_channels,
        "driver_channels": driver_channels,
        "power_settings": {**driver_settings, **dut_settings},
        "all_channels": {**driver_channels, **dut_channels},
        # 路由 token -> 日志里的 ``短标签/CHx``：路由值不变，只是给日志与错误一份可读写法。
        "channel_labels": {**driver_channel_labels, **dut_channel_labels},
        "emergency_channels": _emergency_channels(
            supply_names, driver_channels, dut_channels
        ),
    }


def supply_short_label(label: Any, fallback: Any) -> str:
    """电源的现场短标签：配置里的 ``label`` 优先，没有时才用集合键。

    显示、审计文本与关闭失败记录都从这里取短标签，保证“同一份配置只有一个显示名”。
    """
    if isinstance(label, str) and label.strip():
        return label.strip()
    return str(fallback)


def _mapping_section(
    parent: Mapping[str, Any], key: str, prefix: str = "", *, optional: bool = False
) -> Mapping[str, Any]:
    section = parent.get(key)
    if section is None and optional:
        return {}
    if not isinstance(section, Mapping):
        path = f"{prefix}.{key}" if prefix else key
        raise ValueError(f"硬件配置缺少对象段: {path}")
    return section


def _supply_assignments(assignment: Mapping[str, Any]) -> Mapping[str, Any]:
    supplies = assignment.get("supplies", {})
    if not isinstance(supplies, Mapping):
        raise ValueError("供电分配必须提供 supplies 对象")
    return supplies


def configured_channels(
    power_config: Mapping[str, Any], power_name: str
) -> Mapping[str, Any]:
    """一台电源在配置里声明的通道（配置校验与初始输出检查共用这一份判据）。"""
    channels = power_config.get("channels")
    if not isinstance(channels, Mapping) or not channels:
        raise ValueError(f"生产电源 {power_name} 必须配置非空通道对象")
    return channels


def _required_address(instrument_config: Mapping[str, Any], label: str) -> str:
    address = instrument_config.get("address")
    if not isinstance(address, str) or not address.strip():
        raise ValueError(f"{label} 必须配置 address")
    return address.strip()


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
    channels = {}
    settings = {}
    channel_labels = {}
    for supply in _supply_assignments(assignment).values():
        if not isinstance(supply, Mapping):
            raise ValueError(f"{label} 供电分配条目必须是对象")
        reference = _assigned_supply_name(supply)
        power_name = supply_keys.get(reference)
        power_config = power_configs.get(power_name)
        if not isinstance(power_config, Mapping):
            raise ValueError(f"供电分配引用了不存在的电源: {reference}")
        power_label = supply_labels.get(power_name, str(power_name))
        declared_channels = configured_channels(power_config, power_name)
        for channel in _channel_list(supply, power_label):
            channel_config = declared_channels.get(channel)
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
    enabled_names: Sequence[str],
    power_configs: Mapping[str, Any],
    visual_names: Mapping[str, list[str]],
) -> str:
    """把分配里的电源引用解析成 ``power_supplies`` 的集合键。

    ``supply_id`` 就是集合键，直接命中。旧文件仍可能写 ``label``（迁移前的显示名）
    或 ``visual`` 槽位（``PS1`` 等）：它们必须唯一指向一台电源，且这台电源必须已
    启用、身份自洽。任何歧义都明确失败——按位置或按数量取一台，等于把电接到
    不知道哪台设备上。

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
    if key not in enabled_names:
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


def _duplicate_labels(supply_labels: Mapping[str, str]) -> set[str]:
    """出现不止一次的短标签：显示名重复时日志无法唯一指向一台电源。"""
    seen: set[str] = set()
    duplicated: set[str] = set()
    for label in supply_labels.values():
        if label in seen:
            duplicated.add(label)
        seen.add(label)
    return duplicated


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
    supply_names: Sequence[str],
    *role_groups: Mapping[str, str],
) -> tuple[tuple[int, str], ...]:
    """Return the outputs a shutdown must switch off, drain before gate.

    Only tokens assigned to a declared role are returned: those are the outputs
    the port can energize, and they are the only channels the power drivers
    support.  Order follows the safe off sequence (``drain`` before ``gate``)
    instead of the order channels appear in the configuration.  The first tuple
    item is the supply index, matching the order drivers are created in.
    """
    positions = {name: index for index, name in enumerate(supply_names)}
    ordered: list[str] = []
    for role in ("drain", "gate"):
        for group in role_groups:
            token = group.get(role)
            if token is not None and token not in ordered:
                ordered.append(token)
    return tuple((positions[token.split("/", 1)[0]], token) for token in ordered)


__all__ = [
    "POWER_ROLES",
    "configured_channels",
    "hardware_assembly_plan",
    "supply_short_label",
]
