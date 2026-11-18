"""Qt worker construction kept outside the main window composition layer."""

from __future__ import annotations

from functools import partial
from typing import Any, Callable

from application.dto.measurement_results import MeasurementType

from .measurement_controller_contract import MeasurementCommand, WorkerFactories
from .measurement_state import MeasurementKind
from .workers import AmplifierWorker, CableLossWorker, DriverMappingWorker


def build_measurement_worker_factories(
    *,
    simulated_context_provider: Callable[[], Any] | None = None,
) -> WorkerFactories:
    """Return the production worker factories for the shared controller.

    worker 只接收命令携带的运行契约：配置来源由运行目录冻结快照决定，命令上的
    ``config_path`` 只是页面输入元数据，不再进入 worker 或装配入口。

    ``simulated_context_provider`` 是协调器持有的仿真运行上下文查询（无副作用）。
    返回非空上下文时，仍返回三个现有 worker，但会：

    - 在 worker 创建前调用 ``port.activate(measurement_type)`` 绑定阶段；失败在
      worker 启动前抛出，由控制器发出 ``stage_start_failed``，协调器关闭端口；
    - 用本次装配的仿真 ``PathService`` 注入测量工厂（不读写模块级常量）；
    - 传入按 ``time_scale`` 缩放的 ``sleep_fn``，只缩短休眠，不改变事件顺序。
    """
    def resolve_context() -> Any:
        if simulated_context_provider is None:
            return None
        return simulated_context_provider()

    def cable_loss_factory(
        command: MeasurementCommand, *, measurement_port: Any = None
    ) -> Any:
        context = resolve_context()
        if context is None:
            return _cable_loss_factory(command, measurement_port=measurement_port)
        return _simulated_worker(
            context,
            MeasurementType.CABLE_LOSS,
            lambda: CableLossWorker(
                prepared_run=command.prepared_run,
                sleep_fn=_sleep_fn(context),
                measurement_port=measurement_port,
                measurement_factory=_measurement_factory(
                    "create_cable_loss_measurement", context
                ),
            ),
        )

    def driver_mapping_factory(
        command: MeasurementCommand, *, measurement_port: Any = None
    ) -> Any:
        context = resolve_context()
        if context is None:
            return _driver_mapping_factory(command, measurement_port=measurement_port)
        return _simulated_worker(
            context,
            MeasurementType.DRIVER_POWER_MAPPING,
            lambda: DriverMappingWorker(
                prepared_run=command.prepared_run,
                cable_loss_path=command.cable_loss_path,
                cable_loss_digest=command.cable_loss_digest,
                sleep_fn=_sleep_fn(context),
                measurement_port=measurement_port,
                measurement_factory=_measurement_factory(
                    "create_driver_mapping_measurement", context
                ),
            ),
        )

    def amplifier_factory(
        command: MeasurementCommand, *, measurement_port: Any = None
    ) -> Any:
        context = resolve_context()
        if context is None:
            return _amplifier_factory(command, measurement_port=measurement_port)
        return _simulated_worker(
            context,
            MeasurementType.AMPLIFIER,
            lambda: AmplifierWorker(
                prepared_run=command.prepared_run,
                cable_loss_path=command.cable_loss_path,
                driver_mapping_path=command.driver_mapping_path,
                cable_loss_digest=command.cable_loss_digest,
                driver_mapping_digest=command.driver_mapping_digest,
                sleep_fn=_sleep_fn(context),
                measurement_port=measurement_port,
                measurement_factory=_measurement_factory(
                    "create_amplifier_measurement", context
                ),
            ),
        )

    return {
        MeasurementKind.CABLE_LOSS: cable_loss_factory,
        MeasurementKind.DRIVER_MAPPING: driver_mapping_factory,
        MeasurementKind.AMPLIFIER: amplifier_factory,
    }


def _simulated_worker(context: Any, measurement_type: MeasurementType, build: Callable[[], Any]) -> Any:
    """绑定阶段后再构造正式 worker；激活失败在 worker 启动前抛出。"""
    context.port.activate(measurement_type)
    return build()


def _sleep_fn(context: Any) -> Callable[[float], None]:
    """按上下文时间倍率构造快放 ``sleep_fn``（在调用时才导入 app 装配层）。"""
    from app.simulation_runtime import simulation_sleep_fn

    return simulation_sleep_fn(context.time_scale)


def _measurement_factory(name: str, context: Any) -> Callable[..., Any]:
    """把本次仿真路径注入正式测量工厂，同时保留 worker 的延迟导入习惯。"""
    return partial(_create_measurement, name=name, paths=context.paths)


def _create_measurement(*, name: str, paths: Any, **callbacks: Any) -> Any:
    from app import gui_runtime

    return getattr(gui_runtime, name)(paths=paths, **callbacks)


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


__all__ = [
    "build_measurement_worker_factories",
]
