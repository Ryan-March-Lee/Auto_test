"""Application use case for driver-power mapping measurement."""

from __future__ import annotations

import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from app.cancellation import CancellationToken
from application.dto import DriverPowerMappingRequest, MeasurementResult, legacy_result
from measurement_services import DriverPowerMappingService


class DriverPowerMappingUseCase:
    """Coordinate driver mapping with explicit inputs and persistence."""

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
        self.run_id = request.run_id
        self.run_directory = request.run_directory
        self.result_repository = request.result_repository
        if self.result_repository is None:
            raise ValueError("DriverPowerMappingUseCase 必须注入 result_repository")
        self.input_reader = request.input_reader
        if request.loss_data is not None:
            self.loss_data = request.loss_data
        else:
            if self.input_reader is None:
                raise ValueError("DriverPowerMappingUseCase 必须注入 input_reader 或 loss_data")
            self.loss_data = self.input_reader.read_cable_loss(request.loss_data_path)
        self.sleep_fn = sleep_fn or time.sleep
        self._token = request.cancellation_token or CancellationToken()
        self._service = DriverPowerMappingService(
            self.config,
            self.inst_ctrl,
            self.loss_data,
            run_id=self.run_id,
            event_sink=request.event_sink,
            cancellation_token=self._token,
            sleep_fn=self.sleep_fn,
            settle_delay_s=3.0,
        )
        self.power_mapping: dict[str, dict[str, float]] = {}
        self.last_result: MeasurementResult | None = None

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
        payload = self._service.run()
        result = legacy_result(
            self.request.measurement_type,
            payload,
            run_id=self.run_id,
        )
        self.power_mapping = result["power_mapping"]
        saved = self.result_repository.save(
            result.to_dict(),
            result_type="driver_power_mapping",
            run_id=self.run_id,
            run_directory=self.run_directory,
        )
        self.run_directory = saved.run_directory
        result = result.with_saved_result(
            archive_path=_saved_path(saved, "archive_path"),
            legacy_copy_path=_saved_path(saved, "legacy_copy_path"),
        )
        self.last_result = result
        return result


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
