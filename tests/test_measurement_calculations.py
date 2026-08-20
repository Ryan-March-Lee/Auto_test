"""
measurement_calculations 纯计算函数的单元测试。

阶段 0-3：冻结公式和边界基线，覆盖纯函数和接入层委托验证。
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from measurement_calculations import (
    calculate_cable_losses,
    compensate_amplifier_output_power,
    compensate_driver_output_power,
    interpolate_driver_output_power,
    calculate_dut_input_power,
    calculate_gain,
    calculate_efficiency,
    calculate_compression_result,
)


SAMPLE_LOSS_DATA = {
    "1.0": {
        "cable1": 1.625,
        "cable2": 1.625,
        "cable3": 0.095,
        "cable4": 0.095,
        "total_path1": 33.25,
        "total_path2": 33.44,
    }
}


class CalculateCableLossesTests(unittest.TestCase):
    """测试 calculate_cable_losses() —— 线损分摊公式。"""

    # --- 正常值 ---

    def test_normal_values_match_snapshot_formula(self):
        """cable12 = (p1 - atten) / 2; cable34 = (p2 - p1) / 2。"""
        result = calculate_cable_losses(path1_loss=32.3, path2_loss=33.21,
                                        attenuator_value=30.0)
        self.assertAlmostEqual(result['cable1'], (32.3 - 30.0) / 2)
        self.assertAlmostEqual(result['cable2'], (32.3 - 30.0) / 2)
        self.assertAlmostEqual(result['cable3'], (33.21 - 32.3) / 2)
        self.assertAlmostEqual(result['cable4'], (33.21 - 32.3) / 2)
        self.assertAlmostEqual(result['total_path1'], 32.3)
        self.assertAlmostEqual(result['total_path2'], 33.21)

    def test_cable1_equals_cable2(self):
        """线①和线②假设相等。"""
        result = calculate_cable_losses(25.0, 30.0, 20.0)
        self.assertEqual(result['cable1'], result['cable2'])

    def test_cable3_equals_cable4(self):
        """线③和线④假设相等。"""
        result = calculate_cable_losses(25.0, 30.0, 20.0)
        self.assertEqual(result['cable3'], result['cable4'])

    def test_result_has_all_six_keys(self):
        """结果必须包含 cable1-cable4、total_path1、total_path2。"""
        result = calculate_cable_losses(10.0, 15.0, 5.0)
        expected_keys = {'cable1', 'cable2', 'cable3', 'cable4',
                         'total_path1', 'total_path2'}
        self.assertEqual(set(result.keys()), expected_keys)

    # --- 零值 ---

    def test_zero_path_losses_with_zero_attenuator(self):
        """路径损耗和衰减器均为零时，所有线缆损耗为零。"""
        result = calculate_cable_losses(0.0, 0.0, 0.0)
        for key in ('cable1', 'cable2', 'cable3', 'cable4',
                    'total_path1', 'total_path2'):
            self.assertEqual(result[key], 0.0)

    def test_zero_attenuator_only(self):
        """衰减器为零时，cable12 = p1 / 2。"""
        result = calculate_cable_losses(10.0, 14.0, 0.0)
        self.assertAlmostEqual(result['cable1'], 5.0)
        self.assertAlmostEqual(result['cable3'], 2.0)

    # --- 负值（线损可为负，因校准或测量误差） ---

    def test_negative_cable34_when_path2_less_than_path1(self):
        """path2 < path1 时 cable34 为负（如现场 2.3 GHz 数据）。"""
        result = calculate_cable_losses(32.58, 32.2, 30.0)
        self.assertAlmostEqual(result['cable3'],
                               (32.2 - 32.58) / 2)
        self.assertTrue(result['cable3'] < 0)

    def test_negative_cable12_when_path1_less_than_attenuator(self):
        """path1 < 衰减器时 cable12 为负。"""
        result = calculate_cable_losses(5.0, 10.0, 30.0)
        self.assertTrue(result['cable1'] < 0)
        self.assertTrue(result['cable2'] < 0)

    # --- 浮点精度 ---

    def test_float_precision_matches_snapshot(self):
        """与 cable_loss_results.json 中 0.8 GHz 的实际数据对比。"""
        # p1=32.3, p2=33.21, atten=30.0
        result = calculate_cable_losses(32.3, 33.21, 30.0)
        # cable1 = (32.3 - 30) / 2 = 1.15
        self.assertAlmostEqual(result['cable1'], 1.15, places=10)
        # cable3 = (33.21 - 32.3) / 2 = 0.455
        self.assertAlmostEqual(result['cable3'], 0.455, places=10)


class CompensateAmplifierOutputPowerTests(unittest.TestCase):
    """
    测试 compensate_amplifier_output_power()。

    公式：measured_power + cable4 + attenuator + cable2。
    对应 AmplifierMeasurement.calculate_actual_power()。
    """

    def _sample_loss_data(self):
        """返回一个频率 "1.0" 的线损字典样例。"""
        return {
            "1.0": {
                "cable1": 1.625,
                "cable2": 1.625,
                "cable3": 0.095,
                "cable4": 0.095,
                "total_path1": 33.25,
                "total_path2": 33.44,
            }
        }

    def test_normal_compensation(self):
        """读数 + cable4 + atten + cable2。"""
        loss_data = self._sample_loss_data()
        result = compensate_amplifier_output_power(
            measured_power=-10.0, frequency=1.0,
            loss_data=loss_data, attenuator_value=30.0)
        expected = -10.0 + 0.095 + 30.0 + 1.625
        self.assertAlmostEqual(result, expected)

    def test_zero_measured_power(self):
        """读数为 0 时结果 = cable4 + atten + cable2。"""
        loss_data = self._sample_loss_data()
        result = compensate_amplifier_output_power(
            measured_power=0.0, frequency=1.0,
            loss_data=loss_data, attenuator_value=30.0)
        expected = 0.095 + 30.0 + 1.625
        self.assertAlmostEqual(result, expected)

    def test_zero_attenuator(self):
        """衰减器值为 0 时只补偿 cable4 + cable2。"""
        loss_data = self._sample_loss_data()
        result = compensate_amplifier_output_power(
            measured_power=5.0, frequency=1.0,
            loss_data=loss_data, attenuator_value=0.0)
        expected = 5.0 + 0.095 + 1.625
        self.assertAlmostEqual(result, expected)

    def test_negative_cable4(self):
        """cable4 为负值时仍参与加法。"""
        loss_data = {
            "2.3": {
                "cable1": 1.29, "cable2": 1.29,
                "cable3": -0.19, "cable4": -0.19,
                "total_path1": 32.58, "total_path2": 32.2,
            }
        }
        result = compensate_amplifier_output_power(
            measured_power=0.0, frequency=2.3,
            loss_data=loss_data, attenuator_value=30.0)
        expected = 0.0 + (-0.19) + 30.0 + 1.29
        self.assertAlmostEqual(result, expected)

    def test_string_frequency_key_lookup(self):
        """频率以字符串形式存储在 loss_data 中，传入 float 也应能查到。"""
        loss_data = self._sample_loss_data()
        result = compensate_amplifier_output_power(
            measured_power=0.0, frequency=1.0,
            loss_data=loss_data, attenuator_value=30.0)
        # 不抛异常即说明查找成功
        self.assertAlmostEqual(result, 0.095 + 30.0 + 1.625)


class CompensateDriverOutputPowerTests(unittest.TestCase):
    """
    测试 compensate_driver_output_power()。

    公式：measured_power + attenuator + cable2。
    对应 DriverPowerMapping.calculate_actual_power()。
    不补偿 cable4，因为驱动功放输出端到频谱仪不经过线④。
    """

    def _sample_loss_data(self):
        return {
            "1.0": {
                "cable1": 1.625,
                "cable2": 1.625,
                "cable3": 0.095,
                "cable4": 0.095,
                "total_path1": 33.25,
                "total_path2": 33.44,
            }
        }

    def test_normal_compensation(self):
        """读数 + atten + cable2（不含 cable4）。"""
        loss_data = self._sample_loss_data()
        result = compensate_driver_output_power(
            measured_power=-10.0, frequency=1.0,
            loss_data=loss_data, attenuator_value=30.0)
        expected = -10.0 + 30.0 + 1.625
        self.assertAlmostEqual(result, expected)

    def test_does_not_include_cable4(self):
        """与主功放补偿相比，驱动补偿不应包含 cable4。"""
        loss_data = self._sample_loss_data()
        amp_result = compensate_amplifier_output_power(
            measured_power=0.0, frequency=1.0,
            loss_data=loss_data, attenuator_value=30.0)
        driver_result = compensate_driver_output_power(
            measured_power=0.0, frequency=1.0,
            loss_data=loss_data, attenuator_value=30.0)
        # 差值应为 cable4
        self.assertAlmostEqual(amp_result - driver_result,
                               loss_data["1.0"]["cable4"])

    def test_zero_measured_power(self):
        loss_data = self._sample_loss_data()
        result = compensate_driver_output_power(
            measured_power=0.0, frequency=1.0,
            loss_data=loss_data, attenuator_value=30.0)
        self.assertAlmostEqual(result, 30.0 + 1.625)

    def test_zero_attenuator(self):
        loss_data = self._sample_loss_data()
        result = compensate_driver_output_power(
            measured_power=5.0, frequency=1.0,
            loss_data=loss_data, attenuator_value=0.0)
        self.assertAlmostEqual(result, 5.0 + 1.625)

    def test_string_frequency_key_lookup(self):
        loss_data = self._sample_loss_data()
        result = compensate_driver_output_power(
            measured_power=0.0, frequency=1.0,
            loss_data=loss_data, attenuator_value=30.0)
        self.assertAlmostEqual(result, 30.0 + 1.625)


class MeasurementEntryPointDelegationTests(unittest.TestCase):
    """验证同步类和增强类的兼容入口确实委托给纯函数。"""

    def test_amplifier_entry_point_delegates_to_pure_function(self):
        from amplifier_measurement import AmplifierMeasurement

        measurement = AmplifierMeasurement.__new__(AmplifierMeasurement)
        measurement.config = {"attenuator": {"type": "30dB"}}
        measurement.loss_data = {"cable_losses": SAMPLE_LOSS_DATA}

        with patch("amplifier_measurement.compensate_amplifier_output_power",
                   return_value=123.4) as calculate:
            result = measurement.calculate_actual_power(1.0, -10.0)

        self.assertEqual(result, 123.4)
        calculate.assert_called_once_with(
            measured_power=-10.0,
            frequency=1.0,
            loss_data=SAMPLE_LOSS_DATA,
            attenuator_value=30.0,
        )

    def test_driver_entry_point_delegates_to_pure_function(self):
        from driver_power_mapping import DriverPowerMapping

        measurement = DriverPowerMapping.__new__(DriverPowerMapping)
        measurement.config = {"attenuator": {"type": "30dB"}}
        measurement.loss_data = {"cable_losses": SAMPLE_LOSS_DATA}

        with patch("driver_power_mapping.compensate_driver_output_power",
                   return_value=123.4) as calculate:
            result = measurement.calculate_actual_power(1.0, -10.0)

        self.assertEqual(result, 123.4)
        calculate.assert_called_once_with(
            measured_power=-10.0,
            frequency=1.0,
            loss_data=SAMPLE_LOSS_DATA,
            attenuator_value=30.0,
        )

    def test_driver_output_wrapper_delegates_to_interpolation(self):
        from amplifier_measurement import AmplifierMeasurement

        measurement = AmplifierMeasurement.__new__(AmplifierMeasurement)
        measurement.driver_mapping = SAMPLE_DRIVER_MAPPING

        with patch(
            "measurement_calculations.interpolate_driver_output_power",
            return_value=21.5,
        ) as calculate:
            result = measurement.get_driver_output_power(1.0, -39.0)

        self.assertEqual(result, 21.5)
        calculate.assert_called_once_with(
            frequency=1.0,
            sg_power=-39.0,
            driver_mapping=SAMPLE_DRIVER_MAPPING,
        )

    def test_enhanced_amplifier_inherits_the_same_entry_point(self):
        from enhanced_workers import EnhancedAmplifierMeasurement

        measurement = EnhancedAmplifierMeasurement.__new__(EnhancedAmplifierMeasurement)
        measurement.config = {"attenuator": {"type": "30dB"}}
        measurement.loss_data = {"cable_losses": SAMPLE_LOSS_DATA}

        with patch("amplifier_measurement.compensate_amplifier_output_power",
                   return_value=123.4) as calculate:
            result = measurement.calculate_actual_power(1.0, -10.0)

        self.assertEqual(result, 123.4)
        calculate.assert_called_once()

    def test_sync_cable_measurement_delegates_to_pure_function(self):
        from cable_loss_measurement import CableLossMeasurement

        measurement = CableLossMeasurement.__new__(CableLossMeasurement)
        measurement.config = {"test_frequencies": [1.0]}
        measurement.attenuator_value = 30.0
        measurement.cable_losses = {}
        measurement.measure_path_loss = lambda frequency: 32.3
        measurement.save_results = lambda: None

        result = {
            "cable1": 1.15, "cable2": 1.15,
            "cable3": 0.0, "cable4": 0.0,
            "total_path1": 32.3, "total_path2": 32.3,
        }
        with patch("builtins.input", return_value=""), patch(
            "cable_loss_measurement.calculate_cable_losses",
            return_value=result,
        ) as calculate:
            measurement.measure_all_frequencies()

        self.assertIs(measurement.cable_losses[1.0], result)
        calculate.assert_called_once_with(
            path1_loss=32.3,
            path2_loss=32.3,
            attenuator_value=30.0,
        )

    def test_enhanced_cable_measurement_delegates_to_pure_function(self):
        from enhanced_workers import EnhancedCableLossMeasurement

        measurement = EnhancedCableLossMeasurement.__new__(EnhancedCableLossMeasurement)
        measurement.config = {"test_frequencies": [1.0]}
        measurement.attenuator_value = 30.0
        measurement.path1_losses = {1.0: 32.3}
        measurement.cable_losses = {}
        measurement.should_stop = False
        measurement.save_results = lambda: None
        measurement.emit_message = lambda message: None
        measurement.emit_progress = lambda value: None
        measurement.measure_path_loss = lambda frequency: 33.21

        result = {
            "cable1": 1.15, "cable2": 1.15,
            "cable3": 0.455, "cable4": 0.455,
            "total_path1": 32.3, "total_path2": 33.21,
        }
        with patch(
            "enhanced_workers.calculate_cable_losses",
            return_value=result,
        ) as calculate:
            measurement._measure_step2()

        self.assertIs(measurement.cable_losses[1.0], result)
        calculate.assert_called_once_with(
            path1_loss=32.3,
            path2_loss=33.21,
            attenuator_value=30.0,
        )


# ---------------------------------------------------------------------------
# 阶段 2 测试：驱动插值和 DUT 输入功率
# ---------------------------------------------------------------------------

# 一个有序的驱动映射样例（频率 1.0 GHz）
SAMPLE_DRIVER_MAPPING = {
    "1.0": {
        "-50.0": 10.0,
        "-45.0": 15.0,
        "-40.0": 20.0,
        "-35.0": 25.0,
        "-30.0": 30.0,
    }
}

# 一个乱序的驱动映射样例
SAMPLE_DRIVER_MAPPING_UNORDERED = {
    "1.0": {
        "-30.0": 30.0,
        "-50.0": 10.0,
        "-40.0": 20.0,
        "-45.0": 15.0,
        "-35.0": 25.0,
    }
}


class InterpolateDriverOutputPowerTests(unittest.TestCase):
    """测试 interpolate_driver_output_power()。"""

    def test_in_range_interpolation(self):
        """范围内插值应与线性插值一致。"""
        result = interpolate_driver_output_power(
            frequency=1.0, sg_power=-42.5,
            driver_mapping=SAMPLE_DRIVER_MAPPING)
        # -42.5 在 -45(15) 和 -40(20) 之间，中间值为 17.5
        self.assertAlmostEqual(result, 17.5)

    def test_exact_key_match(self):
        """精确匹配某个键时返回对应值。"""
        result = interpolate_driver_output_power(
            frequency=1.0, sg_power=-40.0,
            driver_mapping=SAMPLE_DRIVER_MAPPING)
        self.assertAlmostEqual(result, 20.0)

    def test_below_range_uses_endpoint(self):
        """低于最小输入功率时返回端点值（np.interp 默认行为）。"""
        result = interpolate_driver_output_power(
            frequency=1.0, sg_power=-60.0,
            driver_mapping=SAMPLE_DRIVER_MAPPING)
        self.assertAlmostEqual(result, 10.0)

    def test_above_range_uses_endpoint(self):
        """高于最大输入功率时返回端点值。"""
        result = interpolate_driver_output_power(
            frequency=1.0, sg_power=-20.0,
            driver_mapping=SAMPLE_DRIVER_MAPPING)
        self.assertAlmostEqual(result, 30.0)

    def test_unordered_keys_are_sorted(self):
        """乱序键应自动排序，结果与有序映射一致。"""
        result_ordered = interpolate_driver_output_power(
            frequency=1.0, sg_power=-42.5,
            driver_mapping=SAMPLE_DRIVER_MAPPING)
        result_unordered = interpolate_driver_output_power(
            frequency=1.0, sg_power=-42.5,
            driver_mapping=SAMPLE_DRIVER_MAPPING_UNORDERED)
        self.assertAlmostEqual(result_ordered, result_unordered)

    def test_string_frequency_key_lookup(self):
        """频率以字符串存储，传入 float 也能查到。"""
        result = interpolate_driver_output_power(
            frequency=1.0, sg_power=-40.0,
            driver_mapping=SAMPLE_DRIVER_MAPPING)
        self.assertAlmostEqual(result, 20.0)


class CalculateDutInputPowerTests(unittest.TestCase):
    """测试 calculate_dut_input_power()。"""

    def test_with_driver_mapping(self):
        """有驱动时走插值路径。"""
        result = calculate_dut_input_power(
            sg_power=-40.0, frequency=1.0,
            loss_data=SAMPLE_LOSS_DATA,
            driver_mapping=SAMPLE_DRIVER_MAPPING)
        self.assertAlmostEqual(result, 20.0)

    def test_without_driver_mapping(self):
        """无驱动时走 sg_power - cable1 路径。"""
        result = calculate_dut_input_power(
            sg_power=-10.0, frequency=1.0,
            loss_data=SAMPLE_LOSS_DATA,
            driver_mapping=None)
        expected = -10.0 - SAMPLE_LOSS_DATA["1.0"]["cable1"]
        self.assertAlmostEqual(result, expected)

    def test_none_driver_mapping_takes_no_driver_path(self):
        """driver_mapping=None 和空映射都按当前行为走无驱动路径。"""
        result_none = calculate_dut_input_power(
            sg_power=-10.0, frequency=1.0,
            loss_data=SAMPLE_LOSS_DATA,
            driver_mapping=None)
        result_empty = calculate_dut_input_power(
            sg_power=-10.0, frequency=1.0,
            loss_data=SAMPLE_LOSS_DATA,
            driver_mapping={})
        self.assertAlmostEqual(result_none, -10.0 - 1.625)
        self.assertAlmostEqual(result_empty, -10.0 - 1.625)

    def test_driver_mode_with_range_outside(self):
        """驱动模式下超出范围时使用端点值。"""
        result = calculate_dut_input_power(
            sg_power=0.0, frequency=1.0,
            loss_data=SAMPLE_LOSS_DATA,
            driver_mapping=SAMPLE_DRIVER_MAPPING)
        self.assertAlmostEqual(result, 30.0)


# ---------------------------------------------------------------------------
# 阶段 3 测试：增益、效率和压缩点
# ---------------------------------------------------------------------------

class CalculateGainTests(unittest.TestCase):
    """测试 calculate_gain()。"""

    def test_normal_gain(self):
        self.assertAlmostEqual(calculate_gain(30.0, 10.0), 20.0)

    def test_zero_output(self):
        self.assertAlmostEqual(calculate_gain(0.0, 10.0), -10.0)

    def test_negative_gain(self):
        self.assertAlmostEqual(calculate_gain(5.0, 15.0), -10.0)


class CalculateEfficiencyTests(unittest.TestCase):
    """测试 calculate_efficiency()。"""

    def test_normal_efficiency(self):
        """30 dBm = 1 W，dc_power=10W -> 10%"""
        result = calculate_efficiency(30.0, 10.0)
        self.assertAlmostEqual(result, 10.0)

    def test_zero_dc_power_returns_zero(self):
        """DC 功耗为 0 时返回 0。"""
        self.assertEqual(calculate_efficiency(30.0, 0.0), 0)

    def test_negative_dc_power_returns_zero(self):
        """DC 功耗为负时返回 0。"""
        self.assertEqual(calculate_efficiency(30.0, -5.0), 0)

    def test_high_power(self):
        """45 dBm = 31.62W，dc_power=100W -> 31.62%"""
        result = calculate_efficiency(45.0, 100.0)
        expected = (10 ** ((45.0 - 30) / 10) / 100) * 100
        self.assertAlmostEqual(result, expected)


class CalculateCompressionResultTests(unittest.TestCase):
    """测试 calculate_compression_result()。"""

    def _make_sweep(self, n_points, gain_base=20.0, compression_at=None,
                    compression_value=5.0):
        """生成扫描数据列表。

        gain 从 gain_base 开始，在 compression_at 索引处下降 compression_value。
        """
        gains = []
        for i in range(n_points):
            if compression_at is not None and i >= compression_at:
                gains.append(gain_base - compression_value)
            else:
                gains.append(gain_base)
        input_powers = [10.0 + i for i in range(n_points)]
        output_powers = [input_powers[i] + gains[i] for i in range(n_points)]
        efficiencies = [50.0 + i for i in range(n_points)]
        sg_powers = [-10.0 + i for i in range(n_points)]
        return gains, input_powers, output_powers, efficiencies, sg_powers

    def test_compression_achieved(self):
        """正常达到压缩点。"""
        gains, inp, outp, eff, sg = self._make_sweep(10, compression_at=5)
        result = calculate_compression_result(
            gains=gains, input_powers=inp, output_powers=outp,
            efficiencies=eff, sg_powers=sg,
            compression_value=5.0, small_gain_points=3)

        self.assertTrue(result['compression_achieved'])
        self.assertAlmostEqual(result['small_signal_gain'], 20.0)
        self.assertAlmostEqual(
            result['compression_point']['compression_dB'], 5.0)
        self.assertAlmostEqual(result['max_compression'], 5.0)

    def test_compression_not_achieved(self):
        """未达到压缩点。"""
        gains, inp, outp, eff, sg = self._make_sweep(10, compression_at=None)
        result = calculate_compression_result(
            gains=gains, input_powers=inp, output_powers=outp,
            efficiencies=eff, sg_powers=sg,
            compression_value=5.0, small_gain_points=3)

        self.assertFalse(result['compression_achieved'])
        # 最接近的点是最后一个（压缩量为 0）
        self.assertAlmostEqual(
            result['compression_point']['compression_dB'], 0.0)
        self.assertAlmostEqual(result['max_compression'], 0.0)

    def test_fewer_than_3_points(self):
        """有效点不足 3 个时，小信号增益取第一个点。"""
        gains = [20.0, 19.0]
        inp = [10.0, 11.0]
        outp = [30.0, 30.0]
        eff = [50.0, 48.0]
        sg = [-10.0, -9.0]
        result = calculate_compression_result(
            gains=gains, input_powers=inp, output_powers=outp,
            efficiencies=eff, sg_powers=sg,
            compression_value=5.0, small_gain_points=3)

        self.assertAlmostEqual(result['small_signal_gain'], 20.0)
        self.assertFalse(result['compression_achieved'])

    def test_exactly_3_points(self):
        """恰好 3 个点时，小信号增益取 3 点平均。"""
        gains = [20.0, 20.0, 20.0]
        inp = [10.0, 11.0, 12.0]
        outp = [30.0, 31.0, 32.0]
        eff = [50.0, 51.0, 52.0]
        sg = [-10.0, -9.0, -8.0]
        result = calculate_compression_result(
            gains=gains, input_powers=inp, output_powers=outp,
            efficiencies=eff, sg_powers=sg,
            compression_value=5.0, small_gain_points=3)

        self.assertAlmostEqual(result['small_signal_gain'], 20.0)

    def test_early_stop_with_fewer_points(self):
        """扫描提前停止且不足 3 点时仍能计算。"""
        gains = [20.0]
        inp = [10.0]
        outp = [30.0]
        eff = [50.0]
        sg = [-10.0]
        result = calculate_compression_result(
            gains=gains, input_powers=inp, output_powers=outp,
            efficiencies=eff, sg_powers=sg,
            compression_value=5.0, small_gain_points=3)

        self.assertAlmostEqual(result['small_signal_gain'], 20.0)
        self.assertFalse(result['compression_achieved'])
        self.assertAlmostEqual(
            result['compression_point']['input_power'], 10.0)

    def test_empty_list_raises_value_error(self):
        """空列表保留当前抛异常行为（np.array([]).argmin() 抛 ValueError）。"""
        with self.assertRaises(ValueError):
            calculate_compression_result(
                gains=[], input_powers=[], output_powers=[],
                efficiencies=[], sg_powers=[],
                compression_value=5.0, small_gain_points=3)

    def test_result_has_expected_keys(self):
        """结果包含所有预期键。"""
        gains, inp, outp, eff, sg = self._make_sweep(10, compression_at=5)
        result = calculate_compression_result(
            gains=gains, input_powers=inp, output_powers=outp,
            efficiencies=eff, sg_powers=sg,
            compression_value=5.0, small_gain_points=3)

        self.assertIn('compression_point', result)
        self.assertIn('small_signal_gain', result)
        self.assertIn('compression_achieved', result)
        self.assertIn('max_compression', result)

        cp = result['compression_point']
        for key in ('input_power', 'output_power', 'gain',
                    'efficiency', 'compression_dB', 'sg_power_at_compression'):
            self.assertIn(key, cp)


if __name__ == "__main__":
    unittest.main()
