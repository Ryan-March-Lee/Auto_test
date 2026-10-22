"""Application-facing pure result parsing API."""

from typing import Any, Mapping

from domain.models import MeasurementResult
from domain.result_reading import (
    get_config_snapshot,
    get_result_metadata,
    get_saturation_points,
    get_sweep_dataframe_data,
    get_sweep_results,
    normalize_frequency_key,
    parse_result_model,
)

__all__ = [
    "build_report_model",
    "get_config_snapshot",
    "get_result_metadata",
    "get_saturation_points",
    "get_sweep_dataframe_data",
    "get_sweep_results",
    "normalize_frequency_key",
    "parse_result_model",
]


def build_report_model(
    data: MeasurementResult | Mapping[str, Any],
    *,
    configuration: Mapping[str, Any] | None = None,
    original_filename: str | None = None,
    measurement_time: str | None = None,
) -> MeasurementResult:
    """把已加载的结果规范化为报告可用的领域模型。

    兼容旧 ``results``、``legacy_payload`` 与 ``canonical_model`` 格式；显式传入的
    ``configuration`` / ``original_filename`` / ``measurement_time`` 覆盖结果中的同名字段。
    没有可绘制扫描点时抛出 ``ValueError``，由调用方决定提示方式。
    """
    if isinstance(data, MeasurementResult):
        payload: dict[str, Any] = dict(data.raw_readings)
    elif isinstance(data, Mapping):
        payload = dict(data)
    else:
        raise ValueError("报告结果必须是映射对象或领域结果模型")

    results = payload.get("results")
    if isinstance(results, Mapping):
        payload = {"results": dict(results)}

    if configuration is not None:
        payload["config"] = configuration
    if measurement_time is not None:
        payload["measurement_time"] = measurement_time
    if original_filename is not None:
        payload["original_filename"] = original_filename

    model = parse_result_model(payload)
    if not model.points:
        raise ValueError("结果中没有可生成报告的扫描点")
    return model
