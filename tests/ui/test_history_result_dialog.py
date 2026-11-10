"""选择历史结果对话框：只读列表、选中加载与失败提示。"""

import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QDialog

from application.dto import AmplifierFrequencyResult, AmplifierMeasurementResult
from application.ports.gui_data_service import LoadedResultDto, ResultFileDto
from presentation.qt.history_result_dialog import HistoryResultDialog
from presentation.qt.history_view_model import HistoryViewModel


RUN_ID = "20261028T010203Z_abcdef12"
CREATED_AT = datetime(2026, 10, 28, 1, 2, 3, tzinfo=timezone.utc)
RESULT_ID = f"run::{RUN_ID}::results/amplifier_measurement.json"


def _result_file() -> ResultFileDto:
    return ResultFileDto(
        result_id=RESULT_ID,
        filename="amplifier_measurement.json",
        file_type="功放测试",
        modified_at=CREATED_AT,
        run_id=RUN_ID,
    )


def _loaded_dto(result_id: str = RESULT_ID) -> LoadedResultDto:
    result = AmplifierMeasurementResult(
        run_id=RUN_ID,
        frequencies=(
            AmplifierFrequencyResult(
                frequency_ghz="5.2",
                sweep_data={"input_power_dut": [-10.0], "output_power_dut": [1.0]},
            ),
        ),
    )
    return LoadedResultDto(result_id, "amplifier_measurement.json", result)


class _Query:
    """按给定行返回结果描述；读取失败可由 ``failure`` 注入。"""

    def __init__(self, rows=(), *, failure=None, load_failure=None):
        self.rows = tuple(rows)
        self.failure = failure
        self.load_failure = load_failure
        self.loaded_ids: list[str] = []

    def list_results(self):
        if self.failure is not None:
            raise self.failure
        return list(self.rows)

    def load(self, result_id):
        if self.load_failure is not None:
            raise self.load_failure
        self.loaded_ids.append(result_id)
        return _loaded_dto(result_id)

    def read_bytes(self, result_id):  # pragma: no cover - 对话框不导出
        raise AssertionError("选择对话框不读取结果字节")


class HistoryResultDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        for name in ("warning", "information", "critical"):
            patcher = patch(
                f"presentation.qt.history_result_dialog.QMessageBox.{name}"
            )
            setattr(self, f"{name}_dialog", patcher.start())
            self.addCleanup(patcher.stop)
        self.logs: list[str] = []

    def _dialog(self, query) -> HistoryResultDialog:
        return HistoryResultDialog(
            HistoryViewModel(query), log_callback=self.logs.append
        )

    def test_lists_results_with_type_source_and_modified_time(self):
        dialog = self._dialog(_Query([_result_file()]))

        self.assertEqual(dialog.table.rowCount(), 1)
        self.assertEqual(dialog.table.item(0, 0).text(), "amplifier_measurement.json")
        self.assertEqual(dialog.table.item(0, 1).text(), "功放测试")
        self.assertEqual(dialog.table.item(0, 2).text(), f"运行 {RUN_ID}")
        self.assertEqual(dialog.table.item(0, 3).text(), "2026-10-28 01:02:03")
        self.assertTrue(dialog.empty_hint.isHidden())
        self.assertFalse(dialog.load_button.isEnabled())

    def test_empty_list_shows_the_hint_and_keeps_load_disabled(self):
        dialog = self._dialog(_Query())

        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertFalse(dialog.empty_hint.isHidden())
        self.assertFalse(dialog.load_button.isEnabled())

    def test_read_failure_clears_the_table_and_keeps_the_dialog_open(self):
        dialog = self._dialog(_Query([_result_file()], failure=OSError("目录不可读")))

        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertFalse(dialog.empty_hint.isHidden())
        self.assertIn("读取历史结果失败", dialog.empty_hint.text())
        self.assertTrue(any("读取历史结果失败" in line for line in self.logs))

    def test_selecting_a_row_enables_loading_and_returns_the_formal_result(self):
        query = _Query([_result_file()])
        dialog = self._dialog(query)

        dialog.table.setCurrentCell(0, 0)
        self.assertTrue(dialog.load_button.isEnabled())
        dialog.accept()

        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertIsNotNone(dialog.loaded_result)
        self.assertEqual(dialog.loaded_result.result_id, RESULT_ID)
        self.assertEqual(query.loaded_ids, [RESULT_ID])

    def test_accept_without_a_selection_warns_and_stays_open(self):
        dialog = self._dialog(_Query([_result_file()]))

        dialog.accept()

        self.assertIsNone(dialog.loaded_result)
        self.warning_dialog.assert_called_once()
        self.assertIn("请先选择", self.warning_dialog.call_args.args[2])

    def test_load_failure_warns_and_does_not_accept(self):
        dialog = self._dialog(
            _Query([_result_file()], load_failure=ValueError("结果已损坏"))
        )
        dialog.table.setCurrentCell(0, 0)

        dialog.accept()

        self.assertIsNone(dialog.loaded_result)
        self.warning_dialog.assert_called_once()
        self.assertIn("结果已损坏", self.warning_dialog.call_args.args[2])
        self.assertTrue(any("加载历史结果失败" in line for line in self.logs))

    def test_refresh_picks_up_a_newly_archived_result(self):
        query = _Query([_result_file()])
        dialog = self._dialog(query)
        self.assertEqual(dialog.table.rowCount(), 1)

        query.rows = (
            _result_file(),
            ResultFileDto(
                result_id=f"run::{RUN_ID}::results/second.json",
                filename="second.json",
                file_type="功放测试",
                modified_at=CREATED_AT,
                run_id=RUN_ID,
            ),
        )
        dialog.refresh()

        self.assertEqual(dialog.table.rowCount(), 2)
        self.assertEqual(dialog.table.item(1, 0).text(), "second.json")


if __name__ == "__main__":
    unittest.main()
