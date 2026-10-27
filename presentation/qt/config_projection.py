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
DEFAULT_MAX_INPUT_POWER = 33.5
DEFAULT_PA_UNIT_COUNT = "2"
DEFAULT_ENABLED_POWER_SUPPLIES = ("PS1", "PS2")
DEFAULT_POWER_SUPPLY_NAMES = ("PS1", "PS2", "PS3", "PS4")
# (控件前缀, 中文标签, 配置键, 默认启用) —— 同时供页面构建与投影使用。
INSTRUMENT_ENTRIES = (
    ("sg", "信号发生器", "signal_generator", True),
    ("sa", "频谱分析仪", "spectrum_analyzer", True),
)
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


def project_authoring_config(widgets: Any, config: Any) -> dict[str, Any]:
    """把一份已冻结的配置编写文档确定性地投影到控件，返回文档副本。

    执行顺序固定：先把所有受控控件重置到空配置基线，再逐字段投影文档内容。缺失或
    类型不正确的字段会落到明确默认值（不会被强制转换成字符串或布尔值），绝不残留
    上一次运行的显示状态。参数不是 JSON 对象（``dict``）时抛出
    :class:`ConfigBackfillError`，避免把回填失败静默当作加载成功。

    只更新控件显示，不写文件、不保存、不连接仪器；调用方负责在投影成功后刷新派生
    界面状态（单元/驱动模式可见性、电源选项）并提交自己的文档副本。
    """
    if not isinstance(config, dict):
        raise ConfigBackfillError(
            "冻结配置必须是 JSON 对象（dict），无法确定性地回填配置页"
        )
    reset_control_widgets(widgets)
    _apply_instrument_widgets(widgets, config.get("instruments"))
    _apply_test_parameter_widgets(widgets, config)
    _apply_power_supply_widgets(widgets, config.get("instruments"))
    _apply_power_assignment_widgets(widgets, config.get("power_supply_assignment"))
    return copy.deepcopy(config)


def reset_control_widgets(widgets: Any) -> None:
    """把所有受控控件重置到空配置基线，消除上一次运行的残留值。"""
    for name, _label, _source, default_enabled in INSTRUMENT_ENTRIES:
        address = getattr(widgets, f"{name}_address", None)
        if address is not None:
            address.setText("")
        enabled = getattr(widgets, f"{name}_enabled", None)
        if enabled is not None:
            enabled.setChecked(bool(default_enabled))
    for name in DEFAULT_POWER_SUPPLY_NAMES:
        address = getattr(widgets, f"{name.lower()}_address", None)
        if address is not None:
            address.setText("")
        enabled = getattr(widgets, f"{name.lower()}_enabled", None)
        if enabled is not None:
            enabled.setChecked(name in DEFAULT_ENABLED_POWER_SUPPLIES)
    widgets.freq_edit.setText(str(list(DEFAULT_TEST_FREQUENCIES)))
    widgets.start_power.setValue(DEFAULT_SIGNAL_SOURCE["start_power"])
    widgets.stop_power.setValue(DEFAULT_SIGNAL_SOURCE["stop_power"])
    widgets.power_step.setValue(DEFAULT_SIGNAL_SOURCE["step"])
    set_combo(widgets.compression_combo, DEFAULT_COMPRESSION_TYPE)
    set_combo(widgets.attenuator_combo, DEFAULT_ATTENUATOR_TYPE)
    widgets.driver_mode_check.setChecked(DEFAULT_DRIVER_MODE_ENABLED)
    widgets.max_input_power.setValue(DEFAULT_MAX_INPUT_POWER)
    set_combo(widgets.pa_unit_count_combo, DEFAULT_PA_UNIT_COUNT)
    _reset_power_supply_channels(widgets)
    _reset_power_assignment_widgets(widgets)


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


def _reset_power_assignment_widgets(widgets: Any) -> None:
    controls = getattr(widgets, "power_assignment_widgets", {})
    if not controls:
        return
    controls["driver_enabled"].setChecked(False)
    for key, default in DEFAULT_POWER_ASSIGNMENT.items():
        set_combo(controls[key], default)


def _apply_instrument_widgets(widgets: Any, instruments: Any) -> None:
    instruments = instruments if isinstance(instruments, dict) else {}
    supplies = instruments.get("power_supplies")
    supplies = supplies if isinstance(supplies, dict) else {}
    for name, _label, source, default_enabled in INSTRUMENT_ENTRIES:
        item = instruments.get(source)
        item = item if isinstance(item, dict) else {}
        address = getattr(widgets, f"{name}_address", None)
        if address is not None:
            address.setText(text_or_empty(item.get("address")))
        enabled = getattr(widgets, f"{name}_enabled", None)
        if enabled is not None:
            enabled.setChecked(
                bool_or_default(item.get("enabled"), bool(default_enabled))
            )
    for name in DEFAULT_POWER_SUPPLY_NAMES:
        item = supplies.get(name)
        item = item if isinstance(item, dict) else {}
        address = getattr(widgets, f"{name.lower()}_address", None)
        if address is not None:
            address.setText(text_or_empty(item.get("address")))
        enabled = getattr(widgets, f"{name.lower()}_enabled", None)
        if enabled is not None:
            enabled.setChecked(
                bool_or_default(item.get("enabled"), name in DEFAULT_ENABLED_POWER_SUPPLIES)
            )


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
    widgets.driver_mode_check.setChecked(
        bool_or_default(mode.get("enabled"), DEFAULT_DRIVER_MODE_ENABLED)
    )
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


def _apply_power_assignment_widgets(widgets: Any, assignment: Any) -> None:
    assignment = assignment if isinstance(assignment, dict) else {}
    driver = assignment.get("driver_amplifier")
    driver = driver if isinstance(driver, dict) else {}
    dut = assignment.get("dut_amplifier")
    dut = dut if isinstance(dut, dict) else {}
    controls = getattr(widgets, "power_assignment_widgets", {})
    if not controls:
        return
    driver_supplies = driver.get("supplies")
    driver_supplies = driver_supplies if isinstance(driver_supplies, dict) else {}
    main = driver_supplies.get("main")
    main = main if isinstance(main, dict) else {}
    count = number_or_none(driver.get("power_supply_count"))
    controls["driver_enabled"].setChecked(count is not None and count > 0)
    set_combo(
        controls["driver_power"],
        text_or_empty(main.get("name")) or DEFAULT_POWER_ASSIGNMENT["driver_power"],
    )
    dut_supplies = dut.get("supplies")
    dut_supplies = dut_supplies if isinstance(dut_supplies, dict) else {}
    for role, key in _ASSIGNMENT_ROLE_KEYS:
        item = dut_supplies.get(role)
        item = item if isinstance(item, dict) else {}
        set_combo(
            controls[key],
            text_or_empty(item.get("name")) or DEFAULT_POWER_ASSIGNMENT[key],
        )


__all__ = [
    "ConfigBackfillError",
    "DEFAULT_ATTENUATOR_TYPE",
    "DEFAULT_COMPRESSION_TYPE",
    "DEFAULT_DRIVER_MODE_ENABLED",
    "DEFAULT_ENABLED_POWER_SUPPLIES",
    "DEFAULT_MAX_INPUT_POWER",
    "DEFAULT_PA_UNIT_COUNT",
    "DEFAULT_POWER_ASSIGNMENT",
    "DEFAULT_POWER_SUPPLY_NAMES",
    "DEFAULT_SIGNAL_SOURCE",
    "DEFAULT_TEST_FREQUENCIES",
    "INSTRUMENT_ENTRIES",
    "POWER_SUPPLY_ROLES",
    "project_authoring_config",
    "reset_control_widgets",
]
