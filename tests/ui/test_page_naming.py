"""页面名称和模块边界：只保留当前的规范入口。"""

import ast
import unittest
from pathlib import Path

from presentation.qt.test_configuration_page import TestConfigurationPage
from presentation.qt.instrument_discovery_panel import (
    InstrumentDiscoveryPanel,
    _USE_SCREENSHOT_FRONTS,
)
from presentation.qt.instrument_setup_page import InstrumentSetupPage
from presentation.qt.ui_loader import UI_DIRECTORY


ROOT = Path(__file__).resolve().parents[2]
QT_ROOT = ROOT / "presentation" / "qt"


def _tree(path: Path):
    return ast.parse(path.read_text(encoding="utf-8-sig"), str(path))


def _class_names(path: Path):
    return {
        node.name
        for node in _tree(path).body
        if isinstance(node, ast.ClassDef)
    }


class PageNamingTests(unittest.TestCase):
    def test_new_page_classes_are_the_canonical_definitions(self):
        self.assertEqual(TestConfigurationPage.__name__, "TestConfigurationPage")
        self.assertEqual(InstrumentDiscoveryPanel.__name__, "InstrumentDiscoveryPanel")
        self.assertEqual(InstrumentSetupPage.__name__, "InstrumentSetupPage")
        self.assertIn(
            "TestConfigurationPage", _class_names(QT_ROOT / "test_configuration_page.py")
        )
        self.assertIn(
            "InstrumentDiscoveryPanel",
            _class_names(QT_ROOT / "instrument_discovery_panel.py"),
        )
        self.assertIn(
            "InstrumentSetupPage",
            _class_names(QT_ROOT / "instrument_setup_page.py"),
        )

    def test_old_page_modules_and_class_aliases_are_removed(self):
        for old_name in (
            "config_page.py",
            "configuration_page.py",
            "connection_page.py",
            "device_discovery_panel.py",
        ):
            with self.subTest(module=old_name):
                self.assertFalse((QT_ROOT / old_name).exists())
        self.assertFalse(hasattr(TestConfigurationPage, "ConfigPage"))
        self.assertFalse(hasattr(InstrumentSetupPage, "ConnectionPage"))
        self.assertFalse(hasattr(InstrumentDiscoveryPanel, "DeviceDiscoveryPanel"))

    def test_window_assembly_imports_the_new_modules(self):
        source = (QT_ROOT / "main_window.py").read_text(encoding="utf-8-sig")
        self.assertIn("from presentation.qt.test_configuration_page import", source)
        self.assertIn("from presentation.qt.instrument_setup_page import", source)
        self.assertNotIn("from presentation.qt.config_page import", source)
        self.assertNotIn("from presentation.qt.connection_page import", source)

    def test_discovery_panel_exposes_only_the_current_animation_field(self):
        panel_source = (QT_ROOT / "instrument_discovery_panel.py").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("self.deal_animation = CardDealAnimation", panel_source)
        self.assertNotIn("def deal_layer", panel_source)
        self.assertFalse(hasattr(InstrumentDiscoveryPanel, "deal_layer"))
        self.assertIsInstance(_USE_SCREENSHOT_FRONTS, bool)

    def test_repository_code_uses_the_new_animation_field_name(self):
        """生产代码和项目工具统一使用 ``deal_animation``。"""
        allowed = {QT_ROOT / "instrument_discovery_panel.py"}
        offenders = []
        for base in (QT_ROOT, ROOT / "tools"):
            for path in sorted(base.rglob("*.py")):
                if path in allowed:
                    continue
                source = path.read_text(encoding="utf-8-sig")
                if ".deal_layer" in source:
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(
            offenders,
            [],
            "仓库内代码应访问 deal_animation: "
            + "; ".join(offenders),
        )
        acceptance = (ROOT / "tools" / "manual_acceptance.py").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("panel.deal_animation", acceptance)

    def test_ui_resource_uses_the_new_page_name(self):
        files = {path.name for path in UI_DIRECTORY.glob("*.ui")}
        self.assertIn("instrument_setup_page.ui", files)
        self.assertNotIn("connection_page.ui", files)


if __name__ == "__main__":
    unittest.main()
