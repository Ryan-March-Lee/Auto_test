"""阶段 0.1 基线整理脚本测试。"""

import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from collect_baseline import collect_baseline, collect_batch_baseline


ROOT = Path(__file__).resolve().parents[1]


class BaselineRepositoryHygieneTests(unittest.TestCase):
    """生成型基线数据只留在本地，仓库里只保留小型模板夹具。"""

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def _require_git_checkout(self) -> None:
        if shutil.which("git") is None or not (ROOT / ".git").exists():
            self.skipTest("需要 git 工作区才能校验版本控制状态")

    def test_collected_baseline_artifacts_are_not_tracked(self):
        self._require_git_checkout()
        completed = self._git("ls-files", "--", "baseline/collected")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            [line for line in completed.stdout.splitlines() if line.strip()],
            [],
            "baseline/collected 是生成型运行产物，只能本地保留或归档，不能进入版本控制",
        )

    def test_new_collected_baseline_output_stays_out_of_version_control(self):
        self._require_git_checkout()
        completed = self._git(
            "check-ignore", "--no-index", "-q",
            "baseline/collected/example/baseline_manifest.json",
        )
        self.assertEqual(completed.returncode, 0, ".gitignore 必须忽略 baseline/collected 下的新产物")

    def test_archive_bundle_stays_out_of_version_control(self):
        self._require_git_checkout()
        completed = self._git(
            "check-ignore", "--no-index", "-q",
            "archive/baseline-collected-20260925-203233-586899.zip",
        )
        self.assertEqual(completed.returncode, 0, ".gitignore 必须忽略 archive/ 下的归档包")
        tracked = self._git("ls-files", "--", "archive")
        self.assertEqual(tracked.returncode, 0, tracked.stderr)
        self.assertEqual(
            [line for line in tracked.stdout.splitlines() if line.strip()],
            [],
            "archive/ 是本地/受控归档区，不能进入版本控制",
        )

    def test_retained_baseline_fixtures_are_templates(self):
        for name in ("test_plan.json", "run_mapping.json"):
            payload = json.loads((ROOT / "baseline" / name).read_text(encoding="utf-8"))
            self.assertIs(payload.get("template"), True, f"baseline/{name} 必须保持脱敏模板")

    def test_retained_baseline_fixtures_carry_no_site_identifiers(self):
        plan_text = (ROOT / "baseline" / "test_plan.json").read_text(encoding="utf-8")
        mapping_text = (ROOT / "baseline" / "run_mapping.json").read_text(encoding="utf-8")
        plan = json.loads(plan_text)
        mapping = json.loads(mapping_text)

        for name, text in (("test_plan.json", plan_text), ("run_mapping.json", mapping_text)):
            self.assertNotRegex(text, r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", f"baseline/{name} 不得含 IP 地址")
            self.assertNotIn("tcpip", text.lower(), f"baseline/{name} 不得含 VISA 资源地址")

        self.assertIsNone(mapping["run_id"])
        self.assertIsNone(mapping["run_datetime"])
        self.assertIsNone(mapping["operator"])
        for role, instrument in mapping["instruments"].items():
            if role == "power_supplies":
                self.assertEqual({}, instrument, "模板电源集合必须为空")
                continue
            self.assertIsNone(instrument["visa_address"], f"模板 instruments.{role} 地址必须为空")
        self.assertEqual([], mapping["driver_mode"]["power_plan"]["power_sequence"])
        self.assertEqual([], mapping["dut_power_plan"]["power_sequence"])
        for kind, path in mapping["result_files"].items():
            self.assertIsNone(path, f"模板 result_files.{kind} 必须为空")

        self.assertEqual([], plan["frequencies"]["values"])
        for key in ("start_power", "stop_power", "step"):
            self.assertIsNone(plan["signal_source"][key], f"模板 signal_source.{key} 必须为空")
        self.assertIsNone(plan["attenuator"]["value"])
        self.assertIsNone(plan["dut"]["max_input_power"])
        self.assertIsNone(plan["dut"]["drain_channel"])
        self.assertEqual([], plan["dut"]["channel_settings"])
        self.assertEqual([], plan["driver_mode"]["channel_settings"])

        # 模板是新 schema 的样例：不能重新出现 1.0 的通道角色/枚举结构。
        for name, payload in (("test_plan.json", plan), ("run_mapping.json", mapping)):
            text = json.dumps(payload, ensure_ascii=False)
            for legacy in ('"power_roles"', '"power_channels"', '"dut_power_channels"', '"role"'):
                self.assertNotIn(legacy, text, f"baseline/{name} 不得写 1.0 旧字段 {legacy}")

    def test_collected_archive_checksum_records_are_well_formed(self):
        entries = self._archive_checksum_entries()
        self.assertEqual(len(entries), 8, "归档校验清单必须完整覆盖样例的 8 个文件；新增归档样例时同步更新本门禁")
        for digest, relative_path in entries:
            self.assertRegex(digest, r"^[0-9a-f]{64}$", f"无效的 SHA-256: {relative_path}")
            self.assertTrue(
                relative_path.startswith("collected/20260925-203233-586899/"),
                f"清单只能描述已归档样例: {relative_path}",
            )

    def test_collected_archive_checksums_match_local_sample(self):
        entries = self._archive_checksum_entries()
        local_sample = ROOT / "baseline" / "collected" / "20260925-203233-586899"
        if not local_sample.is_dir():
            self.skipTest("本地样例副本已移交归档，跳过逐文件哈希比对")
        for digest, relative_path in entries:
            artifact = ROOT / "baseline" / relative_path
            self.assertTrue(artifact.is_file(), f"清单中的文件缺失: {relative_path}")
            actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
            self.assertEqual(actual, digest, f"归档校验清单与本地文件的哈希不一致: {relative_path}")

    def _archive_checksum_entries(self) -> list[tuple[str, str]]:
        text = (ROOT / "baseline" / "collected_archive.sha256").read_text(encoding="utf-8")
        entries = []
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            digest, _, relative_path = line.partition("  ")
            entries.append((digest.strip(), relative_path.strip()))
        return entries


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

    def test_collects_cross_run_batch_and_preserves_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "test_results"
            report_dir = root / "reports"
            report_dir.mkdir(parents=True)
            for run_id in ("cable-run", "driver-run", "amp-run"):
                run = results / run_id
                run.mkdir(parents=True)
                for filename in ("test_plan_snapshot.json", "run_mapping_snapshot.json", "run_metadata.json"):
                    (run / filename).write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
            (results / "cable-run" / "cable_loss_results.json").write_text(
                json.dumps({"run_id": "cable-run", "result_type": "cable_loss"}), encoding="utf-8"
            )
            (results / "driver-run" / "driver_power_mapping_1.json").write_text(
                json.dumps({"run_id": "driver-run", "result_type": "driver_power_mapping"}), encoding="utf-8"
            )
            (results / "amp-run" / "amplifier_measurement_1.json").write_text(
                json.dumps({"run_id": "amp-run", "result_type": "amplifier_measurement"}), encoding="utf-8"
            )
            (report_dir / "test_report.html").write_text("<html>", encoding="utf-8")

            baseline, manifest = collect_batch_baseline(
                results_dir=results,
                output_dir=root / "baseline",
                cable_loss_run_id="cable-run",
                driver_mapping_run_id="driver-run",
                amplifier_run_id="amp-run",
                report_dir=report_dir,
            )

            self.assertTrue(manifest["complete"])
            self.assertEqual(
                manifest["source"]["batch_run_ids"],
                {"cable_loss": "cable-run", "driver_mapping": "driver-run", "amplifier_measurement": "amp-run"},
            )
            self.assertTrue((baseline / "results" / "cable_loss_results.json").exists())
            self.assertTrue((baseline / "results" / "driver_power_mapping_1.json").exists())
            self.assertTrue((baseline / "results" / "amplifier_measurement_1.json").exists())
            self.assertTrue((baseline / "reports" / "test_report.html").exists())


if __name__ == "__main__":
    unittest.main()
