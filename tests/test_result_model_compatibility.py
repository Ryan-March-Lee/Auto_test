"""第一层离线测试：结果模型的历史格式兼容读取。"""

import copy
import json
import unittest

from domain.models import AmplifierMeasurementResult, CableLossResult
from domain.result_reading import parse_result_model


class ResultModelCompatibilityTests(unittest.TestCase):
    def test_cable_loss_legacy_payload_becomes_typed_points(self):
        model = parse_result_model({
            "run_id": "cable-run",
            "cable_losses": {
                "1": {"total_path1": 10, "total_path2": 12, "cable1": 1.0},
                "invalid": {"total_path1": 99},
            },
        })

        self.assertIsInstance(model, CableLossResult)
        self.assertEqual(len(model.points), 1)
        self.assertEqual(model.points[0].frequency_hz, 1.0)
        self.assertEqual(model.points[0].path2_loss_db, 12.0)
        self.assertEqual(model.points[0].cable_losses_db["cable1"], 1.0)

    def test_canonical_payload_overlays_metadata_without_losing_legacy_points(self):
        model = parse_result_model({
            "legacy_payload": {
                "results": {"1.0": {"sweep_data": {
                    "input_power_dut": [-10],
                    "output_power_dut": [20],
                    "gain": [30],
                }}},
            },
            "canonical_model": {
                "run_id": "canonical-run",
                "schema_version": "2.0",
                "method_version": "2.1",
                "plan_snapshot": {"frequencies": [1.0]},
                "metadata": {"run_id": "canonical-run", "result_type": "amplifier"},
            },
        })

        self.assertIsInstance(model, AmplifierMeasurementResult)
        self.assertEqual(len(model.points), 1)
        self.assertEqual(model.run_id, "canonical-run")
        self.assertEqual(model.schema_version, "2.0")
        self.assertEqual(model.method_version, "2.1")
        self.assertEqual(model.points[0].gain_db, 30.0)
        self.assertEqual(list(model.plan_snapshot["frequencies"]), [1.0])

    def test_non_mapping_rows_are_ignored_without_mutating_input(self):
        data = {
            "results": {
                "1.0": {"sweep_data": [None, "bad", {"output_power": 3}]},
            },
        }
        original = copy.deepcopy(data)

        model = parse_result_model(data)

        self.assertEqual(len(model.points), 1)
        self.assertEqual(model.points[0].output_power_dbm, 3.0)
        self.assertEqual(data, original)

    def test_non_numeric_mapping_values_use_compatibility_defaults(self):
        model = parse_result_model({
            "results": {
                "1.0": {"sweep_data": [{
                    "input_power": "invalid",
                    "output_power": "not-a-number",
                    "gain": None,
                    "dc_current": "invalid",
                }]},
            },
        })

        self.assertEqual(len(model.points), 1)
        point = model.points[0]
        self.assertEqual(point.input_power_dbm, 0.0)
        self.assertEqual(point.output_power_dbm, 0.0)
        self.assertEqual(point.gain_db, 0.0)
        self.assertIsNone(point.dc_current_a)

    def test_result_contract_contains_traceability_and_cleanup_fields(self):
        model = parse_result_model({
            "run_id": "trace-run",
            "config": {"frequency": 1e9},
            "device_summary": {"analyzer": "sim"},
            "data_reference": {"raw_data_path": "raw.csv"},
            "derived_metrics": {"gain_max": 30.0},
            "error": "warning",
            "cleanup": {"power_off": "ok"},
            "results": {},
        })

        self.assertEqual(model.configuration_snapshot["frequency"], 1e9)
        self.assertEqual(model.device_summary["analyzer"], "sim")
        self.assertEqual(model.raw_data_reference["raw_data_path"], "raw.csv")
        self.assertEqual(model.calculation_summary["gain_max"], 30.0)
        self.assertEqual(model.errors, ("warning",))
        self.assertEqual(model.cleanup_records[0]["power_off"], "ok")
        self.assertIsNone(model.created_at)

    def test_nested_snapshots_are_immutable_and_json_serializable(self):
        source = {"device": {"model": "sim"}, "levels": [1, {"value": 2}]}
        model = parse_result_model({
            "run_id": "nested-run", "config": source, "results": {},
        })
        source["device"]["model"] = "changed"
        self.assertEqual(model.configuration_snapshot["device"]["model"], "sim")
        with self.assertRaises(TypeError):
            model.configuration_snapshot["device"]["model"] = "changed"
        self.assertEqual(json.loads(json.dumps(model.to_dict()))["run_id"], "nested-run")

    def test_canonical_fields_override_legacy_without_loss(self):
        model = parse_result_model({
            "legacy_payload": {"results": {}},
            "canonical_model": {
                "run_id": "canonical-run",
                "configuration_snapshot": {"mode": "sim"},
                "device_summary": {"analyzer": "sim"},
                "raw_data_reference": {"path": "raw.csv"},
                "calculation_summary": {"gain": 30},
                "errors": ["warning"],
                "cleanup_records": [{"power_off": "ok"}],
                "created_at": "2026-01-01T00:00:00+00:00",
            },
        })
        self.assertEqual(model.run_id, "canonical-run")
        self.assertEqual(model.device_summary["analyzer"], "sim")
        self.assertEqual(model.errors, ("warning",))
        self.assertEqual(model.created_at, "2026-01-01T00:00:00+00:00")

    def test_missing_legacy_created_at_is_stable(self):
        first = parse_result_model({"run_id": "stable", "results": {}})
        second = parse_result_model({"run_id": "stable", "results": {}})
        self.assertIsNone(first.created_at)
        self.assertEqual(first.created_at, second.created_at)


if __name__ == "__main__":
    unittest.main()
