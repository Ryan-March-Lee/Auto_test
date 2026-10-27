"""应用层运行阶段服务：在已创建的运行目录内记录阶段状态和阶段结果。

本模块实现重构指南步骤 4-6 需要的“阶段边界”：

- 阶段开始时把 manifest 中的阶段从 ``pending`` 推进到 ``running``。
- 阶段成功时先把结果原子写入运行目录（固定阶段默认为 ``inputs/<stage>.json``，
  主功放显式指定 ``results/`` 下的主结果并可用 ``extra_files`` 附带多个拆分文件），
  校验并登记文件，再把阶段推进到 ``completed``，因此 manifest 中的
  ``completed`` 一定对应一个真实存在且校验值一致的结果文件。
- 未启用的阶段（例如驱动模式关闭时的 ``driver_mapping``）可用 ``skip`` 标记为
  ``skipped``；标志性阶段全部结束后用 ``complete_run`` 把运行级状态收敛为
  ``completed``。
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
from typing import Any, Mapping

from application.errors import ConfigurationError
from application.ports.run_repository import RunRepositoryPort
from domain.run.models import (
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
    FileReference,
    RunManifest,
    RunStatus,
    StageStatus,
    TERMINAL_RUN_STATUSES,
    TERMINAL_STAGE_STATUSES,
    default_stage_result_path,
)


logger = logging.getLogger(__name__)

# 除 ``amplifier`` 外，各阶段有固定结果路径；``amplifier`` 允许多个结果文件。
_MISSING_RESULT_PATH = "阶段 {stage} 没有固定结果路径，调用方必须显式指定结果路径"


class StageInputUnavailable(ConfigurationError, RuntimeError):
    """前置阶段输入未完成、未登记或与 manifest 校验值不一致。

    后续阶段在硬件动作前用它表示“当前运行明确指定的输入不可用”，
    调用方不应回退到全局兼容结果或其它运行目录继续执行。

    归类为配置失败（同时是 ``ValueError``），并保留历史上的 ``RuntimeError``
    基类：分类之前它就是 ``RuntimeError``，既有处理器不得因为归类而失效。
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

        已进入终态的阶段不能被重新开始；运行本身进入 ``completed`` / ``failed`` /
        ``cancelled`` 终态后也不允许再开始新阶段，避免终态运行被跑出新结果。
        """
        manifest = self.repository.load_manifest(self.run_id)
        if manifest.status in TERMINAL_RUN_STATUSES:
            raise ValueError(
                f"运行 {self.run_id} 已处于终态 {manifest.status.value}，不能开始新的阶段"
            )
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
        extra_files: Mapping[str, Any] | None = None,
        description: str | None = None,
        now: datetime | None = None,
    ) -> FileReference:
        """写入阶段结果并登记为 ``completed``，返回主结果文件引用。

        顺序固定为“文件完成 -> 校验登记 -> 更新 manifest”，因此 manifest 报告
        完成时结果文件必定存在且校验值一致。阶段尚未开始时自动先推进到 ``running``；
        阶段已进入终态时拒绝覆盖。

        ``extra_files`` 用于 ``amplifier`` 这类允许保存多个拆分结果文件的阶段：
        ``{相对路径: 载荷}``，会依次原子写入并登记到 manifest。任一文件提交失败时
        回滚本次已写入的全部文件，不留下“看起来完整”的伪完成结果。
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

        written: list[str] = []
        try:
            relative = self.repository.write_run_json(self.run_id, path, payload)
            written.append(relative)
            reference = self.repository.register_file(
                self.run_id,
                relative,
                stage=stage,
                description=description,
                now=now,
            )
            for extra_path, extra_payload in (extra_files or {}).items():
                extra_relative = self.repository.write_run_json(
                    self.run_id, extra_path, extra_payload
                )
                written.append(extra_relative)
                self.repository.register_file(
                    self.run_id,
                    extra_relative,
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
            for written_path in written:
                self._discard_quietly(written_path)
            raise
        return reference

    def skip(self, stage: str, *, note: str | None = None, now: datetime | None = None) -> RunManifest:
        """把 ``pending`` 阶段标记为 ``skipped``；已 ``skipped`` 时幂等返回。

        未启用驱动模式时用它把 ``driver_mapping`` 阶段标记为 ``skipped``，使主功放
        阶段不会要求映射文件存在，同时记录阶段被有意跳过。

        只允许从 ``pending`` 进入 ``skipped``：``running`` / ``completed`` /
        ``failed`` / ``cancelled`` 等状态不能被静默改成“已跳过”，否则会把未完成或
        已失败的阶段伪装成正常跳过；调用方应改用对应的完成/失败/取消路径。
        """
        manifest = self.repository.load_manifest(self.run_id)
        record = manifest.stage(stage)
        if record.status is StageStatus.SKIPPED:
            return manifest
        if record.status is not StageStatus.PENDING:
            raise ValueError(
                f"阶段 {stage} 当前状态为 {record.status.value}，不能标记为 skipped"
            )
        manifest = manifest.transition_stage(stage, StageStatus.SKIPPED, note=note, now=now)
        return self.repository.update_manifest(self.run_id, manifest, now=now)

    def complete_run(self, *, now: datetime | None = None) -> RunManifest:
        """把运行级状态收敛为 ``completed``。

        只有**所有阶段都已进入 ``completed`` 或 ``skipped``** 时才允许完成运行；
        任何 ``pending`` / ``running`` / ``failed`` / ``cancelled`` 阶段都会让完成
        请求被拒绝，避免把一个仍有未完成或失败阶段的运行标记为完成。

        - 已经 ``completed`` 时按无操作处理，保持幂等。
        - 尚未开始（``created``）或被中断（``interrupted``）的运行先回到 ``running``
          再完成。
        - ``failed`` / ``cancelled`` 运行不允许被静默改成完成，交由运行状态转换
          规则拒绝。
        """
        manifest = self.repository.load_manifest(self.run_id)
        if manifest.status is RunStatus.COMPLETED:
            return manifest
        unfinished = [
            name
            for name, record in manifest.stages.items()
            if record.status not in (StageStatus.COMPLETED, StageStatus.SKIPPED)
        ]
        if unfinished:
            raise ValueError(
                f"运行 {self.run_id} 仍有未完成的阶段，不能标记为 completed: "
                + ", ".join(f"{name}={manifest.stage(name).status.value}" for name in unfinished)
            )
        if manifest.status in (RunStatus.CREATED, RunStatus.INTERRUPTED):
            manifest = manifest.with_status(RunStatus.RUNNING, now=now)
        manifest = manifest.with_status(RunStatus.COMPLETED, now=now)
        return self.repository.update_manifest(self.run_id, manifest, now=now)

    def fail(self, stage: str, error: str, *, now: datetime | None = None) -> RunManifest:
        """把阶段标记为 ``failed``，并把运行级状态同步收敛为 ``failed``。

        阶段失败后本次运行不可能再作为完整运行使用，因此运行级状态也一并置为
        ``failed``（``created`` / ``running`` 允许转换；已终态的运行保持不变），
        避免 manifest 在阶段失败后仍长期显示为 ``running``。已进入终态的阶段不受
        影响。
        """
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
        manifest = self._set_run_terminal(manifest, RunStatus.FAILED, now=now)
        return self.repository.update_manifest(self.run_id, manifest, now=now)

    def cancel(self, stage: str, reason: str | None = None, *, now: datetime | None = None) -> RunManifest:
        """把未完成的阶段标记为 ``cancelled`` 并清理未提交结果，运行级同步为 ``cancelled``。

        ``pending`` 阶段直接取消，``running`` 阶段取消并记录原因，均不会保留
        阶段结果文件。被取消的运行不能再被标记为完成；已进入终态的阶段不受影响。
        """
        manifest = self.repository.load_manifest(self.run_id)
        record = manifest.stage(stage)
        if record.status in TERMINAL_STAGE_STATUSES:
            return manifest
        note = reason if reason is not None else "阶段已取消"
        self._discard_stage_result(stage)
        manifest = self.repository.load_manifest(self.run_id)
        manifest = manifest.transition_stage(stage, StageStatus.CANCELLED, note=note, now=now)
        manifest = self._set_run_terminal(manifest, RunStatus.CANCELLED, now=now)
        return self.repository.update_manifest(self.run_id, manifest, now=now)

    # -- 内部工具 ---------------------------------------------------------

    def _mark_run_running(self, manifest: RunManifest, *, now: datetime | None) -> RunManifest:
        """第一个阶段开始时把运行状态从 ``created`` / ``interrupted`` 推进到 ``running``。"""
        if manifest.status in (RunStatus.CREATED, RunStatus.INTERRUPTED):
            return manifest.with_status(RunStatus.RUNNING, now=now)
        return manifest

    def _set_run_terminal(
        self, manifest: RunManifest, target: RunStatus, *, now: datetime | None
    ) -> RunManifest:
        """阶段失败/取消时把活跃运行同步收敛到对应终态；已终态运行保持不变。"""
        if manifest.status in (RunStatus.CREATED, RunStatus.RUNNING):
            return manifest.with_status(target, now=now)
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
