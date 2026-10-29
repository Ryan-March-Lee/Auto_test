import json
import tempfile
import unittest
from pathlib import Path

from infrastructure.config.json_config_repository import ConfigurationRepository
from presentation.qt.configuration_authoring import author_official_configuration


def _complete_combined_config() -> dict:
    return author_official_configuration(
        {
            "test_frequencies": [2.4],
            "signal_source": {"start_power": -10.0, "stop_power": 0.0, "step": 1.0},
            "compression_point": {"type": "5dB"},
            "driver_mode": {"enabled": False},
            "attenuator": {"type": "30dB"},
            "dut_config": {"max_input_power": 30.0, "power_supply_count": 1},
            "instruments": {
                "signal_generator": {"address": "sim::sg", "model": "SG", "enabled": True},
                "spectrum_analyzer": {"address": "sim::sa", "model": "SA", "enabled": True},
                "power_supplies": {
                    "PS1": {
                        "address": "sim::ps1",
                        "enabled": True,
                        "channels": {
                            "CH1": {"voltage": {"value": 2.8}, "current": {"value": 0.1}},
                            "CH2": {"voltage": {"value": 28.0}, "current": {"value": 2.0}},
                        },
                    }
                },
            },
            "power_supply_assignment": {
                "driver_amplifier": {"power_supply_count": 0, "supplies": {}},
                "dut_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {"carrier": {"name": "PS1", "channel": ["CH1", "CH2"]}},
                },
            },
        }
    )


class ConfigurationRepositoryTests(unittest.TestCase):
    def test_loads_combined_official_config_without_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(_complete_combined_config()), encoding="utf-8")

            result = ConfigurationRepository().load(path)

        self.assertTrue(result.valid, result.validation.errors)
        self.assertEqual(result.configuration.test_plan.frequencies, [2.4])
        self.assertEqual(
            result.configuration.run_mapping.instruments["power_supply"].visa_address,
            "sim::ps1",
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
            path.write_text(json.dumps(_complete_combined_config()), encoding="utf-8")

            result = ConfigurationRepository().load(path)

        self.assertTrue(result.valid, result.validation.errors)
        self.assertEqual(
            set(result.configuration.test_plan.dut_power_channels), {"gate", "drain"}
        )


if __name__ == "__main__":
    unittest.main()
