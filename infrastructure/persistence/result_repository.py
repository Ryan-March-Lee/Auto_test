"""File-backed measurement result repository adapter."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Mapping

from infrastructure.persistence import json_result_repository as result_storage
from application.ports.result_repository import (
    MeasurementResultRepository,
    PathLike,
    SavedMeasurementResult,
)
from infrastructure.persistence.json_encoder import NumpyJSONEncoder


class FileMeasurementResultRepository(MeasurementResultRepository):
    """Adapt the legacy result storage functions to the application port."""

    _FIXED_LEGACY_PATHS = {
        "cable_loss": "cable_loss_results.json",
    }

    _TIMESTAMPED_PREFIXES = {
        "driver_power_mapping": "driver_power_mapping",
        "amplifier_measurement": "amplifier_measurement",
    }

    def __init__(self, *, results_directory: PathLike | None = None):
        self.results_directory = (
            Path(results_directory) if results_directory is not None else None
        )

    def save(
        self,
        result: Mapping[str, Any],
        *,
        result_type: str,
        run_id: str,
        run_directory: PathLike | None = None,
    ) -> SavedMeasurementResult:
        legacy_path = self.legacy_path(result_type)
        effective_run_directory = self._run_directory(run_id, run_directory)
        archive_path, legacy_copy_path = result_storage.save_measurement_result(
            result,
            result_type=result_type,
            legacy_path=legacy_path,
            run_id=run_id,
            run_directory=effective_run_directory,
            encoder=NumpyJSONEncoder,
        )
        return SavedMeasurementResult(
            archive_path=archive_path,
            legacy_copy_path=legacy_copy_path,
            run_directory=archive_path.parent,
            run_id=run_id,
        )

    def new_run_id(self) -> str:
        return result_storage.new_run_id()

    def create_legacy_run_snapshot(
        self, run_id: str, config: Mapping[str, Any], *, status: str = "created"
    ) -> Path:
        return result_storage.write_legacy_run_snapshot(run_id, config, status=status)

    def load(self, path: PathLike) -> Mapping[str, Any]:
        return result_storage.load_json_result(path)

    def legacy_path(self, result_type: str) -> Path:
        if result_type in self._FIXED_LEGACY_PATHS:
            filename = self._FIXED_LEGACY_PATHS[result_type]
            return self._results_directory() / filename

        prefix = self._TIMESTAMPED_PREFIXES.get(result_type)
        if prefix is None:
            raise ValueError(f"不支持的测量结果类型: {result_type}")
        return self._results_directory() / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}.json"

    def latest_path(self, result_type: str) -> Path:
        prefix = self._TIMESTAMPED_PREFIXES.get(result_type)
        if prefix is None:
            return self.legacy_path(result_type)
        files = sorted(
            (
                path
                for path in self._results_directory().glob(f"{prefix}_*.json")
                if not path.name.endswith("_model.json")
            ),
            key=lambda item: item.stat().st_mtime,
        )
        if not files:
            raise FileNotFoundError(f"未找到结果类型为 {result_type} 的结果文件")
        return files[-1]

    def _results_directory(self) -> Path:
        """Resolve the default lazily so path configuration remains patchable."""
        return self.results_directory or Path(result_storage.TEST_RESULTS_DIR)

    def _run_directory(self, run_id: str, run_directory: PathLike | None) -> Path | None:
        if run_directory is not None:
            return Path(run_directory)
        if self.results_directory is None:
            return None

        result_storage.validate_run_id(run_id)
        directory = self.results_directory / run_id
        directory.mkdir(parents=True, exist_ok=False)
        return directory
