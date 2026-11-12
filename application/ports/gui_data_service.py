"""GUI-facing application contracts and data transfer objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Protocol

from application.dto.archival import ArchivalOutcome, ArtifactRepairOutcome
from application.dto.export_requests import ExportOutcome, ExportRequest
from application.dto.measurement_results import MeasurementResult
from application.dto.run_deletion import RunDeletionOutcome
from application.dto.run_history import ResultReportLocation, RunHistorySummary
from domain.run.models import ArtifactKind

from .stage_result_repository import MeasurementStage


# 历史结果来自两个来源：
# - ``stage``：独立阶段目录里的正式结果（重构计划步骤 19 起的浏览主来源）；
# - ``run``：旧运行目录里的完整正式结果文档，按只读兼容分支保留。
# 旧全局兼容副本仍不参与历史查看。
RESULT_SOURCE_RUN = "run"
RESULT_SOURCE_STAGE = "stage"


@dataclass(frozen=True)
class ResultFileDto:
    result_id: str
    filename: str
    file_type: str
    modified_at: datetime
    # ``stage`` 表示独立阶段目录结果，``run`` 表示旧运行目录里的完整结果文档。
    source: str = RESULT_SOURCE_RUN
    # 产生这份结果的运行（阶段结果的来源运行 / 旧运行结果的所属运行）。
    run_id: str | None = None
    # 旧运行结果在运行目录内的相对路径；阶段目录结果没有运行内路径，因此为 ``None``。
    # 定位同一次运行的交付产物时使用，不是文件系统绝对路径，页面仍然只拿结果标识。
    # 为 ``None`` 时产物定位不得退回“该运行第一份产物”，只能明确报告无法归属。
    source_result: str | None = None


@dataclass(frozen=True)
class LoadedResultDto:
    """一条已解析的正式历史结果。

    ``result`` 已经是 ``application.dto`` 的正式结果对象：调用方拿到它就可以直接
    展示、绘图或导出，不需要也不允许再把结果当成 ``Mapping[str, Any]`` 读取。
    原始 JSON 只通过 ``ResultQueryPort.read_bytes()`` 逐字节导出。

    ``sha256`` / ``size_bytes`` 是加载时读取到的文件事实。按阶段文件显式选择结果时，
    调用方把这两个值带进启动链路，应用层据此复核文件在选择之后没有被替换；历史浏览
    入口不要求它们，因此保持可选。
    """

    result_id: str
    filename: str
    result: MeasurementResult
    # 已加载结果所属运行与运行内相对路径：报告定位只按这两个事实查产物，
    # 页面不解析 ``result_id``，也不接触文件系统路径。
    run_id: str | None = None
    source_result: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None


class ConfigurationPort(Protocol):
    """正式用户配置的读写契约：配置编辑的唯一保存入口。

    ``save()`` 只接受调用方已经编写好（authoring）的正式配置文档：校验发生在保存之前，
    持久化只负责原样落盘，因此实现既不解释 schema，也不修补内容。运行快照不属于本端口：
    ``runs/<run_id>/`` 下的冻结配置由运行仓储创建，不经过这里。
    """

    def load(self) -> Mapping[str, Any]: ...
    def save(self, configuration: Mapping[str, Any]) -> None: ...


class ResultQueryPort(Protocol):
    """历史结果查看/导出的只读查询契约。

    该契约服务于结果浏览与用户显式选择的阶段结果加载，不提供隐式“最新结果”作为
    正式测量输入。查询对象以 ``result_id`` 标识结果，路径解析和目录边界校验由基础
    设施实现负责；``load_stage_result()`` 只接受调用方明确给出的阶段目录文件。

    浏览主来源是独立阶段目录：``list_results()`` 直接扫描三类阶段目录并解析正式
    结果 DTO，``result_id`` 编码阶段与测量身份（``stage::<阶段>::<measurement_id>``）；
    旧运行目录中仍保留的完整结果文档通过只读兼容分支继续列出，使用内部
    ``run::<run_id>::<相对路径>`` 标识。运行目录里的阶段结果引用指针不再单独列出，
    对应的正式结果由阶段目录扫描给出，避免同一条结果出现两次。

    查询结果一律是已解析的正式结果 DTO：非正式 schema、损坏、未完成、类型与阶段/
    运行登记不一致的文档不会出现在列表中，也不会被 ``load()`` 或
    ``load_stage_result()`` 返回。
    """

    def list_results(self) -> list[ResultFileDto]: ...
    def load(self, result_id: str) -> LoadedResultDto: ...
    def read_bytes(self, result_id: str) -> bytes:
        """逐字节读取结果原始文档，供 JSON 导出使用。

        返回内容必须对应 ``load(result_id)`` 校验过的那份结果：实现应复用加载时
        读取的同一份字节，不得在校验之后重读可能已被替换的文件。无法保证一致时
        必须明确失败，而不是返回未经校验的新内容。
        """
        ...
    def load_stage_result(
        self, stage: MeasurementStage | str, path: str
    ) -> LoadedResultDto:
        """按用户显式选择的阶段目录文件加载正式结果。

        ``path`` 必须直接位于该阶段的独立结果目录；阶段边界、JSON、结果类型、
        schema 和状态由阶段结果仓库校验。实现不扫描目录、不查找最新文件，失败时
        抛出 ``ValueError``（含 ``StageResultError``）或 ``OSError``。返回值必须
        携带同一次读取得到的 ``sha256`` 与 ``size_bytes``，供启动链路复核文件在
        用户选择之后没有被替换。
        """
        ...

    def latest_result(self, result_type: str) -> LoadedResultDto:
        """返回最新的可校验运行目录结果；没有结果时抛出 ``FileNotFoundError``。

        仅供非测量浏览的兼容入口使用；正式测量的输入必须由用户显式选择。
        """


class ResultExportPort(Protocol):
    """导出实现端口：只接受结果标识，输出位置由实现决定或由调用方给定。

    ``export_report`` 必须返回生成报告的实际位置（非空字符串）；返回 ``None`` 或其它
    类型会被应用用例判为失败，避免把“没有产物”报告成成功。
    """

    def export_json(self, result_id: str, target: str) -> None: ...
    def export_csv(self, result_id: str, target: str) -> None: ...
    def export_report(self, result_id: str) -> str:
        """生成测试汇总报告并返回**实际**产物位置。"""


class ResultExportServicePort(Protocol):
    """导出用例入口：页面只构造 ``ExportRequest``，由应用层执行。"""

    def execute(self, request: ExportRequest) -> ExportOutcome:
        """执行一次导出请求，返回结构化导出结果；用户文案由界面层生成。"""


class RunArchiveStatusPort(Protocol):
    """历史运行状态的只读查询端口（运行、阶段与交付产物状态）。

    实现只读 manifest 与运行目录：返回每次运行的可用性、阶段状态和产物状态，不返回
    文件系统路径，也不写入任何结果或产物。``invalid`` 运行同样会被列出（供诊断），
    但它不能被浏览、导出或重新生成。
    """

    def list_run_summaries(self) -> tuple[RunHistorySummary, ...]:
        """返回运行根目录下全部运行的摘要，按创建时间从新到旧排序。"""
        ...


class RunLocationPort(Protocol):
    """历史运行的只读产物位置入口。

    只把已登记运行的交付产物位置交给界面，不读取文件内容，也不判断产物是否可打开；
    数据可视化页据此触发“打开测试报告”，不自行拼接运行目录或产物路径。
    """

    def artifact_location(
        self,
        run_id: str,
        kind: ArtifactKind,
        *,
        source_result: str | None = None,
    ) -> str | None:
        """返回已登记运行中某类交付产物的本地位置；没有该产物时返回 ``None``。

        ``source_result`` 给出运行内相对路径时只匹配该来源结果的产物，避免同一运行
        有多个同类产物时打开错误的一份。
        """
        ...


class RunDeletionPort(Protocol):
    """运行目录的写入口：只删除已登记的单个运行目录。

    历史浏览与导出端口保持只读；删除能力单独注入，避免查询路径获得写权限。
    实现必须拒绝符号链接/目录联接、确认目标位于运行根目录内，并拒绝删除仍在
    运行的运行目录。
    """

    def delete_run(self, run_id: str) -> None:
        """删除 ``run_id`` 对应的整个运行目录；目录不存在时按幂等无操作处理。"""
        ...


class RunHistoryServicePort(Protocol):
    """历史运行管理的应用用例入口（查询 + 补生成 + 位置定位 + 删除）。"""

    def list_runs(self) -> tuple[RunHistorySummary, ...]:
        """返回历史运行摘要列表。"""
        ...

    def regenerate_failed_artifacts(self, run_id: str) -> ArchivalOutcome:
        """重新生成已登记运行中失败的交付产物，写回原运行目录。

        只接受历史列表中的 ``run_id``；运行不可用、没有失败产物或归档失败时抛出
        ``application.run_history_service.RunHistoryRequestError``。
        """

    def repair_artifacts(self, run_id: str) -> ArtifactRepairOutcome:
        """自动补生成运行中缺失或失败的交付产物，写回原运行目录。

        文件缺失的 ``completed`` 产物按 manifest 登记的校验值原样重建；``failed``
        产物显式重试一次。只接受历史列表中的 ``run_id``，运行不可用或归档契约失败时
        抛出 ``RunHistoryRequestError``。
        """
        ...

    def delete_runs(self, run_ids: Iterable[str]) -> RunDeletionOutcome:
        """删除历史运行目录；只接受历史列表中的运行标识。

        进行中的运行、未知运行与删除失败按 ``RunDeletionFailure`` 逐项返回，不中断
        同一批次的其它运行；空列表或非法输入抛出
        ``application.run_history_service.RunHistoryRequestError``。
        """
        ...

    def artifact_location(
        self,
        run_id: str,
        kind: ArtifactKind,
        *,
        source_result: str | None = None,
    ) -> str | None:
        """返回历史运行中某类交付产物的本地位置；没有可用产物时返回 ``None``。"""
        ...

    def report_for_result(self, result_id: str) -> ResultReportLocation:
        """按一条历史结果定位它所属运行的最终报告。

        只返回结构化状态与位置：结果不生成报告、报告缺失或服务未装配时用状态区分，
        页面不解析结果标识，也不判断文件类型或文件是否存在。
        """
        ...


class GuiDataService(Protocol):
    configuration: ConfigurationPort
    results: ResultQueryPort
    exports: ResultExportPort
    export_requests: ResultExportServicePort
    run_history: RunHistoryServicePort
