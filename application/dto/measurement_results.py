"""统一的应用层测量结果契约。

``payload`` 只用于迁移旧 service 载荷；正式调用方应使用结构化字段或
``to_dict``。所有嵌套容器在构造时递归冻结，避免结果快照被页面或 service
意外修改。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any


class MeasurementType(str, Enum):
    CABLE_LOSS = "cable_loss"
    DRIVER_POWER_MAPPING = "driver_power_mapping"
    AMPLIFIER = "amplifier"


class MeasurementStatus(str, Enum):
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EMERGENCY_STOPPED = "emergency_stopped"


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    if isinstance(value, frozenset):
        return [thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class MeasurementResult(Mapping[str, Any]):
    """跨三类测量共享的结果 DTO。"""

    measurement_type: MeasurementType | str
    status: MeasurementStatus | str
    run_id: str
    data_reference: Mapping[str, Any] = field(default_factory=dict)
    summary: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None
    cleanup: Mapping[str, Any] = field(default_factory=dict)
    payload: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        measurement_type = _enum_value(self.measurement_type, MeasurementType, "measurement_type")
        status = _enum_value(self.status, MeasurementStatus, "status")
        if not self.run_id.strip():
            raise ValueError("run_id 不能为空")
        object.__setattr__(self, "measurement_type", measurement_type)
        object.__setattr__(self, "status", status)
        for name in ("data_reference", "summary", "cleanup", "payload"):
            object.__setattr__(self, name, freeze(getattr(self, name)))
        if status is MeasurementStatus.FAILED and not self.error:
            raise ValueError("failed 结果必须包含 error")

    @classmethod
    def from_payload(
        cls,
        measurement_type: MeasurementType | str,
        payload: Mapping[str, Any],
        *,
        run_id: str,
        status: MeasurementStatus | str | None = None,
        error: str | None = None,
        cleanup: Mapping[str, Any] | None = None,
    ) -> "MeasurementResult":
        value = dict(payload)
        raw_status = status or value.get("status", MeasurementStatus.COMPLETED.value)
        summary = value.get("summary")
        if not isinstance(summary, Mapping):
            summary = _summary_for(value)
        raw_cleanup = cleanup if cleanup is not None else value.get("cleanup", {})
        if not isinstance(raw_cleanup, Mapping):
            raw_cleanup = {}
        return cls(
            measurement_type=measurement_type,
            status=raw_status,
            run_id=run_id,
            data_reference=_data_reference(value),
            summary=summary,
            error=error or value.get("error"),
            cleanup=raw_cleanup,
            payload=value,
        )

    def with_saved_result(
        self,
        *,
        archive_path: str | None = None,
        legacy_copy_path: str | None = None,
    ) -> "MeasurementResult":
        references = dict(self.data_reference)
        if archive_path is not None:
            references["archive_path"] = archive_path
        if legacy_copy_path is not None:
            references["legacy_copy_path"] = legacy_copy_path
        return replace(self, data_reference=references)

    def to_dict(self) -> dict[str, Any]:
        result = thaw(self.payload)
        result.update({
            "measurement_type": self.measurement_type.value,
            "status": self.status.value,
            "run_id": self.run_id,
            "data_reference": thaw(self.data_reference),
            "summary": thaw(self.summary),
            "error": self.error,
            "cleanup": thaw(self.cleanup),
        })
        return result

    def to_legacy_payload(self) -> dict[str, Any]:
        """Return the historical service shape for compatibility file copies."""
        return thaw(self.payload)

    def __getitem__(self, key: str) -> Any:
        return self.payload[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.payload)

    def __len__(self) -> int:
        return len(self.payload)


def _enum_value(value: Any, enum_type: type[Enum], name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{name} 无效: {value!r}，可选值: {allowed}") from error


def _data_reference(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in ("result_path", "archive_path", "raw_data_path")
        if key in payload
    }


def _summary_for(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in ("cable_losses", "power_mapping", "results", "path1_losses", "path2_losses")
        if key in payload
    }
