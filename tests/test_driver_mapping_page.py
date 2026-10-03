import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget

from presentation.qt.driver_mapping_page import DriverMappingPage
from presentation.qt.measurement_controller_contract import MeasurementCommand
from presentation.qt.measurement_state import MeasurementKind, MeasurementStatus


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
            "stopped", "error", "rejected", "state_changed",
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
        self.page = DriverMappingPage(
            config_path_provider=lambda: "config.json",
            prepare_run=lambda: self.prepared.append(True) or True,
            confirm_wiring=lambda: self.confirmed.append(True) or True,
            connection_dialog_factory=lambda *_: self.dialog,
            plot_widget_factory=lambda parent: Plot(parent),
            realtime_data_callback=self.realtime.append,
            clear_realtime_callback=lambda: self.clears.append(True),
            log_callback=self.logs.append,
            error_callback=self.errors.append,
        )
        self.page.bind_controller(self.controller)

        self.assertEqual(self.controller.signals.message.slots, [])
        self.assertEqual(self.controller.signals.rejected.slots, [])

    def test_start_forwards_explicit_command_after_preparation(self):
        self.page.start_measurement()
        self.assertEqual(self.prepared, [True])
        self.assertEqual(self.confirmed, [True])
        self.assertEqual(self.controller.calls[0][0], "start")
        self.assertIsInstance(self.controller.calls[0][1], MeasurementCommand)
        self.assertEqual(self.controller.calls[0][1].config_path, "config.json")
        self.assertEqual(self.clears, [True])

    def test_missing_input_or_rejected_wiring_does_not_start(self):
        self.page._prepare_run = lambda: False
        self.page.start_measurement()
        self.assertEqual(self.controller.calls, [])
        self.dialog.result = QDialog.Rejected
        self.page._prepare_run = lambda: True
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
                "value": {"power_mapping": {"3.0": {"-8.0": 4.0}}},
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
                "value": {"power_mapping": {"1.0": {"bad": None}}},
            }
        )()
        self.controller.signals.result.emit(reference)
        self.assertEqual(self.page._frequency_list, [])
        self.assertEqual(len(self.errors), 1)

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


if __name__ == "__main__":
    unittest.main()
