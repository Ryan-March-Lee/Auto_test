"""配置编写文档（authoring document）到配置页控件的确定性投影。

配置页编辑的是**配置编写文档**：一份面向现场硬件的扁平文档（``test_frequencies``、
``signal_source``、``instruments``、``power_supply_assignment`` 等）。正式配置 schema
（``schema_version`` + ``test_plan`` / ``run_mapping``）只由
:func:`presentation.qt.configuration_authoring.author_official_configuration` 生成，
配置页与投影模块都不读取、不解释 ``test_plan`` / ``run_mapping``。

本模块负责把已冻结的配置编写文档确定性地投射到控件，并在投射前把全部受控控件重置到
空配置基线。规则集中在这里，因此：

- 配置页不再持有逐字段的类型容错/回填逻辑，只负责控件接线与用户意图转发；
- 投影只认识配置编写文档的字段名，不读取旧字段别名，也不做旧格式推断；唯一例外是
  供电分配引用，它统一走 :func:`domain.configuration.rules.assignment_supply_id`（由
  :mod:`presentation.qt.config_form_state` 再导出；``supply_id`` 为准，只有完全没有该字段
  的旧记录才回落读一次 ``name``），与配置页初次建页、文件级校验使用同一判定；
- ``instruments`` 不由控件编辑（仪器连接只来自设备发现结果），因此投影只携带它，
  不把它写到控件上；
- 电源分配的可选项由调用方按设备绑定传入（``supplies``）：配置引用了未绑定的电源时
  选择保持为空，由校验报告，绝不为未知电源临时补一个下拉选项；缺少分配时的缺省选择
  也只从这份绑定集合里取（第一台可用电源），不存在“第几号电源”的固定默认值；
- 下拉框的**显示文本与业务值分离**：显示 ``DP8B1519（DP832A）`` 这类可读标签，业务值
  （``supply_id``）放在 ``itemData`` 里，选中与读取都只认 ``itemData``，绝不从显示字符串
  反解析设备身份；
- 失败必须可识别：非 JSON 对象输入抛出 :class:`ConfigBackfillError`，不会被静默当作
  回填成功；缺失或类型不正确的字段落到明确默认值，绝不残留上一次运行的显示状态。

本模块不依赖 Qt，因此可以用纯 Python 替身控件测试投影规则。
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from application.errors import ConfigurationError
from domain.instruments.labels import supply_display_label

from .config_form_state import assignment_declared, assignment_supply_id, parse_frequency_text
from .configuration_authoring import DUT_CHANNEL_ROLES

# 配置页受控控件的空配置基线。``project_authoring_config()`` 先把控件重置到这些默认值，
# 再逐字段投影配置编写文档，从而保证投影结果与直接用同一份文档构造页面的结果一致。
DEFAULT_TEST_FREQUENCIES = [4.0, 4.4, 4.8, 5.2, 5.6, 5.8]
DEFAULT_SIGNAL_SOURCE = {"start_power": -38.0, "stop_power": -16.0, "step": 1.0}
DEFAULT_COMPRESSION_TYPE = "5dB"
DEFAULT_ATTENUATOR_TYPE = "40dB"
DEFAULT_DRIVER_MODE_ENABLED = True
DRIVER_POWER_SOURCE_LABELS = {"dc_supply": "直流电源", "external_ac": "自带供电"}
DRIVER_POWER_SOURCE_VALUES = tuple(DRIVER_POWER_SOURCE_LABELS.values())
DEFAULT_MAX_INPUT_POWER = 33.5
DEFAULT_PA_UNIT_COUNT = "2"
# 电源通道的空配置基线：重置时把工作值/保护值填成它，构建模块把非法或缺失的字段也显示
# 成它（见 :func:`presentation.qt.config_power_section.spin_value`），因此“没写这个字段”
# 与“字段类型不对”在页面上是同一个显示值，规则只有一处。
DEFAULT_POWER_CHANNEL_VALUE = 0.0
# 电源分配组合框的槽位（驱动功放 + 三个 PA 单元）。槽位是界面上的接线位置，不是电源
# 编号：每个槽位选哪台电源只来自配置引用或设备绑定，没有固定的 ``PS1``..``PS4`` 默认值。
# 重置时按这份槽位列表逐项恢复，因此槽位数量不会因为电源设备的增减而变化。
POWER_ASSIGNMENT_SLOTS = (
    "driver_power",
    "pa_unit1_power",
    "pa_unit2_power",
    "pa_unit3_power",
)
# 供电角色是接线事实，由用户在配置页显式选择；``DUT_CHANNEL_ROLES`` 只提供本机台架
# 固定接线的控件初值（CH1 栅极、CH2 漏极），装配层不再按通道名推导角色。
POWER_SUPPLY_ROLES = ("gate", "drain")

_ASSIGNMENT_ROLE_KEYS = (
    ("carrier", "pa_unit1_power"),
    ("peaking", "pa_unit2_power"),
    ("peaking2", "pa_unit3_power"),
)


class ConfigBackfillError(ConfigurationError):
    """冻结配置无法确定性地回填到配置页控件时抛出。

    回填失败必须可识别：调用方要么收到该异常，要么据此明确报告“未回填成功”，
    不得把它静默当作加载成功。
    """


def bool_or_default(value: Any, default: bool) -> bool:
    """只接受真正的布尔值；其它类型（含 ``"false"``/``1``）视为缺失，用默认值。"""
    return value if isinstance(value, bool) else default


def number_or_none(value: Any) -> float | None:
    """只接受非 bool 的数字；其它类型（含 ``"12"``/``True``）视为缺失。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def set_combo(combo, value) -> None:
    """把组合框切到已知选项；布尔值与未知选项视为缺失，保持当前项。"""
    if isinstance(value, bool) or value is None:
        return
    text = str(value)
    if combo.findText(text) >= 0:
        combo.setCurrentText(text)


def supply_option_label(supply_id: str, model: Any = None) -> str:
    """电源在分配下拉框里的显示文本：``DP8B1519（DP832A）``。

    括号里是型号（人读的补充信息），没有型号时只显示短标签。显示文本是给人看的，
    业务值始终是 ``supply_id``，见 :func:`_supply_options`：两者分离之后，型号重复、
    型号与标签相似或型号缺失都不会让两个选项在控件里变成同一个值。

    显示规则只有一份实现（:func:`domain.instruments.labels.supply_display_label`）：电源
    标签页标题、日志与身份文本都用它，不在这里另写一套格式；标签缺失时也不会从型号或
    序列号推导出一个看起来像 ``supply_id`` 的字符串。
    """
    return supply_display_label(supply_id, model)


def _supply_options(supplies) -> list[tuple[str, str]] | None:
    """规范化电源可选项为 ``(supply_id, 显示文本)``；``None`` 表示调用方没有给出绑定信息。

    ``supplies`` 是“当前绑定电源”的有序集合：元素可以是 ``supply_id`` 字符串，也可以是
    ``{supply_id: {...,"model": ...}}`` 形式的映射（带型号时显示文本里补上型号）。重复的
    ``supply_id`` 只保留第一项；显示文本与业务值分开保存，选中比较始终用 ``supply_id``。
    """
    if supplies is None:
        return None
    if isinstance(supplies, Mapping):
        entries = list(supplies.items())
    else:
        entries = [(name, None) for name in supplies]
    options: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, record in entries:
        text = str(name or "").strip()
        if not text or text in seen:
            continue
        model = record.get("model") if isinstance(record, Mapping) else None
        seen.add(text)
        options.append((text, supply_option_label(text, model)))
    return options


def _supply_ids(options: list[tuple[str, str]] | None) -> list[str]:
    """可选项的业务值列表（``itemData``），用于判断某条引用是否仍被绑定。"""
    return [supply_id for supply_id, _text in options or ()]


def _set_combo_items(combo, options: list[tuple[str, str]]) -> None:
    """按给定选项重建组合框：显示文本与 ``itemData``（``supply_id``）一起写入。

    电源分配的可选项来自设备绑定，重建可以清掉上一次投影留下的、已经不再绑定的电源；
    组合框里不应存在用户看不见来源的选项。业务值放在 ``itemData``，读取方只看它。
    """
    combo.clear()
    for supply_id, text in options:
        combo.addItem(text, supply_id)


def _select_supply(combo, supply_id: str) -> bool:
    """按下拉框 ``itemData`` 选中一台电源；找不到时保持空选择并返回 ``False``。

    选中依据必须是 ``itemData`` 里的 ``supply_id``：显示文本带型号、型号还可能重复，
    用文本匹配会出现“选中的是另一台”的情况。
    """
    index = combo.findData(supply_id) if supply_id else -1
    if index >= 0:
        combo.setCurrentIndex(index)
        return True
    combo.setCurrentIndex(-1)
    return False


def _default_supply(options: list[tuple[str, str]] | None) -> str:
    """缺省电源：第一台可用电源；没有可用电源时保持空选择。

    缺省值只从当前绑定集合里取。固定槽位（``PS1``..``PS4``）既不保证仍被绑定，也不是
    配置里真实存在的引用，用它当缺省只会回填出一个查无此机的值；也不按设备顺序把新
    设备静默填进已有角色——``options`` 的顺序就是绑定顺序，缺省只取第一台，已有引用
    由 :func:`_assign_supply` 优先处理（“已有引用”指当前配置文档里的引用，不是上一次
    控件里的残留选择）。
    """
    ids = _supply_ids(options)
    return ids[0] if ids else ""


def _assignment_target(entry, options: list[tuple[str, str]] | None) -> str:
    """一条电源分配应该选中的 ``supply_id``（空串表示空选择）。

    配置里没有这条分配时按缺省规则落到第一台可用电源；有这条分配时只认它声明的引用，
    引用为空或已不在可选项里就保持空选择，交给校验报告，绝不静默改指另一台设备。
    """
    if not assignment_declared(entry):
        return _default_supply(options)
    explicit = assignment_supply_id(entry) or ""
    if options is None:
        return explicit
    return explicit if explicit in _supply_ids(options) else ""


def _assign_supply(combo, entry, options: list[tuple[str, str]] | None) -> None:
    """把一条电源分配写到组合框。

    引用由 :func:`config_form_state.assignment_supply_id` 解析（``supply_id`` 为准，
    旧记录只回落读一次 ``name``），与配置页初次建页共用同一判定；配置里显式写出的
    电源必须真实存在于可选项里，不存在时清空选择，由校验明确报告，而不是临时把未知
    电源补进下拉框、让一份连不上硬件的配置看起来合法。

    ``entry`` 不是对象表示配置里**没有**这条分配：按缺省规则落到第一台可用电源。
    是对象但引用为空（``supply_id: null``）表示**声明了未绑定**：保持空选择，不补。
    """
    _select_supply(combo, _assignment_target(entry, options))


def project_authoring_config(
    widgets: Any, config: Any, *, supplies: Any = None
) -> dict[str, Any]:
    """把一份已冻结的配置编写文档确定性地投影到控件，返回文档副本。

    执行顺序固定：先把所有受控控件重置到空配置基线，再逐字段投影文档内容。缺失或
    类型不正确的字段会落到明确默认值（不会被强制转换成字符串或布尔值），绝不残留
    上一次运行的显示状态。参数不是 JSON 对象（``dict``）时抛出
    :class:`ConfigBackfillError`，避免把回填失败静默当作加载成功。

    只更新控件显示，不写文件、不保存、不连接仪器；调用方负责在投影成功后刷新派生
    界面状态（单元/驱动模式可见性、电源选项）并提交自己的文档副本。

    仪器连接（``instruments``）不由控件编辑，也就不在这里投影：它由设备发现结果
    （``device_bindings``）决定写入地址，页面只负责把绑定记录交给发现面板。

    ``supplies`` 是当前已绑定设备的电源名称（``None`` 表示调用方不做约束）。给了它，
    电源分配组合框的可选项就被限制在这份列表里，配置引用了未绑定的电源时选择保持为空。
    """
    if not isinstance(config, dict):
        raise ConfigBackfillError(
            "冻结配置必须是 JSON 对象（dict），无法确定性地回填配置页"
        )
    reset_control_widgets(widgets, supplies=supplies)
    _apply_test_parameter_widgets(widgets, config)
    _apply_power_supply_widgets(widgets, config.get("instruments"))
    _apply_power_assignment_widgets(
        widgets, config.get("power_supply_assignment"), supplies=supplies
    )
    return copy.deepcopy(config)


def reset_control_widgets(widgets: Any, *, supplies: Any = None) -> None:
    """把所有受控控件重置到空配置基线，消除上一次运行的残留值。"""
    widgets.freq_edit.setText(str(list(DEFAULT_TEST_FREQUENCIES)))
    widgets.start_power.setValue(DEFAULT_SIGNAL_SOURCE["start_power"])
    widgets.stop_power.setValue(DEFAULT_SIGNAL_SOURCE["stop_power"])
    widgets.power_step.setValue(DEFAULT_SIGNAL_SOURCE["step"])
    set_combo(widgets.compression_combo, DEFAULT_COMPRESSION_TYPE)
    set_combo(widgets.attenuator_combo, DEFAULT_ATTENUATOR_TYPE)
    widgets.test_mode_enabled = DEFAULT_DRIVER_MODE_ENABLED
    if hasattr(widgets, "driver_power_source_combo"):
        set_combo(widgets.driver_power_source_combo, "直流电源")
    widgets.max_input_power.setValue(DEFAULT_MAX_INPUT_POWER)
    set_combo(widgets.pa_unit_count_combo, DEFAULT_PA_UNIT_COUNT)
    _reset_power_supply_channels(widgets)
    _reset_power_assignment_widgets(widgets, supplies=supplies)


def _reset_power_supply_channels(widgets: Any) -> None:
    for record in getattr(widgets, "power_config_widgets", {}).values():
        for channel, controls in record.get("channels", {}).items():
            for key in ("voltage", "voltage_protection", "current", "current_protection"):
                controls[key].setValue(DEFAULT_POWER_CHANNEL_VALUE)
            for key in ("voltage_protection_enabled", "current_protection_enabled"):
                controls[key].setChecked(False)
            controls["role"].setCurrentText(
                DUT_CHANNEL_ROLES.get(channel, POWER_SUPPLY_ROLES[0])
            )


def _reset_power_assignment_widgets(widgets: Any, *, supplies: Any = None) -> None:
    """把电源分配槽位重置为空配置基线：重建选项，缺省落到第一台可用电源。

    重置必须是无状态、确定性的：只读 ``supplies``（当前设备绑定），不读控件上一次的
    选择。否则投影结果会依赖上一次运行的残留值，而“已有引用优先”指的只是当前配置
    文档里的引用（见 :func:`_assign_supply`），不该由重置阶段替它挑选。
    """
    controls = getattr(widgets, "power_assignment_widgets", {})
    if not controls:
        return
    if "driver_source" in controls:
        set_combo(controls["driver_source"], "直流电源")
    options = _supply_options(supplies)
    target = _default_supply(options)
    for key in POWER_ASSIGNMENT_SLOTS:
        combo = controls.get(key)
        if combo is None:
            continue
        if options is not None:
            _set_combo_items(combo, options)
        _select_supply(combo, target)


def _apply_test_parameter_widgets(widgets: Any, config: Mapping[str, Any]) -> None:
    frequencies = config.get("test_frequencies")
    # 复用频率解析规则：非列表、空列表或含非法元素时保持基线默认值。
    if isinstance(frequencies, list) and parse_frequency_text(repr(frequencies)) is not None:
        widgets.freq_edit.setText(str(frequencies))
    signal_source = config.get("signal_source")
    signal_source = signal_source if isinstance(signal_source, dict) else {}
    for widget, key in (
        (widgets.start_power, "start_power"),
        (widgets.stop_power, "stop_power"),
        (widgets.power_step, "step"),
    ):
        value = number_or_none(signal_source.get(key))
        if value is not None:
            widget.setValue(value)
    compression = config.get("compression_point")
    compression = compression if isinstance(compression, dict) else {}
    set_combo(widgets.compression_combo, compression.get("type"))
    attenuator = config.get("attenuator")
    attenuator = attenuator if isinstance(attenuator, dict) else {}
    set_combo(widgets.attenuator_combo, attenuator.get("type"))
    mode = config.get("driver_mode")
    mode = mode if isinstance(mode, dict) else {}
    widgets.test_mode_enabled = bool_or_default(mode.get("enabled"), DEFAULT_DRIVER_MODE_ENABLED)
    if hasattr(widgets, "driver_power_source_combo"):
        set_combo(widgets.driver_power_source_combo, {"dc_supply": "直流电源", "external_ac": "自带供电"}.get(mode.get("power_source"), "直流电源"))
    dut = config.get("dut_config")
    dut = dut if isinstance(dut, dict) else {}
    value = number_or_none(dut.get("max_input_power"))
    if value is not None:
        widgets.max_input_power.setValue(value)
    set_combo(widgets.pa_unit_count_combo, dut.get("power_supply_count"))


def _apply_power_supply_widgets(widgets: Any, instruments: Any) -> None:
    instruments = instruments if isinstance(instruments, dict) else {}
    supplies = instruments.get("power_supplies")
    supplies = supplies if isinstance(supplies, dict) else {}
    for name, record in getattr(widgets, "power_config_widgets", {}).items():
        source = supplies.get(name)
        source = source if isinstance(source, dict) else {}
        channels = source.get("channels")
        channels = channels if isinstance(channels, dict) else {}
        for channel, controls in record.get("channels", {}).items():
            values = channels.get(channel)
            values = values if isinstance(values, dict) else {}
            _apply_channel_widgets(controls, values)


def _apply_channel_widgets(controls: Mapping[str, Any], values: Mapping[str, Any]) -> None:
    voltage = values.get("voltage")
    voltage = voltage if isinstance(voltage, dict) else {}
    current = values.get("current")
    current = current if isinstance(current, dict) else {}
    role = values.get("role")
    role_widget = controls.get("role")
    if role_widget is not None and role in POWER_SUPPLY_ROLES:
        role_widget.setCurrentText(role)
    for key, value in (
        ("voltage", voltage.get("value")),
        ("voltage_protection", voltage.get("protection")),
        ("current", current.get("value")),
        ("current_protection", current.get("protection")),
    ):
        number = number_or_none(value)
        if number is not None:
            controls[key].setValue(number)
    for key, value in (
        ("voltage_protection_enabled", voltage.get("protection_enabled")),
        ("current_protection_enabled", current.get("protection_enabled")),
    ):
        controls[key].setChecked(bool_or_default(value, False))


def driver_power_source_label(
    power_source: Any = None, assignment_count: Any = None
) -> str:
    """驱放的供电方式（界面文本）：``driver_mode.power_source`` 优先，缺失时看分配。

    配置页初次建页、``apply_config`` 回填与 ``set_test_mode`` 共用这一个推断，避免同一
    份配置在两条路径上得到不同的供电方式——它连带决定驱动功放槽位是否可用，也会被写回
    ``driver_mode``。
    """
    if power_source in DRIVER_POWER_SOURCE_LABELS:
        return DRIVER_POWER_SOURCE_LABELS[power_source]
    count = number_or_none(assignment_count)
    if count is not None and count > 0:
        return DRIVER_POWER_SOURCE_LABELS["dc_supply"]
    return DRIVER_POWER_SOURCE_LABELS["external_ac"]


def _apply_power_assignment_widgets(
    widgets: Any, assignment: Any, *, supplies: Any = None
) -> None:
    """投影供电分配：每个槽位“已有引用优先，否则第一台可用电源，否则空选择”。

    已有引用指**本次投影的文档里**这条分配的真实 ``supply_id``；它仍被绑定时保持不动，
    文档缺这条分配或引用失效时才按缺省规则处理，并且绝不把某台新设备静默填进已有角色。
    """
    assignment = assignment if isinstance(assignment, dict) else {}
    driver = assignment.get("driver_amplifier")
    driver = driver if isinstance(driver, dict) else {}
    dut = assignment.get("dut_amplifier")
    dut = dut if isinstance(dut, dict) else {}
    controls = getattr(widgets, "power_assignment_widgets", {})
    if not controls:
        return
    options = _supply_options(supplies)
    driver_supplies = driver.get("supplies")
    driver_supplies = driver_supplies if isinstance(driver_supplies, dict) else {}
    if "driver_source" in controls:
        # 供电方式由调用方按 :func:`driver_power_source_label` 算好后放在页面状态里
        # （``driver_power_source``），投影只把它写到控件上，不另立一套推断。
        set_combo(
            controls["driver_source"],
            getattr(widgets, "driver_power_source", None),
        )
    _assign_supply(controls["driver_power"], driver_supplies.get("main"), options)
    dut_supplies = dut.get("supplies")
    dut_supplies = dut_supplies if isinstance(dut_supplies, dict) else {}
    for role, key in _ASSIGNMENT_ROLE_KEYS:
        _assign_supply(controls[key], dut_supplies.get(role), options)


def _first_available_supply(supplies: Any = None) -> str:
    """第一台可用电源（当前绑定集合里的第一个）；没有绑定时返回空串。

    配置页初次建页与 :func:`_apply_power_assignment_widgets` 共用这一条缺省规则，
    页面不再自带第二套“第几号电源”的默认值。
    """
    return _default_supply(_supply_options(supplies))


def apply_power_choice(combo, entry, supplies: Any) -> None:
    """把一条电源分配回填到配置页的下拉框（配置页初次建页使用）。

    ``supplies`` 是本次绑定的电源集合（``None`` 表示调用方不约束可选项）。规则与
    :func:`_assign_supply` 完全一致，因此建页与整份回填不会对同一份配置给出不同选择：

    - 配置里有这条分配且引用仍在可选项里：选中该引用（按 ``itemData``，不看显示文本）；
    - 配置里有这条分配但引用为空或已失效：保持空选择，交给校验报告；
    - 配置里没有这条分配：落到第一台可用电源，没有可用电源时保持空选择。

    所有槽位（含驱动功放）都用同一条缺省规则；驱动功放是否真的启用由页面根据驱放
    模式与供电方式决定，不在回填阶段另行发明第几号默认值。
    """
    options = _supply_options(supplies)
    if options is not None:
        _set_combo_items(combo, options)
    _select_supply(combo, _assignment_target(entry, options))


__all__ = [
    "ConfigBackfillError",
    "DEFAULT_ATTENUATOR_TYPE",
    "DEFAULT_COMPRESSION_TYPE",
    "DEFAULT_DRIVER_MODE_ENABLED",
    "DRIVER_POWER_SOURCE_LABELS",
    "DRIVER_POWER_SOURCE_VALUES",
    "DEFAULT_MAX_INPUT_POWER",
    "DEFAULT_PA_UNIT_COUNT",
    "DEFAULT_POWER_CHANNEL_VALUE",
    "DEFAULT_SIGNAL_SOURCE",
    "DEFAULT_TEST_FREQUENCIES",
    "POWER_ASSIGNMENT_SLOTS",
    "POWER_SUPPLY_ROLES",
    "apply_power_choice",
    "driver_power_source_label",
    "project_authoring_config",
    "reset_control_widgets",
    "supply_option_label",
]
