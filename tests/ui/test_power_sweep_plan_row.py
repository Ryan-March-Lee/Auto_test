"""DUT 功率扫描计划单行控件的独立测试。

组件只覆盖一行的显示与输入：编号、起始/步长/终止三个数值框、删除意图和灰色预览
标签。计划连续性、点位预览算法与“至少保留一行”的删除规则属于父编辑器，这里不测试。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel

from presentation.qt.config_page_widgets import POWER_RANGE_DBM, POWER_STEP_RANGE_DB
from presentation.qt.power_sweep_plan_row import PowerSweepPlanRow


def _destroy_widget(widget):
    """按离屏用例的收尾规则销毁顶层控件，避免控件在后续用例中累积。"""
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def _row_is_alive(object_name):
    """应用里是否还有这个对象名的控件；按名字判断，避免碰已删除包装对象的比较。"""
    return any(
        widget.objectName() == object_name for widget in QApplication.allWidgets()
    )


class PowerSweepPlanRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_row(self, **kwargs):
        params = {"start_power": -38.0, "step": 1.0, "stop_power": -10.0}
        params.update(kwargs)
        row = PowerSweepPlanRow(**params)
        self.addCleanup(_destroy_widget, row)
        return row


    def test_reads_and_backfills_the_three_values(self):
        row = self.make_row()

        self.assertEqual(row.values(), (-38.0, 1.0, -10.0))

        row.set_values(-20.0, 0.25, -2.0)

        self.assertEqual(row.values(), (-20.0, 0.25, -2.0))


    def test_power_and_step_ranges_are_shared_with_the_configuration_page(self):
        row = self.make_row()

        self.assertEqual(
            (row.start_power.minimum(), row.start_power.maximum()), POWER_RANGE_DBM
        )
        self.assertEqual(
            (row.stop_power.minimum(), row.stop_power.maximum()), POWER_RANGE_DBM
        )
        self.assertEqual(
            (row.step.minimum(), row.step.maximum()), POWER_STEP_RANGE_DB
        )

    def test_value_changes_emit_one_notification(self):
        row = self.make_row()
        changes = []
        row.values_changed.connect(lambda: changes.append(True))

        row.step.setValue(0.5)
        row.stop_power.setValue(-2.0)
        row.stop_power.setValue(-2.0)  # 数值未变，不重复通知

        self.assertEqual(changes, [True, True])

    def test_batch_backfill_emits_one_notification(self):
        row = self.make_row()
        changes = []
        row.values_changed.connect(lambda: changes.append(True))

        row.set_values(-20.0, 0.25, -2.0)

        self.assertEqual(changes, [True])


    def test_delete_button_only_reports_the_intent(self):
        row = self.make_row()
        removed = []
        row.remove_requested.connect(removed.append)

        row.delete_button.click()

        self.assertEqual(removed, [row])



    def test_rows_are_independent(self):
        first = self.make_row(number=1)
        second = self.make_row(number=2, start_power=-10.0, stop_power=-2.0)

        second.set_values(-10.0, 0.25, 0.0)

        self.assertEqual(first.values(), (-38.0, 1.0, -10.0))
        self.assertEqual(second.values(), (-10.0, 0.25, 0.0))
        self.assertEqual((first.number(), second.number()), (1, 2))



if __name__ == "__main__":
    unittest.main()
