"""测量运行时的纯 Python 生命周期决策。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .measurement_lifecycle import StopIntent
from application.dto.measurement_results import MeasurementStatus


@dataclass(frozen=True)
class RuntimeDecision:
    status: MeasurementStatus
    reason: str | None = None
    error: str | None = None
    result: Any = None


class MeasurementRuntimeCoordinator:
    """集中处理 worker 终态和终止意图的优先级。"""

    def __init__(self) -> None:
        self.stop_intent: StopIntent | None = None

    def request(self, intent: StopIntent) -> None:
        if self.stop_intent is StopIntent.EMERGENCY_STOP:
            return
        if self.stop_intent is None or intent is StopIntent.EMERGENCY_STOP:
            self.stop_intent = intent

    def stopped(self, reason: str = "") -> RuntimeDecision:
        intent = self.stop_intent or StopIntent.STOP
        if intent is StopIntent.EMERGENCY_STOP:
            return RuntimeDecision(MeasurementStatus.EMERGENCY_STOPPED, reason or "紧急停止")
        if intent is StopIntent.CANCEL:
            return RuntimeDecision(MeasurementStatus.CANCELLED, reason or "任务已取消")
        return RuntimeDecision(MeasurementStatus.CANCELLED, reason or "用户停止")

    def finished(self, result: Any) -> RuntimeDecision:
        if self.stop_intent is not None:
            return self.stopped()
        if result is None:
            return RuntimeDecision(MeasurementStatus.FAILED, error="worker 完成但未提供测量结果")
        return RuntimeDecision(MeasurementStatus.COMPLETED, result=result)

    @staticmethod
    def failed(error: str) -> RuntimeDecision:
        return RuntimeDecision(MeasurementStatus.FAILED, error=error or "测量失败")


__all__ = ["RuntimeDecision", "MeasurementRuntimeCoordinator"]
