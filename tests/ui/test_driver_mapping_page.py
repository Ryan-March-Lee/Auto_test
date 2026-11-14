import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QLabel, QWidget

from application.dto import DriverMappingRawPoint, DriverPowerMappingMeasurementResult
from presentation.qt.driver_mapping_page import DriverMappingPage
from presentation.qt.measurement_controller_contract import MeasurementCommand
from presentation.qt.measurement_controls import MeasurementAction
from presentation.qt.measurement_state import (
    DriverMappingPageResultState,
    MeasurementKind,
    MeasurementStatus,
)
from presentation.qt.stage_summary import NOT_STARTED, StageMetric
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
        self.state = type(
            "State", (), {"kind": None, "status": MeasurementStatus.IDLE, "is_active": False}
        )()

    def start_driver_mapping(self, command):
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


class DriverMappingPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = Controller()
        self.dialog = Dialog()
        self.prepared = []
        self.dialogs = []
        self.logs = []
        self.errors = []
        self.realtime = []
        self.clears = []
        self.run = type("PreparedRun", (), {
            "run_id": "run-driver", "run_repository": object(),
            "run_directory": "runs/run-driver",
            "hardware_connection_path": lambda self: None,
        })()
        self.page = DriverMappingPage(
            config_path_provider=lambda: "config.json",
            run_provider=lambda: self.prepared.append(True) or self.run,
            connection_dialog_factory=lambda *_: self.dialogs.append(True) or self.dialog,
            plot_widget_factory=lambda parent: Plot(parent),
            realtime_data_callback=self.realtime.append,
            clear_realtime_callback=lambda: self.clears.append(True),
            log_callback=self.logs.append,
            error_callback=self.errors.append,
        )
        self.page.bind_controller(self.controller)

        self.assertTrue(self.page.driver_mapping_btn.isEnabled())
        self.assertFalse(self.page.driver_stop_btn.isEnabled())

        self.assertEqual(self.controller.signals.message.slots, [])
        self.assertEqual(self.controller.signals.rejected.slots, [])

    def _select_cable_loss(
        self,
        filename: str = "20261111T010203Z_a1b2c3d4.json",
        created_at: str = "2026-11-11T01:02:03+00:00",
    ) -> None:
        self.page.show_loaded_cable_loss(
            path=f"test_results/cable_loss/{filename}",
            filename=filename,
            created_at=created_at,
            sha256="a" * 64,
            size_bytes=128,
        )

    def test_start_forwards_explicit_command_after_preparation(self):
        self._select_cable_loss()
        self.page.start_measurement()
        self.assertEqual(self.prepared, [True])
        self.assertEqual(self.controller.calls[0][0], "start")
        self.assertIsInstance(self.controller.calls[0][1], MeasurementCommand)
        command = self.controller.calls[0][1]
        self.assertEqual(command.config_path, "config.json")
        self.assertIs(command.prepared_run, self.run)
        self.assertEqual(
            command.cable_loss_path,
            Path("test_results") / "cable_loss" / "20261111T010203Z_a1b2c3d4.json",
        )
        self.assertEqual(command.cable_loss_digest.sha256, "a" * 64)
        self.assertEqual(command.cable_loss_digest.size_bytes, 128)
        self.assertIsNone(command.driver_mapping_path)
        self.assertEqual(self.clears, [True])

    def test_start_without_selected_cable_loss_does_not_connect_or_start(self):
        self.page.start_measurement()
        self.assertEqual(self.prepared, [])
        self.assertEqual(self.dialogs, [])
        self.assertEqual(self.controller.calls, [])
        self.assertEqual(self.errors, ["请先加载驱动映射使用的线损测量结果"])
        self.assertIn("未选择线损测量结果，驱动映射未启动", self.logs)
        self.assertNotIn("开始驱动功放映射...", self.logs)

    def test_a_busy_measurement_is_reported_before_the_missing_cable_loss(self):
        # 另一页测量运行时“开始”按钮仍可能被点到：先报告更直接的原因，
        # 不要误报线损缺失，也不要读取当前运行。
        self.controller.state = type(
            "State",
            (),
            {
                "kind": MeasurementKind.CABLE_LOSS,
                "status": MeasurementStatus.RUNNING,
                "is_active": True,
            },
        )()
        self.page.start_measurement()

        self.assertEqual(self.prepared, [])
        self.assertEqual(self.dialogs, [])
        self.assertEqual(self.controller.calls, [])
        self.assertEqual(self.errors, [])
        self.assertIn("已有测量正在运行", self.logs)
        self.assertNotIn("未选择线损测量结果，驱动映射未启动", self.logs)

    def test_missing_input_or_rejected_dialog_does_not_start(self):
        self._select_cable_loss()
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
                    raw_points=(
                        DriverMappingRawPoint(
                            frequency_ghz="3.0",
                            source_power_dbm=-8.0,
                            driver_output_power_dbm=4.0,
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


    def test_stop_error_and_finished_update_page_state(self):
        self.controller.signals.state_changed.emit(
            type("State", (), {"kind": MeasurementKind.DRIVER_MAPPING, "is_active": True})()
        )
        self.assertFalse(self.page.driver_mapping_btn.isEnabled())
        self.assertTrue(self.page.driver_stop_btn.isEnabled())
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
        self._select_cable_loss()
        self.controller.state = type(
            "State", (), {"kind": MeasurementKind.DRIVER_MAPPING, "is_active": True}
        )()
        self.page.start_measurement()
        self.assertEqual(self.controller.calls, [])
        self.assertEqual(self.prepared, [])
        self.assertIn("已有测量正在运行", self.logs)

    def test_load_button_only_emits_the_selection_request(self):
        requests = []
        self.page.load_cable_loss_requested.connect(lambda: requests.append(True))
        self.page.load_cable_loss_btn.click()
        self.assertEqual(requests, [True])

    def test_loaded_cable_loss_shows_source_and_measurement_time(self):
        filename = "20261111T010203Z_a1b2c3d4.json"
        created_at = "2026-11-11T01:02:03+00:00"
        self._select_cable_loss(filename, created_at)

        selection = self.page.cable_loss_selection
        self.assertIsNotNone(selection)
        self.assertEqual(selection.path, f"test_results/cable_loss/{filename}")
        self.assertEqual(selection.filename, filename)
        self.assertEqual(selection.created_at, created_at)
        label = self.page.cable_loss_source_label.text()
        self.assertIn(filename, label)
        self.assertIn(created_at, label)
        self.assertIn(f"已选择驱动映射线损测量结果：{filename}", self.logs[-1])

    def test_selection_survives_page_switch_and_can_be_replaced(self):
        self._select_cable_loss("first.json", "2026-11-11T01:02:03+00:00")
        self.page.on_deactivated()
        self.page.on_activated()
        self.assertEqual(self.page.cable_loss_selection.filename, "first.json")

        self._select_cable_loss("second.json", "2026-11-12T04:05:06+00:00")
        self.assertEqual(self.page.cable_loss_selection.filename, "second.json")
        self.assertIn("second.json", self.page.cable_loss_source_label.text())

    def test_without_a_selection_the_label_prompts_for_cable_loss(self):
        self.assertIsNone(self.page.cable_loss_selection)
        self.assertIn("未选择线损结果", self.page.cable_loss_source_label.text())


    def test_left_column_holds_controls_and_plot_takes_the_right(self):
        from PySide6.QtWidgets import QFrame

        page = self.page
        page.resize(1400, 800)
        page.show()
        self.app.processEvents()
        result = page.findChild(QFrame, "measurementResultSurface")
        self.assertIsNotNone(result)
        # 左栏宽度取 theme 里的共用常量，与另两个测量页保持一致。
        self.assertEqual(page.control_panel.width(), MEASUREMENT_LEFT_PANEL_MIN_WIDTH)
        self.assertLess(
            page.control_panel.x() + page.control_panel.width(),
            result.x(),
        )
        self.assertIs(page.driver_plot_widget.parentWidget(), result)
        page.close()







    def test_calibration_inputs_live_in_the_control_surface(self):
        from PySide6.QtWidgets import QFrame

        control = self.page.findChild(QFrame, "measurementSurface")
        self.assertIs(self.page.load_cable_loss_btn.parentWidget(), control)
        self.assertIs(self.page.cable_loss_source_label.parentWidget(), control)
        captions = [
            label.text()
            for label in control.findChildren(QLabel)
            if label.property("role") == "sectionCaption"
        ]
        self.assertEqual(captions, ["测量控制", "校准输入"])

    def test_result_surface_wraps_the_plot(self):
        from PySide6.QtWidgets import QFrame

        result = self.page.findChild(QFrame, "measurementResultSurface")
        self.assertIs(self.page.driver_plot_widget.parentWidget(), result)
        captions = [
            label.text()
            for label in result.findChildren(QLabel)
            if label.property("role") == "sectionCaption"
        ]
        self.assertEqual(captions, ["测量结果"])



    def test_browsing_back_updates_the_current_frequency_and_announces_it(self):
        for frequency in ("2.4", "3.5"):
            self.page.update_realtime({"frequency": frequency})
        events = []
        self.page.stage_summary_changed.connect(lambda: events.append(True))

        self.page.previous_frequency()

        self.assertEqual(
            self.page.stage_summary().metrics[1],
            StageMetric("当前频点", "2.4 GHz"),
        )
        self.assertTrue(events)




if __name__ == "__main__":
    unittest.main()
