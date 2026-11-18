"""仿真运行装配：读数加载、运行创建、标记登记与仿真作用域结果查询。

仿真运行 = 正式运行 + 注入端口。这里只做装配，不复制校验逻辑：

1. 候选配置走正式组合配置解析与完整运行校验；
2. 三份读数包一次性加载、校验频率网格与来源摘要，随后冻结为不可变 DTO；
3. 把规范化后的配置写成仿真输入草稿（唯一名、原子写）；
4. 复用 :func:`app.run_context.create_prepared_run` 创建真实 ``PreparedRun``，
   运行仓储显式指向 ``<simulation_root>/runs``，只跳过“模拟扫描绑定”门禁
   （Mock 快照的设备绑定来源就是模拟扫描）；
5. 经运行仓储写入并登记 ``inputs/simulation.json``；
6. 构造仿真作用域的 ``ResultQueryPort``，供页面校准选择与自动回填使用。

创建运行、写标记与登记 manifest 属于一次事务：任一步失败都用
:func:`application.run_service.discard_unstarted_run` 撤销本次新建且尚未开始的运行，
并尽力删除本次草稿，不留下“看起来像真机”的孤儿目录。真实的 ``runs/``、
``test_results/`` 与本模块无关。
"""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from app.run_context import PreparedRun, create_prepared_run, environment_version
from application.dto.measurement_results import MeasurementType
from application.dto.simulation_readings import SimulationReadings, SimulationReadingsError
from application.errors import ConfigurationError
from application.inputs.simulation_readings import (
    LoadedSimulationReadings,
    load_simulation_readings,
)
from application.ports.gui_data_service import ResultQueryPort
from application.ports.run_repository import RunRepositoryPort
from application.run_service import discard_unstarted_run
from application.simulation_run import (
    SIMULATION_MARKER_PATH,
    SIMULATION_MARKER_SCHEMA_VERSION,
    SimulationMarker,
    SimulationReadingsDigest,
)
from domain.run.models import generate_run_id, utc_now_iso
from infrastructure.config.json_config_repository import load_combined_configuration
from infrastructure.filesystem.atomic_json import write_json_atomic
from infrastructure.filesystem.paths import (
    DEFAULT_PATHS,
    MockDataPathService,
    PathService,
    ensure_directory,
    mock_stage_for_measurement_type,
)
from infrastructure.gui_data_service import ResultQueryAdapter
from infrastructure.logging.app_logging import get_logger
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
from infrastructure.persistence.run_repository import RunRepository
from infrastructure.persistence.stage_artifact_repository import (
    FileStageArtifactRepository,
)
from infrastructure.persistence.stage_result_repository import FileStageResultRepository
from instrument.simulation_port import SimulationRunPort


logger = get_logger(__name__)

#: 仿真运行默认快放倍数：真实服务时序（线损 1 秒/点、驱放与主功放 5 秒/点）
#: 保持事件顺序与分母不变，只把休眠时长除以该倍数。``1`` 保留为手动真实时序验收。
DEFAULT_SIMULATION_TIME_SCALE = 50

#: Mock 数据包与仿真运行固定覆盖的三个阶段；顺序与路径服务里的阶段索引一致。
SIMULATION_MEASUREMENT_TYPES = (
    MeasurementType.CABLE_LOSS,
    MeasurementType.DRIVER_POWER_MAPPING,
    MeasurementType.AMPLIFIER,
)


@dataclass(frozen=True)
class SimulatedRunAssembly:
    """一次成功的仿真运行装配结果；创建后不再被调用方改写。"""

    prepared_run: PreparedRun
    readings: Mapping[MeasurementType, SimulationReadings]
    paths: PathService
    run_repository: RunRepositoryPort
    results: ResultQueryPort
    marker: SimulationMarker
    config_draft: Path
    time_scale: float

    def __post_init__(self) -> None:
        if not isinstance(self.prepared_run, PreparedRun):
            raise TypeError(
                "仿真运行装配必须携带 PreparedRun，"
                f"收到 {type(self.prepared_run).__name__}"
            )
        if not isinstance(self.readings, Mapping):
            raise TypeError("仿真运行装配的读数必须是按阶段索引的映射")
        frozen: dict[MeasurementType, SimulationReadings] = {}
        for key, value in self.readings.items():
            measurement_type = (
                key if isinstance(key, MeasurementType) else MeasurementType(key)
            )
            if not isinstance(value, SimulationReadings):
                raise TypeError(
                    f"阶段 {measurement_type.value} 的读数必须是 SimulationReadings"
                )
            if value.measurement_type is not measurement_type:
                raise ValueError(
                    "仿真运行装配把 "
                    f"{value.measurement_type.value} 读数登记在 "
                    f"{measurement_type.value} 阶段"
                )
            frozen[measurement_type] = value
        object.__setattr__(self, "readings", MappingProxyType(frozen))
        object.__setattr__(self, "paths", PathService(self.paths.root))
        object.__setattr__(self, "config_draft", Path(self.config_draft))
        object.__setattr__(self, "time_scale", _require_time_scale(self.time_scale))


def prepare_simulated_run(
    candidate: Mapping[str, Any],
    match: Any,
    *,
    mock_paths: MockDataPathService,
    paths: PathService = DEFAULT_PATHS,
    time_scale: float = DEFAULT_SIMULATION_TIME_SCALE,
    now: datetime | None = None,
) -> SimulatedRunAssembly:
    """装配一次仿真运行；成功返回不可变结果，失败不留半成品运行。

    ``candidate`` 是协调器已规范化的编辑文档；``match`` 是已加载的 Mock 场景匹配
    结果。``mock_paths`` 决定读数包与上游摘要的解析根，``paths`` 只在默认参数下
    提供仿真数据根（运行仓储仍显式使用 ``paths.simulation_runs_root``）。
    """
    if not isinstance(mock_paths, MockDataPathService):
        raise TypeError(
            "prepare_simulated_run 需要 MockDataPathService，"
            f"收到 {type(mock_paths).__name__}"
        )
    if not isinstance(paths, PathService):
        raise TypeError(
            "prepare_simulated_run 的 paths 必须是 PathService，"
            f"收到 {type(paths).__name__}"
        )
    scale = _require_time_scale(time_scale)
    loaded = _load_candidate_configuration(candidate)
    frequencies = _test_frequencies(loaded.configuration)
    readings = _load_readings(
        mock_paths,
        expected_frequencies_ghz=frequencies,
        driver_enabled=_driver_mode_enabled(loaded.configuration),
    )
    draft = _write_simulation_draft(candidate, paths, now=now)
    repository = RunRepository(root=paths.simulation_runs_root)
    run_id = generate_run_id(now=now)
    try:
        prepared = create_prepared_run(
            loaded,
            run_id=run_id,
            software_version=environment_version(),
            config_path=draft,
            paths=paths,
            run_repository=repository,
            hardware_config=dict(candidate),
        )
        marker = _write_marker(
            repository,
            prepared.run_id,
            readings=readings,
            mock_paths=mock_paths,
            snapshot_name=_snapshot_name(match),
            time_scale=scale,
            now=now,
        )
        return SimulatedRunAssembly(
            prepared_run=prepared,
            readings={stage: item.readings for stage, item in readings.items()},
            paths=paths.simulation_paths,
            run_repository=repository,
            results=_build_result_query(paths.simulation_paths, repository),
            marker=marker,
            config_draft=draft,
            time_scale=scale,
        )
    except Exception:
        # 创建/标记/登记任一失败都撤销本次新建且尚未开始的运行；清理失败只记录，
        # 不覆盖原始异常。未创建成功时 discard_unstarted_run 按幂等处理。
        _discard_created_run(repository, run_id)
        _discard_draft(draft)
        raise


@dataclass(frozen=True)
class SimulationDiscardOutcome:
    """一次未提交仿真运行的补偿结果。

    补偿是两条独立删除（运行目录、输入草稿），任一失败都让结果不可判定为“已清理”：
    调用方必须据此进入人工清理，而不是只看到一条日志就继续下一次事务。``errors``
    保留原始异常，供界面/诊断说明残留原因。
    """

    run_id: str
    run_removed: bool
    draft_removed: bool
    errors: tuple[BaseException, ...] = ()

    @property
    def ok(self) -> bool:
        """运行目录与输入草稿是否都已确认删除。"""
        return self.run_removed and self.draft_removed

    def describe(self) -> str:
        """把清理结果与残留原因收敛成一段可展示文本。"""
        if self.ok:
            return f"仿真运行 {self.run_id} 已撤销"
        residual = []
        if not self.run_removed:
            residual.append("运行目录")
        if not self.draft_removed:
            residual.append("输入草稿")
        reasons = "；".join(str(error) for error in self.errors) or "未知原因"
        return (
            f"仿真运行 {self.run_id} 撤销未完成"
            f"（残留: {'、'.join(residual)}）: {reasons}"
        )


def discard_simulated_run(
    assembly: SimulatedRunAssembly,
) -> SimulationDiscardOutcome:
    """撤销一次已装配但尚未提交的仿真运行（提交失败补偿入口）。

    与装配内部的事务补偿共用同一组删除步骤，但这里返回结构化结果：运行目录与草稿
    分别尝试删除、失败原因逐项收集。调用方据此判断是否必须人工清理；补偿异常不会
    顶替最初的提交失败。
    """
    run_error = _discard_created_run(
        assembly.run_repository, assembly.prepared_run.run_id
    )
    draft_error = _discard_draft(assembly.config_draft)
    errors = tuple(
        error for error in (run_error, draft_error) if error is not None
    )
    return SimulationDiscardOutcome(
        run_id=assembly.prepared_run.run_id,
        run_removed=run_error is None,
        draft_removed=draft_error is None,
        errors=errors,
    )


def build_simulation_port(
    readings: Mapping[MeasurementType, SimulationReadings],
) -> SimulationRunPort:
    """按冻结读数构造运行级端口；不读文件、不打开 VISA。"""
    return SimulationRunPort(readings)


def simulation_sleep_fn(time_scale: float) -> Callable[[float], None]:
    """返回按 ``time_scale`` 缩短休眠时长的 ``sleep_fn``。

    只缩放休眠时长，不改变事件顺序、进度分母或结果计算；``time_scale <= 1`` 时
    保留真实时序（手动验收）。
    """
    scale = _require_time_scale(time_scale)

    def sleep(seconds: float) -> None:
        try:
            delay = float(seconds) / scale
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"sleep_fn 只接受数字时长，收到 {seconds!r}"
            ) from error
        if delay > 0:
            time.sleep(delay)

    return sleep


# -- 配置与读数 -------------------------------------------------------------


def _load_candidate_configuration(candidate: Mapping[str, Any]) -> Any:
    """把已规范化的候选文档解析为正式加载结果；规则复用正式入口。"""
    if not isinstance(candidate, Mapping):
        raise ConfigurationError(
            "仿真运行候选配置必须是 JSON 对象，"
            f"收到 {type(candidate).__name__}"
        )
    loaded = load_combined_configuration(candidate)
    if not loaded.valid:
        details = "; ".join(
            f"{issue.path}: {issue.message}" for issue in loaded.validation.errors
        )
        raise ConfigurationError(
            f"仿真运行候选配置校验失败，不能创建运行: {details}"
        )
    return loaded


def _test_frequencies(configuration: Any) -> tuple[Any, ...]:
    plan = getattr(configuration, "test_plan", None)
    return tuple(getattr(plan, "frequencies", ()) or ())


def _driver_mode_enabled(configuration: Any) -> bool:
    plan = getattr(configuration, "test_plan", None)
    return bool(getattr(plan, "driver_enabled", False))


def _load_readings(
    mock_paths: MockDataPathService,
    *,
    expected_frequencies_ghz: tuple[Any, ...],
    driver_enabled: bool,
) -> dict[MeasurementType, LoadedSimulationReadings]:
    """加载并校验三份读数包，核对来源场景与打包校准结果摘要。"""
    if not expected_frequencies_ghz:
        raise SimulationReadingsError(
            "冻结配置没有测试频点，不能校验仿真读数频率网格"
        )
    result: dict[MeasurementType, LoadedSimulationReadings] = {}
    for measurement_type in SIMULATION_MEASUREMENT_TYPES:
        stage = mock_stage_for_measurement_type(measurement_type.value)
        path = mock_paths.require_readings_path(stage)
        loaded = load_simulation_readings(
            path,
            expected_measurement_type=measurement_type,
            expected_frequencies_ghz=expected_frequencies_ghz,
        )
        _verify_sources(
            loaded,
            mock_paths=mock_paths,
            measurement_type=measurement_type,
            driver_enabled=driver_enabled,
        )
        result[measurement_type] = loaded
    return result


def _verify_sources(
    loaded: LoadedSimulationReadings,
    *,
    mock_paths: MockDataPathService,
    measurement_type: MeasurementType,
    driver_enabled: bool,
) -> None:
    """按 ``source`` 核对生成时的场景与打包校准结果未被替换。"""
    stage = mock_stage_for_measurement_type(measurement_type.value)
    source = loaded.readings.source
    scenario_path = mock_paths.require_scenario_path(stage)
    _require_digest(scenario_path, source.scenario_sha256, f"{stage.value} 场景文件")
    if source.cable_loss_result is not None:
        cable_loss_path = mock_paths.require_result_path(
            "cable_loss", source.cable_loss_result
        )
        _require_digest(
            cable_loss_path,
            source.cable_loss_sha256,
            f"{stage.value} 读数引用的打包线损结果",
        )
    if measurement_type is MeasurementType.AMPLIFIER:
        if driver_enabled:
            if source.driver_mapping_result is None:
                raise SimulationReadingsError(
                    "驱动模式下的主功放读数必须记录并引用打包驱动映射结果"
                    "（source.driver_mapping_result），请重新生成 readings.json"
                )
            mapping_path = mock_paths.require_result_path(
                "driver_mapping", source.driver_mapping_result
            )
            _require_digest(
                mapping_path,
                source.driver_mapping_sha256,
                "主功放读数引用的打包驱动映射结果",
            )
        elif source.driver_mapping_result is not None:
            raise SimulationReadingsError(
                "驱动关闭时主功放读数不能引用驱动映射结果"
                "（source.driver_mapping_result 应为 null），请重新生成 readings.json"
            )


def _require_digest(path: Path, expected: str, description: str) -> None:
    actual = _file_sha256(path, description)
    if actual != expected:
        raise SimulationReadingsError(
            f"{description}的 SHA-256 与仿真读数记录不一致；"
            "数据包在上次生成后被修改，请重新生成 readings.json"
        )


def _file_sha256(path: Path, description: str) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise SimulationReadingsError(
            f"无法读取{description}: {path}"
        ) from error
    return digest.hexdigest()


def _relative_mock_reference(mock_paths: MockDataPathService, path: Path) -> str:
    try:
        relative = Path(path).resolve().relative_to(mock_paths.project_root)
    except ValueError as error:
        raise SimulationReadingsError(
            f"仿真数据文件不在数据包根目录内: {path}"
        ) from error
    return relative.as_posix()


# -- 运行创建与标记 ---------------------------------------------------------


def _write_simulation_draft(
    candidate: Mapping[str, Any], paths: PathService, *, now: datetime | None
) -> Path:
    """原子写入规范化配置草稿；唯一名避免覆盖其它运行。"""
    directory = ensure_directory(paths.simulation_configs_dir)
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    for _attempt in range(8):
        target = directory / f"{stamp}_{uuid4().hex[:8]}.json"
        if target.exists():
            continue
        return write_json_atomic(target, dict(candidate))
    raise RuntimeError(
        f"无法在 {directory} 生成唯一的仿真输入草稿文件名；请稍后重试"
    )


def _write_marker(
    repository: RunRepositoryPort,
    run_id: str,
    *,
    readings: Mapping[MeasurementType, LoadedSimulationReadings],
    mock_paths: MockDataPathService,
    snapshot_name: str,
    time_scale: float,
    now: datetime | None,
) -> SimulationMarker:
    marker = SimulationMarker(
        schema_version=SIMULATION_MARKER_SCHEMA_VERSION,
        run_id=run_id,
        created_at=utc_now_iso(now),
        time_scale=time_scale,
        snapshot_name=snapshot_name,
        readings=tuple(
            _readings_digest(measurement_type, loaded, mock_paths)
            for measurement_type, loaded in readings.items()
        ),
    )
    relative = repository.write_run_json(
        run_id, SIMULATION_MARKER_PATH, marker.to_document()
    )
    repository.register_file(
        run_id,
        relative,
        description="仿真运行标记",
        now=now,
    )
    return marker


def _readings_digest(
    measurement_type: MeasurementType,
    loaded: LoadedSimulationReadings,
    mock_paths: MockDataPathService,
) -> SimulationReadingsDigest:
    stage = mock_stage_for_measurement_type(measurement_type.value)
    scenario_path = mock_paths.require_scenario_path(stage)
    return SimulationReadingsDigest(
        measurement_type=measurement_type,
        readings_path=_relative_mock_reference(mock_paths, loaded.path),
        readings_sha256=loaded.sha256,
        readings_size_bytes=loaded.size_bytes,
        scenario_path=_relative_mock_reference(mock_paths, scenario_path),
        scenario_sha256=loaded.readings.source.scenario_sha256,
    )


def _snapshot_name(match: Any) -> str:
    name = getattr(match, "snapshot_name", None)
    if not isinstance(name, str) or not name.strip():
        raise ConfigurationError(
            "Mock 配置匹配结果缺少快照名称，不能创建仿真运行"
        )
    return name.strip()


def _build_result_query(
    paths: PathService, run_repository: RunRepositoryPort
) -> ResultQueryPort:
    """构造只读的仿真作用域结果查询：阶段目录与运行历史都指向仿真根。"""
    return ResultQueryAdapter(
        paths,
        run_history=FileRunHistoryRepository(runs=run_repository),
        stage_results=FileStageResultRepository(paths=paths),
        stage_artifacts=FileStageArtifactRepository(paths=paths),
    )


# -- 补偿与校验 -------------------------------------------------------------


def _discard_created_run(
    repository: RunRepositoryPort, run_id: str
) -> BaseException | None:
    """尽力撤销本次新建且尚未开始的运行；返回未能撤销的原因。

    装配事务内部与提交失败补偿共用这一步：失败只记录为返回值，由调用方决定是否
    阻断后续事务，绝不在这里抛出并顶替原始创建错误。
    """
    try:
        discard_unstarted_run(repository, run_id)
    except Exception as error:  # noqa: BLE001 - 补偿失败必须记录，但不能顶替原始创建错误
        logger.error(
            "撤销仿真运行 %s 失败；运行根目录中可能残留半成品运行，请人工检查",
            run_id,
            exc_info=True,
        )
        return error
    return None


def _discard_draft(path: Path) -> BaseException | None:
    """尽力删除本次草稿；返回未能删除的原因。"""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as error:
        logger.warning("无法删除仿真输入草稿 %s: %s", path, error)
        return error
    return None


def _require_time_scale(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"time_scale 必须是数字，收到 {type(value).__name__}"
        )
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"time_scale 必须是有限正数，收到 {value!r}")
    return number


__all__ = [
    "DEFAULT_SIMULATION_TIME_SCALE",
    "SIMULATION_MEASUREMENT_TYPES",
    "SimulatedRunAssembly",
    "SimulationDiscardOutcome",
    "build_simulation_port",
    "discard_simulated_run",
    "prepare_simulated_run",
    "simulation_sleep_fn",
]
