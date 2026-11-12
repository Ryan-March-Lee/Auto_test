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
- 功率扫描：页面提供 ``apply_power_scan_plans`` 时把 ``signal_source.power_scan_plans``
  按原顺序重建为动态行；旧替身没有该入口时仍把单段映射到起始/终止/步长三个控件，
  多段只展示首段起点与末段终点并留下 ``power_scan_import_error``，绝不静默折叠；
- 失败必须可识别：非 JSON 对象输入抛出 :class:`ConfigBackfillError`，不会被静默当作
  回填成功；缺失或类型不正确的字段落到明确默认值，绝不残留上一次运行的显示状态。

本模块不依赖 Qt，因此可以用纯 Python 替身控件测试投影规则。

归属：presentation adapter。通道初值是**显示策略**
（第 5.3 节），去掉它正式文档不变，因此不是业务规则；正式 `test_plan` / `run_mapping`
由配置编写服务生成（步骤 4 迁到 `application`）。
"""

from __future__ import annotations

import copy
import math
from typing import Any, Mapping

from application.errors import ConfigurationError
from application.dto.amplifier_inputs import DEFAULT_POWER_ON_DELAY_S
from domain.instruments.labels import supply_display_label

from .frequency_sweep import (
    DEFAULT_FREQUENCY_EXTRA_TEXT,
    DEFAULT_FREQUENCY_START,
    DEFAULT_FREQUENCY_STEP,
    DEFAULT_FREQUENCY_STOP,
    DEFAULT_TEST_FREQUENCIES,
    FrequencySweepError,
    best_effort_sweep,
    format_frequency,
    sweep_from_frequencies,
)

# 配置页受控控件的空配置基线。``project_authoring_config()`` 先把控件重置到这些默认值，
# 再逐字段投影配置编写文档，从而保证投影结果与直接用同一份文档构造页面的结果一致。
#
# 功率扫描的计划默认值只有一处：``DEFAULT_POWER_SCAN_PLAN`` 按控件显示顺序保存
# ``(起始, 步长, 终止)``，动态行编辑器与旧单计划控件都从这里取值，不再各自写一遍。
# ``DEFAULT_SIGNAL_SOURCE`` 只是旧标量控件基线的派生别名（保留键名兼容），新增代码
# 应直接读元组，不要把它当成第二个默认值来源。
DEFAULT_POWER_SCAN_PLAN = (-38.0, 1.0, -16.0)
DEFAULT_SIGNAL_SOURCE = {
    "start_power": DEFAULT_POWER_SCAN_PLAN[0],
    "step": DEFAULT_POWER_SCAN_PLAN[1],
    "stop_power": DEFAULT_POWER_SCAN_PLAN[2],
}
DEFAULT_COMPRESSION_TYPE = "5dB"
DEFAULT_ATTENUATOR_TYPE = "40dB"
DEFAULT_DRIVER_MODE_ENABLED = True
# 驱动功放预估增益的空配置基线：空文本表示“尚未填写”，不是 0 dB。
DEFAULT_DRIVER_GAIN_ESTIMATE = ""
DEFAULT_MAX_INPUT_POWER = 33.5
# 电源通道的空配置基线：重置时把工作值/保护值填成它，卡片输入框也把非法或缺失的字段
# 显示成它（见 :func:`presentation.qt.power_card.PowerSupplyCard.set_data`），因此“没写
# 这个字段”与“字段类型不对”在页面上是同一个显示值，规则只有一处。
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


def driver_gain_text(value: Any) -> str:
    """把配置里的驱动功放预估增益渲染成控件文本。

    只接受有限数值：``None``、布尔值、字符串与非有限值都回到空基线（“尚未填写”），
    不替用户猜一个 0 dB。数值用最短往返表示，避免把用户填的高精度增益截成近似值；
    控件文本怎么解析回数字由 :func:`presentation.qt.config_form_state.driver_gain_value`
    负责，两处不各写一套格式。
    """
    gain = number_or_none(value)
    if gain is None or not math.isfinite(gain):
        return DEFAULT_DRIVER_GAIN_ESTIMATE
    return repr(gain)


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
    显示文本、日志与身份文本都用它，不在这里另写一套格式；缩写码缺失时也不会从型号或
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


def reset_frequency_fields(widgets: Any) -> None:
    """把频率扫描控件重置到空配置基线，并清除加载错误。"""
    widgets.freq_start.setValue(DEFAULT_FREQUENCY_START)
    widgets.freq_stop.setValue(DEFAULT_FREQUENCY_STOP)
    widgets.freq_step.setValue(DEFAULT_FREQUENCY_STEP)
    widgets.freq_extra.setText(DEFAULT_FREQUENCY_EXTRA_TEXT)
    widgets.frequency_import_error = None


def apply_frequency_fields(widgets: Any, frequencies: Any) -> None:
    """把配置文档里的 ``test_frequencies`` 投影到扫描控件。

    严格等步长列表才能被扫描参数完整表达；否则控件只做只读展示用的“尽力摆放”，
    同时在 ``frequency_import_error`` 上留下错误，页面据此禁止保存。这里不拆分旧
    列表、不猜步长，避免把一份不等步长配置静默改写成另一组频点。
    """
    if not isinstance(frequencies, (list, tuple)) or not frequencies:
        reset_frequency_fields(widgets)
        widgets.frequency_import_error = "测试频率必须是非空数字列表"
        return
    if not all(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and float(value) > 0
        for value in frequencies
    ):
        reset_frequency_fields(widgets)
        widgets.frequency_import_error = "测试频率必须都是大于 0 的有限数值"
        return
    try:
        start, stop, step = sweep_from_frequencies(frequencies)
    except FrequencySweepError:
        start, stop, step = best_effort_sweep(frequencies)
        values = "，".join(format_frequency(value) for value in frequencies)
        widgets.freq_start.setValue(start)
        widgets.freq_stop.setValue(stop)
        widgets.freq_step.setValue(step)
        widgets.freq_extra.setText("")
        widgets.frequency_import_error = (
            "配置中的测试频率不是严格等步长序列，无法用起始/结束/步长表示："
            f"{values}；请重新输入扫描参数，不在网格上的频点请写入增补"
        )
        return
    widgets.freq_start.setValue(start)
    widgets.freq_stop.setValue(stop)
    widgets.freq_step.setValue(step)
    widgets.freq_extra.setText("")
    widgets.frequency_import_error = None


def reset_power_scan_fields(widgets: Any) -> None:
    """把 DUT 功率扫描计划重置为空配置基线的一行默认计划。

    动态行编辑器由页面持有，回填前必须整组回到默认行，不能只改第一行的三个输入框；
    页面提供 ``reset_power_scan_plans`` 时由它重建行（含编号、删除按钮与预览）。
    没有该入口的旧替身仍按三个单计划控件复位，保持替身测试与页面的空配置口径一致。
    """
    reset_rows = getattr(widgets, "reset_power_scan_plans", None)
    if callable(reset_rows):
        reset_rows()
        return
    widgets.start_power.setValue(DEFAULT_POWER_SCAN_PLAN[0])
    widgets.power_step.setValue(DEFAULT_POWER_SCAN_PLAN[1])
    widgets.stop_power.setValue(DEFAULT_POWER_SCAN_PLAN[2])
    widgets.power_scan_import_error = None


def reset_control_widgets(widgets: Any) -> None:
    """把所有受控控件重置到空配置基线，消除上一次运行的残留值。"""
    reset_frequency_fields(widgets)
    set_combo(widgets.compression_combo, DEFAULT_COMPRESSION_TYPE)
    set_combo(widgets.attenuator_combo, DEFAULT_ATTENUATOR_TYPE)
    widgets.test_mode_enabled = DEFAULT_DRIVER_MODE_ENABLED
    widgets.driver_gain.setText(DEFAULT_DRIVER_GAIN_ESTIMATE)
    widgets.max_input_power.setValue(DEFAULT_MAX_INPUT_POWER)
    # 计划重置放在上限之后：页面重建行时会按 DUT 上限刷新预览，先用新上限才能得到
    # 与回填后一致的灰色点位文本。
    reset_power_scan_fields(widgets)
    reset_power_controls(widgets)


def _reset_power_supply_channels(widgets: Any) -> None:
    for record in getattr(widgets, "power_config_widgets", {}).values():
        for controls in record.get("channels", {}).values():
            for key in ("voltage", "current"):
                controls[key].setValue(DEFAULT_POWER_CHANNEL_VALUE)
            for key in ("voltage_protection_enabled", "current_protection_enabled"):
                controls[key].setChecked(False)
            delay_control = controls.get("power_on_delay")
            if delay_control is not None:
                delay_control.setValue(DEFAULT_POWER_ON_DELAY_S)


def _power_scan_projection(
    signal_source: Mapping[str, Any],
) -> tuple[str | None, float | None, float | None, float | None]:
    """把配置里的功率扫描投影成单行控件的 ``(错误, 起始, 终止, 步长)``。

    1.2 文档的扫描来源是 ``power_scan_plans``。真实页面通过
    ``apply_power_scan_plans`` 按行重建；本函数只服务没有动态行入口的旧替身：

    - 单段计划：正常回填三个控件，保存时会按控件读数重写这一段；
    - 多段计划：控件只用于展示首段起点与末段终点，并在页面上留下错误，禁止保存
      ——静默折叠成一段会丢掉用户已确认的后面几段；
    - 计划缺失/为空/不是列表：同样给出导入错误，不把空计划伪装成默认值。

    旧 1.1 文档的标量形状继续按三个字段读取，保证旧配置仍可打开与重新保存。
    “计划缺失”按键判断：显式 ``power_scan_plans: null`` 是计划缺失，不能退回
    旧标量分支掩盖它。
    """
    if "power_scan_plans" not in signal_source:
        return (
            None,
            number_or_none(signal_source.get("start_power")),
            number_or_none(signal_source.get("stop_power")),
            number_or_none(signal_source.get("step")),
        )
    plans = signal_source.get("power_scan_plans")
    if not isinstance(plans, list) or not plans:
        return ("配置的 DUT 功率扫描计划为空或格式不正确", None, None, None)
    starts = [
        number_or_none(item.get("start_power"))
        for item in plans
        if isinstance(item, Mapping)
    ]
    stops = [
        number_or_none(item.get("stop_power"))
        for item in plans
        if isinstance(item, Mapping)
    ]
    steps = [
        number_or_none(item.get("step"))
        for item in plans
        if isinstance(item, Mapping)
    ]
    if not starts or not stops or not steps:
        return ("配置的 DUT 功率扫描计划格式不正确", None, None, None)
    if len(plans) > 1:
        return (
            "配置包含多段 DUT 功率扫描计划，当前控件只能编辑单段；"
            "为避免丢失已确认的计划，保存已被阻止",
            starts[0],
            stops[-1],
            steps[0],
        )
    return (None, starts[0], stops[0], steps[0])


def _apply_test_parameter_widgets(widgets: Any, config: Mapping[str, Any]) -> None:
    # 键缺席时保留空配置基线；键存在但值非法/不等步长时由 apply_frequency_fields
    # 写入错误状态，页面据此禁止保存。
    if "test_frequencies" in config:
        apply_frequency_fields(widgets, config.get("test_frequencies"))
    signal_source = config.get("signal_source")
    apply_scan_plans = getattr(widgets, "apply_power_scan_plans", None)
    if callable(apply_scan_plans):
        # 动态计划编辑器自己按行重建；结构错误由它返回，投影只负责放进页面状态。
        # 这里传原始值，让页面区分“键缺失”和“声明了 signal_source 却没有计划”。
        widgets.power_scan_import_error = apply_scan_plans(signal_source)
    else:
        signal_source = signal_source if isinstance(signal_source, dict) else {}
        scan_error, start, stop, step = _power_scan_projection(signal_source)
        widgets.power_scan_import_error = scan_error
        for widget, value in (
            (widgets.start_power, start),
            (widgets.stop_power, stop),
            (widgets.power_step, step),
        ):
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
    # 预估增益缺失或类型不对时回到空基线（“尚未填写”），不替用户猜一个 0 dB。
    widgets.driver_gain.setText(driver_gain_text(mode.get("estimated_gain_db")))
    dut = config.get("dut_config")
    dut = dut if isinstance(dut, dict) else {}
    value = number_or_none(dut.get("max_input_power"))
    if value is not None:
        widgets.max_input_power.setValue(value)


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
        ("current", current.get("value")),
    ):
        number = number_or_none(value)
        if number is not None:
            controls[key].setValue(number)
    for key, value in (
        ("voltage_protection_enabled", voltage.get("protection_enabled")),
        ("current_protection_enabled", current.get("protection_enabled")),
    ):
        controls[key].setChecked(bool_or_default(value, False))
    delay_control = controls.get("power_on_delay")
    if delay_control is not None:
        delay = number_or_none(values.get("power_on_delay_s"))
        delay_control.setValue(
            DEFAULT_POWER_ON_DELAY_S if delay is None else max(0.0, delay)
        )


__all__ = [
    "ConfigBackfillError",
    "DEFAULT_ATTENUATOR_TYPE",
    "DEFAULT_COMPRESSION_TYPE",
    "DEFAULT_DRIVER_GAIN_ESTIMATE",
    "DEFAULT_DRIVER_MODE_ENABLED",
    "DEFAULT_FREQUENCY_EXTRA_TEXT",
    "DEFAULT_FREQUENCY_START",
    "DEFAULT_FREQUENCY_STEP",
    "DEFAULT_FREQUENCY_STOP",
    "DEFAULT_MAX_INPUT_POWER",
    "DEFAULT_POWER_CHANNEL_VALUE",
    "DEFAULT_POWER_SCAN_PLAN",
    "DEFAULT_SIGNAL_SOURCE",
    "DEFAULT_TEST_FREQUENCIES",
    "apply_frequency_fields",
    "bool_or_default",
    "driver_gain_text",
    "number_or_none",
    "project_authoring_config",
    "project_power_config",
    "reset_control_widgets",
    "reset_frequency_fields",
    "reset_power_controls",
    "reset_power_scan_fields",
    "supply_option_label",
]
