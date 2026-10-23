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
    TERMINAL_RUN_STATUSES,
    RunManifest,
    utc_now_iso,
)


RUN_CONFIG_SNAPSHOT_SCHEMA_VERSION = 1

# 挂到项目专用 ``pa_auto_test`` logger 树下（与 infrastructure.logging.get_logger
# 的命名规则一致），清理失败的 ERROR 日志才能进入 ``setup_logging`` 的日志文件；
# 不直接导入 infrastructure，保持应用层依赖边界。
logger = logging.getLogger("pa_auto_test." + __name__)


class RunNotResumable(ValueError):
    """运行不可继续测量（终态、缺硬件配置快照或快照校验失败）。

    调用方应据此提示用户创建新运行，而不是回退到全局配置或其它运行目录。
    """


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
    "RunNotResumable",
    "RunSnapshot",
    "configuration_snapshot",
    "create_run_snapshot",
    "ensure_run_resumable",
    "find_run_hardware_config",
    "load_run_configuration",
]
