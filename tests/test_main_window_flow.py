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
from tests.test_gui_import import gui_dependencies_available
from tests.test_gui_two_phase_flow import _fully_valid_gui_config


@unittest.skipUnless(gui_dependencies_available(), "当前环境缺少完整 GUI 依赖")
class MainWindowCoordinatorFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from presentation.qt.main_window import build_application

        cls.app = build_application([])

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

    @staticmethod
    def _accepted_wiring_dialog():
        from PySide6.QtWidgets import QDialog

        class _AcceptedDialog:
            def __init__(self, *_args, **_kwargs):
                pass

            def exec(self):
                return QDialog.Accepted

        return _AcceptedDialog

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

        with patch(
            "presentation.qt.main_window.ConnectionDialog",
            self._accepted_wiring_dialog(),
        ):
            prepared = window.coordinator.create_prepared_run()

        self.assertIsNotNone(prepared)
        self.assertIs(window.prepared_run, prepared)
        self.assertTrue(prepared.run_directory.is_dir())
        self.assertEqual(
            window.config_page.run_status_label.text(), f"当前运行: {prepared.run_id}"
        )
        # 接线确认已固化到冻结配置里，且窗口与协调器共享同一份配置。
        self.assertTrue(window.config["wiring"]["confirmed"])
        self.assertEqual(
            window.config["wiring"]["confirmation_source"], "create_run_dialog"
        )

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

        with patch(
            "presentation.qt.main_window.ConnectionDialog",
            self._accepted_wiring_dialog(),
        ):
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

        with patch(
            "presentation.qt.main_window.ConnectionDialog",
            self._accepted_wiring_dialog(),
        ):
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
    """步骤 12：驱动模式说明刷新与助手面板在真实窗口里的行为。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from presentation.qt.main_window import build_application

        cls.app = build_application([])

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

    def test_chat_panel_toggle_greets_once_and_keeps_the_window_state(self):
        window = self._window()

        window.toggle_chat_panel()
        first = window.chat_panel.chat_display.toPlainText()
        self.assertTrue(window.chat_visible)
        self.assertIn("AI助手", first)

        window.toggle_chat_panel()
        self.assertFalse(window.chat_visible)
        self.assertEqual(window.chat_panel.chat_display.toPlainText(), first)

        # 再次打开不会重复堆叠同一段欢迎语。
        window.toggle_chat_panel()
        self.assertEqual(window.chat_panel.chat_display.toPlainText(), first)


if __name__ == "__main__":
    unittest.main()
