"""纯 Python 配置表单状态适配器。

该模块只依赖控件的最小读取协议（``text``、``value``、``isChecked`` 和
``currentText``），因此可以在没有 QApplication 的情况下测试配置组装。
"""

import ast
import copy
import math
from typing import Any, Mapping


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
        return errors

    def read_instrument_config(self) -> dict:
        w = self.widgets
        instruments = _dict_copy(self.config.get("instruments"))
        supplies = _dict_copy(instruments.get("power_supplies"))
        for name in ("PS1", "PS2", "PS3", "PS4"):
            item = _dict_copy(supplies.get(name))
            item["address"] = self._text(getattr(w, f"{name.lower()}_address"))
            item["enabled"] = self._checked(getattr(w, f"{name.lower()}_enabled"))
            supplies[name] = item
        for name in ("signal_generator", "spectrum_analyzer"):
            item = _dict_copy(instruments.get(name))
            item["address"] = self._text(getattr(w, "sg_address" if name == "signal_generator" else "sa_address"))
            item["enabled"] = self._checked(getattr(w, "sg_enabled" if name == "signal_generator" else "sa_enabled"))
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
            "driver_mode": {"enabled": self._checked(w.driver_mode_check)},
        })
        return result

    def read_power_supply_config(self) -> dict:
        result = {}
        for name, supply in getattr(self.widgets, "power_config_widgets", {}).items():
            channels = {}
            for channel, controls in supply.get("channels", {}).items():
                channels[channel] = {
                    "voltage": {"value": self._value(controls["voltage"]), "protection": self._value(controls["voltage_protection"]), "protection_enabled": self._checked(controls["voltage_protection_enabled"])},
                    "current": {"value": self._value(controls["current"]), "protection": self._value(controls["current_protection"]), "protection_enabled": self._checked(controls["current_protection_enabled"])},
                }
            result[name] = {"channels": channels}
        return {"instruments": {"power_supplies": result}} if result else {}

    def read_power_assignment(self) -> dict:
        w = self.widgets
        controls = getattr(w, "power_assignment_widgets", None)
        if not controls:
            return {}
        driver_enabled = self._checked(controls["driver_enabled"])
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
