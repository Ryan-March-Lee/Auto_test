"""步骤 11：配置投影规则的独立测试（不需要 Qt）。

覆盖：投影只认识配置编写文档的字段、先重置再投影、非法输入必须失败、配置页不再持有
逐字段回填逻辑，以及“正式 schema 只由 configuration_authoring 生成”的边界。
"""

import ast
import unittest
from pathlib import Path

from presentation.qt.config_projection import (
    DEFAULT_ATTENUATOR_TYPE,
    DEFAULT_COMPRESSION_TYPE,
    DEFAULT_MAX_INPUT_POWER,
    DEFAULT_PA_UNIT_COUNT,
    DEFAULT_SIGNAL_SOURCE,
    DEFAULT_TEST_FREQUENCIES,
    ConfigBackfillError,
    project_authoring_config,
)

ROOT = Path(__file__).resolve().parents[2]
PROJECTION_SOURCE = ROOT / "presentation" / "qt" / "config_projection.py"
PAGE_SOURCE = ROOT / "presentation" / "qt" / "config_page.py"
AUTHORING_SOURCE = ROOT / "presentation" / "qt" / "configuration_authoring.py"
BACKFILL_HELPERS = (
    "_reset_control_widgets",
    "_apply_instrument_widgets",
    "_apply_test_parameter_widgets",
    "_apply_power_supply_widgets",
    "_apply_channel_widgets",
    "_apply_power_assignment_widgets",
    "_text_or_empty",
    "_bool_or_default",
    "_number_or_none",
    "_set_combo",
)


def _runtime_strings(path):
    """返回模块里非文档字符串的字符串常量（即真正参与逻辑的文本）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


class _Text:
    def __init__(self):
        self.current = ""
    def setText(self, value):
        self.current = value

    def text(self):
        return self.current


class _Check:
    def __init__(self):
        self.checked = False

    def setChecked(self, value):
        self.checked = bool(value)

    def isChecked(self):
        return self.checked


class _Value:
    def __init__(self):
        self.current = 0.0

    def setValue(self, value):
        self.current = float(value)

    def value(self):
        return self.current


class _Combo:
    def __init__(self, items):
        self.items = list(items)
        self.current = None

    def findText(self, text):
        return self.items.index(text) if text in self.items else -1

    def addItem(self, text):
        if text not in self.items:
            self.items.append(text)

    def addItems(self, items):
        for item in items:
            self.addItem(item)

    def clear(self):
        self.items.clear()
        self.current = None

    def setCurrentIndex(self, index):
        self.current = self.items[index] if 0 <= index < len(self.items) else None

    def setCurrentText(self, text):
        if self.findText(text) >= 0:
            self.current = text

    def currentText(self):
        return self.current


class FakeForm:
    """配置页控件的最小读取/写入协议替身。"""

    def __init__(self):
        self.freq_edit = _Text()
        self.start_power = _Value()
        self.stop_power = _Value()
        self.power_step = _Value()
        self.max_input_power = _Value()
        self.compression_combo = _Combo(("1dB", "3dB", "5dB"))
        self.attenuator_combo = _Combo(("30dB", "40dB"))
        self.pa_unit_count_combo = _Combo(("1", "2", "3"))
        self.test_mode_enabled = False
        self.driver_power_source_combo = _Combo(("直流电源", "自带供电"))
        self.power_config_widgets = {
            name: {
                "channels": {
                    channel: {
                        "role": _Combo(("gate", "drain")),
                        "voltage": _Value(),
                        "voltage_protection": _Value(),
                        "voltage_protection_enabled": _Check(),
                        "current": _Value(),
                        "current_protection": _Value(),
                        "current_protection_enabled": _Check(),
                    }
                    for channel in ("CH1", "CH2")
                }
            }
            for name in ("PS1", "PS2", "PS3", "PS4")
        }
        self.power_assignment_widgets = {
            "driver_source": _Combo(("直流电源", "自带供电")),
            "driver_power": _Combo(("PS1", "PS2", "PS3", "PS4")),
            "pa_unit1_power": _Combo(("PS1", "PS2", "PS3", "PS4")),
            "pa_unit2_power": _Combo(("PS1", "PS2", "PS3", "PS4")),
            "pa_unit3_power": _Combo(("PS1", "PS2", "PS3", "PS4")),
        }


class ConfigProjectionTests(unittest.TestCase):
    def test_projection_applies_authoring_fields(self):
        form = FakeForm()

        projected = project_authoring_config(
            form,
            {
                "test_frequencies": [2.4, 2.6],
                "signal_source": {"start_power": -30.0, "stop_power": -5.0, "step": 0.5},
                "compression_point": {"type": "3dB"},
                "attenuator": {"type": "30dB"},
                "driver_mode": {"enabled": True},
                "dut_config": {"max_input_power": 25.5, "power_supply_count": 3},
                "instruments": {
                    "signal_generator": {"address": "sim::sg2", "enabled": False},
                    "power_supplies": {
                        "PS1": {
                            "address": "sim::ps1",
                            "enabled": True,
                            "channels": {
                                "CH1": {
                                    "role": "gate",
                                    "voltage": {"value": 3.3, "protection": 5.0},
                                    "current": {"value": 0.5},
                                }
                            },
                        }
                    },
                },
                "power_supply_assignment": {
                    "driver_amplifier": {
                        "power_supply_count": 1,
                        "supplies": {"main": {"name": "PS2"}},
                    },
                    "dut_amplifier": {"supplies": {"carrier": {"name": "PS3"}}},
                },
            },
        )

        self.assertEqual(form.freq_edit.text(), "[2.4, 2.6]")
        self.assertEqual(form.start_power.value(), -30.0)
        self.assertEqual(form.compression_combo.currentText(), "3dB")
        self.assertEqual(form.pa_unit_count_combo.currentText(), "3")
        channel = form.power_config_widgets["PS1"]["channels"]["CH1"]
        self.assertEqual(channel["role"].currentText(), "gate")
        self.assertEqual(channel["voltage"].value(), 3.3)
        self.assertEqual(channel["current"].value(), 0.5)
        self.assertTrue(form.test_mode_enabled)
        self.assertEqual(
            form.driver_power_source_combo.currentText(), "直流电源"
        )
        self.assertEqual(
            form.power_assignment_widgets["driver_source"].currentText(), "直流电源"
        )
        self.assertEqual(
            form.power_assignment_widgets["driver_power"].currentText(), "PS2"
        )
        self.assertEqual(
            form.power_assignment_widgets["pa_unit1_power"].currentText(), "PS3"
        )
        # 仪器连接不由控件编辑：投影只把文档原样带回，交给页面与发现面板处理。
        self.assertEqual(
            projected["instruments"]["signal_generator"]["address"], "sim::sg2"
        )
        # 返回值是独立副本：调用方持有自己的文档，不与控件或入参共享结构。
        projected["test_frequencies"].append(9.9)
        self.assertEqual(form.freq_edit.text(), "[2.4, 2.6]")

    def test_projection_restricts_power_options_to_bound_supplies(self):
        """可选项来自设备绑定：未绑定的电源既不会被选中，也不会被补进下拉框。"""
        form = FakeForm()

        project_authoring_config(
            form,
            {
                "test_frequencies": [2.4],
                "dut_config": {"power_supply_count": 2},
                "power_supply_assignment": {
                    "driver_amplifier": {
                        "power_supply_count": 1,
                        "supplies": {"main": {"name": "PS2"}},
                    },
                    "dut_amplifier": {
                        "supplies": {
                            "carrier": {"name": "PS1"},
                            "peaking": {"name": "PS3"},
                        }
                    },
                },
            },
            supplies=["PS1"],
        )

        controls = form.power_assignment_widgets
        self.assertEqual(controls["driver_power"].items, ["PS1"])
        self.assertEqual(controls["pa_unit1_power"].currentText(), "PS1")
        # 显式引用未绑定的 PS2/PS3：保持空选择，交给校验报告，而不是临时补选项。
        self.assertIsNone(controls["driver_power"].currentText())
        self.assertIsNone(controls["pa_unit2_power"].currentText())

    def test_projection_uses_the_first_bound_supply_when_assignment_is_missing(self):
        form = FakeForm()

        project_authoring_config(
            form, {"test_frequencies": [2.4]}, supplies=["PS3", "PS4"]
        )

        controls = form.power_assignment_widgets
        self.assertEqual(controls["pa_unit1_power"].items, ["PS3", "PS4"])
        self.assertEqual(controls["pa_unit1_power"].currentText(), "PS3")

    def test_projection_without_bindings_leaves_power_assignment_empty(self):
        form = FakeForm()

        project_authoring_config(form, {"test_frequencies": [2.4]}, supplies=[])

        controls = form.power_assignment_widgets
        self.assertEqual(controls["driver_power"].items, [])
        self.assertIsNone(controls["driver_power"].currentText())

    def test_projection_reads_the_reference_from_supply_id(self):
        """``supply_id`` 是主字段：与之冲突的旧别名 ``name`` 不参与选择。"""
        form = FakeForm()

        project_authoring_config(
            form,
            {
                "power_supply_assignment": {
                    "dut_amplifier": {
                        "supplies": {
                            "carrier": {"supply_id": "PS3", "name": "PS1"},
                            "peaking": {"supply_id": "PS9", "name": "PS1"},
                        }
                    }
                }
            },
            supplies=["PS1", "PS2", "PS3"],
        )

        controls = form.power_assignment_widgets
        self.assertEqual(controls["pa_unit1_power"].currentText(), "PS3")
        # supply_id 指向未绑定电源时保持空选择，不被 name 改指另一台。
        self.assertIsNone(controls["pa_unit2_power"].currentText())

    def test_projection_keeps_an_explicitly_unbound_reference_empty(self):
        """``supply_id: null`` 是“声明了未绑定”，不能按缺省规则补第一台可用电源。"""
        form = FakeForm()

        project_authoring_config(
            form,
            {
                "power_supply_assignment": {
                    "dut_amplifier": {
                        "supplies": {
                            "carrier": {"supply_id": None, "channel": ["CH1", "CH2"]}
                        }
                    }
                }
            },
            supplies=["PS1"],
        )

        controls = form.power_assignment_widgets
        self.assertEqual(controls["pa_unit1_power"].items, ["PS1"])
        self.assertIsNone(controls["pa_unit1_power"].currentText())

    def test_projection_reads_a_legacy_reference_once(self):
        """只有 ``name`` 的旧记录仍能回填；它只被读一次，不是第二个可用性来源。"""
        form = FakeForm()

        project_authoring_config(
            form,
            {
                "power_supply_assignment": {
                    "dut_amplifier": {"supplies": {"carrier": {"name": "PS2"}}}
                }
            },
            supplies=["PS1", "PS2"],
        )

        self.assertEqual(
            form.power_assignment_widgets["pa_unit1_power"].currentText(), "PS2"
        )

    def test_projection_resets_before_applying(self):
        form = FakeForm()
        project_authoring_config(
            form,
            {
                "test_frequencies": [2.4],
                "signal_source": {"start_power": -30.0},
                "instruments": {
                    "signal_generator": {"address": "sim::sg2", "enabled": False}
                },
            },
        )

        project_authoring_config(form, {})

        self.assertEqual(form.freq_edit.text(), str(list(DEFAULT_TEST_FREQUENCIES)))
        self.assertEqual(form.start_power.value(), DEFAULT_SIGNAL_SOURCE["start_power"])
        self.assertEqual(form.compression_combo.currentText(), DEFAULT_COMPRESSION_TYPE)
        self.assertEqual(form.attenuator_combo.currentText(), DEFAULT_ATTENUATOR_TYPE)
        self.assertEqual(form.pa_unit_count_combo.currentText(), DEFAULT_PA_UNIT_COUNT)
        self.assertEqual(form.max_input_power.value(), DEFAULT_MAX_INPUT_POWER)

    def test_legacy_field_names_are_ignored(self):
        """投影只认识编写文档字段：旧字段别名不会被当成配置来源。"""
        form = FakeForm()

        project_authoring_config(
            form,
            {
                "config": {"test_frequencies": [9.9]},
                "legacy_payload": {"signal_source": {"start_power": 10.0}},
                "results": {"1.0": {}},
            },
        )

        self.assertEqual(form.freq_edit.text(), str(list(DEFAULT_TEST_FREQUENCIES)))
        self.assertEqual(form.start_power.value(), DEFAULT_SIGNAL_SOURCE["start_power"])

    def test_non_mapping_payloads_are_rejected(self):
        form = FakeForm()

        for payload in (None, ["not", "a", "mapping"], "text", 3):
            with self.subTest(payload=payload):
                with self.assertRaises(ConfigBackfillError):
                    project_authoring_config(form, payload)


class ConfigPageOwnershipTests(unittest.TestCase):
    """配置页只编辑配置编写文档：逐字段回填规则与正式 schema 都不属于页面。"""

    def test_config_page_no_longer_holds_projection_helpers(self):
        tree = ast.parse(PAGE_SOURCE.read_text(encoding="utf-8"))
        names = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        self.assertEqual(
            sorted(name for name in BACKFILL_HELPERS if name in names),
            [],
            "逐字段回填逻辑必须留在 config_projection，页面只负责控件接线",
        )

    def test_config_page_delegates_apply_config_to_the_projection(self):
        source = PAGE_SOURCE.read_text(encoding="utf-8")

        self.assertIn("project_authoring_config(", source)
        self.assertIn("self, config, supplies=", source)
        self.assertIn("from .config_projection import", source)

    def test_config_page_and_projection_do_not_interpret_the_formal_schema(self):
        """正式 ``test_plan`` / ``run_mapping`` 只能由 configuration_authoring 生成。"""
        for path in (PAGE_SOURCE, PROJECTION_SOURCE):
            with self.subTest(module=path.name):
                runtime = _runtime_strings(path)
                self.assertNotIn("test_plan", runtime)
                self.assertNotIn("run_mapping", runtime)
                self.assertFalse(
                    any("author_official_configuration" in text for text in runtime),
                    "配置页不得直接编写正式 schema",
                )

        authoring = AUTHORING_SOURCE.read_text(encoding="utf-8")
        self.assertIn('document["test_plan"]', authoring)
        self.assertIn('document["run_mapping"]', authoring)

    def test_projection_module_is_qt_free(self):
        tree = ast.parse(PROJECTION_SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])

        self.assertNotIn("PySide6", imported)
        self.assertNotIn("infrastructure", imported)


if __name__ == "__main__":
    unittest.main()
