"""Application-level operations used by the Qt presentation adapters."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from application.configuration_service import load_for_run
from application.archival_service import ResultArchivalService
from application.errors import ConfigurationError
from application.ports.config_repository import ConfigurationRepository
from domain.configuration.rules import simulated_binding_issues
from infrastructure.logging.app_logging import get_logger
from .run_context import (
    PreparedRun,
    create_prepared_run,
    environment_version,
    load_prepared_run,
)
from instrument.measurement_factory import create_measurement_port
from instrument.flow.cleanup import SafetyCleanupAdapter
from infrastructure.persistence.result_repository import FileMeasurementResultRepository
from infrastructure.persistence.artifact_generator import ArchiveArtifactGenerator
from application.inputs import ResultInputReader
from application.run_stages import RunStageRecorder
from domain.run.models import STAGE_CABLE_LOSS, STAGE_DRIVER_MAPPING
from application.measurements import (
    AmplifierMeasurementUseCase,
    CableLossUseCase,
    DriverPowerMappingUseCase,
    validate_amplifier_calibration_inputs,
)
from application.run_service import (
    RunNotResumable,
    ensure_run_repository_supports_run_contract,
    has_run_contract,
    load_hardware_config,
    load_run_configuration,
)
from app.events import MessageEvent, ProgressEvent, RealtimeDataEvent
from application.dto import (
    AmplifierMeasurementInputs,
    AmplifierMeasurementRequest,
    CableLossMeasurementRequest,
    DriverPowerMappingRequest,
)
from infrastructure.filesystem.paths import DEFAULT_PATHS, PathService


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


def connect_instruments(prepared_run: PreparedRun, *, recorder: Any = None) -> Any:
    """Create the configured hardware port from the run's frozen hardware snapshot.

    ``prepared_run`` 是**唯一**的硬件装配输入：硬件配置路径在这里经
    :func:`_hardware_config_path` → :meth:`PreparedRun.hardware_connection_path` 解析，
    并在打开任何 VISA 资源前按 manifest 校验 SHA-256/大小，因此“连的是本次运行的
    仪器”可以被证明。

    路径**不接受**任何形式的覆盖：函数没有配置路径参数，调用方无法把任意路径塞进
    硬件装配（D12/D17）。测量工厂同样不解析、也不转发路径，它们只把运行对象交给这个
    入口，因此一次装配里 ``hardware_connection_path()`` 恰好被调用一次，不存在为同一份
    快照做两遍 manifest 校验的写法。

    没有运行契约的运行对象在这里明确失败（:class:`RunNotResumable`）：连接仪器只
    接受运行目录内冻结的硬件配置，不存在“用调用方配置路径装配”的旁路。
    """
    return create_measurement_port(
        _hardware_config_path(prepared_run), mode="hardware", recorder=recorder
    )


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
    repository: ConfigurationRepository | None = None,
    paths: PathService | None = None,
    run_repository: Any = None,
    hardware_config: Mapping[str, Any] | None = None,
) -> PreparedRun:
    """Create the ``runs/<run_id>/`` run contract before an instrument is created.

    这是唯一的“创建运行”入口：配置必须通过**完整运行校验**，随后冻结为
    ``runs/<run_id>/`` 运行目录快照（``config.json`` + ``manifest.json`` + 硬件配置
    ``inputs/hardware_config.json``），返回携带运行仓储的 :class:`PreparedRun`。
    需要重新打开既有运行时使用 :func:`load_prepared_run`。

    旧的 ``run_contract=False`` 兼容快照分支（只做阶段预检、不创建运行目录）已在重构
    步骤 A2 删除：没有运行目录就没有 ``PreparedRun``，也就没有可追溯的结果边界，因此
    这里不再提供“只预检不建运行”的路径。

    ``hardware_config`` 省略时会从 ``config_path`` 读取硬件装配所需的原始配置并
    一并冻结，使运行目录自包含，硬件连接不再依赖全局可变配置。
    """
    if repository is None:
        # Compatibility composition for historical callers. New application
        # callers should inject the port implementation at the composition root.
        from infrastructure.config.json_config_repository import JsonConfigurationRepository

        repository = JsonConfigurationRepository()
    loaded = load_for_run(repository, config_path)
    if hardware_config is None:
        hardware_config = _load_hardware_config(config_path)
    # 模拟扫描的设备绑定可以保存用于演示，但绝不能进入真实运行：在创建任何运行目录
    # 之前拒绝，避免冻结一份“看起来可运行、其实对着演示地址”的运行快照。
    _reject_simulated_bindings(hardware_config)
    return create_prepared_run(
        loaded,
        run_id=run_id,
        software_version=environment_version(),
        config_path=Path(config_path),
        paths=paths,
        run_repository=run_repository,
        hardware_config=hardware_config,
    )


def _load_hardware_config(config_path: str) -> dict[str, Any]:
    """读取硬件装配所需的原始配置，用于冻结进运行目录。"""
    from infrastructure.config.json_io import load_config_file

    config = load_config_file(config_path)
    if not isinstance(config, dict):
        raise ValueError("硬件配置根节点必须是对象")
    return config


def _reject_simulated_bindings(hardware_config: Mapping[str, Any]) -> None:
    """模拟扫描留下的设备绑定不能用于真实运行。"""
    issues = simulated_binding_issues(hardware_config)
    if not issues:
        return
    details = "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
    raise ConfigurationError(f"配置中的设备绑定来自模拟扫描，不能创建真实运行: {details}")


def _hardware_config_path(prepared_run: PreparedRun) -> str:
    """返回硬件连接与测量应使用的配置路径（运行契约是唯一来源）。

    统一走 :meth:`PreparedRun.hardware_connection_path` 的校验入口：路径缺失、快照被
    删除或 manifest/SHA-256 不一致时抛出 :class:`RunNotResumable`；不带运行契约的对象
    同样明确失败，绝不回退到调用方传入的全局可变配置。
    """
    return str(require_run_contract(prepared_run).hardware_connection_path())


def require_run_contract(prepared_run: PreparedRun) -> PreparedRun:
    """确认传入对象满足运行契约并返回它；不满足时明确失败。

    “运行契约缺失视为调用错误”：这里不做降级、不猜测输入，也不允许后续步骤在没有
    运行目录的情况下连接硬件。判据只复用唯一实现
    :func:`application.run_service.has_run_contract`，不新写一套检查。
    """
    if not has_run_contract(prepared_run):
        raise RunNotResumable(
            "运行契约缺失：硬件装配与测量必须绑定 runs/<run_id>/ 运行目录"
            "（运行标识、运行目录、运行仓储与冻结快照解析入口）；"
            "请先创建或重新加载运行"
        )
    return prepared_run


def _stage_recorder(prepared_run: PreparedRun) -> RunStageRecorder:
    """当前运行的阶段记录器；运行契约已校验，运行仓储是唯一写入目标。"""
    return RunStageRecorder(prepared_run.run_repository, prepared_run.context.run_id)


def _require_stage_inputs(prepared_run: PreparedRun, *stages: str) -> None:
    """在连接仪器前校验运行的前置阶段输入已就绪。

    校验失败会抛出 :class:`StageInputUnavailable`，从而在打开仪器或执行任何硬件
    动作前终止，避免为无效输入付出连接代价或产生硬件副作用。
    """
    recorder = _stage_recorder(prepared_run)
    for stage in stages:
        recorder.verified_result_path(stage)


def _driver_mode_enabled(prepared_run: PreparedRun) -> bool:
    """判断当前运行配置是否启用驱动模式，用于决定是否需要驱动映射输入。"""
    plan = getattr(prepared_run.configuration, "test_plan", None)
    return bool(getattr(plan, "driver_enabled", False))


def _test_frequencies(prepared_run: PreparedRun) -> tuple[Any, ...]:
    """从已冻结运行配置中读取测试频率，供运行前输入结构校验使用。"""
    plan = getattr(prepared_run.configuration, "test_plan", None)
    return tuple(getattr(plan, "frequencies", ()) or ())


def _preflight_amplifier_inputs(prepared_run: PreparedRun, input_reader: Any) -> None:
    """在连接仪器前读取并校验主功放的当前运行校准输入。

    只从当前运行目录读取已校验的线损与（按需）驱动映射，再做结构校验，因此结构
    无效的输入会在打开仪器或执行任何 RF 动作前失败。
    """
    recorder = _stage_recorder(prepared_run)
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


def _ensure_domain_snapshot_is_intact(run_repository: Any, run_id: str) -> None:
    """按**领域运行配置**规则校验运行目录内的 ``config.json``。

    这是领域配置快照的唯一校验入口：``read_config`` 先按 manifest 登记的 SHA-256
    和大小确认文件未被替换，:func:`load_run_configuration` 再把内容重建为领域运行
    配置并执行完整运行校验。快照被篡改、``schema_version`` 不受支持或配置语义无效
    时，都在连接仪器前失败。
    """
    try:
        loaded = load_run_configuration(run_repository, run_id)
    except RunNotResumable:
        raise
    except Exception as error:  # noqa: BLE001 - 统一转成前置校验失败
        raise RunNotResumable(
            f"无法读取运行 {run_id} 的配置快照，拒绝连接仪器: {error}"
        ) from error
    if not loaded.valid:
        details = "; ".join(
            f"{issue.path}: {issue.message}" for issue in loaded.validation.errors
        )
        raise RunNotResumable(
            f"运行 {run_id} 的配置快照校验失败，拒绝连接仪器；"
            f"请重新创建运行: {details}"
        )


def _ensure_hardware_snapshot_is_intact(run_repository: Any, run_id: str) -> None:
    """按**硬件装配快照**规则校验运行目录内的 ``inputs/hardware_config.json``。

    统一委托 :func:`application.run_service.load_hardware_config`：manifest 登记的
    SHA-256 和大小一致性校验与 JSON 对象校验都在那里完成。硬件配置格式与领域运行
    配置格式彼此独立，**不**互相套用 schema；两份快照的从属关系由它们同处一个带
    ``run_id`` 的 manifest 保证，因此硬件快照可以合法地拥有与 ``config.json``
    完全不同的字段集合并独立演化。
    """
    load_hardware_config(run_repository, run_id)


def _ensure_run_snapshots_are_intact(prepared_run: PreparedRun) -> None:
    """连接仪器前，按各自职责分别校验运行契约的两份冻结快照。

    - ``config.json`` 走领域运行配置校验（领域 schema + 完整运行校验）；
    - ``inputs/hardware_config.json`` 只做 JSON 对象校验和 manifest SHA-256/大小
      校验。

    两种快照拥有独立、清晰的校验边界，任何一方被替换或损坏都在连接仪器前失败。
    调用方已按运行契约校验过运行对象，因此运行仓储必然存在：仓储必须提供完整的
    校验能力，缺少任一能力都明确失败，绝不静默跳过后继续连接仪器。
    """
    run_repository = prepared_run.run_repository
    ensure_run_repository_supports_run_contract(run_repository)
    run_id = prepared_run.context.run_id
    _ensure_domain_snapshot_is_intact(run_repository, run_id)
    _ensure_hardware_snapshot_is_intact(run_repository, run_id)


def _input_reader_for(repository: Any) -> Any:
    """构建只接受显式路径的测量输入读取器。

    无路径兼容读取已在重构步骤 B3 删除：所有运行都必须显式提供路径，输入读取
    不再回退到按时间扫描的“最新结果”。
    """
    return ResultInputReader(repository)


def _request_kwargs(prepared_run: PreparedRun, callbacks: dict[str, Any]) -> dict[str, Any]:
    """Build the shared request inputs at the application composition boundary.

    阶段记录器与自动归档是运行契约的组成部分：运行契约已校验，因此这里**无条件**
    装配，不再有“没有运行就没有阶段记录/交付产物”的分支。
    """
    port = callbacks.get("measurement_port")
    if port is None:
        raise ValueError("应用测量请求必须显式包含 measurement_port")
    paths = callbacks.get("paths") or DEFAULT_PATHS
    repository = callbacks.get("result_repository") or FileMeasurementResultRepository(paths=paths)
    run_repository = prepared_run.run_repository
    # CSV 与测试报告都落到当前运行的 artifacts/ 下。
    archival_service = ResultArchivalService(
        runs=run_repository,
        generator=ArchiveArtifactGenerator(run_repository),
    )
    return {
        "configuration": prepared_run.configuration,
        "context": prepared_run.context,
        "run_directory": Path(prepared_run.run_directory),
        "measurement_port": port,
        # 安全清理通过 application 端口注入：application 不导入具体设备 flow。
        "safety_cleanup": callbacks.get("safety_cleanup") or SafetyCleanupAdapter(port),
        "event_sink": callbacks.get("event_sink"),
        "cancellation_token": callbacks.get("cancellation_token"),
        "result_repository": repository,
        # 所有运行只接受显式输入路径；无路径兼容读取已在步骤 B3 删除。
        "input_reader": callbacks.get("input_reader") or _input_reader_for(repository),
        "stage_recorder": _stage_recorder(prepared_run),
        "archival_service": archival_service,
        "safety_options": {
            "owns_measurement_port": bool(callbacks.get("owns_measurement_port", False)),
            "close_rf_on_cleanup": True,
            "power_cleanup_required": True,
        },
    }


def create_cable_loss_measurement(*, prepared_run: PreparedRun, **callbacks: Any) -> Any:
    """装配线损测量用例：运行契约是唯一权威。

    硬件配置一律来自 ``prepared_run`` 的冻结快照，没有第二个配置来源：本函数不解析、
    也不接收硬件配置路径，端口由 :func:`connect_instruments` 按运行快照装配（D12）。
    """
    prepared_run = require_run_contract(prepared_run)
    callbacks.update(
        run_id=prepared_run.context.run_id,
        run_directory=prepared_run.run_directory,
        paths=callbacks.get("paths"),
    )
    owned_port = callbacks.get("measurement_port") is None
    try:
        # 运行固定使用运行目录内冻结的硬件配置；连接仪器前分别按领域和硬件两套
        # 职责校验冻结快照，避免用被篡改或损坏的快照打开仪器。
        _ensure_run_snapshots_are_intact(prepared_run)
        if owned_port:
            callbacks["measurement_port"] = connect_instruments(prepared_run)
        callbacks["owns_measurement_port"] = owned_port
        callbacks.setdefault("result_repository", FileMeasurementResultRepository(paths=callbacks.get("paths") or DEFAULT_PATHS))
        callbacks.setdefault("event_sink", _CallbackEventSink(callbacks))
        request = CableLossMeasurementRequest(**_request_kwargs(prepared_run, callbacks))
        return CableLossUseCase(request, sleep_fn=callbacks.get("sleep_fn"))
    except Exception:
        if owned_port and callbacks.get("measurement_port") is not None:
            _close_owned_measurement_port(callbacks["measurement_port"])
        raise


def create_driver_mapping_measurement(*, prepared_run: PreparedRun, **callbacks: Any) -> Any:
    """装配驱动映射测量用例：运行契约是唯一权威。

    与其它测量工厂一样，本函数不解析、也不接收硬件配置路径，端口由
    :func:`connect_instruments` 按运行快照装配（D12）。
    """
    prepared_run = require_run_contract(prepared_run)
    callbacks.update(
        run_id=prepared_run.context.run_id,
        run_directory=prepared_run.run_directory,
        paths=callbacks.get("paths"),
    )
    owned_port = callbacks.get("measurement_port") is None
    try:
        # 连接仪器前校验本次运行实际需要的线损输入：只从当前运行目录读取，缺失时不做
        # 任何仪器连接或硬件动作；随后按领域/硬件两套职责分别校验冻结快照。
        _require_stage_inputs(prepared_run, STAGE_CABLE_LOSS)
        _ensure_run_snapshots_are_intact(prepared_run)
        if owned_port:
            callbacks["measurement_port"] = connect_instruments(prepared_run)
        callbacks["owns_measurement_port"] = owned_port
        callbacks.setdefault("result_repository", FileMeasurementResultRepository(paths=callbacks.get("paths") or DEFAULT_PATHS))
        callbacks.setdefault("event_sink", _CallbackEventSink(callbacks))
        request = DriverPowerMappingRequest(**_request_kwargs(prepared_run, callbacks))
        return DriverPowerMappingUseCase(request, sleep_fn=callbacks.get("sleep_fn"))
    except Exception:
        if owned_port:
            port = callbacks.get("measurement_port")
            if port is not None:
                _close_owned_measurement_port(port)
        raise


def create_amplifier_measurement(*, prepared_run: PreparedRun, **callbacks: Any) -> Any:
    """装配主功放测量用例：运行契约是唯一权威。

    与其它测量工厂一样，本函数不解析、也不接收硬件配置路径，端口由
    :func:`connect_instruments` 按运行快照装配（D12）。
    """
    prepared_run = require_run_contract(prepared_run)
    callbacks.update(
        run_id=prepared_run.context.run_id,
        run_directory=prepared_run.run_directory,
        paths=callbacks.get("paths"),
    )
    owned_port = callbacks.get("measurement_port") is None
    try:
        paths = callbacks.get("paths") or DEFAULT_PATHS
        repository = callbacks.get("result_repository") or FileMeasurementResultRepository(paths=paths)
        input_reader = callbacks.get("input_reader") or _input_reader_for(repository)
        # 在连接仪器前校验本次运行实际需要的校准输入：主功放始终需要线损，仅在
        # 驱动模式启用时才需要驱动映射。输入只从当前运行目录读取并做结构校验，因此
        # 结构无效或配置漂移都在打开仪器或执行任何 RF 动作前失败。随后按领域/硬件
        # 两套职责分别校验冻结快照。
        _preflight_amplifier_inputs(prepared_run, input_reader)
        _ensure_run_snapshots_are_intact(prepared_run)
        # 正式输入模型在应用边界构造（技术债步骤 8）：非法配置在打开任何仪器之前
        # 收敛为 AmplifierInputError；用例与服务只消费这一个类型化对象。
        inputs = AmplifierMeasurementInputs.from_run_configuration(
            prepared_run.configuration
        )
        if owned_port:
            callbacks["measurement_port"] = connect_instruments(prepared_run)
        callbacks["owns_measurement_port"] = owned_port
        callbacks["result_repository"] = repository
        callbacks["input_reader"] = input_reader
        callbacks.setdefault("event_sink", _CallbackEventSink(callbacks))
        request = AmplifierMeasurementRequest(
            **_request_kwargs(prepared_run, callbacks), inputs=inputs
        )
        return AmplifierMeasurementUseCase(request, sleep_fn=callbacks.get("sleep_fn"))
    except Exception:
        if owned_port and callbacks.get("measurement_port") is not None:
            _close_owned_measurement_port(callbacks["measurement_port"])
        raise
