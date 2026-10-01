"""Read persisted measurement inputs at the application composition boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from application.ports.result_input_reader import ResultInputReader as ResultInputReaderPort
from application.ports.result_repository import MeasurementResultRepository, PathLike


class ResultInputReader(ResultInputReaderPort):
    """Resolve and load calibration inputs used by measurement workflows.

    The reader owns compatibility filenames and latest-result discovery.  A
    Caller-provided paths are resolved consistently with the legacy path
    behavior, and explicit inputs never cause a results-directory scan.
    """

    def __init__(self, repository: MeasurementResultRepository):
        self.repository = repository

    @staticmethod
    def _explicit_path(path: PathLike) -> Path:
        """Preserve the legacy behavior of resolving relative input paths."""
        return Path(path).expanduser().resolve()

    def cable_loss_path(self, path: PathLike | None = None) -> Path:
        return self._explicit_path(path) if path is not None else self.repository.legacy_path("cable_loss")

    def driver_mapping_path(self, path: PathLike | None = None) -> Path:
        return (
            self._explicit_path(path)
            if path is not None
            else self.repository.latest_path("driver_power_mapping")
        )

    def read_cable_loss(self, path: PathLike | None = None) -> Mapping[str, Any]:
        return self.repository.load(self.cable_loss_path(path))

    def read_driver_mapping(self, path: PathLike | None = None) -> Mapping[str, Any]:
        result = self.repository.load(self.driver_mapping_path(path))
        try:
            return result["power_mapping"]
        except KeyError as exc:
            raise ValueError("驱动映射结果缺少 power_mapping 字段") from exc
