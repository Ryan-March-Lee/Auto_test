"""步骤 12：独立电源配置页的纯 UI 测试。

覆盖独立创建、绑定状态展示、驱动模式派生、通道参数/分配/上电计划/漏极回填、
片段提交与校验消息。页面只消费配置文档与设备绑定，不启动完整参数页或真实后端。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from presentation.qt.config_projection import ConfigBackfillError
from presentation.qt.power_configuration_page import PowerConfigurationPage

CHANNELS = ("CH1", "CH2")


def power_supply_bindings(*supply_ids):
    return {
        "power_supplies": {
            supply_id: {
                "device_key": f"rigol technologies:{supply_id.casefold()}",
                "visa_address": f"TCPIP0::192.168.1.{index}::inst0::INSTR",
                "model": "DP832A",
            }
            for index, supply_id in enumerate(supply_ids, 1)
        }
    }


def assignment(supply_id, channels=None):
    return {
        "supply_id": supply_id,
        "channel": list(CHANNELS if channels is None else channels),
    }


def plan(*routes, delay=0.0):
    return {
        "power_sequence": [
            {"supply_id": supply_id, "channel": channel}
            for supply_id, channel in routes
        ],
        "inter_channel_delay_s": delay,
    }


def valid_config():
    """一份可被正式校验接受的编辑文档，用于验证页面的错误通道。"""
    return {
        "test_frequencies": [1.0],
        "signal_source": {"start_power": -10.0, "stop_power": 0.0, "step": 1.0},
        "compression_point": {"type": "5dB"},
        "attenuator": {"type": "40dB"},
        "driver_mode": {"enabled": False},
        "dut_config": {
            "max_input_power": 30.0,
            "power_supply_count": 1,
            "drain_channel": {"supply_id": "DP8B1519", "channel": "CH2"},
        },
        "instruments": {
            "signal_generator": {
                "address": "sim::sg",
                "model": "SG",
                "enabled": True,
            },
            "spectrum_analyzer": {
                "address": "sim::sa",
                "model": "SA",
                "enabled": True,
            },
            "power_supplies": {
                "DP8B1519": {
                    "label": "DP8B1519",
                    "device_key": "rigol technologies:dp8b1519",
                    "full_code": "DP8B214901519",
                    "model": "DP832A",
                    "address": "sim::ps1",
                    "enabled": True,
                    "channels": {
                        "CH1": {
                            "voltage": {"value": 2.8},
                            "current": {"value": 0.1},
                        },
                        "CH2": {
                            "voltage": {"value": 28.0},
                            "current": {"value": 2.0},
                        },
                    },
                }
            },
        },
        "device_bindings": {
            "signal_generator": {"visa_address": "sim::sg"},
            "spectrum_analyzer": {"visa_address": "sim::sa"},
            "power_supplies": {
                "DP8B1519": {
                    "device_key": "rigol technologies:dp8b1519",
                    "visa_address": "sim::ps1",
                    "model": "DP832A",
                }
            },
        },
        "power_supply_assignment": {
            "driver_amplifier": {
                "power_supply_count": 0,
                "supplies": {},
                "power_plan": plan(),
            },
            "dut_amplifier": {
                "power_supply_count": 1,
                "supplies": {"carrier": assignment("DP8B1519")},
                "power_plan": plan(
                    ("DP8B1519", "CH1"), ("DP8B1519", "CH2"), delay=1.0
                ),
            },
        },
    }


class PowerConfigurationPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_page(self, **extra):
        config = valid_config()
        config.update(extra)
        page = PowerConfigurationPage(config)
        page.show()
        self.app.processEvents()
        return page

    # ---- 独立创建与绑定状态 ------------------------------------------------

    def test_the_page_is_created_without_the_parameter_page(self):
        page = self.make_page()

        self.assertIsInstance(page, PowerConfigurationPage)
        self.assertIsNotNone(page.power_section)
        self.assertEqual(page.power_tabs.count(), 1)
        self.assertIn("DP8B1519", page.power_tabs.tabText(0))

    def test_binding_status_lists_the_applied_supplies(self):
        page = self.make_page()

        text = page.binding_status_label.text()

        self.assertIn("已绑定电源", text)
        self.assertIn("DP8B1519", text)

    def test_without_bindings_the_status_says_what_to_do(self):
        page = PowerConfigurationPage({})
        page.show()
        self.app.processEvents()

        self.assertIn("尚未绑定电源", page.binding_status_label.text())
        self.assertIn("仪器配置", page.binding_status_label.text())

    def test_applying_bindings_refreshes_the_status_and_options(self):
        page = self.make_page()
        page.apply_device_bindings(power_supply_bindings("DP8B1519", "DP8B0885"))

        self.assertIn("DP8B0885", page.binding_status_label.text())
        options = [
            page.pa_unit1_power_combo.itemData(index)
            for index in range(page.pa_unit1_power_combo.count())
        ]
        self.assertEqual(options, ["DP8B1519", "DP8B0885"])
        self.assertEqual(page.power_tabs.count(), 2)

    def test_power_tabs_are_titled_with_the_label_and_model(self):
        page = PowerConfigurationPage(
            {
                "instruments": {
                    "power_supplies": {
                        "DP8B1519": {"model": "DP832A"},
                        "DP8B0885": {"model": "DP832A"},
                    }
                },
                "device_bindings": power_supply_bindings("DP8B1519", "DP8B0885"),
            }
        )
        page.show()
        self.app.processEvents()

        titles = [
            page.power_tabs.tabText(index) for index in range(page.power_tabs.count())
        ]
        self.assertEqual(titles, ["DP832A（DP8B1519）", "DP832A（DP8B0885）"])
        self.assertEqual(set(page.power_config_widgets), {"DP8B1519", "DP8B0885"})

    # ---- PA 单元与驱动模式 -------------------------------------------------

    def test_pa_unit_visibility_tracks_count(self):
        page = self.make_page(dut_config={"power_supply_count": 1})
        self.assertFalse(page.pa_unit2_power_combo.isVisible())
        self.assertFalse(page.pa_unit3_power_combo.isVisible())

        page.set_pa_unit_count(2)
        self.assertTrue(page.pa_unit2_power_combo.isVisible())
        self.assertFalse(page.pa_unit3_power_combo.isVisible())

        page.set_pa_unit_count(3)
        self.assertTrue(page.pa_unit3_power_combo.isVisible())

    def test_driver_power_channel_follows_the_power_source(self):
        page = self.make_page(
            driver_mode={"enabled": True, "power_source": "dc_supply"},
            power_supply_assignment={
                "driver_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {"main": assignment("DP8B1519", ["CH1"])},
                },
                "dut_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {"carrier": assignment("DP8B1519", ["CH2"])},
                },
            },
        )
        page.set_test_mode(True)
        self.assertEqual(page.driver_power_source_combo.currentText(), "直流电源")
        self.assertTrue(page.driver_power_combo.isEnabled())
        self.assertTrue(page.driver_power_hint.isHidden())

        page.driver_power_source_combo.setCurrentText("自带供电")
        self.assertFalse(page.driver_power_combo.isEnabled())
        self.assertTrue(page.driver_power_hint.isVisible())
        self.assertEqual(page.build_config()["driver_mode"]["power_source"], "external_ac")

        page.driver_power_source_combo.setCurrentText("直流电源")
        self.assertTrue(page.driver_power_combo.isEnabled())

        page.set_test_mode(False)
        self.assertFalse(page.driver_power_combo.isEnabled())

    def test_driver_mode_emits_change_signal(self):
        page = self.make_page()
        changes = []
        page.driver_mode_changed.connect(changes.append)

        page.set_test_mode(False)
        self.assertEqual(changes[-1], False)
        page.set_test_mode(True)
        self.assertEqual(changes[-1], True)

    def test_no_driver_mode_hides_the_power_assignment_box(self):
        page = self.make_page(driver_mode={"enabled": True})

        page.set_test_mode(False)

        self.assertFalse(page.power_assignment_group.isVisible())

    # ---- 片段提交与回填 ----------------------------------------------------

    def test_build_fragment_contains_only_the_power_fields(self):
        page = self.make_page()

        fragment = page.build_fragment()

        self.assertIn("instruments", fragment)
        self.assertIn("power_supply_assignment", fragment)
        self.assertIn("drain_channel", fragment.get("dut_config", {}))
        self.assertNotIn("test_frequencies", fragment)
        self.assertNotIn("signal_source", fragment)

    def test_merging_the_fragment_keeps_the_other_page_fields(self):
        base = {
            "test_frequencies": [9.9],
            "signal_source": {"start_power": -3.0},
            "power_supply_assignment": {"dut_amplifier": {"supplies": {}}},
            "dut_config": {"drain_channel": None},
        }
        page = self.make_page()

        merged = page.build_config(base)

        self.assertEqual(merged["test_frequencies"], [9.9])
        self.assertEqual(merged["signal_source"], {"start_power": -3.0})
        self.assertEqual(
            merged["dut_config"]["drain_channel"],
            {"supply_id": "DP8B1519", "channel": "CH2"},
        )
        self.assertEqual(
            merged["instruments"]["power_supplies"]["DP8B1519"]["channels"]["CH2"][
                "voltage"
            ]["value"],
            28.0,
        )

    def test_apply_config_backfills_channels_plan_and_drain(self):
        page = PowerConfigurationPage({})
        page.show()
        self.app.processEvents()

        page.apply_config(valid_config())

        self.assertEqual(
            page.power_config_widgets["DP8B1519"]["channels"]["CH2"]["voltage"].value(),
            28.0,
        )
        self.assertEqual(
            page.dut_power_plan_editor.read_plan(),
            plan(("DP8B1519", "CH1"), ("DP8B1519", "CH2"), delay=1.0),
        )
        self.assertEqual(
            page.read_drain_channel(),
            {"supply_id": "DP8B1519", "channel": "CH2"},
        )

    def test_apply_config_reports_invalid_payload(self):
        page = self.make_page()
        for payload in (None, ["not", "a", "mapping"], "text"):
            with self.subTest(payload=payload):
                with self.assertRaises(ConfigBackfillError):
                    page.apply_config(payload)

    def test_rebuild_keeps_uncommitted_channels_plan_and_drain(self):
        page = self.make_page(
            device_bindings=power_supply_bindings("DP8B1519", "DP8B0885"),
            power_supply_assignment={
                "driver_amplifier": {
                    "power_supply_count": 0,
                    "supplies": {},
                },
                "dut_amplifier": {
                    "power_supply_count": 2,
                    "supplies": {
                        "carrier": assignment("DP8B1519", ["CH1"]),
                        "peaking": assignment("DP8B0885", ["CH2"]),
                    },
                    "power_plan": plan(
                        ("DP8B0885", "CH2"), ("DP8B1519", "CH1"), delay=2.0
                    ),
                },
            },
            dut_config={
                "power_supply_count": 2,
                "drain_channel": {"supply_id": "DP8B0885", "channel": "CH2"},
            },
        )
        page.power_config_widgets["DP8B0885"]["channels"]["CH1"]["voltage"].setValue(12.5)
        expected_plan = page.dut_power_plan_editor.read_plan()
        expected_drain = page.read_drain_channel()

        page.apply_device_bindings(power_supply_bindings("DP8B1519", "DP8B0885"))

        self.assertEqual(
            page.power_config_widgets["DP8B0885"]["channels"]["CH1"]["voltage"].value(),
            12.5,
        )
        self.assertEqual(page.dut_power_plan_editor.read_plan(), expected_plan)
        self.assertEqual(page.read_drain_channel(), expected_drain)

    # ---- 分配与计划 --------------------------------------------------------

    def test_candidates_are_the_channels_declared_by_each_slot(self):
        page = self.make_page(
            device_bindings=power_supply_bindings("DP8B1519", "DP8B0885"),
            power_supply_assignment={
                "dut_amplifier": {
                    "power_supply_count": 2,
                    "supplies": {
                        "carrier": assignment("DP8B1519", ["CH1"]),
                        "peaking": assignment("DP8B0885", ["CH2"]),
                    },
                }
            },
            dut_config={"power_supply_count": 2},
        )

        self.assertEqual(
            page.dut_power_plan_editor.routes(),
            (("DP8B1519", "CH1"), ("DP8B0885", "CH2")),
        )
        self.assertIn("未确认", page.dut_power_plan_editor.status.text())

    def test_two_slots_may_share_a_supply_without_copying_channels(self):
        page = self.make_page(
            device_bindings=power_supply_bindings("DP8B1519"),
            power_supply_assignment={
                "dut_amplifier": {
                    "power_supply_count": 2,
                    "supplies": {
                        "carrier": assignment("DP8B1519", ["CH1"]),
                        "peaking": assignment("DP8B1519", ["CH2"]),
                    },
                }
            },
            dut_config={"power_supply_count": 2},
        )

        self.assertEqual(
            page.dut_power_plan_editor.routes(),
            (("DP8B1519", "CH1"), ("DP8B1519", "CH2")),
        )

    def test_plan_candidates_follow_the_assignment_channels(self):
        page = self.make_page(
            device_bindings=power_supply_bindings("DP8B1519", "DP8B0885"),
            power_supply_assignment={
                "dut_amplifier": {
                    "power_supply_count": 2,
                    "supplies": {
                        "carrier": assignment("DP8B1519", ["CH1"]),
                        "peaking": assignment("DP8B0885", ["CH2"]),
                    },
                }
            },
            dut_config={"power_supply_count": 2},
        )

        self.assertEqual(
            page.dut_power_plan_editor.routes(),
            (("DP8B1519", "CH1"), ("DP8B0885", "CH2")),
        )
        self.assertIn("未确认", page.dut_power_plan_editor.status.text())

    def test_power_assignment_reference_is_read_from_supply_id(self):
        page = self.make_page(
            device_bindings=power_supply_bindings("DP8B1519", "DP8B0885"),
            power_supply_assignment={
                "dut_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {
                        "carrier": {
                            "supply_id": "DP8B0885",
                            "name": "DP8B1519",
                            "channel": ["CH1", "CH2"],
                        }
                    },
                }
            },
            dut_config={"power_supply_count": 1},
        )

        self.assertEqual(page.pa_unit1_power_combo.currentData(), "DP8B0885")

    def test_unbound_power_reference_does_not_select_another_supply(self):
        page = self.make_page(
            device_bindings=power_supply_bindings("DP8B0885"),
            power_supply_assignment={
                "dut_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {
                        "carrier": {
                            "name": "DP8B1519",
                            "channel": ["CH1", "CH2"],
                        }
                    },
                }
            },
            dut_config={"power_supply_count": 1},
        )

        self.assertEqual(page.pa_unit1_power_combo.currentText(), "")
        self.assertEqual(page.pa_unit1_power_combo.currentIndex(), -1)
        entry = page.build_config()["power_supply_assignment"]["dut_amplifier"][
            "supplies"
        ]["carrier"]
        self.assertIsNone(entry["supply_id"])

    def test_explicitly_unbound_reference_stays_empty(self):
        page = self.make_page(
            device_bindings=power_supply_bindings("DP8B0885"),
            power_supply_assignment={
                "dut_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {"carrier": {"supply_id": None, "channel": CHANNELS}},
                }
            },
            dut_config={"power_supply_count": 1},
        )

        self.assertEqual(page.pa_unit1_power_combo.currentIndex(), -1)
        self.assertEqual(page.pa_unit1_power_combo.currentText(), "")
        entry = page.build_config()["power_supply_assignment"]["dut_amplifier"][
            "supplies"
        ]["carrier"]
        self.assertIsNone(entry["supply_id"])
        self.assertNotIn("name", entry)

    def test_missing_assignment_defaults_to_the_first_bound_supply(self):
        page = self.make_page(
            device_bindings=power_supply_bindings("DP8B1519", "DP8B0885"),
            dut_config={"power_supply_count": 1},
        )

        self.assertEqual(page.pa_unit1_power_combo.currentData(), "DP8B1519")

    def test_power_assignment_shows_the_label_with_model_and_keeps_supply_id_as_data(self):
        bindings = power_supply_bindings("DP8B1519", "DP8B0885")
        page = self.make_page(
            device_bindings=bindings,
            dut_config={"power_supply_count": 1},
        )

        combo = page.pa_unit1_power_combo
        self.assertEqual(
            [combo.itemText(index) for index in range(combo.count())],
            ["DP832A（DP8B1519）", "DP832A（DP8B0885）"],
        )
        self.assertEqual(
            [combo.itemData(index) for index in range(combo.count())],
            ["DP8B1519", "DP8B0885"],
        )

    def test_saving_reads_the_supply_id_from_item_data_not_the_display_text(self):
        page = self.make_page(
            device_bindings=power_supply_bindings("DP8B1519", "DP8B0885"),
            power_supply_assignment={
                "dut_amplifier": {
                    "supplies": {"carrier": {"supply_id": "DP8B0885"}}
                }
            },
            dut_config={"power_supply_count": 1},
        )
        self.assertEqual(page.pa_unit1_power_combo.currentText(), "DP832A（DP8B0885）")

        entry = page.build_config()["power_supply_assignment"]["dut_amplifier"][
            "supplies"
        ]["carrier"]

        self.assertEqual(entry["supply_id"], "DP8B0885")
        self.assertNotIn("name", entry, "1.1 只写 supply_id，不再写过渡别名")

    # ---- 漏极 --------------------------------------------------------------

    def test_the_drain_selection_is_read_as_a_physical_route(self):
        page = self.make_page()

        self.assertEqual(
            page.read_drain_channel(),
            {"supply_id": "DP8B1519", "channel": "CH2"},
        )
        options = [
            page.drain_channel_combo.itemData(index)
            for index in range(page.drain_channel_combo.count())
        ]
        self.assertIn(("DP8B1519", "CH2"), options)

    def test_an_invalid_existing_drain_stays_visible_for_validation(self):
        page = self.make_page(
            dut_config={
                "power_supply_count": 1,
                "drain_channel": {"supply_id": "DP8B9999", "channel": "CH1"},
            }
        )

        self.assertEqual(
            page.read_drain_channel(),
            {"supply_id": "DP8B9999", "channel": "CH1"},
            "不在当前候选里的漏极不能被静默清空",
        )
        self.assertIn("无效", page.drain_channel_combo.currentText())

    def test_drain_options_are_limited_to_the_active_plan(self):
        page = self.make_page(
            device_bindings=power_supply_bindings("DP8B1519", "DP8B0885"),
            power_supply_assignment={
                "dut_amplifier": {
                    "power_supply_count": 2,
                    "supplies": {
                        "carrier": assignment("DP8B1519", ["CH1"]),
                        "peaking": assignment("DP8B0885", ["CH2"]),
                    },
                    "power_plan": plan(("DP8B0885", "CH2")),
                }
            },
            dut_config={
                "power_supply_count": 2,
                "drain_channel": {"supply_id": "DP8B0885", "channel": "CH2"},
            },
        )

        options = [
            page.drain_channel_combo.itemData(index)
            for index in range(1, page.drain_channel_combo.count())
        ]
        self.assertEqual(options, [("DP8B0885", "CH2")])

    # ---- 校验消息 ----------------------------------------------------------

    def test_validation_passes_for_a_complete_power_configuration(self):
        page = self.make_page()

        self.assertTrue(page.validate())

    def test_validation_reports_power_problems_through_the_page_signal(self):
        config = valid_config()
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"][
            "supply_id"
        ] = "DP8B9999"
        page = PowerConfigurationPage(config)
        page.show()
        self.app.processEvents()
        failures = []
        page.validation_failed.connect(failures.append)

        allowed = page.validate()

        self.assertFalse(allowed)
        self.assertTrue(failures)
        self.assertIn("电源", failures[0])

    def test_validation_does_not_report_parameter_errors_as_power_errors(self):
        """正式校验按页面归属拆分：测试参数的问题由参数页报告。"""
        config = valid_config()
        config["test_frequencies"] = []
        page = PowerConfigurationPage(config)
        page.show()
        self.app.processEvents()
        failures = []
        page.validation_failed.connect(failures.append)

        allowed = page.validate()

        self.assertTrue(allowed)
        self.assertEqual(failures, [])

    def test_validation_keeps_power_errors_when_parameter_errors_also_exist(self):
        """两页同时有问题时，电源页只报自己的那一段，不把频率错误混进来。"""
        config = valid_config()
        config["test_frequencies"] = []
        config["power_supply_assignment"]["dut_amplifier"]["supplies"]["carrier"][
            "supply_id"
        ] = "DP8B9999"
        page = PowerConfigurationPage(config)
        page.show()
        self.app.processEvents()
        failures = []
        page.validation_failed.connect(failures.append)

        allowed = page.validate()

        self.assertFalse(allowed)
        joined = "；".join(failures)
        self.assertIn("未选择电源", joined)
        self.assertNotIn("必须是非空列表", joined)
        self.assertNotIn("测试频率", joined)

    # ---- dirty -------------------------------------------------------------

    def test_editing_a_channel_marks_the_page_dirty(self):
        page = self.make_page()
        self.assertFalse(page.is_dirty())

        page.power_config_widgets["DP8B1519"]["channels"]["CH1"]["voltage"].setValue(5.0)

        self.assertTrue(page.is_dirty())
        page.mark_clean()
        self.assertFalse(page.is_dirty())

    def test_every_channel_parameter_edit_notifies_the_shared_session(self):
        """电压/电流及保护值都算电源片段变化，统一会话才能汇总 dirty。"""
        page = self.make_page()
        changes = []
        page.fragment_changed.connect(lambda: changes.append(True))
        controls = page.power_config_widgets["DP8B1519"]["channels"]["CH1"]

        for name, widget, value in (
            ("voltage", controls["voltage"], 5.0),
            ("voltage_protection", controls["voltage_protection"], 6.0),
            ("current", controls["current"], 0.7),
            ("current_protection", controls["current_protection"], 0.9),
        ):
            with self.subTest(name=name):
                before = len(changes)
                widget.setValue(value)
                self.assertEqual(len(changes), before + 1)

        before = len(changes)
        controls["current_protection_enabled"].setChecked(True)
        self.assertEqual(len(changes), before + 1)

    def test_backfill_does_not_report_a_user_edit(self):
        page = self.make_page()
        changes = []
        page.fragment_changed.connect(lambda: changes.append(True))

        page.apply_config(valid_config())

        self.assertEqual(changes, [])
        self.assertFalse(page.is_dirty())

    def test_driver_mode_switch_keeps_unsaved_power_changes_dirty(self):
        """模式切换只更新派生状态；干净基线由共享会话在加载/保存时重置。"""
        page = self.make_page()
        page.power_config_widgets["DP8B1519"]["channels"]["CH1"]["voltage"].setValue(5.0)
        self.assertTrue(page.is_dirty())

        page.set_test_mode(True)
        page.set_test_mode(False)

        self.assertTrue(page.is_dirty(), "切换模式不能把未保存的电源修改变成已保存")


if __name__ == "__main__":
    unittest.main()
