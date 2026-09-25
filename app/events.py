"""不依赖 Qt 的测量事件协议。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, TypeAlias


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class MeasurementEvent:
    run_id: str
    timestamp: datetime = field(default_factory=_now)


@dataclass(frozen=True)
class ProgressEvent(MeasurementEvent):
    fraction: float = 0.0
    stage: str = ""


@dataclass(frozen=True)
class MessageEvent(MeasurementEvent):
    message: str = ""
    level: str = "info"


@dataclass(frozen=True)
class RealtimeDataEvent(MeasurementEvent):
    measurement: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckpointEvent(MeasurementEvent):
    checkpoint: str = ""
    prompt: str = ""
    required: bool = True


@dataclass(frozen=True)
class CompletedEvent(MeasurementEvent):
    result_id: str = ""


@dataclass(frozen=True)
class StoppedEvent(MeasurementEvent):
    reason: str = ""
    emergency: bool = False


@dataclass(frozen=True)
class FailedEvent(MeasurementEvent):
    error_type: str = ""
    message: str = ""


MeasurementEventType: TypeAlias = (
    ProgressEvent | MessageEvent | RealtimeDataEvent | CheckpointEvent |
    CompletedEvent | StoppedEvent | FailedEvent
)


class EventSink(Protocol):
    def publish(self, event: MeasurementEventType) -> None:
        """接收事件；实现不得要求 Qt。"""
