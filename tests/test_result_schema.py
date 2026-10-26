"""正式结果 schema 的解析测试。

步骤 6 收敛结果模型后，解析层只接受正式 schema：结果文档必须带 ``run_id``，测量类型
由 ``cable_losses`` / ``power_mapping`` / ``results`` 之一确定，契约字段只读正式名称。
历史形状（``canonical_model`` + ``legacy_payload`` 混排、``config`` / ``derived_metrics``
等旧别名、行式 ``sweep_data``、旧驱动映射对象）必须直接报错。
"""

import copy
import json
import unittest

from domain.models import AmplifierMeasurementResult, CableLossResult, DriverPowerMappingResult
from domain.result_reading import parse_result_model


class ResultSchemaReadingTests(unittest.TestCase):
    def test_cable_loss_document_becomes_typed_points(self):
        model = parse_result_model({
            "run_id": "cable-run",
            "cable_losses": {
                "1": {"total_path1": 10, "total_path2": 12, "cable1": 1.0},
            },
        })

        self.assertIsInstance(model, CableLossResult)
        self.assertEqual(len(model.points), 1)
        self.assertEqual(model.points[0].frequency_hz, 1.0)
        self.assertEqual(model.points[0].path2_loss_db, 12.0)
        self.assertEqual(model.points[0].cable_losses_db["cable1"], 1.0)

    def test_cable_loss_invalid_frequency_key_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_result_model({
                "run_id": "cable-run",
                "cable_losses": {"invalid": {"total_path1": 99}},
            })

    def test_driver_mapping_document_becomes_typed_points(self):
        model = parse_result_model({
            "run_id": "driver-run",
            "power_mapping": {"1.0": {"-40.0": 5.0, "-30.0": 15.0}},
        })

        self.assertIsInstance(model, DriverPowerMappingResult)
        self.assertEqual(len(model.points), 2)
        self.assertEqual(model.points[0].input_power_dbm, -40.0)
        self.assertEqual(model.points[0].compensated_output_power_dbm, 5.0)

    def test_driver_mapping_legacy_object_shape_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_result_model({
                "run_id": "driver-run",
                "power_mapping": {
                    "1.0": {"input_power": 0.0, "output_power": 10.0},
                },
            })

    def test_amplifier_document_reads_columnar_sweep_data(self):
        model = parse_result_model({
            "run_id": "amp-run",
            "measurement_time": "2026-08-21 10:00:00",
            "results": {
                "1.0": {
                    "sweep_data": {
                        "input_power_dut": [1.0, 2.0],
                        "output_power_dut": [10.0, 19.0],
                        "gain": [9.0, 17.0],
                        "efficiency": [30.0, 40.0],
                    },
                    "compression_point": {"output_power": 19.0, "gain": 17.0},
                }
            },
        })

        self.assertIsInstance(model, AmplifierMeasurementResult)
        self.assertEqual(model.run_id, "amp-run")
        self.assertEqual(model.schema_version, "1.0")
        self.assertEqual(len(model.points), 2)
        self.assertEqual(model.points[1].output_power_dbm, 19.0)
        self.assertEqual(model.compression_points["1.0"].output_power_dbm, 19.0)

    def test_amplifier_row_shaped_sweep_data_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_result_model({
                "run_id": "amp-run",
                "results": {"1.0": {"sweep_data": [{"gain": 9.0}]}},
            })

    def test_result_contract_reads_only_formal_field_names(self):
        model = parse_result_model({
            "run_id": "trace-run",
            "configuration_snapshot": {"frequency": 1e9},
            "device_summary": {"analyzer": "sim"},
            "raw_data_reference": {"raw_data_path": "raw.csv"},
            "calculation_summary": {"gain_max": 30.0},
            "error": "warning",
            "errors": ["warning"],
            "cleanup_records": [{"power_off": "ok"}],
            "created_at": "2026-10-27T00:00:00+00:00",
            "results": {},
        })

        self.assertEqual(model.configuration_snapshot["frequency"], 1e9)
        self.assertEqual(model.device_summary["analyzer"], "sim")
        self.assertEqual(model.raw_data_reference["raw_data_path"], "raw.csv")
        self.assertEqual(model.calculation_summary["gain_max"], 30.0)
        self.assertEqual(model.errors, ("warning",))
        self.assertEqual(model.cleanup_records[0]["power_off"], "ok")
        self.assertEqual(model.created_at, "2026-10-27T00:00:00+00:00")

    def test_legacy_field_aliases_are_not_read(self):
        model = parse_result_model({
            "run_id": "alias-run",
            "config": {"frequency": 1e9},
            "device_summary_legacy": {"analyzer": "sim"},
            "derived_metrics": {"gain_max": 30.0},
            "results": {},
        })

        self.assertEqual(dict(model.configuration_snapshot), {})
        self.assertEqual(dict(model.calculation_summary), {})

    def test_legacy_payload_wrapper_is_rejected(self):
        document = {
            "legacy_payload": {"results": {}},
            "canonical_model": {"run_id": "canonical-run"},
        }
        with self.assertRaises(ValueError):
            parse_result_model(document)

    def test_document_without_run_id_is_rejected(self):
        for document in (
            {"results": {}},
            {"cable_losses": {}},
            {"power_mapping": {}},
        ):
            with self.subTest(document=document):
                with self.assertRaises(ValueError):
                    parse_result_model(document)

    def test_unknown_schema_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_result_model({"run_id": "unknown-run", "some_other_field": 1})

    def test_non_mapping_document_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_result_model(["not", "a", "mapping"])

    def test_input_mapping_is_not_mutated(self):
        data = {
            "run_id": "stable-run",
            "results": {
                "1.0": {"sweep_data": {"gain": [9.0, 17.0]}},
            },
        }
        original = copy.deepcopy(data)

        parse_result_model(data)

        self.assertEqual(data, original)

    def test_nested_snapshots_are_immutable_and_json_serializable(self):
        source = {"device": {"model": "sim"}, "levels": [1, {"value": 2}]}
        model = parse_result_model({
            "run_id": "nested-run",
            "configuration_snapshot": source,
            "results": {},
        })
        source["device"]["model"] = "changed"
        self.assertEqual(model.configuration_snapshot["device"]["model"], "sim")
        with self.assertRaises(TypeError):
            model.configuration_snapshot["device"]["model"] = "changed"
        self.assertEqual(json.loads(json.dumps(model.to_dict()))["run_id"], "nested-run")


if __name__ == "__main__":
    unittest.main()
