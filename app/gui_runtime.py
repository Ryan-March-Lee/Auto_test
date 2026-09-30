"""Application-level operations used by the Qt presentation adapters."""

from __future__ import annotations

from typing import Any, Literal

from enhanced_workers import (
    EnhancedAmplifierMeasurement,
    EnhancedCableLossMeasurement,
    EnhancedDriverPowerMapping,
)
from persistence.config_repository import ConfigurationRepository
from config_models import (
    validate_cable_loss_configuration,
    validate_driver_mapping_configuration,
)
from config_validation import ConfigValidationResult
from app_logging import get_logger
from result_storage import new_run_id
from .run_context import PreparedRun, environment_version, prepare_run
from instrument.measurement_factory import create_measurement_port


Operation = Literal["full", "cable_loss", "driver_mapping"]
logger = get_logger(__name__)


def connect_instruments(config_path: str) -> Any:
    """Create the configured hardware port through the composition root."""
    return create_measurement_port(config_path, mode="hardware")


def connect_instruments_legacy(config_path: str) -> Any:
    """Explicit rollback entry for the pre-refactor controller."""
    from instrument_control import InstrumentControl

    return InstrumentControl(config_path)


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


def _assemble_measurement(
    measurement_type: Any,
    config_path: str,
    callbacks: dict[str, Any],
    *,
    port_factory: Any,
) -> Any:
    """Create a measurement and close only ports owned by this composition call."""
    owned_port = callbacks.get("measurement_port") is None
    if owned_port:
        callbacks["measurement_port"] = port_factory(config_path)
    try:
        return measurement_type(config_path, **callbacks)
    except Exception:
        if owned_port:
            _close_owned_measurement_port(callbacks["measurement_port"])
        raise


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
    repository = ConfigurationRepository()
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
    )


def create_cable_loss_measurement(
    config_path: str, *, prepared_run: PreparedRun, **callbacks: Any
) -> Any:
    callbacks.update(
        run_id=prepared_run.context.run_id,
        run_directory=prepared_run.run_directory,
    )
    return _assemble_measurement(
        EnhancedCableLossMeasurement,
        config_path,
        callbacks,
        port_factory=connect_instruments,
    )


def create_driver_mapping_measurement(
    config_path: str, *, prepared_run: PreparedRun, **callbacks: Any
) -> Any:
    callbacks.update(
        run_id=prepared_run.context.run_id,
        run_directory=prepared_run.run_directory,
    )
    return _assemble_measurement(
        EnhancedDriverPowerMapping,
        config_path,
        callbacks,
        port_factory=connect_instruments,
    )


def create_amplifier_measurement(
    config_path: str, *, prepared_run: PreparedRun, **callbacks: Any
) -> Any:
    callbacks.update(
        run_id=prepared_run.context.run_id,
        run_directory=prepared_run.run_directory,
    )
    return _assemble_measurement(
        EnhancedAmplifierMeasurement,
        config_path,
        callbacks,
        port_factory=connect_instruments,
    )


def create_legacy_cable_loss_measurement(config_path: str, **callbacks: Any) -> Any:
    """Explicit rollback assembly for cable-loss measurement."""
    callbacks["measurement_port"] = None
    return _assemble_measurement(
        EnhancedCableLossMeasurement,
        config_path,
        callbacks,
        port_factory=connect_instruments_legacy,
    )


def create_legacy_driver_mapping_measurement(config_path: str, **callbacks: Any) -> Any:
    """Explicit rollback assembly for driver-power mapping."""
    callbacks["measurement_port"] = None
    return _assemble_measurement(
        EnhancedDriverPowerMapping,
        config_path,
        callbacks,
        port_factory=connect_instruments_legacy,
    )


def create_legacy_amplifier_measurement(config_path: str, **callbacks: Any) -> Any:
    """Explicit rollback assembly for amplifier measurement."""
    callbacks["measurement_port"] = None
    return _assemble_measurement(
        EnhancedAmplifierMeasurement,
        config_path,
        callbacks,
        port_factory=connect_instruments_legacy,
    )
