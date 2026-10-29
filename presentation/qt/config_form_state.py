"""纯 Python 配置表单状态适配器。

该模块只依赖控件的最小读取协议（``text``、``value``、``isChecked`` 和
``currentText``），以及页面持有的 ``device_bindings``（仪器连接的唯一来源），
因此可以在没有 QApplication 的情况下测试配置组装。
"""

import ast
import copy
import math
from typing import Any, Mapping

# 仪器连接只由设备发现结果决定：角色由设备表（instruments.json）登记，界面不再让用户
# 选择；地址来自该设备本次发现的 VISA 地址。下表是配置文档里的角色键，不是界面控件名。
INSTRUMENT_ROLES = ("signal_generator", "spectrum_analyzer")
POWER_SUPPLY_NAMES = ("PS1", "PS2", "PS3", "PS4")
# 旧配置只有 ``instruments.<角色>.address``、没有绑定记录。迁移时用这个来源标记，
# 表示“地址来自已保存的配置，尚未经过设备发现确认”，与 ``scan`` / ``simulated`` 分开。
LEGACY_BINDING_SOURCE = "legacy_config"


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


def binding_address(binding: Any) -> str:
    """返回绑定记录里的连接地址；记录缺失或地址不是文本时返回空串。"""
    if not isinstance(binding, Mapping):
        return ""
    address = binding.get("visa_address")
    return address.strip() if isinstance(address, str) else ""


def _binding_records(bindings: Any) -> dict[str, dict]:
    if not isinstance(bindings, Mapping):
        return {}
    return {
        str(role): copy.deepcopy(dict(record))
        for role, record in bindings.items()
        if isinstance(record, Mapping)
    }


def _legacy_device_bindings(instruments: Mapping[str, Any]) -> dict[str, dict]:
    """把只有地址的旧配置迁移成绑定记录。

    只迁移“启用且地址非空”的仪器：禁用项不会被迁移动作重新启用。迁移记录没有序列号
    与 ``device_key``，因此下一次扫描不会按身份预选，面板会把它显示为“沿用已保存的
    地址，未扫描确认”。
    """
    supplies = instruments.get("power_supplies")
    supplies = supplies if isinstance(supplies, Mapping) else {}
    entries = [(role, instruments.get(role)) for role in INSTRUMENT_ROLES]
    entries.extend(supplies.items())
    bindings: dict[str, dict] = {}
    for role, item in entries:
        if not isinstance(item, Mapping):
            continue
        address = item.get("address")
        if not isinstance(address, str) or not address.strip():
            continue
        if item.get("enabled") is False:
            continue
        bindings[str(role)] = {
            "name": None,
            "vendor": None,
            "model": item.get("model"),
            "serial": None,
            "device_key": None,
            "visa_address": address.strip(),
            "last_ip": None,
            "last_port": None,
            "known": False,
            "source": LEGACY_BINDING_SOURCE,
        }
    return bindings


def effective_device_bindings(config: Any) -> dict[str, dict]:
    """返回一份配置里的有效设备绑定，作为仪器连接的唯一来源。

    配置带 ``device_bindings`` 时以它为准（即使为空——用户清除过绑定）；缺少该键的旧
    配置才按 ``instruments.<角色>.address`` 做一次性迁移。迁移只发生在这里，之后保存
    的配置都带显式绑定记录，不会因为“地址还在”而重新变回可连接状态。
    """
    if not isinstance(config, Mapping):
        return {}
    bindings = config.get("device_bindings")
    if isinstance(bindings, Mapping):
        return _binding_records(bindings)
    instruments = config.get("instruments")
    instruments = instruments if isinstance(instruments, Mapping) else {}
    return _legacy_device_bindings(instruments)


def bound_power_supplies(bindings: Any) -> list[str]:
    """返回已绑定设备的电源名称，按 PS1..PS4 的稳定顺序。"""
    records = _binding_records(bindings)
    return [name for name in POWER_SUPPLY_NAMES if binding_address(records.get(name))]


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
        bindings = bindings if isinstance(bindings, Mapping) else {}
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
                name = item.get("name") if isinstance(item, Mapping) else ""
                name = name.strip() if isinstance(name, str) else ""
                if not name:
                    unselected.append(f"{label}({role})")
                elif not binding_address(bindings.get(name)):
                    unbound.append(f"{label}({role}) → {name}")
        errors = []
        if unselected:
            errors.append(
                f"{'、'.join(unselected)} 未选择电源：请先在“仪器连接配置”中应用电源设备"
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

        连接配置的唯一来源是 :attr:`widgets.device_bindings`（角色 -> 已应用设备）：
        绑定了设备的角色写入该设备的当前地址并标记启用；没有绑定的角色一律清空地址
        并标记为未启用，不再沿用配置里遗留的地址——否则“清除绑定”只是隐藏了入口，
        旧设备仍会被连接。
        """
        instruments = _dict_copy(self.config.get("instruments"))
        supplies = _dict_copy(instruments.get("power_supplies"))
        bindings = getattr(self.widgets, "device_bindings", None)
        bindings = bindings if isinstance(bindings, Mapping) else {}
        for name in POWER_SUPPLY_NAMES:
            item = _dict_copy(supplies.get(name))
            _apply_device_binding(item, bindings.get(name))
            supplies[name] = item
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
            driver["supplies"]["main"] = {"name": self._current(controls["driver_power"]), "channel": ["CH1", "CH2"]}
        count = int(self._current(w.pa_unit_count_combo))
        roles = (("carrier", "pa_unit1_power"), ("peaking", "pa_unit2_power"), ("peaking2", "pa_unit3_power"))
        supplies = {}
        for index, (role, control) in enumerate(roles, 1):
            if index <= count:
                supplies[role] = {"name": self._current(controls[control]), "channel": ["CH1", "CH2"]}
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
        config["wiring"] = {"confirmed": False, "connection_note": None, "confirmed_at": None, "confirmation_source": None}
        return config
