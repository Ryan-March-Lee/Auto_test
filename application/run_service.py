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

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from application.ports.config_repository import ConfigurationLoadResult
from application.ports.run_repository import RunRepositoryPort
from domain.configuration.models import (
    RunConfiguration,
    RunResourceMapping,
    TestPlan,
    validate_run_configuration,
)
from domain.configuration.types import ConfigValidationResult
from domain.run.models import RunManifest, utc_now_iso


RUN_CONFIG_SNAPSHOT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RunSnapshot:
    """一次已创建运行的标识、目录和初始配置快照引用。"""

    run_id: str
    run_directory: Path
    config_path: Path
    manifest: RunManifest


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
    """
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
    return RunSnapshot(
        run_id=manifest.run_id,
        run_directory=Path(layout.run_directory),
        config_path=Path(layout.config_path),
        manifest=manifest,
    )


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


__all__ = [
    "RUN_CONFIG_SNAPSHOT_SCHEMA_VERSION",
    "RunSnapshot",
    "configuration_snapshot",
    "create_run_snapshot",
    "load_run_configuration",
]
