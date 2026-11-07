"""测量页面共享的归档状态生命周期（步骤 6 的收尾收敛）。

三个测量页面的“等待归档 / 正在保存 / 展示结果 / 终止事件”完全同构，差异只有测量
类型。本模块只保留这份状态机，让页面不再各写一套：任何页面都不读文件、不判断产物
是否存在，也不拼接导出规则，只把状态 DTO 写成运行日志。

页面使用顺序：

```python
self._setup_archival(kind, location_provider=..., run_id_provider=...)
...
self._archival_try_start(
    lambda: controller.start_xxx(command), "……未能启动"
)
```

``_archival_try_start()`` 只在 controller 接受启动后才显示“结束后会自动归档”：被拒绝
的启动不能留下等待外观，但也不能抹掉刚被 controller 上报的失败原因或上一次真实结果。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from .archival_status import (
    TONE_ERROR,
    TONE_WARNING,
    ArchivalSummary,
    summarize_archival,
    summarize_archival_missing,
    summarize_archival_running,
    summarize_archival_saving,
    summarize_test_failure,
    summarize_test_stopped,
)
from .measurement_state import MeasurementKind


class ArchivalPageMixin:
    """把归档状态机从三个测量页面收拢到一处。

    页面负责按钮、表格和接线确认；本类只负责归档状态与日志文案。页面必须在
    ``build_ui`` 之前调用 :meth:`_setup_archival`，归档结果只写运行日志，不再创建
    页面内的归档分组控件。
    """

    def _setup_archival(
        self,
        kind: MeasurementKind,
        *,
        location_provider: Callable[[str, str | None], str | None] | None = None,
        run_id_provider: Callable[[], str | None] | None = None,
        run_provider: Callable[[], Any] | None = None,
    ) -> None:
        """登记归档展示所需的窄依赖。

        ``run_id_provider`` 必须是无副作用的静默查询（不弹窗、不检查仪器），因为
        运行归属检查会在页面显示时调用它。没有提供时退回 ``run_provider``，仅供
        未接入窗口的页面测试使用。

        页面在调用前必须已经设置 ``self._log_callback``：归档文案与页面其他日志
        走同一个出口，避免出现两套日志顺序。
        """
        self._archival_kind = kind
        self._archival_location_provider = location_provider
        self._archival_run_id_provider = run_id_provider
        self._archival_run_provider = run_provider
        # 本次测量是否还在等待自动归档结果；用于区分“状态未知”与成功。
        self._archival_pending = False
        # 是否已经提示“正在保存/生成”，避免每次进度刷新都重复写日志。
        self._archival_saving = False
        # 已显示的终端归档提示计数：用于判断“启动被拒”时是否已经有失败文案。
        self._archival_terminal_events = 0
        # 最近一次归档提示只用于切运行时的清理判断；页面不再持有归档控件。
        self._archival_summary: ArchivalSummary | None = None

    # -- controller 事件 ---------------------------------------------------

    def _connect_archival_signals(self, controller: Any) -> None:
        """连接进度与归档事件；页面在 ``_connect_controller_signals`` 中调用。"""
        self._connect_signal(controller.signals.progress, self._on_archival_progress)
        self._connect_signal(controller.signals.archival, self.show_archival)

    def _archival_is_for_this_page(self) -> bool:
        """当前 controller 的测量类型是否属于本页面；未开始时视为属于。"""
        controller = self.controller
        if controller is None:
            return False
        kind = getattr(getattr(controller, "state", None), "kind", None)
        return kind is None or kind is self._archival_kind

    def _archival_measurement_started(self) -> None:
        """controller 已接受启动：显示“结束后会自动归档”。"""
        self._archival_pending = True
        self._archival_saving = False
        self._show_archival_summary(summarize_archival_running(self._archival_kind))

    def _archival_try_start(self, start: Callable[[], Any], rejection: str) -> bool:
        """执行 controller 启动，只在接受之后进入“等待归档”。

        controller 的启动失败是同步上报的：它可能先发出 ``error`` 事件、再返回
        ``False``。这里记住启动期间是否已经发布过终端提示，避免“拒绝启动”的中性
        外观覆盖刚显示出来的失败原因。
        """
        self._archival_terminal_events = 0
        started = start()
        if started is False:
            if self._archival_terminal_events == 0:
                self._archival_start_rejected(rejection)
            return False
        self._archival_measurement_started()
        return True

    def _archival_start_rejected(self, reason: str | None = None) -> None:
        """启动未被接受：撤销等待状态，但不抹掉上一次真实结果。

        “等待归档”外观只在启动被接受后才出现，因此这里只需清掉等待标志；上一次测量
        已经显示的归档结果仍然成立，不能因为一次被拒绝的启动就清零。
        """
        self._archival_pending = False
        self._archival_saving = False
        if reason:
            self._log_callback(reason)

    def _on_archival_progress(self, value: Any) -> None:
        """进度到 100% 表示扫描结束，随后是保存结果与生成产物：明确告知用户。"""
        if not self._archival_pending or self._archival_saving:
            return
        if not self._archival_is_for_this_page():
            return
        try:
            progress = int(value)
        except (TypeError, ValueError):
            return
        if progress < 100:
            return
        self._archival_saving = True
        self._show_archival_summary(summarize_archival_saving(self._archival_kind))

    def show_archival(self, outcome: Any) -> None:
        """显示自动归档结果：只渲染状态，不读取文件、不拼接导出规则。"""
        if getattr(outcome, "stage", None) != self._archival_kind.value:
            return
        self._archival_pending = False
        self._archival_terminal_events += 1
        try:
            summary = summarize_archival(outcome)
        except (TypeError, ValueError) as error:
            self._show_archival_summary(
                summarize_archival_missing(self._archival_kind, self._current_run_id())
            )
            self._log_callback(f"归档结果无法显示: {error}")
            return
        self._show_archival_summary(summary)

    # -- 终止事件 -----------------------------------------------------------

    def _archival_test_failed(self, message: str) -> None:
        """测试异常：本次没有归档结果。"""
        self._archival_pending = False
        self._archival_saving = False
        self._archival_terminal_events += 1
        self._show_archival_summary(
            summarize_test_failure(self._archival_kind, message)
        )

    def _archival_test_stopped(self, reason: str, partial_path: str | None = None) -> None:
        """停止或紧急停止：本次没有归档结果；有部分结果时说明保存位置。"""
        self._archival_pending = False
        self._archival_saving = False
        self._archival_terminal_events += 1
        self._show_archival_summary(
            summarize_test_stopped(self._archival_kind, reason, partial_path)
        )

    def _partial_result_path(self) -> str | None:
        """从 controller 的停止状态里取本次保存的部分结果路径。

        停止事件到达时，``result`` 信号已经把部分结果交给页面；这里只读状态，
        不触发任何文件访问。
        """
        controller = self.controller
        if controller is None:
            return None
        view = getattr(getattr(controller, "state", None), "view_state", None)
        reference = getattr(view, "result", None)
        value = getattr(reference, "value", None)
        references = getattr(value, "result_reference", None)
        if not isinstance(references, Mapping):
            return None
        path = references.get("archive_path")
        return path.strip() if isinstance(path, str) and path.strip() else None

    def _archival_test_finished(self) -> None:
        """测试完成：已收到归档结果时保留；否则如实提示“状态未知”。"""
        if not self._archival_pending:
            return
        self._archival_pending = False
        self._archival_saving = False
        self._archival_terminal_events += 1
        self._show_archival_summary(
            summarize_archival_missing(self._archival_kind, self._current_run_id())
        )

    # -- 运行切换 -----------------------------------------------------------

    def on_activated(self) -> None:
        """页面可见时清理属于其他运行的旧摘要，避免日志里的保存位置指向旧运行。"""
        self._drop_archival_of_another_run()
        super().on_activated()

    def _drop_archival_of_another_run(self) -> None:
        summary = self._archival_summary
        if summary is None or summary.run_id is None:
            return
        current = self._current_run_id()
        if current == summary.run_id:
            return
        self._clear_archival_summary()
        if current is None:
            reason = f"当前没有运行，已清除运行 {summary.run_id} 的归档结果显示"
        else:
            reason = f"当前运行已切换为 {current}，已清除运行 {summary.run_id} 的归档结果显示"
        self._log_callback(f"{reason}；历史运行请在“历史运行”页查看")

    def _clear_archival_summary(self) -> None:
        """清空最近一次归档提示，不写日志（页面清空结果时由调用方决定文案）。"""
        self._archival_summary = None
        self._archival_pending = False
        self._archival_saving = False

    # -- 内部 ---------------------------------------------------------------

    def _show_archival_summary(self, summary: ArchivalSummary) -> None:
        """把一条归档提示写成运行日志；保存位置只在这里说明，页面上不再保留归档框。"""
        self._archival_summary = summary
        message = f"{summary.headline}：{summary.detail}"
        location = self._archival_directory(summary.run_id)
        if location is not None and location not in message:
            message = f"{message} 结果目录：{location}"
        if summary.tone == TONE_WARNING:
            message = f"[归档警告] {message}"
        elif summary.tone == TONE_ERROR:
            message = f"[归档错误] {message}"
        self._log_callback(message)

    def _archival_directory(self, run_id: str | None) -> str | None:
        """解析本次结果的运行目录；只用于日志定位，失败时不改变归档状态。"""
        if run_id is None:
            return None
        provider = self._archival_location_provider
        if provider is None:
            return None
        current = self._current_run_id()
        if current is not None and current != run_id:
            return None
        try:
            target = provider(run_id, None)
        except (OSError, ValueError):
            return None
        return target if isinstance(target, str) and target.strip() else None

    def _current_run_id(self) -> str | None:
        """当前运行 ID，仅用于状态文案；不触发任何检查或对话框。"""
        provider = self._archival_run_id_provider
        if provider is not None:
            run_id = provider()
        else:
            run = self._archival_run_provider() if self._archival_run_provider else None
            run_id = getattr(run, "run_id", None)
        return run_id if isinstance(run_id, str) and run_id.strip() else None


__all__ = ["ArchivalPageMixin"]
