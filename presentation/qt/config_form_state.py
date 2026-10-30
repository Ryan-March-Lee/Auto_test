"""纯 Python 配置表单状态适配器。

该模块只依赖控件的最小读取协议（``text``、``value``、``isChecked`` 和 ``currentText``；
电源分配下拉框另外要求 ``currentIndex`` + ``itemData``，业务值只从那里读），以及页面
持有的 ``device_bindings``（仪器连接的唯一来源），因此可以在没有 QApplication 的情况下
测试配置组装。

职责边界：本模块读表单控件、把读数合并成配置片段，并给出页面自己的校验说明。设备
绑定记录本身的读取与规范化（``supply_id`` 集合、旧结构折叠、不可用记录的问题清单）
在 :mod:`presentation.qt.config_form_bindings`，这里只消费它给出的结果。
"""

import ast
import copy
import math
from typing import Any, Mapping

# 供电分配引用（``supply_id`` 为准、旧记录回落读一次 ``name``）只有一处实现：
# ``domain.configuration.rules.assignment_supply_id``，这里再导出给配置页与投影使用，
# 判据不在这层重写：``supply_id`` 为准、旧记录回落读一次 ``name`` 的规则只有一个实现。
from application.errors import ConfigurationError
from domain.configuration.models import validate_run_configuration
from domain.configuration.rules import assignment_supply_id
from domain.instruments.models import INSTRUMENT_ROLES

from .config_form_bindings import binding_address, power_supply_bindings


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


def _assignment_entry(combo: Any) -> dict:
    """从分配下拉框读出一条供电分配记录。

    引用值取自控件的 ``itemData``（即选中项的 ``supply_id``）：下拉框的显示文本是
    ``DP8B1519（DP832A）`` 这类给人看的标签，业务值绝不能从显示字符串反解析。控件为空
    或选中项没有数据时按“引用未绑定”处理，不会把显示文本当成 ``supply_id``。

    写回时**主字段是 ``supply_id``**；过渡期同时写同名 ``name``，让尚未迁移到
    ``supply_id`` 的读取方（正式运行映射、硬件装配）仍能工作。两者取值必须相同——
    不存在第二套编号。

    控件为空表示“引用未绑定”：写成 ``supply_id: null`` 且不写 ``name``，由校验明确
    报错，绝不在这里挑一台可用电源补上。
    """
    supply_id = _combo_supply_id(combo)
    entry: dict = {"supply_id": supply_id, "channel": ["CH1", "CH2"]}
    if supply_id:
        entry["name"] = supply_id
    return entry


def _combo_supply_id(combo: Any) -> str | None:
    """下拉框当前选中的电源 ``supply_id``；空选择或选中项没有数据时返回 ``None``。

    只读 ``itemData``：显示文本带型号（``DP8B1519（DP832A）``），也可能因型号相同而
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
        errors = []
        if parse_frequency_text(self._text(self.widgets.freq_edit)) is None:
            errors.append("测试频率必须是非空数字列表")
        errors.extend(self._power_role_errors())
        errors.extend(self._power_assignment_errors())
        errors.extend(self._formal_run_errors())
        return errors

    def _formal_run_errors(self) -> list[str]:
        """按当前控件组装出的正式配置必须能通过正式运行校验。

        界面自己的检查只看“选了没有、绑定了没有”；真正决定能否连接仪器的是正式
        ``test_plan`` / ``run_mapping``（多电源的地址重复、同一物理通道重复、供电角色
        冲突等规则都在那里，见步骤 11）。这里用同一套配置作者与正式校验，让这些失败在
        点击“开始”之前、并出现在创建运行的那一条提示里。

        校验对象是页面此刻的读数（:meth:`build_config`）：协调器持有的配置副本可能还带着
        页面已经放弃的旧段（例如关闭驱放前保存的驱动分配），按它报错会让刚做对的界面无法
        开始。绑定写出的地址也在这份读数里，所以“两台电源抢同一个地址”同样在这里被拦住。

        只把 :class:`ConfigurationError` 转成页面错误：那是配置作者对当前读数的判断。
        其它异常是程序缺陷，不能被改写成“配置有问题”的提示而掩盖掉，让它照常抛给上层。
        """
        # 延迟导入：``configuration_authoring`` 反向依赖本模块的 ``assignment_supply_id``，
        # 在模块级互相导入会形成环。
        from domain.configuration.models import RunConfiguration, RunResourceMapping, TestPlan
        from domain.configuration.rules import validate_hardware_connections
        from presentation.qt.configuration_authoring import author_official_configuration

        errors: list[str] = []
        try:
            document = author_official_configuration(self.build_config())
        except ConfigurationError as failure:
            # 配置作者对“现在的配置写不成正式映射”给出的判断（未绑定的分配、缺少身份字段
            # 等），是用户要改的内容，转成页面错误。
            return [f"无法按当前配置生成正式运行映射: {failure}"]
        result = validate_run_configuration(
            RunConfiguration(
                TestPlan.from_dict(document.get("test_plan") or {}),
                RunResourceMapping.from_dict(document.get("run_mapping") or {}),
            )
        )
        errors = [issue.message for issue in result.errors]
        # 正式段之外还有硬件装配段（``instruments`` / ``power_supply_assignment``）：
        # 两台电源抢同一个地址、同一物理通道同时供 DUT 与驱动功放都只在这一段可见。
        errors += [issue.message for issue in validate_hardware_connections(document).errors]
        return errors

    def _power_assignment_errors(self) -> list[str]:
        """电源分配只能引用已应用设备的电源；未绑定的引用必须显式失败。

        电源是否可用不再看界面上的“启用”勾选框，而看设备绑定——与保存出的
        ``instruments.power_supplies.<name>.enabled`` 完全同源。因此这里能提前拦住
        “分配指向一台已经不存在的电源”的配置，而不是等保存/连接时才失败。
        """
        controls = getattr(self.widgets, "power_assignment_widgets", None)
        if not controls:
            return []
        bindings = getattr(self.widgets, "device_bindings", None)
        records = power_supply_bindings(bindings)
        fragment = self.read_power_assignment()
        assignment = fragment.get("power_supply_assignment")
        assignment = assignment if isinstance(assignment, Mapping) else {}
        unselected: list[str] = []
        unbound: list[str] = []
        for group, label in (
            ("driver_amplifier", "驱动功放"),
            ("dut_amplifier", "DUT"),
        ):
            group_entry = assignment.get(group)
            group_entry = group_entry if isinstance(group_entry, Mapping) else {}
            supplies = group_entry.get("supplies")
            supplies = supplies if isinstance(supplies, Mapping) else {}
            for role, item in supplies.items():
                supply_id = assignment_supply_id(item)
                if not supply_id:
                    unselected.append(f"{label}({role})")
                elif not binding_address(records.get(supply_id)):
                    unbound.append(f"{label}({role}) → {supply_id}")
        errors = []
        if unselected:
            errors.append(
                f"{'、'.join(unselected)} 未选择电源：请先在“仪表配置”页应用电源设备"
            )
        if unbound:
            errors.append(
                f"{'、'.join(unbound)} 引用的电源没有已应用的设备，不能保存或运行"
            )
        return errors

    def _power_role_errors(self) -> list[str]:
        """供电角色必须是显式选择且互不重复，避免保存连接时才失败的配置。"""
        errors = []
        fragment = self.read_power_supply_config()
        supplies = fragment.get("instruments", {}).get("power_supplies", {})
        for name, supply in supplies.items():
            roles = [channel.get("role") for channel in supply.get("channels", {}).values()]
            if any(role is None for role in roles):
                errors.append(f"{name} 存在未选择供电角色的通道")
            duplicates = sorted(
                {role for role in roles if role is not None and roles.count(role) > 1}
            )
            if duplicates:
                errors.append(f"{name} 的通道供电角色重复: {'、'.join(duplicates)}")
        return errors

    def read_instrument_config(self) -> dict:
        """按设备发现结果写出仪器连接信息。

        连接配置的唯一来源是 :attr:`widgets.device_bindings`：信号源与频谱仪各一条
        （角色键），电源是 ``power_supplies`` 里按 ``supply_id`` 的多条。绑定了设备的
        项写入该设备的当前地址并标记启用；没有绑定的项一律清空地址并标记为未启用，
        不再沿用配置里遗留的地址——否则“清除绑定”只是隐藏了入口，旧设备仍会被连接。

        旧槽位键（``PS1``..``PS4``）同样是“未绑定”的配置项：通道参数原样保留，地址与
        启用状态随绑定一起失效，等迁移器把参数改挂到真正的 ``supply_id`` 上。

        身份字段（``device_key`` / ``serial`` / ``model``）只认绑定记录：记录里没有的
        字段不能沿用配置里的旧值，否则会出现“地址来自这台、身份还是上一台”的条目。
        """
        instruments = _dict_copy(self.config.get("instruments"))
        supplies = _dict_copy(instruments.get("power_supplies"))
        bindings = getattr(self.widgets, "device_bindings", None)
        bindings = bindings if isinstance(bindings, Mapping) else {}
        power_records = power_supply_bindings(bindings)
        for name in list(supplies):
            item = _dict_copy(supplies.get(name))
            _apply_device_binding(item, power_records.get(name))
            supplies[name] = item
        for supply_id, record in power_records.items():
            item = _dict_copy(supplies.get(supply_id))
            _apply_device_binding(item, record)
            # 集合的键就是引用 ID，条目里保留同名 ``label`` 便于人读与旧读取方兼容；
            # 身份字段只认绑定记录——记录里没有的字段要把配置里的旧值删掉，否则会出现
            # “地址来自这台、身份还是上一台”的条目（绑定是连接身份的唯一权威来源）。
            item["label"] = supply_id
            # 身份字段以绑定记录为准，但记录里**没有**某字段（发现结果不总是带型号）时
            # 不能把配置里同一台的型号删掉：那是这台电源自己的既有事实，不是上一台的
            # 残留。只有记录里出现的字段才覆盖旧值，保证“身份始终来自当前绑定”。
            for field in ("device_key", "serial", "model"):
                if field not in record:
                    continue
                value = record.get(field)
                if isinstance(value, str) and value.strip():
                    item[field] = value
                else:
                    item.pop(field, None)
            supplies[supply_id] = item
        for name in INSTRUMENT_ROLES:
            item = _dict_copy(instruments.get(name))
            _apply_device_binding(item, bindings.get(name))
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
        label = self._current(source_widget) if source_widget is not None else "直流电源"
        return {
            "enabled": True,
            "power_source": {"自带供电": "external_ac"}.get(label, "dc_supply"),
        }

    def read_power_supply_config(self) -> dict:
        result = {}
        configured = (
            self.config.get("instruments", {})
            if isinstance(self.config.get("instruments"), Mapping)
            else {}
        )
        configured_supplies = (
            configured.get("power_supplies", {})
            if isinstance(configured.get("power_supplies"), Mapping)
            else {}
        )
        for name, supply in getattr(self.widgets, "power_config_widgets", {}).items():
            existing_channels = _dict_copy(
                _dict_copy(configured_supplies.get(name)).get("channels")
            )
            channels = {}
            for channel, controls in supply.get("channels", {}).items():
                channels[channel] = {
                    "role": self._channel_role(controls, existing_channels, channel),
                    "voltage": {"value": self._value(controls["voltage"]), "protection": self._value(controls["voltage_protection"]), "protection_enabled": self._checked(controls["voltage_protection_enabled"])},
                    "current": {"value": self._value(controls["current"]), "protection": self._value(controls["current_protection"]), "protection_enabled": self._checked(controls["current_protection_enabled"])},
                }
            result[name] = {"channels": channels}
        return {"instruments": {"power_supplies": result}} if result else {}

    @staticmethod
    def _channel_role(controls: Mapping[str, Any], existing_channels: Mapping[str, Any], channel: str) -> str | None:
        """读控件里用户显式选择的供电角色。

        角色是接线事实，不按通道名推断：控件缺失或值不可识别时只保留配置里
        已有的合法角色，其余一律返回 ``None``，由正式校验在连接前报错。
        """
        role_widget = controls.get("role")
        if role_widget is not None:
            label = role_widget.currentText()
            return label if label in ("gate", "drain") else None
        existing = _dict_copy(existing_channels.get(channel)).get("role")
        return existing if existing in ("gate", "drain") else None

    def read_power_assignment(self) -> dict:
        """按当前下拉框写出供电分配，引用主字段是 ``supply_id``。

        值只来自控件的选中项（``itemData`` 里的 ``supply_id``，见 :func:`_combo_supply_id`），
        不从显示字符串反解析；过渡期同时写同名 ``name``（见 :func:`_assignment_entry`）。
        可选项本身只来自设备绑定，因此这里写出的每个引用都对应一台本次已应用的电源。
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
        driver = {"power_supply_count": 1 if driver_enabled else 0, "supplies": {}}
        if driver_enabled:
            driver["supplies"]["main"] = _assignment_entry(controls.get("driver_power"))
        count = int(self._current(w.pa_unit_count_combo))
        roles = (("carrier", "pa_unit1_power"), ("peaking", "pa_unit2_power"), ("peaking2", "pa_unit3_power"))
        supplies = {}
        for index, (role, control) in enumerate(roles, 1):
            if index <= count:
                supplies[role] = _assignment_entry(controls.get(control))
        return {"power_supply_assignment": {"driver_amplifier": driver, "dut_amplifier": {"power_supply_count": count, "supplies": supplies}}}

    def build_config(self) -> dict:
        w = self.widgets
        config = copy.deepcopy(self.config)
        config["instruments"] = self.read_instrument_config()["instruments"]
        config.update(self.read_test_parameters())
        dut = _dict_copy(config.get("dut_config"))
        dut["max_input_power"] = self._value(w.max_input_power)
        dut["power_supply_count"] = int(self._current(w.pa_unit_count_combo))
        config["dut_config"] = dut
        fragment = self.read_power_supply_config()
        for name, value in fragment.get("instruments", {}).get("power_supplies", {}).items():
            target = _dict_copy(config["instruments"]["power_supplies"].get(name))
            channels = _dict_copy(target.get("channels"))
            channels.update(copy.deepcopy(value.get("channels", {})))
            target["channels"] = channels
            config["instruments"]["power_supplies"][name] = target
        assignment = self.read_power_assignment()
        if assignment:
            config["power_supply_assignment"] = assignment["power_supply_assignment"]
        elif not _dict_copy(config.get("driver_mode")).get("enabled"):
            _clear_driver_power_assignment(config)
        # 现场接线不再属于配置：接线由各阶段开始前的连接图对话框确认，旧文件里的
        # ``wiring`` 块不随表单继续写回，避免留下永远为空的“已确认”字段。
        config.pop("wiring", None)
        return config
