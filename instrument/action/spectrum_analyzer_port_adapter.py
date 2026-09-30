"""Explicitly adapt the legacy spectrum-analyzer port names to action names."""

from __future__ import annotations

from typing import Protocol


class SpectrumAnalyzerLegacyPort(Protocol):
    def connect(self, *, timeout_s: float = 10.0) -> None: ...
    def configure_center_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None: ...
    def configure_bandwidth_hz(self, bandwidth_hz: float, *, timeout_s: float = 5.0) -> None: ...
    def measure_power_dbm(self, *, timeout_s: float = 10.0) -> float: ...
    def close(self, *, timeout_s: float = 5.0) -> None: ...


class SpectrumAnalyzerPortAdapter:
    def __init__(self, port: SpectrumAnalyzerLegacyPort) -> None:
        self._port = port

    def connect(self, *, timeout_s: float = 10.0) -> None:
        self._port.connect(timeout_s=timeout_s)

    def set_center_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None:
        self._port.configure_center_frequency_hz(frequency_hz, timeout_s=timeout_s)

    def set_span_hz(self, span_hz: float, *, timeout_s: float = 5.0) -> None:
        self._port.configure_bandwidth_hz(span_hz, timeout_s=timeout_s)

    def set_resolution_bandwidth_hz(self, bandwidth_hz: float, *, timeout_s: float = 5.0) -> None:
        raise NotImplementedError("当前频谱仪端口不支持 RBW 配置")

    def set_video_bandwidth_hz(self, bandwidth_hz: float, *, timeout_s: float = 5.0) -> None:
        raise NotImplementedError("当前频谱仪端口不支持 VBW 配置")

    def measure_peak_power_dbm(self, *, timeout_s: float = 10.0) -> float:
        return self._port.measure_power_dbm(timeout_s=timeout_s)

    def close(self, *, timeout_s: float = 5.0) -> None:
        self._port.close(timeout_s=timeout_s)
