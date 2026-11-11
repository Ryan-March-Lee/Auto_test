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
        self.logs = []
        self.errors = []
        self.dialogs = []
        self.prepared = []
        self.page = AmplifierPage(
            config_path_provider=lambda: "config.json",
            run_provider=lambda: self.prepared.append(True) or self.run,
            driver_mode_provider=lambda: True,
            connection_dialog_factory=lambda *_: self.dialogs.append(True) or Dialog(),
            plot_widget_factory=lambda parent: Plot(parent),
            log_callback=self.logs.append,
            error_callback=self.errors.append,
        )
        self.page.bind_controller(self.controller)

        self.assertTrue(self.page.amplifier_test_btn.isEnabled())
        self.assertFalse(self.page.stop_btn.isEnabled())
        self.assertFalse(hasattr(self.page, "result_table"))

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
        )

    def _select_driver_mapping(
        self,
        filename: str = "20261110T050607Z_0f1e2d3c.json",
        created_at: str = "2026-11-10T05:06:07+00:00",
    ) -> None:
        self.page.show_loaded_driver_mapping(
            path=f"test_results/driver_mapping/{filename}",
            filename=filename,
            created_at=created_at,
        )

    def _select_both_inputs(self) -> None:
        self._select_cable_loss()
        self._select_driver_mapping()

    def test_start_forwards_command_to_controller(self):
        self._select_both_inputs()
        self.page.start_measurement()
        command = self.controller.calls[0][1]
        self.assertIsInstance(command, MeasurementCommand)
        self.assertEqual(command.config_path, "config.json")
        self.assertEqual(dict(command.options), {})
        self.assertIs(command.prepared_run, self.run)

    def test_missing_current_run_does_not_start_measurement(self):
        self._select_both_inputs()
        self.page._run_provider = lambda: None

        self.page.start_measurement()

        self.assertEqual(self.controller.calls, [])
        self.page._run_provider = lambda: type(
            "LegacyRun", (), {"run_id": "legacy", "run_repository": object()}
        )()
        self.page.start_measurement()
        self.assertEqual(self.controller.calls, [])

    def test_start_submits_to_the_controller_checked_before_the_dialog(self):
        self._select_both_inputs()
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

    def test_load_buttons_only_emit_the_selection_requests(self):
        cable_requests = []
        driver_requests = []
        self.page.load_cable_loss_requested.connect(
            lambda: cable_requests.append(True)
        )
        self.page.load_driver_mapping_requested.connect(
            lambda: driver_requests.append(True)
        )

        self.page.load_cable_loss_btn.click()
        self.page.load_driver_mapping_btn.click()

        self.assertEqual(cable_requests, [True])
        self.assertEqual(driver_requests, [True])

    def test_selected_inputs_show_their_source_and_measurement_time(self):
        # 两类输入互不关联：来源文件与测量时间可以来自不同日期和运行。
        self._select_cable_loss("20261111T010203Z_a1b2c3d4.json", "2026-11-11T01:02:03+00:00")
        self._select_driver_mapping("20261110T050607Z_0f1e2d3c.json", "2026-11-10T05:06:07+00:00")

        cable = self.page.cable_loss_selection
        driver = self.page.driver_mapping_selection
        self.assertIsNotNone(cable)
        self.assertIsNotNone(driver)
        self.assertEqual(cable.filename, "20261111T010203Z_a1b2c3d4.json")
        self.assertEqual(driver.filename, "20261110T050607Z_0f1e2d3c.json")
        self.assertNotEqual(cable.created_at, driver.created_at)
        self.assertIn(cable.filename, self.page.cable_loss_source_label.text())
        self.assertIn(cable.created_at, self.page.cable_loss_source_label.text())
        self.assertIn(driver.filename, self.page.driver_mapping_source_label.text())
        self.assertIn(driver.created_at, self.page.driver_mapping_source_label.text())
        self.assertIn("已选择主功放线损测量结果", self.logs[-2])
        self.assertIn("已选择主功放驱动映射测量结果", self.logs[-1])

    def test_without_selections_the_labels_prompt_per_driver_mode(self):
        self.assertIsNone(self.page.cable_loss_selection)
        self.assertIsNone(self.page.driver_mapping_selection)
        self.assertIn("未选择线损结果", self.page.cable_loss_source_label.text())
        self.assertIn(
            "未选择驱动映射结果", self.page.driver_mapping_source_label.text()
        )
        # 驱动关闭时入口与来源行一起隐藏，不再提示加载驱动映射。
        self.page._driver_mode_provider = lambda: False
        self.page.on_activated()
        self.assertTrue(self.page.load_driver_mapping_btn.isHidden())
        self.assertTrue(self.page.driver_mapping_source_label.isHidden())
        self.assertIn(
            "无驱放测试不需要驱动映射结果",
            self.page.driver_mapping_source_label.text(),
        )
        self.page._driver_mode_provider = lambda: True
        self.page.on_activated()
        self.assertFalse(self.page.load_driver_mapping_btn.isHidden())
        self.assertFalse(self.page.driver_mapping_source_label.isHidden())

    def test_start_without_required_inputs_neither_connects_nor_starts(self):
        self.page.start_measurement()

        self.assertEqual(self.prepared, [])
        self.assertEqual(self.dialogs, [])
        self.assertEqual(self.controller.calls, [])
        self.assertEqual(
            self.errors,
            ["请先加载主功放使用的线损测量结果和驱动映射测量结果"],
        )
        self.assertIn("未选择主功放必需的校准输入，主功放测试未启动", self.logs)
        self.assertNotIn("开始主功放测试...", self.logs)

    def test_driver_on_requires_both_inputs(self):
        # 有驱放：只选线损仍不够，驱动映射也必须显式选择。
        self._select_cable_loss()
        self.page.start_measurement()
        self.assertEqual(self.errors, ["请先加载主功放使用的驱动映射测量结果"])
        self.assertEqual(self.controller.calls, [])

    def test_driver_off_requires_only_cable_loss(self):
        # 无驱放：同一份线损就满足要求，不需要、也不要求驱动映射选择。
        self._select_cable_loss()
        self.page._driver_mode_provider = lambda: False
        self.page.start_measurement()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.controller.calls[0][0], "start")

    def test_a_busy_measurement_is_reported_before_missing_inputs(self):
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
        self.assertNotIn("未选择主功放必需的校准输入，主功放测试未启动", self.logs)

    def test_driver_mode_change_is_revalidated_before_each_start(self):
        # 先用无驱放规则：只有线损即可。
        self._select_cable_loss()
        self.page._driver_mode_provider = lambda: False
        self.page.start_measurement()
        self.assertEqual(len(self.controller.calls), 1)

        # 配置改成有驱放后，同一个页面在下一次开始时重新要求驱动映射。
        self.controller.calls.clear()
        self.page._driver_mode_provider = lambda: True
        self.page.start_measurement()
        self.assertEqual(self.controller.calls, [])
        self.assertEqual(
            self.errors[-1], "请先加载主功放使用的驱动映射测量结果"
        )

    def test_driver_off_ignores_a_stale_driver_mapping_selection(self):
        # 先在有驱放下选好两类输入，再切到无驱放：旧的驱动映射选择保留但不参与检查。
        self._select_both_inputs()
        self.page._driver_mode_provider = lambda: False
        self.page.on_activated()
        self.page.start_measurement()

        self.assertEqual(self.errors, [])
        self.assertEqual(self.controller.calls[0][0], "start")
        self.assertTrue(self.page.driver_mapping_source_label.isHidden())
        self.assertIsNotNone(self.page.driver_mapping_selection)

    def test_switching_back_to_driver_on_rechecks_the_saved_selection(self):
        self._select_both_inputs()
        self.page._driver_mode_provider = lambda: False
        self.page.on_activated()
        self.page._driver_mode_provider = lambda: True
        self.page.on_activated()

        # 切回有驱放不需要重新选择：已有选择重新成为必需输入并照常通过。
        self.assertFalse(self.page.load_driver_mapping_btn.isHidden())
        self.page.start_measurement()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.controller.calls[0][0], "start")

    def test_selection_survives_page_switch_and_can_be_replaced(self):
        self._select_both_inputs()
        self.page.on_deactivated()
        self.page.on_activated()
        self.assertEqual(
            self.page.cable_loss_selection.filename, "20261111T010203Z_a1b2c3d4.json"
        )
        self.assertEqual(
            self.page.driver_mapping_selection.filename,
            "20261110T050607Z_0f1e2d3c.json",
        )

        self._select_cable_loss("second.json", "2026-11-12T04:05:06+00:00")
        self.assertEqual(self.page.cable_loss_selection.filename, "second.json")
        self.assertIn("second.json", self.page.cable_loss_source_label.text())
        # 替换线损不会影响驱动映射选择。
        self.assertEqual(
            self.page.driver_mapping_selection.filename,
            "20261110T050607Z_0f1e2d3c.json",
        )

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
