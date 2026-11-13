import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
)

from application.dto import CableLossMeasurementResult, CableLossRow
from presentation.qt.cable_loss_page import CableLossPage
from presentation.qt.measurement_controller_contract import MeasurementCommand
from presentation.qt.measurement_controls import MeasurementAction
from presentation.qt.measurement_state import (
    MeasurementKind,
    MeasurementResultReference,
    MeasurementStatus,
)


class Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)

    def emit(self, *args):
        for slot in tuple(self.slots):
            slot(*args)


class Signals:
    def __init__(self):
        for name in (
            "progress", "message", "data_update", "result", "finished",
            "stopped", "error", "step_pause", "rejected", "state_changed",
            "archival",
        ):
            setattr(self, name, Signal())


class Controller:
    def __init__(self):
        self.signals = Signals()
        self.calls = []
        self.state = type("State", (), {"kind": None, "status": MeasurementStatus.IDLE, "is_active": False})()

    def start_cable_loss(self, command):
        self.calls.append(("start", command))
        return True

    def stop(self):
        self.calls.append(("stop",))
        return True

    def continue_cable_loss_stage2(self):
        self.calls.append(("continue",))
        return True


class Dialog:
    def __init__(self, result):
        self.result = result

    def exec(self):
        return self.result


class CableLossPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = Controller()
        self.dialog_results = [QDialog.Accepted]
        self.dialog_types = []

        def dialog_factory(diagram_type, _parent):
            self.dialog_types.append(diagram_type)
            return Dialog(self.dialog_results.pop(0))

        self.logs = []
        self.errors = []
        self.run = type("PreparedRun", (), {
            "run_id": "run-cable-loss", "run_repository": object(),
            "run_directory": "runs/run-cable-loss",
            "hardware_connection_path": lambda self: None,
        })()
        self.page = CableLossPage(
            config_path_provider=lambda: "config.json",
            run_provider=lambda: self.run,
            connection_dialog_factory=dialog_factory,
            log_callback=self.logs.append,
            error_callback=self.errors.append,
        )
        self.page.bind_controller(self.controller)

        self.assertEqual(self.page.cable_loss_table.columnCount(), 5)
        self.assertEqual(self.page.start_measurement_btn.text(), "开始测量")
        self.assertFalse(self.page.stop_cable_loss_btn.isEnabled())
        self.assertFalse(hasattr(self.page, "stage2_measurement_btn"))

    def test_start_is_forwarded_as_measurement_command(self):
        self.page.start_measurement()

        self.assertEqual(self.controller.calls[0][0], "start")
        command = self.controller.calls[0][1]
        self.assertIsInstance(command, MeasurementCommand)
        self.assertEqual(command.config_path, "config.json")
        self.assertIs(command.prepared_run, self.run)

    def test_missing_current_run_does_not_start_measurement(self):
        self.page._run_provider = lambda: None

        self.page.start_measurement()

        self.assertEqual(self.controller.calls, [])

    def test_legacy_run_does_not_start_measurement(self):
        self.page._run_provider = lambda: type(
            "LegacyRun", (), {"run_id": "legacy", "run_repository": object()}
        )()
        self.page.start_measurement()
        self.assertEqual(self.controller.calls, [])

    def test_start_submits_to_the_controller_checked_before_the_dialog(self):
        checked = self.controller
        replacement = Controller()

        def factory(*_args):
            # 模态接线对话框运行期间 controller 可能被重新绑定：启动必须继续
            # 提交给完成前置检查的那一个，而不是事后重新读取的 controller。
            self.page.bind_controller(replacement)
            return Dialog(QDialog.Accepted)

        self.page._connection_dialog_factory = factory

        self.page.start_measurement()

        self.assertEqual(len(checked.calls), 1)
        self.assertEqual(checked.calls[0][0], "start")
        self.assertEqual(replacement.calls, [])

    def test_realtime_and_structured_result_fill_table(self):
        self.controller.signals.data_update.emit({
            "frequency": "2.4",
            "cable_losses": {"cable1": 1.234},
        })
        reference = MeasurementResultReference(
            "result-1",
            MeasurementKind.CABLE_LOSS,
            CableLossMeasurementResult(
                run_id="run-cable-loss",
                status=MeasurementStatus.COMPLETED,
                points=(
                    CableLossRow(
                        frequency_ghz="2.4", cable_losses={"cable2": 2.345}
                    ),
                ),
            ),
        )
        self.controller.signals.result.emit(reference)

        self.assertEqual(self.page.cable_loss_table.rowCount(), 1)
        self.assertEqual(self.page.cable_loss_table.item(0, 1).text(), "1.234")
        self.assertEqual(self.page.cable_loss_table.item(0, 2).text(), "2.345")

    def test_result_table_is_a_read_only_display_panel(self):
        self.controller.signals.data_update.emit({
            "frequency": "2.4",
            "cable_losses": {"cable1": 1.234},
        })

        table = self.page.cable_loss_table
        self.assertEqual(table.editTriggers(), QAbstractItemView.NoEditTriggers)
        for column in (0, 1):
            item = table.item(0, column)
            self.assertIsNotNone(item)
            self.assertFalse(item.flags() & Qt.ItemIsEditable)

    def test_pause_accept_continues_and_reject_stops(self):
        self.controller.state = type(
            "State", (), {"kind": MeasurementKind.CABLE_LOSS, "status": MeasurementStatus.WAITING_FOR_CONTINUE}
        )()
        self.controller.signals.step_pause.emit("请重新接线")
        self.assertEqual(self.controller.calls, [("continue",)])

        self.dialog_results.append(QDialog.Rejected)
        self.controller.signals.step_pause.emit("请重新接线")
        self.assertEqual(self.controller.calls, [("continue",), ("stop",)])

    def test_load_results_requests_window_owned_structured_result(self):
        requests = []
        self.page.load_result_requested.connect(lambda: requests.append(True))
        self.page.load_cable_results_btn.click()
        self.assertEqual(requests, [True])
        self.page.show_loaded_result(MeasurementResultReference(
            "loaded-result",
            MeasurementKind.CABLE_LOSS,
            CableLossMeasurementResult(
                run_id="run-cable-loss",
                status=MeasurementStatus.COMPLETED,
                points=(
                    CableLossRow(
                        frequency_ghz="3.5",
                        cable_losses={"cable1": 1.111, "cable4": 4.444},
                    ),
                ),
            ),
            source="result_service",
        ))

        self.assertEqual(self.page.cable_loss_table.rowCount(), 1)
        self.assertEqual(self.page.cable_loss_table.item(0, 0).text(), "3.5")
        self.assertEqual(self.page.cable_loss_table.item(0, 1).text(), "1.111")
        self.assertEqual(self.page.cable_loss_table.item(0, 4).text(), "4.444")

    def test_loaded_result_shows_the_selected_source_filename(self):
        self.page.show_loaded_result(
            MeasurementResultReference(
                "loaded-result",
                MeasurementKind.CABLE_LOSS,
                CableLossMeasurementResult(
                    run_id="run-cable-loss",
                    status=MeasurementStatus.COMPLETED,
                    points=(CableLossRow(frequency_ghz="3.5"),),
                ),
                source="stage_result",
            ),
            filename="20261111T010203Z_a1b2c3d4.json",
        )

        self.assertFalse(self.page.cable_loss_source_label.isHidden())
        self.assertIn(
            "20261111T010203Z_a1b2c3d4.json",
            self.page.cable_loss_source_label.text(),
        )

    def test_invalid_loaded_result_preserves_the_current_selection(self):
        self.page.show_loaded_result(
            MeasurementResultReference(
                "loaded-result",
                MeasurementKind.CABLE_LOSS,
                CableLossMeasurementResult(
                    run_id="run-cable-loss",
                    status=MeasurementStatus.COMPLETED,
                    points=(CableLossRow(frequency_ghz="3.5"),),
                ),
                source="stage_result",
            ),
            filename="valid-result.json",
        )

        self.page.show_loaded_result(
            MeasurementResultReference(
                "invalid-result", MeasurementKind.CABLE_LOSS, object(), source="stage_result"
            ),
            filename="invalid-result.json",
        )

        self.assertEqual(self.page.cable_loss_table.rowCount(), 1)
        self.assertEqual(self.page.cable_loss_table.item(0, 0).text(), "3.5")
        self.assertEqual(self.page.cable_loss_source_label.text(), "来源文件：valid-result.json")
        self.assertIn("不是正式结果模型", self.errors[-1])

    def test_new_measurement_clears_the_selected_source_filename(self):
        self.page.show_loaded_result(
            MeasurementResultReference(
                "loaded-result",
                MeasurementKind.CABLE_LOSS,
                CableLossMeasurementResult(
                    run_id="run-cable-loss",
                    status=MeasurementStatus.COMPLETED,
                    points=(CableLossRow(frequency_ghz="3.5"),),
                ),
                source="stage_result",
            ),
            filename="20261111T010203Z_a1b2c3d4.json",
        )

        self.page.clear_results()

        self.assertTrue(self.page.cable_loss_source_label.isHidden())
        self.assertEqual(self.page.cable_loss_source_label.text(), "")

    def test_control_buttons_follow_the_shared_state_binding(self):
        self.assertEqual(
            self.page.controls.supported_actions,
            frozenset(
                {
                    MeasurementAction.START,
                    MeasurementAction.STOP,
                }
            ),
        )
        self.controller.signals.state_changed.emit(type(
            "State", (), {
                "kind": MeasurementKind.CABLE_LOSS,
                "status": MeasurementStatus.WAITING_FOR_CONTINUE,
                "is_active": True,
            }
        )())
        self.assertFalse(self.page.start_measurement_btn.isEnabled())
        self.assertTrue(self.page.stop_cable_loss_btn.isEnabled())

        self.controller.signals.stopped.emit("用户停止")
        self.assertTrue(self.page.start_measurement_btn.isEnabled())
        self.assertFalse(self.page.stop_cable_loss_btn.isEnabled())

    def test_non_formal_result_is_reported_and_not_displayed(self):
        self.page.show_loaded_result(MeasurementResultReference(
            "legacy-result",
            MeasurementKind.CABLE_LOSS,
            {"cable_losses": {"3.5": {"cable1": 1.111}}},
            source="result_service",
        ))

        self.assertEqual(self.page.cable_loss_table.rowCount(), 0)
        self.assertEqual(self.errors, ["线损测量结果不是正式结果模型，无法显示"])

    def test_connection_diagram_button_lives_in_measurement_controls(self):
        """接线说明只剩一个连接图入口：页面上不再有线路文字。"""
        from PySide6.QtWidgets import QFrame, QGroupBox, QTextEdit

        self.assertEqual(self.page.findChildren(QGroupBox), [])
        control = self.page.findChild(QFrame, "measurementSurface")
        self.assertIsNotNone(control)
        self.assertIs(
            self.page.show_cable_loss_diagram_btn.parentWidget(), control
        )
        self.assertFalse(hasattr(self.page, "show_path1_btn"))
        self.assertFalse(hasattr(self.page, "show_path2_btn"))
        self.assertEqual(self.page.findChildren(QTextEdit), [])

    def test_page_is_split_into_control_and_result_surfaces(self):
        from PySide6.QtWidgets import QFrame

        control = self.page.findChild(QFrame, "measurementSurface")
        result = self.page.findChild(QFrame, "measurementResultSurface")
        self.assertIsNotNone(control)
        self.assertIsNotNone(result)
        self.assertIsNot(control, result)
        self.assertIs(control.parentWidget(), self.page.control_panel)

    def test_start_is_the_only_primary_action(self):
        primary = [
            button
            for button in (
                self.page.start_measurement_btn,
                self.page.stop_cable_loss_btn,
                self.page.load_cable_results_btn,
                self.page.show_cable_loss_diagram_btn,
            )
            if button.property("variant") == "primary"
        ]
        self.assertEqual(primary, [self.page.start_measurement_btn])

    def test_start_measurement_takes_its_own_row_in_the_control_surface(self):
        from PySide6.QtWidgets import QFrame

        control_layout = self.page.findChild(QFrame, "measurementSurface").layout()
        start_items = [
            index
            for index in range(control_layout.count())
            if control_layout.itemAt(index).widget() is self.page.start_measurement_btn
        ]
        self.assertEqual(len(start_items), 1)
        row_index = next(
            index
            for index in range(control_layout.count())
            if control_layout.itemAt(index).layout() is not None
        )
        self.assertLess(start_items[0], row_index)

    def test_secondary_actions_share_one_compact_row(self):
        from PySide6.QtWidgets import QFrame

        control_layout = self.page.findChild(QFrame, "measurementSurface").layout()
        row_layout = None
        for index in range(control_layout.count()):
            item = control_layout.itemAt(index)
            if item.layout() is not None:
                row_layout = item.layout()
                break
        self.assertIsNotNone(row_layout)
        self.assertEqual(
            [row_layout.itemAt(index).widget() for index in range(row_layout.count())],
            [
                self.page.stop_cable_loss_btn,
                self.page.load_cable_results_btn,
                self.page.show_cable_loss_diagram_btn,
            ],
        )

    def test_result_surface_holds_the_table_source_and_empty_state(self):
        from PySide6.QtWidgets import QFrame

        result = self.page.findChild(QFrame, "measurementResultSurface")
        self.assertIs(self.page.cable_loss_table.parentWidget(), result)
        self.assertIs(self.page.cable_loss_empty_hint.parentWidget(), result)
        self.assertIs(self.page.cable_loss_source_label.parentWidget(), result)
        # 空表也要显示表头：不能让用户以为表格没有加载。
        self.assertFalse(self.page.cable_loss_table.isHidden())
        self.assertFalse(self.page.cable_loss_empty_hint.isHidden())

    def test_view_diagram_button_opens_the_combined_diagram(self):
        self.page.show_cable_loss_diagram_btn.click()
        self.assertEqual(self.dialog_types, ["cable_loss_paths"])

    def test_status_slot_sits_below_the_measurement_controls(self):
        """左栏在控制面下面留出状态区：进度与日志由窗口挂进来。"""
        from PySide6.QtWidgets import QFrame

        page = self.page
        page.resize(1200, 900)
        page.show()
        self.app.processEvents()
        control = page.findChild(QFrame, "measurementSurface")
        self.assertIs(page.status_slot.parentWidget(), control.parentWidget())
        self.assertGreaterEqual(page.status_slot.y(), control.y() + control.height())
        page.close()


if __name__ == "__main__":
    unittest.main()
