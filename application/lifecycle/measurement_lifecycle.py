"""Pure application state machine for measurement runs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from application.dto.measurement_results import MeasurementStatus


class StopIntent(str, Enum):
    STOP = "stop"
    NORMAL = "stop"


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
            },
            MeasurementStatus.RUNNING: {
                MeasurementStatus.RUNNING, MeasurementStatus.WAITING_FOR_CONTINUE,
                MeasurementStatus.STOPPING, MeasurementStatus.COMPLETED,
                MeasurementStatus.FAILED, MeasurementStatus.CANCELLED,
            },
            MeasurementStatus.WAITING_FOR_CONTINUE: {
                MeasurementStatus.RUNNING, MeasurementStatus.STOPPING,
                MeasurementStatus.FAILED, MeasurementStatus.CANCELLED,
            },
            MeasurementStatus.STOPPING: {
                MeasurementStatus.CANCELLED,
                MeasurementStatus.FAILED,
            },
            MeasurementStatus.COMPLETED: set(),
            MeasurementStatus.FAILED: set(),
            MeasurementStatus.CANCELLED: set(),
            # 历史结果中可能出现的终止状态；当前运行不再产生，只作为终态保留。
            MeasurementStatus.EMERGENCY_STOPPED: set(),
        }
        if status not in allowed[self.status]:
            raise ValueError(f"状态 {self.status.value} 不能转换为 {status.value}")
        if status is MeasurementStatus.CANCELLED and not reason:
            raise ValueError("取消必须提供原因")
        if status is MeasurementStatus.FAILED and not error:
            raise ValueError("失败状态必须提供错误信息")
        intent = self.stop_intent
        return MeasurementLifecycle(status, intent, reason, error)

    def prepare(self) -> "MeasurementLifecycle":
        return self.transition(MeasurementStatus.PREPARING)

    def start(self) -> "MeasurementLifecycle":
        return self.transition(MeasurementStatus.RUNNING)

    def wait_for_continue(self) -> "MeasurementLifecycle":
        return self.transition(MeasurementStatus.WAITING_FOR_CONTINUE)

    def continue_running(self) -> "MeasurementLifecycle":
        return self.transition(MeasurementStatus.RUNNING)

    def request_stop(self, reason: str) -> "MeasurementLifecycle":
        if not reason.strip():
            raise ValueError("停止原因不能为空")
        if self.stop_intent is not None:
            return self
        intent = StopIntent.STOP
        if self.status is MeasurementStatus.STOPPING:
            return MeasurementLifecycle(self.status, intent, reason, self.error)
        state = self.transition(MeasurementStatus.STOPPING, reason=reason)
        return MeasurementLifecycle(state.status, intent, reason, state.error)

    def worker_stopped(self, reason: str = "") -> "MeasurementLifecycle":
        final_reason = reason.strip() or self.reason or "用户停止"
        return self.transition(MeasurementStatus.CANCELLED, reason=final_reason)

    def complete(self) -> "MeasurementLifecycle":
        return self.transition(MeasurementStatus.COMPLETED)

    def fail(self, error: str) -> "MeasurementLifecycle":
        return self.transition(MeasurementStatus.FAILED, error=error)
