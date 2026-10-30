import copy
import unittest

from domain.configuration.models import validate_run_configuration
from presentation.qt.configuration_authoring import (
    ConfigurationAuthoringError,
    author_official_configuration,
    parse_db_value,
)


def _flat_config() -> dict:
    """一份最小的表单配置：一台电源、一条 DUT 分配。

    电源条目带身份字段（``label`` / ``serial`` / ``device_key``）：正式运行映射的快照
    条目必须自带身份。只有地址的条目仍会被写进映射，但正式校验会报缺少身份。
    """
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
                "DP8B1519": {
                    "label": "DP8B1519",
                    "model": "DP832A",
                    "serial": "DP8B214901519",
                    "device_key": "rigol technologies:dp8b214901519",
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
                "supplies": {
                    "carrier": {
                        "supply_id": "DP8B1519",
                        "name": "DP8B1519",
                        "channel": ["CH1", "CH2"],
                    }
                },
            },
        },
    }


def _second_supply() -> dict:
    return {
        "label": "DP8B0885",
        "model": "DP832A",
        "serial": "DP8B174400885",
        "device_key": "rigol technologies:dp8b174400885",
        "address": "sim::ps2",
        "enabled": True,
        "channels": {
            "CH1": {"voltage": {"value": 5.0}, "current": {"value": 0.5}},
            "CH2": {"voltage": {"value": 28.0}, "current": {"value": 1.0}},
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
        flat["instruments"]["power_supplies"]["DP8B1519"]["channels"]["CH1"]["role"] = "drain"
        document = author_official_configuration(flat)

        channels = document["instruments"]["power_supplies"]["DP8B1519"]["channels"]
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
            "supplies": {
                "main": {"supply_id": "DP8B1519", "name": "DP8B1519", "channel": ["CH1", "CH2"]}
            },
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
        power_supplies = document["run_mapping"]["instruments"]["power_supplies"]
        self.assertEqual(sorted(power_supplies), ["DP8B1519"])
        self.assertEqual(power_supplies["DP8B1519"]["visa_address"], "sim::ps1")

    def test_run_mapping_carries_the_supply_identity_not_just_its_name(self):
        """快照条目必须自带 label / 型号 / 序列号 / 设备键，历史重现不回查注册表。"""
        document = author_official_configuration(_flat_config())
        entry = document["run_mapping"]["instruments"]["power_supplies"]["DP8B1519"]
        self.assertEqual(
            entry,
            {
                "label": "DP8B1519",
                "model": "DP832A",
                "serial": "DP8B214901519",
                "device_key": "rigol technologies:dp8b214901519",
                "visa_address": "sim::ps1",
            },
        )
        self.assertNotIn("power_supply", document["run_mapping"]["instruments"])

    def test_channels_are_written_as_supply_id_slash_channel(self):
        """通道统一写 supply_id/CHx；DUT 的 gate 与 drain 可以来自不同电源。"""
        flat = _flat_config()
        flat["instruments"]["power_supplies"]["DP8B0885"] = _second_supply()
        flat["power_supply_assignment"]["dut_amplifier"]["supplies"] = {
            "gate": {"supply_id": "DP8B1519", "channel": ["CH1"]},
            "drain": {"supply_id": "DP8B0885", "channel": ["CH2"]},
        }
        document = author_official_configuration(flat)

        self.assertEqual(
            [(item["channel"], item["role"]) for item in document["run_mapping"]["dut_power_channels"]],
            [("DP8B1519/CH1", "gate"), ("DP8B0885/CH2", "drain")],
        )
        self.assertEqual(
            sorted(document["run_mapping"]["instruments"]["power_supplies"]),
            ["DP8B0885", "DP8B1519"],
        )

        from domain.configuration.models import RunConfiguration, RunResourceMapping, TestPlan

        configuration = RunConfiguration(
            TestPlan.from_dict(document["test_plan"]),
            RunResourceMapping.from_dict(document["run_mapping"]),
        )
        self.assertTrue(validate_run_configuration(configuration).valid)

    def test_two_supplies_assigned_to_two_groups_are_both_registered(self):
        """步骤 10：多电源指派不再被拒绝，DUT 与驱动功放各登记一台独立电源。"""
        flat = _flat_config()
        flat["driver_mode"] = {"enabled": True, "power_source": "dc_supply"}
        flat["instruments"]["power_supplies"]["DP8B0885"] = _second_supply()
        flat["power_supply_assignment"]["driver_amplifier"] = {
            "power_supply_count": 1,
            "supplies": {
                "driver": {"supply_id": "DP8B0885", "name": "DP8B0885", "channel": ["CH1"]}
            },
        }
        document = author_official_configuration(flat)

        power_supplies = document["run_mapping"]["instruments"]["power_supplies"]
        self.assertEqual(sorted(power_supplies), ["DP8B0885", "DP8B1519"])
        self.assertEqual(power_supplies["DP8B0885"]["visa_address"], "sim::ps2")
        self.assertEqual(
            [item["channel"] for item in document["run_mapping"]["driver_mode"]["power_channels"]],
            ["DP8B0885/CH1"],
        )

        from domain.configuration.models import RunConfiguration, RunResourceMapping, TestPlan

        configuration = RunConfiguration(
            TestPlan.from_dict(document["test_plan"]),
            RunResourceMapping.from_dict(document["run_mapping"]),
        )
        self.assertTrue(validate_run_configuration(configuration).valid)

    def test_unassigned_supplies_are_not_registered_as_the_run_supply(self):
        flat = _flat_config()
        flat["power_supply_assignment"]["dut_amplifier"]["supplies"] = {}
        document = author_official_configuration(flat)
        self.assertEqual(document["run_mapping"]["instruments"]["power_supplies"], {})

        from domain.configuration.models import RunConfiguration, RunResourceMapping, TestPlan

        configuration = RunConfiguration(
            TestPlan.from_dict(document["test_plan"]),
            RunResourceMapping.from_dict(document["run_mapping"]),
        )
        paths = {issue.path for issue in validate_run_configuration(configuration).errors}
        self.assertIn("instruments.power_supplies", paths)

    def test_unbound_reference_never_becomes_a_power_supply(self):
        """``supply_id: null`` 表示“引用了未绑定的电源”，不能凭空写进快照。"""
        flat = _flat_config()
        flat["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"] = {
            "supply_id": None,
            "channel": ["CH1"],
        }
        document = author_official_configuration(flat)

        self.assertEqual(document["run_mapping"]["instruments"]["power_supplies"], {})
        # 未选择电源时只写通道名，没有前缀就不能被当成某台电源的路由。
        self.assertEqual(
            [item["channel"] for item in document["run_mapping"]["dut_power_channels"]],
            ["CH1"],
        )
        from domain.configuration.models import RunConfiguration, RunResourceMapping, TestPlan

        configuration = RunConfiguration(
            TestPlan.from_dict(document["test_plan"]),
            RunResourceMapping.from_dict(document["run_mapping"]),
        )
        paths = {issue.path for issue in validate_run_configuration(configuration).errors}
        self.assertIn("dut_power_channels[0].channel", paths)

    def test_a_channel_used_twice_is_written_and_reported(self):
        """同一条物理通道被分配两次：原样写进映射，由正式校验报错而不是静默去重。"""
        flat = _flat_config()
        flat["power_supply_assignment"]["dut_amplifier"]["supplies"]["peaking"] = {
            "supply_id": "DP8B1519",
            "channel": ["CH1"],
        }
        document = author_official_configuration(flat)

        self.assertEqual(
            [item["channel"] for item in document["run_mapping"]["dut_power_channels"]],
            ["DP8B1519/CH1", "DP8B1519/CH2", "DP8B1519/CH1"],
        )
        from domain.configuration.models import RunConfiguration, RunResourceMapping, TestPlan

        configuration = RunConfiguration(
            TestPlan.from_dict(document["test_plan"]),
            RunResourceMapping.from_dict(document["run_mapping"]),
        )
        paths = {issue.path for issue in validate_run_configuration(configuration).errors}
        self.assertIn("dut_power_channels[2].channel", paths)

    def test_two_supplies_claiming_the_same_role_are_rejected(self):
        """两台电源都声称 gate：通道不同但角色重复，必须在连接前失败。"""
        flat = _flat_config()
        flat["instruments"]["power_supplies"]["DP8B0885"] = _second_supply()
        flat["power_supply_assignment"]["dut_amplifier"]["supplies"] = {
            "gate": {"supply_id": "DP8B1519", "channel": ["CH1"]},
            "drain": {"supply_id": "DP8B0885", "channel": ["CH1"]},
        }
        document = author_official_configuration(flat)

        self.assertEqual(
            [(item["channel"], item["role"]) for item in document["run_mapping"]["dut_power_channels"]],
            [("DP8B1519/CH1", "gate"), ("DP8B0885/CH1", "gate")],
        )
        from domain.configuration.models import RunConfiguration, RunResourceMapping, TestPlan

        configuration = RunConfiguration(
            TestPlan.from_dict(document["test_plan"]),
            RunResourceMapping.from_dict(document["run_mapping"]),
        )
        paths = {issue.path for issue in validate_run_configuration(configuration).errors}
        self.assertIn("dut_power_channels[1].role", paths)

    def test_parse_db_value(self):
        self.assertEqual(parse_db_value("30dB"), 30.0)
        self.assertEqual(parse_db_value(" 5 dB "), 5.0)
        self.assertIsNone(parse_db_value("attenuator"))
        self.assertIsNone(parse_db_value(None))


if __name__ == "__main__":
    unittest.main()
