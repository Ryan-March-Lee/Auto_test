"""主功放页“加载数据”选择对话框：列出两类校准输入与当前来源。"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog

from presentation.qt.load_data_dialog import (
    LoadDataDialog,
    LoadTarget,
    choose_load_target,
    load_tooltip,
    selection_source_text,
)


def _application():
    return QApplication.instance() or QApplication([])


def _destroy_widget(widget):
    """销毁离屏顶层控件，避免 Qt 对象跨用例累积。"""
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class SelectionTextTests(unittest.TestCase):
    def test_selection_becomes_filename_and_measurement_time(self):
        selection = type(
            "Selection",
            (),
            {"filename": "result.json", "created_at": "2026-11-11T01:02:03+00:00"},
        )()

        self.assertEqual(
            selection_source_text(selection),
            "result.json\n测量时间：2026-11-11T01:02:03+00:00",
        )

    def test_missing_selection_has_no_source_text(self):
        self.assertIsNone(selection_source_text(None))

    def test_tooltip_lists_each_target_and_unselected_ones(self):
        text = load_tooltip(
            (
                LoadTarget("cable_loss", "线损数据", "loss.json\n测量时间：2026-11-11"),
                LoadTarget("driver_mapping", "驱放数据"),
            )
        )

        self.assertIn("加载数据", text)
        self.assertIn("线损数据：loss.json", text)
        self.assertIn("驱放数据：未选择", text)


class LoadDataDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = _application()

    def _dialog(self, *targets):
        dialog = LoadDataDialog(targets or (
            LoadTarget("cable_loss", "线损数据", "loss.json\n测量时间：2026-11-11"),
            LoadTarget("driver_mapping", "驱放数据"),
        ))
        self.addCleanup(_destroy_widget, dialog)
        return dialog

    def test_dialog_title_names_the_action(self):
        self.assertEqual(self._dialog().windowTitle(), "加载数据")

    def test_one_full_width_button_per_target(self):
        dialog = self._dialog()
        dialog.show()
        self.application.processEvents()

        buttons = dialog.target_buttons()
        self.assertEqual([button.text() for button in buttons], ["线损数据", "驱放数据"])
        self.assertEqual({button.width() for button in buttons}, {buttons[0].width()})

    def test_selected_target_closes_with_its_key(self):
        dialog = self._dialog()

        dialog.target_buttons()[1].click()

        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.selected_key(), "driver_mapping")

    def test_cancel_returns_no_selection(self):
        dialog = self._dialog()

        dialog.reject()

        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertIsNone(dialog.selected_key())

    def test_empty_targets_are_rejected(self):
        with self.assertRaises(ValueError):
            LoadDataDialog(())
        with self.assertRaises(TypeError):
            LoadDataDialog(("cable_loss",))

    def test_choose_returns_the_clicked_key(self):
        def fake_exec(self):
            self.target_buttons()[0].click()
            return self.result()

        with patch.object(LoadDataDialog, "exec", fake_exec):
            key = choose_load_target(
                None,
                (
                    LoadTarget("cable_loss", "线损数据"),
                    LoadTarget("driver_mapping", "驱放数据"),
                ),
            )

        self.assertEqual(key, "cable_loss")

    def test_choose_returns_none_when_cancelled(self):
        with patch.object(
            LoadDataDialog, "exec", return_value=QDialog.DialogCode.Rejected
        ):
            key = choose_load_target(
                None, (LoadTarget("cable_loss", "线损数据"),)
            )

        self.assertIsNone(key)


if __name__ == "__main__":
    unittest.main()
