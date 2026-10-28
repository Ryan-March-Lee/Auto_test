"""测试结果自动归档的正式契约：归档结果 DTO、产物状态与组合规则。

本模块把“测试成功”与“交付产物生成成功”表达为两个可区分的维度：

- ``test_status`` 描述本次归档对应的测试边界走到了哪一步：完整成功、部分完成、
  取消或异常。部分完成只表示**本阶段**已完成而运行整体尚未完成，不代表缺陷。
- 每个交付产物（CSV、测试报告）用 :class:`ArtifactStatus` 表达自己的状态；产物失败
  不回滚已经保存的正式结果，也不把测试状态改成失败。
- :attr:`ArchivalOutcome.archive_status` 是归档维度的小结：正式结果未保存时是
  ``failed``（异常）或 ``not_applicable``（取消），已保存但有产物失败时是
  ``partial``，全部适用产物完成或不适用时是 ``completed``。

路径规则：

- 正式结果只允许固定阶段路径（``inputs/cable_loss.json``、
  ``inputs/driver_mapping.json``、``results/amplifier_measurement.json``）。
- 交付产物只允许运行目录 ``artifacts/`` 下的 POSIX 相对路径，由
  :func:`domain.run.models.default_artifact_path` 集中给出默认位置。
- DTO 不读写文件、不判断文件是否存在，也不接受绝对路径；文件存在性、SHA-256 校验
  和 manifest 登记由应用服务与基础设施适配器完成。
- DTO 不携带页面文案，不依赖 Qt；用户可见措辞由界面层根据状态生成。

状态组合规则（非法组合直接 ``ValueError``）：

- ``completed``：正式结果必填且必须是阶段固定路径；产物允许 ``pending`` /
  ``completed`` / ``failed``，但**不允许** ``not_applicable``——完整成功必须生成
  全部交付产物，否则交付契约就退化成“报告缺失也算成功”。
- ``partial``：正式结果必填；产物必须全部 ``not_applicable``（不生成交付产物）。
- ``failed``：正式结果可选（运行级失败前保存的结果仍可归档）；产物必须全部
  ``not_applicable``。
- ``cancelled``：禁止携带正式结果路径；产物必须全部 ``not_applicable``（取消会清理
  阶段结果）。

只有 ``amplifier`` 阶段且 ``test_status=completed`` 时才允许 CSV/报告不是
``not_applicable``；线损、驱动映射、取消、异常与部分完成一律不生成完整报告。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    DEFAULT_STAGE_NAMES,
    STAGE_AMPLIFIER,
    ArtifactKind,
    ArtifactStatus,
    coerce_status,
    default_stage_result_path,
    normalize_artifact_path,
    normalize_relative_path,
    validate_run_id,
)


# 页面只汇总这两类交付产物；图表是报告的组成部分，不单独出现在归档摘要里。
DELIVERABLE_ARTIFACT_KINDS = frozenset({ArtifactKind.CSV, ArtifactKind.REPORT})


class ArchivalTestStatus(str, Enum):
    """本次归档对应的测试边界状态。

    这是应用层从 manifest 阶段/运行状态收敛出的归档语义，不是 ``RunStatus`` 或
    ``MeasurementStatus`` 的别名：``partial`` 在运行状态里仍是 ``running``。
    """

    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def requires_formal_result(self) -> bool:
        """是否必须已经保存正式结果才能归档。"""
        return self in {ArchivalTestStatus.COMPLETED, ArchivalTestStatus.PARTIAL}


class ArchivalStatus(str, Enum):
    """归档维度的小结状态，独立于测试是否成功。"""

    PENDING = "pending"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class ArchivedArtifact:
    """归档结果中的一个交付产物，只承载页面需要的结构化事实。"""

    kind: ArtifactKind
    status: ArtifactStatus
    path: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        kind = coerce_status(self.kind, ArtifactKind, field_name="产物类型")
        status = coerce_status(self.status, ArtifactStatus, field_name="产物状态")
        if kind not in DELIVERABLE_ARTIFACT_KINDS:
            raise ValueError(f"归档结果只汇总 CSV 和报告产物，收到: {kind.value}")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "status", status)
        if self.path is not None:
            object.__setattr__(self, "path", normalize_artifact_path(self.path))
        if self.reason is not None and not isinstance(self.reason, str):
            raise ValueError("产物原因必须是字符串")
        if status is ArtifactStatus.COMPLETED:
            if self.path is None:
                raise ValueError("completed 产物必须记录运行目录内的相对路径")
            if self.reason is not None:
                raise ValueError("completed 产物不能携带失败原因")
            return
        if status is ArtifactStatus.FAILED:
            if not isinstance(self.reason, str) or not self.reason.strip():
                raise ValueError("failed 产物必须记录失败原因")
            if self.path is not None:
                raise ValueError("failed 产物不能记录产物路径")
            return
        if self.path is not None:
            raise ValueError(f"{status.value} 产物不能记录路径")
        if self.reason is not None:
            raise ValueError(f"{status.value} 产物不能记录原因")

    @classmethod
    def pending(cls, kind: ArtifactKind | str) -> "ArchivedArtifact":
        return cls(kind=kind, status=ArtifactStatus.PENDING)

    @classmethod
    def completed(cls, kind: ArtifactKind | str, path: str) -> "ArchivedArtifact":
        return cls(kind=kind, status=ArtifactStatus.COMPLETED, path=path)

    @classmethod
    def failed(cls, kind: ArtifactKind | str, reason: str) -> "ArchivedArtifact":
        return cls(kind=kind, status=ArtifactStatus.FAILED, reason=reason)

    @classmethod
    def not_applicable(cls, kind: ArtifactKind | str) -> "ArchivedArtifact":
        return cls(kind=kind, status=ArtifactStatus.NOT_APPLICABLE)


@dataclass(frozen=True)
class ArchivalOutcome:
    """一次阶段完成后的结构化归档结果。

    ``formal_result_path`` 与 ``artifacts`` 都是运行目录内的相对路径，页面只负责把
    状态转成文案和按钮，不读取文件、不拼接导出规则。
    """

    run_id: str
    stage: str
    test_status: ArchivalTestStatus
    formal_result_path: str | None = None
    artifacts: tuple[ArchivedArtifact, ...] = ()
    archived_at: str | None = None

    def __post_init__(self) -> None:
        validate_run_id(self.run_id)
        if self.stage not in DEFAULT_STAGE_NAMES:
            raise ValueError(
                "归档结果的阶段必须是标准阶段: " + ", ".join(DEFAULT_STAGE_NAMES)
            )
        test_status = coerce_status(
            self.test_status, ArchivalTestStatus, field_name="归档测试状态"
        )
        object.__setattr__(self, "test_status", test_status)
        self._validate_formal_result(test_status)
        self._validate_artifacts(test_status)
        if self.archived_at is not None:
            if not isinstance(self.archived_at, str) or not self.archived_at.strip():
                raise ValueError("archived_at 必须是非空字符串")

    def _validate_formal_result(self, test_status: ArchivalTestStatus) -> None:
        if self.formal_result_path is None:
            if test_status.requires_formal_result:
                raise ValueError(
                    f"{test_status.value} 归档必须记录已保存的正式结果路径"
                )
            return
        normalized = normalize_relative_path(
            self.formal_result_path, field_name="正式结果路径"
        )
        expected = _expected_result_path(self.stage)
        if normalized != expected:
            raise ValueError(
                f"阶段 {self.stage} 的正式结果必须位于 {expected}，实际: {normalized!r}"
            )
        if test_status is ArchivalTestStatus.CANCELLED:
            raise ValueError("取消的归档不能保留正式结果路径")
        object.__setattr__(self, "formal_result_path", normalized)

    def _validate_artifacts(self, test_status: ArchivalTestStatus) -> None:
        if not isinstance(self.artifacts, (list, tuple)):
            raise ValueError("artifacts 必须是产物记录序列")
        records = tuple(self.artifacts)
        if not all(isinstance(item, ArchivedArtifact) for item in records):
            raise ValueError("artifacts 中的每项必须是 ArchivedArtifact")
        kinds = [item.kind for item in records]
        if len(set(kinds)) != len(kinds):
            raise ValueError("同一产物类型不能重复登记")
        missing = DELIVERABLE_ARTIFACT_KINDS - set(kinds)
        if missing:
            raise ValueError(
                "归档结果必须显式登记每种交付产物（含 not_applicable）: "
                + ", ".join(sorted(item.value for item in missing))
            )
        applicable = (
            self.stage == STAGE_AMPLIFIER
            and test_status is ArchivalTestStatus.COMPLETED
        )
        if applicable:
            # 完整成功必须交付完整产物；省略报告或 CSV 等于放宽“完整成功”的定义。
            offenders = [
                item.kind.value
                for item in records
                if item.status is ArtifactStatus.NOT_APPLICABLE
            ]
            if offenders:
                raise ValueError(
                    "主功放完整成功必须生成全部交付产物，不能标记为 not_applicable: "
                    + ", ".join(sorted(offenders))
                )
        else:
            offenders = [
                item.kind.value
                for item in records
                if item.status is not ArtifactStatus.NOT_APPLICABLE
            ]
            if offenders:
                raise ValueError(
                    "只有完整成功的主功放运行才能生成交付产物；"
                    f"当前 {self.stage}/{test_status.value} 不允许: "
                    + ", ".join(sorted(offenders))
                )
        object.__setattr__(
            self, "artifacts", tuple(sorted(records, key=lambda item: item.kind.value))
        )

    def artifact(self, kind: ArtifactKind | str) -> ArchivedArtifact:
        """按产物类型取记录；构造时已保证 CSV 与报告都存在。"""
        artifact_kind = coerce_status(kind, ArtifactKind, field_name="产物类型")
        for item in self.artifacts:
            if item.kind is artifact_kind:
                return item
        raise KeyError(f"归档结果缺少产物记录: {artifact_kind.value}")

    @property
    def csv(self) -> ArchivedArtifact:
        return self.artifact(ArtifactKind.CSV)

    @property
    def report(self) -> ArchivedArtifact:
        return self.artifact(ArtifactKind.REPORT)

    @property
    def archive_status(self) -> ArchivalStatus:
        """按正式结果与产物状态收敛出归档小结。

        ``completed`` 只在所有适用产物都已生成（没有适用产物的阶段则全部
        ``not_applicable``）时成立；任一产物 ``failed`` 即为 ``partial``，
        任一产物仍在生成即为 ``pending``。产物失败不会把测试状态改成失败。
        """
        if self.formal_result_path is None:
            if self.test_status is ArchivalTestStatus.FAILED:
                return ArchivalStatus.FAILED
            return ArchivalStatus.NOT_APPLICABLE
        if any(item.status is ArtifactStatus.FAILED for item in self.artifacts):
            return ArchivalStatus.PARTIAL
        if any(item.status is ArtifactStatus.PENDING for item in self.artifacts):
            return ArchivalStatus.PENDING
        return ArchivalStatus.COMPLETED


def _expected_result_path(stage: str) -> str:
    if stage == STAGE_AMPLIFIER:
        return AMPLIFIER_RESULT_PATH
    expected = default_stage_result_path(stage)
    if expected is None:
        raise ValueError(f"阶段 {stage} 没有固定的正式结果路径")
    return expected


__all__ = [
    "ArchivalOutcome",
    "ArchivalStatus",
    "ArchivalTestStatus",
    "ArchivedArtifact",
]
