"""纯 Python 的 GUI 测量状态契约。

本模块只描述页面可以消费的状态和结果，不创建 worker，不读取结果文件，
也不依赖 Qt、硬件端口或持久化模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from application.dto.measurement_results import MeasurementStatus
from application.lifecycle import MeasurementLifecycle


class MeasurementKind(str, Enum):
    CABLE_LOSS = "cable_loss"
    DRIVER_MAPPING = "driver_mapping"
    AMPLIFIER = "amplifier"


def _freeze(value: Any) -> Any:
    """递归冻结页面边界上的常见容器，保留标量和业务对象引用。"""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class MeasurementResultReference:
    """页面结果的来源引用，不包含文件路径或持久化格式。"""

    result_id: str
    kind: MeasurementKind
    value: Any
    source: str = "controller"

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MeasurementKind):
            raise TypeError("kind 必须是 MeasurementKind")
        if not isinstance(self.result_id, str):
            raise TypeError("result_id 必须是字符串")
        if not isinstance(self.source, str):
            raise TypeError("source 必须是字符串")
        if not self.result_id.strip():
            raise ValueError("result_id 不能为空")
        if not self.source.strip():
            raise ValueError("结果来源不能为空")
        object.__setattr__(self, "value", _freeze(self.value))


@dataclass(frozen=True)
class MeasurementViewState:
    """测量页面共享的不可变状态快照。"""

    kind: MeasurementKind
    status: MeasurementStatus = MeasurementStatus.IDLE
    progress: int = 0
    message: str = ""
    error_text: str | None = None
    stop_reason: str | None = None
    result: MeasurementResultReference | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MeasurementKind):
            raise TypeError("kind 必须是 MeasurementKind")
        if not isinstance(self.status, MeasurementStatus):
            raise TypeError("status 必须是 MeasurementStatus")
        if not 0 <= self.progress <= 100:
            raise ValueError("progress 必须在 0 到 100 之间")
        if self.status is MeasurementStatus.FAILED and not self.error_text:
            raise ValueError("failed 状态必须包含 error_text")
        if self.status in {MeasurementStatus.CANCELLED, MeasurementStatus.EMERGENCY_STOPPED} and not self.stop_reason:
            raise ValueError("取消或紧急停止状态必须包含 stop_reason")
        if self.result is not None and self.result.kind is not self.kind:
            raise ValueError("结果类型必须与测量类型一致")

    def transition(self, status: MeasurementStatus, **changes: Any) -> "MeasurementViewState":
        """按 GUI 生命周期转换状态，非法路径统一抛出 ``ValueError``。"""
        lifecycle = MeasurementLifecycle(self.status)
        lifecycle.transition(status, reason=changes.get("stop_reason"), error=changes.get("error_text"))
        return replace(self, status=status, **changes)

    def prepare(self) -> "MeasurementViewState":
        return self.transition(MeasurementStatus.PREPARING, error_text=None, stop_reason=None)

    def run(self, *, message: str = "") -> "MeasurementViewState":
        return self.transition(MeasurementStatus.RUNNING, message=message)

    def wait_for_continue(self, message: str = "") -> "MeasurementViewState":
        return self.transition(MeasurementStatus.WAITING_FOR_CONTINUE, message=message)

    def continue_running(self) -> "MeasurementViewState":
        return self.transition(MeasurementStatus.RUNNING)

    def stop(self, reason: str, *, stopping: bool = False) -> "MeasurementViewState":
        if not reason.strip():
            raise ValueError("停止原因不能为空")
        status = MeasurementStatus.STOPPING if stopping else MeasurementStatus.CANCELLED
        return self.transition(status, stop_reason=reason)

    def emergency_stop(self, reason: str) -> "MeasurementViewState":
        if not reason.strip():
            raise ValueError("紧急停止原因不能为空")
        return self.transition(MeasurementStatus.EMERGENCY_STOPPED, stop_reason=reason)

    def fail(self, error_text: str) -> "MeasurementViewState":
        if not error_text.strip():
            raise ValueError("错误文本不能为空")
        return self.transition(MeasurementStatus.FAILED, error_text=error_text)

    def finish(self, result: MeasurementResultReference) -> "MeasurementViewState":
        if result.kind is not self.kind:
            raise ValueError("结果类型必须与测量类型一致")
        return self.transition(MeasurementStatus.COMPLETED, progress=100, result=result)


@dataclass(frozen=True)
class PageResultState:
    """页面显示用的结构化行数据，不等同于持久化结果。"""

    kind: MeasurementKind
    rows: tuple[Mapping[str, Any], ...] = ()
    message: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, MeasurementKind):
            raise TypeError("kind 必须是 MeasurementKind")
        object.__setattr__(self, "rows", tuple(_freeze(row) for row in self.rows))
        object.__setattr__(self, "metadata", _freeze(self.metadata))


@dataclass(frozen=True)
class CableLossPageResultState(PageResultState):
    kind: MeasurementKind = MeasurementKind.CABLE_LOSS

    def __post_init__(self) -> None:
        if self.kind is not MeasurementKind.CABLE_LOSS:
            raise ValueError("CableLossPageResultState 的 kind 必须为 cable_loss")
        super().__post_init__()


@dataclass(frozen=True)
class DriverMappingPageResultState(PageResultState):
    kind: MeasurementKind = MeasurementKind.DRIVER_MAPPING

    def __post_init__(self) -> None:
        if self.kind is not MeasurementKind.DRIVER_MAPPING:
            raise ValueError("DriverMappingPageResultState 的 kind 必须为 driver_mapping")
        super().__post_init__()


@dataclass(frozen=True)
class AmplifierPageResultState(PageResultState):
    kind: MeasurementKind = MeasurementKind.AMPLIFIER

    def __post_init__(self) -> None:
        if self.kind is not MeasurementKind.AMPLIFIER:
            raise ValueError("AmplifierPageResultState 的 kind 必须为 amplifier")
        super().__post_init__()


__all__ = [
    "MeasurementKind",
    "MeasurementStatus",
    "MeasurementResultReference",
    "MeasurementViewState",
    "PageResultState",
    "CableLossPageResultState",
    "DriverMappingPageResultState",
    "AmplifierPageResultState",
]
