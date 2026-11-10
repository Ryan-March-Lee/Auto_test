import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from domain.configuration.models import (
    READABLE_SCHEMA_VERSIONS,
    REWRITABLE_SCHEMA_VERSIONS,
    SUPPORTED_SCHEMA_VERSION,
    PowerScanPlan,
    RunConfiguration,
    RunResourceMapping,
    TestPlan,
    require_current_schema_version,
    validate_run_configuration,
    validate_cable_loss_configuration,
    validate_driver_mapping_configuration,
    validate_run_mapping,
    validate_test_plan,
)
from infrastructure.config.json_config_repository import load_run_configuration


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
        "driver_mode": {"enabled": False, "channel_settings": []},
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


class SchemaVersionGateTests(unittest.TestCase):
    """步骤 2.1：写入版本为 1.2，旧版仍可读取，运行创建只接受当前版本。"""

    def test_current_version_and_readable_set(self):
        self.assertEqual(SUPPORTED_SCHEMA_VERSION, "1.2")
        self.assertEqual(READABLE_SCHEMA_VERSIONS, ("1.0", "1.1", "1.2"))

    def test_current_plan_version_is_not_treated_as_unknown(self):
        plan = complete_current_plan()
        plan["schema_version"] = SUPPORTED_SCHEMA_VERSION

        result = validate_test_plan(plan)

        self.assertFalse(
            any(issue.path == "schema_version" for issue in result.errors),
            result.errors,
        )

    def test_newer_version_is_reported_as_written_by_a_newer_program(self):
        plan = complete_current_plan()
        plan["schema_version"] = "1.3"

        issues = [
            issue
            for issue in validate_test_plan(plan).errors
            if issue.path == "schema_version"
        ]

        self.assertTrue(issues)
        self.assertIn("更新版本", issues[0].message)

    def test_run_creation_gate_accepts_only_the_current_version(self):
        current = RunConfiguration(
            TestPlan.from_dict(
                {**complete_current_plan(), "schema_version": SUPPORTED_SCHEMA_VERSION}
            ),
            RunResourceMapping(schema_version=SUPPORTED_SCHEMA_VERSION),
        )

        self.assertEqual(require_current_schema_version(current), [])

    def test_1_1_requires_an_explicit_resave_before_creating_a_run(self):
        previous = RunConfiguration(
            TestPlan.from_dict(complete_current_plan()),
            RunResourceMapping(schema_version="1.1"),
        )

        issues = require_current_schema_version(previous)

        self.assertEqual(
            {issue.path for issue in issues},
            {"test_plan.schema_version", "run_mapping.schema_version"},
        )
        self.assertTrue(all("重新保存" in issue.message for issue in issues))

    def test_1_0_still_points_at_the_migrator(self):
        legacy = RunConfiguration(
            TestPlan.from_dict(complete_plan()),
            RunResourceMapping(schema_version="1.0"),
        )

        issues = require_current_schema_version(legacy)

        self.assertTrue(issues)
        self.assertTrue(all("迁移器" in issue.message for issue in issues))

    def test_rewritable_versions_are_explicit_not_derived_from_readable_ones(self):
        """将来新增更高可读版本时不能自动获得重写权限。"""
        self.assertEqual(REWRITABLE_SCHEMA_VERSIONS, ("1.1",))
        self.assertNotIn(SUPPORTED_SCHEMA_VERSION, REWRITABLE_SCHEMA_VERSIONS)
        self.assertNotIn("1.0", REWRITABLE_SCHEMA_VERSIONS)


def _current_plan_with_plans(*plans):
    """1.2 测试方案：显式给出功率扫描计划（每段为 start, step, stop）。"""
    plan = complete_current_plan()
    plan["schema_version"] = SUPPORTED_SCHEMA_VERSION
    plan["signal_source"] = {
        "power_scan_plans": [
            {"start_power": start, "step": step, "stop_power": stop}
            for start, step, stop in plans
        ],
        "unit": "dBm",
    }
    return plan


class PowerScanPlanModelTests(unittest.TestCase):
    """步骤 2.2：1.2 只写计划列表，1.1 单段只作为读取兼容。"""

    def test_1_2_writes_power_scan_plans_and_no_scalar_fields(self):
        plan = TestPlan.from_dict(_current_plan_with_plans((-38.0, 1.0, -10.0)))

        signal_source = plan.to_dict()["signal_source"]

        self.assertEqual(
            signal_source["power_scan_plans"],
            [{"start_power": -38.0, "step": 1.0, "stop_power": -10.0}],
        )
        for key in ("start_power", "stop_power", "step"):
            self.assertNotIn(key, signal_source)

    def test_multiple_segments_round_trip_in_order(self):
        document = _current_plan_with_plans((-38.0, 1.0, -10.0), (-10.0, 0.25, -2.0))

        plan = TestPlan.from_dict(document)
        written = plan.to_dict()

        self.assertEqual(
            [segment.to_dict() for segment in plan.power_scan_plans],
            document["signal_source"]["power_scan_plans"],
        )
        self.assertEqual(
            written["signal_source"]["power_scan_plans"],
            document["signal_source"]["power_scan_plans"],
        )
        # 多段计划下不能把第一段冒充成“完整范围”。
        self.assertIsNone(plan.start_power)
        self.assertIsNone(plan.stop_power)
        self.assertIsNone(plan.power_step)

    def test_1_1_scalar_shape_is_read_as_a_single_plan(self):
        plan = TestPlan.from_dict(complete_current_plan())

        self.assertEqual(len(plan.power_scan_plans), 1)
        self.assertEqual(plan.power_scan_plans[0].start_power, 0)
        self.assertEqual(plan.power_scan_plans[0].step, 1)
        self.assertEqual(plan.power_scan_plans[0].stop_power, 10)
        self.assertEqual(plan.start_power, 0)

    def test_plan_and_scalar_together_are_rejected(self):
        document = _current_plan_with_plans((-10.0, 1.0, 0.0))
        document["signal_source"].update(
            {"start_power": -10.0, "stop_power": 0.0, "step": 1.0}
        )

        result = validate_test_plan(document)

        self.assertTrue(
            any(
                issue.path == "signal_source"
                and "power_scan_plans" in issue.message
                for issue in result.errors
            ),
            result.errors,
        )

    def test_direct_construction_with_plans_and_scalars_is_rejected(self):
        """程序内构造也不能让计划列表与旧标量并存，即使数值恰好一致。"""
        single = PowerScanPlan(start_power=-10.0, step=1.0, stop_power=0.0)
        for scalars in (
            {"start_power": -10.0, "stop_power": 0.0, "power_step": 1.0},
            {"start_power": -20.0, "stop_power": 0.0, "power_step": 1.0},
        ):
            with self.subTest(scalars=scalars):
                plan = TestPlan(
                    frequencies=[2.4],
                    power_scan_plans=(single,),
                    **scalars,
                )

                self.assertEqual(
                    plan.conflicting_scan_scalars,
                    ("start_power", "stop_power", "step"),
                )
                errors = validate_test_plan(plan).errors
                self.assertTrue(
                    any(
                        issue.path == "signal_source"
                        and "power_scan_plans" in issue.message
                        for issue in errors
                    ),
                    errors,
                )

    def test_single_plan_fills_compatibility_scalars_without_a_conflict(self):
        plan = TestPlan(
            frequencies=[2.4],
            power_scan_plans=(
                PowerScanPlan(start_power=-10.0, step=1.0, stop_power=0.0),
            ),
        )

        self.assertEqual(plan.start_power, -10.0)
        self.assertEqual(plan.stop_power, 0.0)
        self.assertEqual(plan.power_step, 1.0)
        self.assertEqual(plan.conflicting_scan_scalars, ())

    def test_replace_does_not_turn_derived_scalars_into_a_conflict(self):
        """``dataclasses.replace`` 会带回派生标量；它们不是第二个来源。"""
        plan = TestPlan.from_dict(_current_plan_with_plans((-10.0, 1.0, 0.0)))

        replaced = replace(plan, frequencies=[2.4])

        self.assertEqual(replaced.conflicting_scan_scalars, ())

    def test_missing_plans_are_reported_instead_of_invented(self):
        document = _current_plan_with_plans()
        document["signal_source"].pop("power_scan_plans")

        result = validate_test_plan(document)

        self.assertTrue(
            any(
                issue.path == "signal_source.power_scan_plans"
                for issue in result.errors
            ),
            result.errors,
        )

    def test_invalid_element_reports_its_index(self):
        document = _current_plan_with_plans((-10.0, 1.0, 0.0))
        document["signal_source"]["power_scan_plans"] = [
            {"start_power": -10.0, "step": 1.0, "stop_power": 0.0},
            "not-a-plan",
        ]

        paths = {issue.path for issue in validate_test_plan(document).errors}

        self.assertIn("signal_source.power_scan_plans[1]", paths)

    def test_negative_step_and_reversed_range_report_the_segment_path(self):
        document = _current_plan_with_plans((-10.0, 0.0, 0.0))

        paths = {issue.path for issue in validate_test_plan(document).errors}

        self.assertIn("signal_source.power_scan_plans[0].step", paths)

        document = _current_plan_with_plans((0.0, 1.0, -10.0))
        paths = {issue.path for issue in validate_test_plan(document).errors}
        self.assertIn("signal_source.power_scan_plans[0]", paths)

    def test_gap_and_overlap_between_adjacent_plans_are_rejected(self):
        gap = _current_plan_with_plans((-38.0, 1.0, -10.0), (-9.0, 0.5, -2.0))
        overlap = _current_plan_with_plans((-38.0, 1.0, -10.0), (-11.0, 0.5, -2.0))
        continuous = _current_plan_with_plans((-38.0, 1.0, -10.0), (-10.0, 0.5, -2.0))

        gap_errors = validate_test_plan(gap).errors
        overlap_errors = validate_test_plan(overlap).errors

        self.assertTrue(
            any(
                issue.path == "signal_source.power_scan_plans[1]"
                and "空档" in issue.message
                for issue in gap_errors
            ),
            gap_errors,
        )
        self.assertTrue(
            any(
                issue.path == "signal_source.power_scan_plans[1]"
                and "重叠" in issue.message
                for issue in overlap_errors
            ),
            overlap_errors,
        )
        self.assertFalse(
            any("空档" in issue.message or "重叠" in issue.message
                for issue in validate_test_plan(continuous).errors)
        )

    def test_start_above_the_effective_dut_limit_is_rejected(self):
        document = _current_plan_with_plans((8.0, 1.0, 20.0))
        document["dut"]["max_input_power"] = 5

        paths = {issue.path for issue in validate_test_plan(document).errors}

        self.assertIn("signal_source.power_scan_plans[0].start_power", paths)

    def test_boundary_tolerance_is_shared_with_the_point_generator(self):
        """浮点表述误差不能造出空档/重叠；真实差值必须按后一段路径报告。"""
        noise = 1e-13
        continuous = _current_plan_with_plans(
            (-10.0, 1.0, 0.0), (noise, 0.5, 5.0)
        )
        boundary_errors = [
            issue
            for issue in validate_test_plan(continuous).errors
            if "空档" in issue.message or "重叠" in issue.message
        ]
        self.assertFalse(boundary_errors)

        for offset, keyword in ((1e-9, "空档"), (-1e-9, "重叠")):
            with self.subTest(keyword=keyword):
                document = _current_plan_with_plans(
                    (-10.0, 1.0, 0.0), (offset, 0.5, 5.0)
                )
                errors = validate_test_plan(document).errors

                self.assertTrue(
                    any(
                        issue.path == "signal_source.power_scan_plans[1]"
                        and keyword in issue.message
                        for issue in errors
                    ),
                    errors,
                )

    def test_multi_segment_configuration_saves_but_is_not_runnable_yet(self):
        plan = TestPlan.from_dict(
            _current_plan_with_plans((-38.0, 1.0, -10.0), (-10.0, 0.5, -2.0))
        )
        configuration = RunConfiguration(
            plan, RunResourceMapping(schema_version=SUPPORTED_SCHEMA_VERSION)
        )

        saved = validate_run_configuration(configuration, require_runnable=False)
        runnable = validate_run_configuration(configuration)

        self.assertFalse(
            any("尚未接入" in issue.message for issue in saved.errors), saved.errors
        )
        self.assertTrue(
            any(
                issue.path == "signal_source.power_scan_plans"
                and "尚未接入" in issue.message
                for issue in runnable.errors
            ),
            runnable.errors,
        )


_UNSET = object()


class PowerScanStageGateTests(unittest.TestCase):
    """步骤 2.4：每个运行阶段只承担自己的最小功率扫描校验。

    驱动映射与完整主功放都要在硬件动作前拒绝非法计划；线损测量不使用目标
    网格，不能因为功率计划缺失或待完善而被无关阻塞。
    """

    def _configuration(self, *plans, max_input_power=_UNSET):
        document = _current_plan_with_plans(*plans)
        if max_input_power is not _UNSET:
            document["dut"]["max_input_power"] = max_input_power
        return RunConfiguration(
            TestPlan.from_dict(document),
            RunResourceMapping.from_dict(complete_mapping()),
        )

    def test_driver_mapping_rejects_invalid_plans_before_hardware(self):
        cases = (
            (
                ((-38.0, 1.0, -10.0), (-9.0, 0.5, -2.0)),
                "signal_source.power_scan_plans[1]",
                "空档",
            ),
            (
                ((-38.0, 1.0, -10.0), (-11.0, 0.5, -2.0)),
                "signal_source.power_scan_plans[1]",
                "重叠",
            ),
            (
                ((-10.0, 0.0, 0.0),),
                "signal_source.power_scan_plans[0].step",
                "大于 0",
            ),
        )
        for plans, path, keyword in cases:
            with self.subTest(path=path, keyword=keyword):
                errors = validate_driver_mapping_configuration(
                    self._configuration(*plans)
                ).errors

                self.assertTrue(
                    any(
                        issue.path == path and keyword in issue.message
                        for issue in errors
                    ),
                    errors,
                )

    def test_driver_mapping_rejects_start_above_the_dut_limit(self):
        errors = validate_driver_mapping_configuration(
            self._configuration((8.0, 1.0, 20.0), max_input_power=5.0)
        ).errors

        self.assertTrue(
            any(
                issue.path == "signal_source.power_scan_plans[0].start_power"
                and "高于有效上限" in issue.message
                for issue in errors
            ),
            errors,
        )

    def test_driver_mapping_accepts_a_continuous_multi_segment_plan(self):
        """阶段门禁只拒绝非法计划；连续的多段计划可以进入驱动映射。"""
        errors = validate_driver_mapping_configuration(
            self._configuration((-38.0, 1.0, -10.0), (-10.0, 0.5, -2.0))
        ).errors

        self.assertEqual(errors, [])

    def test_driver_mapping_requires_a_finite_dut_limit(self):
        """上限缺失或非法时不能跳过越限判据，交给同一套结构化校验报告。"""
        for value in (None, "5", True, float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                errors = validate_driver_mapping_configuration(
                    self._configuration((-10.0, 1.0, 0.0), max_input_power=value)
                ).errors

                self.assertIn(
                    "dut.max_input_power",
                    {issue.path for issue in errors},
                    errors,
                )

    def test_cable_loss_ignores_missing_or_invalid_power_scan_plans(self):
        for signal_source in (
            {},
            {"power_scan_plans": None},
            {"power_scan_plans": [{"start_power": "bad"}]},
            {"power_scan_plans": []},
        ):
            with self.subTest(signal_source=signal_source):
                document = complete_current_plan()
                document["signal_source"] = signal_source
                configuration = RunConfiguration(
                    TestPlan.from_dict(document),
                    RunResourceMapping.from_dict(complete_mapping()),
                )

                result = validate_cable_loss_configuration(configuration)

                self.assertTrue(result.valid, result.errors)

    def test_full_run_rejects_invalid_plans_before_hardware(self):
        errors = validate_run_configuration(
            self._configuration((-38.0, 1.0, -10.0), (-9.0, 0.5, -2.0))
        ).errors

        self.assertTrue(
            any(
                issue.path == "signal_source.power_scan_plans[1]"
                and "空档" in issue.message
                for issue in errors
            ),
            errors,
        )


class PowerSemanticsTests(unittest.TestCase):
    """步骤 2：起止功率表示主功放输入功率，驱动增益是全局 dB 值。"""

    def test_old_plan_without_the_marker_is_readable_with_a_compatibility_warning(self):
        plan = complete_current_plan()

        result = validate_test_plan(plan)

        self.assertTrue(result.valid, result.errors)
        self.assertTrue(
            any(
                issue.level == "warning"
                and issue.path == "signal_source.power_reference"
                and "主功放输入功率" in issue.message
                for issue in result.warnings
            ),
            result.warnings,
        )

    def test_new_power_reference_is_accepted_without_the_compatibility_warning(self):
        plan = complete_current_plan()
        plan["signal_source"]["power_reference"] = "dut_input"

        result = validate_test_plan(plan)

        self.assertTrue(result.valid, result.errors)
        self.assertFalse(
            any(
                issue.path == "signal_source.power_reference"
                for issue in result.warnings
            )
        )

    def test_unknown_power_reference_is_rejected(self):
        for value in ("source_output", "", 1, True):
            with self.subTest(value=value):
                plan = complete_current_plan()
                plan["signal_source"]["power_reference"] = value
                result = validate_test_plan(plan)

                self.assertFalse(result.valid)
                self.assertIn(
                    "signal_source.power_reference",
                    {issue.path for issue in result.errors},
                )

    def test_driver_gain_estimate_accepts_finite_db_and_rejects_other_values(self):
        valid = complete_current_plan()
        valid["signal_source"]["power_reference"] = "dut_input"
        valid["driver_mode"]["estimated_gain_db"] = 20.5
        self.assertTrue(validate_test_plan(valid).valid)

        for value in (float("nan"), float("inf"), float("-inf"), "20dB", True):
            with self.subTest(value=value):
                plan = complete_current_plan()
                plan["signal_source"]["power_reference"] = "dut_input"
                plan["driver_mode"]["estimated_gain_db"] = value
                result = validate_test_plan(plan)

                self.assertFalse(result.valid)
                self.assertIn(
                    "driver_mode.estimated_gain_db",
                    {issue.path for issue in result.errors},
                )

    def test_plan_round_trip_keeps_the_new_fields(self):
        plan = complete_current_plan()
        plan["signal_source"]["power_reference"] = "dut_input"
        plan["driver_mode"]["estimated_gain_db"] = 20.5

        again = TestPlan.from_dict(TestPlan.from_dict(plan).to_dict())

        self.assertEqual(again.power_reference, "dut_input")
        self.assertEqual(again.driver_gain_estimate_db, 20.5)


if __name__ == "__main__":
    unittest.main()
