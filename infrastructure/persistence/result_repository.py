"""File-backed measurement result repository adapter."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Mapping

from infrastructure.persistence import json_result_repository
from application.dto.measurement_results import MeasurementResult, require_formal_result
from application.ports.result_repository import (
    MeasurementResultRepository,
    PathLike,
    SavedMeasurementResult,
)
from infrastructure.persistence.json_encoder import NumpyJSONEncoder
from infrastructure.filesystem.paths import PathService


# 正式结果在运行目录内使用固定文件名：run_id 已经提供运行级隔离，不再叠加时间戳。
# 历史浏览只读取运行目录结果，由 infrastructure.gui_data_service 的查询适配器负责。
RUN_RESULT_FILENAMES: Mapping[str, str] = {
    "cable_loss": "cable_loss_results.json",
    "driver_power_mapping": "driver_power_mapping_results.json",
    "amplifier_measurement": "amplifier_measurement_results.json",
}


class FileMeasurementResultRepository(MeasurementResultRepository):
    """Write measurement results into the current run directory.

    本仓储是**正式测量输入/结果写入**的边界；历史查看和导出不得复用它，应使用
    :class:`infrastructure.gui_data_service.ResultQueryAdapter`。写入只产生运行目录内
    的权威结果，不再额外写全局兼容副本。
    """

    def __init__(
        self,
        *,
        results_directory: PathLike | None = None,
        paths: PathService | None = None,
    ):
        self.paths = paths
        self.results_directory = (
            Path(results_directory) if results_directory is not None else None
        )

    def save(
        self,
        result: MeasurementResult,
        *,
        result_type: str,
        run_id: str,
        run_directory: PathLike | None = None,
    ) -> SavedMeasurementResult:
        document = require_formal_result(result)
        filename = self._archive_filename(result_type)
        effective_run_directory, created_directory = self._run_directory(
            run_id, run_directory
        )
        save_kwargs = {
            "result_type": result_type,
            "filename": filename,
            "run_id": run_id,
            "run_directory": effective_run_directory,
            "encoder": NumpyJSONEncoder,
        }
        if self.paths is not None:
            save_kwargs["paths"] = self.paths
        try:
            archive_path = json_result_repository.save_measurement_result(
                document, **save_kwargs
            )
        except Exception:
            # 本仓储创建的运行目录属于未完成的提交，整体清理，避免留下空壳。
            if created_directory and effective_run_directory is not None:
                shutil.rmtree(effective_run_directory, ignore_errors=True)
            raise
        return SavedMeasurementResult(
            archive_path=archive_path,
            run_directory=archive_path.parent,
            run_id=run_id,
        )

    def new_run_id(self) -> str:
        return json_result_repository.new_run_id()

    def load(self, path: PathLike) -> Mapping[str, Any]:
        return json_result_repository.load_json_result(path)

    def _archive_filename(self, result_type: str) -> str:
        """返回运行目录内的正式结果文件名；未知类型在写盘前拒绝。"""
        try:
            return RUN_RESULT_FILENAMES[result_type]
        except KeyError as error:
            raise ValueError(f"不支持的测量结果类型: {result_type}") from error

    def _run_directory(
        self, run_id: str, run_directory: PathLike | None
    ) -> tuple[Path | None, bool]:
        """返回 (运行目录, 是否由本次调用创建)。"""
        if run_directory is not None:
            return Path(run_directory), False
        if self.results_directory is None:
            if self.paths is None:
                return None, False
            json_result_repository.validate_run_id(run_id)
            directory = self.paths.test_results_dir / run_id
            directory.mkdir(parents=True, exist_ok=False)
            return directory, True

        json_result_repository.validate_run_id(run_id)
        directory = self.results_directory / run_id
        directory.mkdir(parents=True, exist_ok=False)
        return directory, True
