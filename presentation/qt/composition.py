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
from infrastructure.persistence.result_repository import FileMeasurementResultRepository


def build_gui_data_service(
    paths: PathService | None = None, *, config_path: Path | None = None
) -> GuiDataService:
    paths = paths or DEFAULT_PATHS
    repository = FileMeasurementResultRepository(paths=paths)
    query = ResultQueryAdapter(paths, repository)
    return GuiDataService(
        JsonConfigurationAdapter(config_path or paths),
        query,
        ResultExportAdapter(query, paths),
        ReportAdapter(paths),
    )
