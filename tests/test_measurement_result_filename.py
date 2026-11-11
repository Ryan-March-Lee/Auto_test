"""步骤 3：正式结果身份与阶段结果文件名的安全生成规则。

覆盖：

- 身份格式固定为 ``<UTC 时间戳>_<随机短串>``，同一秒内连续或并发生成不产生重复身份；
- 文件名只能由测量身份加 ``.json`` 构成，不接受任何用户文本参与路径构造；
- 路径片段、盘符、路径分隔符、空字符与非法后缀一律拒绝。
"""

import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from application.dto import (
    GENERATED_MEASUREMENT_ID_PATTERN,
    MEASUREMENT_RESULT_SUFFIX,
    generate_measurement_id,
    measurement_result_filename,
    validate_generated_measurement_id,
    validate_measurement_id,
    validate_measurement_result_filename,
)


NOW = datetime(2026, 11, 11, 1, 2, 3, tzinfo=timezone.utc)


class GenerateMeasurementIdTests(unittest.TestCase):
    def test_generated_identity_uses_utc_timestamp_and_random_suffix(self):
        identity = generate_measurement_id(NOW, token="abcd1234")

        self.assertEqual(identity, "20261111T010203Z_abcd1234")
        self.assertRegex(identity, GENERATED_MEASUREMENT_ID_PATTERN)
        self.assertEqual(validate_measurement_id(identity), identity)
        self.assertEqual(validate_generated_measurement_id(identity), identity)

    def test_local_time_is_converted_to_utc(self):
        local_time = datetime(
            2026, 11, 11, 9, 2, 3, tzinfo=timezone(timedelta(hours=8))
        )

        self.assertEqual(
            generate_measurement_id(local_time, token="a1b2c3d4"),
            "20261111T010203Z_a1b2c3d4",
        )

    def test_same_second_generation_does_not_collide(self):
        identities = {generate_measurement_id(NOW) for _ in range(500)}

        self.assertEqual(len(identities), 500)
        for identity in identities:
            self.assertEqual(validate_measurement_id(identity), identity)

    def test_default_suffix_is_a_full_uuid4_hex_token(self):
        identity = generate_measurement_id(NOW)

        self.assertRegex(identity, r"^20261111T010203Z_[0-9a-f]{32}$")

    def test_concurrent_generation_does_not_collide(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            identities = set(
                executor.map(lambda _: generate_measurement_id(NOW), range(200))
            )

        self.assertEqual(len(identities), 200)

    def test_injected_token_must_be_lowercase_hex_of_safe_length(self):
        for token in ("", "abc", "A1B2C3D4", "xyz12345", "a1b2c3d4" * 5, 1234):
            with self.subTest(token=token):
                with self.assertRaises(ValueError):
                    generate_measurement_id(NOW, token=token)

    def test_naive_time_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "时区"):
            generate_measurement_id(datetime(2026, 11, 11, 1, 2, 3))

    def test_generated_format_validator_rejects_legacy_and_unsafe_text(self):
        for value in (
            "",
            "20261111T010203_0a1b2c3d",
            "20261111T010203Z_",
            "20261111T010203Z_0A1B2C3D",
            "20261111T010203Z_../../escape",
            "20261111T010203Z_a1b2c3d4\n",
            None,
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_generated_measurement_id(value)

    def test_generated_format_validator_rejects_impossible_timestamp(self):
        for value in (
            "20260230T010203Z_a1b2c3d4",
            "20230229T010203Z_a1b2c3d4",
            "٢٠٢٦١١١١T010203Z_a1b2c3d4",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_generated_measurement_id(value)

    def test_generated_format_accepts_a_leap_day(self):
        identity = "20240229T010203Z_a1b2c3d4"

        self.assertEqual(validate_generated_measurement_id(identity), identity)


class MeasurementResultFilenameTests(unittest.TestCase):
    def test_filename_is_identity_plus_json(self):
        identity = generate_measurement_id(NOW, token="feedface")

        self.assertEqual(
            measurement_result_filename(identity),
            f"{identity}{MEASUREMENT_RESULT_SUFFIX}",
        )
        self.assertEqual(MEASUREMENT_RESULT_SUFFIX, ".json")

    def test_filename_accepts_legacy_identity_without_generated_timestamp(self):
        self.assertEqual(
            measurement_result_filename("20261111T010203_0a1b2c3d"),
            "20261111T010203_0a1b2c3d.json",
        )

    def test_filename_builder_rejects_path_fragments_and_drive_letters(self):
        for value in (
            "",
            " ../escape",
            "../escape",
            "..\\escape",
            "sub/escape",
            "sub\\escape",
            "C:escape",
            "C:\\escape",
            "/absolute",
            "\\absolute",
            "-leading",
            ".hidden",
            "x" * 101,
            "CON",
            "con",
            "COM1",
            "NUL",
            None,
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    measurement_result_filename(value)

    def test_filename_validator_round_trips_a_generated_name(self):
        identity = generate_measurement_id(NOW, token="cafebabe")
        filename = measurement_result_filename(identity)

        self.assertEqual(validate_measurement_result_filename(filename), filename)
        self.assertEqual(filename[: -len(MEASUREMENT_RESULT_SUFFIX)], identity)

    def test_filename_validator_rejects_paths_drive_letters_and_bad_suffixes(self):
        for value in (
            "",
            " result.json",
            "result.json ",
            "\x00result.json",
            "../result.json",
            "..\\result.json",
            "sub/result.json",
            "sub\\result.json",
            "C:result.json",
            "C:\\result.json",
            ".",
            "..",
            "result",
            "result.txt",
            "result.JSON",
            ".json",
            None,
            123,
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_measurement_result_filename(value)

    def test_filename_validator_rejects_identities_with_path_fragments(self):
        for value in (
            "a..b.json",
            "..hidden.json",
            "bad/name.json",
            "CON.json",
            "CON.foo.json",
            "lpt1.json",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_measurement_result_filename(value)


if __name__ == "__main__":
    unittest.main()
