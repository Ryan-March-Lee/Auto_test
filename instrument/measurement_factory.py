"""Application-level assembly for hardware and offline measurement ports.

The factory keeps hardware construction out of measurement services.  The
    Hardware construction uses the explicit configured power topology.  The
    discovery wrapper remains isolated to the hardware-smoke entry points.
"""

from __future__ import annotations

from typing import Any, Mapping

import pyvisa

from .measurement_adapter import PortMeasurementAdapter
from .drivers import ScpiPowerSupplyDriver, ScpiSignalGeneratorDriver, ScpiSpectrumAnalyzerDriver
from .simulation import SafetyInstrumentSession
from .transport import VisaScpiTransport
from .power_roles import resolve_power_channel_role
from .simulation import (
    SimulatedPowerSupply,
    SimulatedSignalGenerator,
    SimulatedSpectrumAnalyzer,
)


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
    returned session.
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
    session = SafetyInstrumentSession(
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


class _VisaSession(SafetyInstrumentSession):
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
            close = getattr(self.resource_manager, "close", None)
            if close is not None:
                close()
            self._resource_manager_closed = True

    @property
    def resources_closed(self) -> bool:
        return self._resource_manager_closed and self.state.value == "cleaned"


class _DiscoveredPowerSupply:
    """Route power operations to discovered supplies and close all of them."""

    def __init__(self, supplies):
        self.supplies = list(supplies)

    def connect(self, *, timeout_s=10.0):
        for supply in self.supplies:
            supply.connect(timeout_s=timeout_s)

    def close(self, *, timeout_s=5.0):
        errors = []
        for supply in self.supplies:
            try:
                supply.close(timeout_s=timeout_s)
            except Exception as error:
                errors.append(error)
        if errors:
            raise RuntimeError("power supply close failed: " + "; ".join(map(str, errors))) from errors[0]

    def _selected(self, channel):
        if not self.supplies:
            raise RuntimeError("没有可用的已发现电源")
        return self.supplies[0], channel

    def set_output_enabled(self, channel, enabled, *, timeout_s=5.0):
        for supply in self.supplies:
            supply.set_output_enabled(channel, enabled, timeout_s=timeout_s)

    def set_voltage_v(self, channel, voltage_v, *, timeout_s=5.0):
        for supply in self.supplies:
            supply.set_voltage_v(channel, voltage_v, timeout_s=timeout_s)

    def set_current_limit_a(self, channel, current_a, *, timeout_s=5.0):
        for supply in self.supplies:
            supply.set_current_limit_a(channel, current_a, timeout_s=timeout_s)

    def read_voltage_v(self, channel, *, timeout_s=5.0):
        supply, physical_channel = self._selected(channel)
        return supply.read_voltage_v(physical_channel, timeout_s=timeout_s)

    def read_current_a(self, channel, *, timeout_s=5.0):
        supply, physical_channel = self._selected(channel)
        return supply.read_current_a(physical_channel, timeout_s=timeout_s)

    def __getattr__(self, name):
        def routed(channel, *args, **kwargs):
            if name.startswith("read_"):
                supply, physical_channel = self._selected(channel)
                return getattr(supply, name)(physical_channel, *args, **kwargs)
            result = None
            for supply in self.supplies:
                result = getattr(supply, name)(channel, *args, **kwargs)
            return result
        return routed


def _discover_power_supplies(manager, template_config, *, exclude_addresses=(), recorder=None):
    """Discover every idle DP832A and create drivers without fixed addresses."""
    discovered = []
    candidates = manager.list_resources()
    try:
        for address in candidates:
            if address in set(exclude_addresses):
                continue
            if address.endswith("::SOCKET"):
                continue
            resource = None
            try:
                resource = manager.open_resource(address)
                identity = resource.query("*IDN?").strip()
                if "DP832A" not in identity.upper():
                    resource.close()
                    continue
                states = [resource.query(f"OUTP? {channel}").strip().upper() for channel in ("CH1", "CH2")]
                if any(state not in {"0", "OFF"} for state in states):
                    raise ValueError(f"发现的 DP832A 输出未关闭: {address}")
                discovered.append((address, resource, identity))
            except Exception:
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass
                raise
    except Exception:
        for _address, resource, _identity in discovered:
            try:
                resource.close()
            except Exception:
                pass
        raise
    if not discovered:
        raise ValueError("未发现处于空载状态的 DP832A 电源")
    supplies = []
    for address, resource, _identity in discovered:
        supplies.append(ScpiPowerSupplyDriver(VisaScpiTransport(resource, recorder=recorder)))
    return supplies, discovered


class _ConfiguredPowerSupply:
    """Route logical ``supply/channel`` tokens to configured drivers."""

    def __init__(self, supplies):
        self.supplies = dict(supplies)

    def connect(self, *, timeout_s=10.0):
        for supply in self.supplies.values():
            supply.connect(timeout_s=timeout_s)

    def close(self, *, timeout_s=5.0):
        errors = []
        for supply in self.supplies.values():
            try:
                supply.close(timeout_s=timeout_s)
            except Exception as error:
                errors.append(error)
        if errors:
            raise RuntimeError("power supply close failed: " + "; ".join(map(str, errors))) from errors[0]

    def _route(self, channel):
        try:
            supply_name, physical_channel = str(channel).split("/", 1)
            return self.supplies[supply_name], physical_channel
        except (KeyError, ValueError) as error:
            raise ValueError(f"未配置的生产电源通道: {channel}") from error

    def __getattr__(self, name):
        def routed(channel, *args, **kwargs):
            supply, physical_channel = self._route(channel)
            return getattr(supply, name)(physical_channel, *args, **kwargs)
        return routed


def _create_hardware_measurement_port(config_path: str, *, recorder=None):
    from config_io import load_config_file
    config = load_config_file(config_path)
    instruments = config["instruments"]
    manager = pyvisa.ResourceManager()
    resources = []
    try:
        signal_config = instruments["signal_generator"]
        spectrum_config = instruments["spectrum_analyzer"]
        power_configs = instruments.get("power_supplies", {})
        if not signal_config.get("enabled", True) or not spectrum_config.get("enabled", True):
            raise ValueError("硬件测量路径要求信号源和频谱仪均已启用")

        signal_resource = manager.open_resource(signal_config["address"])
        resources.append(signal_resource)
        spectrum_resource = manager.open_resource(spectrum_config["address"])
        resources.append(spectrum_resource)
        assignments = config.get("power_supply_assignment", {})
        enabled_names = [name for name, item in power_configs.items() if item.get("enabled", True)]
        assignments = config.get("power_supply_assignment", {})
        assigned_names = {
            supply.get("name")
            for group_name in ("dut_amplifier", "driver_amplifier")
            for supply in assignments.get(group_name, {}).get("supplies", {}).values()
            if supply.get("name")
        }
        if any(name not in enabled_names for name in assigned_names):
            raise ValueError("供电分配引用了未启用或不存在的电源模板")
        if not enabled_names:
            raise ValueError("硬件测量路径至少需要一个电源模板配置")
        if not assigned_names:
            raise ValueError("生产硬件路径必须显式配置电源角色和通道")
        configured_supplies = {}
        discovered = []
        for power_name in sorted(assigned_names):
            power_config = power_configs[power_name]
            address = power_config.get("address")
            if not isinstance(address, str) or not address.strip():
                raise ValueError(f"生产电源 {power_name} 必须配置 address")
            if address in (signal_config["address"], spectrum_config["address"]):
                raise ValueError(f"生产电源 {power_name} 与其他仪器地址重复")
            resource = manager.open_resource(address)
            resources.append(resource)
            identity = resource.query("*IDN?").strip()
            if "DP832A" not in identity.upper():
                raise ValueError(f"生产电源 {power_name} 设备身份不是 DP832A: {identity}")
            states = [resource.query(f"OUTP? {channel}").strip().upper() for channel in power_config["channels"]]
            if any(state not in {"0", "OFF"} for state in states):
                raise ValueError(f"生产电源 {power_name} 初始输出未关闭")
            driver = ScpiPowerSupplyDriver(VisaScpiTransport(resource, recorder=recorder))
            configured_supplies[power_name] = driver
            discovered.append((address, resource, identity))

        signal = ScpiSignalGeneratorDriver(
            VisaScpiTransport(signal_resource, recorder=recorder),
            min_frequency_hz=signal_config.get("min_frequency_hz"),
            max_frequency_hz=signal_config.get("max_frequency_hz"),
            min_power_dbm=signal_config.get("min_power_dbm"),
            max_power_dbm=signal_config.get("max_power_dbm"),
        )
        spectrum = ScpiSpectrumAnalyzerDriver(VisaScpiTransport(spectrum_resource, recorder=recorder))
        power = _ConfiguredPowerSupply(configured_supplies)
        dut_channels, dut_settings = _assigned_channels(assignments.get("dut_amplifier", {}), power_configs, resolve_power_channel_role)
        driver_channels, driver_settings = _assigned_channels(assignments.get("driver_amplifier", {}), power_configs, resolve_power_channel_role)
        for label, channels in (("DUT", dut_channels), ("driver", driver_channels)):
            if channels and set(channels) != {"gate", "drain"}:
                raise ValueError(f"{label} 电源必须同时配置 gate 和 drain 通道")
        settings = {**driver_settings, **dut_settings}
        all_channels = {**driver_channels, **dut_channels}
        session = _VisaSession(
            manager, signal, spectrum, power,
            all_channels,
        )
        session.validate()
        session.connect()
        session.prepare()
        return PortMeasurementAdapter(
            session,
            dut_power_channels=dut_channels,
            driver_power_channels=driver_channels,
            power_settings=settings,
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


def _assigned_channels(assignment, power_configs, role_resolver):
    channels = {}
    settings = {}
    for supply in assignment.get("supplies", {}).values():
        power_name = supply.get("name")
        power_config = power_configs.get(power_name)
        if power_config is None:
            raise ValueError(f"供电分配引用了不存在的电源: {power_name}")
        for channel in supply.get("channel", []):
            if channel not in power_config.get("channels", {}):
                raise ValueError(f"供电分配引用了不存在的通道: {power_name}/{channel}")
            role = role_resolver(channel, power_config.get("channels", {}).get(channel, {}))
            if role is None:
                raise ValueError(f"无法解析供电通道角色: {power_name}/{channel}")
            token = f"{power_name}/{channel}"
            if role in channels and channels[role] != token:
                raise ValueError(f"供电角色重复配置: {power_name}/{role}")
            channels[role] = token
            channel_config = power_config["channels"][channel]
            settings[role] = {
                "voltage_v": channel_config["voltage"]["value"],
                "current_a": channel_config["current"]["value"],
            }
    return channels, settings
