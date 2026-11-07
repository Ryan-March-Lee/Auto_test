"""电源分区的不可见计划状态与漏极路由状态。

电源通道参数不再在这里建表单：VS/IS、角色滚筒与保护勾选已经融合到卡片上
（:mod:`presentation.qt.power_card` / :mod:`presentation.qt.power_card_wall`）。
上电角色与上电顺序也已经在卡片上编辑，因此卡片下方不再有“通道上电计划”两块设置
框；本模块不再构建任何可见的电源表单，只保留 :class:`PowerPlanState` 的计划状态
和一份漏极路由状态。``Cal.`` 标记是效率计算通道的唯一界面，页面把所有已上电的
标记通道写进漏极集合。
:class:`PowerFormSection` 把它们收在一个不可见的状态对象里，页面只负责创建它、
转发页面级派生状态（测试模式）并按需回填。

页面传进来的输入都是显式声明的结构（见 :data:`PowerSupplySources` 与
:data:`PowerSupplyChoices`）：键是电源键，值是配置条目里本模块要显示的字段，本模块不从
文件里另外取状态。器件电源的选择只由计划路由表达：每组计划的成员就是卡片上拨到
``DUT`` / ``Driver`` 的通道，`(supply_id, channel)` 在哪一组就表示该通道给哪个器件
上电；本模块不再提供按器件固定分配电源的下拉框。

本模块不校验配置、不迁移旧字段、不写配置、也不读文件；字段的回填仍归
:mod:`presentation.qt.config_projection`，正式配置编写归
:mod:`application.configuration_authoring`，正式校验在
:mod:`domain.configuration.rules` 与 :mod:`infrastructure.config.validation`。
"""

from typing import Any, Iterable, Mapping, TypedDict

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from .config_power_assignments import POWER_SUPPLY_CHANNELS, assignment_channels


class PowerSupplyEntry(TypedDict, total=False):
    """一个电源条目里本模块要用的身份字段与通道声明。

    身份字段只用于计划候选与显示（缩写设备码、型号、完整设备码、设备键），缺哪个
    就少显示哪段；通道参数不在这里读取，通道控件已经融合到电源卡片上。
    """

    label: Any
    model: Any
    full_code: Any
    device_key: Any
    channels: Mapping[str, Any]


# 页面交给构建模块的两份输入（都是显式结构，不从控件或文件里再读）：
# - ``sources``：电源键 -> 显示条目，键就是配置里的电源键（旧槽位是 ``PS1`` 这类旧键）。
PowerSupplySources = Mapping[str, PowerSupplyEntry]
# - ``choices``：计划候选与卡面显示用的已绑定电源，``supply_id -> 显示条目``。
PowerSupplyChoices = Mapping[str, Mapping[str, Any]]

__all__ = [
    "POWER_SUPPLY_CHANNELS",
    "PowerFormSection",
    "PowerPlanState",
    "PowerSupplyChoices",
    "PowerSupplyEntry",
    "PowerSupplySources",
]


# ---------------------------------------------------------------------------
# 组上电计划状态与漏极：独立电源表单的状态与控件
# ---------------------------------------------------------------------------

def _route(value: Any) -> tuple[str, str] | None:
    """把两段路由对象（或内部 ``(supply_id, channel)``）读成内部路由元组。"""
    if isinstance(value, (tuple, list)) and len(value) == 2:
        supply_id, channel = value
    elif isinstance(value, Mapping):
        supply_id = value.get("supply_id")
        channel = value.get("channel")
    else:
        return None
    if not isinstance(supply_id, str) or not isinstance(channel, str):
        return None
    supply_id, channel = supply_id.strip(), channel.strip()
    if not supply_id or not channel or "/" in supply_id or "/" in channel:
        return None
    return (supply_id, channel)


def _drain_route_list(value: Any) -> tuple[tuple[str, str], ...]:
    """把漏极字段（单条路由或路由列表）读成去重、保序的内部路由元组。"""
    if value is None:
        return ()
    items = value if isinstance(value, (list, tuple)) else (value,)
    ordered: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        route = _route(item)
        if route is not None and route not in seen:
            seen.add(route)
            ordered.append(route)
    return tuple(ordered)


def _ordered_routes(routes: Iterable[Any]) -> tuple[tuple[str, str], ...]:
    """把候选路由规整成去重、保序的内部元组；非法项跳过，由校验报告。"""
    ordered: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in routes:
        route = _route(item)
        if route is not None and route not in seen:
            seen.add(route)
            ordered.append(route)
    return tuple(ordered)


def _plan_routes(plan: Any) -> tuple[tuple[str, str], ...]:
    """``power_plan`` 里的有序路由；非法/缺字段的项跳过，由校验报告。"""
    if not isinstance(plan, Mapping):
        return ()
    sequence = plan.get("power_sequence")
    if not isinstance(sequence, (list, tuple)):
        return ()
    return _ordered_routes(sequence)


class PowerPlanState(QObject):
    """一组上电计划的不可见状态：候选路由 + 已参与上电的有序路由。

    计划不再有独立的编辑框：通道是否参与、属于哪一组由卡片滚筒决定，顺序由卡面
    下拉框决定。本类只保存页面与表单读取所需的状态，不创建任何控件；成员变化发
    :attr:`changed`，顺序变化发 :attr:`order_changed`，由页面同步卡片 `dirty`。

    ``active`` 为假（驱放关闭）时 :meth:`read_active_plan` 返回空计划，避免页面
    把上一次的勾选写回；界面上的成员仍保留，重新开启驱放模式后继续使用。
    """

    #: ``(supply_id, channel, checked)``：某条路由加入或离开本组计划。
    changed = Signal(str, str, bool)
    #: 已参与上电的路由顺序被重排。
    order_changed = Signal()

    def __init__(self, *, active: bool = True, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._active = bool(active)
        self._candidates: tuple[tuple[str, str], ...] = ()
        self._sequence: tuple[tuple[str, str], ...] = ()

    @property
    def is_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        self._active = bool(active)

    def reset(self) -> None:
        """清空候选与计划（回填前的确定性基线）。"""
        self._candidates = ()
        self._sequence = ()

    def set_plan(self, plan: Any, routes: Iterable[Any] | None = None) -> None:
        """按计划重新填状态；``routes`` 是候选集合，缺省保留上次候选。

        计划缺失（``None``）表示“还没有确认顺序”：序列为空，候选路由也不会被
        擅自加入。显式空计划与缺失计划在这里读数相同，但写回时保留空计划。
        """
        if routes is not None:
            self._candidates = _ordered_routes(routes)
        self._sequence = _plan_routes(plan)

    def refresh(self, routes: Iterable[Any], plan: Any = None) -> None:
        """用新的候选路由重建状态，并按给定计划恢复成员与顺序。"""
        self.set_plan(plan, routes=routes)

    def routes(self) -> tuple[tuple[str, str], ...]:
        """候选路由：计划里的路由先按顺序，再补尚未参与上电的候选。"""
        sequence = self._sequence
        return sequence + tuple(
            route for route in self._candidates if route not in sequence
        )

    def checked_routes(self) -> tuple[tuple[str, str], ...]:
        """按顺序返回已参与上电的路由；它同时就是上电顺序。"""
        return self._sequence

    def set_route_checked(self, route: Any, checked: bool) -> bool:
        """把一条候选路由加入/移出本组计划；路由不认识时返回 ``False``。

        加入时追加到末尾（新通道最后上电），移出时保持其余路由的相对顺序。状态
        没有变化时不发信号，避免绑定刷新等程序化同步触发 `dirty`。
        """
        target = _route(route)
        if target is None:
            return False
        if target not in self._candidates and target not in self._sequence:
            return False
        if checked:
            if target in self._sequence:
                return True
            self._sequence = (*self._sequence, target)
        else:
            if target not in self._sequence:
                return True
            self._sequence = tuple(item for item in self._sequence if item != target)
        self.changed.emit(target[0], target[1], bool(checked))
        return True

    def set_route_order(self, route: Any, order: int) -> bool:
        """把已参与上电的路由移动到 1-based 的 ``order`` 位置；无效时返回 ``False``。"""
        target = _route(route)
        if target is None or target not in self._sequence:
            return False
        if isinstance(order, bool) or not isinstance(order, int):
            return False
        if order < 1 or order > len(self._sequence):
            return False
        if self._sequence.index(target) == order - 1:
            return True
        sequence = [item for item in self._sequence if item != target]
        sequence.insert(order - 1, target)
        self._sequence = tuple(sequence)
        self.order_changed.emit()
        return True

    def read_plan(self) -> dict[str, Any]:
        """读出当前成员顺序；组默认间隔已不再编辑，统一写 0.0。

        ``inter_channel_delay_s`` 仍是 v1.1 计划形状的一部分，但编辑器不再给出
        非零值：每个通道的间隔由卡面单独设置，未设置的通道按 2s 处理。
        """
        return {
            "power_sequence": [
                {"supply_id": supply_id, "channel": channel}
                for supply_id, channel in self._sequence
            ],
            "inter_channel_delay_s": 0.0,
        }

    def read_active_plan(self) -> dict[str, Any]:
        """写回配置时使用的计划；未激活的组固定写成空计划。"""
        if not self._active:
            return {"power_sequence": [], "inter_channel_delay_s": 0.0}
        return self.read_plan()


class PowerFormSection(QWidget):
    """不可见的电源状态容器：两份计划状态与漏极路由状态。

    上电角色、顺序与每通道间隔都在卡片上编辑，``Cal.`` 标记是效率计算通道的唯一
    界面；本组件不再构建任何可见的电源表单。两份 :class:`PowerPlanState` 仍保存
    计划状态，页面通过 :attr:`plan_changed` 同步卡片；漏极状态保存全部已标记路由。
    """

    #: ``(group, supply_id, channel, checked)``：计划成员变化，页面据此同步卡片。
    plan_changed = Signal(str, str, str, bool)

    def __init__(
        self,
        *,
        sources: PowerSupplySources,
        choices: PowerSupplyChoices,
        driver_plan_active: bool = True,
        dut_power_plan: Any = None,
        driver_power_plan: Any = None,
        drain_channel: Any = None,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._driver_plan_active = bool(driver_plan_active)
        # 计划候选：已绑定电源的声明通道（没有声明时回落到每台电源固定的 CH1/CH2）。
        # 两个状态使用同一份候选，器件归属由卡片滚筒决定。
        self._plan_routes = _candidate_routes(sources, choices)
        self.dut_power_plan_editor = PowerPlanState(active=True, parent=self)
        self.driver_power_plan_editor = PowerPlanState(
            active=self._driver_plan_active, parent=self
        )
        # 漏极状态由卡面 ``Cal.`` 标记驱动；多漏极在运行阶段求和。
        self._drain_routes: tuple[tuple[str, str], ...] = ()
        self._connect_assignment_signals()
        self.refresh_plan_routes(
            preserve=False,
            dut_plan=dut_power_plan,
            driver_plan=driver_power_plan,
            drain=drain_channel,
        )

    def select_drain_routes(self, routes: Any, *, clear: bool = True) -> bool:
        """把漏极状态指向给定路由集合。

        返回是否真的改变了当前选择。页面把所有已上电且已标记 ``Cal.`` 的通道按卡片
        顺序传进来；``routes`` 为空表示当前没有可用标记，``clear=False`` 时保留原值
        （用于回填/计划同步，避免把文档里显式写下的无效漏极静默清空）。
        """
        targets = _drain_route_list(routes)
        current = self._drain_routes
        if not targets:
            if not clear or not current:
                return False
        if targets == current:
            return False
        self._set_drain_routes(targets)
        return True

    def _connect_assignment_signals(self) -> None:
        # 卡片换档写计划时，成员变化通知页面同步卡片；顺序变化由页面单独处理。
        for group, editor in (
            ("dut", self.dut_power_plan_editor),
            ("driver", self.driver_power_plan_editor),
        ):
            editor.changed.connect(
                lambda supply_id, channel, checked, name=group: (
                    self._on_plan_membership(name, supply_id, channel, checked)
                )
            )

    def _on_plan_membership(
        self, group: str, supply_id: str, channel: str, checked: bool
    ) -> None:
        self.plan_changed.emit(group, supply_id, channel, bool(checked))

    # ---- 页面级派生状态 -----------------------------------------------------
    def set_power_choices(
        self, sources: PowerSupplySources, choices: PowerSupplyChoices
    ) -> None:
        """设备绑定变化后更新计划候选；已参与的计划与当前顺序保持不变。"""
        routes = _candidate_routes(sources, choices)
        if routes == self._plan_routes:
            return
        self._plan_routes = routes
        self.refresh_plan_routes(preserve=True)

    def set_driver_plan_active(self, active: bool) -> None:
        active = bool(active)
        if active == self._driver_plan_active:
            return
        self._driver_plan_active = active
        self.driver_power_plan_editor.set_active(active)
        self.refresh_plan_routes(preserve=True)

    # ---- 计划 ---------------------------------------------------------------
    def refresh_plan_routes(
        self,
        *,
        preserve: bool,
        dut_plan: Any = None,
        driver_plan: Any = None,
        drain: Any = None,
    ) -> None:
        """按当前候选重建计划状态，并恢复计划与漏极。

        ``preserve=True`` 用于绑定/候选变化后的重建：先读当前状态里的顺序、成员
        和漏极，再用新候选重建，未提交编辑不会丢。候选是已绑定电源的全部声明通道；
        计划里已有、但当前绑定已解析不到的路由仍保留在计划里，交给校验报告。
        """
        if preserve:
            dut_plan = self.dut_power_plan_editor.read_plan()
            driver_plan = self.driver_power_plan_editor.read_plan()
            drain = self.read_drain_channels()
        self.dut_power_plan_editor.refresh(self._plan_routes, plan=dut_plan)
        self.driver_power_plan_editor.set_active(self._driver_plan_active)
        self.driver_power_plan_editor.refresh(self._plan_routes, plan=driver_plan)
        self._set_drain_routes(drain)

    # ---- 回填 ---------------------------------------------------------------
    def apply_power_plan_config(
        self, config: Any, *, driver_plan_active: bool | None = None
    ) -> None:
        """把一份配置文档的组计划与漏极投影到本组件（绑定变化后仍按文档为准）。

        候选来自当前已绑定电源；载入另一份配置后，计划仍可用文档里的路由恢复
        成员与顺序，不需要再读一份按器件固定分配电源的表。
        """
        if driver_plan_active is not None:
            self._driver_plan_active = bool(driver_plan_active)
        assignment = config.get("power_supply_assignment") if isinstance(config, Mapping) else {}
        assignment = assignment if isinstance(assignment, Mapping) else {}
        dut_plan = _group_plan(assignment, "dut_amplifier")
        driver_plan = _group_plan(assignment, "driver_amplifier")
        self.dut_power_plan_editor.refresh(self._plan_routes, plan=dut_plan)
        self.driver_power_plan_editor.set_active(self._driver_plan_active)
        self.driver_power_plan_editor.refresh(self._plan_routes, plan=driver_plan)
        self._set_drain_routes(_document_drain(config))

    def reset_power_plans(self) -> None:
        """回到空计划基线；回填投影开始前调用，保证不留上一次的选择。"""
        self.dut_power_plan_editor.reset()
        self.driver_power_plan_editor.reset()
        self._set_drain_routes(())

    # ---- 漏极 ---------------------------------------------------------------
    def _set_drain_routes(self, current: Any) -> None:
        """保存漏极路由；无效路由保留给正式校验处理。"""
        self._drain_routes = _drain_route_list(current)

    def read_drain_channels(self) -> list[dict[str, str]]:
        """当前漏极集合；多漏极按页面写入顺序返回，未配置时是空列表。"""
        return [
            {"supply_id": supply_id, "channel": channel}
            for supply_id, channel in self._drain_routes
        ]

def _group_plan(assignment: Mapping[str, Any], group: str) -> Any:
    block = assignment.get(group)
    if not isinstance(block, Mapping):
        return None
    return block.get("power_plan")


def _candidate_routes(
    sources: PowerSupplySources, choices: PowerSupplyChoices
) -> tuple[tuple[str, str], ...]:
    """上电计划候选：每台已绑定电源声明（或默认）的物理通道。

    器件归属由卡片滚筒决定，因此两份计划使用同一份候选。顺序来自绑定集合与通道
    声明顺序，只影响列表初值，不表示上电顺序。
    """
    routes: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for supply_id in choices:
        entry = sources.get(supply_id) or {}
        for channel in assignment_channels(entry):
            route = _route((supply_id, channel))
            if route is not None and route not in seen:
                seen.add(route)
                routes.append(route)
    return tuple(routes)


def _document_drain(config: Any) -> Any:
    if not isinstance(config, Mapping):
        return None
    dut = config.get("dut_config")
    return dut.get("drain_channel") if isinstance(dut, Mapping) else None
