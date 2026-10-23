"""Application boundary for preparing a run before hardware access."""

from __future__ import annotations

import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from domain.configuration.models import RunConfiguration
from domain.models import RunContext
from domain.run.models import generate_run_id
from application.ports.config_repository import ConfigurationLoadResult
from application.ports.run_repository import RunRepositoryPort
from application.run_service import (
    RunSnapshot,
    create_run_snapshot,
    ensure_run_resumable,
    find_run_hardware_config,
    load_run_configuration,
)
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence.json_result_repository import write_run_snapshot
from infrastructure.persistence.run_repository import RunRepository


@dataclass(frozen=True)
class PreparedRun:
    """Immutable data passed from configuration loading to an instrument session.

    字段语义必须保持明确：

    - ``config_path``：创建运行时的源 GUI 配置路径，仅用于兼容追踪，**不能**作为
      硬件连接或测量阶段的输入来源。
    - ``hardware_config_path``：运行目录内冻结的硬件装配配置，硬件连接与测量
      阶段必须使用它。
    - ``run_repository``：运行契约仓储；旧兼容快照为 ``None``。
    """

    configuration: RunConfiguration
    context: RunContext
    run_directory: Path
    config_path: Path | None = None
    # 运行契约运行会携带创建它的仓储；旧兼容快照为 None，表示还没有 manifest 契约。
    run_repository: RunRepositoryPort | None = None
    # 运行目录内冻结的硬件装配配置（``inputs/hardware_config.json``）。旧兼容
    # 快照没有该文件，因此为 None。
    hardware_config_path: Path | None = None
    # 上述硬件装配配置的内容副本，供 GUI 展示运行配置；不连接硬件、不参与测量。
    hardware_config: Mapping[str, Any] | None = None

    @property
    def run_id(self) -> str:
        """Expose the prepared identity without making callers unpack context."""
        return self.context.run_id

    @property
    def has_run_contract(self) -> bool:
        """是否携带 ``runs/<run_id>/`` 运行目录契约（可用于阶段结果记录）。"""
        return self.run_repository is not None

    @property
    def frozen_config_path(self) -> Path | None:
        """运行目录内的领域配置快照 ``config.json``；旧兼容快照为 ``None``。"""
        if not self.has_run_contract:
            return None
        return Path(self.run_directory) / "config.json"

    def hardware_connection_path(self) -> Path:
        """硬件连接与测量应使用的配置路径。

        运行契约运行固定使用运行目录内冻结的硬件配置；旧兼容快照没有运行目录，
        才回退到创建时的源配置路径。
        """
        if self.hardware_config_path is not None:
            return Path(self.hardware_config_path)
        if self.config_path is None:
            raise ValueError("运行没有可用的硬件配置路径")
        return Path(self.config_path)


def prepare_run(
    loaded: ConfigurationLoadResult,
    *,
    run_id: str,
    software_version: str = "unknown",
    git_commit: str | None = None,
    config_path: Path | None = None,
    paths: PathService | None = None,
) -> PreparedRun:
    """Validate and snapshot a run without importing or opening an instrument.

    This is the legacy **compatibility** preflight boundary: it writes the
    compatibility snapshot (``test_plan_snapshot.json`` /
    ``run_mapping_snapshot.json`` / ``run_metadata.json``) so the existing
    measurement result paths stay unchanged.  It does **not** create the
    ``runs/<run_id>/`` run contract and therefore does not freeze the
    hardware configuration; such a snapshot is **not** reopenable via
    :func:`load_prepared_run`.

    New callers that need a resumable ``runs/<run_id>/`` run must use
    :func:`create_prepared_run` with an explicit ``hardware_config`` instead.
    """
    if not loaded.valid:
        details = "; ".join(f"{issue.path}: {issue.message}" for issue in loaded.validation.errors)
        raise ValueError(f"运行配置校验失败: {details}")
    configuration = loaded.configuration
    context = RunContext.from_resource_mapping(
        configuration.run_mapping.to_dict(),
        run_id=run_id,
        software_version=software_version,
    )
    run_directory = write_run_snapshot(
        run_id,
        configuration.test_plan.to_dict(),
        {**configuration.run_mapping.to_dict(), **context.to_dict()},
        software_version=software_version,
        git_commit=git_commit,
        start_time=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        status="created",
        error=None,
        paths=paths,
    )
    return PreparedRun(configuration, context, Path(run_directory), config_path)


def create_prepared_run(
    loaded: ConfigurationLoadResult,
    *,
    run_id: str | None = None,
    software_version: str = "unknown",
    git_commit: str | None = None,
    config_path: Path | None = None,
    paths: PathService | None = None,
    run_repository: RunRepositoryPort | None = None,
    hardware_config: Mapping[str, Any],
) -> PreparedRun:
    """Create an immutable run snapshot (``runs/<run_id>/``).

    This is the step-3 "创建运行" entry point: the configuration is validated as
    a complete run, then frozen through the run repository port.  ``hardware_config``
    is **required by the run contract** and freezes the hardware assembly
    configuration inside the run directory (``inputs/hardware_config.json``), so
    the created run is always resumable via :func:`load_prepared_run` and later
    stages never read the global mutable ``config.json``.  Passing ``None``
    fails before any run directory is created.
    """
    if hardware_config is None:
        raise ValueError(
            "运行契约必须包含硬件配置快照：create_prepared_run 的 "
            "hardware_config 不能为 None，缺少硬件配置的运行无法恢复"
        )
    repository = run_repository if run_repository is not None else RunRepository(paths=paths)
    snapshot: RunSnapshot = create_run_snapshot(
        loaded,
        run_repository=repository,
        run_id=run_id or generate_run_id(),
        software_version=software_version,
        git_commit=git_commit,
        hardware_config=hardware_config,
    )
    context = RunContext.from_resource_mapping(
        loaded.configuration.run_mapping.to_dict(),
        run_id=snapshot.run_id,
        software_version=software_version,
    )
    return PreparedRun(
        loaded.configuration,
        context,
        Path(snapshot.run_directory),
        config_path,
        repository,
        snapshot.hardware_config_path,
        hardware_config,
    )


def load_prepared_run(
    run_id: str,
    *,
    config_path: Path | None = None,
    paths: PathService | None = None,
    run_repository: RunRepositoryPort | None = None,
) -> PreparedRun:
    """Reconstruct a :class:`PreparedRun` from an existing ``runs/<run_id>/`` run.

    This is the step-7 "reopen by run_id" entry point.  It refuses to reopen a
    terminal run and requires the frozen hardware configuration snapshot to be
    present and checksum-verified, so a reopened run always assembles hardware
    from its own frozen configuration rather than the global ``config.json``.
    """
    repository = run_repository if run_repository is not None else RunRepository(paths=paths)
    if not repository.run_exists(run_id):
        raise FileNotFoundError(f"运行不存在或结构不完整: {run_id}")
    ensure_run_resumable(repository.load_manifest(run_id))
    hardware_config_path = find_run_hardware_config(repository, run_id)
    loaded = load_run_configuration(repository, run_id)
    if not loaded.valid:
        details = "; ".join(f"{issue.path}: {issue.message}" for issue in loaded.validation.errors)
        raise ValueError(f"运行 {run_id} 的配置快照校验失败: {details}")
    configuration = loaded.configuration
    context = RunContext.from_resource_mapping(
        configuration.run_mapping.to_dict(),
        run_id=run_id,
        software_version=environment_version(),
    )
    layout = repository.layout(run_id)
    return PreparedRun(
        configuration,
        context,
        Path(layout.run_directory),
        config_path,
        repository,
        hardware_config_path,
        _read_json_object(hardware_config_path),
    )


def _read_json_object(path: Path) -> Mapping[str, Any]:
    """读取运行目录内已校验的硬件配置快照内容。"""
    from infrastructure.filesystem.atomic_json import read_json

    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"硬件配置快照根节点必须是对象: {path}")
    return payload


def environment_version() -> str:
    """Return a stable, dependency-free software identity for run metadata."""
    return f"python-{platform.python_version()}"
