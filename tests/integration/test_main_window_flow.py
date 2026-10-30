"""步骤 10 收尾：真实 MainWindow + ApplicationCoordinator 的离屏流程回归。

这些用例不使用协调器替身：它们构造真实窗口、真实 ConfigPage 和真实运行契约，
固定“页面信号 → 协调器 → 运行目录 / 状态栏”的端到端接线，避免只靠属性写入的
静态门禁来判断所有权。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.filesystem.paths import PathService
from tests.integration.test_gui_two_phase_flow import _fully_valid_gui_config
from tests.ui.test_gui_import import gui_dependencies_available


_APPLICATION = None


def _shared_application():
    """整个模块共用一个 QApplication。

    重建应用会再次 ``setStyleSheet``，而 Qt 会给当时存活的每个控件重新 polish；
    多个测试类各建一次应用时，第二类要为上一类留下的窗口付出整段重样式开销。
    """
    global _APPLICATION
    if _APPLICATION is not None:
        return _APPLICATION
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        from presentation.qt.main_window import build_application

        app = build_application([])
    _APPLICATION = app
    return app


@unittest.skipUnless(gui_dependencies_available(), "当前环境缺少完整 GUI 依赖")
class MainWindowCoordinatorFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = _shared_application()

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.paths = PathService(self.root)
        self.config_path = self.paths.config_file
        self.config_path.write_text(
            json.dumps(_fully_valid_gui_config()), encoding="utf-8"
        )

    def _silence_dialogs(self):
        """离屏环境里任何模态对话框都会永久阻塞，这里统一替身并记录调用。"""
        from PySide6.QtWidgets import QMessageBox

        dialogs = {}
        for name in ("critical", "warning", "information"):
            patcher = patch(f"presentation.qt.main_window.QMessageBox.{name}")
            dialogs[name] = patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch(
            "presentation.qt.main_window.QMessageBox.question",
            return_value=QMessageBox.Yes,
        )
        dialogs["question"] = patcher.start()
        self.addCleanup(patcher.stop)
        return dialogs

    def _window(self):
        from presentation.qt.main_window import MainWindow

        # 构造窗口本身不应弹窗；构造后再安装对话框替身，保证失败路径可断言。
        window = MainWindow(paths=self.paths)
        self.addCleanup(window.close)
        return window

    def test_window_exposes_the_coordinator_state_and_initial_run_status(self):
        self._silence_dialogs()
        window = self._window()

        self.assertIs(window.coordinator.gui_data_service, window.gui_data_service)
        self.assertIs(window.coordinator.controller, window.measurement_controller)
        self.assertIs(window.config, window.coordinator.config)
        self.assertIsNone(window.prepared_run)
        self.assertEqual(
            window.config_page.run_status_label.text(), "当前运行: 未创建"
        )
        # 配置页被登记成协调器的窄接口，而不是控件宿主。
        self.assertIs(window.coordinator.config_page, window.config_page)

    def test_startup_shows_welcome_and_selection_applies_mode(self):
        self._silence_dialogs()
        window = self._window()

        self.assertIs(window.view_stack.currentWidget(), window.welcome_page)
        window._enter_test_mode(True)
        # 选好测试模式后先进“仪器连接配置”页（整屏，不占标签），再进仪器配置页。
        self.assertIs(window.view_stack.currentWidget(), window.connection_page)
        self.assertTrue(window.config_page.driver_mode_requested())
        self.assertTrue(window.config["driver_mode"]["enabled"])

        window.connection_page.next_btn.click()
        self.assertIs(window.view_stack.currentWidget(), window.workspace_page)
        self.assertIs(window.tab_widget.currentWidget(), window.config_page)

    def test_connection_page_is_not_a_tab_and_hides_the_status_panel(self):
        """仪器连接配置页占满整个界面：既不在标签栏里，也不显示进度与日志。"""
        self._silence_dialogs()
        window = self._window()

        window._enter_test_mode(False)

        pages = [window.tab_widget.widget(index) for index in range(window.tab_widget.count())]
        self.assertNotIn(window.connection_page, pages)
        self.assertTrue(window.status_panel.isHidden())

    def test_previous_step_returns_to_the_connection_page(self):
        """仪器配置页的“上一步”回到仪器连接配置页，“下一步”再回来。"""
        self._silence_dialogs()
        window = self._window()

        window._enter_test_mode(True)
        window.connection_page.next_btn.click()
        self.assertIs(window.view_stack.currentWidget(), window.workspace_page)

        window.config_page.previous_step_btn.click()
        self.assertIs(window.view_stack.currentWidget(), window.connection_page)

        window.connection_page.next_btn.click()
        self.assertIs(window.view_stack.currentWidget(), window.workspace_page)
        self.assertIs(window.tab_widget.currentWidget(), window.config_page)

    def test_two_supplies_in_one_run_are_both_frozen_with_their_identity(self):
        """步骤 10：DUT 与驱动功放各用一台电源时，两台都进运行快照（各带身份）。

        分配下拉框只选电源（步骤 9 的页面形状），通道是这台电源的固定 CH1/CH2；
        因此这里让驱动功放走``DP8B0885``、DUT 走 ``DP8B1519``，快照里两条独立条目。
        """
        self._silence_dialogs()
        saved = _fully_valid_gui_config()
        saved["driver_mode"] = {"enabled": True, "power_source": "dc_supply"}
        saved["instruments"]["power_supplies"]["DP8B0885"] = {
            "label": "DP8B0885",
            "model": "DP832A",
            "serial": "DP8B174400885",
            "device_key": "rigol technologies:dp8b174400885",
            "address": "sim::ps2",
            "enabled": True,
            "channels": {"CH1": {"voltage": {"value": 5.0}, "current": {"value": 0.5}}},
        }
        saved["device_bindings"]["power_supplies"]["DP8B0885"] = {
            "device_key": "rigol technologies:dp8b174400885",
            "serial": "DP8B174400885",
            "model": "DP832A",
            "visa_address": "sim::ps2",
            "source": "scan",
        }
        # 页面的连接只来自设备绑定：把 DUT 那台的地址落到绑定里，两台都能写进快照。
        saved["device_bindings"]["power_supplies"]["DP8B1519"]["visa_address"] = "sim::ps1"
        saved["device_bindings"]["power_supplies"]["DP8B1519"]["model"] = "DP832A"
        saved["power_supply_assignment"]["driver_amplifier"] = {
            "power_supply_count": 1,
            "supplies": {"driver": {"supply_id": "DP8B0885", "channel": ["CH1", "CH2"]}},
        }
        self.config_path.write_text(json.dumps(saved), encoding="utf-8")

        window = self._window()
        # 创建运行需要驱放模式打开（页面只在驱放模式下提交驱动功放分配）。
        window._enter_test_mode(True)
        # 分配下拉框是用户操作面：按数据（``itemData``）选中另一台电源给驱动功放。
        driver_combo = window.config_page.driver_power_combo
        driver_combo.setCurrentIndex(driver_combo.findData("DP8B0885"))
        prepared = window.coordinator.create_prepared_run()

        self.assertIsNotNone(prepared)
        mapping = prepared.configuration.run_mapping
        self.assertEqual(sorted(mapping.power_supplies), ["DP8B0885", "DP8B1519"])
        self.assertEqual(mapping.power_supplies["DP8B0885"].serial, "DP8B174400885")
        self.assertEqual(mapping.power_supplies["DP8B0885"].visa_address, "sim::ps2")
        self.assertEqual(
            [(item.channel, item.role) for item in mapping.dut_power_channels],
            [("DP8B1519/CH1", "gate"), ("DP8B1519/CH2", "drain")],
        )

    def test_no_driver_mode_drops_a_saved_driver_assignment_and_still_creates_a_run(self):
        self._silence_dialogs()
        saved = _fully_valid_gui_config()
        saved["driver_mode"] = {"enabled": True, "power_source": "dc_supply"}
        saved["power_supply_assignment"]["driver_amplifier"] = {
            "power_supply_count": 1,
            "supplies": {"driver": {"supply_id": "DP8B1519", "channel": ["CH1"]}},
        }
        self.config_path.write_text(json.dumps(saved), encoding="utf-8")

        window = self._window()
        window._enter_test_mode(False)

        self.assertTrue(window.config_page.power_assignment_group.isHidden())
        self.assertFalse(
            window.config_page.build_config()["power_supply_assignment"][
                "driver_amplifier"
            ]["power_supply_count"]
        )
        prepared = window.coordinator.create_prepared_run()

        self.assertIsNotNone(prepared)
        self.assertFalse(prepared.configuration.test_plan.driver_enabled)

    def test_driver_mode_shows_the_box_with_the_inferred_external_power(self):
        self._silence_dialogs()
        window = self._window()

        window._enter_test_mode(True)

        self.assertFalse(window.config_page.power_assignment_group.isHidden())
        # 现场配置里没有本机驱动供电通道：按自带供电推断，不占用本机电源。
        self.assertEqual(
            window.config_page.driver_power_source_combo.currentText(), "自带供电"
        )
        self.assertFalse(window.config_page.driver_power_hint.isHidden())
        self.assertIn(
            "驱动功放", window.amplifier_page.instruction_text.toPlainText()
        )

    def test_return_to_welcome_confirms_only_when_configuration_is_dirty(self):
        dialogs = self._silence_dialogs()
        window = self._window()
        window._enter_test_mode(False)

        window._return_to_welcome()
        dialogs["question"].assert_not_called()
        self.assertIs(window.view_stack.currentWidget(), window.welcome_page)

        window._enter_test_mode(False)
        window.config_page.freq_edit.setText("[9.0]")
        window._return_to_welcome()
        dialogs["question"].assert_called_once()
        self.assertIs(window.view_stack.currentWidget(), window.welcome_page)

    def test_no_driver_mode_hides_the_driver_mapping_tab(self):
        self._silence_dialogs()
        window = self._window()
        index = window._driver_mapping_tab_index
        visible_titles = lambda: [
            window.tab_widget.tabText(position)
            for position in range(window.tab_widget.count())
            if window.tab_widget.isTabVisible(position)
        ]

        window._enter_test_mode(True)
        self.assertTrue(window.tab_widget.isTabVisible(index))
        self.assertIn("驱动映射", visible_titles())

        window._enter_test_mode(False)
        self.assertFalse(window.tab_widget.isTabVisible(index))
        # 页面实例保留并继续绑定 controller，切回有驱放模式时不需要重建窗口。
        self.assertIn("driver_mapping", window.pages)
        self.assertNotIn("驱动映射", visible_titles())
        self.assertIn("线损测量", visible_titles())
        self.assertIn("功放测试", visible_titles())

    def test_configuration_page_hides_progress_and_log_panel(self):
        """仪器配置页只做配置与连接：右侧进度与实时日志不出现，切页后再回来。"""
        self._silence_dialogs()
        window = self._window()
        window._enter_test_mode(True)

        self.assertIs(window.tab_widget.currentWidget(), window.config_page)
        self.assertTrue(window.status_panel.isHidden())

        # 面板隐藏期间仍是全部日志的接收方：配置页的日志不会因为看不见而丢。
        window.add_log_message("配置页日志")
        self.assertIn("配置页日志", window.log_text.toPlainText())

        window.tab_widget.setCurrentIndex(
            window.tab_widget.indexOf(window.pages["cable_loss"])
        )
        self.assertFalse(window.status_panel.isHidden())

        window.tab_widget.setCurrentIndex(window.tab_widget.indexOf(window.config_page))
        self.assertTrue(window.status_panel.isHidden())

    def test_config_page_save_signal_writes_the_official_configuration(self):
        self._silence_dialogs()
        window = self._window()
        self.config_path.unlink()

        window.save_config()

        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertIn("test_plan", saved)
        self.assertIn("run_mapping", saved)
        self.assertIn("test_plan", window.config)

    def test_create_run_freezes_the_run_and_updates_the_page(self):
        self._silence_dialogs()
        window = self._window()

        prepared = window.coordinator.create_prepared_run()

        self.assertIsNotNone(prepared)
        self.assertIs(window.prepared_run, prepared)
        self.assertTrue(prepared.run_directory.is_dir())
        self.assertEqual(
            window.config_page.run_status_label.text(), f"当前运行: {prepared.run_id}"
        )
        # 创建运行只冻结参数：接线不再是配置字段，冻结快照与保存的配置里都没有它。
        frozen = json.loads(prepared.config_path.read_text(encoding="utf-8"))
        self.assertNotIn("wiring", frozen["run_mapping"])
        self.assertNotIn("wiring", window.config)

    def test_run_switch_keeps_the_current_run_when_the_old_port_cannot_be_released(self):
        dialogs = self._silence_dialogs()
        window = self._window()

        class _FailingPort:
            def __init__(self):
                self.close_calls = 0

            def close_all(self, *, close_rf=False):
                self.close_calls += 1
                return [RuntimeError("RF 未确认关闭")]

        port = _FailingPort()
        window.coordinator.instrument_ctrl = port

        prepared = window.coordinator.create_prepared_run()

        self.assertIsNone(prepared)
        self.assertIsNone(window.prepared_run)
        self.assertEqual(port.close_calls, 1)
        self.assertTrue(window.coordinator.has_unreleased_ports())
        self.assertEqual(
            window.config_page.run_status_label.text(), "当前运行: 未创建"
        )
        dialogs["warning"].assert_called_once()

    def test_measurement_start_is_blocked_until_a_port_is_connected(self):
        dialogs = self._silence_dialogs()
        window = self._window()

        # 没有运行、也没有端口时，页面拿不到运行契约，且失败必须可见。
        self.assertIsNone(window.coordinator.resolve_measurement_run())
        self.assertFalse(window.coordinator.amplifier_driver_mode())
        dialogs["critical"].assert_called_once()

        class _Port:
            def close_all(self, *, close_rf=False):
                return []

        window.coordinator.create_prepared_run()

        self.assertIsNotNone(window.prepared_run)
        # 已创建运行但还没有接管端口：仍然拒绝开始测量。
        self.assertIsNone(window.coordinator.resolve_measurement_run())

        self.assertTrue(window.coordinator.accept_instrument_controller(_Port()))
        self.assertIs(
            window.coordinator.resolve_measurement_run(), window.prepared_run
        )


@unittest.skipUnless(gui_dependencies_available(), "当前环境缺少完整 GUI 依赖")
class MainWindowMigrationResidueFlowTests(unittest.TestCase):
    """步骤 12：驱动模式说明刷新在真实窗口里的行为。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cls.app = _shared_application()

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.paths = PathService(self.root)
        self.paths.config_file.write_text(
            json.dumps(_fully_valid_gui_config()), encoding="utf-8"
        )
        from PySide6.QtWidgets import QMessageBox

        for name in ("critical", "warning", "information"):
            patcher = patch(f"presentation.qt.main_window.QMessageBox.{name}")
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch(
            "presentation.qt.main_window.QMessageBox.question",
            return_value=QMessageBox.Yes,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _window(self):
        from presentation.qt.main_window import MainWindow

        window = MainWindow(paths=self.paths)
        self.addCleanup(window.close)
        return window

    def test_driver_mode_switch_updates_the_amplifier_page_instruction(self):
        window = self._window()

        window.config_page.driver_mode_changed.emit(True)
        self.assertIn("驱动功放", window.amplifier_page.instruction_text.toPlainText())

        window.config_page.driver_mode_changed.emit(False)
        self.assertNotIn("驱动功放", window.amplifier_page.instruction_text.toPlainText())


if __name__ == "__main__":
    unittest.main()
