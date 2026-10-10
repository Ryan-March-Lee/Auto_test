"""Application use case for the main amplifier measurement."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Callable

import measurement_calculations
from app.cancellation import CancellationToken
from application.dto import AmplifierMeasurementRequest, MeasurementResult, legacy_result
from measurement_services import AmplifierMeasurementService


class AmplifierMeasurementUseCase:
    """Coordinate the amplifier sweep and its calibration inputs."""

    def __init__(
        self,
        request: AmplifierMeasurementRequest,
        *,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self.request = request
        if request.measurement_port is None:
            raise ValueError("AmplifierMeasurementUseCase 必须由应用组装层注入 measurement_port")
        self.config = _service_config(request.configuration)
        self.inst_ctrl = request.measurement_port
        self.owns_measurement_port = bool(
            request.safety_options.get("owns_measurement_port", False)
        )
        self.run_id = request.run_id
        self.run_directory = request.run_directory
        self.result_repository = request.result_repository
        if self.result_repository is None:
            raise ValueError("AmplifierMeasurementUseCase 必须注入 result_repository")
        self.input_reader = request.input_reader
        if request.loss_data is not None:
            loss_data = request.loss_data
        else:
            if self.input_reader is None:
                raise ValueError("AmplifierMeasurementUseCase 必须注入 input_reader 或 loss_data")
            loss_data = self.input_reader.read_cable_loss(request.loss_data_path)
        driver_mapping = request.driver_mapping
        if self.config.get("driver_mode", {}).get("enabled") and driver_mapping is None:
            if self.input_reader is None:
                raise ValueError(
                    "AmplifierMeasurementUseCase 必须注入 input_reader 或 driver_mapping"
                )
            driver_mapping = self.input_reader.read_driver_mapping(request.driver_mapping_path)
        self.loss_data = loss_data
        self.driver_mapping = driver_mapping
        self.sleep_fn = sleep_fn or time.sleep
        self._token = request.cancellation_token or CancellationToken()
        self._service = AmplifierMeasurementService(
            self.config,
            self.inst_ctrl,
            loss_data,
            driver_mapping,
            run_id=self.run_id,
            event_sink=request.event_sink,
            cancellation_token=self._token,
            sleep_fn=self.sleep_fn,
            settle_delay_s=3.0,
            owns_measurement_port=self.owns_measurement_port,
        )
        self.measurement_results: dict[str, Any] = {}
        self.last_result: MeasurementResult | None = None

    def stop_measurement(self) -> None:
        self._token.request_stop(reason="用户停止")

    def cancel_measurement(self) -> None:
        self._token.request_cancel(reason="任务已取消")

    def emergency_stop(self) -> None:
        self._token.request_emergency_stop(reason="紧急停止")

    def calculate_actual_power(self, frequency: float, measured_power: float) -> float:
        attenuator = float(self.config["attenuator"]["type"].replace("dB", ""))
        return measurement_calculations.compensate_amplifier_output_power(
            measured_power=measured_power,
            frequency=frequency,
            loss_data=self.loss_data["cable_losses"],
            attenuator_value=attenuator,
        )

    def perform_power_sweep(self, frequency: float) -> dict[str, Any]:
        return self._service.perform_power_sweep(frequency)

    def measure_all_frequencies(self) -> MeasurementResult:
        payload = self._service.run()
        result = legacy_result(
            self.request.measurement_type,
            payload,
            run_id=self.run_id,
        )
        self.measurement_results = result["results"]
        saved = self.result_repository.save(
            result.to_dict(),
            result_type="amplifier_measurement",
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
    if hasattr(configuration, "test_plan") and hasattr(configuration, "run_mapping"):
        return _run_configuration_to_service_config(configuration)
    raise TypeError("无法将运行配置转换为主功放测量配置")


def _saved_path(saved: Any, name: str) -> str | None:
    value = getattr(saved, name, None)
    return str(value) if value is not None else None


def _run_configuration_to_service_config(configuration: Any) -> dict[str, Any]:
    """Convert the split configuration model to the legacy service contract.

    ``AmplifierMeasurementService`` still consumes the historical measurement
    shape.  Legacy runs retain that exact payload in ``TestPlan.raw``; modern
    split runs are rebuilt from the plan and resource mapping at this boundary.
    """
    plan = configuration.test_plan
    raw = getattr(plan, "raw", {})
    required = {
        "test_frequencies",
        "signal_source",
        "compression_point",
        "driver_mode",
        "attenuator",
        "dut_config",
        "power_supply_assignment",
    }
    if isinstance(raw, Mapping) and required.issubset(raw):
        return dict(raw)

    mapping = configuration.run_mapping
    supplies = _assignment_supplies(mapping, "dut_power_channels")
    driver_supplies = _assignment_supplies(mapping, "driver_power_channels")
    supply_assignment = {
        "dut_amplifier": {"supplies": supplies},
        "driver_amplifier": {"supplies": driver_supplies},
    }
    return {
        "test_frequencies": list(plan.frequencies),
        "signal_source": {
            "start_power": plan.start_power,
            "stop_power": plan.stop_power,
            "step": plan.power_step,
        },
        "compression_point": {"type": plan.compression_point},
        "driver_mode": {"enabled": bool(plan.driver_enabled)},
        "attenuator": {"type": f"{plan.attenuator_value}dB"},
        "dut_config": {"max_input_power": plan.max_input_power},
        "power_supply_assignment": supply_assignment,
    }


def _assignment_supplies(mapping: Any, field: str) -> dict[str, dict[str, Any]]:
    channels = getattr(mapping, field, ())
    grouped: dict[str, list[str]] = {}
    supply_name = _selected_supply_name(mapping)
    for item in channels:
        channel = getattr(item, "channel", None)
        if channel:
            grouped.setdefault(supply_name, []).append(channel)
    return {
        name: {"name": name, "channel": channel_names}
        for name, channel_names in grouped.items()
    }


def _selected_supply_name(mapping: Any) -> str:
    instruments = getattr(mapping, "instruments", {})
    power_supply = instruments.get("power_supply") if isinstance(instruments, Mapping) else None
    return getattr(power_supply, "model", None) or "power_supply"
