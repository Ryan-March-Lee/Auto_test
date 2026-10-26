"""只读分析入口对结果对象的最小结构契约。

分析层不导入应用层 DTO，也不接受 ``Mapping[str, Any]`` 形式的结果：调用方必须先把
结果文件解析成正式结果 DTO（``application.dto.measurement_result_from_document``），
再按本协议传入。协议只包含分析与报告真正使用的成员，因此新增结果类型时只需满足
这些成员，而不是在分析层再复制一套结果模型。
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

__all__ = ["ReportResult", "require_report_result", "plain_document"]


class ReportResult(Protocol):
    """分析与报告需要的结果成员。"""

    run_id: str
    measurement_time: str | None
    source_filename: str | None
    configuration_snapshot: Mapping[str, Any]
    calculation_summary: Mapping[str, Any]

    @property
    def measurement_type_value(self) -> str: ...

    @property
    def status_value(self) -> str: ...

    @property
    def point_count(self) -> int: ...

    def report_rows(self) -> tuple[dict[str, Any], ...]: ...

    def sweep_rows(self) -> Mapping[str, tuple[dict[str, Any], ...]]: ...

    def saturation_points(self) -> tuple[dict[str, Any], ...]: ...


_REQUIRED_MEMBERS = (
    "run_id",
    "measurement_type_value",
    "status_value",
    "point_count",
    "report_rows",
    "sweep_rows",
    "saturation_points",
)


def require_report_result(value: Any) -> ReportResult:
    """断言输入是正式结果对象；原始映射与共享基类都会被明确拒绝。"""
    if isinstance(value, Mapping):
        raise ValueError(
            "分析层只接受正式结果 DTO；请先用 measurement_result_from_document() "
            "解析结果文档"
        )
    if getattr(value, "is_formal_result", None) is False:
        raise ValueError(
            "分析层只接受三个具体正式结果 DTO"
            "（CableLoss/DriverPowerMapping/Amplifier），不接受共享基类"
        )
    missing = [name for name in _REQUIRED_MEMBERS if not hasattr(value, name)]
    if missing:
        raise ValueError(f"分析层只接受正式结果 DTO，缺少成员: {', '.join(missing)}")
    return value


def plain_document(value: Any) -> Any:
    """把冻结的结果容器转换为可 JSON 序列化的普通容器。"""
    if is_dataclass(value):
        return {item.name: plain_document(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): plain_document(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain_document(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [plain_document(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value
