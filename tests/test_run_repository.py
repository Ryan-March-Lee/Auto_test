"""运行目录仓储测试：创建运行、原子写入、文件登记、校验和运行枚举。"""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from domain.run.models import (
    CABLE_LOSS_INPUT_PATH,
    MANIFEST_FILENAME,
    STAGE_AMPLIFIER,
    STAGE_CABLE_LOSS,
    FileReference,
    RunStatus,
    StageStatus,
    default_manifest,
)
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence.run_directory import file_sha256
from infrastructure.persistence.run_repository import RunRepository


RUN_ID = "20261022T153000Z_a1b2c3d4"
OTHER_ID = "20261022T160000Z_deadbeef"
CREATED_AT = datetime(2026, 10, 22, 15, 30, tzinfo=timezone.utc)
STARTED_AT = datetime(2026, 10, 22, 15, 32, tzinfo=timezone.utc)
FINISHED_AT = datetime(2026, 10, 22, 15, 40, tzinfo=timezone.utc)

CONFIG = {
    "test_plan": {"name": "demo", "frequency_range": [900, 1000]},
    "driver_mode": False,
}


class CreateRunTests(unittest.TestCase):
    def _repository(self, directory) -> RunRepository:
        return RunRepository(Path(directory) / "runs")

    def test_create_run_writes_the_directory_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(directory)
            layout, manifest = repository.create_run(
                CONFIG,
                run_id=RUN_ID,
                software_version="test-runner",
                notes="现场调试",
                product_name="PA-100",
                now=CREATED_AT,
            )

            self.assertEqual(layout.run_directory, repository.root / RUN_ID)
            self.assertTrue(layout.inputs_dir.is_dir())
            self.assertTrue(layout.results_dir.is_dir())
            self.assertTrue(layout.manifest_path.is_file())
            self.assertTrue(layout.config_path.is_file())
            self.assertEqual(json.loads(layout.config_path.read_text(encoding="utf-8")), CONFIG)

            self.assertEqual(manifest.status, RunStatus.CREATED)
            self.assertEqual(manifest.created_at, "2026-10-22T15:30:00Z")
            self.assertEqual(manifest.software_version, "test-runner")
            self.assertEqual(manifest.notes, "现场调试")
            self.assertEqual(manifest.product_name, "PA-100")
            self.assertTrue(all(item.status is StageStatus.PENDING for item in manifest.stages.values()))

            self.assertIsNotNone(manifest.config)
            self.assertEqual(manifest.config.path, "config.json")
            self.assertEqual(manifest.config.sha256, file_sha256(layout.config_path))
            self.assertEqual(manifest.config.size_bytes, layout.config_path.stat().st_size)

    def test_create_run_round_trips_through_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(directory)
            layout, manifest = repository.create_run(CONFIG, run_id=RUN_ID, now=CREATED_AT)
            self.assertEqual(repository.load_manifest(RUN_ID), manifest)
            self.assertEqual(repository.read_config(RUN_ID), CONFIG)
            payload = json.loads(layout.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["config"]["path"], "config.json")
            self.assertEqual(payload["schema_version"], 1)

    def test_create_run_requires_a_config_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(directory)
            with self.assertRaises(ValueError):
                repository.create_run(["not", "a", "mapping"], run_id=RUN_ID, now=CREATED_AT)
            self.assertFalse((repository.root / RUN_ID).exists())

    def test_creating_an_existing_run_never_overwrites_it(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(directory)
            layout, _ = repository.create_run(CONFIG, run_id=RUN_ID, now=CREATED_AT)
            marker = layout.results_dir / "amplifier_measurement.json"
            marker.write_text('{"kept": true}', encoding="utf-8")

            with self.assertRaises(FileExistsError):
                repository.create_run({"other": True}, run_id=RUN_ID, now=CREATED_AT)

            self.assertEqual(json.loads(marker.read_text(encoding="utf-8")), {"kept": True})
            self.assertEqual(repository.read_config(RUN_ID), CONFIG)

    def test_generated_run_id_is_unique_and_path_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(directory)
            first_layout, first_manifest = repository.create_run(CONFIG, now=CREATED_AT)
            second_layout, _ = repository.create_run(CONFIG, now=CREATED_AT)
            self.assertNotEqual(first_layout.run_id, second_layout.run_id)
            self.assertRegex(first_layout.run_id, r"^\d{8}T\d{6}Z_[0-9a-f]{8}$")
            self.assertEqual(first_manifest.run_id, first_layout.run_id)
            self.assertIn(first_layout.run_id, repository.list_run_ids())

    def test_default_software_version_comes_from_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(directory)
            _, manifest = repository.create_run(CONFIG, run_id=RUN_ID, now=CREATED_AT)
            self.assertTrue(manifest.software_version.startswith("python-"))

    def test_failed_config_write_cleans_the_partial_run(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(directory)
            with patch(
                "infrastructure.persistence.run_repository.write_json_atomic",
                side_effect=OSError("磁盘写入失败"),
            ):
                with self.assertRaises(OSError):
                    repository.create_run(CONFIG, run_id=RUN_ID, now=CREATED_AT)
            self.assertFalse((repository.root / RUN_ID).exists())

    def test_concurrent_create_of_same_run_id_builds_one_valid_run(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = self._repository(directory)

            def attempt(config):
                try:
                    repository.create_run(config, run_id=RUN_ID, now=CREATED_AT)
                    return "created"
                except FileExistsError:
                    return "exists"

            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(attempt, [CONFIG] * 8))

            self.assertEqual(results.count("created"), 1)
            self.assertEqual(results.count("exists"), 7)
            self.assertTrue(repository.run_exists(RUN_ID))
            manifest = repository.load_manifest(RUN_ID)
            self.assertEqual(manifest.run_id, RUN_ID)
            self.assertEqual(repository.read_config(RUN_ID), CONFIG)
            payload = json.loads(
                (repository.root / RUN_ID / MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual(payload["run_id"], RUN_ID)


class RunExistenceTests(unittest.TestCase):
    def test_read_operations_require_an_existing_run(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = RunRepository(Path(directory))
            reference = FileReference(path="results/a.json")
            for operation in (
                lambda: repository.load_manifest(RUN_ID),
                lambda: repository.read_config(RUN_ID),
                lambda: repository.register_file(RUN_ID, "results/a.json"),
                lambda: repository.verify_file(RUN_ID, reference),
            ):
                with self.assertRaises(FileNotFoundError):
                    operation()
            self.assertFalse((Path(directory) / RUN_ID).exists())
            self.assertFalse(repository.run_exists(RUN_ID))

    def test_update_manifest_never_creates_a_ghost_run(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = RunRepository(Path(directory))
            manifest = default_manifest(RUN_ID, created_at="2026-10-22T15:30:00Z")
            with self.assertRaises(FileNotFoundError):
                repository.update_manifest(RUN_ID, manifest)
            self.assertFalse((Path(directory) / RUN_ID).exists())

    def test_incomplete_run_is_reported_as_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = RunRepository(Path(directory))
            layout, _ = repository.create_run(CONFIG, run_id=RUN_ID, now=CREATED_AT)
            layout.config_path.unlink()
            self.assertFalse(repository.run_exists(RUN_ID))
            with self.assertRaises(FileNotFoundError):
                repository.load_manifest(RUN_ID)
            with self.assertRaises(FileNotFoundError):
                repository.read_config(RUN_ID)


class ManifestWriteTests(unittest.TestCase):
    def test_update_manifest_rejects_mismatched_run_id(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = RunRepository(Path(directory))
            _, manifest = repository.create_run(CONFIG, run_id=RUN_ID, now=CREATED_AT)
            repository.create_run(CONFIG, run_id=OTHER_ID, now=CREATED_AT)

            with self.assertRaises(ValueError):
                repository.update_manifest(OTHER_ID, manifest)
            with self.assertRaises(TypeError):
                repository.update_manifest(RUN_ID, {"run_id": RUN_ID})
            self.assertEqual(repository.load_manifest(RUN_ID), manifest)

    def test_interrupted_manifest_write_keeps_previous_file(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = RunRepository(Path(directory))
            layout, _ = repository.create_run(CONFIG, run_id=RUN_ID, now=CREATED_AT)
            before = layout.manifest_path.read_text(encoding="utf-8")
            running = repository.load_manifest(RUN_ID).with_status(
                RunStatus.RUNNING, now=STARTED_AT
            )

            with patch(
                "infrastructure.filesystem.atomic_json.os.replace",
                side_effect=OSError("替换失败"),
            ):
                with self.assertRaises(OSError):
                    repository.update_manifest(RUN_ID, running)

            self.assertEqual(layout.manifest_path.read_text(encoding="utf-8"), before)
            self.assertEqual(list(layout.run_directory.glob(".*.tmp")), [])

    def test_failed_register_keeps_file_and_previous_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = RunRepository(Path(directory))
            layout, _ = repository.create_run(CONFIG, run_id=RUN_ID, now=CREATED_AT)
            result = layout.results_dir / "amplifier_measurement.json"
            result.write_text('{"ok": true}', encoding="utf-8")
            before = layout.manifest_path.read_text(encoding="utf-8")

            with patch(
                "infrastructure.persistence.run_repository.save_run_manifest",
                side_effect=OSError("manifest 写入失败"),
            ):
                with self.assertRaises(OSError):
                    repository.register_file(RUN_ID, result)

            self.assertTrue(result.is_file(), "文件已完成，不应被回滚")
            self.assertEqual(layout.manifest_path.read_text(encoding="utf-8"), before)
            self.assertEqual(repository.load_manifest(RUN_ID).outputs, ())

    def test_manifest_records_stage_status_and_file_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = RunRepository(Path(directory))
            layout, _ = repository.create_run(CONFIG, run_id=RUN_ID, now=CREATED_AT)
            cable_loss_file = layout.inputs_dir / "cable_loss.json"
            cable_loss_file.write_text('{"loss_db": 1.5}', encoding="utf-8")
            reference = repository.register_file(
                RUN_ID, cable_loss_file, stage=STAGE_CABLE_LOSS, now=STARTED_AT
            )

            running = (
                repository.load_manifest(RUN_ID)
                .with_status(RunStatus.RUNNING, now=STARTED_AT)
                .transition_stage(STAGE_CABLE_LOSS, StageStatus.RUNNING, now=STARTED_AT)
            )
            repository.update_manifest(RUN_ID, running, now=STARTED_AT)
            completed = repository.load_manifest(RUN_ID).transition_stage(
                STAGE_CABLE_LOSS,
                StageStatus.COMPLETED,
                now=FINISHED_AT,
                result=CABLE_LOSS_INPUT_PATH,
            )
            repository.update_manifest(RUN_ID, completed, now=FINISHED_AT)

            loaded = repository.load_manifest(RUN_ID)
            self.assertEqual(loaded.status, RunStatus.RUNNING)
            self.assertEqual(loaded.stage(STAGE_CABLE_LOSS).status, StageStatus.COMPLETED)
            self.assertEqual(loaded.stage(STAGE_CABLE_LOSS).result, CABLE_LOSS_INPUT_PATH)
            self.assertEqual(loaded.inputs, (reference,))
            self.assertEqual(reference.sha256, file_sha256(cable_loss_file))
            self.assertEqual(loaded.updated_at, "2026-10-22T15:40:00Z")


class RegisterFileTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.repository = RunRepository(Path(self._temporary.name) / "runs")
        self.layout, _ = self.repository.create_run(CONFIG, run_id=RUN_ID, now=CREATED_AT)

    def test_register_file_records_inputs_and_outputs(self):
        cable_loss = self.layout.inputs_dir / "cable_loss.json"
        cable_loss.write_text('{"loss_db": 1.5}', encoding="utf-8")
        result = self.layout.results_dir / "amplifier_measurement.json"
        result.write_text('{"run_id": "%s"}' % RUN_ID, encoding="utf-8")

        input_reference = self.repository.register_file(RUN_ID, cable_loss, now=STARTED_AT)
        output_reference = self.repository.register_file(
            RUN_ID, result, stage=STAGE_AMPLIFIER, now=FINISHED_AT
        )

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.inputs, (input_reference,))
        self.assertEqual(manifest.outputs, (output_reference,))
        self.assertEqual(input_reference.path, "inputs/cable_loss.json")
        self.assertEqual(output_reference.path, "results/amplifier_measurement.json")
        self.assertEqual(input_reference.created_at, "2026-10-22T15:32:00Z")
        self.assertEqual(output_reference.sha256, file_sha256(result))
        self.assertEqual(output_reference.size_bytes, result.stat().st_size)

    def test_register_file_accepts_run_relative_paths(self):
        (self.layout.inputs_dir / "cable_loss.json").write_text("{}", encoding="utf-8")
        (self.layout.results_dir / "amplifier_measurement.json").write_text("{}", encoding="utf-8")

        input_reference = self.repository.register_file(RUN_ID, "inputs/cable_loss.json")
        output_reference = self.repository.register_file(RUN_ID, "results/amplifier_measurement.json")

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.inputs, (input_reference,))
        self.assertEqual(manifest.outputs, (output_reference,))

    def test_register_file_rejects_paths_outside_the_run(self):
        outside = self.repository.root / "outside.json"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("{}", encoding="utf-8")

        with self.assertRaises(ValueError):
            self.repository.register_file(RUN_ID, "../outside.json")
        with self.assertRaises(ValueError):
            self.repository.register_file(RUN_ID, outside)
        with self.assertRaises(ValueError):
            self.repository.register_file(RUN_ID, self.layout.config_path)

    def test_register_file_rejects_missing_files(self):
        with self.assertRaises(FileNotFoundError):
            self.repository.register_file(RUN_ID, "results/missing.json")
        with self.assertRaises(FileNotFoundError):
            self.repository.register_file(RUN_ID, self.layout.results_dir / "missing.json")

    def test_register_file_overwrites_same_path_reference(self):
        result = self.layout.results_dir / "amplifier_measurement.json"
        result.write_text('{"version": 1}', encoding="utf-8")
        self.repository.register_file(RUN_ID, result)
        result.write_text('{"version": 2, "extra": true}', encoding="utf-8")
        reference = self.repository.register_file(RUN_ID, result)

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(len(manifest.outputs), 1)
        self.assertEqual(manifest.outputs[0], reference)
        self.assertEqual(reference.sha256, file_sha256(result))

    def test_write_run_json_refuses_to_overwrite_existing_file(self):
        target = self.layout.results_dir / "amplifier_measurement.json"
        self.repository.write_run_json(
            RUN_ID, "results/amplifier_measurement.json", {"version": 1}
        )

        with self.assertRaises(FileExistsError):
            self.repository.write_run_json(
                RUN_ID, "results/amplifier_measurement.json", {"version": 2}
            )

        self.assertEqual(
            json.loads(target.read_text(encoding="utf-8"))["version"], 1
        )

    def test_write_run_json_allows_explicit_overwrite(self):
        target = self.layout.results_dir / "amplifier_measurement.json"
        self.repository.write_run_json(
            RUN_ID, "results/amplifier_measurement.json", {"version": 1}
        )
        self.repository.write_run_json(
            RUN_ID,
            "results/amplifier_measurement.json",
            {"version": 2},
            overwrite=True,
        )

        self.assertEqual(
            json.loads(target.read_text(encoding="utf-8"))["version"], 2
        )


class VerifyFileTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.repository = RunRepository(Path(self._temporary.name) / "runs")
        self.layout, _ = self.repository.create_run(CONFIG, run_id=RUN_ID, now=CREATED_AT)

    def test_read_config_verifies_recorded_checksum(self):
        self.assertEqual(self.repository.read_config(RUN_ID), CONFIG)

        self.layout.config_path.write_text('{"tampered": true}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.repository.read_config(RUN_ID)
        self.assertEqual(self.repository.read_config(RUN_ID, verify=False), {"tampered": True})

    def test_read_config_rejects_snapshot_removed_after_creation(self):
        self.layout.config_path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.repository.read_config(RUN_ID)

    def test_verify_manifest_files_detects_tampering_and_removal(self):
        cable_loss = self.layout.inputs_dir / "cable_loss.json"
        cable_loss.write_text('{"loss_db": 1.5}', encoding="utf-8")
        result = self.layout.results_dir / "amplifier_measurement.json"
        result.write_text('{"ok": true}', encoding="utf-8")
        self.repository.register_file(RUN_ID, cable_loss)
        self.repository.register_file(RUN_ID, result)

        self.repository.verify_manifest_files(RUN_ID)

        cable_loss.write_text('{"loss_db": 9.9}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.repository.verify_manifest_files(RUN_ID)

        cable_loss.write_text('{"loss_db": 1.5}', encoding="utf-8")
        result.unlink()
        with self.assertRaises(FileNotFoundError):
            self.repository.verify_manifest_files(RUN_ID)

    def test_verify_file_rejects_symlink_escape(self):
        outside = self.repository.root / "outside.json"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("{}", encoding="utf-8")
        link = self.layout.results_dir / "link.json"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("当前环境不支持创建符号链接")
        with self.assertRaises(ValueError):
            self.repository.verify_file(RUN_ID, FileReference(path="results/link.json"))


class DiscardRunTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.repository = RunRepository(Path(self._temporary.name) / "runs")
        self.layout, _ = self.repository.create_run(CONFIG, run_id=RUN_ID, now=CREATED_AT)
        self.other_layout, _ = self.repository.create_run(CONFIG, run_id=OTHER_ID, now=CREATED_AT)
        other_result = self.other_layout.results_dir / "amplifier_measurement.json"
        other_result.write_text('{"kept": true}', encoding="utf-8")
        self.other_reference = self.repository.register_file(OTHER_ID, other_result, now=STARTED_AT)

    def _other_run_digest(self) -> tuple:
        """逐文件记录另一个运行的相对路径与 SHA-256，用于比较删除前后是否变化。"""
        return tuple(
            sorted(
                (
                    path.relative_to(self.other_layout.run_directory).as_posix(),
                    file_sha256(path),
                )
                for path in self.other_layout.run_directory.rglob("*")
                if path.is_file()
            )
        )

    def test_discard_run_removes_only_target_run(self):
        before = self._other_run_digest()

        result = self.repository.discard_run(RUN_ID)

        self.assertEqual(result, RUN_ID)
        self.assertFalse(self.layout.run_directory.exists())
        self.assertFalse(self.repository.run_exists(RUN_ID))
        self.assertNotIn(RUN_ID, self.repository.list_run_ids())
        self.assertTrue(self.repository.run_exists(OTHER_ID))
        self.assertIn(OTHER_ID, self.repository.list_run_ids())
        self.assertEqual(self._other_run_digest(), before)
        self.repository.verify_file(OTHER_ID, self.other_reference)

    def test_discard_run_removes_registered_files_and_manifest(self):
        result_file = self.layout.results_dir / "amplifier_measurement.json"
        result_file.write_text('{"ok": true}', encoding="utf-8")
        self.repository.register_file(RUN_ID, result_file, now=STARTED_AT)

        self.repository.discard_run(RUN_ID)

        self.assertFalse(self.layout.run_directory.exists())

    def test_discard_run_is_idempotent(self):
        self.repository.discard_run(RUN_ID)
        self.assertEqual(self.repository.discard_run(RUN_ID), RUN_ID)
        self.assertFalse(self.layout.run_directory.exists())

        missing_id = "20261022T190000Z_deadbeef"
        self.assertEqual(self.repository.discard_run(missing_id), missing_id)
        self.assertNotIn(missing_id, self.repository.list_run_ids())

    def test_discard_run_tolerates_missing_root(self):
        empty = RunRepository(Path(self._temporary.name) / "missing" / "runs")
        self.assertEqual(empty.discard_run(RUN_ID), RUN_ID)

    def test_discard_run_rejects_invalid_run_id(self):
        for invalid in ("../escape", "..", "a/b", "", "with space", "nested/../.."):
            with self.assertRaises(ValueError):
                self.repository.discard_run(invalid)
        self.assertTrue(self.repository.run_exists(RUN_ID))
        self.assertTrue(self.repository.run_exists(OTHER_ID))
        self.assertFalse((self.repository.root / ".." / "escape").exists())

    def test_discard_run_rejects_plain_file_at_run_path(self):
        before = self._other_run_digest()
        stray_id = "20261022T200000Z_deadbeef"
        stray = self.repository.root / stray_id
        stray.write_text("not a run", encoding="utf-8")

        with self.assertRaises(NotADirectoryError):
            self.repository.discard_run(stray_id)

        self.assertTrue(stray.is_file())
        self.assertEqual(stray.read_text(encoding="utf-8"), "not a run")
        self.assertTrue(self.repository.run_exists(RUN_ID))
        self.assertTrue(self.repository.run_exists(OTHER_ID))
        self.assertEqual(self._other_run_digest(), before)

    def test_discard_run_rejects_symlink_at_run_path(self):
        before = self._other_run_digest()
        linked_id = "20261022T210000Z_deadbeef"
        link = self.repository.root / linked_id
        try:
            link.symlink_to(self.other_layout.run_directory, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("当前环境不支持创建符号链接")

        with self.assertRaises(ValueError):
            self.repository.discard_run(linked_id)

        self.assertTrue(link.is_symlink())
        self.assertTrue(self.repository.run_exists(OTHER_ID))
        self.assertTrue(self.repository.run_exists(RUN_ID))
        self.assertEqual(self._other_run_digest(), before)


class RepositoryRootTests(unittest.TestCase):
    def test_root_defaults_to_result_directory_runs(self):
        paths = PathService(Path("C:/pa-auto-test-root"))
        repository = RunRepository(paths=paths)
        self.assertEqual(repository.root, paths.test_results_dir / "runs")

    def test_list_run_ids_is_empty_for_missing_root(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = RunRepository(Path(directory) / "missing" / "runs")
            self.assertEqual(repository.list_run_ids(), ())

    def test_list_run_ids_filters_invalid_and_broken_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = RunRepository(Path(directory) / "runs")
            repository.create_run(CONFIG, run_id=RUN_ID, now=CREATED_AT)
            root = repository.root

            (root / "cache").mkdir()  # 合法名但缺少 manifest
            (root / "20261022T170000Z_deadbeef").mkdir()
            (root / "20261022T170000Z_deadbeef" / MANIFEST_FILENAME).write_text(
                "{ not json", encoding="utf-8"
            )
            mismatch = root / "20261022T180000Z_deadbeef"
            mismatch.mkdir()
            mismatched_manifest = default_manifest(OTHER_ID, created_at="2026-10-22T15:30:00Z")
            (mismatch / MANIFEST_FILENAME).write_text(
                json.dumps(mismatched_manifest.to_dict()), encoding="utf-8"
            )

            self.assertEqual(repository.list_run_ids(), (RUN_ID,))
            self.assertEqual(
                set(repository.list_run_ids(include_invalid=True)),
                {
                    RUN_ID,
                    "cache",
                    "20261022T170000Z_deadbeef",
                    "20261022T180000Z_deadbeef",
                },
            )


if __name__ == "__main__":
    unittest.main()
