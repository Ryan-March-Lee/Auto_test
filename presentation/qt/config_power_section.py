"""配置页电源分区的控件构建与独立电源表单。

本模块构建配置页右栏的电源分区：按电源键分页的「电源详细配置」标签页、
「驱动功放供电」方式选择，以及步骤 11 新增的「通道上电计划」与「效率测量」漏极选择。
:class:`PowerFormSection` 把这几块控件收在一个独立组件里，页面只负责把它放进布局、
转发页面级派生状态（驱放供电方式、测试模式）并按需回填。

页面传进来的输入都是显式声明的结构（见 :data:`PowerSupplySources` 与
:data:`PowerSupplyChoices`）：键是电源键，值是配置条目里本模块要显示的字段，本模块不从
文件里另外取状态。这里只按字段位置读出要显示的数值，并把不正确的类型回落到与投影一致
的缺省值（见 :func:`spin_value`）：那是显示口径，不是校验。本模块不校验配置、不迁移旧
字段、不写配置、也不读文件；字段的回填仍归 :mod:`presentation.qt.config_projection`，
正式配置编写归 :mod:`application.configuration_authoring`，正式校验在
:mod:`domain.configuration.rules` 与 :mod:`infrastructure.config.validation`。

器件电源的选择只由计划路由表达：每台电源声明（或默认）的通道构成两份计划编辑器的
同一份候选，`(supply_id, channel)` 勾在哪一组就表示该通道给哪个器件上电；本模块不再
提供按器件固定分配电源的下拉框。

通道数值框的上下界（``_VOLTAGE_LIMITS`` / ``_CURRENT_LIMITS``）只约束输入框能填到
哪里，比正式校验更严：界外的值由控件夹到界内，这里不报错也不改配置。重建标签页时
「保留操作者刚填的参数」用到的合并规则也在这里（见 :func:`channel_map`），它只合并
控件快照与配置条目，不判断字段语义。
"""

import math
from typing import Any, Iterable, Mapping, TypedDict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QTabWidget,
    QVBoxLayout, QWidget,
)

from application.dto.amplifier_inputs import MAX_INTER_CHANNEL_DELAY_S
from domain.instruments.labels import supply_identity_text

from .config_page_widgets import combo_box, spin_box
from .config_projection import (
    DEFAULT_POWER_CHANNEL_VALUE,
    DRIVER_POWER_SOURCE_VALUES,
    supply_option_label,
)
from .config_power_assignments import POWER_SUPPLY_CHANNELS, assignment_channels
from .input_controls import KeyboardOnlyDoubleSpinBox

# 通道数值框的显示上下界：只约束输入框能填到哪里。正式校验的合法范围在
# domain.configuration.rules（只要求有限数值），因此界外的配置值在控件里被夹到界内，
# 本模块不因此报错，也不改写配置。
_VOLTAGE_LIMITS = (0.0, 50.0)
_CURRENT_LIMITS = (0.0, 10.0)


class ChannelDisplayFields(TypedDict, total=False):
    """一个电源通道里本模块要显示的字段。

    值原样取自配置条目，取值时的类型回落由 :func:`spin_value` 负责；它不是校验
    清单：缺字段或类型不对只会显示成缺省值。通道不再声明供电角色，这里也没有该字段。
    """

    voltage: Mapping[str, Any]
    current: Mapping[str, Any]


class PowerSupplyEntry(TypedDict, total=False):
    """一个电源标签页要显示的一条条目：身份字段 + 通道参数。

    身份字段只用于显示（缩写设备码、型号，提示里的完整设备码与设备键），缺哪个就少显示哪段。
    """

    label: Any
    model: Any
    full_code: Any
    device_key: Any
    channels: Mapping[str, ChannelDisplayFields]


# 页面交给构建模块的两份输入（都是显式结构，不从控件或文件里再读）：
# - ``sources``：电源键 -> 显示条目，键就是配置里的电源键（旧槽位是 ``PS1`` 这类旧键）。
PowerSupplySources = Mapping[str, PowerSupplyEntry]
# - ``choices``：计划候选与标签页显示用的已绑定电源，``supply_id -> 显示条目``。
PowerSupplyChoices = Mapping[str, Mapping[str, Any]]

__all__ = [
    "ChannelDisplayFields",
    "POWER_SUPPLY_CHANNELS",
    "PowerFormSection",
    "PowerPlanEditor",
    "PowerSupplyChoices",
    "PowerSupplyEntry",
    "PowerSupplySources",
    "build_power_section",
    "channel_map",
    "current_power_tab_key",
    "rebuild_power_tabs",
    "spin_value",
]


def spin_value(value: Any) -> float:
    """通道数值只接受非 bool 的有限数字；其它类型（字符串/布尔/NaN）按缺省值处理。

    配置页也用于回填冻结配置：类型不正确的字段不应让重建标签页崩溃，而应落到与投影
    一致的默认值，交给正式校验报告。缺省值取自
    :data:`presentation.qt.config_projection.DEFAULT_POWER_CHANNEL_VALUE`——投影重置
    控件到空配置基线时用的也是这个数，因此“非法字段”与“配置没写这个字段”显示成同一个值。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return DEFAULT_POWER_CHANNEL_VALUE
    number = float(value)
    return number if math.isfinite(number) else DEFAULT_POWER_CHANNEL_VALUE


def _entry_channels(entry: Any) -> Mapping[str, Any]:
    """取出一个电源条目的 ``通道 -> 参数`` 映射；不是对象或没有映射时返回空字典。

    条目本身是一份完整电源配置（``{model, channels: {...}}``）。
    """
    channels = entry.get("channels") if isinstance(entry, dict) else None
    return channels if isinstance(channels, dict) else {}


def channel_map(source: Any, preserved: Any) -> dict:
    """合并配置条目与重建前控件快照的通道参数，供重建标签页时建控件。

    ``source`` 是这次要显示的配置条目，``preserved`` 是重建前从控件读到的同类条目
    （``preserve=False`` 时为空）。两者合并的优先级是“**控件里刚填的 > 配置里的**”：

    - 重建发生在保存之前（应用绑定、清除绑定、再次回填），操作者刚在控件里改过的参数
      必须保留，否则刷新会把它悄悄改回旧配置值；``preserve=True`` 的整个目的就是这个。
    - 配置里已有、但这次没有对应控件的通道仍从配置取（例如新绑定电源的缺省参数）。

    因此 ``preserve=False`` 时 ``preserved`` 为空，结果就是配置本身；``preserve=True``
    时控件快照优先。判定按 ``supply_id`` + 通道键，与标签页位置无关。
    """
    channels = dict(_entry_channels(source))
    for channel, value in _entry_channels(preserved).items():
        if isinstance(value, dict):
            merged = dict(channels.get(channel) or {})
            merged.update(value)
            channels[channel] = merged
    return channels


def build_power_section(
    page: Any,
    layout: Any,
    *,
    sources: PowerSupplySources,
    driver_power_source: Any,
) -> None:
    """构建电源详细配置与驱放供电方式，并把页面要读写的控件登记到 ``page``。

    电源使用哪个通道不再通过“给器件固定分配某台电源”的下拉框选择：上电计划里的
    路由 ``(supply_id, channel)`` 已经是唯一来源，计划候选由 :class:`PowerFormSection`
    自行按已绑定电源生成；``sources`` 是标签页要显示的电源条目。
    """
    detail = QGroupBox("电源详细配置")
    detail_layout = QVBoxLayout(detail)
    detail_layout.setContentsMargins(0, 0, 0, 0)
    page.power_tabs = QTabWidget()
    page.power_tabs.setObjectName("subTabs")
    page.power_config_widgets = {}
    rebuild_power_tabs(page, sources=sources, preserved={})
    detail_layout.addWidget(page.power_tabs)
    layout.addWidget(detail)

    driver_box = QGroupBox("驱动功放供电")
    form = QGridLayout(driver_box)
    form.setHorizontalSpacing(10)
    form.setVerticalSpacing(8)
    form.setColumnMinimumWidth(0, 118)
    page.power_assignment_group = driver_box
    page.driver_power_source_combo = combo_box(
        DRIVER_POWER_SOURCE_VALUES, str(driver_power_source)
    )
    page.driver_power_source_label = QLabel("驱放供电方式:")
    page.driver_power_hint = QLabel(
        "自带供电的驱放由外部供电设备供电，本系统不会控制其电源；"
        "使用直流电源时，在下方“驱动功放”上电计划里勾选实际使用的通道。"
    )
    page.driver_power_hint.setWordWrap(True)
    form.addWidget(page.driver_power_source_label, 0, 0)
    form.addWidget(page.driver_power_source_combo, 0, 1)
    form.addWidget(page.driver_power_hint, 1, 0, 1, 2)
    form.setColumnStretch(1, 1)
    layout.addWidget(driver_box)
    page.power_assignment_widgets = {
        "driver_source": page.driver_power_source_combo,
    }


def rebuild_power_tabs(
    page: Any, *, sources: PowerSupplySources, preserved: Mapping[str, Any]
) -> None:
    """按电源键重建标签页，并用 ``preserved`` 保留操作者刚填的通道参数。

    电源数量变化（应用/清除绑定、加载另一份配置）后重建，避免按位置把某一台设备的
    参数写到另一台上。通道参数按**配置键**取回（不是按标签页位置），当前选中的标签页
    也按**电源键**恢复：某台电源被移除或新增时，操作者仍然停在原来那台设备上。
    ``preserved`` 为空是回填路径，以新配置为准，不沿用重建前的残留值。

    页面入口已经确认电源分区存在（``PowerConfigurationPage._rebuild_power_tabs``），因此这里直接
    使用 ``page.power_tabs``：分区不存在属于调用方的编程错误，不静默返回。
    """
    tabs = page.power_tabs
    current_key = current_power_tab_key(page)
    tabs.clear()
    # 原地清空：页面或独立组件可能持有同一份映射的引用，重建不应把它换成新对象。
    page.power_config_widgets.clear()
    for key, source in sources.items():
        source = source if isinstance(source, dict) else {}
        _add_power_tab(page, key, source, channel_map(source, preserved.get(key)))
    if current_key in page.power_config_widgets:
        tabs.setCurrentIndex(tabs.indexOf(page.power_config_widgets[current_key]["page"]))


def current_power_tab_key(page: Any) -> str | None:
    """当前选中电源标签页的电源键；没有标签页时返回 ``None``。"""
    tabs = page.power_tabs
    if not tabs.count():
        return None
    index = tabs.currentIndex()
    for key, record in page.power_config_widgets.items():
        if record.get("page") is tabs.widget(index):
            return key
    return None


def _add_power_tab(
    page: Any,
    key: str,
    source: Mapping[str, Any],
    channels: Mapping[str, Any],
) -> None:
    """为一条电源配置建一个标签页；标题是型号加缩写设备码（``DP832A（DP8B1519）``）。

    标签页按配置键（``supply_id``）建，标题只用于显示：设备数量与身份都来自配置与
    当前绑定，没有固定的四个槽位页。

    标签页的悬浮提示给**详情与审计**：型号（缩写设备码）、完整设备码与设备键（见
    :func:`domain.instruments.labels.supply_identity_text`）。卡面上只放型号与缩写码，
    因为标题栏很窄；追溯时把指针停在标签页上就能读到完整设备码。旧槽位条目没有完整设备码，
    提示里就只有它的旧键（``PS1``），不推导一个不存在的缩写码。
    """
    page_widget = QWidget()
    page_layout = QVBoxLayout(page_widget)
    page_layout.setSpacing(10)
    record = {"channels": {}, "page": page_widget}
    for channel in POWER_SUPPLY_CHANNELS:
        value = channels.get(channel, {})
        if not isinstance(value, dict):
            value = {}
        voltage = value.get("voltage", {})
        current = value.get("current", {})
        if not isinstance(voltage, dict):
            voltage = {}
        if not isinstance(current, dict):
            current = {}
        box = QGroupBox(f"通道 {channel}")
        form = QFormLayout(box)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        controls = {
            "voltage": spin_box(spin_value(voltage.get("value")), *_VOLTAGE_LIMITS),
            "voltage_protection": spin_box(
                spin_value(voltage.get("protection")), *_VOLTAGE_LIMITS
            ),
            "voltage_protection_enabled": QCheckBox("启用"),
            "current": spin_box(spin_value(current.get("value")), *_CURRENT_LIMITS),
            "current_protection": spin_box(
                spin_value(current.get("protection")), *_CURRENT_LIMITS
            ),
            "current_protection_enabled": QCheckBox("启用"),
        }
        controls["voltage_protection_enabled"].setChecked(
            voltage.get("protection_enabled") is True
        )
        controls["current_protection_enabled"].setChecked(
            current.get("protection_enabled") is True
        )
        form.addRow("电压 (V)", controls["voltage"])
        form.addRow("保护电压 (V)", controls["voltage_protection"])
        form.addRow("电压保护", controls["voltage_protection_enabled"])
        form.addRow("电流 (A)", controls["current"])
        form.addRow("保护电流 (A)", controls["current_protection"])
        form.addRow("电流保护", controls["current_protection_enabled"])
        page_layout.addWidget(box)
        record["channels"][channel] = controls
    title = supply_option_label(source.get("label") or key, source.get("model"))
    index = page.power_tabs.addTab(page_widget, title)
    # 详情/审计文本与标题同源：型号（缩写设备码）+ 完整设备码 + 设备键，缺哪个就少哪段。
    page.power_tabs.setTabToolTip(
        index,
        supply_identity_text(
            source.get("label") or key,
            source.get("model"),
            source.get("full_code"),
            source.get("device_key"),
        ),
    )
    page.power_config_widgets[key] = record


# ---------------------------------------------------------------------------
# 组上电计划与漏极：独立电源表单的控件与状态
# ---------------------------------------------------------------------------

_ROUTE_ROLE = Qt.ItemDataRole.UserRole
_UNSET_DRAIN_LABEL = "（未指定）"


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


def _route_text(route: tuple[str, str]) -> str:
    return f"{route[0]} / {route[1]}"


def _plan_routes(plan: Any) -> tuple[tuple[str, str], ...]:
    """``power_plan`` 里的有序路由；非法/缺字段的项跳过，由校验报告。"""
    if not isinstance(plan, Mapping):
        return ()
    sequence = plan.get("power_sequence")
    if not isinstance(sequence, (list, tuple)):
        return ()
    routes: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in sequence:
        route = _route(item)
        if route is not None and route not in seen:
            seen.add(route)
            routes.append(route)
    return tuple(routes)


def _plan_delay(plan: Any) -> float:
    value = plan.get("inter_channel_delay_s") if isinstance(plan, Mapping) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0 or seconds > MAX_INTER_CHANNEL_DELAY_S:
        return 0.0
    return seconds


class PowerPlanEditor(QWidget):
    """一组的“通道启用 + 上电顺序 + 组间隔”控件。

    列表从上到下就是上电顺序；每一行的勾选状态表示该物理通道是否参与上电。
    控件只生成 ``{power_sequence, inter_channel_delay_s}`` 形状的计划，不判断路由是否
    属于本次分配、跨组是否重复或间隔是否超界——那些由正式校验报告。``active`` 为假
    （驱放关闭或自带供电）时读取结果是空计划，避免页面留着上一次的勾选被写回。
    """

    def __init__(self, *, active: bool = True, parent: Any = None) -> None:
        super().__init__(parent)
        self._active = bool(active)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        hint = QLabel("勾选参与上电的通道；列表顺序就是上电顺序。")
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        self.list = QListWidget()
        self.list.setObjectName("powerPlanList")
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setMinimumHeight(82)
        self.list.setMaximumHeight(170)
        move_row = QHBoxLayout()
        move_row.setSpacing(8)
        self.up_button = QPushButton("上移")
        self.down_button = QPushButton("下移")
        self.up_button.setToolTip("把选中通道提前一位")
        self.down_button.setToolTip("把选中通道推后一位")
        move_row.addWidget(self.up_button)
        move_row.addWidget(self.down_button)
        move_row.addStretch(1)
        delay_row = QHBoxLayout()
        delay_row.setSpacing(8)
        delay_label = QLabel("通道间隔 (s):")
        self.delay_spin = KeyboardOnlyDoubleSpinBox()
        self.delay_spin.setObjectName("powerPlanDelay")
        self.delay_spin.setRange(0.0, MAX_INTER_CHANNEL_DELAY_S)
        self.delay_spin.setDecimals(2)
        self.delay_spin.setSingleStep(0.5)
        self.delay_spin.setMaximumWidth(140)
        delay_row.addWidget(delay_label)
        delay_row.addWidget(self.delay_spin)
        delay_row.addStretch(1)
        self.status = QLabel()
        self.status.setObjectName("powerPlanStatus")
        self.status.setProperty("role", "hint")
        self.status.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(self.list)
        layout.addLayout(move_row)
        layout.addLayout(delay_row)
        layout.addWidget(self.status)
        self.up_button.clicked.connect(lambda: self.move_current(-1))
        self.down_button.clicked.connect(lambda: self.move_current(1))
        self.list.currentRowChanged.connect(self._update_move_buttons)
        # 勾选变化只影响本编辑器的状态与漏极候选；_update_status 不读配置，不写字段。
        self.list.itemChanged.connect(lambda _item: self._update_status())
        self._update_move_buttons()
        self.set_active(self._active)

    # ---- 写入 ---------------------------------------------------------------
    def set_plan(self, plan: Any, routes: Iterable[Any] | None = None) -> None:
        """按计划重新填列表：先按计划顺序，再补未出现在计划里的候选路由。

        计划缺失（``None``）表示“还没有确认顺序”：候选路由全部未勾选，间隔为 0。
        不能把候选路由擅自勾上——那会把尚未确认的顺序伪装成可运行计划；状态行会
        明确显示“未确认”，由操作者勾选/排序后保存。显式的空计划与缺失计划显示相同，
        但写回时保留空计划（契约允许保存未确认状态，创建运行再由正式校验阻断）。
        """
        sequence = _plan_routes(plan)
        candidates = [_route(item) for item in (routes or ())]
        order: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for route in (*sequence, *candidates):
            if route is not None and route not in seen:
                seen.add(route)
                order.append(route)
        enabled = set(sequence)
        self.list.blockSignals(True)
        self.list.clear()
        for route in order:
            item = QListWidgetItem(_route_text(route))
            item.setData(_ROUTE_ROLE, route)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if route in enabled else Qt.CheckState.Unchecked
            )
            self.list.addItem(item)
        self.delay_spin.setValue(_plan_delay(plan))
        self.list.blockSignals(False)
        self._update_move_buttons()
        self._update_status()

    def refresh(self, routes: Iterable[Any], plan: Any = None) -> None:
        """用新的候选路由重建列表，并按给定计划恢复勾选与间隔。"""
        self.set_plan(plan, routes=routes)

    def reset(self) -> None:
        """清空候选与计划（回填前的确定性基线）。"""
        self.set_plan(None, routes=())

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        self.setEnabled(self._active)
        self._update_status()

    def set_route_checked(self, route: Any, checked: bool) -> bool:
        """按路由设置勾选状态；路由不在列表里时返回 ``False``。"""
        target = _route(route)
        for row in range(self.list.count()):
            if self.list.item(row).data(_ROUTE_ROLE) == target:
                self.list.item(row).setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
                return True
        return False

    def move_row(self, row: int, delta: int) -> int:
        """把第 ``row`` 行移动 ``delta`` 位，返回移动后的行号。"""
        count = self.list.count()
        target = row + delta
        if row < 0 or row >= count or target < 0 or target >= count:
            return row
        item = self.list.takeItem(row)
        self.list.insertItem(target, item)
        self.list.setCurrentRow(target)
        self._update_move_buttons()
        return target

    def move_current(self, delta: int) -> int:
        return self.move_row(self.list.currentRow(), delta)

    # ---- 读取 ---------------------------------------------------------------
    @property
    def is_active(self) -> bool:
        return self._active

    def read_plan(self) -> dict[str, Any]:
        """读出当前列表与间隔（即使该组暂不生效，也保留界面上的勾选）。"""
        sequence = []
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            route = _route(item.data(_ROUTE_ROLE))
            if route is not None:
                sequence.append({"supply_id": route[0], "channel": route[1]})
        return {
            "power_sequence": sequence,
            "inter_channel_delay_s": float(self.delay_spin.value()),
        }

    def read_active_plan(self) -> dict[str, Any]:
        """写回配置时使用的计划；未激活的组固定写成空计划。"""
        if not self._active:
            return {"power_sequence": [], "inter_channel_delay_s": 0.0}
        return self.read_plan()

    def routes(self) -> tuple[tuple[str, str], ...]:
        routes = []
        for row in range(self.list.count()):
            route = _route(self.list.item(row).data(_ROUTE_ROLE))
            if route is not None:
                routes.append(route)
        return tuple(routes)

    def _update_move_buttons(self, _row: int | None = None) -> None:
        row = self.list.currentRow()
        count = self.list.count()
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(0 <= row < count - 1)

    def _update_status(self) -> None:
        """显示计划的确认状态（派生文本，不落库、不改变读数）。"""
        total = self.list.count()
        checked = sum(
            1
            for row in range(total)
            if self.list.item(row).checkState() == Qt.CheckState.Checked
        )
        if not self._active:
            text = "本组不使用本机直流电源，上电顺序不生效。"
        elif total == 0:
            text = "没有已分配的通道。"
        elif checked == 0:
            text = "未确认：请勾选并排序上电通道。"
        elif checked < total:
            text = f"未完成：还有 {total - checked} 个已分配通道未加入上电顺序。"
        else:
            text = f"就绪：{checked} 个通道。"
        self.status.setText(text)


class PowerFormSection(QWidget):
    """独立电源表单：通道参数、组上电计划与漏极选择。

    组件自己保存控件，页面只负责把它放进布局、转发页面级派生状态（驱放是否使用直流
    电源）并读取整份配置。上电计划是“哪些电源的哪些通道给哪个器件上电”的唯一来源，
    计划候选由已绑定电源及其声明通道生成；绑定变化时 :meth:`rebuild` 会保留操作者
    尚未保存的通道参数、顺序、间隔与漏极。
    """

    def __init__(
        self,
        *,
        sources: PowerSupplySources,
        choices: PowerSupplyChoices,
        driver_power_source: Any,
        driver_plan_active: bool = True,
        dut_power_plan: Any = None,
        driver_power_plan: Any = None,
        drain_channel: Any = None,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self._driver_plan_active = bool(driver_plan_active)
        self._driver_power_source = driver_power_source
        # 计划候选：已绑定电源的声明通道（没有声明时回落到每台电源固定的 CH1/CH2）。
        # 两个编辑器的候选相同，器件归属由“勾在哪一组”决定。
        self._plan_routes = _candidate_routes(sources, choices)
        # DUT 计划是否已经在文档里出现（含显式空计划）。未出现时漏极候选退回全部
        # 候选路由；出现后（包括用户开始勾选后）就只能从计划里选择，取消勾选会让旧
        # 漏极标为无效。
        self._dut_plan_defined = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        build_power_section(
            self,
            layout,
            sources=sources,
            driver_power_source=driver_power_source,
        )
        self._build_plan_widgets(layout)
        self._build_drain_widgets(layout)
        self._connect_assignment_signals()
        self.refresh_plan_routes(
            preserve=False,
            dut_plan=dut_power_plan,
            driver_plan=driver_power_plan,
            drain=drain_channel,
        )

    # ---- 构建 ---------------------------------------------------------------
    def _build_plan_widgets(self, layout: Any) -> None:
        group = QGroupBox("通道上电计划")
        outer = QVBoxLayout(group)
        outer.setSpacing(8)
        dut_box = QGroupBox("DUT")
        dut_layout = QVBoxLayout(dut_box)
        self.dut_power_plan_editor = PowerPlanEditor()
        dut_layout.addWidget(self.dut_power_plan_editor)
        driver_box = QGroupBox("驱动功放")
        driver_layout = QVBoxLayout(driver_box)
        self.driver_power_plan_editor = PowerPlanEditor(active=self._driver_plan_active)
        driver_layout.addWidget(self.driver_power_plan_editor)
        self.power_plan_group = group
        self.driver_power_plan_box = driver_box
        outer.addWidget(dut_box)
        outer.addWidget(driver_box)
        layout.addWidget(group)

    def _build_drain_widgets(self, layout: Any) -> None:
        group = QGroupBox("效率测量")
        form = QGridLayout(group)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(6)
        form.setColumnMinimumWidth(0, 118)
        label = QLabel("漏极通道:")
        self.drain_channel_combo = combo_box()
        self.drain_channel_combo.setToolTip(
            "漏极必须是 DUT 上电计划中的通道；只用于效率计算的电压/电流读取，"
            "不参与上电顺序、间隔或安全关断。"
        )
        hint = QLabel("漏极必须是 DUT 上电计划中的通道，仅用于效率测量，不影响上电顺序。")
        hint.setProperty("role", "hint")
        hint.setWordWrap(True)
        form.addWidget(label, 0, 0)
        form.addWidget(self.drain_channel_combo, 0, 1)
        form.addWidget(hint, 1, 0, 1, 2)
        form.setColumnStretch(1, 1)
        self.drain_channel_group = group
        layout.addWidget(group)

    def _connect_assignment_signals(self) -> None:
        # 计划的勾选决定“哪些通道参与上电”，漏极只能在其中选择；勾选变化时只刷新
        # 漏极候选，不重建计划本身（否则会打断操作者的排序）。
        self.dut_power_plan_editor.list.itemChanged.connect(self._on_dut_plan_changed)

    def _on_dut_plan_changed(self, _item: Any = None) -> None:
        plan_routes = _plan_routes(self.dut_power_plan_editor.read_plan())
        if plan_routes:
            # 操作者一旦勾选过通道，计划就从“未确认”进入“已配置”，此后取消全部勾选
            # 仍按空计划处理，不能把漏极候选退回全部候选通道。
            self._dut_plan_defined = True
        candidates = (
            plan_routes if self._dut_plan_defined else self.dut_power_plan_editor.routes()
        )
        self._set_drain_routes(candidates, self.read_drain_channel())

    # ---- 页面级派生状态 -----------------------------------------------------
    def set_power_choices(
        self, sources: PowerSupplySources, choices: PowerSupplyChoices
    ) -> None:
        """设备绑定变化后更新计划候选；已勾选的计划与当前顺序保持不变。"""
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

    # ---- 路由与计划 ---------------------------------------------------------
    def _drain_candidates(
        self, routes: tuple[tuple[str, str], ...]
    ) -> tuple[tuple[str, str], ...]:
        """漏极候选：有计划时只允许计划里参与上电的通道，否则退回全部候选路由。

        “有计划”包括显式的空计划（:attr:`_dut_plan_defined`）；空计划没有任何可测量的
        已启用通道，漏极只能保持未指定或显示旧值（由正式校验报告）。计划还没生成时先列
        所有已绑定通道，让操作者可以先选漏极，保存时会因“漏极必须包含在 DUT 上电顺序
        中”被正式校验阻断。
        """
        if self._dut_plan_defined:
            return _plan_routes(self.dut_power_plan_editor.read_plan())
        return routes

    def refresh_plan_routes(
        self,
        *,
        preserve: bool,
        dut_plan: Any = None,
        driver_plan: Any = None,
        drain: Any = None,
    ) -> None:
        """按当前候选重建计划列表，并恢复计划与漏极。

        ``preserve=True`` 用于绑定/候选变化后的重建：先读当前控件里的顺序、勾选、间隔
        和漏极，再用新候选重建，未提交编辑不会丢。候选是已绑定电源的全部声明通道；计划
        里已有、但当前绑定已解析不到的路由仍保留在列表里，交给校验报告。
        """
        if preserve:
            dut_plan = self.dut_power_plan_editor.read_plan()
            driver_plan = self.driver_power_plan_editor.read_plan()
            drain = self.read_drain_channel()
        else:
            self._dut_plan_defined = dut_plan is not None
        self.dut_power_plan_editor.refresh(self._plan_routes, plan=dut_plan)
        self.driver_power_plan_editor.set_active(self._driver_plan_active)
        self.driver_power_plan_editor.refresh(self._plan_routes, plan=driver_plan)
        self._set_drain_routes(self._drain_candidates(self._plan_routes), drain)

    def rebuild(
        self,
        *,
        sources: PowerSupplySources,
        preserved_channels: Mapping[str, Any],
        preserve: bool,
    ) -> None:
        """按新电源集合重建标签页，并按需保留操作者尚未保存的计划与漏极。"""
        dut_plan = self.dut_power_plan_editor.read_plan() if preserve else None
        driver_plan = self.driver_power_plan_editor.read_plan() if preserve else None
        drain = self.read_drain_channel() if preserve else None
        rebuild_power_tabs(self, sources=sources, preserved=preserved_channels)
        self.refresh_plan_routes(
            preserve=False,
            dut_plan=dut_plan,
            driver_plan=driver_plan,
            drain=drain,
        )

    # ---- 回填 ---------------------------------------------------------------
    def apply_power_plan_config(
        self, config: Any, *, driver_plan_active: bool | None = None
    ) -> None:
        """把一份配置文档的组计划与漏极投影到本组件（绑定变化后仍按文档为准）。

        候选来自当前已绑定电源；载入另一份配置后，计划列表仍可用文档里的计划路由
        恢复勾选与顺序，不需要再读一份按器件固定分配电源的表。
        """
        if driver_plan_active is not None:
            self._driver_plan_active = bool(driver_plan_active)
        assignment = config.get("power_supply_assignment") if isinstance(config, Mapping) else {}
        assignment = assignment if isinstance(assignment, Mapping) else {}
        dut_plan = _group_plan(assignment, "dut_amplifier")
        driver_plan = _group_plan(assignment, "driver_amplifier")
        self._dut_plan_defined = dut_plan is not None
        self.dut_power_plan_editor.refresh(self._plan_routes, plan=dut_plan)
        self.driver_power_plan_editor.set_active(self._driver_plan_active)
        self.driver_power_plan_editor.refresh(self._plan_routes, plan=driver_plan)
        self._set_drain_routes(
            self._drain_candidates(self._plan_routes), _document_drain(config)
        )

    def reset_power_plans(self) -> None:
        """回到空计划基线；回填投影开始前调用，保证不留上一次的勾选。"""
        self._dut_plan_defined = False
        self.dut_power_plan_editor.reset()
        self.driver_power_plan_editor.reset()
        self._set_drain_routes((), None)

    # ---- 漏极 ---------------------------------------------------------------
    def _set_drain_routes(
        self, routes: Iterable[tuple[str, str]], current: Any
    ) -> None:
        current_route = _route(current)
        combo = self.drain_channel_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(_UNSET_DRAIN_LABEL, None)
        known: list[tuple[str, str]] = []
        for route in routes:
            if route not in known:
                known.append(route)
                combo.addItem(_route_text(route), route)
        # 文档里显式写了、但已不在当前候选里的漏极仍显示出来，交给正式校验报告；
        # 静默清空会让一份无效配置看起来像是“用户刚取消了漏极”。
        if current_route is not None and current_route not in known:
            combo.addItem(f"{_route_text(current_route)}（无效）", current_route)
        target = 0
        for index in range(combo.count()):
            if _route(combo.itemData(index)) == current_route:
                target = index
                break
        combo.setCurrentIndex(target)
        combo.blockSignals(False)

    def read_drain_channel(self) -> dict[str, str] | None:
        combo = getattr(self, "drain_channel_combo", None)
        if combo is None:
            return None
        index_of = getattr(combo, "currentIndex", None)
        index = index_of() if callable(index_of) else -1
        item_data = getattr(combo, "itemData", None)
        data = item_data(index) if callable(item_data) and isinstance(index, int) and index >= 0 else None
        route = _route(data)
        return None if route is None else {"supply_id": route[0], "channel": route[1]}


def _group_plan(assignment: Mapping[str, Any], group: str) -> Any:
    block = assignment.get(group)
    if not isinstance(block, Mapping):
        return None
    return block.get("power_plan")


def _candidate_routes(
    sources: PowerSupplySources, choices: PowerSupplyChoices
) -> tuple[tuple[str, str], ...]:
    """上电计划候选：每台已绑定电源声明（或默认）的物理通道。

    器件归属由“勾在哪一组计划里”决定，因此两个编辑器使用同一份候选。顺序来自绑定
    集合与通道声明顺序，只影响列表初值，不表示上电顺序。
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
