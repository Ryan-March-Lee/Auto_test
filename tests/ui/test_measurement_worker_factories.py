"""worker 工厂：把页面冻结的显式校准输入来源交给对应 worker。"""

import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from application.dto.measurement_requests import StageResultDigest
from presentation.qt.measurement_controller_contract import MeasurementCommand
from presentation.qt.measurement_state import MeasurementKind
from presentation.qt.measurement_worker_factories import (
    build_measurement_worker_factories,
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


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
