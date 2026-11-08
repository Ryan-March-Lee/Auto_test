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
    """集中处理 worker 终态和停止意图。"""

    def __init__(self) -> None:
        self.stop_intent: StopIntent | None = None

    def request(self, intent: StopIntent) -> None:
        self.stop_intent = intent

    def stopped(self, reason: str = "") -> RuntimeDecision:
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
