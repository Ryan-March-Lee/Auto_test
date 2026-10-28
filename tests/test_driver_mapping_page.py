import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget

from application.dto import DriverPowerMappingMeasurementResult, DriverPowerPoint
from presentation.qt.driver_mapping_page import DriverMappingPage
from presentation.qt.measurement_controller_contract import MeasurementCommand
from presentation.qt.measurement_state import (
    DriverMappingPageResultState,
    MeasurementKind,
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
            "stopped", "error", "rejected", "state_changed", "archival",
        ):
            setattr(self, name, Signal())


class Controller:
    def __init__(self):
        self.signals = Signals()
        self.calls = []
        self.state = type(
            "State", (), {"kind": None, "status": MeasurementStatus.IDLE, "is_active": False}
        )()

    def start_driver_mapping(self, command):
        self.calls.append(("start", command))
        return True

    def stop(self):
        self.calls.append(("stop",))
        return True

    def emergency_stop(self):
        self.calls.append(("emergency_stop",))
        return True


class Dialog:
    def __init__(self, result=QDialog.Accepted):
        self.result = result

    def exec(self):
        return self.result


class Plot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.updated = []

    def update_plot(self, data):
        self.updated.append(data)


class DriverMappingPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = Controller()
        self.dialog = Dialog()
        self.prepared = []
        self.confirmed = []
        self.logs = []
        self.errors = []
        self.realtime = []
        self.clears = []
        self.run = type("PreparedRun", (), {
            "run_id": "run-driver", "run_repository": object(),
            "has_run_contract": True, "run_directory": "runs/run-driver",
            "hardware_connection_path": lambda self: None,
        })()
        self.page = DriverMappingPage(
            config_path_provider=lambda: "config.json",
            run_provider=lambda: self.prepared.append(True) or self.run,
            confirm_wiring=lambda: self.confirmed.append(True) or True,
            connection_dialog_factory=lambda *_: self.dialog,
            plot_widget_factory=lambda parent: Plot(parent),
            realtime_data_callback=self.realtime.append,
            clear_realtime_callback=lambda: self.clears.append(True),
            log_callback=self.logs.append,
            error_callback=self.errors.append,
        )
        self.page.bind_controller(self.controller)

        self.assertTrue(self.page.driver_mapping_btn.isEnabled())
        self.assertFalse(self.page.driver_stop_btn.isEnabled())
        self.assertFalse(self.page.driver_emergency_stop_btn.isEnabled())

        self.assertEqual(self.controller.signals.message.slots, [])
        self.assertEqual(self.controller.signals.rejected.slots, [])

    def test_start_forwards_explicit_command_after_preparation(self):
        self.page.start_measurement()
        self.assertEqual(self.prepared, [True])
        self.assertEqual(self.confirmed, [True])
        self.assertEqual(self.controller.calls[0][0], "start")
        self.assertIsInstance(self.controller.calls[0][1], MeasurementCommand)
        command = self.controller.calls[0][1]
        self.assertEqual(command.config_path, "config.json")
        self.assertIs(command.prepared_run, self.run)
        self.assertEqual(self.clears, [True])

    def test_missing_input_or_rejected_wiring_does_not_start(self):
        self.page._run_provider = lambda: None
        self.page.start_measurement()
        self.assertEqual(self.controller.calls, [])
        self.page._run_provider = lambda: type(
            "LegacyRun", (), {"run_id": "legacy", "run_repository": object()}
        )()
        self.page.start_measurement()
        self.assertEqual(self.controller.calls, [])
        self.dialog.result = QDialog.Rejected
        self.page._run_provider = lambda: self.run
        self.page.start_measurement()
        self.assertEqual(self.controller.calls, [])

    def test_power_mapping_is_converted_to_plot_display_model(self):
        self.controller.signals.data_update.emit({
            "frequency": "1.0", "sweep_data": {"input_power_sg": [-10], "output_power_driver": [2]},
        })
        self.controller.signals.data_update.emit({
            "frequency": "2.0", "sweep_data": {"input_power_sg": [-9], "output_power_driver": [3]},
        })
        reference = type(
            "Reference", (), {
                "kind": MeasurementKind.DRIVER_MAPPING,
                "value": DriverPowerMappingMeasurementResult(
                    run_id="run-driver",
                    status=MeasurementStatus.COMPLETED,
                    points=(
                        DriverPowerPoint(
                            frequency_ghz="3.0",
                            input_power_dbm=-8.0,
                            output_power_dbm=4.0,
                        ),
                    ),
                ),
            }
        )()
        self.controller.signals.result.emit(reference)
        self.assertEqual(self.page._frequency_list, ["1.0", "2.0", "3.0"])
        self.assertEqual(self.page.driver_plot_widget.updated[-1]["frequency"], "3.0")
        self.assertEqual(
            self.page.driver_plot_widget.updated[-1]["sweep_data"]["output_power_driver"], [4.0]
        )
        self.assertEqual(self.realtime[-1]["frequency"], "3.0")

    def test_invalid_result_rows_are_reported_and_skipped(self):
        reference = type(
            "Reference", (), {
                "kind": MeasurementKind.DRIVER_MAPPING,
                "value": DriverMappingPageResultState(
                    rows=({"frequency": "1.0", "input_power": "bad", "output_power": None},)
                ),
            }
        )()
        self.controller.signals.result.emit(reference)
        self.assertEqual(self.page._frequency_list, [])
        self.assertEqual(len(self.errors), 1)

    def test_non_formal_result_is_reported_and_not_displayed(self):
        reference = type(
            "Reference", (), {
                "kind": MeasurementKind.DRIVER_MAPPING,
                "value": {"power_mapping": {"3.0": {"-8.0": 4.0}}},
            }
        )()
        self.controller.signals.result.emit(reference)
        self.assertEqual(self.page._frequency_list, [])
        self.assertEqual(self.errors, ["驱动映射测量结果不是正式结果模型，无法显示"])

    def test_stop_error_and_finished_update_page_state(self):
        self.controller.signals.state_changed.emit(
            type("State", (), {"kind": MeasurementKind.DRIVER_MAPPING, "is_active": True})()
        )
        self.assertFalse(self.page.driver_mapping_btn.isEnabled())
        self.assertTrue(self.page.driver_stop_btn.isEnabled())
        self.assertTrue(self.page.driver_emergency_stop_btn.isEnabled())
        self.page.stop_measurement()
        self.assertEqual(self.controller.calls, [("stop",)])
        self.controller.signals.stopped.emit("用户停止")
        self.assertIn("测量已停止: 用户停止", self.logs)
        self.controller.signals.error.emit("测量失败")
        self.assertEqual(self.errors, ["测量失败"])
        self.controller.state = type(
            "State", (), {"kind": MeasurementKind.DRIVER_MAPPING, "is_active": True}
        )()
        self.controller.signals.finished.emit()
        self.assertIn("测量完成！", self.logs)

    def test_other_measurement_terminal_events_are_ignored(self):
        self.controller.state = type(
            "State", (), {"kind": MeasurementKind.AMPLIFIER, "is_active": True}
        )()
        self.controller.signals.error.emit("其他测量失败")
        self.controller.signals.stopped.emit("其他测量停止")
        self.controller.signals.finished.emit()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.logs, [])

    def test_repeated_start_is_rejected_before_preparation(self):
        self.controller.state = type(
            "State", (), {"kind": MeasurementKind.DRIVER_MAPPING, "is_active": True}
        )()
        self.page.start_measurement()
        self.assertEqual(self.controller.calls, [])
        self.assertEqual(self.prepared, [])
        self.assertIn("已有测量正在运行", self.logs)

    @patch("presentation.qt.driver_mapping_page.QMessageBox.question", return_value=QMessageBox.Yes)
    def test_emergency_stop_uses_controller_emergency_operation(self, _question):
        self.page.emergency_stop_measurement()
        self.assertEqual(self.controller.calls, [("emergency_stop",)])

    def test_control_buttons_share_one_row(self):
        page = self.page
        page.resize(1200, 800)
        page.show()
        self.app.processEvents()
        buttons = (
            page.driver_mapping_btn,
            page.driver_stop_btn,
            page.driver_cancel_btn,
            page.driver_emergency_stop_btn,
        )
        tops = {button.geometry().top() for button in buttons}
        self.assertEqual(len(tops), 1)
        lefts = [button.geometry().left() for button in buttons]
        self.assertEqual(lefts, sorted(lefts))
        page.close()

    def test_left_column_holds_controls_and_plot_takes_the_right(self):
        from PySide6.QtWidgets import QGroupBox

        page = self.page
        page.resize(1400, 800)
        page.show()
        self.app.processEvents()
        titles = [box.title() for box in page.control_panel.findChildren(QGroupBox)]
        self.assertEqual(titles, ["连接说明", "测量控制", "归档结果"])
        self.assertLessEqual(page.control_panel.width(), 430)
        self.assertLess(
            page.control_panel.x() + page.control_panel.width(),
            page.driver_plot_widget.x(),
        )
        page.close()


if __name__ == "__main__":
    unittest.main()
