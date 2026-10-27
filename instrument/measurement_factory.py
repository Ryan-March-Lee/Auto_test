"""Application-level assembly for hardware and offline measurement ports.

The factory keeps hardware construction out of measurement services.  The
hardware path uses only explicitly configured instrument addresses, power
roles and physical channels: every assigned power channel must declare its
``gate``/``drain`` role in the configuration.  Automatic power-supply
discovery stays in the hardware-smoke entry points
(:mod:`hardware.power_supply_discovery`) and is never used for production
assembly.
"""

from __future__ import annotations

from typing import Any, Mapping

import pyvisa

from .drivers import ScpiPowerSupplyDriver, ScpiSignalGeneratorDriver, ScpiSpectrumAnalyzerDriver
from .measurement_adapter import PortMeasurementAdapter
from .session import ManagedInstrumentSession
from .transport import VisaScpiTransport
from .simulation import (
    SimulatedPowerSupply,
    SimulatedSignalGenerator,
    SimulatedSpectrumAnalyzer,
)

# 生产供电只接受配置中显式声明的角色；不再按 CH1/CH2 推导。
POWER_ROLES = ("gate", "drain")


def create_measurement_port(
    config_path: str | None = None,
    *,
    mode: str = "hardware",
    power_channels: Mapping[str, str] | None = None,
    driver_power_channels: Mapping[str, str] | None = None,
    power_settings: Mapping[str, Mapping[str, float]] | None = None,
    recorder: Any = None,
):
    """Create a service port for hardware or offline simulation mode.

    Hardware construction owns the VISA manager and all resources through the
    returned session.  Validation of addresses, roles and channels happens
    before any VISA resource is opened.
    """
    if mode == "hardware":
        if config_path is None:
            raise ValueError("hardware 模式必须提供 config_path")
        return _create_hardware_measurement_port(config_path, recorder=recorder)
    if mode != "simulation":
        raise ValueError(f"不支持的测量端口模式: {mode}")

    dut_channels = dict(power_channels or {})
    driver_channels = dict(driver_power_channels or {})
    session_channels = dut_channels or {"gate": "A", "drain": "B"}
    session = ManagedInstrumentSession(
        SimulatedSignalGenerator(recorder),
        SimulatedSpectrumAnalyzer(recorder=recorder),
        SimulatedPowerSupply(recorder),
        session_channels,
    )
    session.validate()
    session.connect()
    session.prepare()
    return PortMeasurementAdapter(
        session,
        dut_power_channels=dut_channels,
        driver_power_channels=driver_channels,
        power_settings=power_settings,
    )


class _VisaSession(ManagedInstrumentSession):
    """Session variant that also releases the ResourceManager it owns."""

    def __init__(self, resource_manager, *devices, **kwargs):
        super().__init__(*devices, **kwargs)
        self.resource_manager = resource_manager
        self._resource_manager_closed = False

    def close(self, *args, **kwargs):
        # Keep the manager alive when device cleanup fails: SafetyInstrument-
        # Session deliberately retains failed resources so a later close()
        # can retry them.
        if self.state.value != "cleaned":
            super().close(*args, **kwargs)
        if not self._resource_manager_closed:
            self.resource_manager.close()
            self._resource_manager_closed = True

    @property
    def resources_closed(self) -> bool:
        return self._resource_manager_closed and self.state.value == "cleaned"


class _ConfiguredPowerSupply:
    """Power-supply port that routes explicit ``supply/channel`` tokens.

    Every method mirrors :class:`instrument.ports.PowerSupplyPort`; there is
    no attribute forwarding, so an unknown operation or channel fails at the
    call boundary instead of being routed by method name.
    """

    def __init__(self, supplies: Mapping[str, Any]):
        if not supplies:
            raise ValueError("生产电源路由至少需要一个已配置电源")
        self.supplies = dict(supplies)

    def connect(self, *, timeout_s: float = 10.0) -> None:
        for supply in self.supplies.values():
            supply.connect(timeout_s=timeout_s)

    def close(self, *, timeout_s: float = 5.0) -> None:
        errors = []
        for supply in self.supplies.values():
            try:
                supply.close(timeout_s=timeout_s)
            except Exception as error:
                errors.append(error)
        if errors:
            raise RuntimeError("power supply close failed: " + "; ".join(map(str, errors))) from errors[0]

    def set_voltage_v(self, channel: str, voltage_v: float, *, timeout_s: float = 5.0) -> None:
        supply, physical_channel = self._route(channel)
        supply.set_voltage_v(physical_channel, voltage_v, timeout_s=timeout_s)

    def set_current_limit_a(self, channel: str, current_a: float, *, timeout_s: float = 5.0) -> None:
        supply, physical_channel = self._route(channel)
        supply.set_current_limit_a(physical_channel, current_a, timeout_s=timeout_s)

    def set_voltage_protection_state(
        self, channel: str, enabled: bool, *, timeout_s: float = 5.0
    ) -> None:
        supply, physical_channel = self._route(channel)
        supply.set_voltage_protection_state(physical_channel, enabled, timeout_s=timeout_s)

    def set_current_protection_state(
        self, channel: str, enabled: bool, *, timeout_s: float = 5.0
    ) -> None:
        supply, physical_channel = self._route(channel)
        supply.set_current_protection_state(physical_channel, enabled, timeout_s=timeout_s)

    def set_voltage_protection(
        self, channel: str, voltage_protection_v: float, *, timeout_s: float = 5.0
    ) -> None:
        supply, physical_channel = self._route(channel)
        supply.set_voltage_protection(
            physical_channel, voltage_protection_v, timeout_s=timeout_s
        )

    def set_current_protection(
        self, channel: str, current_protection_a: float, *, timeout_s: float = 5.0
    ) -> None:
        supply, physical_channel = self._route(channel)
        supply.set_current_protection(
            physical_channel, current_protection_a, timeout_s=timeout_s
        )

    def set_output_enabled(self, channel: str, enabled: bool, *, timeout_s: float = 5.0) -> None:
        supply, physical_channel = self._route(channel)
        supply.set_output_enabled(physical_channel, enabled, timeout_s=timeout_s)

    def read_voltage_v(self, channel: str, *, timeout_s: float = 5.0) -> float:
        supply, physical_channel = self._route(channel)
        return supply.read_voltage_v(physical_channel, timeout_s=timeout_s)

    def read_current_a(self, channel: str, *, timeout_s: float = 5.0) -> float:
        supply, physical_channel = self._route(channel)
        return supply.read_current_a(physical_channel, timeout_s=timeout_s)

    def set_voltage(self, channel: str, voltage_v: float, *, timeout_s: float = 5.0) -> None:
        self.set_voltage_v(channel, voltage_v, timeout_s=timeout_s)

    def set_current_limit(self, channel: str, current_a: float, *, timeout_s: float = 5.0) -> None:
        self.set_current_limit_a(channel, current_a, timeout_s=timeout_s)

    def read_voltage(self, channel: str, *, timeout_s: float = 5.0) -> float:
        return self.read_voltage_v(channel, timeout_s=timeout_s)

    def read_current(self, channel: str, *, timeout_s: float = 5.0) -> float:
        return self.read_current_a(channel, timeout_s=timeout_s)

    def _route(self, channel: str):
        try:
            supply_name, physical_channel = str(channel).split("/", 1)
            return self.supplies[supply_name], physical_channel
        except (KeyError, ValueError) as error:
            raise ValueError(f"未配置的生产电源通道: {channel}") from error


def _create_hardware_measurement_port(config_path: str, *, recorder=None):
    from infrastructure.config.json_io import load_config_file

    config = load_config_file(config_path)
    # 装配校验（地址、角色、通道）必须在创建任何 VISA 资源之前完成。
    plan = _hardware_assembly_plan(config)
    manager = pyvisa.ResourceManager()
    resources = []
    try:
        signal_resource = manager.open_resource(plan["signal_address"])
        resources.append(signal_resource)
        spectrum_resource = manager.open_resource(plan["spectrum_address"])
        resources.append(spectrum_resource)

        configured_supplies = {}
        for power_name in plan["supply_names"]:
            power_config = plan["power_configs"][power_name]
            resource = manager.open_resource(power_config["address"])
            resources.append(resource)
            identity = resource.query("*IDN?").strip()
            if "DP832A" not in identity.upper():
                raise ValueError(f"生产电源 {power_name} 设备身份不是 DP832A: {identity}")
            states = [
                resource.query(f"OUTP? {channel}").strip().upper()
                for channel in _configured_channels(power_config, power_name)
            ]
            if any(state not in {"0", "OFF"} for state in states):
                raise ValueError(f"生产电源 {power_name} 初始输出未关闭")
            configured_supplies[power_name] = ScpiPowerSupplyDriver(
                VisaScpiTransport(resource, recorder=recorder)
            )

        signal_config = plan["signal_config"]
        signal = ScpiSignalGeneratorDriver(
            VisaScpiTransport(signal_resource, recorder=recorder),
            min_frequency_hz=signal_config.get("min_frequency_hz"),
            max_frequency_hz=signal_config.get("max_frequency_hz"),
            min_power_dbm=signal_config.get("min_power_dbm"),
            max_power_dbm=signal_config.get("max_power_dbm"),
        )
        spectrum = ScpiSpectrumAnalyzerDriver(
            VisaScpiTransport(spectrum_resource, recorder=recorder)
        )
        power = _ConfiguredPowerSupply(configured_supplies)
        session = _VisaSession(
            manager,
            signal,
            spectrum,
            power,
            plan["all_channels"],
        )
        session.validate()
        session.connect()
        session.prepare()
        return PortMeasurementAdapter(
            session,
            dut_power_channels=plan["dut_channels"],
            driver_power_channels=plan["driver_channels"],
            power_settings=plan["power_settings"],
            qualified_power_channels=True,
            emergency_channels=_emergency_channels(plan["supply_channels"]),
        )
    except Exception:
        for resource in reversed(resources):
            try:
                resource.close()
            except Exception:
                pass
        try:
            manager.close()
        except Exception:
            pass
        raise


def _hardware_assembly_plan(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the hardware assembly and return the explicit construction plan.

    该函数只读取配置，不创建 VISA 资源；地址重复、角色缺失/重复、通道不存在
    都在连接任何仪器之前失败。
    """
    instruments = _mapping_section(config, "instruments")
    signal_config = _mapping_section(instruments, "signal_generator", "instruments")
    spectrum_config = _mapping_section(instruments, "spectrum_analyzer", "instruments")
    power_configs = _mapping_section(instruments, "power_supplies", "instruments", optional=True)
    if not signal_config.get("enabled", True) or not spectrum_config.get("enabled", True):
        raise ValueError("硬件测量路径要求信号源和频谱仪均已启用")

    signal_address = _required_address(signal_config, "信号源")
    spectrum_address = _required_address(spectrum_config, "频谱仪")
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
            name = supply.get("name")
            if name is None:
                continue
            if not isinstance(name, str) or not name.strip():
                raise ValueError("供电分配的电源名称必须是非空字符串")
            assigned_names.add(name)
    if any(name not in enabled_names for name in assigned_names):
        raise ValueError("供电分配引用了未启用或不存在的电源模板")
    if not enabled_names:
        raise ValueError("硬件测量路径至少需要一个电源模板配置")
    if not assigned_names:
        raise ValueError("生产硬件路径必须显式配置电源角色和通道")

    supply_addresses = {}
    for power_name in sorted(assigned_names):
        address = _required_address(power_configs[power_name], f"生产电源 {power_name}")
        if address in (signal_address, spectrum_address) or address in supply_addresses.values():
            raise ValueError(f"生产电源 {power_name} 与其他仪器地址重复")
        supply_addresses[power_name] = address

    dut_channels, dut_settings = _assigned_channels(dut_assignment, power_configs, "DUT")
    driver_channels, driver_settings = _assigned_channels(
        driver_assignment, power_configs, "驱动"
    )
    supply_names = sorted(assigned_names)
    return {
        "signal_config": signal_config,
        "signal_address": signal_address,
        "spectrum_address": spectrum_address,
        "power_configs": power_configs,
        "supply_names": supply_names,
        "supply_channels": {
            name: tuple(_configured_channels(power_configs[name], name)) for name in supply_names
        },
        "dut_channels": dut_channels,
        "driver_channels": driver_channels,
        "power_settings": {**driver_settings, **dut_settings},
        "all_channels": {**driver_channels, **dut_channels},
    }


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


def _configured_channels(power_config: Mapping[str, Any], power_name: str) -> Mapping[str, Any]:
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
    label: str,
) -> tuple[dict[str, str], dict[str, dict[str, float]]]:
    """Resolve assigned logical roles to explicit ``supply/channel`` tokens."""
    channels = {}
    settings = {}
    for supply in _supply_assignments(assignment).values():
        if not isinstance(supply, Mapping):
            raise ValueError(f"{label} 供电分配条目必须是对象")
        power_name = supply.get("name")
        power_config = power_configs.get(power_name)
        if not isinstance(power_config, Mapping):
            raise ValueError(f"供电分配引用了不存在的电源: {power_name}")
        configured_channels = _configured_channels(power_config, power_name)
        for channel in _channel_list(supply, str(power_name)):
            channel_config = configured_channels.get(channel)
            if not isinstance(channel_config, Mapping):
                raise ValueError(f"供电分配引用了不存在的通道: {power_name}/{channel}")
            token = f"{power_name}/{channel}"
            role = _explicit_role(channel_config, token)
            if role in channels and channels[role] != token:
                raise ValueError(f"供电角色重复配置: {power_name}/{role}")
            channels[role] = token
            settings[role] = _channel_settings(channel_config, token)
    if channels and set(channels) != {"gate", "drain"}:
        raise ValueError(f"{label} 电源必须同时配置 gate 和 drain 通道")
    return channels, settings


def _channel_list(supply: Mapping[str, Any], power_name: str) -> list[str]:
    channels = supply.get("channel")
    if not isinstance(channels, list) or not channels:
        raise ValueError(f"供电分配必须提供非空通道列表: {power_name}")
    names = [channel for channel in channels if isinstance(channel, str)]
    if len(names) != len(channels):
        raise ValueError(f"供电分配的通道必须是字符串: {power_name}")
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
    supply_channels: Mapping[str, tuple[str, ...]]
) -> tuple[tuple[int, str], ...]:
    """返回急停要关闭的显式 ``supply/channel`` 令牌（先漏后栅的顺序）。"""
    return tuple(
        (index, f"{name}/{channel}")
        for index, (name, channels) in enumerate(supply_channels.items())
        for channel in reversed(channels)
    )


__all__ = ["create_measurement_port"]
