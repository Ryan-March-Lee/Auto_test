"""Read persisted measurement inputs at the application composition boundary."""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Mapping

from application.ports.result_input_reader import ResultInputReader as ResultInputReaderPort
from application.ports.result_repository import MeasurementResultRepository, PathLike


class ResultInputReader(ResultInputReaderPort):
    """Resolve and load calibration inputs used by measurement workflows.

    The reader owns compatibility filenames for legacy reads.  Current-run
    measurement inputs must be supplied explicitly; the application boundary
    never discovers a result by modification time.
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
        if path is not None:
            return self._explicit_path(path)
        warnings.warn(
            "未显式提供 driver_mapping_path 的兼容读取已弃用；"
            "新运行必须使用当前 run_id manifest 登记的输入",
            DeprecationWarning,
            stacklevel=2,
        )
        return self.repository.latest_path("driver_power_mapping")

    def read_cable_loss(self, path: PathLike | None = None) -> Mapping[str, Any]:
        return self.repository.load(self.cable_loss_path(path))

    def read_driver_mapping(self, path: PathLike | None = None) -> Mapping[str, Any]:
        result = self.repository.load(self.driver_mapping_path(path))
        try:
            return result["power_mapping"]
        except KeyError as exc:
            raise ValueError("驱动映射结果缺少 power_mapping 字段") from exc
