"""Application use case for driver-power mapping measurement."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from app.cancellation import CancellationToken, MeasurementCancelled
from application.archival_service import ArchivalOutcome, archive_completed_stage
from application.dto import (
    DriverPowerMappingMeasurementResult,
    DriverPowerMappingRequest,
    MeasurementResult,
    MeasurementStatus,
    generate_measurement_id,
)
from application.measurements.services import DriverPowerMappingService
from application.inputs.explicit_stage_input import load_selected_stage_result
from application.ports.measurement_session import CleanupRecord
from application.ports.stage_result_repository import MeasurementStage
from application.run_stages import StageResultNotRecorded
from domain.run.models import STAGE_CABLE_LOSS, STAGE_DRIVER_MAPPING


logger = logging.getLogger(__name__)


class DriverPowerMappingUseCase:
    """Coordinate driver mapping with explicit inputs and stage-directory persistence.

    线损输入优先使用**页面显式选择的阶段结果文件**（请求中的 ``loss_data``/
    ``cable_loss_path``），允许复用其它运行的线损结果；省略显式来源的旧调用方才
    读取当前运行登记的 ``inputs/cable_loss.json``。已完成映射结果仍然写入独立阶段
    目录 ``test_results/driver_mapping/``，当前运行只登记指向该阶段文件的引用文档。
    线损输入缺失、损坏或阶段契约不满足时，都在硬件动作前失败；绝不回退到按时间
    扫描的“最新结果”读取。
    """

    def __init__(
        self,
        request: DriverPowerMappingRequest,
        *,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.request = request
        if request.measurement_port is None:
            raise ValueError("DriverPowerMappingUseCase 必须由应用组装层注入 measurement_port")
        self.config = _service_config(request.configuration)
        self.inst_ctrl = request.measurement_port
        self.owns_measurement_port = bool(
            request.safety_options.get("owns_measurement_port", False)
        )
        self.run_id = request.run_id
        self.run_directory = request.run_directory
        self.result_repository = request.result_repository
        if self.result_repository is None:
            raise ValueError("DriverPowerMappingUseCase 必须注入 result_repository")
        self.safety_cleanup = request.safety_cleanup
        if self.safety_cleanup is None:
            raise ValueError("DriverPowerMappingUseCase 必须由应用组装层注入 safety_cleanup")
        # 阶段结果仓库是正式结果写入边界（步骤 8）：结果写入独立阶段目录，运行
        # manifest 只登记引用。
        self.stage_result_repository = request.stage_result_repository
        if self.stage_result_repository is None:
            raise ValueError(
                "DriverPowerMappingUseCase 必须由应用组装层注入 stage_result_repository"
            )
        self.input_reader = request.input_reader
        # 阶段记录器是请求契约的一部分（D1）：省略显式来源时线损输入才从当前运行
        # 目录读取；映射结果的引用登记到 inputs/driver_mapping.json 并更新 manifest。
        self.stage_recorder = request.stage_recorder
        self._stage_open = False
        # 阶段成功提交后的自动归档入口；组装层总是注入。
        self.archival_service = request.archival_service
        self.last_archival_outcome: ArchivalOutcome | None = None
        self.loss_data = self._load_loss_data()
        self.sleep_fn = sleep_fn or time.sleep
        self._token = request.cancellation_token or CancellationToken()
        self._service = DriverPowerMappingService(
            self.config,
            self.inst_ctrl,
            self.loss_data,
            run_id=self.run_id,
            safety_cleanup=self.safety_cleanup,
            event_sink=request.event_sink,
            cancellation_token=self._token,
            sleep_fn=self.sleep_fn,
            settle_delay_s=3.0,
            owns_measurement_port=self.owns_measurement_port,
        )
        self.power_mapping: dict[str, dict[str, float]] = {}
        self.last_result: MeasurementResult | None = None
        self.last_partial_result: MeasurementResult | None = None
        self._partial_saved = False

    @property
    def last_cleanup_record(self) -> CleanupRecord | None:
        """返回最近一次安全清理的结构化记录。"""
        return self._service.last_cleanup_record

    def stop_measurement(self) -> None:
        """Request cancellation; the service owns the safety cleanup."""
        self._token.request_stop(reason="用户停止")

    def measure_all_frequencies(self) -> MeasurementResult:
        try:
            self._begin_stage()
            payload = self._service.run()
            result = DriverPowerMappingMeasurementResult.from_service_payload(
                payload,
                run_id=self.run_id,
            )
            self.power_mapping = result.raw_mapping()
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
        except Exception as error:
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
            result = DriverPowerMappingMeasurementResult.from_service_payload(
                payload,
                run_id=self.run_id,
                status=MeasurementStatus.CANCELLED,
                termination={"intent": "stop", "reason": reason or "用户停止"},
            )
            if not result.raw_points:
                logger.info("停止时没有完整的驱动映射频点，未保存部分结果")
                return
            reference = self.stage_recorder.save_partial(
                STAGE_DRIVER_MAPPING,
                result.to_dict(),
                description="用户停止时保存的部分结果",
            )
            self.last_partial_result = result.with_saved_result(
                archive_path=str(Path(self.run_directory) / reference.path)
            )
            self._partial_saved = True
        except (OSError, TypeError, ValueError):
            logger.exception("保存驱动映射部分结果失败")

    # -- 运行契约：线损输入 ------------------------------------------------

    def _load_loss_data(self) -> Mapping[str, Any]:
        """解析本次测量实际消费的线损输入。

        优先级固定且不推断：组合入口预检读取的 ``loss_data``（与显式选择路径指向
        同一份文件）→ 请求中的显式 ``cable_loss_path`` → 当前运行 manifest 登记的
        线损引用。前两者与 ``run_id`` 无关，因此可以使用其它运行产生的线损结果；
        只有省略显式来源的旧调用方才走运行引用，并由
        :meth:`RunStageRecorder.verified_result_path` 复核摘要。绝不按时间扫描其它
        运行目录或“最新结果”。

        显式路径带选择时摘要时，这里用注入的阶段结果仓库重新加载并核对摘要——调用方
        绕过组装层直接构造请求时同样不会消费被替换的文件；随后把**同一次读取**得到
        的正式结果直接转成线损文档，不再按路径二次读取。省略摘要只表示旧调用方锁定
        路径，阶段契约仍会被完整校验。
        """
        if self.input_reader is None:
            raise ValueError("DriverPowerMappingUseCase 必须注入 input_reader")
        preloaded = self.request.loss_data
        if preloaded is not None:
            return preloaded
        path = self.request.cable_loss_path
        if path is not None:
            digest = self.request.cable_loss_digest
            if digest is None:
                return self.input_reader.read_cable_loss(path)
            loaded = load_selected_stage_result(
                self.stage_result_repository,
                MeasurementStage.CABLE_LOSS,
                path,
                digest=digest,
                label="线损",
            )
            return self.input_reader.read_cable_loss_result(loaded.result)
        return self.input_reader.read_cable_loss(
            self.stage_recorder.verified_result_path(STAGE_CABLE_LOSS)
        )

    # -- 运行契约：阶段生命周期 --------------------------------------------

    def _begin_stage(self) -> None:
        """开始当前运行的驱动映射阶段。"""
        self.stage_recorder.start(STAGE_DRIVER_MAPPING)
        self._stage_open = True

    def _record_complete(self, reference: Any) -> Any:
        """把阶段结果引用登记到当前运行 manifest。"""
        run_reference = self.stage_recorder.complete_with_stage_result(
            STAGE_DRIVER_MAPPING, reference
        )
        self._stage_open = False
        return run_reference

    def _record_failure(self, error: Exception) -> None:
        if not self._stage_open:
            return
        self._stage_open = False
        self._try_record(
            lambda: self.stage_recorder.fail(
                STAGE_DRIVER_MAPPING, str(error) or error.__class__.__name__
            )
        )

    def _record_cancel(self, reason: str) -> None:
        if not self._stage_open:
            return
        self._stage_open = False
        self._try_record(lambda: self.stage_recorder.cancel(STAGE_DRIVER_MAPPING, reason))

    @staticmethod
    def _try_record(action: Callable[[], Any]) -> None:
        """阶段状态写入失败不应掩盖原始测量结果或异常。"""
        try:
            action()
        except Exception:
            logger.exception("驱动映射阶段状态记录失败")

    # -- 运行契约：结果持久化 ----------------------------------------------

    def _save(self, result: MeasurementResult) -> MeasurementResult:
        """正式完成保存：结果写入 ``driver_mapping/``，运行 manifest 只登记引用。

        顺序固定为“构造带身份的正式 DTO → 原子写入阶段目录 → 登记运行引用”，
        因此阶段目录文件是正式结果的权威来源，运行目录不再复制同一份结果。目标网格
        与原始实测点都保留在正式 DTO 中：前者是后续测量的目标输入，后者是审计依据。
        阶段文件保存与引用登记是两个提交边界：阶段文件已提交后登记失败，不删除这份
        正式结果，而是以 :class:`StageResultNotRecorded` 明确报告“结果已保存、运行
        引用登记失败”，错误说明里给出阶段文件位置；这份结果仍可通过阶段结果仓库
        的显式路径加载或阶段目录列举找到，不依赖运行 manifest。阶段结果提交后立即
        触发自动归档：驱动映射没有 CSV/报告交付产物，因此按契约登记为
        ``not_applicable``。
        """
        formal = self._formal_result(result)
        stage_reference = self.stage_result_repository.save(
            MeasurementStage.DRIVER_MAPPING, formal
        )
        try:
            self._record_complete(stage_reference)
        except Exception as error:
            # 已提交的正式结果不能因为运行记账失败而被删除或隐藏：删除本身也可能
            # 失败，而且会丢掉一次已完成的测量。调用方据异常消息提示结果位置。
            raise StageResultNotRecorded(
                STAGE_DRIVER_MAPPING,
                stage_reference,
                str(error) or error.__class__.__name__,
            ) from error
        self.last_archival_outcome = archive_completed_stage(
            self.archival_service,
            run_id=self.run_id,
            stage=STAGE_DRIVER_MAPPING,
        )
        return formal.with_saved_result(archive_path=str(stage_reference.path))

    @staticmethod
    def _formal_result(
        result: MeasurementResult,
    ) -> DriverPowerMappingMeasurementResult:
        """为新正式结果分配自身身份；已带身份的结果保持不变。"""
        if not isinstance(result, DriverPowerMappingMeasurementResult):
            raise ValueError(
                "DriverPowerMappingUseCase 只能保存 DriverPowerMappingMeasurementResult"
            )
        if result.measurement_id is not None:
            return result
        return replace(result, measurement_id=generate_measurement_id())


def _service_config(configuration: Any) -> Mapping[str, Any]:
    if isinstance(configuration, Mapping):
        return configuration
    plan = configuration.test_plan
    attenuator = plan.attenuator_value
    return {
        "test_frequencies": list(plan.frequencies),
        "attenuator": {"type": f"{attenuator}dB"},
        # 目标网格的正式来源是 1.2 的有序计划列表。1.1 单段在 TestPlan 读取时已经
        # 合成为一段计划，因此这里不再写旧 start_power/stop_power/step 标量，避免
        # 映射服务出现“列表与标量”两个来源。
        "signal_source": {
            "power_scan_plans": [
                {
                    "start_power": item.start_power,
                    "step": item.step,
                    "stop_power": item.stop_power,
                }
                for item in plan.power_scan_plans
            ],
        },
        # 驱动映射扫描需要目标网格上限与起点预估增益；缺失时服务在构造阶段失败，
        # 不会带着猜测值开始硬件动作。
        "max_dut_input_power": plan.max_input_power,
        "driver_gain_estimate_db": plan.driver_gain_estimate_db,
    }
