"""Composition root for the Qt presentation process."""

from pathlib import Path

from application.gui_data_service import GuiDataService
from infrastructure.filesystem.paths import DEFAULT_PATHS, PathService
from infrastructure.gui_data_service import (
    JsonConfigurationAdapter,
    ReportAdapter,
    ResultExportAdapter,
    ResultQueryAdapter,
)
from infrastructure.persistence.run_directory import default_runs_root
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
from infrastructure.persistence.run_repository import RunRepository


def build_gui_data_service(
    paths: PathService | None = None, *, config_path: Path | None = None
) -> GuiDataService:
    paths = paths or DEFAULT_PATHS
    # 历史查看/导出只注入运行目录历史：旧全局兼容副本已退役，正式测量结果仓储
    # 不进入 GUI 数据服务，从依赖关系上保证历史数据不会成为新运行的输入。
    run_history = FileRunHistoryRepository(runs=RunRepository(paths=paths))
    query = ResultQueryAdapter(
        paths,
        run_history=run_history,
        runs_root=default_runs_root(paths=paths),
    )
    return GuiDataService(
        JsonConfigurationAdapter(config_path or paths),
        query,
        ResultExportAdapter(query, paths),
        ReportAdapter(paths),
    )
