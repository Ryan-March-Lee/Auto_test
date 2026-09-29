"""SCPI spectrum-analyzer driver."""

from __future__ import annotations

from ._base import DriverTransport, ScpiDriverBase


class ScpiSpectrumAnalyzerDriver(ScpiDriverBase):
    def __init__(self, transport: DriverTransport) -> None:
        super().__init__(transport)
        self._center_frequency_configured = False
        self._bandwidth_configured = False

    def set_center_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None:
        frequency_hz = self._positive(frequency_hz, "frequency_hz")
        self._set_timeout(timeout_s)
        self._require_connected()
        self.transport.write(f"FREQ:CENT {frequency_hz:g}")
        self._center_frequency_configured = True

    def configure_bandwidth_hz(self, bandwidth_hz: float, *, timeout_s: float = 5.0) -> None:
        bandwidth_hz = self._positive(bandwidth_hz, "bandwidth_hz")
        self._set_timeout(timeout_s)
        self._require_connected()
        self.transport.write(f"FREQ:SPAN {bandwidth_hz:g}")
        self._bandwidth_configured = True

    def measure_peak_power_dbm(self, *, timeout_s: float = 10.0) -> float:
        self._set_timeout(timeout_s)
        if not self._center_frequency_configured or not self._bandwidth_configured:
            raise RuntimeError("频谱仪必须先完成配置")
        self.transport.write("CALC:MARK1:MAX")
        return self._read_float("CALC:MARK1:Y?")

    def configure_center_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None:
        self.set_center_frequency_hz(frequency_hz, timeout_s=timeout_s)

    def measure_power_dbm(self, *, timeout_s: float = 10.0) -> float:
        return self.measure_peak_power_dbm(timeout_s=timeout_s)
