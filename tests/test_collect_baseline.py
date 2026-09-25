"""阶段 0.1 基线整理脚本测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from collect_baseline import collect_baseline


class CollectBaselineTests(unittest.TestCase):
    def test_collects_latest_artifacts_and_writes_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "test_results"
            run = results / "run-001"
            run.mkdir(parents=True)
            (run / "test_plan_snapshot.json").write_text('{"frequencies": [1]}', encoding="utf-8")
            (run / "run_mapping_snapshot.json").write_text('{"instruments": {}}', encoding="utf-8")
            (run / "run_metadata.json").write_text('{"status": "created"}', encoding="utf-8")
            (run / "cable_loss_results.json").write_text('{"cable_losses": {}}', encoding="utf-8")
            (results / "driver_power_mapping_20260925_120000.json").write_text("{}", encoding="utf-8")
            (results / "amplifier_measurement_20260925_120001.json").write_text("{}", encoding="utf-8")
            config = root / "config.json"
            config.write_text('{"test_frequencies": [1]}', encoding="utf-8")

            baseline, manifest = collect_baseline(
                results_dir=results,
                output_dir=root / "baseline",
            )

            self.assertTrue((baseline / "baseline_manifest.json").exists())
            self.assertTrue((baseline / "results" / "cable_loss_results.json").exists())
            self.assertTrue((baseline / "results" / "driver_power_mapping_20260925_120000.json").exists())
            self.assertTrue((baseline / "results" / "amplifier_measurement_20260925_120001.json").exists())
            self.assertEqual(manifest["measurement_status"]["cable_loss"], "available")
            self.assertEqual(manifest["measurement_status"]["driver_mapping"], "available")
            self.assertEqual(manifest["measurement_status"]["amplifier_measurement"], "available")

            saved_manifest = json.loads((baseline / "baseline_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_manifest["schema_version"], "1.0")

    def test_marks_missing_measurement_without_failing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "test_results"
            results.mkdir()
            baseline, manifest = collect_baseline(results_dir=results, output_dir=root / "baseline")

            self.assertTrue(baseline.exists())
            self.assertEqual(manifest["measurement_status"]["cable_loss"], "not_found")
            self.assertEqual(manifest["measurement_status"]["driver_mapping"], "not_found")
            self.assertEqual(manifest["measurement_status"]["amplifier_measurement"], "not_found")


if __name__ == "__main__":
    unittest.main()
