"""应用层运行阶段服务：在已创建的运行目录内记录阶段状态和阶段结果。

本模块实现重构指南步骤 4 需要的“阶段边界”：

- 阶段开始时把 manifest 中的阶段从 ``pending`` 推进到 ``running``。
- 阶段成功时先把结果原子写入运行目录（默认为 ``inputs/<stage>.json``），
  校验并登记文件，再把阶段推进到 ``completed``，因此 manifest 中的
  ``completed`` 一定对应一个真实存在且校验值一致的结果文件。
- 失败和取消分别把阶段标记为 ``failed`` / ``cancelled``，且不会生成或保留
  看起来完整的阶段结果文件；对已经进入终态的阶段再次写入会被忽略，避免把
  旧数据重新标记为完成。

本模块只依赖领域模型与 :class:`RunRepositoryPort`，不导入基础设施或 Qt，
也不直接访问仪器。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from application.ports.run_repository import RunRepositoryPort
from domain.run.models import (
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
    FileReference,
    RunManifest,
    RunStatus,
    StageStatus,
    TERMINAL_STAGE_STATUSES,
    default_stage_result_path,
)


logger = logging.getLogger(__name__)

# 除 ``amplifier`` 外，各阶段有固定结果路径；``amplifier`` 允许多个结果文件。
_MISSING_RESULT_PATH = "阶段 {stage} 没有固定结果路径，调用方必须显式指定结果路径"


class StageInputUnavailable(RuntimeError):
    """前置阶段输入未完成、未登记或与 manifest 校验值不一致。

    后续阶段在硬件动作前用它表示“当前运行明确指定的输入不可用”，
    调用方不应回退到全局兼容结果或其它运行目录继续执行。
    """


class RunStageRecorder:
    """在指定运行目录内记录单个阶段的状态和结果。

    ``run_id`` 决定结果文件归属的运行，调用方不能通过路径参数切换到其他运行。
    """

    def __init__(self, repository: RunRepositoryPort, run_id: str) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 不能为空")
        self.repository = repository
        self.run_id = run_id

    # -- 状态读取 ---------------------------------------------------------

    def manifest(self) -> RunManifest:
        """读取当前运行的最新 manifest。"""
        return self.repository.load_manifest(self.run_id)

    def stage_status(self, stage: str) -> StageStatus:
        return self.manifest().stage(stage).status

    def is_completed(self, stage: str) -> bool:
        """阶段是否已 ``completed``；阶段输入只有在此时才可被后续阶段消费。"""
        return self.stage_status(stage) is StageStatus.COMPLETED

    def result_path(self, stage: str) -> str | None:
        """返回已完成阶段登记的结果相对路径；未完成时为 ``None``。"""
        record = self.manifest().stage(stage)
        return record.result if record.status is StageStatus.COMPLETED else None

    def verified_result_path(self, stage: str) -> Path:
        """校验前置阶段输入可用，返回当前运行内的绝对路径。

        只有同时满足以下条件才认为阶段输入可被后续阶段消费：

        - 阶段状态为 ``completed`` 且登记了结果路径；
        - 结果文件已登记到 manifest 的 ``inputs``；
        - 文件存在，且 SHA-256 和大小与登记值一致。

        任一条件不满足都抛出 :class:`StageInputUnavailable`，绝不返回可继续使用的
        路径，从而保证后续阶段不会消费缺失或已被替换的当前运行输入。
        """
        manifest = self.repository.load_manifest(self.run_id)
        record = manifest.stage(stage)
        if record.status is not StageStatus.COMPLETED or record.result is None:
            raise StageInputUnavailable(
                f"当前运行 {self.run_id} 的阶段 {stage} 尚未完成，不能作为后续阶段的输入"
            )
        reference = next(
            (item for item in manifest.inputs if item.path == record.result), None
        )
        if reference is None:
            raise StageInputUnavailable(
                f"当前运行 {self.run_id} 的阶段 {stage} 结果 {record.result} 未登记到 manifest"
            )
        try:
            return self.repository.verify_file(self.run_id, reference)
        except (FileNotFoundError, ValueError) as error:
            raise StageInputUnavailable(
                f"当前运行 {self.run_id} 的阶段 {stage} 输入校验失败: {error}"
            ) from error

    # -- 阶段生命周期 -----------------------------------------------------

    def start(self, stage: str, *, now: datetime | None = None) -> RunManifest:
        """把 ``pending`` 阶段推进到 ``running``；已 ``running`` 时保持不变。

        已进入终态的阶段不能被重新开始；重跑必须创建新运行或显式覆盖。
        """
        manifest = self.repository.load_manifest(self.run_id)
        record = manifest.stage(stage)
        if record.status is StageStatus.RUNNING:
            return manifest
        if record.status is not StageStatus.PENDING:
            raise ValueError(
                f"阶段 {stage} 当前状态为 {record.status.value}，不能重新开始"
            )
        manifest = self._mark_run_running(manifest, now=now)
        manifest = manifest.transition_stage(stage, StageStatus.RUNNING, now=now)
        return self.repository.update_manifest(self.run_id, manifest, now=now)

    def complete(
        self,
        stage: str,
        payload: Any,
        *,
        result_path: str | None = None,
        description: str | None = None,
        now: datetime | None = None,
    ) -> FileReference:
        """写入阶段结果并登记为 ``completed``，返回结果文件引用。

        顺序固定为“文件完成 -> 校验登记 -> 更新 manifest”，因此 manifest 报告
        完成时结果文件必定存在且校验值一致。阶段尚未开始时自动先推进到 ``running``；
        阶段已进入终态时拒绝覆盖。
        """
        manifest = self.repository.load_manifest(self.run_id)
        record = manifest.stage(stage)
        if record.status in TERMINAL_STAGE_STATUSES:
            raise ValueError(
                f"阶段 {stage} 已处于终态 {record.status.value}，不允许重新写入结果"
            )
        path = result_path if result_path is not None else default_stage_result_path(stage)
        if path is None:
            raise ValueError(_MISSING_RESULT_PATH.format(stage=stage))
        if record.status is StageStatus.PENDING:
            manifest = self._mark_run_running(manifest, now=now)
            manifest = manifest.transition_stage(stage, StageStatus.RUNNING, now=now)
            self.repository.update_manifest(self.run_id, manifest, now=now)

        relative = self.repository.write_run_json(self.run_id, path, payload)
        try:
            reference = self.repository.register_file(
                self.run_id,
                relative,
                stage=stage,
                description=description,
                now=now,
            )
            manifest = self.repository.load_manifest(self.run_id)
            manifest = manifest.transition_stage(
                stage, StageStatus.COMPLETED, result=reference.path, now=now
            )
            self.repository.update_manifest(self.run_id, manifest, now=now)
        except Exception:
            # 提交失败必须回滚本次写入，避免留下“看起来完整”的阶段结果。
            self._discard_quietly(relative)
            raise
        return reference

    def fail(self, stage: str, error: str, *, now: datetime | None = None) -> RunManifest:
        """把阶段标记为 ``failed``，记录错误原因并清理未提交结果；终态保持不变。"""
        manifest = self.repository.load_manifest(self.run_id)
        record = manifest.stage(stage)
        if record.status in TERMINAL_STAGE_STATUSES:
            return manifest
        if record.status is StageStatus.PENDING:
            # 失败也要保持运行级状态一致：出现非 pending 阶段即视为运行已开始。
            manifest = self._mark_run_running(manifest, now=now)
            manifest = manifest.transition_stage(stage, StageStatus.RUNNING, now=now)
            self.repository.update_manifest(self.run_id, manifest, now=now)
        self._discard_stage_result(stage)
        manifest = self.repository.load_manifest(self.run_id)
        manifest = manifest.transition_stage(
            stage, StageStatus.FAILED, error=str(error) or "阶段失败", now=now
        )
        return self.repository.update_manifest(self.run_id, manifest, now=now)

    def cancel(self, stage: str, reason: str | None = None, *, now: datetime | None = None) -> RunManifest:
        """把未完成的阶段标记为 ``cancelled`` 并清理未提交结果；终态不变。

        ``pending`` 阶段直接取消，``running`` 阶段取消并记录原因，均不会保留
        阶段结果文件。
        """
        manifest = self.repository.load_manifest(self.run_id)
        record = manifest.stage(stage)
        if record.status in TERMINAL_STAGE_STATUSES:
            return manifest
        note = reason if reason is not None else "阶段已取消"
        self._discard_stage_result(stage)
        manifest = self.repository.load_manifest(self.run_id)
        manifest = manifest.transition_stage(stage, StageStatus.CANCELLED, note=note, now=now)
        return self.repository.update_manifest(self.run_id, manifest, now=now)

    # -- 内部工具 ---------------------------------------------------------

    def _mark_run_running(self, manifest: RunManifest, *, now: datetime | None) -> RunManifest:
        """第一个阶段开始时把运行状态从 ``created`` 推进到 ``running``。"""
        if manifest.status is RunStatus.CREATED:
            return manifest.with_status(RunStatus.RUNNING, now=now)
        return manifest

    def _discard_stage_result(self, stage: str) -> None:
        """清理阶段标准结果路径上的未提交文件；仅对有固定路径的阶段生效。"""
        path = default_stage_result_path(stage)
        if path is None:
            return
        self._discard_quietly(path)

    def _discard_quietly(self, relative_path: str) -> None:
        """尽力删除运行目录内的文件，删除失败只记录日志，不掩盖原始异常。"""
        try:
            self.repository.discard_file(self.run_id, relative_path)
        except Exception:
            logger.exception("清理未提交的运行结果失败: %s", relative_path)


__all__ = [
    "STAGE_CABLE_LOSS",
    "STAGE_DRIVER_MAPPING",
    "RunStageRecorder",
    "StageInputUnavailable",
]
