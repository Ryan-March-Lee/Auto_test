"""配置页电源分区构建模块的独立测试。

覆盖四件事：合并/取值/显示界的规则（与 Qt 无关的纯函数）、分区构建本身能独立工作
（只需要一个能挂控件属性的页面对象，不必构造整个 ``ConfigPage``）、页面交给构建模块的
输入是显式声明的类型，以及页面只组合不重建的职责边界。
"""

import ast
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from presentation.qt import config_power_section as section
from presentation.qt.config_power_section import (
    build_power_section,
    channel_map,
    spin_value,
)
from presentation.qt.config_projection import DEFAULT_POWER_CHANNEL_VALUE

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAGE_SOURCE = PROJECT_ROOT / "presentation" / "qt" / "config_page.py"
SECTION_SOURCE = PROJECT_ROOT / "presentation" / "qt" / "config_power_section.py"

# 页面保留的三个委托壳，以及它们分别要转给的构建模块入口。
POWER_METHOD_BUILDERS = {
    "_build_power_widgets": "build_power_section",
    "_rebuild_power_tabs": "rebuild_power_tabs",
    "_current_power_tab_key": "current_power_tab_key",
}
# 电源分区的控件类：这些只由构建模块创建。
POWER_SECTION_WIDGET_CLASSES = {"QCheckBox", "QFormLayout", "QGroupBox", "QTabWidget"}


def _page_class(tree) -> ast.ClassDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ConfigPage"
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


class ChannelMapTests(unittest.TestCase):
    """重建标签页时的通道合并规则：控件里刚填的值优先，配置补齐没有控件的通道。"""

    def test_widget_values_win_over_the_configuration(self):
        merged = channel_map(
            {"channels": {"CH1": {"voltage": {"value": 5.0}, "current": {"value": 1.0}}}},
            {"channels": {"CH1": {"voltage": {"value": 15.0}}}},
        )

        self.assertEqual(
            merged["CH1"]["voltage"]["value"],
            15.0,
            "重建发生在保存之前，操作者刚改的通道参数不能被刷新改回配置里的旧值",
        )
        self.assertEqual(merged["CH1"]["current"]["value"], 1.0)

    def test_configuration_fills_channels_without_controls(self):
        merged = channel_map({"channels": {"CH2": {"voltage": {"value": 3.3}}}}, {})

        self.assertEqual(merged, {"CH2": {"voltage": {"value": 3.3}}})

    def test_entries_without_a_channel_mapping_are_ignored(self):
        self.assertEqual(channel_map("not a mapping", ["also", "not"]), {})
        self.assertEqual(channel_map({}, {"channels": "not a mapping"}), {})


class SpinValueTests(unittest.TestCase):
    """通道数值只接受非 bool 的有限数字，其余落到空配置基线，交给正式校验报告。"""

    def test_numbers_pass_through(self):
        self.assertEqual(spin_value(12), 12.0)
        self.assertEqual(spin_value(-3.5), -3.5)

    def test_other_values_fall_back_to_the_baseline(self):
        for value in (True, False, "12", None, float("nan"), float("inf"), object()):
            with self.subTest(value=value):
                self.assertEqual(spin_value(value), DEFAULT_POWER_CHANNEL_VALUE)

    def test_the_fallback_is_the_value_the_projection_resets_to(self):
        """“字段没写/类型不对”与“重置到空配置基线”显示同一个数，规则不重复实现。"""
        self.assertEqual(spin_value(None), DEFAULT_POWER_CHANNEL_VALUE)
        self.assertEqual(float(DEFAULT_POWER_CHANNEL_VALUE), 0.0)


class PowerSectionInputContractTests(unittest.TestCase):
    """页面交给构建模块的输入是显式声明的类型，不是随手拼的字典。"""

    def test_the_page_inputs_are_declared_types(self):
        tree = ast.parse(SECTION_SOURCE.read_text(encoding="utf-8"))
        annotated = _annotated_parameters(tree)
        exports = _declared_exports(tree)

        for parameter, alias in (
            ("sources", "PowerSupplySources"),
            ("choices", "PowerSupplyChoices"),
            ("assignment_entries", "PowerAssignmentEntries"),
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
        """页面读出的三份页面状态也按同一份类型声明，契约两端对齐。"""
        page = _page_class(ast.parse(PAGE_SOURCE.read_text(encoding="utf-8")))

        for method_name, alias in (
            ("_power_supply_sources", "PowerSupplySources"),
            ("_power_supply_choices", "PowerSupplyChoices"),
            ("_assignment_entries", "PowerAssignmentEntries"),
        ):
            with self.subTest(method=method_name):
                method = _method(page, method_name)
                self.assertIsNotNone(method, f"页面应有 {method_name}")
                self.assertEqual(
                    ast.unparse(method.returns),
                    alias,
                    f"{method_name} 的返回值应与构建模块的输入类型同名",
                )


class PowerSectionBuildTests(unittest.TestCase):
    """分区构建只依赖一个能挂控件属性的页面对象。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def build(self, **overrides):
        page = QWidget()
        # 布局挂在页面上，控件才会随页面一起存活（构建模块不自己建父控件）。
        layout = QVBoxLayout(page)
        arguments = {
            "sources": {
                "DP8B1519": {
                    "label": "DP8B1519",
                    "model": "DP832A",
                    "serial": "SN-1519",
                    "channels": {"CH1": {"voltage": {"value": 12.0}}},
                }
            },
            "choices": {},
            "assignment_entries": {},
            "driver_power_source": "直流电源",
        }
        arguments.update(overrides)
        build_power_section(page, layout, **arguments)
        return page

    def channels(self, page, key="DP8B1519"):
        return page.power_config_widgets[key]["channels"]["CH1"]

    def test_every_supply_gets_a_tab_with_its_channel_controls(self):
        page = self.build()

        self.assertEqual(list(page.power_config_widgets), ["DP8B1519"])
        self.assertEqual(page.power_tabs.count(), 1)
        self.assertIn("DP8B1519", page.power_tabs.tabText(0))
        self.assertIn("SN-1519", page.power_tabs.tabToolTip(0))
        self.assertEqual(self.channels(page)["voltage"].value(), 12.0)
        self.assertEqual(
            set(page.power_config_widgets["DP8B1519"]),
            {"channels", "page"},
            "标签页记录只登记通道控件与页面，重建与读取都按这两个键工作",
        )

    def test_channel_number_boxes_use_the_display_limits(self):
        """电压与电流输入框的上下界是显示口径；正式校验的合法范围不在这里。"""
        page = self.build()
        channels = self.channels(page)

        for key, limits in (
            ("voltage", (0.0, 50.0)),
            ("voltage_protection", (0.0, 50.0)),
            ("current", (0.0, 10.0)),
            ("current_protection", (0.0, 10.0)),
        ):
            with self.subTest(control=key):
                self.assertEqual(
                    (channels[key].minimum(), channels[key].maximum()),
                    limits,
                    "通道数值框的显示界由模块常量给出一处，不在建控件处各写一遍",
                )

    def test_numbers_outside_the_display_limits_are_clamped(self):
        """界外的配置值由控件夹到界内：显示口径夹值，正式校验另行报告。"""
        page = self.build(
            sources={
                "DP8B1519": {
                    "channels": {
                        "CH1": {"voltage": {"value": 60.0}, "current": {"value": -2.0}}
                    }
                }
            }
        )
        channels = self.channels(page)

        self.assertEqual(channels["voltage"].value(), 50.0)
        self.assertEqual(channels["current"].value(), 0.0)

    def test_non_numeric_channel_fields_are_shown_as_the_baseline(self):
        page = self.build(
            sources={
                "DP8B1519": {
                    "channels": {
                        "CH1": {"voltage": {"value": "12"}, "current": "not a mapping"}
                    }
                }
            }
        )
        channels = self.channels(page)

        self.assertEqual(channels["voltage"].value(), DEFAULT_POWER_CHANNEL_VALUE)
        self.assertEqual(channels["current"].value(), DEFAULT_POWER_CHANNEL_VALUE)

    def test_assignment_form_registers_the_slots_the_page_redraws(self):
        page = self.build()

        self.assertEqual(
            set(page.power_assignment_widgets),
            {
                "driver_source",
                "driver_power",
                "pa_unit1_power",
                "pa_unit2_power",
                "pa_unit2_label",
                "pa_unit3_power",
                "pa_unit3_label",
            },
        )
        self.assertEqual(page.driver_power_source_combo.currentText(), "直流电源")

    def test_a_declared_but_unbound_supply_stays_unselected(self):
        """配置里声明了引用、但本次没有可选项时保持空选择，交给校验报告。"""
        page = self.build(assignment_entries={"pa_unit1_power": {"supply_id": "DP8B0885"}})

        self.assertEqual(page.pa_unit1_power_combo.currentIndex(), -1)

    def test_an_undeclared_assignment_falls_back_to_the_first_bound_supply(self):
        """配置里没有这条分配（``None``）时才按缺省规则选中第一台可用电源。"""
        page = self.build(
            choices={
                "DP8B1519": {"label": "DP8B1519", "model": "DP832A"},
                "DP8B0885": {"label": "DP8B0885", "model": "DP832A"},
            },
            assignment_entries={},
        )

        self.assertEqual(page.pa_unit1_power_combo.currentData(), "DP8B1519")

    def test_a_declared_assignment_selects_the_bound_supply(self):
        """声明了引用、且这台电源本次已绑定：选中它，业务值在 ``itemData``。"""
        page = self.build(
            choices={"DP8B0885": {"label": "DP8B0885", "model": "DP832A"}},
            assignment_entries={"pa_unit1_power": {"supply_id": "DP8B0885"}},
        )

        self.assertEqual(page.pa_unit1_power_combo.currentData(), "DP8B0885")

    def test_tab_tooltip_omits_identity_parts_the_entry_does_not_have(self):
        """旧槽位条目没有序列号与设备键：标题与提示里只有它的旧键，不编造身份。"""
        page = self.build(
            sources={"PS1": {"channels": {"CH1": {"role": "gate"}}}},
        )

        self.assertEqual(page.power_tabs.tabText(0), "PS1")
        self.assertIn("PS1", page.power_tabs.tabToolTip(0))
        self.assertNotIn(
            "序列号",
            page.power_tabs.tabToolTip(0),
            "条目没有序列号时提示不写序列号，也不推导一个不存在的标签",
        )


class PowerSectionOwnershipTests(unittest.TestCase):
    """配置页只组合与协调：电源分区的控件构建与标签页重建留在构建模块。"""

    def setUp(self):
        self.tree = ast.parse(PAGE_SOURCE.read_text(encoding="utf-8"))
        self.page = _page_class(self.tree)

    def test_config_page_imports_the_section_builders(self):
        imported = {
            node.module
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ImportFrom)
        }

        self.assertIn(
            "config_power_section",
            imported,
            "页面必须从 config_power_section 取分区构建与标签页重建",
        )

    def test_each_power_method_delegates_and_builds_no_widgets(self):
        """按方法判断边界：电源分区的三个方法只转调，不自己建控件。"""
        for method_name, builder in POWER_METHOD_BUILDERS.items():
            with self.subTest(method=method_name):
                method = _method(self.page, method_name)
                self.assertIsNotNone(method, f"页面应保留 {method_name} 委托壳")
                called = _called_names(method)
                self.assertIn(builder, called, f"{method_name} 应委托 {builder}")
                self.assertEqual(
                    called & POWER_SECTION_WIDGET_CLASSES,
                    set(),
                    f"{method_name} 不再自己拼电源分区控件："
                    "通道参数控件与标签页属于 config_power_section",
                )

    def test_the_boundary_check_is_scoped_to_the_power_methods(self):
        """别的分区合法使用同一批控件类时不误报——边界只按电源分区的方法判断。"""
        page = _page_class(
            ast.parse(
                "class ConfigPage:\n"
                "    def _build_power_widgets(self, layout, supplies):\n"
                "        build_power_section(self, layout, sources=supplies)\n"
                "\n"
                "    def _build_test_parameter_widgets(self, layout):\n"
                "        group = QGroupBox('测试参数配置')\n"
                "        group.layout().addWidget(QCheckBox('启用'))\n"
            )
        )

        self.assertEqual(
            _called_names(_method(page, "_build_power_widgets"))
            & POWER_SECTION_WIDGET_CLASSES,
            set(),
            "电源分区方法只转调，因此不命中控件类",
        )
        self.assertIn(
            "QCheckBox",
            _called_names(_method(page, "_build_test_parameter_widgets")),
            "其他分区自己建控件不在这条边界检查里",
        )

    def test_config_page_keeps_the_public_entry_and_signal_contract(self):
        signals = {
            target.id
            for node in self.page.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        methods = {node.name for node in self.page.body if isinstance(node, ast.FunctionDef)}

        for signal in (
            "save_requested",
            "connect_requested",
            "create_run_requested",
            "load_run_requested",
            "validation_failed",
        ):
            self.assertIn(signal, signals)
        for entry in ("build_ui", "validate", "build_config", "apply_config"):
            self.assertIn(entry, methods)

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

    def test_config_page_no_longer_builds_single_power_tabs(self):
        methods = {node.name for node in self.page.body if isinstance(node, ast.FunctionDef)}

        for delegating in POWER_METHOD_BUILDERS:
            self.assertIn(delegating, methods)
        self.assertNotIn(
            "_add_power_tab",
            methods,
            "单个电源标签页的控件构建属于 config_power_section",
        )


if __name__ == "__main__":
    unittest.main()
