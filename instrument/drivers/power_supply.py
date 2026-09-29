"""SCPI multi-channel power-supply driver."""

from __future__ import annotations

from ._base import DriverTransport, ScpiDriverBase


class ScpiPowerSupplyDriver(ScpiDriverBase):
    def __init__(self, transport: DriverTransport) -> None:
        super().__init__(transport)
        self._outputs: dict[str, bool] = {}

    def _command(self, channel: str, command: str) -> str:
        if not isinstance(channel, str) or not channel.strip():
            raise ValueError("channel 不能为空")
        return f"{command} {channel}"

    @staticmethod
    def _channel_number(channel: str) -> str:
        if not isinstance(channel, str) or not channel.upper().startswith("CH"):
            raise ValueError("channel 必须使用 CH<n> 格式")
        number = channel[2:].strip()
        if not number.isdigit() or int(number) <= 0:
            raise ValueError("channel 必须使用 CH<n> 格式")
        return number

    def set_voltage_v(self, channel: str, voltage_v: float, *, timeout_s: float = 5.0) -> None:
        voltage_v = self._non_negative(voltage_v, "voltage_v")
        self._set_timeout(timeout_s)
        self._require_connected()
        if self._outputs.get(channel, False):
            raise RuntimeError(f"通道 {channel} 输出开启时不能设置电压")
        number = self._channel_number(channel)
        self.transport.write(f":SOURce{number}:VOLTage {voltage_v:g}")

    def set_current_limit_a(self, channel: str, current_a: float, *, timeout_s: float = 5.0) -> None:
        current_a = self._non_negative(current_a, "current_a")
        self._set_timeout(timeout_s)
        self._require_connected()
        number = self._channel_number(channel)
        self.transport.write(f":SOURce{number}:CURRent {current_a:g}")

    def set_output_enabled(self, channel: str, enabled: bool, *, timeout_s: float = 5.0) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        self._set_timeout(timeout_s)
        self._require_connected()
        self._channel_number(channel)
        self.transport.write(f":OUTPut {channel},{'ON' if enabled else 'OFF'}")
        self._outputs[channel] = enabled

    def read_voltage_v(self, channel: str, *, timeout_s: float = 5.0) -> float:
        self._set_timeout(timeout_s)
        self._channel_number(channel)
        return self._read_float(f":MEASure:VOLTage? {channel}")

    def read_current_a(self, channel: str, *, timeout_s: float = 5.0) -> float:
        self._set_timeout(timeout_s)
        self._channel_number(channel)
        return self._read_float(f":MEASure:CURRent? {channel}")

    def set_voltage(self, channel: str, voltage_v: float, *, timeout_s: float = 5.0) -> None:
        self.set_voltage_v(channel, voltage_v, timeout_s=timeout_s)

    def set_current_limit(self, channel: str, current_a: float, *, timeout_s: float = 5.0) -> None:
        self.set_current_limit_a(channel, current_a, timeout_s=timeout_s)

    def read_voltage(self, channel: str, *, timeout_s: float = 5.0) -> float:
        return self.read_voltage_v(channel, timeout_s=timeout_s)

    def read_current(self, channel: str, *, timeout_s: float = 5.0) -> float:
        return self.read_current_a(channel, timeout_s=timeout_s)
