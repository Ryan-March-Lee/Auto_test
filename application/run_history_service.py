"""历史运行管理的应用用例（重构计划步骤 7）。

把“导出页面”收敛成“历史运行/结果管理”后，页面需要三件事：

1. **列出历史运行及其阶段/产物状态**：事实来自 :class:`RunArchiveStatusPort`
   （只读 manifest 与运行目录），页面不接触路径，也不判断文件是否存在；
2. **补生成交付产物**：缺失的 ``completed`` 产物按 manifest 登记的校验值原样重建，
   ``failed`` 产物显式重试一次；两者都复用 :class:`application.archival_service.
   ResultArchivalService`，把产物写回**原运行目录**的受控路径，而不是另存副本
   （另存副本走 :class:`application.export_service.ResultExportUseCase`）；
3. **删除用户选中的历史运行**：整目录删除，只接受历史列表中的运行标识；进行中的
   运行明确拒绝，未知运行与单个删除失败按运行逐项返回，不影响同批次的其它运行；
4. **按结果定位最终报告**：数据可视化页加载一条历史结果后，用例按结果的**来源**
   定位报告：阶段目录结果按测量身份在阶段目录中查找 ``<id>_report.html``（同目录
   ``<id>.csv`` 由报告自行链接）；旧运行目录里的完整结果即使带 ``measurement_id``，
   仍按来源结果精确匹配该运行的归档报告。返回“可用 / 不适用 / 缺失 / 不可用”的
   结构化状态，页面只消费状态与位置。

边界：

- 重新生成只接受历史列表中的 ``run_id``：用例自己重新列一遍运行摘要并核对，页面无法
  用任意字符串触发归档；阶段取自已登记产物所属的阶段，不接受调用方指定。
- 删除同样先核对历史列表，并额外依赖独立的 :class:`RunDeletionPort`：历史浏览、
  导出与位置端口仍是只读的，删除能力不能从查询对象上“顺手”获得。
- 用例不读文件、不拼路径、不生成页面文案：产物路径与失败原因都在
  :class:`application.dto.archival.ArchivalOutcome` 里，措辞由界面层生成；删除的
  逐项失败原因由删除端口给出，页面负责展示。报告定位同样只返回位置与原因，用户
  可见文案属于页面。
- 运行不可用、没有失败产物、未装配归档/删除服务或归档契约失败，统一抛出
  :class:`RunHistoryRequestError`；其它异常（端口实现缺陷）不在这里兜底。
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from application.archival_service import ResultArchivalService
from application.dto.archival import (
    ArchivalOutcome,
    ArchivalRequest,
    ArtifactRepairOutcome,
)
from application.dto.run_deletion import RunDeletionFailure, RunDeletionOutcome
from application.dto.run_history import (
    ResultReportLocation,
    ResultReportState,
    RunHistorySummary,
)
from application.ports.gui_data_service import (
    RESULT_SOURCE_STAGE,
    ResultQueryPort,
    RunArchiveStatusPort,
    RunDeletionPort,
    RunLocationPort,
)
from application.ports.result_artifacts import ArtifactGenerationError
from application.ports.stage_result_repository import measurement_stage_for_type
from domain.run.models import ArtifactKind, RunStatus

logger = logging.getLogger("pa_auto_test." + __name__)


class RunHistoryRequestError(ValueError):
    """历史运行请求无法执行：运行不可用、不在历史列表中，或没有可重新生成的产物。"""


class RunHistoryService:
    """历史运行查询、交付产物补生成与用户删除的用例入口。"""

    def __init__(
        self,
        *,
        statuses: RunArchiveStatusPort,
        archival: ResultArchivalService | None = None,
        locations: RunLocationPort | None = None,
        deletions: RunDeletionPort | None = None,
        results: ResultQueryPort | None = None,
    ) -> None:
        if not callable(getattr(statuses, "list_run_summaries", None)):
            raise TypeError("历史运行服务需要状态端口提供 list_run_summaries()")
        if archival is not None and not callable(getattr(archival, "archive", None)):
            raise TypeError("历史运行服务的归档入口必须提供 archive()")
        if locations is not None and not callable(
            getattr(locations, "artifact_location", None)
        ):
            raise TypeError("历史运行服务的位置入口必须提供 artifact_location()")
        if deletions is not None and not callable(
            getattr(deletions, "delete_run", None)
        ):
            raise TypeError("历史运行服务的删除入口必须提供 delete_run(run_id)")
        if results is not None and (
            not callable(getattr(results, "list_results", None))
            or not callable(getattr(results, "load", None))
        ):
            raise TypeError(
                "历史运行服务的结果查询入口必须提供 list_results() 与 load(result_id)"
            )
        self.statuses = statuses
        self.archival = archival
        self.locations = locations
        self.deletions = deletions
        self.results = results

    # -- 查询 ---------------------------------------------------------------

    def list_runs(self) -> tuple[RunHistorySummary, ...]:
        """返回历史运行摘要；读取失败按请求错误上报，不返回半份列表。"""
        try:
            summaries = self.statuses.list_run_summaries()
        except (OSError, ValueError) as error:
            raise RunHistoryRequestError(f"无法读取历史运行列表: {error}") from error
        return tuple(summaries)

    # -- 重新生成 -----------------------------------------------------------

    def regenerate_failed_artifacts(self, run_id: str) -> ArchivalOutcome:
        """对已登记运行重新生成失败的交付产物，写回原运行目录。

        ``run_id`` 必须先出现在历史列表中：这样“重新生成”只能基于已登记的运行，
        不能把任意字符串或运行目录外的位置当作输入。
        """
        summary = self._require_run(run_id)
        stage = summary.regeneration_stage
        if stage is None:
            raise RunHistoryRequestError(
                f"运行 {summary.run_id} 没有失败的交付产物，无需重新生成"
            )
        if self.archival is None:
            raise RunHistoryRequestError("未装配归档服务，无法重新生成交付产物")
        try:
            return self.archival.archive(
                ArchivalRequest(run_id=summary.run_id, stage=stage),
                retry_failed=True,
            )
        except (OSError, ValueError) as error:
            # ArchivalRequestError 是 ValueError；manifest 写回失败是 OSError。这里只把
            # 可预期的归档失败转成历史请求错误，正式结果与 manifest 保持原样。
            raise RunHistoryRequestError(
                f"运行 {summary.run_id} 的产物重新生成失败: {error}"
            ) from error

    def repair_artifacts(self, run_id: str) -> ArtifactRepairOutcome:
        """自动补生成已登记运行中缺失或失败的交付产物。

        与 :meth:`regenerate_failed_artifacts` 不同，本入口同时处理“文件被误删”：
        ``completed`` 记录的文件缺失时按 manifest 登记的校验值原样重建，不重跑测量，
        也不改写 ``completed`` 记录。运行必须先出现在历史列表中；运行不可用、没有
        归档服务或归档契约失败时抛出 :class:`RunHistoryRequestError`。
        """
        summary = self._require_run(run_id)
        if self.archival is None:
            raise RunHistoryRequestError("未装配归档服务，无法补生成交付产物")
        repair = getattr(self.archival, "repair_artifacts", None)
        if not callable(repair):
            raise RunHistoryRequestError("归档服务不支持补生成交付产物")
        try:
            return repair(summary.run_id)
        except (OSError, ValueError) as error:
            raise RunHistoryRequestError(
                f"运行 {summary.run_id} 的交付产物补生成失败: {error}"
            ) from error

    # -- 删除 ---------------------------------------------------------------

    def delete_runs(self, run_ids: Iterable[str]) -> RunDeletionOutcome:
        """删除选中的历史运行目录。

        ``run_ids`` 必须是历史列表中已登记的运行标识：用例先重新列出摘要并核对，
        页面无法用任意字符串触发目录删除。进行中的运行、未知运行与单个删除失败
        按 :class:`RunDeletionFailure` 逐项返回，**不中断**同一批次的其它运行。
        """
        normalized = _unique_run_ids(run_ids)
        if self.deletions is None:
            raise RunHistoryRequestError("未装配运行删除入口，无法删除历史运行")
        summaries = {summary.run_id: summary for summary in self.list_runs()}
        deleted: list[str] = []
        failures: list[RunDeletionFailure] = []
        for run_id in normalized:
            summary = summaries.get(run_id)
            if summary is None:
                failures.append(
                    RunDeletionFailure(run_id, "运行不在历史列表中")
                )
                continue
            if summary.run_status is RunStatus.RUNNING:
                failures.append(
                    RunDeletionFailure(run_id, "运行正在进行中，不能删除")
                )
                continue
            try:
                self.deletions.delete_run(run_id)
            except (OSError, ValueError) as error:
                logger.warning("删除历史运行 %s 失败: %s", run_id, error)
                failures.append(
                    RunDeletionFailure(run_id, f"删除运行目录失败: {error}")
                )
            else:
                logger.info("已删除历史运行 %s", run_id)
                deleted.append(run_id)
        return RunDeletionOutcome(tuple(deleted), tuple(failures))

    # -- 位置定位 -----------------------------------------------------------

    def artifact_location(
        self,
        run_id: str,
        kind: ArtifactKind,
        *,
        source_result: str | None = None,
    ) -> str | None:
        """返回历史运行中某类交付产物的本地位置；没有可用产物时返回 ``None``。"""
        summary = self._require_run(run_id)
        if not isinstance(kind, ArtifactKind):
            raise RunHistoryRequestError(f"未知的交付产物类型: {kind!r}")
        if source_result is not None and (
            not isinstance(source_result, str) or not source_result.strip()
        ):
            raise RunHistoryRequestError("来源结果标识必须是非空字符串或 None")
        if self.locations is None:
            raise RunHistoryRequestError("未装配运行位置入口，无法打开交付产物")
        try:
            target = self.locations.artifact_location(
                summary.run_id, kind, source_result=source_result
            )
        except (OSError, ValueError) as error:
            raise RunHistoryRequestError(
                f"运行 {summary.run_id} 的{kind.value}产物位置不可用: {error}"
            ) from error
        return target if isinstance(target, str) and target.strip() else None

    def report_for_result(self, result_id: str) -> ResultReportLocation:
        """按历史结果定位它的最终报告。

        结果不在历史列表中属于调用错误，直接抛 :class:`RunHistoryRequestError`；
        结果不生成报告、报告缺失或位置入口未装配属于可展示状态，用结构化结果返回。

        定位规则（重构计划步骤 20）：

        - **来源决定定位方式**：阶段目录结果按测量身份在阶段目录中查找
          ``<id>_report.html``，同目录的 ``<id>.csv`` 由报告自行链接；旧运行目录里的
          完整结果即使带 ``measurement_id``，报告仍在原运行目录，继续按“运行 +
          运行内来源路径”精确匹配归档报告；
        - 两条路径都不退回该运行的第一份报告，也不把另一份测量的报告当成目标。

        阶段结果缺少测量身份、或定位入口未装配时明确返回不可用，不猜测归属。
        """
        if not isinstance(result_id, str) or not result_id.strip():
            raise RunHistoryRequestError("打开测试报告必须指定历史结果标识")
        if self.results is None:
            return ResultReportLocation(
                result_id,
                ResultReportState.UNAVAILABLE,
                reason="未装配历史结果查询，无法定位测试报告",
            )
        try:
            rows = self.results.list_results()
        except (OSError, ValueError) as error:
            raise RunHistoryRequestError(f"无法读取历史结果列表: {error}") from error
        row = next((item for item in rows if item.result_id == result_id), None)
        if row is None:
            raise RunHistoryRequestError(
                f"结果不在历史列表中，无法打开测试报告: {result_id}"
            )
        if not row.run_id and row.source != RESULT_SOURCE_STAGE:
            return ResultReportLocation(
                result_id,
                ResultReportState.UNAVAILABLE,
                reason="历史结果没有登记所属运行，无法定位测试报告",
            )
        try:
            loaded = self.results.load(result_id)
        except (OSError, ValueError) as error:
            return ResultReportLocation(
                result_id,
                ResultReportState.UNAVAILABLE,
                reason=f"无法读取历史结果: {error}",
            )
        if not loaded.result.is_amplifier:
            return ResultReportLocation(
                result_id,
                ResultReportState.NOT_APPLICABLE,
                reason="只有主功放测试结果会生成测试报告",
            )
        measurement_id = loaded.result.measurement_id
        if row.source == RESULT_SOURCE_STAGE:
            # 阶段目录结果只能按自身测量身份定位同目录报告；缺少身份说明读取实现
            # 违反了契约，不能退回运行目录或“第一份报告”去猜归属。
            if measurement_id is None:
                return ResultReportLocation(
                    result_id,
                    ResultReportState.UNAVAILABLE,
                    reason="阶段结果缺少测量身份，无法定位同目录测试报告",
                )
            return self._report_for_stage_result(
                result_id, loaded.result.measurement_type, measurement_id
            )
        # 旧运行目录里的完整结果即使带测量身份，报告仍在原运行目录：定位方式由
        # 来源决定，不能因为字段存在就改去阶段目录查找，否则会误报缺失或命中另一份。
        if row.source_result is None:
            return ResultReportLocation(
                result_id,
                ResultReportState.UNAVAILABLE,
                reason="历史结果没有登记运行内来源路径，无法确认哪份报告属于它",
            )
        if self.locations is None:
            return ResultReportLocation(
                result_id,
                ResultReportState.UNAVAILABLE,
                reason="未装配运行位置入口，无法打开测试报告",
            )
        try:
            # 直接走只读位置端口：结果列表已经给出运行标识，不再重复读取全部运行摘要。
            target = self.locations.artifact_location(
                row.run_id,
                ArtifactKind.REPORT,
                source_result=row.source_result,
            )
        except (OSError, ValueError) as error:
            return ResultReportLocation(
                result_id,
                ResultReportState.UNAVAILABLE,
                reason=f"无法定位测试报告: {error}",
            )
        if target is None:
            return ResultReportLocation(
                result_id,
                ResultReportState.MISSING,
                reason=(
                    "该运行的报告未生成或文件缺失，"
                    "可用“另存为 → 测试报告…”生成副本"
                ),
            )
        return ResultReportLocation(
            result_id,
            ResultReportState.AVAILABLE,
            location=target,
        )

    def _report_for_stage_result(
        self,
        result_id: str,
        measurement_type: Any,
        measurement_id: str,
    ) -> ResultReportLocation:
        """在阶段目录中按测量身份定位 ``<id>_report.html``。"""
        locate = getattr(self.results, "stage_artifact_path", None)
        if not callable(locate):
            return ResultReportLocation(
                result_id,
                ResultReportState.UNAVAILABLE,
                reason="未装配阶段产物定位，无法打开阶段结果报告",
            )
        try:
            stage = measurement_stage_for_type(measurement_type)
            target = locate(stage, measurement_id, ArtifactKind.REPORT)
        except (ArtifactGenerationError, OSError, ValueError) as error:
            # 定位参数非法、阶段目录边界不成立等属于“无法定位”，不是“缺失”：
            # 页面据此提示不可用，而不是让用户以为文件只是尚未生成。
            return ResultReportLocation(
                result_id,
                ResultReportState.UNAVAILABLE,
                reason=f"无法定位阶段结果报告: {error}",
            )
        if target is None:
            return ResultReportLocation(
                result_id,
                ResultReportState.MISSING,
                reason=(
                    "该阶段结果的报告未生成或文件缺失，"
                    "可用“另存为 → 测试报告…”生成副本"
                ),
            )
        return ResultReportLocation(
            result_id,
            ResultReportState.AVAILABLE,
            location=target,
        )

    def _require_run(self, run_id: str) -> RunHistorySummary:
        """确认 ``run_id`` 是历史列表中可用的一次运行。"""
        if not isinstance(run_id, str) or not run_id.strip():
            raise RunHistoryRequestError("重新生成必须指定运行标识")
        for summary in self.list_runs():
            if summary.run_id != run_id:
                continue
            if not summary.browsable:
                raise RunHistoryRequestError(
                    f"运行 {run_id} 不可用，不能重新生成产物: {summary.problem}"
                )
            return summary
        raise RunHistoryRequestError(f"运行不在历史列表中: {run_id}")


def _unique_run_ids(value: Any) -> tuple[str, ...]:
    """把调用方输入规范成去重后的运行标识序列；结构非法时直接拒绝。"""
    if isinstance(value, (str, bytes)):
        raise RunHistoryRequestError(
            "删除历史运行必须传入运行标识列表，而不是单个字符串"
        )
    try:
        candidates = tuple(value)
    except TypeError as error:
        raise RunHistoryRequestError("删除历史运行必须传入运行标识列表") from error
    if not candidates:
        raise RunHistoryRequestError("删除历史运行必须至少指定一个运行标识")
    unique: list[str] = []
    for run_id in candidates:
        if not isinstance(run_id, str) or not run_id.strip():
            raise RunHistoryRequestError(f"运行标识不合法: {run_id!r}")
        if run_id not in unique:
            unique.append(run_id)
    return tuple(unique)


__all__ = ["RunHistoryRequestError", "RunHistoryService"]
