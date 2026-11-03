"""步骤 13：页面命名与模块边界的兼容契约。

新名称是唯一实现：``ConfigurationPage`` / ``InstrumentDiscoveryPanel`` /
``InstrumentSetupPage`` 分别在新的领域模块里定义，旧模块与旧类名只做再导出。
本组用例固定三件事：

- 新模块是实现的所在地，旧模块不再声明同名页面类；
- 旧模块导出的名称与新模块是同一个对象，兼容期不会分叉出第二份实现；
- 窗口装配只导入新模块，发现面板的发牌字段使用新名称 ``deal_animation``，
  ``deal_layer`` 只是兼容属性。
"""

import ast
import unittest
from pathlib import Path

from presentation.qt.config_page import ConfigPage
from presentation.qt.configuration_page import ConfigurationPage
from presentation.qt.connection_page import ConnectionPage
from presentation.qt.device_discovery_panel import DeviceDiscoveryPanel
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
        self.assertEqual(ConfigurationPage.__name__, "ConfigurationPage")
        self.assertEqual(InstrumentDiscoveryPanel.__name__, "InstrumentDiscoveryPanel")
        self.assertEqual(InstrumentSetupPage.__name__, "InstrumentSetupPage")
        self.assertIn(
            "ConfigurationPage", _class_names(QT_ROOT / "configuration_page.py")
        )
        self.assertIn(
            "InstrumentDiscoveryPanel",
            _class_names(QT_ROOT / "instrument_discovery_panel.py"),
        )
        self.assertIn(
            "InstrumentSetupPage",
            _class_names(QT_ROOT / "instrument_setup_page.py"),
        )

    def test_old_names_are_the_same_objects(self):
        self.assertIs(ConfigPage, ConfigurationPage)
        self.assertIs(DeviceDiscoveryPanel, InstrumentDiscoveryPanel)
        self.assertIs(ConnectionPage, InstrumentSetupPage)

    def test_old_modules_only_reexport_the_new_implementations(self):
        for old_name, old_class, new_class in (
            ("config_page.py", "ConfigPage", "ConfigurationPage"),
            (
                "device_discovery_panel.py",
                "DeviceDiscoveryPanel",
                "InstrumentDiscoveryPanel",
            ),
            ("connection_page.py", "ConnectionPage", "InstrumentSetupPage"),
        ):
            with self.subTest(module=old_name):
                classes = _class_names(QT_ROOT / old_name)
                self.assertNotIn(old_class, classes)
                self.assertNotIn(new_class, classes)

    def test_window_assembly_imports_the_new_modules(self):
        source = (QT_ROOT / "main_window.py").read_text(encoding="utf-8-sig")
        self.assertIn("from presentation.qt.configuration_page import", source)
        self.assertIn("from presentation.qt.instrument_setup_page import", source)
        self.assertNotIn("from presentation.qt.config_page import", source)
        self.assertNotIn("from presentation.qt.connection_page import", source)

    def test_deal_layer_is_only_a_compatibility_alias(self):
        panel_source = (QT_ROOT / "instrument_discovery_panel.py").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("self.deal_animation = CardDealAnimation", panel_source)
        self.assertIn("def deal_layer", panel_source)
        self.assertIsInstance(_USE_SCREENSHOT_FRONTS, bool)

    def test_ui_resource_uses_the_new_page_name(self):
        files = {path.name for path in UI_DIRECTORY.glob("*.ui")}
        self.assertIn("instrument_setup_page.ui", files)
        self.assertNotIn("connection_page.ui", files)


if __name__ == "__main__":
    unittest.main()
