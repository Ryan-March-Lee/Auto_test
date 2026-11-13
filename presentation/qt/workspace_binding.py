"""把窗口的标签宿主接到测量工作台外壳的绑定层。

测量区的外观由 :class:`~presentation.qt.measurement_workspace.MeasurementWorkspace`
承担，页面注册与生命周期仍归 ``QTabWidget`` 和 :mod:`presentation.qt.pages`。这一层
只做三件事，且每件都在同一处：

- 阶段条点击 → 选中对应页面（:meth:`WorkspaceBinding.navigate`）；
- 标签切换 → 同步页头与阶段条（:meth:`WorkspaceBinding.sync`）；
- 无驱放测试 → 停用驱动映射阶段（:meth:`WorkspaceBinding.set_stage_enabled`）。

设计约束：

- 不导入 Qt（只在类型检查时引用 ``QTabWidget``），也不导入任何页面类；页面通过
  ``pages`` 映射注入，因此自定义阶段集合与新增页面都不需要改本模块。
- 两个同步入口都是信号槽，**不能抛异常**：未知阶段 key、被隐藏的标签页、尚未登记
  的 widget 都只静默忽略，把界面留在上一个一致状态，而不是打断 Qt 的信号分发。
- “是不是阶段”由外壳回答（``workspace.has_stage``）：阶段页点亮阶段，其余**已登记**
  页面按工具页显示自己的标签标题。新增报告/历史这类非阶段页不需要改窗口，也不会
  再出现“画得出、同步不了”的 ``KeyError``。
- 本层不切 ``view_stack``、不隐藏标签栏、不调用页面生命周期；窗口仍是唯一装配根。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from .measurement_workspace import MeasurementWorkspace

if TYPE_CHECKING:  # pragma: no cover - 只给类型检查器看，运行时不导入 Qt
    from PySide6.QtWidgets import QTabWidget

__all__ = ["WorkspaceBinding"]


class WorkspaceBinding:
    """标签宿主与工作台外壳之间的唯一同步点。"""

    def __init__(
        self,
        workspace: MeasurementWorkspace,
        tabs: QTabWidget,
        pages: Mapping[str, Any],
        *,
        utility_subtitles: Mapping[str, str] | None = None,
    ) -> None:
        self._workspace = workspace
        self._tabs = tabs
        self._pages = pages
        # 工具页副标题按页面 key 给：只有确实需要第二行说明的页面才登记，其余留空。
        self._utility_subtitles = dict(utility_subtitles or {})

    # ---- 导航 ---------------------------------------------------------------

    def navigate(self, key: str) -> None:
        """阶段条请求切页；未知 key、未登记页面与不可见标签页都静默忽略。

        阶段条已经保证了“停用阶段不可点”，这里再检查标签页可见性：驱动映射在无驱放
        测试下同时被隐藏与停用，任何一侧漏掉都不会把用户带到一个不存在的页面。
        """
        if not isinstance(key, str):
            return
        page = self._pages.get(key)
        if page is None:
            return
        index = self._tabs.indexOf(page)
        if index < 0 or not self._tabs.isTabVisible(index):
            return
        self._tabs.setCurrentIndex(index)

    # ---- 同步 ---------------------------------------------------------------

    def sync(self) -> None:
        """把当前页写入外壳：阶段页点亮阶段，其余已登记页面显示工具页头。

        当前 widget 不在 ``pages`` 里（例如装配过程中的临时容器）时什么都不做：
        外壳留在上一个一致状态，既不猜阶段，也不清空页头。
        """
        index = self._tabs.currentIndex()
        current = self._tabs.widget(index)
        if current is None:
            return
        for key, page in self._pages.items():
            if page is not current:
                continue
            if self._workspace.has_stage(key):
                self._workspace.set_current_stage(key)
            else:
                title = self._tabs.tabText(index).strip() or key
                self._workspace.show_utility(
                    title, self._utility_subtitles.get(key, "")
                )
            return

    def set_stage_enabled(self, key: str, enabled: bool) -> None:
        """启用/停用阶段条上的一个阶段；不改变当前阶段，也不切页。"""
        self._workspace.set_stage_enabled(key, enabled)
