r"""步骤 1 测试：目标 DUT 输入功率网格的纯计算契约。

覆盖 ``application.dto.target_input_power_grid``：

1. 整步上限：扫描结束正好落在步进网格上时，终点只出现一次；
2. 非整步上限：上限不在网格上时作为额外的最后一点补入（不偷偷截断，也不越过）；
3. DUT 限制低于扫描结束：取两者较小值作为有效上限，不生成越界点；
4. 限制点补入：限制值不在网格上时同样补入为最后一点；
5. 起始点越限：起始功率高于有效上限时明确拒绝配置；
6. 浮点边界：0.1dB 步进的 0.3dB 端点不会重复，末点被吸附到上限本身；
7. 容差内的起始点：略高或略低于上限都收敛为单个上限点，超出容差则拒绝配置。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_target_input_power_grid -v
```
"""

import unittest

from application.dto import (
    AmplifierInputError,
    PowerSweep,
    target_input_power_grid,
)
from domain.measurement.values import Decibels, PowerDbm


def _grid(start, stop, step, limit):
    return [
        point.dbm
        for point in target_input_power_grid(
            PowerSweep(
                start=PowerDbm(start),
                stop=PowerDbm(stop),
                step=Decibels(step),
            ),
            PowerDbm(limit),
        )
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


if __name__ == "__main__":
    unittest.main()
