"""测量工作台的阶段元数据。

本模块只描述“测量区有哪些阶段、每个阶段对应哪个测量类型”，供阶段条、工作台外壳
与窗口共享。它不注册页面、不保存运行状态，也不导入 Qt；页面注册仍只在
``presentation.qt.pages``。阶段顺序固定为 01–04，无驱放测试的驱动映射阶段保留在
元组里，用状态 ``DISABLED`` 表达，不删除、不重排。

``StageDescriptor.key`` 是导航/阶段标识：测量阶段的 key 必须等于对应
``MeasurementKind.value``，``visualization`` 是唯一没有测量类型的阶段。key 与正式
结果词汇（``application.ports...MeasurementStage`` / ``MeasurementType``）不是同一个
命名空间，尤其 ``driver_mapping`` 不等于 ``driver_power_mapping``；不要把 UI key
当作阶段结果目录或正式结果类型使用。

``StageState.DISABLED`` 只表示“本次配置下不启用该阶段”（例如无驱放测试时的驱动
映射），由阶段条的 enabled 状态派生；它不是一次运行的结果，``WorkspaceRunState``
（步骤 8）不产生该值。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping

from .measurement_state import MeasurementKind


class StageState(str, Enum):
    """阶段条使用的显示状态。

    ``PENDING`` 表示未开始；``ACTIVE`` 表示正在测量；``COMPLETED`` 表示已完成；
    ``STOPPED`` 表示被用户停止或取消；``FAILED`` 表示测量失败；``DISABLED`` 表示
    该阶段在当前配置下不启用（例如无驱放测试时的驱动映射），由阶段条的 enabled
    状态派生，不是运行结果。界面/日志需要字符串时取 ``.value``；直接 f-string
    会得到 ``StageState.ACTIVE`` 而不是 ``active``。
    """

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    STOPPED = "stopped"
    FAILED = "failed"
    DISABLED = "disabled"


@dataclass(frozen=True)
class StageDescriptor:
    """一个测量阶段的不可变元数据。

    ``kind`` 是阶段对应的测量类型；``visualization`` 只查看结果、不产生测量，
    因此为 ``None``，不参与测量状态。``subtitle`` 是稳定的目的描述，不放
    “未启用/完成/失败”等运行态文案；运行态由 ``StageState`` 与阶段条摘要表达。
    ``order`` 从 1 开始，编号在显示层补齐两位。
    """

    key: str
    order: int
    title: str
    subtitle: str
    kind: MeasurementKind | None

    def __post_init__(self) -> None:
        if not isinstance(self.key, str):
            raise TypeError("阶段 key 必须是字符串")
        if not self.key.strip():
            raise ValueError("阶段 key 不能为空")
        if self.key != self.key.strip():
            raise ValueError("阶段 key 不能包含首尾空白")
        if isinstance(self.order, bool) or not isinstance(self.order, int):
            raise TypeError("阶段 order 必须是整数")
        if self.order <= 0:
            raise ValueError("阶段 order 必须从 1 开始")
        if not isinstance(self.title, str):
            raise TypeError("阶段 title 必须是字符串")
        if not self.title.strip():
            raise ValueError("阶段 title 不能为空")
        if not isinstance(self.subtitle, str):
            raise TypeError("阶段 subtitle 必须是字符串")
        if not self.subtitle.strip():
            raise ValueError("阶段 subtitle 不能为空")
        if self.kind is not None and not isinstance(self.kind, MeasurementKind):
            raise TypeError("阶段 kind 必须是 MeasurementKind 或 None")
        if self.kind is not None and self.key != self.kind.value:
            raise ValueError("阶段 key 必须等于 kind.value")


MEASUREMENT_STAGES: tuple[StageDescriptor, ...] = (
    StageDescriptor(
        key="cable_loss",
        order=1,
        title="线损测量",
        subtitle="测量线缆与测试链路损耗",
        kind=MeasurementKind.CABLE_LOSS,
    ),
    StageDescriptor(
        key="driver_mapping",
        order=2,
        title="驱动映射",
        subtitle="建立驱动功率与频点映射",
        kind=MeasurementKind.DRIVER_MAPPING,
    ),
    StageDescriptor(
        key="amplifier",
        order=3,
        title="功放测试",
        subtitle="测量主功放输出功率",
        kind=MeasurementKind.AMPLIFIER,
    ),
    StageDescriptor(
        key="visualization",
        order=4,
        title="数据可视化",
        subtitle="只读查看与导出结果",
        kind=None,
    ),
)

_STAGE_BY_KEY: Mapping[str, StageDescriptor] = MappingProxyType(
    {stage.key: stage for stage in MEASUREMENT_STAGES}
)
_STAGE_BY_KIND: Mapping[MeasurementKind, StageDescriptor] = MappingProxyType(
    {stage.kind: stage for stage in MEASUREMENT_STAGES if stage.kind is not None}
)


def stage_for_key(key: str) -> StageDescriptor:
    """按阶段 key 返回元数据；非字符串抛 ``TypeError``，未知 key 抛 ``KeyError``。"""
    if not isinstance(key, str):
        raise TypeError("阶段 key 必须是字符串")
    try:
        return _STAGE_BY_KEY[key]
    except KeyError:
        raise KeyError(f"未知测量阶段: {key!r}") from None


def stage_for_kind(kind: MeasurementKind) -> StageDescriptor:
    """按测量类型返回所属阶段；非 ``MeasurementKind`` 抛出 ``TypeError``。

    ``MeasurementKind`` 之外的输入（包括 ``None``）不是测量类型，不映射到只读的
    可视化阶段；未知类型抛出 ``KeyError``，避免静默落到错误的阶段。
    """
    if not isinstance(kind, MeasurementKind):
        raise TypeError("kind 必须是 MeasurementKind")
    try:
        return _STAGE_BY_KIND[kind]
    except KeyError:
        raise KeyError(f"测量类型没有对应阶段: {kind!r}") from None


__all__ = [
    "StageState",
    "StageDescriptor",
    "MEASUREMENT_STAGES",
    "stage_for_key",
    "stage_for_kind",
]
