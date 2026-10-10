"""旧测量结果载荷的短期兼容适配器。

应用层和展示层只应通过本模块读取历史 service 字段。正式结果元数据仍由
``MeasurementResult`` 提供，适配器不负责业务计算或持久化。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .measurement_results import MeasurementResult, MeasurementStatus, thaw


def legacy_status(value: Any) -> tuple[MeasurementStatus, str | None]:
    """Translate historical service status without expanding the new protocol."""
    mapping = {
        "waiting": (MeasurementStatus.WAITING_FOR_CONTINUE, "waiting"),
        "finished": (MeasurementStatus.COMPLETED, "finished"),
        "stopped": (MeasurementStatus.CANCELLED, "stopped"),
    }
    if value in mapping:
        return mapping[value]
    return MeasurementStatus(value), None


def legacy_result(
    measurement_type: Any,
    value: Mapping[str, Any],
    *,
    run_id: str,
) -> MeasurementResult:
    """Build a normalized result from a historical service payload."""
    payload = dict(value)
    status, source_status = legacy_status(payload.get("status", MeasurementStatus.COMPLETED.value))
    if source_status == "waiting":
        payload["pending_action"] = "connect_path_2"
    termination = None
    if source_status == "stopped":
        termination = {
            "intent": payload.get("termination_intent", "stop"),
            "reason": payload.get("termination_reason", "用户停止"),
        }
    return MeasurementResult.from_payload(
        measurement_type,
        payload,
        run_id=run_id,
        status=status,
        termination=termination,
    )


def legacy_payload(value: MeasurementResult | Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a detached mapping for legacy result consumers."""
    if isinstance(value, MeasurementResult):
        return thaw(value.payload)
    if isinstance(value, Mapping):
        return dict(value)
    return {}


__all__ = ["legacy_payload", "legacy_result", "legacy_status"]
