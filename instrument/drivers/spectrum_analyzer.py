"""SCPI spectrum-analyzer driver."""

from __future__ import annotations

import math
import re

from ._base import DriverTransport, ScpiDriverBase


class SpectrumAnalyzerMeasurementError(RuntimeError, ValueError):
    """Raised when a spectrum-analyzer measurement cannot be interpreted."""


class SpectrumAnalyzerDeviceError(SpectrumAnalyzerMeasurementError):
    """Raised when the analyzer returns a SCPI device-error response."""


class ScpiSpectrumAnalyzerDriver(ScpiDriverBase):
    def __init__(self, transport: DriverTransport, *,
                 min_frequency_hz: float | None = None,
                 max_frequency_hz: float | None = None,
                 min_span_hz: float | None = None,
                 max_span_hz: float | None = None,
                 min_resolution_bandwidth_hz: float | None = None,
                 max_resolution_bandwidth_hz: float | None = None,
                 min_video_bandwidth_hz: float | None = None,
                 max_video_bandwidth_hz: float | None = None) -> None:
        super().__init__(transport)
        self._center_frequency_configured = False
        self._bandwidth_configured = False
        raw_limits = {
            "frequency_hz": (min_frequency_hz, max_frequency_hz),
            "span_hz": (min_span_hz, max_span_hz),
            "resolution_bandwidth_hz": (min_resolution_bandwidth_hz, max_resolution_bandwidth_hz),
            "video_bandwidth_hz": (min_video_bandwidth_hz, max_video_bandwidth_hz),
        }
        self._range_limits = {
            name: self._validate_limits(name, limits)
            for name, limits in raw_limits.items()
        }

    def set_center_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None:
        frequency_hz = self._validate_value(frequency_hz, "frequency_hz")
        self._set_timeout(timeout_s)
        self._require_connected()
        self.transport.write(f"FREQ:CENT {frequency_hz:g}")
        self._center_frequency_configured = True

    def set_span_hz(self, span_hz: float, *, timeout_s: float = 5.0) -> None:
        span_hz = self._validate_value(span_hz, "span_hz")
        self._set_timeout(timeout_s)
        self._require_connected()
        self.transport.write(f"FREQ:SPAN {span_hz:g}")
        self._bandwidth_configured = True

    def set_resolution_bandwidth_hz(self, bandwidth_hz: float, *, timeout_s: float = 5.0) -> None:
        bandwidth_hz = self._validate_value(bandwidth_hz, "resolution_bandwidth_hz")
        self._set_timeout(timeout_s)
        self._require_connected()
        self.transport.write(f"BAND:RES {bandwidth_hz:g}")

    def set_video_bandwidth_hz(self, bandwidth_hz: float, *, timeout_s: float = 5.0) -> None:
        bandwidth_hz = self._validate_value(bandwidth_hz, "video_bandwidth_hz")
        self._set_timeout(timeout_s)
        self._require_connected()
        self.transport.write(f"BAND:VID {bandwidth_hz:g}")

    def measure_peak_power_dbm(self, *, timeout_s: float = 10.0) -> float:
        self._set_timeout(timeout_s)
        self._require_connected()
        if not self._center_frequency_configured or not self._bandwidth_configured:
            raise SpectrumAnalyzerMeasurementError("频谱仪必须先完成中心频率和 Span 配置")
        self.transport.write("CALC:MARK1:MAX")
        response = self.transport.query("CALC:MARK1:Y?").strip()
        if not response:
            raise SpectrumAnalyzerMeasurementError("峰值功率查询返回空值")
        if _is_scpi_error_response(response):
            raise SpectrumAnalyzerDeviceError(
                f"频谱仪返回设备错误: CALC:MARK1:Y?: {response}"
            )
        try:
            value = float(response)
        except (TypeError, ValueError) as exc:
            raise SpectrumAnalyzerMeasurementError(
                f"峰值功率查询返回非数字: CALC:MARK1:Y?: {response}"
            ) from exc
        if not math.isfinite(value):
            raise SpectrumAnalyzerMeasurementError(
                f"峰值功率查询返回非有限数字: CALC:MARK1:Y?: {response}"
            )
        return value

    def configure_bandwidth_hz(self, bandwidth_hz: float, *, timeout_s: float = 5.0) -> None:
        """Legacy port alias: bandwidth is the analyzer frequency span."""
        self.set_span_hz(bandwidth_hz, timeout_s=timeout_s)

    def configure_center_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None:
        self.set_center_frequency_hz(frequency_hz, timeout_s=timeout_s)

    def measure_power_dbm(self, *, timeout_s: float = 10.0) -> float:
        return self.measure_peak_power_dbm(timeout_s=timeout_s)

    @staticmethod
    def _validate_limits(
        name: str, limits: tuple[float | None, float | None]
    ) -> tuple[float | None, float | None]:
        minimum, maximum = limits
        normalized: list[float | None] = []
        for value in limits:
            if value is None:
                normalized.append(None)
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{name} 范围必须是数字") from exc
            if not math.isfinite(numeric) or numeric <= 0:
                raise ValueError(f"{name} 范围必须是有限正数")
            normalized.append(numeric)
        minimum, maximum = normalized
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(f"{name} 最小范围不能大于最大范围")
        return minimum, maximum

    def _validate_value(self, value: float, name: str) -> float:
        value = self._positive(value, name)
        minimum, maximum = self._range_limits[name]
        if minimum is not None and value < float(minimum):
            raise ValueError(f"{name} 小于设备允许的最小值 {minimum:g}")
        if maximum is not None and value > float(maximum):
            raise ValueError(f"{name} 大于设备允许的最大值 {maximum:g}")
        return value


def _is_scpi_error_response(response: str) -> bool:
    """Recognize common IEEE-488 SCPI error-list responses."""
    return bool(re.match(r"^[+-]\d{3}\s*(?:,|$)", response))
