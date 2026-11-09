r"""步骤 1 测试：目标 DUT 输入功率网格的纯计算契约。

覆盖 ``application.dto.target_input_power_grid``：

1. 整步上限：扫描结束正好落在步进网格上时，终点只出现一次；
2. 非整步上限：上限不在网格上时作为额外的最后一点补入（不偷偷截断，也不越过）；
3. DUT 限制低于扫描结束：取两者较小值作为有效上限，不生成越界点；
4. 限制点补入：限制值不在网格上时同样补入为最后一点；
5. 起始点越限：起始功率高于有效上限时明确拒绝配置；
6. 浮点边界：0.1dB 步进的 0.3dB 端点不会重复，末点被吸附到上限本身；
7. 容差内的起始点：略高或略低于上限都收敛为单个上限点，超出容差则拒绝配置。

覆盖步骤 1.2 的多段合并：

8. 多段不同步长按顺序生成，相邻计划的公共边界只保留一次；
9. 每段非整步终止点都作为额外最后一点出现；
10. DUT 上限落在某段中间时截断该段并跳过后续整段；
11. 输出始终升序且无重复；
12. 单个 ``PowerSweep`` 仍可由同一入口消费（兼容单计划调用方）；
13. 单段预览（``PowerSweep.points``）与合并入口对同一段给出同一组点；
14. 合并入口先做连续性校验，空档不会被 DUT 上限截断掩盖。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_target_input_power_grid -v
```
"""

import unittest

from application.dto import (
    AmplifierInputError,
    PowerScanSchedule,
    PowerSweep,
    target_input_power_grid,
)
from domain.measurement.values import Decibels, PowerDbm


def _sweep(start, stop, step):
    return PowerSweep(
        start=PowerDbm(start),
        stop=PowerDbm(stop),
        step=Decibels(step),
    )


def _grid(start, stop, step, limit):
    points = target_input_power_grid(_sweep(start, stop, step), PowerDbm(limit))
    return [point.dbm for point in points]


def _schedule(*plans):
    return PowerScanSchedule(
        plans=tuple(_sweep(start, stop, step) for start, stop, step in plans)
    )


def _schedule_grid(plans, limit):
    return [
        point.dbm
        for point in target_input_power_grid(_schedule(*plans), PowerDbm(limit))
    ]


class TargetInputPowerGridTests(unittest.TestCase):
    def test_stop_on_the_step_grid_appears_once(self):
        self.assertEqual(_grid(-20.0, 0.0, 1.0, 10.0), list(range(-20, 1)))

    def test_stop_off_the_step_grid_is_appended_as_the_last_point(self):
        self.assertEqual(_grid(0.0, 1.0, 0.6, 5.0), [0.0, 0.6, 1.0])

    def test_dut_limit_below_stop_caps_the_grid(self):
        self.assertEqual(_grid(0.0, 10.0, 2.0, 6.0), [0.0, 2.0, 4.0, 6.0])

    def test_dut_limit_off_the_grid_is_appended_without_passing_it(self):
        grid = _grid(0.0, 10.0, 4.0, 6.0)

        self.assertEqual(grid, [0.0, 4.0, 6.0])
        self.assertLessEqual(max(grid), 6.0)

    def test_start_above_the_effective_limit_is_rejected(self):
        with self.assertRaises(AmplifierInputError) as raised:
            _grid(-1.0, 5.0, 1.0, -2.0)

        message = str(raised.exception)
        self.assertIn("目标输入功率起始值高于有效上限", message)
        self.assertIn("-1.0dBm", message)
        self.assertIn("-2.0dBm", message)

    def test_start_equal_to_the_effective_limit_yields_one_point(self):
        self.assertEqual(_grid(3.0, 10.0, 1.0, 3.0), [3.0])

    def test_floating_point_endpoint_is_snapped_to_the_limit(self):
        grid = _grid(0.0, 0.3, 0.1, 1.0)

        self.assertEqual(len(grid), 4)
        self.assertEqual(grid, [0.0, 0.1, 0.2, 0.3])
        self.assertEqual(grid[-1], 0.3)

    def test_floating_point_start_and_limit_do_not_duplicate_a_point(self):
        # 1e-16 级的“略高”来自上游浮点运算，不是用户真的把起点设在限制之上。
        grid = _grid(1.0000000000000002, 2.0, 1.0, 1.0)

        self.assertEqual(grid, [1.0])

    def test_floating_point_start_slightly_below_the_limit_snaps_to_the_limit(self):
        # 略低于上限的起点与上限只差浮点误差：视为同一点，返回上限本身。
        grid = _grid(1.0 - 1e-13, 2.0, 1.0, 1.0)

        self.assertEqual(grid, [1.0])

    def test_start_above_the_limit_beyond_tolerance_is_rejected(self):
        # 1e-9 已超出 1e-12 量级的浮点容差，是真实的越限配置，必须拒绝。
        with self.assertRaises(AmplifierInputError):
            _grid(1.0 + 1e-9, 2.0, 1.0, 1.0)

    def test_grid_is_monotonic_and_never_passes_the_limit(self):
        for start, stop, step, limit in (
            (-50.0, 20.0, 0.1, 7.3),
            (-20.0, 20.0, 0.25, 3.15),
            (-10.0, -3.0, 0.7, -3.4),
            (0.0, 9.0, 3.0, 8.0),
        ):
            with self.subTest(start=start, stop=stop, step=step, limit=limit):
                grid = _grid(start, stop, step, limit)
                self.assertTrue(grid)
                self.assertTrue(all(isinstance(point, float) for point in grid))
                self.assertEqual(grid, sorted(grid))
                self.assertLessEqual(max(grid), min(stop, limit) + 1e-12)
                self.assertEqual(grid[0], start)
                self.assertEqual(grid[-1], min(stop, limit))

    def test_values_are_power_value_objects_and_validation_is_explicit(self):
        points = target_input_power_grid(
            PowerSweep(
                start=PowerDbm(-2.0),
                stop=PowerDbm(0.0),
                step=Decibels(1.0),
            ),
            PowerDbm(0.0),
        )

        self.assertIsInstance(points, tuple)
        self.assertTrue(all(isinstance(point, PowerDbm) for point in points))
        for sweep, limit in (
            ((-20.0, 0.0, 1.0), 0.0),
            (PowerSweep(start=PowerDbm(-2.0), stop=PowerDbm(0.0), step=Decibels(1.0)), "0"),
        ):
            with self.subTest(sweep=sweep, limit=limit):
                with self.assertRaises(AmplifierInputError):
                    target_input_power_grid(sweep, limit)


class PowerScanScheduleGridTests(unittest.TestCase):
    def test_continuous_plans_share_the_boundary_once(self):
        grid = _schedule_grid(
            ((-38.0, -10.0, 1.0), (-10.0, -2.0, 0.25)),
            limit=10.0,
        )

        self.assertEqual(grid[0], -38.0)
        self.assertEqual(grid[-1], -2.0)
        self.assertEqual(grid.count(-10.0), 1)
        self.assertEqual(grid, sorted(grid))
        self.assertEqual(len(grid), len(set(grid)))
        # 粗段 -38..-10 共 29 点；细段 -10..-2 步长 0.25 共 33 点，共享 -10。
        self.assertEqual(len(grid), 29 + 33 - 1)

    def test_non_grid_segment_stops_are_all_visible(self):
        grid = _schedule_grid(
            ((0.0, 1.0, 0.6), (1.0, 1.5, 0.2)),
            limit=10.0,
        )

        self.assertEqual(grid, [0.0, 0.6, 1.0, 1.2, 1.4, 1.5])

    def test_dut_limit_caps_the_schedule_mid_segment(self):
        grid = _schedule_grid(
            ((-20.0, 0.0, 1.0), (0.0, 10.0, 0.25)),
            limit=5.0,
        )

        self.assertEqual(grid[-1], 5.0)
        self.assertEqual(grid.count(0.0), 1)
        self.assertTrue(all(point <= 5.0 for point in grid))
        # 粗段 -20..0 共 21 点；细段 0..5 共 21 点，共享 0。
        self.assertEqual(len(grid), 21 + 21 - 1)

    def test_dut_limit_off_the_step_grid_is_appended(self):
        grid = _schedule_grid(
            ((-10.0, 0.0, 3.0), (0.0, 10.0, 4.0)),
            limit=6.0,
        )

        self.assertEqual(grid, [-10.0, -7.0, -4.0, -1.0, 0.0, 4.0, 6.0])

    def test_single_point_plan_is_kept(self):
        grid = _schedule_grid(((-5.0, -5.0, 1.0),), limit=10.0)

        self.assertEqual(grid, [-5.0])

    def test_first_plan_above_the_effective_limit_is_rejected(self):
        with self.assertRaises(AmplifierInputError) as raised:
            _schedule_grid(((-1.0, 5.0, 1.0), (5.0, 6.0, 1.0)), limit=-2.0)

        message = str(raised.exception)
        self.assertIn("目标输入功率起始值高于有效上限", message)
        self.assertIn("-1.0dBm", message)
        self.assertIn("-2.0dBm", message)

    def test_overlapping_plans_are_not_silently_deduplicated(self):
        with self.assertRaises(AmplifierInputError) as raised:
            _schedule_grid(((-20.0, -10.0, 1.0), (-12.0, 0.0, 1.0)), limit=10.0)

        message = str(raised.exception)
        self.assertIn("重叠", message)
        self.assertIn("第 1 段终止功率", message)
        self.assertIn("第 2 段起始功率", message)

    def test_a_gap_is_rejected_before_any_point_is_generated(self):
        with self.assertRaises(AmplifierInputError) as raised:
            _schedule_grid(((-20.0, -10.0, 1.0), (-8.0, 0.0, 1.0)), limit=10.0)

        message = str(raised.exception)
        self.assertIn("空档", message)
        self.assertIn("第 1 段终止功率 -10.0dBm", message)
        self.assertIn("第 2 段起始功率 -8.0dBm", message)

    def test_a_gap_above_the_dut_limit_is_still_rejected(self):
        # 上限只截断点位，不替代连续性校验；被截断的计划仍必须与上一段首尾相接。
        with self.assertRaises(AmplifierInputError) as raised:
            _schedule_grid(((0.0, 1.0, 1.0), (5.0, 6.0, 1.0)), limit=1.0)

        self.assertIn("空档", str(raised.exception))

    def test_a_single_segment_preview_matches_the_merged_grid(self):
        sweep = _sweep(0.0, 1.0, 0.6)

        merged = target_input_power_grid(sweep, PowerDbm(10.0))

        self.assertEqual(merged, sweep.points())

    def test_each_plan_preview_is_the_same_generator_as_the_merged_grid(self):
        plans = ((-38.0, -10.0, 1.0), (-10.0, -2.0, 0.25))
        schedule = _schedule(*plans)

        merged = [
            point.dbm
            for point in target_input_power_grid(schedule, PowerDbm(10.0))
        ]

        concatenated = []
        for plan in schedule.plans:
            for point in plan.points():
                if concatenated and abs(point.dbm - concatenated[-1]) <= 1e-12:
                    continue
                concatenated.append(point.dbm)

        self.assertEqual(merged, concatenated)

    def test_a_single_sweep_still_uses_the_same_entry(self):
        sweep = PowerSweep(
            start=PowerDbm(-2.0),
            stop=PowerDbm(0.0),
            step=Decibels(1.0),
        )

        self.assertEqual(
            [point.dbm for point in target_input_power_grid(sweep, PowerDbm(0.0))],
            [-2.0, -1.0, 0.0],
        )


if __name__ == "__main__":
    unittest.main()
