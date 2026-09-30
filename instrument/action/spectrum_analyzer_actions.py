"""Application-facing spectrum-analyzer actions."""

from __future__ import annotations

from typing import Protocol


class SpectrumAnalyzerActionDriver(Protocol):
    def connect(self, *, timeout_s: float = 10.0) -> None: ...
    def set_center_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None: ...
    def set_span_hz(self, span_hz: float, *, timeout_s: float = 5.0) -> None: ...
    def set_resolution_bandwidth_hz(self, bandwidth_hz: float, *, timeout_s: float = 5.0) -> None: ...
    def set_video_bandwidth_hz(self, bandwidth_hz: float, *, timeout_s: float = 5.0) -> None: ...
    def measure_peak_power_dbm(self, *, timeout_s: float = 10.0) -> float: ...
    def close(self, *, timeout_s: float = 5.0) -> None: ...


class SpectrumAnalyzerActions:
    """Stable action entry point; SCPI syntax remains in the driver."""

    def __init__(self, driver: SpectrumAnalyzerActionDriver) -> None:
        self.driver = driver

    def connect(self, *, timeout_s: float = 10.0) -> None:
        self.driver.connect(timeout_s=timeout_s)

    def set_center_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None:
        method = getattr(self.driver, "set_center_frequency_hz", None)
        if method is None:
            method = self.driver.configure_center_frequency_hz
        method(frequency_hz, timeout_s=timeout_s)

    def set_span_hz(self, span_hz: float, *, timeout_s: float = 5.0) -> None:
        method = getattr(self.driver, "set_span_hz", None)
        if method is None:
            method = self.driver.configure_bandwidth_hz
        method(span_hz, timeout_s=timeout_s)

    def set_resolution_bandwidth_hz(self, bandwidth_hz: float, *, timeout_s: float = 5.0) -> None:
        method = getattr(self.driver, "set_resolution_bandwidth_hz", None)
        if method is None:
            raise NotImplementedError("当前频谱仪实现不支持 RBW 配置")
        method(bandwidth_hz, timeout_s=timeout_s)

    def set_video_bandwidth_hz(self, bandwidth_hz: float, *, timeout_s: float = 5.0) -> None:
        method = getattr(self.driver, "set_video_bandwidth_hz", None)
        if method is None:
            raise NotImplementedError("当前频谱仪实现不支持 VBW 配置")
        method(bandwidth_hz, timeout_s=timeout_s)

    def measure_peak_power_dbm(self, *, timeout_s: float = 10.0) -> float:
        method = getattr(self.driver, "measure_peak_power_dbm", None)
        if method is None:
            method = self.driver.measure_power_dbm
        return method(timeout_s=timeout_s)

    def close(self, *, timeout_s: float = 5.0) -> None:
        self.driver.close(timeout_s=timeout_s)
