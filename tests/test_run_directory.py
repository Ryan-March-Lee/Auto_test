"""运行目录仓储级测试：空运行创建、manifest 原子写读和路径规则。"""

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from domain.run.models import (
    DEFAULT_STAGE_NAMES,
    MANIFEST_FILENAME,
    RunStatus,
    StageStatus,
    default_manifest,
)
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence.run_directory import (
    RunDirectoryLayout,
    create_empty_run,
    default_runs_root,
    file_reference,
    file_sha256,
    file_sha256_and_size,
    load_run_manifest,
    load_run_manifest_from_directory,
    save_run_manifest,
)


RUN_ID = "20261022T153000Z_a1b2c3d4"
CREATED_AT = datetime(2026, 10, 22, 15, 30, tzinfo=timezone.utc)


class EmptyRunTests(unittest.TestCase):
    def test_empty_run_builds_the_directory_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "runs"
            layout, manifest = create_empty_run(
                root, run_id=RUN_ID, software_version="test-runner", now=CREATED_AT
            )

            self.assertEqual(layout.run_directory, root / RUN_ID)
            self.assertTrue(layout.inputs_dir.is_dir())
            self.assertTrue(layout.results_dir.is_dir())
            self.assertTrue(layout.manifest_path.is_file())
            self.assertFalse(layout.config_path.exists(), "空运行不应写入配置快照")
            self.assertEqual(list(layout.inputs_dir.iterdir()), [])
            self.assertEqual(list(layout.results_dir.iterdir()), [])

            self.assertEqual(manifest.run_id, RUN_ID)
            self.assertEqual(manifest.status, RunStatus.CREATED)
            self.assertEqual(manifest.created_at, "2026-10-22T15:30:00Z")
            self.assertEqual(manifest.software_version, "test-runner")
            self.assertEqual(tuple(manifest.stages), DEFAULT_STAGE_NAMES)
            self.assertTrue(
                all(item.status is StageStatus.PENDING for item in manifest.stages.values())
            )

    def test_empty_run_status_is_fixed_to_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest = create_empty_run(root, run_id=RUN_ID, now=CREATED_AT)
            self.assertEqual(manifest.status, RunStatus.CREATED)
            with self.assertRaises(TypeError):
                create_empty_run(root, run_id=RUN_ID, status=RunStatus.COMPLETED)

    def test_default_software_version_comes_from_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            _, manifest = create_empty_run(Path(directory), run_id=RUN_ID, now=CREATED_AT)
            self.assertTrue(manifest.software_version.startswith("python-")), manifest.software_version

    def test_manifest_round_trips_through_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            layout, manifest = create_empty_run(Path(directory), run_id=RUN_ID, now=CREATED_AT)
            loaded = load_run_manifest(layout.manifest_path)
            self.assertEqual(loaded, manifest)
            self.assertEqual(
                load_run_manifest_from_directory(layout.run_directory).to_dict(), manifest.to_dict()
            )
            payload = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertIn("cable_loss", payload["stages"])

    def test_creating_an_existing_run_never_overwrites_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout, _ = create_empty_run(root, run_id=RUN_ID, now=CREATED_AT)
            marker = layout.run_directory / "results" / "amplifier_measurement.json"
            marker.write_text('{"kept": true}', encoding="utf-8")

            with self.assertRaises(FileExistsError):
                create_empty_run(root, run_id=RUN_ID, now=CREATED_AT)

            self.assertEqual(json.loads(marker.read_text(encoding="utf-8")), {"kept": True})
            self.assertTrue(layout.manifest_path.is_file())

    def test_failed_manifest_write_cleans_the_partial_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch(
                "infrastructure.persistence.run_directory.write_json_atomic",
                side_effect=OSError("磁盘写入失败"),
            ):
                with self.assertRaises(OSError):
                    create_empty_run(root, run_id=RUN_ID, now=CREATED_AT)
            self.assertFalse((root / RUN_ID).exists())

    def test_atomic_write_leaves_no_partial_file_or_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "manifest.json"
            with patch(
                "infrastructure.filesystem.atomic_json.os.replace",
                side_effect=OSError("替换失败"),
            ):
                with self.assertRaises(OSError):
                    save_run_manifest(
                        destination,
                        default_manifest(
                            RUN_ID, created_at="2026-10-22T15:30:00Z", software_version="test-runner"
                        ),
                    )
            self.assertFalse(destination.exists())
            self.assertEqual(list(root.glob(".*.tmp")), [])

    def test_generated_run_id_is_used_when_omitted(self):
        with tempfile.TemporaryDirectory() as directory:
            layout, manifest = create_empty_run(Path(directory), now=CREATED_AT)
            self.assertRegex(layout.run_id, r"^\d{8}T\d{6}Z_[0-9a-f]{8}$")
            self.assertEqual(manifest.run_id, layout.run_id)


class RunPathTests(unittest.TestCase):
    def test_relative_path_accepts_only_run_members(self):
        with tempfile.TemporaryDirectory() as directory:
            layout, _ = create_empty_run(Path(directory), run_id=RUN_ID, now=CREATED_AT)
            self.assertEqual(
                layout.relative_path(layout.results_dir / "amplifier_measurement.json"),
                "results/amplifier_measurement.json",
            )
            self.assertEqual(
                layout.relative_path("inputs\\cable_loss.json"), "inputs/cable_loss.json"
            )
            self.assertEqual(
                layout.relative_path(layout.inputs_dir / ".." / "results" / "a.json"),
                "results/a.json",
            )
            with self.assertRaises(ValueError):
                layout.relative_path(Path(directory) / "outside.json")
            with self.assertRaises(ValueError):
                layout.relative_path("../outside.json")
            with self.assertRaises(ValueError):
                layout.relative_path(layout.run_directory)

    def test_relative_path_rejects_symlink_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layout, _ = create_empty_run(root / "runs", run_id=RUN_ID, now=CREATED_AT)
            outside = root / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            link = layout.results_dir / "link.json"
            try:
                link.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("当前环境不支持创建符号链接")
            with self.assertRaises(ValueError):
                layout.relative_path(link)

    def test_file_reference_registers_hash_and_size(self):
        with tempfile.TemporaryDirectory() as directory:
            layout, _ = create_empty_run(Path(directory), run_id=RUN_ID, now=CREATED_AT)
            target = layout.results_dir / "amplifier_measurement.json"
            target.write_text('{"run_id": "%s"}' % RUN_ID, encoding="utf-8")

            reference = file_reference(target, layout=layout, stage="amplifier")
            self.assertEqual(reference.path, "results/amplifier_measurement.json")
            self.assertEqual(reference.stage, "amplifier")
            self.assertEqual(reference.size_bytes, target.stat().st_size)
            self.assertEqual(reference.sha256, hashlib.sha256(target.read_bytes()).hexdigest())
            self.assertEqual(file_sha256(target), reference.sha256)
            digest, size = file_sha256_and_size(target)
            self.assertEqual((digest, size), (reference.sha256, reference.size_bytes))

            outside = Path(directory) / "outside.json"
            outside.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                file_reference(outside, layout=layout)

    def test_file_reference_requires_existing_file_and_consistent_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            layout, _ = create_empty_run(Path(directory), run_id=RUN_ID, now=CREATED_AT)
            missing = layout.results_dir / "missing.json"
            with self.assertRaises(FileNotFoundError):
                file_reference(missing, layout=layout)
            with self.assertRaises(FileNotFoundError):
                file_reference(layout.results_dir, layout=layout)

            target = layout.results_dir / "amplifier_measurement.json"
            target.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                file_reference(target, layout=layout, sha256="f" * 64)
            with self.assertRaises(ValueError):
                file_reference(target, layout=layout, size_bytes=999)

    def test_layout_rejects_unsafe_run_id(self):
        for value in ("..", "run/id", "C:\\outside"):
            with self.assertRaises(ValueError, msg=value):
                RunDirectoryLayout(Path("C:/runs"), value)

    def test_default_runs_root_follows_injected_path_service(self):
        paths = PathService(Path("C:/pa-auto-test-root"))
        self.assertEqual(default_runs_root(paths=paths), paths.test_results_dir / "runs")
        self.assertEqual(default_runs_root().name, "runs")

    def test_layout_manifest_name_matches_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            layout, _ = create_empty_run(Path(directory), run_id=RUN_ID, now=CREATED_AT)
            self.assertEqual(layout.manifest_path.name, MANIFEST_FILENAME)
            self.assertEqual(layout.manifest_path.parent, layout.run_directory)


if __name__ == "__main__":
    unittest.main()
