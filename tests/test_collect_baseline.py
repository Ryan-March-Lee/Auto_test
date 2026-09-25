"""阶段 0.1 基线整理脚本测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from collect_baseline import collect_baseline


class CollectBaselineTests(unittest.TestCase):
    def test_collects_only_one_run_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "test_results"
            run = results / "run-001"
            run.mkdir(parents=True)
            (run / "test_plan_snapshot.json").write_text('{"run_id": "run-001", "frequencies": [1]}', encoding="utf-8")
            (run / "run_mapping_snapshot.json").write_text('{"run_id": "run-001", "instruments": {}}', encoding="utf-8")
            (run / "run_metadata.json").write_text('{"run_id": "run-001", "status": "created"}', encoding="utf-8")
            (run / "cable_loss_results.json").write_text('{"run_id": "run-001", "cable_losses": {}}', encoding="utf-8")
            (run / "driver_power_mapping_20260925_120000.json").write_text('{"run_id": "run-001"}', encoding="utf-8")
            (run / "amplifier_measurement_20260925_120001.json").write_text('{"run_id": "run-001"}', encoding="utf-8")
            other = results / "run-002"
            other.mkdir()
            (other / "test_plan_snapshot.json").write_text('{"frequencies": [2]}', encoding="utf-8")
            config = root / "config.json"
            config.write_text('{"test_frequencies": [1]}', encoding="utf-8")

            baseline, manifest = collect_baseline(
                results_dir=results,
                output_dir=root / "baseline",
                run_id="run-001",
            )

            self.assertTrue((baseline / "baseline_manifest.json").exists())
            self.assertTrue((baseline / "results" / "cable_loss_results.json").exists())
            self.assertTrue((baseline / "results" / "driver_power_mapping_20260925_120000.json").exists())
            self.assertTrue((baseline / "results" / "amplifier_measurement_20260925_120001.json").exists())
            self.assertEqual(manifest["measurement_status"]["cable_loss"], "available")
            self.assertEqual(manifest["measurement_status"]["driver_mapping"], "available")
            self.assertEqual(manifest["measurement_status"]["amplifier_measurement"], "available")
            self.assertTrue(manifest["complete"])
            self.assertNotIn("run-002", (baseline / "baseline_manifest.json").read_text(encoding="utf-8"))

            saved_manifest = json.loads((baseline / "baseline_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_manifest["schema_version"], "1.0")

    def test_marks_missing_measurement_without_failing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "test_results"
            results.mkdir()
            (results / "run-003").mkdir()
            baseline, manifest = collect_baseline(
                results_dir=results, output_dir=root / "baseline", run_id="run-003"
            )

            self.assertTrue(baseline.exists())
            self.assertEqual(manifest["measurement_status"]["cable_loss"], "not_found")
            self.assertEqual(manifest["measurement_status"]["driver_mapping"], "not_found")
            self.assertEqual(manifest["measurement_status"]["amplifier_measurement"], "not_found")
            self.assertFalse(manifest["complete"])

    def test_missing_requested_run_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "test_results"
            results.mkdir()
            with self.assertRaises(FileNotFoundError):
                collect_baseline(results_dir=results, output_dir=root / "baseline", run_id="missing")
            self.assertFalse((root / "baseline" / "collected").exists())

    def test_invalid_run_id_is_rejected_before_path_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "test_results"
            results.mkdir()
            with self.assertRaises(ValueError):
                collect_baseline(results_dir=results, output_dir=root / "baseline", run_id="..\\outside")
            self.assertFalse((root / "baseline" / "collected").exists())

    def test_non_object_json_marks_baseline_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "test_results" / "run-005"
            run.mkdir(parents=True)
            for filename in (
                "test_plan_snapshot.json", "run_mapping_snapshot.json", "run_metadata.json",
                "cable_loss_results.json", "driver_power_mapping_1.json", "amplifier_measurement_1.json",
            ):
                payload = json.dumps({"run_id": "run-005"})
                (run / filename).write_text(payload, encoding="utf-8")
            (run / "test_plan_snapshot.json").write_text("[]", encoding="utf-8")

            _, manifest = collect_baseline(
                results_dir=root / "test_results", output_dir=root / "baseline", run_id="run-005"
            )

        self.assertFalse(manifest["complete"])
        self.assertIn("invalid_root:test_plan_snapshot", manifest["integrity_issues"])

    def test_run_id_mismatch_makes_baseline_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "test_results" / "run-004"
            run.mkdir(parents=True)
            for filename in (
                "test_plan_snapshot.json", "run_mapping_snapshot.json", "run_metadata.json",
                "cable_loss_results.json", "driver_power_mapping_1.json", "amplifier_measurement_1.json",
            ):
                run_id = "other-run" if filename == "run_mapping_snapshot.json" else "run-004"
                (run / filename).write_text(json.dumps({"run_id": run_id}), encoding="utf-8")

            _, manifest = collect_baseline(
                results_dir=root / "test_results", output_dir=root / "baseline", run_id="run-004"
            )

        self.assertFalse(manifest["complete"])
        self.assertIn("run_id_mismatch:run_mapping_snapshot", manifest["integrity_issues"])


if __name__ == "__main__":
    unittest.main()
