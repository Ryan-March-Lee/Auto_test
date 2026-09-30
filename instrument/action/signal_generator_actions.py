"""Application-facing signal-generator actions."""

from __future__ import annotations

from typing import Protocol

from .context import ActionBase, ActionContext, ActionLogger


class SignalGeneratorActionDriver(Protocol):
    def connect(self, *, timeout_s: float = 10.0) -> None: ...
    def set_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None: ...
    def set_power_dbm(self, power_dbm: float, *, timeout_s: float = 5.0) -> None: ...
    def set_rf_enabled(self, enabled: bool, *, timeout_s: float = 5.0) -> None: ...
    def close(self, *, timeout_s: float = 5.0) -> None: ...


class SignalGeneratorActions(ActionBase):
    """Stable action entry point; SCPI syntax remains in the driver."""

    def __init__(self, driver: SignalGeneratorActionDriver, *, context: ActionContext | None = None,
                 logger: ActionLogger | None = None) -> None:
        super().__init__(context, logger)
        self._driver = driver

    @property
    def driver(self) -> SignalGeneratorActionDriver:
        return self._driver

    def connect(self, *, timeout_s: float = 10.0) -> None:
        self._call("connect", self._driver.connect, timeout_s=timeout_s)

    def set_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None:
        self._call("set_frequency_hz", self._driver.set_frequency_hz, frequency_hz, timeout_s=timeout_s,
                   event_values={"frequency_hz": frequency_hz})

    def set_power_dbm(self, power_dbm: float, *, timeout_s: float = 5.0) -> None:
        self._call("set_power_dbm", self._driver.set_power_dbm, power_dbm, timeout_s=timeout_s,
                   event_values={"power_dbm": power_dbm})

    def set_rf_enabled(self, enabled: bool, *, timeout_s: float = 5.0) -> None:
        self._call("set_rf_enabled", self._driver.set_rf_enabled, enabled, timeout_s=timeout_s,
                   event_values={"enabled": enabled})

    def close(self, *, timeout_s: float = 5.0) -> None:
        self._call("close", self._driver.close, timeout_s=timeout_s)
