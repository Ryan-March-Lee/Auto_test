"""步骤 2 契约：Mock 场景的纯 Python 结构、顺序与配置摘要。"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from application.dto.measurement_results import MeasurementType, thaw
from application.dto.mock_scenario import (
    MOCK_CONFIRM_ACCEPTED,
    MOCK_CONFIRM_REJECTED,
    MOCK_CONFIRM_SIGNAL,
    MOCK_SCENARIO_KIND,
    MockArchivalCompletion,
    MockAutoPhaseStart,
    MockCompletionEvent,
    MockDataEvent,
    MockFinishedCompletion,
    MockMessageCompletion,
    MockMessageEvent,
    MockPhase,
    MockPhaseStart,
    MockPhaseStartType,
    MockPowerScanPlan,
    MockProgressEvent,
    MockResultCompletion,
    MockScenario,
    MockScenarioError,
    MockScenarioEvent,
    MockUserConfirmPhaseStart,
    mock_scenario_from_document,
)
from application.errors import ConfigurationError
from application.inputs.mock_scenario import load_mock_scenario
from application.ports.stage_result_repository import measurement_type_for_stage
from infrastructure.filesystem.paths import DEFAULT_MOCK_DATA_PATHS, MockStage


def _events(*, percent: int = 10, at_ms: int = 0):
    return [
        {"at_ms": at_ms, "type": "message", "text": "开始"},
        {
            "at_ms": at_ms + 500,
            "type": "data",
            "payload": {"frequency": 1.0, "value": 1.0},
        },
        {
            "at_ms": at_ms + 600,
            "type": "progress",
            "percent": percent,
            "stage": "scan",
        },
    ]


def _auto_phase(name="path1", events=None):
    return {
        "name": name,
        "title": "阶段",
        "start": {"type": "auto"},
        "events": copy.deepcopy(_events() if events is None else events),
    }


def _confirm_phase(name="path2", events=None):
    return {
        "name": name,
        "title": "路径2",
        "start": {
            "type": "user_confirm",
            "signal": MOCK_CONFIRM_SIGNAL,
            "checkpoint": "path2",
            "prompt": "请连接路径2",
            "accepted": MOCK_CONFIRM_ACCEPTED,
            "rejected": MOCK_CONFIRM_REJECTED,
        },
        "events": copy.deepcopy(
            _events(percent=60) if events is None else events
        ),
    }


def _completion():
    return [
        {"type": "result", "file": "result.json"},
        {
            "type": "archival",
            "outcome": {"stage": "cable_loss", "test_status": "partial"},
        },
        {"type": "message", "text": "完成"},
        {"type": "finished"},
    ]


def _document():
    return {
        "schema_version": "1.0",
        "kind": MOCK_SCENARIO_KIND,
        "measurement_type": "cable_loss",
        "title": "测试场景",
        "description": "",
        "source_config": {
            "template": "config.mock.json",
            "test_frequencies_ghz": [1.0, 1.1],
            "attenuator_db": 40.0,
            "compression_type": "5dB",
            "driver_enabled": True,
            "driver_estimated_gain_db": 40.0,
            "driver_max_input_power_dbm": 0.0,
            "dut_max_input_power_dbm": 6.0,
            "power_scan_plans": [
                {"start_power": -20.0, "step": 1.0, "stop_power": 0.0}
            ],
        },
        "run": {
            "run_id": "20261117T060200Z_c3d9a1f4",
            "mode": "driver",
            "started_at": "2026-11-17T06:02:00Z",
        },
        "phases": [_auto_phase(), _confirm_phase()],
        "completion": _completion(),
    }


class RepositoryMockScenarioTests(unittest.TestCase):
    """三套仓库场景必须能直接通过契约加载，且关键事实与 README 一致。"""

    @classmethod
    def setUpClass(cls):
        cls.scenarios = {}
        for stage in MockStage:
            path = DEFAULT_MOCK_DATA_PATHS.require_scenario_path(stage)
            cls.scenarios[stage] = load_mock_scenario(
                path,
                expected_measurement_type=measurement_type_for_stage(
                    stage.value
                ),
            )

    def test_cable_loss_scenario_matches_the_recorded_replay(self):
        scenario = self.scenarios[MockStage.CABLE_LOSS]
        self.assertIs(scenario.measurement_type, MeasurementType.CABLE_LOSS)
        self.assertEqual(
            [phase.name for phase in scenario.phases], ["path1", "path2"]
        )
        self.assertEqual(
            [phase.data_event_count for phase in scenario.phases], [11, 11]
        )
        self.assertEqual(scenario.data_event_count, 22)
        self.assertEqual(
            sum(
                1
                for phase in scenario.phases
                for event in phase.events
                if isinstance(event, MockProgressEvent)
            ),
            23,
        )
        self.assertIsInstance(
            scenario.phases[0].start, MockAutoPhaseStart
        )
        self.assertIsInstance(
            scenario.phases[1].start, MockUserConfirmPhaseStart
        )
        self.assertIs(scenario.user_confirm_phase, scenario.phases[1])
        self.assertEqual(
            scenario.user_confirm_phase.start.checkpoint, "path2"
        )
        self.assertEqual(
            scenario.result_filename, "20261117T060245Z_9b2e7d10.json"
        )
        self.assertIsInstance(scenario.completion[0], MockResultCompletion)
        self.assertIsInstance(
            scenario.completion[1], MockArchivalCompletion
        )
        self.assertIsInstance(
            scenario.completion[2], MockMessageCompletion
        )
        self.assertIsInstance(
            scenario.completion[3], MockFinishedCompletion
        )

    def test_driver_mapping_scenario_matches_the_recorded_replay(self):
        scenario = self.scenarios[MockStage.DRIVER_MAPPING]
        self.assertIs(
            scenario.measurement_type, MeasurementType.DRIVER_POWER_MAPPING
        )
        self.assertEqual([phase.name for phase in scenario.phases], ["scan"])
        self.assertEqual(scenario.data_event_count, 363)
        self.assertEqual(
            sum(
                1
                for event in scenario.phases[0].events
                if isinstance(event, MockProgressEvent)
            ),
            704,
        )
        self.assertEqual(len(scenario.phases[0].events), 1068)
        self.assertIsNone(scenario.user_confirm_phase)
        self.assertEqual(
            scenario.result_filename, "20261117T060600Z_5c1d9e42.json"
        )

    def test_amplifier_scenario_matches_the_recorded_replay(self):
        scenario = self.scenarios[MockStage.AMPLIFIER]
        self.assertIs(scenario.measurement_type, MeasurementType.AMPLIFIER)
        self.assertEqual([phase.name for phase in scenario.phases], ["scan"])
        self.assertEqual(scenario.data_event_count, 312)
        self.assertEqual(
            sum(
                1
                for event in scenario.phases[0].events
                if isinstance(event, MockProgressEvent)
            ),
            313,
        )
        self.assertEqual(len(scenario.phases[0].events), 626)
        self.assertEqual(
            scenario.result_filename, "20261117T070237Z_7b3d9f21.json"
        )

    def test_measurement_type_agrees_with_the_stage_index(self):
        for stage, scenario in self.scenarios.items():
            with self.subTest(stage=stage.value):
                self.assertIs(
                    scenario.measurement_type,
                    measurement_type_for_stage(stage.value),
                )

    def test_completion_result_files_exist_in_the_stage_directory(self):
        for stage, scenario in self.scenarios.items():
            with self.subTest(stage=stage.value):
                result = DEFAULT_MOCK_DATA_PATHS.require_result_path(
                    stage, scenario.result_filename
                )
                self.assertTrue(result.is_file())
                self.assertEqual(
                    result.parent,
                    DEFAULT_MOCK_DATA_PATHS.stage_dir(stage),
                )

    def test_source_config_exposes_the_shared_mock_facts(self):
        for stage, scenario in self.scenarios.items():
            with self.subTest(stage=stage.value):
                source = scenario.source_config
                self.assertEqual(len(source.test_frequencies_ghz), 11)
                self.assertEqual(source.test_frequencies_ghz[0], 1.0)
                self.assertEqual(source.test_frequencies_ghz[-1], 2.0)
                self.assertEqual(source.attenuator_db, 40.0)
                self.assertTrue(source.driver_enabled)
                self.assertEqual(source.driver_estimated_gain_db, 40.0)
                self.assertEqual(source.driver_max_input_power_dbm, 0.0)
                self.assertEqual(source.dut_max_input_power_dbm, 6.0)
                self.assertEqual(len(source.power_scan_plans), 2)
                self.assertIsInstance(
                    source.power_scan_plans[0], MockPowerScanPlan
                )
                self.assertEqual(
                    source.power_scan_plans[0].start_power_dbm, -20.0
                )
                self.assertEqual(source.power_scan_plans[0].stop_power_dbm, 0.0)
                self.assertEqual(source.power_scan_plans[0].step_db, 1.0)
                self.assertEqual(
                    source.power_scan_plans[1].step_db, 0.5
                )

    def test_source_config_keeps_stage_specific_facts(self):
        cable = self.scenarios[MockStage.CABLE_LOSS].source_config
        driver = self.scenarios[MockStage.DRIVER_MAPPING].source_config
        amplifier = self.scenarios[MockStage.AMPLIFIER].source_config

        self.assertEqual(cable.compression_point, "5dB")
        self.assertEqual(cable.target_input_powers_dbm, ())
        self.assertIsNone(cable.signal_source_power_range_dbm)
        self.assertIsNone(cable.cable_loss_result)

        self.assertIsNone(driver.compression_point)
        self.assertEqual(len(driver.target_input_powers_dbm), 31)
        self.assertEqual(driver.target_input_powers_dbm[0], -20.0)
        self.assertEqual(driver.target_input_powers_dbm[-1], 5.0)
        self.assertEqual(
            driver.signal_source_power_range_dbm, (-100.0, 20.0)
        )
        self.assertEqual(
            driver.cable_loss_result,
            "mock_data/cable_loss/20261117T060245Z_9b2e7d10.json",
        )

        self.assertEqual(amplifier.compression_point, "5dB")
        self.assertEqual(len(amplifier.target_input_powers_dbm), 31)
        self.assertEqual(
            amplifier.driver_mapping_result,
            "mock_data/driver_mapping/20261117T060600Z_5c1d9e42.json",
        )
        self.assertIn("drain_channel", amplifier.document)
        self.assertIn("dut_power_sequence", amplifier.document)

    def test_data_payload_is_deeply_frozen_for_cross_thread_handoff(self):
        scenario = self.scenarios[MockStage.DRIVER_MAPPING]
        data_event = next(
            event
            for event in scenario.phases[0].events
            if isinstance(event, MockDataEvent)
        )
        self.assertNotIsInstance(data_event.payload, dict)
        sweep = data_event.payload["sweep_data"]
        self.assertNotIsInstance(sweep, dict)
        self.assertIsInstance(sweep["input_power_sg"], tuple)

        restored = thaw(data_event.payload)
        self.assertIsInstance(restored, dict)
        self.assertIsInstance(restored["sweep_data"], dict)
        self.assertIsInstance(restored["sweep_data"]["input_power_sg"], list)

    def test_message_events_use_the_declared_type(self):
        scenario = self.scenarios[MockStage.CABLE_LOSS]
        messages = [
            event
            for event in scenario.phases[0].events
            if isinstance(event, MockMessageEvent)
        ]
        self.assertEqual(len(messages), 1)
        self.assertEqual(repr(messages[0].type), "<MockEventType.MESSAGE: 'message'>")
        self.assertEqual(
            repr(scenario.phases[0].start.type),
            "<MockPhaseStartType.AUTO: 'auto'>",
        )


class MockScenarioReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.path = self.root / "scenario.json"

    def test_reader_loads_a_valid_document_and_checks_expected_type(self):
        self.path.write_text(
            json.dumps(_document(), ensure_ascii=False), encoding="utf-8"
        )
        scenario = load_mock_scenario(
            self.path, expected_measurement_type=MeasurementType.CABLE_LOSS
        )
        self.assertIs(scenario.measurement_type, MeasurementType.CABLE_LOSS)
        scenario = load_mock_scenario(
            self.path, expected_measurement_type="cable_loss"
        )
        self.assertIs(scenario.measurement_type, MeasurementType.CABLE_LOSS)

    def test_reader_rejects_missing_file(self):
        missing = self.root / "missing.json"
        with self.assertRaises(MockScenarioError) as raised:
            load_mock_scenario(missing)
        self.assertIn("无法读取 Mock 场景文件", str(raised.exception))
        self.assertIn(str(missing), str(raised.exception))

    def test_reader_rejects_corrupt_json_before_playback(self):
        self.path.write_text('{"schema_version": ', encoding="utf-8")
        with self.assertRaises(MockScenarioError) as raised:
            load_mock_scenario(self.path)
        self.assertIn("不是有效的 JSON", str(raised.exception))
        self.assertIn("行", str(raised.exception))

    def test_reader_rejects_non_object_root(self):
        self.path.write_text("[]", encoding="utf-8")
        with self.assertRaises(MockScenarioError) as raised:
            load_mock_scenario(self.path)
        self.assertIn("必须是 JSON 对象", str(raised.exception))

    def test_reader_rejects_non_utf8_file(self):
        self.path.write_bytes(b"\xff\xfe\x00\x00")
        with self.assertRaises(MockScenarioError) as raised:
            load_mock_scenario(self.path)
        self.assertIn("UTF-8", str(raised.exception))

    def test_reader_contract_errors_include_the_scenario_path(self):
        document = _document()
        document["unexpected"] = True
        self.path.write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )
        with self.assertRaises(MockScenarioError) as raised:
            load_mock_scenario(self.path)
        message = str(raised.exception)
        self.assertIn(str(self.path), message)
        self.assertIn("未知字段", message)


class MockScenarioRejectionTests(unittest.TestCase):
    """任何结构、顺序或类型错误都必须在播放前以可读错误失败。"""

    def setUp(self):
        self.document = _document()

    def _assert_rejects(self, document, *fragments):
        with self.assertRaises(MockScenarioError) as raised:
            mock_scenario_from_document(document)
        message = str(raised.exception)
        for fragment in fragments:
            self.assertIn(fragment, message)
        self.assertIsInstance(raised.exception, ConfigurationError)
        self.assertIsInstance(raised.exception, ValueError)

    def test_unknown_top_level_field_is_rejected(self):
        self.document["unexpected"] = 1
        self._assert_rejects(self.document, "未知字段", "unexpected")

    def test_wrong_kind_is_rejected(self):
        self.document["kind"] = "measurement_result"
        self._assert_rejects(self.document, "kind", MOCK_SCENARIO_KIND)

    def test_unsupported_schema_version_is_rejected(self):
        self.document["schema_version"] = "2.0"
        self._assert_rejects(self.document, "schema_version", "1.0")

    def test_unknown_measurement_type_is_rejected(self):
        self.document["measurement_type"] = "cable_loss_v2"
        self._assert_rejects(self.document, "测量类型", "cable_loss")

    def test_expected_measurement_type_must_match(self):
        with self.assertRaises(MockScenarioError) as raised:
            mock_scenario_from_document(
                self.document,
                expected_measurement_type=MeasurementType.DRIVER_POWER_MAPPING,
            )
        self.assertIn("与目标阶段不一致", str(raised.exception))
        self.assertIn("cable_loss", str(raised.exception))

    def test_missing_phases_is_rejected(self):
        del self.document["phases"]
        self._assert_rejects(self.document, "缺少必需字段", "phases")

    def test_empty_phases_is_rejected(self):
        self.document["phases"] = []
        self._assert_rejects(self.document, "至少包含一个回放阶段")

    def test_first_phase_must_start_automatically(self):
        self.document["phases"] = [
            _confirm_phase(),
            _auto_phase(name="path1", events=_events(percent=70)),
        ]
        self._assert_rejects(self.document, "必须以 auto 开始")

    def test_duplicate_phase_names_are_rejected(self):
        self.document["phases"] = [
            _auto_phase(name="scan"),
            _auto_phase(name="scan", events=_events(percent=60)),
        ]
        self._assert_rejects(self.document, "阶段名重复")

    def test_more_than_one_user_confirm_phase_is_rejected(self):
        self.document["phases"] = [
            _auto_phase(),
            _confirm_phase(name="path2"),
            _confirm_phase(name="path3", events=_events(percent=70)),
        ]
        self._assert_rejects(self.document, "最多只能有一个 user_confirm")

    def test_phase_without_data_event_is_rejected(self):
        self.document["phases"][0]["events"] = [
            {"at_ms": 0, "type": "message", "text": "开始"},
            {"at_ms": 100, "type": "progress", "percent": 10, "stage": "scan"},
        ]
        self._assert_rejects(self.document, "至少需要一个 data 事件")

    def test_empty_phase_events_are_rejected(self):
        self.document["phases"][0]["events"] = []
        self._assert_rejects(self.document, "events 不能为空数组")

    def test_event_at_ms_must_not_decrease(self):
        self.document["phases"][0]["events"][1]["at_ms"] = -1
        self._assert_rejects(self.document, "at_ms", "不能小于")

        self.document = _document()
        self.document["phases"][0]["events"][2]["at_ms"] = 400
        self._assert_rejects(self.document, "at_ms", "非递减")

    def test_unknown_event_type_is_rejected_instead_of_skipped(self):
        self.document["phases"][0]["events"][1]["type"] = "archival"
        self._assert_rejects(
            self.document, "不支持的事件类型", "message、progress、data"
        )

    def test_unknown_event_field_is_rejected(self):
        self.document["phases"][0]["events"][0]["extra"] = True
        self._assert_rejects(self.document, "未知字段", "extra")

    def test_progress_requires_a_valid_percent_and_stage(self):
        self.document["phases"][0]["events"][2]["percent"] = 101
        self._assert_rejects(self.document, "0–100")

        self.document = _document()
        del self.document["phases"][0]["events"][2]["stage"]
        self._assert_rejects(self.document, "缺少必需字段", "stage")

    def test_progress_must_not_go_backwards_across_phases(self):
        self.document["phases"] = [
            _auto_phase(events=_events(percent=60)),
            _confirm_phase(events=_events(percent=10)),
        ]
        self._assert_rejects(self.document, "进度不能倒退")

    def test_data_event_requires_a_frequency_payload(self):
        del self.document["phases"][0]["events"][1]["payload"]["frequency"]
        self._assert_rejects(self.document, "payload.frequency")

        self.document = _document()
        self.document["phases"][0]["events"][1]["payload"]["frequency"] = 0
        self._assert_rejects(self.document, "payload.frequency", "必须大于")

    def test_message_event_requires_non_empty_text(self):
        self.document["phases"][0]["events"][0]["text"] = "   "
        self._assert_rejects(self.document, "text", "不能为空")

    def test_phase_start_rejects_unknown_type_and_missing_fields(self):
        self.document["phases"][1]["start"]["type"] = "manual"
        self._assert_rejects(self.document, "不支持的阶段起点", "manual")

        self.document = _document()
        del self.document["phases"][1]["start"]["prompt"]
        self._assert_rejects(self.document, "缺少必需字段", "prompt")

    def test_user_confirm_actions_are_fixed_by_the_replay_contract(self):
        self.document["phases"][1]["start"]["signal"] = "pause"
        self._assert_rejects(self.document, "step_pause")

        self.document = _document()
        self.document["phases"][1]["start"]["accepted"] = "skip"
        self._assert_rejects(self.document, "continue_stage2")

        self.document = _document()
        self.document["phases"][1]["start"]["rejected"] = "continue"
        self._assert_rejects(self.document, "stop")

    def test_completion_requires_result_and_finished(self):
        self.document["completion"] = [
            event for event in _completion() if event["type"] != "result"
        ]
        self._assert_rejects(self.document, "只包含一个 result")

        self.document = _document()
        self.document["completion"] = [
            event for event in _completion() if event["type"] != "finished"
        ]
        self._assert_rejects(self.document, "只包含一个 finished")

    def test_result_completion_requires_a_file_name(self):
        del self.document["completion"][0]["file"]
        self._assert_rejects(self.document, "缺少必需字段", "file")

        self.document = _document()
        self.document["completion"][0]["file"] = ""
        self._assert_rejects(self.document, "file", "不能为空")

    def test_result_completion_rejects_path_traversal(self):
        invalid_names = (
            "../evil.json",
            "..\\evil.json",
            "subdir/result.json",
            "C:\\result.json",
            ".",
            "..",
            "result.json\0",
            " result.json",
            "result.json.",
            "CON.json",
        )
        for filename in invalid_names:
            with self.subTest(filename=filename):
                self.document = _document()
                self.document["completion"][0]["file"] = filename
                self._assert_rejects(self.document, "file")

    def test_result_completion_accepts_a_plain_file_name(self):
        self.document["completion"][0]["file"] = "console.json"
        scenario = mock_scenario_from_document(self.document)
        self.assertEqual(scenario.result_filename, "console.json")

    def test_completion_order_and_counts_are_fixed(self):
        self.document["completion"] = [
            {"type": "message", "text": "完成"},
            {"type": "result", "file": "result.json"},
            {"type": "finished"},
        ]
        self._assert_rejects(
            self.document, "顺序必须是", "result", "finished"
        )

        self.document = _document()
        self.document["completion"].append({"type": "finished"})
        self._assert_rejects(self.document, "只包含一个 finished")

        self.document = _document()
        self.document["completion"].insert(
            2,
            {
                "type": "archival",
                "outcome": {"stage": "cable_loss", "test_status": "partial"},
            },
        )
        self._assert_rejects(self.document, "最多只能有一个 archival")

        self.document = _document()
        self.document["completion"][1]["type"] = "unknown"
        self._assert_rejects(self.document, "不支持的完成事件类型")

    def test_archival_outcome_must_carry_stage_and_status(self):
        self.document["completion"][1]["outcome"] = {"stage": "cable_loss"}
        self._assert_rejects(
            self.document, "archival.outcome 缺少必需字段", "test_status"
        )

    def test_source_config_requires_the_shared_facts(self):
        for field in (
            "template",
            "test_frequencies_ghz",
            "attenuator_db",
            "driver_enabled",
            "driver_estimated_gain_db",
            "driver_max_input_power_dbm",
            "dut_max_input_power_dbm",
            "power_scan_plans",
        ):
            with self.subTest(field=field):
                document = _document()
                del document["source_config"][field]
                self._assert_rejects(
                    document, "source_config 缺少必需字段", field
                )

    def test_source_config_rejects_invalid_values(self):
        self.document["source_config"]["test_frequencies_ghz"] = [1.1, 1.0]
        self._assert_rejects(self.document, "严格递增")

        self.document = _document()
        self.document["source_config"]["attenuator_db"] = -1
        self._assert_rejects(self.document, "attenuator_db", "不能小于")

        self.document = _document()
        self.document["source_config"]["driver_enabled"] = "yes"
        self._assert_rejects(self.document, "driver_enabled", "布尔值")

        self.document = _document()
        self.document["source_config"]["power_scan_plans"][0]["step"] = 0
        self._assert_rejects(self.document, "step", "必须大于")

        self.document = _document()
        self.document["source_config"]["power_scan_plans"][0][
            "stop_power"
        ] = -30.0
        self._assert_rejects(self.document, "stop_power", "不能低于")

    def test_source_config_rejects_conflicting_compression_keys(self):
        self.document["source_config"]["compression_point"] = "3dB"
        self._assert_rejects(self.document, "不一致", "5dB", "3dB")

    def test_source_config_rejects_unknown_or_misspelled_fields(self):
        self.document["source_config"]["compression_typ"] = "5dB"
        self._assert_rejects(
            self.document, "未知字段", "compression_typ"
        )

        self.document = _document()
        self.document["source_config"]["unexpected"] = True
        self._assert_rejects(self.document, "未知字段", "unexpected")

    def test_source_config_calibration_references_must_stay_under_mock_data(self):
        invalid = (
            "../../evil.json",
            "..\\evil.json",
            "/mock_data/cable_loss/result.json",
            "C:/mock_data/cable_loss/result.json",
            "mock_data/driver_mapping/result.json",
            "mock_data/cable_loss/subdir/result.json",
            "mock_data/cable_loss/../result.json",
            "mock_data/cable_loss/",
        )
        for value in invalid:
            with self.subTest(value=value):
                self.document = _document()
                self.document["source_config"]["cable_loss_result"] = value
                self._assert_rejects(
                    self.document, "cable_loss_result", "mock_data/cable_loss"
                )

        driver_invalid = tuple(
            value.replace("cable_loss", "driver_mapping")
            for value in invalid
            if value != "mock_data/driver_mapping/result.json"
        ) + ("mock_data/cable_loss/result.json",)
        for value in driver_invalid:
            with self.subTest(field="driver_mapping_result", value=value):
                self.document = _document()
                self.document["source_config"]["driver_mapping_result"] = value
                self._assert_rejects(
                    self.document,
                    "driver_mapping_result",
                    "mock_data/driver_mapping",
                )

    def test_source_config_calibration_references_accept_expected_paths(self):
        self.document["source_config"]["cable_loss_result"] = (
            "mock_data/cable_loss/result.json"
        )
        self.document["source_config"]["driver_mapping_result"] = (
            "mock_data/driver_mapping/result.json"
        )
        scenario = mock_scenario_from_document(self.document)
        self.assertEqual(
            scenario.source_config.cable_loss_result,
            "mock_data/cable_loss/result.json",
        )
        self.assertEqual(
            scenario.source_config.driver_mapping_result,
            "mock_data/driver_mapping/result.json",
        )

    def test_numbers_too_large_for_float_are_contract_errors(self):
        self.document["source_config"]["attenuator_db"] = 10**400
        self._assert_rejects(self.document, "attenuator_db", "有限数字")

        self.document = _document()
        self.document["source_config"]["test_frequencies_ghz"][0] = 10**400
        self._assert_rejects(
            self.document, "test_frequencies_ghz", "有限数字"
        )

        self.document = _document()
        self.document["source_config"]["power_scan_plans"][0]["step"] = 10**400
        self._assert_rejects(self.document, "step", "有限数字")

    def test_source_config_validates_the_optional_grid_and_range(self):
        self.document["source_config"]["target_input_powers_dbm"] = [0.0, -1.0]
        self._assert_rejects(self.document, "target_input_powers_dbm", "严格递增")

        self.document = _document()
        self.document["source_config"]["signal_source_power_range_dbm"] = [
            20.0,
            -100.0,
        ]
        self._assert_rejects(self.document, "最大值必须大于最小值")

        self.document = _document()
        self.document["source_config"]["signal_source_power_range_dbm"] = [1.0]
        self._assert_rejects(self.document, "2 个值")

    def test_source_config_must_be_an_object(self):
        self.document["source_config"] = []
        self._assert_rejects(self.document, "source_config", "必须是 JSON 对象")

    def test_run_identity_and_time_are_validated(self):
        self.document["run"]["run_id"] = "../outside"
        self._assert_rejects(self.document, "run_id 非法")

        self.document = _document()
        self.document["run"]["started_at"] = "yesterday"
        self._assert_rejects(self.document, "ISO 8601")

        self.document = _document()
        del self.document["run"]["mode"]
        self._assert_rejects(self.document, "缺少必需字段", "mode")

    def test_started_at_requires_a_timezone(self):
        for value in ("2026-11-17", "2026-11-17T06:02:00"):
            with self.subTest(value=value):
                self.document = _document()
                self.document["run"]["started_at"] = value
                self._assert_rejects(self.document, "时区")

    def test_title_and_description_types_are_validated(self):
        self.document["title"] = "  "
        self._assert_rejects(self.document, "title", "不能为空")

        self.document = _document()
        self.document["description"] = None
        self._assert_rejects(self.document, "description", "必须是字符串")


class MockScenarioConstructionTests(unittest.TestCase):
    def test_scenario_error_belongs_to_the_configuration_category(self):
        self.assertTrue(issubclass(MockScenarioError, ConfigurationError))
        self.assertTrue(issubclass(MockScenarioError, ValueError))

    def test_phase_start_and_completion_types_are_public_enums(self):
        self.assertEqual(MockPhaseStartType.AUTO.value, "auto")
        self.assertEqual(MockPhaseStartType.USER_CONFIRM.value, "user_confirm")

    def test_minimal_document_exposes_confirm_and_result_helpers(self):
        scenario = mock_scenario_from_document(_document())
        self.assertEqual(scenario.user_confirm_phase.name, "path2")
        self.assertEqual(scenario.result_filename, "result.json")
        self.assertEqual(scenario.data_event_count, 2)
        self.assertIsInstance(
            scenario.phases[0].events[1], MockDataEvent
        )
        self.assertIsInstance(
            scenario.phases[0].events[2], MockProgressEvent
        )

    def test_scenario_exposes_its_declared_kind(self):
        scenario = mock_scenario_from_document(_document())
        self.assertEqual(scenario.kind, MOCK_SCENARIO_KIND)

    def test_contract_is_exported_from_the_dto_package(self):
        from application.dto import (
            MockScenario as ExportedScenario,
            MockScenarioError as ExportedError,
            mock_scenario_from_document as exported_parser,
        )

        self.assertIs(ExportedScenario, MockScenario)
        self.assertIs(ExportedError, MockScenarioError)
        self.assertIs(exported_parser, mock_scenario_from_document)

    def test_phase_rejects_base_start_and_base_event_types(self):
        with self.assertRaises(MockScenarioError):
            MockPhase(
                name="path",
                title="路径",
                start=MockPhaseStart(),
                events=(
                    MockDataEvent(
                        at_ms=0, payload={"frequency": 1.0}
                    ),
                ),
            )

        with self.assertRaises(MockScenarioError):
            MockPhase(
                name="path",
                title="路径",
                start=MockAutoPhaseStart(),
                events=(
                    MockScenarioEvent(at_ms=0),
                    MockDataEvent(
                        at_ms=1, payload={"frequency": 1.0}
                    ),
                ),
            )

    def test_scenario_rejects_base_completion_and_non_sequence_values(self):
        scenario = mock_scenario_from_document(_document())
        with self.assertRaises(MockScenarioError):
            MockScenario(
                schema_version=scenario.schema_version,
                measurement_type=scenario.measurement_type,
                title=scenario.title,
                description=scenario.description,
                source_config=scenario.source_config,
                run=scenario.run,
                phases=scenario.phases,
                completion=(MockCompletionEvent(),),
            )

        with self.assertRaises(MockScenarioError):
            MockScenario(
                schema_version=scenario.schema_version,
                measurement_type=scenario.measurement_type,
                title=scenario.title,
                description=scenario.description,
                source_config=scenario.source_config,
                run=scenario.run,
                phases=None,
                completion=scenario.completion,
            )

        with self.assertRaises(MockScenarioError):
            MockScenario(
                schema_version=scenario.schema_version,
                measurement_type=scenario.measurement_type,
                title=scenario.title,
                description=scenario.description,
                source_config=scenario.source_config,
                run=scenario.run,
                phases=scenario.phases,
                completion=None,
            )


if __name__ == "__main__":
    unittest.main()
