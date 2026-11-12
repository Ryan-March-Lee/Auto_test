"""频率扫描灰字预览的展示契约测试：行内频点、汇总与问题文案。"""

import unittest

from presentation.qt.frequency_scan_preview import preview_frequency_scan


class FrequencyScanPreviewTests(unittest.TestCase):
    def test_valid_sweep_shows_points_then_a_summary_range(self):
        preview = preview_frequency_scan(4.0, 5.6, 0.4, "")

        self.assertEqual(
            preview.row_text, "共 5 个频点：4，4.4，4.8，5.2，5.6 GHz"
        )
        self.assertEqual(
            preview.summary, "DUT 频率扫描：1 段，共 5 个频点（4 → 5.6 GHz）"
        )
        self.assertEqual(preview.row_tone, "muted")
        self.assertEqual(preview.summary_tone, "muted")
        self.assertEqual(preview.frequencies, (4.0, 4.4, 4.8, 5.2, 5.6))
        self.assertIsNone(preview.error)
        # tooltip 给出该段的扫描参数与完整点列。
        self.assertIn("起始 4 GHz", preview.row_tooltip)
        self.assertIn("步长 0.4 GHz", preview.row_tooltip)
        self.assertIn("终止 5.6 GHz", preview.row_tooltip)
        self.assertIn("5.6 GHz", preview.summary_tooltip)

    def test_extra_points_are_merged_and_called_out(self):
        preview = preview_frequency_scan(4.0, 4.4, 0.4, "5.8，2.4")

        self.assertEqual(preview.frequencies, (2.4, 4.0, 4.4, 5.8))
        self.assertIn("共 4 个频点", preview.row_text)
        self.assertIn("（含增补 5.8，2.4 GHz）", preview.row_text)
        # 汇总与点列都按生成结果排序，而不是按增补原文。
        self.assertIn("2.4 → 5.8 GHz", preview.summary)
        self.assertIn("2.4，4，4.4，5.8 GHz", preview.summary_tooltip)

    def test_long_sweeps_keep_head_tail_and_total(self):
        preview = preview_frequency_scan(1.0, 1.9, 0.1, "")

        self.assertIn("共 10 个频点", preview.row_text)
        self.assertIn("，…，", preview.row_text)
        self.assertNotIn("1.4", preview.row_text)
        # 完整点列仍能在 tooltip 里看到，不因为行内截断而丢失。
        self.assertIn("1，1.1，1.2，1.3，1.4，1.5，1.6，1.7，1.8，1.9 GHz", preview.row_tooltip)

    def test_invalid_grid_reports_one_problem_in_both_lines(self):
        preview = preview_frequency_scan(4.0, 5.5, 0.4, "")

        self.assertEqual(preview.row_tone, "warning")
        self.assertEqual(preview.summary_tone, "warning")
        self.assertIn("结束频率必须落在步长上", preview.row_text)
        self.assertEqual(
            preview.summary,
            "DUT 频率扫描：1 段，1 个问题；" + preview.row_text,
        )
        self.assertEqual(preview.frequencies, ())
        self.assertEqual(preview.error, preview.row_text)

    def test_invalid_extra_text_is_reported(self):
        preview = preview_frequency_scan(4.0, 5.6, 0.4, "not-a-frequency")

        self.assertIn("增补频点", preview.row_text)
        self.assertEqual(preview.summary_tone, "warning")
        self.assertIsNotNone(preview.error)

    def test_import_error_wins_over_the_control_readings(self):
        preview = preview_frequency_scan(
            2.4, 3.0, 0.2, "", import_error="测试频率不是严格等步长序列"
        )

        self.assertEqual(preview.row_text, "测试频率不是严格等步长序列")
        self.assertIn("1 个问题；测试频率不是严格等步长序列", preview.summary)
        self.assertEqual(preview.frequencies, ())

    def test_single_point_sweep_has_a_degenerate_range(self):
        preview = preview_frequency_scan(4.4, 4.4, 0.4, "")

        self.assertEqual(preview.row_text, "共 1 个频点：4.4 GHz")
        self.assertIn("（4.4 → 4.4 GHz）", preview.summary)


if __name__ == "__main__":
    unittest.main()
