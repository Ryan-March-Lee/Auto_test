"""Pure application state machine for measurement runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from application.dto.measurement_results import MeasurementStatus


class StopIntent(str, Enum):
    STOP = "stop"
    CANCEL = "cancel"
    EMERGENCY_STOP = "emergency_stop"
    NORMAL = "stop"
    EMERGENCY = "emergency_stop"


@dataclass(frozen=True)
class MeasurementLifecycle:
    status: MeasurementStatus = MeasurementStatus.IDLE
    stop_intent: StopIntent | None = None
    reason: str | None = None
    error: str | None = None

    def transition(
        self,
        status: MeasurementStatus,
        *,
        reason: str | None = None,
        error: str | None = None,
    ) -> "MeasurementLifecycle":
        if not isinstance(status, MeasurementStatus):
            raise TypeError("status 必须是 MeasurementStatus")
        allowed = {
            MeasurementStatus.IDLE: {MeasurementStatus.PREPARING},
            MeasurementStatus.PREPARING: {
                MeasurementStatus.RUNNING, MeasurementStatus.STOPPING,
                MeasurementStatus.FAILED, MeasurementStatus.CANCELLED,
                MeasurementStatus.EMERGENCY_STOPPED,
            },
            MeasurementStatus.RUNNING: {
                MeasurementStatus.RUNNING, MeasurementStatus.WAITING_FOR_CONTINUE,
                MeasurementStatus.STOPPING, MeasurementStatus.COMPLETED,
                MeasurementStatus.FAILED, MeasurementStatus.CANCELLED,
                MeasurementStatus.EMERGENCY_STOPPED,
            },
            MeasurementStatus.WAITING_FOR_CONTINUE: {
                MeasurementStatus.RUNNING, MeasurementStatus.STOPPING,
                MeasurementStatus.FAILED, MeasurementStatus.CANCELLED,
                MeasurementStatus.EMERGENCY_STOPPED,
            },
            MeasurementStatus.STOPPING: {
                MeasurementStatus.CANCELLED, MeasurementStatus.EMERGENCY_STOPPED,
                MeasurementStatus.FAILED,
            },
            MeasurementStatus.COMPLETED: set(),
            MeasurementStatus.FAILED: set(),
            MeasurementStatus.CANCELLED: set(),
            MeasurementStatus.EMERGENCY_STOPPED: set(),
        }
        if status not in allowed[self.status]:
            raise ValueError(f"状态 {self.status.value} 不能转换为 {status.value}")
        if status in {MeasurementStatus.CANCELLED, MeasurementStatus.EMERGENCY_STOPPED} and not reason:
            raise ValueError("取消或紧急停止必须提供原因")
        if status is MeasurementStatus.FAILED and not error:
            raise ValueError("失败状态必须提供错误信息")
        intent = self.stop_intent
        if status is MeasurementStatus.EMERGENCY_STOPPED:
            intent = StopIntent.EMERGENCY_STOP
        return MeasurementLifecycle(status, intent, reason, error)

    def prepare(self) -> "MeasurementLifecycle":
        return self.transition(MeasurementStatus.PREPARING)

    def start(self) -> "MeasurementLifecycle":
        return self.transition(MeasurementStatus.RUNNING)

    def wait_for_continue(self) -> "MeasurementLifecycle":
        return self.transition(MeasurementStatus.WAITING_FOR_CONTINUE)

    def continue_running(self) -> "MeasurementLifecycle":
        return self.transition(MeasurementStatus.RUNNING)

    def request_stop(self, reason: str, *, emergency: bool = False) -> "MeasurementLifecycle":
        if not reason.strip():
            raise ValueError("停止原因不能为空")
        if self.stop_intent is StopIntent.EMERGENCY_STOP:
            return self
        if self.stop_intent is not None and not emergency and self.stop_intent is not StopIntent.STOP:
            return self
        intent = StopIntent.EMERGENCY_STOP if emergency else StopIntent.STOP
        if self.status is MeasurementStatus.STOPPING:
            return MeasurementLifecycle(self.status, intent, reason, self.error)
        state = self.transition(MeasurementStatus.STOPPING, reason=reason)
        return MeasurementLifecycle(state.status, intent, reason, state.error)

    def request_cancel(self, reason: str) -> "MeasurementLifecycle":
        if not reason.strip():
            raise ValueError("取消原因不能为空")
        if self.stop_intent is StopIntent.EMERGENCY_STOP:
            return self
        if self.stop_intent is StopIntent.STOP:
            return self
        if self.status is MeasurementStatus.STOPPING:
            return MeasurementLifecycle(self.status, StopIntent.CANCEL, reason, self.error)
        state = self.transition(MeasurementStatus.STOPPING, reason=reason)
        return MeasurementLifecycle(state.status, StopIntent.CANCEL, reason, state.error)

    def worker_stopped(self, reason: str = "") -> "MeasurementLifecycle":
        intent = self.stop_intent or StopIntent.NORMAL
        final_reason = reason.strip() or self.reason or "用户停止"
        target = MeasurementStatus.EMERGENCY_STOPPED if intent is StopIntent.EMERGENCY_STOP else MeasurementStatus.CANCELLED
        return self.transition(target, reason=final_reason)

    def complete(self) -> "MeasurementLifecycle":
        if self.stop_intent is StopIntent.EMERGENCY_STOP:
            return self.worker_stopped()
        return self.transition(MeasurementStatus.COMPLETED)

    def fail(self, error: str) -> "MeasurementLifecycle":
        return self.transition(MeasurementStatus.FAILED, error=error)
