"""应用层运行阶段服务：在已创建的运行目录内记录阶段状态和阶段结果。

本模块实现运行契约需要的“阶段边界”：

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

本模块只依赖领域模型、应用层端口与结果 DTO 契约，不导入基础设施或 Qt，
也不直接访问仪器。

阶段正式结果从重构步骤 7 起写入独立阶段目录，**运行目录只登记引用**：
:meth:`RunStageRecorder.complete_with_stage_result` 把
:class:`application.ports.stage_result_repository.StageResultReference` 收敛为一份
指针文档（阶段、测量身份、阶段目录路径、SHA-256 与大小），仍写在该阶段固定的
运行相对路径上并登记 manifest，但不复制正式结果本身。读取时
:meth:`RunStageRecorder.verified_result_path` 识别指针文档并解析回阶段目录文件，
因此过渡期的旧读取链路仍消费同一份已校验结果；旧运行目录中的完整结果文档继续
按原路径返回。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from application.dto.calibration_inputs import CalibrationInputSource
from application.dto.measurement_results import (
    measurement_result_filename,
    validate_measurement_id,
)
from application.errors import ConfigurationError
from application.ports.run_repository import RunRepositoryPort
from application.ports.stage_result_repository import (
    LoadedStageResult,
    MeasurementStage,
    StageResultError,
    StageResultReference,
    StageResultRepository,
    coerce_measurement_stage,
)
from domain.run.models import (
    SHA256_PATTERN,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
    FileReference,
    RunManifest,
    RunStatus,
    StageStatus,
    TERMINAL_RUN_STATUSES,
    TERMINAL_STAGE_STATUSES,
    default_partial_stage_result_path,
    default_stage_result_path,
    utc_now_iso,
)


logger = logging.getLogger(__name__)

# 除 ``amplifier`` 外，各阶段有固定结果路径；``amplifier`` 允许多个结果文件。
_MISSING_RESULT_PATH = "阶段 {stage} 没有固定结果路径，调用方必须显式指定结果路径"

# 运行目录内的阶段结果引用文档：只描述阶段目录中的权威结果，不包含结果正文。
# ``schema`` 是识别标记，旧运行目录的完整结果文档没有该字段，因此不会被误判。
STAGE_RESULT_REFERENCE_SCHEMA = "pa_auto_test.stage_result_reference"
STAGE_RESULT_REFERENCE_VERSION = 1

# 本次运行消费的外部校准输入来源：只记录阶段、身份、路径、摘要、大小与来源运行，
# 与本运行产出的阶段结果引用分开，避免把“消费的输入”误读成“产出的结果”。
CALIBRATION_INPUTS_INPUT_PATH = "inputs/calibration_inputs.json"
CALIBRATION_INPUTS_SCHEMA = "pa_auto_test.calibration_inputs"
CALIBRATION_INPUTS_VERSION = 1


class StageInputUnavailable(ConfigurationError, RuntimeError):
    """前置阶段输入未完成、未登记或与 manifest 校验值不一致。

    后续阶段在硬件动作前用它表示“当前运行明确指定的输入不可用”，
    调用方不应回退到全局兼容结果或其它运行目录继续执行。

    归类为配置失败（同时是 ``ValueError``），并保留历史上的 ``RuntimeError``
    基类：分类之前它就是 ``RuntimeError``，既有处理器不得因为归类而失效。
    """


class StageResultNotRecorded(ConfigurationError, RuntimeError):
    """阶段正式结果已写入阶段目录，但运行 manifest 的引用登记失败。

    阶段目录文件是正式结果的权威副本，因此登记失败时**不删除**已经保存的结果：
    删除本身也可能失败，还会把一次已完成的测量变成不可恢复的损失。调用方必须把
    本异常报告成“结果已保存、运行引用登记失败”，不能用“没有结果的失败测量”掩盖。

    运行阶段仍会收敛为 ``failed``（manifest 无法声明 ``completed``），但错误说明
    里给出阶段文件的实际位置：这份结果仍可在阶段目录中找到，并作为后续测量的显式
    输入使用。``reference`` 保留登记失败时已提交的引用事实（路径、身份、摘要与大小）。
    """

    def __init__(
        self,
        stage: str,
        reference: StageResultReference,
        reason: str,
    ) -> None:
        self.stage = coerce_measurement_stage(stage)
        self.reference = reference
        self.reason = reason
        super().__init__(
            f"阶段 {self.stage.value} 的正式结果已保存到 {reference.path}，"
            f"但运行引用登记失败: {reason}"
        )


class RunStageRecorder:
    """在指定运行目录内记录单个阶段的状态和结果。

    ``run_id`` 决定结果文件归属的运行，调用方不能通过路径参数切换到其他运行。
    ``stage_results`` 是阶段结果仓库（可选）：登记阶段结果引用时给出阶段目录事实，
    解析指针文档时用它重新加载并校验权威结果。实际测量链路由组装层注入，旧运行
    目录兼容用例可以省略。
    """

    def __init__(
        self,
        repository: RunRepositoryPort,
        run_id: str,
        *,
        stage_results: StageResultRepository | None = None,
    ) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 不能为空")
        self.repository = repository
        self.run_id = run_id
        self.stage_results = stage_results

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

        步骤 7 起阶段正式结果写入独立阶段目录，运行目录只保存引用文档：识别到
        引用文档时，先按登记值确认引用文件本身未被替换，再用注入的阶段结果仓库
        重新加载权威结果并核对指针中的 SHA-256 与大小，返回阶段目录文件路径。
        旧运行目录中的完整结果文档没有引用标记，仍按原路径返回。

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
            path = self.repository.verify_file(self.run_id, reference)
        except (FileNotFoundError, ValueError) as error:
            raise StageInputUnavailable(
                f"当前运行 {self.run_id} 的阶段 {stage} 输入校验失败: {error}"
            ) from error
        return self._resolve_stage_result_reference(stage, record.result, path)

    def _resolve_stage_result_reference(
        self, stage: str, relative_path: str, verified_path: Path
    ) -> Path:
        """把运行目录的引用文档解析为阶段目录中的权威结果路径。

        旧运行目录的结果文档没有引用标记，原样返回 ``verified_path``。引用文档
        损坏、阶段不符、身份/摘要格式非法或权威结果已被替换时，统一以
        :class:`StageInputUnavailable` 结束，不回退到引用文件或其它路径。
        """
        try:
            document = self.repository.read_run_json(self.run_id, relative_path)
        except (FileNotFoundError, ValueError) as error:
            raise StageInputUnavailable(
                f"当前运行 {self.run_id} 的阶段 {stage} 结果不是可读取的 JSON: {error}"
            ) from error
        try:
            loaded = resolve_stage_result_reference(
                document, self.stage_results, stage=stage
            )
        except (OSError, ValueError) as error:
            raise StageInputUnavailable(
                f"当前运行 {self.run_id} 的阶段 {stage} 引用结果不可用: {error}"
            ) from error
        return verified_path if loaded is None else loaded.path

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

    def complete_with_stage_result(
        self,
        stage: str,
        reference: StageResultReference,
        *,
        result_path: str | None = None,
        extra_files: Mapping[str, Any] | None = None,
        description: str | None = None,
        now: datetime | None = None,
    ) -> FileReference:
        """阶段结果已写入独立阶段目录时，只在运行目录登记引用。

        正式结果的权威副本由阶段结果仓库写进 ``test_results/<stage>/``；本方法不
        写入结果正文，只把阶段、测量身份、阶段目录路径、SHA-256 与大小写成引用
        文档，按阶段固定路径落到运行目录并登记 manifest，然后照常把阶段推进到
        ``completed``。没有固定路径的阶段（主功放）由 ``result_path`` 显式给出引用
        文档位置，``extra_files`` 用于继续登记拆分审计文件。因此“manifest 报告完成”
        与“阶段目录中存在对应结果”仍然是同一件事，但运行目录不再保存同一正式结果
        的第二份副本。

        登记前按引用重新加载权威结果并核对摘要与大小：``complete()`` 用“先写文件、
        再登记校验值”保证 manifest 的 ``completed`` 对应真实文件，引用路径必须守住
        同一个不变式。引用文件缺失、被替换或不再是正式结果时在登记前失败，调用方
        据此报告“结果已保存、运行引用登记失败”（:class:`StageResultNotRecorded`）。
        """
        target_stage = coerce_measurement_stage(stage)
        if not isinstance(reference, StageResultReference):
            raise ValueError("阶段结果引用必须是 StageResultReference")
        if reference.stage is not target_stage:
            raise ValueError(
                "阶段结果引用与阶段不符："
                f"阶段 {target_stage.value} 收到 {reference.stage.value} 的结果"
            )
        self._verify_stage_result_reference(reference)
        path = result_path or default_stage_result_path(stage)
        if path is None:
            raise ValueError(_MISSING_RESULT_PATH.format(stage=stage))
        document = _stage_result_reference_document(
            reference, run_id=self.run_id, now=now
        )
        return self.complete(
            stage,
            document,
            result_path=path,
            extra_files=extra_files,
            description=description,
            now=now,
        )

    # -- 运行契约：本次消费的校准输入来源 -------------------------------

    def record_calibration_inputs(
        self,
        sources: tuple[CalibrationInputSource, ...] | list[CalibrationInputSource],
        *,
        now: datetime | None = None,
    ) -> FileReference | None:
        """登记本次测量实际消费的校准输入来源，返回来源文档的运行引用。

        ``sources`` 是阶段结果仓库在**同一次读取**中给出的事实（阶段、身份、路径、
        摘要、大小与来源运行）。本方法只写运行目录内的来源文档并登记 manifest，不
        写入、不复制阶段结果正文，也不重新读取被消费的文件。

        同一运行可能先后做驱动映射与主功放测量，因此记录按
        ``(消费方测量类型, 输入阶段)`` 合并：同一键重复登记相同事实是幂等 no-op，
        不会因为二次预检改写已登记的来源。合并后的集合与既有记录一致时不落盘。

        运行目录只保存“谁消费了什么”；旧 manifest 没有这份文档时按无记录处理，
        不影响阶段结果按显式路径加载。
        """
        existing = self._read_calibration_inputs()
        entries = self._merged_calibration_inputs(sources, existing)
        if not entries:
            return None
        if entries == existing:
            return self._calibration_inputs_reference()
        document = {
            "schema": CALIBRATION_INPUTS_SCHEMA,
            "version": CALIBRATION_INPUTS_VERSION,
            "run_id": self.run_id,
            "registered_at": utc_now_iso(now),
            "inputs": entries,
        }
        relative = self.repository.write_run_json(
            self.run_id,
            CALIBRATION_INPUTS_INPUT_PATH,
            document,
            overwrite=True,
        )
        return self.repository.register_file(
            self.run_id,
            relative,
            description="本次测量消费的校准输入来源",
            now=now,
        )

    def _merged_calibration_inputs(
        self,
        sources: tuple[CalibrationInputSource, ...] | list[CalibrationInputSource],
        existing: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """按 ``(消费方测量类型, 输入阶段)`` 合并来源记录，顺序稳定。"""
        merged: dict[tuple[str, str], dict[str, Any]] = {}
        for entry in existing or ():
            merged[(entry["measurement_type"], entry["stage"])] = entry
        for source in sources:
            if not isinstance(source, CalibrationInputSource):
                raise ValueError(
                    "校准输入来源必须是 CalibrationInputSource，"
                    f"收到 {type(source).__name__}"
                )
            entry = source.to_dict()
            merged[(entry["measurement_type"], entry["stage"])] = entry
        return [merged[key] for key in sorted(merged)]

    def _read_calibration_inputs(self) -> list[dict[str, Any]]:
        """读取已登记的来源记录；缺失、未登记或不可校验时按“没有记录”处理。

        来源文档是审计信息，不是阶段结果的权威副本：它丢失或被改写时，新一次登记
        应当完整重写，而不是让测量因为审计文档不可读而停止。阶段结果仍必须按显式
        路径重新加载校验。
        """
        try:
            document = self.repository.read_run_json(
                self.run_id, CALIBRATION_INPUTS_INPUT_PATH
            )
        except (FileNotFoundError, ValueError):
            return []
        if not isinstance(document, Mapping):
            return []
        if document.get("schema") != CALIBRATION_INPUTS_SCHEMA:
            return []
        raw_inputs = document.get("inputs")
        if not isinstance(raw_inputs, list):
            return []
        entries: list[dict[str, Any]] = []
        for item in raw_inputs:
            if not isinstance(item, Mapping):
                return []
            try:
                entries.append(CalibrationInputSource.from_dict(item).to_dict())
            except (TypeError, ValueError):
                return []
        return entries

    def _calibration_inputs_reference(self) -> FileReference | None:
        """返回来源文档已登记的运行引用；尚未登记时返回 ``None``。"""
        manifest = self.repository.load_manifest(self.run_id)
        return next(
            (
                item
                for item in manifest.inputs
                if item.path == CALIBRATION_INPUTS_INPUT_PATH
            ),
            None,
        )

    def _verify_stage_result_reference(self, reference: StageResultReference) -> None:
        """登记前复核阶段目录文件确实存在且与引用事实一致。

        ``complete()`` 用“先写文件、再登记校验值”保证 manifest 的 ``completed``
        一定有真实文件；引用路径必须守住同一个不变式。参考文件缺失、被替换或不再是
        正式 ``completed`` 结果时在这里以 ``ValueError``/``OSError`` 失败，绝不登记
        一份无法兑现的完成声明。
        """
        if self.stage_results is None:
            raise ValueError(
                "登记阶段结果引用需要注入阶段结果仓库，无法复核引用文件"
            )
        loaded = self.stage_results.load(reference.stage, reference.path)
        if (loaded.sha256, loaded.size_bytes) != (reference.sha256, reference.size_bytes):
            raise ValueError(
                f"阶段结果引用与阶段目录文件不一致: {reference.path}"
            )

    def save_partial(
        self,
        stage: str,
        payload: Any,
        *,
        result_path: str | None = None,
        description: str | None = None,
        now: datetime | None = None,
    ) -> FileReference:
        """保存用户停止时已完成的部分结果，但不把阶段标记为完成。

        部分结果写在 ``results/`` 下并登记为输出，阶段随后仍由 :meth:`cancel`
        标记为 ``cancelled``。因此：

        - 后续阶段的 :meth:`verified_result_path` 不会接受它（阶段不是 completed）；
        - 历史浏览仍能按运行 manifest 登记找到它；
        - 它不会覆盖阶段正式结果路径，也不会生成 CSV/报告。

        写入或登记失败会原样抛出，由用例决定是否降级为日志；停止流程不能因此被
        掩盖。写入与登记是一个提交边界：登记没有成功时尽力删除刚写入的文件，避免
        留下“历史列表看不到的半截部分结果”。
        """
        path = (
            result_path
            if result_path is not None
            else default_partial_stage_result_path(stage)
        )
        if path is None:
            raise ValueError(f"阶段 {stage} 没有固定的部分结果路径")
        relative = self.repository.write_run_json(
            self.run_id, path, payload, overwrite=True
        )
        registered = False
        try:
            reference = self.repository.register_file(
                self.run_id,
                relative,
                stage=stage,
                description=description,
                now=now,
            )
            registered = True
        finally:
            # 登记是“部分结果已保存”的唯一判据；任何失败（含意外异常）都要回滚
            # 这次写入，但不得吞掉异常本身。
            if not registered:
                self._discard_quietly(relative)
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


def _stage_result_reference_document(
    reference: StageResultReference, *, run_id: str, now: datetime | None = None
) -> dict[str, Any]:
    """把阶段结果引用收敛为运行目录内的指针文档（不含结果正文）。"""
    return {
        "schema": STAGE_RESULT_REFERENCE_SCHEMA,
        "schema_version": STAGE_RESULT_REFERENCE_VERSION,
        "stage": reference.stage.value,
        "measurement_id": reference.measurement_id,
        "path": str(reference.path),
        "sha256": reference.sha256,
        "size_bytes": reference.size_bytes,
        "run_id": run_id,
        "created_at": utc_now_iso(now),
    }


def read_stage_result_reference(document: Any) -> StageResultReference | None:
    """把运行目录的阶段结果引用文档解析为结构化引用。

    不是引用文档（例如旧运行目录里的完整结果文档）时返回 ``None``，调用方按原
    语义处理；引用文档自身不自洽时抛 ``ValueError``。本函数只校验文档内容，不读取
    阶段目录文件，也不判断结果是否可用。
    """
    if not _is_stage_result_reference_document(document):
        return None
    stage, path, digest, size_bytes = _stage_result_reference_target(
        document, document.get("stage")
    )
    return StageResultReference(
        stage=stage,
        measurement_id=document.get("measurement_id"),
        path=path,
        sha256=digest,
        size_bytes=size_bytes,
    )


def resolve_stage_result_reference(
    document: Any,
    stage_results: StageResultRepository | None,
    *,
    stage: str | None = None,
) -> LoadedStageResult | None:
    """把阶段结果引用文档解析为阶段目录中的权威结果。

    不是引用文档时返回 ``None``，旧运行目录的完整结果文档仍由调用方按原路径处理。
    ``stage`` 给出时要求引用与之一致。解析必须经过阶段结果仓库的 ``load()``：阶段
    目录边界、正式 DTO、类型/状态/身份都在那里校验；随后再核对引用中登记的 SHA-256
    与大小，确保调用方拿到的是登记时的那一份结果。权威文件缺失、被替换或阶段结果
    仓库缺失时抛 ``ValueError``，调用方不得回退到引用文档或其它路径。
    """
    reference = read_stage_result_reference(document)
    if reference is None:
        return None
    if stage is not None:
        expected = coerce_measurement_stage(stage)
        if reference.stage is not expected:
            raise StageResultError(
                "阶段结果引用与阶段不符："
                f"阶段 {expected.value} 引用的是 {reference.stage.value}"
            )
    if stage_results is None:
        raise StageResultError("阶段结果引用需要注入阶段结果仓库，无法解析权威结果")
    loaded = stage_results.load(reference.stage, reference.path)
    if (loaded.sha256, loaded.size_bytes) != (reference.sha256, reference.size_bytes):
        raise StageResultError(f"阶段结果引用已被替换: {loaded.path}")
    return loaded


def _is_stage_result_reference_document(document: Any) -> bool:
    """是否为阶段结果引用文档；旧运行目录的完整结果文档不会被误判。"""
    return (
        isinstance(document, Mapping)
        and document.get("schema") == STAGE_RESULT_REFERENCE_SCHEMA
    )


def _stage_result_reference_target(
    document: Mapping[str, Any], stage: str
) -> tuple[MeasurementStage, Path, str, int]:
    """校验引用文档并返回 ``(阶段, 结果路径, sha256, 大小)``。

    只校验文档自身是否自洽；阶段目录边界、正式 DTO、类型/状态/schema 与最终文件
    事实由阶段结果仓库的 ``load()`` 负责，调用方不得把这里的返回值当作已加载结果。
    """
    target = coerce_measurement_stage(stage)
    version = document.get("schema_version")
    if isinstance(version, bool) or version != STAGE_RESULT_REFERENCE_VERSION:
        raise ValueError(f"不支持的阶段结果引用版本: {version!r}")
    document_stage = coerce_measurement_stage(document.get("stage"))
    if document_stage is not target:
        raise ValueError(
            "阶段结果引用与阶段不符："
            f"阶段 {target.value} 引用的是 {document_stage.value}"
        )
    identity = validate_measurement_id(document.get("measurement_id"))
    raw_path = document.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("阶段结果引用缺少结果路径")
    path = Path(raw_path)
    if not path.is_absolute():
        raise ValueError("阶段结果引用的结果路径必须是绝对路径")
    if path.name != measurement_result_filename(identity):
        raise ValueError(
            "阶段结果引用的文件名必须由 measurement_id 构造："
            f"{path.name!r} 与 {identity!r} 不一致"
        )
    digest = document.get("sha256")
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise ValueError("阶段结果引用的摘要必须是 64 位小写十六进制 sha256")
    size_bytes = document.get("size_bytes")
    if (
        isinstance(size_bytes, bool)
        or not isinstance(size_bytes, int)
        or size_bytes < 0
    ):
        raise ValueError("阶段结果引用的大小必须是非负整数")
    return document_stage, path, digest, size_bytes


__all__ = [
    "CALIBRATION_INPUTS_INPUT_PATH",
    "CALIBRATION_INPUTS_SCHEMA",
    "CALIBRATION_INPUTS_VERSION",
    "STAGE_CABLE_LOSS",
    "STAGE_DRIVER_MAPPING",
    "STAGE_RESULT_REFERENCE_SCHEMA",
    "STAGE_RESULT_REFERENCE_VERSION",
    "RunStageRecorder",
    "StageInputUnavailable",
    "StageResultNotRecorded",
    "read_stage_result_reference",
    "resolve_stage_result_reference",
]
