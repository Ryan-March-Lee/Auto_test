"""File-backed adapters used by the GUI composition root."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from application.inputs.result_reading import build_report_model
from application.ports.gui_data_service import LoadedResultDto, ResultFileDto
from application.ports.result_history import ResultHistoryRepository
from analysis.visualization import DataVisualization
from infrastructure.config.json_io import load_config_file
from infrastructure.filesystem.paths import (
    PathService,
    ensure_directory,
    unique_directory_name,
)
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
    """历史结果浏览适配器。

    只消费只读历史端口 :class:`ResultHistoryRepository`，不复用正式测量输入
    仓储：列表、按标识读取、最新副本浏览和导出全部走同一个 ``result_id`` 入口，
    路径边界由历史仓储统一校验，因此既不能写入旧结果文件，也不会把历史文件
    带入任何运行 manifest。
    """

    _LABELS = {
        "cable_loss": "线损数据",
        "driver_power_mapping": "驱动映射",
        "amplifier_measurement": "功放测试",
    }

    def __init__(self, paths: PathService, history: ResultHistoryRepository):
        self.paths = paths
        self.history = history

    def list_results(self) -> list[ResultFileDto]:
        rows = [
            ResultFileDto(
                result_id=entry.result_id,
                filename=entry.result_id,
                file_type=self._LABELS.get(entry.result_type, entry.result_type),
                modified_at=entry.modified_at,
            )
            for entry in self.history.list_entries()
        ]
        return sorted(rows, key=lambda item: item.modified_at, reverse=True)

    def load(self, result_id: str) -> LoadedResultDto:
        return LoadedResultDto(result_id, result_id, self.history.load(result_id))

    def read_bytes(self, result_id: str) -> bytes:
        """逐字节读取历史结果，供 JSON 导出使用。"""
        return self.history.read_bytes(result_id)

    def choose_and_load(self) -> LoadedResultDto | None:
        selected, _ = QFileDialog.getOpenFileName(
            None, "选择测试数据文件", str(self.paths.test_results_dir),
            "JSON files (*.json);;All files (*.*)",
        )
        if not selected:
            return None
        path = Path(selected).expanduser().resolve()
        # 只接受历史目录内的单个文件；结果标识仍由历史仓储再校验一次。
        path.relative_to(self.paths.test_results_dir.resolve())
        if not path.is_file():
            raise FileNotFoundError(path.name)
        return self.load(path.name)

    def latest_legacy_result(self, result_type: str) -> LoadedResultDto:
        """读取最新全局兼容副本，仅用于历史查看/导出，不作为正式测量输入。"""
        return self.load(self.history.latest_result_id(result_type))


class ResultExportAdapter:
    def __init__(self, query: ResultQueryAdapter, paths: PathService):
        self.query = query
        self.paths = paths

    def export_json(self, result_id: str, target: str) -> None:
        # 逐字节复制历史目录内已校验的源文件，导出不会改动源文件。
        Path(target).write_bytes(self.query.read_bytes(result_id))

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
