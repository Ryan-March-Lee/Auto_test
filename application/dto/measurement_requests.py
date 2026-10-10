"""Explicit inputs assembled for application measurement use cases.

The request objects are deliberately independent of Qt and file-format details.
The composition root owns configuration loading, run identity, input paths and
resource ownership; a measurement use case only receives this contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Mapping

from app.cancellation import CancellationToken
from app.events import EventSink
from domain.models import RunContext
from domain.configuration.models import RunConfiguration
from application.ports.result_repository import MeasurementResultRepository
from application.ports.result_input_reader import ResultInputReader
from .measurement_results import MeasurementType, freeze


@dataclass(frozen=True)
class MeasurementRequest:
    """Common, fully assembled input for one measurement execution."""

    configuration: RunConfiguration
    context: RunContext
    run_directory: Path
    measurement_port: Any
    measurement_type: MeasurementType | str = "measurement"
    configuration_snapshot: Mapping[str, Any] | None = None
    frequency_range: tuple[float, ...] = ()
    power_range: tuple[float | None, float | None, float | None] = (None, None, None)
    safety_options: Mapping[str, Any] = field(default_factory=dict)
    event_sink: EventSink | None = None
    cancellation_token: CancellationToken | None = None
    result_repository: MeasurementResultRepository | None = None
    input_reader: ResultInputReader | None = None
    config_path: Path | None = None
    _expected_measurement_type: ClassVar[MeasurementType | None] = None

    @property
    def run_id(self) -> str:
        return self.context.run_id

    def __post_init__(self) -> None:
        if not str(self.measurement_type).strip():
            raise ValueError("measurement_type 不能为空")
        if not isinstance(self.measurement_type, MeasurementType):
            try:
                object.__setattr__(self, "measurement_type", MeasurementType(str(self.measurement_type)))
            except ValueError as error:
                raise ValueError(f"不支持的 measurement_type: {self.measurement_type}") from error
        if (
            self._expected_measurement_type is not None
            and self.measurement_type is not self._expected_measurement_type
        ):
            raise ValueError(
                f"{type(self).__name__} 的 measurement_type 必须是 "
                f"{self._expected_measurement_type.value}"
            )
        if not self.frequency_range:
            plan = getattr(self.configuration, "test_plan", None)
            frequencies = getattr(plan, "frequencies", ())
            if not frequencies and isinstance(self.configuration, Mapping):
                frequencies = self.configuration.get("test_frequencies", ())
            object.__setattr__(self, "frequency_range", tuple(frequencies))
        if self.power_range == (None, None, None):
            plan = getattr(self.configuration, "test_plan", None)
            if plan is None and isinstance(self.configuration, Mapping):
                source = self.configuration.get("signal_source", {})
                source = source if isinstance(source, Mapping) else {}
                values = (
                    source.get("start_power"),
                    source.get("stop_power"),
                    source.get("step"),
                )
            else:
                values = (
                    getattr(plan, "start_power", None),
                    getattr(plan, "stop_power", None),
                    getattr(plan, "power_step", None),
                )
            object.__setattr__(
                self,
                "power_range",
                values,
            )
        if self.configuration_snapshot is None:
            snapshot = getattr(self.configuration, "to_dict", None)
            if callable(snapshot):
                snapshot = snapshot()
            elif isinstance(self.configuration, Mapping):
                snapshot = dict(self.configuration)
            else:
                snapshot = {}
            object.__setattr__(self, "configuration_snapshot", snapshot)
        object.__setattr__(self, "configuration_snapshot", freeze(self.configuration_snapshot or {}))
        object.__setattr__(self, "safety_options", freeze(self.safety_options or {
            "owns_measurement_port": False,
            "close_rf_on_cleanup": True,
            "power_cleanup_required": True,
        }))


@dataclass(frozen=True)
class CableLossMeasurementRequest(MeasurementRequest):
    """Inputs for the two-step cable-loss measurement."""

    measurement_type: MeasurementType | str = MeasurementType.CABLE_LOSS
    _expected_measurement_type: ClassVar[MeasurementType] = MeasurementType.CABLE_LOSS


@dataclass(frozen=True)
class DriverPowerMappingRequest(MeasurementRequest):
    """Inputs for driver-power mapping, including its cable-loss input."""

    loss_data_path: Path | None = None
    loss_data: Mapping[str, Any] | None = None
    measurement_type: MeasurementType | str = MeasurementType.DRIVER_POWER_MAPPING
    _expected_measurement_type: ClassVar[MeasurementType] = MeasurementType.DRIVER_POWER_MAPPING


@dataclass(frozen=True)
class AmplifierMeasurementRequest(MeasurementRequest):
    """Inputs for amplifier measurement and its calibration data."""

    loss_data_path: Path | None = None
    loss_data: Mapping[str, Any] | None = None
    driver_mapping_path: Path | None = None
    driver_mapping: Mapping[str, Any] | None = None
    measurement_type: MeasurementType | str = MeasurementType.AMPLIFIER
    _expected_measurement_type: ClassVar[MeasurementType] = MeasurementType.AMPLIFIER
