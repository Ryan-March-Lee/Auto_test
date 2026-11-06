"""步骤 12：独立电源配置页的纯 UI 测试。

覆盖独立创建、驱动模式派生、通道参数/保护开关/上电计划/漏极回填、片段提交与
校验消息。页面只消费配置文档与设备绑定，不启动完整参数页或真实后端。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from presentation.qt.config_projection import ConfigBackfillError
from presentation.qt.power_card import CHANNEL_NAMES
from presentation.qt.power_configuration_page import PowerConfigurationPage


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
                "power_plan": plan(),
            },
            "dut_amplifier": {
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
        page.resize(1100, 900)
        page.show()
        self.app.processEvents()
        self.addCleanup(self._destroy_page, page)
        return page

    def _destroy_page(self, page) -> None:
        page.close()
        page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    # ---- 独立创建与绑定状态 ------------------------------------------------

    def test_the_page_is_created_without_the_parameter_page(self):
        page = self.make_page()

        self.assertIsInstance(page, PowerConfigurationPage)
        self.assertIsNotNone(page.power_section)
        self.assertFalse(hasattr(page, "power_tabs"), "电源详细配置标签页已删除")
        self.assertEqual(
            [cell.supply_id for cell in page.card_wall.cards()], ["DP8B1519"]
        )

    def test_the_card_view_is_the_main_area_above_the_form(self):
        """页面以通道卡片为主：卡片在内容列顶部，上电计划接在下面。"""
        page = self.make_page()

        self.assertIsNotNone(page.card_wall)
        self.assertEqual(
            [cell.supply_id for cell in page.card_wall.cards()], ["DP8B1519"]
        )
        self.assertLess(
            page.card_wall.geometry().top(),
            page.power_section.geometry().top(),
            "卡片在表单上方",
        )

    def _card(self, page):
        return page.card_wall.supply_card("DP8B1519")

    def _channel_controls(self, page, channel: str = "CH1"):
        return page.power_config_widgets["DP8B1519"]["channels"][channel]

    def test_all_channels_start_disabled_on_page_entry(self):
        page = self.make_page()
        card = self._card(page)

        for channel in CHANNEL_NAMES:
            with self.subTest(channel=channel):
                self.assertFalse(card.channel_enabled(channel))
                self.assertFalse(
                    card.value_field(channel, "voltage").isEnabled()
                )
                self.assertFalse(
                    card.protection_check(channel, "voltage").isEnabled()
                )

    def test_clicking_the_power_switch_enables_editing_without_dirtying(self):
        page = self.make_page()
        card = self._card(page)
        changes: list[bool] = []
        page.fragment_changed.connect(lambda: changes.append(True))
        switch = card.channel_switch("CH1")

        QTest.mouseClick(switch, Qt.LeftButton, pos=switch.rect().center())

        self.assertTrue(card.channel_enabled("CH1"))
        self.assertTrue(card.value_field("CH1", "voltage").isEnabled())
        self.assertEqual(changes, [], "启用状态不落配置，不算编辑")
        self.assertFalse(page.is_dirty())

    def test_card_wall_protection_checks_read_the_configuration(self):
        config = valid_config()
        channel = config["instruments"]["power_supplies"]["DP8B1519"]["channels"][
            "CH1"
        ]
        channel["voltage"]["protection_enabled"] = True
        channel["current"]["protection_enabled"] = False
        page = PowerConfigurationPage(config)
        page.resize(1100, 900)
        page.show()
        self.app.processEvents()
        self.addCleanup(self._destroy_page, page)

        data = page.card_wall.cards()[0].data
        self.assertTrue(data.channel_protection("CH1", "voltage"))
        self.assertFalse(data.channel_protection("CH1", "current"))

    def test_clicking_a_card_protection_check_updates_the_config_and_dirty(self):
        page = self.make_page()
        card = self._card(page)
        card.set_channel_enabled("CH1", True)
        changes: list[bool] = []
        page.fragment_changed.connect(lambda: changes.append(True))

        check = card.protection_check("CH1", "voltage")
        QTest.mouseClick(check, Qt.LeftButton, pos=check.rect().center())

        self.assertTrue(card.channel_protection("CH1", "voltage"))
        self.assertTrue(
            page.build_config()["instruments"]["power_supplies"]["DP8B1519"][
                "channels"
            ]["CH1"]["voltage"]["protection_enabled"]
        )
        self.assertEqual(changes, [True], "卡面勾选要计入 dirty")

    def test_editing_a_card_value_writes_the_config_and_marks_dirty(self):
        page = self.make_page()
        card = self._card(page)
        card.set_channel_enabled("CH1", True)

        card.value_field("CH1", "voltage").setValue(7.5)

        channel = page.build_config()["instruments"]["power_supplies"]["DP8B1519"][
            "channels"
        ]["CH1"]
        self.assertEqual(channel["voltage"]["value"], 7.5)
        self.assertEqual(channel["voltage"]["protection"], 8.25)
        self.assertTrue(page.is_dirty())

    def test_leaving_and_reentering_the_page_resets_the_channel_switches(self):
        page = self.make_page()
        card = self._card(page)
        card.set_channel_enabled("CH1", True)

        page.hide()
        page.show()
        self.app.processEvents()

        self.assertFalse(self._card(page).channel_enabled("CH1"))

    def test_read_only_or_morphing_page_does_not_toggle_from_the_card(self):
        page = self.make_page()
        card = self._card(page)
        card.set_channel_enabled("CH1", True)
        check = card.protection_check("CH1", "voltage")

        page.set_read_only("1.0 配置只能查看")
        self.assertFalse(page.card_wall.isEnabled())
        QTest.mouseClick(check, Qt.LeftButton, pos=check.rect().center())
        self.assertFalse(card.protection_check("CH1", "voltage").isChecked())

        page.set_read_only(None)
        self.assertTrue(page.card_wall.isEnabled())
        start = page.mapToGlobal(QPoint(240, 420))
        page.play_card_morph(
            {"DP8B1519": QRect(start.x(), start.y(), 170, 80)}
        )
        self.assertFalse(page.card_wall.isEnabled(), "过渡中不接受卡面点击")
        QTest.mouseClick(check, Qt.LeftButton, pos=check.rect().center())
        self.assertFalse(card.protection_check("CH1", "voltage").isChecked())
        page.finish_card_morph()
        self.assertTrue(page.card_wall.isEnabled())

    def test_rack_slots_decide_the_cards_relative_positions(self):
        """排布页的九宫格格位决定卡片在页面上的相对位置。"""
        page = self.make_page()
        page.apply_device_bindings(power_supply_bindings("DP8B1519", "DP8B0885"))
        page.set_rack_slots({"DP8B1519": 0, "DP8B0885": 4})

        first = page.card_wall.card_rect("DP8B1519")
        second = page.card_wall.card_rect("DP8B0885")
        self.assertLess(first.x(), second.x())
        self.assertLess(first.y(), second.y())

    def test_the_card_morph_runs_between_the_rack_tiles_and_the_wall(self):
        page = self.make_page()
        start = page.mapToGlobal(QPoint(240, 420))
        starts = {"DP8B1519": QRect(start.x(), start.y(), 170, 80)}

        self.assertTrue(page.play_card_morph(starts))
        self.assertTrue(page.card_morph_running())
        overlay = page._card_morph
        self.assertEqual(
            [item.supply_id for item in overlay.items()], ["DP8B1519"]
        )

        page.finish_card_morph()

        self.assertFalse(page.card_morph_running())
        self.assertTrue(page.card_wall.paint_enabled(), "交接后卡片墙恢复自绘")

    def test_a_read_only_document_disables_the_power_form_and_shows_the_reason(self):
        page = self.make_page()

        page.set_read_only("1.0 配置只能查看，不能保存；请先运行迁移器升级到 1.1")

        self.assertTrue(page.read_only_notice.isVisibleTo(page))
        self.assertIn("迁移器", page.read_only_notice.text())
        self.assertFalse(page.power_section.isEnabled())

        page.set_read_only(None)
        self.assertFalse(page.read_only_notice.isVisibleTo(page))
        self.assertTrue(page.power_section.isEnabled())

    def test_the_page_has_no_device_binding_box(self):
        """绑定只在上游“仪器配置”页确认，电源页不再显示绑定状态框。"""
        page = self.make_page()

        self.assertFalse(hasattr(page, "binding_group"))
        self.assertFalse(hasattr(page, "binding_status_label"))

    def test_applying_bindings_refreshes_the_power_options(self):
        page = self.make_page()
        page.apply_device_bindings(power_supply_bindings("DP8B1519", "DP8B0885"))

        self.assertEqual(
            page.dut_power_plan_editor.routes(),
            (
                ("DP8B1519", "CH1"),
                ("DP8B1519", "CH2"),
                ("DP8B0885", "CH1"),
                ("DP8B0885", "CH2"),
            ),
            "计划候选是每台已绑定电源声明（或默认）的全部通道",
        )
        self.assertEqual(len(page.card_wall.cards()), 2)

    def test_cards_show_each_supply_code_and_identity_tooltip(self):
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

        self.assertEqual(
            [cell.data.code for cell in page.card_wall.cards()],
            ["DP8B1519", "DP8B0885"],
        )
        self.assertEqual(set(page.power_config_widgets), {"DP8B1519", "DP8B0885"})
        self.assertIn("DP832A", page.card_wall.supply_card("DP8B1519").toolTip())

    # ---- 驱动模式 -----------------------------------------------------------

    def test_driver_power_plan_is_active_whenever_driver_mode_is_on(self):
        page = self.make_page(
            driver_mode={"enabled": True},
            power_supply_assignment={
                "driver_amplifier": {
                    "power_plan": plan(("DP8B1519", "CH1")),
                },
                "dut_amplifier": {
                    "power_plan": plan(("DP8B1519", "CH2")),
                },
            },
        )
        page.set_test_mode(True)
        self.assertTrue(page.driver_power_plan_editor.isEnabled())
        self.assertNotIn("power_source", page.build_config()["driver_mode"])

        page.set_test_mode(False)
        self.assertFalse(page.driver_power_plan_editor.isEnabled())

    def test_driver_mode_emits_change_signal(self):
        page = self.make_page()
        changes = []
        page.driver_mode_changed.connect(changes.append)

        page.set_test_mode(False)
        self.assertEqual(changes[-1], False)
        page.set_test_mode(True)
        self.assertEqual(changes[-1], True)

    def test_no_driver_mode_hides_the_driver_plan_box(self):
        page = self.make_page(driver_mode={"enabled": True})

        page.set_test_mode(False)

        self.assertFalse(page.driver_power_plan_box.isVisible())

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
            "power_supply_assignment": {
                "dut_amplifier": {"power_plan": plan()}
            },
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
                    "power_plan": plan(),
                },
                "dut_amplifier": {
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

    # ---- 计划候选 ----------------------------------------------------------

    def test_candidates_are_all_channels_of_the_bound_supplies(self):
        page = self.make_page(
            device_bindings=power_supply_bindings("DP8B1519", "DP8B0885"),
            dut_config={"power_supply_count": 2},
        )

        self.assertEqual(
            page.dut_power_plan_editor.routes(),
            (
                ("DP8B1519", "CH1"),
                ("DP8B1519", "CH2"),
                ("DP8B0885", "CH1"),
                ("DP8B0885", "CH2"),
            ),
        )
        self.assertEqual(
            page.driver_power_plan_editor.routes(),
            page.dut_power_plan_editor.routes(),
            "两个编辑器使用同一份候选，器件归属由勾在哪一组决定",
        )

    def test_one_supply_may_power_driver_and_dut_on_different_channels(self):
        """同一台电源 CH1 供驱放、CH2 供 DUT：计划已经表达清楚，不需要固定分配。"""
        page = self.make_page(
            device_bindings=power_supply_bindings("DP8B1519"),
            driver_mode={"enabled": True},
            power_supply_assignment={
                "driver_amplifier": {
                    "power_plan": plan(("DP8B1519", "CH1")),
                },
                "dut_amplifier": {
                    "power_plan": plan(("DP8B1519", "CH2")),
                },
            },
            dut_config={
                "drain_channel": {"supply_id": "DP8B1519", "channel": "CH2"}
            },
        )

        written = page.build_config()["power_supply_assignment"]
        self.assertEqual(
            written["driver_amplifier"]["power_plan"]["power_sequence"],
            [{"supply_id": "DP8B1519", "channel": "CH1"}],
        )
        self.assertEqual(
            written["dut_amplifier"]["power_plan"]["power_sequence"],
            [{"supply_id": "DP8B1519", "channel": "CH2"}],
        )
        self.assertTrue(page.validate())

    def test_a_plan_route_from_an_unbound_supply_stays_visible(self):
        """引用已失效的电源不能从计划里静默消失，交给正式校验报告。"""
        page = self.make_page(
            device_bindings=power_supply_bindings("DP8B0885"),
            power_supply_assignment={
                "dut_amplifier": {"power_plan": plan(("DP8B1519", "CH1"))}
            },
            dut_config={"power_supply_count": 1},
        )

        self.assertIn(("DP8B1519", "CH1"), page.dut_power_plan_editor.routes())
        self.assertEqual(
            page.build_config()["power_supply_assignment"]["dut_amplifier"][
                "power_plan"
            ]["power_sequence"],
            [{"supply_id": "DP8B1519", "channel": "CH1"}],
        )

    def test_newly_bound_supply_channels_become_candidates(self):
        page = self.make_page(
            device_bindings=power_supply_bindings("DP8B1519", "DP8B0885"),
            dut_config={"power_supply_count": 1},
        )

        self.assertEqual(
            page.dut_power_plan_editor.routes(),
            (
                ("DP8B1519", "CH1"),
                ("DP8B1519", "CH2"),
                ("DP8B0885", "CH1"),
                ("DP8B0885", "CH2"),
            ),
        )

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
        config["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ][0]["supply_id"] = "DP8B9999"
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
        config["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ][0]["supply_id"] = "DP8B9999"
        page = PowerConfigurationPage(config)
        page.show()
        self.app.processEvents()
        failures = []
        page.validation_failed.connect(failures.append)

        allowed = page.validate()

        self.assertFalse(allowed)
        joined = "；".join(failures)
        self.assertIn("上电顺序引用了不存在的电源", joined)
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
        """电压/电流与两个保护开关都算电源片段变化，统一会话才能汇总 dirty。"""
        page = self.make_page()
        changes = []
        page.fragment_changed.connect(lambda: changes.append(True))
        controls = page.power_config_widgets["DP8B1519"]["channels"]["CH1"]

        for name, widget, value in (
            ("voltage", controls["voltage"], 5.0),
            ("current", controls["current"], 0.7),
        ):
            with self.subTest(name=name):
                before = len(changes)
                widget.setValue(value)
                self.assertEqual(len(changes), before + 1)

        for key in ("voltage_protection_enabled", "current_protection_enabled"):
            with self.subTest(name=key):
                before = len(changes)
                controls[key].setChecked(not controls[key].isChecked())
                self.assertEqual(len(changes), before + 1)

    def test_protection_threshold_is_derived_from_the_work_value(self):
        """用户只勾选是否启用保护，阈值自动取设定值的 110%。"""
        page = self.make_page()
        controls = page.power_config_widgets["DP8B1519"]["channels"]["CH1"]
        controls["voltage"].setValue(10.0)
        controls["current"].setValue(1.0)
        controls["voltage_protection_enabled"].setChecked(True)
        controls["current_protection_enabled"].setChecked(False)

        channel = page.build_config()["instruments"]["power_supplies"][
            "DP8B1519"
        ]["channels"]["CH1"]

        self.assertEqual(channel["voltage"]["protection"], 11.0)
        self.assertTrue(channel["voltage"]["protection_enabled"])
        self.assertEqual(channel["current"]["protection"], 1.1)
        self.assertFalse(channel["current"]["protection_enabled"])

    def test_channel_controls_have_no_protection_value_boxes(self):
        page = self.make_page()
        controls = page.power_config_widgets["DP8B1519"]["channels"]["CH1"]

        self.assertNotIn("voltage_protection", controls)
        self.assertNotIn("current_protection", controls)

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
