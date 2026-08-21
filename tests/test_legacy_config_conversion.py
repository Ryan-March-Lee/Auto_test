import copy
import json
import tempfile
import unittest
from pathlib import Path

from legacy_config_conversion import (
    convert_legacy_config,
    convert_legacy_config_file,
    parse_legacy_attenuator,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "config_driver_enabled_no_assignment.json"


class LegacyConfigConversionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 固定夹具：驱动模式启用但未配置驱动供电分配。
        # 不读取现场 config.json，避免测试结果随每日现场配置变化。
        with FIXTURE_PATH.open("r", encoding="utf-8") as config_file:
            cls.base_config = json.load(config_file)

    def test_attenuator_parser(self):
        self.assertEqual(parse_legacy_attenuator("30dB"), 30.0)
        self.assertEqual(parse_legacy_attenuator(" 30 dB "), 30.0)
        self.assertIsNone(parse_legacy_attenuator("attenuator"))

    def test_current_config_converts_without_hardware_access(self):
        result = convert_legacy_config(self.base_config)
        self.assertEqual(result.test_plan.frequencies, self.base_config["test_frequencies"])
        self.assertEqual(result.test_plan.attenuator_value, 30.0)
        self.assertEqual(result.test_plan.max_input_power, 29.51)
        self.assertEqual(result.status, "needs_review")
        self.assertIn("run_mapping.wiring.confirmed", result.unresolved_fields)

    def test_unique_enabled_supply_is_used_as_default(self):
        result = convert_legacy_config(self.base_config)
        self.assertEqual(result.selected_supply, "PS4")
        self.assertEqual(result.run_mapping.instruments["power_supply"].model, "PS4")
        self.assertEqual(
            result.run_mapping.instruments["power_supply"].visa_address,
            self.base_config["instruments"]["power_supplies"]["PS4"]["address"],
        )

    def test_explicit_unknown_supply_is_error(self):
        result = convert_legacy_config(self.base_config, selected_supply="PS_UNKNOWN")
        self.assertFalse(result.errors == [])
        self.assertTrue(any(issue.path == "selected_supply" for issue in result.errors))

    def test_old_channels_do_not_become_roles_automatically(self):
        result = convert_legacy_config(self.base_config)
        self.assertEqual([item.channel for item in result.run_mapping.dut_power_channels], ["CH1", "CH2"])
        self.assertTrue(all(item.role is None for item in result.run_mapping.dut_power_channels))
        self.assertTrue(any("gate/drain" in issue.message for issue in result.warnings))
        candidates = result.test_plan.other_parameters["legacy_power_channel_candidates"]
        self.assertEqual([item["channel"] for item in candidates], ["CH1", "CH2"])
        self.assertEqual(candidates[0]["settings"]["voltage"]["value"], 2.8)

    def test_driver_enabled_without_assignment_requires_review(self):
        result = convert_legacy_config(self.base_config)
        self.assertTrue(result.test_plan.driver_enabled)
        self.assertEqual(result.run_mapping.driver_power_channels, [])
        self.assertIn("run_mapping.driver_mode.power_channels", result.unresolved_fields)

    def test_multiple_enabled_supplies_are_not_selected_implicitly(self):
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS1"]["enabled"] = True
        result = convert_legacy_config(config)
        self.assertIsNone(result.selected_supply)
        self.assertIsNone(result.run_mapping.instruments["power_supply"].visa_address)
        self.assertIn("run_mapping.instruments.power_supply", result.unresolved_fields)

    def test_explicit_supply_selection_is_preserved(self):
        result = convert_legacy_config(self.base_config, selected_supply="PS4")
        self.assertEqual(result.selected_supply, "PS4")
        self.assertEqual(result.run_mapping.raw["selected_supply"], "PS4")

    def test_invalid_legacy_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(ValueError):
                convert_legacy_config_file(path)

    def test_missing_legacy_sections_are_reported_without_hardware_access(self):
        config = copy.deepcopy(self.base_config)
        del config["signal_source"]
        result = convert_legacy_config(config)
        self.assertTrue(any(issue.path == "signal_source" for issue in result.errors))

    def test_missing_power_assignment_is_reported(self):
        config = copy.deepcopy(self.base_config)
        del config["power_supply_assignment"]
        result = convert_legacy_config(config)
        self.assertTrue(any(issue.path == "power_supply_assignment" for issue in result.errors))


if __name__ == "__main__":
    unittest.main()
