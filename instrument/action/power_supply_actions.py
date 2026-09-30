"""Application-facing actions for one bound power-supply channel."""

from __future__ import annotations

from typing import Protocol
from dataclasses import replace

from .context import ActionBase, ActionContext, ActionLogger


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


class PowerSupplyActions(ActionBase):
    """稳定的电源动作入口；每个实例绑定一台电源的一个物理通道。"""

    def __init__(self, driver: PowerSupplyActionDriver, *, channel: str,
                 context: ActionContext | None = None,
                 logger: ActionLogger | None = None) -> None:
        if not isinstance(channel, str) or not channel.strip():
            raise ValueError("channel 必须是非空字符串")
        if context is not None and context.channel_id not in (None, channel):
            raise ValueError("ActionContext.channel_id 与绑定通道不一致")
        context = replace(context or ActionContext(), channel_id=channel)
        super().__init__(context, logger)
        self._driver = driver
        self.channel = channel

    @property
    def driver(self) -> PowerSupplyActionDriver:
        return self._driver

    def connect(self, *, timeout_s: float = 10.0) -> None:
        self._call("connect", self._driver.connect, timeout_s=timeout_s)

    def set_voltage_v(self, voltage_v: float, *, timeout_s: float = 5.0) -> None:
        self._call("set_voltage_v", self._driver.set_voltage_v, self.channel, voltage_v,
                   timeout_s=timeout_s, event_values={"voltage_v": voltage_v})

    def set_current_limit_a(self, current_a: float, *, timeout_s: float = 5.0) -> None:
        self._call("set_current_limit_a", self._driver.set_current_limit_a, self.channel, current_a,
                   timeout_s=timeout_s, event_values={"current_a": current_a})

    def set_voltage_protection_state(self, enabled: bool, *, timeout_s: float = 5.0) -> None:
        self._call("set_voltage_protection_state", self._driver.set_voltage_protection_state,
                   self.channel, enabled, timeout_s=timeout_s, event_values={"enabled": enabled})

    def set_current_protection_state(self, enabled: bool, *, timeout_s: float = 5.0) -> None:
        self._call("set_current_protection_state", self._driver.set_current_protection_state,
                   self.channel, enabled, timeout_s=timeout_s, event_values={"enabled": enabled})

    def set_voltage_protection(self, voltage_protection_v: float, *, timeout_s: float = 5.0) -> None:
        self._call("set_voltage_protection", self._driver.set_voltage_protection,
                   self.channel, voltage_protection_v, timeout_s=timeout_s,
                   event_values={"voltage_protection_v": voltage_protection_v})

    def set_current_protection(self, current_protection_a: float, *, timeout_s: float = 5.0) -> None:
        self._call("set_current_protection", self._driver.set_current_protection,
                   self.channel, current_protection_a, timeout_s=timeout_s,
                   event_values={"current_protection_a": current_protection_a})

    def set_output_enabled(self, enabled: bool, *, timeout_s: float = 5.0) -> None:
        self._call("set_output_enabled", self._driver.set_output_enabled,
                   self.channel, enabled, timeout_s=timeout_s, event_values={"enabled": enabled})

    def read_voltage_v(self, *, timeout_s: float = 5.0) -> float:
        return self._call("read_voltage_v", self._driver.read_voltage_v, self.channel, timeout_s=timeout_s)

    def read_current_a(self, *, timeout_s: float = 5.0) -> float:
        return self._call("read_current_a", self._driver.read_current_a, self.channel, timeout_s=timeout_s)

    def close(self, *, timeout_s: float = 5.0) -> None:
        self._call("close", self._driver.close, timeout_s=timeout_s)
