"""Pure-Python contract for the GUI measurement controller.

The contract separates page commands from runtime-owned resources. It has no
Qt, worker implementation, persistence, VISA, or SCPI dependency.

``MeasurementCommand`` 的运行契约判据不在这里实现：它经
:func:`presentation.qt.run_contract.is_valid_prepared_run` 委托到唯一判据
(:func:`application.run_service.has_run_contract`)，因此本模块只消费判据、不复刻判据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from application.dto.measurement_requests import (
    StageResultDigest,
    coerce_input_path,
    coerce_stage_digest,
)

from .measurement_state import (
    MeasurementKind,
    MeasurementResultReference,
    MeasurementStatus,
    MeasurementViewState,
)
from .run_contract import is_valid_prepared_run
from .simulation_session import SimulationSession


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


class MeasurementExecutionMode(str, Enum):
    """测量命令的执行模式；正式运行与模拟回放互斥。"""

    FORMAL = "formal"
    SIMULATION = "simulation"


@dataclass(frozen=True)
class MeasurementCommand:
    """页面提交给 controller 的不可变测量命令。

    配置解析、worker 创建、端口所有权和结果保存不属于页面命令职责。

    ``prepared_run`` 是配置阶段创建（或按 run_id 重新打开）的运行契约
    :class:`app.run_context.PreparedRun`；测量页面只携带它，不能携带运行期间可变
    的 GUI 配置状态。**正式模式**下它是必填且必须在构造期就满足契约：判据只有一份
    （:func:`presentation.qt.run_contract.is_valid_prepared_run` → 承载四项要求的
    ``has_run_contract``），缺少运行对象或运行对象不满足契约的命令都在构造期被拒绝，
    而不是留到底层再猜，也不接受“非空但不带契约”的替身。

    **模拟模式**（``mode=SIMULATION``）携带 :class:`SimulationSession`，不携带
    ``prepared_run``：模拟链路没有正式运行、运行目录或仪器端口。两种模式在构造期
    互相拒绝，controller 只按 ``mode`` 选择正式 worker 或模拟回放 worker。

    ``cable_loss_path`` / ``driver_mapping_path`` 是页面在点击开始时冻结的显式校准
    输入来源。命令只保存已规范化的路径，不读文件、不判断阶段归属；阶段目录边界、
    正式结果契约与硬件前校验由应用组装层完成。``*_digest`` 是同一时刻的 SHA-256 与
    文件大小，随命令原样送到测量工厂，用于复核文件没有被替换。
    """

    config_path: str
    prepared_run: Any = None
    options: Mapping[str, Any] = field(default_factory=dict)
    cable_loss_path: Path | str | None = None
    driver_mapping_path: Path | str | None = None
    cable_loss_digest: StageResultDigest | None = None
    driver_mapping_digest: StageResultDigest | None = None
    mode: MeasurementExecutionMode = MeasurementExecutionMode.FORMAL
    simulation_session: SimulationSession | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.config_path, str) or not self.config_path.strip():
            raise ValueError("config_path 不能为空")
        mode = self.mode
        if isinstance(mode, str):
            try:
                mode = MeasurementExecutionMode(mode)
            except ValueError as error:
                allowed = "、".join(item.value for item in MeasurementExecutionMode)
                raise ValueError(
                    f"未知的测量执行模式: {self.mode!r}；可用模式: {allowed}"
                ) from error
        if not isinstance(mode, MeasurementExecutionMode):
            raise TypeError("mode 必须是 MeasurementExecutionMode")
        object.__setattr__(self, "mode", mode)
        if mode is MeasurementExecutionMode.SIMULATION:
            self._validate_simulation_command()
        else:
            self._validate_formal_command()
        object.__setattr__(self, "options", _freeze(self.options))
        cable_loss_path = coerce_input_path(self.cable_loss_path, "cable_loss_path")
        driver_mapping_path = coerce_input_path(
            self.driver_mapping_path, "driver_mapping_path"
        )
        object.__setattr__(self, "cable_loss_path", cable_loss_path)
        object.__setattr__(self, "driver_mapping_path", driver_mapping_path)
        object.__setattr__(
            self,
            "cable_loss_digest",
            coerce_stage_digest(
                self.cable_loss_digest,
                cable_loss_path,
                field_name="cable_loss_digest",
                path_name="cable_loss_path",
            ),
        )
        object.__setattr__(
            self,
            "driver_mapping_digest",
            coerce_stage_digest(
                self.driver_mapping_digest,
                driver_mapping_path,
                field_name="driver_mapping_digest",
                path_name="driver_mapping_path",
            ),
        )

    def _validate_formal_command(self) -> None:
        if self.simulation_session is not None:
            raise ValueError(
                "正式测量命令不能携带模拟会话 simulation_session；"
                "模拟回放必须使用 mode=SIMULATION"
            )
        if self.prepared_run is None:
            raise ValueError(
                "测量命令必须携带运行契约 prepared_run；请先完成配置并点击“连接仪器”"
            )
        if not is_valid_prepared_run(self.prepared_run):
            raise ValueError(
                "测量命令携带的运行对象不满足运行契约（需要 run_id、run_directory、"
                "运行仓储与 hardware_connection_path()）；请先完成配置并点击“连接仪器”"
            )

    def _validate_simulation_command(self) -> None:
        if self.prepared_run is not None:
            raise ValueError(
                "模拟测量命令不能携带正式运行契约 prepared_run；"
                "模拟链路不创建运行目录，也不连接仪器"
            )
        session = self.simulation_session
        if session is None:
            raise ValueError(
                "模拟测量命令必须携带模拟会话 simulation_session"
            )
        if not isinstance(session, SimulationSession):
            raise ValueError(
                "模拟测量命令的 simulation_session 必须是 SimulationSession，"
                f"收到 {type(session).__name__}"
            )


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
    archival: ControllerSignal  # (outcome: application.dto.archival.ArchivalOutcome)
    simulation_completed: ControllerSignal  # (completion: MockReplayCompletion)
    finished: ControllerSignal  # ()
    stopped: ControllerSignal  # (reason: str)
    error: ControllerSignal  # (text: str)
    step_pause: ControllerSignal  # (message: str)
    rejected: ControllerSignal  # (reason: str)
    state_changed: ControllerSignal  # (state: ControllerState)
    thread_finished: ControllerSignal  # ()；worker 线程真正结束并清理后
    stage_start_failed: ControllerSignal  # (reason: str)；阶段在 worker 创建前失败，端口必须按异常阶段关闭


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

    def continue_cable_loss_stage2(self) -> bool:
        """仅在 waiting_for_continue 状态接受线损二阶段继续。"""
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
    "MeasurementExecutionMode",
    "ControllerState",
    "ControllerSignal",
    "MeasurementWorker",
    "WorkerFactory",
    "WorkerFactories",
    "MeasurementControllerSignals",
    "MeasurementControllerProtocol",
    "START_METHODS",
]
