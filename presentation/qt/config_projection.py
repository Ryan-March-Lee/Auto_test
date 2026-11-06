"""配置编写文档（authoring document）到配置页控件的确定性投影。

配置页编辑的是**配置编写文档**：一份面向现场硬件的扁平文档（``test_frequencies``、
``signal_source``、``instruments``、``power_supply_assignment`` 等）。正式配置 schema
（``schema_version`` + ``test_plan`` / ``run_mapping``）只由
:class:`application.configuration_authoring.ConfigurationAuthoringService` 生成，
配置页与投影模块都不读取、不解释 ``test_plan`` / ``run_mapping``。

本模块负责把已冻结的配置编写文档确定性地投射到控件，并在投射前把全部受控控件重置到
空配置基线。规则集中在这里，因此：

- 配置页不再持有逐字段的类型容错/回填逻辑，只负责控件接线与用户意图转发；
- 投影只认识配置编写文档的字段名，不读取旧字段别名，也不做旧格式推断；旧 `power_supply_assignment.*.supplies` / `power_supply_count` 不再参与投影（器件电源由计划路由决定），只有通道工作值与漏极仍从这里回填；
- ``instruments`` 不由控件编辑（仪器连接只来自设备发现结果），因此投影只携带它，
  不把它写到控件上；
- 上电计划与漏极属于电源表单组件：投影只重置它们，实际回填由
  ``PowerFormSection.apply_power_plan_config`` 按同一份文档完成；计划路由就是“哪个电源的
  哪个通道给哪个器件上电”的唯一来源；
- 电源候选（计划列表的可选项）由页面按设备绑定与电源声明的通道生成，投影不挑选电源、
  不补默认值；引用了未绑定电源的路由保留在计划里，由正式校验报告；
- 失败必须可识别：非 JSON 对象输入抛出 :class:`ConfigBackfillError`，不会被静默当作
  回填成功；缺失或类型不正确的字段落到明确默认值，绝不残留上一次运行的显示状态。

本模块不依赖 Qt，因此可以用纯 Python 替身控件测试投影规则。

归属（见 `docs/配置领域词汇与模块归属.md`）：presentation adapter。通道初值是**显示策略**
（第 5.3 节），去掉它正式文档不变，因此不是业务规则；正式 `test_plan` / `run_mapping`
由配置编写服务生成（步骤 4 迁到 `application`）。
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from application.errors import ConfigurationError
from domain.instruments.labels import supply_display_label

from .config_form_state import parse_frequency_text

# 配置页受控控件的空配置基线。``project_authoring_config()`` 先把控件重置到这些默认值，
# 再逐字段投影配置编写文档，从而保证投影结果与直接用同一份文档构造页面的结果一致。
DEFAULT_TEST_FREQUENCIES = [4.0, 4.4, 4.8, 5.2, 5.6, 5.8]
DEFAULT_SIGNAL_SOURCE = {"start_power": -38.0, "stop_power": -16.0, "step": 1.0}
DEFAULT_COMPRESSION_TYPE = "5dB"
DEFAULT_ATTENUATOR_TYPE = "40dB"
DEFAULT_DRIVER_MODE_ENABLED = True
DEFAULT_MAX_INPUT_POWER = 33.5
DEFAULT_PA_UNIT_COUNT = "2"
# 电源通道的空配置基线：重置时把工作值/保护值填成它，构建模块把非法或缺失的字段也显示
# 成它（见 :func:`presentation.qt.config_power_section.spin_value`），因此“没写这个字段”
# 与“字段类型不对”在页面上是同一个显示值，规则只有一处。
DEFAULT_POWER_CHANNEL_VALUE = 0.0


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
    """电源的显示文本：``DP832A（DP8B1519）``。

    括号里是缩写设备码（人读的区分信息），没有型号时只显示缩写设备码。显示文本是给人看
    的，业务身份始终是 ``supply_id``（计划路由与集合键都只用它），绝不从显示字符串反解析
    设备身份。

    显示规则只有一份实现（:func:`domain.instruments.labels.supply_display_label`）：电源
    标签页标题、日志与身份文本都用它，不在这里另写一套格式；缩写码缺失时也不会从型号或
    完整设备码推导出一个看起来像 ``supply_id`` 的字符串。
    """
    return supply_display_label(supply_id, model)


def project_authoring_config(widgets: Any, config: Any) -> dict[str, Any]:
    """把一份已冻结的配置编写文档确定性地投影到控件，返回文档副本。

    执行顺序固定：先把所有受控控件重置到空配置基线，再逐字段投影文档内容。缺失或
    类型不正确的字段会落到明确默认值（不会被强制转换成字符串或布尔值），绝不残留
    上一次运行的显示状态。参数不是 JSON 对象（``dict``）时抛出
    :class:`ConfigBackfillError`，避免把回填失败静默当作加载成功。

    只更新控件显示，不写文件、不保存、不连接仪器；调用方负责在投影成功后刷新派生
    界面状态（驱动模式可见性、计划候选）并提交自己的文档副本。

    仪器连接（``instruments``）不由控件编辑，也就不在这里投影：它由设备发现结果
    （``device_bindings``）决定写入地址，页面只负责把绑定记录交给发现面板。

    器件电源不再通过下拉框选择：计划路由是唯一来源，这里不限制也不补齐任何选项。
    """
    if not isinstance(config, dict):
        raise ConfigBackfillError(
            "冻结配置必须是 JSON 对象（dict），无法确定性地回填配置页"
        )
    reset_control_widgets(widgets)
    _apply_test_parameter_widgets(widgets, config)
    _apply_power_supply_widgets(widgets, config.get("instruments"))
    return copy.deepcopy(config)


def project_power_config(widgets: Any, config: Any) -> dict[str, Any]:
    """把一份配置编写文档里的电源字段投影到独立电源页的控件。

    与 :func:`project_authoring_config` 的区别只在受控范围：测试参数与运行控件归
    完整配置页，电源页只重置并回填通道参数与组计划（计划与漏极由电源表单组件在同一
    入口回填）。两处共用的重置规则（通道基线、计划清空）只实现一次，避免独立页面与
    完整页面出现两套回填口径。

    参数不是 JSON 对象时同样抛出 :class:`ConfigBackfillError`，与整份投影的失败
    语义一致。
    """
    if not isinstance(config, dict):
        raise ConfigBackfillError(
            "冻结配置必须是 JSON 对象（dict），无法确定性地回填电源配置页"
        )
    reset_power_controls(widgets)
    _apply_power_supply_widgets(widgets, config.get("instruments"))
    return copy.deepcopy(config)


def reset_power_controls(widgets: Any) -> None:
    """把电源页受控控件重置到空配置基线（不触碰测试参数控件）。"""
    _reset_power_supply_channels(widgets)
    reset_plans = getattr(widgets, "reset_power_plans", None)
    if callable(reset_plans):
        # 计划与漏极属于独立电源表单；回填前一起回到空基线，避免旧勾选残留。
        reset_plans()


def reset_control_widgets(widgets: Any) -> None:
    """把所有受控控件重置到空配置基线，消除上一次运行的残留值。"""
    widgets.freq_edit.setText(str(list(DEFAULT_TEST_FREQUENCIES)))
    widgets.start_power.setValue(DEFAULT_SIGNAL_SOURCE["start_power"])
    widgets.stop_power.setValue(DEFAULT_SIGNAL_SOURCE["stop_power"])
    widgets.power_step.setValue(DEFAULT_SIGNAL_SOURCE["step"])
    set_combo(widgets.compression_combo, DEFAULT_COMPRESSION_TYPE)
    set_combo(widgets.attenuator_combo, DEFAULT_ATTENUATOR_TYPE)
    widgets.test_mode_enabled = DEFAULT_DRIVER_MODE_ENABLED
    widgets.max_input_power.setValue(DEFAULT_MAX_INPUT_POWER)
    set_combo(widgets.pa_unit_count_combo, DEFAULT_PA_UNIT_COUNT)
    reset_power_controls(widgets)


def _reset_power_supply_channels(widgets: Any) -> None:
    for record in getattr(widgets, "power_config_widgets", {}).values():
        for controls in record.get("channels", {}).values():
            for key in ("voltage", "voltage_protection", "current", "current_protection"):
                controls[key].setValue(DEFAULT_POWER_CHANNEL_VALUE)
            for key in ("voltage_protection_enabled", "current_protection_enabled"):
                controls[key].setChecked(False)


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


__all__ = [
    "ConfigBackfillError",
    "DEFAULT_ATTENUATOR_TYPE",
    "DEFAULT_COMPRESSION_TYPE",
    "DEFAULT_DRIVER_MODE_ENABLED",
    "DEFAULT_MAX_INPUT_POWER",
    "DEFAULT_PA_UNIT_COUNT",
    "DEFAULT_POWER_CHANNEL_VALUE",
    "DEFAULT_SIGNAL_SOURCE",
    "DEFAULT_TEST_FREQUENCIES",
    "project_authoring_config",
    "project_power_config",
    "reset_control_widgets",
    "reset_power_controls",
    "supply_option_label",
]
