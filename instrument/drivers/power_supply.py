"""SCPI multi-channel power-supply driver."""

from __future__ import annotations

import re

from ._base import DriverTransport, ScpiDriverBase


class ScpiPowerSupplyDriver(ScpiDriverBase):
    def __init__(self, transport: DriverTransport) -> None:
        super().__init__(transport)
        self._outputs: dict[str, bool] = {}

    @staticmethod
    def _validated_channel(channel: str) -> tuple[str, str]:
        if not isinstance(channel, str):
            raise ValueError("channel 必须是 CH1 或 CH2")
        match = re.fullmatch(r"CH([12])", channel)
        if match is None:
            raise ValueError("channel 必须是 CH1 或 CH2")
        return channel, match.group(1)

    def set_voltage_v(self, channel: str, voltage_v: float, *, timeout_s: float = 5.0) -> None:
        voltage_v = self._non_negative(voltage_v, "voltage_v")
        channel, number = self._validated_channel(channel)
        self._set_timeout(timeout_s)
        self._require_connected()
        if self._outputs.get(channel, False):
            raise RuntimeError(f"通道 {channel} 输出开启时不能设置电压")
        self.transport.write(f":SOURce{number}:VOLTage {voltage_v:g}")

    def set_current_limit_a(self, channel: str, current_a: float, *, timeout_s: float = 5.0) -> None:
        current_a = self._non_negative(current_a, "current_a")
        _channel, number = self._validated_channel(channel)
        self._set_timeout(timeout_s)
        self._require_connected()
        self.transport.write(f":SOURce{number}:CURRent {current_a:g}")

    def set_voltage_protection_state(self, channel: str, enabled: bool, *, timeout_s: float = 5.0) -> None:
        channel, number = self._validated_channel(channel)
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        self._set_timeout(timeout_s)
        self._require_connected()
        state = "ON" if enabled else "OFF"
        self.transport.write(f":SOURce{number}:VOLTage:PROTection:STATe {state}")

    def set_current_protection_state(self, channel: str, enabled: bool, *, timeout_s: float = 5.0) -> None:
        channel, number = self._validated_channel(channel)
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        self._set_timeout(timeout_s)
        self._require_connected()
        state = "ON" if enabled else "OFF"
        self.transport.write(f":SOURce{number}:CURRent:PROTection:STATe {state}")

    def set_voltage_protection(
        self,
        channel: str,
        voltage_protection_v: float,
        *,
        timeout_s: float = 5.0,
    ) -> None:
        voltage_protection_v = self._non_negative(voltage_protection_v, "voltage_protection_v")
        _channel, number = self._validated_channel(channel)
        self._set_timeout(timeout_s)
        self._require_connected()
        self.transport.write(f":SOURce{number}:VOLTage:PROTection {voltage_protection_v:g}")

    def set_current_protection(
        self,
        channel: str,
        current_protection_a: float,
        *,
        timeout_s: float = 5.0,
    ) -> None:
        current_protection_a = self._non_negative(current_protection_a, "current_protection_a")
        _channel, number = self._validated_channel(channel)
        self._set_timeout(timeout_s)
        self._require_connected()
        self.transport.write(f":SOURce{number}:CURRent:PROTection {current_protection_a:g}")

    def set_output_enabled(self, channel: str, enabled: bool, *, timeout_s: float = 5.0) -> None:
        channel, _number = self._validated_channel(channel)
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        self._set_timeout(timeout_s)
        self._require_connected()
        self.transport.write(f":OUTPut {channel},{'ON' if enabled else 'OFF'}")
        self._outputs[channel] = enabled

    def read_voltage_v(self, channel: str, *, timeout_s: float = 5.0) -> float:
        channel, _number = self._validated_channel(channel)
        self._set_timeout(timeout_s)
        return self._read_non_negative_float(f":MEASure:VOLTage? {channel}", "voltage_v")

    def read_current_a(self, channel: str, *, timeout_s: float = 5.0) -> float:
        channel, _number = self._validated_channel(channel)
        self._set_timeout(timeout_s)
        return self._read_non_negative_float(f":MEASure:CURRent? {channel}", "current_a")

    def _read_non_negative_float(self, command: str, name: str) -> float:
        value = self._read_float(command)
        if value < 0:
            raise ValueError(f"SCPI 查询返回负的 {name}: {command}: {value}")
        return value

    def set_voltage(self, channel: str, voltage_v: float, *, timeout_s: float = 5.0) -> None:
        self.set_voltage_v(channel, voltage_v, timeout_s=timeout_s)

    def set_current_limit(self, channel: str, current_a: float, *, timeout_s: float = 5.0) -> None:
        self.set_current_limit_a(channel, current_a, timeout_s=timeout_s)

    def read_voltage(self, channel: str, *, timeout_s: float = 5.0) -> float:
        return self.read_voltage_v(channel, timeout_s=timeout_s)

    def read_current(self, channel: str, *, timeout_s: float = 5.0) -> float:
        return self.read_current_a(channel, timeout_s=timeout_s)
