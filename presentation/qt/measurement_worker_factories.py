"""Qt worker construction kept outside the main window composition layer."""

from __future__ import annotations

from typing import Any

from application.dto.measurement_results import MeasurementType

from .measurement_controller_contract import MeasurementCommand, WorkerFactories
from .measurement_state import MEASUREMENT_TYPES, MeasurementKind
from .mock_replay_worker import MockReplayWorker
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


def build_simulation_worker_factories() -> WorkerFactories:
    """Return the Mock replay worker factories, one per measurement kind.

    模拟回放没有测量端口，也不接收正式运行契约；工厂只把命令里的模拟会话阶段交给
    ``MockReplayWorker``。缺少对应阶段时在 worker 启动前失败。
    """
    return {
        kind: _simulation_factory(measurement_type)
        for kind, measurement_type in MEASUREMENT_TYPES.items()
    }


def _cable_loss_factory(command: MeasurementCommand, *, measurement_port: Any = None) -> Any:
    return CableLossWorker(
        prepared_run=command.prepared_run,
        measurement_port=measurement_port,
    )


def _driver_mapping_factory(command: MeasurementCommand, *, measurement_port: Any = None) -> Any:
    return DriverMappingWorker(
        prepared_run=command.prepared_run,
        cable_loss_path=command.cable_loss_path,
        cable_loss_digest=command.cable_loss_digest,
        measurement_port=measurement_port,
    )


def _amplifier_factory(command: MeasurementCommand, *, measurement_port: Any = None) -> Any:
    return AmplifierWorker(
        prepared_run=command.prepared_run,
        cable_loss_path=command.cable_loss_path,
        driver_mapping_path=command.driver_mapping_path,
        cable_loss_digest=command.cable_loss_digest,
        driver_mapping_digest=command.driver_mapping_digest,
        measurement_port=measurement_port,
    )


def _simulation_factory(measurement_type: MeasurementType):
    def factory(command: MeasurementCommand, *, measurement_port: Any = None) -> Any:
        if measurement_port is not None:
            raise ValueError("模拟回放 worker 不接受测量端口")
        session = command.simulation_session
        if session is None:
            raise ValueError("模拟回放命令缺少模拟会话")
        stage = session.stage_for(measurement_type)
        return MockReplayWorker(stage=stage)

    return factory


__all__ = [
    "build_measurement_worker_factories",
    "build_simulation_worker_factories",
]
