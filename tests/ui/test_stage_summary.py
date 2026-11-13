"""阶段摘要与结果 KPI 的纯数据契约（重构计划步骤 15）。

摘要必须来自页面已经显示的数据：没有数据时统一显示“未开始”，绝不写 0 或占位假值。
本模块不导入 Qt，用例也只校验纯函数与不可变数据。
"""

import unittest
from dataclasses import FrozenInstanceError

from presentation.qt.stage_summary import (
    NOT_STARTED,
    StageMetric,
    StageSummary,
    count_text,
    frequency_headline,
    frequency_text,
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


class CountTextTests(unittest.TestCase):
    def test_positive_counts_are_shown_and_empty_is_not_started(self):
        self.assertEqual(count_text(3), "3")
        self.assertEqual(count_text(0), NOT_STARTED)
        self.assertEqual(count_text(-2), NOT_STARTED)

    def test_non_integers_are_rejected(self):
        for value in (None, "3", 3.0, True):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    count_text(value)


class FrequencyTextTests(unittest.TestCase):
    def test_numbers_are_rendered_in_ghz_without_trailing_noise(self):
        self.assertEqual(frequency_text(2.4), "2.4 GHz")
        self.assertEqual(frequency_text("3.50"), "3.5 GHz")
        self.assertEqual(frequency_text(6), "6 GHz")

    def test_non_numeric_text_is_kept_as_displayed(self):
        self.assertEqual(frequency_text("Unknown"), "Unknown")

    def test_empty_and_non_finite_values_have_no_text(self):
        for value in (None, "", "   ", True, float("nan"), float("inf")):
            with self.subTest(value=value):
                self.assertIsNone(frequency_text(value))


class StageSummaryTests(unittest.TestCase):
    def test_summary_carries_a_headline_and_metrics(self):
        summary = StageSummary(
            "6 频点",
            (
                StageMetric("频点", "6"),
                StageMetric("当前频点", "3.5 GHz"),
            ),
        )

        self.assertTrue(summary.has_data)
        self.assertEqual(summary.headline, "6 频点")
        self.assertEqual(
            [metric.label for metric in summary.metrics],
            ["频点", "当前频点"],
        )

    def test_not_started_is_not_zero_and_has_no_data(self):
        summary = StageSummary.not_started()

        self.assertFalse(summary.has_data)
        self.assertEqual(summary.headline, NOT_STARTED)
        self.assertEqual(
            summary.metrics, (StageMetric("频点", NOT_STARTED),)
        )

    def test_summary_is_immutable(self):
        summary = StageSummary.not_started()
        with self.assertRaises(FrozenInstanceError):
            summary.headline = "6 频点"  # type: ignore[misc]

    def test_invalid_payloads_are_rejected(self):
        with self.assertRaises(TypeError):
            StageMetric(1, "6")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            StageSummary("6 频点", [StageMetric("频点", "6")])  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            StageSummary("6 频点", ("6",))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            StageSummary(None)  # type: ignore[arg-type]

    def test_not_started_cannot_carry_counts(self):
        with self.assertRaises(ValueError):
            StageSummary(NOT_STARTED, (StageMetric("频点", "6"),))

    def test_summary_with_data_requires_at_least_one_metric(self):
        with self.assertRaises(ValueError):
            StageSummary("6 频点")

    def test_not_started_may_omit_the_metric_row(self):
        summary = StageSummary(NOT_STARTED)

        self.assertFalse(summary.has_data)
        self.assertEqual(summary.metrics, ())

    def test_not_started_keeps_a_custom_metric_label(self):
        summary = StageSummary.not_started(metric_label="线缆")

        self.assertFalse(summary.has_data)
        self.assertEqual(
            summary.metrics, (StageMetric("线缆", NOT_STARTED),)
        )


if __name__ == "__main__":
    unittest.main()
