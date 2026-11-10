"""步骤 11 / 步骤 8：配置投影规则的独立测试（不需要 Qt）。

覆盖：投影只认识配置编写文档的字段、先重置再投影、非法输入必须失败、配置页不再持有
逐字段回填逻辑、“正式 schema 只由 configuration_authoring 生成”，以及器件电源选择不再
有固定槽位下拉框（供电方式由页面推断，器件用哪台电源由计划路由决定）。
"""

import ast
import unittest
from pathlib import Path

from presentation.qt.config_projection import (
    DEFAULT_ATTENUATOR_TYPE,
    DEFAULT_COMPRESSION_TYPE,
    DEFAULT_DRIVER_GAIN_ESTIMATE,
    DEFAULT_FREQUENCY_EXTRA_TEXT,
    DEFAULT_FREQUENCY_START,
    DEFAULT_FREQUENCY_STEP,
    DEFAULT_FREQUENCY_STOP,
    DEFAULT_MAX_INPUT_POWER,
    DEFAULT_PA_UNIT_COUNT,
    DEFAULT_POWER_CHANNEL_VALUE,
    DEFAULT_SIGNAL_SOURCE,
    ConfigBackfillError,
    project_authoring_config,
    reset_control_widgets,
)

ROOT = Path(__file__).resolve().parents[2]
PROJECTION_SOURCE = ROOT / "presentation" / "qt" / "config_projection.py"
PAGE_SOURCE = ROOT / "presentation" / "qt" / "test_configuration_page.py"
ADAPTER_SOURCE = ROOT / "presentation" / "qt" / "config_form_adapter.py"
AUTHORING_SOURCE = ROOT / "application" / "configuration_authoring.py"
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
        self.freq_start = _Value()
        self.freq_stop = _Value()
        self.freq_step = _Value()
        self.freq_extra = _Text()
        self.frequency_import_error = None
        self.power_scan_import_error = None
        self.start_power = _Value()
        self.stop_power = _Value()
        self.power_step = _Value()
        self.driver_gain = _Text()
        self.max_input_power = _Value()
        self.compression_combo = _Combo(("1dB", "3dB", "5dB"))
        self.attenuator_combo = _Combo(("30dB", "40dB"))
        self.pa_unit_count_combo = _Combo(("1", "2", "3"))
        self.test_mode_enabled = False
        self.power_config_widgets = {
            name: {
                "channels": {
                    channel: {
                        "voltage": _Value(),
                        "voltage_protection_enabled": _Check(),
                        "current": _Value(),
                        "current_protection_enabled": _Check(),
                    }
                    for channel in ("CH1", "CH2")
                }
            }
            for name in supplies
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
                        "power_plan": {
                            "power_sequence": [
                                {"supply_id": "DP8B1519", "channel": "CH1"}
                            ],
                            "inter_channel_delay_s": 0.0,
                        }
                    },
                    "dut_amplifier": {
                        "power_plan": {
                            "power_sequence": [
                                {"supply_id": "DP8B1519", "channel": "CH2"}
                            ],
                            "inter_channel_delay_s": 0.5,
                        }
                    },
                },
            },
        )

        self.assertEqual(form.freq_start.value(), 2.4)
        self.assertEqual(form.freq_stop.value(), 2.6)
        self.assertEqual(form.freq_step.value(), 0.2)
        self.assertEqual(form.freq_extra.text(), "")
        self.assertIsNone(form.frequency_import_error)
        self.assertEqual(form.start_power.value(), -30.0)
        self.assertEqual(form.compression_combo.currentText(), "3dB")
        self.assertEqual(form.pa_unit_count_combo.currentText(), "3")
        channel = form.power_config_widgets["DP8B1519"]["channels"]["CH1"]
        self.assertNotIn("role", channel, "1.1 电源通道不再有供电角色控件")
        self.assertEqual(channel["voltage"].value(), 3.3)
        self.assertEqual(channel["current"].value(), 0.5)
        self.assertTrue(form.test_mode_enabled)
        # 仪器连接不由控件编辑：投影只把文档原样带回，交给页面与发现面板处理。
        self.assertEqual(
            projected["instruments"]["signal_generator"]["address"], "sim::sg2"
        )
        # 返回值是独立副本：调用方持有自己的文档，不与控件或入参共享结构。
        projected["test_frequencies"].append(9.9)
        self.assertEqual(form.freq_stop.value(), 2.6)

    def test_projection_applies_and_clears_the_driver_gain(self):
        """预估增益回填为文本；缺失时回到“尚未填写”，不伪造 0 dB。"""
        form = FakeForm()

        project_authoring_config(
            form,
            {"driver_mode": {"enabled": True, "estimated_gain_db": 20.5}},
        )
        self.assertTrue(form.test_mode_enabled)
        self.assertEqual(form.driver_gain.text(), "20.5")

        project_authoring_config(form, {"driver_mode": {"enabled": True}})
        self.assertEqual(form.driver_gain.text(), DEFAULT_DRIVER_GAIN_ESTIMATE)

    def test_non_boolean_driver_mode_values_fall_back_to_the_default(self):
        """``"false"``/``0``/``None`` 都不是布尔值：按缺失处理，回填默认启用。"""
        form = FakeForm()

        for value in (None, 0, 1, "", "false", "true"):
            with self.subTest(enabled=value):
                project_authoring_config(
                    form, {"driver_mode": {"enabled": value}}
                )
                self.assertTrue(form.test_mode_enabled)

    def test_driver_gain_rendering_keeps_precision_and_rejects_non_numbers(self):
        """回填文本保留数值精度；非数值/非有限值回到空基线，不静默改写配置。"""
        form = FakeForm()

        project_authoring_config(
            form,
            {"driver_mode": {"enabled": True, "estimated_gain_db": 20.1234567}},
        )
        self.assertEqual(form.driver_gain.text(), "20.1234567")
        self.assertEqual(float(form.driver_gain.text()), 20.1234567)

        for value in (None, True, False, "20.5", float("nan"), float("inf")):
            with self.subTest(value=value):
                project_authoring_config(
                    form,
                    {"driver_mode": {"enabled": True, "estimated_gain_db": value}},
                )
                self.assertEqual(
                    form.driver_gain.text(), DEFAULT_DRIVER_GAIN_ESTIMATE
                )

    def test_projection_sets_the_protection_switches_without_threshold_widgets(self):
        """保护阈值已改为自动推导，投影只恢复启用开关，不再有阈值输入框。"""
        form = FakeForm(("DP8B1519",))

        project_authoring_config(
            form,
            {
                "instruments": {
                    "power_supplies": {
                        "DP8B1519": {
                            "channels": {
                                "CH1": {
                                    "voltage": {
                                        "value": 3.3,
                                        "protection": 5.0,
                                        "protection_enabled": True,
                                    },
                                    "current": {
                                        "value": 0.5,
                                        "protection_enabled": False,
                                    },
                                }
                            }
                        }
                    }
                }
            },
        )

        channel = form.power_config_widgets["DP8B1519"]["channels"]["CH1"]
        self.assertNotIn("voltage_protection", channel)
        self.assertNotIn("current_protection", channel)
        self.assertTrue(channel["voltage_protection_enabled"].isChecked())
        self.assertFalse(channel["current_protection_enabled"].isChecked())

    def test_projection_does_not_restore_supply_selection_combos(self):
        """计划路由是器件电源选择的唯一来源，投影不再恢复任何按器件的下拉框。"""
        form = FakeForm()

        project_authoring_config(
            form,
            {
                "power_supply_assignment": {
                    "dut_amplifier": {
                        "power_supply_count": 1,
                        "supplies": {"carrier": {"supply_id": "DP8B0885"}},
                    }
                }
            },
        )

        self.assertFalse(hasattr(form, "power_assignment_widgets"))

    def test_reset_uses_the_shared_channel_baseline(self):
        """通道数值重置到空配置基线的值，与构建模块的非法值回落同源。"""
        form = FakeForm()
        for record in form.power_config_widgets.values():
            for controls in record["channels"].values():
                controls["voltage"].setValue(9.5)
                controls["current"].setValue(4.0)
                controls["voltage_protection_enabled"].setChecked(True)
                controls["current_protection_enabled"].setChecked(True)

        reset_control_widgets(form)

        channel = form.power_config_widgets["DP8B1519"]["channels"]["CH1"]
        for key in ("voltage", "current"):
            with self.subTest(control=key):
                self.assertEqual(channel[key].value(), DEFAULT_POWER_CHANNEL_VALUE)
        self.assertFalse(channel["voltage_protection_enabled"].isChecked())
        self.assertFalse(channel["current_protection_enabled"].isChecked())

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

        self.assertEqual(form.freq_start.value(), DEFAULT_FREQUENCY_START)
        self.assertEqual(form.freq_stop.value(), DEFAULT_FREQUENCY_STOP)
        self.assertEqual(form.freq_step.value(), DEFAULT_FREQUENCY_STEP)
        self.assertEqual(form.freq_extra.text(), DEFAULT_FREQUENCY_EXTRA_TEXT)
        self.assertIsNone(form.frequency_import_error)
        self.assertEqual(form.start_power.value(), DEFAULT_SIGNAL_SOURCE["start_power"])
        self.assertEqual(form.driver_gain.text(), DEFAULT_DRIVER_GAIN_ESTIMATE)
        self.assertEqual(form.compression_combo.currentText(), DEFAULT_COMPRESSION_TYPE)
        self.assertEqual(form.attenuator_combo.currentText(), DEFAULT_ATTENUATOR_TYPE)
        self.assertEqual(form.pa_unit_count_combo.currentText(), DEFAULT_PA_UNIT_COUNT)
        self.assertEqual(form.max_input_power.value(), DEFAULT_MAX_INPUT_POWER)

    def test_non_uniform_frequencies_are_loaded_as_an_error_state(self):
        """非等步长旧列表不做兼容拆分：控件只做展示，页面凭错误状态禁止保存。"""
        form = FakeForm()

        project_authoring_config(
            form,
            {"test_frequencies": [4.0, 4.4, 4.8, 5.2, 5.6, 5.8]},
        )

        self.assertIn("不是严格等步长", form.frequency_import_error)
        self.assertIn("5.8", form.frequency_import_error)
        self.assertEqual(form.freq_start.value(), 4.0)
        self.assertEqual(form.freq_stop.value(), 5.8)
        self.assertEqual(form.freq_step.value(), 0.4)

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

        self.assertEqual(form.freq_start.value(), DEFAULT_FREQUENCY_START)
        self.assertEqual(form.freq_stop.value(), DEFAULT_FREQUENCY_STOP)
        self.assertEqual(form.freq_step.value(), DEFAULT_FREQUENCY_STEP)
        self.assertEqual(form.freq_extra.text(), DEFAULT_FREQUENCY_EXTRA_TEXT)
        self.assertEqual(form.start_power.value(), DEFAULT_SIGNAL_SOURCE["start_power"])

    def test_non_mapping_payloads_are_rejected(self):
        form = FakeForm()

        for payload in (None, ["not", "a", "mapping"], "text", 3):
            with self.subTest(payload=payload):
                with self.assertRaises(ConfigBackfillError):
                    project_authoring_config(form, payload)


class ConfigurationPageOwnershipTests(unittest.TestCase):
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

    def test_config_page_delegates_apply_config_to_the_form_adapter(self):
        page_source = PAGE_SOURCE.read_text(encoding="utf-8")

        self.assertIn("self.form_adapter.apply_configuration(", page_source)
        self.assertNotIn("project_authoring_config(", page_source)
        adapter = ADAPTER_SOURCE.read_text(encoding="utf-8")
        self.assertIn("project_authoring_config(", adapter)
        self.assertNotIn(
            "supplies=",
            adapter,
            "投影不再接收或转发旧固定分配参数",
        )
        self.assertIn("from .config_projection import", adapter)

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
        self.assertIn('values["test_plan"]', authoring)
        self.assertIn('values["run_mapping"]', authoring)

    def test_projection_has_no_fixed_power_slot_defaults(self):
        """步骤 8：投影不得再用 ``PS1``..``PS4`` 当电源分配的默认值。"""
        runtime = _runtime_strings(PROJECTION_SOURCE)
        slots = {f"PS{index}" for index in range(1, 5)}
        self.assertEqual(
            sorted(slot for slot in slots if any(slot in text for text in runtime)),
            [],
            "电源分配缺省值必须来自当前绑定集合，而不是固定槽位",
        )

    def test_single_plan_projects_into_the_three_widgets(self):
        form = FakeForm()

        project_authoring_config(
            form,
            {
                "signal_source": {
                    "power_scan_plans": [
                        {"start_power": -30.0, "step": 0.5, "stop_power": -5.0}
                    ]
                }
            },
        )

        self.assertEqual(form.start_power.value(), -30.0)
        self.assertEqual(form.stop_power.value(), -5.0)
        self.assertEqual(form.power_step.value(), 0.5)
        self.assertIsNone(form.power_scan_import_error)

    def test_multiple_plans_block_saving_instead_of_collapsing_to_one(self):
        form = FakeForm()

        project_authoring_config(
            form,
            {
                "signal_source": {
                    "power_scan_plans": [
                        {"start_power": -38.0, "step": 1.0, "stop_power": -10.0},
                        {"start_power": -10.0, "step": 0.25, "stop_power": -2.0},
                    ]
                }
            },
        )

        self.assertIn("多段", form.power_scan_import_error)
        # 展示首段起点与末段终点；保存被阻止，行不会因此被折叠成一段。
        self.assertEqual(form.start_power.value(), -38.0)
        self.assertEqual(form.stop_power.value(), -2.0)

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
