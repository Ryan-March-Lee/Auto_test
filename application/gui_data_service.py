"""Application facade for GUI data workflows.

The facade only coordinates ports. Storage formats, paths and report file
lifecycle belong to infrastructure adapters.
"""

from __future__ import annotations

from application.ports.gui_data_service import (
    ConfigurationPort,
    ReportPort,
    ResultExportPort,
    ResultQueryPort,
    GuiDataService as GuiDataServicePort,
)


class GuiDataService(GuiDataServicePort):
    def __init__(
        self,
        configuration: ConfigurationPort,
        results: ResultQueryPort,
        exports: ResultExportPort,
        reports: ReportPort,
    ):
        self.configuration = configuration
        self.results = results
        self.exports = exports
        self.reports = reports
