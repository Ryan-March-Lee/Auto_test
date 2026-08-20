"""同步和增强测量循环的阶段4行为回归测试。"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from amplifier_measurement import AmplifierMeasurement
from enhanced_workers import EnhancedAmplifierMeasurement


class FakeMeasurementInstrument:
    """记录测量循环动作，并返回由当前信号源功率推导的固定读数。"""

    def __init__(self):
        self.operations = []
        self.current_power = None
        self.measurement_count = 0

    def set_power(self, power):
        self.current_power = power
        self.operations.append(("set_power", power))

    def set_frequency(self, frequency):
        self.operations.append(("set_frequency", frequency))

    def set_center_frequency(self, frequency):
        self.operations.append(("set_center_frequency", frequency))

    def set_span(self, span):
        self.operations.append(("set_span", span))

    def rf_output_on(self):
        self.operations.append(("rf_output_on",))

    def rf_output_off(self):
        self.operations.append(("rf_output_off",))

    def measure_power_with_average(self):
        self.measurement_count += 1
        self.operations.append(("measure_power",))
        return self.current_power + 20.0

    def read_voltage(self, supply_name, channel):
        self.operations.append(("read_voltage", supply_name, channel))
        return 5.0

    def read_current(self, supply_name, channel):
        self.operations.append(("read_current", supply_name, channel))
        return 2.0


def make_measurement(measurement_class, max_input_power=float("inf")):
    measurement = measurement_class.__new__(measurement_class)
    measurement.config = {
        "compression_point": {"type": "5dB"},
        "attenuator": {"type": "0dB"},
        "dut_config": {"max_input_power": max_input_power},
        "signal_source": {"start_power": -3.0, "stop_power": 0.0, "step": 1.0},
        "power_supply_assignment": {
            "dut_amplifier": {
                "supplies": {
                    "main": {"name": "PS1", "channel": ["CH1"]},
                },
            },
        },
    }
    measurement.loss_data = {
        "cable_losses": {
            "1.0": {
                "cable1": 0.0,
                "cable2": 0.0,
                "cable3": 0.0,
                "cable4": 0.0,
            },
        },
    }
    measurement.driver_mapping = None
    measurement.inst_ctrl = FakeMeasurementInstrument()
    measurement.progress_callback = None
    measurement.message_callback = None
    measurement.data_callback = None
    measurement.should_stop = False
    return measurement


class MeasurementLoopRegressionTests(unittest.TestCase):
    def run_sweep(self, measurement_class):
        measurement = make_measurement(measurement_class)
        with patch("amplifier_measurement.time.sleep"), patch(
            "enhanced_workers.time.sleep"
        ), patch("builtins.print"):
            result = measurement.perform_power_sweep(1.0)
        return measurement, result

    def test_sync_and_enhanced_results_are_compatible(self):
        sync_measurement, sync_result = self.run_sweep(AmplifierMeasurement)
        enhanced_measurement, enhanced_result = self.run_sweep(
            EnhancedAmplifierMeasurement
        )

        self.assertEqual(sync_result.keys(), enhanced_result.keys())
        self.assertEqual(sync_result["compression_type"], "5dB")
        self.assertEqual(sync_result["compression_point"], enhanced_result["compression_point"])
        self.assertEqual(sync_result["small_signal_gain"], enhanced_result["small_signal_gain"])
        self.assertEqual(sync_result["compression_achieved"], False)
        self.assertEqual(sync_result["sweep_data"], enhanced_result["sweep_data"])
        self.assertEqual(
            len(sync_result["sweep_data"]["sg_power"]),
            4,
        )
        self.assertEqual(sync_measurement.inst_ctrl.measurement_count, 4)
        self.assertEqual(enhanced_measurement.inst_ctrl.measurement_count, 4)

    def test_hardware_action_order_is_preserved(self):
        measurement, result = self.run_sweep(AmplifierMeasurement)
        operations = measurement.inst_ctrl.operations

        self.assertEqual(
            operations[:6],
            [
                ("set_power", -40),
                ("set_frequency", 1.0),
                ("set_center_frequency", 1.0),
                ("set_span", 10),
                ("rf_output_on",),
                ("set_power", -3.0),
            ],
        )
        self.assertEqual(
            operations[6:11],
            [
                ("measure_power",),
                ("read_voltage", "PS1", "CH1"),
                ("read_current", "PS1", "CH1"),
                ("set_power", -2.0),
                ("measure_power",),
            ],
        )
        self.assertEqual(operations[-1], ("rf_output_off",))
        self.assertEqual(len(result["sweep_data"]["sg_power"]), 4)

    def test_protection_happens_before_hardware_measurement(self):
        for measurement_class in (AmplifierMeasurement, EnhancedAmplifierMeasurement):
            measurement = make_measurement(measurement_class, max_input_power=-2.0)
            with patch("amplifier_measurement.time.sleep"), patch(
                "enhanced_workers.time.sleep"
            ), patch("builtins.print"):
                result = measurement.perform_power_sweep(1.0)

            operations = measurement.inst_ctrl.operations
            set_power_values = [
                operation[1]
                for operation in operations
                if operation[0] == "set_power"
            ]
            self.assertEqual(set_power_values, [-40, -3.0, -2.0])
            self.assertEqual(measurement.inst_ctrl.measurement_count, 2)
            self.assertEqual(len(result["sweep_data"]["sg_power"]), 2)
            self.assertNotIn(("set_power", -1.0), operations)


if __name__ == "__main__":
    unittest.main()
