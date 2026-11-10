"""历史运行页删除的真实后端场景（离屏 Qt + 真实运行目录）。"""

import gc
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QItemSelection,
    QItemSelectionModel,
)
from PySide6.QtWidgets import QApplication, QMessageBox

from application.archival_service import ResultArchivalService
from application.run_history_service import RunHistoryService
from application.run_stages import RunStageRecorder
from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    STAGE_AMPLIFIER,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
)
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence.artifact_generator import ArchiveArtifactGenerator
from infrastructure.persistence.run_deletion import FileRunDeletion
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
from infrastructure.persistence.run_repository import RunRepository
from presentation.qt.history_view_model import HistoryViewModel
from presentation.qt.run_history_page import RunHistoryPage


RUN_ID = "20261028T010203Z_abcdef12"
OTHER_RUN_ID = "20261027T010203Z_abcdef12"
BROKEN_RUN_ID = "20260101T000000Z_ffffffff"
CREATED_AT = datetime(2026, 10, 28, 1, 2, 3, tzinfo=timezone.utc)


def _amplifier_payload(run_id):
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


def _destroy_page(page) -> None:
    """销毁离屏顶层控件，避免用例之间累积 Qt 对象。"""
    page.close()
    page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


class RunHistoryDeletionPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.paths = PathService(Path(self._temporary.name))
        self.runs = RunRepository(paths=self.paths)
        self.history = FileRunHistoryRepository(runs=self.runs)
        for name in ("warning", "information", "critical"):
            patcher = patch(f"presentation.qt.run_history_page.QMessageBox.{name}")
            setattr(self, f"{name}_dialog", patcher.start())
            self.addCleanup(patcher.stop)
        question = patch(
            "presentation.qt.run_history_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        )
        self.question_dialog = question.start()
        self.addCleanup(question.stop)

    def _create_completed_run(self, run_id):
        self.runs.create_run({"run_id": run_id}, run_id=run_id)
        recorder = RunStageRecorder(self.runs, run_id)
        recorder.skip(STAGE_CABLE_LOSS, now=CREATED_AT)
        recorder.skip(STAGE_DRIVER_MAPPING, now=CREATED_AT)
        recorder.start(STAGE_AMPLIFIER, now=CREATED_AT)
        recorder.complete(
            STAGE_AMPLIFIER,
            _amplifier_payload(run_id),
            result_path=AMPLIFIER_RESULT_PATH,
            now=CREATED_AT,
        )
        recorder.complete_run(now=CREATED_AT)

    def _create_running_run(self, run_id=RUN_ID):
        self.runs.create_run({"run_id": run_id}, run_id=run_id)
        RunStageRecorder(self.runs, run_id).start(STAGE_CABLE_LOSS, now=CREATED_AT)

    def _create_broken_run(self):
        broken = self.runs.root / BROKEN_RUN_ID
        broken.mkdir(parents=True)
        (broken / "manifest.json").write_text("{not json", encoding="utf-8")

    def _page(self):
        service = RunHistoryService(
            statuses=self.history,
            archival=ResultArchivalService(
                runs=self.runs, generator=ArchiveArtifactGenerator(self.runs)
            ),
            locations=self.history,
            deletions=FileRunDeletion(runs=self.runs),
        )
        page = RunHistoryPage(
            history=HistoryViewModel(runs=service), run_service=service
        )
        self.addCleanup(_destroy_page, page)
        return page

    def _run_directory(self, run_id):
        return self.runs.layout(run_id).run_directory

    def _select_rows(self, page, *run_ids):
        model = page.run_table.model()
        selection = QItemSelection()
        for run_id in run_ids:
            row = self._row_index(page, run_id)
            selection.select(
                model.index(row, 0),
                model.index(row, page.run_table.columnCount() - 1),
            )
        page.run_table.selectionModel().select(
            selection, QItemSelectionModel.ClearAndSelect
        )

    def _row_index(self, page, run_id):
        for index in range(page.run_table.rowCount()):
            if page.run_table.item(index, 0).text() == run_id:
                return index
        raise AssertionError(f"运行表格里没有 {run_id}")

    def test_delete_selected_runs_removes_directories_and_refreshes(self):
        self._create_completed_run(RUN_ID)
        self._create_completed_run(OTHER_RUN_ID)
        page = self._page()
        self.assertEqual(page.run_table.rowCount(), 2)

        self._select_rows(page, RUN_ID, OTHER_RUN_ID)
        self.assertTrue(page.delete_runs_button.isEnabled())

        self.assertTrue(page.delete_selected_runs())

        self.assertFalse(self._run_directory(RUN_ID).exists())
        self.assertFalse(self._run_directory(OTHER_RUN_ID).exists())
        self.assertEqual(page.run_table.rowCount(), 0)
        message = self.information_dialog.call_args.args[2]
        self.assertIn(RUN_ID, message)
        self.assertIn(OTHER_RUN_ID, message)

    def test_delete_keeps_unselected_runs(self):
        self._create_completed_run(RUN_ID)
        self._create_completed_run(OTHER_RUN_ID)
        page = self._page()

        self._select_rows(page, RUN_ID)
        self.assertTrue(page.delete_selected_runs())

        self.assertFalse(self._run_directory(RUN_ID).exists())
        self.assertTrue(self._run_directory(OTHER_RUN_ID).is_dir())
        self.assertEqual(page.run_table.rowCount(), 1)
        self.assertIn(OTHER_RUN_ID, page.run_table.item(0, 0).text())

    def test_running_run_is_not_deletable(self):
        self._create_running_run()
        page = self._page()
        self._select_rows(page, RUN_ID)

        self.assertFalse(page.delete_runs_button.isEnabled())
        self.assertFalse(page.delete_selected_runs())
        self.assertTrue(self._run_directory(RUN_ID).is_dir())
        self.assertIn("正在进行中", self.warning_dialog.call_args.args[2])

    def test_invalid_run_can_be_deleted(self):
        self._create_broken_run()
        page = self._page()
        self.assertEqual(page.run_table.rowCount(), 1)

        self._select_rows(page, BROKEN_RUN_ID)
        self.assertTrue(page.delete_selected_runs())

        self.assertFalse(self._run_directory(BROKEN_RUN_ID).exists())

    def test_cancelled_confirmation_keeps_the_run(self):
        self._create_completed_run(RUN_ID)
        page = self._page()
        self._select_rows(page, RUN_ID)

        with patch(
            "presentation.qt.run_history_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            self.assertFalse(page.delete_selected_runs())

        self.assertTrue(self._run_directory(RUN_ID).is_dir())
        self.assertEqual(page.run_table.rowCount(), 1)


if __name__ == "__main__":
    unittest.main()
