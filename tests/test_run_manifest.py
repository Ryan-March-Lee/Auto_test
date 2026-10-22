"""运行目录数据契约测试：manifest 模型、run_id 唯一性和路径规范化。"""

import json
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from domain.run.models import (
    CABLE_LOSS_INPUT_PATH,
    DEFAULT_STAGE_NAMES,
    GENERATED_RUN_ID_PATTERN,
    MANIFEST_SCHEMA_VERSION,
    STAGE_AMPLIFIER,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
    STAGE_STATUS_TRANSITIONS,
    FileReference,
    RunManifest,
    RunStatus,
    StageRecord,
    StageStatus,
    default_manifest,
    default_stage_result_path,
    generate_run_id,
    is_relative_path,
    normalize_relative_path,
    utc_now_iso,
    validate_generated_run_id,
    validate_run_id,
    validate_transition,
)


class RunIdTests(unittest.TestCase):
    def test_generated_run_id_is_path_safe_and_unique(self):
        identifiers = {generate_run_id() for _ in range(500)}
        self.assertEqual(len(identifiers), 500)
        for value in identifiers:
            self.assertEqual(validate_run_id(value), value)
            self.assertEqual(validate_generated_run_id(value), value)
            self.assertRegex(value, GENERATED_RUN_ID_PATTERN)

    def test_generated_run_id_uses_given_timestamp_and_token(self):
        moment = datetime(2026, 10, 22, 15, 30, tzinfo=timezone.utc)
        self.assertEqual(generate_run_id(moment, token="a1b2c3d4"), "20261022T153000Z_a1b2c3d4")

    def test_timestamp_is_normalised_to_utc(self):
        moment = datetime(2026, 10, 22, 23, 30, tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(generate_run_id(moment, token="a1b2c3d4"), "20261022T153000Z_a1b2c3d4")

    def test_naive_timestamp_and_invalid_token_are_rejected(self):
        with self.assertRaises(ValueError):
            generate_run_id(datetime(2026, 10, 22, 15, 30))
        with self.assertRaises(ValueError):
            generate_run_id(token="A1B2C3D4")
        with self.assertRaises(ValueError):
            generate_run_id(token="abc")

    def test_run_id_rejects_unsafe_values(self):
        for value in ("", ".", "..", "run/id", "run\\id", "C:\\outside", "-abc", "a" * 101, "run..1"):
            with self.assertRaises(ValueError, msg=value):
                validate_run_id(value)
        for value in ("run-1", "20260821-120000-test", "20261022T153000Z_a1b2c3d4"):
            self.assertEqual(validate_run_id(value), value)

    def test_generated_run_id_rejects_legacy_and_short_forms(self):
        self.assertEqual(
            validate_generated_run_id("20261022T153000Z_a1b2c3d4"), "20261022T153000Z_a1b2c3d4"
        )
        for value in ("run-1", "20260821-120000-test", "20261022T153000Z_ABC", "20261022T153000Z_ab"):
            with self.assertRaises(ValueError, msg=value):
                validate_generated_run_id(value)

    def test_utc_now_iso_requires_aware_timestamp(self):
        self.assertEqual(
            utc_now_iso(datetime(2026, 10, 22, 15, 30, tzinfo=timezone.utc)),
            "2026-10-22T15:30:00Z",
        )
        with self.assertRaises(ValueError):
            utc_now_iso(datetime(2026, 10, 22, 15, 30))


class RelativePathTests(unittest.TestCase):
    def test_backslash_is_normalised_to_posix_separator(self):
        self.assertEqual(normalize_relative_path("inputs\\cable_loss.json"), CABLE_LOSS_INPUT_PATH)
        self.assertEqual(normalize_relative_path("  inputs/cable_loss.json  "), CABLE_LOSS_INPUT_PATH)

    def test_absolute_and_traversal_paths_are_rejected(self):
        for value in (
            "/etc/passwd",
            "\\outside.json",
            "C:/outside.json",
            "C:\\outside.json",
            "../outside.json",
            "inputs/../../outside.json",
            "./config.json",
            "inputs//cable_loss.json",
            "inputs/",
            "",
        ):
            with self.assertRaises(ValueError, msg=value):
                normalize_relative_path(value)
        with self.assertRaises(ValueError):
            normalize_relative_path(None)
        with self.assertRaises(ValueError):
            normalize_relative_path("inputs/\x00cable.json")

    def test_is_relative_path_only_accepts_manifest_paths(self):
        self.assertTrue(is_relative_path("results/amplifier_measurement.json"))
        self.assertFalse(is_relative_path("../results/amplifier_measurement.json"))
        self.assertFalse(is_relative_path(123))

    def test_stage_result_paths_follow_the_directory_contract(self):
        self.assertEqual(default_stage_result_path(STAGE_CABLE_LOSS), "inputs/cable_loss.json")
        self.assertEqual(
            default_stage_result_path(STAGE_DRIVER_MAPPING), "inputs/driver_mapping.json"
        )
        self.assertIsNone(default_stage_result_path(STAGE_AMPLIFIER))
        self.assertIsNone(default_stage_result_path("unknown"))


class FileReferenceTests(unittest.TestCase):
    def test_reference_normalises_path_and_omits_empty_fields(self):
        reference = FileReference(path="inputs\\cable_loss.json", sha256="a" * 64, stage="cable_loss")
        self.assertEqual(reference.path, "inputs/cable_loss.json")
        self.assertEqual(reference.to_dict(), {"path": "inputs/cable_loss.json", "sha256": "a" * 64, "stage": "cable_loss"})

    def test_reference_rejects_illegal_path_hash_and_size(self):
        with self.assertRaises(ValueError):
            FileReference(path="../outside.json")
        with self.assertRaises(ValueError):
            FileReference(path="results/a.json", sha256="ABCD")
        with self.assertRaises(ValueError):
            FileReference(path="results/a.json", size_bytes=-1)

    def test_reference_round_trips_through_dict(self):
        reference = FileReference(
            path="results/amplifier_measurement.json",
            sha256="b" * 64,
            size_bytes=128,
            created_at="2026-10-22T15:45:00Z",
            stage="amplifier",
        )
        self.assertEqual(FileReference.from_dict(reference.to_dict()), reference)
        self.assertEqual(FileReference.from_dict({"path": "results/a.json"}).path, "results/a.json")
        with self.assertRaises(ValueError):
            FileReference.from_dict({"sha256": "a" * 64})


class StageRecordTests(unittest.TestCase):
    def test_pending_running_completed_transitions_stamp_times(self):
        started = datetime(2026, 10, 22, 15, 30, tzinfo=timezone.utc)
        finished = datetime(2026, 10, 22, 15, 45, tzinfo=timezone.utc)
        record = StageRecord(name=STAGE_CABLE_LOSS)
        running = record.transition_to(StageStatus.RUNNING, now=started)
        completed = running.transition_to(
            StageStatus.COMPLETED, now=finished, result=CABLE_LOSS_INPUT_PATH
        )
        self.assertEqual(running.status, StageStatus.RUNNING)
        self.assertEqual(running.started_at, "2026-10-22T15:30:00Z")
        self.assertEqual(completed.status, StageStatus.COMPLETED)
        self.assertEqual(completed.ended_at, "2026-10-22T15:45:00Z")
        self.assertEqual(completed.result, CABLE_LOSS_INPUT_PATH)

    def test_invalid_stage_status_and_transitions_are_rejected(self):
        with self.assertRaises(ValueError):
            StageRecord(name=STAGE_CABLE_LOSS, status="finished")
        with self.assertRaises(ValueError):
            StageRecord(name=STAGE_CABLE_LOSS).transition_to(StageStatus.COMPLETED)
        completed = StageRecord(
            name=STAGE_AMPLIFIER,
            status=StageStatus.COMPLETED,
            result="results/amplifier_measurement.json",
        )
        with self.assertRaises(ValueError):
            completed.transition_to(StageStatus.RUNNING)
        with self.assertRaises(ValueError):
            StageRecord(name=STAGE_CABLE_LOSS, result="C:/outside.json")
        with self.assertRaises(ValueError):
            StageRecord(name="")

    def test_completed_requires_result_and_other_states_forbid_it(self):
        with self.assertRaises(ValueError):
            StageRecord(name=STAGE_CABLE_LOSS, status=StageStatus.COMPLETED)
        with self.assertRaises(ValueError):
            StageRecord(name=STAGE_CABLE_LOSS, status=StageStatus.PENDING, result=CABLE_LOSS_INPUT_PATH)
        with self.assertRaises(ValueError):
            StageRecord(
                name=STAGE_CABLE_LOSS,
                status=StageStatus.FAILED,
                result=CABLE_LOSS_INPUT_PATH,
            )
        with self.assertRaises(ValueError):
            StageRecord(name=STAGE_CABLE_LOSS).transition_to(StageStatus.RUNNING).transition_to(
                StageStatus.COMPLETED
            )
        with self.assertRaises(ValueError):
            StageRecord(name=STAGE_CABLE_LOSS).transition_to(StageStatus.RUNNING).transition_to(
                StageStatus.COMPLETED, result=None
            )

    def test_transition_to_completed_records_result_path(self):
        completed = (
            StageRecord(name=STAGE_CABLE_LOSS)
            .transition_to(StageStatus.RUNNING)
            .transition_to(StageStatus.COMPLETED, result=CABLE_LOSS_INPUT_PATH)
        )
        self.assertEqual(completed.result, CABLE_LOSS_INPUT_PATH)
        self.assertIsNotNone(completed.started_at)
        self.assertIsNotNone(completed.ended_at)

    def test_transition_validation_normalises_strings(self):
        self.assertEqual(
            validate_transition(
                "pending",
                "running",
                transitions=STAGE_STATUS_TRANSITIONS,
                label="阶段 cable_loss",
            ),
            StageStatus.RUNNING,
        )
        with self.assertRaises(ValueError):
            validate_transition(
                "pending",
                "completed",
                transitions=STAGE_STATUS_TRANSITIONS,
                label="阶段 cable_loss",
            )
        with self.assertRaises(ValueError):
            validate_transition(
                "finished",
                "running",
                transitions=STAGE_STATUS_TRANSITIONS,
                label="阶段 cable_loss",
            )
        with self.assertRaises(ValueError):
            validate_transition(
                StageStatus.PENDING,
                StageStatus.COMPLETED,
                transitions=STAGE_STATUS_TRANSITIONS,
                label="阶段 cable_loss",
            )

    def test_transition_table_matches_recovery_rules(self):
        self.assertEqual(
            STAGE_STATUS_TRANSITIONS[StageStatus.PENDING],
            frozenset({StageStatus.RUNNING, StageStatus.SKIPPED, StageStatus.CANCELLED}),
        )
        self.assertEqual(STAGE_STATUS_TRANSITIONS[StageStatus.COMPLETED], frozenset())
        with self.assertRaises(ValueError):
            validate_transition(
                StageStatus.PENDING,
                StageStatus.COMPLETED,
                transitions=STAGE_STATUS_TRANSITIONS,
                label="阶段 cable_loss",
            )

    def test_stage_record_round_trips_through_dict(self):
        record = StageRecord(
            name=STAGE_DRIVER_MAPPING,
            status=StageStatus.SKIPPED,
            note="未启用驱动模式",
        )
        payload = record.to_dict()
        self.assertNotIn("result", payload)
        self.assertEqual(StageRecord.from_dict(payload), record)
        with self.assertRaises(ValueError):
            StageRecord.from_dict({"name": STAGE_DRIVER_MAPPING, "status": "unknown"})


class RunManifestTests(unittest.TestCase):
    def _manifest(self) -> RunManifest:
        return default_manifest(
            "20261022T153000Z_a1b2c3d4",
            created_at="2026-10-22T15:30:00Z",
            software_version="test-runner",
        )

    def test_default_manifest_starts_all_stages_pending(self):
        manifest = self._manifest()
        self.assertEqual(manifest.status, RunStatus.CREATED)
        self.assertEqual(tuple(manifest.stages), DEFAULT_STAGE_NAMES)
        self.assertTrue(all(item.status is StageStatus.PENDING for item in manifest.stages.values()))
        self.assertIsNone(manifest.config)
        self.assertEqual(manifest.schema_version, MANIFEST_SCHEMA_VERSION)
        self.assertIsNone(manifest.updated_at)

    def test_manifest_round_trips_through_dict(self):
        manifest = (
            self._manifest()
            .with_status(RunStatus.RUNNING, now=datetime(2026, 10, 22, 15, 31, tzinfo=timezone.utc))
            .transition_stage(
                STAGE_CABLE_LOSS,
                StageStatus.RUNNING,
                now=datetime(2026, 10, 22, 15, 32, tzinfo=timezone.utc),
            )
            .transition_stage(
                STAGE_CABLE_LOSS,
                StageStatus.COMPLETED,
                now=datetime(2026, 10, 22, 15, 40, tzinfo=timezone.utc),
                result=CABLE_LOSS_INPUT_PATH,
            )
            .with_stage(
                StageRecord(
                    name=STAGE_DRIVER_MAPPING,
                    status=StageStatus.SKIPPED,
                    ended_at="2026-10-22T15:41:00Z",
                )
            )
        )
        manifest = replace(
            manifest,
            inputs=(FileReference(path=CABLE_LOSS_INPUT_PATH, sha256="c" * 64, size_bytes=42),),
            outputs=(
                FileReference(path="results/amplifier_measurement.json", stage=STAGE_AMPLIFIER),
            ),
        )
        payload = manifest.to_dict()
        self.assertEqual(json.loads(json.dumps(payload))["run_id"], manifest.run_id)
        self.assertEqual(RunManifest.from_dict(payload), manifest)
        self.assertEqual(RunManifest.from_dict(json.loads(json.dumps(payload))), manifest)

    def test_manifest_rejects_invalid_status_path_and_schema(self):
        base = self._manifest().to_dict()
        with self.assertRaises(ValueError):
            RunManifest.from_dict({**base, "status": "paused"})
        with self.assertRaises(ValueError):
            RunManifest.from_dict(
                {**base, "stages": {STAGE_CABLE_LOSS: {"name": STAGE_CABLE_LOSS, "status": "done"}}}
            )
        with self.assertRaises(ValueError):
            RunManifest.from_dict({**base, "config": {"path": "/abs/config.json"}})
        with self.assertRaises(ValueError):
            RunManifest.from_dict({**base, "inputs": [{"path": "../cable_loss.json"}]})
        with self.assertRaises(ValueError):
            RunManifest.from_dict({**base, "schema_version": 99})
        with self.assertRaises(ValueError):
            RunManifest.from_dict({**base, "run_id": ".."})
        with self.assertRaises(ValueError):
            RunManifest.from_dict({"run_id": base["run_id"]})
        with self.assertRaises(ValueError):
            RunManifest.from_dict(
                {**base, "stages": {STAGE_CABLE_LOSS: {"name": STAGE_AMPLIFIER}}}
            )

    def test_manifest_requires_standard_stages(self):
        base = self._manifest().to_dict()
        with self.assertRaises(ValueError):
            RunManifest.from_dict({**base, "stages": {}})
        stages = dict(base["stages"])
        stages.pop(STAGE_DRIVER_MAPPING)
        with self.assertRaises(ValueError):
            RunManifest.from_dict({**base, "stages": stages})
        with self.assertRaises(ValueError):
            RunManifest(run_id=base["run_id"], created_at=base["created_at"])

    def test_completed_fixed_path_stage_must_use_contract_path(self):
        base = self._manifest().to_dict()
        stages = dict(base["stages"])
        stages[STAGE_CABLE_LOSS] = {
            "name": STAGE_CABLE_LOSS,
            "status": StageStatus.COMPLETED.value,
            "result": "results/cable_loss.json",
        }
        with self.assertRaises(ValueError):
            RunManifest.from_dict({**base, "stages": stages})

    def test_manifest_allows_unknown_extension_stages(self):
        base = self._manifest().to_dict()
        stages = dict(base["stages"])
        stages["custom_stage"] = {"name": "custom_stage", "status": StageStatus.PENDING.value}
        manifest = RunManifest.from_dict({**base, "stages": stages})
        self.assertIn("custom_stage", manifest.stages)

    def test_manifest_rejects_invalid_run_transitions(self):
        manifest = self._manifest()
        with self.assertRaises(ValueError):
            manifest.with_status(RunStatus.COMPLETED)
        running = manifest.with_status(RunStatus.RUNNING, now=datetime(2026, 10, 22, 15, 31, tzinfo=timezone.utc))
        completed = running.with_status(RunStatus.COMPLETED)
        self.assertEqual(completed.status, RunStatus.COMPLETED)
        self.assertTrue(completed.updated_at)
        self.assertNotEqual(completed.updated_at, manifest.updated_at)
        with self.assertRaises(ValueError):
            completed.with_status(RunStatus.RUNNING)

    def test_unknown_manifest_fields_are_ignored_for_forward_compatibility(self):
        payload = {**self._manifest().to_dict(), "future_field": {"anything": 1}}
        manifest = RunManifest.from_dict(payload)
        self.assertEqual(manifest.run_id, self._manifest().run_id)
        self.assertNotIn("future_field", manifest.to_dict())

    def test_missing_stage_lookup_raises(self):
        manifest = self._manifest()
        with self.assertRaises(KeyError):
            manifest.stage("unknown_stage")


if __name__ == "__main__":
    unittest.main()
