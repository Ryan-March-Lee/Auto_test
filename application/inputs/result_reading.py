"""Application-facing report input API for formal measurement results."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from application.dto.measurement_results import (
    MeasurementResult,
    measurement_result_from_document,
    require_formal_result,
)

__all__ = ["build_report_result", "measurement_result_from_document"]


def build_report_result(
    result: MeasurementResult,
    *,
    configuration: Mapping[str, Any] | None = None,
    original_filename: str | None = None,
    measurement_time: str | None = None,
) -> MeasurementResult:
    """在正式结果 DTO 上叠加报告参数，返回可直接交给分析层的结果对象。

    只接受已解析的正式结果；结果文档必须由调用方在读取边界用
    ``measurement_result_from_document()`` 转换，报告层不再解析 ``Mapping``。
    显式传入的 ``configuration`` / ``original_filename`` / ``measurement_time``
    覆盖结果中的同名字段；没有可绘制的结果行时抛出 ``ValueError``。
    """
    result = require_formal_result(result)
    updates: dict[str, Any] = {}
    if configuration is not None:
        updates["configuration_snapshot"] = configuration
    if original_filename is not None:
        updates["source_filename"] = original_filename
    if measurement_time is not None:
        updates["measurement_time"] = measurement_time
    if updates:
        result = replace(result, **updates)
    if not result.point_count:
        raise ValueError("结果中没有可生成报告的扫描点")
    return result
