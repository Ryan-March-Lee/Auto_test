"""Application-level operations used by the Qt presentation adapters."""

from __future__ import annotations

from typing import Any, Literal

from enhanced_workers import (
    EnhancedAmplifierMeasurement,
    EnhancedCableLossMeasurement,
    EnhancedDriverPowerMapping,
)
from persistence.config_repository import ConfigurationRepository
from config_models import validate_cable_loss_configuration
from config_validation import ConfigValidationResult
from result_storage import new_run_id
from .run_context import PreparedRun, environment_version, prepare_run


Operation = Literal["full", "cable_loss"]


def connect_instruments(config_path: str) -> Any:
    """Create the configured instrument session for the application layer."""
    from instrument_control import InstrumentControl

    return InstrumentControl(config_path)


def prepare_configuration(
    config_path: str, *, run_id: str | None = None, operation: Operation = "full"
) -> PreparedRun:
    """Perform the GUI preflight and snapshot before an instrument is created."""
    if operation not in ("full", "cable_loss"):
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
    return EnhancedCableLossMeasurement(config_path, **callbacks)


def create_driver_mapping_measurement(
    config_path: str, *, prepared_run: PreparedRun, **callbacks: Any
) -> Any:
    callbacks.update(
        run_id=prepared_run.context.run_id,
        run_directory=prepared_run.run_directory,
    )
    return EnhancedDriverPowerMapping(config_path, **callbacks)


def create_amplifier_measurement(
    config_path: str, *, prepared_run: PreparedRun, **callbacks: Any
) -> Any:
    callbacks.update(
        run_id=prepared_run.context.run_id,
        run_directory=prepared_run.run_directory,
    )
    return EnhancedAmplifierMeasurement(config_path, **callbacks)
