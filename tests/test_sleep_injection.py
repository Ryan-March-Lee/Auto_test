import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from application.dto import AmplifierMeasurementRequest
from application.measurements import AmplifierMeasurementUseCase
from cable_loss_measurement import CableLossMeasurement
from driver_power_mapping import DriverPowerMapping


class _Repository:
    def save(self, result, **kwargs):
        return SimpleNamespace(run_directory=Path("saved-run"))


def _amplifier_request(measurement_port):
    return AmplifierMeasurementRequest(
        configuration={
            "attenuator": {"type": "0dB"},
            "driver_mode": {"enabled": False},
        },
        context=SimpleNamespace(run_id="run-amplifier"),
        run_directory=Path("run-directory"),
        measurement_port=measurement_port,
        result_repository=_Repository(),
        loss_data={"cable_losses": {}},
    )


class SleepInjectionConstructorTests(unittest.TestCase):
    def _make_config(self):
        return {
            "attenuator": {"type": "0dB"},
            "driver_mode": {"enabled": False},
        }

    def test_measurement_constructors_keep_injected_sleep_function(self):
        injected_sleep = lambda _: None
        config = self._make_config()
        with patch("cable_loss_measurement.load_config_file", return_value=config), patch(
            "cable_loss_measurement.write_legacy_run_snapshot", return_value=None
        ):
            cable = CableLossMeasurement(sleep_fn=injected_sleep, measurement_port=object())
        self.assertIs(cable.sleep_fn, injected_sleep)

        with patch("driver_power_mapping.load_config_file", return_value=config), patch(
            "driver_power_mapping.load_json_result", return_value={}
        ), patch("driver_power_mapping.write_legacy_run_snapshot", return_value=None):
            mapping = DriverPowerMapping(sleep_fn=injected_sleep, measurement_port=object())
        self.assertIs(mapping.sleep_fn, injected_sleep)

        amplifier = AmplifierMeasurementUseCase(
            _amplifier_request(object()), sleep_fn=injected_sleep
        )
        self.assertIs(amplifier.sleep_fn, injected_sleep)

    def test_module_sleep_patch_is_used_when_no_sleep_function_is_injected(self):
        with patch("application.measurements.amplifier_test.time.sleep") as patched_sleep:
            amplifier = AmplifierMeasurementUseCase(_amplifier_request(object()))
        self.assertIs(amplifier.sleep_fn, patched_sleep)

    def test_amplifier_use_case_rejects_missing_measurement_port(self):
        with self.assertRaisesRegex(ValueError, "注入 measurement_port"):
            AmplifierMeasurementUseCase(_amplifier_request(None))

    def test_legacy_constructors_reject_missing_measurement_port_before_snapshot(self):
        config = self._make_config()
        cases = (
            ("cable_loss_measurement", CableLossMeasurement, "load_config_file"),
            ("driver_power_mapping", DriverPowerMapping, "load_config_file"),
        )
        for module_name, measurement_type, loader_name in cases:
            with self.subTest(measurement_type=measurement_type.__name__):
                with patch(f"{module_name}.{loader_name}", return_value=config), patch(
                    f"{module_name}.write_legacy_run_snapshot"
                ) as snapshot:
                    with self.assertRaisesRegex(ValueError, "必须显式传入 measurement_port"):
                        measurement_type()
                snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
