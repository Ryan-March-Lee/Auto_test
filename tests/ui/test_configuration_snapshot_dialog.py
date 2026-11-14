"""选择已保存配置快照的对话框：列表、空态与选中结果。

对话框只消费 ``ConfigurationSnapshotRepository.list()`` 返回的正式快照；这里用
纯 Python 替身验证“名称 + 保存时间”的展示、选中后交回的快照以及读取失败时的
空态，不连接真实文件系统。
"""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from application.ports.configuration_snapshots import (  # noqa: E402
    ConfigurationSnapshot,
)
from presentation.qt.configuration_snapshot_dialog import (  # noqa: E402
    ConfigurationSnapshotDialog,
)


def _destroy(widget):
    widget.close()
    widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _snapshot(name: str, saved_at: str) -> ConfigurationSnapshot:
    directory = Path("configurations") / name
    return ConfigurationSnapshot(
        display_name=name,
        directory=directory,
        configuration_path=directory / f"{name}.json",
        metadata_path=directory / "metadata.json",
        saved_at=saved_at,
    )


class _Repository:
    def __init__(self, snapshots=(), error=None):
        self.snapshots = tuple(snapshots)
        self.error = error

    def list(self):
        if self.error is not None:
            raise self.error
        return self.snapshots


class ConfigurationSnapshotDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _dialog(self, repository, *, log_callback=None):
        dialog = ConfigurationSnapshotDialog(
            repository, log_callback=log_callback
        )
        dialog.show()
        self.app.processEvents()
        self.addCleanup(_destroy, dialog)
        return dialog

    def test_lists_snapshots_with_name_and_saved_time_in_repository_order(self):
        first = _snapshot("粗扫", "2026-11-14T02:00:00Z")
        second = _snapshot("细扫", "2026-11-14T01:00:00Z")
        dialog = self._dialog(_Repository((first, second)))

        self.assertEqual(dialog.table.rowCount(), 2)
        self.assertEqual(dialog.table.item(0, 0).text(), "粗扫")
        self.assertTrue(dialog.table.item(0, 1).text().strip())
        self.assertEqual(dialog.table.item(1, 0).text(), "细扫")
        self.assertFalse(dialog.empty_hint.isVisible())

    def test_selected_snapshot_is_the_one_accept_returns(self):
        first = _snapshot("粗扫", "2026-11-14T02:00:00Z")
        second = _snapshot("细扫", "2026-11-14T01:00:00Z")
        dialog = self._dialog(_Repository((first, second)))

        dialog.table.selectRow(1)
        self.assertIs(dialog.selected_row_snapshot(), second)
        self.assertTrue(dialog.load_button.isEnabled())

        dialog.accept()
        self.assertIs(dialog.selected_snapshot, second)

    def test_accept_without_a_selection_stays_open(self):
        dialog = self._dialog(_Repository((_snapshot("粗扫", "2026-11-14T02:00:00Z"),)))

        with patch.object(
            dialog, "selected_row_snapshot", return_value=None
        ), patch(
            "presentation.qt.configuration_snapshot_dialog.QMessageBox.warning"
        ) as warning:
            dialog.accept()

        warning.assert_called_once()
        self.assertIsNone(dialog.selected_snapshot)

    def test_empty_repository_shows_the_empty_hint(self):
        dialog = self._dialog(_Repository(()))

        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertTrue(dialog.empty_hint.isVisible())
        self.assertIn("还没有已保存的配置", dialog.empty_hint.text())

    def test_read_failure_is_shown_as_an_empty_state(self):
        messages = []
        dialog = self._dialog(
            _Repository(error=OSError("目录不可读")),
            log_callback=messages.append,
        )

        self.assertEqual(dialog.table.rowCount(), 0)
        self.assertTrue(dialog.empty_hint.isVisible())
        self.assertIn("读取已保存配置失败", dialog.empty_hint.text())
        self.assertTrue(any("目录不可读" in message for message in messages))


if __name__ == "__main__":
    unittest.main()
