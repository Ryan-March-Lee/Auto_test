"""File-backed adapters used by the GUI composition root.

历史查看、导出和报告只读取**正式运行目录结果**：每条结果都由运行 manifest 登记并
通过完整性校验，``result_id`` 使用内部 ``run::<run_id>::<相对路径>`` 编码。适配器
不再扫描 ``test_results/`` 下的旧全局结果文件，也不提供旧历史回退。
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from application.dto.measurement_results import (
    MeasurementResult,
    measurement_result_from_document,
)
from application.inputs.result_reading import build_report_result
from application.ports.gui_data_service import (
    RESULT_SOURCE_RUN,
    LoadedResultDto,
    ResultFileDto,
)
from application.ports.run_history import RunHistoryRepository
from analysis.visualization import DataVisualization
from infrastructure.config.json_io import load_config_file
from infrastructure.filesystem.paths import (
    PathService,
    ensure_directory,
    unique_directory_name,
)
from PySide6.QtWidgets import QFileDialog


logger = logging.getLogger(__name__)


# 运行目录结果的标识前缀：``run::<run_id>::<运行内相对路径>``。运行标识不允许
# 包含 ``:``，因此编码没有歧义；该编码只在本适配器内部使用，不暴露文件系统路径。
_RUN_RESULT_PREFIX = "run::"


def _encode_run_result_id(run_id: str, relative_path: str) -> str:
    return f"{_RUN_RESULT_PREFIX}{run_id}::{relative_path}"


def _decode_run_result_id(result_id: str) -> tuple[str, str] | None:
    if not isinstance(result_id, str) or not result_id.startswith(_RUN_RESULT_PREFIX):
        return None
    run_id, _separator, relative_path = result_id[len(_RUN_RESULT_PREFIX):].partition("::")
    if not run_id or not relative_path:
        return None
    return run_id, relative_path


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
    """只读的正式运行结果查询适配器。

    唯一数据源是运行目录：列表、按标识读取、最新结果和导出全部走运行 manifest
    登记并校验过的结果。旧全局结果文件不再被发现或回退，历史查看只读正式运行
    目录结果。
    """

    _LABELS = {
        "cable_loss": "线损数据",
        "driver_power_mapping": "驱动映射",
        "amplifier_measurement": "功放测试",
    }

    def __init__(
        self,
        paths: PathService,
        *,
        run_history: RunHistoryRepository,
        runs_root: Path | None = None,
    ):
        self.paths = paths
        self.run_history = run_history
        self.runs_root = runs_root

    def list_results(self) -> list[ResultFileDto]:
        rows: list[ResultFileDto] = []
        for run in self.run_history.list_runs():
            if not run.browsable:
                continue
            try:
                results = self.run_history.list_results(run.run_id)
            except (OSError, ValueError) as error:
                logger.warning("跳过无法列出的运行 %s 的历史结果: %s", run.run_id, error)
                continue
            for result in results:
                rows.append(
                    ResultFileDto(
                        result_id=_encode_run_result_id(run.run_id, result.result_id),
                        filename=Path(result.result_id).name,
                        file_type=self._LABELS.get(result.result_type, result.result_type),
                        modified_at=result.modified_at,
                        source=RESULT_SOURCE_RUN,
                        run_id=run.run_id,
                    )
                )
        return sorted(rows, key=lambda item: item.modified_at.timestamp(), reverse=True)

    def load(self, result_id: str) -> LoadedResultDto:
        decoded = _decode_run_result_id(result_id)
        if decoded is None:
            raise ValueError(f"无效的结果标识: {result_id!r}")
        run_id, relative_path = decoded
        return LoadedResultDto(
            result_id,
            Path(relative_path).name,
            self.run_history.load(run_id, relative_path),
        )

    def read_bytes(self, result_id: str) -> bytes:
        """逐字节读取运行结果，供 JSON 导出使用。"""
        decoded = _decode_run_result_id(result_id)
        if decoded is None:
            raise ValueError(f"无效的结果标识: {result_id!r}")
        run_id, relative_path = decoded
        return self.run_history.read_bytes(run_id, relative_path)

    def choose_and_load(self) -> LoadedResultDto | None:
        """从运行目录中选择一个已登记结果；运行目录外的文件一律拒绝。"""
        if self.runs_root is None:
            raise RuntimeError("未配置运行结果目录")
        selected, _ = QFileDialog.getOpenFileName(
            None, "选择运行结果文件", str(self.runs_root),
            "JSON files (*.json);;All files (*.*)",
        )
        if not selected:
            return None
        path = Path(selected).expanduser().resolve()
        try:
            relative = path.relative_to(self.runs_root.resolve())
        except ValueError as error:
            raise ValueError(f"结果文件必须位于运行目录内: {path}") from error
        if not path.is_file():
            raise FileNotFoundError(path.name)
        if len(relative.parts) < 2:
            raise ValueError(f"结果文件必须位于运行目录内: {path}")
        run_id = relative.parts[0]
        return self.load(
            _encode_run_result_id(run_id, Path(*relative.parts[1:]).as_posix())
        )

    def latest_result(self, result_type: str) -> LoadedResultDto:
        """返回最新的**可校验**正式运行结果。

        顺序是确定性的：先按运行创建时间从新到旧（``list_runs`` 的顺序），同一运行
        内再按结果修改时间从新到旧，不依赖文件系统 mtime 的跨运行比较。某个候选
        损坏（被篡改、缺失、未登记）时记录日志并继续尝试更旧的候选。没有任何可用
        运行结果时抛出 ``FileNotFoundError``。
        """
        label = self._LABELS.get(result_type, result_type)
        for run in self.run_history.list_runs():
            if not run.browsable:
                continue
            try:
                results = self.run_history.list_results(run.run_id)
            except (OSError, ValueError) as error:
                logger.warning("跳过无法列出的运行 %s 的历史结果: %s", run.run_id, error)
                continue
            matches = [
                result
                for result in results
                if self._LABELS.get(result.result_type, result.result_type) == label
            ]
            for result in sorted(
                matches, key=lambda item: item.modified_at.timestamp(), reverse=True
            ):
                result_id = _encode_run_result_id(run.run_id, result.result_id)
                try:
                    return self.load(result_id)
                except (OSError, ValueError) as error:
                    logger.warning("跳过损坏的运行结果 %s: %s", result_id, error)
                    continue
        raise FileNotFoundError(f"未找到结果类型为 {result_type} 的运行结果")


class ResultExportAdapter:
    def __init__(self, query: ResultQueryAdapter, paths: PathService):
        self.query = query
        self.paths = paths

    def export_json(self, result_id: str, target: str) -> None:
        # 逐字节复制已校验的源文件，导出不会改动源文件。
        Path(target).write_bytes(self.query.read_bytes(result_id))

    def export_csv(self, result_id: str, target: str) -> None:
        # 结果读取走运行结果查询，分析层只接收已解析的正式结果 DTO。
        loaded = self.query.load(result_id)
        result = measurement_result_from_document(loaded.data)
        visualizer = DataVisualization(output_dir=self._report_output_dir())
        visualizer.generate_csv_report(result)
        generated = Path(visualizer.output_dir) / "full_sweep_data.csv"
        if not generated.is_file():
            raise FileNotFoundError(str(generated))
        shutil.copy2(generated, Path(target))

    def export_pdf(self, result_id: str) -> None:
        loaded = self.query.load(result_id)
        result = measurement_result_from_document(loaded.data)
        DataVisualization(output_dir=self._report_output_dir()).create_summary_report(
            result, loaded.filename
        )

    def _report_output_dir(self) -> Path:
        return self.paths.test_results_dir / unique_directory_name("report")


class ReportAdapter:
    def __init__(self, paths: PathService):
        self.paths = paths

    def create_summary_report(
        self,
        result: MeasurementResult,
        configuration: Mapping[str, Any],
        filename: str,
    ) -> None:
        # 报告只在正式结果 DTO 上叠加展示参数；分析层不接触原始文档。
        report_result = build_report_result(
            result,
            configuration=configuration,
            original_filename=filename,
            measurement_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
        output_dir = self.paths.test_results_dir / unique_directory_name("report")
        DataVisualization(output_dir=output_dir).create_summary_report(
            report_result, filename
        )
