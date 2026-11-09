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


if __name__ == "__main__":
    unittest.main()
