"""同步测量循环的硬件动作和保护行为回归测试。

历史入口 ``amplifier_measurement`` 的扫描逻辑已委托给正式实现
:class:`application.measurements.services.AmplifierMeasurementService`，因此这里
直接针对正式实现验证硬件动作顺序与 DUT 输入保护。
"""

import sys
import unittest
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application.measurements.services import AmplifierMeasurementService


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


def make_service(max_input_power=float("inf")):
    config = {
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
    loss_data = {
        "cable_losses": {
            "1.0": {
                "cable1": 0.0,
                "cable2": 0.0,
                "cable3": 0.0,
                "cable4": 0.0,
            },
        },
    }
    return AmplifierMeasurementService(
        config,
        FakeMeasurementInstrument(),
        loss_data,
        None,
        sleep_fn=lambda _: None,
    )


class MeasurementLoopRegressionTests(unittest.TestCase):
    def run_sweep(self, max_input_power=float("inf")):
        service = make_service(max_input_power)
        result = service.perform_power_sweep(1.0)
        return service, result

    def test_hardware_action_order_is_preserved(self):
        service, result = self.run_sweep()
        operations = service.inst_ctrl.operations

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
        service, result = self.run_sweep(max_input_power=-2.0)

        operations = service.inst_ctrl.operations
        set_power_values = [operation[1] for operation in operations if operation[0] == "set_power"]
        self.assertEqual(set_power_values, [-40, -3.0, -2.0])
        self.assertEqual(service.inst_ctrl.measurement_count, 2)
        self.assertEqual(len(result["sweep_data"]["sg_power"]), 2)
        self.assertNotIn(("set_power", -1.0), operations)


if __name__ == "__main__":
    unittest.main()
