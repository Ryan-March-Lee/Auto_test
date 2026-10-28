"""统一的结果归档应用服务（重构计划步骤 3 的服务骨架）。

职责：在测试阶段的完成边界之后，把“已登记的正式结果”收敛成结构化归档结果，
并按 ``(source_result, kind)`` 幂等地维护交付产物状态：

```text
正式结果已登记 -> 初始化产物 pending -> 生成产物 -> 校验登记 -> 写回归档状态
```

边界：

- 只依赖 :class:`application.ports.run_repository.RunRepositoryPort` 与
  :class:`application.ports.result_artifacts.ResultArtifactGeneratorPort`；
  不导入 Qt、基础设施实现或 ``analysis``。
- 正式结果只由阶段记录器写入：本服务**从不写正式结果**，因此“正式 JSON 永不覆盖”
  由调用链结构保证，而不是靠运行期判断；它只读 manifest、只写产物记录。
- 归档语义由 manifest 推导（阶段状态 + 运行状态），调用方不能自称“完整成功”，
  部分完成、取消和异常路径不会被伪装成完整的成功运行。
- 幂等：已完成产物默认复用；``failed`` 产物只有显式 ``retry_failed=True`` 才回到
  ``pending`` 重新生成；已登记 ``not_applicable`` 的产物不会被悄悄改判。
- 产物生成失败只把该产物登记为 ``failed`` 并保留正式结果；manifest 写回失败直接
  抛出，不静默成成功。
- 端口返回值必须与请求的受控目标路径一致：生成到别的路径同样算该产物失败，避免
  manifest 登记一份调用方无法定位、也可能被下一次重试覆盖的“另一份”产物。

本步骤只实现编排骨架：产物可以停在 ``pending``（真实 CSV/报告生成在计划步骤 5
由基础设施适配器实现），不接入三类测量的完成路径（计划步骤 4）。
"""

from __future__ import annotations

import logging
from datetime import datetime

from application.dto.archival import (
    ArchivalOutcome,
    ArchivalRequest,
    ArchivalTestStatus,
    ArchivedArtifact,
)
from application.ports.result_artifacts import (
    ArtifactGenerationError,
    ArtifactGenerationRequest,
    GeneratedArtifact,
    ResultArtifactGeneratorPort,
)
from application.ports.run_repository import RunRepositoryPort
from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    STAGE_AMPLIFIER,
    ArtifactKind,
    ArtifactRecord,
    ArtifactStatus,
    FileReference,
    RunManifest,
    RunStatus,
    StageStatus,
    default_artifact_path,
    default_stage_result_path,
    utc_now_iso,
)


logger = logging.getLogger(__name__)

# 归档只汇总这两类交付产物；顺序固定，便于 manifest 与页面展示稳定。
_DELIVERABLES = (ArtifactKind.CSV, ArtifactKind.REPORT)

# 视为“运行整体可完成”的阶段状态：完成或有意跳过。
_FINISHED_STAGE_STATUSES = frozenset({StageStatus.COMPLETED, StageStatus.SKIPPED})


class ArchivalRequestError(ValueError):
    """归档请求无法执行：运行/阶段状态与正式结果登记不符。"""


class ResultArchivalService:
    """按运行与阶段归档正式结果，并维护交付产物的结构化状态。"""

    def __init__(
        self,
        *,
        runs: RunRepositoryPort,
        generator: ResultArtifactGeneratorPort | None = None,
    ) -> None:
        for name in ("load_manifest", "update_manifest"):
            if not callable(getattr(runs, name, None)):
                raise TypeError(f"归档服务需要运行仓储提供 {name}()")
        if generator is not None and not callable(getattr(generator, "generate", None)):
            raise TypeError("产物生成端口必须提供 generate()")
        self.runs = runs
        self.generator = generator

    # -- 入口 -------------------------------------------------------------

    def archive(
        self,
        request: ArchivalRequest,
        *,
        retry_failed: bool = False,
        now: datetime | None = None,
    ) -> ArchivalOutcome:
        """归档一次阶段完成边界，返回结构化归档结果。

        ``retry_failed=True`` 是唯一的“重新生成失败产物”入口；其余情况下失败产物
        保持 ``failed``，已完成与不适用的产物保持原状，重复调用不会产生第二份正式
        结果，也不会改写已经登记的产物状态。
        """
        if not isinstance(request, ArchivalRequest):
            raise ArchivalRequestError("归档入口只接受 ArchivalRequest")
        if not isinstance(retry_failed, bool):
            raise ArchivalRequestError("retry_failed 必须是布尔值")
        manifest = self._load(request.run_id)
        test_status = _derive_test_status(manifest, request.stage)
        formal_result = self._formal_result(manifest, request, test_status)
        applicable = (
            request.stage == STAGE_AMPLIFIER
            and test_status is ArchivalTestStatus.COMPLETED
        )
        # 线损、驱动映射永远没有交付产物，把“不适用”持久化进 manifest；主功放尚未
        # 完整成功时只在本次归档结果里表达不适用，不写 manifest，避免以后的完整成功
        # 被自己登记的 not_applicable 挡住。
        persists_not_applicable = request.stage != STAGE_AMPLIFIER
        manifest = self._prepare_artifacts(
            manifest,
            source_result=formal_result,
            applicable=applicable,
            persists_not_applicable=persists_not_applicable,
            retry_failed=retry_failed,
            now=now,
        )
        if applicable and self.generator is not None:
            manifest = self._generate_pending(
                manifest,
                stage=request.stage,
                source_result=formal_result,
                now=now,
            )
        return ArchivalOutcome(
            run_id=request.run_id,
            stage=request.stage,
            test_status=test_status,
            formal_result_path=formal_result,
            artifacts=tuple(
                _archived_artifact(
                    manifest,
                    kind,
                    formal_result,
                    required=applicable or persists_not_applicable,
                )
                for kind in _DELIVERABLES
            ),
            archived_at=utc_now_iso(now),
        )

    # -- 事实校验 ---------------------------------------------------------

    def _load(self, run_id: str) -> RunManifest:
        """读取 manifest；运行不存在或不可读时按归档请求失败上报。"""
        try:
            return self.runs.load_manifest(run_id)
        except FileNotFoundError as error:
            raise ArchivalRequestError(f"运行不存在或不可读: {run_id}") from error
        except ValueError as error:
            raise ArchivalRequestError(
                f"运行 {run_id} 的 manifest 不可用: {error}"
            ) from error

    def _formal_result(
        self,
        manifest: RunManifest,
        request: ArchivalRequest,
        test_status: ArchivalTestStatus,
    ) -> str | None:
        """确认正式结果已写入并登记，返回其在运行目录内的相对路径。

        只接受该阶段的固定结果路径：请求参数不能把归档指向其它文件，manifest 也
        必须已经登记该路径及校验值，否则它不是“正式结果”。
        """
        expected = _stage_result_path(request.stage)
        if request.source_result is not None and request.source_result != expected:
            raise ArchivalRequestError(
                f"阶段 {request.stage} 的正式结果只能是 {expected}，"
                f"收到: {request.source_result!r}"
            )
        record = _stage_record(manifest, request.stage)
        if test_status is ArchivalTestStatus.CANCELLED:
            if record.result is not None:
                raise ArchivalRequestError(
                    f"运行 {manifest.run_id} 的阶段 {request.stage} 已取消但仍登记结果 "
                    f"{record.result!r}，归档状态与运行状态不一致"
                )
            return None
        if record.status is not StageStatus.COMPLETED:
            # 阶段失败且没有完成过：没有可归档的正式结果。
            return None
        if record.result != expected:
            raise ArchivalRequestError(
                f"运行 {manifest.run_id} 的阶段 {request.stage} 登记的正式结果不是 "
                f"{expected}，实际: {record.result!r}"
            )
        reference = _registered_reference(manifest, expected, stage=request.stage)
        if reference is None:
            raise ArchivalRequestError(
                f"正式结果 {expected} 未登记到运行 {manifest.run_id} 的 manifest，不能归档"
            )
        if reference.sha256 is None or reference.size_bytes is None:
            raise ArchivalRequestError(
                f"正式结果 {expected} 的 manifest 登记缺少校验值，不能确认已完整写入"
            )
        return expected

    # -- 产物状态编排 -----------------------------------------------------

    def _prepare_artifacts(
        self,
        manifest: RunManifest,
        *,
        source_result: str | None,
        applicable: bool,
        persists_not_applicable: bool,
        retry_failed: bool,
        now: datetime | None,
    ) -> RunManifest:
        """把两类交付产物初始化到 pending / not_applicable，并按需登记显式重试。"""
        if not applicable and not persists_not_applicable:
            # 主功放未达完整成功：不写产物记录，只在本次归档结果里给出不适用；但
            # manifest 若已登记交付产物，说明运行状态与产物状态不一致，必须拒绝。
            inconsistent = [
                kind.value
                for kind in _DELIVERABLES
                for record in (
                    manifest.find_artifact(kind, source_result=source_result),
                )
                if record is not None
                and record.status is not ArtifactStatus.NOT_APPLICABLE
            ]
            if inconsistent:
                raise ArchivalRequestError(
                    "主功放尚未达到完整成功，但 manifest 已登记交付产物: "
                    + ", ".join(inconsistent)
                )
            return manifest
        for kind in _DELIVERABLES:
            existing = manifest.find_artifact(kind, source_result=source_result)
            if not applicable:
                manifest = self._record_not_applicable(
                    manifest, kind, source_result, existing, now=now
                )
                continue
            if existing is not None and existing.status is ArtifactStatus.NOT_APPLICABLE:
                raise ArchivalRequestError(
                    f"产物 {kind.value} 已登记为不适用，不能在同一来源结果上改判为待生成: "
                    f"{source_result}"
                )
            if (
                existing is not None
                and existing.status is ArtifactStatus.FAILED
                and retry_failed
            ):
                manifest = self._commit(
                    manifest.retry_artifact(
                        kind, source_result=source_result, now=now
                    ),
                    now=now,
                )
                continue
            if existing is None:
                manifest = self._commit(
                    manifest.with_artifact(
                        ArtifactRecord(
                            kind=kind,
                            status=ArtifactStatus.PENDING,
                            source_result=source_result,
                            created_at=utc_now_iso(now),
                        ),
                        now=now,
                    ),
                    now=now,
                )
        return manifest

    def _record_not_applicable(
        self,
        manifest: RunManifest,
        kind: ArtifactKind,
        source_result: str | None,
        existing: ArtifactRecord | None,
        *,
        now: datetime | None,
    ) -> RunManifest:
        """登记“该阶段没有交付产物”，并拒绝把已生成/已失败的产物改判为不适用。"""
        if existing is None:
            return self._commit(
                manifest.with_artifact(
                    ArtifactRecord(
                        kind=kind,
                        status=ArtifactStatus.NOT_APPLICABLE,
                        source_result=source_result,
                        created_at=utc_now_iso(now),
                    ),
                    now=now,
                ),
                now=now,
            )
        if existing.status is ArtifactStatus.NOT_APPLICABLE:
            return manifest
        if existing.status is ArtifactStatus.PENDING:
            return self._commit(
                manifest.with_artifact(
                    existing.transition_to(ArtifactStatus.NOT_APPLICABLE, now=now),
                    now=now,
                ),
                now=now,
            )
        raise ArchivalRequestError(
            f"产物 {kind.value} 已登记为 {existing.status.value}，"
            f"不能在同一来源结果上改判为不适用: {source_result or '无正式结果'}"
        )

    def _generate_pending(
        self,
        manifest: RunManifest,
        *,
        stage: str,
        source_result: str,
        now: datetime | None,
    ) -> RunManifest:
        """对仍为 pending 的产物调用生成端口，并把结果登记为 completed / failed。"""
        for kind in _DELIVERABLES:
            record = manifest.find_artifact(kind, source_result=source_result)
            if record is None or record.status is not ArtifactStatus.PENDING:
                continue
            updated = self._generate_one(
                record,
                stage=stage,
                source_result=source_result,
                run_id=manifest.run_id,
                now=now,
            )
            manifest = self._commit(
                manifest.with_artifact(updated, now=now), now=now
            )
        return manifest

    def _generate_one(
        self,
        record: ArtifactRecord,
        *,
        stage: str,
        source_result: str,
        run_id: str,
        now: datetime | None,
    ) -> ArtifactRecord:
        """生成单个产物：失败只影响该产物，不回滚正式结果。

        只把“约定的生成失败”登记为 ``failed``：:class:`ArtifactGenerationError`、
        ``OSError`` 与 ``ValueError``。其它异常（例如端口实现里的编程错误）不在这里
        兜底，会向上抛出并让该产物停在 ``pending``，由调用方或恢复流程重新检查；
        端口实现有责任把可预期的渲染/写入/空数据失败显式包成
        :class:`ArtifactGenerationError`。
        """
        target_path = default_artifact_path(stage, record.kind)
        request = ArtifactGenerationRequest(
            run_id=run_id,
            stage=stage,
            kind=record.kind,
            source_result=source_result,
            target_path=target_path,
        )
        try:
            generated = self.generator.generate(request)
            if not isinstance(generated, GeneratedArtifact):
                raise ArtifactGenerationError(
                    "产物生成端口必须返回 GeneratedArtifact，实际: "
                    f"{type(generated).__name__}"
                )
            if generated.path != target_path:
                # 受控路径是归档契约的一部分：写到别处就既不能保证重试覆盖同一份
                # 文件，也无法让 manifest 里的路径与生成请求对应。
                raise ArtifactGenerationError(
                    "产物生成端口返回的路径与请求的受控目标不一致: "
                    f"期望 {target_path}，实际 {generated.path}"
                )
            return record.transition_to(
                ArtifactStatus.COMPLETED,
                path=generated.path,
                sha256=generated.sha256,
                size_bytes=generated.size_bytes,
                now=now,
            )
        except (ArtifactGenerationError, OSError, ValueError) as error:
            reason = _failure_text(error)
            logger.warning(
                "自动归档产物生成失败: run=%s kind=%s stage=%s error=%s",
                run_id,
                record.kind.value,
                stage,
                reason,
            )
            return record.transition_to(
                ArtifactStatus.FAILED, error=reason, now=now
            )

    def _commit(self, manifest: RunManifest, *, now: datetime | None) -> RunManifest:
        """原子写回 manifest；写失败直接抛出，不把归档错误吞成成功。"""
        return self.runs.update_manifest(manifest.run_id, manifest, now=now)


# -- 纯函数工具 -----------------------------------------------------------


def _stage_record(manifest: RunManifest, stage: str):
    try:
        return manifest.stage(stage)
    except KeyError as error:
        raise ArchivalRequestError(
            f"运行 {manifest.run_id} 的 manifest 缺少阶段 {stage}"
        ) from error


def _stage_result_path(stage: str) -> str:
    """返回阶段的固定正式结果路径；主功放使用显式主结果路径。"""
    if stage == STAGE_AMPLIFIER:
        return AMPLIFIER_RESULT_PATH
    expected = default_stage_result_path(stage)
    if expected is None:
        raise ArchivalRequestError(f"阶段 {stage} 没有固定的正式结果路径")
    return expected


def _derive_test_status(
    manifest: RunManifest, stage: str
) -> ArchivalTestStatus:
    """按 manifest 的阶段/运行状态推导归档测试语义。

    推导而不是让调用方传入，是为了让“部分运行”无法自称完整成功：只有阶段完成且
    运行整体完成（所有阶段 completed/skipped）才会得到 ``completed``。
    """
    record = _stage_record(manifest, stage)
    if manifest.status is RunStatus.CANCELLED or record.status is StageStatus.CANCELLED:
        return ArchivalTestStatus.CANCELLED
    if manifest.status is RunStatus.FAILED or record.status is StageStatus.FAILED:
        return ArchivalTestStatus.FAILED
    if record.status is StageStatus.SKIPPED:
        raise ArchivalRequestError(
            f"运行 {manifest.run_id} 的阶段 {stage} 被跳过，没有可归档的正式结果"
        )
    if record.status is not StageStatus.COMPLETED:
        raise ArchivalRequestError(
            f"运行 {manifest.run_id} 的阶段 {stage} 尚未完成"
            f"（当前 {record.status.value}），不能归档"
        )
    if manifest.status is not RunStatus.COMPLETED:
        return ArchivalTestStatus.PARTIAL
    unfinished = [
        name
        for name, item in manifest.stages.items()
        if item.status not in _FINISHED_STAGE_STATUSES
    ]
    if unfinished:
        raise ArchivalRequestError(
            f"运行 {manifest.run_id} 标记为 completed 但仍有未完成阶段: "
            + ", ".join(
                f"{name}={manifest.stage(name).status.value}" for name in unfinished
            )
        )
    return ArchivalTestStatus.COMPLETED


def _registered_reference(
    manifest: RunManifest, path: str, *, stage: str
) -> FileReference | None:
    """在 manifest 的 inputs/outputs 中查找正式结果登记。"""
    for reference in (*manifest.inputs, *manifest.outputs):
        if reference.path != path:
            continue
        if reference.stage is not None and reference.stage != stage:
            raise ArchivalRequestError(
                f"正式结果 {path} 登记的阶段是 {reference.stage}，"
                f"与归档阶段 {stage} 不一致"
            )
        return reference
    return None


def _archived_artifact(
    manifest: RunManifest,
    kind: ArtifactKind,
    source_result: str | None,
    *,
    required: bool,
) -> ArchivedArtifact:
    """把 manifest 产物记录映射成归档 DTO；``required`` 时记录缺失视为契约破坏。"""
    record = manifest.find_artifact(kind, source_result=source_result)
    if record is None:
        if required:
            raise ArchivalRequestError(
                f"manifest 缺少产物记录 {kind.value} <- "
                f"{source_result or '无正式结果'}"
            )
        return ArchivedArtifact.not_applicable(kind)
    if record.status is ArtifactStatus.COMPLETED:
        if record.path is None:
            raise ArchivalRequestError(f"completed 产物 {kind.value} 没有登记路径")
        return ArchivedArtifact.completed(kind, record.path)
    if record.status is ArtifactStatus.FAILED:
        return ArchivedArtifact.failed(kind, record.error or "产物生成失败")
    if record.status is ArtifactStatus.PENDING:
        return ArchivedArtifact.pending(kind)
    return ArchivedArtifact.not_applicable(kind)


def _failure_text(error: BaseException) -> str:
    """把产物生成失败转成可登记的非空文本。"""
    message = str(error).strip()
    return message or type(error).__name__


__all__ = ["ArchivalRequestError", "ResultArchivalService"]
