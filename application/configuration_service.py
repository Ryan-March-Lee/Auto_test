"""应用层配置加载与运行前预检服务。"""

from __future__ import annotations

from dataclasses import replace

from application.ports.config_repository import ConfigurationLoadResult, ConfigurationRepository, PathLike
from domain.configuration.types import ConfigIssue, ConfigValidationResult


def load_for_run(
    repository: ConfigurationRepository,
    path: PathLike,
    mapping_path: PathLike | None = None,
) -> ConfigurationLoadResult:
    """通过仓储加载配置，并执行运行前接线确认门禁。

    文件格式和 legacy 转换由仓储负责；``wiring.confirmed`` 是运行策略，
    因此在应用层统一执行。
    """
    loaded = repository.load(path, mapping_path)
    if not loaded.valid or loaded.configuration.run_mapping.wiring_confirmed:
        return loaded

    issue = ConfigIssue("error", "wiring.confirmed", "运行前必须确认现场接线")
    return replace(
        loaded,
        validation=ConfigValidationResult(
            errors=loaded.validation.errors + [issue],
            warnings=loaded.validation.warnings,
        ),
    )


__all__ = ["load_for_run"]
