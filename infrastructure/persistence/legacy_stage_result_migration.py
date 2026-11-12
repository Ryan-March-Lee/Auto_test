"""旧运行结果复制迁移：把 ``runs/<run_id>/inputs/*.json`` 中的正式结果复制到阶段目录。

重构步骤 7、8 之前，线损与驱动映射的正式结果保存在运行目录的固定输入路径上；此后
正式结果以 ``test_results/<stage>/<measurement_id>.json`` 为权威来源，运行目录只登记
引用。本模块提供一个**独立、只读源目录**的迁移工具：扫描旧运行目录中的完整结果文档，
补齐测量身份后经阶段结果仓库原子写入阶段目录，并把每一份来源的处理结果汇总为报告。

边界：

- 只复制，不移动、不改写旧运行目录（包括 manifest）；
- 目标文件已存在时绝不覆盖：内容一致视为已迁移，内容不同记为冲突；
- 重复执行幂等：迁移身份由“来源阶段 + 来源运行 + 源内容摘要”确定，同一份源文件
  每次都会指向同一个阶段结果文件；
- 不接入启动流程，也不改变测量链路；由 :mod:`tools.migrate_legacy_results` 显式调用。

源文件只有在能完整解析为当前正式结果 DTO（类型与阶段相符、状态为 ``completed``、
带保存时间）时才会被复制；旧 ``power_mapping``、损坏 JSON、未完成或被取消的结果、
阶段引用指针文档都不会伪装成正式结果，分别记为失败、跳过或已迁移。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from application.dto.measurement_results import (
    MeasurementResult,
    MeasurementStatus,
    measurement_result_from_document,
    require_measurement_identity,
    validate_measurement_id,
)
from application.ports.run_repository import RunRepositoryPort
from application.ports.stage_result_repository import (
    MeasurementStage,
    StageResultError,
    StageResultRepository,
)
from application.run_stages import STAGE_RESULT_REFERENCE_SCHEMA
from domain.run.models import CABLE_LOSS_INPUT_PATH, DRIVER_MAPPING_INPUT_PATH

__all__ = [
    "LEGACY_MIGRATION_REPORT_SCHEMA",
    "LEGACY_MIGRATION_REPORT_VERSION",
    "LEGACY_STAGE_INPUTS",
    "LegacyMigrationEntry",
    "LegacyMigrationOutcome",
    "LegacyMigrationReport",
    "LegacyStageResultMigration",
    "legacy_migration_measurement_id",
]


logger = logging.getLogger(__name__)

LEGACY_MIGRATION_REPORT_SCHEMA = "pa_auto_test.legacy_stage_result_migration"
LEGACY_MIGRATION_REPORT_VERSION = 1

# 旧运行目录中可能保存正式结果的固定输入路径。主功放结果从未作为可复用输入，
# 因此不在迁移范围；它仍可由旧运行目录只读浏览。
LEGACY_STAGE_INPUTS: tuple[tuple[MeasurementStage, str], ...] = (
    (MeasurementStage.CABLE_LOSS, CABLE_LOSS_INPUT_PATH),
    (MeasurementStage.DRIVER_MAPPING, DRIVER_MAPPING_INPUT_PATH),
)

# 迁移身份的固定前缀：让阶段目录里一眼能看出结果来自旧运行复制，而不是新测量。
_LEGACY_IDENTITY_PREFIX = "legacy_"
_LEGACY_IDENTITY_DIGEST_LENGTH = 32


class LegacyMigrationOutcome(str, Enum):
    """单份旧结果文件的迁移处理结果。"""

    MIGRATED = "migrated"
    ALREADY_MIGRATED = "already_migrated"
    CONFLICT = "conflict"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class LegacyMigrationEntry:
    """一份旧结果文件的处理记录。"""

    run_id: str
    stage: MeasurementStage
    source_path: Path
    outcome: LegacyMigrationOutcome
    measurement_id: str | None = None
    target_path: Path | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "stage": self.stage.value,
            "source_path": str(self.source_path),
            "outcome": self.outcome.value,
            "measurement_id": self.measurement_id,
            "target_path": None if self.target_path is None else str(self.target_path),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class LegacyMigrationReport:
    """一次迁移的完整结果；``to_dict()`` 是可直接落盘的报告文档。"""

    entries: tuple[LegacyMigrationEntry, ...] = ()

    def counts(self) -> dict[str, int]:
        """按处理结果统计条目数；每种结果都出现，便于命令行直接展示。"""
        counts = {outcome.value: 0 for outcome in LegacyMigrationOutcome}
        for entry in self.entries:
            counts[entry.outcome.value] += 1
        return counts

    @property
    def ok(self) -> bool:
        """没有任何失败或冲突时才算一次干净的迁移。"""
        return not any(
            entry.outcome in {LegacyMigrationOutcome.FAILED, LegacyMigrationOutcome.CONFLICT}
            for entry in self.entries
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": LEGACY_MIGRATION_REPORT_SCHEMA,
            "schema_version": LEGACY_MIGRATION_REPORT_VERSION,
            "ok": self.ok,
            "counts": self.counts(),
            "entries": [entry.to_dict() for entry in self.entries],
        }


class LegacyStageResultMigration:
    """扫描旧运行目录并把合法正式结果复制到对应阶段目录。

    只依赖运行仓储与阶段结果仓库两个端口：源目录由运行仓储枚举，写入由阶段结果
    仓库完成，因此目录边界、原子写入、拒绝覆盖、正式 DTO 校验都只实现一次。
    """

    def __init__(
        self,
        *,
        stage_results: StageResultRepository,
        run_repository: RunRepositoryPort,
    ) -> None:
        if stage_results is None:
            raise ValueError("旧结果迁移必须注入阶段结果仓库")
        if run_repository is None:
            raise ValueError("旧结果迁移必须注入运行仓储")
        self._stage_results = stage_results
        self._runs = run_repository
        # ``(阶段, measurement_id) -> 阶段目录文件``；迁移前建立索引，已迁移的结果
        # 不必再让原子写入走到“目标已存在”才判断，也避免重复列举整个阶段目录。
        self._existing: dict[tuple[MeasurementStage, str], Path] = {}

    def run(self) -> LegacyMigrationReport:
        """扫描全部运行目录，返回逐文件处理结果。"""
        self._existing = self._index_existing_results()
        entries: list[LegacyMigrationEntry] = []
        for run_id in self._runs.list_run_ids(include_invalid=True):
            layout = self._runs.layout(run_id)
            for stage, relative_path in LEGACY_STAGE_INPUTS:
                source = layout.run_directory / Path(*relative_path.split("/"))
                # 迁移边界限定为运行目录内的普通输入文件。跟随符号链接可能会把
                # runs 树外的文件当成旧结果读取，因此对此明确记录失败而不读取。
                if source.is_symlink():
                    entries.append(
                        self._failed(
                            run_id,
                            stage,
                            source,
                            "旧结果源文件不能是符号链接",
                        )
                    )
                    continue
                if not source.is_file():
                    continue
                entries.append(self._migrate_one(run_id, stage, source))
        return LegacyMigrationReport(tuple(entries))

    def _index_existing_results(
        self,
    ) -> dict[tuple[MeasurementStage, str], Path]:
        existing: dict[tuple[MeasurementStage, str], Path] = {}
        for stage, _relative_path in LEGACY_STAGE_INPUTS:
            for entry in self._stage_results.list_results(stage):
                existing[(stage, entry.measurement_id)] = entry.path
        return existing

    def _migrate_one(
        self,
        run_id: str,
        stage: MeasurementStage,
        source: Path,
    ) -> LegacyMigrationEntry:
        try:
            raw_bytes = source.read_bytes()
        except OSError as error:
            return self._failed(run_id, stage, source, f"无法读取旧结果文件: {error}")
        try:
            document = _decode_result_document(raw_bytes, source)
        except ValueError as error:
            return self._failed(run_id, stage, source, str(error))
        if document.get("schema") == STAGE_RESULT_REFERENCE_SCHEMA:
            return LegacyMigrationEntry(
                run_id=run_id,
                stage=stage,
                source_path=source,
                outcome=LegacyMigrationOutcome.SKIPPED,
                detail="运行目录已登记阶段结果引用，不是待迁移的完整结果文档",
            )
        try:
            parsed = measurement_result_from_document(document)
        except (TypeError, ValueError) as error:
            return self._failed(
                run_id, stage, source, f"旧结果文件不是正式测量结果: {error}"
            )
        if parsed.measurement_type is not stage.measurement_type:
            return self._failed(
                run_id,
                stage,
                source,
                "结果类型与阶段不符："
                f"{stage.value} 需要 {stage.measurement_type.value}，"
                f"收到 {parsed.measurement_type.value}",
            )
        if parsed.status is not MeasurementStatus.COMPLETED:
            return LegacyMigrationEntry(
                run_id=run_id,
                stage=stage,
                source_path=source,
                outcome=LegacyMigrationOutcome.SKIPPED,
                detail=f"旧结果状态为 {parsed.status.value}，不作为正式阶段结果迁移",
            )
        try:
            source_run_id = _source_run_id(document, run_id)
        except ValueError as error:
            return self._failed(run_id, stage, source, str(error))
        if parsed.run_id != source_run_id:
            return self._failed(
                run_id,
                stage,
                source,
                f"结果文档的 run_id {parsed.run_id!r} 与来源运行 "
                f"{source_run_id!r} 不一致",
            )
        try:
            identity = _migration_identity(parsed, stage, source_run_id, raw_bytes)
            migrated = replace(parsed, measurement_id=identity)
            require_measurement_identity(migrated)
        except ValueError as error:
            return self._failed(run_id, stage, source, str(error))

        target = self._existing.get((stage, identity))
        if target is not None:
            return self._classify_existing(
                run_id, stage, source, migrated, target
            )
        try:
            reference = self._stage_results.save(stage, migrated)
        except StageResultError as error:
            # 另一个进程可能在本进程建立索引之后写入同一目标；重新定位后按内容
            # 一致/冲突分类，绝不覆盖。
            target = self._locate_existing(stage, identity)
            if target is not None:
                return self._classify_existing(
                    run_id, stage, source, migrated, target
                )
            return self._failed(run_id, stage, source, str(error))
        except OSError as error:
            return self._failed(run_id, stage, source, f"写入阶段目录失败: {error}")

        self._existing[(stage, identity)] = reference.path
        return LegacyMigrationEntry(
            run_id=run_id,
            stage=stage,
            source_path=source,
            outcome=LegacyMigrationOutcome.MIGRATED,
            measurement_id=identity,
            target_path=reference.path,
            detail="已复制到阶段结果目录",
        )

    def _locate_existing(
        self, stage: MeasurementStage, measurement_id: str
    ) -> Path | None:
        for entry in self._stage_results.list_results(stage):
            if entry.measurement_id == measurement_id:
                return entry.path
        return None

    def _classify_existing(
        self,
        run_id: str,
        stage: MeasurementStage,
        source: Path,
        migrated: MeasurementResult,
        target: Path,
    ) -> LegacyMigrationEntry:
        try:
            loaded = self._stage_results.load(stage, target)
        except StageResultError as error:
            return self._failed(
                run_id,
                stage,
                source,
                f"目标已存在但无法作为正式结果读取，未覆盖: {error}",
            )
        if loaded.result.to_dict() == migrated.to_dict():
            return LegacyMigrationEntry(
                run_id=run_id,
                stage=stage,
                source_path=source,
                outcome=LegacyMigrationOutcome.ALREADY_MIGRATED,
                measurement_id=migrated.measurement_id,
                target_path=loaded.path,
                detail="目标文件已存在且内容一致，本次未改写",
            )
        return LegacyMigrationEntry(
            run_id=run_id,
            stage=stage,
            source_path=source,
            outcome=LegacyMigrationOutcome.CONFLICT,
            measurement_id=migrated.measurement_id,
            target_path=loaded.path,
            detail="目标文件已存在且内容不同，未覆盖；请人工核对",
        )

    @staticmethod
    def _failed(
        run_id: str,
        stage: MeasurementStage,
        source: Path,
        detail: str,
    ) -> LegacyMigrationEntry:
        logger.info("旧结果迁移失败 %s (%s): %s", source, stage.value, detail)
        return LegacyMigrationEntry(
            run_id=run_id,
            stage=stage,
            source_path=source,
            outcome=LegacyMigrationOutcome.FAILED,
            detail=detail,
        )


def legacy_migration_measurement_id(
    stage: MeasurementStage | str,
    run_id: str,
    source_sha256: str,
) -> str:
    """由“阶段 + 来源运行 + 源内容摘要”确定迁移后的测量身份。

    身份必须稳定，重复执行才会指向同一个阶段文件；把源内容摘要纳入身份后，源文件
    被改动会得到新的身份，不会与上一次迁移的内容互相覆盖。它不是新测量生成的身份，
    因此使用 ``legacy_`` 前缀，而不是时间戳格式。
    """
    target_stage = MeasurementStage(stage) if not isinstance(stage, MeasurementStage) else stage
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("来源运行不能为空")
    if not isinstance(source_sha256, str) or not source_sha256:
        raise ValueError("源内容摘要不能为空")
    seed = f"{target_stage.value}\0{run_id}\0{source_sha256}".encode("utf-8")
    digest = hashlib.sha256(seed).hexdigest()[:_LEGACY_IDENTITY_DIGEST_LENGTH]
    return validate_measurement_id(f"{_LEGACY_IDENTITY_PREFIX}{target_stage.value}_{digest}")


def _migration_identity(
    parsed: MeasurementResult,
    stage: MeasurementStage,
    source_run_id: str,
    raw_bytes: bytes,
) -> str:
    """已有身份时保留，缺失时按源内容生成稳定身份。"""
    if parsed.measurement_id is not None:
        return parsed.measurement_id
    return legacy_migration_measurement_id(
        stage, source_run_id, hashlib.sha256(raw_bytes).hexdigest()
    )


def _source_run_id(document: Mapping[str, Any], run_directory_name: str) -> str:
    """确定结果来源运行；文档缺失时用目录名补齐，冲突时拒绝迁移。"""
    raw = document.get("run_id")
    if raw is None:
        return run_directory_name
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("结果文档的 run_id 为空，无法确认来源运行")
    if raw != run_directory_name:
        raise ValueError(
            f"结果文档的 run_id {raw!r} 与运行目录 {run_directory_name!r} 不一致"
        )
    return raw


def _decode_result_document(raw_bytes: bytes, path: Path) -> Mapping[str, Any]:
    """把旧结果文件解析为 JSON 对象；失败信息与“损坏文件”一致。"""
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(f"旧结果文件不是 UTF-8 文本: {path}: {error}") from error
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"旧结果文件不是有效 JSON: {path}: {error}") from error
    if not isinstance(document, Mapping):
        raise ValueError(f"旧结果文件根节点必须是 JSON 对象: {path}")
    return document
