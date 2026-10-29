import unittest

from presentation.qt.config_form_state import (
    LEGACY_BINDING_SOURCE,
    ConfigFormState,
    bound_power_supplies,
    effective_device_bindings,
    parse_frequency_text,
)


class _Text:
    def __init__(self, value):
        self.value = value

    def text(self):
        return self.value


class ConfigFormStateTests(unittest.TestCase):
    def test_frequency_parser_accepts_numbers_without_eval(self):
        self.assertEqual(parse_frequency_text("[1, 2.5]"), [1.0, 2.5])
        self.assertIsNone(parse_frequency_text("__import__('os').system('x')"))
        self.assertIsNone(parse_frequency_text("[1, 'bad']"))

    def test_build_preserves_unknown_fields_and_drops_legacy_wiring(self):
        widgets = type("Widgets", (), {})()
        widgets.freq_edit = _Text("[1, 2]")
        widgets.start_power = type("V", (), {"value": lambda self: -10})()
        widgets.stop_power = type("V", (), {"value": lambda self: 10})()
        widgets.power_step = type("V", (), {"value": lambda self: 1})()
        widgets.compression_combo = type("C", (), {"currentText": lambda self: "5dB"})()
        widgets.attenuator_combo = type("C", (), {"currentText": lambda self: "30dB"})()
        widgets.driver_mode_check = type("B", (), {"isChecked": lambda self: False})()
        widgets.max_input_power = type("V", (), {"value": lambda self: 30})()
        widgets.pa_unit_count_combo = type("C", (), {"currentText": lambda self: "1"})()
        widgets.power_config_widgets = {}
        widgets.power_assignment_widgets = {
            "driver_enabled": type("B", (), {"isChecked": lambda self: False})(),
            "driver_power": type("C", (), {"currentText": lambda self: "PS1"})(),
            "pa_unit1_power": type("C", (), {"currentText": lambda self: "PS1"})(),
            "pa_unit2_power": type("C", (), {"currentText": lambda self: "PS2"})(),
            "pa_unit3_power": type("C", (), {"currentText": lambda self: "PS3"})(),
        }
        # 仪器连接只来自设备发现结果：绑定记录就是唯一的地址来源，界面没有地址控件。
        widgets.device_bindings = {
            "signal_generator": {"visa_address": "TCPIP0::192.168.1.201::inst0::INSTR"},
            "PS1": {"visa_address": "TCPIP0::192.168.1.101::inst0::INSTR"},
        }

        original = {"top": "keep", "wiring": {"confirmed": True}, "dut_config": {"extra": 1}}
        result = ConfigFormState(original, widgets).build_config()
        self.assertEqual(result["top"], "keep")
        self.assertEqual(result["dut_config"]["extra"], 1)
        self.assertNotIn("wiring", result)
        instruments = result["instruments"]
        self.assertEqual(
            instruments["signal_generator"]["address"],
            "TCPIP0::192.168.1.201::inst0::INSTR",
        )
        self.assertTrue(instruments["signal_generator"]["enabled"])
        self.assertEqual(
            instruments["power_supplies"]["PS1"]["address"],
            "TCPIP0::192.168.1.101::inst0::INSTR",
        )
        # 没有绑定记录的角色不臆造地址，并显式写成“未启用”，避免旧地址继续被连接。
        self.assertNotIn("address", instruments["spectrum_analyzer"])
        self.assertFalse(instruments["spectrum_analyzer"]["enabled"])
        self.assertNotIn("address", instruments["power_supplies"]["PS2"])
        self.assertFalse(instruments["power_supplies"]["PS2"]["enabled"])

    def test_legacy_config_addresses_become_unconfirmed_bindings(self):
        """只有地址的旧配置迁移成绑定记录；迁移后地址仍可用于连接，但标记来源。"""
        config = {
            "instruments": {
                "signal_generator": {"address": "TCPIP0::192.168.1.201::inst0::INSTR"},
                "spectrum_analyzer": {
                    "address": "TCPIP0::192.168.1.202::inst0::INSTR",
                    "enabled": False,
                },
                "power_supplies": {
                    "PS1": {"address": "TCPIP0::192.168.1.101::inst0::INSTR", "enabled": True},
                    "PS2": {"address": "TCPIP0::192.168.1.102::inst0::INSTR", "enabled": False},
                },
            }
        }

        bindings = effective_device_bindings(config)

        # 启用且地址非空的项被迁移；禁用项不会被迁移动作重新启用。
        self.assertEqual(
            bindings["signal_generator"]["visa_address"], "TCPIP0::192.168.1.201::inst0::INSTR"
        )
        self.assertEqual(bindings["signal_generator"]["source"], LEGACY_BINDING_SOURCE)
        self.assertIn("PS1", bindings)
        self.assertNotIn("spectrum_analyzer", bindings)
        self.assertNotIn("PS2", bindings)
        self.assertEqual(bound_power_supplies(bindings), ["PS1"])

    def test_explicit_empty_bindings_do_not_resurrect_legacy_addresses(self):
        """配置里已有（空的）device_bindings 时，旧地址不得再被当成绑定。"""
        config = {
            "device_bindings": {},
            "instruments": {
                "signal_generator": {"address": "TCPIP0::192.168.1.201::inst0::INSTR"}
            },
        }

        self.assertEqual(effective_device_bindings(config), {})

    def test_power_assignment_must_reference_a_bound_supply(self):
        widgets = type("Widgets", (), {})()
        widgets.freq_edit = _Text("[1.0]")
        widgets.device_bindings = {"PS1": {"visa_address": "TCPIP0::192.168.1.101::inst0::INSTR"}}
        widgets.power_config_widgets = {}
        widgets.pa_unit_count_combo = type("C", (), {"currentText": lambda self: "2"})()
        widgets.power_assignment_widgets = {
            "driver_source": type("C", (), {"currentText": lambda self: "自带供电"})(),
            "driver_power": type("C", (), {"currentText": lambda self: ""})(),
            "pa_unit1_power": type("C", (), {"currentText": lambda self: "PS1"})(),
            "pa_unit2_power": type("C", (), {"currentText": lambda self: "PS3"})(),
        }

        errors = ConfigFormState({}, widgets).validation_errors()

        joined = "；".join(errors)
        self.assertIn("PS3", joined)
        self.assertIn("没有已应用的设备", joined)

    def test_power_assignment_without_any_supply_is_reported(self):
        widgets = type("Widgets", (), {})()
        widgets.freq_edit = _Text("[1.0]")
        widgets.device_bindings = {}
        widgets.power_config_widgets = {}
        widgets.pa_unit_count_combo = type("C", (), {"currentText": lambda self: "1"})()
        widgets.power_assignment_widgets = {
            "driver_source": type("C", (), {"currentText": lambda self: "自带供电"})(),
            "driver_power": type("C", (), {"currentText": lambda self: ""})(),
            "pa_unit1_power": type("C", (), {"currentText": lambda self: ""})(),
        }

        errors = ConfigFormState({}, widgets).validation_errors()

        self.assertIn("未选择电源", "；".join(errors))


if __name__ == "__main__":
    unittest.main()
