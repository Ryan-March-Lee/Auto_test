import unittest

from presentation.qt.config_form_state import ConfigFormState, parse_frequency_text


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

    def test_build_preserves_unknown_fields_and_invalidates_wiring(self):
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
        for name in ("sg", "sa"):
            setattr(widgets, f"{name}_address", _Text(name))
            setattr(widgets, f"{name}_enabled", type("B", (), {"isChecked": lambda self: True})())
        for name in ("ps1", "ps2", "ps3", "ps4"):
            setattr(widgets, f"{name}_address", _Text(name))
            setattr(widgets, f"{name}_enabled", type("B", (), {"isChecked": lambda self: True})())

        original = {"top": "keep", "wiring": {"confirmed": True}, "dut_config": {"extra": 1}}
        result = ConfigFormState(original, widgets).build_config()
        self.assertEqual(result["top"], "keep")
        self.assertEqual(result["dut_config"]["extra"], 1)
        self.assertFalse(result["wiring"]["confirmed"])


if __name__ == "__main__":
    unittest.main()
