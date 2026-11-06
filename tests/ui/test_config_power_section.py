"""电源分区（上电计划 + 漏极）与卡片分工的边界测试。

通道参数已经融合到电源卡片：``config_power_section`` 只构建卡片下方的计划与漏极，
通道输入控件归 ``power_card`` / ``power_card_wall``。本模块锁住三件事：

- 页面交给构建模块的输入仍是显式声明的类型；
- 电源分区不再创建通道参数表单或标签页；
- 页面只组合、卡片只做卡面，通道控件的唯一归属是可静态检查的。
"""

import ast
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from presentation.qt import config_power_section as section
from presentation.qt.config_power_section import PowerFormSection

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAGE_SOURCE = PROJECT_ROOT / "presentation" / "qt" / "configuration_page.py"
POWER_PAGE_SOURCE = (
    PROJECT_ROOT / "presentation" / "qt" / "power_configuration_page.py"
)
SECTION_SOURCE = PROJECT_ROOT / "presentation" / "qt" / "config_power_section.py"
CARD_SOURCE = PROJECT_ROOT / "presentation" / "qt" / "power_card.py"
WALL_SOURCE = PROJECT_ROOT / "presentation" / "qt" / "power_card_wall.py"


def _page_class(tree, name: str = "ConfigurationPage") -> ast.ClassDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def _method(node: ast.ClassDef, name: str) -> ast.FunctionDef | None:
    """返回类里的一个方法定义；没有该方法时返回 ``None``。"""
    for item in node.body:
        if isinstance(item, ast.FunctionDef) and item.name == name:
            return item
    return None


def _called_names(node: ast.AST) -> set[str]:
    """节点里按名字调用的函数/类名（属性调用不算，它可能是页面自己的方法）。"""
    return {
        child.func.id
        for child in ast.walk(node)
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
    }


def _annotated_parameters(tree) -> dict[str, str]:
    """模块内每个带标注的形参名 -> 标注文本（同名形参在多处出现时以最后一次为准）。"""
    annotated: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for argument in (*node.args.args, *node.args.kwonlyargs):
            if argument.annotation is not None:
                annotated[argument.arg] = ast.unparse(argument.annotation)
    return annotated


def _declared_exports(tree) -> set[str]:
    """模块字面量 ``__all__`` 里的名字；没有声明时返回空集合。"""
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if any(isinstance(target, ast.Name) and target.id == "__all__" for target in targets):
            return {
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant)
            }
    return set()


def _defined_names(path: Path) -> set[str]:
    """模块里定义的顶层函数/类名。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }


class PowerSectionInputContractTests(unittest.TestCase):
    """页面交给构建模块的输入是显式声明的类型，不是随手拼的字典。"""

    def test_the_page_inputs_are_declared_types(self):
        tree = ast.parse(SECTION_SOURCE.read_text(encoding="utf-8"))
        annotated = _annotated_parameters(tree)
        exports = _declared_exports(tree)

        for parameter, alias in (
            ("sources", "PowerSupplySources"),
            ("choices", "PowerSupplyChoices"),
        ):
            with self.subTest(parameter=parameter):
                self.assertEqual(
                    annotated.get(parameter),
                    alias,
                    f"{parameter} 应标注为 {alias}：输入结构是模块契约的一部分",
                )
                self.assertIn(alias, exports, f"{alias} 应在 __all__ 内")
                self.assertTrue(hasattr(section, alias), f"{alias} 应可在运行时取到")

    def test_the_page_side_producers_use_the_same_types(self):
        """独立电源页读出的页面状态也按同一份类型声明，契约两端对齐。"""
        page = _page_class(
            ast.parse(POWER_PAGE_SOURCE.read_text(encoding="utf-8")),
            "PowerConfigurationPage",
        )

        for method_name, alias in (
            ("_power_supply_sources", "PowerSupplySources"),
            ("_power_supply_choices", "PowerSupplyChoices"),
        ):
            with self.subTest(method=method_name):
                method = _method(page, method_name)
                self.assertIsNotNone(method, f"页面应有 {method_name}")
                self.assertEqual(
                    ast.unparse(method.returns),
                    alias,
                    f"{method_name} 的返回值应与构建模块的输入类型同名",
                )


class PowerSectionWidgetsTests(unittest.TestCase):
    """电源分区只剩上电计划与漏极，不再创建通道参数表单。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def section(self) -> PowerFormSection:
        widget = PowerFormSection(
            sources={
                "DP8B1519": {
                    "label": "DP8B1519",
                    "model": "DP832A",
                    "channels": {"CH1": {}, "CH2": {}},
                }
            },
            choices={"DP8B1519": {"label": "DP8B1519"}},
        )
        self.addCleanup(widget.deleteLater)
        return widget

    def test_the_section_builds_plans_and_drain_only(self):
        widget = self.section()

        self.assertTrue(hasattr(widget, "power_plan_group"))
        self.assertTrue(hasattr(widget, "drain_channel_group"))
        self.assertFalse(hasattr(widget, "power_tabs"))
        self.assertFalse(hasattr(widget, "power_config_widgets"))

    def test_plan_candidates_are_the_bound_supply_channels(self):
        widget = self.section()

        self.assertEqual(
            widget.dut_power_plan_editor.routes(),
            (("DP8B1519", "CH1"), ("DP8B1519", "CH2")),
        )
        self.assertEqual(
            widget.driver_power_plan_editor.routes(),
            widget.dut_power_plan_editor.routes(),
        )


class PowerSectionOwnershipTests(unittest.TestCase):
    """通道参数归卡片，计划与漏极归电源分区；页面只组合。"""

    def setUp(self):
        self.tree = ast.parse(POWER_PAGE_SOURCE.read_text(encoding="utf-8"))
        self.page = _page_class(self.tree, "PowerConfigurationPage")

    def test_power_page_imports_the_section_builders(self):
        imported = {
            node.module
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ImportFrom)
        }

        self.assertIn(
            "config_power_section",
            imported,
            "独立电源页必须从 config_power_section 取上电计划与漏极组件",
        )

    def test_power_page_has_no_channel_form_delegates(self):
        methods = {node.name for node in self.page.body if isinstance(node, ast.FunctionDef)}

        for removed in ("_rebuild_power_tabs", "_current_power_tab_key"):
            self.assertNotIn(removed, methods, f"{removed} 属于已删除的通道标签页")
        for required in ("_build_power_widgets", "_refresh_power_plans"):
            self.assertIn(required, methods)

    def test_each_power_method_delegates_and_builds_no_widgets(self):
        """按方法判断边界：电源分区方法只转调，不自己建控件。"""
        widget_classes = {"QCheckBox", "QFormLayout", "QGroupBox", "QTabWidget"}
        for method_name in ("_build_power_widgets", "_refresh_power_plans"):
            with self.subTest(method=method_name):
                method = _method(self.page, method_name)
                self.assertIsNotNone(method, f"页面应保留 {method_name}")
                self.assertEqual(
                    _called_names(method) & widget_classes,
                    set(),
                    f"{method_name} 不再自己拼电源分区控件",
                )

    def test_section_module_no_longer_builds_channel_forms(self):
        names = _defined_names(SECTION_SOURCE)

        for removed in (
            "build_power_section",
            "rebuild_power_tabs",
            "current_power_tab_key",
            "_add_power_tab",
            "channel_map",
            "spin_value",
        ):
            self.assertNotIn(removed, names, f"{removed} 已随「电源详细配置」删除")

    def test_channel_inputs_live_on_the_card(self):
        card_names = _defined_names(CARD_SOURCE)
        wall_names = _defined_names(WALL_SOURCE)

        self.assertIn("CardValueField", card_names)
        self.assertIn("PowerSupplyCard", card_names)
        self.assertIn("PowerCardWall", wall_names)

    def test_section_module_has_no_state_reading_or_file_io(self):
        tree = ast.parse(SECTION_SOURCE.read_text(encoding="utf-8"))
        imported = {
            (node.module or "").split(".")[-1]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        roots = {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }

        self.assertNotIn(
            "config_form_state",
            imported,
            "读写控件值组装配置属于页面与 config_form_state，构建模块不读取表单状态",
        )
        self.assertNotIn("infrastructure", roots)
        self.assertEqual(
            [
                node.lineno
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "open"
            ],
            [],
            "控件构建模块不读文件",
        )

    def test_config_page_keeps_the_public_entry_and_signal_contract(self):
        page = _page_class(ast.parse(PAGE_SOURCE.read_text(encoding="utf-8")))
        signals = {
            target.id
            for node in page.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        methods = {node.name for node in page.body if isinstance(node, ast.FunctionDef)}

        for signal in (
            "save_requested",
            "finalize_run_requested",
            "load_run_requested",
            "validation_failed",
        ):
            self.assertIn(signal, signals)
        for entry in ("build_ui", "validate", "build_config", "apply_config"):
            self.assertIn(entry, methods)


if __name__ == "__main__":
    unittest.main()
