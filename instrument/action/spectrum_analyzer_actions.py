"""Application-facing spectrum-analyzer actions."""

from __future__ import annotations

from typing import Protocol

from .context import ActionBase, ActionContext, ActionLogger


class SpectrumAnalyzerActionDriver(Protocol):
    def connect(self, *, timeout_s: float = 10.0) -> None: ...
    def set_center_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None: ...
    def set_span_hz(self, span_hz: float, *, timeout_s: float = 5.0) -> None: ...
    def measure_peak_power_dbm(self, *, timeout_s: float = 10.0) -> float: ...
    def close(self, *, timeout_s: float = 5.0) -> None: ...


class SpectrumAnalyzerOptionalBandwidthDriver(Protocol):
    def set_resolution_bandwidth_hz(self, bandwidth_hz: float, *, timeout_s: float = 5.0) -> None: ...
    def set_video_bandwidth_hz(self, bandwidth_hz: float, *, timeout_s: float = 5.0) -> None: ...


class SpectrumAnalyzerActions(ActionBase):
    """Stable action entry point; SCPI syntax remains in the driver."""

    def __init__(self, driver: SpectrumAnalyzerActionDriver, *, context: ActionContext | None = None,
                 logger: ActionLogger | None = None) -> None:
        super().__init__(context, logger)
        self._driver = driver

    @property
    def driver(self) -> SpectrumAnalyzerActionDriver:
        return self._driver

    def connect(self, *, timeout_s: float = 10.0) -> None:
        self._call("connect", self._driver.connect, timeout_s=timeout_s)

    def set_center_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None:
        self._call("set_center_frequency_hz", self._driver.set_center_frequency_hz,
                   frequency_hz, timeout_s=timeout_s, event_values={"frequency_hz": frequency_hz})

    def set_span_hz(self, span_hz: float, *, timeout_s: float = 5.0) -> None:
        self._call("set_span_hz", self._driver.set_span_hz, span_hz, timeout_s=timeout_s,
                   event_values={"span_hz": span_hz})

    def set_resolution_bandwidth_hz(self, bandwidth_hz: float, *, timeout_s: float = 5.0) -> None:
        method = getattr(self._driver, "set_resolution_bandwidth_hz", None)
        if method is None:
            raise NotImplementedError("当前频谱仪实现不支持 RBW 配置")
        self._call("set_resolution_bandwidth_hz", method, bandwidth_hz, timeout_s=timeout_s,
                   event_values={"bandwidth_hz": bandwidth_hz})

    def set_video_bandwidth_hz(self, bandwidth_hz: float, *, timeout_s: float = 5.0) -> None:
        method = getattr(self._driver, "set_video_bandwidth_hz", None)
        if method is None:
            raise NotImplementedError("当前频谱仪实现不支持 VBW 配置")
        self._call("set_video_bandwidth_hz", method, bandwidth_hz, timeout_s=timeout_s,
                   event_values={"bandwidth_hz": bandwidth_hz})

    def measure_peak_power_dbm(self, *, timeout_s: float = 10.0) -> float:
        return self._call("measure_peak_power_dbm", self._driver.measure_peak_power_dbm,
                          timeout_s=timeout_s)

    def close(self, *, timeout_s: float = 5.0) -> None:
        self._call("close", self._driver.close, timeout_s=timeout_s)
