"""DUT 功率扫描点位预览的纯展示测试。

这里只验证 ``presentation.qt.power_scan_preview`` 如何排版共享点位生成器的结果：
每段点数、长列表折叠、tooltip 全文、相邻计划错误归属与 DUT 上限截断。点位算法本身
由 ``tests/test_power_scan_schedule.py`` 与 ``tests/test_target_input_power_grid.py``
覆盖，本文件不重复实现或复制容差规则。
"""

import unittest

from presentation.qt.power_scan_preview import preview_power_scan_plans


class PowerScanPreviewTests(unittest.TestCase):
    def test_single_plan_lists_target_points_and_total(self):
        preview = preview_power_scan_plans([(-38.0, 1.0, -10.0)], 33.5)

        row = preview.rows[0]
        self.assertEqual(row.tone, "muted")
        self.assertEqual(row.error, None)
        self.assertEqual(len(row.points), 29)
        self.assertIn("共 29 点", row.text)
        self.assertIn("-38", row.text)
        self.assertIn("-10 dBm", row.text)
        self.assertEqual(tuple(preview.target_points), row.points)
        self.assertEqual(preview.errors, ())
        self.assertIn("29 个目标点", preview.summary)

    def test_shared_boundary_is_counted_once_in_the_merged_grid(self):
        preview = preview_power_scan_plans(
            [(-38.0, 1.0, -10.0), (-10.0, 0.25, -2.0)], 33.5
        )

        # 29 + 33 - 1（共享的 -10 dBm 只出现一次）。
        self.assertEqual(len(preview.target_points), 61)
        self.assertIn("61 个目标点", preview.summary)
        self.assertIn("-38", preview.summary)
        self.assertIn("-2 dBm", preview.summary)

    def test_dut_limit_truncates_the_points_that_would_be_measured(self):
        preview = preview_power_scan_plans([(-38.0, 1.0, -10.0)], -20.0)

        row = preview.rows[0]
        self.assertEqual(len(row.points), 19)
        self.assertIn("共 19 点", row.text)
        self.assertIn("受 DUT 最大输入 -20 dBm 限制", row.text)
        self.assertIn("止于 -20 dBm", row.text)
        self.assertIn("19 个目标点", preview.summary)
        self.assertIn("受 DUT 最大输入 -20 dBm 截断", preview.summary)
        self.assertIn("19", preview.summary_tooltip)

    def test_a_later_plan_above_the_dut_limit_generates_no_points(self):
        preview = preview_power_scan_plans(
            [(-38.0, 1.0, -10.0), (-10.0, 1.0, -2.0)], -15.0
        )

        self.assertEqual(len(preview.target_points), 24)  # -38..-15
        self.assertIn("不产生目标点", preview.rows[1].text)
        self.assertIn("-15 dBm", preview.rows[1].text)
        self.assertEqual(preview.rows[1].points, ())

    def test_gap_is_reported_on_the_following_row_with_both_segment_numbers(self):
        preview = preview_power_scan_plans(
            [(-38.0, 1.0, -10.0), (-9.0, 0.25, -2.0)], 33.5
        )

        self.assertIsNone(preview.rows[0].error)
        error = preview.rows[1].error
        self.assertIn("空档", error)
        self.assertIn("第 1 段", error)
        self.assertIn("第 2 段", error)
        self.assertIn("-10", error)
        self.assertIn("-9", error)
        self.assertEqual(preview.rows[1].tone, "warning")
        self.assertEqual(preview.target_points, ())
        self.assertIn("1 个问题", preview.summary)
        self.assertEqual(preview.summary_tone, "warning")

    def test_overlap_is_reported_on_the_following_row(self):
        preview = preview_power_scan_plans(
            [(-38.0, 1.0, -10.0), (-11.0, 0.25, -2.0)], 33.5
        )

        self.assertIn("重叠", preview.rows[1].error)
        self.assertIn("第 1 段", preview.rows[1].error)
        self.assertIn("第 2 段", preview.rows[1].error)

    def test_error_numbers_stay_correct_for_a_later_pair(self):
        """逐对校验不能让第 3 行的问题显示成“第 1/第 2 段”。"""
        preview = preview_power_scan_plans(
            [
                (-38.0, 1.0, -10.0),
                (-10.0, 1.0, -2.0),
                (-1.0, 0.25, 0.0),
            ],
            33.5,
        )

        error = preview.rows[2].error
        self.assertIn("空档", error)
        self.assertIn("第 2 段终止功率", error)
        self.assertIn("第 3 段起始功率", error)
        self.assertNotIn("第 1 段", error)

    def test_each_bad_boundary_is_reported_independently(self):
        preview = preview_power_scan_plans(
            [
                (-38.0, 1.0, -10.0),
                (-9.0, 1.0, -2.0),
                (-1.0, 0.25, 0.0),
            ],
            33.5,
        )

        self.assertIn("第 1 段", preview.rows[1].error)
        self.assertIn("第 2 段", preview.rows[1].error)
        self.assertIn("第 2 段", preview.rows[2].error)
        self.assertIn("第 3 段", preview.rows[2].error)
        self.assertEqual(len(preview.errors), 2)
        self.assertIn("2 个问题", preview.summary)

    def test_invalid_row_shows_the_error_instead_of_stale_points(self):
        preview = preview_power_scan_plans([(-5.0, 1.0, -10.0)], 33.5)

        row = preview.rows[0]
        self.assertIn("起始功率不能大于终止功率", row.text)
        self.assertEqual(row.tone, "warning")
        self.assertEqual(row.points, ())
        self.assertIn("起始功率不能大于终止功率", row.tooltip)
        self.assertIn("1 个问题", preview.summary)

    def test_a_long_plan_collapses_the_text_but_keeps_the_full_tooltip(self):
        preview = preview_power_scan_plans([(-38.0, 0.1, -10.0)], 33.5)

        row = preview.rows[0]
        self.assertEqual(len(row.points), 281)
        self.assertIn("共 281 点", row.text)
        self.assertIn("…", row.text)
        self.assertGreater(row.tooltip.count("，"), row.text.count("，"))
        self.assertIn("共 281 点", row.tooltip)
        self.assertIn("-10 dBm", row.tooltip)

    def test_an_empty_plan_list_is_reported_as_missing(self):
        preview = preview_power_scan_plans([], 33.5)

        self.assertEqual(preview.rows, ())
        self.assertEqual(preview.target_points, ())
        self.assertIn("尚未配置", preview.summary)
        self.assertEqual(preview.summary_tone, "warning")

    def test_an_unreadable_row_shows_its_own_error_and_keeps_the_other_rows(self):
        """一行读不出来不能连累其它行，也不能让预览槽函数抛出。"""
        preview = preview_power_scan_plans(
            [(-38.0, 1.0, -10.0), ("bad", 0.25, -2.0)], 33.5
        )

        self.assertIn("共 29 点", preview.rows[0].text)
        self.assertIsNone(preview.rows[0].error)
        self.assertIn("不是有效数字", preview.rows[1].text)
        self.assertEqual(preview.rows[1].tone, "warning")
        self.assertEqual(preview.rows[1].points, ())
        self.assertEqual(preview.target_points, ())
        self.assertIn("1 个问题", preview.summary)

    def test_a_row_with_the_wrong_number_of_readings_is_reported_not_raised(self):
        preview = preview_power_scan_plans([(-38.0, 1.0)], 33.5)

        self.assertIn("三个读数", preview.rows[0].text)
        self.assertIn("收到 2 个", preview.rows[0].text)
        self.assertEqual(preview.rows[0].tone, "warning")
        self.assertEqual(preview.summary_tone, "warning")

    def test_an_invalid_dut_limit_replaces_every_row_with_the_reason(self):
        preview = preview_power_scan_plans(
            [(-38.0, 1.0, -10.0), (-10.0, 0.25, -2.0)], float("nan")
        )

        self.assertEqual(len(preview.rows), 2)
        for row in preview.rows:
            self.assertIn("DUT 最大输入功率不是有效数值", row.text)
            self.assertEqual(row.tone, "warning")
        self.assertEqual(preview.target_points, ())
        self.assertIn("1 个问题", preview.summary)


if __name__ == "__main__":
    unittest.main()
