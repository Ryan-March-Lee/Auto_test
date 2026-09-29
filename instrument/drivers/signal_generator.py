"""SCPI signal-generator driver."""

from __future__ import annotations

import math

from ._base import DriverTransport, ScpiDriverBase


class SignalGeneratorDriver(ScpiDriverBase):
    def __init__(
        self,
        transport: DriverTransport,
        *,
        min_frequency_hz: float | None = None,
        max_frequency_hz: float | None = None,
        min_power_dbm: float | None = None,
        max_power_dbm: float | None = None,
    ) -> None:
        super().__init__(transport)
        self.min_frequency_hz = self._optional_bound(min_frequency_hz, "min_frequency_hz")
        self.max_frequency_hz = self._optional_bound(max_frequency_hz, "max_frequency_hz")
        self.min_power_dbm = self._optional_bound(min_power_dbm, "min_power_dbm")
        self.max_power_dbm = self._optional_bound(max_power_dbm, "max_power_dbm")
        if self.min_frequency_hz is not None and self.min_frequency_hz <= 0:
            raise ValueError("min_frequency_hz 必须大于 0")
        if self.max_frequency_hz is not None and self.max_frequency_hz <= 0:
            raise ValueError("max_frequency_hz 必须大于 0")
        if (
            self.min_frequency_hz is not None
            and self.max_frequency_hz is not None
            and self.max_frequency_hz < self.min_frequency_hz
        ):
            raise ValueError("max_frequency_hz 不能小于 min_frequency_hz")
        if (
            self.min_power_dbm is not None
            and self.max_power_dbm is not None
            and self.max_power_dbm < self.min_power_dbm
        ):
            raise ValueError("max_power_dbm 不能小于 min_power_dbm")
        self.rf_enabled = False
        self.prepared = False

    def set_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None:
        frequency_hz = self._positive(frequency_hz, "frequency_hz")
        if (self.min_frequency_hz is not None and frequency_hz < self.min_frequency_hz) or (
            self.max_frequency_hz is not None and frequency_hz > self.max_frequency_hz
        ):
            raise ValueError("frequency_hz 超出允许范围")
        self._set_timeout(timeout_s)
        self._require_connected()
        self.transport.write(f"FREQ {self._format_number(frequency_hz)}")
        self.prepared = True

    def set_power_dbm(self, power_dbm: float, *, timeout_s: float = 5.0) -> None:
        try:
            power_dbm = float(power_dbm)
        except (TypeError, ValueError) as exc:
            raise ValueError("power_dbm 必须是数字") from exc
        if not math.isfinite(power_dbm):
            raise ValueError("power_dbm 必须是有限数字")
        if (
            (self.min_power_dbm is not None and power_dbm < self.min_power_dbm)
            or (self.max_power_dbm is not None and power_dbm > self.max_power_dbm)
        ):
            raise ValueError("power_dbm 超出允许范围")
        self._set_timeout(timeout_s)
        self._require_connected()
        if self.rf_enabled:
            raise RuntimeError("RF 开启时不能设置功率")
        self.transport.write(f"POW:LEV {self._format_number(power_dbm)}")

    def set_rf_enabled(self, enabled: bool, *, timeout_s: float = 5.0) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        self._set_timeout(timeout_s)
        self._require_connected()
        if enabled and not self.prepared:
            raise RuntimeError("RF 开启前必须完成频率设置")
        self.transport.write(f"OUTP:STAT {self._format_output_state(enabled)}")
        self.rf_enabled = enabled

    def close(self, *, timeout_s: float = 5.0) -> None:
        if self.closed:
            return
        self._set_timeout(timeout_s)
        if self.connected and self.rf_enabled:
            try:
                self.transport.write(f"OUTP:STAT {self._format_output_state(False)}")
                self.rf_enabled = False
            except Exception as exc:
                raise RuntimeError("信号源 RF 关闭失败") from exc
        self.closed = True
        self.connected = False
        try:
            self.transport.close()
        except Exception as exc:
            raise RuntimeError("信号源 transport 关闭失败") from exc

    @staticmethod
    def _format_number(value: float) -> str:
        return f"{value:g}"

    @staticmethod
    def _format_output_state(enabled: bool) -> str:
        return "ON" if enabled else "OFF"

    @staticmethod
    def _finite_bound(value: float, name: str) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} 必须是数字") from exc
        if not math.isfinite(value):
            raise ValueError(f"{name} 必须是有限数字")
        return value

    @classmethod
    def _optional_bound(cls, value: float | None, name: str) -> float | None:
        return None if value is None else cls._finite_bound(value, name)


# Backward-compatible import path and runtime class name.
ScpiSignalGeneratorDriver = SignalGeneratorDriver
