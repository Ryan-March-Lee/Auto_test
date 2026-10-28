"""步骤 4：主功放 CSV 交付产物的自动生成测试。

覆盖重构计划「步骤 4：把 CSV 生成接入成功完成路径」的验收口径：

- 成功运行结束后 CSV 自动落在运行目录 ``artifacts/`` 的约定位置，路径登记在归档结果与
  manifest 中，校验值与实际文件一致；
- 空数据、结果不可读（未登记/被替换）、非主功放阶段、尚未接入的报告产物都是**显式失败**
  或 ``pending``，不会生成空文件、也不会伪造成功；
- 写入失败只把 CSV 产物登记为 ``failed``，正式结果、manifest 输出登记都不受影响；
- 已完成产物按幂等复用，产物文件缺失能被独立校验发现。
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from application.archival_service import ArchivalRequestError, ResultArchivalService
from application.dto.archival import ArchivalRequest, ArchivalStatus
from application.ports.result_artifacts import (
    ArtifactGenerationError,
    ArtifactGenerationRequest,
)
from application.run_stages import RunStageRecorder
from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    CABLE_LOSS_INPUT_PATH,
    STAGE_AMPLIFIER,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
    ArtifactKind,
    ArtifactStatus,
)
from infrastructure.persistence import artifact_generator
from infrastructure.persistence.artifact_generator import CsvArtifactGenerator
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
from infrastructure.persistence.run_repository import RunRepository


RUN_ID = "20261028T010203Z_abcdef12"
CREATED_AT = datetime(2026, 10, 28, 1, 2, 3, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 28, 2, 3, 4, tzinfo=timezone.utc)
CSV_PATH = "artifacts/amplifier.csv"

CABLE_LOSSES = {"1.0": {"cable1": 0.5, "cable2": 0.5, "cable3": 0.0, "cable4": 0.0}}


def _make_directory_link(case: unittest.TestCase, target: Path, link: Path) -> None:
    """把 ``link`` 造成指向 ``target`` 的目录重定向。

    优先用符号链接；Windows 默认没有创建符号链接的权限，退化为不需要特权的
    junction（同样是目录重定向，必须被真实路径校验挡住）。两者都不可用时跳过用例。
    """
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError) as symlink_error:
        if os.name != "nt":  # pragma: no cover - 平台相关
            case.skipTest(f"当前环境无法创建符号链接: {symlink_error}")
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    if completed.returncode == 0 and link.exists():
        return
    case.skipTest(f"当前环境无法创建目录链接: {completed.stderr.strip()}")


def _amplifier_payload(*, results=None) -> dict:
    """正式主功放结果文档（``measurement_result_from_document`` 可直接解析）。"""
    if results is None:
        results = {
            "1.0": {
                "compression_type": "1dB",
                "compression_point": {},
                "small_signal_gain_db": 20.0,
                "compression_achieved": False,
                "sweep_data": {
                    "sg_power": [-20.0, -15.0],
                    "input_power_dut": [-10.0, -5.0],
                    "output_power_dut": [10.0, 14.0],
                    "gain": [20.0, 19.0],
                },
            }
        }
    return {
        "measurement_type": "amplifier",
        "status": "completed",
        "run_id": RUN_ID,
        "results": results,
    }


def _csv_request(
    *, kind=ArtifactKind.CSV, stage=STAGE_AMPLIFIER, source_result=AMPLIFIER_RESULT_PATH,
    target_path=CSV_PATH,
) -> ArtifactGenerationRequest:
    return ArtifactGenerationRequest(
        run_id=RUN_ID,
        stage=stage,
        kind=kind,
        source_result=source_result,
        target_path=target_path,
    )


class _CountingGenerator:
    """包住真实生成器，统计生成调用次数（验证幂等复用）。"""

    def __init__(self, generator):
        self.generator = generator
        self.supported_kinds = generator.supported_kinds
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        return self.generator.generate(request)


class RunDirectoryFixture(unittest.TestCase):
    """创建临时运行目录，并提供写入已完成阶段结果的辅助方法。"""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.repository = RunRepository(self.base / "runs")
        self.layout, _manifest = self.repository.create_run(
            {"run_id": RUN_ID}, run_id=RUN_ID, now=CREATED_AT
        )
        self.run_directory = self.layout.run_directory

    def _recorder(self):
        return RunStageRecorder(self.repository, RUN_ID)

    def _complete_cable_loss(self):
        recorder = self._recorder()
        recorder.start(STAGE_CABLE_LOSS, now=CREATED_AT)
        recorder.complete(
            STAGE_CABLE_LOSS,
            {"run_id": RUN_ID, "cable_losses": CABLE_LOSSES},
            now=CREATED_AT,
        )

    def _complete_amplifier(self, *, payload=None):
        """写入已完成的主功放阶段并把运行收敛为 ``completed``。"""
        recorder = self._recorder()
        recorder.skip(STAGE_DRIVER_MAPPING, now=CREATED_AT)
        recorder.start(STAGE_AMPLIFIER, now=CREATED_AT)
        recorder.complete(
            STAGE_AMPLIFIER,
            payload if payload is not None else _amplifier_payload(),
            result_path=AMPLIFIER_RESULT_PATH,
            now=CREATED_AT,
        )
        recorder.complete_run(now=CREATED_AT)

    def _csv_file(self) -> Path:
        return self.run_directory / CSV_PATH

    def _temporary_files(self) -> list[Path]:
        """列出 ``artifacts/`` 下遗留的产物临时文件（文件名带随机后缀）。"""
        artifacts = self.run_directory / "artifacts"
        return sorted(artifacts.glob("*.tmp")) if artifacts.is_dir() else []

    def _service(self, generator=None):
        return ResultArchivalService(
            runs=self.repository,
            generator=generator if generator is not None else CsvArtifactGenerator(self.repository),
        )


# -- 生成适配器本身 -------------------------------------------------------


class CsvArtifactGeneratorTests(RunDirectoryFixture):
    def test_completed_amplifier_result_is_written_as_csv(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        generator = CsvArtifactGenerator(self.repository)

        generated = generator.generate(_csv_request())

        self.assertEqual(generated.path, CSV_PATH)
        csv_file = self._csv_file()
        self.assertTrue(csv_file.is_file())
        payload = csv_file.read_bytes()
        self.assertEqual(generated.sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(generated.size_bytes, len(payload))

    def test_csv_contains_sweep_rows_of_the_formal_result(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        CsvArtifactGenerator(self.repository).generate(_csv_request())

        lines = self._csv_file().read_text(encoding="utf-8-sig").splitlines()
        header = lines[0].split(",")
        self.assertIn("frequency_ghz", header)
        self.assertIn("measurement_type", header)
        self.assertIn("run_id", header)
        self.assertIn("sg_power", header)
        self.assertEqual(len(lines), 3)  # 表头 + 两个扫描点
        self.assertTrue(all(RUN_ID in line for line in lines[1:]))

    def test_csv_generator_declares_only_the_csv_kind(self):
        self.assertEqual(
            CsvArtifactGenerator.supported_kinds, frozenset({ArtifactKind.CSV})
        )

    def test_empty_result_fails_with_an_explicit_reason(self):
        self._complete_cable_loss()
        self._complete_amplifier(payload=_amplifier_payload(results={}))

        with self.assertRaisesRegex(ArtifactGenerationError, "数据行"):
            CsvArtifactGenerator(self.repository).generate(_csv_request())

        self.assertFalse(self._csv_file().exists())
        self.assertEqual(self._temporary_files(), [])

    def test_non_amplifier_stage_is_rejected(self):
        self._complete_cable_loss()

        with self.assertRaisesRegex(ArtifactGenerationError, "只有主功放"):
            CsvArtifactGenerator(self.repository).generate(
                _csv_request(
                    stage=STAGE_CABLE_LOSS,
                    source_result=CABLE_LOSS_INPUT_PATH,
                    target_path="artifacts/cable_loss.csv",
                )
            )

    def test_csv_generator_rejects_the_report_kind(self):
        """CSV 适配器只生成 CSV；报告由 ReportArtifactGenerator 负责（步骤 5）。"""
        self._complete_cable_loss()
        self._complete_amplifier()

        with self.assertRaisesRegex(ArtifactGenerationError, "只生成 CSV"):
            CsvArtifactGenerator(self.repository).generate(
                _csv_request(
                    kind=ArtifactKind.REPORT,
                    target_path="artifacts/amplifier_report.html",
                )
            )

        self.assertFalse((self.run_directory / "artifacts" / "amplifier_report.html").exists())

    def test_unregistered_source_result_is_rejected(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        (self.run_directory / "results" / "unregistered.json").write_text(
            json.dumps(_amplifier_payload()), encoding="utf-8"
        )

        with self.assertRaisesRegex(ArtifactGenerationError, "未登记"):
            CsvArtifactGenerator(self.repository).generate(
                _csv_request(source_result="results/unregistered.json")
            )

        self.assertFalse(self._csv_file().exists())

    def test_tampered_source_result_is_rejected(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        (self.run_directory / AMPLIFIER_RESULT_PATH).write_text(
            json.dumps(_amplifier_payload(results={"2.0": {}})), encoding="utf-8"
        )

        with self.assertRaises(ArtifactGenerationError):
            CsvArtifactGenerator(self.repository).generate(_csv_request())

        self.assertFalse(self._csv_file().exists())

    def test_result_document_from_another_run_is_rejected(self):
        """共享的输入边界：结果内容自称属于别的运行时不生成任何交付产物。"""
        self._complete_cable_loss()
        self._complete_amplifier(
            payload={**_amplifier_payload(), "run_id": "20261028T010203Z_ffffffff"}
        )

        with self.assertRaisesRegex(
            ArtifactGenerationError, "运行标识与本次归档运行不一致"
        ):
            CsvArtifactGenerator(self.repository).generate(_csv_request())

        self.assertFalse(self._csv_file().exists())

    def test_write_failure_leaves_no_partial_artifact(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        # 让目标路径不可写：同名的目录会让临时文件替换目标时失败。
        (self.run_directory / CSV_PATH).mkdir(parents=True)

        with self.assertRaisesRegex(ArtifactGenerationError, "CSV 写入失败"):
            CsvArtifactGenerator(self.repository).generate(_csv_request())

        self.assertTrue((self.run_directory / CSV_PATH).is_dir())
        self.assertEqual(self._temporary_files(), [])
        # 正式结果没有被产物失败影响。
        self.assertTrue((self.run_directory / AMPLIFIER_RESULT_PATH).is_file())

    def test_real_path_guard_rejects_targets_outside_the_artifacts_directory(self):
        """无链接权限也能验证的确定性分支：目标解析后不在 artifacts/ 内即拒绝。"""
        artifacts = self.run_directory / "artifacts"
        outside = self.run_directory / "results" / "amplifier.csv"

        with self.assertRaisesRegex(ArtifactGenerationError, "artifacts"):
            artifact_generator._require_inside_artifacts(
                self.run_directory, outside, "../results/amplifier.csv"
            )
        # 目录自身不能当作产物目标。
        with self.assertRaisesRegex(ArtifactGenerationError, "artifacts"):
            artifact_generator._require_inside_artifacts(
                self.run_directory, artifacts, "artifacts/"
            )

    def test_redirected_artifacts_directory_is_rejected(self):
        """``artifacts/`` 被重定向到运行目录外时，CSV 不能写到链接目标。"""
        self._complete_cable_loss()
        self._complete_amplifier()
        outside = self.base / "outside"
        outside.mkdir()
        artifacts = self.run_directory / "artifacts"
        shutil.rmtree(artifacts)
        _make_directory_link(self, outside, artifacts)

        with self.assertRaisesRegex(ArtifactGenerationError, "产物目录"):
            CsvArtifactGenerator(self.repository).generate(_csv_request())

        self.assertEqual(list(outside.iterdir()), [])

    def test_redirected_subdirectory_outside_artifacts_is_rejected(self):
        """``artifacts/`` 内的子目录被重定向到目录外时，目标真实路径越界必须拒绝。"""
        self._complete_cable_loss()
        self._complete_amplifier()
        outside = self.base / "outside"
        outside.mkdir()
        _make_directory_link(self, outside, self.run_directory / "artifacts" / "nested")

        with self.assertRaisesRegex(ArtifactGenerationError, "artifacts"):
            CsvArtifactGenerator(self.repository).generate(
                _csv_request(target_path="artifacts/nested/amplifier.csv")
            )

        self.assertEqual(list(outside.iterdir()), [])

    def test_write_uses_a_unique_temporary_name_per_call(self):
        """临时文件名带随机后缀：并发归档不会互相覆盖对方的半成品。"""
        self._complete_cable_loss()
        self._complete_amplifier()
        generator = CsvArtifactGenerator(self.repository)
        written: list[str] = []
        real_export = artifact_generator.export_csv

        def _recording_export(result, destination):
            written.append(Path(destination).name)
            return real_export(result, destination)

        with patch.object(artifact_generator, "export_csv", _recording_export):
            generator.generate(_csv_request())
            generator.generate(_csv_request())

        self.assertEqual(len(written), 2)
        self.assertNotEqual(written[0], written[1])
        self.assertTrue(all(name.startswith("amplifier.csv.") for name in written))
        self.assertEqual(self._temporary_files(), [])


# -- 归档服务 + CSV 生成 --------------------------------------------------


class ArchivalCsvIntegrationTests(RunDirectoryFixture):
    def test_successful_archive_registers_the_csv_checksum(self):
        self._complete_cable_loss()
        self._complete_amplifier()

        outcome = self._service().archive(
            ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW
        )

        self.assertIs(outcome.csv.status, ArtifactStatus.COMPLETED)
        self.assertEqual(outcome.csv.path, CSV_PATH)
        # 报告生成在计划步骤 5 接入：端口未声明时不伪造失败，保持 pending。
        self.assertIs(outcome.report.status, ArtifactStatus.PENDING)
        self.assertIs(outcome.archive_status, ArchivalStatus.PENDING)

        manifest = self.repository.load_manifest(RUN_ID)
        record = manifest.find_artifact(ArtifactKind.CSV, source_result=AMPLIFIER_RESULT_PATH)
        self.assertIs(record.status, ArtifactStatus.COMPLETED)
        payload = self._csv_file().read_bytes()
        self.assertEqual(record.sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(record.size_bytes, len(payload))
        # 独立校验入口能确认产物文件与 manifest 记录一致。
        self.repository.verify_artifact_files(RUN_ID)
        # 历史查询仍能读取正式结果。
        self.assertIsInstance(
            self.repository.read_run_json(RUN_ID, AMPLIFIER_RESULT_PATH), dict
        )

    def test_completed_csv_is_reused_on_repeated_archival(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        counting = _CountingGenerator(CsvArtifactGenerator(self.repository))
        service = self._service(generator=counting)

        first = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)
        fingerprint = (self._csv_file().stat().st_mtime_ns, self._csv_file().read_bytes())
        second = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertEqual(len(counting.calls), 1)
        self.assertEqual(second.csv.path, first.csv.path)
        self.assertEqual(
            (self._csv_file().stat().st_mtime_ns, self._csv_file().read_bytes()),
            fingerprint,
        )
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(
            len(
                [
                    record
                    for record in manifest.artifacts
                    if record.kind is ArtifactKind.CSV
                    and record.source_result == AMPLIFIER_RESULT_PATH
                ]
            ),
            1,
        )

    def test_damaged_completed_csv_blocks_reuse(self):
        """已完成产物是终态：文件消失后再次归档必须拒绝，不能假装复用成功。"""
        self._complete_cable_loss()
        self._complete_amplifier()
        service = self._service()
        service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)
        self._csv_file().unlink()

        with self.assertRaisesRegex(ArchivalRequestError, "拒绝复用"):
            service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        record = self.repository.load_manifest(RUN_ID).find_artifact(
            ArtifactKind.CSV, source_result=AMPLIFIER_RESULT_PATH
        )
        self.assertIs(record.status, ArtifactStatus.COMPLETED)
        self.assertTrue((self.run_directory / AMPLIFIER_RESULT_PATH).is_file())
        with self.assertRaises(FileNotFoundError):
            self.repository.verify_artifact_files(RUN_ID)

    def test_tampered_completed_csv_blocks_reuse(self):
        """文件被改写后同样拒绝复用，且 manifest 记录不会被就地改判。"""
        self._complete_cable_loss()
        self._complete_amplifier()
        service = self._service()
        service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)
        self._csv_file().write_text("篡改", encoding="utf-8")

        with self.assertRaisesRegex(ArchivalRequestError, "拒绝复用"):
            service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        record = self.repository.load_manifest(RUN_ID).find_artifact(
            ArtifactKind.CSV, source_result=AMPLIFIER_RESULT_PATH
        )
        self.assertIs(record.status, ArtifactStatus.COMPLETED)
        self.assertIsNotNone(record.sha256)

    def test_concurrent_archival_keeps_a_single_consistent_artifact(self):
        """并发归档同一运行：只有一条产物记录，摘要与文件一致，无残留临时文件。"""
        self._complete_cable_loss()
        self._complete_amplifier()
        service = self._service()
        outcomes: list[object] = []
        errors: list[Exception] = []
        start = threading.Barrier(4)

        def _archive() -> None:
            start.wait()
            try:
                outcomes.append(
                    service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)
                )
            except Exception as error:  # 记录线程内异常，交主线程断言
                errors.append(error)

        threads = [
            threading.Thread(target=_archive, name=f"archive-{index}")
            for index in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(outcomes), 4)
        records = [
            record
            for record in self.repository.load_manifest(RUN_ID).artifacts
            if record.kind is ArtifactKind.CSV
        ]
        self.assertEqual(len(records), 1)
        self.assertIs(records[0].status, ArtifactStatus.COMPLETED)
        payload = self._csv_file().read_bytes()
        self.assertEqual(records[0].sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(self._temporary_files(), [])
        self.repository.verify_artifact_files(RUN_ID)

    def test_csv_write_failure_only_marks_that_artifact(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        (self.run_directory / CSV_PATH).mkdir(parents=True)

        outcome = self._service().archive(
            ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW
        )

        self.assertIs(outcome.csv.status, ArtifactStatus.FAILED)
        self.assertIn("CSV 写入失败", outcome.csv.reason)
        self.assertIs(outcome.report.status, ArtifactStatus.PENDING)
        self.assertIs(outcome.archive_status, ArchivalStatus.PARTIAL)
        # 正式结果与 manifest 输出登记都没有被产物失败破坏。
        self.assertTrue((self.run_directory / AMPLIFIER_RESULT_PATH).is_file())
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertIn(
            AMPLIFIER_RESULT_PATH, {reference.path for reference in manifest.outputs}
        )
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status.value, "completed")

    def test_history_browsing_still_lists_the_formal_result(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        self._service().archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        history = FileRunHistoryRepository(runs=self.repository)
        runs = {entry.run_id: entry for entry in history.list_runs()}
        self.assertTrue(runs[RUN_ID].browsable)
        results = {entry.result_id for entry in history.list_results(RUN_ID)}
        self.assertIn(AMPLIFIER_RESULT_PATH, results)
        self.assertIn(CABLE_LOSS_INPUT_PATH, results)
        # 产物不是正式结果：不会被历史列表当成阶段结果。
        self.assertNotIn(CSV_PATH, results)

    def test_missing_artifact_file_is_detected(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        self._service().archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self._csv_file().unlink()

        with self.assertRaises(FileNotFoundError):
            self.repository.verify_artifact_files(RUN_ID)

    def test_tampered_artifact_file_is_detected(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        self._service().archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self._csv_file().write_text("篡改", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "不一致"):
            self.repository.verify_artifact_files(RUN_ID)


# -- 端口声明 -------------------------------------------------------------


class DeclaredKindsTests(RunDirectoryFixture):
    def test_unknown_declared_kind_is_rejected_constructing_the_service(self):
        class _BadGenerator:
            supported_kinds = ("pdf",)

            def generate(self, request):  # pragma: no cover - 构造阶段即失败
                raise AssertionError

        with self.assertRaises(TypeError):
            ResultArchivalService(runs=self.repository, generator=_BadGenerator())

    def test_generator_without_declaration_keeps_generating_every_deliverable(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        inner = CsvArtifactGenerator(self.repository)

        class _UndeclaredGenerator:
            """只实现 generate() 的端口按“支持全部交付产物”处理。"""

            def generate(self, request):
                if request.kind is ArtifactKind.CSV:
                    return inner.generate(request)
                raise ArtifactGenerationError("报告模板缺失")

        service = ResultArchivalService(
            runs=self.repository, generator=_UndeclaredGenerator()
        )
        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertIs(outcome.csv.status, ArtifactStatus.COMPLETED)
        self.assertIs(outcome.report.status, ArtifactStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
