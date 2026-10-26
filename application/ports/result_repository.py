"""Application contract for persisting measurement results.

写入端口只接受正式结果 DTO（``application.dto.measurement_results`` 的三个具体
类型），不再接受 ``Mapping[str, Any]``：DTO 是唯一结果模型，转换与校验发生在 DTO
和基础设施写入实现内部。

``load()`` 只服务于**校准输入**（线损 / 驱动映射阶段文件的原始文档），不用于结果
模型读取；结果展示与导出必须走
``application.dto.measurement_result_from_document()``。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Protocol

from .types import PathLike

if TYPE_CHECKING:  # 避免 application.dto <-> application.ports 的运行时导入环
    from application.dto.measurement_results import MeasurementResult


__all__ = ["MeasurementResultRepository", "PathLike", "SavedMeasurementResult"]


@dataclass(frozen=True)
class SavedMeasurementResult:
    """Locations and identity assigned to a persisted measurement result."""

    archive_path: Path
    run_directory: Path
    run_id: str


class MeasurementResultRepository(Protocol):
    """Minimal persistence boundary required by measurement use cases."""

    def new_run_id(self) -> str:
        """Create a path-safe identifier for a measurement run."""

    def save(
        self,
        result: MeasurementResult,
        *,
        result_type: str,
        run_id: str,
        run_directory: PathLike | None = None,
    ) -> SavedMeasurementResult:
        """Persist a formal result in the run directory and return its authoritative path."""

    def load(self, path: PathLike) -> Mapping[str, Any]:
        """Load a persisted calibration-input document (not a result model)."""
