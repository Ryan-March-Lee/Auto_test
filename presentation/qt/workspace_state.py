"""测量工作台的阶段运行状态模型（重构计划步骤 8）。

阶段条自己只保存“画什么”，运行状态由本模型按当前 run 记录：controller 的每一次
:class:`~presentation.qt.measurement_controller_contract.ControllerState` 只折叠成一个
阶段的一项 :class:`~presentation.qt.measurement_stage.StageState`，窗口再把变化推给
外壳。这样做的原因是 controller 只暴露“当前这一次测量”的快照：下一个阶段一启动，
上一次的 ``COMPLETED`` 就不在 controller 里了，但阶段条必须继续显示“01 已完成”。

设计约束（见 ``docs/测量工作台重构计划.md`` 步骤 8）：

- **纯 Python**：不导入 Qt、不复制 ``MeasurementLifecycle``、不解析结果 DTO，也不
  统计频点；只消费 ``ControllerState`` 的 ``kind`` / ``status``。
- **终态保留到 run 重置**：``COMPLETED`` / ``STOPPED`` / ``FAILED`` 不会被同一 run
  内的后续终态覆盖；同一阶段重新开始（``ACTIVE``）才把它拉回进行中，换 run 时全部
  回到 ``PENDING``。
- **只读视图不参与**：``visualization`` 没有测量类型，不进入模型；
  ``test_configuration`` 不是阶段，也不会被记录。``DISABLED`` 表示“本次配置不启用
  该阶段”，由阶段条的 enabled 状态派生，本模型不产生该值。
- **``observe`` 只返回变化**：调用方把返回的 ``{阶段 key: 新状态}`` 逐项写入外壳，
  同一状态的重复事件不会产生多余写入，也不会让页头或页面导航跟着动。
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Iterable, Mapping

from application.dto.measurement_results import MeasurementStatus

from .measurement_controller_contract import ControllerState
from .measurement_stage import (
    MEASUREMENT_STAGES,
    StageDescriptor,
    StageState,
    stage_for_kind,
)

__all__ = ["WorkspaceRunState"]


#: controller 生命周期到阶段条状态的唯一映射；``IDLE`` 不属于任何一个具体阶段，
#: 因此不在表里（``ControllerState`` 也不允许“有测量类型但状态为 idle”）。
_STATUS_STAGE_STATES: Mapping[MeasurementStatus, StageState] = MappingProxyType(
    {
        MeasurementStatus.PREPARING: StageState.ACTIVE,
        MeasurementStatus.RUNNING: StageState.ACTIVE,
        MeasurementStatus.WAITING_FOR_CONTINUE: StageState.ACTIVE,
        MeasurementStatus.STOPPING: StageState.ACTIVE,
        MeasurementStatus.COMPLETED: StageState.COMPLETED,
        MeasurementStatus.FAILED: StageState.FAILED,
        # 取消与急停都是“已停止”：阶段条用警告色表达，不和失败共用红色。
        MeasurementStatus.CANCELLED: StageState.STOPPED,
        MeasurementStatus.EMERGENCY_STOPPED: StageState.STOPPED,
    }
)

_TERMINAL_STATES = frozenset(
    {StageState.COMPLETED, StageState.STOPPED, StageState.FAILED}
)

_NO_CHANGES: Mapping[str, StageState] = MappingProxyType({})


def _normalize_run_id(run_id: str | None) -> str | None:
    """把可选运行标识规整成“去首尾空白后的非空字符串”或 ``None``。"""
    if run_id is None:
        return None
    if not isinstance(run_id, str):
        raise TypeError("run_id 必须是字符串或 None")
    cleaned = run_id.strip()
    return cleaned or None


class WorkspaceRunState:
    """当前 run 下每个测量阶段的显示状态。

    构造时只登记参与运行的阶段（``StageDescriptor.kind is not None``）；只读的
    ``visualization`` 不进入状态表，查询它会得到 ``KeyError``，而不是一个永远
    ``PENDING`` 的假状态。默认阶段集合是
    :data:`~presentation.qt.measurement_stage.MEASUREMENT_STAGES`，也可以传入自定义
    集合；不在集合里的测量类型会被静默忽略，避免自定义阶段出现“能观测、写不进去”。
    """

    def __init__(
        self, stages: Iterable[StageDescriptor] = MEASUREMENT_STAGES
    ) -> None:
        descriptors = tuple(stages)
        keys: list[str] = []
        seen: set[str] = set()
        for descriptor in descriptors:
            if not isinstance(descriptor, StageDescriptor):
                raise TypeError("stages 只能包含 StageDescriptor")
            if descriptor.key in seen:
                raise ValueError(f"阶段 key 重复: {descriptor.key!r}")
            seen.add(descriptor.key)
            if descriptor.kind is not None:
                keys.append(descriptor.key)
        self._keys: tuple[str, ...] = tuple(keys)
        self._states: dict[str, StageState] = {
            key: StageState.PENDING for key in self._keys
        }
        self._run_id: str | None = None

    # ---- 读取 ---------------------------------------------------------------

    def run_id(self) -> str | None:
        """本模型记录的当前 run 标识；尚未绑定运行或已回到欢迎页时为 ``None``。"""
        return self._run_id

    def stage_keys(self) -> tuple[str, ...]:
        """参与运行状态的阶段 key，按构造时的阶段顺序返回。"""
        return self._keys

    def state_for(self, key: str) -> StageState:
        """读取一个阶段的状态。

        非字符串抛 ``TypeError``；未知阶段、``visualization`` 与
        ``test_configuration`` 抛 ``KeyError``：它们不参与运行状态，不能返回一个
        看起来像“未开始”的值。
        """
        if not isinstance(key, str):
            raise TypeError("阶段 key 必须是字符串")
        try:
            return self._states[key]
        except KeyError:
            raise KeyError(f"阶段不参与运行状态: {key!r}") from None

    def snapshot(self) -> Mapping[str, StageState]:
        """返回全部阶段状态的只读副本，供测试或诊断使用。"""
        return MappingProxyType(dict(self._states))

    # ---- 写入：run 与状态 -----------------------------------------------------

    def reset_run(self, run_id: str | None = None) -> Mapping[str, StageState]:
        """切换到 ``run_id`` 并把所有阶段清回 ``PENDING``。

        返回值只包含**真正发生变化**的阶段（``{key: PENDING}``），调用方把这份映射
        推给阶段条即可清掉上一轮的完成/失败；已经是 ``PENDING`` 的阶段不必重写。
        """
        normalized = _normalize_run_id(run_id)
        self._run_id = normalized
        changes: dict[str, StageState] = {}
        for key in self._keys:
            if self._states[key] is StageState.PENDING:
                continue
            self._states[key] = StageState.PENDING
            changes[key] = StageState.PENDING
        return MappingProxyType(changes)

    def observe(self, state: ControllerState) -> Mapping[str, StageState]:
        """折叠一次 controller 状态快照，返回需要写入阶段条的阶段变化。

        规则：

        - ``kind`` 映射到同 key 的阶段；``kind is None``（空闲）不改变任何阶段。
        - ``PREPARING`` / ``RUNNING`` / ``WAITING_FOR_CONTINUE`` / ``STOPPING`` 都是
          进行中；``COMPLETED``、``FAILED``、``CANCELLED`` / ``EMERGENCY_STOPPED``
          分别落到完成、失败、已停止。
        - 终态在 run 重置前保持不变：同一阶段的后续终态事件不会改写它；只有重新
          开始（``ACTIVE``）才把该阶段拉回进行中。
        - 不在本模型阶段集合里的测量类型、以及状态本身没有变化时返回空映射。

        非 ``ControllerState`` 抛 ``TypeError``：这是调用方接线错误，不猜一个替代
        状态，避免把别的对象悄悄显示成测量状态。
        """
        if not isinstance(state, ControllerState):
            raise TypeError("state 必须是 ControllerState")
        if state.kind is None:
            return _NO_CHANGES
        try:
            descriptor = stage_for_kind(state.kind)
        except KeyError:
            # 自定义阶段集合之外的测量类型：本模型不负责它的阶段条状态。
            return _NO_CHANGES
        if descriptor.key not in self._states:
            return _NO_CHANGES
        target = _STATUS_STAGE_STATES.get(state.status)
        if target is None:
            return _NO_CHANGES
        current = self._states[descriptor.key]
        if current is target:
            return _NO_CHANGES
        if target is not StageState.ACTIVE and current in _TERMINAL_STATES:
            return _NO_CHANGES
        self._states[descriptor.key] = target
        return MappingProxyType({descriptor.key: target})
