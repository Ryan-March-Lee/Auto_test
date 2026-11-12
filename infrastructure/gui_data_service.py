"""File-backed adapters used by the GUI composition root.

应用配置的写入只有一个入口：:class:`JsonConfigurationAdapter` 把保存委托给配置仓储
（``JsonConfigurationRepository.save_document``），由它统一套用原子 JSON 原语。保存失败
时原配置文件保持不变，写入失败原因原样上抛，清理临时文件失败也不会顶替该原因。

历史查看、导出和报告读取的是正式结果：步骤 19 起浏览主来源改为独立阶段目录，
``list_results()`` 直接扫描 ``test_results/cable_loss/``、``driver_mapping/``、
``amplifier/`` 并解析正式结果 DTO，``result_id`` 使用 ``stage::<阶段>::<measurement_id>``
编码。旧运行目录里仍保留的完整结果文档通过只读兼容分支继续列出（``result_id`` 为
内部 ``run::<run_id>::<相对路径>``）；运行目录里的阶段结果引用指针不再单独列出，
避免同一条正式结果出现两次。兼容分支读取失败只记录日志，不影响阶段结果浏览。

适配器在读取边界调用唯一解析入口 ``measurement_result_from_document()``，因此上层拿到
的一律是正式结果 DTO；不再扫描 ``test_results/`` 下的旧全局结果文件，也不提供旧历史
回退。逐字节导出使用加载时校验过的那份字节，不会在校验之后重读可能已被替换的文件。
步骤 20 再把交付产物定位切换到阶段结果。

报告只有一个适配器实现：:meth:`ResultExportAdapter.export_report`。它把归档结果交给
``build_report_result()`` 后再渲染，因此不存在第二个“页面配置 + 生成时刻”的报告入口
（重构计划步骤 8 删除的 ``ReportAdapter``）。
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from application.dto.measurement_results import (
    MeasurementResult,
    MeasurementType,
    measurement_result_filename,
    measurement_result_from_document,
    measurement_type_for_result_type,
)
from application.inputs.result_reading import build_report_result
from application.ports.gui_data_service import (
    RESULT_SOURCE_RUN,
    RESULT_SOURCE_STAGE,
    LoadedResultDto,
    ResultFileDto,
)
from application.ports.run_history import (
    RunHistoryRepository,
    result_type_for_result,
)
from application.ports.stage_result_repository import (
    LoadedStageResult,
    MeasurementStage,
    StageResultEntry,
    StageResultRepository,
)
from application.run_stages import (
    read_stage_result_reference,
    resolve_stage_result_reference,
)
from analysis.visualization import DataVisualization
from infrastructure.config.json_config_repository import JsonConfigurationRepository
from infrastructure.config.json_io import load_config_file
from infrastructure.filesystem.paths import (
    PathService,
    unique_directory_name,
)


logger = logging.getLogger(__name__)


# 运行目录结果的标识前缀：``run::<run_id>::<运行内相对路径>``。运行标识不允许
# 包含 ``:``，因此编码没有歧义；该编码只在本适配器内部使用，不暴露文件系统路径。
_RUN_RESULT_PREFIX = "run::"
# 阶段目录结果的标识前缀：``stage::<阶段>::<measurement_id>``。测量身份同样不含
# ``:``，编码唯一；阶段目录由仓库按阶段解析，标识本身不携带文件系统路径。
_STAGE_RESULT_PREFIX = "stage::"


def _encode_run_result_id(run_id: str, relative_path: str) -> str:
    return f"{_RUN_RESULT_PREFIX}{run_id}::{relative_path}"


def _decode_run_result_id(result_id: str) -> tuple[str, str] | None:
    if not isinstance(result_id, str) or not result_id.startswith(_RUN_RESULT_PREFIX):
        return None
    run_id, _separator, relative_path = result_id[len(_RUN_RESULT_PREFIX):].partition("::")
    if not run_id or not relative_path:
        return None
    return run_id, relative_path


def _encode_stage_result_id(stage: MeasurementStage, measurement_id: str) -> str:
    return f"{_STAGE_RESULT_PREFIX}{stage.value}::{measurement_id}"


def _decode_stage_result_id(
    result_id: str,
) -> tuple[MeasurementStage, str] | None:
    """把 ``stage::<阶段>::<measurement_id>`` 拆成阶段与测量身份。"""
    if not isinstance(result_id, str) or not result_id.startswith(
        _STAGE_RESULT_PREFIX
    ):
        return None
    stage_text, separator, measurement_id = result_id[
        len(_STAGE_RESULT_PREFIX):
    ].partition("::")
    if not separator or not stage_text or not measurement_id:
        return None
    if ":" in measurement_id:
        return None
    try:
        stage = MeasurementStage(stage_text)
    except ValueError:
        return None
    return stage, measurement_id


def _stage_result_path(
    paths: PathService, stage: MeasurementStage, measurement_id: str
) -> Path:
    """由阶段与测量身份定位阶段目录内的正式结果文件。

    路径只用于把结果标识交给阶段结果仓库：仓库仍会展开符号链接、校验它们直接位于
    阶段目录内，并按正式 DTO、状态与身份重新读取，因此这里不承担任何读取语义。
    """
    directories = {
        MeasurementStage.CABLE_LOSS: paths.cable_loss_results_dir,
        MeasurementStage.DRIVER_MAPPING: paths.driver_mapping_results_dir,
        MeasurementStage.AMPLIFIER: paths.amplifier_results_dir,
    }
    return directories[stage] / measurement_result_filename(measurement_id)


# 列表排序/时间展示使用的固定纪元：与日期相减得到秒数，避免 ``datetime.timestamp()``
# 在 Windows 上对 ``0001-01-01`` 这类合法但极端的日期抛 ``OSError``。
_SORT_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _stage_result_modified_at(entry: StageResultEntry) -> datetime:
    """把阶段结果的保存时间转成列表展示用时间，解析失败时退回文件修改时间。"""
    try:
        moment = datetime.fromisoformat(entry.created_at)
    except (TypeError, ValueError):
        logger.debug("阶段结果保存时间无法解析 %s: %r", entry.path, entry.created_at)
        try:
            return datetime.fromtimestamp(entry.path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            return _SORT_EPOCH
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _utc_seconds(moment: datetime) -> float:
    """把日期折算成相对固定 UTC 纪元的秒数，失败时返回 ``-inf``。

    不使用 ``datetime.timestamp()``：该调用在 Windows 上对 ``0001-01-01`` 这类合法
    但极端的日期会抛 ``OSError``，让整轮列举或排序失败。无时区时间按 UTC 解释，
    因此排序结果不依赖本机时区或夏令时。
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        return (moment.astimezone(timezone.utc) - _SORT_EPOCH).total_seconds()
    except (OverflowError, OSError, ValueError):
        return float("-inf")


def _result_sort_key(row: ResultFileDto) -> float:
    """按时间从新到旧排序；极端日期不会让排序失败。"""
    return _utc_seconds(row.modified_at)


def _verified_stage_bytes(loaded: LoadedStageResult) -> bytes:
    """返回与已校验阶段结果对应的原始字节，供逐字节导出使用。

    端口实现随加载结果给出解析用的同一份字节时直接复用，导出内容与 DTO、摘要必然
    一致。实现没有提供字节时只能重读文件，但必须再用加载时登记的摘要与大小复核；
    文件在校验之后被替换就明确失败，不能把未经校验的新内容导出成已加载的结果。
    """
    if loaded.raw_bytes is not None:
        return loaded.raw_bytes
    try:
        raw = loaded.path.read_bytes()
    except OSError as error:
        raise ValueError(
            f"无法重新读取阶段结果 {loaded.path}: {error}"
        ) from error
    if (
        len(raw) != loaded.size_bytes
        or hashlib.sha256(raw).hexdigest() != loaded.sha256
    ):
        raise ValueError(
            "阶段结果文件在加载之后发生变化，无法按已校验内容导出: "
            f"{loaded.path}"
        )
    return raw


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
    """只读的正式结果查询与显式阶段文件加载适配器。

    浏览主来源是独立阶段目录：``list_results()`` 扫描三类阶段目录并经阶段结果仓库
    解析正式结果 DTO，新结果标识为 ``stage::<阶段>::<measurement_id>``；旧运行目录
    里仍保留的完整结果文档通过只读兼容分支继续列出，运行目录里的阶段结果引用指针
    不重复列出。``load()`` / ``read_bytes()`` 同时接受两种标识。

    用户显式选择阶段文件时，``load_stage_result()`` 只加载调用方给出的路径，不扫描
    目录、不选择最新文件，也不回退到其它结果。返回结果均解析为正式结果 DTO。
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
        stage_results: StageResultRepository | None = None,
    ):
        self.paths = paths
        self.run_history = run_history
        # 引用解析必须经过阶段结果仓库（端口实现由组装层注入）。省略时只能读取旧运行
        # 目录里的完整结果文档：遇到阶段结果引用会明确失败，绝不退回引用文档本身。
        self.stage_results = stage_results

    # -- 列表 ---------------------------------------------------------------

    def list_results(self) -> list[ResultFileDto]:
        """列出三类阶段正式结果与旧运行目录兼容结果，按保存时间从新到旧。

        阶段目录是浏览主来源：三类目录分别经阶段结果仓库列举并解析，损坏、未完成、
        类型不匹配的文件由仓库跳过。旧运行目录只补上仍完整的正式结果文档；运行目录
        里的阶段结果引用指针由阶段目录列表覆盖，不再重复出现。单个阶段或旧运行目录
        读取失败只记录日志，不影响其余结果——阶段结果不因为运行目录不可读而消失。
        """
        rows = self._list_stage_results()
        try:
            rows.extend(self._list_run_results())
        except (OSError, ValueError) as error:
            # 兼容分支只补充旧运行文档；枚举运行根目录失败不能拖垮阶段结果浏览。
            logger.warning("跳过无法列出的旧运行目录结果: %s", error)
        return sorted(rows, key=_result_sort_key, reverse=True)

    def _list_stage_results(self) -> list[ResultFileDto]:
        """扫描三类阶段目录，把可用正式结果转成列表行。"""
        if self.stage_results is None:
            return []
        rows: list[ResultFileDto] = []
        for stage in MeasurementStage:
            try:
                entries = self.stage_results.list_results(stage)
            except (OSError, ValueError) as error:
                logger.warning("跳过无法列出的阶段目录 %s: %s", stage.value, error)
                continue
            for entry in entries:
                if entry.measurement_type is not stage.measurement_type:
                    logger.warning(
                        "跳过类型与阶段不一致的结果 %s: %s != %s",
                        entry.path,
                        entry.measurement_type.value,
                        stage.measurement_type.value,
                    )
                    continue
                kind = result_type_for_result(stage.value, entry.path.name)
                rows.append(
                    ResultFileDto(
                        result_id=_encode_stage_result_id(
                            stage, entry.measurement_id
                        ),
                        filename=entry.path.name,
                        file_type=self._LABELS.get(kind, kind),
                        modified_at=_stage_result_modified_at(entry),
                        source=RESULT_SOURCE_STAGE,
                        run_id=entry.run_id,
                        source_result=None,
                    )
                )
        return rows

    def _list_run_results(self) -> list[ResultFileDto]:
        """旧运行目录的只读兼容分支：只列出仍完整的正式结果文档。"""
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
                    if read_stage_result_reference(document) is not None:
                        # 阶段正式结果已由阶段目录列表给出；运行目录里只是引用指针，
                        # 不再以第二个标识重复呈现同一条结果。
                        continue
                except (TypeError, ValueError) as error:
                    logger.debug("跳过不可解析的阶段结果引用 %s: %s", result_id, error)
                    continue
                try:
                    result = measurement_result_from_document(document)
                except (OSError, ValueError) as error:
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
                        source_result=entry.result_id,
                    )
                )
        return rows

    # -- 读取 ---------------------------------------------------------------

    def load(self, result_id: str) -> LoadedResultDto:
        stage_target = _decode_stage_result_id(result_id)
        if stage_target is not None:
            loaded = self._load_stage_by_id(*stage_target)
            return LoadedResultDto(
                result_id,
                loaded.path.name,
                loaded.result,
                run_id=loaded.result.run_id,
                sha256=loaded.sha256,
                size_bytes=loaded.size_bytes,
            )
        run_id, relative_path = self._decode(result_id)
        expected = self._expected_measurement_type(run_id, relative_path)
        if expected is None:
            raise ValueError(f"结果不是正式测量结果类型: {relative_path}")
        result = self._load_verified(run_id, relative_path, expected)
        return LoadedResultDto(
            result_id,
            Path(relative_path).name,
            result,
            run_id=run_id,
            source_result=relative_path,
        )

    def read_bytes(self, result_id: str) -> bytes:
        """逐字节读取结果，供 JSON 导出使用。

        阶段目录结果读取的是权威阶段文件；旧运行结果仍按运行目录读取。运行目录里的
        阶段结果引用继续解析到阶段目录文件，保证导出内容与已校验的正式结果一致。
        """
        stage_target = _decode_stage_result_id(result_id)
        if stage_target is not None:
            return _verified_stage_bytes(self._load_stage_by_id(*stage_target))
        run_id, relative_path = self._decode(result_id)
        document = self.run_history.load(run_id, relative_path)
        resolved = resolve_stage_result_reference(document, self.stage_results)
        if resolved is not None:
            return _verified_stage_bytes(resolved)
        return self.run_history.read_bytes(run_id, relative_path)

    def _load_stage_by_id(
        self, stage: MeasurementStage, measurement_id: str
    ) -> LoadedStageResult:
        """按阶段标识读取阶段目录中的权威结果。

        标识只给出阶段与测量身份；实际文件路径由阶段目录派生，目录边界、正式 DTO、
        类型、状态与身份仍由阶段结果仓库重新校验。
        """
        if self.stage_results is None:
            raise ValueError("阶段结果仓储未装配，无法读取阶段结果")
        try:
            path = _stage_result_path(self.paths, stage, measurement_id)
        except ValueError as error:
            raise ValueError(
                f"无效的结果标识: {measurement_id!r}: {error}"
            ) from error
        return self.stage_results.load(stage, path)

    def load_stage_result(
        self, stage: MeasurementStage | str, path: str
    ) -> LoadedResultDto:
        """按用户显式选择的阶段目录文件加载正式结果。

        文件选择器只负责给出路径；阶段目录边界、文件存在性、JSON、正式 DTO、
        类型/状态/身份与 schema 都由阶段结果仓库校验。适配器不扫描目录，也不在
        失败时回退到“最新结果”，因此一次选择只会读取用户选中的那份文件。
        """
        if self.stage_results is None:
            raise ValueError("阶段结果仓储未装配，无法按文件加载正式结果")
        loaded = self.stage_results.load(stage, path)
        return LoadedResultDto(
            result_id=_encode_stage_result_id(
                loaded.reference.stage, loaded.measurement_id
            ),
            filename=loaded.path.name,
            result=loaded.result,
            run_id=loaded.result.run_id,
            sha256=loaded.sha256,
            size_bytes=loaded.size_bytes,
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
                matches, key=lambda item: _utc_seconds(item.modified_at), reverse=True
            ):
                result_id = _encode_run_result_id(run.run_id, entry.result_id)
                try:
                    result = self._load_verified(run.run_id, entry.result_id, target)
                except (OSError, ValueError, TypeError) as error:
                    logger.warning("跳过损坏的运行结果 %s: %s", result_id, error)
                    continue
                return LoadedResultDto(
                    result_id,
                    Path(entry.result_id).name,
                    result,
                    run_id=run.run_id,
                    source_result=entry.result_id,
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
        result = self._measurement_result(document)
        if result.measurement_type is not expected:
            raise ValueError(
                "结果文件类型与 manifest 登记不一致: "
                f"{relative_path} ({result.measurement_type.value} != {expected.value})"
            )
        return result

    def _measurement_result(self, document: Mapping[str, Any]) -> MeasurementResult:
        """把运行目录登记的结果文档解析为正式结果 DTO。

        运行目录里可能只是阶段结果引用（步骤 7 起的阶段正式结果）：此时按引用中的
        阶段、路径与校验值读取阶段目录里的权威结果，列表、最新结果和导出因此看到
        同一份已校验的正式 DTO。引用不是正式结果文档时仍按原路径解析，保持旧运行
        目录的读取语义。
        """
        loaded = resolve_stage_result_reference(document, self.stage_results)
        if loaded is not None:
            return loaded.result
        return measurement_result_from_document(document)


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
