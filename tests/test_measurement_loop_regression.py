"""同步测量循环的硬件动作和保护行为回归测试。

历史入口 ``amplifier_measurement`` 的扫描逻辑已委托给正式实现
:class:`application.measurements.services.AmplifierMeasurementService`，因此这里
直接针对正式实现验证硬件动作顺序与 DUT 输入保护。
"""

import sys
import unittest
from dataclasses import replace
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application.dto import AmplifierInputError, DrainChannel
from application.measurements.services import AmplifierMeasurementService
from tests.contract_doubles import amplifier_inputs
from tests.fakes import safety_cleanup_for


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

    def setup_dut_power(self):
        self.operations.append(("setup_dut_power",))

    def power_on_sequence(self):
        self.operations.append(("power_on_sequence",))

    def power_off_sequence(self):
        self.operations.append(("power_off_sequence",))

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


class FakeChannelMeasurementInstrument(FakeMeasurementInstrument):
    """按物理通道返回不同读数，用于验证效率输入只取漏极。"""

    def __init__(self, voltages, currents):
        super().__init__()
        self.voltages = dict(voltages)
        self.currents = dict(currents)

    def read_voltage(self, supply_name, channel):
        self.operations.append(("read_voltage", supply_name, channel))
        return self.voltages[(supply_name, channel)]

    def read_current(self, supply_name, channel):
        self.operations.append(("read_current", supply_name, channel))
        return self.currents[(supply_name, channel)]


def make_service(
    max_input_power=1000.0,
    *,
    instrument=None,
    supplies=(("PS1", "CH1"),),
    drain=None,
    start_power_dbm=-3.0,
    stop_power_dbm=0.0,
    inputs=None,
    sleeps=None,
    owns_measurement_port=True,
):
    instrument = instrument or FakeMeasurementInstrument()
    if inputs is None:
        inputs = amplifier_inputs(
            start_power_dbm=start_power_dbm,
            stop_power_dbm=stop_power_dbm,
            step_db=1.0,
            compression_db=5.0,
            attenuator_db=0.0,
            max_input_power_dbm=max_input_power,
            supplies=supplies,
            drain=drain,
        )
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
        inputs,
        instrument,
        loss_data,
        None,
        run_id="run-amplifier",
        safety_cleanup=safety_cleanup_for(instrument),
        sleep_fn=sleeps.append if sleeps is not None else (lambda _: None),
        owns_measurement_port=owns_measurement_port,
    )


class MeasurementLoopRegressionTests(unittest.TestCase):
    def run_sweep(self, max_input_power=1000.0):
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


class DrainEfficiencyTests(unittest.TestCase):
    """步骤 10：效率只读用户指定漏极，不再汇总全部 DUT 通道。"""

    SUPPLIES = (("PS1", "CH1"), ("PS1", "CH2"))
    VOLTAGES = {("PS1", "CH1"): 30.0, ("PS1", "CH2"): 20.0}
    CURRENTS = {("PS1", "CH1"): 0.1, ("PS1", "CH2"): 2.0}

    def _sweep(self, supplies, drain):
        instrument = FakeChannelMeasurementInstrument(self.VOLTAGES, self.CURRENTS)
        service = make_service(
            instrument=instrument,
            supplies=supplies,
            drain=drain,
            start_power_dbm=0.0,
            stop_power_dbm=0.0,
        )
        return service, service.perform_power_sweep(1.0)["sweep_data"]

    def test_efficiency_uses_only_the_non_first_drain_channel(self):
        service, sweep = self._sweep(self.SUPPLIES, ("PS1", "CH2"))

        # 漏极 CH2：20 V × 2 A = 40 W；若按旧的“全部通道求和”会是 40 + 3 = 43 W。
        self.assertEqual(sweep["dc_power"], [40.0])
        self.assertAlmostEqual(sweep["efficiency"][0], 0.25)
        # 非漏极通道仍然逐通道采样并保留在结果里。
        self.assertEqual(
            sweep["voltages"][0], {"PS1_CH1": 30.0, "PS1_CH2": 20.0}
        )
        self.assertEqual(
            sweep["currents"][0], {"PS1_CH1": 0.1, "PS1_CH2": 2.0}
        )
        self.assertEqual(
            [operation for operation in service.inst_ctrl.operations
             if operation[0] in {"read_voltage", "read_current"}],
            [
                ("read_voltage", "PS1", "CH1"),
                ("read_current", "PS1", "CH1"),
                ("read_voltage", "PS1", "CH2"),
                ("read_current", "PS1", "CH2"),
            ],
        )

    def test_multiple_drains_sum_their_dc_power(self):
        instrument = FakeChannelMeasurementInstrument(self.VOLTAGES, self.CURRENTS)
        inputs = replace(
            amplifier_inputs(
                supplies=self.SUPPLIES,
                drain=("PS1", "CH2"),
                start_power_dbm=0.0,
                stop_power_dbm=0.0,
                attenuator_db=0.0,
            ),
            drain_channel=DrainChannel.from_document(
                [
                    {"supply_id": "PS1", "channel": "CH1"},
                    {"supply_id": "PS1", "channel": "CH2"},
                ]
            ),
        )
        service = make_service(
            instrument=instrument,
            inputs=inputs,
        )

        sweep = service.perform_power_sweep(1.0)["sweep_data"]

        # CH1: 30 V × 0.1 A = 3 W；CH2: 20 V × 2 A = 40 W；两条漏极求和为 43 W。
        self.assertEqual(sweep["dc_power"], [43.0])
        self.assertAlmostEqual(sweep["efficiency"][0], 0.1 / 43.0 * 100.0)
        self.assertEqual(
            sweep["voltages"][0], {"PS1_CH1": 30.0, "PS1_CH2": 20.0}
        )

    def test_changing_power_order_does_not_change_the_efficiency_input(self):
        _, forward = self._sweep(self.SUPPLIES, ("PS1", "CH2"))
        _, reversed_order = self._sweep(
            (("PS1", "CH2"), ("PS1", "CH1")), ("PS1", "CH2")
        )

        self.assertEqual(forward["dc_power"], reversed_order["dc_power"])
        self.assertEqual(forward["efficiency"], reversed_order["efficiency"])
        self.assertEqual(
            set(reversed_order["voltages"][0]), {"PS1_CH1", "PS1_CH2"},
            "结果键集合不随上电顺序变化（只与通道集合有关）",
        )

    def test_efficiency_uses_physical_route_not_the_concatenated_key(self):
        # 两条不同物理路由拼出同一个历史结果键：PS_1/CH1 与 PS/1_CH1 都是
        # "PS_1_CH1"。漏极必须按路由对象选中，不能按键反查。
        supplies = (("PS_1", "CH1"), ("PS", "1_CH1"))
        instrument = FakeChannelMeasurementInstrument(
            {("PS_1", "CH1"): 10.0, ("PS", "1_CH1"): 50.0},
            {("PS_1", "CH1"): 1.0, ("PS", "1_CH1"): 3.0},
        )
        service = make_service(
            instrument=instrument,
            supplies=supplies,
            drain=("PS_1", "CH1"),
            start_power_dbm=0.0,
            stop_power_dbm=0.0,
        )

        sweep = service.perform_power_sweep(1.0)["sweep_data"]

        # 10 V × 1 A = 10 W；若按碰撞后的键取值会得到 50 V × 3 A = 150 W。
        self.assertEqual(sweep["dc_power"], [10.0])
        # 兼容键格式保持不变：后写入的 PS/1_CH1 覆盖同键值。
        self.assertEqual(sweep["voltages"][0], {"PS_1_CH1": 50.0})
        self.assertEqual(sweep["currents"][0], {"PS_1_CH1": 3.0})
        # 两条物理路由仍各自采样。
        read_routes = [
            operation[1:]
            for operation in service.inst_ctrl.operations
            if operation[0] == "read_voltage"
        ]
        self.assertEqual(read_routes, [("PS_1", "CH1"), ("PS", "1_CH1")])

    def test_drain_selection_does_not_change_the_power_lifecycle(self):
        lifecycle_names = {"setup_dut_power", "power_on_sequence", "power_off_sequence"}

        def lifecycle(drain):
            sleeps = []
            instrument = FakeChannelMeasurementInstrument(self.VOLTAGES, self.CURRENTS)
            service = make_service(
                instrument=instrument,
                supplies=self.SUPPLIES,
                drain=drain,
                start_power_dbm=0.0,
                stop_power_dbm=0.0,
                sleeps=sleeps,
                # 清理端口不释放连接，避免测试替身实现 VISA 关闭语义。
                owns_measurement_port=False,
            )
            service.run()
            operations = [
                operation
                for operation in instrument.operations
                if operation[0] in lifecycle_names
            ]
            return operations, sleeps

        first_operations, first_sleeps = lifecycle(("PS1", "CH1"))
        second_operations, second_sleeps = lifecycle(("PS1", "CH2"))

        expected_order = [
            ("setup_dut_power",),
            ("power_on_sequence",),
            ("power_off_sequence",),
        ]
        self.assertEqual(first_operations, expected_order)
        self.assertEqual(second_operations, expected_order)
        # 上电后的稳定等待与每个扫描点的稳定延时都不随漏极变化。
        self.assertEqual(first_sleeps, [2, 5.0])
        self.assertEqual(first_sleeps, second_sleeps)

    def test_service_rejects_inputs_without_a_drain(self):
        inputs = replace(amplifier_inputs(), drain_channel=DrainChannel())
        instrument = FakeMeasurementInstrument()

        with self.assertRaises(AmplifierInputError):
            make_service(instrument=instrument, inputs=inputs)


if __name__ == "__main__":
    unittest.main()
