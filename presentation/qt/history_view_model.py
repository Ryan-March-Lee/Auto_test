"""历史查看的只读视图模型（不依赖 Qt）。

页面通过这里读取历史列表、选中项和已加载结果：

- 行内只有**结果标识与展示文本**，不暴露文件系统路径；列表来自只读历史查询端口。
- 历史运行摘要（运行/阶段/产物状态）同样在这里转成展示文本：页面不读 manifest、
  不解析运行目录，也不自己推断“能否重新生成”。数据来自应用层的运行摘要端口
  （``RunHistoryServicePort.list_runs()``）。
- 已加载结果只接受历史查询返回的正式结果 DTO；绘图用的频率分组在这里一次性构建，
  页面不再把结果当成 ``Mapping[str, Any]`` 读取。
- 视图模型不写文件、不解析路径、不缓存原始文档，因此历史查看、导出和配置编辑可以
  分别测试。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from application.dto import MeasurementResult, normalize_frequency_key
from application.dto.run_history import (
    RunArchiveState,
    RunHistorySummary,
)
from application.ports.gui_data_service import (
    RESULT_SOURCE_RUN,
    LoadedResultDto,
    ResultFileDto,
    ResultQueryPort,
)
from domain.run.models import ArtifactKind, ArtifactStatus, RunStatus

# 报告兜底使用的正式结果类型（主功放测量结果）。
AMPLIFIER_RESULT_TYPE = "amplifier_measurement"
UNREGISTERED_SOURCE_LABEL = "未登记来源"


def result_plot_series(
    result: MeasurementResult,
) -> tuple[dict[str, dict[str, Any]], list[float]]:
    """把正式结果转换为绘图用的按频率分组数据。

    返回 ``(series, frequencies)``：``series`` 的键是规范化频率字符串，值是绘图控件
    需要的 ``sweep_data`` 列；``frequencies`` 是升序频率列表。
    """
    series: dict[str, dict[str, Any]] = {}
    if not isinstance(result, MeasurementResult):
        raise ValueError("绘图数据必须来自正式结果 DTO")
    for frequency, rows in result.sweep_rows().items():
        key = normalize_frequency_key(frequency)
        if key is None or not math.isfinite(float(key)):
            raise ValueError(f"包含非法频率: {frequency}")
        if key in series:
            raise ValueError(f"重复频率: {frequency}")
        columns: dict[str, list[Any]] = {}
        for row in rows:
            for name, value in row.items():
                columns.setdefault(name, []).append(value)
        series[key] = {"sweep_data": columns}
    return series, sorted(float(key) for key in series)


def result_kind(result: MeasurementResult) -> str:
    """返回绘图/报告使用的测量类型标识。"""
    return result.measurement_type_value


def _freeze_series(
    series: Mapping[str, Mapping[str, Any]],
) -> Mapping[str, Mapping[str, tuple[Any, ...]]]:
    """把绘图视图冻结为“映射代理 + 元组”，视图模型缓存不再可被外部改写。"""
    frozen: dict[str, Mapping[str, tuple[Any, ...]]] = {}
    for key, record in series.items():
        columns = record.get("sweep_data") if isinstance(record, Mapping) else None
        columns = columns if isinstance(columns, Mapping) else {}
        frozen[key] = MappingProxyType(
            {name: tuple(values) for name, values in columns.items()}
        )
    return MappingProxyType(frozen)


@dataclass(frozen=True)
class HistoryRow:
    """历史列表的一行：只有结果标识与展示文本，没有文件系统路径。"""

    result_id: str
    filename: str
    file_type: str
    modified_at: datetime
    source: str = RESULT_SOURCE_RUN
    run_id: str | None = None

    @classmethod
    def from_dto(cls, result_file: ResultFileDto) -> "HistoryRow":
        """由历史查询返回的只读行构造；非正式行会被明确拒绝。"""
        if not isinstance(result_file, ResultFileDto):
            raise TypeError("历史行必须来自 ResultFileDto")
        return cls(
            result_id=result_file.result_id,
            filename=result_file.filename,
            file_type=result_file.file_type,
            modified_at=result_file.modified_at,
            source=result_file.source,
            run_id=result_file.run_id,
        )

    @property
    def source_label(self) -> str:
        if self.source == RESULT_SOURCE_RUN and self.run_id:
            return f"运行 {self.run_id}"
        return UNREGISTERED_SOURCE_LABEL

    @property
    def modified_text(self) -> str:
        return self.modified_at.strftime("%Y-%m-%d %H:%M:%S")

    def suggested_filename(self, suffix: str) -> str:
        """导出对话框使用的默认文件名；后缀与记录一致。"""
        stem = self.filename.rsplit(".", 1)[0] if "." in self.filename else self.filename
        return f"{stem}.{suffix.lstrip('.')}"


# -- 历史运行摘要的展示文本 -------------------------------------------------

_RUN_STATUS_LABELS = {
    RunStatus.CREATED: "已创建",
    RunStatus.RUNNING: "进行中",
    RunStatus.COMPLETED: "已完成",
    RunStatus.FAILED: "失败",
    RunStatus.CANCELLED: "已取消",
    RunStatus.INTERRUPTED: "已中断",
}
_RUN_STATE_LABELS = {
    RunArchiveState.USABLE: "可用",
    RunArchiveState.INCOMPLETE: "结构不完整",
    RunArchiveState.INVALID: "不可用",
}
_ARTIFACT_KIND_LABELS = {ArtifactKind.CSV: "CSV", ArtifactKind.REPORT: "报告"}
_ARTIFACT_STATUS_LABELS = {
    ArtifactStatus.PENDING: "待生成",
    ArtifactStatus.COMPLETED: "已生成",
    ArtifactStatus.FAILED: "失败",
    ArtifactStatus.NOT_APPLICABLE: "不适用",
}
UNKNOWN_RUN_STATUS_LABEL = "状态未知"


def artifact_kind_text(kind: ArtifactKind) -> str:
    """交付产物类型的中文名；页面与结果文案共用同一份映射。"""
    return _ARTIFACT_KIND_LABELS.get(kind, kind.value)


def artifact_status_text(status: ArtifactStatus) -> str:
    """交付产物状态的中文名。"""
    return _ARTIFACT_STATUS_LABELS.get(status, status.value)



@dataclass(frozen=True)
class RunHistoryRow:
    """历史运行列表的一行：展示文本 + “能否重新生成”的结构化事实。

    视图模型只把 DTO 转成文案，不读取 manifest、不判断文件是否存在：``can_regenerate``
    直接来自 :class:`application.dto.run_history.RunHistorySummary`。
    """

    run_id: str
    created_at: datetime
    state: RunArchiveState
    run_status_text: str
    stage_text: str
    result_text: str
    artifact_text: str
    problem_text: str = ""
    can_regenerate: bool = False

    @classmethod
    def from_summary(cls, summary: RunHistorySummary) -> "RunHistoryRow":
        """由应用层的运行摘要构造；非正式 DTO 会被明确拒绝。"""
        if not isinstance(summary, RunHistorySummary):
            raise TypeError("历史运行行必须来自 RunHistorySummary")
        return cls(
            run_id=summary.run_id,
            created_at=summary.created_at,
            state=summary.state,
            run_status_text=_run_status_text(summary),
            stage_text=_stage_text(summary),
            result_text=f"{summary.result_count} 个正式结果",
            artifact_text=_artifact_text(summary),
            problem_text=_problem_text(summary),
            can_regenerate=summary.can_regenerate,
        )

    @property
    def created_text(self) -> str:
        return self.created_at.strftime("%Y-%m-%d %H:%M:%S")

    @property
    def state_text(self) -> str:
        return _RUN_STATE_LABELS[self.state]

    @property
    def status_text(self) -> str:
        """运行状态；不可用时同时给出可用性，避免“已完成”看起来仍可浏览。"""
        if self.state is RunArchiveState.USABLE:
            return self.run_status_text
        return f"{self.run_status_text}（{self.state_text}）"

    @property
    def browsable(self) -> bool:
        return self.state.browsable


def _run_status_text(summary: RunHistorySummary) -> str:
    if summary.run_status is None:
        return UNKNOWN_RUN_STATUS_LABEL
    return _RUN_STATUS_LABELS.get(summary.run_status, summary.run_status.value)


def _stage_text(summary: RunHistorySummary) -> str:
    if not summary.stages:
        return "—"
    return f"{summary.finished_stages}/{summary.total_stages} 已完成"


def _artifact_text(summary: RunHistorySummary) -> str:
    if not summary.artifacts:
        return "无交付产物"
    return "、".join(
        f"{artifact_kind_text(item.kind)} {artifact_status_text(item.status)}"
        for item in summary.artifacts
    )


def _problem_text(summary: RunHistorySummary) -> str:
    """说明列：运行级阻塞原因 + 每个失败产物的原因。

    失败原因来自 manifest 登记的错误文本，只作为纯文本展示；页面不据此判断文件状态。
    """
    parts = [summary.problem] if summary.problem else []
    parts.extend(
        f"{artifact_kind_text(item.kind)} 失败: {item.error or '未记录原因'}"
        for item in summary.failed_artifacts
    )
    return "；".join(parts)


@dataclass(frozen=True)
class LoadedHistoryResult:
    """一条已加载的历史结果：正式 DTO 加**不可变**绘图视图。

    ``series`` 是冻结结构（映射代理 + 元组），调用方无法通过它改动视图模型缓存；
    需要可变副本（绘图控件、聊天上下文）时使用 :meth:`series_snapshot` 或
    :meth:`sweep_data_at`，它们每次返回新的列表副本。
    """

    filename: str
    result: MeasurementResult
    series: Mapping[str, Mapping[str, tuple[Any, ...]]]
    frequencies: tuple[float, ...]

    @property
    def kind(self) -> str:
        return result_kind(self.result)

    @property
    def label(self) -> str:
        return "功放测试" if self.result.is_amplifier else "驱动映射"

    def columns_at(self, index: int) -> Mapping[str, tuple[Any, ...]] | None:
        """返回指定频率的冻结列；越界或缺少频率时返回 ``None``。"""
        if not 0 <= index < len(self.frequencies):
            return None
        key = normalize_frequency_key(self.frequencies[index])
        return self.series.get(key) if key else None

    def sweep_data_at(self, index: int) -> dict[str, list[Any]] | None:
        """返回指定频率的 ``sweep_data`` 可变副本；越界时返回 ``None``。"""
        columns = self.columns_at(index)
        if columns is None:
            return None
        return {name: list(values) for name, values in columns.items()}

    def plot_record(self, index: int) -> dict[str, Any] | None:
        """返回绘图控件需要的 ``{"frequency", "sweep_data"}`` 新副本。"""
        sweep_data = self.sweep_data_at(index)
        if sweep_data is None:
            return None
        return {"frequency": self.frequencies[index], "sweep_data": sweep_data}

    def series_snapshot(self) -> dict[str, dict[str, Any]]:
        """返回整份绘图视图的可变副本（按频率分组），不共享内部结构。"""
        return {
            key: {"sweep_data": {name: list(values) for name, values in columns.items()}}
            for key, columns in self.series.items()
        }


class RunSummarySource(Protocol):
    """历史运行摘要来源（由 ``application.run_history_service.RunHistoryService`` 提供）。"""

    def list_runs(self) -> tuple[RunHistorySummary, ...]: ...


class HistoryViewModel:
    """只读历史视图模型：结果列表、运行摘要、选中项、已加载结果与频率浏览。

    视图模型不持有 Qt 控件，也不接收路径：结果列表来自
    ``ResultQueryPort.list_results()``，运行摘要来自 ``RunSummarySource.list_runs()``，
    加载只经过 ``load()`` / ``latest_result()``。
    """

    def __init__(
        self,
        query: ResultQueryPort,
        *,
        runs: RunSummarySource | None = None,
    ):
        self.query = query
        self._rows: tuple[HistoryRow, ...] = ()
        self._selected_index: int | None = None
        self._loaded: LoadedHistoryResult | None = None
        self._frequency_index = 0
        # 历史运行摘要来源是可选依赖：可视化页只需要结果列表，历史管理页两者都要。
        self.run_source = runs
        self._run_rows: tuple[RunHistoryRow, ...] = ()
        self._selected_run_index: int | None = None

    # -- 列表 ---------------------------------------------------------------

    @property
    def rows(self) -> tuple[HistoryRow, ...]:
        """当前历史列表（只读）。"""
        return self._rows

    def reload(self) -> tuple[HistoryRow, ...]:
        """重新读取历史列表；选中项已不存在时清空，不保留陈旧选择。"""
        self._rows = tuple(
            HistoryRow.from_dto(row) for row in self.query.list_results()
        )
        if self._selected_index is not None and not self.row_at(self._selected_index):
            self._selected_index = None
        return self._rows

    def row_at(self, index: int | None) -> HistoryRow | None:
        if not isinstance(index, int) or isinstance(index, bool):
            return None
        if 0 <= index < len(self._rows):
            return self._rows[index]
        return None

    def select(self, index: int | None) -> HistoryRow | None:
        """选中一行；非法或空行索引会清空选择。"""
        row = self.row_at(index)
        self._selected_index = None if row is None else index
        return row

    @property
    def selected_index(self) -> int | None:
        return self._selected_index

    @property
    def selected(self) -> HistoryRow | None:
        return self.row_at(self._selected_index)

    # -- 运行摘要 -----------------------------------------------------------

    @property
    def run_rows(self) -> tuple[RunHistoryRow, ...]:
        """当前历史运行列表（只读）。"""
        return self._run_rows

    def reload_runs(self) -> tuple[RunHistoryRow, ...]:
        """重新读取历史运行摘要；选中运行已不存在时清空选择。"""
        if self.run_source is None:
            raise RuntimeError("未配置历史运行摘要来源，无法读取运行列表")
        self._run_rows = tuple(
            RunHistoryRow.from_summary(summary)
            for summary in self.run_source.list_runs()
        )
        if (
            self._selected_run_index is not None
            and not self.run_at(self._selected_run_index)
        ):
            self._selected_run_index = None
        return self._run_rows

    def run_at(self, index: int | None) -> RunHistoryRow | None:
        if not isinstance(index, int) or isinstance(index, bool):
            return None
        if 0 <= index < len(self._run_rows):
            return self._run_rows[index]
        return None

    def select_run(self, index: int | None) -> RunHistoryRow | None:
        """选中一个运行；非法或空行索引会清空选择。"""
        row = self.run_at(index)
        self._selected_run_index = None if row is None else index
        return row

    @property
    def selected_run_index(self) -> int | None:
        return self._selected_run_index

    @property
    def selected_run(self) -> RunHistoryRow | None:
        return self.run_at(self._selected_run_index)

    # -- 加载 ---------------------------------------------------------------

    def show(self, loaded: LoadedResultDto) -> LoadedHistoryResult:
        """登记一条已加载的正式结果，并切回第一个频率。"""
        if not isinstance(loaded, LoadedResultDto):
            raise TypeError("历史加载必须返回 LoadedResultDto")
        series, frequencies = result_plot_series(loaded.result)
        if not frequencies:
            raise ValueError("未找到有效的频率数据")
        self._loaded = LoadedHistoryResult(
            filename=loaded.filename,
            result=loaded.result,
            series=_freeze_series(series),
            frequencies=tuple(frequencies),
        )
        self._frequency_index = 0
        return self._loaded

    def load_selected(self) -> LoadedHistoryResult:
        """读取当前选中的历史结果；未选中时抛出 ``ValueError``。"""
        row = self.selected
        if row is None:
            raise ValueError("请先在历史列表中选择一条结果")
        return self.show(self.query.load(row.result_id))

    def load_latest(self, result_type: str = AMPLIFIER_RESULT_TYPE) -> LoadedHistoryResult:
        """读取最新可校验结果；没有可用结果时抛出 ``FileNotFoundError``。"""
        return self.show(self.query.latest_result(result_type))

    def clear(self) -> None:
        """清空已加载结果，回到“未加载数据”状态。"""
        self._loaded = None
        self._frequency_index = 0

    @property
    def loaded(self) -> LoadedHistoryResult | None:
        return self._loaded

    # -- 频率浏览 -----------------------------------------------------------

    @property
    def frequency_count(self) -> int:
        return 0 if self._loaded is None else len(self._loaded.frequencies)

    @property
    def frequency_index(self) -> int:
        return self._frequency_index

    @property
    def current_frequency(self) -> float | None:
        loaded = self._loaded
        if loaded is None or not loaded.frequencies:
            return None
        return loaded.frequencies[self._frequency_index]

    @property
    def current_series(self) -> dict[str, list[Any]] | None:
        """当前频率的 ``sweep_data`` 副本；每次访问都返回新列表。"""
        if self._loaded is None:
            return None
        return self._loaded.sweep_data_at(self._frequency_index)

    @property
    def has_previous(self) -> bool:
        return self._frequency_index > 0

    @property
    def has_next(self) -> bool:
        return self._frequency_index < self.frequency_count - 1

    def previous_frequency(self) -> bool:
        """切到更低频率；已在第一个频率时返回 ``False``。"""
        if not self.has_previous:
            return False
        self._frequency_index -= 1
        return True

    def next_frequency(self) -> bool:
        """切到更高频率；已在最后一个频率时返回 ``False``。"""
        if not self.has_next:
            return False
        self._frequency_index += 1
        return True


__all__ = [
    "AMPLIFIER_RESULT_TYPE",
    "HistoryRow",
    "HistoryViewModel",
    "LoadedHistoryResult",
    "RunHistoryRow",
    "RunSummarySource",
    "UNKNOWN_RUN_STATUS_LABEL",
    "artifact_kind_text",
    "artifact_status_text",
    "result_kind",
    "result_plot_series",
]
