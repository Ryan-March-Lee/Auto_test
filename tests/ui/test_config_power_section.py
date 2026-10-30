"""配置页电源分区构建模块的独立测试。

覆盖三件事：合并/取值规则（与 Qt 无关的纯函数）、分区构建本身能独立工作（只需要一个
能挂控件属性的页面对象，不必构造整个 ``ConfigPage``）、以及页面只组合不重建的职责边界。
"""

import ast
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from presentation.qt.config_power_section import (
    build_power_section,
    channel_map,
    spin_value,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAGE_SOURCE = PROJECT_ROOT / "presentation" / "qt" / "config_page.py"
SECTION_SOURCE = PROJECT_ROOT / "presentation" / "qt" / "config_power_section.py"


def _page_class(tree) -> ast.ClassDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ConfigPage"
    )


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
    """通道数值只接受非 bool 的有限数字，其余落到 0，交给正式校验报告。"""

    def test_numbers_pass_through(self):
        self.assertEqual(spin_value(12), 12.0)
        self.assertEqual(spin_value(-3.5), -3.5)

    def test_other_values_fall_back_to_zero(self):
        for value in (True, False, "12", None, float("nan"), float("inf"), object()):
            with self.subTest(value=value):
                self.assertEqual(spin_value(value), 0.0)


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

    def test_every_supply_gets_a_tab_with_its_channel_controls(self):
        page = self.build()

        self.assertEqual(list(page.power_config_widgets), ["DP8B1519"])
        self.assertEqual(page.power_tabs.count(), 1)
        self.assertIn("DP8B1519", page.power_tabs.tabText(0))
        self.assertIn("SN-1519", page.power_tabs.tabToolTip(0))
        self.assertEqual(
            page.power_config_widgets["DP8B1519"]["channels"]["CH1"]["voltage"].value(),
            12.0,
        )
        self.assertEqual(
            set(page.power_config_widgets["DP8B1519"]),
            {"channels", "page"},
            "标签页记录只登记通道控件与页面，重建与读取都按这两个键工作",
        )

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

    def test_config_page_delegates_the_power_section(self):
        tree = ast.parse(PAGE_SOURCE.read_text(encoding="utf-8"))
        imported = {
            node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        }

        self.assertIn(
            "config_power_section",
            imported,
            "页面必须从 config_power_section 取分区构建与标签页重建",
        )

        page = _page_class(tree)
        called = {
            node.func.id
            for node in ast.walk(page)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for delegated in ("build_power_section", "rebuild_power_tabs"):
            self.assertIn(delegated, called, f"页面应委托 {delegated}")

        power_widgets = {"QCheckBox", "QTabWidget"}
        self.assertEqual(
            called & power_widgets,
            set(),
            "通道参数控件与标签页属于 config_power_section，页面不再自己拼电源分区",
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
        page = _page_class(ast.parse(PAGE_SOURCE.read_text(encoding="utf-8")))
        methods = {node.name for node in page.body if isinstance(node, ast.FunctionDef)}

        for delegating in ("_build_power_widgets", "_rebuild_power_tabs", "_current_power_tab_key"):
            self.assertIn(delegating, methods)
        self.assertNotIn(
            "_add_power_tab",
            methods,
            "单个电源标签页的控件构建属于 config_power_section",
        )


if __name__ == "__main__":
    unittest.main()
