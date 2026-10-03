"""Qt worker construction kept outside the main window composition layer."""

from __future__ import annotations

from typing import Any

from .measurement_controller_contract import MeasurementCommand, WorkerFactories
from .measurement_state import MeasurementKind
from .workers import AmplifierWorker, CableLossWorker, DriverMappingWorker


def build_measurement_worker_factories() -> WorkerFactories:
    """Return the production worker factories for the shared controller."""
    return {
        MeasurementKind.CABLE_LOSS: _cable_loss_factory,
        MeasurementKind.DRIVER_MAPPING: _driver_mapping_factory,
        MeasurementKind.AMPLIFIER: _amplifier_factory,
    }


def _cable_loss_factory(command: MeasurementCommand, *, measurement_port: Any = None) -> Any:
    return CableLossWorker(command.config_path, measurement_port=measurement_port)


def _driver_mapping_factory(command: MeasurementCommand, *, measurement_port: Any = None) -> Any:
    return DriverMappingWorker(command.config_path, measurement_port=measurement_port)


def _amplifier_factory(command: MeasurementCommand, *, measurement_port: Any = None) -> Any:
    return AmplifierWorker(command.config_path, measurement_port=measurement_port)


__all__ = ["build_measurement_worker_factories"]
