"""Application-level operations used by the Qt presentation adapters."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from application.configuration_service import load_for_run
from application.ports.config_repository import ConfigurationRepository
from domain.configuration.models import (
    validate_cable_loss_configuration,
    validate_driver_mapping_configuration,
)
from domain.configuration.rules import ConfigValidationResult
from infrastructure.logging.app_logging import get_logger
from infrastructure.persistence.json_result_repository import new_run_id
from .run_context import (
    PreparedRun,
    create_prepared_run,
    environment_version,
    prepare_run,
)
from instrument.measurement_factory import create_measurement_port
from infrastructure.persistence.result_repository import FileMeasurementResultRepository
from application.inputs import ResultInputReader
from application.run_stages import RunStageRecorder
from domain.run.models import STAGE_CABLE_LOSS, STAGE_DRIVER_MAPPING
from application.measurements import (
    AmplifierMeasurementUseCase,
    CableLossUseCase,
    DriverPowerMappingUseCase,
    validate_amplifier_calibration_inputs,
)
from application.run_service import configuration_snapshot
from app.events import MessageEvent, ProgressEvent, RealtimeDataEvent
from application.dto import (
    AmplifierMeasurementRequest,
    CableLossMeasurementRequest,
    DriverPowerMappingRequest,
)
from infrastructure.filesystem.paths import DEFAULT_PATHS, PathService


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
    config_path: str,
    *,
    run_id: str | None = None,
    operation: Operation = "full",
    repository: ConfigurationRepository | None = None,
    paths: PathService | None = None,
    run_contract: bool = False,
    run_repository: Any = None,
) -> PreparedRun:
    """Perform the GUI preflight and snapshot before an instrument is created.

    ``run_contract=True`` 是本重构引入的“创建运行”入口：配置必须通过**完整运行
    校验**，随后冻结为 ``runs/<run_id>/`` 运行目录快照（``config.json`` +
    ``manifest.json``），返回携带运行仓储的 :class:`PreparedRun`。默认
    ``run_contract=False`` 时保持旧兼容快照行为，GUI 与步骤 5-7 迁移前不会受影响。
    """
    if operation not in ("full", "cable_loss", "driver_mapping"):
        raise ValueError(f"不支持的测量类型: {operation}")
    if repository is None:
        # Compatibility composition for historical callers. New application
        # callers should inject the port implementation at the composition root.
        from infrastructure.config.json_config_repository import JsonConfigurationRepository

        repository = JsonConfigurationRepository()
    loaded = load_for_run(repository, config_path)
    if run_contract:
        # 运行契约要求完整运行配置；阶段专用校验通过但完整校验失败的配置不会
        # 产生运行目录，因此这里不套用阶段专用校验。
        return create_prepared_run(
            loaded,
            run_id=run_id,
            software_version=environment_version(),
            config_path=Path(config_path),
            paths=paths,
            run_repository=run_repository,
        )
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
        paths=paths,
    )


def _require_stage_inputs(prepared_run: PreparedRun, *stages: str) -> None:
    """在连接仪器前校验运行契约运行的前置阶段输入已就绪。

    只有携带运行仓储的运行契约运行才需要校验；旧兼容快照没有阶段契约，保持原行为。
    校验失败会抛出 :class:`StageInputUnavailable`，从而在打开仪器或执行任何硬件
    动作前终止，避免为无效输入付出连接代价或产生硬件副作用。
    """
    run_repository = getattr(prepared_run, "run_repository", None)
    if run_repository is None:
        return
    recorder = RunStageRecorder(run_repository, prepared_run.context.run_id)
    for stage in stages:
        recorder.verified_result_path(stage)


def _driver_mode_enabled(prepared_run: PreparedRun) -> bool:
    """判断当前运行配置是否启用驱动模式，用于决定是否需要驱动映射输入。

    兼容旧版组合配置映射和现代拆分配置模型；无法识别时按未启用处理，避免对
    旧兼容快照或多注入的替身对象错误地强制要求映射文件。
    """
    configuration = getattr(prepared_run, "configuration", None)
    plan = getattr(configuration, "test_plan", None)
    if plan is not None:
        return bool(getattr(plan, "driver_enabled", False))
    if isinstance(configuration, Mapping):
        mode = configuration.get("driver_mode", {})
        return bool(mode.get("enabled")) if isinstance(mode, Mapping) else False
    return False


def _test_frequencies(prepared_run: PreparedRun) -> tuple[Any, ...]:
    """从已冻结配置中读取测试频率，供运行前输入结构校验使用。"""
    configuration = getattr(prepared_run, "configuration", None)
    plan = getattr(configuration, "test_plan", None)
    if plan is not None:
        return tuple(getattr(plan, "frequencies", ()) or ())
    if isinstance(configuration, Mapping):
        return tuple(configuration.get("test_frequencies", ()) or ())
    return ()


def _preflight_amplifier_inputs(
    prepared_run: PreparedRun, repository: Any, input_reader: Any
) -> None:
    """在连接仪器前读取并校验主功放的当前运行校准输入。

    运行契约运行先从当前运行目录读取已校验的线损与（按需）驱动映射，再做结构
    校验，因此结构无效的输入会在打开仪器或执行任何 RF 动作前失败。旧兼容快照
    没有阶段契约，保持原行为。
    """
    run_repository = getattr(prepared_run, "run_repository", None)
    if run_repository is None:
        return
    recorder = RunStageRecorder(run_repository, prepared_run.context.run_id)
    loss_data = input_reader.read_cable_loss(recorder.verified_result_path(STAGE_CABLE_LOSS))
    driver_enabled = _driver_mode_enabled(prepared_run)
    driver_mapping = None
    if driver_enabled:
        driver_mapping = input_reader.read_driver_mapping(
            recorder.verified_result_path(STAGE_DRIVER_MAPPING)
        )
    validate_amplifier_calibration_inputs(
        loss_data,
        driver_mapping,
        frequencies=_test_frequencies(prepared_run),
        driver_enabled=driver_enabled,
    )


def _ensure_frozen_config_matches(
    prepared_run: PreparedRun, config_path: str, *, repository: Any = None
) -> None:
    """确认 ``config_path`` 仍与运行冻结配置一致，拒绝用可变配置连接仪器。

    运行目录中的 ``config.json`` 是本次运行的唯一配置事实来源。硬件端口当前仍按
    ``config_path`` 打开仪器，因此运行契约运行必须确认该文件自创建运行后没有被
    修改：一旦检测到配置漂移，就在连接仪器前失败，避免仪器地址或供电通道与冻结
    配置不一致。把硬件端口完全改为从运行快照装配属于后续 GUI 两阶段收敛步骤。
    """
    run_repository = getattr(prepared_run, "run_repository", None)
    # 只有实现了运行目录端口（能读取冻结配置）的运行才需要做配置一致性校验；
    # 旧兼容快照和最小替身对象没有该能力，按无冻结配置处理。
    if run_repository is None or not hasattr(run_repository, "read_config"):
        return
    try:
        frozen = run_repository.read_config(prepared_run.context.run_id)
    except Exception as error:  # noqa: BLE001 - 统一转成前置校验失败
        raise ValueError(f"无法读取运行配置快照，拒绝连接仪器: {error}") from error
    if repository is None:
        from infrastructure.config.json_config_repository import JsonConfigurationRepository

        repository = JsonConfigurationRepository()
    loaded = load_for_run(repository, config_path)
    if not loaded.valid:
        raise ValueError("配置文件当前无效，不能用于连接仪器；请重新创建运行")
    current = configuration_snapshot(
        loaded.configuration, run_id=prepared_run.context.run_id
    )
    if (
        current["test_plan"] != frozen.get("test_plan")
        or current["run_mapping"] != frozen.get("run_mapping")
    ):
        raise ValueError(
            "配置文件在创建运行后已被修改，拒绝使用可变配置连接仪器；请重新创建运行"
        )


def _request_kwargs(prepared_run: PreparedRun, config_path: str, callbacks: dict[str, Any]) -> dict[str, Any]:
    """Build the shared request inputs at the application composition boundary."""
    port = callbacks.get("measurement_port")
    if port is None:
        raise ValueError("应用测量请求必须显式包含 measurement_port")
    paths = callbacks.get("paths") or DEFAULT_PATHS
    repository = callbacks.get("result_repository") or FileMeasurementResultRepository(paths=paths)
    run_repository = getattr(prepared_run, "run_repository", None)
    stage_recorder = (
        RunStageRecorder(run_repository, prepared_run.context.run_id)
        if run_repository is not None
        else None
    )
    return {
        "configuration": prepared_run.configuration,
        "context": prepared_run.context,
        "run_directory": Path(prepared_run.run_directory),
        "measurement_port": port,
        "event_sink": callbacks.get("event_sink"),
        "cancellation_token": callbacks.get("cancellation_token"),
        "result_repository": repository,
        "input_reader": callbacks.get("input_reader") or ResultInputReader(repository),
        "stage_recorder": stage_recorder,
        "config_path": Path(config_path),
        "safety_options": {
            "owns_measurement_port": bool(callbacks.get("owns_measurement_port", False)),
            "close_rf_on_cleanup": True,
            "power_cleanup_required": True,
        },
    }


def create_cable_loss_measurement(
    config_path: str, *, prepared_run: PreparedRun, **callbacks: Any
) -> Any:
    callbacks.update(
        run_id=prepared_run.context.run_id,
        run_directory=prepared_run.run_directory,
        paths=callbacks.get("paths"),
    )
    owned_port = callbacks.get("measurement_port") is None
    try:
        # 运行契约运行在连接仪器前确认配置文件未被创建运行后修改。
        _ensure_frozen_config_matches(prepared_run, config_path)
        if owned_port:
            callbacks["measurement_port"] = connect_instruments(config_path)
        callbacks["owns_measurement_port"] = owned_port
        callbacks.setdefault("result_repository", FileMeasurementResultRepository(paths=callbacks.get("paths") or DEFAULT_PATHS))
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
        paths=callbacks.get("paths"),
    )
    owned_port = callbacks.get("measurement_port") is None
    try:
        # 运行契约运行在连接仪器前校验当前运行的线损输入，缺失时不做任何硬件动作。
        _require_stage_inputs(prepared_run, STAGE_CABLE_LOSS)
        _ensure_frozen_config_matches(prepared_run, config_path)
        if owned_port:
            callbacks["measurement_port"] = connect_instruments(config_path)
        callbacks["owns_measurement_port"] = owned_port
        callbacks.setdefault("result_repository", FileMeasurementResultRepository(paths=callbacks.get("paths") or DEFAULT_PATHS))
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
        paths=callbacks.get("paths"),
    )
    owned_port = callbacks.get("measurement_port") is None
    try:
        paths = callbacks.get("paths") or DEFAULT_PATHS
        repository = callbacks.get("result_repository") or FileMeasurementResultRepository(paths=paths)
        input_reader = callbacks.get("input_reader") or ResultInputReader(repository)
        # 运行契约运行在连接仪器前读取并校验当前运行的校准输入：主功放始终需要
        # 线损，仅在驱动模式启用时才需要驱动映射；结构无效或配置漂移都在打开仪器
        # 或执行任何 RF 动作前失败。
        _preflight_amplifier_inputs(prepared_run, repository, input_reader)
        _ensure_frozen_config_matches(prepared_run, config_path)
        if owned_port:
            callbacks["measurement_port"] = connect_instruments(config_path)
        callbacks["owns_measurement_port"] = owned_port
        callbacks["result_repository"] = repository
        callbacks["input_reader"] = input_reader
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
