"""配置表单 Adapter 测试（GUI 重构步骤 7，不需要 QApplication）。

Adapter 是控件读写的唯一边界，因此这里用最小控件协议替身验证：整份配置组装、
测试参数快照、组合框业务值、回填投影、电源分配刷新与错误显示。业务规则仍分别由
application 的编写/绑定服务与 domain 校验定义，本模块不重复测试它们。
"""

import ast
import unittest
from pathlib import Path

from presentation.qt.config_form_adapter import ConfigurationFormAdapter

ROOT = Path(__file__).resolve().parents[2]
ADAPTER_SOURCE = ROOT / "presentation" / "qt" / "config_form_adapter.py"
PAGE_SOURCE = ROOT / "presentation" / "qt" / "configuration_page.py"


class _Text:
    def __init__(self, value=""):
        self.current = value

    def text(self):
        return self.current

    def setText(self, value):
        self.current = value


class _Value:
    def __init__(self, value=0.0):
        self.current = float(value)

    def value(self):
        return self.current

    def setValue(self, value):
        self.current = float(value)


class _Check:
    def __init__(self, checked=False):
        self.checked = bool(checked)

    def isChecked(self):
        return self.checked

    def setChecked(self, checked):
        self.checked = bool(checked)


class _Combo:
    """``QComboBox`` 的最小替身：显示文本与 ``itemData`` 分开保存。"""

    def __init__(self, items, data=None, current=0):
        self.items = list(items)
        self.data = list(data) if data is not None else [None] * len(self.items)
        self.current = current if 0 <= current < len(self.items) else None

    def clear(self):
        self.items = []
        self.data = []
        self.current = None

    def addItem(self, text, data=None):
        self.items.append(text)
        self.data.append(data)

    def count(self):
        return len(self.items)

    def findText(self, text):
        return self.items.index(text) if text in self.items else -1

    def findData(self, data):
        return self.data.index(data) if data in self.data else -1

    def itemText(self, index):
        return self.items[index] if 0 <= index < len(self.items) else ""

    def itemData(self, index):
        return self.data[index] if 0 <= index < len(self.data) else None

    def currentIndex(self):
        return self.current if self.current is not None else -1

    def currentText(self):
        index = self.currentIndex()
        return self.items[index] if index >= 0 else ""

    def currentData(self):
        return self.itemData(self.currentIndex())

    def setCurrentIndex(self, index):
        self.current = index if 0 <= index < len(self.items) else None

    def setCurrentText(self, text):
        index = self.findText(text)
        if index >= 0:
            self.current = index


def _bindings(*supply_ids):
    result = {
        "signal_generator": {
            "visa_address": "TCPIP0::192.168.1.201::inst0::INSTR",
            "model": "SMW200A",
            "full_code": "105462",
            "device_key": "rohde&schwarz:105462",
        }
    }
    if supply_ids:
        result["power_supplies"] = {
            supply_id: {
                "visa_address": f"TCPIP0::192.168.1.{index}::inst0::INSTR",
                "model": "DP832A",
                "full_code": f"DP8B2149015{index:02d}",
                "device_key": f"rigol technologies:dp8b2149015{index:02d}",
            }
            for index, supply_id in enumerate(supply_ids, 1)
        }
    return result


def _page(config=None, *, supplies=("DP8B1519", "DP8B0885"), frequency="[1.0]"):
    page = type("Page", (), {})()
    page.config = config if isinstance(config, dict) else {}
    page.device_bindings = _bindings(*supplies)
    page.freq_edit = _Text(frequency)
    page.start_power = _Value(-10.0)
    page.stop_power = _Value(0.0)
    page.power_step = _Value(1.0)
    page.max_input_power = _Value(30.0)
    page.compression_combo = _Combo(("1dB", "3dB", "5dB"))
    page.attenuator_combo = _Combo(("30dB", "40dB"))
    page.pa_unit_count_combo = _Combo(("1", "2", "3"))
    page.test_mode_enabled = False
    page.driver_power_source_combo = _Combo(("直流电源", "自带供电"))
    page.driver_power_source = "直流电源"
    page.power_config_widgets = {}
    page.power_assignment_widgets = {
        "driver_source": _Combo(("直流电源", "自带供电")),
        "driver_power": _Combo(supplies, data=supplies),
        "pa_unit1_power": _Combo(supplies, data=supplies),
        "pa_unit2_power": _Combo(supplies, data=supplies),
        "pa_unit3_power": _Combo(supplies, data=supplies),
    }
    return page


def _supply_channels(page, supply_id):
    """给页面替身补上一台电源的通道控件。"""
    page.power_config_widgets[supply_id] = {
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


class ConfigurationFormAdapterReadTests(unittest.TestCase):
    def test_read_configuration_builds_the_document_from_widgets(self):
        page = _page({"top": "keep", "wiring": {"confirmed": True}})
        adapter = ConfigurationFormAdapter(page)

        config = adapter.read_configuration()

        self.assertEqual(config["test_frequencies"], [1.0])
        self.assertEqual(config["signal_source"]["start_power"], -10.0)
        self.assertEqual(config["compression_point"]["type"], "1dB")
        self.assertEqual(config["instruments"]["signal_generator"]["address"],
                         "TCPIP0::192.168.1.201::inst0::INSTR")
        self.assertEqual(config["top"], "keep")
        self.assertNotIn("wiring", config)

    def test_read_test_parameters_turns_the_reading_into_a_typed_snapshot(self):
        page = _page()
        page.compression_combo.setCurrentText("3dB")
        adapter = ConfigurationFormAdapter(page)

        parameters = adapter.read_test_parameters()

        self.assertEqual([frequency.ghz for frequency in parameters.frequencies], [1.0])
        self.assertEqual(parameters.compression_target.db, 3.0)
        self.assertEqual(parameters.dut_supply_count, 1)

    def test_invalid_frequency_is_not_replaced_by_the_previous_reading(self):
        page = _page({"test_frequencies": [9.9]}, frequency="not-a-frequency")
        adapter = ConfigurationFormAdapter(page)

        parameters = adapter.read_test_parameters()

        # 旧值属于基线，不是这次控件读到的新值；页面校验仍按原文案报告。
        self.assertEqual(parameters.frequencies, ())
        self.assertFalse(adapter.report_errors(["测试频率必须是非空数字列表"]))

    def test_read_form_returns_the_document_and_snapshot_from_one_reading(self):
        page = _page({"test_frequencies": [9.9]}, frequency="not-a-frequency")
        adapter = ConfigurationFormAdapter(page)

        reading = adapter.read_form()

        # 完整文档保留基线里的频率（无效文本不写回配置），类型化快照则如实表示
        # “这次没有读到频率”；两者来自同一次控件读数，不各自再读一遍。
        self.assertEqual(reading.document["test_frequencies"], [9.9])
        self.assertEqual(reading.test_parameters.frequencies, ())

class ConfigurationFormAdapterWriteTests(unittest.TestCase):
    def test_apply_configuration_projects_the_document_onto_widgets(self):
        page = _page()
        adapter = ConfigurationFormAdapter(page)

        projected = adapter.apply_configuration(
            {
                "test_frequencies": [3.5],
                "signal_source": {"start_power": -5.0, "stop_power": 5.0, "step": 0.5},
                "compression_point": {"type": "3dB"},
                "attenuator": {"type": "30dB"},
                "driver_mode": {"enabled": False},
                "dut_config": {"max_input_power": 20.0, "power_supply_count": 2},
            }
        )

        self.assertEqual(projected["test_frequencies"], [3.5])
        self.assertEqual(page.freq_edit.text(), "[3.5]")
        self.assertEqual(page.compression_combo.currentText(), "3dB")
        self.assertEqual(page.pa_unit_count_combo.currentText(), "2")
        self.assertEqual(adapter.read_configuration()["test_frequencies"], [3.5])

    def test_read_power_channel_snapshot_returns_the_supplies_mapping(self):
        page = _page()
        _supply_channels(page, "DP8B1519")
        page.power_config_widgets["DP8B1519"]["channels"]["CH1"]["voltage"].setValue(12.0)
        adapter = ConfigurationFormAdapter(page)

        snapshot = adapter.read_power_channel_snapshot()

        self.assertEqual(
            snapshot["DP8B1519"]["channels"]["CH1"]["voltage"]["value"], 12.0
        )


class ConfigurationFormAdapterErrorTests(unittest.TestCase):
    def test_report_errors_joins_the_messages_into_one_notice(self):
        messages = []
        adapter = ConfigurationFormAdapter(_page(), on_error=messages.append)

        self.assertFalse(adapter.report_errors(["第一处错误", "", "第二处错误"]))

        self.assertEqual(messages, ["第一处错误；第二处错误"])

    def test_report_errors_returns_success_without_notifying(self):
        messages = []
        adapter = ConfigurationFormAdapter(_page(), on_error=messages.append)

        self.assertTrue(adapter.report_errors([]))

        self.assertEqual(messages, [])

    def test_validation_errors_are_the_page_visible_messages(self):
        page = _page(frequency="not-a-frequency")
        adapter = ConfigurationFormAdapter(page)

        errors = adapter.validation_errors()

        self.assertIn("测试频率必须是非空数字列表", errors)


class ConfigurationFormAdapterBoundaryTests(unittest.TestCase):
    def test_adapter_does_not_import_qt_or_reimplement_business_rules(self):
        tree = ast.parse(ADAPTER_SOURCE.read_text(encoding="utf-8-sig"))
        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
        self.assertNotIn("PySide6", {module.split(".")[0] for module in modules})
        source = ADAPTER_SOURCE.read_text(encoding="utf-8-sig")
        for forbidden in ("DeviceBindingService", "author_official_configuration"):
            self.assertNotIn(forbidden, source)

    def test_config_page_only_delegates_widget_reads_to_the_adapter(self):
        source = PAGE_SOURCE.read_text(encoding="utf-8-sig")
        self.assertIn("from .config_form_adapter import ConfigurationFormAdapter", source)
        self.assertIn("reading = self.form_adapter.read_form(", source)
        self.assertIn("self.form_adapter.apply_configuration(", source)
        # 页面不得为了测试参数快照再读一次控件：快照必须来自 read_form 的同一次读数。
        self.assertNotIn("self.form_adapter.read_configuration(", source)
        self.assertNotIn("self.form_adapter.read_test_parameters(", source)
        self.assertNotIn("from .config_form_state import ConfigFormState", source)
        self.assertNotIn("ConfigFormState(", source)
        self.assertNotIn("project_authoring_config(", source)


if __name__ == "__main__":
    unittest.main()
