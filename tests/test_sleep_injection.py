import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from application.dto import AmplifierMeasurementRequest
from application.measurements import AmplifierMeasurementUseCase
from app.legacy_amplifier_measurement import AmplifierMeasurement
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


class FormalMeasurementSleepInjectionTests(unittest.TestCase):
    def test_amplifier_use_case_keeps_injected_sleep_function(self):
        injected_sleep = lambda _: None

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


class LegacyMeasurementEntryGuardTests(unittest.TestCase):
    def test_legacy_entries_fail_before_loading_or_storage(self):
        cases = (
            CableLossMeasurement,
            DriverPowerMapping,
            AmplifierMeasurement,
        )
        for measurement_type in cases:
            with self.subTest(measurement_type=measurement_type.__name__):
                with self.assertRaisesRegex(RuntimeError, "旧入口已停用"):
                    measurement_type(measurement_port=object())


if __name__ == "__main__":
    unittest.main()
