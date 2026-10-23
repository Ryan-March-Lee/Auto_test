"""GUI-facing application contracts and data transfer objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class ResultFileDto:
    result_id: str
    filename: str
    file_type: str
    modified_at: datetime


@dataclass(frozen=True)
class LoadedResultDto:
    result_id: str
    filename: str
    data: Mapping[str, Any]


class ConfigurationPort(Protocol):
    def load(self) -> Mapping[str, Any]: ...
    def save(self, configuration: Mapping[str, Any]) -> None: ...


class ResultQueryPort(Protocol):
    """历史结果查看/导出的只读查询契约。

    该契约只服务于历史浏览，不提供正式测量输入；正式测量输入必须来自当前
    运行 manifest 登记并校验的阶段结果。查询对象以 ``result_id`` 标识结果，路径
    解析和目录边界校验由基础设施实现负责。
    """

    def list_results(self) -> list[ResultFileDto]: ...
    def load(self, result_id: str) -> LoadedResultDto: ...
    def read_bytes(self, result_id: str) -> bytes: ...
    def choose_and_load(self) -> LoadedResultDto | None: ...
    def latest_legacy_result(self, result_type: str) -> LoadedResultDto:
        """按类型读取最新全局兼容副本，仅用于历史查看。"""


class LegacyResultQueryPort(Protocol):
    """仅用于兼容期的历史查询别名。

    ``latest()`` 已由 :meth:`ResultQueryPort.latest_legacy_result` 取代，仓库内
    生产代码不得再调用；保留本协议只是为了记录外部脚本仍可能依赖的旧入口，
    删除条件见重构指南步骤 E。
    """

    def latest(self, result_type: str) -> LoadedResultDto: ...


class ResultExportPort(Protocol):
    def export_json(self, result_id: str, target: str) -> None: ...
    def export_csv(self, result_id: str, target: str) -> None: ...
    def export_pdf(self, result_id: str) -> None: ...


class ReportPort(Protocol):
    def create_summary_report(
        self, result: Mapping[str, Any], configuration: Mapping[str, Any], filename: str
    ) -> None: ...


class GuiDataService(Protocol):
    configuration: ConfigurationPort
    results: ResultQueryPort
    exports: ResultExportPort
    reports: ReportPort
