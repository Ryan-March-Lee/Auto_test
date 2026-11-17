"""步骤 6：归档结果到页面文案的映射（无 Qt、无文件访问）。"""

import unittest
from pathlib import Path

from application.dto.archival import (
    ArchivalOutcome,
    ArchivalStatus,
    ArchivalTestStatus,
    ArchivedArtifact,
)
from application.dto.measurement_results import MeasurementType
from domain.run.models import ArtifactKind
from presentation.qt.archival_status import (
    TONE_ERROR,
    TONE_INFO,
    TONE_SUCCESS,
    TONE_WARNING,
    ArchivalSummary,
    summarize_archival,
    summarize_archival_missing,
    summarize_archival_running,
    summarize_archival_saving,
    summarize_simulation_completion,
    summarize_simulation_failure,
    summarize_simulation_running,
    summarize_simulation_stopped,
    summarize_test_failure,
    summarize_test_stopped,
)
from presentation.qt.measurement_state import MeasurementKind
from presentation.qt.mock_replay import MOCK_SIMULATION_NOTICE, MockReplayCompletion


RUN_ID = "20260101T000000Z_deadbeef"
CSV_PATH = "artifacts/amplifier.csv"
REPORT_PATH = "artifacts/amplifier_report.html"


def _amplifier_outcome(
    *,
    csv: ArchivedArtifact | None = None,
    report: ArchivedArtifact | None = None,
    test_status: ArchivalTestStatus = ArchivalTestStatus.COMPLETED,
    formal_result_path: str | None = "results/amplifier_measurement.json",
) -> ArchivalOutcome:
    return ArchivalOutcome(
        run_id=RUN_ID,
        stage="amplifier",
        test_status=test_status,
        formal_result_path=formal_result_path,
        artifacts=(
            csv or ArchivedArtifact.completed(ArtifactKind.CSV, CSV_PATH),
            report or ArchivedArtifact.completed(ArtifactKind.REPORT, REPORT_PATH),
        ),
        archived_at="2026-01-01T00:00:00+00:00",
    )


class ArchivalSummaryTests(unittest.TestCase):
    def test_completed_amplifier_offers_location_and_report(self):
        outcome = _amplifier_outcome()

        self.assertIs(outcome.archive_status, ArchivalStatus.COMPLETED)
        summary = summarize_archival(outcome)

        self.assertIn("自动归档", summary.headline)
        self.assertEqual(summary.tone, TONE_SUCCESS)
        self.assertIn(RUN_ID, summary.detail)
        self.assertIn(CSV_PATH, summary.detail)
        self.assertIn(REPORT_PATH, summary.detail)

    def test_failed_artifact_keeps_the_run_id_and_the_report(self):
        outcome = _amplifier_outcome(
            csv=ArchivedArtifact.failed(ArtifactKind.CSV, "磁盘空间不足"),
        )

        self.assertIs(outcome.archive_status, ArchivalStatus.PARTIAL)
        summary = summarize_archival(outcome)

        self.assertIn("部分", summary.headline)
        self.assertEqual(summary.tone, TONE_WARNING)
        self.assertIn("磁盘空间不足", summary.detail)
        self.assertIn(RUN_ID, summary.detail)
        self.assertIn("补生成", summary.detail)
        self.assertTrue(summary.can_regenerate)

    def test_all_artifacts_failed_still_points_at_the_run(self):
        outcome = _amplifier_outcome(
            csv=ArchivedArtifact.failed(ArtifactKind.CSV, "写入失败"),
            report=ArchivedArtifact.failed(ArtifactKind.REPORT, "渲染失败"),
        )

        summary = summarize_archival(outcome)

        self.assertEqual(summary.tone, TONE_WARNING)
        self.assertIn("补生成", summary.detail)
        self.assertTrue(summary.can_regenerate)

    def test_completed_and_stage_only_summaries_offer_no_repair(self):
        """补生成只出现在“产物失败”上；成功与不适用都不提供按钮。"""
        completed = summarize_archival(_amplifier_outcome())
        stage_only = summarize_archival(
            ArchivalOutcome(
                run_id=RUN_ID,
                stage="cable_loss",
                test_status=ArchivalTestStatus.PARTIAL,
                formal_result_path="inputs/cable_loss.json",
                artifacts=(
                    ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                    ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
                ),
            )
        )

        self.assertFalse(completed.can_regenerate)
        self.assertFalse(stage_only.can_regenerate)

    def test_pending_artifacts_report_that_generation_continues(self):
        outcome = _amplifier_outcome(
            csv=ArchivedArtifact.pending(ArtifactKind.CSV),
            report=ArchivedArtifact.pending(ArtifactKind.REPORT),
        )

        self.assertIs(outcome.archive_status, ArchivalStatus.PENDING)
        summary = summarize_archival(outcome)

        self.assertEqual(summary.tone, TONE_INFO)
        self.assertIn("仍在生成", summary.headline)

    def test_stage_only_run_is_archived_without_deliverables(self):
        outcome = ArchivalOutcome(
            run_id=RUN_ID,
            stage="cable_loss",
            test_status=ArchivalTestStatus.PARTIAL,
            formal_result_path="inputs/cable_loss.json",
            artifacts=(
                ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
            ),
        )

        summary = summarize_archival(outcome)

        self.assertEqual(summary.tone, TONE_SUCCESS)
        self.assertIn("阶段结果已自动归档", summary.headline)
        self.assertIn("inputs/cable_loss.json", summary.detail)
        self.assertIn(RUN_ID, summary.detail)

    def test_cancelled_archive_has_no_artifacts_and_no_report(self):
        outcome = ArchivalOutcome(
            run_id=RUN_ID,
            stage="amplifier",
            test_status=ArchivalTestStatus.CANCELLED,
            artifacts=(
                ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
            ),
        )

        self.assertIs(outcome.archive_status, ArchivalStatus.NOT_APPLICABLE)
        summary = summarize_archival(outcome)

        self.assertIn("已停止", summary.headline)
        self.assertEqual(summary.tone, TONE_WARNING)

    def test_failed_archive_distinguishes_saved_and_missing_results(self):
        without_result = ArchivalOutcome(
            run_id=RUN_ID,
            stage="driver_mapping",
            test_status=ArchivalTestStatus.FAILED,
            artifacts=(
                ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
            ),
        )
        with_result = ArchivalOutcome(
            run_id=RUN_ID,
            stage="driver_mapping",
            test_status=ArchivalTestStatus.FAILED,
            formal_result_path="inputs/driver_mapping.json",
            artifacts=(
                ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
            ),
        )

        missing = summarize_archival(without_result)
        kept = summarize_archival(with_result)

        self.assertEqual(missing.tone, TONE_ERROR)
        self.assertIn("没有保存正式结果", missing.detail)
        self.assertEqual(kept.tone, TONE_ERROR)
        self.assertIn("inputs/driver_mapping.json", kept.detail)

    def test_non_formal_payload_is_rejected(self):
        with self.assertRaises(TypeError):
            summarize_archival({"run_id": RUN_ID})


class ArchivalFallbackSummaryTests(unittest.TestCase):
    def test_running_summary_explains_automatic_archival(self):
        summary = summarize_archival_running(MeasurementKind.AMPLIFIER)

        self.assertEqual(summary.tone, TONE_INFO)
        self.assertIn("自动归档", summary.headline)
        self.assertIn("独立结果目录", summary.detail)
        self.assertIn("无需再手动导出", summary.detail)

    def test_saving_summary_says_the_result_is_still_being_written(self):
        summary = summarize_archival_saving(MeasurementKind.CABLE_LOSS)

        self.assertEqual(summary.tone, TONE_INFO)
        self.assertIn("正在保存结果", summary.headline)
        self.assertIn("正式结果保存", summary.detail)
        self.assertIn("自动更新", summary.detail)

    def test_missing_outcome_keeps_the_run_identifiable(self):
        summary = summarize_archival_missing(MeasurementKind.CABLE_LOSS, RUN_ID)

        self.assertEqual(summary.tone, TONE_WARNING)
        self.assertIn("未收到归档结果", summary.headline)
        self.assertIn(RUN_ID, summary.detail)

    def test_missing_outcome_without_run_id_says_status_unknown(self):
        summary = summarize_archival_missing(MeasurementKind.CABLE_LOSS, None)

        self.assertIn("状态未知", summary.detail)

    def test_failure_summary_carries_the_reason(self):
        summary = summarize_test_failure(MeasurementKind.AMPLIFIER, "仪器无响应")

        self.assertEqual(summary.tone, TONE_ERROR)
        self.assertIn("本次未归档结果", summary.headline)
        self.assertIn("仪器无响应", summary.detail)

    def test_failure_summary_never_shows_an_empty_reason(self):
        summary = summarize_test_failure(MeasurementKind.AMPLIFIER, "   ")

        self.assertIn("未提供失败原因", summary.detail)

    def test_stopped_summary_carries_the_stop_reason(self):
        summary = summarize_test_stopped(MeasurementKind.DRIVER_MAPPING, "用户停止")

        self.assertEqual(summary.tone, TONE_WARNING)
        self.assertIn("用户停止", summary.detail)

    def test_stopped_summary_reports_a_saved_partial_result(self):
        summary = summarize_test_stopped(
            MeasurementKind.DRIVER_MAPPING,
            "用户停止",
            "C:/runs/run/results/driver_mapping_partial.json",
        )

        self.assertIn("已保存部分结果", summary.headline)
        self.assertIn("driver_mapping_partial.json", summary.detail)

    def test_unknown_measurement_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            summarize_archival_running("unknown")

    def test_summary_requires_text_and_a_known_tone(self):
        with self.assertRaises(ValueError):
            ArchivalSummary(headline=" ", detail="说明")
        with self.assertRaises(ValueError):
            ArchivalSummary(headline="标题", detail="说明", tone="purple")
        with self.assertRaises(ValueError):
            ArchivalSummary(headline="标题", detail="说明", run_id="  ")
        with self.assertRaises(ValueError):
            ArchivalSummary(
                headline="标题", detail="说明", can_regenerate=True
            )


class SimulationSummaryTests(unittest.TestCase):
    """步骤 13：模拟回放只展示数据，文案不能冒充正式归档。"""

    def _completion(self):
        return MockReplayCompletion(
            measurement_type=MeasurementType.CABLE_LOSS,
            title="线损模拟",
            result_filename="20261117T060245Z_9b2e7d10.json",
            result_path=Path("mock_data/cable_loss/20261117T060245Z_9b2e7d10.json"),
        )

    def test_running_summary_state_the_mock_boundary_and_interval(self):
        summary = summarize_simulation_running(MeasurementKind.CABLE_LOSS)

        self.assertEqual(summary.tone, TONE_INFO)
        self.assertIn("模拟回放开始", summary.headline)
        self.assertIn("0.5 秒", summary.detail)
        self.assertIn(MOCK_SIMULATION_NOTICE, summary.detail)

    def test_completion_summary_names_the_mock_result_and_never_archive(self):
        summary = summarize_simulation_completion(
            MeasurementKind.CABLE_LOSS, self._completion()
        )

        self.assertEqual(summary.tone, TONE_INFO)
        self.assertIn("模拟数据已加载", summary.headline)
        self.assertIn("20261117T060245Z_9b2e7d10.json", summary.detail)
        self.assertIn("线损模拟", summary.detail)
        self.assertIn(MOCK_SIMULATION_NOTICE, summary.detail)
        self.assertNotIn("自动归档", summary.headline)
        self.assertIsNone(summary.run_id)

    def test_completion_summary_rejects_a_foreign_payload(self):
        with self.assertRaises(TypeError):
            summarize_simulation_completion(
                MeasurementKind.CABLE_LOSS, object()
            )

    def test_stopped_and_failed_summaries_are_distinct(self):
        stopped = summarize_simulation_stopped(MeasurementKind.AMPLIFIER, "用户停止")
        failed = summarize_simulation_failure(MeasurementKind.AMPLIFIER, "数据包损坏")

        self.assertEqual(stopped.tone, TONE_WARNING)
        self.assertIn("模拟回放已停止", stopped.headline)
        self.assertIn("用户停止", stopped.detail)
        self.assertIn(MOCK_SIMULATION_NOTICE, stopped.detail)
        self.assertEqual(failed.tone, TONE_ERROR)
        self.assertIn("模拟回放失败", failed.headline)
        self.assertIn("数据包损坏", failed.detail)
        self.assertIn(MOCK_SIMULATION_NOTICE, failed.detail)


if __name__ == "__main__":
    unittest.main()
