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
from infrastructure.persistence.result_history_repository import FileResultHistoryRepository
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
from infrastructure.persistence.run_repository import RunRepository


def build_gui_data_service(
    paths: PathService | None = None, *, config_path: Path | None = None
) -> GuiDataService:
    paths = paths or DEFAULT_PATHS
    # 历史查看/导出只注入只读历史仓储：运行目录历史为首选来源，旧的全局兼容
    # 副本保持只读可查看。正式测量结果仓储不进入 GUI 数据服务，从依赖关系上
    # 保证历史数据不会成为新运行的输入。
    history = FileResultHistoryRepository(paths=paths)
    run_history = FileRunHistoryRepository(runs=RunRepository(paths=paths))
    query = ResultQueryAdapter(paths, history, run_history=run_history)
    return GuiDataService(
        JsonConfigurationAdapter(config_path or paths),
        query,
        ResultExportAdapter(query, paths),
        ReportAdapter(paths),
    )
