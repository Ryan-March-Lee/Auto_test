"""设备供电控制边界。

该模块只接收 ``PowerSupplyPort`` 和逻辑角色映射，不读取配置文件，也不
持有 VISA 资源。所有需要功放供电的调用方都应通过这里表达上电和掉电，
从而固定 ``Gate -> Drain`` 上电、``Drain -> Gate`` 掉电的安全顺序。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Callable


class PowerControlError(RuntimeError):
    """一个或多个供电动作失败。"""

    def __init__(self, message: str, errors: list[BaseException]):
        super().__init__(message)
        self.errors = tuple(errors)


class PowerController:
    """集中管理逻辑 gate/drain 角色的供电动作。"""

    def __init__(self, power_supply, channels: Mapping[str, str], *,
                 sleep_fn: Callable[[float], None] | None = None,
                 settle_time_s: float = 2.0,
                 gate_settle_time_s: float | None = None,
                 drain_settle_time_s: float | None = None):
        if set(channels) - {"gate", "drain"}:
            raise ValueError("供电通道只允许 gate 和 drain 角色")
        self.power_supply = power_supply
        self.channels = dict(channels)
        self.sleep_fn = sleep_fn or (lambda _seconds: None)
        self.gate_settle_time_s = float(
            settle_time_s if gate_settle_time_s is None else gate_settle_time_s
        )
        self.drain_settle_time_s = float(
            settle_time_s if drain_settle_time_s is None else drain_settle_time_s
        )
        if self.gate_settle_time_s < 0 or self.drain_settle_time_s < 0:
            raise ValueError("供电稳定时间必须大于或等于 0")

    def power_on(self, *, timeout_s: float = 10.0) -> None:
        for role in ("gate", "drain"):
            channel = self.channels.get(role)
            if channel is not None:
                self.power_supply.set_output_enabled(channel, True, timeout_s=timeout_s)
            if role == "gate":
                self.sleep_fn(self.gate_settle_time_s)

    def power_off(self, *, timeout_s: float = 10.0) -> None:
        errors: list[BaseException] = []
        for role in ("drain", "gate"):
            channel = self.channels.get(role)
            if channel is not None:
                try:
                    self.power_supply.set_output_enabled(channel, False, timeout_s=timeout_s)
                except Exception as error:
                    errors.append(error)
            if role == "drain":
                self.sleep_fn(self.drain_settle_time_s)
        if errors:
            raise PowerControlError("供电安全掉电存在失败", errors) from errors[0]
