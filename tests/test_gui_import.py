import importlib.util
import unittest
from unittest.mock import patch


def gui_dependencies_available():
    dependencies = ("PySide6", "matplotlib", "numpy", "pandas", "seaborn", "markdown", "requests", "pyvisa")
    return all(importlib.util.find_spec(name) is not None for name in dependencies)


@unittest.skipUnless(gui_dependencies_available(), "当前环境缺少完整 GUI 依赖")
class GuiImportSmokeTests(unittest.TestCase):
    def test_gui_module_import_does_not_open_instruments(self):
        with patch("pyvisa.ResourceManager") as resource_manager:
            import enhanced_main_gui  # noqa: F401
            resource_manager.assert_not_called()


if __name__ == "__main__":
    unittest.main()
