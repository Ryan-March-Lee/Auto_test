import copy
import json
import tempfile
import unittest
from pathlib import Path

from domain.configuration.rules import validate_config
from infrastructure.config.validation import load_config, validate_config_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "config_driver_enabled_no_assignment.json"


class ConfigValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 固定夹具：驱动模式启用但未配置驱动供电分配。
        # 不读取现场 config.json，避免测试结果随每日现场配置变化。
        with FIXTURE_PATH.open("r", encoding="utf-8") as config_file:
            cls.base_config = json.load(config_file)

    def test_current_config_is_readable_and_only_has_known_warning(self):
        result = validate_config(self.base_config)
        self.assertTrue(result.valid, result.errors)
        self.assertTrue(any("按自带供电处理" in issue.message for issue in result.warnings))

    def test_confirmed_external_driver_power_has_no_assignment_warning(self):
        config = copy.deepcopy(self.base_config)
        config["driver_mode"] = {"enabled": True, "power_source": "external_ac"}
        result = validate_config(config)
        self.assertFalse(any(issue.path == "driver_mode.power_source" for issue in result.warnings))

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

    def test_assigned_channel_requires_explicit_role(self):
        config = copy.deepcopy(self.base_config)
        del config["instruments"]["power_supplies"]["PS4"]["channels"]["CH1"]["role"]
        result = validate_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("显式声明 role" in issue.message for issue in result.errors),
            [issue.message for issue in result.errors],
        )

    def test_duplicate_assigned_role_is_error(self):
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS4"]["channels"]["CH2"]["role"] = "gate"
        result = validate_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("供电角色重复配置" in issue.message for issue in result.errors),
            [issue.message for issue in result.errors],
        )

    def test_duplicate_assigned_role_across_supplies_is_error(self):
        """gate 由两个不同电源同时提供同样是重复配置。"""
        config = copy.deepcopy(self.base_config)
        config["instruments"]["power_supplies"]["PS1"]["enabled"] = True
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["peaking"] = {
            "name": "PS4",
            "channel": ["CH1"],
        }
        result = validate_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("供电角色重复配置" in issue.message for issue in result.errors),
            [issue.message for issue in result.errors],
        )

    def test_assignment_must_provide_both_gate_and_drain(self):
        config = copy.deepcopy(self.base_config)
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]["channel"] = ["CH1"]
        result = validate_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("gate 和 drain" in issue.message for issue in result.errors),
            [issue.message for issue in result.errors],
        )

    def test_file_loader_uses_explicit_path_without_hardware_access(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "config.json"
            config_path.write_text(json.dumps(self.base_config), encoding="utf-8")
            self.assertEqual(load_config(config_path)["test_frequencies"], self.base_config["test_frequencies"])
            self.assertTrue(validate_config_file(config_path).valid)


class OfficialConfigFileValidationTests(unittest.TestCase):
    """文件级校验必须同时覆盖正式运行 schema 与硬件装配段。"""

    @classmethod
    def setUpClass(cls):
        with FIXTURE_PATH.open("r", encoding="utf-8") as config_file:
            cls.base_config = json.load(config_file)

    def _flat_only_config(self):
        config = copy.deepcopy(self.base_config)
        for key in ("schema_version", "test_plan", "run_mapping"):
            config.pop(key, None)
        return config

    def _write(self, directory, document):
        path = Path(directory) / "config.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_fixture_is_a_valid_official_configuration(self):
        """测试夹具本身就是能被正式仓储接受的正式配置。"""
        with tempfile.TemporaryDirectory() as directory:
            result = validate_config_file(self._write(directory, self.base_config))
        self.assertTrue(result.valid, result.errors)

    def test_flat_fields_alone_are_not_a_valid_configuration(self):
        """扁平硬件字段全部合法、但缺正式段时，文件校验必须失败。

        这是步骤 5 的关键契约：启动器不得用只认扁平字段的旧校验放行无法创建运行的配置。
        """
        with tempfile.TemporaryDirectory() as directory:
            result = validate_config_file(self._write(directory, self._flat_only_config()))
        self.assertFalse(result.valid)
        paths = {issue.path for issue in result.errors}
        self.assertIn("schema_version", paths)
        self.assertIn("test_plan", paths)
        self.assertIn("run_mapping", paths)

    def test_unsupported_root_schema_version_is_rejected(self):
        config = copy.deepcopy(self.base_config)
        config["schema_version"] = "9.0"
        with tempfile.TemporaryDirectory() as directory:
            result = validate_config_file(self._write(directory, config))
        self.assertFalse(result.valid)
        self.assertTrue(any(issue.path == "schema_version" for issue in result.errors))

    def test_legacy_wiring_block_no_longer_blocks_the_file_level_check(self):
        """接线不再是配置字段：旧文件残留的 wiring 块既不校验也不阻塞。"""
        config = copy.deepcopy(self.base_config)
        config["run_mapping"]["wiring"]["confirmed"] = False
        with tempfile.TemporaryDirectory() as directory:
            result = validate_config_file(self._write(directory, config))
        self.assertFalse(any(issue.path.startswith("wiring") for issue in result.errors))

    def test_hardware_section_errors_are_reported_alongside_formal_schema(self):
        config = copy.deepcopy(self.base_config)
        config["test_plan"]["frequencies"]["values"] = []
        config["instruments"]["signal_generator"]["address"] = ""
        with tempfile.TemporaryDirectory() as directory:
            result = validate_config_file(self._write(directory, config))
        paths = {issue.path for issue in result.errors}
        self.assertIn("frequencies.values", paths)
        self.assertIn("instruments.signal_generator.address", paths)

    def test_identical_issues_are_reported_once(self):
        """正式段与硬件段对同一字段的错误不重复堆叠。"""
        config = copy.deepcopy(self.base_config)
        config["test_plan"]["frequencies"]["values"] = []
        with tempfile.TemporaryDirectory() as directory:
            result = validate_config_file(self._write(directory, config))
        keys = [(issue.level, issue.path, issue.message) for issue in result.errors]
        self.assertEqual(len(keys), len(set(keys)))


class SimulatedDeviceBindingValidationTests(unittest.TestCase):
    """模拟扫描写入的设备绑定可以保存，但绝不能通过真实运行校验。"""

    @classmethod
    def setUpClass(cls):
        with FIXTURE_PATH.open("r", encoding="utf-8") as config_file:
            cls.base_config = json.load(config_file)

    def test_simulated_binding_is_rejected_with_a_pointed_message(self):
        config = copy.deepcopy(self.base_config)
        config["device_bindings"] = {
            "signal_generator": {
                "serial": "105462",
                "device_key": "rohde&schwarz:105462",
                "source": "simulated",
            }
        }

        result = validate_config(config)

        paths = {issue.path for issue in result.errors}
        self.assertIn("device_bindings.signal_generator.source", paths)
        self.assertIn("模拟扫描", result.errors[-1].message)

    def test_real_scan_binding_is_accepted(self):
        config = copy.deepcopy(self.base_config)
        config["device_bindings"] = {
            "signal_generator": {
                "serial": "105462",
                "device_key": "rohde&schwarz:105462",
                "source": "scan",
            }
        }

        result = validate_config(config)

        self.assertFalse(
            any(issue.path.startswith("device_bindings") for issue in result.errors)
        )

    def test_missing_or_malformed_bindings_are_ignored_by_this_rule(self):
        for bindings in (None, [], "text", {"signal_generator": "not-a-record"}):
            with self.subTest(bindings=bindings):
                config = copy.deepcopy(self.base_config)
                if bindings is not None:
                    config["device_bindings"] = bindings
                result = validate_config(config)
                self.assertFalse(
                    any(issue.path.startswith("device_bindings") for issue in result.errors)
                )


class ShippedConfigValidationTests(unittest.TestCase):
    """仓库默认生产配置必须通过启动器使用的同一套文件级校验。"""

    def test_repository_config_passes_the_launcher_file_check(self):
        result = validate_config_file(PROJECT_ROOT / "config.json")
        self.assertTrue(
            result.valid,
            [f"{issue.path}: {issue.message}" for issue in result.errors],
        )


if __name__ == "__main__":
    unittest.main()
