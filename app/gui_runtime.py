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
from infrastructure.persistence.stage_result_repository import FileStageResultRepository
from infrastructure.persistence.artifact_generator import ArchiveArtifactGenerator
from application.inputs import ResultInputReader
from application.inputs.explicit_stage_input import load_selected_stage_result
from application.run_stages import RunStageRecorder
from domain.run.models import STAGE_CABLE_LOSS, STAGE_DRIVER_MAPPING
from application.measurements import (
    AmplifierMeasurementUseCase,
    CableLossUseCase,
    DriverPowerMappingUseCase,
    validate_amplifier_calibration_inputs,
)
from application.measurements.driver_target_grid import require_driver_target_grid
from application.ports.stage_result_repository import (
    LoadedStageResult,
    MeasurementStage,
)
from application.run_service import (
    RunNotResumable,
    ensure_run_repository_supports_run_contract,
    has_run_contract,
    load_hardware_config,
    load_run_configuration,
)
from domain.configuration.models import require_current_schema_version
from app.events import MessageEvent, ProgressEvent, RealtimeDataEvent
from application.dto import (
    AmplifierCalibrationInputs,
    AmplifierMeasurementInputs,
    AmplifierMeasurementRequest,
    CableLossMeasurementRequest,
    DriverPowerMappingRequest,
)
from application.dto.measurement_requests import (
    StageResultDigest,
    coerce_input_path,
    coerce_stage_digest,
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
    dut_power_plan: Any = None,
    driver_power_plan: Any = None,
    recorder: Any = None,
) -> Any:
    """Create the explicit simulation assembly used by offline callers."""
    return create_measurement_port(
        mode="simulation",
        dut_power_plan=dut_power_plan,
        driver_power_plan=driver_power_plan,
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
    """模拟扫描留下的设备绑定不能用于真实运行。

    这是唯一的硬门禁：``validate_config``/启动器只把模拟绑定标成 warning，允许配置
    加载、保存和启动界面；本函数在创建运行目录之前拒绝，避免冻结一份对着演示地址的
    运行快照。
    """
    issues = simulated_binding_issues(hardware_config)
    if not issues:
        return
    details = "；".join(issue.path for issue in issues)
    raise ConfigurationError(
        "配置中的设备绑定来自模拟扫描，不能创建真实运行："
        "请用“真实网络扫描”重新获取设备并应用后再创建运行（"
        + details
        + "）"
    )


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


def _stage_recorder(
    prepared_run: PreparedRun, paths: PathService | None = None
) -> RunStageRecorder:
    """当前运行的阶段记录器；运行契约已校验，运行仓储是唯一写入目标。

    同时注入阶段结果仓库：阶段正式结果写入独立阶段目录，运行目录只保存引用文档，
    解析引用时必须再次加载并校验权威结果（步骤 7）。
    """
    service = paths if paths is not None else DEFAULT_PATHS
    return RunStageRecorder(
        prepared_run.run_repository,
        prepared_run.context.run_id,
        stage_results=FileStageResultRepository(paths=service),
    )


def _require_stage_inputs(
    prepared_run: PreparedRun, *stages: str, paths: PathService | None = None
) -> None:
    """在连接仪器前校验运行的前置阶段输入已就绪。

    校验失败会抛出 :class:`StageInputUnavailable`，从而在打开仪器或执行任何硬件
    动作前终止，避免为无效输入付出连接代价或产生硬件副作用。
    """
    recorder = _stage_recorder(prepared_run, paths)
    for stage in stages:
        recorder.verified_result_path(stage)


def _driver_mode_enabled(prepared_run: PreparedRun) -> bool:
    """判断当前运行配置是否启用驱动模式，用于决定是否需要驱动映射输入。"""
    plan = getattr(prepared_run.configuration, "test_plan", None)
    return bool(getattr(plan, "driver_enabled", False))


def _load_explicit_stage_result(
    path: Path,
    stage: MeasurementStage,
    paths: PathService | None = None,
    *,
    digest: StageResultDigest | None = None,
    label: str,
) -> LoadedStageResult:
    """按阶段结果仓库加载用户显式选择的阶段文件，并复核选择时的摘要。

    显式路径与 ``run_id`` 无关，因此这里不读运行 manifest、不扫描目录，也不查找
    “最新结果”。阶段目录边界、文件名身份、JSON、正式 DTO、类型与 ``completed``
    状态全部由 :meth:`StageResultRepository.load` 一次校验；给出 ``digest`` 时再核对
    文件在选择之后没有被改动或替换。返回**同一次读取**的结果，调用方应直接用它构造
    输入，而不是按路径二次读取。
    """
    repository = FileStageResultRepository(paths=paths or DEFAULT_PATHS)
    return load_selected_stage_result(
        repository, stage, path, digest=digest, label=label
    )


def _preflight_driver_mapping_inputs(
    prepared_run: PreparedRun,
    input_reader: Any,
    paths: PathService | None,
    *,
    cable_loss_path: Path | None,
    cable_loss_digest: StageResultDigest | None = None,
) -> Mapping[str, Any] | None:
    """在连接仪器前确定驱动映射实际消费的线损输入。

    ``cable_loss_path`` 是页面显式选择的阶段结果文件：先按阶段结果仓库校验边界与
    正式结果契约，并核对选择时的摘要与大小，再按**同一次读取**的结果转成线损文档，
    因此显式选择不受“当前运行是否登记线损阶段”影响，也不会消费被替换的文件。
    省略路径时保持旧调用方的运行引用读取（``verified_result_path()`` 会复核 manifest
    摘要），返回 ``None`` 表示由用例按运行目录自行读取。
    """
    if cable_loss_path is None:
        _require_stage_inputs(prepared_run, STAGE_CABLE_LOSS, paths=paths)
        return None
    loaded = _load_explicit_stage_result(
        cable_loss_path,
        MeasurementStage.CABLE_LOSS,
        paths,
        digest=cable_loss_digest,
        label="线损",
    )
    return input_reader.read_cable_loss_result(loaded.result)


def _test_frequencies(prepared_run: PreparedRun) -> tuple[Any, ...]:
    """从已冻结运行配置中读取测试频率，供运行前输入结构校验使用。"""
    plan = getattr(prepared_run.configuration, "test_plan", None)
    return tuple(getattr(plan, "frequencies", ()) or ())


def _preflight_amplifier_inputs(
    prepared_run: PreparedRun,
    input_reader: Any,
    paths: PathService | None = None,
    *,
    cable_loss_path: Path | None = None,
    driver_mapping_path: Path | None = None,
    cable_loss_digest: StageResultDigest | None = None,
    driver_mapping_digest: StageResultDigest | None = None,
) -> AmplifierCalibrationInputs:
    """在连接仪器前读取并校验主功放的校准输入。

    显式路径（页面选择）优先：先按阶段结果仓库校验阶段目录边界与正式结果契约，并
    核对选择时的摘要与大小，再用**同一次读取**的正式结果构造输入，允许线损与驱动
    映射来自不同运行；省略某个路径时，该输入回退到当前运行目录的已校验引用
    （``verified_result_path()`` 复核 manifest 摘要）。驱动模式关闭时不读取驱动映射，
    因此 ``driver_mapping_path`` 在该模式下不参与校验。

    返回本次读取到的校准输入：调用方在构造正式输入后继续用它校验目标表与 DUT
    功率扫描计划是否逐点一致，并把同一个对象交给用例，正式测量因此不会重复读取
    同一份文件，也不会消费与预检不同的对象。
    """
    recorder = _stage_recorder(prepared_run, paths)
    if cable_loss_path is None:
        loss_data = input_reader.read_cable_loss(
            recorder.verified_result_path(STAGE_CABLE_LOSS)
        )
    else:
        loaded_loss = _load_explicit_stage_result(
            cable_loss_path,
            MeasurementStage.CABLE_LOSS,
            paths,
            digest=cable_loss_digest,
            label="线损",
        )
        loss_data = input_reader.read_cable_loss_result(loaded_loss.result)
    driver_enabled = _driver_mode_enabled(prepared_run)
    driver_mapping = None
    if driver_enabled:
        if driver_mapping_path is None:
            driver_mapping = input_reader.read_driver_mapping(
                recorder.verified_result_path(STAGE_DRIVER_MAPPING)
            )
        else:
            loaded_mapping = _load_explicit_stage_result(
                driver_mapping_path,
                MeasurementStage.DRIVER_MAPPING,
                paths,
                digest=driver_mapping_digest,
                label="驱动映射",
            )
            driver_mapping = input_reader.read_driver_mapping_result(
                loaded_mapping.result
            )
    validate_amplifier_calibration_inputs(
        loss_data,
        driver_mapping,
        frequencies=_test_frequencies(prepared_run),
        driver_enabled=driver_enabled,
    )
    return AmplifierCalibrationInputs(
        loss_data=loss_data,
        driver_mapping=driver_mapping,
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
    # 规则 16：1.0 历史快照可查看已保存结果，但不能继续运行/连接仪器。
    version_issues = require_current_schema_version(loaded.configuration)
    if version_issues:
        details = "; ".join(
            f"{issue.path}: {issue.message}" for issue in version_issues
        )
        raise RunNotResumable(
            f"运行 {run_id} 使用旧版配置，不能继续测量；请重新创建运行: {details}"
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


def _request_kwargs(
    prepared_run: PreparedRun,
    callbacks: dict[str, Any],
    *,
    cable_loss_path: Path | None = None,
    driver_mapping_path: Path | None = None,
    cable_loss_digest: StageResultDigest | None = None,
    driver_mapping_digest: StageResultDigest | None = None,
) -> dict[str, Any]:
    """Build the shared request inputs at the application composition boundary.

    阶段记录器与自动归档是运行契约的组成部分：运行契约已校验，因此这里**无条件**
    装配，不再有“没有运行就没有阶段记录/交付产物”的分支。

    ``cable_loss_path`` / ``driver_mapping_path`` 是页面显式选择的阶段结果文件
    （步骤 13）。它们与 ``run_id`` 无关，调用方必须原样给出；工厂已在连接仪器前用
    :func:`coerce_input_path` 校验过类型与空值。``*_digest`` 是选择那一刻的文件事实，
    随请求交给用例，使绕过组装层直接构造请求的调用方同样能复核文件未被替换。
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
        # 页面显式选择的校准输入来源（步骤 13）；与运行身份无关。
        "cable_loss_path": cable_loss_path,
        "driver_mapping_path": driver_mapping_path,
        # 选择时的文件事实：用例侧同样可以复核，防止绕过组装层时消费被替换的文件。
        "cable_loss_digest": cable_loss_digest,
        "driver_mapping_digest": driver_mapping_digest,
        # 正式结果写入独立阶段目录，运行目录只登记引用（步骤 7）。
        "stage_result_repository": (
            callbacks.get("stage_result_repository")
            or FileStageResultRepository(paths=paths)
        ),
        "stage_recorder": _stage_recorder(prepared_run, paths),
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


def create_driver_mapping_measurement(
    *,
    prepared_run: PreparedRun,
    cable_loss_path: Path | str | None = None,
    cable_loss_digest: StageResultDigest | None = None,
    **callbacks: Any,
) -> Any:
    """装配驱动映射测量用例：运行契约是唯一运行权威，线损可显式选择。

    与其它测量工厂一样，本函数不解析、也不接收硬件配置路径，端口由
    :func:`connect_instruments` 按运行快照装配（D12）。

    ``cable_loss_path`` 是页面选中的线损阶段结果文件，``cable_loss_digest`` 是选择
    那一刻的 SHA-256 与大小。路径与摘要都在连接仪器前校验：阶段目录边界、正式结果
    契约、以及“内容仍是用户选择的那一份”；校验通过后按同一次读取的文档构造输入，
    省略路径时沿用当前运行引用的兼容读取。
    """
    prepared_run = require_run_contract(prepared_run)
    # 显式输入来源先于任何快照校验/仪器连接确定：空路径、非法类型或缺失路径的摘要
    # 都不会付出连接代价。
    cable_loss_path = coerce_input_path(cable_loss_path, "cable_loss_path")
    cable_loss_digest = coerce_stage_digest(
        cable_loss_digest,
        cable_loss_path,
        field_name="cable_loss_digest",
        path_name="cable_loss_path",
    )
    callbacks.update(
        run_id=prepared_run.context.run_id,
        run_directory=prepared_run.run_directory,
        paths=callbacks.get("paths"),
    )
    owned_port = callbacks.get("measurement_port") is None
    try:
        paths = callbacks.get("paths") or DEFAULT_PATHS
        repository = callbacks.get("result_repository") or FileMeasurementResultRepository(
            paths=paths
        )
        input_reader = callbacks.get("input_reader") or _input_reader_for(repository)
        # 连接仪器前确定本次实际消费的线损：显式路径按阶段结果仓库校验并核对选择时
        # 摘要，缺失/损坏/被替换的输入不会付出连接代价；省略路径时沿用运行引用。
        loss_data = _preflight_driver_mapping_inputs(
            prepared_run,
            input_reader,
            paths,
            cable_loss_path=cable_loss_path,
            cable_loss_digest=cable_loss_digest,
        )
        _ensure_run_snapshots_are_intact(prepared_run)
        if owned_port:
            callbacks["measurement_port"] = connect_instruments(prepared_run)
        callbacks["owns_measurement_port"] = owned_port
        callbacks["result_repository"] = repository
        callbacks["input_reader"] = input_reader
        callbacks.setdefault("event_sink", _CallbackEventSink(callbacks))
        request = DriverPowerMappingRequest(
            **_request_kwargs(
                prepared_run,
                callbacks,
                cable_loss_path=cable_loss_path,
                cable_loss_digest=cable_loss_digest,
            ),
            # 预检读取并校验过的同一份线损文档：用例直接复用，不再二次读取。
            loss_data=loss_data,
        )
        return DriverPowerMappingUseCase(request, sleep_fn=callbacks.get("sleep_fn"))
    except Exception:
        if owned_port:
            port = callbacks.get("measurement_port")
            if port is not None:
                _close_owned_measurement_port(port)
        raise


def create_amplifier_measurement(
    *,
    prepared_run: PreparedRun,
    cable_loss_path: Path | str | None = None,
    driver_mapping_path: Path | str | None = None,
    cable_loss_digest: StageResultDigest | None = None,
    driver_mapping_digest: StageResultDigest | None = None,
    **callbacks: Any,
) -> Any:
    """装配主功放测量用例：运行契约是唯一运行权威，校准输入可显式选择。

    与其它测量工厂一样，本函数不解析、也不接收硬件配置路径，端口由
    :func:`connect_instruments` 按运行快照装配（D12）。

    ``cable_loss_path`` / ``driver_mapping_path`` 是页面选中的阶段结果文件，两个
    ``*_digest`` 是选择那一刻的 SHA-256 与大小。路径、阶段契约与摘要都在连接仪器前
    校验，并用同一次读取的结果完成线损/驱动映射的结构校验；省略时保持当前运行引用
    的兼容读取。
    """
    prepared_run = require_run_contract(prepared_run)
    # 显式校准输入来源先于任何快照校验/仪器连接确定：空路径、非法类型或缺失路径的
    # 摘要都不会付出连接代价。
    cable_loss_path = coerce_input_path(cable_loss_path, "cable_loss_path")
    driver_mapping_path = coerce_input_path(
        driver_mapping_path, "driver_mapping_path"
    )
    cable_loss_digest = coerce_stage_digest(
        cable_loss_digest,
        cable_loss_path,
        field_name="cable_loss_digest",
        path_name="cable_loss_path",
    )
    driver_mapping_digest = coerce_stage_digest(
        driver_mapping_digest,
        driver_mapping_path,
        field_name="driver_mapping_digest",
        path_name="driver_mapping_path",
    )
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
        # 驱动模式启用时才需要驱动映射。显式路径按阶段结果仓库校验并核对选择时摘要；
        # 省略时回退到当前运行引用。结构无效或配置漂移都在打开仪器或执行任何 RF
        # 动作前失败。随后按领域/硬件两套职责分别校验冻结快照。
        calibration_inputs = _preflight_amplifier_inputs(
            prepared_run,
            input_reader,
            paths,
            cable_loss_path=cable_loss_path,
            driver_mapping_path=driver_mapping_path,
            cable_loss_digest=cable_loss_digest,
            driver_mapping_digest=driver_mapping_digest,
        )
        _ensure_run_snapshots_are_intact(prepared_run)
        # 正式输入模型在应用边界构造（技术债步骤 8）：非法配置在打开任何仪器之前
        # 收敛为 AmplifierInputError；用例与服务只消费这一个类型化对象。
        inputs = AmplifierMeasurementInputs.from_run_configuration(
            prepared_run.configuration
        )
        # 驱动模式的目标表必须与配置生成的目标 DUT 点逐点一致（4.4）；在这里校验
        # 可以把“缺点/重复点/旧单计划目标表”挡在连接仪器之前，而不是等用例或扫描
        # 中途才发现。未启用驱动模式时该校验直接返回空网格。
        require_driver_target_grid(inputs, calibration_inputs.driver_mapping)
        if owned_port:
            callbacks["measurement_port"] = connect_instruments(prepared_run)
        callbacks["owns_measurement_port"] = owned_port
        callbacks["result_repository"] = repository
        callbacks["input_reader"] = input_reader
        callbacks.setdefault("event_sink", _CallbackEventSink(callbacks))
        request = AmplifierMeasurementRequest(
            **_request_kwargs(
                prepared_run,
                callbacks,
                cable_loss_path=cable_loss_path,
                driver_mapping_path=driver_mapping_path,
                cable_loss_digest=cable_loss_digest,
                driver_mapping_digest=driver_mapping_digest,
            ),
            inputs=inputs,
            # 预检已经读取并校验过同一份校准输入；用例直接复用，不再二次读取。
            calibration_inputs=calibration_inputs,
        )
        return AmplifierMeasurementUseCase(request, sleep_fn=callbacks.get("sleep_fn"))
    except Exception:
        if owned_port and callbacks.get("measurement_port") is not None:
            _close_owned_measurement_port(callbacks["measurement_port"])
        raise
