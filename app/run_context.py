"""Application boundary for preparing a run before hardware access."""

from __future__ import annotations

import platform
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from config_models import RunConfiguration
from domain.models import RunContext
from persistence.config_repository import ConfigurationLoadResult
from result_storage import write_run_snapshot


@dataclass(frozen=True)
class PreparedRun:
    """Immutable data passed from configuration loading to an instrument session."""

    configuration: RunConfiguration
    context: RunContext
    run_directory: Any


def prepare_run(
    loaded: ConfigurationLoadResult,
    *,
    run_id: str,
    software_version: str = "unknown",
    git_commit: str | None = None,
) -> PreparedRun:
    """Validate and snapshot a run without importing or opening an instrument.

    This is the required preflight boundary.  Callers must pass the returned
    ``PreparedRun`` into their hardware factory; a failed snapshot raises before
    any instrument object is created.
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
    )
    return PreparedRun(configuration, context, run_directory)


def environment_version() -> str:
    """Return a stable, dependency-free software identity for run metadata."""
    return f"python-{platform.python_version()}"
