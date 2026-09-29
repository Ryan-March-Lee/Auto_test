"""Composition helpers for explicitly configured signal-generator drivers."""

from __future__ import annotations

from collections.abc import Mapping

from .drivers.signal_generator import SignalGeneratorDriver
from .transport import ScpiTransport


def create_signal_generator_driver(
    transport: ScpiTransport,
    instrument_config: Mapping[str, object] | None = None,
) -> SignalGeneratorDriver:
    """Build a driver from an already parsed signal-generator config section.

    Configuration fields are optional because existing project configurations
    do not declare model capability limits. The driver itself never reads files.
    """
    config = instrument_config or {}
    return SignalGeneratorDriver(
        transport,
        min_frequency_hz=config.get("min_frequency_hz"),
        max_frequency_hz=config.get("max_frequency_hz"),
        min_power_dbm=config.get("min_power_dbm"),
        max_power_dbm=config.get("max_power_dbm"),
    )
