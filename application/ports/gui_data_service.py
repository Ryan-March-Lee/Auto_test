"""GUI-facing application contracts and data transfer objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol

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
    def export_json(self, result_id: str, target: str) -> None: ...
    def export_csv(self, result_id: str, target: str) -> None: ...
    def export_pdf(self, result_id: str) -> None: ...


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
