"""运行目录历史浏览的只读端口，与正式测量输入端口相互独立。

历史浏览只负责列出、定位和读取 ``runs/<run_id>/`` 运行目录内、由 manifest
登记的阶段结果，供 GUI 查看和导出；正式测量输入只能来自当前运行 manifest
登记并校验过的阶段结果。

端口刻意只暴露 ``run_id`` 与结果标识（相对运行目录的 POSIX 路径），而不是任意
文件系统路径：运行边界校验、manifest 登记检查和 SHA-256 校验都由基础设施实现
负责，调用方无法借历史浏览读取某一运行目录之外的文件，也无法把未登记或被替换
的文件当作结果。端口没有写入方法，也不会把历史文件带入任何运行。

展示语义（对缺失、损坏和部分运行）：

- ``USABLE``：manifest 可解析、``run_id`` 与目录名一致、目录结构完整；
  可以浏览其结果，未完成的阶段只表示这是**部分运行**（``finished_stages`` 小于
  ``total_stages``）。
- ``INCOMPLETE``：manifest 可解析且一致，但运行目录结构不完整（缺少配置快照或
  ``inputs`` / ``results`` 目录）。运行仍可被列出，但其结果可能缺失。
- ``INVALID``：manifest 缺失或无法解析，或 ``run_id`` 与目录名不一致。这类目录
  不会被当作可用历史结果，只能用于诊断。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable

from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    CABLE_LOSS_INPUT_PATH,
    DRIVER_MAPPING_INPUT_PATH,
    STAGE_AMPLIFIER,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
)


# 与历史浏览标签对应的领域结果类型。
RESULT_TYPE_CABLE_LOSS = "cable_loss"
RESULT_TYPE_DRIVER_MAPPING = "driver_power_mapping"
RESULT_TYPE_AMPLIFIER = "amplifier_measurement"
RESULT_TYPE_UNKNOWN = "unknown"

# 阶段名到结果类型的映射；未识别阶段时按结果路径推断。
_STAGE_RESULT_TYPES: Mapping[str, str] = {
    STAGE_CABLE_LOSS: RESULT_TYPE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING: RESULT_TYPE_DRIVER_MAPPING,
    STAGE_AMPLIFIER: RESULT_TYPE_AMPLIFIER,
}

_PATH_RESULT_TYPES: Mapping[str, str] = {
    CABLE_LOSS_INPUT_PATH: RESULT_TYPE_CABLE_LOSS,
    DRIVER_MAPPING_INPUT_PATH: RESULT_TYPE_DRIVER_MAPPING,
    AMPLIFIER_RESULT_PATH: RESULT_TYPE_AMPLIFIER,
}


def result_type_for_result(stage: str | None, relative_path: str) -> str:
    """推断一个运行结果的领域类型，无法识别时返回 ``unknown``。

    优先按阶段名映射，其次按固定结果路径映射，最后按文件名前缀匹配主功放的
    拆分结果文件（``amplifier_measurement_*.json``）。
    """
    if stage in _STAGE_RESULT_TYPES:
        return _STAGE_RESULT_TYPES[stage]
    if relative_path in _PATH_RESULT_TYPES:
        return _PATH_RESULT_TYPES[relative_path]
    name = relative_path.rsplit("/", 1)[-1]
    if name.startswith(RESULT_TYPE_AMPLIFIER):
        return RESULT_TYPE_AMPLIFIER
    if name.startswith(RESULT_TYPE_CABLE_LOSS):
        return RESULT_TYPE_CABLE_LOSS
    if name.startswith(RESULT_TYPE_DRIVER_MAPPING):
        return RESULT_TYPE_DRIVER_MAPPING
    return RESULT_TYPE_UNKNOWN


class RunHistoryState(str, Enum):
    """运行目录作为历史浏览数据源时的可用状态。"""

    USABLE = "usable"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"


@dataclass(frozen=True)
class RunHistoryEntry:
    """一次运行在历史列表中的只读摘要。

    ``result_count`` 是该运行可浏览的阶段结果数量；``INVALID`` 运行的阶段与结果
    计数固定为 0，并通过 ``problem`` 说明判定为损坏/缺失的原因。
    """

    run_id: str
    created_at: datetime
    status: str
    state: RunHistoryState = RunHistoryState.USABLE
    total_stages: int = 0
    finished_stages: int = 0
    result_count: int = 0
    problem: str | None = None

    @property
    def browsable(self) -> bool:
        """只有非 ``INVALID`` 运行才允许浏览其结果。"""
        return self.state is not RunHistoryState.INVALID


@dataclass(frozen=True)
class RunStageResultEntry:
    """运行目录内一个已登记阶段结果的只读元数据。"""

    run_id: str
    result_id: str
    stage: str | None
    result_type: str
    modified_at: datetime
    size_bytes: int | None = None


@runtime_checkable
class RunHistoryRepository(Protocol):
    """运行目录历史结果的只读视图。

    实现方不得写入、移动或删除运行目录及其文件，不得读取运行根目录之外的
    文件，也不得把结果写入任何 manifest；正式测量输入请使用当前运行的阶段
    记录器。
    """

    def list_runs(self) -> tuple[RunHistoryEntry, ...]:
        """返回运行根目录下的全部运行摘要，按创建时间从新到旧排序。"""

    def list_results(self, run_id: str) -> tuple[RunStageResultEntry, ...]:
        """返回指定运行 manifest 登记、可浏览的阶段结果元数据。"""

    def load(self, run_id: str, result_id: str) -> Mapping[str, Any]:
        """按运行与结果标识读取已登记且校验通过的 JSON 结果，不写回。"""

    def read_bytes(self, run_id: str, result_id: str) -> bytes:
        """按运行与结果标识读取已登记且校验通过的原始字节，用于逐字节导出。"""


__all__ = [
    "RESULT_TYPE_AMPLIFIER",
    "RESULT_TYPE_CABLE_LOSS",
    "RESULT_TYPE_DRIVER_MAPPING",
    "RESULT_TYPE_UNKNOWN",
    "RunHistoryEntry",
    "RunHistoryRepository",
    "RunHistoryState",
    "RunStageResultEntry",
    "result_type_for_result",
]
