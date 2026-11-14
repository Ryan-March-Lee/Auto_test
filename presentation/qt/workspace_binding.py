"""把窗口的标签宿主接到测量工作台外壳的绑定层。

测量区的外观由 :class:`~presentation.qt.measurement_workspace.MeasurementWorkspace`
承担，页面注册与生命周期仍归 ``QTabWidget`` 和 :mod:`presentation.qt.pages`。这一层
只做六件事，且每件都在同一处：

- 阶段条点击 → 选中对应页面（:meth:`WorkspaceBinding.navigate`）；
- 标签切换 → 同步页头、阶段条与进入页过渡（:meth:`WorkspaceBinding.sync`）；
- 无驱放测试 → 停用驱动映射阶段（:meth:`WorkspaceBinding.set_stage_enabled`）；
- controller 生命周期 → 各阶段运行状态
  （:meth:`WorkspaceBinding.observe_measurement_state`），run 边界清空上一轮状态；
- 活动测量与 ``signals.progress`` → 页头进度发丝线
  （:meth:`WorkspaceBinding.observe_measurement_state` /
  :meth:`WorkspaceBinding.observe_progress`）；
- 测试模式与运行身份 → 徽标、驱动映射标签页、阶段条和页头
  （:meth:`WorkspaceBinding.start_session` /
  :meth:`WorkspaceBinding.bind_run_identity`）。

设计约束：

- 不导入 Qt（只在类型检查时引用 ``QTabWidget``），也不导入任何页面类；页面通过
  ``pages`` 映射注入，因此自定义阶段集合与新增页面都不需要改本模块。
- 同步入口都是信号槽，**不能抛异常**：未知阶段 key、被隐藏的标签页、尚未登记的
  widget 都只静默忽略，把界面留在上一个一致状态，而不是打断 Qt 的信号分发。
- “是不是阶段”由外壳回答（``workspace.has_stage``）：阶段页点亮阶段，其余**已登记**
  页面按工具页同步。新增报告/历史这类非阶段页不需要改窗口，也不会再出现“画得出、
  同步不了”的 ``KeyError``；工具页标题不再写进页头。
- **会话模式与运行身份是两个状态**：模式只由 :meth:`start_session`、
  :meth:`set_session_mode` 和 :meth:`bind_run_identity` 改写，``None`` 表示尚未选择
  测试模式；:meth:`sync_run_identity` 只刷新页头，不能从冻结运行重新推导模式，否则
  旧运行会覆盖用户在欢迎页的新选择。
- 本层不切 ``view_stack``、不隐藏标签栏、不调用页面生命周期；窗口仍是唯一装配根。
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Mapping

from .measurement_controller_contract import ControllerState
from .measurement_workspace import MeasurementWorkspace, mode_text
from .stage_summary import StageSummary
from .workspace_state import WorkspaceRunState

if TYPE_CHECKING:  # pragma: no cover - 只给类型检查器看，运行时不导入 Qt
    from PySide6.QtWidgets import QTabWidget

__all__ = ["WorkspaceBinding"]


def _clean_run_id(value: Any) -> str | None:
    """把可选运行标识规整成“去首尾空白后的非空字符串”或 ``None``。"""
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _page_stage_summary(page: Any) -> StageSummary | None:
    """读取页面的阶段摘要；页面没有该方法或返回非契约对象时静默放弃。

    摘要是展示数据，不是业务事实：这里的读取失败不能让切页或数据更新中断，也不能
    清掉上一个有效摘要。
    """
    method = getattr(page, "stage_summary", None)
    if not callable(method):
        return None
    try:
        summary = method()
    except (TypeError, ValueError, RuntimeError, AttributeError):
        return None
    return summary if isinstance(summary, StageSummary) else None


class WorkspaceBinding:
    """标签宿主与工作台外壳之间的唯一同步点。"""

    def __init__(
        self,
        workspace: MeasurementWorkspace,
        tabs: QTabWidget,
        pages: Mapping[str, Any],
        *,
        mode_badge: Any | None = None,
        controller: Any | None = None,
        run_state: WorkspaceRunState | None = None,
    ) -> None:
        self._workspace = workspace
        self._tabs = tabs
        self._pages = pages
        # 徽标是窗口创建、挂在页头右侧的控件；绑定层只写文本，不拥有它。
        self._mode_badge = mode_badge
        # 阶段运行状态模型是纯 Python：controller 只暴露“当前这一次测量”，
        # 上一个阶段的完成态必须由这里记住，不能从 controller 的当前快照推断。
        self._run_state = run_state if run_state is not None else WorkspaceRunState()
        self._driver_mode: bool | None = None
        self._identity_run_id: str | None = None
        self._controller = controller
        # 页面在数据变化时发出摘要变更；绑定层只负责把摘要转发给外壳。保存槽引用，
        # 避免 Qt 连接依赖临时 lambda 的生命周期。
        self._summary_connections: list[tuple[Any, Any]] = []
        self._connect_stage_summaries()
        if controller is not None:
            controller.signals.state_changed.connect(
                self.observe_measurement_state
            )
            # 页头发丝线直接消费 controller 的进度信号；假控制器可以没有这一项。
            progress = getattr(controller.signals, "progress", None)
            connect = getattr(progress, "connect", None)
            if callable(connect):
                connect(self.observe_progress)

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

        切页完成后顺带播放进入页过渡（阶段 → 阶段时才有方向；测量进行中瞬时）；
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
                # 页头与阶段条共用同一份摘要；切回该阶段时按页面当前数据显示。
                self.refresh_stage_summary(key)
            else:
                self._workspace.show_utility()
            # 进入页过渡的开关与“测量中瞬时切换”都在外壳里；本层只回答现在忙不忙。
            self._workspace.reveal_current_page(animate=self._measurement_idle())
            return

    def _measurement_idle(self) -> bool:
        """当前没有活动测量；controller 缺失或没有状态快照时按空闲处理。"""
        state = getattr(self._controller, "state", None)
        return not bool(getattr(state, "is_active", False))

    # ---- 阶段摘要 -------------------------------------------------------------

    def _connect_stage_summaries(self) -> None:
        """连接页面自己的摘要变更信号；没有该信号的页面直接跳过。"""
        for key, page in self._pages.items():
            signal = getattr(page, "stage_summary_changed", None)
            connect = getattr(signal, "connect", None)
            if not callable(connect):
                continue
            slot = lambda _value=None, key=key: self.refresh_stage_summary(key)
            connect(slot)
            self._summary_connections.append((signal, slot))

    def refresh_summaries(self) -> None:
        """重新读取全部阶段页的摘要；只写展示，不触发页面导航或测量。"""
        for key in list(self._pages):
            self.refresh_stage_summary(key)

    def refresh_stage_summary(self, key: str) -> None:
        """把一个页面的 ``stage_summary()`` 写进阶段条与页头。

        摘要由页面从已经显示的数据里取好；本层不统计、不读结果 DTO。未知 key、
        非阶段页、页面没有该方法或载荷不符合契约时静默忽略，信号槽不抛异常。
        """
        if not isinstance(key, str):
            return
        page = self._pages.get(key)
        if page is None or not self._workspace.has_stage(key):
            return
        summary = _page_stage_summary(page)
        if summary is None:
            return
        self._workspace.set_stage_summary(key, summary)

    def set_stage_enabled(self, key: str, enabled: bool) -> None:
        """启用/停用阶段条上的一个阶段；不改变当前阶段，也不切页。"""
        self._workspace.set_stage_enabled(key, enabled)

    def observe_measurement_state(self, state: ControllerState) -> None:
        """把 controller 状态折叠成阶段条状态与页头发丝线的活动态。

        这是 controller 与外壳之间唯一的阶段状态入口：完成/失败/停止由
        :class:`~presentation.qt.workspace_state.WorkspaceRunState` 记住，写穿的只有
        真正发生变化的阶段。页头文本与页面导航都不动，发丝线只跟随“这次测量是否还在
        走”。非 ``ControllerState`` 的载荷静默忽略，信号分发不因接线错误中断。
        """
        if not isinstance(state, ControllerState):
            return
        for key, stage_state in self._run_state.observe(state).items():
            self._workspace.set_stage_state(key, stage_state)
        # 页头进度发丝线只跟随“这次测量是否还在走”；阶段条呼吸点仍由 ACTIVE 状态派生。
        self._workspace.set_measurement_active(state.is_active)

    def observe_progress(self, value: int | float) -> None:
        """把 controller 的粗粒度进度写到页头发丝线；非法载荷静默忽略。

        发丝线只确认测量在推进，不替代状态面板里的详细进度条；同一条信号也仍由页面
        转发给状态面板，这里不再创造第二份数值来源。``bool``、非数值与 ``NaN``/``±inf``
        都在这里丢掉：信号槽不能抛异常，展示层也只接受 0–100 的有限值。
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        if not math.isfinite(float(value)):
            return
        self._workspace.set_progress(value)

    def run_state(self) -> WorkspaceRunState:
        """当前 run 的阶段状态模型；供窗口与测试读取，不转移所有权。"""
        return self._run_state

    # ---- 会话模式与运行身份 ---------------------------------------------------

    def start_session(self, driver_enabled: bool | None) -> None:
        """开始一次配置会话：写入模式，页头先回到“未连接”。

        ``None`` 表示尚未选择测试模式：驱动映射页隐藏、02 阶段停用、徽标显示
        “未选择测试模式”。会话一开始就解除旧运行与页头的关联，之后
        :meth:`sync_run_identity` 不会把上一轮运行重新挂回来；阶段状态与页头进度
        发丝线也一起清空。
        """
        self._identity_run_id = None
        self._apply_driver_mode(driver_enabled)
        self._workspace.set_run_identity(None, None)
        self._workspace.set_measurement_active(False)
        self._sync_run_state(None)

    def set_session_mode(self, driver_enabled: bool) -> None:
        """只更新本次配置会话的编辑模式，不改运行身份。

        加载一份命名配置时，用户还没有提交执行会话：配置里的驱放模式就是下一次
        连接要用的模式，徽标、驱动映射标签页与阶段条必须一起更新。已经提交的会话
        仍由 :meth:`bind_run_identity` 写入冻结模式；调用方不得用本方法把它改掉。
        """
        self._apply_driver_mode(bool(driver_enabled))
        if self._identity_run_id is not None:
            # 若调用方确实已经关联了运行，页头标签也必须与新会话模式一致，不能留下
            # “徽标已改、页头仍是旧模式”的半更新。
            self.sync_run_identity(self._identity_run_id)

    def bind_run_identity(
        self, run_id: str | None, driver_enabled: bool | None
    ) -> None:
        """把当前运行及其模式绑定到工作台。

        只有成功加载的冻结运行或连接提交后的新运行才调用这里：模式、徽标、
        驱动映射标签页、阶段条与页头必须一起更新。
        """
        self._apply_driver_mode(driver_enabled)
        self._identity_run_id = _clean_run_id(run_id)
        self.sync_run_identity(run_id)

    def sync_run_identity(self, run_id: str | None) -> None:
        """刷新页头运行身份并处理 run 边界，不改变模式或标签页。

        只有仍与本次会话关联的 run 才显示；返回欢迎页后再进入配置流程，旧 run
        不会被重新写回页头，也不会把它的模式带回徽标。关联的 run 变化时清空上一轮
        的完成/失败状态；同一 run 的普通刷新不动阶段条。
        """
        cleaned = _clean_run_id(run_id)
        associated = cleaned is not None and cleaned == self._identity_run_id
        if not associated:
            self._workspace.set_run_identity(None, None)
        else:
            label = mode_text(self._driver_mode) if self._driver_mode is not None else None
            self._workspace.set_run_identity(cleaned, label)
        self._sync_run_state(cleaned if associated else None)

    def reset_identity(self) -> None:
        """回到未选择状态：清页头，并让徽标、驱动页与阶段条一起复位。"""
        self.start_session(None)

    def driver_mode(self) -> bool | None:
        """当前会话模式；``None`` 表示尚未选择测试模式。"""
        return self._driver_mode

    def _sync_run_state(self, run_id: str | None) -> None:
        """run 变化时把上一轮阶段状态清回未开始，并收起发丝线；同一 run 保持不变。"""
        cleaned = _clean_run_id(run_id)
        if self._run_state.run_id() == cleaned:
            return
        for key, stage_state in self._run_state.reset_run(cleaned).items():
            self._workspace.set_stage_state(key, stage_state)
        # 换 run 或清空身份时上一轮的活动测量已结束：发丝线跟着收掉。
        self._workspace.set_measurement_active(False)

    def _apply_driver_mode(self, driver_enabled: bool | None) -> None:
        """把会话模式写穿到徽标、驱动映射标签页和阶段条。

        这是模式唯一的写入点：``None`` 与 ``False`` 必须区分开，否则返回欢迎页
        后无法把“未选择”与“无驱放测试”表示成不同状态。
        """
        enabled = None if driver_enabled is None else bool(driver_enabled)
        page = self._pages.get("driver_mapping")
        if page is not None:
            index = self._tabs.indexOf(page)
            if index >= 0:
                self._tabs.setTabVisible(index, enabled is True)
        if self._workspace.has_stage("driver_mapping"):
            self._workspace.set_stage_enabled("driver_mapping", enabled is True)
        if self._mode_badge is not None:
            setter = getattr(self._mode_badge, "setText", None)
            if callable(setter):
                setter(mode_text(enabled))
        self._driver_mode = enabled
