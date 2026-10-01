import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.gui_runtime import (
    create_amplifier_measurement,
    create_cable_loss_measurement,
    create_driver_mapping_measurement,
    prepare_configuration,
)


FIXTURE = Path(__file__).parent / "fixtures" / "config_driver_enabled_no_assignment.json"


class _Port:
    def __init__(self):
        self.close_calls = []

    def close_all(self, *, close_rf=False):
        self.close_calls.append(close_rf)


def _prepared_run():
    return SimpleNamespace(
        context=SimpleNamespace(run_id="run-assembly-test"),
        run_directory=Path("run-directory"),
    )


class GuiRuntimeAssemblyTests(unittest.TestCase):
    def test_default_factories_create_and_inject_hardware_port(self):
        cases = (
            (create_cable_loss_measurement, "EnhancedCableLossMeasurement"),
            (create_driver_mapping_measurement, "EnhancedDriverPowerMapping"),
            (create_amplifier_measurement, "EnhancedAmplifierMeasurement"),
        )
        for factory, measurement_name in cases:
            with self.subTest(factory=factory.__name__):
                port = _Port()
                expected = object()
                with patch("app.gui_runtime.connect_instruments", return_value=port) as connect, \
                        patch(f"app.gui_runtime.{measurement_name}", return_value=expected) as measurement:
                    result = factory("config.json", prepared_run=_prepared_run())
                self.assertIs(result, expected)
                connect.assert_called_once_with("config.json")
                self.assertIs(measurement.call_args.kwargs["measurement_port"], port)
                self.assertEqual(measurement.call_args.kwargs["run_id"], "run-assembly-test")
                self.assertEqual(measurement.call_args.kwargs["run_directory"], Path("run-directory"))
                self.assertEqual(port.close_calls, [])

    def test_default_factory_closes_owned_port_when_measurement_construction_fails(self):
        port = _Port()
        with patch("app.gui_runtime.connect_instruments", return_value=port), \
                patch("app.gui_runtime.EnhancedCableLossMeasurement", side_effect=OSError("bad result file")):
            with self.assertRaisesRegex(OSError, "bad result file"):
                create_cable_loss_measurement("config.json", prepared_run=_prepared_run())
        self.assertEqual(port.close_calls, [True])

    def test_default_factory_does_not_close_injected_port_on_construction_failure(self):
        port = _Port()
        with patch("app.gui_runtime.connect_instruments") as connect, \
                patch("app.gui_runtime.EnhancedCableLossMeasurement", side_effect=OSError("bad result file")):
            with self.assertRaisesRegex(OSError, "bad result file"):
                create_cable_loss_measurement(
                    "config.json",
                    prepared_run=_prepared_run(),
                    measurement_port=port,
                )
        connect.assert_not_called()
        self.assertEqual(port.close_calls, [])

class GuiRuntimePreparationTests(unittest.TestCase):
    def _write_config(self, *, confirmed=True):
        config = json.loads(FIXTURE.read_text(encoding="utf-8"))
        config["wiring"] = {
            "confirmed": confirmed,
            "connection_note": "confirmed" if confirmed else None,
        }
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return directory, path

    def test_cable_loss_preparation_uses_operation_specific_validation(self):
        directory, path = self._write_config()
        try:
            with patch("app.gui_runtime.prepare_run", return_value="prepared") as prepare_run:
                result = prepare_configuration(str(path), operation="cable_loss")
            self.assertEqual(result, "prepared")
            loaded = prepare_run.call_args.args[0]
            self.assertTrue(loaded.valid)
        finally:
            directory.cleanup()

    def test_driver_mapping_preparation_allows_external_driver_power(self):
        directory, path = self._write_config()
        try:
            with patch("app.gui_runtime.prepare_run", return_value="prepared") as prepare_run:
                result = prepare_configuration(str(path), operation="driver_mapping")
            self.assertEqual(result, "prepared")
            loaded = prepare_run.call_args.args[0]
            self.assertTrue(loaded.valid)
        finally:
            directory.cleanup()

    def test_cable_loss_preparation_rejects_missing_wiring_confirmation(self):
        directory, path = self._write_config(confirmed=False)
        try:
            with self.assertRaisesRegex(ValueError, "wiring.confirmed"):
                prepare_configuration(str(path), operation="cable_loss")
        finally:
            directory.cleanup()

    def test_unknown_operation_is_rejected(self):
        directory, path = self._write_config()
        try:
            with self.assertRaisesRegex(ValueError, "不支持的测量类型"):
                prepare_configuration(str(path), operation="unknown")
        finally:
            directory.cleanup()


if __name__ == "__main__":
    unittest.main()
