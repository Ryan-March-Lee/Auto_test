import unittest
from unittest.mock import patch

from amplifier_measurement import AmplifierMeasurement
from cable_loss_measurement import CableLossMeasurement
from driver_power_mapping import DriverPowerMapping


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
        ), patch("cable_loss_measurement.InstrumentControl"):
            cable = CableLossMeasurement(sleep_fn=injected_sleep)
        self.assertIs(cable.sleep_fn, injected_sleep)

        with patch("driver_power_mapping.load_config_file", return_value=config), patch(
            "driver_power_mapping.load_json_result", return_value={}
        ), patch("driver_power_mapping.write_legacy_run_snapshot", return_value=None), patch(
            "driver_power_mapping.InstrumentControl"
        ):
            mapping = DriverPowerMapping(sleep_fn=injected_sleep)
        self.assertIs(mapping.sleep_fn, injected_sleep)

        with patch("amplifier_measurement.load_config_file", return_value=config), patch(
            "amplifier_measurement.load_json_result", return_value={}
        ), patch("amplifier_measurement.write_legacy_run_snapshot", return_value=None), patch(
            "amplifier_measurement.InstrumentControl"
        ):
            amplifier = AmplifierMeasurement(sleep_fn=injected_sleep)
        self.assertIs(amplifier.sleep_fn, injected_sleep)

    def test_module_sleep_patch_is_used_when_no_sleep_function_is_injected(self):
        config = self._make_config()
        with patch("amplifier_measurement.load_config_file", return_value=config), patch(
            "amplifier_measurement.load_json_result", return_value={}
        ), patch("amplifier_measurement.write_legacy_run_snapshot", return_value=None), patch(
            "amplifier_measurement.InstrumentControl"
        ), patch("amplifier_measurement.time.sleep") as patched_sleep:
            amplifier = AmplifierMeasurement()
        self.assertIs(amplifier.sleep_fn, patched_sleep)


if __name__ == "__main__":
    unittest.main()
