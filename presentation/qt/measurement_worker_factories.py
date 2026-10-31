"""Qt worker construction kept outside the main window composition layer."""

from __future__ import annotations

from typing import Any

from .measurement_controller_contract import MeasurementCommand, WorkerFactories
from .measurement_state import MeasurementKind
from .workers import AmplifierWorker, CableLossWorker, DriverMappingWorker


def build_measurement_worker_factories() -> WorkerFactories:
    """Return the production worker factories for the shared controller.

    worker 只接收命令携带的运行契约：配置来源由运行目录冻结快照决定，命令上的
    ``config_path`` 只是页面输入元数据，不再进入 worker 或装配入口。
    """
    return {
        MeasurementKind.CABLE_LOSS: _cable_loss_factory,
        MeasurementKind.DRIVER_MAPPING: _driver_mapping_factory,
        MeasurementKind.AMPLIFIER: _amplifier_factory,
    }


def _cable_loss_factory(command: MeasurementCommand, *, measurement_port: Any = None) -> Any:
    return CableLossWorker(
        prepared_run=command.prepared_run,
        measurement_port=measurement_port,
    )


def _driver_mapping_factory(command: MeasurementCommand, *, measurement_port: Any = None) -> Any:
    return DriverMappingWorker(
        prepared_run=command.prepared_run,
        measurement_port=measurement_port,
    )


def _amplifier_factory(command: MeasurementCommand, *, measurement_port: Any = None) -> Any:
    return AmplifierWorker(
        prepared_run=command.prepared_run,
        measurement_port=measurement_port,
    )


__all__ = ["build_measurement_worker_factories"]
