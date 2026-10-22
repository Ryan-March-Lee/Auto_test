"""File-backed adapters used by the GUI composition root."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from application.inputs.result_reading import build_report_model
from application.ports.gui_data_service import LoadedResultDto, ResultFileDto
from analysis.visualization import DataVisualization
from infrastructure.config.json_io import load_config_file
from infrastructure.filesystem.paths import (
    PathService,
    ensure_directory,
    unique_directory_name,
)
from infrastructure.persistence.result_repository import FileMeasurementResultRepository
from PySide6.QtWidgets import QFileDialog


class JsonConfigurationAdapter:
    def __init__(self, path: PathService | Path):
        self.path = path.config_file if isinstance(path, PathService) else Path(path)

    def load(self):
        return load_config_file(self.path)

    def save(self, configuration: Mapping[str, Any]) -> None:
        ensure_directory(self.path.parent)
        with self.path.open("w", encoding="utf-8") as stream:
            json.dump(configuration, stream, indent=4, ensure_ascii=False)


class ResultQueryAdapter:
    _TYPES = (
        ("cable_loss", "cable_loss_results.json", "线损数据"),
        ("driver_power_mapping", "driver_power_mapping_*.json", "驱动映射"),
        ("amplifier_measurement", "amplifier_measurement_*.json", "功放测试"),
    )

    def __init__(self, paths: PathService, repository: FileMeasurementResultRepository):
        self.paths = paths
        self.repository = repository

    def list_results(self) -> list[ResultFileDto]:
        rows = []
        for result_type, pattern, label in self._TYPES:
            for path in self.paths.test_results_dir.glob(pattern):
                if path.is_file():
                    rows.append(ResultFileDto(
                        result_id=path.name,
                        filename=path.name,
                        file_type=label,
                        modified_at=datetime.fromtimestamp(path.stat().st_mtime),
                    ))
        return sorted(rows, key=lambda item: item.modified_at, reverse=True)

    def _path(self, result_id: str) -> Path:
        if Path(result_id).name != result_id or Path(result_id).suffix != ".json":
            raise ValueError("无效的结果标识")
        path = self.paths.test_results_dir / result_id
        path.resolve().relative_to(self.paths.test_results_dir.resolve())
        if not path.is_file():
            raise FileNotFoundError(result_id)
        return path

    def load(self, result_id: str) -> LoadedResultDto:
        path = self._path(result_id)
        return LoadedResultDto(result_id, path.name, self.repository.load(path))

    def choose_and_load(self) -> LoadedResultDto | None:
        selected, _ = QFileDialog.getOpenFileName(
            None, "选择测试数据文件", str(self.paths.test_results_dir),
            "JSON files (*.json);;All files (*.*)",
        )
        if not selected:
            return None
        path = Path(selected).expanduser().resolve()
        path.relative_to(self.paths.test_results_dir.resolve())
        if not path.is_file():
            raise FileNotFoundError(path.name)
        return LoadedResultDto(path.name, path.name, self.repository.load(path))

    def latest(self, result_type: str) -> LoadedResultDto:
        path = self.repository.latest_path(result_type)
        return self.load(path.name)


class ResultExportAdapter:
    def __init__(self, query: ResultQueryAdapter, paths: PathService):
        self.query = query
        self.paths = paths

    def _source(self, result_id: str) -> Path:
        return self.query._path(result_id)

    def export_json(self, result_id: str, target: str) -> None:
        shutil.copy2(self._source(result_id), Path(target))

    def export_csv(self, result_id: str, target: str) -> None:
        # 结果读取走 application 输入端口，分析层只接收已加载的数据。
        loaded = self.query.load(result_id)
        visualizer = DataVisualization(output_dir=self._report_output_dir())
        visualizer.generate_csv_report(loaded.data)
        generated = Path(visualizer.output_dir) / "full_sweep_data.csv"
        if not generated.is_file():
            raise FileNotFoundError(str(generated))
        shutil.copy2(generated, Path(target))

    def export_pdf(self, result_id: str) -> None:
        loaded = self.query.load(result_id)
        DataVisualization(output_dir=self._report_output_dir()).create_summary_report(
            loaded.data, loaded.filename
        )

    def _report_output_dir(self) -> Path:
        return self.paths.test_results_dir / unique_directory_name("report")


class ReportAdapter:
    def __init__(self, paths: PathService):
        self.paths = paths

    def create_summary_report(self, result, configuration, filename) -> None:
        # 结果规范化走 application 输入；分析层只接收稳定的领域模型，不落地临时文件。
        model = build_report_model(
            result,
            configuration=configuration,
            original_filename=filename,
            measurement_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        output_dir = self.paths.test_results_dir / unique_directory_name("report")
        DataVisualization(output_dir=output_dir).create_summary_report(model, filename)
