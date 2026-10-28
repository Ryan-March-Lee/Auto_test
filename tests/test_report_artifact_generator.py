"""步骤 5：把测试报告生成接入完整成功路径。

覆盖重构计划「步骤 5：将测试报告生成接入完整成功路径」的验收口径：

- 主功放完整成功（阶段完成且运行整体完成、正式结果通过校验）后自动生成
  ``artifacts/amplifier_report.html``，路径登记在归档结果与 manifest 中，校验值与实际
  文件一致；
- 部分完成、线损、驱动映射、取消/异常不生成完整报告（``not_applicable``）；
- 报告生成失败只把该产物登记为 ``failed`` 并保留正式结果，显式重试（``retry_failed``）
  是唯一的重新生成入口；
- 报告只引用与它同目录的兄弟文件，不写绝对路径，也不依赖 matplotlib 渲染；
- 组合端口把 CSV 与报告合成一个生成入口，类型派发与声明一致性在装配阶段校验。
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
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
from infrastructure.persistence.artifact_generator import (
    ArchiveArtifactGenerator,
    CsvArtifactGenerator,
    ReportArtifactGenerator,
)
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
from infrastructure.persistence.run_repository import RunRepository


RUN_ID = "20261028T030405Z_1a2b3c4d"
CREATED_AT = datetime(2026, 10, 28, 3, 4, 5, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 28, 4, 5, 6, tzinfo=timezone.utc)
CSV_PATH = "artifacts/amplifier.csv"
REPORT_PATH = "artifacts/amplifier_report.html"

CABLE_LOSSES = {"1.0": {"cable1": 0.5, "cable2": 0.5, "cable3": 0.0, "cable4": 0.0}}


def _make_directory_link(case: unittest.TestCase, target: Path, link: Path) -> None:
    """把 ``link`` 造成指向 ``target`` 的目录重定向（符号链接或 junction）。"""
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
                "compression_point": {
                    "input_power_dbm": -5.0,
                    "output_power_dbm": 14.0,
                    "gain_db": 19.0,
                    "efficiency_percent": 35.0,
                    "compression_db": 1.0,
                    "signal_generator_power_dbm": -10.0,
                },
                "small_signal_gain_db": 20.0,
                "compression_achieved": True,
                "sweep_data": {
                    "sg_power": [-20.0, -15.0],
                    "input_power_dut": [-10.0, -5.0],
                    "output_power_dut": [10.0, 14.0],
                    "gain": [20.0, 19.0],
                    "efficiency": [30.0, 35.0],
                },
            }
        }
    return {
        "measurement_type": "amplifier",
        "status": "completed",
        "run_id": RUN_ID,
        "measurement_time": "2026-10-28 03:04:05",
        "source_filename": "amplifier_measurement_20261028.json",
        "results": results,
    }


def _report_request(
    *,
    kind=ArtifactKind.REPORT,
    stage=STAGE_AMPLIFIER,
    source_result=AMPLIFIER_RESULT_PATH,
    target_path=REPORT_PATH,
) -> ArtifactGenerationRequest:
    return ArtifactGenerationRequest(
        run_id=RUN_ID,
        stage=stage,
        kind=kind,
        source_result=source_result,
        target_path=target_path,
    )


class _CountingGenerator:
    """包住真实组合端口，统计各产物类型的生成调用次数（验证幂等复用）。"""

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

    def _complete_amplifier(self, *, payload=None, complete_run=True):
        """写入已完成的主功放阶段；默认把运行收敛为 ``completed``。"""
        recorder = self._recorder()
        recorder.skip(STAGE_DRIVER_MAPPING, now=CREATED_AT)
        recorder.start(STAGE_AMPLIFIER, now=CREATED_AT)
        recorder.complete(
            STAGE_AMPLIFIER,
            payload if payload is not None else _amplifier_payload(),
            result_path=AMPLIFIER_RESULT_PATH,
            now=CREATED_AT,
        )
        if complete_run:
            recorder.complete_run(now=CREATED_AT)

    def _report_file(self) -> Path:
        return self.run_directory / REPORT_PATH

    def _csv_file(self) -> Path:
        return self.run_directory / CSV_PATH

    def _temporary_files(self) -> list[Path]:
        """列出 ``artifacts/`` 下遗留的产物临时文件（文件名带随机后缀）。"""
        artifacts = self.run_directory / "artifacts"
        return sorted(artifacts.glob("*.tmp")) if artifacts.is_dir() else []

    def _service(self, generator=None):
        return ResultArchivalService(
            runs=self.repository,
            generator=(
                generator
                if generator is not None
                else ArchiveArtifactGenerator(self.repository)
            ),
        )


# -- 报告生成适配器 -------------------------------------------------------


class ReportArtifactGeneratorTests(RunDirectoryFixture):
    def test_completed_amplifier_result_is_written_as_html_report(self):
        self._complete_cable_loss()
        self._complete_amplifier()

        generated = ReportArtifactGenerator(self.repository).generate(
            _report_request()
        )

        self.assertEqual(generated.path, REPORT_PATH)
        report_file = self._report_file()
        self.assertTrue(report_file.is_file())
        payload = report_file.read_bytes()
        self.assertEqual(generated.sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(generated.size_bytes, len(payload))

    def test_report_contains_the_formal_result_metadata_and_metrics(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        ReportArtifactGenerator(self.repository).generate(_report_request())

        html = self._report_file().read_text(encoding="utf-8")

        self.assertIn(RUN_ID, html)
        self.assertIn("measurement_type: amplifier", html)
        self.assertIn("status: completed", html)
        self.assertIn("PA 测量报告", html)
        # 指标表来自正式结果的压缩点，不是页面状态或“最新文件”。
        self.assertIn("测量指标", html)
        self.assertIn("19", html)
        self.assertIn("35", html)
        self.assertIn("是", html)  # compression_achieved
        # 单独生成报告时 manifest 里没有已完成 CSV：报告不凭空链接不存在的产物。
        self.assertNotIn('href="amplifier.csv"', html)

    def test_report_links_the_registered_csv_sibling(self):
        """CSV 已登记为 completed 且校验一致时，报告才链接它。"""
        self._complete_cable_loss()
        self._complete_amplifier()
        self._service().archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        html = self._report_file().read_text(encoding="utf-8")

        self.assertIn('href="amplifier.csv"', html)
        self.assertIn("数据文件: ", html)
        self.assertNotIn(str(self.run_directory), html)

    def test_report_omits_the_csv_link_when_the_csv_is_not_a_sibling(self):
        """CSV 仍在 manifest 里是 completed，但文件被删除：报告不写死链。"""
        self._complete_cable_loss()
        self._complete_amplifier()
        self._service().archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)
        self._csv_file().unlink()

        ReportArtifactGenerator(self.repository).generate(
            _report_request(target_path="artifacts/amplifier_report_retry.html")
        )

        html = (
            self.run_directory / "artifacts" / "amplifier_report_retry.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn('href="amplifier.csv"', html)
        # 正式结果与已登记的报告都不受这次“只读判断”影响。
        self.assertTrue((self.run_directory / AMPLIFIER_RESULT_PATH).is_file())
        self.assertTrue(self._report_file().is_file())

    def test_result_document_from_another_run_is_rejected(self):
        """正式结果内容自称属于别的运行：它不是本次运行的正式结果。"""
        self._complete_cable_loss()
        self._complete_amplifier(
            payload={**_amplifier_payload(), "run_id": "20261028T030405Z_ffffffff"}
        )

        with self.assertRaisesRegex(
            ArtifactGenerationError, "运行标识与本次归档运行不一致"
        ):
            ReportArtifactGenerator(self.repository).generate(_report_request())

        self.assertFalse(self._report_file().exists())

    def test_report_references_only_sibling_files(self):
        """报告不写运行目录的绝对路径：交付产物换机器仍然可用。"""
        self._complete_cable_loss()
        self._complete_amplifier()
        ReportArtifactGenerator(self.repository).generate(_report_request())

        html = self._report_file().read_text(encoding="utf-8")

        self.assertNotIn(str(self.run_directory), html)
        self.assertNotIn("artifacts/amplifier_report.html", html)

    def test_generator_declares_the_report_kind_only(self):
        self.assertEqual(
            ReportArtifactGenerator.supported_kinds, frozenset({ArtifactKind.REPORT})
        )

    def test_report_generator_requires_manifest_verification_capabilities(self):
        """报告要读 manifest 判断同目录 CSV，装配阶段就要求这项能力。"""

        class _ReaderOnly:
            def read_run_json(self, run_id, relative_path, *, verify=True):
                raise AssertionError  # pragma: no cover - 构造阶段即失败

        with self.assertRaisesRegex(TypeError, "load_manifest"):
            ReportArtifactGenerator(_ReaderOnly())
        # CSV 不需要这份能力，装配边界没有被顺手收紧。
        self.assertIsInstance(CsvArtifactGenerator(_ReaderOnly()), CsvArtifactGenerator)

    def test_report_generator_rejects_the_csv_kind(self):
        self._complete_cable_loss()
        self._complete_amplifier()

        with self.assertRaisesRegex(ArtifactGenerationError, "只生成 报告"):
            ReportArtifactGenerator(self.repository).generate(
                _report_request(kind=ArtifactKind.CSV, target_path=CSV_PATH)
            )

        self.assertFalse(self._csv_file().exists())

    def test_empty_result_fails_with_an_explicit_reason(self):
        self._complete_cable_loss()
        self._complete_amplifier(payload=_amplifier_payload(results={}))

        with self.assertRaisesRegex(ArtifactGenerationError, "数据行"):
            ReportArtifactGenerator(self.repository).generate(_report_request())

        self.assertFalse(self._report_file().exists())
        self.assertEqual(self._temporary_files(), [])

    def test_non_amplifier_stage_is_rejected(self):
        self._complete_cable_loss()

        with self.assertRaisesRegex(ArtifactGenerationError, "只有主功放"):
            ReportArtifactGenerator(self.repository).generate(
                _report_request(
                    stage=STAGE_CABLE_LOSS,
                    source_result=CABLE_LOSS_INPUT_PATH,
                    target_path="artifacts/cable_loss_report.html",
                )
            )

    def test_unregistered_source_result_is_rejected(self):
        self._complete_cable_loss()
        self._complete_amplifier()

        with self.assertRaisesRegex(ArtifactGenerationError, "未登记"):
            ReportArtifactGenerator(self.repository).generate(
                _report_request(source_result="results/unregistered.json")
            )

        self.assertFalse(self._report_file().exists())

    def test_write_failure_leaves_no_partial_artifact(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        # 目标路径被同名目录占住：报告一定写不进去。
        self._report_file().mkdir(parents=True)

        with self.assertRaisesRegex(ArtifactGenerationError, "报告写入失败"):
            ReportArtifactGenerator(self.repository).generate(_report_request())

        self.assertTrue(self._report_file().is_dir())
        self.assertEqual(self._temporary_files(), [])
        # 正式结果没有被产物失败影响。
        self.assertTrue((self.run_directory / AMPLIFIER_RESULT_PATH).is_file())

    def test_real_path_guard_rejects_targets_outside_the_artifacts_directory(self):
        outside = self.run_directory / "results" / "amplifier_report.html"

        with self.assertRaisesRegex(ArtifactGenerationError, "artifacts"):
            artifact_generator._require_inside_artifacts(
                self.run_directory, outside, "../results/amplifier_report.html"
            )

    def test_redirected_artifacts_directory_is_rejected(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        outside = self.base / "outside"
        outside.mkdir()
        artifacts = self.run_directory / "artifacts"
        shutil.rmtree(artifacts)
        _make_directory_link(self, outside, artifacts)

        with self.assertRaisesRegex(ArtifactGenerationError, "产物目录"):
            ReportArtifactGenerator(self.repository).generate(_report_request())

        self.assertEqual(list(outside.iterdir()), [])

    def test_write_uses_a_unique_temporary_name_per_call(self):
        """临时文件名带随机后缀：并发归档不会互相覆盖对方的半成品。"""
        self._complete_cable_loss()
        self._complete_amplifier()
        generator = ReportArtifactGenerator(self.repository)
        written: list[str] = []
        real_render = artifact_generator.render_html_report

        def _recording_render(result, destination, **kwargs):
            written.append(Path(destination).name)
            return real_render(result, destination, **kwargs)

        with patch.object(artifact_generator, "render_html_report", _recording_render):
            generator.generate(_report_request())
            generator.generate(_report_request())

        self.assertEqual(len(written), 2)
        self.assertNotEqual(written[0], written[1])
        self.assertTrue(
            all(name.startswith("amplifier_report.html.") for name in written)
        )
        self.assertEqual(self._temporary_files(), [])


# -- 报告写入本身 ---------------------------------------------------------


def _render_sample_result():
    """构造一份最小主功放结果，只用于验证 HTML 写入边界。"""
    from application.dto import (
        AmplifierFrequencyResult,
        AmplifierMeasurementResult,
    )

    return AmplifierMeasurementResult(
        run_id="report-render-run",
        frequencies=(
            AmplifierFrequencyResult(
                frequency_ghz="1.0",
                sweep_data={
                    "input_power_dut": [-10.0],
                    "output_power_dut": [10.0],
                },
            ),
        ),
    )


class ReportRenderingTests(unittest.TestCase):
    """`analysis.reporting.render_html_report` 是自动报告的唯一 HTML 写入入口。"""

    def test_plot_paths_are_referenced_by_sibling_file_name(self):
        from analysis.reporting import render_html_report

        result = _render_sample_result()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = render_html_report(
                result,
                root / "artifacts" / "amplifier_report.html",
                plot_paths=(root / "artifacts" / "p_in_vs_p_out.png",),
                data_file="amplifier.csv",
            )

            html = report.read_text(encoding="utf-8")

        # 只写兄弟文件名：交付产物换机器仍然可用，也不泄漏运行目录绝对路径。
        self.assertIn('src="p_in_vs_p_out.png"', html)
        self.assertIn('href="amplifier.csv"', html)
        self.assertNotIn(str(Path(directory)), html)

    def test_data_file_must_be_a_sibling_file_name(self):
        """报告的数据链接只能是文件名：绝对路径、盘符、目录分隔符与 ``..`` 都拒绝。"""
        from analysis.reporting import render_html_report

        result = _render_sample_result()
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "artifacts" / "amplifier_report.html"
            for bad in (
                "",
                "   ",
                "artifacts/amplifier.csv",
                "../amplifier.csv",
                "..",
                r"C:\secret\amplifier.csv",
                r"sub\amplifier.csv",
                "amplifier\x00.csv",
            ):
                with self.subTest(data_file=bad):
                    with self.assertRaises(ValueError):
                        render_html_report(result, destination, data_file=bad)
            self.assertFalse(destination.exists())

    def test_plot_path_must_resolve_to_a_file_name(self):
        """图表路径必须能取到文件名；空路径不会被渲染成 ``src=""``。"""
        from analysis.reporting import render_html_report

        result = _render_sample_result()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                render_html_report(
                    result,
                    Path(directory) / "amplifier_report.html",
                    plot_paths=("",),
                )


# -- 组合端口 -------------------------------------------------------------

class ArchiveArtifactGeneratorTests(RunDirectoryFixture):
    def test_composite_declares_both_deliverable_kinds(self):
        generator = ArchiveArtifactGenerator(self.repository)

        self.assertEqual(
            generator.supported_kinds,
            frozenset({ArtifactKind.CSV, ArtifactKind.REPORT}),
        )

    def test_composite_dispatches_each_kind_to_its_implementation(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        generator = ArchiveArtifactGenerator(self.repository)

        csv = generator.generate(
            _report_request(kind=ArtifactKind.CSV, target_path=CSV_PATH)
        )
        report = generator.generate(_report_request())

        self.assertEqual(csv.path, CSV_PATH)
        self.assertEqual(report.path, REPORT_PATH)
        self.assertTrue(self._csv_file().is_file())
        self.assertTrue(self._report_file().is_file())

    def test_composite_rejects_a_kind_without_an_implementation(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        csv_only = ArchiveArtifactGenerator(
            self.repository, generators=(CsvArtifactGenerator(self.repository),)
        )

        with self.assertRaisesRegex(ArtifactGenerationError, "report"):
            csv_only.generate(_report_request())

        self.assertFalse(self._report_file().exists())

    def test_composite_rejects_duplicate_registration(self):
        with self.assertRaises(TypeError):
            ArchiveArtifactGenerator(
                self.repository,
                generators=(
                    CsvArtifactGenerator(self.repository),
                    CsvArtifactGenerator(self.repository),
                ),
            )

    def test_composite_rejects_an_implementation_without_declaration(self):
        class _Undeclared:
            def generate(self, request):  # pragma: no cover - 装配阶段即失败
                raise AssertionError

        with self.assertRaises(TypeError):
            ArchiveArtifactGenerator(self.repository, generators=(_Undeclared(),))


# -- 归档服务 + 报告生成 --------------------------------------------------


class ArchivalReportIntegrationTests(RunDirectoryFixture):
    def test_successful_archive_registers_csv_and_report(self):
        self._complete_cable_loss()
        self._complete_amplifier()

        outcome = self._service().archive(
            ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW
        )

        self.assertIs(outcome.csv.status, ArtifactStatus.COMPLETED)
        self.assertIs(outcome.report.status, ArtifactStatus.COMPLETED)
        self.assertEqual(outcome.report.path, REPORT_PATH)
        self.assertIs(outcome.archive_status, ArchivalStatus.COMPLETED)

        manifest = self.repository.load_manifest(RUN_ID)
        record = manifest.find_artifact(
            ArtifactKind.REPORT, source_result=AMPLIFIER_RESULT_PATH
        )
        self.assertIs(record.status, ArtifactStatus.COMPLETED)
        self.assertEqual(record.path, REPORT_PATH)
        payload = self._report_file().read_bytes()
        self.assertEqual(record.sha256, hashlib.sha256(payload).hexdigest())
        self.assertEqual(record.size_bytes, len(payload))
        self.repository.verify_artifact_files(RUN_ID)
        # 正式结果仍是唯一可浏览的结果，产物不会被当成阶段结果。
        self.assertIsInstance(
            self.repository.read_run_json(RUN_ID, AMPLIFIER_RESULT_PATH), dict
        )
        history = FileRunHistoryRepository(runs=self.repository)
        self.assertIn(
            AMPLIFIER_RESULT_PATH,
            {entry.result_id for entry in history.list_results(RUN_ID)},
        )

    def test_partial_run_does_not_generate_a_report(self):
        """主功放阶段完成但运行仍未完成：只在本次结果里表达不适用，不写产物记录。"""
        self._complete_cable_loss()
        self._complete_amplifier(complete_run=False)

        outcome = self._service().archive(
            ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW
        )

        self.assertEqual(outcome.test_status.value, "partial")
        self.assertIs(outcome.report.status, ArtifactStatus.NOT_APPLICABLE)
        self.assertIs(outcome.csv.status, ArtifactStatus.NOT_APPLICABLE)
        self.assertFalse(self._report_file().exists())
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertIsNone(
            manifest.find_artifact(
                ArtifactKind.REPORT, source_result=AMPLIFIER_RESULT_PATH
            )
        )

    def test_csv_failure_does_not_leave_a_broken_link_in_the_report(self):
        """CSV 失败只影响 CSV：报告照常生成，但不链接那份不存在的文件。"""
        self._complete_cable_loss()
        self._complete_amplifier()
        # 目标被同名目录占住：CSV 一定写不进去，报告仍然可写。
        self._csv_file().mkdir(parents=True)

        outcome = self._service().archive(
            ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW
        )

        self.assertIs(outcome.csv.status, ArtifactStatus.FAILED)
        self.assertIs(outcome.report.status, ArtifactStatus.COMPLETED)
        self.assertIs(outcome.archive_status, ArchivalStatus.PARTIAL)
        html = self._report_file().read_text(encoding="utf-8")
        self.assertNotIn('href="amplifier.csv"', html)
        self.assertNotIn("数据文件: ", html)
        # 报告不因为缺一份兄弟产物而失败，正式结果也不受影响。
        self.assertTrue((self.run_directory / AMPLIFIER_RESULT_PATH).is_file())

    def test_report_failure_only_marks_that_artifact_and_can_be_retried(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        # 目标被同名目录占住：只有报告失败，CSV 与正式结果都不受影响。
        self._report_file().mkdir(parents=True)
        service = self._service()

        outcome = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        self.assertIs(outcome.csv.status, ArtifactStatus.COMPLETED)
        self.assertIs(outcome.report.status, ArtifactStatus.FAILED)
        self.assertIn("报告写入失败", outcome.report.reason)
        self.assertIs(outcome.archive_status, ArchivalStatus.PARTIAL)
        self.assertTrue((self.run_directory / AMPLIFIER_RESULT_PATH).is_file())
        manifest = self.repository.load_manifest(RUN_ID)
        record = manifest.find_artifact(
            ArtifactKind.REPORT, source_result=AMPLIFIER_RESULT_PATH
        )
        self.assertIs(record.status, ArtifactStatus.FAILED)
        self.assertIsNone(record.path)
        self.assertIn("报告写入失败", record.error)

        # 失败产物默认不重复生成；显式重试是唯一的重新生成入口。
        again = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)
        self.assertIs(again.report.status, ArtifactStatus.FAILED)

        self._report_file().rmdir()
        retried = service.archive(
            ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), retry_failed=True, now=NOW
        )

        self.assertIs(retried.report.status, ArtifactStatus.COMPLETED)
        self.assertEqual(retried.report.path, REPORT_PATH)
        self.assertIs(retried.archive_status, ArchivalStatus.COMPLETED)
        self.assertTrue(self._report_file().is_file())
        self.repository.verify_artifact_files(RUN_ID)

    def test_completed_report_is_reused_on_repeated_archival(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        counting = _CountingGenerator(ArchiveArtifactGenerator(self.repository))
        service = self._service(generator=counting)

        first = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)
        fingerprint = (
            self._report_file().stat().st_mtime_ns,
            self._report_file().read_bytes(),
        )
        second = service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        report_calls = [
            request for request in counting.calls if request.kind is ArtifactKind.REPORT
        ]
        self.assertEqual(len(report_calls), 1)
        self.assertEqual(second.report.path, first.report.path)
        self.assertEqual(
            (
                self._report_file().stat().st_mtime_ns,
                self._report_file().read_bytes(),
            ),
            fingerprint,
        )
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(
            len(
                [
                    record
                    for record in manifest.artifacts
                    if record.kind is ArtifactKind.REPORT
                    and record.source_result == AMPLIFIER_RESULT_PATH
                ]
            ),
            1,
        )

    def test_deleted_completed_report_blocks_reuse(self):
        self._complete_cable_loss()
        self._complete_amplifier()
        service = self._service()
        service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)
        self._report_file().unlink()

        with self.assertRaisesRegex(ArchivalRequestError, "拒绝复用"):
            service.archive(ArchivalRequest(RUN_ID, STAGE_AMPLIFIER), now=NOW)

        record = self.repository.load_manifest(RUN_ID).find_artifact(
            ArtifactKind.REPORT, source_result=AMPLIFIER_RESULT_PATH
        )
        self.assertIs(record.status, ArtifactStatus.COMPLETED)
        self.assertTrue((self.run_directory / AMPLIFIER_RESULT_PATH).is_file())
        with self.assertRaises(FileNotFoundError):
            self.repository.verify_artifact_files(RUN_ID)

    def test_non_amplifier_stages_do_not_produce_a_report(self):
        self._complete_cable_loss()

        outcome = self._service().archive(
            ArchivalRequest(RUN_ID, STAGE_CABLE_LOSS), now=NOW
        )

        self.assertIs(outcome.report.status, ArtifactStatus.NOT_APPLICABLE)
        self.assertFalse(
            (self.run_directory / "artifacts" / "cable_loss_report.html").exists()
        )
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(
            {
                record.kind
                for record in manifest.artifacts
                if record.source_result == CABLE_LOSS_INPUT_PATH
            },
            {ArtifactKind.CSV, ArtifactKind.REPORT},
        )
        self.assertTrue(
            all(
                record.status is ArtifactStatus.NOT_APPLICABLE
                for record in manifest.artifacts
            )
        )


if __name__ == "__main__":
    unittest.main()
