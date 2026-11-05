"""纯 Python 配置表单状态适配器。

该模块只依赖控件的最小读取协议（``text``、``value``、``isChecked`` 和 ``currentText``；
电源分配下拉框另外要求 ``currentIndex`` + ``itemData``，业务值只从那里读），以及页面
持有的 ``device_bindings``（仪器连接的唯一来源），因此可以在没有 QApplication 的情况下
测试配置组装。

职责边界：本模块读表单控件、把读数合并成配置片段，并给出页面自己的校验说明。设备
绑定记录本身的读取与规范化（``supply_id`` 集合、旧结构折叠、不可用记录的问题清单）
在 :mod:`presentation.qt.config_form_bindings`，这里只消费它给出的结果。

归属（见 `docs/配置领域词汇与模块归属.md`）：presentation adapter。读数后的合法性判据属于
`domain` 配置规则；正式运行校验与正式配置编写都委托
`application.configuration_authoring`，本模块只把当前的读数包成配置编辑 DTO 并展示
返回的消息。供电分配引用的判据也只由 domain 定义（步骤 13 收口），这里不再保留页面副本。
"""

import ast
import copy
import math
from typing import Any, Mapping

from application.errors import ConfigurationError
from domain.instruments.models import INSTRUMENT_ROLES

from .config_form_bindings import binding_address, power_supply_bindings
# 电源通道的显示顺序：界面固定为每台电源两个通道。组计划与分配写出都按这个顺序，
# 但顺序本身不是上电顺序——上电顺序只来自计划列表。
from .config_power_assignments import POWER_SUPPLY_CHANNELS, assignment_channels


def parse_frequency_text(value: str) -> list[float] | None:
    """安全解析频率列表；非法输入返回 ``None``，不会执行任意代码。"""
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError, TypeError):
        return None
    if not isinstance(parsed, (list, tuple)) or not parsed:
        return None
    result = []
    for item in parsed:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return None
        number = float(item)
        if not math.isfinite(number):
            return None
        result.append(number)
    return result


def _dict_copy(value: Any) -> dict:
    return copy.deepcopy(value) if isinstance(value, dict) else {}


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

    电源参数已拆到独立页，但参数页组装文档时仍会带上基线里的旧字段；两条写入路径
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


def assignment_declared(entry: Any) -> bool:
    """配置里是否存在这条供电分配（与它是否指向一台已绑定电源无关）。

    回填控件必须分开两种状态：**没有这条分配**（键缺失/不是对象）按缺省规则选第一台
    可用电源；**声明了但未绑定**（``supply_id: null``、空白引用，或记录里根本没有引用
    字段）必须保持空选择并交给校验报告。把后者当成前者，会在载入一份“等人工补全”的
    配置时静默选上一台电源，保存后就把“这台电源已不在”写成了一次看不见的分配改动。
    """
    return isinstance(entry, Mapping)


def _clear_driver_power_assignment(document):
    """关闭驱放测试模式时清掉文档里遗留的驱动功放供电分配。

    页面在关闭驱放后不再提交驱动分配（不占本机电源），但协调器持有的配置副本仍带着上一次
    保存的 ``driver_amplifier.supplies``；两份副本因此不一致。正式校验会按“驱放模式关闭时
    不能配置驱动功放通道”拒绝它，于是页面刚做的正确选择反而让运行创建失败（步骤 11）。
    这里只清这一段供电分配，电源通道参数与 DUT 分配原样保留。
    """
    assignment = _dict_copy(document.get("power_supply_assignment"))
    if not assignment or assignment.get("driver_amplifier") in (None, {}):
        return False
    assignment["driver_amplifier"] = {"power_supply_count": 0, "supplies": {}}
    document["power_supply_assignment"] = assignment
    return True


def _assignment_entry(combo: Any, channels: Any = None) -> dict:
    """从分配下拉框读出一条供电分配记录。

    引用值取自控件的 ``itemData``（即选中项的 ``supply_id``）：下拉框的显示文本是
    ``DP832A（DP8B1519）`` 这类给人看的标签，业务值绝不能从显示字符串反解析。控件为空
    或选中项没有数据时按“引用未绑定”处理，不会把显示文本当成 ``supply_id``。

    ``channels`` 是配置里这条分配声明的通道集合（:func:`assignment_channels`）；它表达
    “这个 PA 单元接哪些物理通道”，**不**由计划勾选或排序推导。计划只写
    ``power_plan.power_sequence``；两处集合是否相等由正式校验比较。缺少通道声明时保持
    每台电源固定的两个通道。

    写回只写 ``supply_id``：1.1 的唯一引用字段是它；旧 ``name`` 只属于迁移/读取兼容，
    不再由表单写回。控件为空表示“引用未绑定”，写成 ``supply_id: null``，由校验明确
    报错，绝不在这里挑一台可用电源补上。
    """
    supply_id = _combo_supply_id(combo)
    selected = list(POWER_SUPPLY_CHANNELS) if channels is None else list(channels)
    return {"supply_id": supply_id, "channel": selected}


def _combo_supply_id(combo: Any) -> str | None:
    """下拉框当前选中的电源 ``supply_id``；空选择或选中项没有数据时返回 ``None``。

    只读 ``itemData``：显示文本带型号（``DP832A（DP8B1519）``），也可能因型号相同而
    只是互相接近，回落到文本就等于“从显示字符串反解析身份”，会把标签原样写成
    ``supply_id``。没有 ``itemData`` 能力的控件因此被视为空选择，由校验明确报错。
    """
    if combo is None:
        return None
    index_of = getattr(combo, "currentIndex", None)
    index = index_of() if callable(index_of) else -1
    if not isinstance(index, int) or index < 0:
        return None
    item_data = getattr(combo, "itemData", None)
    data = item_data(index) if callable(item_data) else None
    if isinstance(data, str) and data.strip():
        return data
    return None


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
        """参数页要显示的用户错误：只报本页负责的字段。

        整份配置的正式校验按页面归属拆开：本页只展示测试参数、设备连接等非电源问题；
        供电分配引用、上电计划、通道与漏极的问题由独立电源页展示。两页合起来仍是
        完整校验，因此最终动作不需要第二份校验入口，也不会把电源故障写进本页。
        """
        errors = []
        if parse_frequency_text(self._text(self.widgets.freq_edit)) is None:
            errors.append("测试频率必须是非空数字列表")
        errors.extend(
            _formal_validation_errors(self.build_config(), scope="parameters")
        )
        return errors

    def formal_validation_errors(self) -> list[str]:
        """按整张表单做正式校验（含设备连接与电源段）。

        参数页不再调用它；保留为共享实现，供电源页与单独校验表单状态的测试使用。
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
        frequencies = parse_frequency_text(self._text(w.freq_edit))
        if frequencies is not None:
            result["test_frequencies"] = frequencies
        result.update({
            "signal_source": {
                "start_power": self._value(w.start_power),
                "stop_power": self._value(w.stop_power),
                "step": self._value(w.power_step),
            },
            "compression_point": {"type": self._current(w.compression_combo)},
            "attenuator": {"type": self._current(w.attenuator_combo)},
            "driver_mode": self._read_driver_mode(w),
        })
        return result

    def _read_driver_mode(self, w: Any) -> dict:
        """读取驱放测试模式与供电方式。

        供电方式只在驱放测试模式下有意义：关闭时写回不带 ``power_source`` 的
        ``{"enabled": False}``，避免保存出“关闭却又声明供电方式”的配置。
        """
        enabled = bool(getattr(w, "test_mode_enabled", False))
        if not enabled:
            return {"enabled": False}
        source_widget = getattr(w, "driver_power_source_combo", None)
        if source_widget is not None:
            label = self._current(source_widget)
            return {
                "enabled": True,
                "power_source": {"自带供电": "external_ac"}.get(label, "dc_supply"),
            }
        # 电源表单已拆到独立页面：参数页没有供电方式控件，不能把已保存的
        # ``external_ac`` 默认改回 ``dc_supply``；来源只由回填基线保留。
        existing = _dict_copy(self.config.get("driver_mode")).get("power_source")
        return {
            "enabled": True,
            "power_source": (
                existing if existing in ("dc_supply", "external_ac") else "dc_supply"
            ),
        }

    def read_power_supply_config(self) -> dict:
        result = {}
        for name, supply in getattr(self.widgets, "power_config_widgets", {}).items():
            channels = {}
            for channel, controls in supply.get("channels", {}).items():
                # 1.1 正式配置不再写通道 role；电源表单也只保存工作值与保护值。
                channels[channel] = {
                    "voltage": {"value": self._value(controls["voltage"]), "protection": self._value(controls["voltage_protection"]), "protection_enabled": self._checked(controls["voltage_protection_enabled"])},
                    "current": {"value": self._value(controls["current"]), "protection": self._value(controls["current_protection"]), "protection_enabled": self._checked(controls["current_protection_enabled"])},
                }
            result[name] = {"channels": channels}
        return {"instruments": {"power_supplies": result}} if result else {}

    def read_power_assignment(self) -> dict:
        """按当前下拉框、分配通道与组计划写出供电分配，引用主字段是 ``supply_id``。

        值只来自控件的选中项（``itemData`` 里的 ``supply_id``，见 :func:`_combo_supply_id`），
        不从显示字符串反解析；写出只含 ``supply_id``（1.1 不再写过渡别名 ``name``）。
        可选项本身只来自设备绑定，因此这里写出的每个引用都对应一台本次已应用的电源。

        分配的 ``channel`` 是配置里该槽位已有的通道集合（:func:`assignment_channels`），
        与计划勾选、排序互不推导：计划只写 ``power_plan``，集合是否与序列相等交给正式
        校验。这样同一台电源供两个 PA 单元时，两个槽位各自保留自己的通道，不会因为
        计划里勾了某台电源的全部通道而互相复制。

        每组的 ``power_plan`` 读自计划控件；没有计划控件的旧表单沿用配置里已有的计划，
        不能因为页面尚未接入排序控件就把用户写入的顺序丢掉。
        """
        w = self.widgets
        controls = getattr(w, "power_assignment_widgets", None)
        if not controls:
            return {}
        source_control = controls.get("driver_source")
        if source_control is not None:
            source = self._current(source_control)
        else:
            legacy_control = controls.get("driver_enabled")
            source = "直流电源" if legacy_control is not None and self._checked(legacy_control) else "自带供电"
        # 无驱放测试不写驱动功放供电分配；有驱放但驱放自带供电时同样不占本机电源。
        driver_enabled = source == "直流电源" and bool(getattr(w, "test_mode_enabled", True))
        # PA 单元数量属于测试参数：完整配置页有下拉框，独立电源页只接收页面级派生值
        # （``pa_unit_count``）。两者都只是“要写几条 DUT 分配”的输入，不在这里推断。
        count_widget = getattr(w, "pa_unit_count_combo", None)
        count = (
            int(self._current(count_widget))
            if count_widget is not None
            else max(0, int(getattr(w, "pa_unit_count", 0)))
        )
        existing = _dict_copy(self.config.get("power_supply_assignment"))
        driver_existing = _dict_copy(existing.get("driver_amplifier"))
        dut_existing = _dict_copy(existing.get("dut_amplifier"))
        driver_plan = self._read_power_plan(
            "driver_power_plan_editor", driver_existing.get("power_plan")
        )
        dut_plan = self._read_power_plan(
            "dut_power_plan_editor", dut_existing.get("power_plan")
        )
        driver = {"power_supply_count": 1 if driver_enabled else 0, "supplies": {}}
        if driver_enabled:
            driver["supplies"]["main"] = _assignment_entry(
                controls.get("driver_power"),
                assignment_channels(
                    _dict_copy(driver_existing.get("supplies")).get("main")
                ),
            )
        roles = (("carrier", "pa_unit1_power"), ("peaking", "pa_unit2_power"), ("peaking2", "pa_unit3_power"))
        supplies = {}
        existing_supplies = _dict_copy(dut_existing.get("supplies"))
        for index, (role, control) in enumerate(roles, 1):
            if index > count:
                continue
            supplies[role] = _assignment_entry(
                controls.get(control),
                assignment_channels(existing_supplies.get(role)),
            )
        if driver_plan is not None:
            driver["power_plan"] = _dict_copy(driver_plan)
        dut = {"power_supply_count": count, "supplies": supplies}
        if dut_plan is not None:
            dut["power_plan"] = _dict_copy(dut_plan)
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

    def read_drain_channel(self) -> dict | None:
        """读取漏极选择；未选择或控件缺失时返回 ``None``。

        漏极是独立的测量路由，不参与分配与上电；这里只从控件的 ``itemData`` 读两段
        路由，不把显示文本或通道名当成路由身份。
        """
        combo = getattr(self.widgets, "drain_channel_combo", None)
        if combo is None:
            return None
        index_of = getattr(combo, "currentIndex", None)
        index = index_of() if callable(index_of) else -1
        item_data = getattr(combo, "itemData", None)
        data = item_data(index) if callable(item_data) and isinstance(index, int) and index >= 0 else None
        if isinstance(data, Mapping):
            supply_id, channel = data.get("supply_id"), data.get("channel")
        elif isinstance(data, (list, tuple)) and len(data) == 2:
            supply_id, channel = data
        else:
            return None
        if not isinstance(supply_id, str) or not isinstance(channel, str):
            return None
        supply_id, channel = supply_id.strip(), channel.strip()
        if not supply_id or not channel:
            return None
        return {"supply_id": supply_id, "channel": channel}

    def read_power_fragment(self) -> dict:
        """只读出电源相关的配置片段（独立电源页提交给统一会话的形状）。

        片段只含本页拥有的字段：``instruments.power_supplies``（通道参数）、
        ``power_supply_assignment``（分配与组计划）和 ``dut_config.drain_channel``。
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
        if getattr(self.widgets, "driver_power_source_combo", None) is not None:
            # 驱放供电方式是电源分配的一部分，但落在测试参数段 ``driver_mode`` 里。
            # 片段只带 ``power_source``，``enabled`` 仍由参数页决定；合并方按字段合并，
            # 不能用电源片段整段覆盖 ``driver_mode``。
            source = self._read_driver_mode(self.widgets).get("power_source")
            if source is not None:
                fragment["driver_mode"] = {"power_source": source}
        if getattr(self.widgets, "drain_channel_combo", None) is not None:
            fragment["dut_config"] = {
                "drain_channel": self.read_drain_channel(),
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
        strip_channel_roles(supplies)
        instruments["power_supplies"] = supplies
        config["instruments"] = instruments
        assignment = self.read_power_assignment()
        if assignment:
            config["power_supply_assignment"] = assignment["power_supply_assignment"]
        elif not _dict_copy(config.get("driver_mode")).get("enabled"):
            _clear_driver_power_assignment(config)
        if getattr(self.widgets, "driver_power_source_combo", None) is not None:
            # 供电方式是否生效以页面当前的测试模式为准，而不是基线里的旧 ``enabled``。
            read_mode = self._read_driver_mode(self.widgets)
            if read_mode.get("enabled"):
                mode = _dict_copy(config.get("driver_mode"))
                mode["power_source"] = read_mode.get("power_source")
                config["driver_mode"] = mode
        if getattr(self.widgets, "drain_channel_combo", None) is not None:
            dut = _dict_copy(config.get("dut_config"))
            dut["drain_channel"] = self.read_drain_channel()
            config["dut_config"] = dut
        return config

    def power_validation_errors(self, base: Mapping[str, Any] | None = None) -> list[str]:
        """按电源页当前读数给出正式校验问题（只供独立电源页显示）。

        校验对象仍是应用边界的同一套编写与正式校验：页面只把读数包成编辑文档并
        展示返回的消息，不在这里复制计划/漏极/分配引用规则。这里只保留电源字段的
        问题，测试参数与设备连接的错误由参数页报告。
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
        if getattr(w, "drain_channel_combo", None) is not None:
            dut["drain_channel"] = self.read_drain_channel()
        config["dut_config"] = dut
        fragment = self.read_power_supply_config()
        for name, value in fragment.get("instruments", {}).get("power_supplies", {}).items():
            target = _dict_copy(config["instruments"]["power_supplies"].get(name))
            channels = _dict_copy(target.get("channels"))
            channels.update(copy.deepcopy(value.get("channels", {})))
            target["channels"] = channels
            config["instruments"]["power_supplies"][name] = target
        # 1.1 正式文档不再包含通道 role：电源表单已拆到独立页，参数页仍不能把基线里
        # 的旧值原样写回。整组清理一次，不依赖本页是否存在电源控件。
        strip_channel_roles(config["instruments"]["power_supplies"])
        assignment = self.read_power_assignment()
        if assignment:
            config["power_supply_assignment"] = assignment["power_supply_assignment"]
        elif not _dict_copy(config.get("driver_mode")).get("enabled"):
            _clear_driver_power_assignment(config)
        # 现场接线不再属于配置：接线由各阶段开始前的连接图对话框确认，旧文件里的
        # ``wiring`` 块不随表单继续写回，避免留下永远为空的“已确认”字段。
        config.pop("wiring", None)
        return config
