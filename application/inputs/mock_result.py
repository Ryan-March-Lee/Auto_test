"""Mock 正式结果读取入口：文件 → JSON → 正式结果 DTO。

模拟链路只展示 Mock 数据包里的正式结果，不把它当成运行目录结果或归档产物。读取器
把文件读取、JSON 解码与正式结果契约校验收敛为一次调用，回放线程不需要自己判断
JSON 细节；路径归属与边界仍由 :class:`infrastructure.filesystem.paths.MockDataPathService`
在调用前确认。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from application.dto.measurement_results import (
    MeasurementResult,
    MeasurementType,
    measurement_result_from_document,
)
from application.errors import ConfigurationError


def load_mock_result(
    path: str | Path,
    *,
    expected_measurement_type: MeasurementType | str | None = None,
) -> MeasurementResult:
    """读取并校验一份 Mock 正式结果文件。

    ``expected_measurement_type`` 由模拟会话给出该阶段应当展示的测量类型；类型不一致
    时不把结果交给页面，避免把线损结果当成主功放结果展示。
    """
    candidate = Path(path)
    expected = _coerce_expected_type(expected_measurement_type)
    try:
        text = candidate.read_text(encoding="utf-8-sig")
    except OSError as error:
        raise ConfigurationError(
            f"无法读取 Mock 正式结果文件: {candidate}"
        ) from error
    except UnicodeDecodeError as error:
        raise ConfigurationError(
            f"Mock 正式结果文件不是有效的 UTF-8 文本: {candidate}"
        ) from error
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigurationError(
            "Mock 正式结果文件不是有效的 JSON: "
            f"{candidate}（第 {error.lineno} 行第 {error.colno} 列）"
        ) from error
    if not isinstance(document, Mapping):
        raise ConfigurationError(
            f"Mock 正式结果文件必须是 JSON 对象: {candidate}"
        )
    try:
        result = measurement_result_from_document(document)
    except ValueError as error:
        raise ConfigurationError(
            f"Mock 正式结果 {candidate} 不符合正式结果契约: {error}"
        ) from error
    if expected is not None and result.measurement_type is not expected:
        raise ConfigurationError(
            "Mock 正式结果的测量类型与目标阶段不一致: "
            f"{result.measurement_type.value} != {expected.value}"
        )
    return result


def _coerce_expected_type(
    value: Any,
) -> MeasurementType | None:
    if value is None:
        return None
    if isinstance(value, MeasurementType):
        return value
    try:
        return MeasurementType(value)
    except (TypeError, ValueError) as error:
        allowed = "、".join(kind.value for kind in MeasurementType)
        raise ConfigurationError(
            f"期望的 Mock 测量类型非法: {value!r}；可用类型: {allowed}"
        ) from error


__all__ = ["load_mock_result"]
