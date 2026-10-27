import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


class MainWindowTests(unittest.TestCase):
    def test_build_application_reuses_qapplication(self):
        from PySide6.QtWidgets import QApplication
        from presentation.qt.main_window import build_application

        first = build_application([])
        second = build_application([])
        self.assertIs(first, second)
        self.assertIs(first, QApplication.instance())

    def test_build_application_forces_light_palette(self):
        """系统深色模式不得把未设置样式的容器底色变成深色（表现为界面“黑框”）。"""
        from PySide6.QtGui import QPalette
        from presentation.qt.main_window import build_application

        app = build_application([])
        for role in (QPalette.Window, QPalette.Base, QPalette.Button):
            color = app.palette().color(role)
            self.assertGreater(
                color.lightness(),
                200,
                f"{role} 颜色为 {color.name()}，深色调色板会让分组框边距和容器间隙露出黑底",
            )

    def test_launcher_uses_new_main_window_module(self):
        import launcher

        fake_module = type(sys)("presentation.qt.main_window")
        fake_module.main = Mock()
        with patch.dict(sys.modules, {"presentation.qt.main_window": fake_module}):
            with patch.object(launcher, "PROJECT_ROOT", ROOT):
                self.assertTrue(
                    launcher.launch_gui_version(
                        "enhanced", {"PySide6": {"installed": True}}, True
                    )
                )
        fake_module.main.assert_called_once_with()

    def test_new_main_window_has_no_reverse_compatibility_import(self):
        source = (ROOT / "presentation" / "qt" / "main_window.py").read_text(
            encoding="utf-8-sig"
        )
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        self.assertNotIn("enhanced_main_gui", imported)


class MainWindowCloseEventTests(unittest.TestCase):
    """关闭窗口：确认只做一次，之后的停止动作视为已提交。"""

    def _window(self, *, measurement_active=False, worker_running=False,
                shutdown_ok=True, stop_ok=True, release_ok=True,
                unreleased=False, assistant_stop_ok=True):
        from PySide6.QtWidgets import QMessageBox

        from presentation.qt.main_window import MainWindow

        controller = Mock()
        controller.state.is_active = measurement_active
        controller.shutdown.return_value = shutdown_ok
        coordinator = Mock()
        coordinator.instrument_worker_running.return_value = worker_running
        coordinator.stop_instrument_worker.return_value = stop_ok
        coordinator.release_instrument_port.return_value = release_ok
        coordinator.has_unreleased_ports.return_value = unreleased

        chat_panel = Mock()
        chat_panel.shutdown.return_value = assistant_stop_ok

        tab_signal = Mock()
        pages = {"first": Mock(), "second": Mock()}
        window = SimpleNamespace(
            measurement_controller=controller,
            coordinator=coordinator,
            chat_panel=chat_panel,
            tab_widget=SimpleNamespace(currentChanged=tab_signal),
            pages=pages,
            add_log_message=Mock(),
        )
        window.ASSISTANT_SHUTDOWN_TIMEOUT_MS = MainWindow.ASSISTANT_SHUTDOWN_TIMEOUT_MS
        window._on_page_changed = Mock()
        # 未通过 __init__ 构造的替身窗口需要显式绑定这三个确认/收尾方法。
        window._confirm_exit = lambda: MainWindow._confirm_exit(window)
        window._confirm_exit_with_unreleased_ports = (
            lambda: MainWindow._confirm_exit_with_unreleased_ports(window)
        )
        window._shutdown_assistant = lambda: MainWindow._shutdown_assistant(window)
        window._disconnect_page_signals = lambda: MainWindow._disconnect_page_signals(
            window
        )
        event = Mock()
        self.Yes = QMessageBox.Yes
        self.No = QMessageBox.No
        return MainWindow, window, event, pages, tab_signal

    def test_close_event_disconnects_tabs_and_closes_pages(self):
        MainWindow, window, event, pages, tab_signal = self._window()

        MainWindow.closeEvent(window, event)

        window.coordinator.release_instrument_port.assert_called_once_with()
        tab_signal.disconnect.assert_called_once_with(window._on_page_changed)
        for page in pages.values():
            page.close.assert_called_once_with()
        event.accept.assert_called_once_with()

    def test_close_event_asks_only_once_before_stopping_everything(self):
        MainWindow, window, event, _pages, _tab = self._window(
            measurement_active=True, worker_running=True
        )

        with patch(
            "presentation.qt.main_window.QMessageBox.question", return_value=self.Yes
        ) as question:
            MainWindow.closeEvent(window, event)

        # 确认只在开头做一次：此前的实现会在停掉测量后再问一遍，
        # 用户第二次拒绝就会留下“测量已停、窗口还在”的半提交状态。
        self.assertEqual(question.call_count, 1)
        window.measurement_controller.shutdown.assert_called_once_with(timeout_ms=5000)
        window.coordinator.stop_instrument_worker.assert_called_once_with(5000)
        event.accept.assert_called_once_with()

    def test_close_event_cancelled_confirmation_changes_nothing(self):
        MainWindow, window, event, pages, _tab = self._window(
            measurement_active=True, worker_running=True
        )

        with patch(
            "presentation.qt.main_window.QMessageBox.question", return_value=self.No
        ):
            MainWindow.closeEvent(window, event)

        window.measurement_controller.shutdown.assert_not_called()
        window.coordinator.stop_instrument_worker.assert_not_called()
        window.coordinator.release_instrument_port.assert_not_called()
        for page in pages.values():
            page.close.assert_not_called()
        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()

    def test_close_event_keeps_window_open_when_measurement_will_not_stop(self):
        MainWindow, window, event, _pages, _tab = self._window(
            measurement_active=True, shutdown_ok=False
        )

        with patch(
            "presentation.qt.main_window.QMessageBox.question", return_value=self.Yes
        ):
            MainWindow.closeEvent(window, event)

        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()
        self.assertTrue(
            any(
                "未能在关闭期限内停止" in str(call.args[0])
                for call in window.add_log_message.call_args_list
            )
        )

    def test_close_event_keeps_window_open_when_instrument_worker_will_not_stop(self):
        MainWindow, window, event, _pages, _tab = self._window(
            worker_running=True, stop_ok=False
        )

        with patch(
            "presentation.qt.main_window.QMessageBox.question", return_value=self.Yes
        ):
            MainWindow.closeEvent(window, event)

        window.coordinator.stop_instrument_worker.assert_called_once_with(5000)
        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()

    def test_close_event_asks_before_leaving_unreleased_ports(self):
        MainWindow, window, event, pages, _tab = self._window(
            release_ok=False, unreleased=True
        )

        with patch(
            "presentation.qt.main_window.QMessageBox.question",
            side_effect=[self.No],
        ) as question:
            MainWindow.closeEvent(window, event)

        self.assertEqual(question.call_count, 1)
        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()
        for page in pages.values():
            page.close.assert_not_called()

    def test_close_event_leaves_unreleased_ports_only_with_explicit_confirmation(self):
        MainWindow, window, event, pages, _tab = self._window(
            release_ok=False, unreleased=True
        )

        with patch(
            "presentation.qt.main_window.QMessageBox.question", return_value=self.Yes
        ):
            MainWindow.closeEvent(window, event)

        event.accept.assert_called_once_with()
        for page in pages.values():
            page.close.assert_called_once_with()

    def test_close_event_stops_the_assistant_before_closing_pages(self):
        MainWindow, window, event, pages, _tab = self._window()

        MainWindow.closeEvent(window, event)

        window.chat_panel.shutdown.assert_called_once_with(
            MainWindow.ASSISTANT_SHUTDOWN_TIMEOUT_MS
        )
        event.accept.assert_called_once_with()
        for page in pages.values():
            page.close.assert_called_once_with()

    def test_close_event_keeps_window_open_when_the_assistant_will_not_stop(self):
        MainWindow, window, event, pages, _tab = self._window(assistant_stop_ok=False)

        MainWindow.closeEvent(window, event)

        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()
        for page in pages.values():
            page.close.assert_not_called()
        self.assertTrue(
            any(
                "助手回复仍在生成" in str(call.args[0])
                for call in window.add_log_message.call_args_list
            )
        )

    def test_assistant_shutdown_runs_after_the_instrument_port_is_released(self):
        """助手收尾失败不得让仪器端口的安全释放被跳过。"""
        MainWindow, window, event, _pages, _tab = self._window(assistant_stop_ok=False)
        order = Mock()
        order.attach_mock(window.coordinator.release_instrument_port, "release_port")
        order.attach_mock(window.chat_panel.shutdown, "stop_assistant")

        MainWindow.closeEvent(window, event)

        names = [name for name, _args, _kwargs in order.mock_calls]
        window.coordinator.release_instrument_port.assert_called_once_with()
        window.chat_panel.shutdown.assert_called_once_with(
            MainWindow.ASSISTANT_SHUTDOWN_TIMEOUT_MS
        )
        self.assertLess(names.index("release_port"), names.index("stop_assistant"))


class MainWindowAssemblyBoundaryTests(unittest.TestCase):
    """步骤 10：主窗口只做组装，配置、运行和硬件入口都在协调器。"""

    SOURCE = ROOT / "presentation" / "qt" / "main_window.py"

    def setUp(self):
        self.source = self.SOURCE.read_text(encoding="utf-8-sig")
        self.tree = ast.parse(self.source)

    def _main_window(self):
        for node in self.tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "MainWindow":
                return node
        raise AssertionError("main_window.py 中未找到 MainWindow 类")

    def test_main_window_does_not_load_configuration_json_directly(self):
        methods = {node.name for node in self._main_window().body if isinstance(node, ast.FunctionDef)}
        for name in ("load_config", "update_and_save_config", "_save_config_file",
                     "_build_config_from_ui", "_read_instrument_config_from_ui",
                     "_measurement_config_path"):
            self.assertNotIn(
                name, methods,
                f"{name} 已迁移到 ApplicationCoordinator，主窗口不得重新持有配置/运行逻辑",
            )

    def test_main_window_does_not_modify_sys_path(self):
        self.assertNotIn("sys.path", self.source)
        self.assertNotIn("project_root =", self.source)

    def test_main_window_has_no_dead_legacy_implementation_blocks(self):
        # 旧实现不得以文档字符串形式残留在源码里。
        self.assertNotIn("Legacy configuration-page implementation", self.source)
        self.assertNotIn("Legacy configuration helpers removed", self.source)
        self.assertNotIn("create_power_supply_config", self.source)

    def test_main_window_stays_small_and_assembly_focused(self):
        line_count = len(self.source.splitlines())
        self.assertLessEqual(
            line_count, 650, f"main_window.py 有 {line_count} 行，超出步骤 10 的收敛目标"
        )

    def test_main_window_uses_the_coordinator_for_gui_state(self):
        for literal in (
            "ApplicationCoordinator(",
            "run_provider=self.coordinator.resolve_measurement_run",
            "config_path_provider=self.coordinator.measurement_config_path",
            "driver_mode_provider=self.coordinator.amplifier_driver_mode",
        ):
            self.assertIn(literal, self.source)

    def test_main_window_does_not_read_config_widgets(self):
        for widget in ("driver_mode_check", "freq_edit", "sg_address", "power_config_widgets"):
            self.assertNotIn(
                widget, self.source,
                f"主窗口不得直接读取配置控件 {widget}；应使用配置页的窄接口",
            )


class MainWindowMigrationResidueTests(unittest.TestCase):
    """步骤 12：窗口不再保留迁移期的旧实现与空占位方法。"""

    SOURCE = ROOT / "presentation" / "qt" / "main_window.py"

    def setUp(self):
        self.source = self.SOURCE.read_text(encoding="utf-8-sig")
        self.tree = ast.parse(self.source)

    def _method(self, name):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"main_window.py 中未找到 {name}")

    def test_dead_instruction_text_implementation_is_gone(self):
        # 旧实现写的是窗口自己并不存在的 instruction_text 控件，说明文本归功放页面。
        methods = {
            node.name
            for node in self.tree.body if isinstance(node, ast.FunctionDef)
        }
        methods |= {
            node.name
            for node in ast.walk(self.tree)
            if isinstance(node, ast.FunctionDef)
        }
        self.assertNotIn("update_amplifier_instruction_text", methods)
        self.assertNotIn("self.instruction_text", self.source)

    def test_driver_mode_change_refreshes_the_amplifier_page(self):
        self.assertIn(
            "tab.driver_mode_changed.connect(self._refresh_amplifier_instruction_text)",
            self.source,
            "驱动模式变化必须接到功放页的连接说明刷新",
        )
        method = self._method("_refresh_amplifier_instruction_text")
        segment = ast.get_source_segment(self.source, method)
        self.assertIn('pages", {}).get("amplifier")', segment)
        self.assertIn("update_instruction_text(driver_enabled)", segment)

    def test_chat_panel_welcome_text_belongs_to_the_panel(self):
        # 窗口只切换可见性；欢迎语与模型名展示归面板。
        self.assertNotIn("你好！我是", self.source)
        self.assertNotIn("chat_display", self.source)
        self.assertIn("self.chat_panel.show_welcome_message()", self.source)

    def test_cable_loss_result_loading_declares_its_expected_failures(self):
        method = self._method("_load_cable_loss_result")
        handlers = [
            ast.unparse(handler.type) if handler.type else None
            for handler in ast.walk(method)
            if isinstance(handler, ast.ExceptHandler)
        ]
        self.assertEqual(
            handlers,
            ["FileNotFoundError", "(OSError, ValueError)"],
            "读取历史结果只处理“没有结果”和“结果不可读”两类明确失败",
        )

    def test_window_uses_logging_instead_of_print(self):
        self.assertNotIn("print(", self.source)


if __name__ == "__main__":
    unittest.main()
