"""步骤 6：归档结果到页面文案的映射（无 Qt、无文件访问）。"""

import unittest

from application.dto.archival import (
    ArchivalOutcome,
    ArchivalStatus,
    ArchivalTestStatus,
    ArchivedArtifact,
)
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
    summarize_test_cancelled,
    summarize_test_failure,
)
from presentation.qt.measurement_state import MeasurementKind


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
        self.assertEqual(summary.report_path, REPORT_PATH)
        self.assertTrue(summary.can_open_location)
        self.assertTrue(summary.can_open_report)

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
        self.assertIn("重新生成", summary.detail)
        self.assertEqual(summary.report_path, REPORT_PATH)

    def test_all_artifacts_failed_still_points_at_the_run(self):
        outcome = _amplifier_outcome(
            csv=ArchivedArtifact.failed(ArtifactKind.CSV, "写入失败"),
            report=ArchivedArtifact.failed(ArtifactKind.REPORT, "渲染失败"),
        )

        summary = summarize_archival(outcome)

        self.assertEqual(summary.tone, TONE_WARNING)
        self.assertEqual(summary.report_path, None)
        self.assertTrue(summary.can_open_location)
        self.assertFalse(summary.can_open_report)

    def test_pending_artifacts_report_that_generation_continues(self):
        outcome = _amplifier_outcome(
            csv=ArchivedArtifact.pending(ArtifactKind.CSV),
            report=ArchivedArtifact.pending(ArtifactKind.REPORT),
        )

        self.assertIs(outcome.archive_status, ArchivalStatus.PENDING)
        summary = summarize_archival(outcome)

        self.assertEqual(summary.tone, TONE_INFO)
        self.assertIn("仍在生成", summary.headline)
        self.assertFalse(summary.can_open_report)

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
        self.assertFalse(summary.can_open_report)
        self.assertTrue(summary.can_open_location)

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

        self.assertIn("已取消", summary.headline)
        self.assertEqual(summary.tone, TONE_WARNING)
        self.assertFalse(summary.can_open_report)

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
        self.assertIn("无需再手动导出", summary.detail)
        self.assertFalse(summary.can_open_location)

    def test_saving_summary_says_the_result_is_still_being_written(self):
        summary = summarize_archival_saving(MeasurementKind.CABLE_LOSS)

        self.assertEqual(summary.tone, TONE_INFO)
        self.assertIn("正在保存结果", summary.headline)
        self.assertIn("自动更新", summary.detail)
        self.assertFalse(summary.can_open_location)

    def test_missing_outcome_keeps_the_run_identifiable(self):
        summary = summarize_archival_missing(MeasurementKind.CABLE_LOSS, RUN_ID)

        self.assertEqual(summary.tone, TONE_WARNING)
        self.assertIn("未收到归档结果", summary.headline)
        self.assertIn(RUN_ID, summary.detail)
        self.assertTrue(summary.can_open_location)

    def test_missing_outcome_without_run_id_says_status_unknown(self):
        summary = summarize_archival_missing(MeasurementKind.CABLE_LOSS, None)

        self.assertIn("状态未知", summary.detail)
        self.assertFalse(summary.can_open_location)

    def test_failure_summary_carries_the_reason(self):
        summary = summarize_test_failure(MeasurementKind.AMPLIFIER, "仪器无响应")

        self.assertEqual(summary.tone, TONE_ERROR)
        self.assertIn("本次未归档结果", summary.headline)
        self.assertIn("仪器无响应", summary.detail)
        self.assertFalse(summary.can_open_location)

    def test_failure_summary_never_shows_an_empty_reason(self):
        summary = summarize_test_failure(MeasurementKind.AMPLIFIER, "   ")

        self.assertIn("未提供失败原因", summary.detail)

    def test_cancelled_summary_carries_the_stop_reason(self):
        summary = summarize_test_cancelled(MeasurementKind.DRIVER_MAPPING, "任务已取消")

        self.assertEqual(summary.tone, TONE_WARNING)
        self.assertIn("任务已取消", summary.detail)
        self.assertFalse(summary.can_open_location)

    def test_unknown_measurement_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            summarize_archival_running("unknown")

    def test_summary_requires_text_and_a_known_tone(self):
        with self.assertRaises(ValueError):
            ArchivalSummary(headline=" ", detail="说明")
        with self.assertRaises(ValueError):
            ArchivalSummary(headline="标题", detail="说明", tone="purple")
        with self.assertRaises(ValueError):
            ArchivalSummary(
                headline="标题", detail="说明", report_path=REPORT_PATH
            )


if __name__ == "__main__":
    unittest.main()
