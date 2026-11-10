import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QGroupBox, QWidget

from presentation.qt.amplifier_page import AmplifierPage
from presentation.qt.measurement_controller_contract import MeasurementCommand
from presentation.qt.measurement_controls import MeasurementAction
from presentation.qt.measurement_state import MeasurementKind, MeasurementStatus
from presentation.qt.theme import MEASUREMENT_LEFT_PANEL_MIN_WIDTH


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
        self.state = type("State", (), {"kind": None, "status": MeasurementStatus.IDLE, "is_active": False})()

    def start_amplifier(self, command):
        self.calls.append(("start", command))
        return True

    def stop(self):
        self.calls.append(("stop",))
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
        self.run = type("PreparedRun", (), {
            "run_id": "run-amplifier", "run_repository": object(),
            "run_directory": "runs/run-amplifier",
            "hardware_connection_path": lambda self: None,
        })()
        self.page = AmplifierPage(
            config_path_provider=lambda: "config.json",
            run_provider=lambda: self.run,
            driver_mode_provider=lambda: True,
            connection_dialog_factory=lambda *_: Dialog(),
            plot_widget_factory=lambda parent: Plot(parent),
            error_callback=lambda _message: None,
        )
        self.page.bind_controller(self.controller)

        self.assertTrue(self.page.amplifier_test_btn.isEnabled())
        self.assertFalse(self.page.stop_btn.isEnabled())
        self.assertFalse(hasattr(self.page, "result_table"))

        self.assertEqual(self.controller.signals.message.slots, [])
        self.assertEqual(self.controller.signals.rejected.slots, [])

    def test_start_forwards_command_to_controller(self):
        self.page.start_measurement()
        command = self.controller.calls[0][1]
        self.assertIsInstance(command, MeasurementCommand)
        self.assertEqual(command.config_path, "config.json")
        self.assertEqual(dict(command.options), {})
        self.assertIs(command.prepared_run, self.run)

    def test_missing_current_run_does_not_start_measurement(self):
        self.page._run_provider = lambda: None

        self.page.start_measurement()

        self.assertEqual(self.controller.calls, [])
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
            return Dialog()

        self.page._connection_dialog_factory = factory

        self.page.start_measurement()

        self.assertEqual(len(checked.calls), 1)
        self.assertEqual(checked.calls[0][0], "start")
        self.assertEqual(replacement.calls, [])

    def test_result_signal_is_not_subscribed(self):
        """测量结果只在应用层保存与归档，页面不再接收结果信号做展示。"""
        self.assertEqual(self.controller.signals.result.slots, [])

    def test_realtime_data_updates_the_browse_plot(self):
        self.controller.signals.data_update.emit(
            {"frequency": "4.0", "sweep_data": {"output_power_dut": [10]}}
        )

        self.assertEqual(self.page.amplifier_plot_widget.updated[-1]["frequency"], "4.0")
        self.assertEqual(self.page.realtime_buffer.frequencies, [4.0])

    def test_stop_and_error_restore_controls(self):
        self.controller.signals.state_changed.emit(type("State", (), {
            "kind": MeasurementKind.AMPLIFIER, "is_active": True,
        })())
        self.assertFalse(self.page.amplifier_test_btn.isEnabled())
        self.page.stop_measurement()
        self.assertEqual(self.controller.calls, [("stop",)])
        self.controller.signals.error.emit("失败")
        self.assertTrue(self.page.amplifier_test_btn.isEnabled())

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
        self.controller.signals.state_changed.emit(type("State", (), {
            "kind": MeasurementKind.AMPLIFIER, "is_active": True,
        })())
        self.assertFalse(self.page.amplifier_test_btn.isEnabled())
        self.assertTrue(self.page.stop_btn.isEnabled())

        self.controller.signals.finished.emit()
        self.assertTrue(self.page.amplifier_test_btn.isEnabled())
        self.assertFalse(self.page.stop_btn.isEnabled())

    def test_control_buttons_share_one_row(self):
        page = self.page
        page.resize(1200, 800)
        page.show()
        self.app.processEvents()
        buttons = (page.amplifier_test_btn, page.stop_btn)
        tops = {button.geometry().top() for button in buttons}
        self.assertEqual(len(tops), 1)
        lefts = [button.geometry().left() for button in buttons]
        self.assertEqual(lefts, sorted(lefts))
        page.close()

    def test_left_column_holds_only_controls_and_plot_takes_the_right(self):
        page = self.page
        page.resize(1600, 900)
        page.show()
        self.app.processEvents()
        titles = [box.title() for box in page.control_panel.findChildren(QGroupBox)]
        self.assertEqual(titles, ["测量控制"])
        # 接线说明只保留连接图按钮，按钮归入测量控制。
        self.assertEqual(page.show_amp_diagram_btn.parent().title(), "测量控制")
        # 左栏宽度取 theme 里的共用常量，与另两个测量页保持一致。
        self.assertEqual(page.control_panel.width(), MEASUREMENT_LEFT_PANEL_MIN_WIDTH)
        self.assertLess(
            page.control_panel.x() + page.control_panel.width(),
            page.amplifier_plot_widget.x(),
        )
        page.close()

    def test_status_slot_sits_below_the_measurement_controls(self):
        """左栏在“测量控制”下面留出状态区：进度与日志由窗口挂进来。"""
        page = self.page
        page.resize(1200, 900)
        page.show()
        self.app.processEvents()
        group = next(box for box in page.control_panel.findChildren(QGroupBox))
        self.assertIs(page.status_slot.parentWidget(), page.control_panel)
        self.assertGreaterEqual(page.status_slot.y(), group.y() + group.height())
        page.close()

    def test_close_disconnects_controller_signals(self):
        self.assertEqual(len(self.controller.signals.data_update.slots), 1)
        self.page.close()
        self.assertEqual(self.controller.signals.data_update.slots, [])
        self.page.close()

    def test_connection_diagram_follows_the_driver_mode(self):
        opened = []
        self.page._connection_dialog_factory = lambda diagram_type, _parent: (
            opened.append(diagram_type) or Dialog()
        )

        self.page._driver_mode_provider = lambda: True
        self.page.show_connection_diagram()
        self.page._driver_mode_provider = lambda: False
        self.page.show_connection_diagram()

        self.assertEqual(opened, ["amplifier_test", "amplifier_test_no_driver"])


if __name__ == "__main__":
    unittest.main()
