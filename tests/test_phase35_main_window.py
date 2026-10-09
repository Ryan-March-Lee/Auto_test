import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


class Phase35MainWindowTests(unittest.TestCase):
    def test_compatibility_entry_reexports_same_main_window(self):
        import enhanced_main_gui
        from presentation.qt.main_window import MainWindow

        self.assertIs(enhanced_main_gui.MainWindow, MainWindow)

    def test_build_application_reuses_qapplication(self):
        from PySide6.QtWidgets import QApplication
        from presentation.qt.main_window import build_application

        first = build_application([])
        second = build_application([])
        self.assertIs(first, second)
        self.assertIs(first, QApplication.instance())

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
        window = SimpleNamespace()
        window.measurement_controller = controller
        window.instrument_worker = None
        window.instrument_ctrl = None
        window.tab_widget = SimpleNamespace(currentChanged=tab_signal)
        window._on_page_changed = Mock()
        window.pages = pages
        event = Mock()

        MainWindow.closeEvent(window, event)

        tab_signal.disconnect.assert_called_once_with(window._on_page_changed)
        for page in pages.values():
            page.close.assert_called_once_with()
        event.accept.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
