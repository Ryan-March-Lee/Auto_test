import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QGroupBox

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

    def continue_cable_loss(self):
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
            connection_dialog_factory=lambda *_: Dialog(self.dialog_results.pop(0)),
            log_callback=self.logs.append,
            error_callback=self.errors.append,
        )
        self.page.bind_controller(self.controller)

        self.assertEqual(self.page.cable_loss_table.columnCount(), 5)
        self.assertFalse(self.page.stop_cable_loss_btn.isEnabled())
        self.assertFalse(self.page.continue_cable_loss_btn.isEnabled())

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

    def test_control_buttons_follow_the_shared_state_binding(self):
        self.assertEqual(
            self.page.controls.supported_actions,
            frozenset(
                {
                    MeasurementAction.START,
                    MeasurementAction.STOP,
                    MeasurementAction.CONTINUE,
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
        self.assertFalse(self.page.cable_loss_btn.isEnabled())
        self.assertTrue(self.page.stop_cable_loss_btn.isEnabled())
        self.assertTrue(self.page.continue_cable_loss_btn.isEnabled())

        self.controller.signals.stopped.emit("用户停止")
        self.assertTrue(self.page.cable_loss_btn.isEnabled())
        self.assertFalse(self.page.stop_cable_loss_btn.isEnabled())
        self.assertFalse(self.page.continue_cable_loss_btn.isEnabled())

    def test_non_formal_result_is_reported_and_not_displayed(self):
        self.page.show_loaded_result(MeasurementResultReference(
            "legacy-result",
            MeasurementKind.CABLE_LOSS,
            {"cable_losses": {"3.5": {"cable1": 1.111}}},
            source="result_service",
        ))

        self.assertEqual(self.page.cable_loss_table.rowCount(), 0)
        self.assertEqual(self.errors, ["线损测量结果不是正式结果模型，无法显示"])

    def test_connection_diagram_buttons_live_in_measurement_controls(self):
        """接线说明只剩连接图入口：页面上不再有线路文字。"""
        from PySide6.QtWidgets import QGroupBox, QTextEdit

        titles = [box.title() for box in self.page.findChildren(QGroupBox)]
        self.assertEqual(titles, ["测量控制", "测量结果"])
        self.assertEqual(self.page.show_path1_btn.parent().title(), "测量控制")
        self.assertEqual(self.page.show_path2_btn.parent().title(), "测量控制")
        self.assertEqual(self.page.findChildren(QTextEdit), [])

    def test_status_slot_sits_below_the_measurement_controls(self):
        """左栏在“测量控制”下面留出状态区：进度与日志由窗口挂进来。"""
        page = self.page
        page.resize(1200, 900)
        page.show()
        self.app.processEvents()
        group = next(
            box for box in page.findChildren(QGroupBox) if box.title() == "测量控制"
        )
        self.assertIs(page.status_slot.parentWidget(), group.parentWidget())
        self.assertGreaterEqual(page.status_slot.y(), group.y() + group.height())
        page.close()


if __name__ == "__main__":
    unittest.main()
