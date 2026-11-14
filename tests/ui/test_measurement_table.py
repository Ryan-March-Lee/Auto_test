"""测量结果表：行高、当前行、表头色点与数值范围。"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QTableWidgetItem,
)

from presentation.qt.measurement_table import MeasurementTable, header_item


def _destroy(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class MeasurementTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _track(self, widget):
        self.addCleanup(_destroy, widget)
        return widget

    def _table(self):
        table = self._track(MeasurementTable(value_columns=(1, 2)))
        table.setColumnCount(3)
        table.setRowCount(2)
        table.set_headers(
            (
                ("频率(GHz)", None),
                ("线缆1", "#2563EB"),
                ("线缆2", "#16A34A"),
            )
        )
        table.setItem(0, 0, QTableWidgetItem("2.40"))
        table.setItem(0, 1, QTableWidgetItem("0.42"))
        table.setItem(0, 2, QTableWidgetItem("0.51"))
        table.setItem(1, 0, QTableWidgetItem("2.45"))
        table.setItem(1, 1, QTableWidgetItem("0.44"))
        table.setItem(1, 2, QTableWidgetItem("0.53"))
        table.refresh_value_ranges()
        return table

    def test_table_defaults_follow_the_measurement_reading_style(self):
        table = self._track(MeasurementTable())

        self.assertEqual(table.objectName(), "measurementResultTable")
        self.assertEqual(table.verticalHeader().defaultSectionSize(), 34)
        self.assertFalse(table.showGrid())
        self.assertTrue(table.alternatingRowColors())
        self.assertEqual(
            table.selectionBehavior(),
            QAbstractItemView.SelectionBehavior.SelectRows,
        )
        self.assertEqual(
            table.selectionMode(),
            QAbstractItemView.SelectionMode.SingleSelection,
        )
        self.assertEqual(
            table.editTriggers(),
            QAbstractItemView.EditTrigger.NoEditTriggers,
        )

    def test_header_carries_the_series_colour_dot(self):
        table = self._table()

        header = table.horizontalHeaderItem(1)

        self.assertIsNotNone(header)
        self.assertIsNotNone(header.data(Qt.ItemDataRole.DecorationRole))
        self.assertIsNone(
            table.horizontalHeaderItem(0).data(Qt.ItemDataRole.DecorationRole)
        )

    def test_refresh_computes_a_range_per_value_column(self):
        table = self._table()

        self.assertEqual(table.value_range(1), (0.42, 0.44))
        self.assertEqual(table.value_range(2), (0.51, 0.53))
        self.assertIsNone(table.value_range(0))

    def test_current_row_is_independent_from_selection(self):
        table = self._table()
        table.selectRow(0)

        table.set_current_row(1)

        self.assertEqual(table.current_row(), 1)
        self.assertEqual(table.selectedItems()[0].row(), 0)

    def test_out_of_range_current_row_clears_the_marker(self):
        table = self._table()
        table.set_current_row(1)

        table.set_current_row(9)

        self.assertEqual(table.current_row(), -1)

    def test_invalid_payloads_are_rejected(self):
        table = self._table()

        with self.assertRaises(TypeError):
            table.set_current_row("1")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            table.set_current_row(True)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            table.set_headers((("only-one", None),))

    def test_header_item_requires_a_decorated_colour_when_given(self):
        item = header_item("线缆1", "#2563EB")

        self.assertEqual(item.text(), "线缆1")
        self.assertIsNotNone(item.data(Qt.ItemDataRole.DecorationRole))

    def test_grab_draws_the_current_row_and_bars(self):
        table = self._table()
        table.resize(520, 140)
        table.set_current_row(1)
        table.show()
        self.app.processEvents()

        image = table.grab().toImage()

        self.assertFalse(image.isNull())
        self.assertGreater(image.width(), 0)
        self.assertGreater(image.height(), 0)


class MeasurementTableThemeTests(unittest.TestCase):
    def test_stylesheet_contains_table_selectors(self):
        from presentation.qt.theme import build_stylesheet

        stylesheet = build_stylesheet()

        for selector in (
            "QTableWidget#measurementResultTable",
            "QTableWidget#measurementResultTable QHeaderView::section",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, stylesheet)


if __name__ == "__main__":
    unittest.main()
