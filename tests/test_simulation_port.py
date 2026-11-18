"""仿真运行端口（``instrument/simulation_port.py``）的契约与读数测试。

覆盖重构计划步骤 5～9：

- 端口能力、生命周期（``begin_stage`` / ``activate`` / ``end_stage`` / ``close_all``）
  与安全清理路径；
- 线损有序读数、驱动映射与主功放的记录点查找、区间内插值、禁止外推、严格模式；
- 端口模块的导入边界：不依赖 VISA、驱动、传输或文件读取。

测试只用内存里的小型读数包，不读写 ``mock_data``，也不构造 VISA/驱动对象。
真实数据与时序由阶段三的无 GUI 用例对照覆盖。
"""

import ast
import unittest
from pathlib import Path

from application.dto.measurement_results import MeasurementType
from application.dto.simulation_readings import SimulationReadings
from application.ports.measurement_session import CleanupStepStatus
from instrument.flow.cleanup import SafetyCleanupAdapter
from instrument.ports import (
    DRIVER_POWER_CAPABILITIES,
    DUT_POWER_CAPABILITIES,
    SIGNAL_MEASUREMENT_CAPABILITIES,
    require_measurement_capabilities,
)
from instrument.simulation_port import (
    SimulationPortError,
    SimulationPortStateError,
    SimulationRunPort,
)


ROOT = Path(__file__).parents[1]
PORT_SOURCE = ROOT / "instrument" / "simulation_port.py"
SHA_A = "a" * 64
SHA_B = "b" * 64


def _source_document(*, cable_loss=False, driver_mapping=False):
    return {
        "scenario": "scenario.json",
        "scenario_sha256": SHA_A,
        "cable_loss_result": "loss.json" if cable_loss else None,
        "cable_loss_sha256": SHA_B if cable_loss else None,
        "driver_mapping_result": "driver.json" if driver_mapping else None,
        "driver_mapping_sha256": SHA_B if driver_mapping else None,
    }


def _readings(measurement_type, payload, *, source=None):
    return SimulationReadings.from_document(
        {
            "schema_version": 1,
            "kind": "simulation_readings",
            "measurement_type": measurement_type.value,
            "source": source
            or _source_document(
                cable_loss=measurement_type is not MeasurementType.CABLE_LOSS,
                driver_mapping=measurement_type is MeasurementType.AMPLIFIER,
            ),
            "payload": payload,
        }
    )


def _cable_loss_readings(frequencies=(1.0, 2.0), *, path1_offset=40.0, path2_offset=42.0):
    measurements = []
    for path, offset in ((1, path1_offset), (2, path2_offset)):
        for index, frequency in enumerate(frequencies):
            measurements.append(
                {
                    "frequency_ghz": frequency,
                    "path": path,
                    "raw_dbm": -(offset + index),
                }
            )
    return _readings(MeasurementType.CABLE_LOSS, {"measurements": measurements})


def _driver_readings():
    return _readings(
        MeasurementType.DRIVER_POWER_MAPPING,
        {
            "frequencies": {
                "1.0": {
                    "probes": [
                        {"source_power_dbm": -70.0, "raw_output_dbm": -70.8},
                        {"source_power_dbm": -60.0, "raw_output_dbm": -60.8},
                    ]
                },
                "2.0": {
                    "probes": [
                        {"source_power_dbm": -50.0, "raw_output_dbm": -50.5},
                        {"source_power_dbm": -40.0, "raw_output_dbm": -40.5},
                    ]
                },
            }
        },
    )


def _amplifier_readings():
    return _readings(
        MeasurementType.AMPLIFIER,
        {
            "frequencies": {
                "1.0": {
                    "points": [
                        {
                            "sg_power_dbm": -60.0,
                            "raw_output_dbm": -26.0,
                            "voltages": {"SUP_CH1": 1.0, "SUP_CH2": 2.0},
                            "currents": {"SUP_CH1": 0.1, "SUP_CH2": 0.2},
                        },
                        {
                            "sg_power_dbm": -50.0,
                            "raw_output_dbm": -16.0,
                            "voltages": {"SUP_CH1": 3.0, "SUP_CH2": 4.0},
                            "currents": {"SUP_CH1": 0.3, "SUP_CH2": 0.4},
                        },
                    ]
                }
            }
        },
    )


def _recorded_driver_readings():
    """记录点使用真实驱动量化后的功率值（``-69.753`` 而非 ``-69.752975``）。"""

    return _readings(
        MeasurementType.DRIVER_POWER_MAPPING,
        {
            "frequencies": {
                "1.0": {
                    "probes": [
                        {"source_power_dbm": -70.0, "raw_output_dbm": -70.8},
                        {"source_power_dbm": -69.753, "raw_output_dbm": -70.558},
                        {"source_power_dbm": -60.0, "raw_output_dbm": -60.8},
                    ]
                }
            }
        },
    )


def _activated_port(readings, stage, *, strict=False):
    port = SimulationRunPort(readings, strict=strict)
    port.begin_stage()
    port.activate(stage)
    return port


def _measure_cable_loss(port, frequency):
    port.set_frequency(frequency)
    port.set_power(0.0)
    port.rf_output_on()
    try:
        return port.measure_power_with_average()
    finally:
        port.rf_output_off()


class SimulationPortLifecycleTests(unittest.TestCase):
    """步骤 5：控制面、阶段生命周期与关闭语义。"""

    def setUp(self):
        self.readings = _cable_loss_readings()

    def test_port_satisfies_measurement_capabilities(self):
        port = SimulationRunPort(self.readings)
        capabilities = (
            SIGNAL_MEASUREMENT_CAPABILITIES
            + DRIVER_POWER_CAPABILITIES
            + DUT_POWER_CAPABILITIES
        )
        self.assertIs(
            require_measurement_capabilities(
                port, requirement="仿真端口", capabilities=capabilities
            ),
            port,
        )

    def test_reading_before_activation_requires_activation(self):
        port = SimulationRunPort(self.readings)
        port.set_frequency(1.0)
        port.set_power(0.0)
        with self.assertRaises(SimulationPortStateError) as raised:
            port.measure_power_with_average()
        self.assertIn("activate", str(raised.exception))

    def test_begin_stage_alone_does_not_enable_readings(self):
        port = SimulationRunPort(self.readings)
        port.begin_stage()
        self.assertIsNone(port.active_stage)
        with self.assertRaises(SimulationPortStateError):
            port.measure_power_with_average()

    def test_control_methods_record_state_without_acting(self):
        port = SimulationRunPort(self.readings)
        port.begin_stage()
        port.activate(MeasurementType.CABLE_LOSS)
        port.set_power(0.0)
        port.set_frequency(1.0)
        port.set_center_frequency(1.0)
        port.set_span(10.0)
        port.rf_output_on()
        port.rf_output_off()
        port.setup_driver_amplifier_power()
        port.power_on_driver()
        port.power_off_driver()
        port.setup_dut_power()
        port.power_on_sequence()
        port.power_off_sequence()

        self.assertEqual(
            [name for name, _value in port.recorded_commands],
            [
                "set_power",
                "set_frequency",
                "set_center_frequency",
                "set_span",
                "rf_output_on",
                "rf_output_off",
                "setup_driver_amplifier_power",
                "power_on_driver",
                "power_off_driver",
                "setup_dut_power",
                "power_on_sequence",
                "power_off_sequence",
            ],
        )
        self.assertIsNotNone(port.signal_gen, "安全清理必须看到 RF 关闭能力")
        self.assertFalse(port.signal_gen.rf_enabled)
        self.assertFalse(port.closed)

    def test_end_stage_is_idempotent_and_keeps_port_reusable(self):
        port = _activated_port(self.readings, MeasurementType.CABLE_LOSS)
        first = _measure_cable_loss(port, 1.0)
        port.end_stage()
        port.end_stage()
        port.end_stage(timeout_s=0.0)
        self.assertFalse(port.closed)
        self.assertIsNone(port.active_stage)

        port.begin_stage()
        port.activate(MeasurementType.CABLE_LOSS)
        self.assertEqual(_measure_cable_loss(port, 1.0), first)

    def test_begin_stage_rejects_unsettled_stage(self):
        port = _activated_port(self.readings, MeasurementType.CABLE_LOSS)
        with self.assertRaises(SimulationPortStateError):
            port.begin_stage()

    def test_activation_while_a_stage_is_active_reports_the_lifecycle_error_first(self):
        """已有未结算阶段时，即使目标读数包没加载，也先说生命周期误用。"""

        port = _activated_port(self.readings, MeasurementType.CABLE_LOSS)
        with self.assertRaises(SimulationPortStateError) as raised:
            port.activate(MeasurementType.AMPLIFIER)
        self.assertIn("cable_loss", str(raised.exception))
        self.assertIs(port.active_stage, MeasurementType.CABLE_LOSS)

    def test_activation_clears_the_previous_stage_control_values(self):
        """阶段切换后不得复用上一阶段设置的频率与功率。"""

        port = SimulationRunPort(
            {
                MeasurementType.DRIVER_POWER_MAPPING: _driver_readings(),
                MeasurementType.AMPLIFIER: _amplifier_readings(),
            }
        )
        port.begin_stage()
        port.activate(MeasurementType.DRIVER_POWER_MAPPING)
        port.set_frequency(1.0)
        port.set_power(-60.0)
        self.assertEqual(port.measure_power_with_average(), -60.8)
        port.end_stage()

        port.begin_stage()
        port.activate(MeasurementType.AMPLIFIER)
        with self.assertRaises(SimulationPortStateError) as raised:
            port.measure_power_with_average()
        self.assertIn("set_frequency", str(raised.exception))

        port.set_frequency(1.0)
        with self.assertRaises(SimulationPortStateError) as raised:
            port.measure_power_with_average()
        self.assertIn("set_power", str(raised.exception))

        port.set_power(-60.0)
        self.assertEqual(port.measure_power_with_average(), -26.0)

    def test_activate_rejects_a_stage_other_than_the_frozen_readings(self):
        port = SimulationRunPort(self.readings)
        port.begin_stage()
        with self.assertRaises(SimulationPortError) as raised:
            port.activate(MeasurementType.AMPLIFIER)
        self.assertIn("cable_loss", str(raised.exception))
        self.assertIsNone(port.active_stage)

    def test_run_level_port_routes_each_stage_to_its_own_package(self):
        """一次仿真运行只有一个端口：三个阶段共用并各自激活自己的读数包。"""

        port = SimulationRunPort(
            {
                MeasurementType.CABLE_LOSS: _cable_loss_readings(),
                MeasurementType.DRIVER_POWER_MAPPING: _driver_readings(),
                MeasurementType.AMPLIFIER: _amplifier_readings(),
            }
        )

        port.begin_stage()
        port.activate(MeasurementType.CABLE_LOSS)
        self.assertEqual(_measure_cable_loss(port, 1.0), -40.0)
        port.end_stage()

        port.begin_stage()
        port.activate("driver_power_mapping")
        port.set_frequency(2.0)
        port.set_power(-50.0)
        self.assertEqual(port.measure_power_with_average(), -50.5)
        port.end_stage()

        port.begin_stage()
        port.activate(MeasurementType.AMPLIFIER)
        port.set_frequency(1.0)
        port.set_power(-60.0)
        self.assertEqual(port.measure_power_with_average(), -26.0)
        self.assertEqual(port.read_voltage("SUP", "CH1"), 1.0)
        port.end_stage()

        self.assertFalse(port.closed)
        self.assertEqual(
            set(port.readings),
            {
                MeasurementType.CABLE_LOSS,
                MeasurementType.DRIVER_POWER_MAPPING,
                MeasurementType.AMPLIFIER,
            },
        )

    def test_activate_rejects_a_stage_that_was_not_loaded(self):
        port = SimulationRunPort(self.readings)
        with self.assertRaises(SimulationPortError) as raised:
            port.activate(MeasurementType.AMPLIFIER)
        self.assertIn("没有加载", str(raised.exception))
        self.assertIn("cable_loss", str(raised.exception))

    def test_construction_rejects_invalid_reading_packages(self):
        with self.assertRaises(SimulationPortError):
            SimulationRunPort({})
        with self.assertRaises(SimulationPortError):
            SimulationRunPort(
                {MeasurementType.CABLE_LOSS: _driver_readings()}
            )
        with self.assertRaises(TypeError):
            SimulationRunPort([object()])
        with self.assertRaises(TypeError):
            SimulationRunPort(self.readings, strict="yes")
        # 字符串阶段键与枚举键等价（MeasurementType 是 str 枚举）。
        self.assertEqual(
            set(SimulationRunPort({"cable_loss": _cable_loss_readings()}).readings),
            {MeasurementType.CABLE_LOSS},
        )

    def test_activate_rejects_unknown_type(self):
        port = SimulationRunPort(self.readings)
        with self.assertRaises(SimulationPortError):
            port.activate("not_a_measurement")

    def test_close_is_idempotent_and_requires_reconnect(self):
        port = _activated_port(self.readings, MeasurementType.CABLE_LOSS)
        self.assertEqual(port.close_all(close_rf=True), [])
        self.assertEqual(port.close_all(), [])
        self.assertTrue(port.closed)

        for action in (
            port.begin_stage,
            port.measure_power_with_average,
            lambda: port.activate(MeasurementType.CABLE_LOSS),
            lambda: port.set_frequency(1.0),
        ):
            with self.subTest(action=action):
                with self.assertRaises(SimulationPortStateError) as raised:
                    action()
                self.assertIn("重新连接", str(raised.exception))

    def test_end_stage_after_close_does_not_raise(self):
        port = _activated_port(self.readings, MeasurementType.CABLE_LOSS)
        port.close_all(close_rf=True)
        port.end_stage()

    def test_stage_start_failure_then_close_requires_reconnect(self):
        """阶段启动失败后端口处于已关闭状态，下一次读取要求重新连接。"""

        port = SimulationRunPort(self.readings)
        port.begin_stage()
        with self.assertRaises(SimulationPortError):
            port.activate(MeasurementType.DRIVER_POWER_MAPPING)
        port.close_all(close_rf=True)
        with self.assertRaises(SimulationPortStateError):
            port.begin_stage()

    def test_invalid_control_values_fail_before_touching_state(self):
        port = SimulationRunPort(self.readings)
        for action, value in (
            (port.set_frequency, 0.0),
            (port.set_frequency, float("nan")),
            (port.set_power, float("inf")),
            (port.set_span, -1.0),
        ):
            with self.subTest(action=action, value=value):
                with self.assertRaises(ValueError):
                    action(value)


class SimulationCableLossReadingTests(unittest.TestCase):
    """步骤 6：线损按有序序列消费并校验频率。"""

    def setUp(self):
        self.readings = _cable_loss_readings()

    def test_sequence_follows_path1_then_path2(self):
        port = _activated_port(self.readings, MeasurementType.CABLE_LOSS)
        raw = [_measure_cable_loss(port, frequency) for frequency in (1.0, 2.0)]
        self.assertEqual(raw, [-40.0, -41.0])
        raw = [_measure_cable_loss(port, frequency) for frequency in (1.0, 2.0)]
        self.assertEqual(raw, [-42.0, -43.0])

    def test_frequency_mismatch_does_not_advance_the_cursor(self):
        port = _activated_port(self.readings, MeasurementType.CABLE_LOSS)
        with self.assertRaises(SimulationPortError) as raised:
            _measure_cable_loss(port, 2.0)
        self.assertIn("1 GHz", str(raised.exception))
        self.assertEqual(_measure_cable_loss(port, 1.0), -40.0)

    def test_extra_reads_fail(self):
        port = _activated_port(self.readings, MeasurementType.CABLE_LOSS)
        for frequency in (1.0, 2.0, 1.0, 2.0):
            _measure_cable_loss(port, frequency)
        with self.assertRaises(SimulationPortError) as raised:
            _measure_cable_loss(port, 1.0)
        self.assertIn("全部消费", str(raised.exception))

    def test_restarting_mid_sequence_requires_reactivation(self):
        """停止后复用旧游标：顺序校验让错位立即失败，而不是继续读错频点。"""

        port = _activated_port(self.readings, MeasurementType.CABLE_LOSS)
        self.assertEqual(_measure_cable_loss(port, 1.0), -40.0)
        with self.assertRaises(SimulationPortError):
            _measure_cable_loss(port, 1.0)

    def test_activate_resets_the_cursor(self):
        port = _activated_port(self.readings, MeasurementType.CABLE_LOSS)
        self.assertEqual(_measure_cable_loss(port, 1.0), -40.0)
        port.end_stage()
        port.begin_stage()
        port.activate(MeasurementType.CABLE_LOSS)
        self.assertEqual(_measure_cable_loss(port, 1.0), -40.0)

    def test_reading_without_frequency_is_a_state_error(self):
        port = _activated_port(self.readings, MeasurementType.CABLE_LOSS)
        with self.assertRaises(SimulationPortStateError) as raised:
            port.measure_power_with_average()
        self.assertIn("set_frequency", str(raised.exception))


class SimulationDriverMappingReadingTests(unittest.TestCase):
    """步骤 7：驱动映射按 (频率, 源功率) 查找，区间内插值、禁止外推。"""

    def setUp(self):
        self.readings = _driver_readings()

    def _port(self, *, strict=False):
        port = _activated_port(
            self.readings, MeasurementType.DRIVER_POWER_MAPPING, strict=strict
        )
        port.set_frequency(1.0)
        return port

    def test_exact_record_point_returns_recorded_raw(self):
        port = self._port()
        port.set_power(-70.0)
        self.assertEqual(port.measure_power_with_average(), -70.8)

    def test_power_requests_are_quantized_like_the_signal_generator_command(self):
        """真实驱动按 6 位有效数字下发功率，冻结记录点也是量化值。"""

        from instrument.drivers.signal_generator import SignalGeneratorDriver

        port = _activated_port(
            _recorded_driver_readings(),
            MeasurementType.DRIVER_POWER_MAPPING,
            strict=True,
        )
        port.set_frequency(1.0)
        request = -69.752975  # 自适应搜索的真实请求；仪器实际收到 -69.753
        self.assertEqual(
            float(SignalGeneratorDriver._format_number(request)), -69.753
        )
        port.set_power(request)
        self.assertEqual(port.measure_power_with_average(), -70.558)
        self.assertIn(("set_power", -69.753), port.recorded_commands)

    def test_quantization_does_not_widen_the_recorded_point_tolerance(self):
        port = _activated_port(
            _recorded_driver_readings(),
            MeasurementType.DRIVER_POWER_MAPPING,
            strict=True,
        )
        port.set_frequency(1.0)
        port.set_power(-69.7525)  # 量化后仍是 -69.7525，不是记录点
        with self.assertRaises(SimulationPortError):
            port.measure_power_with_average()
        self.assertEqual(dict(port.interpolation_counts), {})

    def test_repeated_reads_return_the_same_value_without_interpolating(self):
        port = self._port()
        port.set_power(-65.0)
        first = port.measure_power_with_average()
        second = port.measure_power_with_average()
        self.assertEqual(first, second)
        self.assertEqual(first, -65.8)
        counts = port.interpolation_counts
        self.assertEqual(sum(counts.values()), 1)

    def test_interpolation_stays_between_neighbouring_records(self):
        port = self._port()
        port.set_power(-68.0)
        value = port.measure_power_with_average()
        self.assertAlmostEqual(value, -68.8)
        self.assertGreaterEqual(value, -70.8)
        self.assertLessEqual(value, -60.8)

    def test_out_of_range_power_is_rejected_without_extrapolation(self):
        port = self._port()
        for power in (-80.0, -30.0):
            port.set_power(power)
            with self.subTest(power=power):
                with self.assertRaises(SimulationPortError) as raised:
                    port.measure_power_with_average()
                self.assertIn("readings.json", str(raised.exception))
        self.assertEqual(dict(port.interpolation_counts), {})

    def test_unknown_frequency_lists_the_available_grid(self):
        port = self._port()
        port.set_frequency(3.0)
        port.set_power(-70.0)
        with self.assertRaises(SimulationPortError) as raised:
            port.measure_power_with_average()
        self.assertIn("1.0", str(raised.exception))
        self.assertIn("2.0", str(raised.exception))

    def test_strict_mode_only_accepts_recorded_points(self):
        port = self._port(strict=True)
        port.set_power(-70.0)
        self.assertEqual(port.measure_power_with_average(), -70.8)
        port.set_power(-65.0)
        with self.assertRaises(SimulationPortError) as raised:
            port.measure_power_with_average()
        self.assertIn("严格模式", str(raised.exception))

    def test_reactivation_clears_lookup_state_and_counts(self):
        port = self._port()
        port.set_power(-65.0)
        self.assertAlmostEqual(port.measure_power_with_average(), -65.8)
        port.end_stage()
        port.begin_stage()
        port.activate(MeasurementType.DRIVER_POWER_MAPPING)
        self.assertEqual(dict(port.interpolation_counts), {})
        port.set_frequency(1.0)
        port.set_power(-65.0)
        self.assertAlmostEqual(port.measure_power_with_average(), -65.8)
        self.assertEqual(
            sum(port.interpolation_counts.values()), 1
        )


class SimulationAmplifierReadingTests(unittest.TestCase):
    """步骤 8：主功放扫描点、V/I 绑定与插值。"""

    def setUp(self):
        self.readings = _amplifier_readings()

    def _port(self, *, strict=False):
        port = _activated_port(self.readings, MeasurementType.AMPLIFIER, strict=strict)
        port.set_frequency(1.0)
        return port

    def test_exact_point_binds_recorded_voltage_and_current(self):
        port = self._port()
        port.set_power(-60.0)
        self.assertEqual(port.measure_power_with_average(), -26.0)
        self.assertEqual(port.read_voltage("SUP", "CH1"), 1.0)
        self.assertEqual(port.read_voltage("SUP", "CH2"), 2.0)
        self.assertEqual(port.read_current("SUP", "CH1"), 0.1)
        self.assertEqual(port.read_current("SUP", "CH2"), 0.2)

    def test_voltage_read_before_any_consumed_point_fails(self):
        port = self._port()
        with self.assertRaises(SimulationPortStateError) as raised:
            port.read_voltage("SUP", "CH1")
        self.assertIn("measure_power_with_average", str(raised.exception))

    def test_unknown_channel_is_rejected_with_available_channels(self):
        port = self._port()
        port.set_power(-60.0)
        port.measure_power_with_average()
        with self.assertRaises(SimulationPortError) as raised:
            port.read_current("SUP", "CH9")
        self.assertIn("SUP_CH1", str(raised.exception))
        self.assertIn("readings.json", str(raised.exception))

    def test_interpolated_point_interpolates_raw_and_vi(self):
        port = self._port()
        port.set_power(-55.0)
        self.assertAlmostEqual(port.measure_power_with_average(), -21.0)
        self.assertAlmostEqual(port.read_voltage("SUP", "CH1"), 2.0)
        self.assertAlmostEqual(port.read_voltage("SUP", "CH2"), 3.0)
        self.assertAlmostEqual(port.read_current("SUP", "CH1"), 0.2)
        self.assertAlmostEqual(port.read_current("SUP", "CH2"), 0.3)
        self.assertEqual(sum(port.interpolation_counts.values()), 1)

    def test_out_of_range_power_is_rejected(self):
        port = self._port()
        for power in (-70.0, -40.0):
            port.set_power(power)
            with self.subTest(power=power):
                with self.assertRaises(SimulationPortError):
                    port.measure_power_with_average()

    def test_binding_follows_the_last_successful_measurement(self):
        port = self._port()
        port.set_power(-60.0)
        port.measure_power_with_average()
        port.set_power(-50.0)
        port.measure_power_with_average()
        self.assertEqual(port.read_voltage("SUP", "CH1"), 3.0)
        # 失败的读数不改变绑定：仍然是最近一次成功的点。
        port.set_power(-40.0)
        with self.assertRaises(SimulationPortError):
            port.measure_power_with_average()
        self.assertEqual(port.read_voltage("SUP", "CH1"), 3.0)

    def test_strict_mode_only_accepts_recorded_points(self):
        port = self._port(strict=True)
        port.set_power(-55.0)
        with self.assertRaises(SimulationPortError):
            port.measure_power_with_average()

    def test_voltage_read_after_settlement_requires_new_consumption(self):
        port = self._port()
        port.set_power(-60.0)
        port.measure_power_with_average()
        port.end_stage()
        port.begin_stage()
        port.activate(MeasurementType.AMPLIFIER)
        with self.assertRaises(SimulationPortStateError):
            port.read_voltage("SUP", "CH1")

    def test_voltage_read_in_another_stage_is_a_state_error(self):
        port = _activated_port(
            _cable_loss_readings(), MeasurementType.CABLE_LOSS
        )
        with self.assertRaises(SimulationPortStateError):
            port.read_voltage("SUP", "CH1")


class SimulationPortSafetyCleanupTests(unittest.TestCase):
    """步骤 9：能力、安全清理与导入边界。"""

    def test_cleanup_closes_the_port_and_reports_every_step(self):
        port = _activated_port(
            _amplifier_readings(), MeasurementType.AMPLIFIER
        )
        port.set_frequency(1.0)
        port.set_power(-60.0)
        port.measure_power_with_average()
        port.power_on_sequence()
        port.rf_output_on()

        record = SafetyCleanupAdapter(port).cleanup(
            power_cleanup=port.power_off_sequence
        )

        self.assertTrue(record.succeeded)
        self.assertEqual(record.rf_output, CleanupStepStatus.PERFORMED)
        self.assertEqual(record.power_supplies, CleanupStepStatus.PERFORMED)
        self.assertEqual(record.resources, CleanupStepStatus.PERFORMED)
        self.assertTrue(port.closed)

    def test_cleanup_without_release_keeps_the_port_open(self):
        """worker 以 ``owns_measurement_port=False`` 使用端口：清理不停用端口。"""

        port = _activated_port(
            _amplifier_readings(), MeasurementType.AMPLIFIER
        )
        port.rf_output_on()

        record = SafetyCleanupAdapter(port).cleanup(
            power_cleanup=port.power_off_sequence, release_resources=False
        )

        self.assertTrue(record.succeeded)
        self.assertEqual(record.rf_output, CleanupStepStatus.PERFORMED)
        self.assertEqual(record.resources, CleanupStepStatus.SKIPPED)
        self.assertFalse(port.closed)
        self.assertIs(port.active_stage, MeasurementType.AMPLIFIER)

    def test_port_module_does_not_depend_on_visa_drivers_transport_or_files(self):
        tree = ast.parse(PORT_SOURCE.read_text(encoding="utf-8"), str(PORT_SOURCE))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        forbidden_roots = {"pyvisa", "visa", "scpi", "pathlib", "json"}
        forbidden_modules = {
            "instrument.drivers",
            "instrument.transport",
            "instrument.session",
            "instrument.measurement_factory",
            "instrument.measurement_adapter",
            "infrastructure.filesystem.paths",
        }
        offenders = sorted(
            module
            for module in modules
            if module.split(".")[0] in forbidden_roots
            or module in forbidden_modules
            or any(module.startswith(name + ".") for name in forbidden_modules)
        )
        self.assertEqual(
            offenders,
            [],
            "仿真端口不得依赖 VISA/驱动/传输，也不得直接读文件",
        )


if __name__ == "__main__":
    unittest.main()
