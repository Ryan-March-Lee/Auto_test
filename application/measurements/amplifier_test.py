"""Application use case for the main amplifier measurement."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import domain.measurement.calculations as measurement_calculations
from app.cancellation import CancellationToken, MeasurementCancelled
from application.archival_service import ArchivalOutcome, archive_completed_stage
from application.dto import (
    AmplifierMeasurementInputs,
    AmplifierMeasurementRequest,
    AmplifierMeasurementResult,
    DriverMappingInput,
    MeasurementResult,
    MeasurementStatus,
    generate_measurement_id,
)
from application.errors import ConfigurationError
from application.measurements.driver_target_grid import require_driver_target_grid
from application.measurements.services import AmplifierMeasurementService
from application.dto.calibration_inputs import CalibrationInputSource
from application.dto.measurement_results import MeasurementType
from application.inputs.explicit_stage_input import (
    calibration_input_source,
    load_selected_stage_result,
)
from application.ports.measurement_session import CleanupRecord
from application.ports.stage_artifacts import StageArtifactReference
from application.ports.stage_result_repository import MeasurementStage
from application.run_stages import StageInputUnavailable, StageResultNotRecorded
from domain.measurement.values import (
    CableLossTable,
    Frequency,
    MeasurementValueError,
    PowerDbm,
)
from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    STAGE_AMPLIFIER,
    STAGE_DRIVER_MAPPING,
    ArtifactKind,
)


logger = logging.getLogger(__name__)

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")
# 拆分结果文件的稳定格式版本；脱离 GUI 单独读取时据此判断字段语义。
AMPLIFIER_SPLIT_SCHEMA_VERSION = 1


class AmplifierCalibrationError(ConfigurationError):
    """主功放校准输入（线损 / 驱动映射）结构无效。"""


class AmplifierMeasurementUseCase:
    """Coordinate the amplifier sweep and its calibration inputs.

    校准输入只使用组合入口预检并随请求交来的同一份对象，或页面显式选择的阶段结果
    路径（含选择时摘要）；步骤 17 起不再读取当前运行登记的 ``inputs/cable_loss.json`` /
    ``inputs/driver_mapping.json``，缺失显式来源时在硬件动作前失败。步骤 20 起，正式
    结果保存位置只由 ``stage_result_repository`` 决定：装配了它就把主功放结果写入
    ``test_results/amplifier/``，运行目录只登记引用；``stage_artifact_repository`` 只
    决定是否额外生成与结果同目录、由 ``measurement_id`` 关联的 CSV 与报告。未装配阶段
    结果仓库的旧兼容调用方仍把完整结果写入运行目录并触发原来的运行目录归档。驱动模式
    关闭时不要求映射文件存在。绝不回退到按时间扫描的“最新结果”读取。
    """

    def __init__(
        self,
        request: AmplifierMeasurementRequest,
        *,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.request = request
        if request.measurement_port is None:
            raise ValueError("AmplifierMeasurementUseCase 必须由应用组装层注入 measurement_port")
        # 正式输入模型是主功放唯一的输入表示（技术债步骤 8）：用例不再把运行配置
        # 重建成历史测量字典，频率/扫描/压缩目标/衰减器/DUT 限制/驱动模式/供电通道
        # 全部来自同一个类型化对象。
        self.inputs: AmplifierMeasurementInputs = request.inputs
        self.inst_ctrl = request.measurement_port
        self.owns_measurement_port = bool(
            request.safety_options.get("owns_measurement_port", False)
        )
        self.run_id = request.run_id
        self.run_directory = request.run_directory
        self.result_repository = request.result_repository
        if self.result_repository is None:
            raise ValueError("AmplifierMeasurementUseCase 必须注入 result_repository")
        self.safety_cleanup = request.safety_cleanup
        if self.safety_cleanup is None:
            raise ValueError("AmplifierMeasurementUseCase 必须由应用组装层注入 safety_cleanup")
        self.input_reader = request.input_reader
        # 显式输入带选择时摘要时，用阶段结果仓库重新加载并核对，不消费被替换的文件。
        self.stage_result_repository = request.stage_result_repository
        # 阶段交付产物仓库：只决定是否额外生成 CSV/报告，不决定正式结果往哪写。
        # 未装配时仍按阶段结果仓库保存正式结果，只是没有派生产物。
        self.stage_artifact_repository = request.stage_artifact_repository
        if self.stage_artifact_repository is not None and self.stage_result_repository is None:
            raise ValueError(
                "主功放生成阶段交付产物时必须同时注入 stage_result_repository"
            )
        self.last_stage_artifact_references: tuple[StageArtifactReference, ...] = ()
        # 阶段记录器是请求契约的一部分（D1）：结果写入当前运行的 results/ 并更新
        # manifest；本次消费的线损/驱动映射来源也由它登记。
        self.stage_recorder = request.stage_recorder
        self._stage_open = False
        # 阶段成功提交后的自动归档入口；组装层总是注入。
        self.archival_service = request.archival_service
        self.last_archival_outcome: ArchivalOutcome | None = None
        self._calibration_sources: list[CalibrationInputSource] = []
        self.loss_data = self._load_loss_data()
        self.driver_mapping = self._load_driver_mapping()
        # 构造阶段就拒绝结构无效的校准输入，避免先连接仪器或执行 RF 动作后才在扫描
        # 计算中暴露缺字段问题。
        validate_amplifier_calibration_inputs(
            self.loss_data,
            self.driver_mapping,
            frequencies=tuple(item.ghz for item in self.inputs.frequencies),
            driver_enabled=self.inputs.driver_enabled,
        )
        # 驱动模式的目标表必须与配置生成的 DUT 目标点逐点一致（4.4）。这里与
        # 服务构造期使用同一个纯校验入口，数量、顺序、频率或功率不一致都在任何
        # 仪器动作之前失败，而不是扫到某个频点才发现目标表不完整。
        require_driver_target_grid(self.inputs, self.driver_mapping)
        # 输入通过结构校验后才登记来源：未真正消费的输入不留审计记录。
        self._record_calibration_sources()
        self.sleep_fn = sleep_fn or time.sleep
        self._token = request.cancellation_token or CancellationToken()
        self._service = AmplifierMeasurementService(
            self.inputs,
            self.inst_ctrl,
            self.loss_data,
            # 主功放驱动扫描直接消费目标网格；原始实测点只留在结果文档供审计。
            self.driver_mapping,
            run_id=self.run_id,
            safety_cleanup=self.safety_cleanup,
            event_sink=request.event_sink,
            cancellation_token=self._token,
            sleep_fn=self.sleep_fn,
            settle_delay_s=3.0,
            owns_measurement_port=self.owns_measurement_port,
            configuration_snapshot=request.configuration_snapshot,
        )
        self.measurement_results: dict[str, Any] = {}
        self.last_result: MeasurementResult | None = None
        self.last_partial_result: MeasurementResult | None = None
        self._partial_saved = False

    @property
    def last_cleanup_record(self) -> CleanupRecord | None:
        """返回最近一次安全清理的结构化记录。"""
        return self._service.last_cleanup_record

    def stop_measurement(self) -> None:
        self._token.request_stop(reason="用户停止")

    def calculate_actual_power(self, frequency: float, measured_power: float) -> float:
        """按强类型值对象补偿 DUT 输出功率，对外仍返回 dBm 浮点数。

        线损表和衰减器在用例构造时已解析为值对象，与测量扫描使用同一份；
        这里不再按调用重复解析原始文档。
        """
        actual = measurement_calculations.compensate_amplifier_output_power(
            measured_power=PowerDbm(measured_power),
            frequency=Frequency.from_ghz(frequency),
            loss_table=self._service.loss_table,
            attenuator_value=self._service.attenuator_value,
        )
        return actual.dbm

    def perform_power_sweep(self, frequency: float) -> dict[str, Any]:
        return self._service.perform_power_sweep(frequency)

    def measure_all_frequencies(self) -> MeasurementResult:
        try:
            self._begin_stage()
            payload = self._service.run()
            result = AmplifierMeasurementResult.from_service_payload(
                payload,
                run_id=self.run_id,
            )
            self.measurement_results = {
                item.frequency_ghz: item for item in result.frequencies
            }
            result = self._save(result)
            self.last_result = result
            return result
        except MeasurementCancelled as error:
            try:
                self._save_partial(error.reason)
            finally:
                # 停止必须是确定的终态：部分结果保存（含意外异常）不得让阶段
                # 停留在 running，也不得改动“本次已停止”的语义。
                self._record_cancel(getattr(error, "reason", None) or "测量已取消")
            raise
        except Exception as error:  # 任何测量失败都要登记阶段失败后原样上抛
            self._record_failure(error)
            raise

    def _save_partial(self, reason: str) -> None:
        """保存停止前已经完整扫完的频点。

        可预期的持久化失败（I/O、数据契约）只记录日志，停止照常成立；其余异常原样
        上抛，由调用点的 ``finally`` 保证阶段已经落到 ``cancelled``。
        """
        if self._partial_saved or not self._stage_open:
            return
        try:
            payload = self._service.partial_payload()
            result = AmplifierMeasurementResult.from_service_payload(
                payload,
                run_id=self.run_id,
                status=MeasurementStatus.CANCELLED,
                termination={"intent": "stop", "reason": reason or "用户停止"},
            )
            if not result.frequencies:
                logger.info("停止时没有完整的主功放频点，未保存部分结果")
                return
            reference = self.stage_recorder.save_partial(
                STAGE_AMPLIFIER,
                result.to_dict(),
                description="用户停止时保存的部分结果",
            )
            self.last_partial_result = result.with_saved_result(
                archive_path=str(Path(self.run_directory) / reference.path)
            )
            self._partial_saved = True
        except (OSError, TypeError, ValueError):
            logger.exception("保存主功放部分结果失败")

    # -- 运行契约：校准输入 ------------------------------------------------

    def _driver_mode_enabled(self) -> bool:
        return self.inputs.driver_enabled

    def _load_loss_data(self) -> Mapping[str, Any]:
        """解析本次测量实际消费的线损输入。

        组合入口已在预检时读取并交出同一份对象时直接复用，避免同一运行文件被读取
        两次。没有预检对象时只接受显式选择的阶段结果路径：路径一律经阶段结果仓库
        加载（阶段目录边界、正式 DTO、类型与 ``completed`` 状态），带选择时摘要时再
        核对“内容仍是用户选择的那一份”，随后把**同一次读取**的正式结果转成线损
        文档并登记来源。步骤 17 起不再回退到当前运行登记的 ``inputs/cable_loss.json``，
        省略显式来源时直接失败；``verified_result_path`` 只保留给旧数据/迁移。
        """
        preloaded = self.request.calibration_inputs
        if preloaded is not None:
            return preloaded.loss_data
        explicit = self._read_explicit_input(
            self.request.cable_loss_path,
            self.request.cable_loss_digest,
            MeasurementStage.CABLE_LOSS,
            label="线损",
        )
        if explicit is None:
            raise StageInputUnavailable(
                "主功放测量必须显式选择线损结果文件；"
                "新测量链路不再读取当前运行目录的固定校准输入"
            )
        return explicit

    def _load_driver_mapping(self) -> DriverMappingInput | None:
        """解析本次测量实际消费的驱动映射输入。

        驱动模式关闭时不要求映射文件存在，直接返回 ``None``。启用时优先复用组合入口
        预检的同一份对象；没有预检对象时只接受显式选择的阶段结果路径，路径同样经
        阶段结果仓库加载校验（摘要只用于额外核对内容）。步骤 17 起不再读取当前运行
        登记的 ``inputs/driver_mapping.json``，省略显式来源时直接失败，而不是把运行
        目录当成校准输入。返回类型不是裸映射，避免下游把原始曲线误当成目标输入表。
        """
        if not self._driver_mode_enabled():
            return None
        preloaded = self.request.calibration_inputs
        if preloaded is not None:
            if preloaded.driver_mapping is None:
                raise StageInputUnavailable(
                    "驱动模式下的主功放测量必须显式选择驱动映射结果文件；"
                    "新测量链路不再读取当前运行目录的固定校准输入"
                )
            return preloaded.driver_mapping
        explicit = self._read_explicit_input(
            self.request.driver_mapping_path,
            self.request.driver_mapping_digest,
            MeasurementStage.DRIVER_MAPPING,
            label="驱动映射",
        )
        if explicit is None:
            raise StageInputUnavailable(
                "驱动模式下的主功放测量必须显式选择驱动映射结果文件；"
                "新测量链路不再读取当前运行目录的固定校准输入"
            )
        return explicit

    def _read_explicit_input(
        self,
        path: Path | None,
        digest: Any,
        stage: MeasurementStage,
        *,
        label: str,
    ) -> Any:
        """按显式选择的阶段结果文件读取校准输入；未给出路径时返回 ``None``。

        显式路径一律先经阶段结果仓库加载：省略摘要只表示不比较选择时的文件事实，
        阶段目录边界、文件名身份、正式 DTO、类型与完成状态仍会被完整校验，因此
        “有路径但没有摘要”的调用方也不能把运行目录的旧文件当作新链路输入。随后
        用同一次读取的正式结果构造输入，不做第二次文件读取，并记录这次消费的来源。
        """
        if path is None:
            return None
        if self.input_reader is None:
            raise ValueError("主功放消费显式输入时必须注入 input_reader")
        repository = self.stage_result_repository
        if repository is None:
            raise ValueError(
                "主功放消费显式阶段结果时必须注入 stage_result_repository"
            )
        loaded = load_selected_stage_result(
            repository, stage, path, digest=digest, label=label
        )
        self._calibration_sources.append(
            calibration_input_source(MeasurementType.AMPLIFIER, loaded)
        )
        if stage is MeasurementStage.CABLE_LOSS:
            return self.input_reader.read_cable_loss_result(loaded.result)
        return self.input_reader.read_driver_mapping_result(loaded.result)

    def _record_calibration_sources(self) -> None:
        """在硬件动作前登记本次实际消费的校准输入来源。

        组装层已在连接仪器前完成登记；这里覆盖绕过组装层、直接带显式阶段路径构造
        请求的调用方。记录按“消费方测量类型 + 输入阶段”合并且幂等，重复预检不会
        改写既有来源。登记失败直接终止构造，避免带着无法追溯的输入开始测量。
        """
        if not self._calibration_sources:
            return
        self.stage_recorder.record_calibration_inputs(
            tuple(self._calibration_sources)
        )

    # -- 运行契约：阶段生命周期 --------------------------------------------

    def _begin_stage(self) -> None:
        """开始当前运行的主功放阶段。"""
        if not self._driver_mode_enabled():
            # 未启用驱动模式时，映射阶段不属于本次运行，标记为 skipped，
            # 主功放阶段因此不要求映射文件存在。
            self.stage_recorder.skip(STAGE_DRIVER_MAPPING, note="未启用驱动模式")
        self.stage_recorder.start(STAGE_AMPLIFIER)
        self._stage_open = True

    def _record_complete(self, result: MeasurementResult) -> Any:
        """把完成的主功放结果写入当前运行，登记阶段完成并收敛运行状态。"""
        payload = result.to_dict()
        reference = self.stage_recorder.complete(
            STAGE_AMPLIFIER,
            payload,
            result_path=AMPLIFIER_RESULT_PATH,
            extra_files=_split_result_files(payload),
        )
        self._stage_open = False
        # 运行级完成是权威提交的一部分：更新失败必须向调用方暴露，不能把
        # “阶段已完成但运行仍在 running”的状态伪装成完整成功。
        self.stage_recorder.complete_run()
        return reference

    def _record_failure(self, error: Exception) -> None:
        if not self._stage_open:
            return
        self._stage_open = False
        self._try_record(
            lambda: self.stage_recorder.fail(
                STAGE_AMPLIFIER, str(error) or error.__class__.__name__
            )
        )

    def _record_cancel(self, reason: str) -> None:
        if not self._stage_open:
            return
        self._stage_open = False
        self._try_record(lambda: self.stage_recorder.cancel(STAGE_AMPLIFIER, reason))

    @staticmethod
    def _try_record(action: Callable[[], Any]) -> None:
        """阶段状态写入失败不应掩盖原始测量结果或异常。"""
        try:
            action()
        except Exception:
            logger.exception("主功放阶段状态记录失败")

    # -- 运行契约：结果持久化 ----------------------------------------------

    def _save(self, result: MeasurementResult) -> MeasurementResult:
        """运行契约运行：保存权威结果，并按需生成交付产物。

        正式结果写到哪里只由 ``stage_result_repository`` 决定：装配了阶段结果仓库的
        正式链路把主功放结果写入 ``test_results/amplifier/<measurement_id>.json``，
        运行目录只登记引用；未装配它的旧兼容调用方仍把完整结果写入运行目录并触发
        原来的运行目录归档。阶段交付产物仓库是**独立**的可选能力：装配时在正式结果
        提交后尽力生成同目录 CSV 与报告，失败只记录日志，正式结果、阶段和运行状态
        都不受影响。
        """
        if self.stage_result_repository is not None:
            return self._save_stage_result(result)
        reference = self._record_complete(result)
        self.last_archival_outcome = archive_completed_stage(
            self.archival_service,
            run_id=self.run_id,
            stage=STAGE_AMPLIFIER,
        )
        archive_path = Path(self.run_directory) / reference.path
        return result.with_saved_result(archive_path=str(archive_path))

    def _save_stage_result(self, result: MeasurementResult) -> MeasurementResult:
        """把主功放正式结果写入阶段目录，并尽力生成同目录交付产物。"""
        if result.measurement_id is None:
            result = replace(result, measurement_id=generate_measurement_id())
        payload = result.to_dict()
        reference = self.stage_result_repository.save(
            MeasurementStage.AMPLIFIER, result
        )
        try:
            self.stage_recorder.complete_with_stage_result(
                STAGE_AMPLIFIER,
                reference,
                result_path=AMPLIFIER_RESULT_PATH,
                extra_files=_split_result_files(payload),
            )
            self._stage_open = False
            # 运行级完成是权威提交的一部分：更新失败必须向调用方暴露。
            self.stage_recorder.complete_run()
        except Exception as error:
            # 阶段目录文件已提交，是正式结果的权威副本：不能因为运行记账失败就删除
            # 或隐藏它。调用方据异常消息提示“结果已保存、运行引用登记失败”。
            raise StageResultNotRecorded(
                STAGE_AMPLIFIER,
                reference,
                str(error) or error.__class__.__name__,
            ) from error
        self._generate_stage_artifacts(result)
        return result.with_saved_result(archive_path=str(reference.path))

    def _generate_stage_artifacts(self, result: MeasurementResult) -> None:
        """尽力生成与阶段结果同目录的 CSV 与报告；任何失败都不改变正式结果。

        产物是正式结果的可选派生物，不属于权威提交：CSV 与报告分别尝试，单项失败
        不阻止另一项；日志保留完整堆栈便于诊断，但异常不向测量调用方传播（``
        BaseException`` 仍按原语义中断）。
        """
        if self.stage_artifact_repository is None:
            return
        references: list[StageArtifactReference] = []
        for kind in (ArtifactKind.CSV, ArtifactKind.REPORT):
            try:
                references.append(
                    self.stage_artifact_repository.save(
                        MeasurementStage.AMPLIFIER,
                        result.measurement_id,
                        kind,
                        result,
                    )
                )
            except Exception:  # 产物失败不能把已完成的测量变成失败
                logger.exception(
                    "主功放交付产物生成失败 stage=amplifier kind=%s measurement_id=%s",
                    kind.value,
                    result.measurement_id,
                )
        self.last_stage_artifact_references = tuple(references)


def validate_amplifier_calibration_inputs(
    loss_data: Any,
    driver_mapping: Any,
    *,
    frequencies: Any,
    driver_enabled: bool,
) -> None:
    """校验主功放校准输入结构，确保后续计算所需字段都存在。

    只做结构与必需字段校验，不做取值范围判断：

    - ``cable_losses`` 必须是对象，且包含每个测试频率对应对象；
    - 无驱动时每个频率必须包含 ``cable1``（DUT 输入功率 = sg_power - cable1）；
    - 补偿 DUT 输出功率始终需要 ``cable2`` 与 ``cable4``；
    - 驱动模式启用时，每个测试频率必须在正式目标网格中有目标点；原始实测点不参与
      主功放扫描，只作审计。

    文档在边界处收敛为 :class:`CableLossTable` / :class:`DriverMappingInput`；
    “某个计算路径需要哪几段线缆”由 :class:`CableLossProfile` 的
    ``require_*_path()`` 方法表达，本函数不再解释 ``cable1`` 等字符串字段名。

    任一项缺失都抛出 :class:`AmplifierCalibrationError`，供组装层在连接仪器前和
    用例构造时统一拒绝无效输入。
    """
    if not isinstance(loss_data, Mapping):
        raise AmplifierCalibrationError("线损输入必须是 JSON 对象")
    cable_losses = loss_data.get("cable_losses")
    if not isinstance(cable_losses, Mapping):
        raise AmplifierCalibrationError("线损输入缺少 cable_losses 子对象")
    try:
        loss_table = CableLossTable.from_mapping(cable_losses, source="线损输入")
    except (TypeError, ValueError) as error:
        raise AmplifierCalibrationError(str(error)) from error

    frequency_values = [Frequency.from_ghz(value) for value in frequencies]
    for frequency in frequency_values:
        try:
            profile = loss_table.profile_at(frequency)
            # DUT 输出补偿始终需要线④ + 线②；无驱动时 DUT 输入还需要线①。
            profile.require_dut_output_path()
            if not driver_enabled:
                profile.require_dut_input_path()
        except MeasurementValueError as error:
            raise AmplifierCalibrationError(str(error)) from error
    if not driver_enabled:
        return
    if not isinstance(driver_mapping, DriverMappingInput):
        raise AmplifierCalibrationError(
            "驱动模式启用时缺少有效的双层驱动映射输入；"
            "请重新读取当前运行的 driver_mapping.json"
        )
    target_frequencies = {
        Frequency.from_document_key(point.frequency_ghz)
        for point in driver_mapping.target_points
    }
    for frequency in frequency_values:
        if frequency not in target_frequencies:
            raise AmplifierCalibrationError(
                f"驱动映射目标表缺少测试频率 {frequency.key} 的目标点"
            )


def _split_result_files(payload: Mapping[str, Any]) -> dict[str, Any]:
    """把每个频率的扫描结果拆成独立的 ``results/`` 文件。

    每个拆分文件带有稳定的 ``schema_version``、``stage``、``run_id``、
    ``measurement_type``、``frequency`` 和 ``result`` 字段，脱离 GUI 后仍可单独
    识别来源与语义。返回值供 :meth:`RunStageRecorder.complete` 作为 ``extra_files``
    一并登记，因此所有拆分文件都能从 manifest 找到。
    """
    results = payload.get("results")
    if not isinstance(results, Mapping):
        return {}
    files: dict[str, Any] = {}
    for frequency, sweep in results.items():
        name = f"results/amplifier_measurement_{_safe_token(frequency)}.json"
        if name in files:
            raise ValueError(f"主功放拆分结果文件名冲突，拒绝覆盖: {name}")
        files[name] = {
            "schema_version": AMPLIFIER_SPLIT_SCHEMA_VERSION,
            "stage": STAGE_AMPLIFIER,
            "run_id": payload.get("run_id"),
            "measurement_type": payload.get("measurement_type"),
            "frequency": frequency,
            "result": sweep,
        }
    return files


def _safe_token(value: Any) -> str:
    """把频率等值转成文件名安全片段，避免路径穿越或非法字符。"""
    token = _UNSAFE_FILENAME.sub("_", str(value)).strip("._")
    return token or "unknown"
