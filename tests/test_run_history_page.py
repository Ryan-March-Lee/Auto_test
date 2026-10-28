"""步骤 7：历史运行/结果管理页（原导出页）的契约与页面行为测试。

覆盖：

- 历史运行 DTO：可用性三档、非法组合拒绝、``can_regenerate`` 只在“结构完整 + 有可定位
  失败产物”时成立；
- ``RunHistoryService``：只接受历史列表中的运行标识，重新生成走归档服务的
  ``retry_failed=True``，未装配归档服务/没有失败产物/运行不可用都明确失败；
- ``FileRunHistoryRepository.list_run_summaries``：从 manifest 读出运行/阶段/产物状态，
  损坏运行只给可用性与原因；
- ``HistoryViewModel``：运行摘要转成展示文本，选择清空规则；
- ``ExportPage``：显示运行与产物状态、按失败产物启用“重新生成”、不可用运行禁止导出，
  导出副本按**结果所属运行**判定可用性（两张表格各选一行时不会张冠李戴）。
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from application.archival_service import ResultArchivalService
from application.dto.archival import (
    ArchivalOutcome,
    ArchivalRequest,
    ArchivalTestStatus,
    ArchivedArtifact,
)
from application.dto.run_history import (
    RunArchiveState,
    RunArtifactSummary,
    RunHistorySummary,
    RunStageSummary,
)
from application.export_service import ResultExportUseCase
from application.ports.result_artifacts import ArtifactGenerationError
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
from infrastructure.gui_data_service import ResultExportAdapter, ResultQueryAdapter
from infrastructure.persistence.artifact_generator import ArchiveArtifactGenerator
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
from infrastructure.persistence.run_repository import RunRepository
from presentation.qt.export_page import ExportPage
from presentation.qt.history_view_model import HistoryViewModel, RunHistoryRow


RUN_ID = "20261028T010203Z_abcdef12"
OTHER_RUN_ID = "20261027T010203Z_abcdef12"
CREATED_AT = datetime(2026, 10, 28, 1, 2, 3, tzinfo=timezone.utc)


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

    def test_archive_contract_errors_become_history_request_errors(self):
        class BrokenArchival:
            def archive(self, request, *, retry_failed=False, now=None):
                raise ValueError("manifest 写回失败")

        service = RunHistoryService(
            statuses=StaticStatusPort([_summary()]), archival=BrokenArchival()
        )

        with self.assertRaisesRegex(RunHistoryRequestError, "重新生成失败"):
            service.regenerate_failed_artifacts(RUN_ID)

    def test_constructor_rejects_objects_without_the_port_methods(self):
        with self.assertRaises(TypeError):
            RunHistoryService(statuses=object())
        with self.assertRaises(TypeError):
            RunHistoryService(statuses=StaticStatusPort([]), archival=object())


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
        with self.assertLogs("application.archival_service", level="WARNING"):
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


# -- 视图模型 -------------------------------------------------------------


class RunHistoryViewModelTests(unittest.TestCase):
    def _view_model(self, summaries):
        class EmptyQuery:
            """只需要满足视图模型构造的只读查询替身。"""

            def list_results(self):
                return []

        return HistoryViewModel(EmptyQuery(), runs=StaticStatusPort(summaries))

    def test_run_rows_are_rendered_as_display_text(self):
        view_model = self._view_model([_summary()])

        [row] = view_model.reload_runs()

        self.assertIsInstance(row, RunHistoryRow)
        self.assertEqual(row.run_id, RUN_ID)
        self.assertEqual(row.status_text, "已完成")
        self.assertEqual(row.stage_text, "1/1 已完成")
        self.assertEqual(row.result_text, "1 个正式结果")
        self.assertEqual(row.artifact_text, "CSV 失败")
        self.assertEqual(row.problem_text, "CSV 失败: 磁盘已满")
        self.assertTrue(row.can_regenerate)
        self.assertTrue(row.browsable)

    def test_invalid_run_shows_state_and_problem(self):
        view_model = self._view_model(
            [
                _summary(
                    state=RunArchiveState.INVALID,
                    run_status=None,
                    stages=(),
                    artifacts=(),
                    problem="manifest 损坏",
                )
            ]
        )

        [row] = view_model.reload_runs()

        self.assertEqual(row.status_text, "状态未知（不可用）")
        self.assertEqual(row.stage_text, "—")
        self.assertEqual(row.artifact_text, "无交付产物")
        self.assertEqual(row.problem_text, "manifest 损坏")
        self.assertFalse(row.browsable)
        self.assertFalse(row.can_regenerate)

    def test_selection_is_cleared_when_the_run_disappears(self):
        view_model = self._view_model([_summary()])
        view_model.reload_runs()
        self.assertIsNotNone(view_model.select_run(0))

        view_model.run_source.summaries = ()
        view_model.reload_runs()

        self.assertIsNone(view_model.selected_run)
        self.assertIsNone(view_model.selected_run_index)
        self.assertIsNone(view_model.select_run(5))

    def test_reload_runs_requires_a_summary_source(self):
        class EmptyQuery:
            def list_results(self):
                return []

        view_model = HistoryViewModel(EmptyQuery())

        with self.assertRaises(RuntimeError):
            view_model.reload_runs()


# -- 页面 -----------------------------------------------------------------


class ExportPageHistoryTests(unittest.TestCase):
    def setUp(self):
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.paths = PathService(self.root)
        self.runs = RunRepository(self.paths.test_results_dir / "runs")
        self.history = FileRunHistoryRepository(runs=self.runs)
        # 离屏环境里任何模态对话框都会永久阻塞：统一替身并记录调用。
        for name in ("warning", "information", "critical"):
            patcher = patch(f"presentation.qt.export_page.QMessageBox.{name}")
            setattr(self, f"{name}_dialog", patcher.start())
            self.addCleanup(patcher.stop)

    def _complete_amplifier_run(self, run_id=RUN_ID):
        self.runs.create_run({"run_id": run_id}, run_id=run_id)
        recorder = RunStageRecorder(self.runs, run_id)
        recorder.skip(STAGE_CABLE_LOSS, now=CREATED_AT)
        recorder.skip(STAGE_DRIVER_MAPPING, now=CREATED_AT)
        recorder.start(STAGE_AMPLIFIER, now=CREATED_AT)
        recorder.complete(
            STAGE_AMPLIFIER,
            _amplifier_payload(run_id=run_id),
            result_path=AMPLIFIER_RESULT_PATH,
            now=CREATED_AT,
        )
        recorder.complete_run(now=CREATED_AT)

    def _archive(self, generator):
        return ResultArchivalService(runs=self.runs, generator=generator).archive(
            ArchivalRequest(run_id=RUN_ID, stage=STAGE_AMPLIFIER)
        )

    def _archive_failing(self):
        with self.assertLogs("application.archival_service", level="WARNING"):
            return self._archive(FailingGenerator())

    def _query(self):
        return ResultQueryAdapter(
            self.paths,
            run_history=self.history,
            runs_root=self.paths.test_results_dir / "runs",
        )

    def _page(self):
        query = self._query()
        service = RunHistoryService(
            statuses=self.history,
            archival=ResultArchivalService(
                runs=self.runs, generator=ArchiveArtifactGenerator(self.runs)
            ),
        )
        return ExportPage(
            history=HistoryViewModel(query, runs=service),
            export_service=ResultExportUseCase(
                history=query, exports=ResultExportAdapter(query, self.paths)
            ),
            run_service=service,
        )

    def _run_row_index(self, page, run_id: str) -> int:
        for index in range(page.run_table.rowCount()):
            if page.run_table.item(index, 0).text() == run_id:
                return index
        raise AssertionError(f"运行表格里没有 {run_id}")

    def _file_row_index(self, page, run_id: str) -> int:
        for index in range(page.file_table.rowCount()):
            if page.file_table.item(index, 2).text() == f"运行 {run_id}":
                return index
        raise AssertionError(f"结果表格里没有运行 {run_id} 的结果")

    def test_page_shows_run_and_artifact_state(self):
        self._complete_amplifier_run()
        self._archive_failing()

        page = self._page()

        self.assertEqual(page.run_table.rowCount(), 1)
        self.assertEqual(page.run_table.item(0, 0).text(), RUN_ID)
        self.assertTrue(page.run_table.item(0, 1).text())
        self.assertEqual(page.run_table.item(0, 2).text(), "已完成")
        self.assertEqual(page.run_table.item(0, 3).text(), "3/3 已完成")
        self.assertEqual(page.run_table.item(0, 4).text(), "1 个正式结果")
        self.assertEqual(page.run_table.item(0, 5).text(), "CSV 失败、报告 失败")
        self.assertIn("CSV 失败", page.run_table.item(0, 6).text())
        self.assertIn("故意失败", page.run_table.item(0, 6).text())
        self.assertFalse(page.regenerate_button.isEnabled())

        page.run_table.setCurrentCell(0, 0)
        self.assertTrue(page.regenerate_button.isEnabled())

    def test_regenerate_button_is_disabled_for_completed_artifacts(self):
        self._complete_amplifier_run()
        self._archive(ArchiveArtifactGenerator(self.runs))

        page = self._page()
        page.run_table.setCurrentCell(0, 0)

        self.assertFalse(page.regenerate_button.isEnabled())
        self.assertFalse(page.regenerate_failed_artifacts())
        self.warning_dialog.assert_called_once()

    def test_regenerate_writes_artifacts_into_the_run_directory(self):
        self._complete_amplifier_run()
        self._archive_failing()
        page = self._page()
        page.run_table.setCurrentCell(0, 0)

        with patch.object(page, "refresh") as refresh:
            self.assertTrue(page.regenerate_failed_artifacts())

        run_dir = self.runs.layout(RUN_ID).run_directory
        self.assertTrue((run_dir / "artifacts" / "amplifier.csv").is_file())
        self.assertTrue((run_dir / "artifacts" / "amplifier_report.html").is_file())
        message = self.information_dialog.call_args.args[2]
        self.assertIn(RUN_ID, message)
        self.assertIn("amplifier.csv", message)
        refresh.assert_called_once_with()

    def test_invalid_run_cannot_export_results(self):
        self._complete_amplifier_run()
        broken = self.runs.root / "20260101T000000Z_ffffffff"
        broken.mkdir(parents=True)
        (broken / "manifest.json").write_text("{not json", encoding="utf-8")
        page = self._page()
        self.assertEqual(page.run_table.rowCount(), 2)
        invalid_row = next(
            index
            for index in range(page.run_table.rowCount())
            if "不可用" in page.run_table.item(index, 2).text()
        )
        page.run_table.setCurrentCell(invalid_row, 0)
        page.file_table.setCurrentCell(0, 0)

        with patch(
            "presentation.qt.export_page.QFileDialog.getSaveFileName"
        ) as dialog:
            self.assertFalse(page.export_json())

        dialog.assert_not_called()
        self.warning_dialog.assert_called_once()
        # 无效运行本身不产生历史结果行：结果列表里只有可用运行的结果。
        self.assertEqual(page.file_table.rowCount(), 1)
        self.assertIn("不可用", self.warning_dialog.call_args.args[2])

    def test_export_refuses_a_result_that_belongs_to_another_run(self):
        """两张表格可以各选一行；导出必须按结果所属运行判定，而不是按选中运行放行。"""
        self._complete_amplifier_run()
        self._complete_amplifier_run(OTHER_RUN_ID)
        page = self._page()
        page.run_table.setCurrentCell(self._run_row_index(page, RUN_ID), 0)
        page.file_table.setCurrentCell(self._file_row_index(page, OTHER_RUN_ID), 0)

        with patch(
            "presentation.qt.export_page.QFileDialog.getSaveFileName"
        ) as dialog:
            self.assertFalse(page.export_json())

        dialog.assert_not_called()
        self.warning_dialog.assert_called_once()
        self.assertIn("不是同一次运行", self.warning_dialog.call_args.args[2])

    def test_export_refuses_a_result_whose_run_is_not_listed(self):
        """运行摘要里查不到结果所属运行时不导出：不能凭结果行绕过运行级判定。"""
        self._complete_amplifier_run()
        query = self._query()
        page = ExportPage(
            history=HistoryViewModel(query, runs=StaticStatusPort([])),
            export_service=ResultExportUseCase(
                history=query, exports=ResultExportAdapter(query, self.paths)
            ),
        )
        page.file_table.setCurrentCell(0, 0)

        with patch(
            "presentation.qt.export_page.QFileDialog.getSaveFileName"
        ) as dialog:
            self.assertFalse(page.export_json())

        dialog.assert_not_called()
        self.warning_dialog.assert_called_once()
        self.assertIn("不在历史运行列表中", self.warning_dialog.call_args.args[2])

    def test_export_without_a_run_selection_uses_the_result_owner(self):
        """未选运行行时以结果所属运行的可用性为准，正常结果仍可导出副本。"""
        self._complete_amplifier_run()
        page = self._page()
        page.file_table.setCurrentCell(0, 0)
        target = self.root / "copy.json"

        with patch(
            "presentation.qt.export_page.QFileDialog.getSaveFileName",
            return_value=(str(target), "JSON files (*.json)"),
        ):
            self.assertTrue(page.export_json())

        self.assertTrue(target.is_file())

    def test_incomplete_run_is_listed_and_its_verified_result_is_exportable(self):
        """结构不完整的运行仍显示、仍可导出已校验的正式结果（不伪装成完整成功）。"""
        self._complete_amplifier_run()
        run_directory = self.runs.layout(RUN_ID).run_directory
        (run_directory / "config.json").unlink()

        page = self._page()

        self.assertEqual(page.run_table.rowCount(), 1)
        self.assertIn("结构不完整", page.run_table.item(0, 2).text())
        self.assertIn("运行目录结构不完整", page.run_table.item(0, 6).text())
        page.run_table.setCurrentCell(0, 0)
        page.file_table.setCurrentCell(0, 0)
        target = self.root / "copy.json"

        with patch(
            "presentation.qt.export_page.QFileDialog.getSaveFileName",
            return_value=(str(target), "JSON files (*.json)"),
        ):
            self.assertTrue(page.export_json())

        self.assertTrue(target.is_file())

    def test_run_list_failure_keeps_the_page_usable(self):
        """运行摘要读取失败不能让页面构造失败：清空运行表、写日志，结果列表仍可用。"""
        self._complete_amplifier_run()
        query = self._query()
        messages = []
        page = ExportPage(
            history=HistoryViewModel(
                query,
                runs=StaticStatusPort([], failure=OSError("运行根目录不可读")),
            ),
            export_service=ResultExportUseCase(
                history=query, exports=ResultExportAdapter(query, self.paths)
            ),
            log_callback=messages.append,
        )

        self.assertEqual(page.run_table.rowCount(), 0)
        self.assertFalse(page.regenerate_button.isEnabled())
        self.assertEqual(page.file_table.rowCount(), 1)
        self.assertTrue(any("读取历史运行失败" in message for message in messages))

    def test_export_copy_still_works_for_browsable_runs(self):
        self._complete_amplifier_run()
        page = self._page()
        page.run_table.setCurrentCell(0, 0)
        page.file_table.setCurrentCell(0, 0)
        target = self.root / "copy.json"

        with patch(
            "presentation.qt.export_page.QFileDialog.getSaveFileName",
            return_value=(str(target), "JSON files (*.json)"),
        ):
            self.assertTrue(page.export_json())

        self.assertTrue(target.is_file())

    def test_page_without_run_service_only_lists_results(self):
        self._complete_amplifier_run()
        query = self._query()
        page = ExportPage(
            history=HistoryViewModel(query),
            export_service=ResultExportUseCase(
                history=query, exports=ResultExportAdapter(query, self.paths)
            ),
        )

        self.assertEqual(page.run_table.rowCount(), 0)
        self.assertFalse(page.regenerate_button.isEnabled())
        self.assertEqual(page.file_table.rowCount(), 1)


class RegenerationMessageTests(unittest.TestCase):
    """页面文案：逐个产物说明结果，部分仍失败时不说成“全部完成”。"""

    def test_message_reports_each_artifact_state(self):
        outcome = ArchivalOutcome(
            run_id=RUN_ID,
            stage=STAGE_AMPLIFIER,
            test_status=ArchivalTestStatus.COMPLETED,
            formal_result_path=AMPLIFIER_RESULT_PATH,
            artifacts=(
                ArchivedArtifact.completed(ArtifactKind.CSV, "artifacts/amplifier.csv"),
                ArchivedArtifact.failed(ArtifactKind.REPORT, "模板缺失"),
            ),
        )

        message = ExportPage._regeneration_message(outcome)

        self.assertIn("CSV 已生成: artifacts/amplifier.csv", message)
        self.assertIn("报告 未生成: 模板缺失", message)

    def test_message_rejects_non_dto_outcomes(self):
        with self.assertRaises(TypeError):
            ExportPage._regeneration_message(object())


class CompositionWiringTests(unittest.TestCase):
    """组合根把历史管理能力真正接进 GUI 数据门面（页面不再自己装配）。"""

    def setUp(self):
        from PySide6.QtWidgets import QApplication

        self.app = QApplication.instance() or QApplication([])

    def test_facade_exposes_the_history_service_and_builds_the_page(self):
        from presentation.qt.composition import build_gui_data_service
        from presentation.qt.export_page import build_history_page

        with tempfile.TemporaryDirectory() as directory:
            service = build_gui_data_service(PathService(Path(directory)))
            page = build_history_page(service, log_callback=lambda _message: None)

        self.assertIsInstance(service.run_history, RunHistoryService)
        self.assertEqual(service.run_history.list_runs(), ())
        self.assertEqual(page.run_table.rowCount(), 0)


if __name__ == "__main__":
    unittest.main()
