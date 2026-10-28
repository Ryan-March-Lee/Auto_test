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
  ``pending`` 重新生成；已登记 ``not_applicable`` 的产物不会被悄悄改判。重试同样受
  端口能力约束：当前端口没有声明的产物类型保持 ``failed`` 与原有失败原因，不会被
  复位成 ``pending``（“没有实现”不等于“正在生成”）。
- 复用已完成产物前用 ``RunRepositoryPort.verify_artifact()`` 复核文件仍存在且与
  manifest 登记值一致： ``completed`` 是终态，不能因为文件丢失就原地改判，只能拒绝
  本次归档并交由显式重新生成入口处理。
- 同一运行的归档在本进程内串行执行：manifest 是“读-改-写”，并发归档会互相覆盖
  产物记录。
- 产物生成失败只把该产物登记为 ``failed`` 并保留正式结果；manifest 写回失败直接
  抛出，不静默成成功。
- 端口返回值必须与请求的受控目标路径一致：生成到别的路径同样算该产物失败，避免
  manifest 登记一份调用方无法定位、也可能被下一次重试覆盖的“另一份”产物。
- 端口可以用可选属性 ``supported_kinds`` 声明自己**当前能生成**的产物类型；未声明的
  产物保持 ``pending``（等待实现方补齐能力），既不会被伪造成 ``failed``，也不会被当作
  已完成。当前基础设施组合端口声明 CSV 与报告，主功放完整成功时两者都会生成。
- 日志挂在 ``pa_auto_test`` logger 树下：产物生成失败（``WARNING``）、重试跳过
  （``INFO``）与归档边界错误（``ERROR``）都进入 ``setup_logging()`` 的应用日志文件，
  历史页显示的产物状态与日志可相互对照。端口实现缺陷（未预期异常）属于实现问题，不被
  降级成产物失败，直接上抛给调用方。

本模块同时提供阶段完成边界的调用入口 :func:`archive_completed_stage`：它只把归档契约里
可预期的失败（请求不合法、manifest 写回失败）降级为日志，绝不改变已经保存的正式结果，
也不把测量失败与产物失败混为一谈。
"""

from __future__ import annotations

import logging
import threading
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


# 挂到项目专用 ``pa_auto_test`` logger 树下（与 ``infrastructure.logging.get_logger``
# 的命名规则一致）：``setup_logging()`` 只给该树装配文件处理器，直接用
# ``logging.getLogger(__name__)`` 会让产物失败、重试跳过和归档边界错误只落到 stderr，
# ``logs/pa_auto_test_*.log`` 里查不到——而“自动生成错误写入应用日志”是归档契约的一
# 部分。这里不导入 infrastructure，保持应用层依赖边界。
logger = logging.getLogger("pa_auto_test." + __name__)

# 归档按运行串行化：manifest 是“读 manifest -> 改产物记录 -> 写回”的多步过程，两个
# 线程同时归档同一运行会互相覆盖产物记录。锁只保证**本进程内**不并发，跨进程并发不在
# 本服务范围内（一个运行目录只由一个实例负责，同一 run_id 的重复触发由本锁合并）。
_LOCKS_GUARD = threading.Lock()
_RUN_LOCKS: dict[str, threading.RLock] = {}

# 归档只汇总这两类交付产物；顺序固定，便于 manifest 与页面展示稳定。
_DELIVERABLES = (ArtifactKind.CSV, ArtifactKind.REPORT)
_DELIVERABLE_KINDS = frozenset(_DELIVERABLES)

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
        # verify_artifact 是必需能力：复用已完成产物前必须确认文件仍与登记一致。
        for name in ("load_manifest", "update_manifest", "verify_artifact"):
            if not callable(getattr(runs, name, None)):
                raise TypeError(f"归档服务需要运行仓储提供 {name}()")
        if generator is not None and not callable(getattr(generator, "generate", None)):
            raise TypeError("产物生成端口必须提供 generate()")
        self.runs = runs
        self.generator = generator
        # 端口声明的生成能力；未声明的交付产物停在 pending，等实现方补齐。
        self.supported_kinds = _declared_kinds(generator)

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

        同一运行的归档过程在本进程内串行执行：manifest 的“读-改-写”不是原子的，
        并发调用会互相覆盖产物记录。
        """
        if not isinstance(request, ArchivalRequest):
            raise ArchivalRequestError("归档入口只接受 ArchivalRequest")
        if not isinstance(retry_failed, bool):
            raise ArchivalRequestError("retry_failed 必须是布尔值")
        with _run_lock(request.run_id):
            return self._archive_locked(request, retry_failed=retry_failed, now=now)

    def _archive_locked(
        self,
        request: ArchivalRequest,
        *,
        retry_failed: bool,
        now: datetime | None,
    ) -> ArchivalOutcome:
        """在已持有该运行归档锁的前提下执行一次归档。"""
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
            if existing is not None and existing.status is ArtifactStatus.COMPLETED:
                # 已完成是终态：复用前必须确认文件仍在且与登记一致，否则“已生成”只是
                # manifest 里的一句声明。发现不一致时按归档请求失败上报，不改写记录。
                self._verify_completed(existing, run_id=manifest.run_id)
                continue
            if (
                existing is not None
                and existing.status is ArtifactStatus.FAILED
                and retry_failed
            ):
                if kind not in self.supported_kinds:
                    # 端口当前不生成该产物：重试只能保持 ``failed``。把记录复位成
                    # ``pending`` 会让“没有实现”伪装成“正在生成”，并丢掉失败原因与
                    # 历史状态里的重试线索（``pending`` 不算可重试的失败产物）。
                    logger.info(
                        "跳过产物重试：当前生成端口不支持该类型 run=%s kind=%s",
                        manifest.run_id,
                        kind.value,
                    )
                    continue
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

    def _verify_completed(self, record: ArtifactRecord, *, run_id: str) -> None:
        """复用已完成产物前确认文件仍存在且与 manifest 登记一致。

        已完成是终态：文件被删除或被替换时不能把它原地改判成 ``failed``，也不能假装
        复用成功。这里按归档请求失败上报，manifest 与正式结果都保持原样，重新生成要
        走显式入口（计划步骤 7/9 的历史重试）。
        """
        try:
            self.runs.verify_artifact(run_id, record)
        except (OSError, ValueError) as error:
            raise ArchivalRequestError(
                f"已完成产物 {record.kind.value} 与 manifest 登记不一致，拒绝复用: "
                f"{record.path}（{error}）"
            ) from error

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
            if record.kind not in self.supported_kinds:
                # 端口尚未提供该产物的生成能力：保持 pending，不报假失败、也不谎称完成。
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
        :class:`ArtifactGenerationError`。缺陷路径不在这里记日志：项目的宽泛捕获基线
        禁止在运行时包里新增 ``except Exception``（见
        ``docs/异常分类与静态质量门禁.md``），把缺陷降级成日志等于把它伪装成一次可
        恢复的生成失败。
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


# -- 阶段完成边界的调用入口 -------------------------------------------------


def archive_completed_stage(
    service: ResultArchivalService | None,
    *,
    run_id: str,
    stage: str,
) -> ArchivalOutcome | None:
    """在阶段成功提交后自动归档，返回结构化结果供上层展示。

    这是应用层测量用例唯一的自动归档入口。归档是**测量之后**的动作：

    - ``service`` 为 ``None``（旧兼容流程、无运行契约）时什么都不做，返回 ``None``；
    - 产物级失败已经在服务内部登记进 manifest 并按结构化状态返回，不算归档失败；
    - 只有归档契约里可预期的失败（:class:`ArchivalRequestError`、manifest 写回失败）
      被降级为错误日志，正式结果、阶段状态和测量返回值都不受影响；
    - 其它异常仍然上抛：端口或服务实现缺陷不能被静默成“归档成功”。
    """
    if service is None:
        return None
    try:
        return service.archive(ArchivalRequest(run_id=run_id, stage=stage))
    except (OSError, ValueError) as error:
        # ArchivalRequestError 是 ValueError 子类，manifest 写回失败是 OSError。
        logger.error(
            "阶段完成后的自动归档失败: run=%s stage=%s error=%s",
            run_id,
            stage,
            str(error) or type(error).__name__,
        )
        return None


# -- 纯函数工具 -----------------------------------------------------------


def _run_lock(run_id: str) -> threading.RLock:
    """取得某个运行的进程内可重入归档锁。"""
    with _LOCKS_GUARD:
        lock = _RUN_LOCKS.get(run_id)
        if lock is None:
            lock = threading.RLock()
            _RUN_LOCKS[run_id] = lock
        return lock


def _declared_kinds(
    generator: ResultArtifactGeneratorPort | None,
) -> frozenset[ArtifactKind]:
    """读取端口可选声明的 ``supported_kinds``；未声明时视为支持全部交付产物。

    未声明保持“全部支持”是为了让只实现 ``generate()`` 的端口（含测试替身）行为不变；
    声明了未登记的产物类型直接拒绝，避免归档摘要与 manifest 记录出现未知类型。
    """
    if generator is None:
        return frozenset()
    declared = getattr(generator, "supported_kinds", None)
    if declared is None:
        return _DELIVERABLE_KINDS
    if isinstance(declared, (str, bytes)) or not isinstance(
        declared, (list, tuple, set, frozenset)
    ):
        raise TypeError("产物生成端口的 supported_kinds 必须是产物类型集合")
    kinds = set()
    for item in declared:
        try:
            kind = ArtifactKind(item)
        except ValueError as error:
            raise TypeError(f"产物生成端口声明了未知产物类型: {item!r}") from error
        if kind not in _DELIVERABLE_KINDS:
            raise TypeError(f"归档只汇总 CSV 与报告产物，端口声明了: {kind.value}")
        kinds.add(kind)
    return frozenset(kinds)


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


__all__ = [
    "ArchivalRequestError",
    "ResultArchivalService",
    "archive_completed_stage",
]
