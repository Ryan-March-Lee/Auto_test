"""删除历史运行：应用用例与运行目录删除适配器。

覆盖：

- ``RunHistoryService.delete_runs`` 只删除历史列表中非进行中的运行，未知运行、
  进行中运行与单个删除失败按运行逐项返回，不中断同批次其它运行；
- 空列表、单个字符串与非法标识在删除前直接拒绝；
- ``FileRunDeletion`` 只删除一个运行目录，拒绝符号链接/目录联接与进行中的运行，
  目录缺失时幂等，损坏 manifest 的目录仍可清理；
- 组合根把删除入口接进历史运行服务。
"""

import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from application.dto.run_deletion import RunDeletionFailure, RunDeletionOutcome
from application.dto.run_history import RunArchiveState, RunHistorySummary
from application.run_history_service import RunHistoryRequestError, RunHistoryService
from application.run_stages import RunStageRecorder
from domain.run.models import STAGE_CABLE_LOSS, RunStatus
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence.run_deletion import FileRunDeletion
from infrastructure.persistence.run_repository import RunRepository

RUN_ID = "20261028T010203Z_abcdef12"
OTHER_RUN_ID = "20261027T010203Z_abcdef12"
CREATED_AT = datetime(2026, 10, 28, 1, 2, 3, tzinfo=timezone.utc)


def _summary(
    run_id: str = RUN_ID,
    *,
    run_status: RunStatus | None = RunStatus.COMPLETED,
) -> RunHistorySummary:
    return RunHistorySummary(
        run_id=run_id,
        created_at=CREATED_AT,
        state=RunArchiveState.USABLE,
        run_status=run_status,
    )


class _StaticStatusPort:
    def __init__(self, summaries):
        self.summaries = tuple(summaries)

    def list_run_summaries(self):
        return self.summaries


class _RecordingDeletion:
    """记录删除调用；可让指定运行抛出异常。"""

    def __init__(self, *, failing=()):
        self.calls = []
        self.failing = set(failing)

    def delete_run(self, run_id):
        self.calls.append(run_id)
        if run_id in self.failing:
            raise ValueError(f"模拟删除失败: {run_id}")


class RunDeletionDtoTests(unittest.TestCase):
    def test_outcome_reports_deleted_and_failed_runs(self):
        failure = RunDeletionFailure(RUN_ID, "运行正在进行中")
        outcome = RunDeletionOutcome((OTHER_RUN_ID,), (failure,))

        self.assertEqual(outcome.deleted_count, 1)
        self.assertEqual(outcome.failed_count, 1)
        self.assertTrue(outcome.has_failures)

    def test_same_run_cannot_be_deleted_and_failed(self):
        with self.assertRaises(ValueError):
            RunDeletionOutcome(
                (RUN_ID,), (RunDeletionFailure(RUN_ID, "模拟失败"),)
            )

    def test_duplicate_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            RunDeletionOutcome((RUN_ID, RUN_ID))
        with self.assertRaises(ValueError):
            RunDeletionOutcome(
                (),
                (
                    RunDeletionFailure(RUN_ID, "失败一"),
                    RunDeletionFailure(RUN_ID, "失败二"),
                ),
            )

    def test_blank_fields_are_rejected(self):
        with self.assertRaises(ValueError):
            RunDeletionFailure(" ", "失败")
        with self.assertRaises(ValueError):
            RunDeletionFailure(RUN_ID, " ")
        with self.assertRaises(ValueError):
            RunDeletionOutcome((RUN_ID, " "))


class RunHistoryDeletionServiceTests(unittest.TestCase):
    def _service(self, summaries, *, deletion=None):
        return RunHistoryService(
            statuses=_StaticStatusPort(summaries),
            deletions=deletion if deletion is not None else _RecordingDeletion(),
        )

    def test_deletes_terminal_runs_and_skips_running_runs(self):
        deletion = _RecordingDeletion()
        service = self._service(
            [
                _summary(),
                _summary(OTHER_RUN_ID, run_status=RunStatus.RUNNING),
            ],
            deletion=deletion,
        )

        outcome = service.delete_runs([RUN_ID, OTHER_RUN_ID])

        self.assertEqual(outcome.deleted_run_ids, (RUN_ID,))
        self.assertEqual(
            [(item.run_id, item.reason) for item in outcome.failures],
            [(OTHER_RUN_ID, "运行正在进行中，不能删除")],
        )
        # 进行中的运行不会进入删除端口。
        self.assertEqual(deletion.calls, [RUN_ID])

    def test_unknown_run_is_reported_without_blocking_other_deletions(self):
        deletion = _RecordingDeletion()
        service = self._service(
            [_summary(), _summary(OTHER_RUN_ID)],
            deletion=deletion,
        )

        outcome = service.delete_runs([RUN_ID, "20260101T000000Z_ffffffff"])

        self.assertEqual(outcome.deleted_run_ids, (RUN_ID,))
        self.assertEqual(
            outcome.failures[0].reason, "运行不在历史列表中"
        )
        self.assertEqual(deletion.calls, [RUN_ID])

    def test_invalid_and_incomplete_runs_are_deletable(self):
        invalid = RunHistorySummary(
            run_id=RUN_ID,
            created_at=CREATED_AT,
            state=RunArchiveState.INVALID,
            problem="manifest 损坏",
        )
        incomplete = RunHistorySummary(
            run_id=OTHER_RUN_ID,
            created_at=CREATED_AT,
            state=RunArchiveState.INCOMPLETE,
            run_status=RunStatus.CREATED,
            problem="运行目录结构不完整",
        )
        deletion = _RecordingDeletion()
        service = self._service([invalid, incomplete], deletion=deletion)

        outcome = service.delete_runs([RUN_ID, OTHER_RUN_ID])

        self.assertEqual(outcome.deleted_run_ids, (RUN_ID, OTHER_RUN_ID))
        self.assertFalse(outcome.has_failures)

    def test_single_deletion_failure_does_not_block_the_rest(self):
        deletion = _RecordingDeletion(failing={RUN_ID})
        service = self._service(
            [_summary(), _summary(OTHER_RUN_ID)], deletion=deletion
        )

        outcome = service.delete_runs([RUN_ID, OTHER_RUN_ID])

        self.assertEqual(outcome.deleted_run_ids, (OTHER_RUN_ID,))
        self.assertEqual(outcome.failures[0].run_id, RUN_ID)
        self.assertIn("模拟删除失败", outcome.failures[0].reason)

    def test_duplicate_ids_are_deleted_once(self):
        deletion = _RecordingDeletion()
        service = self._service([_summary()], deletion=deletion)

        outcome = service.delete_runs([RUN_ID, RUN_ID])

        self.assertEqual(outcome.deleted_run_ids, (RUN_ID,))
        self.assertEqual(deletion.calls, [RUN_ID])

    def test_empty_or_single_string_input_is_rejected_before_deleting(self):
        deletion = _RecordingDeletion()
        service = self._service([_summary()], deletion=deletion)

        for value in ([], (), RUN_ID, [RUN_ID, " "]):
            with self.subTest(value=value):
                with self.assertRaises(RunHistoryRequestError):
                    service.delete_runs(value)
        self.assertEqual(deletion.calls, [])

    def test_missing_deletion_port_is_reported(self):
        service = RunHistoryService(statuses=_StaticStatusPort([_summary()]))

        with self.assertRaisesRegex(RunHistoryRequestError, "未装配运行删除入口"):
            service.delete_runs([RUN_ID])

    def test_constructor_rejects_objects_without_delete_run(self):
        with self.assertRaises(TypeError):
            RunHistoryService(
                statuses=_StaticStatusPort([]), deletions=object()
            )


class FileRunDeletionTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.runs = RunRepository(self.base / "runs")
        self.deletion = FileRunDeletion(runs=self.runs)

    def _create_run(self, run_id=RUN_ID):
        self.runs.create_run({"run_id": run_id}, run_id=run_id)
        return self.runs.layout(run_id).run_directory

    def test_deletes_only_the_selected_run_directory(self):
        selected = self._create_run(RUN_ID)
        other = self._create_run(OTHER_RUN_ID)

        self.deletion.delete_run(RUN_ID)

        self.assertFalse(selected.exists())
        self.assertTrue(other.is_dir())

    def test_missing_directory_is_idempotent(self):
        self.deletion.delete_run(RUN_ID)
        self.deletion.delete_run(RUN_ID)

    def test_running_run_is_rejected(self):
        directory = self._create_run(RUN_ID)
        RunStageRecorder(self.runs, RUN_ID).start(STAGE_CABLE_LOSS)

        with self.assertRaisesRegex(ValueError, "正在进行中"):
            self.deletion.delete_run(RUN_ID)

        self.assertTrue(directory.is_dir())
        self.assertEqual(self.runs.load_manifest(RUN_ID).status, RunStatus.RUNNING)

    def test_corrupt_manifest_directory_can_still_be_removed(self):
        directory = self._create_run(RUN_ID)
        (directory / "manifest.json").write_text("{not json", encoding="utf-8")

        self.deletion.delete_run(RUN_ID)

        self.assertFalse(directory.exists())

    def test_non_directory_path_is_rejected(self):
        self.runs.root.mkdir(parents=True, exist_ok=True)
        (self.runs.root / RUN_ID).write_text("not a directory", encoding="utf-8")

        with self.assertRaises(NotADirectoryError):
            self.deletion.delete_run(RUN_ID)

    def test_symlinked_run_directory_is_rejected(self):
        outside = self.base / "outside_run"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep", encoding="utf-8")
        self.runs.root.mkdir(parents=True, exist_ok=True)
        link = self.runs.root / RUN_ID
        self._make_directory_link(link, outside)
        self.addCleanup(self._remove_directory_link, link)

        with self.assertRaisesRegex(ValueError, "符号链接"):
            self.deletion.delete_run(RUN_ID)

        self.assertTrue((outside / "keep.txt").is_file())

    def _make_directory_link(self, link: Path, target: Path) -> None:
        """创建一个目录重解析点：优先符号链接，Windows 回退目录联接。"""
        try:
            link.symlink_to(target, target_is_directory=True)
            return
        except (OSError, NotImplementedError):
            pass
        if os.name == "nt":
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
            )
            if result.returncode == 0:
                return
        self.skipTest("当前平台不支持创建目录符号链接或目录联接")

    def _remove_directory_link(self, link: Path) -> None:
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)
        else:
            try:
                link.unlink()
            except OSError:
                pass


class DeletionCompositionTests(unittest.TestCase):
    def test_facade_wires_the_deletion_entry(self):
        from presentation.qt.composition import build_gui_data_service

        with tempfile.TemporaryDirectory() as directory:
            paths = PathService(Path(directory))
            runs = RunRepository(paths=paths)
            runs.create_run({"run_id": RUN_ID}, run_id=RUN_ID)
            service = build_gui_data_service(paths)

            outcome = service.run_history.delete_runs([RUN_ID])

            self.assertEqual(outcome.deleted_run_ids, (RUN_ID,))
            self.assertFalse(
                (paths.test_results_dir / "runs" / RUN_ID).exists()
            )


if __name__ == "__main__":
    unittest.main()
