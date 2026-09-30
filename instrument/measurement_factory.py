"""Application-level assembly for legacy and offline measurement ports.

The factory keeps hardware construction out of measurement services.  The
legacy mode deliberately returns the existing controller so production callers
can opt out of the new session path until hardware acceptance is complete.
"""

from __future__ import annotations

from typing import Any, Mapping

from .measurement_adapter import PortMeasurementAdapter
from .simulation import (
    SafetyInstrumentSession,
    SimulatedPowerSupply,
    SimulatedSignalGenerator,
    SimulatedSpectrumAnalyzer,
)


def create_measurement_port(
    config_path: str | None = None,
    *,
    mode: str = "legacy",
    power_channels: Mapping[str, str] | None = None,
    driver_power_channels: Mapping[str, str] | None = None,
    power_settings: Mapping[str, Mapping[str, float]] | None = None,
    recorder: Any = None,
):
    """Create a service port for ``legacy`` or offline ``simulation`` mode.

    ``legacy`` returns ``None`` because callers use their historical
    ``InstrumentControl`` fallback.  ``simulation`` creates a fully connected
    new session and adapts it to the historical measurement-service protocol.
    No real-device path is selected implicitly.
    """
    del config_path  # Reserved for the future hardware session assembler.
    if mode == "legacy":
        return None
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
