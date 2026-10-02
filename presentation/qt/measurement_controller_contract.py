"""Pure-Python contract for the GUI measurement controller.

The contract separates page commands from runtime-owned resources. It has no
Qt, worker implementation, persistence, VISA, or SCPI dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from .measurement_state import (
    MeasurementKind,
    MeasurementResultReference,
    MeasurementStatus,
    MeasurementViewState,
)


def _freeze(value: Any) -> Any:
    """递归冻结页面命令中的常见容器。"""
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
class MeasurementCommand:
    """页面提交给 controller 的不可变测量命令。

    配置解析、worker 创建、端口所有权和结果保存不属于页面命令职责。
    """

    config_path: str
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.config_path, str) or not self.config_path.strip():
            raise ValueError("config_path 不能为空")
        object.__setattr__(self, "options", _freeze(self.options))


# 迁移期间保留旧名称，新的页面代码应使用 MeasurementCommand。
MeasurementRequest = MeasurementCommand


@dataclass(frozen=True)
class ControllerState:
    """controller 对外暴露的只读生命周期快照。"""

    kind: MeasurementKind | None = None
    status: MeasurementStatus = MeasurementStatus.IDLE
    view_state: MeasurementViewState | None = None

    def __post_init__(self) -> None:
        if self.kind is None:
            if self.status is not MeasurementStatus.IDLE or self.view_state is not None:
                raise ValueError("idle 状态不能包含活动测量或 view_state")
            return
        if self.status is MeasurementStatus.IDLE:
            raise ValueError("有测量类型时不能是 idle 状态")
        if self.view_state is None:
            raise ValueError("非 idle 状态必须包含 view_state")
        if self.view_state.kind is not self.kind:
            raise ValueError("view_state 的测量类型必须与 controller 状态一致")
        if self.view_state.status is not self.status:
            raise ValueError("view_state 状态必须与 controller 状态一致")

    @property
    def is_active(self) -> bool:
        return self.kind is not None and not self.status.is_terminal


@runtime_checkable
class ControllerSignal(Protocol):
    """Qt signal 和纯 Python fake signal 共同需要的最小接口。"""

    def connect(self, slot: Callable[..., Any]) -> Any:
        ...

    def disconnect(self, slot: Callable[..., Any]) -> Any:
        ...


@runtime_checkable
class MeasurementWorker(Protocol):
    """controller 所需的最小 worker 接口。"""

    signals: Any

    def start(self) -> Any:
        ...

    def stop(self) -> Any:
        """执行普通停止。"""
        ...

    def emergency_stop(self) -> Any:
        """执行不可降级的紧急停止。"""
        ...


@runtime_checkable
class WorkerFactory(Protocol):
    """按测量类型创建 worker 的注入点。"""

    def __call__(
        self,
        command: MeasurementCommand,
        *,
        measurement_port: Any = None,
    ) -> MeasurementWorker:
        ...


WorkerFactories = Mapping[MeasurementKind, WorkerFactory]


@runtime_checkable
class MeasurementControllerSignals(Protocol):
    """controller 对页面转发的稳定事件及其载荷语义。"""

    progress: ControllerSignal  # (value: int)
    message: ControllerSignal  # (text: str)
    data_update: ControllerSignal  # (data: Mapping[str, Any])
    result: ControllerSignal  # (reference: MeasurementResultReference)
    finished: ControllerSignal  # ()
    stopped: ControllerSignal  # (reason: str)
    error: ControllerSignal  # (text: str)
    step_pause: ControllerSignal  # (message: str)
    rejected: ControllerSignal  # (reason: str)
    state_changed: ControllerSignal  # (state: ControllerState)


@runtime_checkable
class MeasurementControllerProtocol(Protocol):
    """页面使用的 controller 命令、状态和错误语义。"""

    signals: MeasurementControllerSignals

    @property
    def state(self) -> ControllerState:
        ...

    def start_cable_loss(self, command: MeasurementCommand) -> bool:
        """参数错误抛出；并发冲突返回 False 并发出 rejected。"""
        ...

    def start_driver_mapping(self, command: MeasurementCommand) -> bool:
        ...

    def start_amplifier(self, command: MeasurementCommand) -> bool:
        ...

    def stop(self) -> bool:
        """请求普通停止；无可停止测量时返回 False 并拒绝。"""
        ...

    def emergency_stop(self) -> bool:
        """请求紧急停止；不得调用普通 stop 代替。"""
        ...

    def continue_cable_loss(self) -> bool:
        """仅在 waiting_for_continue 状态接受线损第二步继续。"""
        ...


START_METHODS: Mapping[MeasurementKind, str] = MappingProxyType(
    {
        MeasurementKind.CABLE_LOSS: "start_cable_loss",
        MeasurementKind.DRIVER_MAPPING: "start_driver_mapping",
        MeasurementKind.AMPLIFIER: "start_amplifier",
    }
)


__all__ = [
    "MeasurementCommand",
    "MeasurementRequest",
    "ControllerState",
    "ControllerSignal",
    "MeasurementWorker",
    "WorkerFactory",
    "WorkerFactories",
    "MeasurementControllerSignals",
    "MeasurementControllerProtocol",
    "START_METHODS",
]
