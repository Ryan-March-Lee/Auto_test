import copy
import json
import tempfile
import unittest
from pathlib import Path

from config_validation import load_config, validate_config, validate_config_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConfigValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (PROJECT_ROOT / "config.json").open("r", encoding="utf-8") as config_file:
            cls.base_config = json.load(config_file)

    def test_current_config_is_readable_and_only_has_known_warning(self):
        result = validate_config(self.base_config)
        self.assertTrue(result.valid, result.errors)
        self.assertTrue(any("驱动模式已启用" in issue.message for issue in result.warnings))

    def test_missing_top_level_section_is_error(self):
        config = copy.deepcopy(self.base_config)
        del config["signal_source"]
        result = validate_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(any(issue.path == "signal_source" for issue in result.errors))

    def test_invalid_power_range_and_step_are_errors(self):
        config = copy.deepcopy(self.base_config)
        config["signal_source"].update({"start_power": 0, "stop_power": -1, "step": 0})
        result = validate_config(config)
        paths = {issue.path for issue in result.errors}
        self.assertIn("signal_source", paths)
        self.assertIn("signal_source.step", paths)

    def test_invalid_db_value_is_error(self):
        config = copy.deepcopy(self.base_config)
        config["attenuator"]["type"] = "attenuator"
        result = validate_config(config)
        self.assertIn("attenuator.type", {issue.path for issue in result.errors})

    def test_non_finite_numbers_are_errors(self):
        config = copy.deepcopy(self.base_config)
        config["test_frequencies"][0] = float("nan")
        config["signal_source"]["start_power"] = float("inf")
        config["instruments"]["power_supplies"]["PS4"]["channels"]["CH1"]["voltage"]["value"] = float("-inf")
        result = validate_config(config)
        paths = {issue.path for issue in result.errors}
        self.assertIn("test_frequencies[0]", paths)
        self.assertIn("signal_source.start_power", paths)
        self.assertIn("instruments.power_supplies.PS4.channels.CH1.voltage.value", paths)

    def test_missing_section_does_not_duplicate_nested_errors(self):
        config = copy.deepcopy(self.base_config)
        del config["signal_source"]
        result = validate_config(config)
        self.assertEqual([issue.path for issue in result.errors], ["signal_source"])

    def test_compression_point_must_be_positive_and_attenuator_non_negative(self):
        config = copy.deepcopy(self.base_config)
        config["compression_point"]["type"] = "0dB"
        config["attenuator"]["type"] = "-1dB"
        result = validate_config(config)
        paths = {issue.path for issue in result.errors}
        self.assertIn("compression_point.type", paths)
        self.assertIn("attenuator.type", paths)

    def test_enabled_protection_above_working_value_is_warning(self):
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS4"]["enabled"] = True
        config["instruments"]["power_supplies"]["PS4"]["channels"]["CH2"]["current"].update(
            {"value": 3.0, "protection": 2.0, "protection_enabled": True}
        )
        result = validate_config(config)
        self.assertTrue(result.valid)
        self.assertTrue(any("保护已启用" in issue.message for issue in result.warnings))

    def test_disabled_supply_does_not_report_runtime_protection_warning(self):
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS4"]["enabled"] = False
        config["instruments"]["power_supplies"]["PS4"]["channels"]["CH2"]["current"].update(
            {"value": 3.0, "protection": 2.0, "protection_enabled": True}
        )
        result = validate_config(config)
        self.assertFalse(any("保护已启用" in issue.message for issue in result.warnings))

    def test_missing_assigned_supply_is_error(self):
        config = copy.deepcopy(self.base_config)
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]["name"] = "PS_UNKNOWN"
        result = validate_config(config)
        self.assertTrue(any("不存在的电源" in issue.message for issue in result.errors))

    def test_disabled_assigned_supply_is_error(self):
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS4"]["enabled"] = False
        result = validate_config(config)
        self.assertTrue(any("未启用" in issue.message for issue in result.errors))

    def test_missing_assigned_channel_is_error(self):
        config = copy.deepcopy(self.base_config)
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]["channel"] = ["CH99"]
        result = validate_config(config)
        self.assertTrue(any("CH99" in issue.message for issue in result.errors))

    def test_file_loader_uses_explicit_path_without_hardware_access(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "config.json"
            config_path.write_text(json.dumps(self.base_config), encoding="utf-8")
            self.assertEqual(load_config(config_path)["test_frequencies"], self.base_config["test_frequencies"])
            self.assertTrue(validate_config_file(config_path).valid)


if __name__ == "__main__":
    unittest.main()
