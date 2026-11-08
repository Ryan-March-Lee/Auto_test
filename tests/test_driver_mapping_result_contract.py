"""步骤 4：双层驱动映射结果契约。

覆盖重构计划「定义双层驱动映射结果契约」的验收口径：

- 原始实测点记录源功率、补偿后驱放输出与增益；
- 目标网格记录目标 DUT 输入功率、反查源功率和插值输出功率；
- 正式文档必须显式带 ``schema_version = 2.0``；
- 旧 ``power_mapping``（输入 -> 输出）字典不能被静默解释为目标网格；
- 结构缺失、未知字段、非有限数值、重复锚点和无效版本都明确报错。

验证方式：

```powershell
& "D:\\Anaconda\\envs\\Auto_test\\python.exe" -m unittest tests.test_driver_mapping_result_contract -v
```
"""

import json
import unittest

from application.dto import (
    DRIVER_MAPPING_SCHEMA_VERSION,
    DriverMappingInput,
    DriverMappingRawPoint,
    DriverMappingTargetPoint,
    DriverPowerMappingMeasurementResult,
    MeasurementResult,
    measurement_result_from_document,
)


def _document(**overrides):
    document = {
        "run_id": "driver-run",
        "measurement_type": "driver_power_mapping",
        "status": "completed",
        "schema_version": DRIVER_MAPPING_SCHEMA_VERSION,
        "raw_points": {
            "2.4": [
                {
                    "source_power_dbm": -20.0,
                    "driver_output_power_dbm": 10.0,
                    "gain_db": 30.0,
                },
                {
                    "source_power_dbm": -15.0,
                    "driver_output_power_dbm": 14.5,
                    "gain_db": 29.5,
                },
            ]
        },
        "target_mapping": {
            "2.4": [
                {
                    "target_input_power_dbm": 12.0,
                    "source_power_dbm": -17.5,
                    "driver_output_power_dbm": 12.0,
                }
            ]
        },
    }
    document.update(overrides)
    return document


class DriverMappingResultDocumentTests(unittest.TestCase):
    def test_two_layers_round_trip_through_the_formal_document(self):
        result = measurement_result_from_document(_document())

        self.assertIsInstance(result, DriverPowerMappingMeasurementResult)
        self.assertEqual(len(result.raw_points), 2)
        self.assertEqual(result.raw_points[0].source_power_dbm, -20.0)
        self.assertEqual(result.raw_points[0].driver_output_power_dbm, 10.0)
        self.assertEqual(result.raw_points[0].gain_db, 30.0)
        self.assertEqual(len(result.target_points), 1)
        self.assertEqual(result.target_points[0].target_input_power_dbm, 12.0)
        self.assertEqual(result.target_points[0].source_power_dbm, -17.5)
        self.assertEqual(result.target_points[0].driver_output_power_dbm, 12.0)

        document = result.to_dict()
        self.assertEqual(document["schema_version"], DRIVER_MAPPING_SCHEMA_VERSION)
        self.assertNotIn("power_mapping", document)
        json.dumps(document)
        self.assertEqual(
            measurement_result_from_document(document).to_dict(), document
        )

    def test_empty_target_mapping_is_valid_contract_but_not_a_formal_grid(self):
        """映射刚完成原始点、目标表尚未生成时，文档结构仍然合法。"""

        result = measurement_result_from_document(_document(target_mapping={}))

        self.assertEqual(result.target_points, ())
        self.assertEqual(result.to_dict()["target_mapping"], {})

    def test_raw_mapping_is_an_audit_view_not_the_target_table(self):
        result = measurement_result_from_document(_document())

        self.assertEqual(result.raw_mapping(), {"2.4": {"-20.0": 10.0, "-15.0": 14.5}})
        self.assertEqual(result.target_points[0].target_input_power_dbm, 12.0)

    def test_raw_point_derives_gain_when_it_is_omitted_in_code(self):
        point = DriverMappingRawPoint(
            frequency_ghz="2.4", source_power_dbm=-20.0, driver_output_power_dbm=10.0
        )

        self.assertEqual(point.gain_db, 30.0)


class DriverMappingInputTests(unittest.TestCase):
    """正式输入边界：目标网格是主视图，原始点只随输入携带供审计。"""

    def test_input_exposes_the_target_grid_as_its_primary_view(self):
        result = measurement_result_from_document(_document())

        driver_input = DriverMappingInput.from_result(result)

        self.assertEqual(
            driver_input.target_mapping(), {"2.4": {12.0: (-17.5, 12.0)}}
        )
        self.assertEqual(
            driver_input.raw_mapping(), {"2.4": {"-20.0": 10.0, "-15.0": 14.5}}
        )

    def test_empty_target_grid_cannot_be_a_formal_input(self):
        result = measurement_result_from_document(_document(target_mapping={}))

        with self.assertRaisesRegex(ValueError, "目标网格"):
            DriverMappingInput.from_result(result)

    def test_target_frequency_without_raw_points_is_rejected(self):
        result = measurement_result_from_document(_document())

        with self.assertRaisesRegex(ValueError, "缺少原始实测点"):
            DriverMappingInput(
                raw_points=result.raw_points,
                target_points=(
                    DriverMappingTargetPoint(
                        frequency_ghz="5.8",
                        target_input_power_dbm=12.0,
                        source_power_dbm=-17.5,
                        driver_output_power_dbm=12.0,
                    ),
                ),
            )

    def test_target_source_power_outside_the_raw_curve_is_rejected(self):
        result = measurement_result_from_document(_document())

        with self.assertRaisesRegex(ValueError, "超出原始映射覆盖范围"):
            DriverMappingInput(
                raw_points=result.raw_points,
                target_points=(
                    DriverMappingTargetPoint(
                        frequency_ghz="2.4",
                        target_input_power_dbm=12.0,
                        source_power_dbm=-25.0,
                        driver_output_power_dbm=12.0,
                    ),
                ),
            )

    def test_input_requires_a_driver_mapping_result(self):
        with self.assertRaisesRegex(ValueError, "driver_power_mapping"):
            DriverMappingInput.from_result(
                MeasurementResult(
                    measurement_type="cable_loss",
                    run_id="driver-run",
                )
            )


class DriverMappingResultRejectionTests(unittest.TestCase):
    def test_missing_layers_are_rejected(self):
        document = _document()
        document.pop("raw_points")
        with self.assertRaisesRegex(ValueError, "raw_points"):
            measurement_result_from_document(document)

        document = _document()
        document.pop("target_mapping")
        with self.assertRaisesRegex(ValueError, "target_mapping"):
            measurement_result_from_document(document)

    def test_missing_schema_version_is_rejected_with_migration_hint(self):
        document = _document()
        document.pop("schema_version")

        with self.assertRaisesRegex(ValueError, "schema_version"):
            measurement_result_from_document(document)

    def test_unknown_schema_version_is_rejected(self):
        for version in ("1.0", "3.0", 2):
            with self.subTest(version=version):
                with self.assertRaisesRegex(ValueError, "schema_version"):
                    measurement_result_from_document(
                        _document(schema_version=version)
                    )

    def test_legacy_power_mapping_is_rejected_instead_of_being_read_as_target_grid(self):
        document = _document()
        document.pop("raw_points")
        document.pop("target_mapping")
        document["power_mapping"] = {"2.4": {"-20.0": 10.0}}

        with self.assertRaisesRegex(ValueError, "power_mapping"):
            measurement_result_from_document(document)

    def test_missing_raw_point_field_is_rejected(self):
        document = _document()
        document["raw_points"]["2.4"][0].pop("gain_db")

        with self.assertRaisesRegex(ValueError, "gain_db"):
            measurement_result_from_document(document)

    def test_missing_target_point_field_is_rejected(self):
        document = _document()
        document["target_mapping"]["2.4"][0].pop("target_input_power_dbm")

        with self.assertRaisesRegex(ValueError, "target_input_power_dbm"):
            measurement_result_from_document(document)

    def test_unknown_point_fields_are_rejected(self):
        document = _document()
        document["raw_points"]["2.4"][0]["input_power_dbm"] = 1.0

        with self.assertRaisesRegex(ValueError, "未知字段"):
            measurement_result_from_document(document)

        document = _document()
        document["target_mapping"]["2.4"][0]["input_power_dbm"] = 1.0

        with self.assertRaisesRegex(ValueError, "未知字段"):
            measurement_result_from_document(document)

    def test_non_finite_values_are_rejected(self):
        for field, value in (
            ("source_power_dbm", float("nan")),
            ("driver_output_power_dbm", float("inf")),
            ("gain_db", float("-inf")),
        ):
            with self.subTest(field=field):
                document = _document()
                document["raw_points"]["2.4"][0][field] = value
                with self.assertRaises(ValueError):
                    measurement_result_from_document(document)

        document = _document()
        document["target_mapping"]["2.4"][0]["source_power_dbm"] = float("nan")
        with self.assertRaises(ValueError):
            measurement_result_from_document(document)

    def test_gain_must_match_the_two_powers(self):
        document = _document()
        document["raw_points"]["2.4"][0]["gain_db"] = 12.0

        with self.assertRaisesRegex(ValueError, "gain_db"):
            measurement_result_from_document(document)

    def test_duplicate_anchor_powers_within_a_frequency_are_rejected(self):
        document = _document()
        document["raw_points"]["2.4"].append(
            {
                "source_power_dbm": -20.0,
                "driver_output_power_dbm": 11.0,
                "gain_db": 31.0,
            }
        )
        with self.assertRaisesRegex(ValueError, "重复"):
            measurement_result_from_document(document)

        document = _document()
        document["target_mapping"]["2.4"].append(
            {
                "target_input_power_dbm": 12.0,
                "source_power_dbm": -16.0,
                "driver_output_power_dbm": 12.0,
            }
        )
        with self.assertRaisesRegex(ValueError, "重复"):
            measurement_result_from_document(document)

    def test_bucket_must_be_a_sequence_of_point_mappings(self):
        for value in ({"2.4": {"-20.0": 10.0}}, {"2.4": "not-a-sequence"}):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    measurement_result_from_document(_document(raw_points=value))

    def test_invalid_frequency_key_is_rejected(self):
        document = _document()
        document["raw_points"] = {
            "not-a-frequency": document["raw_points"]["2.4"]
        }

        with self.assertRaises(ValueError):
            measurement_result_from_document(document)


class ServicePayloadConversionTests(unittest.TestCase):
    def test_legacy_service_mapping_only_becomes_raw_audit_points(self):
        result = DriverPowerMappingMeasurementResult.from_service_payload(
            {"power_mapping": {"2.4": {"-20.0": 10.0}}},
            run_id="driver-run",
        )

        self.assertEqual(len(result.raw_points), 1)
        self.assertEqual(result.raw_points[0].source_power_dbm, -20.0)
        self.assertEqual(result.raw_points[0].driver_output_power_dbm, 10.0)
        self.assertEqual(result.raw_points[0].gain_db, 30.0)
        self.assertEqual(result.target_points, ())
        self.assertNotIn("power_mapping", result.to_dict())

    def test_new_service_payload_reads_both_layers(self):
        result = DriverPowerMappingMeasurementResult.from_service_payload(
            {
                "raw_points": _document()["raw_points"],
                "target_mapping": _document()["target_mapping"],
            },
            run_id="driver-run",
        )

        self.assertEqual(len(result.raw_points), 2)
        self.assertEqual(len(result.target_points), 1)
        self.assertEqual(result.target_points[0].target_input_power_dbm, 12.0)

    def test_service_payload_cannot_mix_legacy_and_new_raw_shapes(self):
        with self.assertRaisesRegex(ValueError, "power_mapping"):
            DriverPowerMappingMeasurementResult.from_service_payload(
                {
                    "raw_points": _document()["raw_points"],
                    "power_mapping": {"2.4": {"-20.0": 10.0}},
                },
                run_id="driver-run",
            )

    def test_service_payload_must_carry_raw_points(self):
        with self.assertRaisesRegex(ValueError, "原始映射点"):
            DriverPowerMappingMeasurementResult.from_service_payload(
                {"target_mapping": _document()["target_mapping"]},
                run_id="driver-run",
            )

    def test_null_legacy_service_mapping_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "power_mapping"):
            DriverPowerMappingMeasurementResult.from_service_payload(
                {"power_mapping": None}, run_id="driver-run"
            )

    def test_target_point_type_is_explicit(self):
        with self.assertRaises(ValueError):
            DriverPowerMappingMeasurementResult(
                run_id="driver-run", target_points=(object(),)
            )
        with self.assertRaises(ValueError):
            DriverPowerMappingMeasurementResult(
                run_id="driver-run", raw_points=(object(),)
            )
        point = DriverMappingTargetPoint(
            frequency_ghz="2.4",
            target_input_power_dbm=12.0,
            source_power_dbm=-17.5,
            driver_output_power_dbm=12.0,
        )
        self.assertEqual(point.target_input_power_dbm, 12.0)


if __name__ == "__main__":
    unittest.main()
