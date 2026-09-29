"""SCPI signal-generator driver."""

from __future__ import annotations

import math

from ._base import DriverTransport, ScpiDriverBase


class ScpiSignalGeneratorDriver(ScpiDriverBase):
    def __init__(self, transport: DriverTransport) -> None:
        super().__init__(transport)
        self.rf_enabled = False
        self.prepared = False

    def set_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None:
        frequency_hz = self._positive(frequency_hz, "frequency_hz")
        self._set_timeout(timeout_s)
        self._require_connected()
        self.transport.write(f"FREQ {frequency_hz:g}")
        self.prepared = True

    def set_power_dbm(self, power_dbm: float, *, timeout_s: float = 5.0) -> None:
        try:
            power_dbm = float(power_dbm)
        except (TypeError, ValueError) as exc:
            raise ValueError("power_dbm 必须是数字") from exc
        if not math.isfinite(power_dbm):
            raise ValueError("power_dbm 必须是有限数字")
        self._set_timeout(timeout_s)
        self._require_connected()
        if self.rf_enabled:
            raise RuntimeError("RF 开启时不能设置功率")
        self.transport.write(f"POW:LEV {power_dbm:g}")

    def set_rf_enabled(self, enabled: bool, *, timeout_s: float = 5.0) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        self._set_timeout(timeout_s)
        self._require_connected()
        if enabled and not self.prepared:
            raise RuntimeError("RF 开启前必须完成频率设置")
        self.transport.write(f"OUTP:STAT {'ON' if enabled else 'OFF'}")
        self.rf_enabled = enabled

    def close(self, *, timeout_s: float = 5.0) -> None:
        if self.closed:
            return
        self._set_timeout(timeout_s)
        if self.connected and self.rf_enabled:
            try:
                self.transport.write("OUTP:STAT OFF")
                self.rf_enabled = False
            except Exception as exc:
                # 保留通信通道，允许调用方修复/重试 RF 关闭。
                raise RuntimeError("信号源 RF 关闭失败") from exc
        self.closed = True
        self.connected = False
        try:
            self.transport.close()
        except Exception as exc:
            raise RuntimeError("信号源 transport 关闭失败") from exc
