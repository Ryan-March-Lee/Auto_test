"""Application facade for GUI data workflows.

The facade only coordinates ports. Storage formats, paths and report file
lifecycle belong to infrastructure adapters.

导出在该门面上只有一个入口：``export_requests``（
:class:`application.export_service.ResultExportUseCase`）。页面用
:class:`application.dto.export_requests.ExportRequest` 描述意图，拿到结构化的
:class:`application.dto.export_requests.ExportOutcome` 后自己生成用户文案。
"""

from __future__ import annotations

from application.export_service import ResultExportUseCase
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
        # 导出用例只依赖历史查询与导出两个端口：页面不直接触碰导出实现。
        self.export_requests = ResultExportUseCase(history=results, exports=exports)
