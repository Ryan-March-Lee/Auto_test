"""worker 工厂：把页面冻结的显式校准输入来源交给对应 worker。"""

import unittest
from pathlib import Path
from unittest.mock import patch

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
from presentation.qt.workers import (
    AmplifierWorker,
    CableLossWorker,
    DriverMappingWorker,
)

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


class SimulatedDispatchFactoryTests(unittest.TestCase):
    """仿真运行分派：激活阶段、注入仿真路径与快放 sleep_fn。"""

    class _Port:
        def __init__(self, *, fail=False):
            self.activated = []
            self.fail = fail

        def activate(self, measurement_type):
            if self.fail:
                raise ValueError("端口激活失败")
            self.activated.append(measurement_type)

    class _Context:
        def __init__(self, port):
            self.port = port
            self.paths = object()
            self.time_scale = 50

    @classmethod
    def setUpClass(cls):
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def _factories(self, port):
        context = self._Context(port)
        return build_measurement_worker_factories(
            simulated_context_provider=lambda: context
        ), context

    def test_each_kind_activates_its_measurement_type_and_injects_simulation_paths(self):
        cases = (
            (MeasurementKind.CABLE_LOSS, MeasurementType.CABLE_LOSS, CableLossWorker),
            (
                MeasurementKind.DRIVER_MAPPING,
                MeasurementType.DRIVER_POWER_MAPPING,
                DriverMappingWorker,
            ),
            (MeasurementKind.AMPLIFIER, MeasurementType.AMPLIFIER, AmplifierWorker),
        )
        for kind, measurement_type, worker_type in cases:
            with self.subTest(kind=kind.value):
                port = self._Port()
                factories, context = self._factories(port)
                worker = factories[kind](MeasurementCommand("config.json", _prepared_run()))
                self.assertIsInstance(worker, worker_type)
                self.assertEqual(port.activated, [measurement_type])
                self.assertIs(
                    worker.measurement_factory.keywords["paths"], context.paths
                )
                self.assertEqual(
                    worker.measurement_factory.func.__name__, "_create_measurement"
                )

    def test_time_scale_only_shortens_sleeps(self):
        port = self._Port()
        factories, _context = self._factories(port)
        worker = factories[MeasurementKind.CABLE_LOSS](
            MeasurementCommand("config.json", _prepared_run())
        )
        with patch("time.sleep") as sleeper:
            worker.sleep_fn(10)
        sleeper.assert_called_once_with(0.2)

    def test_activation_failure_happens_before_a_worker_is_returned(self):
        factories, _context = self._factories(self._Port(fail=True))
        with self.assertRaisesRegex(ValueError, "激活失败"):
            factories[MeasurementKind.AMPLIFIER](
                MeasurementCommand("config.json", _prepared_run())
            )

    def test_formal_factories_are_unchanged_without_a_simulated_context(self):
        factories = build_measurement_worker_factories(
            simulated_context_provider=lambda: None
        )
        worker = factories[MeasurementKind.CABLE_LOSS](
            MeasurementCommand("config.json", _prepared_run())
        )
        self.assertIsNone(worker.measurement_factory)
        self.assertIsNone(worker.sleep_fn)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
