import json
import tempfile
import unittest
from pathlib import Path

from domain.configuration.models import (
    RunConfiguration,
    RunResourceMapping,
    TestPlan,
    validate_run_configuration,
    validate_cable_loss_configuration,
    validate_driver_mapping_configuration,
    validate_run_mapping,
    validate_test_plan,
)
from infrastructure.config.json_config_repository import (
    load_run_configuration,
    load_run_mapping,
    load_test_plan,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def complete_plan():
    return {
        "schema_version": "1.0",
        "template": False,
        "plan_id": "plan-1",
        "frequencies": {"values": [1.0, 1.2], "unit": "GHz"},
        "signal_source": {"start_power": 0, "stop_power": 10, "step": 1, "unit": "dBm"},
        "compression_point": {"type": "3dB"},
        "attenuator": {"value": 30, "unit": "dB"},
        "dut": {
            "max_input_power": 15,
            "power_channels": {
                "gate": {
                    "role": "gate",
                    "voltage": -2.8,
                    "current": 0.01,
                    "voltage_protection": -1,
                    "current_protection": 0.1,
                },
                "drain": {
                    "role": "drain",
                    "voltage": 28,
                    "current": 1,
                    "voltage_protection": 30,
                    "current_protection": 2,
                },
            },
        },
        "driver_mode": {"enabled": False, "power_channels": {}},
        "other_parameters": {},
    }


def complete_mapping():
    return {
        "schema_version": "1.0",
        "template": False,
        "run_id": "run-1",
        "run_datetime": "2026-08-19T10:00:00",
        "operator": "tester",
        "instruments": {
            "signal_generator": {"model": "SG", "visa_address": "TCPIP::SG::INSTR"},
            "spectrum_analyzer": {"model": "SA", "visa_address": "TCPIP::SA::INSTR"},
            "power_supply": {"model": "PS", "visa_address": "TCPIP::PS::INSTR"},
        },
        "driver_mode": {"enabled": False, "power_channels": []},
        "dut_power_channels": [
            {"channel": "CH_A", "role": "gate", "connection": "DUT 栅极"},
            {"channel": "CH_B", "role": "drain", "connection": "DUT 漏极"},
        ],
    }


def complete_current_plan():
    """一份通过 v1.1 形状校验的当前版测试方案（单电源、两通道）。"""
    return {
        "schema_version": "1.1",
        "template": False,
        "plan_id": "plan-1",
        "frequencies": {"values": [1.0, 1.2], "unit": "GHz"},
        "signal_source": {"start_power": 0, "stop_power": 10, "step": 1, "unit": "dBm"},
        "compression_point": {"type": "3dB"},
        "attenuator": {"value": 30, "unit": "dB"},
        "dut": {
            "max_input_power": 15,
            "drain_channel": {"supply_id": "PS1", "channel": "CH2"},
            "channel_settings": [
                {
                    "supply_id": "PS1",
                    "channel": "CH1",
                    "voltage": 2.8,
                    "current": 0.1,
                    "voltage_protection": None,
                    "current_protection": None,
                    "unit_voltage": "V",
                    "unit_current": "A",
                },
                {
                    "supply_id": "PS1",
                    "channel": "CH2",
                    "voltage": 28.0,
                    "current": 1.0,
                    "voltage_protection": None,
                    "current_protection": None,
                    "unit_voltage": "V",
                    "unit_current": "A",
                },
            ],
        },
        "driver_mode": {"enabled": False, "power_source": None, "channel_settings": []},
        "other_parameters": {},
    }


class ConfigModelTests(unittest.TestCase):
    def test_cable_loss_does_not_require_amplifier_power_roles(self):
        plan = complete_plan()
        plan["dut"]["power_channels"] = {}
        plan["driver_mode"] = {"enabled": True, "power_channels": {}}
        mapping = complete_mapping()
        mapping["dut_power_channels"] = []
        mapping["driver_mode"] = {"enabled": True, "power_channels": []}
        result = validate_cable_loss_configuration(
            RunConfiguration(TestPlan.from_dict(plan), RunResourceMapping.from_dict(mapping))
        )
        self.assertTrue(result.valid, result.errors)

    def test_cable_loss_requires_no_wiring_confirmation(self):
        """接线由测量前的连接图对话框确认，不再是配置字段。"""
        plan = complete_plan()
        mapping = complete_mapping()
        result = validate_cable_loss_configuration(
            RunConfiguration(TestPlan.from_dict(plan), RunResourceMapping.from_dict(mapping))
        )
        self.assertTrue(result.valid, result.errors)
        self.assertNotIn("wiring", RunResourceMapping.from_dict(mapping).to_dict())

    def test_driver_mapping_does_not_require_any_power_channel_assignment(self):
        plan = complete_plan()
        plan["dut"]["power_channels"] = {}
        plan["driver_mode"] = {"enabled": True, "power_channels": {}}
        mapping = complete_mapping()
        mapping["dut_power_channels"] = []
        mapping["driver_mode"] = {"enabled": True, "power_channels": []}
        result = validate_driver_mapping_configuration(
            RunConfiguration(TestPlan.from_dict(plan), RunResourceMapping.from_dict(mapping))
        )
        self.assertTrue(result.valid, result.errors)

    def test_legacy_wiring_block_is_ignored(self):
        """旧配置里的 wiring 块不再进入映射契约，也不会影响校验。"""
        mapping = complete_mapping()
        mapping["wiring"] = {"confirmed": False, "connection_note": None}
        parsed = RunResourceMapping.from_dict(mapping)
        self.assertNotIn("wiring", parsed.to_dict())
        result = validate_run_mapping(parsed)
        self.assertTrue(result.valid, result.errors)

    def test_templates_load_but_do_not_pass_formal_validation(self):
        plan = load_test_plan(PROJECT_ROOT / "baseline" / "test_plan.json")
        mapping = load_run_mapping(PROJECT_ROOT / "baseline" / "run_mapping.json")
        self.assertTrue(plan.template)
        self.assertTrue(mapping.template)
        self.assertFalse(validate_test_plan(plan).valid)
        self.assertFalse(validate_run_mapping(mapping).valid)

    def test_complete_plan_supports_different_per_channel_values(self):
        plan = TestPlan.from_dict(complete_plan())
        result = validate_test_plan(plan)
        self.assertTrue(result.valid, result.errors)
        self.assertNotEqual(plan.dut_power_channels["gate"].voltage, plan.dut_power_channels["drain"].voltage)

    def test_plan_rejects_unsupported_units_instead_of_assuming_canonical_units(self):
        for section, unit in (("frequencies", "MHz"), ("signal_source", "W"), ("attenuator", "dBm")):
            with self.subTest(section=section, unit=unit):
                plan = complete_plan()
                plan[section]["unit"] = unit
                result = validate_test_plan(plan)
                self.assertIn(section + ".unit", {issue.path for issue in result.errors})

    def test_empty_frequency_list_is_invalid(self):
        config = complete_plan()
        config["frequencies"]["values"] = []
        result = validate_test_plan(config)
        self.assertIn("frequencies.values", {issue.path for issue in result.errors})

    def test_numeric_duplicate_frequencies_are_invalid(self):
        config = complete_plan()
        config["frequencies"]["values"] = [1, 1.0]
        result = validate_test_plan(config)
        self.assertIn("frequencies.values", {issue.path for issue in result.errors})

    def test_empty_dut_power_channels_are_invalid(self):
        config = complete_plan()
        config["dut"]["power_channels"] = {}
        result = validate_test_plan(config)
        self.assertIn("dut.power_channels", {issue.path for issue in result.errors})

    def test_malformed_channel_entries_are_not_silently_dropped(self):
        plan = complete_plan()
        plan["dut"]["power_channels"]["CH_BAD"] = None
        result = validate_test_plan(plan)
        # 角色已不参与校验；非对象条目仍按空参数保留在计划里，不再伪装成角色错误。
        self.assertTrue(result.valid, result.errors)
        mapping = complete_mapping()
        mapping["dut_power_channels"].append(None)
        result = validate_run_mapping(mapping)
        self.assertTrue(any("dut_power_channels[2].channel" == issue.path for issue in result.errors))

    def test_invalid_power_range_and_step_are_invalid(self):
        config = complete_plan()
        config["signal_source"].update({"start_power": 10, "stop_power": 0, "step": 0})
        result = validate_test_plan(config)
        paths = {issue.path for issue in result.errors}
        self.assertIn("signal_source", paths)
        self.assertIn("signal_source.step", paths)

    def test_invalid_compression_point_is_invalid(self):
        config = complete_plan()
        config["compression_point"]["type"] = "2dB"
        result = validate_test_plan(config)
        self.assertIn("compression_point.type", {issue.path for issue in result.errors})

    def test_driver_mode_allows_external_power_without_local_channels(self):
        config = complete_plan()
        config["driver_mode"]["enabled"] = True
        result = validate_test_plan(config)
        self.assertNotIn("driver_mode.power_channels", {issue.path for issue in result.errors})

    def test_driver_mode_must_be_boolean(self):
        plan = complete_plan()
        plan["driver_mode"]["enabled"] = "false"
        mapping = complete_mapping()
        mapping["driver_mode"]["enabled"] = "false"
        self.assertIn("driver_mode.enabled", {issue.path for issue in validate_test_plan(plan).errors})
        self.assertIn("driver_mode.enabled", {issue.path for issue in validate_run_mapping(mapping).errors})

    def test_missing_mapping_address_is_invalid(self):
        config = complete_mapping()
        config["instruments"]["power_supply"]["visa_address"] = None
        result = validate_run_mapping(config)
        paths = {issue.path for issue in result.errors}
        self.assertIn("instruments.power_supply.visa_address", paths)

    def test_driver_and_dut_cannot_share_a_channel(self):
        config = complete_mapping()
        config["driver_mode"] = {
            "enabled": True,
            "power_channels": [{"channel": "CH_A", "role": "driver", "connection": "驱动功放"}],
        }
        result = validate_run_mapping(config)
        self.assertTrue(any("不能重复使用通道" in issue.message for issue in result.errors))

    def test_driver_disabled_rejects_driver_mapping(self):
        config = complete_mapping()
        config["driver_mode"]["power_channels"] = [{"channel": "CH_X", "role": "driver", "connection": "驱动功放"}]
        result = validate_run_mapping(config)
        self.assertTrue(any(issue.path == "driver_mode.power_channels" for issue in result.errors))

    def test_round_trip_and_combined_validation(self):
        plan = TestPlan.from_dict(complete_plan())
        mapping = RunResourceMapping.from_dict(complete_mapping())
        combined = validate_run_configuration(RunConfiguration(plan, mapping))
        self.assertTrue(combined.valid, combined.errors)
        self.assertEqual(TestPlan.from_dict(plan.to_dict()).dut_power_channels.keys(), plan.dut_power_channels.keys())
        self.assertEqual(RunResourceMapping.from_dict(mapping.to_dict()).dut_power_channels[0].channel, "CH_A")

    def test_combined_validation_does_not_compare_channel_roles(self):
        """角色不再参与 DUT 计划与运行映射的配对：物理通道一致即可。"""
        plan = TestPlan.from_dict(complete_plan())
        mapping_data = complete_mapping()
        mapping_data["dut_power_channels"][1]["role"] = "bias"
        result = validate_run_configuration(
            RunConfiguration(plan, RunResourceMapping.from_dict(mapping_data))
        )
        self.assertTrue(result.valid, result.errors)

    def test_run_mapping_does_not_require_channel_role(self):
        mapping = complete_mapping()
        del mapping["dut_power_channels"][0]["role"]
        result = validate_run_mapping(mapping)
        self.assertTrue(result.valid, result.errors)

    def test_role_free_plan_and_mapping_are_valid(self):
        """步骤 5：通道没有 role 也能通过正式校验。"""
        plan = complete_plan()
        for channel in plan["dut"]["power_channels"].values():
            channel.pop("role", None)
        mapping = complete_mapping()
        for channel in mapping["dut_power_channels"]:
            channel.pop("role", None)
        result = validate_run_configuration(
            RunConfiguration(TestPlan.from_dict(plan), RunResourceMapping.from_dict(mapping))
        )
        self.assertTrue(result.valid, result.errors)

    def test_channel_settings_rejects_legacy_and_unknown_fields(self):
        """1.1 的 ``channel_settings`` 不接受 ``role`` / ``connection`` 或任意未知键。"""
        for field in ("role", "connection", "mystery"):
            with self.subTest(field=field):
                plan = complete_current_plan()
                plan["dut"]["channel_settings"][0][field] = "值"
                result = validate_test_plan(plan)
                self.assertIn(
                    "dut.channel_settings[0]",
                    {issue.path for issue in result.errors},
                )

    def test_channel_settings_accepts_the_frozen_shape(self):
        self.assertTrue(validate_test_plan(complete_current_plan()).valid)

    def test_load_run_configuration_is_file_only(self):
        with tempfile.TemporaryDirectory() as directory:
            plan_path = Path(directory) / "plan.json"
            mapping_path = Path(directory) / "mapping.json"
            plan_path.write_text(json.dumps(complete_plan(), ensure_ascii=False), encoding="utf-8")
            mapping_path.write_text(json.dumps(complete_mapping(), ensure_ascii=False), encoding="utf-8")
            configuration = load_run_configuration(plan_path, mapping_path)
            self.assertEqual(configuration.test_plan.plan_id, "plan-1")
            self.assertEqual(configuration.run_mapping.run_id, "run-1")


if __name__ == "__main__":
    unittest.main()
