import copy
import unittest

from domain.configuration.models import validate_run_configuration
from presentation.qt.configuration_authoring import (
    ConfigurationAuthoringError,
    author_official_configuration,
    parse_db_value,
)


def _flat_config() -> dict:
    return {
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


class ConfigurationAuthoringTests(unittest.TestCase):
    def test_authored_configuration_is_valid_official_schema(self):
        document = author_official_configuration(_flat_config())
        from domain.configuration.models import RunConfiguration, RunResourceMapping, TestPlan

        configuration = RunConfiguration(
            TestPlan.from_dict(document["test_plan"]),
            RunResourceMapping.from_dict(document["run_mapping"]),
        )
        self.assertTrue(validate_run_configuration(configuration).valid)
        self.assertEqual(
            set(configuration.test_plan.dut_power_channels), {"gate", "drain"}
        )

    def test_unknown_channel_role_is_not_guessed(self):
        flat = _flat_config()
        flat["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"]["channel"] = [
            "CH99"
        ]
        document = author_official_configuration(flat)
        from domain.configuration.models import RunConfiguration, RunResourceMapping, TestPlan

        configuration = RunConfiguration(
            TestPlan.from_dict(document["test_plan"]),
            RunResourceMapping.from_dict(document["run_mapping"]),
        )
        paths = {issue.path for issue in validate_run_configuration(configuration).errors}
        self.assertIn("dut_power_channels[0].role", paths)

    def test_instrument_power_channel_roles_are_not_invented(self):
        """仪器侧供电角色由配置页控件显式选择，authoring 不按 CH1/CH2 补默认值。"""
        flat = _flat_config()
        flat["instruments"]["power_supplies"]["PS1"]["channels"]["CH1"]["role"] = "drain"
        document = author_official_configuration(flat)

        channels = document["instruments"]["power_supplies"]["PS1"]["channels"]
        self.assertEqual(channels["CH1"]["role"], "drain")
        self.assertNotIn("role", channels["CH2"])

    def test_externally_powered_driver_mode_keeps_the_driver_mapping(self):
        """驱放自带供电：不占本机电源通道，但驱动映射照常生成并执行。"""
        flat = _flat_config()
        flat["driver_mode"] = {"enabled": True, "power_source": "external_ac"}
        flat["power_supply_assignment"]["driver_amplifier"] = {
            "power_supply_count": 0,
            "supplies": {},
        }
        document = author_official_configuration(flat)

        self.assertTrue(document["test_plan"]["driver_mode"]["enabled"])
        self.assertEqual(
            document["test_plan"]["driver_mode"]["power_source"], "external_ac"
        )
        self.assertTrue(document["run_mapping"]["driver_mode"]["enabled"])
        self.assertEqual(
            document["run_mapping"]["driver_mode"]["power_source"], "external_ac"
        )
        self.assertEqual(
            document["run_mapping"]["driver_mode"]["power_channels"], []
        )

        from domain.configuration.models import RunConfiguration, RunResourceMapping, TestPlan

        configuration = RunConfiguration(
            TestPlan.from_dict(document["test_plan"]),
            RunResourceMapping.from_dict(document["run_mapping"]),
        )
        self.assertTrue(validate_run_configuration(configuration).valid)

    def test_driver_mode_without_a_power_source_is_inferred_from_the_channels(self):
        flat = _flat_config()
        flat["driver_mode"] = {"enabled": True}
        flat["power_supply_assignment"]["driver_amplifier"] = {
            "power_supply_count": 1,
            "supplies": {"main": {"name": "PS1", "channel": ["CH1", "CH2"]}},
        }
        document = author_official_configuration(flat)

        self.assertEqual(
            document["test_plan"]["driver_mode"]["power_source"], "dc_supply"
        )

    def test_authoring_does_not_mutate_the_input(self):
        flat = _flat_config()
        original = copy.deepcopy(flat)
        author_official_configuration(flat)
        self.assertEqual(flat, original)

    def test_power_supply_follows_the_assignment_not_the_enabled_count(self):
        """正式 run_mapping 记录被分配使用的电源，而不是“唯一启用的电源”。"""
        flat = _flat_config()
        flat["instruments"]["power_supplies"]["PS2"] = {
            "address": "sim::ps2",
            "enabled": True,
            "channels": {"CH1": {"voltage": {"value": 5.0}, "current": {"value": 0.5}}},
        }
        document = author_official_configuration(flat)
        self.assertEqual(document["run_mapping"]["instruments"]["power_supply"]["model"], "PS1")
        self.assertEqual(
            document["run_mapping"]["instruments"]["power_supply"]["visa_address"], "sim::ps1"
        )

    def test_unassigned_supplies_are_not_registered_as_the_run_supply(self):
        flat = _flat_config()
        flat["power_supply_assignment"]["dut_amplifier"]["supplies"] = {}
        document = author_official_configuration(flat)
        self.assertIsNone(document["run_mapping"]["instruments"]["power_supply"]["model"])
        self.assertIsNone(
            document["run_mapping"]["instruments"]["power_supply"]["visa_address"]
        )

    def test_multiple_assigned_supplies_are_rejected_instead_of_guessed(self):
        """正式 schema 只能登记一个本机电源：多电源指派必须显式失败。"""
        flat = _flat_config()
        flat["driver_mode"] = {"enabled": True}
        flat["instruments"]["power_supplies"]["PS2"] = {
            "address": "sim::ps2",
            "enabled": True,
            "channels": {
                "CH1": {"voltage": {"value": 5.0}, "current": {"value": 0.5}},
                "CH2": {"voltage": {"value": 28.0}, "current": {"value": 1.0}},
            },
        }
        flat["power_supply_assignment"]["driver_amplifier"] = {
            "power_supply_count": 1,
            "supplies": {"driver": {"name": "PS2", "channel": ["CH1", "CH2"]}},
        }
        with self.assertRaises(ConfigurationAuthoringError) as raised:
            author_official_configuration(flat)
        message = str(raised.exception)
        self.assertIn("PS1", message)
        self.assertIn("PS2", message)

    def test_parse_db_value(self):
        self.assertEqual(parse_db_value("30dB"), 30.0)
        self.assertEqual(parse_db_value(" 5 dB "), 5.0)
        self.assertIsNone(parse_db_value("attenuator"))
        self.assertIsNone(parse_db_value(None))


if __name__ == "__main__":
    unittest.main()
