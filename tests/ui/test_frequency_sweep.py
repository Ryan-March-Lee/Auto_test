"""频率扫描参数的纯 Python 测试：生成、增补合并与旧列表判定。"""

import unittest

from presentation.qt.frequency_sweep import (
    DEFAULT_FREQUENCY_EXTRA_TEXT,
    DEFAULT_FREQUENCY_START,
    DEFAULT_FREQUENCY_STEP,
    DEFAULT_FREQUENCY_STOP,
    DEFAULT_TEST_FREQUENCIES,
    FrequencySweepError,
    best_effort_sweep,
    format_frequency,
    frequencies_are_uniform,
    generate_frequencies,
    parse_extra_frequencies,
    sweep_from_frequencies,
)


class GenerateFrequencyTests(unittest.TestCase):
    def test_generates_an_inclusive_step_grid(self):
        self.assertEqual(
            generate_frequencies(4.0, 5.6, 0.4),
            (4.0, 4.4, 4.8, 5.2, 5.6),
        )

    def test_merges_and_sorts_extra_points(self):
        self.assertEqual(
            generate_frequencies(4.0, 5.6, 0.4, (5.8, 2.4, 4.4)),
            (2.4, 4.0, 4.4, 4.8, 5.2, 5.6, 5.8),
        )

    def test_extra_points_can_be_outside_the_sweep_range(self):
        self.assertEqual(
            generate_frequencies(4.0, 4.4, 0.4, (1.0, 8.0)),
            (1.0, 4.0, 4.4, 8.0),
        )

    def test_end_must_fall_on_the_step_grid(self):
        with self.assertRaisesRegex(FrequencySweepError, "结束频率必须落在步长上"):
            generate_frequencies(4.0, 5.8, 0.4)

    def test_start_must_not_exceed_end(self):
        with self.assertRaisesRegex(FrequencySweepError, "结束频率不能小于起始频率"):
            generate_frequencies(5.0, 4.0, 0.4)

    def test_step_and_frequencies_must_be_positive(self):
        for start, stop, step in ((0.0, 1.0, 0.1), (1.0, 2.0, 0.0), (-1.0, 1.0, 0.1)):
            with self.subTest(start=start, stop=stop, step=step):
                with self.assertRaises(FrequencySweepError):
                    generate_frequencies(start, stop, step)

    def test_single_point_uses_a_zero_length_grid(self):
        self.assertEqual(generate_frequencies(5.8, 5.8, 0.4), (5.8,))

    def test_default_sweep_matches_the_previous_default_list(self):
        values = generate_frequencies(
            DEFAULT_FREQUENCY_START,
            DEFAULT_FREQUENCY_STOP,
            DEFAULT_FREQUENCY_STEP,
            parse_extra_frequencies(DEFAULT_FREQUENCY_EXTRA_TEXT),
        )
        self.assertEqual(values, tuple(DEFAULT_TEST_FREQUENCIES))


class ParseExtraFrequencyTests(unittest.TestCase):
    def test_accepts_commas_spaces_chinese_commas_and_semicolons(self):
        self.assertEqual(
            parse_extra_frequencies("5.8, 6.2 7.0，8.0；9.0"),
            (5.8, 6.2, 7.0, 8.0, 9.0),
        )

    def test_empty_text_means_no_extra_points(self):
        self.assertEqual(parse_extra_frequencies(""), ())
        self.assertEqual(parse_extra_frequencies("   "), ())

    def test_rejects_non_numeric_or_non_positive_values(self):
        for text in ("abc", "5.8, abc", "0", "-1", "nan", "inf"):
            with self.subTest(text=text):
                with self.assertRaises(FrequencySweepError):
                    parse_extra_frequencies(text)


class FrequencyListTests(unittest.TestCase):
    def test_uniform_list_is_recognised(self):
        self.assertTrue(frequencies_are_uniform([2.3, 2.4, 2.5, 2.6]))
        self.assertTrue(frequencies_are_uniform([2.4]))
        self.assertFalse(frequencies_are_uniform([4.0, 4.4, 4.8, 5.2, 5.6, 5.8]))
        self.assertFalse(frequencies_are_uniform([2.4, 2.6, 2.5]))
        self.assertFalse(frequencies_are_uniform([]))

    def test_sweep_from_uniform_list(self):
        self.assertEqual(sweep_from_frequencies([2.3, 2.4, 2.5]), (2.3, 2.5, 0.1))
        self.assertEqual(sweep_from_frequencies([2.4]), (2.4, 2.4, 0.4))

    def test_sweep_from_non_uniform_list_is_rejected(self):
        with self.assertRaises(FrequencySweepError):
            sweep_from_frequencies([4.0, 4.4, 4.8, 5.2, 5.6, 5.8])

    def test_best_effort_sweep_keeps_first_endpoint_and_first_gap(self):
        self.assertEqual(
            best_effort_sweep([4.0, 4.4, 4.8, 5.2, 5.6, 5.8]),
            (4.0, 5.8, 0.4),
        )

    def test_format_frequency_strips_trailing_zeros(self):
        self.assertEqual(format_frequency(4.0), "4")
        self.assertEqual(format_frequency(4.8), "4.8")
        self.assertEqual(format_frequency(2.000001), "2.000001")


if __name__ == "__main__":
    unittest.main()
