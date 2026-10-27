"""测量计算纯函数与测量值对象的单元测试。

技术债步骤 13 的验收点：

- 纯函数只接受 :mod:`domain.measurement.values` 的值对象，裸浮点数或其他单位的
  值对象一律 ``TypeError``；
- GHz/Hz、dBm/W、dB/% 的换算有独立测试；
- 线损拓扑用 :class:`CableLossProfile` / :class:`CableLossTable` 表达，字符串频率键
  与 ``cable1`` 等字段名只在值对象里出现；
- 既有公式的数值结果与历史快照逐位一致。
"""

import sys
import unittest
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]

from domain.measurement.calculations import (
    calculate_cable_losses,
    compensate_amplifier_output_power,
    compensate_driver_output_power,
    interpolate_driver_output_power,
    calculate_dut_input_power,
    calculate_gain,
    calculate_efficiency,
    calculate_compression_result,
)
from domain.measurement.values import (
    CableLossProfile,
    CableLossTable,
    CompressionPoint,
    CompressionResult,
    Decibels,
    DriverOutputTable,
    Frequency,
    MeasurementUnitError,
    MeasurementValueError,
    Percent,
    PowerDbm,
    PowerWatt,
)


def db(value: float) -> Decibels:
    return Decibels(value)


def dbm(value: float) -> PowerDbm:
    return PowerDbm(value)


def ghz(value: float) -> Frequency:
    return Frequency.from_ghz(value)


SAMPLE_LOSS_DATA = {
    "1.0": {
        "cable1": 1.625,
        "cable2": 1.625,
        "cable3": 0.095,
        "cable4": 0.095,
        "total_path1": 33.25,
        "total_path2": 33.44,
    }
}

SAMPLE_LOSS_TABLE = CableLossTable.from_mapping(SAMPLE_LOSS_DATA, source="线损输入")


class FrequencyValueTests(unittest.TestCase):
    """Frequency：GHz/Hz/MHz 换算与文本解析。"""

    def test_ghz_is_the_canonical_unit(self):
        frequency = Frequency.from_ghz(2.4)
        self.assertEqual(frequency.ghz, 2.4)
        self.assertEqual(frequency.mhz, 2400.0)
        self.assertEqual(frequency.hz, 2.4e9)
        self.assertEqual(frequency.key, "2.4")

    def test_unit_conversions_are_exact_round_trips(self):
        self.assertEqual(Frequency.from_hz(2.4e9), Frequency.from_ghz(2.4))
        self.assertEqual(Frequency.from_mhz(2400.0), Frequency.from_ghz(2.4))
        self.assertEqual(Frequency.from_hz(2.4e9).key, "2.4")
        self.assertEqual(Frequency.from_mhz(2300.0).key, "2.3")

    def test_parse_accepts_documented_units(self):
        for text in ("2.4GHz", "2400MHz", "2.4e9Hz", " 2.4 ghz "):
            with self.subTest(text=text):
                self.assertEqual(Frequency.parse(text), Frequency.from_ghz(2.4))

    def test_parse_rejects_bare_numbers_without_a_unit(self):
        """裸数值文本无法判断单位：测量文本必须写单位，文档键走另一个入口。"""
        for text in ("2.4", "2400", "2.4e9"):
            with self.subTest(text=text):
                with self.assertRaises(MeasurementUnitError):
                    Frequency.parse(text)

    def test_document_key_accepts_bare_ghz_and_explicit_units(self):
        for text in ("2.4", "2.40", " 2.4 ", "2.4GHz", "2400MHz"):
            with self.subTest(text=text):
                self.assertEqual(
                    Frequency.from_document_key(text), Frequency.from_ghz(2.4)
                )

    def test_document_key_rejects_non_string_keys(self):
        """JSON 键本来就是字符串；浮点数、布尔值和字节串不能悄悄变成频率。"""
        for key in (2.4, True, None, b"2.4"):
            with self.subTest(key=key):
                with self.assertRaises(TypeError):
                    Frequency.from_document_key(key)

    def test_document_key_rejects_empty_and_unparsable_keys(self):
        for key in ("", "   ", "abc", "2.4THz"):
            with self.subTest(key=key):
                with self.assertRaises(MeasurementUnitError):
                    Frequency.from_document_key(key)

    def test_parse_rejects_unknown_units_and_nan(self):
        for text in ("2.4GHz-extra", "abc", "", "2.4THz"):
            with self.subTest(text=text):
                with self.assertRaises(MeasurementUnitError):
                    Frequency.parse(text)
        with self.assertRaises(MeasurementValueError):
            Frequency.from_ghz(float("nan"))
        with self.assertRaises(MeasurementValueError):
            Frequency.from_ghz(float("inf"))

    def test_frequency_rejects_non_numeric_and_value_objects(self):
        with self.assertRaises(TypeError):
            Frequency("2.4")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            Frequency.from_ghz(db(2.4))


class DecibelsValueTests(unittest.TestCase):
    """Decibels：dB 文本解析与标量算术。"""

    def test_parse_requires_the_db_suffix(self):
        self.assertEqual(Decibels.parse("30dB"), db(30.0))
        self.assertEqual(Decibels.parse("30 db"), db(30.0))
        for text in ("30", "30dBm", "30dB/Hz", ""):
            with self.subTest(text=text):
                with self.assertRaises(MeasurementUnitError):
                    Decibels.parse(text)

    def test_parse_rejects_non_text(self):
        with self.assertRaises(MeasurementUnitError):
            Decibels.parse(30.0)  # type: ignore[arg-type]

    def test_scalar_arithmetic_stays_in_db(self):
        self.assertEqual(db(30.0) - db(10.0), db(20.0))
        self.assertEqual(db(1.5) + db(2.5), db(4.0))
        self.assertAlmostEqual(((db(33.21) - db(30.0)) / 2).db, 1.605)
        self.assertEqual(-db(3.0), db(-3.0))

    def test_arithmetic_rejects_raw_numbers_and_other_units(self):
        with self.assertRaises(TypeError):
            db(1.0) + 2.0  # type: ignore[operator]
        with self.assertRaises(TypeError):
            db(1.0) + dbm(2.0)  # type: ignore[operator]
        with self.assertRaises(TypeError):
            db(1.0) / "2"  # type: ignore[operator]


class PowerValueTests(unittest.TestCase):
    """PowerDbm / PowerWatt：dBm 与 W 的换算。"""

    def test_zero_dbm_is_one_milliwatt(self):
        self.assertEqual(dbm(0.0).to_watt(), PowerWatt(0.001))

    def test_thirty_dbm_is_one_watt(self):
        self.assertEqual(dbm(30.0).to_watt(), PowerWatt(1.0))
        self.assertEqual(PowerWatt(1.0).to_dbm(), dbm(30.0))

    def test_power_conversions_round_trip(self):
        for value in (-40.0, -10.0, 0.0, 20.0, 45.0):
            with self.subTest(dbm=value):
                restored = dbm(value).to_watt().to_dbm()
                self.assertAlmostEqual(restored.dbm, value, places=9)

    def test_power_arithmetic_only_accepts_matching_units(self):
        self.assertEqual(dbm(0.0) + db(3.0), dbm(3.0))
        self.assertEqual(dbm(30.0) - db(10.0), dbm(20.0))
        self.assertEqual(dbm(30.0) - dbm(10.0), db(20.0))
        with self.assertRaises(TypeError):
            dbm(0.0) + 3.0  # type: ignore[operator]
        with self.assertRaises(TypeError):
            dbm(0.0) + dbm(3.0)  # type: ignore[operator]

    def test_non_positive_watt_cannot_become_dbm(self):
        with self.assertRaises(MeasurementValueError):
            PowerWatt(0.0).to_dbm()


class PercentValueTests(unittest.TestCase):
    """Percent：比值与百分数的换算。"""

    def test_percent_and_ratio(self):
        self.assertEqual(Percent.from_ratio(0.1), Percent(10.0))
        self.assertEqual(Percent(10.0).ratio, 0.1)
        self.assertEqual(Percent(0), Percent(0.0))

    def test_percent_rejects_non_finite(self):
        with self.assertRaises(MeasurementValueError):
            Percent(float("nan"))


class CableLossProfileTests(unittest.TestCase):
    """CableLossProfile：线损拓扑与结果文档形状。"""

    def test_mapping_round_trip_keeps_document_shape(self):
        profile = CableLossProfile.from_mapping(SAMPLE_LOSS_DATA["1.0"])
        self.assertEqual(profile.to_mapping(), SAMPLE_LOSS_DATA["1.0"])
        self.assertEqual(profile.cable1, db(1.625))
        self.assertEqual(profile.total_path2, db(33.44))

    def test_unknown_fields_are_rejected(self):
        with self.assertRaises(MeasurementValueError):
            CableLossProfile.from_mapping({"cable5": 1.0})
        with self.assertRaises(MeasurementValueError):
            CableLossProfile.from_mapping({"attenuator": 30.0})

    def test_non_numeric_and_non_finite_values_are_rejected(self):
        with self.assertRaises(TypeError):
            CableLossProfile.from_mapping({"cable1": "1.0"})
        with self.assertRaises(MeasurementValueError):
            CableLossProfile.from_mapping({"cable1": float("nan")})

    def test_partial_profiles_only_require_the_fields_the_path_needs(self):
        profile = CableLossProfile.from_mapping({"cable2": 2.0})
        self.assertEqual(profile.to_mapping(), {"cable2": 2.0})
        self.assertEqual(profile.driver_output_path_loss(db(30.0)), db(32.0))
        with self.assertRaises(MeasurementValueError):
            profile.dut_output_path_loss(db(30.0))
        with self.assertRaises(MeasurementValueError):
            profile.dut_input_path_loss()

    def test_path_losses_use_the_declared_topology(self):
        profile = CableLossProfile.from_mapping(SAMPLE_LOSS_DATA["1.0"])
        self.assertEqual(profile.dut_input_path_loss(), db(1.625))
        self.assertEqual(profile.driver_output_path_loss(db(30.0)), db(31.625))
        self.assertEqual(profile.dut_output_path_loss(db(30.0)), db(31.72))

    def test_path_requirements_report_only_the_missing_segment(self):
        """路径需求由值对象表达：调用方不再枚举 cable1 等字符串字段名。"""
        profile = CableLossProfile.from_mapping({"cable2": 2.0, "cable4": 3.0})
        profile.require_driver_output_path()
        profile.require_dut_output_path()
        with self.assertRaises(MeasurementValueError) as raised:
            profile.require_dut_input_path()
        self.assertIn("cable1", str(raised.exception))

        with self.assertRaises(MeasurementValueError) as raised:
            CableLossProfile.from_mapping({"cable2": 2.0}).require_dut_output_path()
        self.assertIn("cable4", str(raised.exception))


class CableLossTableTests(unittest.TestCase):
    """CableLossTable：字符串频率键的集中解析。"""

    def test_string_frequency_keys_become_value_objects(self):
        table = CableLossTable.from_mapping({"1.0": {"cable1": 1.0}, "2.4GHz": {"cable1": 2.0}})
        self.assertEqual(len(table), 2)
        self.assertEqual(table.profile_at(ghz(1.0)).cable1, db(1.0))
        self.assertEqual(table.profile_at(ghz(2.4)).cable1, db(2.0))

    def test_document_round_trip_uses_canonical_keys(self):
        table = CableLossTable.from_mapping({"1.0": {"cable1": 1.0, "cable2": 2.0}})
        self.assertEqual(table.to_document(), {"1.0": {"cable1": 1.0, "cable2": 2.0}})

    def test_missing_frequency_names_the_frequency(self):
        with self.assertRaises(MeasurementValueError) as raised:
            SAMPLE_LOSS_TABLE.profile_at(ghz(2.4))
        self.assertIn("2.4", str(raised.exception))

    def test_invalid_frequency_key_is_rejected(self):
        with self.assertRaises(MeasurementUnitError):
            CableLossTable.from_mapping({"not-a-frequency": {"cable1": 1.0}})

    def test_table_rejects_untyped_profiles(self):
        with self.assertRaises(TypeError):
            CableLossTable({ghz(1.0): {"cable1": 1.0}})  # type: ignore[dict-item]

    def test_non_string_frequency_key_is_rejected(self):
        """频率键必须是字符串：数值键会掩盖写入方用错类型或用错单位。"""
        with self.assertRaises(TypeError):
            CableLossTable.from_mapping({1.0: {"cable1": 1.0}})  # type: ignore[dict-item]

    def test_equivalent_key_spellings_are_the_same_frequency(self):
        table = CableLossTable.from_mapping({"2.4": {"cable1": 1.0}})
        self.assertEqual(table.profile_at(ghz(2.4)).cable1, db(1.0))
        with self.assertRaises(MeasurementValueError):
            CableLossTable.from_mapping(
                {"2.4": {"cable1": 1.0}, "2.40": {"cable1": 2.0}}
            )

    def test_document_output_uses_the_canonical_frequency_key(self):
        table = CableLossTable.from_mapping({"2.40": {"cable1": 1.0}})
        self.assertEqual(table.to_document(), {"2.4": {"cable1": 1.0}})


class CalculateCableLossesTests(unittest.TestCase):
    """测试 calculate_cable_losses() —— 线损分摊公式（数值不变）。"""

    # --- 正常值 ---

    def test_normal_values_match_snapshot_formula(self):
        """cable12 = (p1 - atten) / 2; cable34 = (p2 - p1) / 2。"""
        result = calculate_cable_losses(path1_loss=db(32.3), path2_loss=db(33.21),
                                        attenuator_value=db(30.0))
        self.assertAlmostEqual(result.cable1.db, (32.3 - 30.0) / 2)
        self.assertAlmostEqual(result.cable2.db, (32.3 - 30.0) / 2)
        self.assertAlmostEqual(result.cable3.db, (33.21 - 32.3) / 2)
        self.assertAlmostEqual(result.cable4.db, (33.21 - 32.3) / 2)
        self.assertAlmostEqual(result.total_path1.db, 32.3)
        self.assertAlmostEqual(result.total_path2.db, 33.21)

    def test_cable1_equals_cable2(self):
        """线①和线②假设相等。"""
        result = calculate_cable_losses(db(25.0), db(30.0), db(20.0))
        self.assertEqual(result.cable1, result.cable2)

    def test_cable3_equals_cable4(self):
        """线③和线④假设相等。"""
        result = calculate_cable_losses(db(25.0), db(30.0), db(20.0))
        self.assertEqual(result.cable3, result.cable4)

    def test_result_has_all_six_keys(self):
        """结果必须包含 cable1-cable4、total_path1、total_path2。"""
        result = calculate_cable_losses(db(10.0), db(15.0), db(5.0))
        expected_keys = {'cable1', 'cable2', 'cable3', 'cable4',
                         'total_path1', 'total_path2'}
        self.assertEqual(set(result.to_mapping()), expected_keys)

    # --- 零值 ---

    def test_zero_path_losses_with_zero_attenuator(self):
        """路径损耗和衰减器均为零时，所有线缆损耗为零。"""
        result = calculate_cable_losses(db(0.0), db(0.0), db(0.0))
        self.assertEqual(
            result.to_mapping(),
            {key: 0.0 for key in
             ('cable1', 'cable2', 'cable3', 'cable4', 'total_path1', 'total_path2')},
        )

    def test_zero_attenuator_only(self):
        """衰减器为零时，cable12 = p1 / 2。"""
        result = calculate_cable_losses(db(10.0), db(14.0), db(0.0))
        self.assertAlmostEqual(result.cable1.db, 5.0)
        self.assertAlmostEqual(result.cable3.db, 2.0)

    # --- 负值（线损可为负，因校准或测量误差） ---

    def test_negative_cable34_when_path2_less_than_path1(self):
        """path2 < path1 时 cable34 为负（如现场 2.3 GHz 数据）。"""
        result = calculate_cable_losses(db(32.58), db(32.2), db(30.0))
        self.assertAlmostEqual(result.cable3.db, (32.2 - 32.58) / 2)
        self.assertTrue(result.cable3.db < 0)

    def test_negative_cable12_when_path1_less_than_attenuator(self):
        """path1 < 衰减器时 cable12 为负。"""
        result = calculate_cable_losses(db(5.0), db(10.0), db(30.0))
        self.assertTrue(result.cable1.db < 0)
        self.assertTrue(result.cable2.db < 0)

    # --- 浮点精度 ---

    def test_float_precision_matches_snapshot(self):
        """与 cable_loss_results.json 中 0.8 GHz 的实际数据对比。"""
        # p1=32.3, p2=33.21, atten=30.0
        result = calculate_cable_losses(db(32.3), db(33.21), db(30.0))
        # cable1 = (32.3 - 30) / 2 = 1.15
        self.assertAlmostEqual(result.cable1.db, 1.15, places=10)
        # cable3 = (33.21 - 32.3) / 2 = 0.455
        self.assertAlmostEqual(result.cable3.db, 0.455, places=10)

    # --- 单位边界 ---

    def test_raw_numbers_and_wrong_units_are_rejected(self):
        """裸浮点数与错误单位不能静默进入线损计算。"""
        with self.assertRaises(TypeError):
            calculate_cable_losses(32.3, 33.21, 30.0)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            calculate_cable_losses(dbm(32.3), db(33.21), db(30.0))  # type: ignore[arg-type]


class CompensateAmplifierOutputPowerTests(unittest.TestCase):
    """
    测试 compensate_amplifier_output_power()。

    公式：measured_power + cable4 + attenuator + cable2。
    对应历史 AmplifierMeasurement.calculate_actual_power()。
    """

    def _sample_loss_table(self):
        """返回一个频率 "1.0" 的线损表样例。"""
        return CableLossTable.from_mapping(SAMPLE_LOSS_DATA, source="线损输入")

    def test_normal_compensation(self):
        """读数 + cable4 + atten + cable2。"""
        result = compensate_amplifier_output_power(
            measured_power=dbm(-10.0), frequency=ghz(1.0),
            loss_table=self._sample_loss_table(), attenuator_value=db(30.0))
        expected = -10.0 + 0.095 + 30.0 + 1.625
        self.assertAlmostEqual(result.dbm, expected)

    def test_zero_measured_power(self):
        """读数为 0 时结果 = cable4 + atten + cable2。"""
        result = compensate_amplifier_output_power(
            measured_power=dbm(0.0), frequency=ghz(1.0),
            loss_table=self._sample_loss_table(), attenuator_value=db(30.0))
        expected = 0.095 + 30.0 + 1.625
        self.assertAlmostEqual(result.dbm, expected)

    def test_zero_attenuator(self):
        """衰减器值为 0 时只补偿 cable4 + cable2。"""
        result = compensate_amplifier_output_power(
            measured_power=dbm(5.0), frequency=ghz(1.0),
            loss_table=self._sample_loss_table(), attenuator_value=db(0.0))
        expected = 5.0 + 0.095 + 1.625
        self.assertAlmostEqual(result.dbm, expected)

    def test_negative_cable4(self):
        """cable4 为负值时仍参与加法。"""
        loss_table = CableLossTable.from_mapping({
            "2.3": {
                "cable1": 1.29, "cable2": 1.29,
                "cable3": -0.19, "cable4": -0.19,
                "total_path1": 32.58, "total_path2": 32.2,
            }
        })
        result = compensate_amplifier_output_power(
            measured_power=dbm(0.0), frequency=ghz(2.3),
            loss_table=loss_table, attenuator_value=db(30.0))
        expected = 0.0 + (-0.19) + 30.0 + 1.29
        self.assertAlmostEqual(result.dbm, expected)

    def test_string_frequency_key_lookup(self):
        """频率以字符串形式存储在结果文档中，Frequency 仍能查到。"""
        result = compensate_amplifier_output_power(
            measured_power=dbm(0.0), frequency=ghz(1.0),
            loss_table=self._sample_loss_table(), attenuator_value=db(30.0))
        self.assertAlmostEqual(result.dbm, 0.095 + 30.0 + 1.625)

    def test_raw_numbers_and_wrong_units_are_rejected(self):
        with self.assertRaises(TypeError):
            compensate_amplifier_output_power(
                measured_power=-10.0, frequency=ghz(1.0),  # type: ignore[arg-type]
                loss_table=self._sample_loss_table(), attenuator_value=db(30.0))
        with self.assertRaises(TypeError):
            compensate_amplifier_output_power(
                measured_power=dbm(-10.0), frequency=ghz(1.0),
                loss_table=SAMPLE_LOSS_DATA,  # type: ignore[arg-type]
                attenuator_value=db(30.0))
        with self.assertRaises(TypeError):
            compensate_amplifier_output_power(
                measured_power=dbm(-10.0), frequency=ghz(1.0),
                loss_table=self._sample_loss_table(),
                attenuator_value=PowerWatt(1.0))  # type: ignore[arg-type]


class CompensateDriverOutputPowerTests(unittest.TestCase):
    """
    测试 compensate_driver_output_power()。

    公式：measured_power + attenuator + cable2。
    对应历史 DriverPowerMapping.calculate_actual_power()。
    不补偿 cable4，因为驱动功放输出端到频谱仪不经过线④。
    """

    def _sample_loss_table(self):
        return CableLossTable.from_mapping(SAMPLE_LOSS_DATA, source="线损输入")

    def test_normal_compensation(self):
        """读数 + atten + cable2（不含 cable4）。"""
        result = compensate_driver_output_power(
            measured_power=dbm(-10.0), frequency=ghz(1.0),
            loss_table=self._sample_loss_table(), attenuator_value=db(30.0))
        expected = -10.0 + 30.0 + 1.625
        self.assertAlmostEqual(result.dbm, expected)

    def test_does_not_include_cable4(self):
        """与主功放补偿相比，驱动补偿不应包含 cable4。"""
        loss_table = self._sample_loss_table()
        amp_result = compensate_amplifier_output_power(
            measured_power=dbm(0.0), frequency=ghz(1.0),
            loss_table=loss_table, attenuator_value=db(30.0))
        driver_result = compensate_driver_output_power(
            measured_power=dbm(0.0), frequency=ghz(1.0),
            loss_table=loss_table, attenuator_value=db(30.0))
        # 差值应为 cable4
        self.assertAlmostEqual(amp_result.dbm - driver_result.dbm, 0.095)

    def test_zero_measured_power(self):
        result = compensate_driver_output_power(
            measured_power=dbm(0.0), frequency=ghz(1.0),
            loss_table=self._sample_loss_table(), attenuator_value=db(30.0))
        self.assertAlmostEqual(result.dbm, 30.0 + 1.625)

    def test_zero_attenuator(self):
        result = compensate_driver_output_power(
            measured_power=dbm(5.0), frequency=ghz(1.0),
            loss_table=self._sample_loss_table(), attenuator_value=db(0.0))
        self.assertAlmostEqual(result.dbm, 5.0 + 1.625)

    def test_string_frequency_key_lookup(self):
        result = compensate_driver_output_power(
            measured_power=dbm(0.0), frequency=ghz(1.0),
            loss_table=self._sample_loss_table(), attenuator_value=db(30.0))
        self.assertAlmostEqual(result.dbm, 30.0 + 1.625)


class MeasurementEntryPointDelegationTestsRemoved(unittest.TestCase):
    """旧计算再导出模块已在技术债步骤 16 退役；正式函数只由领域层提供。"""

    def test_legacy_reexport_module_is_gone(self):
        self.assertFalse((ROOT / "measurement_calculations.py").exists())


# ---------------------------------------------------------------------------
# 阶段 2 测试：驱动插值和 DUT 输入功率
# ---------------------------------------------------------------------------

# 一个有序的驱动映射样例（频率 1.0 GHz）
SAMPLE_DRIVER_MAPPING = {
    "1.0": {
        "-50.0": 10.0,
        "-45.0": 15.0,
        "-40.0": 20.0,
        "-35.0": 25.0,
        "-30.0": 30.0,
    }
}

# 一个乱序的驱动映射样例
SAMPLE_DRIVER_MAPPING_UNORDERED = {
    "1.0": {
        "-30.0": 30.0,
        "-50.0": 10.0,
        "-40.0": 20.0,
        "-45.0": 15.0,
        "-35.0": 25.0,
    }
}


class InterpolateDriverOutputPowerTests(unittest.TestCase):
    """测试 interpolate_driver_output_power()。"""

    def _table(self, mapping):
        return DriverOutputTable.from_mapping(mapping, source="驱动映射输入")

    def test_in_range_interpolation(self):
        """范围内插值应与线性插值一致。"""
        result = interpolate_driver_output_power(
            frequency=ghz(1.0), sg_power=dbm(-42.5),
            driver_mapping=self._table(SAMPLE_DRIVER_MAPPING))
        # -42.5 在 -45(15) 和 -40(20) 之间，中间值为 17.5
        self.assertAlmostEqual(result.dbm, 17.5)

    def test_exact_key_match(self):
        """精确匹配某个键时返回对应值。"""
        result = interpolate_driver_output_power(
            frequency=ghz(1.0), sg_power=dbm(-40.0),
            driver_mapping=self._table(SAMPLE_DRIVER_MAPPING))
        self.assertAlmostEqual(result.dbm, 20.0)

    def test_below_range_uses_endpoint(self):
        """低于最小输入功率时返回端点值（np.interp 默认行为）。"""
        result = interpolate_driver_output_power(
            frequency=ghz(1.0), sg_power=dbm(-60.0),
            driver_mapping=self._table(SAMPLE_DRIVER_MAPPING))
        self.assertAlmostEqual(result.dbm, 10.0)

    def test_above_range_uses_endpoint(self):
        """高于最大输入功率时返回端点值。"""
        result = interpolate_driver_output_power(
            frequency=ghz(1.0), sg_power=dbm(-20.0),
            driver_mapping=self._table(SAMPLE_DRIVER_MAPPING))
        self.assertAlmostEqual(result.dbm, 30.0)

    def test_unordered_keys_are_sorted(self):
        """乱序键应自动排序，结果与有序映射一致。"""
        result_ordered = interpolate_driver_output_power(
            frequency=ghz(1.0), sg_power=dbm(-42.5),
            driver_mapping=self._table(SAMPLE_DRIVER_MAPPING))
        result_unordered = interpolate_driver_output_power(
            frequency=ghz(1.0), sg_power=dbm(-42.5),
            driver_mapping=self._table(SAMPLE_DRIVER_MAPPING_UNORDERED))
        self.assertAlmostEqual(result_ordered.dbm, result_unordered.dbm)

    def test_string_frequency_key_lookup(self):
        """频率以字符串存储，Frequency 也能查到。"""
        result = interpolate_driver_output_power(
            frequency=ghz(1.0), sg_power=dbm(-40.0),
            driver_mapping=self._table(SAMPLE_DRIVER_MAPPING))
        self.assertAlmostEqual(result.dbm, 20.0)

    def test_missing_frequency_and_empty_bucket_are_rejected(self):
        table = self._table(SAMPLE_DRIVER_MAPPING)
        with self.assertRaises(MeasurementValueError):
            interpolate_driver_output_power(
                frequency=ghz(2.4), sg_power=dbm(-40.0), driver_mapping=table)
        empty = self._table({"1.0": {}})
        with self.assertRaises(MeasurementValueError):
            interpolate_driver_output_power(
                frequency=ghz(1.0), sg_power=dbm(-40.0), driver_mapping=empty)

    def test_unparsable_input_power_key_is_rejected(self):
        with self.assertRaises(MeasurementUnitError):
            self._table({"1.0": {"abc": 10.0}})

    def test_non_string_frequency_key_is_rejected(self):
        with self.assertRaises(TypeError):
            self._table({1.0: {"-40.0": 20.0}})  # type: ignore[dict-item]

    def test_numeric_input_power_keys_are_read_as_dbm(self):
        table = self._table({"1.0": {-40.0: 20.0}})  # type: ignore[dict-item]
        self.assertEqual(
            interpolate_driver_output_power(
                frequency=ghz(1.0), sg_power=dbm(-40.0), driver_mapping=table
            ).dbm,
            20.0,
        )

    def test_boolean_input_power_key_is_rejected(self):
        with self.assertRaises(MeasurementUnitError):
            self._table({"1.0": {True: 20.0}})  # type: ignore[dict-item]

    def test_has_points_separates_missing_and_empty_frequencies(self):
        table = self._table(SAMPLE_DRIVER_MAPPING)
        self.assertTrue(table.has_points(ghz(1.0)))
        self.assertFalse(table.has_points(ghz(2.4)))
        self.assertFalse(self._table({"1.0": {}}).has_points(ghz(1.0)))

    def test_raw_mapping_is_not_accepted_by_the_pure_function(self):
        with self.assertRaises(TypeError):
            interpolate_driver_output_power(
                frequency=ghz(1.0), sg_power=dbm(-40.0),
                driver_mapping=SAMPLE_DRIVER_MAPPING)  # type: ignore[arg-type]


class CalculateDutInputPowerTests(unittest.TestCase):
    """测试 calculate_dut_input_power()。"""

    def _table(self, mapping):
        return DriverOutputTable.from_mapping(mapping, source="驱动映射输入")

    def test_with_driver_mapping(self):
        """有驱动时走插值路径。"""
        result = calculate_dut_input_power(
            sg_power=dbm(-40.0), frequency=ghz(1.0),
            loss_table=SAMPLE_LOSS_TABLE,
            driver_mapping=self._table(SAMPLE_DRIVER_MAPPING))
        self.assertAlmostEqual(result.dbm, 20.0)

    def test_without_driver_mapping(self):
        """无驱动时走 sg_power - cable1 路径。"""
        result = calculate_dut_input_power(
            sg_power=dbm(-10.0), frequency=ghz(1.0),
            loss_table=SAMPLE_LOSS_TABLE,
            driver_mapping=None)
        expected = -10.0 - SAMPLE_LOSS_DATA["1.0"]["cable1"]
        self.assertAlmostEqual(result.dbm, expected)

    def test_none_and_empty_driver_mapping_take_the_no_driver_path(self):
        """driver_mapping=None 和空表都按当前行为走无驱动路径。"""
        result_none = calculate_dut_input_power(
            sg_power=dbm(-10.0), frequency=ghz(1.0),
            loss_table=SAMPLE_LOSS_TABLE,
            driver_mapping=None)
        result_empty = calculate_dut_input_power(
            sg_power=dbm(-10.0), frequency=ghz(1.0),
            loss_table=SAMPLE_LOSS_TABLE,
            driver_mapping=DriverOutputTable())
        self.assertAlmostEqual(result_none.dbm, -10.0 - 1.625)
        self.assertAlmostEqual(result_empty.dbm, -10.0 - 1.625)

    def test_driver_mode_with_range_outside(self):
        """驱动模式下超出范围时使用端点值。"""
        result = calculate_dut_input_power(
            sg_power=dbm(0.0), frequency=ghz(1.0),
            loss_table=SAMPLE_LOSS_TABLE,
            driver_mapping=self._table(SAMPLE_DRIVER_MAPPING))
        self.assertAlmostEqual(result.dbm, 30.0)

    def test_missing_cable1_names_the_field(self):
        loss_table = CableLossTable.from_mapping({"1.0": {"cable2": 1.0}})
        with self.assertRaises(MeasurementValueError) as raised:
            calculate_dut_input_power(
                sg_power=dbm(-10.0), frequency=ghz(1.0),
                loss_table=loss_table, driver_mapping=None)
        self.assertIn("cable1", str(raised.exception))


# ---------------------------------------------------------------------------
# 阶段 3 测试：增益、效率和压缩点
# ---------------------------------------------------------------------------

class CalculateGainTests(unittest.TestCase):
    """测试 calculate_gain()。"""

    def test_normal_gain(self):
        self.assertAlmostEqual(calculate_gain(dbm(30.0), dbm(10.0)).db, 20.0)

    def test_zero_output(self):
        self.assertAlmostEqual(calculate_gain(dbm(0.0), dbm(10.0)).db, -10.0)

    def test_negative_gain(self):
        self.assertAlmostEqual(calculate_gain(dbm(5.0), dbm(15.0)).db, -10.0)

    def test_raw_numbers_are_rejected(self):
        with self.assertRaises(TypeError):
            calculate_gain(30.0, 10.0)  # type: ignore[arg-type]


class CalculateEfficiencyTests(unittest.TestCase):
    """测试 calculate_efficiency()。"""

    def test_normal_efficiency(self):
        """30 dBm = 1 W，dc_power=10W -> 10%"""
        result = calculate_efficiency(dbm(30.0), PowerWatt(10.0))
        self.assertAlmostEqual(result.percent, 10.0)

    def test_zero_dc_power_returns_zero(self):
        """DC 功耗为 0 时返回 0。"""
        self.assertEqual(calculate_efficiency(dbm(30.0), PowerWatt(0.0)), Percent(0))

    def test_negative_dc_power_returns_zero(self):
        """DC 功耗为负时返回 0。"""
        self.assertEqual(calculate_efficiency(dbm(30.0), PowerWatt(-5.0)), Percent(0))

    def test_high_power(self):
        """45 dBm = 31.62W，dc_power=100W -> 31.62%"""
        result = calculate_efficiency(dbm(45.0), PowerWatt(100.0))
        expected = (10 ** ((45.0 - 30) / 10) / 100) * 100
        self.assertAlmostEqual(result.percent, expected)

    def test_units_must_be_explicit(self):
        with self.assertRaises(TypeError):
            calculate_efficiency(dbm(30.0), 10.0)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            calculate_efficiency(dbm(30.0), dbm(10.0))  # type: ignore[arg-type]


class CalculateCompressionResultTests(unittest.TestCase):
    """测试 calculate_compression_result()。"""

    def _make_sweep(self, n_points, gain_base=20.0, compression_at=None,
                    compression_value=5.0):
        """生成扫描数据列表。

        gain 从 gain_base 开始，在 compression_at 索引处下降 compression_value。
        """
        gains = []
        for i in range(n_points):
            if compression_at is not None and i >= compression_at:
                gains.append(gain_base - compression_value)
            else:
                gains.append(gain_base)
        input_powers = [10.0 + i for i in range(n_points)]
        output_powers = [input_powers[i] + gains[i] for i in range(n_points)]
        efficiencies = [50.0 + i for i in range(n_points)]
        sg_powers = [-10.0 + i for i in range(n_points)]
        return (
            [db(value) for value in gains],
            [dbm(value) for value in input_powers],
            [dbm(value) for value in output_powers],
            [Percent(value) for value in efficiencies],
            [dbm(value) for value in sg_powers],
        )

    def test_compression_achieved(self):
        """正常达到压缩点。"""
        gains, inp, outp, eff, sg = self._make_sweep(10, compression_at=5)
        result = calculate_compression_result(
            gains=gains, input_powers=inp, output_powers=outp,
            efficiencies=eff, sg_powers=sg,
            compression_value=db(5.0), small_gain_points=3)

        self.assertIsInstance(result, CompressionResult)
        self.assertIsInstance(result.compression_point, CompressionPoint)
        self.assertTrue(result.compression_achieved)
        self.assertAlmostEqual(result.small_signal_gain.db, 20.0)
        self.assertAlmostEqual(result.compression_point.compression.db, 5.0)
        self.assertAlmostEqual(result.max_compression.db, 5.0)
        self.assertEqual(result.compression_point.input_power, dbm(15.0))

    def test_compression_not_achieved(self):
        """未达到压缩点。"""
        gains, inp, outp, eff, sg = self._make_sweep(10, compression_at=None)
        result = calculate_compression_result(
            gains=gains, input_powers=inp, output_powers=outp,
            efficiencies=eff, sg_powers=sg,
            compression_value=db(5.0), small_gain_points=3)

        self.assertFalse(result.compression_achieved)
        # 最接近的点是最后一个（压缩量为 0）
        self.assertAlmostEqual(result.compression_point.compression.db, 0.0)
        self.assertAlmostEqual(result.max_compression.db, 0.0)

    def test_fewer_than_3_points(self):
        """有效点不足 3 个时，小信号增益取第一个点。"""
        gains = [db(20.0), db(19.0)]
        inp = [dbm(10.0), dbm(11.0)]
        outp = [dbm(30.0), dbm(30.0)]
        eff = [Percent(50.0), Percent(48.0)]
        sg = [dbm(-10.0), dbm(-9.0)]
        result = calculate_compression_result(
            gains=gains, input_powers=inp, output_powers=outp,
            efficiencies=eff, sg_powers=sg,
            compression_value=db(5.0), small_gain_points=3)

        self.assertAlmostEqual(result.small_signal_gain.db, 20.0)
        self.assertFalse(result.compression_achieved)

    def test_exactly_3_points(self):
        """恰好 3 个点时，小信号增益取 3 点平均。"""
        gains = [db(20.0), db(20.0), db(20.0)]
        inp = [dbm(10.0), dbm(11.0), dbm(12.0)]
        outp = [dbm(30.0), dbm(31.0), dbm(32.0)]
        eff = [Percent(50.0), Percent(51.0), Percent(52.0)]
        sg = [dbm(-10.0), dbm(-9.0), dbm(-8.0)]
        result = calculate_compression_result(
            gains=gains, input_powers=inp, output_powers=outp,
            efficiencies=eff, sg_powers=sg,
            compression_value=db(5.0), small_gain_points=3)

        self.assertAlmostEqual(result.small_signal_gain.db, 20.0)

    def test_early_stop_with_fewer_points(self):
        """扫描提前停止且不足 3 点时仍能计算。"""
        gains = [db(20.0)]
        inp = [dbm(10.0)]
        outp = [dbm(30.0)]
        eff = [Percent(50.0)]
        sg = [dbm(-10.0)]
        result = calculate_compression_result(
            gains=gains, input_powers=inp, output_powers=outp,
            efficiencies=eff, sg_powers=sg,
            compression_value=db(5.0), small_gain_points=3)

        self.assertAlmostEqual(result.small_signal_gain.db, 20.0)
        self.assertFalse(result.compression_achieved)
        self.assertAlmostEqual(result.compression_point.input_power.dbm, 10.0)

    def test_empty_list_raises_value_error(self):
        """空扫描点是无效输入：显式 ValueError。"""
        with self.assertRaises(ValueError):
            calculate_compression_result(
                gains=[], input_powers=[], output_powers=[],
                efficiencies=[], sg_powers=[],
                compression_value=db(5.0), small_gain_points=3)

    def test_unequal_columns_are_rejected(self):
        with self.assertRaises(ValueError):
            calculate_compression_result(
                gains=[db(20.0), db(19.0)], input_powers=[dbm(10.0)],
                output_powers=[dbm(30.0), dbm(29.0)],
                efficiencies=[Percent(50.0), Percent(49.0)],
                sg_powers=[dbm(-10.0), dbm(-9.0)],
                compression_value=db(5.0))

    def test_raw_numbers_are_rejected(self):
        with self.assertRaises(TypeError):
            calculate_compression_result(
                gains=[20.0], input_powers=[dbm(10.0)], output_powers=[dbm(30.0)],
                efficiencies=[Percent(50.0)], sg_powers=[dbm(-10.0)],
                compression_value=db(5.0))

    def test_invalid_small_gain_point_count_is_rejected(self):
        """小信号增益点数必须是正整数：布尔值、浮点数、0 和负数都不能进入切片。"""
        gains = [db(20.0), db(20.0), db(19.0)]
        inp = [dbm(10.0), dbm(11.0), dbm(12.0)]
        outp = [dbm(30.0), dbm(31.0), dbm(31.0)]
        eff = [Percent(50.0), Percent(49.0), Percent(48.0)]
        sg = [dbm(-10.0), dbm(-9.0), dbm(-8.0)]
        for value in (True, 1.5, "3", None):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    calculate_compression_result(
                        gains=gains, input_powers=inp, output_powers=outp,
                        efficiencies=eff, sg_powers=sg,
                        compression_value=db(5.0), small_gain_points=value)
        for value in (0, -1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    calculate_compression_result(
                        gains=gains, input_powers=inp, output_powers=outp,
                        efficiencies=eff, sg_powers=sg,
                        compression_value=db(5.0), small_gain_points=value)

    def test_small_gain_point_count_controls_the_average(self):
        gains = [db(20.0), db(22.0), db(18.0), db(10.0)]
        inp = [dbm(10.0), dbm(11.0), dbm(12.0), dbm(13.0)]
        outp = [dbm(30.0), dbm(33.0), dbm(30.0), dbm(23.0)]
        eff = [Percent(50.0), Percent(51.0), Percent(52.0), Percent(53.0)]
        sg = [dbm(-10.0), dbm(-9.0), dbm(-8.0), dbm(-7.0)]
        result = calculate_compression_result(
            gains=gains, input_powers=inp, output_powers=outp,
            efficiencies=eff, sg_powers=sg,
            compression_value=db(5.0), small_gain_points=2)

        self.assertAlmostEqual(result.small_signal_gain.db, 21.0)
        self.assertAlmostEqual(result.max_compression.db, 11.0)

    def test_result_mapping_matches_the_result_document(self):
        """``to_mapping()`` 的输出键与既有结果文档一致。"""
        gains, inp, outp, eff, sg = self._make_sweep(10, compression_at=5)
        mapping = calculate_compression_result(
            gains=gains, input_powers=inp, output_powers=outp,
            efficiencies=eff, sg_powers=sg,
            compression_value=db(5.0), small_gain_points=3).to_mapping()

        self.assertEqual(
            set(mapping),
            {"compression_point", "small_signal_gain",
             "compression_achieved", "max_compression"},
        )
        self.assertEqual(
            set(mapping["compression_point"]),
            {"input_power", "output_power", "gain",
             "efficiency", "compression_dB", "sg_power_at_compression"},
        )


if __name__ == "__main__":
    unittest.main()
