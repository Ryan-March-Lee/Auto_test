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

    def test_close_event_disconnects_tabs_and_closes_pages(self):
        from presentation.qt.main_window import MainWindow

        tab_signal = Mock()
        pages = {"first": Mock(), "second": Mock()}
        controller = Mock()
        controller.state.is_active = False
        coordinator = Mock()
        coordinator.instrument_worker_running.return_value = False
        window = SimpleNamespace()
        window.measurement_controller = controller
        window.coordinator = coordinator
        window.tab_widget = SimpleNamespace(currentChanged=tab_signal)
        window._on_page_changed = Mock()
        window.pages = pages
        event = Mock()

        MainWindow.closeEvent(window, event)

        coordinator.release_instrument_port.assert_called_once_with()
        tab_signal.disconnect.assert_called_once_with(window._on_page_changed)
        for page in pages.values():
            page.close.assert_called_once_with()
        event.accept.assert_called_once_with()

    def test_close_event_keeps_window_open_when_instrument_worker_will_not_stop(self):
        from presentation.qt.main_window import MainWindow

        controller = Mock()
        controller.state.is_active = False
        coordinator = Mock()
        coordinator.instrument_worker_running.return_value = True
        coordinator.stop_instrument_worker.return_value = False
        window = SimpleNamespace(
            measurement_controller=controller, coordinator=coordinator
        )
        event = Mock()

        from PySide6.QtWidgets import QMessageBox

        with patch(
            "presentation.qt.main_window.QMessageBox.question",
            return_value=QMessageBox.Yes,
        ):
            MainWindow.closeEvent(window, event)

        coordinator.stop_instrument_worker.assert_called_once_with(5000)
        event.ignore.assert_called_once_with()
        event.accept.assert_not_called()


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
        source = self.source
        for literal in (
            "ApplicationCoordinator(",
            "run_provider=self.coordinator.resolve_measurement_run",
            "config_path_provider=self.coordinator.measurement_config_path",
            "driver_mode_provider=self.coordinator.amplifier_driver_mode",
        ):
            self.assertIn(literal, source)


if __name__ == "__main__":
    unittest.main()
