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


class _Instrument:
    """满足主功放服务能力校验的最小设备替身（驱动模式关闭）。"""

    def set_power(self, _power):
        pass

    def set_frequency(self, _frequency):
        pass

    def set_center_frequency(self, _frequency):
        pass

    def set_span(self, _span):
        pass

    def rf_output_on(self):
        pass

    def rf_output_off(self):
        pass

    def measure_power_with_average(self):
        return -10.0

    def setup_dut_power(self):
        pass

    def power_on_sequence(self):
        pass

    def power_off_sequence(self):
        pass

    def read_voltage(self, _name, _channel):
        return 5.0

    def read_current(self, _name, _channel):
        return 1.0

    def close_all(self, close_rf=False):
        return []


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
            _amplifier_request(_Instrument()), sleep_fn=injected_sleep
        )

        self.assertIs(amplifier.sleep_fn, injected_sleep)

    def test_module_sleep_patch_is_used_when_no_sleep_function_is_injected(self):
        with patch("application.measurements.amplifier_test.time.sleep") as patched_sleep:
            amplifier = AmplifierMeasurementUseCase(_amplifier_request(_Instrument()))
        self.assertIs(amplifier.sleep_fn, patched_sleep)

    def test_amplifier_use_case_rejects_missing_measurement_port(self):
        with self.assertRaisesRegex(ValueError, "注入 measurement_port"):
            AmplifierMeasurementUseCase(_amplifier_request(None))


if __name__ == "__main__":
    unittest.main()
