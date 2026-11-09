r"""步骤 1.1 测试：DUT 功率扫描计划的不可变值对象。

覆盖 ``application.dto.PowerScanSchedule``：

1. 多段 ``PowerSweep`` 按调用方给出的顺序原样保存（元组顺序就是执行顺序，不排序）；
2. 单段与单点扫描（``start == stop``）都是合法输入；
3. 空集合、缺省集合与无序容器在构造期明确失败；只接受 ``list``/``tuple``，
   构造后修改来源列表不会改变计划集合；
4. 非 ``PowerSweep`` 元素报出具体下标，便于映射到配置行；
5. 对象不可变，计划集合不会被测量过程中的 UI 状态改写；
6. 错误按层归属：非有限数值由功率值对象以 ``MeasurementValueError`` 拒绝，
   非正步长和起点大于终点由 ``PowerSweep`` 以 ``AmplifierInputError`` 拒绝。

本步骤只定义值对象，不生成点位、不校验相邻计划衔接，也不读取配置或接触 Qt。
相邻计划的连续性校验（步骤 1.3）由同一模块的
``validate_power_scan_schedule`` 负责，测试见本文件末尾的
``PlanContinuityTests``；点位生成见 ``tests.test_target_input_power_grid``。

验证方式（``application`` 层改动，使用仓库 runner，包含编译与依赖锁门禁；
只跑单个模块可改用 ``-m unittest tests.test_power_scan_schedule``，但不替代交付验证）：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" tools/run_tests.py
```
"""

import unittest
from collections import deque
from dataclasses import FrozenInstanceError

from application.dto import (
    AmplifierInputError,
    PowerScanSchedule,
    PowerSweep,
    validate_power_scan_schedule,
)
from domain.measurement.values import Decibels, MeasurementValueError, PowerDbm


def _sweep(start, stop, step=1.0):
    return PowerSweep(
        start=PowerDbm(start),
        stop=PowerDbm(stop),
        step=Decibels(step),
    )


class PowerScanScheduleTests(unittest.TestCase):
    def test_multiple_sweeps_keep_the_caller_order(self):
        coarse = _sweep(-38.0, -10.0)
        fine = _sweep(-10.0, -2.0, 0.25)

        schedule = PowerScanSchedule(plans=(fine, coarse))

        self.assertEqual(schedule.plans, (fine, coarse))
        self.assertEqual(list(schedule), [fine, coarse])
        self.assertEqual(len(schedule), 2)

    def test_plans_may_be_given_as_a_list_and_are_frozen_into_a_tuple(self):
        first = _sweep(-20.0, -10.0)
        second = _sweep(-10.0, 0.0, 0.5)

        schedule = PowerScanSchedule(plans=[first, second])

        self.assertIsInstance(schedule.plans, tuple)
        self.assertEqual(schedule.plans, (first, second))

    def test_mutating_the_source_list_does_not_change_the_schedule(self):
        first = _sweep(-20.0, -10.0)
        second = _sweep(-10.0, 0.0, 0.5)
        source = [first]

        schedule = PowerScanSchedule(plans=source)
        source.append(second)
        source.clear()

        self.assertEqual(schedule.plans, (first,))

    def test_single_plan_and_single_point_scan_are_allowed(self):
        single_point = _sweep(-5.0, -5.0)

        schedule = PowerScanSchedule(plans=(single_point,))

        self.assertEqual(schedule.plans, (single_point,))
        self.assertEqual(len(schedule), 1)

    def test_empty_plan_collection_is_rejected(self):
        with self.assertRaises(AmplifierInputError) as raised:
            PowerScanSchedule(plans=())

        self.assertIn("不能为空", str(raised.exception))
        self.assertIn("至少需要一段", str(raised.exception))

    def test_missing_plans_field_is_a_construction_error(self):
        with self.assertRaises(TypeError):
            PowerScanSchedule()

    def test_unordered_containers_are_rejected_instead_of_being_ordered(self):
        sweep = _sweep(-20.0, -10.0)
        for values in (
            {sweep},
            iter((sweep,)),
            {"plan": sweep},
            deque((sweep,)),
            "-20,-10",
        ):
            with self.subTest(values=type(values).__name__):
                with self.assertRaises(AmplifierInputError) as raised:
                    PowerScanSchedule(plans=values)
                self.assertIn("有序序列", str(raised.exception))

    def test_non_plan_elements_report_their_index(self):
        with self.assertRaises(AmplifierInputError) as raised:
            PowerScanSchedule(plans=(_sweep(-20.0, -10.0), object()))

        message = str(raised.exception)
        self.assertIn("功率扫描计划[1]", message)
        self.assertIn("PowerSweep", message)

    def test_invalid_sweeps_still_fail_at_their_own_boundary(self):
        for start, stop, step in ((0.0, -1.0, 0.1), (-1.0, 0.0, 0.0), (-1.0, 0.0, -0.5)):
            with self.subTest(start=start, stop=stop, step=step):
                with self.assertRaises(AmplifierInputError):
                    _sweep(start, stop, step)

    def test_non_finite_values_are_rejected_before_a_schedule_exists(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(MeasurementValueError):
                    PowerDbm(value)
                with self.assertRaises(MeasurementValueError):
                    Decibels(value)

    def test_schedule_is_immutable(self):
        schedule = PowerScanSchedule(plans=(_sweep(-20.0, -10.0),))

        with self.assertRaises(FrozenInstanceError):
            schedule.plans = ()


class PlanContinuityTests(unittest.TestCase):
    """步骤 1.3：相邻计划必须首尾相接，且错误能定位到计划行。"""

    def test_continuous_plans_pass_and_the_schedule_is_returned(self):
        schedule = PowerScanSchedule(
            plans=(_sweep(-38.0, -10.0), _sweep(-10.0, -2.0, 0.25))
        )

        self.assertIs(validate_power_scan_schedule(schedule), schedule)

    def test_a_single_plan_has_no_adjacent_pair_to_check(self):
        schedule = PowerScanSchedule(plans=(_sweep(-5.0, -5.0),))

        self.assertIs(validate_power_scan_schedule(schedule), schedule)

    def test_a_single_sweep_is_accepted_by_the_same_entry(self):
        sweep = _sweep(-20.0, 0.0)

        self.assertEqual(validate_power_scan_schedule(sweep).plans, (sweep,))

    def test_gap_reports_both_plan_indices_and_values(self):
        schedule = PowerScanSchedule(
            plans=(_sweep(-20.0, -10.0), _sweep(-8.0, 0.0))
        )

        with self.assertRaises(AmplifierInputError) as raised:
            validate_power_scan_schedule(schedule)

        message = str(raised.exception)
        self.assertIn("空档", message)
        self.assertIn("第 1 段终止功率 -10.0dBm", message)
        self.assertIn("第 2 段起始功率 -8.0dBm", message)

    def test_overlap_reports_both_plan_indices_and_values(self):
        schedule = PowerScanSchedule(
            plans=(_sweep(-20.0, -10.0), _sweep(-12.0, 0.0))
        )

        with self.assertRaises(AmplifierInputError) as raised:
            validate_power_scan_schedule(schedule)

        message = str(raised.exception)
        self.assertIn("重叠", message)
        self.assertIn("第 1 段终止功率 -10.0dBm", message)
        self.assertIn("第 2 段起始功率 -12.0dBm", message)

    def test_the_reported_index_follows_the_plan_order_not_the_power_order(self):
        # 顺序就是执行顺序：即使后一段起点更低，编号仍按列表位置给出。
        schedule = PowerScanSchedule(
            plans=(_sweep(-2.0, 0.0), _sweep(-20.0, -10.0), _sweep(-10.0, 0.0))
        )

        with self.assertRaises(AmplifierInputError) as raised:
            validate_power_scan_schedule(schedule)

        message = str(raised.exception)
        self.assertIn("第 1 段终止功率 0.0dBm", message)
        self.assertIn("第 2 段起始功率 -20.0dBm", message)

    def test_floating_point_noise_at_the_boundary_is_tolerated(self):
        noisy_start = -10.0 + 1e-13
        schedule = PowerScanSchedule(
            plans=(_sweep(-20.0, -10.0), _sweep(noisy_start, 0.0))
        )

        self.assertIs(validate_power_scan_schedule(schedule), schedule)

    def test_a_boundary_difference_beyond_tolerance_is_rejected(self):
        for start, expected in ((1e-9, "空档"), (-1e-9, "重叠")):
            with self.subTest(start=start):
                schedule = PowerScanSchedule(
                    plans=(_sweep(-20.0, -10.0), _sweep(-10.0 + start, 0.0))
                )

                with self.assertRaises(AmplifierInputError) as raised:
                    validate_power_scan_schedule(schedule)

                self.assertIn(expected, str(raised.exception))

    def test_validating_does_not_reorder_or_modify_the_schedule(self):
        fine = _sweep(-10.0, -2.0, 0.25)
        coarse = _sweep(-38.0, -10.0)
        schedule = PowerScanSchedule(plans=(coarse, fine))

        validate_power_scan_schedule(schedule)

        self.assertEqual(schedule.plans, (coarse, fine))

    def test_a_non_plan_argument_is_rejected(self):
        for value in (None, (-20.0, 0.0, 1.0), "plan"):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(AmplifierInputError) as raised:
                    validate_power_scan_schedule(value)
                self.assertIn("PowerScanSchedule", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
