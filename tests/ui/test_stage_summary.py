"""阶段摘要的纯数据契约（重构计划步骤 15）。

摘要必须来自页面已经显示的数据：没有数据时统一显示“未开始”，绝不写 0 或占位假值。
本模块不导入 Qt，用例也只校验纯函数与不可变数据。
"""

import unittest
from dataclasses import FrozenInstanceError

from presentation.qt.stage_summary import (
    NOT_STARTED,
    StageSummary,
    frequency_headline,
)


class FrequencyHeadlineTests(unittest.TestCase):
    def test_counts_become_a_rail_headline(self):
        self.assertEqual(frequency_headline(6), "6 频点")
        self.assertEqual(frequency_headline(1), "1 频点")
        self.assertEqual(frequency_headline(0), NOT_STARTED)

    def test_negative_and_non_integer_counts_are_rejected(self):
        with self.assertRaises(ValueError):
            frequency_headline(-1)
        for value in (None, "6", 6.0, True):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    frequency_headline(value)


class StageSummaryTests(unittest.TestCase):
    def test_summary_carries_a_headline(self):
        summary = StageSummary("6 频点")

        self.assertTrue(summary.has_data)
        self.assertEqual(summary.headline, "6 频点")

    def test_not_started_is_not_zero_and_has_no_data(self):
        summary = StageSummary.not_started()

        self.assertFalse(summary.has_data)
        self.assertEqual(summary.headline, NOT_STARTED)

    def test_summary_is_immutable(self):
        summary = StageSummary.not_started()
        with self.assertRaises(FrozenInstanceError):
            summary.headline = "6 频点"  # type: ignore[misc]

    def test_invalid_headline_is_rejected(self):
        with self.assertRaises(TypeError):
            StageSummary(None)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            StageSummary(6)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
