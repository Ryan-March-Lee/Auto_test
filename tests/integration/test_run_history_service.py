"""历史运行服务、只读仓储与结果报告定位的契约测试。

历史运行页已从界面移除，页面与视图模型测试不再保留；这里只固定后台仍在使用的行为：

- 历史运行 DTO：可用性三档、非法组合拒绝，``can_regenerate`` 只在“结构完整 + 有可定位
  失败产物”时成立；``missing`` 只能出现在 ``completed`` 产物上，且是只读诊断；
- ``RunHistoryService``：只接受历史列表中的运行标识，重新生成走归档服务的
  ``retry_failed=True``，未装配归档服务/没有失败产物/运行不可用都明确失败；
- ``FileRunHistoryRepository.list_run_summaries``：从 manifest 读出运行/阶段/产物状态，
  损坏运行只给可用性与原因；已登记为 ``completed`` 的产物文件被删除时标记 ``missing``，
  但 manifest 记录不被改写；
- 结果报告定位：阶段目录结果按 ``measurement_id`` 找到同目录报告，缺失时返回
  ``MISSING``，不退回“该运行第一份报告”。
"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from application.archival_service import ResultArchivalService
from application.dto.archival import (
    ArchivalRequest,
    ArchivalTestStatus,
    ArtifactRepairFailure,
    ArtifactRepairOutcome,
)
from application.dto.run_history import (
    ResultReportState,
    RunArchiveState,
    RunArtifactSummary,
    RunHistorySummary,
    RunStageSummary,
)
from application.dto import measurement_result_from_document
from application.ports.gui_data_service import (
    RESULT_SOURCE_STAGE,
    LoadedResultDto,
    ResultFileDto,
)
from application.ports.result_artifacts import ArtifactGenerationError
from application.ports.stage_result_repository import MeasurementStage
from application.run_history_service import RunHistoryRequestError, RunHistoryService
from application.run_stages import RunStageRecorder
from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    STAGE_AMPLIFIER,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
    ArtifactKind,
    ArtifactStatus,
    RunStatus,
    StageStatus,
)
from infrastructure.filesystem.paths import PathService
from infrastructure.gui_data_service import ResultQueryAdapter
from infrastructure.persistence import artifact_generator
from infrastructure.persistence.artifact_generator import ArchiveArtifactGenerator
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
from infrastructure.persistence.run_repository import RunRepository


RUN_ID = "20261028T010203Z_abcdef12"
OTHER_RUN_ID = "20261027T010203Z_abcdef12"
CREATED_AT = datetime(2026, 10, 28, 1, 2, 3, tzinfo=timezone.utc)
# 归档服务的日志挂在应用日志树（``pa_auto_test``）下，才进得了 ``setup_logging()``
# 的文件；这里按契约名断言，不跟随实现里的 logger 变量。
ARCHIVAL_LOGGER = "pa_auto_test.application.archival_service"


def _fake_report_plots(result, output_dir):
    """历史服务集成测试不真实渲染 matplotlib；只验证报告与图表文件的接线。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "amplifier_p_in_vs_p_out.png"
    path.write_bytes(b"fake-plot")
    return {"p_in_vs_p_out": path}, ()


def _amplifier_payload(*, run_id=RUN_ID) -> dict:
    """可解析的正式主功放结果文档。"""
    return {
        "measurement_type": "amplifier",
        "status": "completed",
        "run_id": run_id,
        "results": {
            "5.2": {
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
        },
    }


class FailingGenerator:
    """每次都失败的产物生成端口：把 CSV 与报告都登记成 failed。"""

    supported_kinds = frozenset({ArtifactKind.CSV, ArtifactKind.REPORT})

    def generate(self, request):
        raise ArtifactGenerationError(f"{request.kind.value} 故意失败")


class StaticStatusPort:
    """固定返回一批运行摘要的只读状态端口 / 运行摘要来源替身。"""

    def __init__(self, summaries, *, failure=None):
        self.summaries = tuple(summaries)
        self.failure = failure
        self.calls = 0

    def list_run_summaries(self):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return self.summaries

    def list_runs(self):
        """视图模型使用的入口（与 RunHistoryServicePort 一致）。"""
        return self.list_run_summaries()


class RecordingArchival:
    """记录归档调用的归档服务替身。"""

    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def archive(self, request, *, retry_failed=False, now=None):
        self.calls.append((request, retry_failed))
        return self.outcome


def _summary(**overrides) -> RunHistorySummary:
    payload = dict(
        run_id=RUN_ID,
        created_at=CREATED_AT,
        state=RunArchiveState.USABLE,
        run_status=RunStatus.COMPLETED,
        stages=(
            RunStageSummary(STAGE_AMPLIFIER, StageStatus.COMPLETED, AMPLIFIER_RESULT_PATH),
        ),
        artifacts=(
            RunArtifactSummary(
                ArtifactKind.CSV,
                ArtifactStatus.FAILED,
                source_result=AMPLIFIER_RESULT_PATH,
                stage=STAGE_AMPLIFIER,
                error="磁盘已满",
            ),
        ),
    )
    payload.update(overrides)
    return RunHistorySummary(**payload)


# -- DTO 契约 -------------------------------------------------------------


class RunHistoryDtoTests(unittest.TestCase):
    def test_completed_summary_reports_stage_and_artifact_state(self):
        summary = _summary()

        self.assertTrue(summary.browsable)
        self.assertEqual(summary.total_stages, 1)
        self.assertEqual(summary.finished_stages, 1)
        self.assertEqual(summary.result_count, 1)
        self.assertEqual([item.kind for item in summary.failed_artifacts], [ArtifactKind.CSV])
        self.assertEqual(summary.regeneration_stage, STAGE_AMPLIFIER)
        self.assertTrue(summary.can_regenerate)

    def test_invalid_summary_requires_reason_and_no_records(self):
        with self.assertRaises(ValueError):
            RunHistorySummary(
                run_id=RUN_ID,
                created_at=CREATED_AT,
                state=RunArchiveState.INVALID,
            )
        with self.assertRaises(ValueError):
            RunHistorySummary(
                run_id=RUN_ID,
                created_at=CREATED_AT,
                state=RunArchiveState.INVALID,
                problem="manifest 损坏",
                stages=(RunStageSummary(STAGE_AMPLIFIER, StageStatus.COMPLETED),),
            )

    def test_summary_requires_timezone_aware_creation_time(self):
        with self.assertRaises(ValueError):
            RunHistorySummary(
                run_id=RUN_ID,
                created_at=datetime(2026, 10, 28, 1, 2, 3),
                state=RunArchiveState.USABLE,
            )

    def test_regeneration_needs_a_failed_artifact_with_a_known_stage(self):
        no_stage = _summary(
            artifacts=(
                RunArtifactSummary(
                    ArtifactKind.REPORT,
                    ArtifactStatus.FAILED,
                    source_result=AMPLIFIER_RESULT_PATH,
                    error="模板缺失",
                ),
            )
        )
        self.assertFalse(no_stage.can_regenerate)

        pending_only = _summary(
            artifacts=(
                RunArtifactSummary(
                    ArtifactKind.CSV,
                    ArtifactStatus.PENDING,
                    source_result=AMPLIFIER_RESULT_PATH,
                    stage=STAGE_AMPLIFIER,
                ),
            )
        )
        self.assertFalse(pending_only.can_regenerate)

        incomplete = _summary(state=RunArchiveState.INCOMPLETE, problem="缺少配置快照")
        self.assertFalse(incomplete.can_regenerate)

    def test_stage_and_artifact_names_cannot_be_blank(self):
        with self.assertRaises(ValueError):
            RunStageSummary(" ", StageStatus.PENDING)
        with self.assertRaises(ValueError):
            RunArtifactSummary(ArtifactKind.CSV, ArtifactStatus.PENDING, stage="")

    def test_missing_is_only_reported_for_completed_artifacts(self):
        with self.assertRaises(ValueError):
            RunArtifactSummary(ArtifactKind.CSV, ArtifactStatus.FAILED, missing=True)
        with self.assertRaises(ValueError):
            RunArtifactSummary(ArtifactKind.CSV, ArtifactStatus.PENDING, missing=True)
        with self.assertRaises(ValueError):
            RunArtifactSummary(
                ArtifactKind.CSV, ArtifactStatus.COMPLETED, missing="yes"
            )

        missing = RunArtifactSummary(
            ArtifactKind.CSV,
            ArtifactStatus.COMPLETED,
            source_result=AMPLIFIER_RESULT_PATH,
            stage=STAGE_AMPLIFIER,
            missing=True,
        )
        summary = _summary(artifacts=(missing,))

        self.assertEqual([item.kind for item in summary.missing_artifacts], [ArtifactKind.CSV])
        # 只读诊断不改变“能否重新生成”：completed 仍是终态。
        self.assertFalse(summary.can_regenerate)
        self.assertEqual(summary.failed_artifacts, ())


# -- 应用用例 -------------------------------------------------------------


class RunHistoryServiceTests(unittest.TestCase):
    def test_list_runs_returns_port_summaries(self):
        port = StaticStatusPort([_summary()])
        service = RunHistoryService(statuses=port)

        self.assertEqual(service.list_runs(), (_summary(),))
        self.assertEqual(port.calls, 1)

    def test_list_runs_reports_port_failure(self):
        service = RunHistoryService(
            statuses=StaticStatusPort([], failure=OSError("磁盘错误"))
        )

        with self.assertRaisesRegex(RunHistoryRequestError, "无法读取历史运行列表"):
            service.list_runs()

    def test_regenerate_uses_the_registered_run_and_retries_failed_artifacts(self):
        outcome = object()
        archival = RecordingArchival(outcome)
        service = RunHistoryService(
            statuses=StaticStatusPort([_summary()]), archival=archival
        )

        self.assertIs(service.regenerate_failed_artifacts(RUN_ID), outcome)

        request, retry_failed = archival.calls[0]
        self.assertEqual(request.run_id, RUN_ID)
        self.assertEqual(request.stage, STAGE_AMPLIFIER)
        self.assertTrue(retry_failed)

    def test_regenerate_rejects_unknown_or_invalid_runs(self):
        service = RunHistoryService(
            statuses=StaticStatusPort(
                [
                    _summary(),
                    _summary(
                        run_id=OTHER_RUN_ID,
                        state=RunArchiveState.INVALID,
                        problem="manifest 损坏",
                        stages=(),
                        artifacts=(),
                    ),
                ]
            ),
            archival=RecordingArchival(object()),
        )

        with self.assertRaisesRegex(RunHistoryRequestError, "不在历史列表中"):
            service.regenerate_failed_artifacts("20260101T000000Z_ffffffff")
        with self.assertRaisesRegex(RunHistoryRequestError, "不可用"):
            service.regenerate_failed_artifacts(OTHER_RUN_ID)
        with self.assertRaises(RunHistoryRequestError):
            service.regenerate_failed_artifacts("")

    def test_regenerate_rejects_runs_without_failed_artifacts(self):
        service = RunHistoryService(
            statuses=StaticStatusPort(
                [
                    _summary(
                        artifacts=(
                            RunArtifactSummary(
                                ArtifactKind.CSV,
                                ArtifactStatus.COMPLETED,
                                source_result=AMPLIFIER_RESULT_PATH,
                                stage=STAGE_AMPLIFIER,
                            ),
                        )
                    )
                ]
            ),
            archival=RecordingArchival(object()),
        )

        with self.assertRaisesRegex(RunHistoryRequestError, "没有失败的交付产物"):
            service.regenerate_failed_artifacts(RUN_ID)

    def test_regenerate_requires_an_archival_service(self):
        service = RunHistoryService(statuses=StaticStatusPort([_summary()]))

        with self.assertRaisesRegex(RunHistoryRequestError, "未装配归档服务"):
            service.regenerate_failed_artifacts(RUN_ID)

    def test_artifact_location_requires_a_location_port(self):
        service = RunHistoryService(statuses=StaticStatusPort([_summary()]))

        with self.assertRaisesRegex(RunHistoryRequestError, "未装配运行位置入口"):
            service.artifact_location(RUN_ID, ArtifactKind.REPORT)

    def test_artifact_location_rejects_runs_outside_the_history(self):
        service = RunHistoryService(statuses=StaticStatusPort([_summary()]))

        with self.assertRaisesRegex(RunHistoryRequestError, "不在历史列表中"):
            service.artifact_location(OTHER_RUN_ID, ArtifactKind.REPORT)

    def test_archive_contract_errors_become_history_request_errors(self):
        class BrokenArchival:
            def archive(self, request, *, retry_failed=False, now=None):
                raise ValueError("manifest 写回失败")

        service = RunHistoryService(
            statuses=StaticStatusPort([_summary()]), archival=BrokenArchival()
        )

        with self.assertRaisesRegex(RunHistoryRequestError, "重新生成失败"):
            service.regenerate_failed_artifacts(RUN_ID)

    def test_repair_artifacts_uses_the_registered_run(self):
        class RepairArchival:
            def __init__(self):
                self.calls = []

            def archive(self, request, *, retry_failed=False, now=None):
                raise AssertionError("补生成用例不应走归档入口")

            def repair_artifacts(self, run_id):
                self.calls.append(run_id)
                return ArtifactRepairOutcome(
                    run_id=run_id, repaired=(ArtifactKind.CSV,)
                )

        archival = RepairArchival()
        service = RunHistoryService(
            statuses=StaticStatusPort([_summary()]), archival=archival
        )

        outcome = service.repair_artifacts(RUN_ID)

        self.assertEqual(outcome.repaired, (ArtifactKind.CSV,))
        self.assertEqual(archival.calls, [RUN_ID])

    def test_repair_artifacts_rejects_unknown_or_invalid_runs(self):
        service = RunHistoryService(
            statuses=StaticStatusPort(
                [
                    _summary(),
                    _summary(
                        run_id=OTHER_RUN_ID,
                        state=RunArchiveState.INVALID,
                        problem="manifest 损坏",
                        stages=(),
                        artifacts=(),
                    ),
                ]
            ),
            archival=SimpleNamespace(
                archive=lambda *args, **kwargs: None,
                repair_artifacts=lambda run_id: ArtifactRepairOutcome(run_id=run_id),
            ),
        )

        with self.assertRaisesRegex(RunHistoryRequestError, "不在历史列表中"):
            service.repair_artifacts("20260101T000000Z_ffffffff")
        with self.assertRaisesRegex(RunHistoryRequestError, "不可用"):
            service.repair_artifacts(OTHER_RUN_ID)

    def test_repair_artifacts_requires_the_archival_capability(self):
        service = RunHistoryService(statuses=StaticStatusPort([_summary()]))

        with self.assertRaisesRegex(RunHistoryRequestError, "未装配归档服务"):
            service.repair_artifacts(RUN_ID)

    def test_repair_artifacts_contract_errors_become_history_request_errors(self):
        class BrokenArchival:
            def archive(self, request, *, retry_failed=False, now=None):
                raise AssertionError("补生成用例不应走归档入口")

            def repair_artifacts(self, run_id):
                raise ValueError("manifest 写回失败")

        service = RunHistoryService(
            statuses=StaticStatusPort([_summary()]), archival=BrokenArchival()
        )

        with self.assertRaisesRegex(RunHistoryRequestError, "补生成失败"):
            service.repair_artifacts(RUN_ID)

    def test_constructor_rejects_objects_without_the_port_methods(self):
        with self.assertRaises(TypeError):
            RunHistoryService(statuses=object())
        with self.assertRaises(TypeError):
            RunHistoryService(statuses=StaticStatusPort([]), archival=object())


class StaticResultsPort:
    """固定返回一条历史结果及其已加载 DTO 的查询替身。"""

    def __init__(self, rows=(), loaded=None, *, stage_artifact=None):
        self.rows = tuple(rows)
        self.loaded = dict(loaded or {})
        self.stage_artifact = stage_artifact
        self.stage_artifact_calls = []

    def list_results(self):
        return list(self.rows)

    def load(self, result_id):
        if result_id not in self.loaded:
            raise FileNotFoundError(result_id)
        return self.loaded[result_id]

    def stage_artifact_path(self, stage, measurement_id, kind):
        self.stage_artifact_calls.append((stage, measurement_id, kind))
        return self.stage_artifact


class StaticLocationPort:
    """记录产物定位调用的位置端口替身。"""

    def __init__(self, target=None):
        self.target = target
        self.calls = []

    def artifact_location(self, run_id, kind, *, source_result=None):
        self.calls.append((run_id, kind, source_result))
        return self.target


class ResultReportLocationTests(unittest.TestCase):
    """按已加载结果定位归档报告：页面只消费结构化状态。"""

    def _row(self, result_id, filename, source_result):
        return ResultFileDto(
            result_id=result_id,
            filename=filename,
            file_type="功放测试",
            modified_at=CREATED_AT,
            run_id=RUN_ID,
            source_result=source_result,
        )

    def _amplifier(self, *, measurement_id=None):
        return LoadedResultDto(
            f"run::{RUN_ID}::{AMPLIFIER_RESULT_PATH}",
            "amplifier_measurement.json",
            measurement_result_from_document(
                {
                    **_amplifier_payload(),
                    **(
                        {"measurement_id": measurement_id}
                        if measurement_id is not None
                        else {}
                    ),
                }
            ),
            run_id=RUN_ID,
            source_result=AMPLIFIER_RESULT_PATH,
        )

    def _cable_loss(self):
        return LoadedResultDto(
            f"run::{RUN_ID}::inputs/cable_loss.json",
            "cable_loss.json",
            measurement_result_from_document(
                {
                    "run_id": RUN_ID,
                    "measurement_type": "cable_loss",
                    "status": "completed",
                    "cable_losses": {"1.0": {"cable1": 1.0}},
                }
            ),
            run_id=RUN_ID,
            source_result="inputs/cable_loss.json",
        )

    def test_amplifier_result_locates_the_matching_archived_report(self):
        loaded = self._amplifier()
        location = StaticLocationPort("/runs/report/artifacts/amplifier_report.html")
        service = RunHistoryService(
            statuses=StaticStatusPort([_summary()]),
            locations=location,
            results=StaticResultsPort(
                [self._row(loaded.result_id, loaded.filename, loaded.source_result)],
                {loaded.result_id: loaded},
            ),
        )

        report = service.report_for_result(loaded.result_id)

        self.assertIs(report.state, ResultReportState.AVAILABLE)
        self.assertEqual(report.location, location.target)
        self.assertEqual(
            location.calls, [(RUN_ID, ArtifactKind.REPORT, AMPLIFIER_RESULT_PATH)]
        )

    def test_non_amplifier_result_is_not_applicable_and_does_not_look_up_a_report(self):
        loaded = self._cable_loss()
        location = StaticLocationPort("/runs/report/artifacts/amplifier_report.html")
        service = RunHistoryService(
            statuses=StaticStatusPort([_summary()]),
            locations=location,
            results=StaticResultsPort(
                [self._row(loaded.result_id, loaded.filename, loaded.source_result)],
                {loaded.result_id: loaded},
            ),
        )

        report = service.report_for_result(loaded.result_id)

        self.assertIs(report.state, ResultReportState.NOT_APPLICABLE)
        self.assertIsNone(report.location)
        self.assertIn("主功放", report.reason)
        self.assertEqual(location.calls, [])

    def test_missing_archived_report_is_a_user_visible_state(self):
        loaded = self._amplifier()
        service = RunHistoryService(
            statuses=StaticStatusPort([_summary()]),
            locations=StaticLocationPort(None),
            results=StaticResultsPort(
                [self._row(loaded.result_id, loaded.filename, loaded.source_result)],
                {loaded.result_id: loaded},
            ),
        )

        report = service.report_for_result(loaded.result_id)

        self.assertIs(report.state, ResultReportState.MISSING)
        self.assertIsNone(report.location)
        self.assertIn("另存为", report.reason)

    def test_unknown_result_is_rejected(self):
        service = RunHistoryService(
            statuses=StaticStatusPort([_summary()]),
            locations=StaticLocationPort("/runs/report.html"),
            results=StaticResultsPort(),
        )

        with self.assertRaisesRegex(RunHistoryRequestError, "不在历史列表中"):
            service.report_for_result("run::missing::results/amplifier_measurement.json")

    def test_missing_location_port_is_reported_as_unavailable(self):
        loaded = self._amplifier()
        service = RunHistoryService(
            statuses=StaticStatusPort([_summary()]),
            results=StaticResultsPort(
                [self._row(loaded.result_id, loaded.filename, loaded.source_result)],
                {loaded.result_id: loaded},
            ),
        )

        report = service.report_for_result(loaded.result_id)

        self.assertIs(report.state, ResultReportState.UNAVAILABLE)
        self.assertIn("位置入口", report.reason)

    def test_stage_result_without_report_does_not_fall_back_to_another_results_report(self):
        """阶段结果按 measurement_id 查找同目录报告，不猜该运行的第一份报告。"""
        measurement_id = "20261112T010203Z_abcdef12"
        loaded = self._amplifier(measurement_id=measurement_id)
        row = ResultFileDto(
            result_id=f"stage::amplifier::{measurement_id}",
            filename=f"{measurement_id}.json",
            file_type="功放测试",
            modified_at=CREATED_AT,
            source=RESULT_SOURCE_STAGE,
            run_id=RUN_ID,
        )
        location = StaticLocationPort("/runs/report/artifacts/amplifier_report.html")
        results = StaticResultsPort([row], {row.result_id: loaded})
        service = RunHistoryService(
            statuses=StaticStatusPort([_summary()]),
            locations=location,
            results=results,
        )

        report = service.report_for_result(row.result_id)

        self.assertIs(report.state, ResultReportState.MISSING)
        self.assertIn("另存为", report.reason)
        self.assertEqual(location.calls, [])
        self.assertEqual(
            results.stage_artifact_calls,
            [(MeasurementStage.AMPLIFIER, measurement_id, ArtifactKind.REPORT)],
        )

    def test_stage_result_locates_the_report_in_its_stage_directory(self):
        """步骤 20：阶段结果的报告与正式结果同目录，按 measurement_id 定位。"""
        measurement_id = "20261112T010203Z_abcdef12"
        loaded = self._amplifier(measurement_id=measurement_id)
        row = ResultFileDto(
            result_id=f"stage::amplifier::{measurement_id}",
            filename=f"{measurement_id}.json",
            file_type="功放测试",
            modified_at=CREATED_AT,
            source=RESULT_SOURCE_STAGE,
            run_id=RUN_ID,
        )
        target = f"/results/amplifier/{measurement_id}_report.html"
        results = StaticResultsPort(
            [row], {row.result_id: loaded}, stage_artifact=target
        )
        service = RunHistoryService(
            statuses=StaticStatusPort([_summary()]),
            locations=StaticLocationPort("/runs/report/artifacts/amplifier_report.html"),
            results=results,
        )

        report = service.report_for_result(row.result_id)

        self.assertIs(report.state, ResultReportState.AVAILABLE)
        self.assertEqual(report.location, target)
        self.assertEqual(
            results.stage_artifact_calls,
            [(MeasurementStage.AMPLIFIER, measurement_id, ArtifactKind.REPORT)],
        )

    def test_run_result_with_a_measurement_id_still_uses_its_run_report(self):
        """身份字段存在不等于结果在阶段目录：旧运行结果仍按来源路径定位。"""
        measurement_id = "20261112T010203Z_abcdef12"
        loaded = self._amplifier(measurement_id=measurement_id)
        location = StaticLocationPort("/runs/report/artifacts/amplifier_report.html")
        results = StaticResultsPort(
            [self._row(loaded.result_id, loaded.filename, loaded.source_result)],
            {loaded.result_id: loaded},
            stage_artifact="/stage/another_measurement_report.html",
        )
        service = RunHistoryService(
            statuses=StaticStatusPort([_summary()]),
            locations=location,
            results=results,
        )

        report = service.report_for_result(loaded.result_id)

        self.assertIs(report.state, ResultReportState.AVAILABLE)
        self.assertEqual(report.location, location.target)
        self.assertEqual(
            location.calls, [(RUN_ID, ArtifactKind.REPORT, AMPLIFIER_RESULT_PATH)]
        )
        self.assertEqual(results.stage_artifact_calls, [])

    def test_stage_result_without_measurement_identity_is_unavailable(self):
        """阶段结果缺少测量身份时不能退回运行目录或猜报告归属。"""
        loaded = self._amplifier()
        row = ResultFileDto(
            result_id="stage::amplifier::unknown",
            filename="unknown.json",
            file_type="功放测试",
            modified_at=CREATED_AT,
            source=RESULT_SOURCE_STAGE,
            run_id=RUN_ID,
        )
        location = StaticLocationPort("/runs/report/artifacts/amplifier_report.html")
        results = StaticResultsPort([row], {row.result_id: loaded})
        service = RunHistoryService(
            statuses=StaticStatusPort([_summary()]),
            locations=location,
            results=results,
        )

        report = service.report_for_result(row.result_id)

        self.assertIs(report.state, ResultReportState.UNAVAILABLE)
        self.assertIn("测量身份", report.reason)
        self.assertEqual(location.calls, [])
        self.assertEqual(results.stage_artifact_calls, [])

    def test_run_result_without_a_source_path_is_not_attributed_by_guess(self):
        loaded = self._amplifier()
        row = self._row(loaded.result_id, loaded.filename, None)
        location = StaticLocationPort("/runs/report/artifacts/amplifier_report.html")
        service = RunHistoryService(
            statuses=StaticStatusPort([_summary()]),
            locations=location,
            results=StaticResultsPort([row], {loaded.result_id: loaded}),
        )

        report = service.report_for_result(loaded.result_id)

        self.assertIs(report.state, ResultReportState.UNAVAILABLE)
        self.assertIn("来源路径", report.reason)
        self.assertEqual(location.calls, [])


# -- 基础设施：manifest 摘要 ----------------------------------------------


class RunSummaryFixture(unittest.TestCase):
    """创建临时运行目录，并提供“完成主功放阶段 + 归档”的辅助方法。"""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.paths = PathService(self.base)
        self.runs = RunRepository(self.paths.test_results_dir / "runs")
        self.history = FileRunHistoryRepository(runs=self.runs)
        self._plot_patch = patch.object(
            artifact_generator, "_default_report_plots", _fake_report_plots
        )
        self._plot_patch.start()
        self.addCleanup(self._plot_patch.stop)

    def _create_run(self):
        layout, _manifest = self.runs.create_run({"run_id": RUN_ID}, run_id=RUN_ID)
        recorder = RunStageRecorder(self.runs, RUN_ID)
        recorder.skip(STAGE_CABLE_LOSS, now=CREATED_AT)
        recorder.skip(STAGE_DRIVER_MAPPING, now=CREATED_AT)
        recorder.start(STAGE_AMPLIFIER, now=CREATED_AT)
        recorder.complete(
            STAGE_AMPLIFIER,
            _amplifier_payload(),
            result_path=AMPLIFIER_RESULT_PATH,
            now=CREATED_AT,
        )
        recorder.complete_run(now=CREATED_AT)
        return layout

    def _archive(self, generator):
        return ResultArchivalService(runs=self.runs, generator=generator).archive(
            ArchivalRequest(run_id=RUN_ID, stage=STAGE_AMPLIFIER)
        )

    def _archive_failing(self):
        """用失败端口归档一次：产物登记为 failed，并断言失败被写入应用日志。"""
        with self.assertLogs(ARCHIVAL_LOGGER, level="WARNING"):
            return self._archive(FailingGenerator())


class RunSummaryRepositoryTests(RunSummaryFixture):
    def test_summary_reports_failed_artifacts_from_the_manifest(self):
        self._create_run()
        self._archive_failing()

        [summary] = self.history.list_run_summaries()

        self.assertEqual(summary.run_id, RUN_ID)
        self.assertEqual(summary.state, RunArchiveState.USABLE)
        self.assertEqual(summary.run_status, RunStatus.COMPLETED)
        self.assertEqual(
            [(stage.name, stage.status, stage.result_id) for stage in summary.stages],
            [
                (STAGE_CABLE_LOSS, StageStatus.SKIPPED, None),
                (STAGE_DRIVER_MAPPING, StageStatus.SKIPPED, None),
                (STAGE_AMPLIFIER, StageStatus.COMPLETED, AMPLIFIER_RESULT_PATH),
            ],
        )
        self.assertEqual(
            [(item.kind, item.status, item.stage) for item in summary.artifacts],
            [
                (ArtifactKind.CSV, ArtifactStatus.FAILED, STAGE_AMPLIFIER),
                (ArtifactKind.REPORT, ArtifactStatus.FAILED, STAGE_AMPLIFIER),
            ],
        )
        self.assertTrue(all(item.error for item in summary.artifacts))
        self.assertTrue(summary.can_regenerate)

    def test_regeneration_turns_failed_artifacts_into_completed_ones(self):
        self._create_run()
        self._archive_failing()
        service = RunHistoryService(
            statuses=self.history,
            archival=ResultArchivalService(
                runs=self.runs, generator=ArchiveArtifactGenerator(self.runs)
            ),
        )

        outcome = service.regenerate_failed_artifacts(RUN_ID)

        self.assertIs(outcome.test_status, ArchivalTestStatus.COMPLETED)
        self.assertTrue(
            all(item.status is ArtifactStatus.COMPLETED for item in outcome.artifacts)
        )
        [summary] = self.history.list_run_summaries()
        self.assertFalse(summary.can_regenerate)
        self.assertEqual(
            [item.status for item in summary.artifacts],
            [ArtifactStatus.COMPLETED, ArtifactStatus.COMPLETED],
        )

    def test_missing_completed_artifact_is_reported_without_rewriting_the_manifest(self):
        self._create_run()
        self._archive(ArchiveArtifactGenerator(self.runs))
        csv_path = self.runs.layout(RUN_ID).run_directory / "artifacts" / "amplifier.csv"
        csv_path.unlink()

        [summary] = self.history.list_run_summaries()

        by_kind = {item.kind: item for item in summary.artifacts}
        self.assertTrue(by_kind[ArtifactKind.CSV].missing)
        self.assertFalse(by_kind[ArtifactKind.REPORT].missing)
        # manifest 的产物状态不被只读诊断改写：completed 仍是终态。
        self.assertIs(by_kind[ArtifactKind.CSV].status, ArtifactStatus.COMPLETED)
        self.assertEqual([item.kind for item in summary.missing_artifacts], [ArtifactKind.CSV])
        self.assertFalse(summary.can_regenerate)
        stored = {
            record.kind: record
            for record in self.runs.load_manifest(RUN_ID).artifacts
        }
        self.assertIs(stored[ArtifactKind.CSV].status, ArtifactStatus.COMPLETED)
        self.assertEqual(stored[ArtifactKind.CSV].path, "artifacts/amplifier.csv")
        with self.assertRaises(FileNotFoundError):
            self.runs.verify_artifact_files(RUN_ID)

    def test_incomplete_run_with_intact_artifacts_is_not_marked_missing(self):
        """结构不完整的运行仍按文件事实诊断，不因为缺配置快照就谎报产物缺失。"""
        self._create_run()
        self._archive(ArchiveArtifactGenerator(self.runs))
        run_directory = self.runs.layout(RUN_ID).run_directory
        (run_directory / "config.json").unlink()

        [summary] = self.history.list_run_summaries()

        self.assertEqual(summary.state, RunArchiveState.INCOMPLETE)
        self.assertTrue(summary.artifacts)
        self.assertFalse(any(item.missing for item in summary.artifacts))

    def test_damaged_run_is_listed_as_invalid_without_records(self):
        self._create_run()
        broken = self.runs.root / "20260101T000000Z_ffffffff"
        broken.mkdir(parents=True)
        (broken / "manifest.json").write_text("{not json", encoding="utf-8")

        summaries = self.history.list_run_summaries()

        invalid = [item for item in summaries if item.state is RunArchiveState.INVALID]
        self.assertEqual(len(invalid), 1)
        self.assertFalse(invalid[0].browsable)
        self.assertTrue(invalid[0].problem)
        self.assertEqual(invalid[0].stages, ())
        self.assertEqual(invalid[0].artifacts, ())
        # 可用的运行仍按 manifest 事实列出。
        self.assertTrue(any(item.run_id == RUN_ID for item in summaries))

    def test_run_history_entries_keep_their_previous_counts(self):
        self._create_run()
        self._archive_failing()

        [entry] = self.history.list_runs()

        self.assertEqual(entry.run_id, RUN_ID)
        self.assertEqual(entry.total_stages, 3)
        self.assertEqual(entry.finished_stages, 3)
        self.assertEqual(entry.result_count, 1)


if __name__ == "__main__":
    unittest.main()
