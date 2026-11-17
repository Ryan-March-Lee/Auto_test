"""步骤 3：Mock 配置匹配器的纯 Python 单元测试。

匹配器的输入是已加载的场景、快照显示名称和已保存配置文档；这里不启动 Qt、不读
Mock 结果文件，也不创建运行目录。
"""

import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path

from application.dto.measurement_results import MeasurementType
from application.dto.mock_scenario import MockPowerScanPlan, MockScenario
from application.errors import ConfigurationError
from application.inputs.mock_scenario import load_mock_scenario
from application.mock_configuration_matcher import (
    MOCK_CONFIGURATION_NAME,
    MockConfigurationError,
    MockConfigurationMatch,
    MockConfigurationMatcher,
    is_mock_configuration_name,
)
from application.ports.stage_result_repository import measurement_type_for_stage
from infrastructure.filesystem.paths import DEFAULT_MOCK_DATA_PATHS, MockStage


ROOT = Path(__file__).parents[1]
# 单测只读仓库跟踪的 config.mock.json。test_results 下的保存快照被 .gitignore
# 排除，不能作为可移植夹具；README 已说明两者内容一致。
CONFIG_DOCUMENT = ROOT / "config.mock.json"


def _load_scenarios() -> dict[MockStage, MockScenario]:
    return {
        stage: load_mock_scenario(
            DEFAULT_MOCK_DATA_PATHS.require_scenario_path(stage),
            expected_measurement_type=measurement_type_for_stage(stage.value),
        )
        for stage in MockStage
    }


def _load_configuration_document() -> dict:
    return json.loads(CONFIG_DOCUMENT.read_text(encoding="utf-8"))


class MockConfigurationNameTests(unittest.TestCase):
    """名称只是入口：只有 Mock（忽略大小写与首尾空白）才是候选模拟配置。"""

    def test_canonical_name_is_a_candidate(self):
        self.assertTrue(is_mock_configuration_name("Mock"))
        self.assertTrue(MockConfigurationMatcher.matches_name("Mock"))

    def test_leading_trailing_whitespace_and_case_are_ignored(self):
        for name in (" mock ", "MOCK", "MoCk", "\tMock\n"):
            with self.subTest(name=name):
                self.assertTrue(is_mock_configuration_name(name))

    def test_other_names_are_not_candidates(self):
        for name in (
            "",
            "   ",
            "Mock 副本",
            "Mock_2",
            "mock快照",
            "默认配置",
        ):
            with self.subTest(name=name):
                self.assertFalse(is_mock_configuration_name(name))

    def test_non_string_names_are_not_candidates(self):
        for value in (None, 1, 1.0, ["Mock"], {"name": "Mock"}):
            with self.subTest(value=value):
                self.assertFalse(is_mock_configuration_name(value))


class MockConfigurationMatcherTests(unittest.TestCase):
    """当前 Mock 快照匹配成功；摘要任一事实被改动都得到可读错误。"""

    @classmethod
    def setUpClass(cls):
        cls.scenarios = _load_scenarios()
        cls.cable = cls.scenarios[MockStage.CABLE_LOSS]
        cls.driver = cls.scenarios[MockStage.DRIVER_MAPPING]
        cls.amplifier = cls.scenarios[MockStage.AMPLIFIER]
        cls.matcher = MockConfigurationMatcher(tuple(cls.scenarios.values()))
        cls.document = _load_configuration_document()

    def setUp(self):
        self.draft = copy.deepcopy(self.document)

    def _match(self, name="Mock"):
        return self.matcher.match(name, self.draft)

    def _mismatch_message(self, name="Mock"):
        with self.assertRaises(MockConfigurationError) as caught:
            self.matcher.match(name, self.draft)
        return str(caught.exception)

    # ---- 成功路径 ----

    def test_tracked_mock_configuration_matches_every_stage(self):
        match = self._match()
        self.assertIsInstance(match, MockConfigurationMatch)
        self.assertEqual(match.snapshot_name, MOCK_CONFIGURATION_NAME)
        self.assertEqual(
            match.measurement_types,
            (
                MeasurementType.CABLE_LOSS,
                MeasurementType.DRIVER_POWER_MAPPING,
                MeasurementType.AMPLIFIER,
            ),
        )
        self.assertIs(
            match.scenario_for(MeasurementType.CABLE_LOSS), self.cable
        )
        self.assertIs(match.scenario_for("amplifier"), self.amplifier)
        # 阶段摘要有各自的可选事实，不能取“第一个场景”冒充共享摘要。
        self.assertEqual(
            match.scenario_for(
                MeasurementType.CABLE_LOSS
            ).source_config.compression_point,
            "5dB",
        )
        self.assertIsNone(
            match.scenario_for(
                MeasurementType.DRIVER_POWER_MAPPING
            ).source_config.compression_point
        )

    def test_name_whitespace_is_stripped_in_the_result(self):
        match = self.matcher.match("  mOcK  ", self.draft)
        self.assertEqual(match.snapshot_name, "mOcK")

    def test_matching_does_not_modify_the_configuration_document(self):
        before = copy.deepcopy(self.draft)
        self._match()
        self.assertEqual(self.draft, before)

    def test_float_representation_noise_is_accepted(self):
        test_plan = self.draft["test_plan"]
        test_plan["attenuator"]["value"] = 40.0 + 1e-10
        test_plan["signal_source"]["power_scan_plans"][1]["step"] = (
            0.5 + 1e-12
        )
        self._match()

    def test_a_single_scenario_matcher_is_supported(self):
        matcher = MockConfigurationMatcher(self.cable)
        self.assertEqual(matcher.scenarios, (self.cable,))
        matcher.match("Mock", self.draft)

    def test_legacy_single_scan_scalar_shape_is_read(self):
        signal_source = self.draft["test_plan"]["signal_source"]
        signal_source.pop("power_scan_plans")
        signal_source.update(
            {"start_power": -20.0, "stop_power": 0.0, "step": 1.0}
        )
        message = self._mismatch_message()
        # 1.1 标量形状被正式读取为一段计划，因此错误里能看到实际值而不是“未配置”。
        self.assertIn("功率扫描计划不一致", message)
        self.assertIn("-20→0 dBm（步进 1 dB）", message)

    # ---- 入口与错误路径 ----

    def test_other_names_are_refused_even_when_the_summary_matches(self):
        message = self._mismatch_message("Mock 副本")
        self.assertIn("不是 Mock 模拟配置", message)

    def test_non_string_name_is_refused(self):
        with self.assertRaises(MockConfigurationError) as caught:
            self.matcher.match(None, self.draft)
        self.assertIn("必须是字符串", str(caught.exception))

    def test_missing_test_plan_is_refused_before_comparison(self):
        self.draft.pop("test_plan")
        message = self._mismatch_message()
        self.assertIn("缺少 test_plan 段", message)

    def test_non_object_test_plan_is_refused(self):
        self.draft["test_plan"] = []
        message = self._mismatch_message()
        self.assertIn("test_plan 段必须是 JSON 对象", message)

    def test_non_object_document_is_refused(self):
        with self.assertRaises(MockConfigurationError) as caught:
            self.matcher.match("Mock", ["test_plan"])
        self.assertIn("JSON 对象", str(caught.exception))

    def test_changed_frequency_is_reported(self):
        self.draft["test_plan"]["frequencies"]["values"][3] = 1.35
        message = self._mismatch_message()
        self.assertIn("测试频点不一致", message)
        self.assertIn("1.35", message)
        self.assertIn("场景「", message)

    def test_invalid_frequency_value_is_reported_as_unreadable(self):
        self.draft["test_plan"]["frequencies"]["values"][0] = "1.0"
        message = self._mismatch_message()
        self.assertIn("测试频点不一致", message)
        self.assertIn("未配置或包含非法数值", message)

    def test_out_of_range_numbers_are_reported_as_unreadable(self):
        """超出浮点范围的 JSON 整数不能以 OverflowError 逃出匹配器。"""
        huge = 10**400
        cases = (
            ("测试频点", ("frequencies", "values", 0)),
            ("衰减器", ("attenuator", "value")),
            ("驱放预估增益", ("driver_mode", "estimated_gain_db")),
            ("驱放最大输入", ("driver_mode", "max_input_power")),
            ("DUT 最大输入", ("dut", "max_input_power")),
            (
                "扫描计划步进",
                ("signal_source", "power_scan_plans", 0, "step"),
            ),
        )
        for label, path in cases:
            with self.subTest(field=label):
                draft = copy.deepcopy(self.document)
                target = draft["test_plan"]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = huge
                with self.assertRaises(MockConfigurationError) as caught:
                    self.matcher.match("Mock", draft)
                message = str(caught.exception)
                self.assertTrue(
                    "未配置有效数值" in message
                    or "未配置或包含非法数值" in message,
                    message,
                )

    def test_changed_frequency_unit_is_reported(self):
        self.draft["test_plan"]["frequencies"]["unit"] = "MHz"
        message = self._mismatch_message()
        self.assertIn("频率单位必须是 GHz", message)
        self.assertIn("MHz", message)

    def test_changed_attenuator_is_reported(self):
        self.draft["test_plan"]["attenuator"]["value"] = 30.0
        message = self._mismatch_message()
        self.assertIn("衰减器不一致", message)
        self.assertIn("30 dB", message)
        self.assertIn("40 dB", message)

    def test_missing_attenuator_value_is_reported(self):
        self.draft["test_plan"]["attenuator"]["value"] = None
        message = self._mismatch_message()
        self.assertIn("衰减器未配置有效数值", message)

    def test_changed_compression_point_is_reported(self):
        self.draft["test_plan"]["compression_point"]["type"] = "3dB"
        message = self._mismatch_message()
        self.assertIn("压缩点不一致", message)
        self.assertIn("3dB", message)
        self.assertIn("5dB", message)

    def test_changed_power_scan_plan_is_reported(self):
        self.draft["test_plan"]["signal_source"]["power_scan_plans"][0][
            "step"
        ] = 2.0
        message = self._mismatch_message()
        self.assertIn("功率扫描计划不一致", message)
        self.assertIn("步进 2 dB", message)

    def test_power_scan_unit_must_be_dbm(self):
        self.draft["test_plan"]["signal_source"]["unit"] = "dBW"
        message = self._mismatch_message()
        self.assertIn("功率扫描单位必须是 dBm", message)
        self.assertIn("dBW", message)

    def test_conflicting_scan_sources_are_rejected(self):
        """计划列表与旧标量同时出现时不能替调用方选一个来源。"""
        plans = self.document["test_plan"]["signal_source"]["power_scan_plans"]
        for candidate in (plans, []):
            with self.subTest(plans=candidate):
                draft = copy.deepcopy(self.document)
                signal_source = draft["test_plan"]["signal_source"]
                signal_source["power_scan_plans"] = copy.deepcopy(candidate)
                signal_source.update(
                    {
                        "start_power": -20.0,
                        "stop_power": 0.0,
                        "step": 1.0,
                    }
                )
                with self.assertRaises(MockConfigurationError) as caught:
                    self.matcher.match("Mock", draft)
                message = str(caught.exception)
                self.assertIn("只能有一个来源", message)
                self.assertIn("start_power", message)

    def test_changed_driver_switch_is_reported(self):
        self.draft["test_plan"]["driver_mode"]["enabled"] = False
        message = self._mismatch_message()
        self.assertIn("驱放开关不一致", message)

    def test_changed_driver_gain_is_reported(self):
        self.draft["test_plan"]["driver_mode"]["estimated_gain_db"] = 30.0
        message = self._mismatch_message()
        self.assertIn("驱放预估增益不一致", message)
        self.assertIn("30 dB", message)

    def test_missing_driver_gain_is_reported(self):
        self.draft["test_plan"]["driver_mode"]["estimated_gain_db"] = None
        message = self._mismatch_message()
        self.assertIn("驱放预估增益未配置有效数值", message)

    def test_changed_driver_max_input_is_reported(self):
        self.draft["test_plan"]["driver_mode"]["max_input_power"] = 3.0
        message = self._mismatch_message()
        self.assertIn("驱放最大输入功率不一致", message)

    def test_changed_dut_max_input_is_reported(self):
        self.draft["test_plan"]["dut"]["max_input_power"] = 7.0
        message = self._mismatch_message()
        self.assertIn("DUT 最大输入功率不一致", message)

    def test_missing_power_reference_is_reported(self):
        self.draft["test_plan"]["signal_source"].pop("power_reference")
        message = self._mismatch_message()
        self.assertIn("功率基准必须是 dut_input", message)

    def test_every_mismatching_scenario_is_listed(self):
        self.draft["test_plan"]["attenuator"]["value"] = 30.0
        message = self._mismatch_message()
        self.assertEqual(message.count("场景「"), len(self.scenarios))

    def test_stage_specific_facts_compare_only_where_the_scene_declares_them(self):
        # 驱动映射场景不声明压缩点：改压缩点不影响它；线损场景声明了 5dB，仍拒绝。
        self.draft["test_plan"]["compression_point"]["type"] = "3dB"
        MockConfigurationMatcher(self.driver).match("Mock", self.draft)
        with self.assertRaises(MockConfigurationError):
            MockConfigurationMatcher(self.cable).match("Mock", self.draft)

    def test_unknown_measurement_type_lookup_is_reported(self):
        match = self._match()
        with self.assertRaises(MockConfigurationError) as caught:
            match.scenario_for("unknown")
        self.assertIn("未知测量类型", str(caught.exception))
        self.assertIn("cable_loss", str(caught.exception))

    # ---- 构造与类型 ----

    def test_empty_scenario_collection_is_rejected(self):
        with self.assertRaises(MockConfigurationError):
            MockConfigurationMatcher([])

    def test_duplicate_measurement_types_are_rejected(self):
        with self.assertRaises(MockConfigurationError) as caught:
            MockConfigurationMatcher([self.cable, self.cable])
        self.assertIn("重复的测量类型", str(caught.exception))

    def test_non_scenario_inputs_are_rejected(self):
        for value in ("cable_loss", 1, [object()]):
            with self.subTest(value=value):
                with self.assertRaises(MockConfigurationError):
                    MockConfigurationMatcher(value)

    def test_error_belongs_to_the_configuration_category(self):
        self.assertTrue(issubclass(MockConfigurationError, ConfigurationError))
        self.assertTrue(issubclass(MockConfigurationError, ValueError))

    def test_single_scan_plan_scene_rejects_a_two_plan_configuration(self):
        source = replace(
            self.cable.source_config,
            power_scan_plans=(
                MockPowerScanPlan(
                    start_power_dbm=-20.0,
                    stop_power_dbm=0.0,
                    step_db=1.0,
                ),
            ),
        )
        scenario = replace(self.cable, source_config=source)
        with self.assertRaises(MockConfigurationError) as caught:
            MockConfigurationMatcher(scenario).match("Mock", self.draft)
        self.assertIn("功率扫描计划不一致", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
