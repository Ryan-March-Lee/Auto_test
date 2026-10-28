"""步骤 2：归档状态与产物契约测试。

覆盖：

- manifest v2 的 ``artifacts`` 记录、v1 兼容读取与状态转换规则；
- 运行目录产物路径策略（只允许 ``artifacts/`` 下的相对路径）；
- 归档结果 DTO 对完整成功、部分完成、产物失败、不适用、取消与异常的组合同规则；
- DTO 不依赖 Qt/文件系统，也不携带页面文案。
"""

import ast
import json
import unittest
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path

from application.dto.archival import (
    ArchivalOutcome,
    ArchivalStatus,
    ArchivalTestStatus,
    ArchivedArtifact,
)
from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    CABLE_LOSS_INPUT_PATH,
    DEFAULT_STAGE_NAMES,
    DRIVER_MAPPING_INPUT_PATH,
    MANIFEST_SCHEMA_VERSION,
    STAGE_AMPLIFIER,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
    ArtifactKind,
    ArtifactRecord,
    ArtifactStatus,
    RunManifest,
    StageRecord,
    default_artifact_path,
    default_manifest,
    normalize_artifact_path,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "20261028T010203Z_abcdef12"
NOW = datetime(2026, 10, 28, 1, 2, 3, tzinfo=timezone.utc)
CSV_PATH = "artifacts/amplifier.csv"
REPORT_PATH = "artifacts/amplifier_report.html"
SHA = "a" * 64


def _completed_artifact(kind=ArtifactKind.CSV, path=CSV_PATH) -> ArtifactRecord:
    return ArtifactRecord(
        kind=kind,
        status=ArtifactStatus.COMPLETED,
        path=path,
        source_result=AMPLIFIER_RESULT_PATH,
        created_at="2026-10-28T01:02:03Z",
        sha256=SHA,
        size_bytes=12,
    )


def _outcome(**overrides) -> ArchivalOutcome:
    values = {
        "run_id": RUN_ID,
        "stage": STAGE_AMPLIFIER,
        "test_status": ArchivalTestStatus.COMPLETED,
        "formal_result_path": AMPLIFIER_RESULT_PATH,
        "artifacts": (
            ArchivedArtifact.completed(ArtifactKind.CSV, CSV_PATH),
            ArchivedArtifact.completed(ArtifactKind.REPORT, REPORT_PATH),
        ),
    }
    values.update(overrides)
    return ArchivalOutcome(**values)


class ArtifactPathPolicyTests(unittest.TestCase):
    def test_artifacts_live_under_the_controlled_directory(self):
        self.assertEqual(normalize_artifact_path("artifacts\\a.csv"), "artifacts/a.csv")
        self.assertEqual(
            normalize_artifact_path("  artifacts/report/index.html  "),
            "artifacts/report/index.html",
        )

    def test_paths_outside_the_artifact_directory_are_rejected(self):
        for value in (
            "/abs/artifacts/a.csv",
            "C:/artifacts/a.csv",
            "../artifacts/a.csv",
            "results/a.csv",
            "inputs/a.csv",
            "artifacts/../results/a.csv",
            "artifacts/",
            "",
        ):
            with self.subTest(path=value):
                with self.assertRaises(ValueError):
                    normalize_artifact_path(value)

    def test_default_paths_are_centralised_and_stable(self):
        self.assertEqual(
            default_artifact_path(STAGE_AMPLIFIER, ArtifactKind.CSV), CSV_PATH
        )
        self.assertEqual(
            default_artifact_path(STAGE_AMPLIFIER, "report"), REPORT_PATH
        )
        self.assertEqual(
            default_artifact_path(STAGE_CABLE_LOSS, ArtifactKind.CSV),
            "artifacts/cable_loss.csv",
        )
        with self.assertRaises(ValueError):
            default_artifact_path(STAGE_AMPLIFIER, "plot")
        with self.assertRaises(ValueError):
            default_artifact_path("unknown", ArtifactKind.CSV)


class ArtifactRecordTests(unittest.TestCase):
    def test_completed_record_requires_path_hash_and_size(self):
        record = _completed_artifact()
        self.assertEqual(record.to_dict()["path"], CSV_PATH)
        self.assertEqual(ArtifactRecord.from_dict(record.to_dict()), record)
        for field_name in ("path", "sha256", "size_bytes"):
            values = {
                "kind": ArtifactKind.CSV,
                "status": ArtifactStatus.COMPLETED,
                "path": CSV_PATH,
                "source_result": AMPLIFIER_RESULT_PATH,
                "sha256": SHA,
                "size_bytes": 12,
            }
            values[field_name] = None
            with self.subTest(missing=field_name):
                with self.assertRaises(ValueError):
                    ArtifactRecord(**values)

    def test_non_completed_records_cannot_carry_artifacts(self):
        for status in (
            ArtifactStatus.PENDING,
            ArtifactStatus.NOT_APPLICABLE,
        ):
            with self.subTest(status=status):
                for extra in (
                    {"path": CSV_PATH},
                    {"sha256": SHA},
                    {"size_bytes": 1},
                    {"error": "写失败"},
                ):
                    with self.assertRaises(ValueError):
                        ArtifactRecord(
                            kind=ArtifactKind.CSV,
                            status=status,
                            source_result=AMPLIFIER_RESULT_PATH,
                            **extra,
                        )

    def test_failed_record_requires_reason_and_no_path(self):
        record = ArtifactRecord(
            kind=ArtifactKind.REPORT,
            status=ArtifactStatus.FAILED,
            source_result=AMPLIFIER_RESULT_PATH,
            error="报告模板渲染失败",
        )
        self.assertEqual(ArtifactRecord.from_dict(record.to_dict()), record)
        with self.assertRaises(ValueError):
            ArtifactRecord(
                kind=ArtifactKind.REPORT,
                status=ArtifactStatus.FAILED,
                source_result=AMPLIFIER_RESULT_PATH,
            )
        with self.assertRaises(ValueError):
            ArtifactRecord(
                kind=ArtifactKind.REPORT,
                status=ArtifactStatus.FAILED,
                path=REPORT_PATH,
                source_result=AMPLIFIER_RESULT_PATH,
                error="失败",
            )

    def test_unknown_kind_status_and_bad_source_are_rejected(self):
        with self.assertRaises(ValueError):
            ArtifactRecord(kind="html", status=ArtifactStatus.PENDING)
        with self.assertRaises(ValueError):
            ArtifactRecord(kind=ArtifactKind.CSV, status="done")
        for source in ("C:/outside.json", "../results/a.json", "config.json"):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    ArtifactRecord(
                        kind=ArtifactKind.CSV,
                        status=ArtifactStatus.PENDING,
                        source_result=source,
                    )

    def test_deliverable_states_require_the_source_result(self):
        for status in (ArtifactStatus.PENDING, ArtifactStatus.FAILED):
            with self.subTest(status=status.value):
                with self.assertRaises(ValueError):
                    ArtifactRecord(
                        kind=ArtifactKind.CSV,
                        status=status,
                        error="磁盘写入失败",
                    )
        with self.assertRaises(ValueError):
            ArtifactRecord(
                kind=ArtifactKind.CSV,
                status=ArtifactStatus.COMPLETED,
                path=CSV_PATH,
                sha256=SHA,
                size_bytes=12,
            )
        not_applicable = ArtifactRecord(
            kind=ArtifactKind.CSV, status=ArtifactStatus.NOT_APPLICABLE
        )
        self.assertIsNone(not_applicable.source_result)

    def test_status_transitions_allow_retry_but_not_silent_rewrite(self):
        pending = ArtifactRecord(
            kind=ArtifactKind.CSV,
            status=ArtifactStatus.PENDING,
            source_result=AMPLIFIER_RESULT_PATH,
        )
        completed = pending.transition_to(
            ArtifactStatus.COMPLETED,
            path=CSV_PATH,
            sha256=SHA,
            size_bytes=12,
            now=NOW,
        )
        self.assertEqual(completed.status, ArtifactStatus.COMPLETED)
        self.assertEqual(completed.created_at, "2026-10-28T01:02:03Z")

        failed = pending.transition_to(ArtifactStatus.FAILED, error="磁盘写入失败")
        self.assertEqual(failed.status, ArtifactStatus.FAILED)
        self.assertIsNone(failed.path)
        retried = failed.transition_to(ArtifactStatus.PENDING)
        self.assertEqual(retried.status, ArtifactStatus.PENDING)

        with self.assertRaises(ValueError):
            completed.transition_to(ArtifactStatus.PENDING)
        with self.assertRaises(ValueError):
            ArtifactRecord(
                kind=ArtifactKind.CSV,
                status=ArtifactStatus.NOT_APPLICABLE,
            ).transition_to(ArtifactStatus.COMPLETED)
        with self.assertRaises(ValueError):
            pending.transition_to(ArtifactStatus.COMPLETED, path=CSV_PATH)


class ManifestArtifactContractTests(unittest.TestCase):
    def _manifest(self) -> RunManifest:
        return default_manifest(
            RUN_ID, created_at="2026-10-28T01:02:03Z", software_version="test-runner"
        )

    def test_default_manifest_has_no_artifacts(self):
        manifest = self._manifest()
        self.assertEqual(manifest.artifacts, ())
        self.assertEqual(manifest.to_dict()["artifacts"], [])
        self.assertEqual(manifest.schema_version, MANIFEST_SCHEMA_VERSION)

    def test_v1_manifest_without_artifacts_stays_readable_and_is_upgraded(self):
        payload = self._manifest().to_dict()
        payload["schema_version"] = 1
        payload.pop("artifacts")

        manifest = RunManifest.from_dict(json.loads(json.dumps(payload)))

        self.assertEqual(manifest.artifacts, ())
        self.assertEqual(manifest.schema_version, MANIFEST_SCHEMA_VERSION)
        self.assertEqual(tuple(manifest.stages), DEFAULT_STAGE_NAMES)

    def test_v1_manifest_cannot_store_artifacts(self):
        payload = self._manifest().to_dict()
        payload["schema_version"] = 1
        payload["artifacts"] = [_completed_artifact().to_dict()]
        with self.assertRaises(ValueError):
            RunManifest.from_dict(payload)

        v1 = RunManifest(
            run_id=RUN_ID,
            created_at="2026-10-28T01:02:03Z",
            schema_version=1,
            stages={name: StageRecord(name=name) for name in DEFAULT_STAGE_NAMES},
        )
        with self.assertRaises(ValueError):
            RunManifest(
                run_id=v1.run_id,
                created_at=v1.created_at,
                schema_version=1,
                stages=v1.stages,
                artifacts=(_completed_artifact(),),
            )
        upgraded = v1.with_artifact(_completed_artifact(), now=NOW)
        self.assertEqual(upgraded.schema_version, MANIFEST_SCHEMA_VERSION)

    def test_v1_manifest_rejects_an_empty_artifacts_field(self):
        payload = self._manifest().to_dict()
        payload["schema_version"] = 1
        payload["artifacts"] = []
        with self.assertRaises(ValueError):
            RunManifest.from_dict(payload)

    def test_v1_manifest_serialisation_omits_artifacts(self):
        v1 = RunManifest(
            run_id=RUN_ID,
            created_at="2026-10-28T01:02:03Z",
            schema_version=1,
            stages={name: StageRecord(name=name) for name in DEFAULT_STAGE_NAMES},
        )
        payload = v1.to_dict()
        self.assertNotIn("artifacts", payload)
        self.assertEqual(
            RunManifest.from_dict(payload).schema_version, MANIFEST_SCHEMA_VERSION
        )

    def test_manifest_round_trips_with_artifacts(self):
        manifest = self._manifest().with_artifact(_completed_artifact(), now=NOW)
        payload = json.loads(json.dumps(manifest.to_dict()))
        loaded = RunManifest.from_dict(payload)

        self.assertEqual(loaded, manifest)
        self.assertEqual(
            loaded.find_artifact(ArtifactKind.CSV, source_result=AMPLIFIER_RESULT_PATH),
            _completed_artifact(),
        )
        self.assertIsNone(
            loaded.find_artifact(ArtifactKind.REPORT, source_result=AMPLIFIER_RESULT_PATH)
        )

    def test_with_artifact_keeps_one_record_per_kind_and_source(self):
        manifest = self._manifest()
        first = manifest.with_artifact(
            ArtifactRecord(
                kind=ArtifactKind.CSV,
                status=ArtifactStatus.PENDING,
                source_result=AMPLIFIER_RESULT_PATH,
            ),
            now=NOW,
        )
        second = first.with_artifact(_completed_artifact(), now=NOW)
        third = second.with_artifact(
            ArtifactRecord(
                kind=ArtifactKind.CSV,
                status=ArtifactStatus.PENDING,
                source_result=CABLE_LOSS_INPUT_PATH,
            ),
            now=NOW,
        )

        self.assertEqual(len(first.artifacts), 1)
        self.assertEqual(len(second.artifacts), 1)
        self.assertEqual(
            second.find_artifact(
                ArtifactKind.CSV, source_result=AMPLIFIER_RESULT_PATH
            ).status,
            ArtifactStatus.COMPLETED,
        )
        self.assertEqual(len(third.artifacts), 2)

    def test_repeated_identical_artifact_is_an_idempotent_no_op(self):
        manifest = self._manifest().with_artifact(_completed_artifact(), now=NOW)
        again = manifest.with_artifact(_completed_artifact(), now=NOW)
        self.assertIs(again, manifest)
        self.assertEqual(len(again.artifacts), 1)

    def test_completed_artifacts_cannot_be_rewritten(self):
        manifest = self._manifest().with_artifact(_completed_artifact(), now=NOW)
        for regress in (
            ArtifactRecord(
                kind=ArtifactKind.CSV,
                status=ArtifactStatus.PENDING,
                source_result=AMPLIFIER_RESULT_PATH,
            ),
            ArtifactRecord(
                kind=ArtifactKind.CSV,
                status=ArtifactStatus.FAILED,
                source_result=AMPLIFIER_RESULT_PATH,
                error="重新登记为失败",
            ),
        ):
            with self.subTest(status=regress.status.value):
                with self.assertRaises(ValueError):
                    manifest.with_artifact(regress, now=NOW)
        record = manifest.find_artifact(
            ArtifactKind.CSV, source_result=AMPLIFIER_RESULT_PATH
        )
        self.assertEqual(record.status, ArtifactStatus.COMPLETED)
        self.assertEqual(record.path, CSV_PATH)

    def test_not_applicable_artifacts_are_terminal(self):
        manifest = self._manifest().with_artifact(
            ArtifactRecord(
                kind=ArtifactKind.REPORT,
                status=ArtifactStatus.NOT_APPLICABLE,
                source_result=AMPLIFIER_RESULT_PATH,
            ),
            now=NOW,
        )
        with self.assertRaises(ValueError):
            manifest.with_artifact(
                ArtifactRecord(
                    kind=ArtifactKind.REPORT,
                    status=ArtifactStatus.COMPLETED,
                    path=REPORT_PATH,
                    source_result=AMPLIFIER_RESULT_PATH,
                    sha256=SHA,
                    size_bytes=12,
                ),
                now=NOW,
            )

    def test_failed_artifact_only_returns_to_pending_through_retry(self):
        failed = ArtifactRecord(
            kind=ArtifactKind.CSV,
            status=ArtifactStatus.FAILED,
            source_result=AMPLIFIER_RESULT_PATH,
            error="磁盘写入失败",
        )
        manifest = self._manifest().with_artifact(failed, now=NOW)
        with self.assertRaises(ValueError):
            manifest.with_artifact(
                ArtifactRecord(
                    kind=ArtifactKind.CSV,
                    status=ArtifactStatus.PENDING,
                    source_result=AMPLIFIER_RESULT_PATH,
                ),
                now=NOW,
            )

        retried = manifest.retry_artifact(
            ArtifactKind.CSV, source_result=AMPLIFIER_RESULT_PATH, now=NOW
        )
        self.assertEqual(
            retried.find_artifact(
                ArtifactKind.CSV, source_result=AMPLIFIER_RESULT_PATH
            ).status,
            ArtifactStatus.PENDING,
        )
        self.assertEqual(len(retried.artifacts), 1)

        done = retried.with_artifact(_completed_artifact(), now=NOW)
        self.assertEqual(
            done.find_artifact(
                ArtifactKind.CSV, source_result=AMPLIFIER_RESULT_PATH
            ).status,
            ArtifactStatus.COMPLETED,
        )
        with self.assertRaises(ValueError):
            done.retry_artifact(
                ArtifactKind.CSV, source_result=AMPLIFIER_RESULT_PATH, now=NOW
            )
        with self.assertRaises(KeyError):
            done.retry_artifact(
                ArtifactKind.REPORT, source_result=AMPLIFIER_RESULT_PATH, now=NOW
            )

    def test_artifact_order_is_stable_across_updates(self):
        manifest = self._manifest()
        for record in (
            ArtifactRecord(
                kind=ArtifactKind.CSV,
                status=ArtifactStatus.PENDING,
                source_result=AMPLIFIER_RESULT_PATH,
            ),
            ArtifactRecord(
                kind=ArtifactKind.REPORT,
                status=ArtifactStatus.FAILED,
                source_result=AMPLIFIER_RESULT_PATH,
                error="模板渲染失败",
            ),
        ):
            manifest = manifest.with_artifact(record, now=NOW)

        manifest = manifest.with_artifact(_completed_artifact(), now=NOW)
        self.assertEqual(
            [item.kind for item in manifest.artifacts],
            [ArtifactKind.CSV, ArtifactKind.REPORT],
        )
        retried = manifest.retry_artifact(
            ArtifactKind.REPORT, source_result=AMPLIFIER_RESULT_PATH, now=NOW
        )
        self.assertEqual(
            [item.kind for item in retried.artifacts],
            [ArtifactKind.CSV, ArtifactKind.REPORT],
        )

    def test_duplicate_artifact_records_are_rejected(self):
        payload = self._manifest().to_dict()
        payload["artifacts"] = [
            _completed_artifact().to_dict(),
            _completed_artifact().to_dict(),
        ]
        with self.assertRaises(ValueError):
            RunManifest.from_dict(payload)

    def test_unsupported_schema_version_is_rejected(self):
        payload = self._manifest().to_dict()
        payload["schema_version"] = 99
        with self.assertRaises(ValueError):
            RunManifest.from_dict(payload)


class ArchivalOutcomeTests(unittest.TestCase):
    def test_artifact_status_vocabulary_matches_the_contract(self):
        self.assertEqual(
            {item.value for item in ArtifactKind}, {"csv", "report"}
        )
        self.assertEqual(
            {item.value for item in ArtifactStatus},
            {"pending", "completed", "failed", "not_applicable"},
        )
        self.assertEqual(
            {item.value for item in ArchivalTestStatus},
            {"completed", "partial", "failed", "cancelled"},
        )
        self.assertEqual(
            {item.value for item in ArchivalStatus},
            {"pending", "completed", "partial", "failed", "not_applicable"},
        )

    def test_full_success_is_archived_completed(self):
        outcome = _outcome()
        self.assertEqual(outcome.test_status, ArchivalTestStatus.COMPLETED)
        self.assertEqual(outcome.archive_status, ArchivalStatus.COMPLETED)
        self.assertEqual(outcome.csv.status, ArtifactStatus.COMPLETED)
        self.assertEqual(outcome.report.status, ArtifactStatus.COMPLETED)
        self.assertEqual(outcome.formal_result_path, AMPLIFIER_RESULT_PATH)

    def test_full_success_requires_both_deliverables(self):
        for kind in (ArtifactKind.CSV, ArtifactKind.REPORT):
            with self.subTest(missing=kind.value):
                artifacts = {
                    ArtifactKind.CSV: ArchivedArtifact.completed(ArtifactKind.CSV, CSV_PATH),
                    ArtifactKind.REPORT: ArchivedArtifact.completed(
                        ArtifactKind.REPORT, REPORT_PATH
                    ),
                }
                artifacts[kind] = ArchivedArtifact.not_applicable(kind)
                with self.assertRaises(ValueError):
                    _outcome(artifacts=(artifacts[ArtifactKind.CSV], artifacts[ArtifactKind.REPORT]))

        with self.assertRaises(ValueError):
            _outcome(
                artifacts=(
                    ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                    ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
                )
            )

    def test_artifact_failure_is_visible_without_changing_test_status(self):
        outcome = _outcome(
            artifacts=(
                ArchivedArtifact.failed(ArtifactKind.CSV, "磁盘写入失败"),
                ArchivedArtifact.completed(ArtifactKind.REPORT, REPORT_PATH),
            )
        )
        self.assertEqual(outcome.test_status, ArchivalTestStatus.COMPLETED)
        self.assertEqual(outcome.archive_status, ArchivalStatus.PARTIAL)
        self.assertEqual(outcome.csv.reason, "磁盘写入失败")
        self.assertEqual(outcome.csv.status, ArtifactStatus.FAILED)

        report_failed = _outcome(
            artifacts=(
                ArchivedArtifact.completed(ArtifactKind.CSV, CSV_PATH),
                ArchivedArtifact.failed(ArtifactKind.REPORT, "模板渲染失败"),
            )
        )
        self.assertEqual(report_failed.test_status, ArchivalTestStatus.COMPLETED)
        self.assertEqual(report_failed.archive_status, ArchivalStatus.PARTIAL)

    def test_pending_artifact_is_archived_pending(self):
        outcome = _outcome(
            artifacts=(
                ArchivedArtifact.completed(ArtifactKind.CSV, CSV_PATH),
                ArchivedArtifact.pending(ArtifactKind.REPORT),
            )
        )
        self.assertEqual(outcome.archive_status, ArchivalStatus.PENDING)
        self.assertEqual(outcome.test_status, ArchivalTestStatus.COMPLETED)

    def test_partial_run_archives_the_saved_stage_result_only(self):
        outcome = ArchivalOutcome(
            run_id=RUN_ID,
            stage=STAGE_CABLE_LOSS,
            test_status=ArchivalTestStatus.PARTIAL,
            formal_result_path=CABLE_LOSS_INPUT_PATH,
            artifacts=(
                ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
            ),
        )
        self.assertEqual(outcome.test_status, ArchivalTestStatus.PARTIAL)
        self.assertEqual(outcome.archive_status, ArchivalStatus.COMPLETED)

        driver = ArchivalOutcome(
            run_id=RUN_ID,
            stage=STAGE_DRIVER_MAPPING,
            test_status=ArchivalTestStatus.PARTIAL,
            formal_result_path=DRIVER_MAPPING_INPUT_PATH,
            artifacts=(
                ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
            ),
        )
        self.assertEqual(driver.archive_status, ArchivalStatus.COMPLETED)

    def test_cancelled_and_failed_runs_are_distinguishable(self):
        cancelled = ArchivalOutcome(
            run_id=RUN_ID,
            stage=STAGE_AMPLIFIER,
            test_status=ArchivalTestStatus.CANCELLED,
            formal_result_path=None,
            artifacts=(
                ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
            ),
        )
        failed = ArchivalOutcome(
            run_id=RUN_ID,
            stage=STAGE_AMPLIFIER,
            test_status=ArchivalTestStatus.FAILED,
            formal_result_path=None,
            artifacts=(
                ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
            ),
        )
        self.assertEqual(cancelled.archive_status, ArchivalStatus.NOT_APPLICABLE)
        self.assertEqual(failed.archive_status, ArchivalStatus.FAILED)

    def test_failed_run_with_saved_result_keeps_the_result_traceable(self):
        outcome = ArchivalOutcome(
            run_id=RUN_ID,
            stage=STAGE_AMPLIFIER,
            test_status=ArchivalTestStatus.FAILED,
            formal_result_path=AMPLIFIER_RESULT_PATH,
            artifacts=(
                ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
            ),
        )
        self.assertEqual(outcome.test_status, ArchivalTestStatus.FAILED)
        self.assertEqual(outcome.formal_result_path, AMPLIFIER_RESULT_PATH)
        self.assertEqual(outcome.archive_status, ArchivalStatus.COMPLETED)

    def test_illegal_combinations_are_rejected(self):
        with self.assertRaises(ValueError):
            _outcome(
                stage=STAGE_CABLE_LOSS,
                formal_result_path=CABLE_LOSS_INPUT_PATH,
                artifacts=(
                    ArchivedArtifact.completed(ArtifactKind.CSV, CSV_PATH),
                    ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
                ),
            )
        with self.assertRaises(ValueError):
            _outcome(
                test_status=ArchivalTestStatus.PARTIAL,
                artifacts=(
                    ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                    ArchivedArtifact.completed(ArtifactKind.REPORT, REPORT_PATH),
                ),
            )
        with self.assertRaises(ValueError):
            _outcome(
                test_status=ArchivalTestStatus.CANCELLED,
                formal_result_path=AMPLIFIER_RESULT_PATH,
                artifacts=(
                    ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                    ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
                ),
            )
        with self.assertRaises(ValueError):
            _outcome(formal_result_path=CABLE_LOSS_INPUT_PATH)
        with self.assertRaises(ValueError):
            _outcome(formal_result_path=None)
        with self.assertRaises(ValueError):
            _outcome(formal_result_path="   ")
        with self.assertRaises(ValueError):
            _outcome(
                artifacts=(ArchivedArtifact.not_applicable(ArtifactKind.CSV),)
            )
        with self.assertRaises(ValueError):
            _outcome(
                artifacts=(
                    ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                    ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                )
            )
        with self.assertRaises(ValueError):
            _outcome(
                artifacts=(
                    ArchivedArtifact.completed(ArtifactKind.CSV, "   "),
                    ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
                )
            )
        with self.assertRaises(ValueError):
            _outcome(archived_at="   ")
        with self.assertRaises(ValueError):
            _outcome(stage="unknown")
        with self.assertRaises(ValueError):
            _outcome(run_id="..")

    def test_deliverable_artifacts_are_ordered_and_accessible(self):
        outcome = _outcome(
            artifacts=(
                ArchivedArtifact.completed(ArtifactKind.REPORT, REPORT_PATH),
                ArchivedArtifact.completed(ArtifactKind.CSV, CSV_PATH),
            )
        )
        self.assertEqual(
            [item.kind for item in outcome.artifacts],
            [ArtifactKind.CSV, ArtifactKind.REPORT],
        )
        self.assertIs(outcome.csv, outcome.artifact(ArtifactKind.CSV))
        self.assertIs(outcome.report, outcome.artifact(ArtifactKind.REPORT))


class ArchivalContractLayeringTests(unittest.TestCase):
    """归档 DTO 是纯数据契约：不依赖 Qt、文件系统或基础设施。"""

    def test_dto_imports_stay_in_the_application_domain_boundary(self):
        source = ROOT / "application" / "dto" / "archival.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        roots = {module.split(".")[0] for module in modules}
        for forbidden in ("PySide6", "PyQt5", "pathlib", "os", "infrastructure"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, roots)

    def test_dto_carries_no_ui_wording_field(self):
        for dataclass_type in (ArchivalOutcome, ArchivedArtifact):
            annotated = [field.name for field in fields(dataclass_type)]
            with self.subTest(dataclass=dataclass_type.__name__):
                self.assertNotIn("message", annotated)
                self.assertNotIn("text", annotated)
                self.assertNotIn("label", annotated)


if __name__ == "__main__":
    unittest.main()
