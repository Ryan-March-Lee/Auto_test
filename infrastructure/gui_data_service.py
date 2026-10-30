"""File-backed adapters used by the GUI composition root.

应用配置的写入只有一个入口：:class:`JsonConfigurationAdapter` 把保存委托给配置仓储
（``JsonConfigurationRepository.save_document``），由它统一套用原子 JSON 原语。保存失败
时原配置文件保持不变，写入失败原因原样上抛，清理临时文件失败也不会顶替该原因。

历史查看、导出和报告只读取**正式运行目录结果**：每条结果都由运行 manifest 登记并
通过完整性校验，``result_id`` 使用内部 ``run::<run_id>::<相对路径>`` 编码。适配器
在读取边界调用唯一解析入口 ``measurement_result_from_document()``，因此上层拿到的
一律是正式结果 DTO；不再扫描 ``test_results/`` 下的旧全局结果文件，也不提供旧历史
回退。

报告只有一个适配器实现：:meth:`ResultExportAdapter.export_report`。它把归档结果交给
``build_report_result()`` 后再渲染，因此不存在第二个“页面配置 + 生成时刻”的报告入口
（重构计划步骤 8 删除的 ``ReportAdapter``）。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Mapping

from application.dto.measurement_results import (
    MeasurementResult,
    MeasurementType,
    measurement_result_from_document,
    measurement_type_for_result_type,
)
from application.inputs.result_reading import build_report_result
from application.ports.gui_data_service import (
    RESULT_SOURCE_RUN,
    LoadedResultDto,
    ResultFileDto,
)
from application.ports.run_history import RunHistoryRepository
from analysis.visualization import DataVisualization
from infrastructure.config.json_config_repository import JsonConfigurationRepository
from infrastructure.config.json_io import load_config_file
from infrastructure.filesystem.paths import (
    PathService,
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
    """应用配置文件的读写适配器（配置保存的唯一对外入口）。

    适配器只把“当前配置文件路径”绑定到配置仓储上，本身不掌握文件打开模式、序列化
    格式或临时文件策略：

    - 读取委托 :func:`infrastructure.config.json_io.load_config_file`（与启动器、校验
      入口同一个读取边界）；
    - 写入委托
      :meth:`infrastructure.config.json_config_repository.JsonConfigurationRepository.save_document`，
      即“同目录唯一临时文件 -> flush/fsync -> 原子替换”的唯一正式写入口。因此序列化
      失败、fsync 失败或替换失败时，磁盘上仍是上一次成功保存的完整配置，既不会被截断
      成半份 JSON，也不会出现半份新配置；清理临时文件失败只写日志，**不会**顶替真正的
      写入失败原因，调用方拿到的始终是可直接呈现给用户的原始异常。

    这里刻意不合并用途不同的 JSON 持久化：运行目录快照（``runs/<run_id>/config.json``
    与 manifest）由运行仓储负责，适配器不写任何运行目录，也不从运行目录读取配置。
    """

    def __init__(self, path: PathService | Path):
        self.path = path.config_file if isinstance(path, PathService) else Path(path)
        self.repository = JsonConfigurationRepository()

    def load(self):
        return load_config_file(self.path)

    def save(self, configuration: Mapping[str, Any]) -> None:
        """保存正式配置：只做委托，写入格式与失败语义由配置仓储决定。"""
        self.repository.save_document(self.path, configuration)


class ResultQueryAdapter:
    """只读的正式运行结果查询适配器。

    唯一数据源是运行目录：列表、按标识读取、最新结果和导出全部走运行 manifest
    登记并校验过的结果。每条结果在返回前都会解析为正式结果 DTO；无法解析的文档
    （非正式 schema、拆分明细文件、被篡改文件）既不会出现在列表里，也不会被读取。
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

    # -- 列表 ---------------------------------------------------------------

    def list_results(self) -> list[ResultFileDto]:
        rows: list[ResultFileDto] = []
        for run in self.run_history.list_runs():
            if not run.browsable:
                continue
            try:
                entries = self.run_history.list_results(run.run_id)
            except (OSError, ValueError) as error:
                logger.warning("跳过无法列出的运行 %s 的历史结果: %s", run.run_id, error)
                continue
            for entry in entries:
                expected = measurement_type_for_result_type(entry.result_type)
                if expected is None:
                    continue
                result_id = _encode_run_result_id(run.run_id, entry.result_id)
                try:
                    document = self.run_history.load(run.run_id, entry.result_id)
                except (OSError, ValueError) as error:
                    logger.warning("跳过无法校验的运行结果 %s: %s", result_id, error)
                    continue
                try:
                    result = measurement_result_from_document(document)
                except ValueError as error:
                    # 拆分明细等非正式文档属于预期情况，不刷警告。
                    logger.debug("跳过非正式结果文档 %s: %s", result_id, error)
                    continue
                if result.measurement_type is not expected:
                    logger.warning(
                        "跳过类型与 manifest 登记不一致的结果 %s: %s != %s",
                        result_id,
                        result.measurement_type.value,
                        expected.value,
                    )
                    continue
                rows.append(
                    ResultFileDto(
                        result_id=result_id,
                        filename=Path(entry.result_id).name,
                        file_type=self._LABELS.get(
                            entry.result_type, entry.result_type
                        ),
                        modified_at=entry.modified_at,
                        source=RESULT_SOURCE_RUN,
                        run_id=run.run_id,
                    )
                )
        return sorted(rows, key=lambda item: item.modified_at.timestamp(), reverse=True)

    # -- 读取 ---------------------------------------------------------------

    def load(self, result_id: str) -> LoadedResultDto:
        run_id, relative_path = self._decode(result_id)
        expected = self._expected_measurement_type(run_id, relative_path)
        if expected is None:
            raise ValueError(f"结果不是正式测量结果类型: {relative_path}")
        result = self._load_verified(run_id, relative_path, expected)
        return LoadedResultDto(result_id, Path(relative_path).name, result)

    def read_bytes(self, result_id: str) -> bytes:
        """逐字节读取运行结果，供 JSON 导出使用。"""
        run_id, relative_path = self._decode(result_id)
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
        损坏（被篡改、缺失、未登记、非正式 schema）时记录日志并继续尝试更旧的候选。
        没有任何可用运行结果时抛出 ``FileNotFoundError``。
        """
        target = measurement_type_for_result_type(result_type)
        if target is None:
            raise ValueError(f"无法识别的结果类型: {result_type!r}")
        for run in self.run_history.list_runs():
            if not run.browsable:
                continue
            try:
                entries = self.run_history.list_results(run.run_id)
            except (OSError, ValueError) as error:
                logger.warning("跳过无法列出的运行 %s 的历史结果: %s", run.run_id, error)
                continue
            matches = [
                entry
                for entry in entries
                if measurement_type_for_result_type(entry.result_type) is target
            ]
            for entry in sorted(
                matches, key=lambda item: item.modified_at.timestamp(), reverse=True
            ):
                result_id = _encode_run_result_id(run.run_id, entry.result_id)
                try:
                    result = self._load_verified(run.run_id, entry.result_id, target)
                except (OSError, ValueError, TypeError) as error:
                    logger.warning("跳过损坏的运行结果 %s: %s", result_id, error)
                    continue
                return LoadedResultDto(
                    result_id, Path(entry.result_id).name, result
                )
        raise FileNotFoundError(f"未找到结果类型为 {result_type} 的运行结果")

    # -- 内部工具 -----------------------------------------------------------

    @staticmethod
    def _decode(result_id: str) -> tuple[str, str]:
        decoded = _decode_run_result_id(result_id)
        if decoded is None:
            raise ValueError(f"无效的结果标识: {result_id!r}")
        return decoded

    def _expected_measurement_type(
        self, run_id: str, relative_path: str
    ) -> MeasurementType | None:
        for entry in self.run_history.list_results(run_id):
            if entry.result_id == relative_path:
                return measurement_type_for_result_type(entry.result_type)
        return None

    def _load_verified(
        self, run_id: str, relative_path: str, expected: MeasurementType
    ) -> MeasurementResult:
        """读取已登记结果并解析为正式 DTO，同时校验与 manifest 登记类型一致。"""
        document = self.run_history.load(run_id, relative_path)
        result = measurement_result_from_document(document)
        if result.measurement_type is not expected:
            raise ValueError(
                "结果文件类型与 manifest 登记不一致: "
                f"{relative_path} ({result.measurement_type.value} != {expected.value})"
            )
        return result


class ResultExportAdapter:
    def __init__(self, query: ResultQueryAdapter, paths: PathService):
        self.query = query
        self.paths = paths

    def export_json(self, result_id: str, target: str) -> None:
        # 逐字节复制已校验的源文件，导出不会改动源文件。
        Path(target).write_bytes(self.query.read_bytes(result_id))

    def export_csv(self, result_id: str, target: str) -> None:
        # 结果读取走运行结果查询，分析层只接收已解析的正式结果 DTO。
        result = self.query.load(result_id).result
        visualizer = DataVisualization(output_dir=self._report_output_dir())
        visualizer.generate_csv_report(result)
        generated = Path(visualizer.output_dir) / "full_sweep_data.csv"
        if not generated.is_file():
            raise FileNotFoundError(str(generated))
        shutil.copy2(generated, Path(target))

    def export_report(self, result_id: str) -> str:
        """生成测试汇总报告（HTML + 图表），返回实际报告位置。

        报告内容只来自**归档结果**：配置快照、测量时间与来源文件名都按结果契约读取。
        适配器既不接收“当前配置”，也不写入生成时刻，因此历史结果的报告副本与自动归档
        写回运行目录的报告使用同一份结果事实，可以追溯到明确的 ``run_id`` 与结果标识
        （重构计划步骤 8）。
        """
        loaded = self.query.load(result_id)
        report_result = build_report_result(
            loaded.result, original_filename=loaded.filename
        )
        report_path = DataVisualization(
            output_dir=self._report_output_dir()
        ).create_summary_report(report_result, loaded.filename)
        return str(report_path)

    def _report_output_dir(self) -> Path:
        return self.paths.test_results_dir / unique_directory_name("report")
