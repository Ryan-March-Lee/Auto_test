"""历史运行管理的应用用例（重构计划步骤 7）。

把“导出页面”收敛成“历史运行/结果管理”后，页面需要两件事：

1. **列出历史运行及其阶段/产物状态**：事实来自 :class:`RunArchiveStatusPort`
   （只读 manifest 与运行目录），页面不接触路径，也不判断文件是否存在；
2. **重新生成失败的交付产物**：复用 :class:`application.archival_service.
   ResultArchivalService` 的 ``retry_failed=True`` 入口，把产物写回**原运行目录**的
   受控路径，而不是另存副本（另存副本走 :class:`application.export_service.
   ResultExportUseCase`）。

边界：

- 重新生成只接受历史列表中的 ``run_id``：用例自己重新列一遍运行摘要并核对，页面无法
  用任意字符串触发归档；阶段取自已登记产物所属的阶段，不接受调用方指定。
- 用例不读文件、不拼路径、不生成页面文案：产物路径与失败原因都在
  :class:`application.dto.archival.ArchivalOutcome` 里，措辞由界面层生成。
- 运行不可用、没有失败产物、未装配归档服务或归档契约失败，统一抛出
  :class:`RunHistoryRequestError`；其它异常（端口实现缺陷）不在这里兜底。
"""

from __future__ import annotations

from application.archival_service import ResultArchivalService
from application.dto.archival import ArchivalOutcome, ArchivalRequest
from application.dto.run_history import RunHistorySummary
from application.ports.gui_data_service import RunArchiveStatusPort, RunLocationPort
from domain.run.models import ArtifactKind


class RunHistoryRequestError(ValueError):
    """历史运行请求无法执行：运行不可用、不在历史列表中，或没有可重新生成的产物。"""


class RunHistoryService:
    """历史运行查询与失败产物重新生成的用例入口。"""

    def __init__(
        self,
        *,
        statuses: RunArchiveStatusPort,
        archival: ResultArchivalService | None = None,
        locations: RunLocationPort | None = None,
    ) -> None:
        if not callable(getattr(statuses, "list_run_summaries", None)):
            raise TypeError("历史运行服务需要状态端口提供 list_run_summaries()")
        if archival is not None and not callable(getattr(archival, "archive", None)):
            raise TypeError("历史运行服务的归档入口必须提供 archive()")
        if locations is not None and (
            not callable(getattr(locations, "run_directory", None))
            or not callable(getattr(locations, "artifact_location", None))
        ):
            raise TypeError(
                "历史运行服务的位置入口必须提供 run_directory() 与 artifact_location()"
            )
        self.statuses = statuses
        self.archival = archival
        self.locations = locations

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

    # -- 位置定位 -----------------------------------------------------------

    def run_directory(self, run_id: str) -> str:
        """返回历史运行的本地目录；只接受历史列表中可浏览的运行。"""
        summary = self._require_run(run_id)
        if self.locations is None:
            raise RunHistoryRequestError("未装配运行位置入口，无法打开运行目录")
        try:
            target = self.locations.run_directory(summary.run_id)
        except (OSError, ValueError) as error:
            raise RunHistoryRequestError(
                f"运行 {summary.run_id} 的目录不可用: {error}"
            ) from error
        if not isinstance(target, str) or not target.strip():
            raise RunHistoryRequestError(f"运行 {summary.run_id} 没有可打开的目录")
        return target

    def artifact_location(self, run_id: str, kind: ArtifactKind) -> str | None:
        """返回历史运行中某类交付产物的本地位置；没有可用产物时返回 ``None``。"""
        summary = self._require_run(run_id)
        if not isinstance(kind, ArtifactKind):
            raise RunHistoryRequestError(f"未知的交付产物类型: {kind!r}")
        if self.locations is None:
            raise RunHistoryRequestError("未装配运行位置入口，无法打开交付产物")
        try:
            target = self.locations.artifact_location(summary.run_id, kind)
        except (OSError, ValueError) as error:
            raise RunHistoryRequestError(
                f"运行 {summary.run_id} 的{kind.value}产物位置不可用: {error}"
            ) from error
        return target if isinstance(target, str) and target.strip() else None

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


__all__ = ["RunHistoryRequestError", "RunHistoryService"]
