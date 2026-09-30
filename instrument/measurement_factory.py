"""Application-level assembly for hardware, legacy, and offline ports.

The factory keeps hardware construction out of measurement services.  The
legacy mode deliberately returns the existing controller so production callers
can opt out of the new session path until hardware acceptance is complete.
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
    """Create a service port for hardware, legacy, or offline simulation mode.

    ``legacy`` returns ``None`` because callers use their historical
    ``InstrumentControl`` fallback.  Hardware construction owns the VISA
    manager and all resources through the returned session.
    """
    if mode == "legacy":
        return None
    if mode == "hardware":
        if config_path is None:
            raise ValueError("hardware 模式必须提供 config_path")
        return _create_hardware_measurement_port(config_path)
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
        try:
            return super().close(*args, **kwargs)
        finally:
            if not self._resource_manager_closed:
                close = getattr(self.resource_manager, "close", None)
                if close is not None:
                    close()
                self._resource_manager_closed = True


def _create_hardware_measurement_port(config_path: str):
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
        assigned_names = {
            supply.get("name")
            for group_name in ("dut_amplifier", "driver_amplifier")
            for supply in assignments.get(group_name, {}).get("supplies", {}).values()
            if supply.get("name")
        }
        enabled_names = [
            name for name, item in power_configs.items() if item.get("enabled", True)
        ]
        if len(assigned_names) > 1:
            raise ValueError("当前硬件组装器仅支持 DUT 和驱动功放共用一台双通道电源")
        if assigned_names:
            power_name = next(iter(assigned_names))
            if power_name not in enabled_names:
                raise ValueError(f"供电分配引用了未启用电源: {power_name}")
        else:
            if len(enabled_names) != 1:
                raise ValueError("存在多个或零个启用电源时必须显式配置供电分配")
            power_name = enabled_names[0]
        power_config = power_configs.get(power_name) if power_name else None
        if power_config is None:
            raise ValueError("硬件测量路径至少需要一个已启用电源")
        power_resource = manager.open_resource(power_config["address"])
        resources.append(power_resource)

        signal = ScpiSignalGeneratorDriver(
            VisaScpiTransport(signal_resource),
            min_frequency_hz=signal_config.get("min_frequency_hz"),
            max_frequency_hz=signal_config.get("max_frequency_hz"),
            min_power_dbm=signal_config.get("min_power_dbm"),
            max_power_dbm=signal_config.get("max_power_dbm"),
        )
        spectrum = ScpiSpectrumAnalyzerDriver(VisaScpiTransport(spectrum_resource))
        power = ScpiPowerSupplyDriver(VisaScpiTransport(power_resource))
        dut_channels = _assigned_channels(assignments.get("dut_amplifier", {}), power_name, power_config, resolve_power_channel_role)
        driver_channels = _assigned_channels(assignments.get("driver_amplifier", {}), power_name, power_config, resolve_power_channel_role)
        if len(set(dut_channels.values()) | set(driver_channels.values())) > 2:
            raise ValueError("单台双通道电源最多支持两个不同物理通道")
        for label, channels in (("DUT", dut_channels), ("driver", driver_channels)):
            if channels and set(channels) != {"gate", "drain"}:
                raise ValueError(f"{label} 电源必须同时配置 gate 和 drain 通道")
        settings = {
            role: {
                "voltage_v": power_config["channels"][channel]["voltage"]["value"],
                "current_a": power_config["channels"][channel]["current"]["value"],
            }
            for role, channel in {**driver_channels, **dut_channels}.items()
        }
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


def _assigned_channels(assignment, power_name, power_config, role_resolver):
    channels = {}
    for supply in assignment.get("supplies", {}).values():
        if supply.get("name") != power_name:
            continue
        for channel in supply.get("channel", []):
            if channel not in power_config.get("channels", {}):
                raise ValueError(f"供电分配引用了不存在的通道: {power_name}/{channel}")
            role = role_resolver(channel, power_config.get("channels", {}).get(channel, {}))
            if role is None:
                raise ValueError(f"无法解析供电通道角色: {power_name}/{channel}")
            if role in channels and channels[role] != channel:
                raise ValueError(f"供电角色重复配置: {power_name}/{role}")
            channels[role] = channel
    return channels
