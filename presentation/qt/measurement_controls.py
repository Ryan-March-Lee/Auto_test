"""三个测量页面共享的按钮可用性绑定（GUI 重构步骤 10）。

页面只声明自己支持哪些动作（开始、停止、取消、紧急停止、继续）；本模块把
controller 的只读状态快照映射成这些动作的可用性，并写到页面提供的按钮上。
状态判据仍来自 :class:`presentation.qt.measurement_controller_contract.ControllerState`
与 :class:`presentation.qt.measurement_state.MeasurementViewState`，这里不新增第二套
测量状态机。

本模块是纯 Python：不导入 PySide6、不连接 signal、不启动或停止 worker，也不改变
测量线程与硬件清理流程。页面仍负责自己的接线图、日志、归档和结果展示。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

from .measurement_state import MeasurementKind, MeasurementStatus


class MeasurementAction(str, Enum):
    """测量页面可以声明支持的控制动作。"""

    START = "start"
    STOP = "stop"
    CANCEL = "cancel"
    EMERGENCY_STOP = "emergency_stop"
    CONTINUE = "continue"


@runtime_checkable
class EnableSink(Protocol):
    """按钮只需要的最小接口；Qt ``QPushButton`` 直接满足。"""

    def setEnabled(self, enabled: bool) -> None:
        ...


@dataclass(frozen=True)
class MeasurementControlAvailability:
    """一次控制按钮可用性结论；只描述按钮，不描述测量业务状态。"""

    enabled: frozenset[MeasurementAction] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "enabled", frozenset(self.enabled))
        for action in self.enabled:
            if not isinstance(action, MeasurementAction):
                raise TypeError("enabled 只能包含 MeasurementAction")

    def is_enabled(self, action: MeasurementAction) -> bool:
        if not isinstance(action, MeasurementAction):
            raise TypeError("action 必须是 MeasurementAction")
        return action in self.enabled


_MISSING = object()


def _state_snapshot(state: Any) -> tuple[MeasurementKind | None, MeasurementStatus, bool]:
    """读取 controller 状态快照；缺少 ``is_active`` 时按状态推导（ViewState 也适用）。

    用户可见行为只取决于“是不是本页面的活动测量”和“是否在等待第二步继续”：
    前者来自 ``ControllerState.is_active``（或 ``MeasurementViewState.status``），
    后者来自 ``status``。缺少 ``is_active`` 的属性才按状态推导；属性存在但不是
    ``bool``（包括显式 ``None``）视为调用方契约错误并抛 ``TypeError``，避免把
    ``"false"`` 之类的值当真值悄悄点亮停止类按钮。
    """
    kind = getattr(state, "kind", None)
    if kind is not None and not isinstance(kind, MeasurementKind):
        raise TypeError("state.kind 必须是 MeasurementKind 或 None")
    status = getattr(state, "status", MeasurementStatus.IDLE)
    if not isinstance(status, MeasurementStatus):
        raise TypeError("state.status 必须是 MeasurementStatus")
    active = getattr(state, "is_active", _MISSING)
    if active is _MISSING:
        active = status is not MeasurementStatus.IDLE and not status.is_terminal
    elif not isinstance(active, bool):
        raise TypeError("state.is_active 必须是 bool 或缺失")
    return kind, status, active


class MeasurementControlPresenter:
    """把测量状态映射成页面声明过的控制按钮可用性。

    ``buttons`` 只登记页面实际拥有的动作；没有登记的动作不会被写入，页面因此可以
    继续保留各页自己的按钮组合。每次状态变化写入**全部**已登记动作：活动测量期间
    关闭“开始”，终止或属于其他测量时恢复“开始”并关闭停止类动作。
    """

    def __init__(
        self,
        kind: MeasurementKind,
        buttons: Mapping[MeasurementAction, EnableSink],
    ) -> None:
        if not isinstance(kind, MeasurementKind):
            raise TypeError("kind 必须是 MeasurementKind")
        if not isinstance(buttons, Mapping):
            raise TypeError("buttons 必须是 MeasurementAction 到控件的映射")
        for action, sink in buttons.items():
            if not isinstance(action, MeasurementAction):
                raise TypeError("buttons 的键必须是 MeasurementAction")
            if not callable(getattr(sink, "setEnabled", None)):
                raise TypeError(f"{action.value} 对应的控件必须提供 setEnabled")
        self._kind = kind
        self._buttons = MappingProxyType(dict(buttons))

    @property
    def kind(self) -> MeasurementKind:
        return self._kind

    @property
    def supported_actions(self) -> frozenset[MeasurementAction]:
        return frozenset(self._buttons)

    def availability(self, state: Any) -> MeasurementControlAvailability:
        """计算某个 controller 状态下本页面各动作的可用性，不写控件。"""
        kind, status, active = _state_snapshot(state)
        if kind is not self._kind or not active:
            return self._availability({MeasurementAction.START})
        enabled = {
            MeasurementAction.STOP,
            MeasurementAction.CANCEL,
            MeasurementAction.EMERGENCY_STOP,
        }
        if status is MeasurementStatus.WAITING_FOR_CONTINUE:
            enabled.add(MeasurementAction.CONTINUE)
        return self._availability(enabled)

    def apply(self, state: Any) -> MeasurementControlAvailability:
        """按 controller 状态更新已登记按钮，并返回本次可用性。"""
        availability = self.availability(state)
        self._write(availability)
        return availability

    def show_idle(self) -> MeasurementControlAvailability:
        """终止事件或页面初始化：恢复“开始”，关闭停止类动作。"""
        availability = self._availability({MeasurementAction.START})
        self._write(availability)
        return availability

    def _availability(
        self, actions: set[MeasurementAction]
    ) -> MeasurementControlAvailability:
        # 页面没有登记的动作不参与计算，避免把“不支持”误显示成“暂时不可用”。
        return MeasurementControlAvailability(
            frozenset(actions & set(self._buttons))
        )

    def _write(self, availability: MeasurementControlAvailability) -> None:
        for action, sink in self._buttons.items():
            sink.setEnabled(action in availability.enabled)


__all__ = [
    "EnableSink",
    "MeasurementAction",
    "MeasurementControlAvailability",
    "MeasurementControlPresenter",
]
