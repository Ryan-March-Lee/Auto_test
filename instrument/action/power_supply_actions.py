"""Application-facing actions for a single power-supply channel."""

from __future__ import annotations

from typing import Protocol


class PowerSupplyActionDriver(Protocol):
    def connect(self, *, timeout_s: float = 10.0) -> None: ...
    def set_voltage_v(self, channel: str, voltage_v: float, *, timeout_s: float = 5.0) -> None: ...
    def set_current_limit_a(self, channel: str, current_a: float, *, timeout_s: float = 5.0) -> None: ...
    def set_voltage_protection_state(self, channel: str, enabled: bool, *, timeout_s: float = 5.0) -> None: ...
    def set_current_protection_state(self, channel: str, enabled: bool, *, timeout_s: float = 5.0) -> None: ...
    def set_voltage_protection(self, channel: str, voltage_protection_v: float, *, timeout_s: float = 5.0) -> None: ...
    def set_current_protection(self, channel: str, current_protection_a: float, *, timeout_s: float = 5.0) -> None: ...
    def set_output_enabled(self, channel: str, enabled: bool, *, timeout_s: float = 5.0) -> None: ...
    def read_voltage_v(self, channel: str, *, timeout_s: float = 5.0) -> float: ...
    def read_current_a(self, channel: str, *, timeout_s: float = 5.0) -> float: ...
    def close(self, *, timeout_s: float = 5.0) -> None: ...


class PowerSupplyActions:
    """稳定的电源动作入口；通道角色和上电顺序由上层 flow 决定。"""

    def __init__(self, driver: PowerSupplyActionDriver) -> None:
        self.driver = driver

    def connect(self, *, timeout_s: float = 10.0) -> None:
        self.driver.connect(timeout_s=timeout_s)

    def set_voltage_v(self, channel: str, voltage_v: float, *, timeout_s: float = 5.0) -> None:
        self.driver.set_voltage_v(channel, voltage_v, timeout_s=timeout_s)

    def set_current_limit_a(self, channel: str, current_a: float, *, timeout_s: float = 5.0) -> None:
        self.driver.set_current_limit_a(channel, current_a, timeout_s=timeout_s)

    def set_voltage_protection_state(self, channel: str, enabled: bool, *, timeout_s: float = 5.0) -> None:
        self.driver.set_voltage_protection_state(channel, enabled, timeout_s=timeout_s)

    def set_current_protection_state(self, channel: str, enabled: bool, *, timeout_s: float = 5.0) -> None:
        self.driver.set_current_protection_state(channel, enabled, timeout_s=timeout_s)

    def set_voltage_protection(self, channel: str, voltage_protection_v: float, *, timeout_s: float = 5.0) -> None:
        self.driver.set_voltage_protection(channel, voltage_protection_v, timeout_s=timeout_s)

    def set_current_protection(self, channel: str, current_protection_a: float, *, timeout_s: float = 5.0) -> None:
        self.driver.set_current_protection(channel, current_protection_a, timeout_s=timeout_s)

    def set_output_enabled(self, channel: str, enabled: bool, *, timeout_s: float = 5.0) -> None:
        self.driver.set_output_enabled(channel, enabled, timeout_s=timeout_s)

    def read_voltage_v(self, channel: str, *, timeout_s: float = 5.0) -> float:
        return self.driver.read_voltage_v(channel, timeout_s=timeout_s)

    def read_current_a(self, channel: str, *, timeout_s: float = 5.0) -> float:
        return self.driver.read_current_a(channel, timeout_s=timeout_s)

    def close(self, *, timeout_s: float = 5.0) -> None:
        self.driver.close(timeout_s=timeout_s)
