"""阶段五步骤 15：``mock_data`` 数据包的一致性、结果对应与配置自洽。

三套仓库场景、三份正式结果和 ``config.mock.json`` 必须彼此自洽：频点覆盖、事件
数量、目标功率网格、校准引用和结果 DTO 类型都不能只靠人工检查。场景现在是仿真运行
的 ``readings.json`` 来源与期望输出，不再有 UI 回放预算。

本模块只读仓库数据，不复制大 JSON，也不生成新的正式结果文件。
"""

import json
import unittest
from collections import Counter
from pathlib import Path

from application.dto.measurement_results import (
    AmplifierMeasurementResult,
    CableLossMeasurementResult,
    DriverPowerMappingMeasurementResult,
    MeasurementType,
)
from application.dto.mock_scenario import (
    MockDataEvent,
    MockMessageEvent,
    MockProgressEvent,
)
from application.inputs.mock_result import load_mock_result
from application.inputs.mock_scenario import load_mock_scenario
from application.ports.stage_result_repository import measurement_type_for_stage
from infrastructure.filesystem.paths import DEFAULT_MOCK_DATA_PATHS, MockStage

PROJECT_ROOT = Path(__file__).parents[1]
MOCK_CONFIG = PROJECT_ROOT / "config.mock.json"
CONFIGURED_FREQUENCIES = [round(1.0 + index * 0.1, 1) for index in range(11)]

#: README 记录的事件规模；场景数据变化时这里必须同步复核，不能静默漂移。
EXPECTED_EVENT_COUNTS = {
    MockStage.CABLE_LOSS: {"data": 22, "progress": 23, "message": 1, "total": 46},
    MockStage.DRIVER_MAPPING: {
        "data": 363,
        "progress": 704,
        "message": 1,
        "total": 1068,
    },
    MockStage.AMPLIFIER: {"data": 312, "progress": 313, "message": 1, "total": 626},
}

#: 每个频点包含的数据点数（线损按两条路径分别计数）。
EXPECTED_PER_FREQUENCY = {
    # 线损每个频点在路径 1、路径 2 各有一个事件。
    MockStage.CABLE_LOSS: {frequency: 2 for frequency in CONFIGURED_FREQUENCIES},
    MockStage.DRIVER_MAPPING: {frequency: 33 for frequency in CONFIGURED_FREQUENCIES},
    MockStage.AMPLIFIER: {
        1.0: 29,
        1.1: 29,
        1.2: 29,
        1.3: 29,
        1.4: 29,
        1.5: 28,
        1.6: 28,
        1.7: 28,
        1.8: 28,
        1.9: 28,
        2.0: 27,
    },
}

RESULT_TYPES = {
    MockStage.CABLE_LOSS: (CableLossMeasurementResult, MeasurementType.CABLE_LOSS),
    MockStage.DRIVER_MAPPING: (
        DriverPowerMappingMeasurementResult,
        MeasurementType.DRIVER_POWER_MAPPING,
    ),
    MockStage.AMPLIFIER: (AmplifierMeasurementResult, MeasurementType.AMPLIFIER),
}


def _data_events(scenario):
    return [
        event
        for phase in scenario.phases
        for event in phase.events
        if isinstance(event, MockDataEvent)
    ]


def _frequency_of(event):
    return float(event.payload["frequency"])


class MockDataPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scenarios = {}
        cls.results = {}
        for stage in MockStage:
            scenario = load_mock_scenario(
                DEFAULT_MOCK_DATA_PATHS.require_scenario_path(stage),
                expected_measurement_type=measurement_type_for_stage(stage.value),
            )
            cls.scenarios[stage] = scenario
            cls.results[stage] = load_mock_result(
                DEFAULT_MOCK_DATA_PATHS.require_result_path(
                    stage, scenario.result_filename
                ),
                expected_measurement_type=measurement_type_for_stage(
                    stage.value
                ),
            )
        cls.configuration = json.loads(
            MOCK_CONFIG.read_text(encoding="utf-8-sig")
        )

    def test_event_counts_match_the_documented_replay_scale(self):
        for stage, scenario in self.scenarios.items():
            with self.subTest(stage=stage.value):
                events = [
                    event for phase in scenario.phases for event in phase.events
                ]
                actual = {
                    "data": sum(
                        isinstance(event, MockDataEvent) for event in events
                    ),
                    "progress": sum(
                        isinstance(event, MockProgressEvent) for event in events
                    ),
                    "message": sum(
                        isinstance(event, MockMessageEvent) for event in events
                    ),
                    "total": len(events),
                }
                self.assertEqual(actual, EXPECTED_EVENT_COUNTS[stage])
                self.assertEqual(actual["data"], scenario.data_event_count)

    def test_data_events_cover_every_configured_frequency(self):
        for stage, scenario in self.scenarios.items():
            with self.subTest(stage=stage.value):
                source = scenario.source_config
                self.assertEqual(
                    list(source.test_frequencies_ghz), CONFIGURED_FREQUENCIES
                )
                data = _data_events(scenario)
                frequencies = [_frequency_of(event) for event in data]
                self.assertEqual(
                    sorted(set(frequencies)), CONFIGURED_FREQUENCIES
                )
                per_frequency = Counter(frequencies)
                self.assertEqual(
                    dict(per_frequency), EXPECTED_PER_FREQUENCY[stage]
                )

                if stage is MockStage.CABLE_LOSS:
                    # 两条路径各自完整扫过 11 个频点，顺序与配置一致。
                    for phase in scenario.phases:
                        phase_frequencies = [
                            _frequency_of(event)
                            for event in phase.events
                            if isinstance(event, MockDataEvent)
                        ]
                        self.assertEqual(
                            phase_frequencies, CONFIGURED_FREQUENCIES
                        )
                    path2 = [
                        event
                        for event in data
                        if event.payload.get("path") == 2
                    ]
                    self.assertEqual(
                        len(path2), len(CONFIGURED_FREQUENCIES)
                    )

    def test_result_files_declare_the_expected_formal_types(self):
        for stage, scenario in self.scenarios.items():
            with self.subTest(stage=stage.value):
                result_type, measurement_type = RESULT_TYPES[stage]
                result = self.results[stage]
                self.assertIsInstance(result, result_type)
                self.assertIs(result.measurement_type, measurement_type)
                self.assertTrue(result.run_id)
                self.assertTrue(result.is_formal_result)
                self.assertEqual(
                    result.to_dict().get("measurement_type"),
                    measurement_type.value,
                )

    def test_cable_loss_result_matches_the_final_path2_payload(self):
        result = self.results[MockStage.CABLE_LOSS]
        self.assertEqual(len(result.points), len(CONFIGURED_FREQUENCIES))
        self.assertEqual(
            [point.frequency_ghz for point in result.points],
            [str(frequency) for frequency in CONFIGURED_FREQUENCIES],
        )

        final_payload = {}
        for event in _data_events(self.scenarios[MockStage.CABLE_LOSS]):
            if event.payload.get("path") == 2:
                final_payload[_frequency_of(event)] = event.payload["cable_losses"]
        self.assertEqual(
            sorted(final_payload), CONFIGURED_FREQUENCIES
        )

        for point in result.points:
            expected = final_payload[float(point.frequency_ghz)]
            actual = point.cable_losses.to_mapping()
            with self.subTest(frequency=point.frequency_ghz):
                self.assertEqual(set(actual), set(expected))
                for key, value in expected.items():
                    self.assertAlmostEqual(
                        actual[key], float(value), places=12
                    )

    def test_driver_mapping_result_matches_the_target_grid_and_replay_curve(self):
        stage = MockStage.DRIVER_MAPPING
        result = self.results[stage]
        scenario = self.scenarios[stage]
        source = scenario.source_config

        self.assertEqual(len(result.raw_points), 363)
        self.assertEqual(len(result.target_points), 341)
        self.assertEqual(
            Counter(float(point.frequency_ghz) for point in result.raw_points),
            Counter(
                {
                    frequency: count
                    for frequency, count in EXPECTED_PER_FREQUENCY[stage].items()
                }
            ),
        )
        self.assertEqual(
            sorted({float(point.frequency_ghz) for point in result.raw_points}),
            CONFIGURED_FREQUENCIES,
        )

        expected_grid = list(source.target_input_powers_dbm)
        self.assertEqual(len(expected_grid), 31)
        for frequency in CONFIGURED_FREQUENCIES:
            targets = [
                point.target_input_power_dbm
                for point in result.target_points
                if float(point.frequency_ghz) == frequency
            ]
            with self.subTest(frequency=frequency):
                self.assertEqual(targets, expected_grid)

        # 每个频点最后一个回放事件携带 33 个原始探测点（锚点 + 目标点 + 搜索点）。
        latest_by_frequency = {}
        for event in _data_events(scenario):
            latest_by_frequency[_frequency_of(event)] = event.payload[
                "sweep_data"
            ]
        self.assertEqual(
            sorted(latest_by_frequency), CONFIGURED_FREQUENCIES
        )
        for frequency, sweep_data in latest_by_frequency.items():
            with self.subTest(frequency=frequency):
                self.assertEqual(
                    len(sweep_data["input_power_sg"]),
                    EXPECTED_PER_FREQUENCY[stage][frequency],
                )

    def test_amplifier_result_matches_each_replayed_sweep(self):
        stage = MockStage.AMPLIFIER
        result = self.results[stage]
        scenario = self.scenarios[stage]

        self.assertEqual(
            [float(item.frequency_ghz) for item in result.frequencies],
            CONFIGURED_FREQUENCIES,
        )
        self.assertEqual(result.point_count, sum(
            EXPECTED_PER_FREQUENCY[stage].values()
        ))
        self.assertEqual(result.point_count, 312)

        latest_by_frequency = {}
        for event in _data_events(scenario):
            latest_by_frequency[_frequency_of(event)] = event.payload[
                "sweep_data"
            ]

        total_rows = 0
        for item in result.frequencies:
            frequency = float(item.frequency_ghz)
            sweep = item.sweep_data
            expected_count = EXPECTED_PER_FREQUENCY[stage][frequency]
            with self.subTest(frequency=frequency):
                self.assertTrue(item.compression_achieved)
                self.assertEqual(item.compression_type, "5dB")
                self.assertEqual(
                    len(sweep["input_power_dut"]), expected_count
                )
                self.assertEqual(
                    len(sweep["output_power_dut"]), expected_count
                )
                self.assertEqual(len(sweep["voltages"]), expected_count)
                self.assertEqual(len(sweep["currents"]), expected_count)
                self.assertEqual(
                    sweep["input_power_dut"],
                    latest_by_frequency[frequency]["input_power_dut"],
                )
                total_rows += expected_count
        self.assertEqual(total_rows, 312)

    def test_stage_source_facts_are_consistent_across_the_package(self):
        shared = (
            "template",
            "test_frequencies_ghz",
            "attenuator_db",
            "driver_enabled",
            "driver_estimated_gain_db",
            "driver_max_input_power_dbm",
            "dut_max_input_power_dbm",
            "power_scan_plans",
        )
        cable = self.scenarios[MockStage.CABLE_LOSS].source_config
        driver = self.scenarios[MockStage.DRIVER_MAPPING].source_config
        amplifier = self.scenarios[MockStage.AMPLIFIER].source_config
        for field in shared:
            with self.subTest(field=field):
                value = getattr(cable, field)
                self.assertEqual(getattr(driver, field), value)
                self.assertEqual(getattr(amplifier, field), value)

        self.assertEqual(driver.target_input_powers_dbm, amplifier.target_input_powers_dbm)
        self.assertEqual(len(driver.target_input_powers_dbm), 31)
        self.assertEqual(driver.target_input_powers_dbm[0], -20.0)
        self.assertEqual(driver.target_input_powers_dbm[-1], 5.0)

        self.assertEqual(cable.compression_point, "5dB")
        self.assertIsNone(driver.compression_point)
        self.assertEqual(amplifier.compression_point, "5dB")

        # 正式结果的频点来自同一份配置摘要，而不是各文件各写一份。
        for stage, result in self.results.items():
            with self.subTest(stage=stage.value):
                if isinstance(result, AmplifierMeasurementResult):
                    frequencies = [
                        float(item.frequency_ghz)
                        for item in result.frequencies
                    ]
                elif isinstance(result, DriverPowerMappingMeasurementResult):
                    frequencies = sorted(
                        {
                            float(point.frequency_ghz)
                            for point in result.raw_points
                        }
                    )
                else:
                    frequencies = [
                        float(point.frequency_ghz)
                        for point in result.points
                    ]
                self.assertEqual(frequencies, CONFIGURED_FREQUENCIES)

    def test_calibration_references_point_at_the_upstream_mock_results(self):
        cable = self.scenarios[MockStage.CABLE_LOSS]
        driver = self.scenarios[MockStage.DRIVER_MAPPING]
        amplifier = self.scenarios[MockStage.AMPLIFIER]
        cable_reference = f"mock_data/cable_loss/{cable.result_filename}"
        driver_reference = f"mock_data/driver_mapping/{driver.result_filename}"

        self.assertIsNone(cable.source_config.cable_loss_result)
        self.assertEqual(driver.source_config.cable_loss_result, cable_reference)
        self.assertEqual(amplifier.source_config.cable_loss_result, cable_reference)
        self.assertEqual(
            amplifier.source_config.driver_mapping_result, driver_reference
        )

        for reference, expected_type in (
            (cable_reference, MeasurementType.CABLE_LOSS),
            (driver_reference, MeasurementType.DRIVER_POWER_MAPPING),
        ):
            with self.subTest(reference=reference):
                path = DEFAULT_MOCK_DATA_PATHS.require_reference_path(reference)
                self.assertTrue(path.is_file())
                result = load_mock_result(
                    path, expected_measurement_type=expected_type
                )
                self.assertIs(result.measurement_type, expected_type)

    def test_repository_configuration_document_matches_the_scenario_facts(self):
        """``config.mock.json`` 仍是三套场景共同的配置来源，不只存在于 README。"""
        test_plan = self.configuration["test_plan"]
        self.assertEqual(
            [round(float(value), 1) for value in test_plan["frequencies"]["values"]],
            CONFIGURED_FREQUENCIES,
        )
        self.assertEqual(test_plan["frequencies"]["unit"], "GHz")
        driver = test_plan["driver_mode"]
        self.assertEqual(driver["enabled"], True)
        self.assertEqual(driver["estimated_gain_db"], 40.0)
        self.assertEqual(driver["max_input_power"], 0.0)
        self.assertEqual(test_plan["dut"]["max_input_power"], 6.0)
        self.assertEqual(test_plan["attenuator"]["value"], 40.0)
        self.assertEqual(test_plan["compression_point"]["type"], "5dB")


if __name__ == "__main__":
    unittest.main()
