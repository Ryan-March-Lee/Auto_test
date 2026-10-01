"""Explicit inputs assembled for application measurement use cases.

The request objects are deliberately independent of Qt and file-format details.
The composition root owns configuration loading, run identity, input paths and
resource ownership; a measurement use case only receives this contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.cancellation import CancellationToken
from app.events import EventSink
from domain.models import RunContext
from config_models import RunConfiguration
from application.ports.result_repository import MeasurementResultRepository


@dataclass(frozen=True)
class MeasurementRequest:
    """Common, fully assembled input for one measurement execution."""

    configuration: RunConfiguration
    context: RunContext
    run_directory: Path
    measurement_port: Any
    event_sink: EventSink | None = None
    cancellation_token: CancellationToken | None = None
    result_repository: MeasurementResultRepository | None = None
    config_path: Path | None = None

    @property
    def run_id(self) -> str:
        return self.context.run_id


@dataclass(frozen=True)
class CableLossMeasurementRequest(MeasurementRequest):
    """Inputs for the two-step cable-loss measurement."""


@dataclass(frozen=True)
class DriverPowerMappingRequest(MeasurementRequest):
    """Inputs for driver-power mapping, including its cable-loss input."""

    loss_data_path: Path | None = None
    loss_data: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class AmplifierMeasurementRequest(MeasurementRequest):
    """Inputs for amplifier measurement and its calibration data."""

    loss_data_path: Path | None = None
    loss_data: Mapping[str, Any] | None = None
    driver_mapping_path: Path | None = None
    driver_mapping: Mapping[str, Any] | None = None
