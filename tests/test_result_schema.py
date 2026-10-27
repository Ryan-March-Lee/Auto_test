"""正式结果 schema 的唯一读取入口测试。

步骤 6 收敛结果模型后只有一套正式结果契约（``application.dto.measurement_results``）：

- 文档必须带 ``run_id``、``measurement_type`` 和 ``status``；
- 测量类型必须与结果数据字段一一对应；
- 只允许正式字段名：``legacy_payload``、``config``、``derived_metrics``、
  ``resource_snapshot`` 等旧别名与未知字段一律报错；
- 行式 ``sweep_data``、列长度不一致、非有限数值、非布尔 ``compression_achieved``
  都是非正式结果，必须明确报错。
"""

import json
import unittest

from application.dto import (
    AmplifierMeasurementResult,
    CableLossMeasurementResult,
    DriverPowerMappingMeasurementResult,
    MeasurementStatus,
    MeasurementType,
    measurement_result_from_document,
)
from domain.measurement.values import Decibels


def _cable_document(**overrides):
    document = {
        "run_id": "cable-run",
        "measurement_type": "cable_loss",
        "status": "completed",
        "attenuator_value": 20.0,
        "cable_losses": {
            "1.0": {"total_path1": 10.0, "total_path2": 12.0, "cable1": 1.0},
        },
    }
    document.update(overrides)
    return document


def _driver_document(**overrides):
    document = {
        "run_id": "driver-run",
        "measurement_type": "driver_power_mapping",
        "status": "completed",
        "power_mapping": {"1.0": {"-40.0": 5.0, "-30.0": 15.0}},
    }
    document.update(overrides)
    return document


def _amplifier_document(**overrides):
    document = {
        "run_id": "amp-run",
        "measurement_type": "amplifier",
        "status": "completed",
        "measurement_time": "2026-08-21 10:00:00",
        "results": {
            "1.0": {
                "sweep_data": {
                    "input_power_dut": [1.0, 2.0],
                    "output_power_dut": [10.0, 19.0],
                    "gain": [9.0, 17.0],
                    "efficiency": [30.0, 40.0],
                },
                "compression_point": {
                    "output_power_dbm": 19.0,
                    "input_power_dbm": 2.0,
                    "gain_db": 17.0,
                },
                "small_signal_gain_db": 9.0,
                "compression_achieved": True,
            }
        },
    }
    document.update(overrides)
    return document


class FormalResultDocumentTests(unittest.TestCase):
    def test_cable_loss_document_becomes_typed_points(self):
        result = measurement_result_from_document(_cable_document())

        self.assertIsInstance(result, CableLossMeasurementResult)
        self.assertIs(result.measurement_type, MeasurementType.CABLE_LOSS)
        self.assertIs(result.status, MeasurementStatus.COMPLETED)
        self.assertEqual(len(result.points), 1)
        self.assertEqual(result.points[0].frequency_ghz, "1.0")
        self.assertEqual(result.points[0].cable_losses.cable1, Decibels(1.0))
        self.assertEqual(result.point_count, 1)

    def test_driver_mapping_document_becomes_typed_points(self):
        result = measurement_result_from_document(_driver_document())

        self.assertIsInstance(result, DriverPowerMappingMeasurementResult)
        self.assertEqual(len(result.points), 2)
        self.assertEqual(result.points[0].input_power_dbm, -40.0)
        self.assertEqual(result.points[0].output_power_dbm, 5.0)
        self.assertEqual(
            result.sweep_rows()["1.0"][1]["gain"], 15.0 - (-30.0)
        )

    def test_amplifier_document_reads_columnar_sweep_data(self):
        result = measurement_result_from_document(_amplifier_document())

        self.assertIsInstance(result, AmplifierMeasurementResult)
        self.assertEqual(len(result.frequencies), 1)
        frequency = result.frequencies[0]
        self.assertEqual(frequency.compression_point.output_power_dbm, 19.0)
        self.assertEqual(frequency.compression_achieved, True)
        self.assertEqual(len(result.sweep_rows()["1.0"]), 2)
        self.assertEqual(result.saturation_points()[0]["output_power"], 19.0)
        self.assertEqual(result.point_count, 2)

    def test_documents_round_trip_through_to_dict(self):
        for document in (_cable_document(), _driver_document(), _amplifier_document()):
            with self.subTest(document=document["measurement_type"]):
                result = measurement_result_from_document(document)
                restored = measurement_result_from_document(result.to_dict())
                self.assertEqual(restored.to_dict(), result.to_dict())
                json.dumps(result.to_dict())

    def test_artifact_metadata_fields_are_accepted(self):
        document = _cable_document(
            schema_version="1.0",
            result_type="cable_loss",
            saved_at="2026-10-27T00:00:00",
        )
        self.assertIsInstance(
            measurement_result_from_document(document), CableLossMeasurementResult
        )


class NonFormalResultRejectionTests(unittest.TestCase):
    def test_missing_run_id_is_rejected(self):
        for document in (_cable_document(), _driver_document(), _amplifier_document()):
            document.pop("run_id")
            with self.subTest(document=document["measurement_type"]):
                with self.assertRaises(ValueError):
                    measurement_result_from_document(document)

    def test_missing_measurement_type_is_rejected(self):
        document = _cable_document()
        document.pop("measurement_type")
        with self.assertRaises(ValueError):
            measurement_result_from_document(document)

    def test_unknown_measurement_type_is_rejected(self):
        with self.assertRaises(ValueError):
            measurement_result_from_document(_cable_document(measurement_type="unknown"))

    def test_missing_status_is_rejected(self):
        document = _driver_document()
        document.pop("status")
        with self.assertRaises(ValueError):
            measurement_result_from_document(document)

    def test_measurement_type_must_match_the_result_field(self):
        with self.assertRaises(ValueError):
            measurement_result_from_document(
                _driver_document(cable_losses={"1.0": {"cable1": 1.0}})
            )
        with self.assertRaises(ValueError):
            measurement_result_from_document(
                _cable_document(measurement_type="amplifier")
            )

    def test_legacy_payload_and_aliases_are_rejected(self):
        for key, value in (
            ("legacy_payload", {"results": {}}),
            ("config", {"frequency": 1e9}),
            ("derived_metrics", {"gain_max": 30.0}),
            ("resource_snapshot", {"analyzer": "sim"}),
            ("plan_snapshot", {"frequency": 1e9}),
            ("raw_readings", {"gain": 1.0}),
            ("metadata", {"run_id": "amp-run"}),
            ("original_filename", "old.json"),
        ):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    measurement_result_from_document(_cable_document(**{key: value}))

    def test_unknown_document_field_is_rejected(self):
        with self.assertRaises(ValueError):
            measurement_result_from_document(_cable_document(extra_field=1))

    def test_unknown_amplifier_frequency_field_is_rejected(self):
        document = _amplifier_document()
        document["results"]["1.0"]["small_signal_gain"] = 9.0
        with self.assertRaises(ValueError):
            measurement_result_from_document(document)

    def test_row_shaped_sweep_data_is_rejected(self):
        with self.assertRaises(ValueError):
            measurement_result_from_document(
                _amplifier_document(
                    results={"1.0": {"sweep_data": [{"gain": 9.0}]}}
                )
            )

    def test_mismatched_sweep_columns_are_rejected(self):
        with self.assertRaises(ValueError):
            measurement_result_from_document(
                _amplifier_document(
                    results={
                        "1.0": {
                            "sweep_data": {"gain": [9.0, 17.0], "efficiency": [30.0]}
                        }
                    }
                )
            )

    def test_non_finite_numbers_are_rejected(self):
        with self.assertRaises(ValueError):
            measurement_result_from_document(
                _driver_document(power_mapping={"1.0": {"-40.0": float("nan")}})
            )
        with self.assertRaises(ValueError):
            measurement_result_from_document(
                _cable_document(attenuator_value=float("inf"))
            )

    def test_invalid_frequency_key_is_rejected(self):
        for document in (
            _cable_document(cable_losses={"invalid": {"cable1": 1.0}}),
            _driver_document(power_mapping={"invalid": {"-40.0": 5.0}}),
            _amplifier_document(results={"invalid": {"sweep_data": {"gain": [1.0]}}}),
        ):
            with self.subTest(document=document["measurement_type"]):
                with self.assertRaises(ValueError):
                    measurement_result_from_document(document)

    def test_frequency_key_text_is_preserved_on_read_and_rewrite(self):
        """读取保留文件里的键文本；解析仍走唯一的频率边界（无单位按 GHz）。"""
        for key in ("2.40", "2.4GHz"):
            with self.subTest(key=key):
                document = _cable_document(
                    cable_losses={key: {"cable1": 1.0}}
                )
                result = measurement_result_from_document(document)
                self.assertEqual(result.points[0].frequency_ghz, key)
                self.assertEqual(result.to_dict()["cable_losses"], {key: {"cable1": 1.0}})

    def test_driver_mapping_legacy_object_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            measurement_result_from_document(
                _driver_document(
                    power_mapping={"1.0": {"input_power": 0.0, "output_power": 10.0}}
                )
            )

    def test_non_mapping_document_is_rejected(self):
        with self.assertRaises(ValueError):
            measurement_result_from_document(["not", "a", "mapping"])


class SweepDataContractTests(unittest.TestCase):
    """扫描数据必须是可通过绘图与 CSV 导出的有限数值列。"""

    def test_non_finite_sweep_value_is_rejected(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                document = _amplifier_document()
                document["results"]["1.0"]["sweep_data"]["gain"] = [value]
                with self.assertRaises(ValueError):
                    measurement_result_from_document(document)

    def test_non_numeric_sweep_value_is_rejected(self):
        for value in ("10.0", None, {"value": 1.0}, [1.0]):
            with self.subTest(value=value):
                document = _amplifier_document()
                document["results"]["1.0"]["sweep_data"]["gain"] = [value]
                with self.assertRaises(ValueError):
                    measurement_result_from_document(document)

    def test_mapping_sweep_columns_are_validated_and_frozen(self):
        document = _amplifier_document()
        document["results"]["1.0"]["sweep_data"]["voltages"] = [
            {"main_A": 28.0},
            {"main_A": 28.5},
        ]
        result = measurement_result_from_document(document)
        column = result.frequencies[0].sweep_data["voltages"]
        self.assertEqual(column[1]["main_A"], 28.5)
        with self.assertRaises(TypeError):
            column[0]["main_A"] = 0.0

    def test_mapping_sweep_column_rejects_non_finite_leaf(self):
        document = _amplifier_document()
        document["results"]["1.0"]["sweep_data"]["currents"] = [
            {"main_A": 0.1},
            {"main_A": float("nan")},
        ]
        with self.assertRaises(ValueError):
            measurement_result_from_document(document)


class ArtifactMetadataTests(unittest.TestCase):
    def test_result_type_label_must_match_measurement_type(self):
        with self.assertRaisesRegex(ValueError, "result_type"):
            measurement_result_from_document(
                _cable_document(result_type="driver_power_mapping")
            )

    def test_known_alias_label_is_accepted(self):
        result = measurement_result_from_document(
            _amplifier_document(result_type="amplifier_measurement")
        )
        self.assertEqual(result.measurement_type.value, "amplifier")

    def test_unknown_result_type_label_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "result_type"):
            measurement_result_from_document(_cable_document(result_type="mystery"))

    def test_unsupported_schema_version_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "schema_version"):
            measurement_result_from_document(_cable_document(schema_version="2.0"))

    def test_blank_saved_at_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "saved_at"):
            measurement_result_from_document(_cable_document(saved_at="  "))

    def test_canonical_artifact_requires_its_own_result_type(self):
        payload = {
            "canonical_model": _cable_document(),
            "run_id": "cable-run",
            "result_type": "cable_loss",
        }
        with self.assertRaisesRegex(ValueError, "版本化模型工件"):
            measurement_result_from_document(payload)

    def test_shared_base_result_is_not_formal(self):
        from application.dto import MeasurementResult, require_formal_result

        base = MeasurementResult(measurement_type="cable_loss", run_id="base-run")
        with self.assertRaisesRegex(ValueError, "正式测量结果 DTO"):
            require_formal_result(base)
        with self.assertRaisesRegex(ValueError, "正式测量结果 DTO"):
            require_formal_result({"measurement_type": "cable_loss"})

    def test_concrete_results_are_formal(self):
        from application.dto import require_formal_result

        for document in (_cable_document(), _driver_document(), _amplifier_document()):
            with self.subTest(document=document["measurement_type"]):
                result = require_formal_result(measurement_result_from_document(document))
                self.assertTrue(result.is_formal_result)


class ResultContractValueTests(unittest.TestCase):
    def test_compression_achieved_parses_false_string(self):
        document = _amplifier_document()
        document["results"]["1.0"]["compression_achieved"] = "false"
        result = measurement_result_from_document(document)
        self.assertIs(result.frequencies[0].compression_achieved, False)

    def test_invalid_compression_achieved_is_rejected(self):
        document = _amplifier_document()
        document["results"]["1.0"]["compression_achieved"] = "maybe"
        with self.assertRaises(ValueError):
            measurement_result_from_document(document)

    def test_compression_point_values_are_finite_numbers(self):
        document = _amplifier_document()
        document["results"]["1.0"]["compression_point"] = {"output_power_dbm": "oops"}
        with self.assertRaises(ValueError):
            measurement_result_from_document(document)

    def test_nested_snapshots_are_immutable_and_json_serializable(self):
        source = {"device": {"model": "sim"}, "levels": [1, {"value": 2}]}
        document = _amplifier_document(configuration_snapshot=source)
        result = measurement_result_from_document(document)
        source["device"]["model"] = "changed"
        self.assertEqual(result.configuration_snapshot["device"]["model"], "sim")
        with self.assertRaises(TypeError):
            result.configuration_snapshot["device"]["model"] = "changed"
        self.assertEqual(
            json.loads(json.dumps(result.to_dict()))["run_id"], "amp-run"
        )

    def test_result_contract_reads_only_formal_field_names(self):
        document = _amplifier_document(
            device_summary={"analyzer": "sim"},
            raw_data_reference={"raw_data_path": "raw.csv"},
            calculation_summary={"gain_max": 30.0},
            error="warning",
            errors=["warning"],
            cleanup_records=[{"power_off": "ok"}],
            created_at="2026-10-27T00:00:00+00:00",
        )
        result = measurement_result_from_document(document)

        self.assertEqual(result.device_summary["analyzer"], "sim")
        self.assertEqual(result.raw_data_reference["raw_data_path"], "raw.csv")
        self.assertEqual(result.calculation_summary["gain_max"], 30.0)
        self.assertEqual(result.errors, ("warning",))
        self.assertEqual(result.cleanup_records[0]["power_off"], "ok")
        self.assertEqual(result.created_at, "2026-10-27T00:00:00+00:00")

    def test_cleanup_records_are_deeply_frozen(self):
        records = [{"power_off": {"status": "ok"}}]
        document = _amplifier_document(cleanup_records=records)
        result = measurement_result_from_document(document)

        records[0]["power_off"]["status"] = "changed"

        self.assertEqual(result.cleanup_records[0]["power_off"]["status"], "ok")
        with self.assertRaises(TypeError):
            result.cleanup_records[0]["power_off"]["status"] = "changed"

    def test_input_mapping_is_not_mutated(self):
        document = _amplifier_document()
        original = json.loads(json.dumps(document))

        measurement_result_from_document(document)

        self.assertEqual(document, original)


if __name__ == "__main__":
    unittest.main()
