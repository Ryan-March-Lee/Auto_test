"""配置编写文档（authoring document）到配置页控件的确定性投影。

配置页编辑的是**配置编写文档**：一份面向现场硬件的扁平文档（``test_frequencies``、
``signal_source``、``instruments``、``power_supply_assignment`` 等）。正式配置 schema
（``schema_version`` + ``test_plan`` / ``run_mapping``）只由
:func:`presentation.qt.configuration_authoring.author_official_configuration` 生成，
配置页与投影模块都不读取、不解释 ``test_plan`` / ``run_mapping``。

本模块负责把已冻结的配置编写文档确定性地投射到控件，并在投射前把全部受控控件重置到
空配置基线。规则集中在这里，因此：

- 配置页不再持有逐字段的类型容错/回填逻辑，只负责控件接线与用户意图转发；
- 投影只认识配置编写文档的字段名，不读取任何旧字段别名，也不做旧格式推断；
- ``instruments`` 不由控件编辑（仪器连接只来自设备发现结果），因此投影只携带它，
  不把它写到控件上；
- 电源分配的可选项由调用方按设备绑定传入（``supplies``）：配置引用了未绑定的电源时
  选择保持为空，由校验报告，绝不为未知电源临时补一个下拉选项；
- 失败必须可识别：非 JSON 对象输入抛出 :class:`ConfigBackfillError`，不会被静默当作
  回填成功；缺失或类型不正确的字段落到明确默认值，绝不残留上一次运行的显示状态。

本模块不依赖 Qt，因此可以用纯 Python 替身控件测试投影规则。
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from application.errors import ConfigurationError

from .config_form_state import parse_frequency_text
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
# 电源分配组合框的默认映射，重置时逐项恢复。
DEFAULT_POWER_ASSIGNMENT = {
    "driver_power": "PS1",
    "pa_unit1_power": "PS2",
    "pa_unit2_power": "PS3",
    "pa_unit3_power": "PS4",
}
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


def text_or_empty(value: Any) -> str:
    """只接受字符串文本（如仪器地址）；其它类型一律视为缺失，返回空串。"""
    return value if isinstance(value, str) else ""


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


def _supply_options(supplies) -> list[str] | None:
    """规范化电源可选项；``None`` 表示“调用方没有给出绑定信息”，不要改动选项。"""
    if supplies is None:
        return None
    options: list[str] = []
    for name in supplies:
        text = str(name or "").strip()
        if text and text not in options:
            options.append(text)
    return options


def _set_combo_items(combo, items) -> None:
    """按给定选项重建组合框。

    电源分配的可选项来自设备绑定，重建可以清掉上一次投影留下的、已经不再绑定的电源；
    组合框里不应存在用户看不见来源的选项。
    """
    combo.clear()
    combo.addItems(list(items))


def _default_supply(key: str, options: list[str] | None) -> str:
    """默认电源：优先沿用表单默认值，其次第一个可选电源。"""
    default = DEFAULT_POWER_ASSIGNMENT[key]
    if options is None:
        return default
    if not options:
        return ""
    return default if default in options else options[0]


def _assign_supply(combo, name, key: str, options: list[str] | None) -> None:
    """把一条电源分配写到组合框。

    配置里显式写出的电源必须真实存在于可选项里；不存在时清空选择，由校验明确报告，
    而不是临时把未知电源补进下拉框、让一份连不上硬件的配置看起来合法。
    """
    explicit = text_or_empty(name).strip()
    if explicit:
        target = explicit if options is None or explicit in options else ""
    else:
        target = _default_supply(key, options)
    if target:
        set_combo(combo, target)
    else:
        combo.setCurrentIndex(-1)


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
                controls[key].setValue(0.0)
            for key in ("voltage_protection_enabled", "current_protection_enabled"):
                controls[key].setChecked(False)
            controls["role"].setCurrentText(
                DUT_CHANNEL_ROLES.get(channel, POWER_SUPPLY_ROLES[0])
            )


def _reset_power_assignment_widgets(widgets: Any, *, supplies: Any = None) -> None:
    controls = getattr(widgets, "power_assignment_widgets", {})
    if not controls:
        return
    if "driver_source" in controls:
        set_combo(controls["driver_source"], "直流电源")
    options = _supply_options(supplies)
    for key in DEFAULT_POWER_ASSIGNMENT:
        combo = controls.get(key)
        if combo is None:
            continue
        if options is not None:
            _set_combo_items(combo, options)
        target = _default_supply(key, options)
        if target:
            set_combo(combo, target)
        else:
            combo.setCurrentIndex(-1)


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


def _apply_power_assignment_widgets(
    widgets: Any, assignment: Any, *, supplies: Any = None
) -> None:
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
    main = driver_supplies.get("main")
    main = main if isinstance(main, dict) else {}
    count = number_or_none(driver.get("power_supply_count"))
    if "driver_source" in controls:
        set_combo(controls["driver_source"], "直流电源" if count is not None and count > 0 else "自带供电")
    _assign_supply(controls["driver_power"], main.get("name"), "driver_power", options)
    dut_supplies = dut.get("supplies")
    dut_supplies = dut_supplies if isinstance(dut_supplies, dict) else {}
    for role, key in _ASSIGNMENT_ROLE_KEYS:
        item = dut_supplies.get(role)
        item = item if isinstance(item, dict) else {}
        _assign_supply(controls[key], item.get("name"), key, options)


__all__ = [
    "ConfigBackfillError",
    "DEFAULT_ATTENUATOR_TYPE",
    "DEFAULT_COMPRESSION_TYPE",
    "DEFAULT_DRIVER_MODE_ENABLED",
    "DRIVER_POWER_SOURCE_LABELS",
    "DRIVER_POWER_SOURCE_VALUES",
    "DEFAULT_MAX_INPUT_POWER",
    "DEFAULT_PA_UNIT_COUNT",
    "DEFAULT_POWER_ASSIGNMENT",
    "DEFAULT_SIGNAL_SOURCE",
    "DEFAULT_TEST_FREQUENCIES",
    "POWER_SUPPLY_ROLES",
    "project_authoring_config",
    "reset_control_widgets",
]
