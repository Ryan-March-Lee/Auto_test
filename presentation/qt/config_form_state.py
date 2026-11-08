"""纯 Python 配置表单状态适配器。

该模块只依赖控件的最小读取协议（``text``、``value``、``isChecked`` 和 ``currentText``），以及页面
持有的 ``device_bindings``（仪器连接的唯一来源），因此可以在没有 QApplication 的情况下
测试配置组装。

职责边界：本模块读表单控件、把读数合并成配置片段，并给出页面自己的校验说明。设备
绑定记录本身的读取与规范化（``supply_id`` 集合、旧结构折叠、不可用记录的问题清单）
在 :mod:`presentation.qt.config_form_bindings`，这里只消费它给出的结果。

归属：presentation adapter。读数后的合法性判据属于
`domain` 配置规则；正式运行校验与正式配置编写都委托
`application.configuration_authoring`，本模块只把当前的读数包成配置编辑 DTO 并展示
返回的消息。上电计划的判据只由 domain 定义（步骤 13 收口），这里不再保留页面副本。
"""

import copy
import math
from typing import Any, Mapping

from application.errors import ConfigurationError
from domain.instruments.models import INSTRUMENT_ROLES

from .config_form_bindings import binding_address, power_supply_bindings
from .frequency_sweep import (
    FrequencySweepError,
    generate_frequencies,
    parse_extra_frequencies,
)

#: 保护阈值相对工作值的比例：页面只让用户勾选是否启用，阈值自动取 110%。
PROTECTION_RATIO = 1.1
#: 保护阈值保留的小数位，与通道数值框的显示精度一致。
PROTECTION_DECIMALS = 2


def protection_threshold(value: Any) -> float:
    """按工作值计算保护阈值：工作值 × 110%，保留两位小数。

    保护阈值不再由用户填写，因此这里只有一条规则，读取配置与保存配置都走它。
    非法或非有限值按 0 处理，与通道数值框的非法值回落口径一致。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        return 0.0
    return round(number * PROTECTION_RATIO, PROTECTION_DECIMALS)


def _dict_copy(value: Any) -> dict:
    return copy.deepcopy(value) if isinstance(value, dict) else {}


def driver_gain_value(text: Any) -> float | str | None:
    """把“驱动功放预估增益”控件的文本转成编辑文档里的值。

    空文本表示尚未填写（``None``），不折叠成 0；能解析成数字时写数字，否则把原文
    交给配置 DTO 校验并报出“必须是有限数值”。页面不判断范围或有限性，也不承担任何
    增益推算——控件只负责把用户输入原样交给正式校验。
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return text


def _formal_validation_errors(
    document: Mapping[str, Any], *, scope: str = "all"
) -> list[str]:
    """在应用边界编写并校验一份编辑文档，返回用户可见的错误消息。

    编写与正式校验都在 application 边界完成（GUI 重构步骤 4）：页面只把当前读数
    包成配置编辑 DTO 并展示消息，不导入 domain 的正式模型或校验函数。只把
    :class:`ConfigurationError` 转成页面错误；其它异常是程序缺陷，不能被改写成
    “配置有问题”的提示而掩盖掉。

    ``scope`` 决定页面归属：``"parameters"`` 只返回非电源问题，``"power"`` 只返回
    独立电源页负责的问题，两页合起来仍是完整校验；``"all"`` 保留整份结果。
    """
    from application.configuration_authoring import (
        ConfigurationAuthoringService,
        ConfigurationEditingDocument,
    )

    service = ConfigurationAuthoringService()
    editing = ConfigurationEditingDocument.from_mapping(document)
    try:
        if scope == "power":
            result = service.validate_power_document(editing)
        elif scope == "parameters":
            result = service.validate_parameter_document(editing)
        else:
            result = service.validate_document(editing)
    except ConfigurationError as failure:
        return [f"无法按当前配置生成正式运行映射: {failure}"]
    return [issue.message for issue in result.errors]


def strip_channel_roles(supplies: Any) -> None:
    """原地删除电源通道里的旧 ``role`` 字段（1.1 编辑段不再包含它）。

    电源参数已拆到独立页，但测试配置页组装文档时仍会带上基线里的旧字段；两条写入路径
    都调用这里，保证保存出的文档不因页面上没有对应控件而把 1.0 角色写回去。
    """
    if not isinstance(supplies, Mapping):
        return
    for supply in supplies.values():
        if not isinstance(supply, dict):
            continue
        channels = supply.get("channels")
        if not isinstance(channels, dict):
            continue
        for channel in channels.values():
            if isinstance(channel, dict):
                channel.pop("role", None)


def _clear_driver_power_plan(document):
    """关闭驱放测试模式时清掉文档里遗留的驱动功放上电计划。

    页面在关闭驱放后不再提交驱动计划（不占本机电源），但协调器持有的配置副本仍带着
    上一次保存的 ``driver_amplifier.power_plan``；两份副本因此不一致。正式校验会按
    “驱放模式关闭时不能配置驱动功放上电计划”拒绝它，于是页面刚做的正确选择反而让运行
    创建失败。这里只清这一段计划，电源通道参数与 DUT 计划原样保留。
    """
    assignment = _dict_copy(document.get("power_supply_assignment"))
    if not assignment:
        return False
    driver = assignment.get("driver_amplifier")
    if not isinstance(driver, dict) or "power_plan" not in driver:
        return False
    driver["power_plan"] = {"power_sequence": [], "inter_channel_delay_s": 0.0}
    assignment["driver_amplifier"] = driver
    document["power_supply_assignment"] = assignment
    return True


def _apply_device_binding(item: dict, binding: Any) -> None:
    """按绑定记录写出配置项的连接状态。

    绑定是唯一连接来源：没有可用地址时清空地址并显式禁用，用户清除绑定后旧地址不会
    继续留在配置里冒充可连接设备；通道、模型、扩展字段等非连接字段保持不变。
    显式禁用也是正式校验的要求——``instruments.signal_generator`` 缺省为“启用”，只清
    地址不置 ``enabled: false`` 会让一份未连接设备的配置无法通过校验。
    """
    item.pop("address", None)
    address = binding_address(binding)
    item["enabled"] = bool(address)
    if address:
        item["address"] = address


def _apply_identity_fields(item: dict, record: Any) -> None:
    """把绑定记录里的设备身份写进配置条目；记录没有声明的字段保持原值。

    记录里出现的字段才是本次绑定的身份来源；字段值为空时删除配置里的旧值，避免出现
    “地址来自这台、身份还是上一台”的条目。记录完全没写的字段不删除，因为它可能是这台
    设备自己的既有事实（发现结果不总是带齐所有字段）。
    """
    if not isinstance(record, Mapping):
        return
    for field in ("label", "model", "full_code", "device_key"):
        if field not in record:
            continue
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            item[field] = value
        else:
            item.pop(field, None)


class ConfigFormState:
    """从配置页面控件读取片段，并合并为完整配置。"""

    def __init__(self, config: Mapping[str, Any] | None = None, widgets: Any = None):
        self.config = config if isinstance(config, Mapping) else {}
        self.widgets = widgets

    @staticmethod
    def _text(widget):
        return widget.text()

    @staticmethod
    def _checked(widget):
        return widget.isChecked()

    @staticmethod
    def _value(widget):
        return widget.value()

    @staticmethod
    def _current(widget):
        return widget.currentText()

    def validation_errors(self) -> list[str]:
        """测试配置页要显示的用户错误：只报本页负责的字段。

        整份配置的正式校验按页面归属拆开：本页只展示测试参数、设备连接等非电源问题；
        上电计划、通道与漏极的问题由独立电源页展示。两页合起来仍是完整校验，因此最终
        动作不需要第二份校验入口，也不会把电源故障写进本页。
        """
        errors = []
        frequency_error = self.frequency_error()
        if frequency_error:
            errors.append(frequency_error)
        errors.extend(
            _formal_validation_errors(self.build_config(), scope="parameters")
        )
        return errors

    def frequency_error(self) -> str | None:
        """返回当前频率扫描读数或加载状态的用户错误；合法时返回 ``None``。

        加载错误优先于控件读数：非等步长的旧列表虽然被放进了控件做展示，但在用户
        重新输入扫描参数之前不能被当成可保存的配置。
        """
        imported = getattr(self.widgets, "frequency_import_error", None)
        if imported:
            return str(imported)
        try:
            self.frequency_values()
        except FrequencySweepError as failure:
            return str(failure)
        return None

    def frequency_values(self) -> tuple[float, ...]:
        """按控件读数生成频点列表；非法参数抛出 :class:`FrequencySweepError`。"""
        w = self.widgets
        extras = parse_extra_frequencies(self._text(w.freq_extra))
        return generate_frequencies(
            self._value(w.freq_start),
            self._value(w.freq_stop),
            self._value(w.freq_step),
            extras,
        )

    def formal_validation_errors(self) -> list[str]:
        """按整张表单做正式校验（含设备连接与电源段）。

        测试配置页不再调用它；保留为共享实现，供电源页与单独校验表单状态的测试使用。
        """
        return _formal_validation_errors(self.build_config())

    def read_instrument_config(self) -> dict:
        """按设备发现结果写出仪器连接信息。

        连接配置的唯一来源是 :attr:`widgets.device_bindings`：信号源与频谱仪各一条
        （角色键），电源是 ``power_supplies`` 里按 ``supply_id`` 的多条。绑定了设备的
        项写入该设备的当前地址并标记启用；没有绑定的项一律清空地址并标记为未启用，
        不再沿用配置里遗留的地址——否则“清除绑定”只是隐藏了入口，旧设备仍会被连接。

        旧槽位键（``PS1``..``PS4``）同样是“未绑定”的配置项：通道参数原样保留，地址与
        启用状态随绑定一起失效，等迁移器把参数改挂到真正的 ``supply_id`` 上。

        身份字段（``label`` / ``model`` / ``full_code`` / ``device_key``）只认绑定记录里
        实际出现的字段：记录没有的字段保留配置里的既有事实（发现结果不总是带齐型号等
        字段），记录里出现但为空时则删除，避免出现“地址来自这台、身份还是上一台”的条目。
        """
        instruments = _dict_copy(self.config.get("instruments"))
        supplies = _dict_copy(instruments.get("power_supplies"))
        bindings = getattr(self.widgets, "device_bindings", None)
        bindings = bindings if isinstance(bindings, Mapping) else {}
        power_records = power_supply_bindings(bindings)
        for name in list(supplies):
            item = _dict_copy(supplies.get(name))
            _apply_device_binding(item, power_records.get(name))
            _apply_identity_fields(item, power_records.get(name))
            supplies[name] = item
        for supply_id, record in power_records.items():
            item = _dict_copy(supplies.get(supply_id))
            _apply_device_binding(item, record)
            _apply_identity_fields(item, record)
            # 集合的键就是引用 ID，条目里保留同名 ``label`` 便于人读与旧读取方兼容。
            item["label"] = supply_id
            supplies[supply_id] = item
        for name in INSTRUMENT_ROLES:
            item = _dict_copy(instruments.get(name))
            record = bindings.get(name)
            _apply_device_binding(item, record)
            _apply_identity_fields(item, record)
            instruments[name] = item
        instruments["power_supplies"] = supplies
        return {"instruments": instruments}

    def read_test_parameters(self) -> dict:
        w = self.widgets
        result = {}
        if not getattr(w, "frequency_import_error", None):
            try:
                frequencies = self.frequency_values()
            except FrequencySweepError:
                pass
            else:
                result["test_frequencies"] = list(frequencies)
        signal_source = {
            "start_power": self._value(w.start_power),
            "stop_power": self._value(w.stop_power),
            "step": self._value(w.power_step),
        }
        # 功率语义标记不是页面可编辑字段：基线里有就原样保留（新配置），没有也保持缺失
        # （旧配置）。是否写入新语义只由保存边界决定，不能在每次读数时悄悄改写。
        existing_source = _dict_copy(self.config.get("signal_source"))
        if "power_reference" in existing_source:
            signal_source["power_reference"] = existing_source.get("power_reference")
        result.update({
            "signal_source": signal_source,
            "compression_point": {"type": self._current(w.compression_combo)},
            "attenuator": {"type": self._current(w.attenuator_combo)},
            "driver_mode": self._read_driver_mode(w),
        })
        return result

    def _read_driver_mode(self, w: Any) -> dict:
        """读取驱放测试模式；供电方式由驱动功放上电计划是否有路由表达。

        ``estimated_gain_db`` 是测试配置里的全局驱动增益预估，读自页面的
        ``driver_gain`` 控件：空文本写 ``None``（尚未填写），非法文本原样交给配置
        DTO 报错。控件不存在时（旧替身或尚未接入的表单）保留基线值，不擅自改写；
        驱动关闭时增益不是运行必填项，页面仍保留用户已填写的内容。
        """
        mode: dict = {"enabled": bool(getattr(w, "test_mode_enabled", False))}
        gain_widget = getattr(w, "driver_gain", None)
        if gain_widget is None:
            existing = _dict_copy(self.config.get("driver_mode"))
            if "estimated_gain_db" in existing:
                mode["estimated_gain_db"] = existing.get("estimated_gain_db")
            return mode
        mode["estimated_gain_db"] = driver_gain_value(self._text(gain_widget))
        return mode

    def read_power_supply_config(self) -> dict:
        result = {}
        for name, supply in getattr(self.widgets, "power_config_widgets", {}).items():
            channels = {}
            for channel, controls in supply.get("channels", {}).items():
                # 1.1 正式配置不再写通道 role；保护阈值由工作值推导，用户只勾选是否启用。
                voltage = self._value(controls["voltage"])
                current = self._value(controls["current"])
                channel_config = {
                    "voltage": {
                        "value": voltage,
                        "protection": protection_threshold(voltage),
                        "protection_enabled": self._checked(
                            controls["voltage_protection_enabled"]
                        ),
                    },
                    "current": {
                        "value": current,
                        "protection": protection_threshold(current),
                        "protection_enabled": self._checked(
                            controls["current_protection_enabled"]
                        ),
                    },
                }
                delay_control = controls.get("power_on_delay")
                if delay_control is not None:
                    channel_config["power_on_delay_s"] = float(
                        delay_control.value()
                    )
                channels[channel] = channel_config
            result[name] = {"channels": channels}
        return {"instruments": {"power_supplies": result}} if result else {}

    def read_power_assignment(self) -> dict:
        """写出两组的通道上电计划（``power_plan``）。

        计划是“哪些电源的哪些通道给哪个器件上电”的唯一来源：这里不再读取按器件选择
        电源的下拉框，也不写 ``supplies`` / ``power_supply_count``。每组的计划读自电源
        页的计划状态；没有计划状态的旧表单沿用配置里已有的计划，不能因为页面尚未接入
        排序状态就把用户写入的顺序丢掉。
        """
        existing = _dict_copy(self.config.get("power_supply_assignment"))
        driver_existing = _dict_copy(existing.get("driver_amplifier"))
        dut_existing = _dict_copy(existing.get("dut_amplifier"))
        driver_editor = getattr(
            self.widgets, "driver_power_plan_editor", None
        )
        dut_editor = getattr(self.widgets, "dut_power_plan_editor", None)
        if driver_editor is None and dut_editor is None:
            # 测试配置页没有计划控件：它不拥有电源段，不能把旧编辑字段顺手改写掉。
            return {}
        driver_plan = self._read_power_plan(
            "driver_power_plan_editor", driver_existing.get("power_plan")
        )
        dut_plan = self._read_power_plan(
            "dut_power_plan_editor", dut_existing.get("power_plan")
        )
        if driver_plan is None and dut_plan is None:
            return {}
        driver = (
            {"power_plan": _dict_copy(driver_plan)}
            if driver_plan is not None
            else {}
        )
        dut = (
            {"power_plan": _dict_copy(dut_plan)}
            if dut_plan is not None
            else {}
        )
        return {"power_supply_assignment": {"driver_amplifier": driver, "dut_amplifier": dut}}

    def _read_power_plan(self, editor_name: str, existing: Any) -> Any:
        """读取一组的计划控件；没有控件时沿用配置里已有的计划。

        没有计划控件的表单（旧替身或尚未接入的页面）不能被悄悄写成空计划；有控件时
        以控件读数为准，包括明确的空计划（“尚未确认顺序”）。
        """
        editor = getattr(self.widgets, editor_name, None)
        reader = getattr(editor, "read_active_plan", None)
        if not callable(reader):
            reader = getattr(editor, "read_plan", None)
        if callable(reader):
            return reader()
        return _dict_copy(existing) if isinstance(existing, Mapping) else None

    def read_drain_channels(self) -> list[dict]:
        """读取漏极集合；单漏极配置也返回只含一条的列表。

        从电源页读取完整的漏极路由集合。
        """
        reader = getattr(self.widgets, "read_drain_channels", None)
        if not callable(reader):
            return []
        routes = reader()
        return [dict(route) for route in routes] if routes else []

    def _drain_channel_document(self) -> Any:
        """配置文档里的漏极形状：未配置写 ``None``，单漏极写对象，多漏极写列表。"""
        routes = self.read_drain_channels()
        if not routes:
            return None
        return routes[0] if len(routes) == 1 else routes

    def read_power_fragment(self) -> dict:
        """只读出电源相关的配置片段（独立电源页提交给统一会话的形状）。

        片段只含本页拥有的字段：``instruments.power_supplies``（通道参数）、
        ``power_supply_assignment``（两组上电计划）和 ``dut_config.drain_channel``
        （单漏极写对象，多漏极写列表）。
        测试参数、设备地址与身份不在这里读取，页面之间因此不需要互相持有控件。
        没有对应控件时该段缺席，而不是写成空段——由调用方决定是否保留基线值。
        """
        fragment: dict[str, Any] = {}
        channels = self.read_power_supply_config()
        if channels:
            fragment.update(channels)
        assignment = self.read_power_assignment()
        if assignment:
            fragment["power_supply_assignment"] = assignment["power_supply_assignment"]
        if callable(getattr(self.widgets, "read_drain_channels", None)):
            fragment["dut_config"] = {
                "drain_channel": self._drain_channel_document(),
            }
        return fragment

    def build_power_config(self, base: Mapping[str, Any] | None = None) -> dict:
        """把当前电源控件读数合并进 ``base``，返回完整编辑文档。

        合并只覆盖电源字段，基线里的测试参数、仪器连接与未知字段原样保留；这与
        :meth:`build_config` 的整份读数互补：独立电源页只提交自己负责的片段，不
        重新发明另一页的字段。
        """
        config = copy.deepcopy(base if base is not None else self.config)
        instruments = _dict_copy(config.get("instruments"))
        supplies = _dict_copy(instruments.get("power_supplies"))
        fragment = self.read_power_supply_config().get("instruments", {}).get(
            "power_supplies", {}
        )
        for name, value in fragment.items():
            target = _dict_copy(supplies.get(name))
            channels = _dict_copy(target.get("channels"))
            channels.update(copy.deepcopy(value.get("channels", {})))
            target["channels"] = channels
            supplies[name] = target
        # 连接状态只来自设备绑定：电源页校验的是“这次绑定的设备能不能按计划上电”，
        # 因此先把每台电源的地址/启用状态按当前绑定刷新一遍，再交给正式校验。绑定写入
        # 与测试配置页共用同一个 `_apply_device_binding`，不另立一套判据。
        bindings = getattr(self.widgets, "device_bindings", None)
        if isinstance(bindings, Mapping):
            records = power_supply_bindings(bindings)
            for name in list(supplies):
                item = _dict_copy(supplies.get(name))
                _apply_device_binding(item, records.get(name))
                supplies[name] = item
        strip_channel_roles(supplies)
        instruments["power_supplies"] = supplies
        config["instruments"] = instruments
        assignment = self.read_power_assignment()
        if assignment:
            config["power_supply_assignment"] = assignment["power_supply_assignment"]
        elif not _dict_copy(config.get("driver_mode")).get("enabled"):
            _clear_driver_power_plan(config)
        if callable(getattr(self.widgets, "read_drain_channels", None)):
            dut = _dict_copy(config.get("dut_config"))
            dut["drain_channel"] = self._drain_channel_document()
            config["dut_config"] = dut
        return config

    def power_validation_errors(self, base: Mapping[str, Any] | None = None) -> list[str]:
        """按电源页当前读数给出正式校验问题（只供独立电源页显示）。

        校验对象仍是应用边界的同一套编写与正式校验：页面只把读数包成编辑文档并
        展示返回的消息，不在这里复制计划/漏极/计划候选规则。这里只保留电源字段的
        问题，测试参数与设备连接的错误由测试配置页报告。
        """
        return _formal_validation_errors(
            self.build_power_config(base), scope="power"
        )

    def build_config(
        self, *, test_parameters: Mapping[str, Any] | None = None
    ) -> dict:
        """合并控件读数与基线，返回完整配置文档。

        ``test_parameters`` 是调用方已经读出的测试参数片段（见
        :meth:`read_test_parameters`）；传入时直接复用，避免为了同一份文档再读一次控件。
        不传时保持原行为，由本方法自己读。
        """
        w = self.widgets
        config = copy.deepcopy(self.config)
        config["instruments"] = self.read_instrument_config()["instruments"]
        config.update(
            self.read_test_parameters()
            if test_parameters is None
            else test_parameters
        )
        dut = _dict_copy(config.get("dut_config"))
        dut["max_input_power"] = self._value(w.max_input_power)
        dut["power_supply_count"] = int(self._current(w.pa_unit_count_combo))
        if callable(getattr(w, "read_drain_channels", None)):
            dut["drain_channel"] = self._drain_channel_document()
        config["dut_config"] = dut
        fragment = self.read_power_supply_config()
        for name, value in fragment.get("instruments", {}).get("power_supplies", {}).items():
            target = _dict_copy(config["instruments"]["power_supplies"].get(name))
            channels = _dict_copy(target.get("channels"))
            channels.update(copy.deepcopy(value.get("channels", {})))
            target["channels"] = channels
            config["instruments"]["power_supplies"][name] = target
        # 1.1 正式文档不再包含通道 role：电源表单已拆到独立页，测试配置页仍不能把基线里
        # 的旧值原样写回。整组清理一次，不依赖本页是否存在电源控件。
        strip_channel_roles(config["instruments"]["power_supplies"])
        assignment = self.read_power_assignment()
        if assignment:
            config["power_supply_assignment"] = assignment["power_supply_assignment"]
        elif not _dict_copy(config.get("driver_mode")).get("enabled"):
            _clear_driver_power_plan(config)
        # 现场接线不再属于配置：接线由各阶段开始前的连接图对话框确认，旧文件里的
        # ``wiring`` 块不随表单继续写回，避免留下永远为空的“已确认”字段。
        config.pop("wiring", None)
        return config
