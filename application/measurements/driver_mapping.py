"""Application use case for driver-power mapping measurement."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from app.cancellation import CancellationToken, MeasurementCancelled
from application.archival_service import ArchivalOutcome, archive_completed_stage
from application.dto import (
    DriverPowerMappingMeasurementResult,
    DriverPowerMappingRequest,
    MeasurementResult,
)
from application.measurements.services import DriverPowerMappingService
from application.ports.measurement_session import CleanupRecord
from domain.run.models import STAGE_CABLE_LOSS, STAGE_DRIVER_MAPPING


logger = logging.getLogger(__name__)


class DriverPowerMappingUseCase:
    """Coordinate driver mapping with run-directory inputs and persistence.

    只消费**当前运行**显式登记的线损输入 ``inputs/cable_loss.json``，并把映射结果
    写回同一运行的 ``inputs/driver_mapping.json``；当前运行线损阶段未完成时在硬件
    动作前失败。运行目录是唯一输入来源，绝不回退到按时间扫描的“最新结果”读取。
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
        self.input_reader = request.input_reader
        # 阶段记录器是请求契约的一部分（D1）：线损输入只从当前运行目录读取，映射结果
        # 写入 inputs/driver_mapping.json 并更新 manifest。
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

    @property
    def last_cleanup_record(self) -> CleanupRecord | None:
        """返回最近一次安全清理的结构化记录。"""
        return self._service.last_cleanup_record

    def stop_measurement(self) -> None:
        """Request cancellation; the service owns the safety cleanup."""
        self._token.request_stop(reason="用户停止")

    def cancel_measurement(self) -> None:
        """Request cancellation without emergency escalation."""
        self._token.request_cancel(reason="任务已取消")

    def emergency_stop(self) -> None:
        """Request emergency cancellation; the service owns safety cleanup."""
        self._token.request_emergency_stop(reason="紧急停止")

    def measure_all_frequencies(self) -> MeasurementResult:
        try:
            self._begin_stage()
            payload = self._service.run()
            result = DriverPowerMappingMeasurementResult.from_service_payload(
                payload,
                run_id=self.run_id,
            )
            self.power_mapping = result.power_mapping()
            result = self._save(result)
            self.last_result = result
            return result
        except MeasurementCancelled as error:
            self._record_cancel(getattr(error, "reason", None) or "测量已取消")
            raise
        except Exception as error:
            self._record_failure(error)
            raise

    # -- 运行契约：线损输入 ------------------------------------------------

    def _load_loss_data(self) -> Mapping[str, Any]:
        """解析本次运行实际消费的线损输入。

        当前运行目录是唯一输入来源：通过
        :meth:`RunStageRecorder.verified_result_path` 强制校验
        ``inputs/cable_loss.json`` 已完成、已登记且校验值一致，绝不回退到全局兼容
        结果或扫描其它运行目录，因此另一个运行的线损数据不会被自动使用，已被替换
        的输入也会被拒绝。
        """
        if self.input_reader is None:
            raise ValueError("DriverPowerMappingUseCase 必须注入 input_reader")
        return self.input_reader.read_cable_loss(
            self.stage_recorder.verified_result_path(STAGE_CABLE_LOSS)
        )

    # -- 运行契约：阶段生命周期 --------------------------------------------

    def _begin_stage(self) -> None:
        """开始当前运行的驱动映射阶段。"""
        self.stage_recorder.start(STAGE_DRIVER_MAPPING)
        self._stage_open = True

    def _record_complete(self, result: MeasurementResult) -> Any:
        """把完成的映射结果写入当前运行并登记为 ``completed``。"""
        reference = self.stage_recorder.complete(STAGE_DRIVER_MAPPING, result.to_dict())
        self._stage_open = False
        return reference

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
        """运行契约运行：权威结果写入 ``inputs/driver_mapping.json``。

        运行目录结果是本次运行的权威来源：先原子提交并登记 manifest，返回值只暴露该
        权威路径；不再同时写全局兼容副本。正式结果提交后立即触发自动归档：驱动映射
        没有 CSV/报告交付产物，因此按契约登记为 ``not_applicable``。
        """
        reference = self._record_complete(result)
        self.last_archival_outcome = archive_completed_stage(
            self.archival_service,
            run_id=self.run_id,
            stage=STAGE_DRIVER_MAPPING,
        )
        archive_path = Path(self.run_directory) / reference.path
        return result.with_saved_result(archive_path=str(archive_path))


def _service_config(configuration: Any) -> Mapping[str, Any]:
    if isinstance(configuration, Mapping):
        return configuration
    plan = configuration.test_plan
    attenuator = plan.attenuator_value
    return {
        "test_frequencies": list(plan.frequencies),
        "attenuator": {"type": f"{attenuator}dB"},
        "signal_source": {
            "start_power": plan.start_power,
            "stop_power": plan.stop_power,
            "step": plan.power_step,
        },
    }
