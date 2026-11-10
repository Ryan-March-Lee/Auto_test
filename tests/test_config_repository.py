import json
import tempfile
import unittest
from pathlib import Path

from infrastructure.config.json_config_repository import ConfigurationRepository
from tests.config_fixtures import complete_combined_config


class ConfigurationRepositoryTests(unittest.TestCase):
    def test_loads_combined_official_config_without_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(complete_combined_config()), encoding="utf-8")

            result = ConfigurationRepository().load(path)

        self.assertTrue(result.valid, result.validation.errors)
        self.assertEqual(result.configuration.test_plan.frequencies, [2.4])
        self.assertEqual(
            result.configuration.run_mapping.power_supplies["DP8B1519"].visa_address,
            "sim::ps1",
        )
        # 单数电源条目只读兼容：新配置不再写它。
        self.assertNotIn("power_supply", result.configuration.run_mapping.instruments)

    def test_old_power_range_semantics_is_read_with_a_compatibility_warning(self):
        config = complete_combined_config()
        # 模拟步骤 2 之前保存的 1.1 组合文档：只缺少新语义标记与驱动增益键。
        for section in (config, config["test_plan"]):
            section.get("signal_source", {}).pop("power_reference", None)
            section.get("driver_mode", {}).pop("estimated_gain_db", None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")

            result = ConfigurationRepository().load(path)

        self.assertTrue(result.valid, result.validation.errors)
        self.assertTrue(
            any(
                issue.level == "warning"
                and issue.path == "signal_source.power_reference"
                for issue in result.validation.warnings
            ),
            result.validation.messages(),
        )
        self.assertFalse(
            any(
                "正式段与编辑段不一致" in issue.message
                for issue in result.validation.errors
            )
        )

    def test_flat_legacy_config_is_rejected_instead_of_converted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "test_frequencies": [2.4],
                        "signal_source": {"start_power": -10.0, "stop_power": 0.0, "step": 1.0},
                    }
                ),
                encoding="utf-8",
            )

            result = ConfigurationRepository().load(path)

        self.assertFalse(result.valid)
        self.assertTrue(
            any(issue.path == "test_plan" for issue in result.validation.errors),
            [issue.path for issue in result.validation.errors],
        )

    def test_loads_split_models_and_validates_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            mapping_path = Path(directory) / "mapping.json"
            plan_path.write_text(json.dumps({"schema_version": "9.0"}), encoding="utf-8")
            mapping_path.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")

            result = ConfigurationRepository().load(plan_path, mapping_path)

        self.assertFalse(result.valid)
        self.assertTrue(any(issue.path == "schema_version" for issue in result.validation.errors))

    def test_config_page_save_output_is_directly_readable(self):
        """配置页面保存出的组合配置无需任何旧格式转换即可被正式仓储读取。"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(complete_combined_config()), encoding="utf-8")

            result = ConfigurationRepository().load(path)

        self.assertTrue(result.valid, result.validation.errors)
        self.assertEqual(
            result.configuration.test_plan.drain_channel,
            {"supply_id": "DP8B1519", "channel": "CH2"},
        )
        self.assertEqual(
            [
                (item["supply_id"], item["channel"])
                for item in result.configuration.test_plan.channel_settings
            ],
            [("DP8B1519", "CH1"), ("DP8B1519", "CH2")],
        )

    def test_hand_edited_formal_sections_are_reported_as_stale(self):
        document = complete_combined_config()
        document["test_plan"]["frequencies"]["values"] = [9.9]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            result = ConfigurationRepository().load(path)

        self.assertFalse(result.valid)
        self.assertIn("test_plan", {issue.path for issue in result.validation.errors})

    def test_editing_section_with_both_scan_shapes_is_rejected(self):
        """当前版本文档的编辑段不能同时保留计划列表与旧标量。"""
        document = complete_combined_config()
        document["signal_source"].update(
            {"start_power": -10.0, "stop_power": 0.0, "step": 1.0}
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            result = ConfigurationRepository().load(path)

        self.assertFalse(result.valid)
        self.assertTrue(
            any(
                issue.path == "signal_source" and "power_scan_plans" in issue.message
                for issue in result.validation.errors
            ),
            [f"{issue.path}: {issue.message}" for issue in result.validation.errors],
        )

    def test_saved_current_document_contains_only_the_plan_list(self):
        document = complete_combined_config()

        self.assertEqual(
            document["test_plan"]["signal_source"]["power_scan_plans"],
            [{"start_power": -10.0, "step": 1.0, "stop_power": 0.0}],
        )
        for section in (document, document["test_plan"]):
            for key in ("start_power", "stop_power", "step"):
                self.assertNotIn(key, section["signal_source"])

    def test_stale_check_does_not_depend_on_a_single_editing_field(self):
        """只要是带编辑段的 1.1 组合文件就比对，不能拿 test_frequencies 当开关。"""
        document = complete_combined_config()
        document.pop("test_frequencies")
        document["test_plan"]["dut"]["channel_settings"] = []

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            result = ConfigurationRepository().load(path)

        self.assertFalse(result.valid)
        self.assertIn("test_plan", {issue.path for issue in result.validation.errors})
        self.assertTrue(
            any(
                issue.path == "test_plan" and "正式段" in issue.message
                for issue in result.validation.errors
            ),
            [issue.message for issue in result.validation.errors],
        )


if __name__ == "__main__":
    unittest.main()
