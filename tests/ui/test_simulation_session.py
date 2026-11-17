"""阶段二：Mock 模拟会话状态模型与校准来源绑定。

会话只接受已加载的场景与已解析的路径；本文件用仓库跟踪的线损场景作为基线，再用
``dataclasses.replace`` 派生驱放/主功放阶段，不复制大 JSON，也不打开真实仪器。
"""

import unittest
from dataclasses import replace
from pathlib import Path

from application.dto.measurement_results import MeasurementType
from application.dto.mock_scenario import MockResultCompletion
from application.errors import ConfigurationError
from application.inputs.mock_scenario import load_mock_scenario
from application.mock_configuration_matcher import MockConfigurationMatch
from presentation.qt.simulation_session import (
    SimulationCalibrationInput,
    SimulationSession,
    SimulationStage,
    build_simulation_session,
)

PROJECT_ROOT = Path(__file__).parents[2]
CABLE_SCENARIO = PROJECT_ROOT / "mock_data" / "cable_loss" / "scenario.json"
CABLE_RESULT = "20261117T060245Z_9b2e7d10.json"
DRIVER_RESULT = "20261117T060600Z_5c1d9e42.json"


class _Paths:
    """Mock 路径服务替身：路径按阶段目录派生，记录被询问过的引用。"""

    _DIRECTORIES = {"driver_power_mapping": "driver_mapping"}

    def __init__(self, root=Path("mock_data")):
        self.root = root
        self.references = []
        self.results = {}
        self.missing_reference = None

    def _directory(self, measurement_type):
        return self._DIRECTORIES.get(measurement_type, measurement_type)

    def require_result_path_for(self, measurement_type, filename):
        return self.results.get(
            measurement_type,
            self.root / self._directory(measurement_type) / filename,
        )

    def require_scenario_path_for(self, measurement_type):
        return self.root / self._directory(measurement_type) / "scenario.json"

    def require_reference_path(self, reference):
        self.references.append(reference)
        if reference == self.missing_reference:
            raise ConfigurationError(f"Mock 校准结果不存在: {reference}")
        _mock_data, stage, filename = reference.split("/")
        return self.root / stage / filename


def _cable_scenario():
    return load_mock_scenario(CABLE_SCENARIO)


def _derived_scenario(
    measurement_type,
    *,
    cable_loss=None,
    driver_mapping=None,
    result_file=None,
):
    base = _cable_scenario()
    source = replace(
        base.source_config,
        cable_loss_result=cable_loss,
        driver_mapping_result=driver_mapping,
    )
    completion = base.completion
    if result_file is not None:
        completion = tuple(
            replace(event, file=result_file)
            if isinstance(event, MockResultCompletion)
            else event
            for event in completion
        )
    return replace(
        base,
        measurement_type=measurement_type,
        source_config=source,
        completion=completion,
    )


def _match(*scenarios):
    return MockConfigurationMatch(
        snapshot_name="Mock", scenarios=tuple(scenarios)
    )


class SimulationSessionTests(unittest.TestCase):
    def setUp(self):
        self.paths = _Paths()
        self.cable = _cable_scenario()

    def _session(self, *scenarios):
        return build_simulation_session(_match(*scenarios), mock_paths=self.paths)

    def test_cable_loss_session_has_no_calibration_input(self):
        session = self._session(self.cable)

        self.assertEqual(session.snapshot_name, "Mock")
        self.assertEqual(
            session.measurement_types, (MeasurementType.CABLE_LOSS,)
        )
        stage = session.stage_for(MeasurementType.CABLE_LOSS)
        self.assertIs(stage.scenario, self.cable)
        self.assertEqual(stage.result_path, self.paths.root / "cable_loss" / CABLE_RESULT)
        self.assertEqual(stage.title, self.cable.title)
        self.assertEqual(session.calibration_inputs_for("cable_loss"), ())
        self.assertTrue(session.has_stage("cable_loss"))
        self.assertFalse(session.has_stage("amplifier"))
        self.assertFalse(session.has_stage(object()))

    def test_driver_stage_auto_binds_the_cable_loss_result(self):
        driver = _derived_scenario(
            MeasurementType.DRIVER_POWER_MAPPING,
            cable_loss="mock_data/cable_loss/20261117T060245Z_9b2e7d10.json",
        )

        session = self._session(self.cable, driver)

        self.assertEqual(
            session.measurement_types,
            (MeasurementType.CABLE_LOSS, MeasurementType.DRIVER_POWER_MAPPING),
        )
        inputs = session.calibration_inputs_for(MeasurementType.DRIVER_POWER_MAPPING)
        self.assertEqual(len(inputs), 1)
        self.assertIsInstance(inputs[0], SimulationCalibrationInput)
        self.assertIs(inputs[0].measurement_type, MeasurementType.CABLE_LOSS)
        self.assertEqual(
            inputs[0].path, self.paths.root / "cable_loss" / CABLE_RESULT
        )
        self.assertEqual(
            self.paths.references,
            ["mock_data/cable_loss/20261117T060245Z_9b2e7d10.json"],
        )

    def test_amplifier_stage_binds_both_upstream_results(self):
        driver = _derived_scenario(
            MeasurementType.DRIVER_POWER_MAPPING,
            cable_loss="mock_data/cable_loss/20261117T060245Z_9b2e7d10.json",
            result_file=DRIVER_RESULT,
        )
        amplifier = _derived_scenario(
            MeasurementType.AMPLIFIER,
            cable_loss="mock_data/cable_loss/20261117T060245Z_9b2e7d10.json",
            driver_mapping="mock_data/driver_mapping/20261117T060600Z_5c1d9e42.json",
        )

        session = self._session(self.cable, driver, amplifier)

        inputs = session.calibration_inputs_for(MeasurementType.AMPLIFIER)
        self.assertEqual(
            [item.measurement_type for item in inputs],
            [MeasurementType.CABLE_LOSS, MeasurementType.DRIVER_POWER_MAPPING],
        )
        self.assertEqual(inputs[1].path.name, DRIVER_RESULT)

    def test_driver_stage_without_calibration_reference_is_rejected(self):
        driver = _derived_scenario(MeasurementType.DRIVER_POWER_MAPPING)

        with self.assertRaisesRegex(ConfigurationError, "缺少 cable_loss_result"):
            self._session(driver)

    def test_cable_stage_cannot_declare_an_upstream_reference(self):
        cable = _derived_scenario(
            MeasurementType.CABLE_LOSS,
            cable_loss="mock_data/cable_loss/20261117T060245Z_9b2e7d10.json",
        )

        with self.assertRaisesRegex(ConfigurationError, "不能声明校准结果引用"):
            self._session(cable)

    def test_driver_stage_cannot_declare_a_driver_mapping_reference(self):
        driver = _derived_scenario(
            MeasurementType.DRIVER_POWER_MAPPING,
            cable_loss="mock_data/cable_loss/20261117T060245Z_9b2e7d10.json",
            driver_mapping="mock_data/driver_mapping/20261117T060600Z_5c1d9e42.json",
        )

        with self.assertRaisesRegex(ConfigurationError, "driver_mapping_result"):
            self._session(self.cable, driver)

    def test_amplifier_stage_requires_both_upstream_references(self):
        amplifier = _derived_scenario(
            MeasurementType.AMPLIFIER,
            cable_loss="mock_data/cable_loss/20261117T060245Z_9b2e7d10.json",
        )

        with self.assertRaisesRegex(ConfigurationError, "缺少 driver_mapping_result"):
            self._session(self.cable, amplifier)

    def test_reference_to_a_stage_missing_from_the_package_is_rejected(self):
        driver = _derived_scenario(
            MeasurementType.DRIVER_POWER_MAPPING,
            cable_loss="mock_data/cable_loss/20261117T060245Z_9b2e7d10.json",
        )

        with self.assertRaisesRegex(ConfigurationError, "不包含该阶段"):
            self._session(driver)

    def test_reference_to_another_result_is_rejected(self):
        driver = _derived_scenario(
            MeasurementType.DRIVER_POWER_MAPPING,
            cable_loss="mock_data/cable_loss/another.json",
        )

        with self.assertRaisesRegex(ConfigurationError, "Mock 数据包不一致"):
            self._session(self.cable, driver)

    def test_missing_reference_file_propagates_as_configuration_error(self):
        reference = "mock_data/cable_loss/20261117T060245Z_9b2e7d10.json"
        self.paths.missing_reference = reference
        driver = _derived_scenario(
            MeasurementType.DRIVER_POWER_MAPPING, cable_loss=reference
        )

        with self.assertRaisesRegex(ConfigurationError, "不存在"):
            self._session(self.cable, driver)

    def test_missing_stage_is_reported_with_available_stages(self):
        session = self._session(self.cable)

        with self.assertRaisesRegex(ConfigurationError, "cable_loss"):
            session.stage_for(MeasurementType.AMPLIFIER)

    def test_builder_requires_a_match_and_path_service(self):
        with self.assertRaises(ConfigurationError):
            build_simulation_session(self.cable, mock_paths=self.paths)
        with self.assertRaisesRegex(ConfigurationError, "未装配"):
            build_simulation_session(_match(self.cable), mock_paths=None)

    def test_session_index_is_immutable_and_validates_its_entries(self):
        stage = SimulationStage(
            measurement_type=MeasurementType.CABLE_LOSS,
            scenario=self.cable,
            scenario_path=Path("mock_data/cable_loss/scenario.json"),
            result_path=Path("mock_data/cable_loss/result.json"),
        )
        session = SimulationSession(
            snapshot_name=" Mock ", stages={MeasurementType.CABLE_LOSS: stage}
        )

        self.assertEqual(session.snapshot_name, "Mock")
        with self.assertRaises(TypeError):
            session.stages[MeasurementType.CABLE_LOSS] = stage  # type: ignore[index]
        with self.assertRaises(ConfigurationError):
            SimulationSession(snapshot_name="", stages={})
        with self.assertRaises(ConfigurationError):
            SimulationSession(
                snapshot_name="Mock",
                stages={MeasurementType.CABLE_LOSS: "not-a-stage"},
            )
        with self.assertRaisesRegex(ConfigurationError, "不一致"):
            SimulationSession(
                snapshot_name="Mock",
                stages={MeasurementType.AMPLIFIER: stage},
            )


if __name__ == "__main__":
    unittest.main()
