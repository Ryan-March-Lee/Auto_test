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
    def list_results(self) -> list[ResultFileDto]: ...
    def load(self, result_id: str) -> LoadedResultDto: ...
    def choose_and_load(self) -> LoadedResultDto | None: ...
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
