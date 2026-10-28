"""步骤 3：统一归档应用服务（骨架）测试。

覆盖：

- 归档请求只接受标准阶段与运行目录内的正式结果路径，拒绝运行目录外路径；
- 测试状态由 manifest 推导：完整成功、部分完成、失败、取消不会被调用方改写；
- 产物按 ``(source_result, kind)`` 幂等登记：pending / completed / not_applicable；
- 已完成产物默认复用、失败产物只有显式重试才重新生成、正式结果永不改写；
- 产物生成失败只影响该产物，manifest 写失败不会被吞成成功；
- 生成端口返回非请求的受控路径算该产物失败；未预期异常上抛并让产物停在 pending；
- 应用服务不导入基础设施、Qt 或 ``analysis``；
- 真实运行目录与 manifest v2 记录保持兼容。
"""

import ast
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from application.archival_service import ArchivalRequestError, ResultArchivalService
from application.dto.archival import (
    ArchivalRequest,
    ArchivalStatus,
    ArchivalTestStatus,
)
from application.ports.result_artifacts import (
    ArtifactGenerationError,
    ArtifactGenerationRequest,
    GeneratedArtifact,
)
from application.run_stages import RunStageRecorder
from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    CABLE_LOSS_INPUT_PATH,
    DRIVER_MAPPING_INPUT_PATH,
    MANIFEST_SCHEMA_VERSION,
    STAGE_AMPLIFIER,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
    ArtifactKind,
    ArtifactRecord,
    ArtifactStatus,
    FileReference,
    RunManifest,
    RunStatus,
    StageRecord,
    StageStatus,
    default_manifest,
)
from infrastructure.persistence.run_repository import RunRepository


ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "20261028T010203Z_abcdef12"
CREATED_AT = "2026-10-28T01:02:03Z"
NOW = datetime(2026, 10, 28, 2, 3, 4, tzinfo=timezone.utc)
RESULT_SHA = "a" * 64
ARTIFACT_SHA = "b" * 64
CSV_PATH = "artifacts/amplifier.csv"
REPORT_PATH = "artifacts/amplifier_report.html"


# -- 测试替身 -------------------------------------------------------------


class InMemoryRunRepository:
    """只实现归档服务需要的 manifest 读写；写正式结果视为违约。

    内存替身没有真实文件，因此 ``verify_artifact()`` 只记录调用并默认视为一致；
    需要验证“已完成产物被损坏”的场景时设置 ``fail_verify``。
    """

    def __init__(self, manifest: RunManifest) -> None:
        self.manifest = manifest
        self.updates = []
        self.write_calls = []
        self.verified = []
        self.fail_verify = None
        self.fail_update = None

    def load_manifest(self, run_id):
        if run_id != self.manifest.run_id:
            raise FileNotFoundError(f"运行不存在: {run_id}")
        return self.manifest

    def update_manifest(self, run_id, manifest, *, now=None):
        if self.fail_update is not None:
            raise self.fail_update
        if run_id != manifest.run_id:
            raise ValueError("run_id 不一致")
        self.updates.append(manifest)
        self.manifest = manifest
        return manifest

    def verify_artifact(self, run_id, artifact):
        if self.fail_verify is not None:
            raise self.fail_verify
        if run_id != self.manifest.run_id:
            raise FileNotFoundError(f"运行不存在: {run_id}")
        self.verified.append((artifact.kind, artifact.path))
        return Path(self.manifest.run_id) / artifact.path

    def write_run_json(self, *args, **kwargs):
        self.write_calls.append((args, kwargs))
        raise AssertionError("归档服务不得写正式结果")


class RecordingGenerator:
    """记录生成请求的产物生成端口替身。"""

    def __init__(self, *, failures=None, returns=None) -> None:
        self.calls = []
        self.failures = dict(failures or {})
        self.returns = dict(returns or {})

    def generate(self, request):
        self.calls.append(request)
        if request.kind in self.failures:
            raise self.failures[request.kind]
        if request.kind in self.returns:
            return self.returns[request.kind]
        return GeneratedArtifact(
            path=request.target_path, sha256=ARTIFACT_SHA, size_bytes=42
        )


class FilesystemGenerator:
    """把产物真正写进运行目录的生成端口替身（真实仓储集成测试用）。"""

    def __init__(self, layout) -> None:
        self.layout = layout
        self.calls = []

    def generate(self, request):
        import hashlib

        self.calls.append(request)
        target = self.layout.run_directory / request.target_path
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = f"{request.kind.value}:{request.source_result}".encode("utf-8")
        target.write_bytes(payload)
        return GeneratedArtifact(
            path=request.target_path,
            sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
        )


# -- manifest 夹具 --------------------------------------------------------


def _reference(path: str, stage: str) -> FileReference:
    return FileReference(
        path=path, sha256=RESULT_SHA, size_bytes=128, stage=stage, created_at=CREATED_AT
    )


def _manifest(*, status, stages, inputs=(), outputs=(), artifacts=()) -> RunManifest:
    base = default_manifest(RUN_ID, created_at=CREATED_AT)
    return replace(
        base,
        status=status,
        stages={record.name: record for record in stages},
        inputs=tuple(inputs),
        outputs=tuple(outputs),
        artifacts=tuple(artifacts),
    )


def _cable_loss_manifest(*, run_status=RunStatus.RUNNING) -> RunManifest:
    return _manifest(
        status=run_status,
        stages=[
            StageRecord(STAGE_CABLE_LOSS, StageStatus.COMPLETED, CABLE_LOSS_INPUT_PATH),
            StageRecord(STAGE_DRIVER_MAPPING),
            StageRecord(STAGE_AMPLIFIER),
        ],
        inputs=[_reference(CABLE_LOSS_INPUT_PATH, STAGE_CABLE_LOSS)],
    )


def _driver_mapping_manifest(*, run_status=RunStatus.RUNNING) -> RunManifest:
    return _manifest(
        status=run_status,
        stages=[
            StageRecord(STAGE_CABLE_LOSS, StageStatus.COMPLETED, CABLE_LOSS_INPUT_PATH),
            StageRecord(
                STAGE_DRIVER_MAPPING, StageStatus.COMPLETED, DRIVER_MAPPING_INPUT_PATH
            ),
            StageRecord(STAGE_AMPLIFIER),
        ],
        inputs=[
            _reference(CABLE_LOSS_INPUT_PATH, STAGE_CABLE_LOSS),
            _reference(DRIVER_MAPPING_INPUT_PATH, STAGE_DRIVER_MAPPING),
        ],
    )


def _amplifier_manifest(
    *,
    run_status=RunStatus.COMPLETED,
    amplifier_status=StageStatus.COMPLETED,
    outputs=None,
    artifacts=(),
) -> RunManifest:
    return _manifest(
        status=run_status,
        stages=[
            StageRecord(STAGE_CABLE_LOSS, StageStatus.COMPLETED, CABLE_LOSS_INPUT_PATH),
            StageRecord(STAGE_DRIVER_MAPPING, StageStatus.SKIPPED),
            StageRecord(
                STAGE_AMPLIFIER,
                amplifier_status,
                AMPLIFIER_RESULT_PATH
                if amplifier_status is StageStatus.COMPLETED
                else None,
            ),
        ],
        inputs=[_reference(CABLE_LOSS_INPUT_PATH, STAGE_CABLE_LOSS)],
        outputs=(
            [_reference(AMPLIFIER_RESULT_PATH, STAGE_AMPLIFIER)]
            if outputs is None
            else outputs
        ),
        artifacts=artifacts,
    )


def _completed_artifact(kind=ArtifactKind.CSV, path=CSV_PATH) -> ArtifactRecord:
    return ArtifactRecord(
        kind=kind,
        status=ArtifactStatus.COMPLETED,
        path=path,
        source_result=AMPLIFIER_RESULT_PATH,
        created_at=CREATED_AT,
        sha256=ARTIFACT_SHA,
        size_bytes=42,
    )


def _failed_artifact(kind, error="报告模板渲染失败") -> ArtifactRecord:
    return ArtifactRecord(
        kind=kind,
        status=ArtifactStatus.FAILED,
        source_result=AMPLIFIER_RESULT_PATH,
        error=error,
        created_at=CREATED_AT,
    )


# -- 请求与端口契约 -------------------------------------------------------


class ArchivalRequestContractTests(unittest.TestCase):
    def test_request_accepts_standard_stages_and_relative_results(self):
        request = ArchivalRequest(
            RUN_ID, STAGE_AMPLIFIER, "results\\amplifier_measurement.json"
        )
        self.assertEqual(request.source_result, AMPLIFIER_RESULT_PATH)
        self.assertIsNone(ArchivalRequest(RUN_ID, STAGE_CABLE_LOSS).source_result)

    def test_request_rejects_unknown_stage_and_unsafe_run_id(self):
        with self.assertRaises(ValueError):
            ArchivalRequest(RUN_ID, "unknown_stage")
        for run_id in ("../escape", "", "a/b"):
            with self.subTest(run_id=run_id):
                with self.assertRaises(ValueError):
                    ArchivalRequest(run_id, STAGE_AMPLIFIER)

    def test_request_rejects_paths_outside_the_run_directory(self):
        for value in (
            "C:/outside/results/amplifier_measurement.json",
            "../results/amplifier_measurement.json",
            "/results/amplifier_measurement.json",
            "config.json",
        ):
            with self.subTest(path=value):
                with self.assertRaises(ValueError):
                    ArchivalRequest(RUN_ID, STAGE_AMPLIFIER, value)

    def test_generated_artifact_requires_controlled_path_and_checksum(self):
        self.assertEqual(
            GeneratedArtifact(CSV_PATH, ARTIFACT_SHA, 1).path, CSV_PATH
        )
        for values in (
            ("", ARTIFACT_SHA, 1),
            ("results/amplifier.csv", ARTIFACT_SHA, 1),
            (CSV_PATH, "A" * 64, 1),
            (CSV_PATH, ARTIFACT_SHA, -1),
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    GeneratedArtifact(*values)

    def test_generation_request_requires_deliverable_kind_and_artifact_path(self):
        request = ArtifactGenerationRequest(
            run_id=RUN_ID,
            stage=STAGE_AMPLIFIER,
            kind=ArtifactKind.CSV,
            source_result=AMPLIFIER_RESULT_PATH,
            target_path=CSV_PATH,
        )
        self.assertEqual(request.kind, ArtifactKind.CSV)
        for overrides in (
            {"kind": "plot"},
            {"target_path": "results/amplifier.csv"},
            {"source_result": "config.json"},
            {"stage": "unknown_stage"},
        ):
            values = {
                "run_id": RUN_ID,
                "stage": STAGE_AMPLIFIER,
                "kind": ArtifactKind.CSV,
                "source_result": AMPLIFIER_RESULT_PATH,
                "target_path": CSV_PATH,
            }
            values.update(overrides)
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    ArtifactGenerationRequest(**values)


# -- 请求拒绝 -------------------------------------------------------------


class ArchiveRequestRejectionTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryRunRepository(_cable_loss_manifest())
        self.service = ResultArchivalService(runs=self.repository)

    def test_service_requires_run_repository_and_generator_contract(self):
        with self.assertRaises(TypeError):
            ResultArchivalService(runs=object())
        with self.assertRaises(TypeError):
            ResultArchivalService(runs=self.repository, generator=object())

    def test_service_requires_artifact_verification_capability(self):
        """复用已完成产物前要复核文件，因此仓储缺少 verify_artifact() 时直接拒绝。"""

        class _ManifestOnlyRepository:
            def load_manifest(self, run_id):  # pragma: no cover - 构造阶段即失败
                raise AssertionError

            def update_manifest(self, run_id, manifest, *, now=None):
                raise AssertionError  # pragma: no cover - 构造阶段即失败

        with self.assertRaisesRegex(TypeError, "verify_artifact"):
            ResultArchivalService(runs=_ManifestOnlyRepository())

    def test_archive_rejects_foreign_request_types(self):
        with self.assertRaises(ArchivalRequestError):
            self.service.archive(("run", STAGE_CABLE_LOSS))
        with self.assertRaises(ArchivalRequestError):
            self.service.archive(ArchivalRequest(RUN_ID, STAGE_CABLE_LOSS), retry_failed=1)

    def test_archive_rejects_unknown_run(self):
        service = ResultArchivalService(
            runs=InMemoryRunRepository(_amplifier_manifest())
        )
        with self.assertRaises(ArchivalRequestError):
            service.archive(ArchivalRequest("20261028T010204Z_abcdef12", STAGE_AMPLIFIER))

    def test_archive_rejects_a_source_result_that_is_not_the_stage_path(self):
        with self.assertRaises(ArchivalRequestError):
            self.service.archive(
                ArchivalRequest(RUN_ID, STAGE_CABLE_LOSS, AMPLIFIER_RESULT_PATH)
            )

    def test_archive_rejects_unregistered_formal_results(self):
        manifest = _cable_loss_manifest()
        repository = InMemoryRunRepository(replace(manifest, inputs=()))
        service = ResultArchivalService(runs=repository)
        with self.assertRaises(ArchivalRequestError):
            service.archive(ArchivalRequest(RUN_ID, STAGE_CABLE_LOSS))
        self.assertEqual(repository.manifest.artifacts, ())

    def test_archive_rejects_a_registration_without_checksums(self):
        manifest = _cable_loss_manifest()
        repository = InMemoryRunRepository(
            replace(
                manifest,
                inputs=[FileReference(CABLE_LOSS_INPUT_PATH, stage=STAGE_CABLE_LOSS)],
            )
        )
        service = ResultArchivalService(runs=repository)
        with self.assertRaises(ArchivalRequestError):
            service.archive(ArchivalRequest(RUN_ID, STAGE_CABLE_LOSS))

    def test_archive_rejects_unfinished_and_skipped_stages(self):
        repository = InMemoryRunRepository(
            _amplifier_manifest(
                run_status=RunStatus.RUNNING, amplifier_status=StageStatus.RUNNING
            )
        )
        service = ResultArchivalService(runs=repository)
        with self.assertRaises(ArchivalRequestError):
            service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER))

        skipped = InMemoryRunRepository(
            _manifest(
                status=RunStatus.RUNNING,
                stages=[
                    StageRecord(STAGE_CABLE_LOSS),
                    StageRecord(STAGE_DRIVER_MAPPING, StageStatus.SKIPPED),
                    StageRecord(STAGE_AMPLIFIER),
                ],
            )
        )
        with self.assertRaises(ArchivalRequestError):
            ResultArchivalService(runs=skipped).archive(
                ArchivalRequest(RUN_ID, STAGE_DRIVER_MAPPING)
            )

    def test_archive_rejects_a_completed_run_with_unfinished_stages(self):
        manifest = _amplifier_manifest()
        stages = dict(manifest.stages)
        stages[STAGE_CABLE_LOSS] = StageRecord(STAGE_CABLE_LOSS, StageStatus.RUNNING)
        repository = InMemoryRunRepository(replace(manifest, stages=stages))
        service = ResultArchivalService(runs=repository)
        with self.assertRaises(ArchivalRequestError):
            service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER))


# -- 非交付产物阶段 -------------------------------------------------------


class PartialStageArchiveTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryRunRepository(_cable_loss_manifest())
        self.service = ResultArchivalService(runs=self.repository)

    def test_cable_loss_stage_completion_archives_without_deliverables(self):
        outcome = self.service.archive(
            ArchivalRequest(RUN_ID, STAGE_CABLE_LOSS), now=NOW
        )
        self.assertEqual(outcome.test_status, ArchivalTestStatus.PARTIAL)
        self.assertEqual(outcome.formal_result_path, CABLE_LOSS_INPUT_PATH)
        self.assertEqual(outcome.csv.status, ArtifactStatus.NOT_APPLICABLE)
        self.assertEqual(outcome.report.status, ArtifactStatus.NOT_APPLICABLE)
        self.assertEqual(outcome.archive_status, ArchivalStatus.COMPLETED)
        self.assertEqual(outcome.archived_at, "2026-10-28T02:03:04Z")

        records = self.repository.manifest.artifacts
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertIs(record.status, ArtifactStatus.NOT_APPLICABLE)
            self.assertEqual(record.source_result, CABLE_LOSS_INPUT_PATH)
        self.assertEqual(self.repository.write_calls, [])

    def test_repeated_partial_archive_is_idempotent(self):
        first = self.service.archive(ArchivalRequest(RUN_ID, STAGE_CABLE_LOSS), now=NOW)
        updates_after_first = len(self.repository.updates)
        second = self.service.archive(ArchivalRequest(RUN_ID, STAGE_CABLE_LOSS), now=NOW)

        self.assertEqual(second.test_status, first.test_status)
        self.assertEqual(
            [(item.kind, item.status) for item in second.artifacts],
            [(item.kind, item.status) for item in first.artifacts],
        )
        self.assertEqual(len(self.repository.manifest.artifacts), 2)
        self.assertEqual(len(self.repository.updates), updates_after_first)

    def test_driver_mapping_uses_its_own_registered_result(self):
        repository = InMemoryRunRepository(_driver_mapping_manifest())
        service = ResultArchivalService(runs=repository)
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_DRIVER_MAPPING), now=NOW)
        self.assertEqual(outcome.formal_result_path, DRIVER_MAPPING_INPUT_PATH)
        self.assertEqual(outcome.test_status, ArchivalTestStatus.PARTIAL)
        self.assertEqual(outcome.archive_status, ArchivalStatus.COMPLETED)


# -- 主功放产物编排 -------------------------------------------------------


class AmplifierArchiveTests(unittest.TestCase):
    def _service(self, manifest=None, generator=None):
        self.repository = InMemoryRunRepository(manifest or _amplifier_manifest())
        self.generator = generator
        return ResultArchivalService(runs=self.repository, generator=generator)

    def test_full_success_registers_pending_artifacts_without_generator(self):
        service = self._service()
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(outcome.test_status, ArchivalTestStatus.COMPLETED)
        self.assertEqual(outcome.formal_result_path, AMPLIFIER_RESULT_PATH)
        self.assertEqual(outcome.csv.status, ArtifactStatus.PENDING)
        self.assertEqual(outcome.report.status, ArtifactStatus.PENDING)
        self.assertEqual(outcome.archive_status, ArchivalStatus.PENDING)

        records = self.repository.manifest.artifacts
        self.assertEqual(len(records), 2)
        for record in records:
            self.assertIs(record.status, ArtifactStatus.PENDING)
            self.assertEqual(record.source_result, AMPLIFIER_RESULT_PATH)
        self.assertEqual(self.repository.write_calls, [])

    def test_repeated_archive_does_not_duplicate_pending_records(self):
        service = self._service()
        service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)
        updates = len(self.repository.updates)
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(len(self.repository.manifest.artifacts), 2)
        self.assertEqual(len(self.repository.updates), updates)
        self.assertEqual(outcome.archive_status, ArchivalStatus.PENDING)

    def test_generator_completes_both_artifacts(self):
        generator = RecordingGenerator()
        service = self._service(generator=generator)
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(outcome.csv.status, ArtifactStatus.COMPLETED)
        self.assertEqual(outcome.csv.path, CSV_PATH)
        self.assertEqual(outcome.report.status, ArtifactStatus.COMPLETED)
        self.assertEqual(outcome.report.path, REPORT_PATH)
        self.assertEqual(outcome.archive_status, ArchivalStatus.COMPLETED)
        self.assertEqual(
            [call.target_path for call in generator.calls], [CSV_PATH, REPORT_PATH]
        )
        self.assertEqual(
            [call.kind for call in generator.calls],
            [ArtifactKind.CSV, ArtifactKind.REPORT],
        )
        for record in self.repository.manifest.artifacts:
            self.assertIs(record.status, ArtifactStatus.COMPLETED)
            self.assertEqual(record.sha256, ARTIFACT_SHA)
            self.assertEqual(record.size_bytes, 42)
        self.assertEqual(self.repository.write_calls, [])

    def test_report_failure_only_fails_that_artifact(self):
        generator = RecordingGenerator(
            failures={ArtifactKind.REPORT: ArtifactGenerationError("报告模板缺失")}
        )
        service = self._service(generator=generator)
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(outcome.test_status, ArchivalTestStatus.COMPLETED)
        self.assertEqual(outcome.csv.status, ArtifactStatus.COMPLETED)
        self.assertEqual(outcome.report.status, ArtifactStatus.FAILED)
        self.assertEqual(outcome.report.reason, "报告模板缺失")
        self.assertEqual(outcome.archive_status, ArchivalStatus.PARTIAL)
        # 正式结果仍是 manifest 登记的唯一事实来源，不因产物失败被改写。
        self.assertEqual(
            [item.path for item in self.repository.manifest.outputs],
            [AMPLIFIER_RESULT_PATH],
        )

    def test_completed_artifacts_are_reused_without_regeneration(self):
        generator = RecordingGenerator()
        service = self._service(generator=generator)
        service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)
        calls_after_first = len(generator.calls)
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(len(generator.calls), calls_after_first)
        self.assertEqual(outcome.archive_status, ArchivalStatus.COMPLETED)
        self.assertEqual(len(self.repository.manifest.artifacts), 2)

    def test_reused_completed_artifacts_are_verified_against_the_manifest(self):
        """复用已完成产物前必须复核文件：归档结果不能只靠 manifest 的一句声明。"""
        service = self._service(generator=RecordingGenerator())
        first = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)
        second = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(second.csv.status, first.csv.status)
        self.assertEqual(
            self.repository.verified,
            [
                (ArtifactKind.CSV, CSV_PATH),
                (ArtifactKind.REPORT, REPORT_PATH),
            ],
        )

    def test_damaged_completed_artifact_is_rejected_instead_of_reused(self):
        """文件被删除的已完成产物按归档请求失败上报，manifest 记录不被改写。"""
        service = self._service(generator=RecordingGenerator())
        service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)
        before = self.repository.manifest.artifacts
        updates_before = len(self.repository.updates)
        self.repository.fail_verify = FileNotFoundError("产物文件已被删除")

        with self.assertRaisesRegex(ArchivalRequestError, "拒绝复用"):
            service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(self.repository.manifest.artifacts, before)
        self.assertEqual(len(self.repository.updates), updates_before)
        self.assertEqual(
            [record.status for record in self.repository.manifest.artifacts],
            [ArtifactStatus.COMPLETED, ArtifactStatus.COMPLETED],
        )

    def test_failed_artifact_is_not_regenerated_without_explicit_retry(self):
        generator = RecordingGenerator(
            failures={ArtifactKind.REPORT: ArtifactGenerationError("渲染失败")}
        )
        service = self._service(generator=generator)
        service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)
        calls_after_first = len(generator.calls)
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(len(generator.calls), calls_after_first)
        self.assertEqual(outcome.report.status, ArtifactStatus.FAILED)
        self.assertEqual(outcome.archive_status, ArchivalStatus.PARTIAL)

    def test_explicit_retry_regenerates_only_the_failed_artifact(self):
        generator = RecordingGenerator(
            failures={ArtifactKind.REPORT: ArtifactGenerationError("渲染失败")}
        )
        service = self._service(generator=generator)
        service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        generator.failures.clear()
        calls_after_first = len(generator.calls)
        outcome = service.archive(
            ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), retry_failed=True, now=NOW
        )

        self.assertEqual(len(generator.calls), calls_after_first + 1)
        self.assertEqual(generator.calls[-1].kind, ArtifactKind.REPORT)
        self.assertEqual(outcome.report.status, ArtifactStatus.COMPLETED)
        self.assertEqual(outcome.archive_status, ArchivalStatus.COMPLETED)
        records = {
            record.kind: record for record in self.repository.manifest.artifacts
        }
        self.assertIs(records[ArtifactKind.REPORT].status, ArtifactStatus.COMPLETED)
        self.assertEqual(records[ArtifactKind.REPORT].error, None)

    def test_generator_returning_the_wrong_type_is_a_failed_artifact(self):
        generator = RecordingGenerator(returns={ArtifactKind.CSV: CSV_PATH})
        service = self._service(generator=generator)
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(outcome.csv.status, ArtifactStatus.FAILED)
        self.assertIn("GeneratedArtifact", outcome.csv.reason)
        self.assertEqual(outcome.archive_status, ArchivalStatus.PARTIAL)

    def test_generator_returning_a_different_artifact_path_is_a_failed_artifact(self):
        generator = RecordingGenerator(
            returns={
                ArtifactKind.CSV: GeneratedArtifact(
                    "artifacts/amplifier_copy.csv", ARTIFACT_SHA, 42
                )
            }
        )
        service = self._service(generator=generator)
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(outcome.csv.status, ArtifactStatus.FAILED)
        self.assertIn(CSV_PATH, outcome.csv.reason)
        self.assertIn("artifacts/amplifier_copy.csv", outcome.csv.reason)
        self.assertEqual(outcome.report.status, ArtifactStatus.COMPLETED)
        self.assertEqual(outcome.archive_status, ArchivalStatus.PARTIAL)
        records = {record.kind: record for record in self.repository.manifest.artifacts}
        self.assertIs(records[ArtifactKind.CSV].status, ArtifactStatus.FAILED)
        self.assertIsNone(records[ArtifactKind.CSV].path)
        self.assertNotIn(
            "artifacts/amplifier_copy.csv",
            [record.path for record in self.repository.manifest.artifacts],
        )

    def test_unexpected_generator_exception_propagates_and_leaves_the_artifact_pending(self):
        """端口缺陷不被兜底成产物失败：异常上抛，状态停在 pending 由调用方复查。"""
        generator = RecordingGenerator(
            failures={ArtifactKind.CSV: RuntimeError("端口实现缺陷")}
        )
        service = self._service(generator=generator)
        with self.assertRaises(RuntimeError):
            service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        records = {record.kind: record for record in self.repository.manifest.artifacts}
        self.assertIs(records[ArtifactKind.CSV].status, ArtifactStatus.PENDING)
        self.assertIs(records[ArtifactKind.REPORT].status, ArtifactStatus.PENDING)

    def test_generator_value_error_is_a_failed_artifact(self):
        generator = RecordingGenerator(
            failures={ArtifactKind.CSV: ValueError("产量为零，无可用 CSV 数据")}
        )
        service = self._service(generator=generator)
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)
        self.assertEqual(outcome.csv.reason, "产量为零，无可用 CSV 数据")

    def test_os_error_from_the_generator_is_a_failed_artifact(self):
        generator = RecordingGenerator(
            failures={ArtifactKind.REPORT: OSError("磁盘已满")}
        )
        service = self._service(generator=generator)
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)
        self.assertEqual(outcome.report.status, ArtifactStatus.FAILED)
        self.assertEqual(outcome.report.reason, "磁盘已满")

    def test_manifest_write_failure_is_not_swallowed(self):
        service = self._service()
        self.repository.fail_update = OSError("manifest 写失败")
        with self.assertRaises(OSError):
            service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

    def test_partial_amplifier_run_never_generates_deliverables(self):
        generator = RecordingGenerator()
        service = self._service(
            _amplifier_manifest(run_status=RunStatus.RUNNING), generator=generator
        )
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(outcome.test_status, ArchivalTestStatus.PARTIAL)
        self.assertEqual(outcome.formal_result_path, AMPLIFIER_RESULT_PATH)
        self.assertEqual(outcome.csv.status, ArtifactStatus.NOT_APPLICABLE)
        self.assertEqual(outcome.report.status, ArtifactStatus.NOT_APPLICABLE)
        self.assertEqual(generator.calls, [])
        # 不写 manifest，避免以后的完整成功被本次 not_applicable 挡住。
        self.assertEqual(self.repository.manifest.artifacts, ())

    def test_failed_run_with_registered_result_keeps_the_result(self):
        generator = RecordingGenerator()
        service = self._service(
            _amplifier_manifest(run_status=RunStatus.FAILED), generator=generator
        )
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(outcome.test_status, ArchivalTestStatus.FAILED)
        self.assertEqual(outcome.formal_result_path, AMPLIFIER_RESULT_PATH)
        self.assertEqual(outcome.archive_status, ArchivalStatus.COMPLETED)
        self.assertEqual(outcome.report.status, ArtifactStatus.NOT_APPLICABLE)
        self.assertEqual(generator.calls, [])

    def test_partial_amplifier_run_with_registered_artifacts_is_rejected(self):
        manifest = _amplifier_manifest(
            run_status=RunStatus.RUNNING,
            artifacts=(_completed_artifact(ArtifactKind.CSV),),
        )
        service = self._service(manifest)
        with self.assertRaises(ArchivalRequestError):
            service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)
        self.assertEqual(self.repository.manifest.artifacts, manifest.artifacts)

    def test_not_applicable_artifact_blocks_later_generation(self):
        manifest = _amplifier_manifest(
            artifacts=(
                ArtifactRecord(
                    kind=ArtifactKind.CSV,
                    status=ArtifactStatus.NOT_APPLICABLE,
                    source_result=AMPLIFIER_RESULT_PATH,
                ),
            )
        )
        service = self._service(manifest)
        with self.assertRaises(ArchivalRequestError):
            service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

    def test_failed_artifact_from_a_previous_run_state_blocks_nothing(self):
        manifest = _amplifier_manifest(
            artifacts=(_failed_artifact(ArtifactKind.REPORT),)
        )
        generator = RecordingGenerator()
        service = self._service(manifest, generator=generator)
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(outcome.report.status, ArtifactStatus.FAILED)
        self.assertEqual(
            [call.kind for call in generator.calls], [ArtifactKind.CSV]
        )


# -- 取消与失败 -----------------------------------------------------------


class CancellationArchiveTests(unittest.TestCase):
    def test_cancelled_stage_archives_without_formal_result(self):
        manifest = _manifest(
            status=RunStatus.CANCELLED,
            stages=[
                StageRecord(STAGE_CABLE_LOSS),
                StageRecord(STAGE_DRIVER_MAPPING),
                StageRecord(STAGE_AMPLIFIER, StageStatus.CANCELLED),
            ],
        )
        repository = InMemoryRunRepository(manifest)
        service = ResultArchivalService(runs=repository)
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(outcome.test_status, ArchivalTestStatus.CANCELLED)
        self.assertIsNone(outcome.formal_result_path)
        self.assertEqual(outcome.csv.status, ArtifactStatus.NOT_APPLICABLE)
        self.assertEqual(outcome.archive_status, ArchivalStatus.NOT_APPLICABLE)

    def test_cancelled_run_with_a_leftover_result_is_rejected(self):
        manifest = _amplifier_manifest(run_status=RunStatus.CANCELLED)
        repository = InMemoryRunRepository(manifest)
        service = ResultArchivalService(runs=repository)
        with self.assertRaises(ArchivalRequestError):
            service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

    def test_stage_failure_without_result_has_no_deliverables(self):
        manifest = _manifest(
            status=RunStatus.FAILED,
            stages=[
                StageRecord(STAGE_CABLE_LOSS),
                StageRecord(STAGE_DRIVER_MAPPING),
                StageRecord(STAGE_AMPLIFIER, StageStatus.FAILED),
            ],
        )
        repository = InMemoryRunRepository(manifest)
        service = ResultArchivalService(runs=repository)
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(outcome.test_status, ArchivalTestStatus.FAILED)
        self.assertIsNone(outcome.formal_result_path)
        self.assertEqual(outcome.archive_status, ArchivalStatus.FAILED)


# -- 真实运行目录 ---------------------------------------------------------


class RealRunDirectoryArchiveTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.repository = RunRepository(self.base / "runs")
        self.layout, _manifest = self.repository.create_run(
            {"run_id": RUN_ID}, run_id=RUN_ID
        )

    def test_cable_loss_archive_is_written_to_the_run_manifest(self):
        recorder = RunStageRecorder(self.repository, RUN_ID)
        recorder.complete(
            STAGE_CABLE_LOSS, {"run_id": RUN_ID, "cable_losses": [{"frequency": 2.4}]}
        )
        service = ResultArchivalService(runs=self.repository)
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_CABLE_LOSS), now=NOW)

        self.assertEqual(outcome.formal_result_path, CABLE_LOSS_INPUT_PATH)
        stored = RunRepository(self.base / "runs").load_manifest(RUN_ID)
        self.assertEqual(stored.schema_version, MANIFEST_SCHEMA_VERSION)
        self.assertEqual(len(stored.artifacts), 2)
        self.assertTrue(
            all(
                record.status is ArtifactStatus.NOT_APPLICABLE
                for record in stored.artifacts
            )
        )
        payload = json.loads(self.layout.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["artifacts"]), 2)

    def test_amplifier_archive_records_real_artifacts(self):
        recorder = RunStageRecorder(self.repository, RUN_ID)
        recorder.complete(
            STAGE_CABLE_LOSS, {"run_id": RUN_ID, "cable_losses": [{"frequency": 2.4}]}
        )
        recorder.skip(STAGE_DRIVER_MAPPING)
        recorder.complete(
            STAGE_AMPLIFIER,
            {"run_id": RUN_ID, "frequencies": []},
            result_path=AMPLIFIER_RESULT_PATH,
        )
        recorder.complete_run()

        generator = FilesystemGenerator(self.layout)
        service = ResultArchivalService(runs=self.repository, generator=generator)
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(outcome.test_status, ArchivalTestStatus.COMPLETED)
        self.assertEqual(outcome.archive_status, ArchivalStatus.COMPLETED)
        self.assertEqual(outcome.csv.path, CSV_PATH)
        self.assertEqual(outcome.report.path, REPORT_PATH)
        for relative in (CSV_PATH, REPORT_PATH):
            self.assertTrue((self.layout.run_directory / relative).is_file())

        stored = self.repository.load_manifest(RUN_ID)
        records = {record.kind: record for record in stored.artifacts}
        self.assertIs(records[ArtifactKind.CSV].status, ArtifactStatus.COMPLETED)
        self.assertIs(records[ArtifactKind.REPORT].status, ArtifactStatus.COMPLETED)
        self.assertEqual(records[ArtifactKind.REPORT].source_result, AMPLIFIER_RESULT_PATH)


# -- 分层门禁 -------------------------------------------------------------


class ArchivalServiceBoundaryTests(unittest.TestCase):
    def test_service_imports_no_infrastructure_qt_or_analysis(self):
        source_path = ROOT / "application" / "archival_service.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8-sig"), str(source_path))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        for forbidden in ("infrastructure", "PySide6", "PyQt5", "analysis"):
            offenders = {
                module
                for module in imported
                if module == forbidden or module.startswith(forbidden + ".")
            }
            self.assertEqual(offenders, set(), f"归档服务不得依赖 {forbidden}")

    def test_service_does_not_use_broad_except(self):
        """宽泛捕获会把产物失败与运行失败混在一起，服务只捕获明确的失败类别。"""
        source_path = ROOT / "application" / "archival_service.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8-sig"), str(source_path))
        broad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            types = node.type
            names = []
            if isinstance(types, ast.Tuple):
                names = [getattr(item, "id", "") for item in types.elts]
            elif isinstance(types, ast.Name):
                names = [types.id]
            if not names or "Exception" in names or "BaseException" in names:
                broad.append(node.lineno)
        self.assertEqual(broad, [], f"归档服务出现宽泛捕获: {broad}")
