"""应用层配置加载服务。"""

from __future__ import annotations

from application.ports.config_repository import ConfigurationLoadResult, ConfigurationRepository, PathLike


def load_for_run(
    repository: ConfigurationRepository,
    path: PathLike,
    mapping_path: PathLike | None = None,
) -> ConfigurationLoadResult:
    """通过仓储加载正式配置，供“校验并创建运行”消费。

    这里不再附加任何运行策略门禁：创建运行只做**参数校验**（完整运行校验由
    :func:`application.run_service.create_run_snapshot` 执行）。现场接线改由各测量
    阶段开始前的连接图对话框确认，不再作为配置字段在创建运行前预置。
    """
    return repository.load(path, mapping_path)


__all__ = ["load_for_run"]
