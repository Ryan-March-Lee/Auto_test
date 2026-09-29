"""Application-facing signal-generator actions."""

from __future__ import annotations

from typing import Protocol


class SignalGeneratorActionDriver(Protocol):
    def connect(self, *, timeout_s: float = 10.0) -> None: ...
    def set_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None: ...
    def set_power_dbm(self, power_dbm: float, *, timeout_s: float = 5.0) -> None: ...
    def set_rf_enabled(self, enabled: bool, *, timeout_s: float = 5.0) -> None: ...
    def close(self, *, timeout_s: float = 5.0) -> None: ...


class SignalGeneratorActions:
    """Stable action entry point; SCPI syntax remains in the driver."""

    def __init__(self, driver: SignalGeneratorActionDriver) -> None:
        self.driver = driver

    def connect(self, *, timeout_s: float = 10.0) -> None:
        self.driver.connect(timeout_s=timeout_s)

    def set_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None:
        self.driver.set_frequency_hz(frequency_hz, timeout_s=timeout_s)

    def set_power_dbm(self, power_dbm: float, *, timeout_s: float = 5.0) -> None:
        self.driver.set_power_dbm(power_dbm, timeout_s=timeout_s)

    def set_rf_enabled(self, enabled: bool, *, timeout_s: float = 5.0) -> None:
        self.driver.set_rf_enabled(enabled, timeout_s=timeout_s)

    def close(self, *, timeout_s: float = 5.0) -> None:
        self.driver.close(timeout_s=timeout_s)
