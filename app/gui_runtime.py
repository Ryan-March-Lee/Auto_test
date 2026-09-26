"""Application-level operations used by the Qt presentation adapters."""

from __future__ import annotations

from typing import Any

from enhanced_workers import (
    EnhancedAmplifierMeasurement,
    EnhancedCableLossMeasurement,
    EnhancedDriverPowerMapping,
)
from persistence.config_repository import ConfigurationRepository
from result_storage import new_run_id
from .run_context import PreparedRun, environment_version, prepare_run


def connect_instruments(config_path: str) -> Any:
    """Create the configured instrument session for the application layer."""
    from instrument_control import InstrumentControl

    return InstrumentControl(config_path)


def prepare_configuration(config_path: str, *, run_id: str | None = None) -> PreparedRun:
    """Perform the GUI preflight and snapshot before an instrument is created."""
    loaded = ConfigurationRepository().load_for_run(config_path)
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
