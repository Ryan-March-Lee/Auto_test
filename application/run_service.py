"""应用层运行创建服务：把已校验配置冻结为运行快照。

本模块实现重构指南步骤 3 的“创建运行”操作：

- 创建快照前执行**完整运行配置校验**，阶段专用校验通过但完整校验失败的配置
  不会产生可运行的运行目录。
- 通过 :class:`RunRepositoryPort` 在运行目录写入不可变的 ``config.json`` 和初始 manifest。
- 返回 :class:`RunSnapshot`，后续阶段据此携带的运行目录读取配置和写入结果。
- 提供 :func:`load_run_configuration`，使快照可以脱离 GUI 单独加载并重新校验。

本模块只依赖领域模型与端口，不导入基础设施或 Qt。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from application.errors import ConfigurationError
from application.ports.config_repository import ConfigurationLoadResult
from application.ports.run_repository import RunRepositoryPort
from domain.configuration.models import (
    RunConfiguration,
    RunResourceMapping,
    TestPlan,
    validate_run_configuration,
)
from domain.configuration.types import ConfigValidationResult
from domain.run.models import (
    HARDWARE_CONFIG_INPUT_PATH,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
    FileReference,
    StageStatus,
    TERMINAL_RUN_STATUSES,
    RunManifest,
    RunStatus,
    utc_now_iso,
)


RUN_CONFIG_SNAPSHOT_SCHEMA_VERSION = 1

# 挂到项目专用 ``pa_auto_test`` logger 树下（与 infrastructure.logging.get_logger
# 的命名规则一致），清理失败的 ERROR 日志才能进入 ``setup_logging`` 的日志文件；
# 不直接导入 infrastructure，保持应用层依赖边界。
logger = logging.getLogger("pa_auto_test." + __name__)


class RunNotResumable(ConfigurationError):
    """运行不可继续测量（终态、缺硬件配置快照或快照校验失败）。

    调用方应据此提示用户创建新运行，而不是回退到全局配置或其它运行目录。
    """


class RunIntegrityError(RunNotResumable):
    """运行目录文件缺失、被替换或未通过快照校验。"""


@dataclass(frozen=True)
class FileCheck:
    """一个 manifest 文件引用的离线校验结果。"""

    path: str
    valid: bool
    error: str | None = None


@dataclass(frozen=True)
class RunInspection:
    """运行目录的只读诊断结果。"""

    manifest: RunManifest
    files: tuple[FileCheck, ...]
    resumable: bool
    reason: str | None = None
    failure_kind: str | None = None


def inspect_run(
    run_repository: RunRepositoryPort,
    run_id: str,
    *,
    verify_files: bool = False,
) -> RunInspection:
    """只读检查运行状态、配置和可恢复性，不修改 manifest 或连接硬件。"""
    manifest = run_repository.load_manifest(run_id)
    checks: list[FileCheck] = []
    references: list[FileReference] = []
    if manifest.config is not None:
        references.append(manifest.config)
    references.extend(manifest.inputs)
    if verify_files:
        references.extend(manifest.outputs)
    for reference in references:
        try:
            run_repository.verify_file(run_id, reference)
        except (OSError, ValueError) as error:
            checks.append(FileCheck(reference.path, False, str(error)))
        else:
            checks.append(FileCheck(reference.path, True))

    if any(not item.valid for item in checks):
        return RunInspection(
            manifest, tuple(checks), False, "运行目录存在文件完整性错误", "integrity"
        )

    if manifest.status is RunStatus.RUNNING:
        return RunInspection(
            manifest, tuple(checks), False,
            "运行仍处于 running，必须确认原进程已退出后再显式处理",
            "not_resumable",
        )
    if manifest.status in TERMINAL_RUN_STATUSES:
        return RunInspection(
            manifest, tuple(checks), False,
            f"运行已处于终态 {manifest.status.value}，请创建新运行",
            "not_resumable",
        )
    try:
        loaded = load_run_configuration(run_repository, run_id)
        if not loaded.valid:
            raise RunIntegrityError(
                "运行配置快照校验失败: " + _validation_details(loaded.validation)
            )
        load_hardware_config(run_repository, run_id)
        _require_completed_stage_inputs(run_repository, manifest)
    except OSError as error:
        return RunInspection(manifest, tuple(checks), False, str(error), "integrity")
    except ValueError as error:
        return RunInspection(manifest, tuple(checks), False, str(error), "integrity")
    return RunInspection(manifest, tuple(checks), True)


def _require_completed_stage_inputs(
    run_repository: RunRepositoryPort, manifest: RunManifest
) -> None:
    """只校验已完成阶段的结果输入，避免无关输出阻断前置恢复检查。"""
    for name in (STAGE_CABLE_LOSS, STAGE_DRIVER_MAPPING):
        record = manifest.stage(name)
        if record.status is not StageStatus.COMPLETED or record.result is None:
            continue
        reference = next((item for item in manifest.inputs if item.path == record.result), None)
        if reference is None:
            raise RunIntegrityError(f"阶段 {name} 的结果未登记: {record.result}")
        try:
            run_repository.verify_file(manifest.run_id, reference)
        except (OSError, ValueError) as error:
            raise RunIntegrityError(f"阶段 {name} 的输入校验失败: {error}") from error


@dataclass(frozen=True)
class RunSnapshot:
    """一次已创建运行的标识、目录和初始配置快照引用。

    运行契约快照必须同时冻结硬件配置，因此 ``hardware_config_path`` 是必需
    字段且不允许为空；旧版兼容快照请使用 ``app.run_context.prepare_run``。
    """

    run_id: str
    run_directory: Path
    config_path: Path
    manifest: RunManifest
    hardware_config_path: Path

    def __post_init__(self) -> None:
        # 类型标注只提供静态保证，这里补上运行时保证：即便显式传入 None 也拒绝，
        # 避免构造出缺少硬件配置、无法被 load_prepared_run 恢复的快照。
        if self.hardware_config_path is None:
            raise ValueError(
                "RunSnapshot.hardware_config_path 不能为空："
                "运行契约快照必须包含 inputs/hardware_config.json"
            )


def configuration_snapshot(
    configuration: RunConfiguration,
    *,
    run_id: str,
    software_version: str | None = None,
    git_commit: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """把一次运行的完整配置序列化成运行目录 ``config.json`` 的内容。"""
    return {
        "schema_version": RUN_CONFIG_SNAPSHOT_SCHEMA_VERSION,
        "run_id": run_id,
        "created_at": created_at,
        "software_version": software_version,
        "git_commit": git_commit,
        "test_plan": configuration.test_plan.to_dict(),
        "run_mapping": configuration.run_mapping.to_dict(),
    }


def _validation_details(validation: ConfigValidationResult) -> str:
    return "; ".join(f"{issue.path}: {issue.message}" for issue in validation.errors)


def create_run_snapshot(
    loaded: ConfigurationLoadResult,
    *,
    run_repository: RunRepositoryPort,
    run_id: str,
    hardware_config: Mapping[str, Any],
    software_version: str | None = None,
    git_commit: str | None = None,
    notes: str | None = None,
    product_name: str | None = None,
    now: datetime | None = None,
) -> RunSnapshot:
    """校验配置并创建运行快照，失败时不产生可运行的运行目录。

    创建完整运行必须同时通过两层校验：

    1. 调用方加载结果 ``loaded.valid``（可能包含文件格式/legacy 转换错误）。
    2. 领域层的完整运行校验 :func:`validate_run_configuration`。

    因此阶段专用校验（线损、驱动映射）通过但完整运行校验失败的配置，仍会被
    拒绝，不会留下半成品运行目录，也不会被后续阶段误当作可运行运行。

    ``hardware_config`` 是**必需参数**：本次运行用于装配硬件端口的完整配置
    （旧版组合格式）。它会被冻结为 ``inputs/hardware_config.json`` 并登记到
    manifest，使运行目录自包含；硬件连接和测量阶段只消费该快照，而不是全局
    可变配置。缺少该参数、显式传入 ``None`` 或传入非映射对象时，都在创建任何
    运行目录之前立即失败，保证任何成功创建的运行都可以按当前恢复规则（
    ``load_prepared_run``）重新加载。不携带硬件配置的兼容快照请改用
    ``app.run_context.prepare_run``，它不属于可恢复运行契约。

    硬件快照写入、登记或 manifest 更新失败时，会调用仓储的 ``discard_run``
    撤销刚创建的整个运行目录，不遗留看似有效的半成品运行；原始异常仍向
    调用方抛出，清理失败只记录日志，不掩盖创建错误。
    """
    if hardware_config is None:
        raise ValueError(
            "运行契约必须包含硬件配置快照：create_run_snapshot 的 "
            "hardware_config 不能为 None，缺少硬件配置的运行无法恢复"
        )
    if not isinstance(hardware_config, Mapping):
        # 在创建运行目录前拒绝非法参数，避免依赖后续补偿清理。
        raise ValueError("hardware_config 必须是配置映射")
    if not loaded.valid:
        raise ValueError(
            "运行配置校验失败，不能创建运行快照: " + _validation_details(loaded.validation)
        )
    full_validation = validate_run_configuration(loaded.configuration)
    if not full_validation.valid:
        raise ValueError(
            "完整运行配置校验失败，不能创建运行快照: " + _validation_details(full_validation)
        )
    created_at = utc_now_iso(now)
    payload = configuration_snapshot(
        loaded.configuration,
        run_id=run_id,
        software_version=software_version,
        git_commit=git_commit,
        created_at=created_at,
    )
    layout, manifest = run_repository.create_run(
        payload,
        run_id=run_id,
        software_version=software_version,
        notes=notes,
        product_name=product_name,
        now=now,
    )
    try:
        hardware_config_path = _freeze_hardware_config(
            run_repository, layout.run_id, hardware_config, now=now
        )
    except Exception:
        # 创建后的任何失败都撤销整个运行目录，不遗留半成品运行。
        _discard_created_run(run_repository, layout.run_id)
        raise
    return RunSnapshot(
        run_id=manifest.run_id,
        run_directory=Path(layout.run_directory),
        config_path=Path(layout.config_path),
        manifest=manifest,
        hardware_config_path=hardware_config_path,
    )


def _discard_created_run(run_repository: RunRepositoryPort, run_id: str) -> None:
    """创建失败后补偿清理整个运行目录；清理失败不掩盖原始创建异常。"""
    try:
        run_repository.discard_run(run_id)
    except Exception:  # noqa: BLE001 - 只记录清理失败上下文，仍抛原始异常
        logger.error(
            "硬件快照写入失败后清理运行目录 %s 也失败了；"
            "运行根目录中可能残留半成品运行，请人工检查",
            run_id,
            exc_info=True,
        )


def _freeze_hardware_config(
    run_repository: RunRepositoryPort,
    run_id: str,
    hardware_config: Mapping[str, Any],
    *,
    now: datetime | None,
) -> Path:
    """把硬件装配配置原子写入运行目录并登记，返回快照绝对路径。

    调用方 :func:`create_run_snapshot` 已在创建运行目录前校验 ``hardware_config``
    是配置映射，因此本函数不再重复校验类型。写入、登记或 manifest 更新抛出的
    异常直接上抛，由调用方通过 ``discard_run`` 撤销整个运行目录，因此本函数不做
    文件级补偿清理。
    """
    relative = run_repository.write_run_json(
        run_id, HARDWARE_CONFIG_INPUT_PATH, dict(hardware_config)
    )
    reference = run_repository.register_file(
        run_id,
        relative,
        description="硬件组装配置快照",
        now=now,
    )
    layout = run_repository.layout(run_id)
    return Path(layout.run_directory) / reference.path


def load_run_configuration(
    run_repository: RunRepositoryPort, run_id: str
) -> ConfigurationLoadResult:
    """从运行目录的 ``config.json`` 单独重建配置并重新执行完整运行校验。

    返回带校验结果的 :class:`ConfigurationLoadResult`，因此调用方可以脱离 GUI
    判断快照是否仍可作为一次完整运行使用，而不仅仅是“能解析”。
    """
    payload = run_repository.read_config(run_id)
    if not isinstance(payload, dict):
        raise ValueError(f"运行 {run_id} 的 config.json 根节点必须是对象")
    version = payload.get("schema_version")
    if version != RUN_CONFIG_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            f"运行 {run_id} 的 config.json schema_version 不受支持: {version!r}，"
            f"期望 {RUN_CONFIG_SNAPSHOT_SCHEMA_VERSION}"
        )
    try:
        test_plan = TestPlan.from_dict(payload["test_plan"])
        run_mapping = RunResourceMapping.from_dict(payload["run_mapping"])
    except (KeyError, TypeError) as error:
        raise ValueError(f"运行 {run_id} 的 config.json 缺少必需的配置段: {error}") from error
    configuration = RunConfiguration(test_plan=test_plan, run_mapping=run_mapping)
    return ConfigurationLoadResult(
        configuration=configuration,
        validation=validate_run_configuration(configuration),
    )


def ensure_run_resumable(manifest: RunManifest) -> None:
    """拒绝把已完成、失败或取消的运行当作可继续测量的运行。

    重跑终态运行必须创建新运行或走显式覆盖流程，因此加载阶段就要失败，
    避免把用户带到连接仪器和接线确认之后才在阶段启动时失败。
    """
    if manifest.status in TERMINAL_RUN_STATUSES:
        raise RunNotResumable(
            f"运行 {manifest.run_id} 已处于终态 {manifest.status.value}，不能继续测量；"
            "请创建新运行"
        )


# 运行契约完整性校验所需的最小仓储能力：
#
# - ``read_config`` / ``read_run_json``：分别读取领域配置快照和任意已登记 JSON 输入；
# - ``load_manifest`` / ``verify_file``：解析硬件快照登记项并校验 SHA-256 与大小。
#
# ``run_repository`` 非空即代表这是一个运行契约运行；此时缺少任一能力都必须明确
# 失败，绝不因为方法缺失而静默跳过快照校验后继续连接仪器。
REQUIRED_RUN_REPOSITORY_METHODS: tuple[str, ...] = (
    "read_config",
    "read_run_json",
    "load_manifest",
    "verify_file",
)


def ensure_run_repository_supports_run_contract(run_repository: Any) -> None:
    """确认运行契约仓储具备快照完整性校验所需的全部能力。

    ``None`` 表示旧兼容流程，直接返回。运行契约仓储缺少必需方法时抛出
    :class:`RunNotResumable`：装配不完整（错误注入、旧替身或未实现的适配器）
    不允许被当成“无需校验”，否则会绕过冻结快照的完整性检查。
    """
    if run_repository is None:
        return
    missing = [
        name
        for name in REQUIRED_RUN_REPOSITORY_METHODS
        if not callable(getattr(run_repository, name, None))
    ]
    if missing:
        raise RunNotResumable(
            "运行契约仓储不完整，缺少 " + ", ".join(missing)
            + "，无法校验冻结快照；请重新创建或重新加载运行"
        )


def load_hardware_config(
    run_repository: RunRepositoryPort, run_id: str
) -> Mapping[str, Any]:
    """读取并校验当前运行已登记的硬件配置快照对象。

    这是硬件装配快照的**统一校验入口**：按 manifest 登记查找
    ``inputs/hardware_config.json``、校验 SHA-256 与大小，再确认根节点是 JSON
    对象。硬件快照不按领域运行配置解析，因此允许拥有与 ``config.json`` 完全不同的
    字段集合；缺少登记、文件被替换或根节点不是对象时抛出 :class:`RunNotResumable`，
    绝不回退到全局配置。
    """
    ensure_run_repository_supports_run_contract(run_repository)
    try:
        payload = run_repository.read_run_json(run_id, HARDWARE_CONFIG_INPUT_PATH)
    except RunNotResumable:
        raise
    except (OSError, ValueError) as error:
        raise RunNotResumable(
            f"运行 {run_id} 的硬件配置快照校验失败: {error}"
        ) from error
    if not isinstance(payload, Mapping):
        raise RunNotResumable(
            f"运行 {run_id} 的硬件配置快照根节点必须是 JSON 对象"
        )
    return payload


def find_run_hardware_config(
    run_repository: RunRepositoryPort, run_id: str
) -> Path:
    """返回当前运行**已登记且校验通过**的硬件配置快照绝对路径。

    运行目录内没有登记硬件配置快照、文件缺失或 SHA-256/大小不一致时抛出
    :class:`RunNotResumable`，保证硬件连接与测量阶段不会回退到全局可变配置。

    本函数只按硬件装配快照的规则做登记与完整性校验（manifest 引用 + SHA-256/
    大小），**不**把硬件配置当作领域运行配置解析。硬件快照的 schema 与领域配置
    快照 ``config.json`` 相互独立、可以各自演化；两者的从属关系由同一份带
    ``run_id`` 的运行 manifest 保证，无需互相套用对方的 schema。
    """
    ensure_run_repository_supports_run_contract(run_repository)
    manifest = run_repository.load_manifest(run_id)
    reference = next(
        (item for item in manifest.inputs if item.path == HARDWARE_CONFIG_INPUT_PATH),
        None,
    )
    if reference is None:
        raise RunNotResumable(
            f"运行 {run_id} 缺少硬件配置快照，不能继续测量；请重新创建运行"
        )
    try:
        return run_repository.verify_file(run_id, reference)
    except (FileNotFoundError, ValueError) as error:
        raise RunNotResumable(
            f"运行 {run_id} 的硬件配置快照校验失败: {error}"
        ) from error


__all__ = [
    "RUN_CONFIG_SNAPSHOT_SCHEMA_VERSION",
    "REQUIRED_RUN_REPOSITORY_METHODS",
    "RunNotResumable",
    "RunIntegrityError",
    "RunInspection",
    "FileCheck",
    "RunSnapshot",
    "configuration_snapshot",
    "create_run_snapshot",
    "ensure_run_repository_supports_run_contract",
    "ensure_run_resumable",
    "find_run_hardware_config",
    "inspect_run",
    "load_hardware_config",
    "load_run_configuration",
]
