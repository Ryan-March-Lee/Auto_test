"""统一的应用层测量结果契约。

``payload`` 只用于迁移旧 service 载荷；正式调用方应使用结构化字段或
``to_dict``。所有嵌套容器在构造时递归冻结，避免结果快照被页面或 service
意外修改。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any


class MeasurementType(str, Enum):
    CABLE_LOSS = "cable_loss"
    DRIVER_POWER_MAPPING = "driver_power_mapping"
    AMPLIFIER = "amplifier"


class MeasurementStatus(str, Enum):
    """统一的测量生命周期状态。"""

    IDLE = "idle"
    PREPARING = "preparing"
    RUNNING = "running"
    WAITING_FOR_CONTINUE = "waiting_for_continue"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EMERGENCY_STOPPED = "emergency_stopped"

    @property
    def is_terminal(self) -> bool:
        return self in {
            MeasurementStatus.COMPLETED,
            MeasurementStatus.FAILED,
            MeasurementStatus.CANCELLED,
            MeasurementStatus.EMERGENCY_STOPPED,
        }


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
    termination: Mapping[str, Any] | None = None
    payload: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)
    configuration_snapshot: Mapping[str, Any] = field(default_factory=dict)
    device_summary: Mapping[str, Any] = field(default_factory=dict)
    raw_data_reference: Mapping[str, Any] = field(default_factory=dict)
    calculation_summary: Mapping[str, Any] = field(default_factory=dict)
    errors: tuple[str, ...] = ()
    cleanup_records: tuple[Mapping[str, Any], ...] = ()
    result_reference: Mapping[str, Any] = field(default_factory=dict)
    created_at: str | None = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        measurement_type = _enum_value(self.measurement_type, MeasurementType, "measurement_type")
        status = _enum_value(self.status, MeasurementStatus, "status")
        if not self.run_id.strip():
            raise ValueError("run_id 不能为空")
        object.__setattr__(self, "measurement_type", measurement_type)
        object.__setattr__(self, "status", status)
        for name in (
            "data_reference", "summary", "cleanup", "termination", "payload",
            "configuration_snapshot", "device_summary", "raw_data_reference",
            "calculation_summary",
            "result_reference",
        ):
            object.__setattr__(self, name, freeze(getattr(self, name)))
        object.__setattr__(self, "errors", _errors(getattr(self, "errors")))
        object.__setattr__(self, "cleanup_records", _records(getattr(self, "cleanup_records")))
        if status is MeasurementStatus.COMPLETED and self.termination is not None:
            raise ValueError("completed 结果不能包含 termination")
        if status is MeasurementStatus.EMERGENCY_STOPPED:
            _validate_termination(self.termination, "emergency_stop")
        if status is MeasurementStatus.CANCELLED:
            _validate_termination(self.termination, None)
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
        termination: Mapping[str, Any] | None = None,
    ) -> "MeasurementResult":
        value = dict(payload)
        raw_status = status or value.get("status", MeasurementStatus.COMPLETED.value)
        summary = value.get("summary")
        if not isinstance(summary, Mapping):
            summary = _summary_for(value)
        raw_cleanup = cleanup if cleanup is not None else value.get("cleanup", {})
        cleanup_records = value.get("cleanup_records", raw_cleanup)
        if isinstance(cleanup_records, Mapping):
            cleanup_records = (cleanup_records,)
        elif not isinstance(cleanup_records, (list, tuple)):
            cleanup_records = ()
        raw_errors = value.get("errors", error or value.get("error"))
        if isinstance(raw_errors, str):
            raw_errors = (raw_errors,)
        elif not isinstance(raw_errors, (list, tuple)):
            raw_errors = ()
        return cls(
            measurement_type=measurement_type,
            status=raw_status,
            run_id=run_id,
            data_reference=_data_reference(value),
            summary=summary,
            error=error or value.get("error"),
            cleanup=raw_cleanup,
            termination=termination if termination is not None else value.get("termination"),
            payload=value,
            configuration_snapshot=value.get("configuration_snapshot", value.get("config", {})),
            device_summary=value.get("device_summary", value.get("resource_snapshot", {})),
            raw_data_reference=value.get("raw_data_reference", _raw_data_reference(value)),
            result_reference=value.get("result_reference", _result_reference(value)),
            calculation_summary=value.get("calculation_summary", value.get("derived_metrics", {})),
            errors=raw_errors,
            cleanup_records=cleanup_records,
            created_at=value.get("created_at", value.get("saved_at")),
        )

    def with_saved_result(
        self,
        *,
        archive_path: str | None = None,
        legacy_copy_path: str | None = None,
    ) -> "MeasurementResult":
        references = dict(self.result_reference)
        if archive_path is not None:
            references["archive_path"] = archive_path
        if legacy_copy_path is not None:
            references["legacy_copy_path"] = legacy_copy_path
        return replace(self, result_reference=references, data_reference=references)

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
            "termination": thaw(self.termination) if self.termination is not None else None,
            "configuration_snapshot": thaw(self.configuration_snapshot),
            "device_summary": thaw(self.device_summary),
            "raw_data_reference": thaw(self.raw_data_reference),
            "calculation_summary": thaw(self.calculation_summary),
            "created_at": self.created_at,
            "errors": list(self.errors),
            "cleanup_records": thaw(self.cleanup_records),
            "result_reference": thaw(self.result_reference),
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


def _raw_data_reference(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: payload[key] for key in ("raw_data_path",) if key in payload}


def _result_reference(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in ("result_path", "archive_path", "legacy_copy_path")
        if key in payload
    }


def _errors(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)):
        raise ValueError("errors 必须是字符串或字符串序列")
    return tuple(str(item) for item in value)


def _records(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        value = (value,)
    if not isinstance(value, (list, tuple)):
        raise ValueError("cleanup_records 必须是映射序列")
    if not all(isinstance(item, Mapping) for item in value):
        raise ValueError("cleanup_records 中的每项必须是映射")
    return tuple(value)


def _summary_for(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: payload[key]
        for key in ("cable_losses", "power_mapping", "results", "path1_losses", "path2_losses")
        if key in payload
    }


def _validate_termination(value: Mapping[str, Any] | None, expected: str | None) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("终止结果必须包含 termination")
    intent = value.get("intent")
    if intent not in {"stop", "cancel", "emergency_stop"}:
        raise ValueError("termination.intent 无效")
    if expected is not None and intent != expected:
        raise ValueError(f"termination.intent 必须为 {expected}")
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("termination.reason 不能为空")
