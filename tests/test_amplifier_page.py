import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QWidget

from presentation.qt.amplifier_page import AmplifierPage
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
        self.state = type("State", (), {"kind": None, "status": MeasurementStatus.IDLE, "is_active": False})()

    def start_amplifier(self, command):
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


class AmplifierPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = Controller()
        self.page = AmplifierPage(
            config_path_provider=lambda: "config.json",
            prepare_run=lambda: True,
            confirm_wiring=lambda: True,
            driver_mode_provider=lambda: True,
            connection_dialog_factory=lambda *_: Dialog(),
            plot_widget_factory=lambda parent: Plot(parent),
            error_callback=lambda _message: None,
        )
        self.page.bind_controller(self.controller)

        self.assertTrue(self.page.amplifier_test_btn.isEnabled())
        self.assertFalse(self.page.emergency_stop_btn.isEnabled())
        self.assertEqual(self.page.result_table.columnCount(), 4)

        self.assertEqual(self.controller.signals.message.slots, [])
        self.assertEqual(self.controller.signals.rejected.slots, [])

    def test_start_forwards_command_to_controller(self):
        self.page.start_measurement()
        command = self.controller.calls[0][1]
        self.assertIsInstance(command, MeasurementCommand)
        self.assertEqual(command.config_path, "config.json")
        self.assertEqual(dict(command.options), {})

    def test_realtime_data_and_result_update_page_models(self):
        self.controller.signals.data_update.emit({"frequency": "4.0", "sweep_data": {"output_power_dut": [10]}})
        self.controller.signals.result.emit(type("Reference", (), {
            "kind": MeasurementKind.AMPLIFIER,
            "value": {"results": {"4.0": {
                "compression_point": {"output_power": 20.0},
                "small_signal_gain": 12.5,
                "compression_achieved": True,
                "sweep_data": {"output_power_dut": [20]},
            }}}
        })())
        self.assertEqual(self.page.result_table.rowCount(), 1)
        self.assertEqual(self.page.result_table.item(0, 0).text(), "4.0")
        self.assertEqual(self.page.result_table.item(0, 2).text(), "12.5")
        self.assertEqual(self.page.amplifier_plot_widget.updated[-1]["frequency"], "4.0")

    def test_stop_and_error_restore_controls(self):
        self.controller.signals.state_changed.emit(type("State", (), {
            "kind": MeasurementKind.AMPLIFIER, "is_active": True,
        })())
        self.assertFalse(self.page.amplifier_test_btn.isEnabled())
        self.page.stop_measurement()
        self.assertEqual(self.controller.calls, [("stop",)])
        self.controller.signals.error.emit("失败")
        self.assertTrue(self.page.amplifier_test_btn.isEnabled())

    def test_close_disconnects_controller_signals(self):
        self.assertEqual(len(self.controller.signals.result.slots), 1)
        self.page.close()
        self.assertEqual(self.controller.signals.result.slots, [])
        self.page.close()


if __name__ == "__main__":
    unittest.main()
