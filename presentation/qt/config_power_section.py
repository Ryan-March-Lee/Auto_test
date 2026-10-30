"""配置页电源分区的控件构建。

本模块构建配置页右栏的两块控件：按电源键分页的「电源详细配置」标签页，以及
「电源分配设置」表单。构建所需的配置条目与可选项由页面
（:mod:`presentation.qt.config_page`）读出后传入，控件仍登记在页面上——页面负责信号
连接、派生界面状态（PA 单元可见性、驱放供电方式）和配置读写。

这里只按配置条目的**字段位置**读出要显示的数值，并把不正确的类型回落到与投影一致的
缺省值（见 :func:`spin_value` 与通道角色回退）：那是显示口径，不是校验。本模块不校验
配置、不迁移旧字段、不写配置、也不读文件，更不持有页面状态；字段的校验与编写仍归
:mod:`presentation.qt.config_projection` 与
:mod:`presentation.qt.configuration_authoring`，「槽位 -> 配置路径」的映射留在页面
（``ConfigPage._assignment_entries``）。重建标签页时「保留操作者刚填的参数」用到的合并
规则也在这里（见 :func:`channel_map`），它只合并控件快照与配置条目，不判断字段语义。
"""

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QGridLayout, QGroupBox, QLabel, QTabWidget,
    QVBoxLayout, QWidget,
)

from domain.instruments.labels import supply_identity_text

from .config_page_widgets import combo_box, spin_box
from .config_projection import (
    DRIVER_POWER_SOURCE_VALUES,
    POWER_SUPPLY_ROLES,
    apply_power_choice,
    supply_option_label,
)
from .configuration_authoring import DUT_CHANNEL_ROLES

# 每台电源固定两个通道：通道名既是配置里的键，也是标签页内通道分组框的标题。
POWER_SUPPLY_CHANNELS = ("CH1", "CH2")

__all__ = [
    "POWER_SUPPLY_CHANNELS",
    "build_power_section",
    "channel_map",
    "current_power_tab_key",
    "power_choice_combo",
    "rebuild_power_tabs",
    "spin_value",
]


def spin_value(value) -> float:
    """通道数值只接受非 bool 的有限数字；其它类型（字符串/布尔/NaN）按 0 处理。

    配置页也用于回填冻结配置：类型不正确的字段不应让重建标签页崩溃，而应落到与投影
    一致的默认值，交给正式校验报告。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    return number if math.isfinite(number) else 0.0


def _entry_channels(entry) -> dict:
    """取出一个电源条目的 ``通道 -> 参数`` 映射；不是对象或没有映射时返回空字典。

    条目本身是一份完整电源配置（``{model, channels: {...}}``）。
    """
    channels = entry.get("channels") if isinstance(entry, dict) else None
    return channels if isinstance(channels, dict) else {}


def channel_map(source, preserved) -> dict:
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


def power_choice_combo(choices, entry):
    """电源分配下拉框：只把配置里的真实引用回填到当前可选项。

    引用按 ``supply_id`` 读取（旧配置只有 ``name`` 时读一次，见
    :func:`config_form_state.assignment_supply_id`），并且必须**出现在本次绑定的
    可选项里**才会被选中。配置写了引用、但那台电源本次没有绑定（含还没迁移的旧槽位
    ``PS1``）时保持空选择，交给校验报告——静默停在第一项会把“这台电源已经不在”
    显示成“换成了另一台电源”。

    显示文本是 ``DP8B1519（DP832A）`` 这样的短标签加型号，业务值放在 ``itemData``
    （``supply_id``）；选中与读取都只看数据，不从显示字符串反解析身份。

    两种状态严格分开（见 :func:`config_form_state.assignment_declared`）：配置里
    **没有**这条分配时落到第一台可用电源；配置里**有**这条分配但引用为空
    （``supply_id: null``）时保持空选择，不能按缺省规则补一台。判定与整份配置回填
    共用 :func:`presentation.qt.config_projection.apply_power_choice`，所有槽位
    （含驱动功放）用同一条缺省规则；驱动功放是否启用由
    :meth:`ConfigPage.update_driver_power_ui` 按驱放模式与供电方式决定。
    """
    combo = combo_box()
    apply_power_choice(combo, entry, choices)
    return combo


def build_power_section(
    page,
    layout,
    *,
    sources,
    choices,
    assignment_entries,
    driver_power_source,
) -> None:
    """构建电源详细配置与电源分配设置，并把页面要读写的控件登记到 ``page``。

    - ``sources``：标签页要显示的电源条目（配置里已有的键 + 本次绑定的 ``supply_id``），
      由页面合并配置与绑定后传入。
    - ``choices``：电源分配下拉框的可选项（``supply_id -> 条目``），只来自当前绑定。
    - ``assignment_entries``：槽位 -> 配置里该槽位已有的引用条目，``None`` 表示配置
      没有这条分配（与“声明了但引用为空”在回填时不是一回事）。
    - ``driver_power_source``：页面按驱放模式推断出的供电方式显示文本。
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

    assignment = QGroupBox("电源分配设置")
    form = QGridLayout(assignment)
    form.setHorizontalSpacing(10)
    form.setVerticalSpacing(8)
    form.setColumnMinimumWidth(0, 118)
    page.power_assignment_group = assignment
    page.driver_power_source_combo = combo_box(
        DRIVER_POWER_SOURCE_VALUES, str(driver_power_source)
    )
    page.driver_power_source_label = QLabel("驱放供电方式:")
    page.driver_power_hint = QLabel("自带供电的驱放由外部供电设备供电，本系统不会控制其电源。")
    page.driver_power_hint.setWordWrap(True)
    # 所有槽位（含驱动功放）用同一条缺省规则：已有引用优先，否则第一台可用电源。
    # 驱动功放是否真的启用由 update_driver_power_ui() 按驱放模式与供电方式处理。
    page.driver_power_combo = power_choice_combo(
        choices, assignment_entries.get("driver_power")
    )
    page.pa_unit1_power_combo = power_choice_combo(
        choices, assignment_entries.get("pa_unit1_power")
    )
    page.pa_unit2_power_combo = power_choice_combo(
        choices, assignment_entries.get("pa_unit2_power")
    )
    page.pa_unit3_power_combo = power_choice_combo(
        choices, assignment_entries.get("pa_unit3_power")
    )
    page.driver_power_label = QLabel("驱动功放电源:")
    page.pa_unit2_label = QLabel("PA Unit2电源:")
    page.pa_unit3_label = QLabel("PA Unit3电源:")
    # 两列布局（标签 + 下拉框），避免右栏过窄时撑出横向滚动条。
    form.addWidget(page.driver_power_source_label, 0, 0)
    form.addWidget(page.driver_power_source_combo, 0, 1)
    form.addWidget(page.driver_power_hint, 1, 0, 1, 2)
    form.addWidget(page.driver_power_label, 2, 0)
    form.addWidget(page.driver_power_combo, 2, 1)
    form.addWidget(QLabel("PA Unit1电源:"), 3, 0)
    form.addWidget(page.pa_unit1_power_combo, 3, 1)
    form.addWidget(page.pa_unit2_label, 4, 0)
    form.addWidget(page.pa_unit2_power_combo, 4, 1)
    form.addWidget(page.pa_unit3_label, 5, 0)
    form.addWidget(page.pa_unit3_power_combo, 5, 1)
    form.setColumnStretch(1, 1)
    layout.addWidget(assignment)
    page.power_assignment_widgets = {
        "driver_source": page.driver_power_source_combo,
        "driver_power": page.driver_power_combo,
        "pa_unit1_power": page.pa_unit1_power_combo,
        "pa_unit2_power": page.pa_unit2_power_combo,
        "pa_unit2_label": page.pa_unit2_label,
        "pa_unit3_power": page.pa_unit3_power_combo,
        "pa_unit3_label": page.pa_unit3_label,
    }


def rebuild_power_tabs(page, *, sources, preserved) -> None:
    """按电源键重建标签页，并用 ``preserved`` 保留操作者刚填的通道参数。

    电源数量变化（应用/清除绑定、加载另一份配置）后重建，避免按位置把某一台设备的
    参数写到另一台上。通道参数按**配置键**取回（不是按标签页位置），当前选中的标签页
    也按**电源键**恢复：某台电源被移除或新增时，操作者仍然停在原来那台设备上。
    ``preserved`` 为空是回填路径，以新配置为准，不沿用重建前的残留值。

    页面入口已经确认电源分区存在（``ConfigPage._rebuild_power_tabs``），因此这里直接
    使用 ``page.power_tabs``：分区不存在属于调用方的编程错误，不静默返回。
    """
    tabs = page.power_tabs
    current_key = current_power_tab_key(page)
    tabs.clear()
    page.power_config_widgets = {}
    for key, source in sources.items():
        source = source if isinstance(source, dict) else {}
        _add_power_tab(page, key, source, channel_map(source, preserved.get(key)))
    if current_key in page.power_config_widgets:
        tabs.setCurrentIndex(tabs.indexOf(page.power_config_widgets[current_key]["page"]))


def current_power_tab_key(page) -> str | None:
    """当前选中电源标签页的电源键；没有标签页时返回 ``None``。"""
    tabs = page.power_tabs
    if not tabs.count():
        return None
    index = tabs.currentIndex()
    for key, record in page.power_config_widgets.items():
        if record.get("page") is tabs.widget(index):
            return key
    return None


def _add_power_tab(page, key, source, channels) -> None:
    """为一条电源配置建一个标签页；标题是短标签加型号（``DP8B1519（DP832A）``）。

    标签页按配置键（``supply_id``）建，标题只用于显示：设备数量与身份都来自配置与
    当前绑定，没有固定的四个槽位页。

    标签页的悬浮提示给**详情与审计**：短标签、型号、完整序列号与设备键（见
    :func:`domain.instruments.labels.supply_identity_text`）。卡面上只放标签与型号，
    因为标题栏很窄；追溯时把指针停在标签页上就能读到完整序列号。旧槽位条目没有序列号，
    提示里就只有它的旧键（``PS1``），不推导一个不存在的标签。
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
        role = value.get("role")
        if role not in POWER_SUPPLY_ROLES:
            role = DUT_CHANNEL_ROLES.get(channel, POWER_SUPPLY_ROLES[0])
        controls = {
            "role": combo_box(POWER_SUPPLY_ROLES, role),
            "voltage": spin_box(spin_value(voltage.get("value")), 0, 50),
            "voltage_protection": spin_box(spin_value(voltage.get("protection")), 0, 50),
            "voltage_protection_enabled": QCheckBox("启用"),
            "current": spin_box(spin_value(current.get("value")), 0, 10),
            "current_protection": spin_box(spin_value(current.get("protection")), 0, 10),
            "current_protection_enabled": QCheckBox("启用"),
        }
        controls["voltage_protection_enabled"].setChecked(
            voltage.get("protection_enabled") is True
        )
        controls["current_protection_enabled"].setChecked(
            current.get("protection_enabled") is True
        )
        form.addRow("供电角色", controls["role"])
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
    # 详情/审计文本与标题同源：标签 + 型号 + 完整序列号 + 设备键，缺哪个就少哪段。
    page.power_tabs.setTabToolTip(
        index,
        supply_identity_text(
            source.get("label") or key,
            source.get("model"),
            source.get("serial"),
            source.get("device_key"),
        ),
    )
    page.power_config_widgets[key] = record
