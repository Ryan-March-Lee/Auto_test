"""GUI-facing application contracts and data transfer objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol

from application.dto.export_requests import ExportOutcome, ExportRequest
from application.dto.measurement_results import MeasurementResult


# 历史结果只来自正式运行目录；旧全局兼容副本已不再参与历史查看。
RESULT_SOURCE_RUN = "run"


@dataclass(frozen=True)
class ResultFileDto:
    result_id: str
    filename: str
    file_type: str
    modified_at: datetime
    source: str = RESULT_SOURCE_RUN
    run_id: str | None = None


@dataclass(frozen=True)
class LoadedResultDto:
    """一条已解析的正式历史结果。

    ``result`` 已经是 ``application.dto`` 的正式结果对象：调用方拿到它就可以直接
    展示、绘图或导出，不需要也不允许再把结果当成 ``Mapping[str, Any]`` 读取。
    原始 JSON 只通过 ``ResultQueryPort.read_bytes()`` 逐字节导出。
    """

    result_id: str
    filename: str
    result: MeasurementResult


class ConfigurationPort(Protocol):
    def load(self) -> Mapping[str, Any]: ...
    def save(self, configuration: Mapping[str, Any]) -> None: ...


class ResultQueryPort(Protocol):
    """历史结果查看/导出的只读查询契约。

    该契约只服务于历史浏览，不提供正式测量输入；正式测量输入必须来自当前
    运行 manifest 登记并校验的阶段结果。查询对象以 ``result_id`` 标识结果，路径
    解析和目录边界校验由基础设施实现负责，数据源是正式运行目录结果。

    查询结果一律是已解析的正式结果 DTO：非正式 schema、类型与 manifest 登记不一致
    的文档不会出现在列表中，也不会被 ``load()`` 返回。
    """

    def list_results(self) -> list[ResultFileDto]: ...
    def load(self, result_id: str) -> LoadedResultDto: ...
    def read_bytes(self, result_id: str) -> bytes: ...
    def choose_and_load(self) -> LoadedResultDto | None: ...
    def latest_result(self, result_type: str) -> LoadedResultDto:
        """返回最新的可校验运行目录结果；没有结果时抛出 ``FileNotFoundError``。"""


class ResultExportPort(Protocol):
    """导出实现端口：只接受结果标识，输出位置由实现决定或由调用方给定。

    ``export_report`` 必须返回生成报告的实际位置（非空字符串）；返回 ``None`` 或其它
    类型会被应用用例判为失败，避免把“没有产物”报告成成功。
    """

    def export_json(self, result_id: str, target: str) -> None: ...
    def export_csv(self, result_id: str, target: str) -> None: ...
    def export_report(self, result_id: str) -> str:
        """生成测试汇总报告并返回**实际**产物位置。"""


class ResultExportServicePort(Protocol):
    """导出用例入口：页面只构造 ``ExportRequest``，由应用层执行。"""

    def execute(self, request: ExportRequest) -> ExportOutcome:
        """执行一次导出请求，返回带用户可见文案的结果。"""


class ReportPort(Protocol):
    def create_summary_report(
        self,
        result: MeasurementResult,
        configuration: Mapping[str, Any],
        filename: str,
    ) -> None: ...


class GuiDataService(Protocol):
    configuration: ConfigurationPort
    results: ResultQueryPort
    exports: ResultExportPort
    reports: ReportPort
    export_requests: ResultExportServicePort
