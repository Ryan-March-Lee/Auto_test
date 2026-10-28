"""历史运行管理的正式 DTO：运行、阶段与交付产物的结构化视图。

重构计划步骤 7 把原来的“导出页面”收敛成“历史运行/结果管理”：页面需要一次性看到
**运行 → 阶段 → 正式结果 → 交付产物** 的状态，而不是只有一份文件列表。本模块只定义
这份只读视图的数据契约：

- 事实来源只有运行目录与 manifest：可用性、阶段状态、产物状态都由基础设施适配器读出
  后填进 DTO，DTO 自己**不读文件、不判断文件是否存在**，也不拼接导出默认路径。
- 路径只以运行目录内的受控相对路径出现（阶段结果路径与产物路径），不携带绝对路径，
  页面因此无法绕过运行目录和 manifest 访问任意文件。
- DTO 不携带页面文案，不依赖 Qt；中文措辞由表现层的视图模型生成。
- 可用性只有三档：``usable``（可浏览）、``incomplete``（结构不完整但仍可列出）、
  ``invalid``（manifest 缺失/损坏，或运行标识与目录名不一致）。``invalid`` 运行不会被
  当作可用历史，也不允许导出或重新生成。

合法组合由 ``__post_init__`` 校验：``invalid`` 运行必须给出阻塞原因，其余运行必须是
带时区的时间、合法的运行/阶段/产物状态；阶段与产物只能来自 manifest 已登记的事实。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from domain.run.models import (
    ArtifactKind,
    ArtifactStatus,
    RunStatus,
    StageStatus,
    coerce_status,
    validate_run_id,
)


# 视为“阶段已走完”的状态：完成或有意跳过，与归档服务的判定保持一致。
_FINISHED_STAGE_STATUSES = frozenset({StageStatus.COMPLETED, StageStatus.SKIPPED})


class RunArchiveState(str, Enum):
    """运行目录作为历史数据源时的可用性。

    ``USABLE`` 与 ``INCOMPLETE`` 都允许浏览其结果；区别只在于运行目录结构是否完整。
    ``INVALID`` 表示无法确认这次运行，只能用于诊断。
    """

    USABLE = "usable"
    INCOMPLETE = "incomplete"
    INVALID = "invalid"

    @property
    def browsable(self) -> bool:
        """是否允许浏览、导出或重新生成该运行的内容。"""
        return self is not RunArchiveState.INVALID


@dataclass(frozen=True)
class RunStageSummary:
    """运行内一个阶段的历史状态。

    ``result_id`` 是阶段正式结果在运行目录内的相对路径，只在阶段已完成时存在；它不
    携带绝对路径，也不代表文件一定可读——可读性由历史查询端口在使用时校验。
    """

    name: str
    status: StageStatus
    result_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("历史阶段名不能为空")
        object.__setattr__(
            self,
            "status",
            coerce_status(self.status, StageStatus, field_name=f"阶段 {self.name} 的状态"),
        )
        if self.result_id is not None and (
            not isinstance(self.result_id, str) or not self.result_id.strip()
        ):
            raise ValueError(f"阶段 {self.name} 的结果标识必须是非空字符串")

    @property
    def finished(self) -> bool:
        return self.status in _FINISHED_STAGE_STATUSES


@dataclass(frozen=True)
class RunArtifactSummary:
    """运行内一条交付产物的历史状态（CSV 或测试报告）。

    ``stage`` 是产生该产物的阶段名，由适配器按来源正式结果对应到 manifest 的阶段；
    无法对应时为 ``None``，这种产物不能作为“重新生成”的输入。
    """

    kind: ArtifactKind
    status: ArtifactStatus
    source_result: str | None = None
    stage: str | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "kind",
            coerce_status(self.kind, ArtifactKind, field_name="产物类型"),
        )
        object.__setattr__(
            self,
            "status",
            coerce_status(self.status, ArtifactStatus, field_name="产物状态"),
        )
        for name in ("source_result", "stage", "error"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{name} 必须是非空字符串")


@dataclass(frozen=True)
class RunHistorySummary:
    """一次历史运行的只读摘要：运行状态、阶段与交付产物状态。

    ``run_status`` 为 ``None`` 表示 manifest 不可用，运行状态未知；这种运行的 ``state``
    必然是 ``INVALID``，且不携带阶段与产物记录。
    """

    run_id: str
    created_at: datetime
    state: RunArchiveState
    run_status: RunStatus | None = None
    stages: tuple[RunStageSummary, ...] = ()
    artifacts: tuple[RunArtifactSummary, ...] = ()
    problem: str | None = None

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise ValueError("历史运行的创建时间必须是带时区的 datetime")
        state = (
            self.state
            if isinstance(self.state, RunArchiveState)
            else RunArchiveState(self.state)
        )
        object.__setattr__(self, "state", state)
        if self.run_status is not None:
            object.__setattr__(
                self,
                "run_status",
                coerce_status(self.run_status, RunStatus, field_name="运行状态"),
            )
        object.__setattr__(self, "stages", _stages(self.stages))
        object.__setattr__(self, "artifacts", _artifacts(self.artifacts))
        if self.problem is not None and (
            not isinstance(self.problem, str) or not self.problem.strip()
        ):
            raise ValueError("阻塞原因必须是非空字符串")
        if state is RunArchiveState.INVALID and self.problem is None:
            raise ValueError(f"不可用的运行 {self.run_id} 必须说明阻塞原因")
        if state is RunArchiveState.INVALID and (self.stages or self.artifacts):
            raise ValueError(f"不可用的运行 {self.run_id} 不能携带阶段或产物记录")

    # -- 浏览与展示 ---------------------------------------------------------

    @property
    def browsable(self) -> bool:
        """是否允许浏览、导出该运行的结果。"""
        return self.state.browsable

    @property
    def total_stages(self) -> int:
        return len(self.stages)

    @property
    def finished_stages(self) -> int:
        return sum(1 for stage in self.stages if stage.finished)

    @property
    def result_count(self) -> int:
        """已登记正式结果的阶段数；``incomplete`` 运行的结果可能缺失。"""
        return sum(1 for stage in self.stages if stage.result_id is not None)

    # -- 产物与重新生成 -----------------------------------------------------

    @property
    def failed_artifacts(self) -> tuple[RunArtifactSummary, ...]:
        return tuple(
            artifact
            for artifact in self.artifacts
            if artifact.status is ArtifactStatus.FAILED
        )

    @property
    def regeneration_stage(self) -> str | None:
        """可重新生成的阶段：第一条失败产物所属的阶段；没有则为 ``None``。"""
        for artifact in self.failed_artifacts:
            if artifact.stage is not None:
                return artifact.stage
        return None

    @property
    def can_regenerate(self) -> bool:
        """只有结构完整且存在可定位失败产物的运行才能重新生成。"""
        return self.state is RunArchiveState.USABLE and self.regeneration_stage is not None


def _stages(value: Any) -> tuple[RunStageSummary, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError("stages 必须是阶段摘要序列")
    records = tuple(value)
    if not all(isinstance(item, RunStageSummary) for item in records):
        raise ValueError("stages 中的每项必须是 RunStageSummary")
    names = [item.name for item in records]
    if len(set(names)) != len(names):
        raise ValueError("同一运行内的阶段摘要不能重复")
    return records


def _artifacts(value: Any) -> tuple[RunArtifactSummary, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError("artifacts 必须是产物摘要序列")
    records = tuple(value)
    if not all(isinstance(item, RunArtifactSummary) for item in records):
        raise ValueError("artifacts 中的每项必须是 RunArtifactSummary")
    # 幂等键与 manifest 一致：同一来源结果的同类产物只有一条。
    keys = [(item.kind, item.source_result) for item in records]
    if len(set(keys)) != len(keys):
        raise ValueError("同一运行内同一来源结果的同类产物只能登记一次")
    return records


__all__ = [
    "RunArchiveState",
    "RunArtifactSummary",
    "RunHistorySummary",
    "RunStageSummary",
]
