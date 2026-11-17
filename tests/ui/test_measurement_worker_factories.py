"""worker 工厂：把页面冻结的显式校准输入来源交给对应 worker。"""

import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from application.dto.measurement_requests import StageResultDigest
from application.dto.measurement_results import MeasurementType
from application.errors import ConfigurationError
from application.inputs.mock_scenario import load_mock_scenario
from presentation.qt.measurement_controller_contract import (
    MeasurementCommand,
    MeasurementExecutionMode,
)
from presentation.qt.measurement_state import MeasurementKind
from presentation.qt.measurement_worker_factories import (
    build_measurement_worker_factories,
    build_simulation_worker_factories,
)
from presentation.qt.mock_replay_worker import MockReplayWorker
from presentation.qt.simulation_session import SimulationSession, SimulationStage

PROJECT_ROOT = Path(__file__).parents[2]
_STAGE_DIRECTORIES = {
    MeasurementType.CABLE_LOSS: "cable_loss",
    MeasurementType.DRIVER_POWER_MAPPING: "driver_mapping",
    MeasurementType.AMPLIFIER: "amplifier",
}


def _simulation_stage(measurement_type):
    directory = _STAGE_DIRECTORIES[measurement_type]
    scenario_path = PROJECT_ROOT / "mock_data" / directory / "scenario.json"
    scenario = load_mock_scenario(
        scenario_path, expected_measurement_type=measurement_type
    )
    return SimulationStage(
        measurement_type=measurement_type,
        scenario=scenario,
        scenario_path=scenario_path,
        result_path=(
            PROJECT_ROOT / "mock_data" / directory / scenario.result_filename
        ),
    )


def _simulation_session(*measurement_types):
    stages = {
        measurement_type: _simulation_stage(measurement_type)
        for measurement_type in measurement_types
    }
    return SimulationSession(snapshot_name="Mock", stages=stages)


def _simulation_command(session):
    return MeasurementCommand(
        "config.json",
        mode=MeasurementExecutionMode.SIMULATION,
        simulation_session=session,
    )


def _prepared_run():
    return type(
        "PreparedRun",
        (),
        {
            "run_id": "run-factory",
            "run_directory": "runs/run-factory",
            "run_repository": object(),
            "hardware_connection_path": lambda self: None,
        },
    )()


class WorkerFactoryCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def test_driver_mapping_factory_forwards_the_cable_loss_path(self):
        cable = Path("test_results") / "cable_loss" / "a.json"
        digest = StageResultDigest(sha256="a" * 64, size_bytes=128)
        command = MeasurementCommand(
            "config.json",
            _prepared_run(),
            cable_loss_path=cable,
            cable_loss_digest=digest,
        )

        worker = build_measurement_worker_factories()[MeasurementKind.DRIVER_MAPPING](
            command
        )

        self.assertIs(worker.prepared_run, command.prepared_run)
        self.assertEqual(worker.cable_loss_path, cable)
        self.assertEqual(worker.cable_loss_digest, digest)

    def test_amplifier_factory_forwards_both_calibration_paths(self):
        cable = Path("test_results") / "cable_loss" / "a.json"
        mapping = Path("test_results") / "driver_mapping" / "b.json"
        cable_digest = StageResultDigest(sha256="a" * 64, size_bytes=128)
        mapping_digest = StageResultDigest(sha256="b" * 64, size_bytes=256)
        command = MeasurementCommand(
            "config.json",
            _prepared_run(),
            cable_loss_path=cable,
            driver_mapping_path=mapping,
            cable_loss_digest=cable_digest,
            driver_mapping_digest=mapping_digest,
        )

        worker = build_measurement_worker_factories()[MeasurementKind.AMPLIFIER](
            command
        )

        self.assertEqual(worker.cable_loss_path, cable)
        self.assertEqual(worker.driver_mapping_path, mapping)
        self.assertEqual(worker.cable_loss_digest, cable_digest)
        self.assertEqual(worker.driver_mapping_digest, mapping_digest)

    def test_commands_without_selections_keep_the_run_reference_fallback(self):
        command = MeasurementCommand("config.json", _prepared_run())
        factories = build_measurement_worker_factories()

        mapping_worker = factories[MeasurementKind.DRIVER_MAPPING](command)
        amplifier_worker = factories[MeasurementKind.AMPLIFIER](command)

        self.assertIsNone(mapping_worker.cable_loss_path)
        self.assertIsNone(mapping_worker.cable_loss_digest)
        self.assertIsNone(amplifier_worker.cable_loss_path)
        self.assertIsNone(amplifier_worker.cable_loss_digest)
        self.assertIsNone(amplifier_worker.driver_mapping_path)
        self.assertIsNone(amplifier_worker.driver_mapping_digest)


class MeasurementCommandModeTests(unittest.TestCase):
    def test_simulation_command_requires_a_session_and_rejects_a_prepared_run(self):
        command = _simulation_command(_simulation_session(MeasurementType.CABLE_LOSS))
        self.assertIs(command.mode, MeasurementExecutionMode.SIMULATION)
        self.assertIsNone(command.prepared_run)

        with self.assertRaisesRegex(ValueError, "必须携带模拟会话"):
            MeasurementCommand("config.json", mode="simulation")
        with self.assertRaisesRegex(ValueError, "不能携带正式运行契约"):
            MeasurementCommand(
                "config.json",
                prepared_run=_prepared_run(),
                mode=MeasurementExecutionMode.SIMULATION,
                simulation_session=_simulation_session(MeasurementType.CABLE_LOSS),
            )
        with self.assertRaisesRegex(ValueError, "simulation_session 必须是"):
            MeasurementCommand(
                "config.json",
                mode=MeasurementExecutionMode.SIMULATION,
                simulation_session=object(),
            )
        with self.assertRaisesRegex(ValueError, "未知的测量执行模式"):
            MeasurementCommand("config.json", mode="unknown")

    def test_formal_command_rejects_a_simulation_session(self):
        with self.assertRaisesRegex(ValueError, "不能携带模拟会话"):
            MeasurementCommand(
                "config.json",
                _prepared_run(),
                simulation_session=_simulation_session(MeasurementType.CABLE_LOSS),
            )


class SimulationWorkerFactoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def test_simulation_factories_select_the_session_stage_per_kind(self):
        session = _simulation_session(
            MeasurementType.CABLE_LOSS,
            MeasurementType.DRIVER_POWER_MAPPING,
            MeasurementType.AMPLIFIER,
        )
        command = _simulation_command(session)
        factories = build_simulation_worker_factories()

        self.assertEqual(set(factories), set(MeasurementKind))
        for kind, measurement_type in (
            (MeasurementKind.CABLE_LOSS, MeasurementType.CABLE_LOSS),
            (MeasurementKind.DRIVER_MAPPING, MeasurementType.DRIVER_POWER_MAPPING),
            (MeasurementKind.AMPLIFIER, MeasurementType.AMPLIFIER),
        ):
            with self.subTest(kind=kind.value):
                worker = factories[kind](command)
                self.assertIsInstance(worker, MockReplayWorker)
                self.assertIs(worker.stage, session.stage_for(measurement_type))
                self.assertIsNone(worker.measurement_port)

    def test_missing_or_mismatched_stage_fails_before_a_worker_starts(self):
        session = _simulation_session(MeasurementType.CABLE_LOSS)
        command = _simulation_command(session)
        factories = build_simulation_worker_factories()

        with self.assertRaisesRegex(ConfigurationError, "不包含 amplifier 阶段"):
            factories[MeasurementKind.AMPLIFIER](command)

    def test_simulation_factory_refuses_a_measurement_port(self):
        command = _simulation_command(_simulation_session(MeasurementType.CABLE_LOSS))

        with self.assertRaisesRegex(ValueError, "不接受测量端口"):
            build_simulation_worker_factories()[MeasurementKind.CABLE_LOSS](
                command, measurement_port=object()
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
