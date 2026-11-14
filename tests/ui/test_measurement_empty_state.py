"""测量结果空态：线稿、标题/说明与主题选择器。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from presentation.qt.measurement_empty_state import MeasurementEmptyState
from presentation.qt.theme import build_stylesheet


def _destroy(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class MeasurementEmptyStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _track(self, widget):
        self.addCleanup(_destroy, widget)
        return widget

    def test_defaults_and_text_updates(self):
        empty = self._track(MeasurementEmptyState())

        self.assertEqual(empty.objectName(), "measurementEmptyState")
        self.assertEqual(empty.title(), "尚无测量结果")
        self.assertEqual(empty.description(), "")

        empty.set_texts("还没有线损结果", "开始测量后每个频点会在此列出。")

        self.assertEqual(empty.title_label.text(), "还没有线损结果")
        self.assertEqual(
            empty.description_label.text(), "开始测量后每个频点会在此列出。"
        )
        self.assertEqual(empty.accessibleName(), "还没有线损结果")

    def test_blank_title_falls_back_to_the_fixed_copy(self):
        empty = self._track(MeasurementEmptyState("   ", "说明"))

        self.assertEqual(empty.title(), "尚无测量结果")

    def test_widget_can_be_shown_and_grab_rendered(self):
        empty = self._track(
            MeasurementEmptyState(
                "尚无测量结果",
                "开始测量后，每个频点的线缆损耗会在此列出。",
            )
        )
        empty.resize(520, 180)
        empty.show()
        self.app.processEvents()

        image = empty.grab().toImage()

        self.assertFalse(image.isNull())
        self.assertGreater(image.width(), 0)
        self.assertGreater(image.height(), 0)


class MeasurementEmptyStateThemeTests(unittest.TestCase):
    def test_stylesheet_contains_empty_state_selectors(self):
        stylesheet = build_stylesheet()

        for selector in (
            "QWidget#measurementEmptyState",
            "QLabel#measurementEmptyStateTitle",
            "QLabel#measurementEmptyStateDescription",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, stylesheet)


if __name__ == "__main__":
    unittest.main()
