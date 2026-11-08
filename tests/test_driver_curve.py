"""步骤 5：驱放曲线校验与反向插值。

覆盖计划中的验收点：

- 恒定增益映射可以直接反解；
- 增益压缩导致斜率下降时仍可反解，不要求恒定增益；
- 目标恰好命中实测点、落在两点之间时都返回源功率与插值输出；
- 目标超出实测覆盖范围时禁止外推；
- 重复源功率、重复输出、非单调输出给出具体频率/区间错误；
- 输出必须严格递增：极小正增量可通过，零或负增量一律拒绝（容差只用于错误分类）；
- 反查的覆盖边界与命中判断精确，不因容差吸附到端点，中间目标输出等于目标本身。
"""

import sys
import unittest
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from domain.measurement.values import (
    DRIVER_OUTPUT_TOLERANCE_DB,
    DriverCurve,
    DriverCurveTarget,
    Frequency,
    MeasurementValueError,
    PowerDbm,
)


def ghz(value: float) -> Frequency:
    return Frequency.from_ghz(value)


def dbm(value: float) -> PowerDbm:
    return PowerDbm(value)


def curve(points, *, frequency: float = 1.0) -> DriverCurve:
    return DriverCurve(
        ghz(frequency),
        tuple((dbm(source), dbm(output)) for source, output in points),
    )


class DriverCurveValidationTests(unittest.TestCase):
    """曲线构造时完成排序与可反查性校验。"""

    def test_points_are_sorted_by_source_power(self):
        unordered = curve([(-30, 0.0), (-40, -10.0), (-20, 10.0)])

        self.assertEqual(
            [source.dbm for source, _output in unordered.points],
            [-40.0, -30.0, -20.0],
        )
        result = unordered.reverse_interpolate(dbm(-5.0))
        self.assertAlmostEqual(result.source_power.dbm, -35.0)

    def test_duplicate_source_power_is_rejected_with_frequency(self):
        with self.assertRaises(MeasurementValueError) as raised:
            curve([(-40, 20.0), (-30, 29.0), (-40, 21.0)])

        message = str(raised.exception)
        self.assertIn("1.0", message)
        self.assertIn("-40.0", message)
        self.assertIn("重复", message)

    def test_duplicate_output_power_is_rejected_with_interval(self):
        with self.assertRaises(MeasurementValueError) as raised:
            curve([(-40, 20.0), (-30, 20.0)])

        message = str(raised.exception)
        self.assertIn("1.0", message)
        self.assertIn("[-40.0, -30.0]", message)
        self.assertIn("重复", message)

    def test_decreasing_output_is_rejected_with_frequency_and_interval(self):
        with self.assertRaises(MeasurementValueError) as raised:
            curve([(-40, 20.0), (-30, 19.0)])

        message = str(raised.exception)
        self.assertIn("1.0", message)
        self.assertIn("[-40.0, -30.0]", message)
        self.assertIn("下降", message)

    def test_decreasing_output_is_not_hidden_by_sorting(self):
        with self.assertRaises(MeasurementValueError):
            curve([(-20, 37.0), (-30, 30.0), (-40, 20.0), (-25, 29.0)])

    def test_tiny_positive_increment_is_accepted_as_approximately_monotonic(self):
        barely_rising = 20.0 + 0.5 * DRIVER_OUTPUT_TOLERANCE_DB
        compressed = curve([(-40, 20.0), (-39, barely_rising), (-38, 21.0)])

        target = dbm(20.0 + 0.25 * DRIVER_OUTPUT_TOLERANCE_DB)
        result = compressed.reverse_interpolate(target)
        # 1e-9 量级的差值已接近 double 在 20 dBm 附近的分辨率，
        # 这里只验证反查落在正确区间且输出等于目标，不做绝对精度断言。
        self.assertLess(-40.0, result.source_power.dbm)
        self.assertGreater(-39.0, result.source_power.dbm)
        self.assertEqual(result.driver_output, target)

    def test_tiny_negative_increment_is_rejected_as_platform(self):
        barely_falling = 20.0 - 0.5 * DRIVER_OUTPUT_TOLERANCE_DB
        with self.assertRaises(MeasurementValueError) as raised:
            curve([(-40, 20.0), (-39, barely_falling), (-38, 21.0)])

        message = str(raised.exception)
        self.assertIn("1.0", message)
        self.assertIn("[-40.0, -39.0]", message)
        self.assertIn("重复或近似平台", message)

    def test_negative_increment_at_tolerance_boundary_is_rejected(self):
        with self.assertRaises(MeasurementValueError) as raised:
            curve([(-40, 20.0), (-39, 20.0 - DRIVER_OUTPUT_TOLERANCE_DB)])

        # 边界差值的分类受 double 精度影响，此处只要求拒绝并给出可定位信息。
        message = str(raised.exception)
        self.assertIn("[-40.0, -39.0]", message)

    def test_decreasing_increment_beyond_tolerance_reports_decrease(self):
        with self.assertRaises(MeasurementValueError) as raised:
            curve([(-40, 20.0), (-39, 20.0 - 2 * DRIVER_OUTPUT_TOLERANCE_DB)])

        self.assertIn("下降", str(raised.exception))

    def test_empty_curve_is_rejected(self):
        with self.assertRaises(MeasurementValueError) as raised:
            curve([])

        self.assertIn("没有有效映射点", str(raised.exception))

    def test_curve_rejects_raw_numbers_and_wrong_units(self):
        with self.assertRaises(TypeError):
            DriverCurve(ghz(1.0), ((20.0, 30.0),))  # type: ignore[arg-type]

    def test_curve_rejects_non_sequence_points(self):
        with self.assertRaises(TypeError):
            DriverCurve(ghz(1.0), "not-points")  # type: ignore[arg-type]


class DriverCurveReverseInterpolationTests(unittest.TestCase):
    """分段线性反插值：目标输出 -> 源功率 + 插值输出。"""

    def test_constant_gain_curve_is_reversible(self):
        constant_gain = curve([(-40, -10.0), (-30, 0.0), (-20, 10.0)])

        below = constant_gain.reverse_interpolate(dbm(-5.0))
        above = constant_gain.reverse_interpolate(dbm(5.0))

        self.assertIsInstance(below, DriverCurveTarget)
        self.assertAlmostEqual(below.source_power.dbm, -35.0)
        self.assertAlmostEqual(below.driver_output.dbm, -5.0)
        self.assertAlmostEqual(above.source_power.dbm, -25.0)
        self.assertAlmostEqual(above.driver_output.dbm, 5.0)

    def test_compression_curve_with_decreasing_slope_is_reversible(self):
        compressed = curve([(-40, 20.0), (-30, 29.0), (-20, 37.0)])

        low_segment = compressed.reverse_interpolate(dbm(24.5))
        high_segment = compressed.reverse_interpolate(dbm(33.0))

        self.assertAlmostEqual(low_segment.source_power.dbm, -35.0)
        self.assertAlmostEqual(low_segment.driver_output.dbm, 24.5)
        self.assertAlmostEqual(high_segment.source_power.dbm, -25.0)
        self.assertAlmostEqual(high_segment.driver_output.dbm, 33.0)

    def test_target_exactly_hits_a_measured_point(self):
        compressed = curve([(-40, 20.0), (-30, 29.0), (-20, 37.0)])

        result = compressed.reverse_interpolate(dbm(29.0))

        self.assertEqual(result.target_input, dbm(29.0))
        self.assertEqual(result.source_power, dbm(-30.0))
        self.assertEqual(result.driver_output, dbm(29.0))

    def test_target_between_two_points_uses_piecewise_linear_interpolation(self):
        two_points = curve([(-40, 20.0), (-20, 40.0)])

        middle = two_points.reverse_interpolate(dbm(30.0))
        quarter = two_points.reverse_interpolate(dbm(25.0))

        self.assertAlmostEqual(middle.source_power.dbm, -30.0)
        self.assertAlmostEqual(middle.driver_output.dbm, 30.0)
        self.assertAlmostEqual(quarter.source_power.dbm, -35.0)
        self.assertAlmostEqual(quarter.driver_output.dbm, 25.0)

    def test_interpolated_output_is_the_target_input(self):
        compressed = curve([(-40, 20.0), (-30, 29.0), (-20, 37.0)])

        result = compressed.reverse_interpolate(dbm(24.5))

        self.assertEqual(result.driver_output, dbm(24.5))
        self.assertAlmostEqual(result.source_power.dbm, -35.0)

    def test_narrow_segment_uses_actual_ratio_not_midpoint(self):
        narrow = curve([(-40, 20.0), (-39, 20.0 + 4 * DRIVER_OUTPUT_TOLERANCE_DB)])

        result = narrow.reverse_interpolate(dbm(20.0 + DRIVER_OUTPUT_TOLERANCE_DB))

        self.assertAlmostEqual(result.source_power.dbm, -39.75)

    def test_measured_endpoints_are_inside_the_coverage(self):
        table = curve([(-40, 20.0), (-30, 29.0)])

        low = table.reverse_interpolate(dbm(20.0))
        high = table.reverse_interpolate(dbm(29.0))

        self.assertEqual(low.source_power, dbm(-40.0))
        self.assertEqual(low.driver_output, dbm(20.0))
        self.assertEqual(high.source_power, dbm(-30.0))
        self.assertEqual(high.driver_output, dbm(29.0))

    def test_targets_outside_the_coverage_are_rejected_without_extrapolation(self):
        table = curve([(-40, 20.0), (-30, 29.0)])
        for target in (
            19.999,
            29.001,
            20.0 - DRIVER_OUTPUT_TOLERANCE_DB,
            29.0 + DRIVER_OUTPUT_TOLERANCE_DB,
        ):
            with self.subTest(target=target):
                with self.assertRaises(MeasurementValueError) as raised:
                    table.reverse_interpolate(dbm(target))
                message = str(raised.exception)
                self.assertIn("1.0", message)
                self.assertIn("禁止外推", message)

    def test_single_point_curve_only_accepts_the_measured_output(self):
        single = curve([(-40, 20.0)])

        hit = single.reverse_interpolate(dbm(20.0))
        self.assertEqual(hit.source_power, dbm(-40.0))
        self.assertEqual(hit.driver_output, dbm(20.0))

        with self.assertRaises(MeasurementValueError):
            single.reverse_interpolate(dbm(20.5))

    def test_targets_are_resolved_in_input_order(self):
        table = curve([(-40, 20.0), (-30, 29.0), (-20, 37.0)])

        results = table.reverse_interpolate_targets(
            [dbm(33.0), dbm(24.5), dbm(29.0)]
        )

        self.assertEqual(
            [round(result.target_input.dbm, 6) for result in results],
            [33.0, 24.5, 29.0],
        )
        self.assertEqual(
            [round(result.source_power.dbm, 6) for result in results],
            [-25.0, -35.0, -30.0],
        )

    def test_reverse_interpolation_rejects_raw_numbers(self):
        table = curve([(-40, 20.0), (-30, 29.0)])

        with self.assertRaises(TypeError):
            table.reverse_interpolate(24.5)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
