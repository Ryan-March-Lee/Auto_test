"""Composition root for the Qt presentation process."""

from pathlib import Path

from application.archival_service import ResultArchivalService
from application.gui_data_service import GuiDataService
from application.run_history_service import RunHistoryService
from infrastructure.filesystem.paths import DEFAULT_PATHS, PathService
from infrastructure.gui_data_service import (
    JsonConfigurationAdapter,
    ReportAdapter,
    ResultExportAdapter,
    ResultQueryAdapter,
)
from infrastructure.persistence.artifact_generator import ArchiveArtifactGenerator
from infrastructure.persistence.run_directory import default_runs_root
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
from infrastructure.persistence.run_repository import RunRepository


def build_gui_data_service(
    paths: PathService | None = None, *, config_path: Path | None = None
) -> GuiDataService:
    paths = paths or DEFAULT_PATHS
    # 历史查看/导出只注入运行目录历史：旧全局兼容副本已退役，正式测量结果仓储
    # 不进入 GUI 数据服务，从依赖关系上保证历史数据不会成为新运行的输入。
    runs = RunRepository(paths=paths)
    run_history = FileRunHistoryRepository(runs=runs)
    query = ResultQueryAdapter(
        paths,
        run_history=run_history,
        runs_root=default_runs_root(paths=paths),
    )
    # 历史管理的“重新生成失败产物”复用同一个归档服务与产物生成端口：产物写回原运行
    # 目录的受控路径，页面既不需要拼接路径，也不需要另写一份导出规则。
    archival = ResultArchivalService(
        runs=runs,
        generator=ArchiveArtifactGenerator(runs),
    )
    return GuiDataService(
        JsonConfigurationAdapter(config_path or paths),
        query,
        ResultExportAdapter(query, paths),
        ReportAdapter(paths),
        run_history=RunHistoryService(statuses=run_history, archival=archival),
    )
