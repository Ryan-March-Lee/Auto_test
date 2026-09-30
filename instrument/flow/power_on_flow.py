"""功放安全上电流程：先准备参数，再 Gate，最后 Drain。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping


@dataclass(frozen=True)
class PowerChannelSetup:
    """一个物理通道的安全上电参数，电压单位 V、电流单位 A。"""

    voltage_v: float
    current_a: float


class PowerOnFlowError(RuntimeError):
    """上电失败；errors 保留原始异常，便于日志和上层汇总。"""

    def __init__(self, message: str, errors: list[BaseException]):
        super().__init__(message)
        self.errors = tuple(errors)


class PowerOnFlow:
    """按配置角色执行幂等前置检查后的安全上电。

    ``channels`` 的键必须恰好包含 ``gate`` 和 ``drain``，值为绑定单一
    物理通道的 PowerSupplyActions。设置电压和限流永远发生在输出打开之前。
    """

    def __init__(self, channels: Mapping[str, object], *, sleep_fn: Callable[[float], None] | None = None,
                 settle_time_s: float = 1.5, event_sink: Callable[[str, str], None] | None = None):
        self.channels = dict(channels)
        self.sleep_fn = sleep_fn or (lambda _seconds: None)
        if settle_time_s < 0:
            raise ValueError("settle_time_s 必须大于或等于 0")
        self.settle_time_s = float(settle_time_s)
        self.event_sink = event_sink or (lambda _step, _status: None)
        self._validate_roles()

    def _validate_roles(self) -> None:
        if set(self.channels) != {"gate", "drain"}:
            raise ValueError("安全上电必须同时提供 gate 和 drain 通道")
        physical_channels = [getattr(action, "channel", None) for action in self.channels.values()]
        if any(not isinstance(channel, str) or not channel.strip() for channel in physical_channels):
            raise ValueError("安全上电通道必须绑定非空物理通道")
        if len(set(physical_channels)) != len(physical_channels):
            raise ValueError("gate 和 drain 不能映射到同一个物理通道")

    def run(self, setup: Mapping[str, PowerChannelSetup | Mapping[str, float]], *, timeout_s: float = 5.0) -> tuple[str, ...]:
        normalized = self._normalize_setup(setup)
        completed: list[str] = []
        try:
            for role in ("gate", "drain"):
                action = self.channels[role]
                values = normalized[role]
                self._step(f"{role}.set_voltage_v", lambda: action.set_voltage_v(values.voltage_v, timeout_s=timeout_s), completed)
                self._step(f"{role}.set_current_limit_a", lambda: action.set_current_limit_a(values.current_a, timeout_s=timeout_s), completed)
            self._step("gate.output_on", lambda: self.channels["gate"].set_output_enabled(True, timeout_s=timeout_s), completed)
            self.sleep_fn(self.settle_time_s)
            self._step("drain.output_on", lambda: self.channels["drain"].set_output_enabled(True, timeout_s=timeout_s), completed)
        except Exception as error:
            errors = [error]
            # 上电部分成功时也必须尽力回到安全状态，且不掩盖首个错误。
            for role in ("drain", "gate"):
                try:
                    self.channels[role].set_output_enabled(False, timeout_s=timeout_s)
                except Exception as cleanup_error:
                    errors.append(cleanup_error)
            raise PowerOnFlowError("安全上电失败: " + "; ".join(map(str, errors)), errors) from error
        return tuple(completed)

    @staticmethod
    def _normalize_setup(setup: Mapping[str, PowerChannelSetup | Mapping[str, float]]) -> dict[str, PowerChannelSetup]:
        if set(setup) != {"gate", "drain"}:
            raise ValueError("上电参数必须同时包含 gate 和 drain")
        result: dict[str, PowerChannelSetup] = {}
        for role, value in setup.items():
            if isinstance(value, PowerChannelSetup):
                item = value
            elif isinstance(value, Mapping):
                try:
                    item = PowerChannelSetup(float(value["voltage_v"]), float(value["current_a"]))
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError(f"{role} 必须提供 voltage_v 和 current_a") from error
            else:
                raise TypeError(f"{role} 上电参数类型无效")
            if item.voltage_v < 0 or item.current_a < 0:
                raise ValueError(f"{role} 的电压和限流不能为负数")
            result[role] = item
        return result

    def _step(self, name: str, operation: Callable[[], None], completed: list[str]) -> None:
        self.event_sink(name, "started")
        try:
            operation()
        except Exception:
            self.event_sink(name, "failed")
            raise
        completed.append(name)
        self.event_sink(name, "completed")
