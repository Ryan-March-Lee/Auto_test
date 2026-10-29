"""步骤 11 / 步骤 8：配置投影规则的独立测试（不需要 Qt）。

覆盖：投影只认识配置编写文档的字段、先重置再投影、非法输入必须失败、配置页不再持有
逐字段回填逻辑、“正式 schema 只由 configuration_authoring 生成”，以及电源分配不再
使用固定槽位默认值（缺省值只来自当前绑定集合）。
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
    POWER_ASSIGNMENT_SLOTS,
    ConfigBackfillError,
    driver_power_source_label,
    project_authoring_config,
    reset_control_widgets,
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
    """``QComboBox`` 的最小替身：显示文本与 ``itemData`` 分开保存（与真实控件一致）。"""

    def __init__(self, items):
        self.items = list(items)
        self.data = [None] * len(self.items)
        self.current = None

    def findText(self, text):
        return self.items.index(text) if text in self.items else -1

    def findData(self, data):
        return self.data.index(data) if data in self.data else -1

    def addItem(self, text, data=None):
        if text not in self.items:
            self.items.append(text)
            self.data.append(data)

    def addItems(self, items):
        for item in items:
            self.addItem(item)

    def clear(self):
        self.items.clear()
        self.data.clear()
        self.current = None

    def count(self):
        return len(self.items)

    def itemText(self, index):
        return self.items[index] if 0 <= index < len(self.items) else ""

    def itemData(self, index):
        return self.data[index] if 0 <= index < len(self.data) else None

    def currentIndex(self):
        return self.items.index(self.current) if self.current in self.items else -1

    def setCurrentIndex(self, index):
        self.current = self.items[index] if 0 <= index < len(self.items) else None

    def setCurrentText(self, text):
        if self.findText(text) >= 0:
            self.current = text

    def setCurrentData(self, data):
        """按业务值选中（真实控件用 ``findData`` + ``setCurrentIndex``）。"""
        index = self.findData(data)
        self.setCurrentIndex(index)

    def currentText(self):
        return self.current or ""

    def currentData(self):
        index = self.currentIndex()
        return self.data[index] if index >= 0 else None


class FakeForm:
    """配置页控件的最小读取/写入协议替身。"""

    def __init__(self, supplies=("DP8B1519", "DP8B0885")):
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
            for name in supplies
        }
        self.power_assignment_widgets = {
            "driver_source": _Combo(("直流电源", "自带供电")),
            "driver_power": _Combo(supplies),
            "pa_unit1_power": _Combo(supplies),
            "pa_unit2_power": _Combo(supplies),
            "pa_unit3_power": _Combo(supplies),
        }


class ConfigProjectionTests(unittest.TestCase):
    def test_projection_applies_authoring_fields(self):
        form = FakeForm(("DP8B1519", "DP8B0885", "DP8B0950"))
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
                        "DP8B1519": {
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
                        "supplies": {"main": {"supply_id": "DP8B0885"}},
                    },
                    "dut_amplifier": {
                        "supplies": {"carrier": {"supply_id": "DP8B0950"}}
                    },
                },
            },
            supplies=["DP8B1519", "DP8B0885", "DP8B0950"],
        )

        self.assertEqual(form.freq_edit.text(), "[2.4, 2.6]")
        self.assertEqual(form.start_power.value(), -30.0)
        self.assertEqual(form.compression_combo.currentText(), "3dB")
        self.assertEqual(form.pa_unit_count_combo.currentText(), "3")
        channel = form.power_config_widgets["DP8B1519"]["channels"]["CH1"]
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
            form.power_assignment_widgets["driver_power"].currentData(), "DP8B0885"
        )
        self.assertEqual(
            form.power_assignment_widgets["pa_unit1_power"].currentData(), "DP8B0950"
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
                        "supplies": {"main": {"name": "DP8B0885"}},
                    },
                    "dut_amplifier": {
                        "supplies": {
                            "carrier": {"name": "DP8B1519"},
                            "peaking": {"name": "DP8B0950"},
                        }
                    },
                },
            },
            supplies=["DP8B1519"],
        )

        controls = form.power_assignment_widgets
        self.assertEqual(controls["driver_power"].items, ["DP8B1519"])
        self.assertEqual(controls["pa_unit1_power"].currentText(), "DP8B1519")
        # 显式引用未绑定的 DP8B0885/DP8B0950：保持空选择，交给校验报告，而不是临时补选项。
        self.assertIsNone(controls["driver_power"].current)
        self.assertIsNone(controls["pa_unit2_power"].current)

    def test_projection_uses_the_first_bound_supply_when_assignment_is_missing(self):
        form = FakeForm()

        project_authoring_config(
            form, {"test_frequencies": [2.4]}, supplies=["DP8B0885", "DP8B0950"]
        )

        controls = form.power_assignment_widgets
        self.assertEqual(controls["pa_unit1_power"].items, ["DP8B0885", "DP8B0950"])
        # 缺失的分配落到绑定集合的第一台，而不是固定的 PS1..PS4。
        self.assertEqual(controls["pa_unit1_power"].currentText(), "DP8B0885")
        self.assertEqual(controls["driver_power"].currentText(), "DP8B0885")

    def test_projection_never_uses_a_fixed_slot_default(self):
        """绑定里只有 PS1 之外的短标签时，缺省值也必须是绑定里的设备。"""
        form = FakeForm(("DP8B0885",))

        project_authoring_config(form, {}, supplies=["DP8B0885"])

        for key in POWER_ASSIGNMENT_SLOTS:
            with self.subTest(slot=key):
                self.assertEqual(
                    form.power_assignment_widgets[key].currentText(), "DP8B0885"
                )
                self.assertNotIn("PS1", form.power_assignment_widgets[key].items)

    def test_projection_without_bindings_leaves_power_assignment_empty(self):
        form = FakeForm()

        project_authoring_config(form, {"test_frequencies": [2.4]}, supplies=[])

        controls = form.power_assignment_widgets
        self.assertEqual(controls["driver_power"].items, [])
        self.assertIsNone(controls["driver_power"].current)

    def test_projection_reads_the_reference_from_supply_id(self):
        """``supply_id`` 是主字段：与之冲突的旧别名 ``name`` 不参与选择。"""
        form = FakeForm()

        project_authoring_config(
            form,
            {
                "power_supply_assignment": {
                    "dut_amplifier": {
                        "supplies": {
                            "carrier": {
                                "supply_id": "DP8B0950",
                                "name": "DP8B1519",
                            },
                            "peaking": {
                                "supply_id": "DP9B9999",
                                "name": "DP8B1519",
                            },
                        }
                    }
                }
            },
            supplies=["DP8B1519", "DP8B0885", "DP8B0950"],
        )

        controls = form.power_assignment_widgets
        self.assertEqual(controls["pa_unit1_power"].currentText(), "DP8B0950")
        # supply_id 指向未绑定电源时保持空选择，不被 name 改指另一台。
        self.assertIsNone(controls["pa_unit2_power"].current)

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
            supplies=["DP8B1519"],
        )

        controls = form.power_assignment_widgets
        self.assertEqual(controls["pa_unit1_power"].items, ["DP8B1519"])
        self.assertIsNone(controls["pa_unit1_power"].current)

    def test_projection_reads_a_legacy_reference_once(self):
        """只有 ``name`` 的旧记录仍能回填；它只被读一次，不是第二个可用性来源。"""
        form = FakeForm()

        project_authoring_config(
            form,
            {
                "power_supply_assignment": {
                    "dut_amplifier": {"supplies": {"carrier": {"name": "DP8B0885"}}}
                }
            },
            supplies=["DP8B1519", "DP8B0885"],
        )

        self.assertEqual(
            form.power_assignment_widgets["pa_unit1_power"].currentText(), "DP8B0885"
        )

    def test_power_options_show_the_label_with_model_and_keep_supply_id_as_data(self):
        """步骤 9：显示文本是 ``短标签（型号）``，业务值是 ``itemData`` 里的 ``supply_id``。"""
        form = FakeForm()
        supplies = {
            "DP8B1519": {"model": "DP832A"},
            "DP8B0885": {"model": "DP832A"},
            "DP8B0950": {},
        }

        project_authoring_config(
            form,
            {
                "power_supply_assignment": {
                    "dut_amplifier": {"supplies": {"carrier": {"supply_id": "DP8B0885"}}}
                }
            },
            supplies=supplies,
        )

        combo = form.power_assignment_widgets["pa_unit1_power"]
        self.assertEqual(
            [combo.itemText(index) for index in range(combo.count())],
            ["DP8B1519（DP832A）", "DP8B0885（DP832A）", "DP8B0950"],
        )
        self.assertEqual(
            [combo.itemData(index) for index in range(combo.count())],
            ["DP8B1519", "DP8B0885", "DP8B0950"],
        )
        # 两台型号相同：选中靠 itemData，不会因为显示文本相同而落到第一台。
        self.assertEqual(combo.currentData(), "DP8B0885")
        self.assertEqual(combo.currentText(), "DP8B0885（DP832A）")

    def test_identical_models_do_not_make_two_supplies_the_same_option(self):
        """型号重复时显示文本可能完全相同，按文本选中会选错设备；必须按数据选中。"""
        form = FakeForm(("DP8B1519", "DP8B0885"))

        project_authoring_config(
            form, {}, supplies={"DP8B1519": {"model": "DP832A"}, "DP8B0885": {"model": "DP832A"}}
        )

        for key in POWER_ASSIGNMENT_SLOTS:
            with self.subTest(slot=key):
                combo = form.power_assignment_widgets[key]
                self.assertEqual(combo.count(), 2)
                self.assertEqual(combo.currentData(), "DP8B1519")

    def test_projection_ignores_a_previous_control_selection(self):
        """重置是无状态的：上一次的控件选择不参与缺省决策，只按当前绑定取第一台。"""
        form = FakeForm()
        form.power_assignment_widgets["pa_unit1_power"].setCurrentData("DP8B0885")

        reset_control_widgets(form, supplies=["DP8B1519", "DP8B0885"])

        for key in POWER_ASSIGNMENT_SLOTS:
            with self.subTest(slot=key):
                self.assertEqual(
                    form.power_assignment_widgets[key].currentData(), "DP8B1519"
                )

    def test_projection_keeps_an_existing_reference_from_the_document(self):
        """“已有引用优先”指配置文档里的引用，而不是控件残留值。"""
        form = FakeForm()
        form.power_assignment_widgets["pa_unit1_power"].setCurrentData("DP8B1519")

        project_authoring_config(
            form,
            {
                "power_supply_assignment": {
                    "dut_amplifier": {
                        "supplies": {"carrier": {"supply_id": "DP8B0885"}}
                    }
                }
            },
            supplies=["DP8B1519", "DP8B0885"],
        )

        self.assertEqual(
            form.power_assignment_widgets["pa_unit1_power"].currentData(), "DP8B0885"
        )
        # 文档里没有的分配仍取第一台可用电源，不受上面那条引用的影响。
        self.assertEqual(
            form.power_assignment_widgets["pa_unit2_power"].currentData(), "DP8B1519"
        )

    def test_driver_source_uses_the_page_inference_without_its_own_guess(self):
        """供电方式由 :func:`driver_power_source_label` 算好，投影不另立一套推断。"""
        form = FakeForm()
        form.driver_power_source = "自带供电"

        project_authoring_config(
            form,
            {
                "driver_mode": {"enabled": True},
                "power_supply_assignment": {
                    "driver_amplifier": {"power_supply_count": 1}
                },
            },
            supplies=["DP8B0885"],
        )

        # 分配里有 1 台驱动电源，但页面推断是自带供电：投影必须听页面的。
        self.assertEqual(
            form.power_assignment_widgets["driver_source"].currentText(), "自带供电"
        )

    def test_driver_source_label_falls_back_to_the_assignment(self):
        """``power_source`` 缺失时按驱动功放分配推断，两条路径同源。"""
        self.assertEqual(driver_power_source_label(None, 1), "直流电源")
        self.assertEqual(driver_power_source_label(None, 0), "自带供电")
        self.assertEqual(driver_power_source_label(None, None), "自带供电")
        self.assertEqual(driver_power_source_label("external_ac", 1), "自带供电")

    def test_driver_slot_uses_the_same_dynamic_default(self):
        """驱动功放与 PA 单元共用缺省规则，页面不再另设“空选择”默认。"""
        form = FakeForm()

        project_authoring_config(
            form,
            {"driver_mode": {"enabled": True, "power_source": "dc_supply"}},
            supplies=["DP8B0885", "DP8B0950"],
        )

        self.assertEqual(
            form.power_assignment_widgets["driver_power"].currentText(), "DP8B0885"
        )
        self.assertEqual(
            form.driver_power_source_combo.currentText(), "直流电源"
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

    def test_projection_has_no_fixed_power_slot_defaults(self):
        """步骤 8：投影不得再用 ``PS1``..``PS4`` 当电源分配的默认值。"""
        runtime = _runtime_strings(PROJECTION_SOURCE)
        slots = {f"PS{index}" for index in range(1, 5)}
        self.assertEqual(
            sorted(slot for slot in slots if any(slot in text for text in runtime)),
            [],
            "电源分配缺省值必须来自当前绑定集合，而不是固定槽位",
        )

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
