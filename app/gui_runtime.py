"""Application-level operations used by the Qt presentation adapters."""

from __future__ import annotations

from typing import Any

from enhanced_workers import (
    EnhancedAmplifierMeasurement,
    EnhancedCableLossMeasurement,
    EnhancedDriverPowerMapping,
)


def connect_instruments(config_path: str) -> Any:
    """Create the configured instrument session for the application layer."""
    from instrument_control import InstrumentControl

    return InstrumentControl(config_path)


def create_cable_loss_measurement(config_path: str, **callbacks: Any) -> Any:
    return EnhancedCableLossMeasurement(config_path, **callbacks)


def create_driver_mapping_measurement(config_path: str, **callbacks: Any) -> Any:
    return EnhancedDriverPowerMapping(config_path, **callbacks)


def create_amplifier_measurement(config_path: str, **callbacks: Any) -> Any:
    return EnhancedAmplifierMeasurement(config_path, **callbacks)
