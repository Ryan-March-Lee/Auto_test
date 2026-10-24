"""File-backed adapters used by the GUI composition root."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from application.inputs.result_reading import build_report_model
from application.ports.gui_data_service import (
    RESULT_SOURCE_LEGACY,
    RESULT_SOURCE_RUN,
    LoadedResultDto,
    ResultFileDto,
)
from application.ports.result_history import ResultHistoryRepository
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
# 包含 ``:``，因此可以无歧义地把“运行结果”和“全局兼容副本文件名”区分开；该编码
# 只在本适配器内部使用，不暴露文件系统路径。
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
    """历史结果浏览适配器。

    历史浏览同时消费两类只读数据源：

    - 运行目录历史（:class:`RunHistoryRepository`）：新流程写入 ``runs/<run_id>/``
      并登记到 manifest 的阶段结果，是历史列表的首选来源。
    - 全局兼容副本（:class:`ResultHistoryRepository`）：迁移前写入的旧历史文件，
      保持只读可查看。

    两类来源都只提供 ``result_id``（运行结果用内部 ``run::`` 编码）而不是任意
    文件系统路径：路径边界由各自仓储统一校验，因此历史浏览既不能写入旧结果
    文件，也不会把历史文件带入任何运行 manifest。列表、按标识读取、最新结果
    浏览和导出全部走同一个 ``result_id`` 入口。
    """

    _LABELS = {
        "cable_loss": "线损数据",
        "driver_power_mapping": "驱动映射",
        "amplifier_measurement": "功放测试",
    }

    def __init__(
        self,
        paths: PathService,
        history: ResultHistoryRepository,
        *,
        run_history: RunHistoryRepository | None = None,
    ):
        self.paths = paths
        self.history = history
        self.run_history = run_history

    def list_results(self) -> list[ResultFileDto]:
        rows = self._run_rows() + self._legacy_rows()
        # 运行结果时间带时区、旧历史文件时间来自本地 mtime；统一按时间戳排序，
        # 避免混排时比较“无时区/有时区”的 datetime 报错。
        return sorted(rows, key=lambda item: item.modified_at.timestamp(), reverse=True)

    def load(self, result_id: str) -> LoadedResultDto:
        decoded = _decode_run_result_id(result_id)
        if decoded is not None:
            run_id, relative_path = decoded
            filename = Path(relative_path).name
            return LoadedResultDto(
                result_id, filename, self._require_run_history().load(run_id, relative_path)
            )
        return LoadedResultDto(result_id, result_id, self.history.load(result_id))

    def read_bytes(self, result_id: str) -> bytes:
        """逐字节读取历史结果，供 JSON 导出使用。"""
        decoded = _decode_run_result_id(result_id)
        if decoded is not None:
            run_id, relative_path = decoded
            return self._require_run_history().read_bytes(run_id, relative_path)
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

    def latest_result(self, result_type: str) -> LoadedResultDto:
        """返回最新的**可校验**历史结果：优先运行目录，其次全局兼容副本。

        选择顺序是确定性的：先按运行创建时间从新到旧（``list_runs`` 的顺序），
        同一运行内再按结果修改时间从新到旧，不依赖文件系统 mtime 的跨运行比较。

        每个候选都会做完整性校验；某个候选损坏（被篡改、缺失、未登记）时记录日志
        并继续尝试更旧的候选，而不是让最新损坏文件阻断可用数据。只有当所有运行
        候选都不可用时，才回退到旧的全局兼容副本，保持旧历史仍可查看。
        """
        label = self._LABELS.get(result_type, result_type)
        if self.run_history is not None:
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
        return self.latest_legacy_result(result_type)

    def latest_legacy_result(self, result_type: str) -> LoadedResultDto:
        """读取最新全局兼容副本，仅用于旧历史的只读查看。"""
        return self.load(self.history.latest_result_id(result_type))

    # -- 内部工具 -----------------------------------------------------------

    def _legacy_rows(self) -> list[ResultFileDto]:
        return [
            ResultFileDto(
                result_id=entry.result_id,
                filename=entry.result_id,
                file_type=self._LABELS.get(entry.result_type, entry.result_type),
                modified_at=entry.modified_at,
                source=RESULT_SOURCE_LEGACY,
            )
            for entry in self.history.list_entries()
        ]

    def _run_rows(self) -> list[ResultFileDto]:
        if self.run_history is None:
            return []
        rows: list[ResultFileDto] = []
        for run in self.run_history.list_runs():
            if not run.browsable:
                continue
            for result in self.run_history.list_results(run.run_id):
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
        return rows

    def _require_run_history(self) -> RunHistoryRepository:
        if self.run_history is None:
            raise RuntimeError("未配置运行历史仓储，无法读取运行结果")
        return self.run_history


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
