"""Application boundary for preparing a run before hardware access."""

from __future__ import annotations

import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from domain.configuration.models import RunConfiguration
from domain.models import RunContext
from domain.run.models import generate_run_id
from application.ports.config_repository import ConfigurationLoadResult
from application.ports.run_repository import RunRepositoryPort
from application.run_service import RunSnapshot, create_run_snapshot
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence.json_result_repository import write_run_snapshot
from infrastructure.persistence.run_repository import RunRepository


@dataclass(frozen=True)
class PreparedRun:
    """Immutable data passed from configuration loading to an instrument session."""

    configuration: RunConfiguration
    context: RunContext
    run_directory: Path
    config_path: Path | None = None
    # 运行契约运行会携带创建它的仓储；旧兼容快照为 None，表示还没有 manifest 契约。
    run_repository: RunRepositoryPort | None = None

    @property
    def run_id(self) -> str:
        """Expose the prepared identity without making callers unpack context."""
        return self.context.run_id

    @property
    def has_run_contract(self) -> bool:
        """是否携带 ``runs/<run_id>/`` 运行目录契约（可用于阶段结果记录）。"""
        return self.run_repository is not None


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

    This is the current measurement preflight boundary.  It keeps writing the
    compatibility snapshot (``test_plan_snapshot.json`` /
    ``run_mapping_snapshot.json`` / ``run_metadata.json``) so the existing
    measurement result paths stay unchanged until steps 4-6 migrate the stages
    to the run-directory contract.

    New callers that need a frozen ``runs/<run_id>/config.json`` run must use
    :func:`create_prepared_run` instead.
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
) -> PreparedRun:
    """Create an immutable run snapshot (``runs/<run_id>/config.json``).

    This is the step-3 "创建运行" entry point: the configuration is validated as
    a complete run, then frozen through the run repository port.  The returned
    ``PreparedRun`` carries the new run directory for the stages that migrate in
    steps 4-6; the current measurement flow still uses :func:`prepare_run`.
    """
    repository = run_repository if run_repository is not None else RunRepository(paths=paths)
    snapshot: RunSnapshot = create_run_snapshot(
        loaded,
        run_repository=repository,
        run_id=run_id or generate_run_id(),
        software_version=software_version,
        git_commit=git_commit,
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
    )


def environment_version() -> str:
    """Return a stable, dependency-free software identity for run metadata."""
    return f"python-{platform.python_version()}"
