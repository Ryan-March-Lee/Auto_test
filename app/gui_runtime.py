"""Application-level operations used by the Qt presentation adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from infrastructure.persistence.json_config_repository import JsonConfigurationRepository
from config_models import (
    validate_cable_loss_configuration,
    validate_driver_mapping_configuration,
)
from config_validation import ConfigValidationResult
from app_logging import get_logger
from infrastructure.persistence.json_result_repository import new_run_id
from .run_context import PreparedRun, environment_version, prepare_run
from instrument.measurement_factory import create_measurement_port
from infrastructure.persistence.result_repository import FileMeasurementResultRepository
from application.inputs import ResultInputReader
from application.measurements import (
    AmplifierMeasurementUseCase,
    CableLossUseCase,
    DriverPowerMappingUseCase,
)
from app.events import MessageEvent, ProgressEvent, RealtimeDataEvent
from application.dto import (
    AmplifierMeasurementRequest,
    CableLossMeasurementRequest,
    DriverPowerMappingRequest,
)


Operation = Literal["full", "cable_loss", "driver_mapping"]
logger = get_logger(__name__)


class _CallbackEventSink:
    """Translate application events to the callback contract used by Qt."""

    def __init__(self, callbacks: dict[str, Any]) -> None:
        self.callbacks = callbacks

    def publish(self, event: Any) -> None:
        if isinstance(event, ProgressEvent) and self.callbacks.get("progress_callback"):
            self.callbacks["progress_callback"](int(event.fraction * 100))
        elif isinstance(event, MessageEvent) and self.callbacks.get("message_callback"):
            self.callbacks["message_callback"](event.message)
        elif isinstance(event, RealtimeDataEvent) and self.callbacks.get("data_callback"):
            self.callbacks["data_callback"](dict(event.data))


def connect_instruments(config_path: str, *, recorder: Any = None) -> Any:
    """Create the configured hardware port through the composition root."""
    return create_measurement_port(config_path, mode="hardware", recorder=recorder)


def _close_owned_measurement_port(port: Any) -> None:
    """Best-effort cleanup when composition fails after opening instruments."""
    try:
        close = getattr(port, "close_all", None)
        if close is not None:
            close(close_rf=True)
            return
        shutdown = getattr(port, "safe_shutdown", None)
        if shutdown is not None:
            shutdown()
    except Exception:
        logger.exception("测量对象构造失败后的仪器清理也失败")


def create_offline_measurement_port(
    *,
    power_channels: dict[str, str] | None = None,
    driver_power_channels: dict[str, str] | None = None,
    recorder: Any = None,
) -> Any:
    """Create the explicit simulation assembly used by offline callers."""
    return create_measurement_port(
        mode="simulation",
        power_channels=power_channels,
        driver_power_channels=driver_power_channels,
        recorder=recorder,
    )


def prepare_configuration(
    config_path: str, *, run_id: str | None = None, operation: Operation = "full"
) -> PreparedRun:
    """Perform the GUI preflight and snapshot before an instrument is created."""
    if operation not in ("full", "cable_loss", "driver_mapping"):
        raise ValueError(f"不支持的测量类型: {operation}")
    repository = JsonConfigurationRepository()
    loaded = repository.load_for_run(config_path)
    if operation == "cable_loss":
        # The legacy GUI stores one combined config.json. Convert it as usual,
        # then apply the smaller contract required by cable-loss measurement.
        # DUT roles and driver channels belong to other measurements.
        cable_validation = validate_cable_loss_configuration(loaded.configuration)
        if loaded.conversion_errors:
            cable_validation = ConfigValidationResult(
                errors=cable_validation.errors + loaded.conversion_errors,
                warnings=cable_validation.warnings,
            )
        loaded = type(loaded)(
            configuration=loaded.configuration,
            validation=cable_validation,
            warnings=loaded.warnings,
            unresolved_fields=loaded.unresolved_fields,
            source_format=loaded.source_format,
            conversion_errors=loaded.conversion_errors,
        )
    elif operation == "driver_mapping":
        # 驱动功放可以由外部供电；映射测量不使用 DUT 电源角色，也不要求
        # 驱动功放必须分配到本机电源。
        mapping_validation = validate_driver_mapping_configuration(loaded.configuration)
        if loaded.conversion_errors:
            mapping_validation = ConfigValidationResult(
                errors=mapping_validation.errors + loaded.conversion_errors,
                warnings=mapping_validation.warnings,
            )
        loaded = type(loaded)(
            configuration=loaded.configuration,
            validation=mapping_validation,
            warnings=loaded.warnings,
            unresolved_fields=loaded.unresolved_fields,
            source_format=loaded.source_format,
            conversion_errors=loaded.conversion_errors,
        )
    return prepare_run(
        loaded,
        run_id=run_id or new_run_id(),
        software_version=environment_version(),
        config_path=Path(config_path),
    )


def _request_kwargs(prepared_run: PreparedRun, config_path: str, callbacks: dict[str, Any]) -> dict[str, Any]:
    """Build the shared request inputs at the application composition boundary."""
    port = callbacks.get("measurement_port")
    if port is None:
        raise ValueError("应用测量请求必须显式包含 measurement_port")
    repository = callbacks.get("result_repository") or FileMeasurementResultRepository()
    return {
        "configuration": prepared_run.configuration,
        "context": prepared_run.context,
        "run_directory": Path(prepared_run.run_directory),
        "measurement_port": port,
        "event_sink": callbacks.get("event_sink"),
        "cancellation_token": callbacks.get("cancellation_token"),
        "result_repository": repository,
        "input_reader": callbacks.get("input_reader") or ResultInputReader(repository),
        "config_path": Path(config_path),
    }


def create_cable_loss_measurement(
    config_path: str, *, prepared_run: PreparedRun, **callbacks: Any
) -> Any:
    callbacks.update(
        run_id=prepared_run.context.run_id,
        run_directory=prepared_run.run_directory,
    )
    owned_port = callbacks.get("measurement_port") is None
    try:
        if owned_port:
            callbacks["measurement_port"] = connect_instruments(config_path)
        callbacks.setdefault("result_repository", FileMeasurementResultRepository())
        callbacks.setdefault("event_sink", _CallbackEventSink(callbacks))
        request = CableLossMeasurementRequest(
            **_request_kwargs(prepared_run, config_path, callbacks)
        )
        return CableLossUseCase(request, sleep_fn=callbacks.get("sleep_fn"))
    except Exception:
        if owned_port and callbacks.get("measurement_port") is not None:
            _close_owned_measurement_port(callbacks["measurement_port"])
        raise


def create_driver_mapping_measurement(
    config_path: str, *, prepared_run: PreparedRun, **callbacks: Any
) -> Any:
    callbacks.update(
        run_id=prepared_run.context.run_id,
        run_directory=prepared_run.run_directory,
    )
    owned_port = callbacks.get("measurement_port") is None
    try:
        if owned_port:
            callbacks["measurement_port"] = connect_instruments(config_path)
        callbacks.setdefault("result_repository", FileMeasurementResultRepository())
        callbacks.setdefault("event_sink", _CallbackEventSink(callbacks))
        request = DriverPowerMappingRequest(
            **_request_kwargs(prepared_run, config_path, callbacks),
            loss_data_path=(Path(callbacks["loss_data_path"]) if callbacks.get("loss_data_path") else None),
        )
        return DriverPowerMappingUseCase(request, sleep_fn=callbacks.get("sleep_fn"))
    except Exception:
        if owned_port:
            port = callbacks.get("measurement_port")
            if port is not None:
                _close_owned_measurement_port(port)
        raise


def create_amplifier_measurement(
    config_path: str, *, prepared_run: PreparedRun, **callbacks: Any
) -> Any:
    callbacks.update(
        run_id=prepared_run.context.run_id,
        run_directory=prepared_run.run_directory,
    )
    owned_port = callbacks.get("measurement_port") is None
    try:
        if owned_port:
            callbacks["measurement_port"] = connect_instruments(config_path)
        callbacks.setdefault("result_repository", FileMeasurementResultRepository())
        callbacks.setdefault("event_sink", _CallbackEventSink(callbacks))
        request = AmplifierMeasurementRequest(
            **_request_kwargs(prepared_run, config_path, callbacks),
            loss_data_path=(Path(callbacks["loss_data_path"]) if callbacks.get("loss_data_path") else None),
            driver_mapping_path=(
                Path(callbacks["driver_mapping_path"])
                if callbacks.get("driver_mapping_path") else None
            ),
            loss_data=callbacks.get("loss_data"),
            driver_mapping=callbacks.get("driver_mapping"),
        )
        return AmplifierMeasurementUseCase(request, sleep_fn=callbacks.get("sleep_fn"))
    except Exception:
        if owned_port and callbacks.get("measurement_port") is not None:
            _close_owned_measurement_port(callbacks["measurement_port"])
        raise
