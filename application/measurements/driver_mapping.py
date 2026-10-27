"""Application use case for driver-power mapping measurement."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from app.cancellation import CancellationToken, MeasurementCancelled
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
    """Coordinate driver mapping with explicit inputs and persistence.

    运行契约运行（注入 ``stage_recorder``）只消费**当前运行**显式登记的线损输入
    ``inputs/cable_loss.json``，并把映射结果写回同一运行的
    ``inputs/driver_mapping.json``；当前运行线损阶段未完成时在硬件动作前失败。
    旧兼容流程不注入 ``stage_recorder``，必须显式提供 ``loss_data`` 或
    ``loss_data_path``；缺少显式输入时在硬件动作前失败，不再回退到按时间扫描的
    “最新结果”读取。
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
        # 运行契约运行会注入阶段记录器：线损输入只从当前运行目录读取，映射结果写入
        # inputs/driver_mapping.json 并更新 manifest；未注入记录器的旧流程只写运行目录。
        self.stage_recorder = request.stage_recorder
        self._stage_open = False
        self.loss_data = self._load_loss_data(request)
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

    def _load_loss_data(self, request: DriverPowerMappingRequest) -> Mapping[str, Any]:
        """解析本次运行实际消费的线损输入。

        运行契约运行（注入 ``stage_recorder``）以当前运行目录为唯一输入来源：
        强制校验 ``inputs/cable_loss.json`` 已完成且校验值一致，显式
        ``loss_data`` / ``loss_data_path`` 只记录告警、不参与解析；旧兼容流程
        （``stage_recorder`` 为 ``None``）必须显式提供 ``loss_data`` 或
        ``loss_data_path``，缺少显式输入时在硬件动作前立即失败，绝不回退到按时间
        扫描的“最新结果”读取。
        """
        if self.stage_recorder is not None:
            if request.loss_data is not None or request.loss_data_path is not None:
                logger.warning(
                    "运行契约运行 %s 忽略显式 loss_data/loss_data_path，只消费当前运行输入",
                    self.run_id,
                )
            return self._read_run_cable_loss()
        if request.loss_data is not None:
            return request.loss_data
        if self.input_reader is None:
            raise ValueError("DriverPowerMappingUseCase 必须注入 input_reader 或 loss_data")
        if request.loss_data_path is not None:
            return self.input_reader.read_cable_loss(request.loss_data_path)
        raise ValueError(
            "旧兼容流程必须显式提供 loss_data_path（或改用当前运行 manifest 的显式输入）；"
            "不再自动查找最新线损结果"
        )

    def _read_run_cable_loss(self) -> Mapping[str, Any]:
        """只从当前运行目录读取已校验的线损输入，缺失时在硬件动作前失败。

        通过 :meth:`RunStageRecorder.verified_result_path` 校验线损阶段已完成、结果
        文件已登记且校验值一致；绝不回退到全局兼容结果或扫描其它运行目录，因此
        另一个运行的线损数据不会被自动使用，已被替换的输入也会被拒绝。
        """
        if self.input_reader is None:
            raise ValueError("驱动映射读取当前运行线损输入时必须注入 input_reader")
        path = self.stage_recorder.verified_result_path(STAGE_CABLE_LOSS)
        return self.input_reader.read_cable_loss(path)

    # -- 运行契约：阶段生命周期 --------------------------------------------

    def _begin_stage(self) -> None:
        """开始当前运行的驱动映射阶段；未注入记录器时保持旧流程行为。"""
        if self.stage_recorder is None:
            return
        self.stage_recorder.start(STAGE_DRIVER_MAPPING)
        self._stage_open = True

    def _record_complete(self, result: MeasurementResult) -> Any:
        """把完成的映射结果写入当前运行并登记为 ``completed``。"""
        if self.stage_recorder is None:
            return None
        reference = self.stage_recorder.complete(STAGE_DRIVER_MAPPING, result.to_dict())
        self._stage_open = False
        return reference

    def _record_failure(self, error: Exception) -> None:
        if self.stage_recorder is None or not self._stage_open:
            return
        self._stage_open = False
        self._try_record(
            lambda: self.stage_recorder.fail(
                STAGE_DRIVER_MAPPING, str(error) or error.__class__.__name__
            )
        )

    def _record_cancel(self, reason: str) -> None:
        if self.stage_recorder is None or not self._stage_open:
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
        if self.stage_recorder is not None:
            return self._save_to_run_directory(result)
        saved = self.result_repository.save(
            result,
            result_type="driver_power_mapping",
            run_id=self.run_id,
            run_directory=self.run_directory,
        )
        self.run_directory = saved.run_directory
        return result.with_saved_result(archive_path=_saved_path(saved, "archive_path"))

    def _save_to_run_directory(self, result: MeasurementResult) -> MeasurementResult:
        """运行契约运行：权威结果写入 ``inputs/driver_mapping.json``。

        运行目录结果是本次运行的权威来源：先原子提交并登记 manifest，返回值只暴露该
        权威路径；不再同时写全局兼容副本。
        """
        reference = self._record_complete(result)
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


def _saved_path(saved: Any, name: str) -> str | None:
    value = getattr(saved, name, None)
    return str(value) if value is not None else None
