"""历史结果导出的应用用例。

唯一职责：把一份 :class:`application.dto.export_requests.ExportRequest` 变成一次导出
端口调用，并在执行前完成页面无权完成的判定。

边界：

- 导出只能针对**历史查询端口返回的结果标识**：用例执行前用
  ``ResultQueryPort.list_results()`` 核对标识，页面无法凭任意路径触发导出。
- 结果一律经由历史查询端口读取为正式 DTO，格式适用性（例如报告只支持主功放结果）
  基于 DTO 的类型判断，页面不再比较展示标签。
- 用例只返回结构化的 :class:`ExportOutcome`（来源文件名 + 实际产物位置）；用户可见
  文案属于界面层，应用层不拼接本地化提示。
- 导出端口的返回值是契约的一部分：报告导出必须返回**非空**的产物位置，返回其它类型
  或空值时视为导出失败，不能报告成成功。
"""

from __future__ import annotations

from application.dto.export_requests import ExportFormat, ExportOutcome, ExportRequest
from application.ports.gui_data_service import (
    LoadedResultDto,
    ResultExportPort,
    ResultQueryPort,
)


class ExportRequestError(ValueError):
    """导出请求无法执行：结果不在历史列表中，或格式不适用于该结果。"""


class ResultExportUseCase:
    """执行一次导出请求的应用用例。"""

    def __init__(self, *, history: ResultQueryPort, exports: ResultExportPort):
        self.history = history
        self.exports = exports

    def execute(self, request: ExportRequest) -> ExportOutcome:
        """执行导出；请求非法或无法执行时抛出 :class:`ExportRequestError`。"""
        if not isinstance(request, ExportRequest):
            raise ExportRequestError("导出入口只接受 ExportRequest")
        loaded = self._require_history_result(request.result_id)
        if request.format is ExportFormat.JSON:
            self.exports.export_json(request.result_id, request.target_path)
            return self._outcome(request, loaded, request.target_path)
        if request.format is ExportFormat.CSV:
            self._require_amplifier(loaded, request.format)
            self.exports.export_csv(request.result_id, request.target_path)
            return self._outcome(request, loaded, request.target_path)
        self._require_amplifier(loaded, request.format)
        return self._outcome(request, loaded, self._export_report(request.result_id))

    # -- 内部工具 -----------------------------------------------------------

    def _export_report(self, result_id: str) -> str:
        """调用报告导出端口，并校验它返回了真实的产物位置。"""
        report_path = self.exports.export_report(result_id)
        if not isinstance(report_path, str) or not report_path.strip():
            raise ExportRequestError(
                "导出端口没有返回报告位置，无法确认报告产物: "
                f"{type(report_path).__name__}"
            )
        return report_path

    def _require_history_result(self, result_id: str) -> LoadedResultDto:
        """确认结果来自历史列表，并返回已解析的正式 DTO。"""
        if not self._is_registered(result_id):
            raise ExportRequestError(f"结果不在历史列表中，无法导出: {result_id}")
        try:
            return self.history.load(result_id)
        except (OSError, ValueError) as error:
            raise ExportRequestError(f"无法读取历史结果 {result_id}: {error}") from error

    def _is_registered(self, result_id: str) -> bool:
        try:
            rows = self.history.list_results()
        except (OSError, ValueError) as error:
            raise ExportRequestError(f"无法读取历史结果列表: {error}") from error
        return any(row.result_id == result_id for row in rows)

    @staticmethod
    def _require_amplifier(loaded: LoadedResultDto, export_format: ExportFormat) -> None:
        if not loaded.result.is_amplifier:
            raise ExportRequestError(
                f"只有功放测试结果支持 {export_format.label} 导出"
            )

    @staticmethod
    def _outcome(
        request: ExportRequest, loaded: LoadedResultDto, artifact_path: str
    ) -> ExportOutcome:
        return ExportOutcome(
            result_id=request.result_id,
            format=request.format,
            filename=loaded.filename,
            artifact_path=artifact_path,
        )


__all__ = ["ExportRequestError", "ResultExportUseCase"]
