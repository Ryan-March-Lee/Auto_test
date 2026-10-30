"""硬件装配计划：把配置解析成一份显式的、只读的设备构造计划。

本模块只**读配置**、只**做校验**，不打开任何 VISA 资源、不构造驱动、不认识会话：
地址重复、角色缺失/重复、通道不存在、引用歧义都在这里失败，因此调用方可以在连接
任何仪器之前拿到一份完整的构造计划（:class:`HardwareAssemblyPlan`），再由
:mod:`instrument.measurement_factory` 按计划逐台打开设备。

计划的边界是“配置 schema 在这里结束”：计划只携带构造所需的显式事实（仪器地址与
能力参数、每台电源的地址/短标签/身份字段/声明通道、`gate`/`drain` 路由 token、
日志文本与急停顺序），**不再带原始配置段**。装配方因此不必回读配置，配置字段的
含义只有一个解释处。

供电分配的引用解析、角色校验与 token 拼装属于纯路由计算，在
:mod:`instrument.power_routing` 里完成；本模块把它的结果与仪器身份组装成计划。

硬件路径只使用配置里显式声明的仪器地址、电源角色与物理通道：每个被分配的电源通道
都必须声明自己的 ``gate``/``drain`` 角色。自动发现电源留在硬件 smoke 入口
（:mod:`hardware.power_supply_discovery`），生产装配永不使用。

电源是**动态集合**：``instruments.power_supplies`` 以 ``supply_id`` 为键（现场短标签，
如 ``DP8B1519``），每条物理路由都是显式的 ``supply_id/CHx`` token。计划按实际被引用的
电源逐台解析，1 台、2 台或 5 台都不需要改代码；``test_instrument_routing`` 禁止把
``CH1``/``CH2`` 写成装配常量，因此电源驱动型号与通道查询写法留在构造计划的使用方，
不在逐台电源的循环里。

计划里同时带三类文本，各司其职、互不参与路由：

- ``SupplyPlan.label``：现场短标签，只用于显示与错误消息；
- ``channel_labels``：每个路由 token（``supply_id/CHx``）到日志写法（``短标签/CHx``）
  的映射，在拼 token 的同一处生成，调用方不必也不许从 token 反解析；
- ``SupplyPlan.identity_text``：同一台电源的详情/审计文本（短标签 · 型号 · 完整序列号 ·
  设备键），身份核对日志直接用它，不再从配置里各挑各的字段。
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

from domain.instruments.labels import supply_identity_text

from .power_routing import (
    ChannelRouting,
    SupplyKeyResolution,
    assigned_supply_references,
    declared_channels,
    resolve_channel_routing,
    resolve_supply_keys,
)

__all__ = [
    "HardwareAssemblyPlan",
    "SignalPlan",
    "SpectrumPlan",
    "SupplyPlan",
    "hardware_assembly_plan",
]


@dataclass(frozen=True)
class SignalPlan:
    """信号源：构造驱动所需的地址与能力范围，配置字段的处理到此为止。"""

    address: str
    min_frequency_hz: Any
    max_frequency_hz: Any
    min_power_dbm: Any
    max_power_dbm: Any


@dataclass(frozen=True)
class SpectrumPlan:
    """频谱仪：构造驱动只需要地址。"""

    address: str


@dataclass(frozen=True)
class SupplyPlan:
    """一台被引用的电源：构造、身份核对与初始输出检查所需的全部事实。

    ``declared_channels`` 是这台电源在配置里声明的**全部**通道（不只是本次被分配
    的那些）：初始输出检查要覆盖它们，任何通道的输出没关都不允许进入测量。
    """

    supply_id: str
    address: str
    label: str
    serial: Any
    identity_text: str
    declared_channels: tuple[str, ...]


@dataclass(frozen=True)
class HardwareAssemblyPlan:
    """一份完整的硬件装配计划：构造方只读使用，不改计划也不回读配置。"""

    signal: SignalPlan
    spectrum: SpectrumPlan
    supplies: tuple[SupplyPlan, ...]
    dut_channels: Mapping[str, str]
    driver_channels: Mapping[str, str]
    power_settings: Mapping[str, Mapping[str, float]]
    all_channels: Mapping[str, str]
    channel_labels: Mapping[str, str]
    emergency_channels: tuple[tuple[int, str], ...]

    @property
    def supply_labels(self) -> dict[str, str]:
        """路由键（``supply_id``）到现场短标签的映射，只用于显示与错误消息。"""
        return {supply.supply_id: supply.label for supply in self.supplies}


def hardware_assembly_plan(config: Mapping[str, Any]) -> HardwareAssemblyPlan:
    """Validate the hardware assembly and return the explicit construction plan.

    该函数只读取配置，不创建 VISA 资源；地址重复、角色缺失/重复、通道不存在、
    引用歧义都在连接任何仪器之前失败。返回的是一份只读计划：装配方按 ``supplies``
    的顺序逐台打开电源，急停顺序里的电源下标与这个顺序一致。
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
    enabled_supply_ids = []
    for name, item in power_configs.items():
        if not isinstance(item, Mapping):
            raise ValueError(f"生产电源 {name} 必须配置对象")
        if item.get("enabled", True):
            enabled_supply_ids.append(name)

    assignments = _mapping_section(config, "power_supply_assignment")
    dut_assignment = _mapping_section(
        assignments, "dut_amplifier", "power_supply_assignment", optional=True
    )
    driver_assignment = _mapping_section(
        assignments, "driver_amplifier", "power_supply_assignment", optional=True
    )
    references = assigned_supply_references(dut_assignment, driver_assignment)
    if not enabled_supply_ids and not references:
        raise ValueError("硬件测量路径至少需要一个电源模板配置")
    if not references:
        raise ValueError("生产硬件路径必须显式配置电源角色和通道")

    routed = resolve_supply_keys(power_configs, references, enabled_supply_ids)
    _reject_duplicate_labels(routed.supply_labels)
    supply_addresses = _checked_supply_addresses(
        routed, references, power_configs, signal_address, spectrum_address
    )
    supplies = tuple(
        _supply_plan(power_configs, routed, supply_addresses, supply_id)
        for supply_id in routed.supply_ids
    )
    routing = resolve_channel_routing(
        dut_assignment=dut_assignment,
        driver_assignment=driver_assignment,
        power_configs=power_configs,
        resolution=routed,
    )
    return _plan(
        signal_config=signal_config,
        signal_address=signal_address,
        spectrum_address=spectrum_address,
        supplies=supplies,
        routing=routing,
    )


def _plan(
    *,
    signal_config: Mapping[str, Any],
    signal_address: str,
    spectrum_address: str,
    supplies: tuple[SupplyPlan, ...],
    routing: ChannelRouting,
) -> HardwareAssemblyPlan:
    """把已校验的事实装订成只读计划（映射冻结，避免构造方顺手改计划）。"""
    return HardwareAssemblyPlan(
        signal=SignalPlan(
            address=signal_address,
            min_frequency_hz=signal_config.get("min_frequency_hz"),
            max_frequency_hz=signal_config.get("max_frequency_hz"),
            min_power_dbm=signal_config.get("min_power_dbm"),
            max_power_dbm=signal_config.get("max_power_dbm"),
        ),
        spectrum=SpectrumPlan(address=spectrum_address),
        supplies=supplies,
        dut_channels=_readonly(routing.dut_channels),
        driver_channels=_readonly(routing.driver_channels),
        power_settings=_readonly(routing.power_settings),
        all_channels=_readonly(routing.all_channels),
        channel_labels=_readonly(routing.channel_labels),
        emergency_channels=routing.emergency_channels,
    )


def _supply_plan(
    power_configs: Mapping[str, Any],
    routed: SupplyKeyResolution,
    supply_addresses: Mapping[str, str],
    supply_id: str,
) -> SupplyPlan:
    """一台电源的构造事实：地址、短标签、身份字段、审计文本与声明通道。"""
    power_config = power_configs[supply_id]
    label = routed.supply_labels[supply_id]
    return SupplyPlan(
        supply_id=supply_id,
        address=supply_addresses[supply_id],
        label=label,
        serial=power_config.get("serial"),
        identity_text=supply_identity_text(
            label,
            power_config.get("model"),
            power_config.get("serial"),
            power_config.get("device_key"),
        ),
        declared_channels=tuple(declared_channels(power_config, supply_id)),
    )


def _checked_supply_addresses(
    routed: SupplyKeyResolution,
    references: tuple[str, ...],
    power_configs: Mapping[str, Any],
    signal_address: str,
    spectrum_address: str,
) -> dict[str, str]:
    """逐条引用校验电源地址：不许与信号源、频谱仪或另一台电源重复。"""
    addresses: dict[str, str] = {}
    for reference in references:
        supply_id = routed.supply_keys[reference]
        label = routed.supply_labels[supply_id]
        address = _required_address(power_configs[supply_id], f"生产电源 {label}")
        if address == signal_address:
            raise ValueError(f"生产电源 {label} 与信号源地址重复: {address}")
        if address == spectrum_address:
            raise ValueError(f"生产电源 {label} 与频谱仪地址重复: {address}")
        for other_id, other_address in addresses.items():
            if address == other_address:
                raise ValueError(
                    f"生产电源 {label} 与"
                    f"生产电源 {routed.supply_labels[other_id]} 地址重复: {address}"
                )
        addresses[supply_id] = address
    return addresses


def _reject_duplicate_labels(supply_labels: Mapping[str, str]) -> None:
    """同一个显示名对应两台电源时直接拒绝。

    路由本身仍然正确，但日志与错误会指向错误的设备编号；与其让操作者认错电源，
    不如在装配前失败。
    """
    seen: set[str] = set()
    duplicated: set[str] = set()
    for label in supply_labels.values():
        if label in seen:
            duplicated.add(label)
        seen.add(label)
    if duplicated:
        raise ValueError("生产电源短标签重复: " + ", ".join(sorted(duplicated)))


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


def _required_address(instrument_config: Mapping[str, Any], label: str) -> str:
    address = instrument_config.get("address")
    if not isinstance(address, str) or not address.strip():
        raise ValueError(f"{label} 必须配置 address")
    return address.strip()


def _readonly(mapping: Mapping[str, Any]) -> Mapping[str, Any]:
    """只读视图：计划被构造方共享，读得到、改不了。"""
    return MappingProxyType(
        {
            key: _readonly(value) if isinstance(value, Mapping) else value
            for key, value in mapping.items()
        }
    )
