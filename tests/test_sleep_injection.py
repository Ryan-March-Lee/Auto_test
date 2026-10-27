import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from application.dto import AmplifierMeasurementRequest
from application.measurements import AmplifierMeasurementUseCase
from tests.fakes import safety_cleanup_for


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
        safety_cleanup=safety_cleanup_for(measurement_port),
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


if __name__ == "__main__":
    unittest.main()
