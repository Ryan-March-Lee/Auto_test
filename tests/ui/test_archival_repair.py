"""测量页补生成转发：只调用用例、只把结果交回页面（无 Qt、无文件访问）。"""

import unittest
from types import SimpleNamespace

from application.dto.archival import (
    ArchivalOutcome,
    ArchivalTestStatus,
    ArchivedArtifact,
)
from domain.run.models import ArtifactKind
from presentation.qt.archival_repair import repair_failed_artifacts


RUN_ID = "20260101T000000Z_deadbeef"
FORMAL_RESULT = "results/amplifier_measurement.json"


def _outcome(*artifacts: ArchivedArtifact) -> ArchivalOutcome:
    return ArchivalOutcome(
        run_id=RUN_ID,
        stage="amplifier",
        test_status=ArchivalTestStatus.COMPLETED,
        formal_result_path=FORMAL_RESULT,
        artifacts=artifacts,
    )


def _completed() -> tuple[ArchivedArtifact, ...]:
    return (
        ArchivedArtifact.completed(ArtifactKind.CSV, "artifacts/amplifier.csv"),
        ArchivedArtifact.completed(
            ArtifactKind.REPORT, "artifacts/amplifier_report.html"
        ),
    )


class RecordingPage:
    def __init__(self) -> None:
        self.outcomes = []

    def show_archival(self, outcome) -> None:
        self.outcomes.append(outcome)


class RepairFailedArtifactsTests(unittest.TestCase):
    def test_success_returns_the_outcome_and_updates_the_page(self):
        outcome = _outcome(*_completed())
        page = RecordingPage()
        service = SimpleNamespace(
            regenerate_failed_artifacts=lambda run_id: outcome
        )

        result, problem = repair_failed_artifacts(
            service, RUN_ID, page_for_stage={"amplifier": page}.get
        )

        self.assertIs(result, outcome)
        self.assertIsNone(problem)
        self.assertEqual(page.outcomes, [outcome])

    def test_still_failed_artifacts_are_reported_as_incomplete(self):
        outcome = _outcome(
            ArchivedArtifact.failed(ArtifactKind.CSV, "磁盘空间不足"),
            ArchivedArtifact.completed(
                ArtifactKind.REPORT, "artifacts/amplifier_report.html"
            ),
        )
        page = RecordingPage()
        service = SimpleNamespace(
            regenerate_failed_artifacts=lambda run_id: outcome
        )

        result, problem = repair_failed_artifacts(
            service, RUN_ID, page_for_stage={"amplifier": page}.get
        )

        self.assertIs(result, outcome)
        self.assertIn("仍有交付产物未生成", problem)
        # 页面仍然收到警告摘要，提示条保持可见。
        self.assertEqual(page.outcomes, [outcome])

    def test_missing_service_is_reported_without_touching_the_page(self):
        page = RecordingPage()

        result, problem = repair_failed_artifacts(
            None, RUN_ID, page_for_stage={"amplifier": page}.get
        )

        self.assertIsNone(result)
        self.assertIn("未装配历史运行服务", problem)
        self.assertEqual(page.outcomes, [])

    def test_service_error_becomes_a_user_visible_message(self):
        class _FailingService:
            def regenerate_failed_artifacts(self, run_id):
                raise ValueError("磁盘只读，无法写入产物")

        page = RecordingPage()

        result, problem = repair_failed_artifacts(
            _FailingService(), RUN_ID, page_for_stage={"amplifier": page}.get
        )

        self.assertIsNone(result)
        self.assertEqual(problem, "磁盘只读，无法写入产物")
        self.assertEqual(page.outcomes, [])

    def test_unknown_stage_does_not_fail_the_request(self):
        outcome = _outcome(*_completed())
        service = SimpleNamespace(
            regenerate_failed_artifacts=lambda run_id: outcome
        )
        seen = []

        result, problem = repair_failed_artifacts(
            service, RUN_ID, page_for_stage=lambda stage: seen.append(stage)
        )

        self.assertIs(result, outcome)
        self.assertIsNone(problem)
        self.assertEqual(seen, ["amplifier"])


if __name__ == "__main__":
    unittest.main()
