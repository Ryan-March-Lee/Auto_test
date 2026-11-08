"""步骤 12：独立电源配置页的纯 UI 测试。

覆盖独立创建、驱动模式派生、通道参数/保护开关/上电计划/漏极回填、片段提交与
校验消息。页面只消费配置文档与设备绑定，不启动完整测试配置页或真实后端。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from presentation.qt.config_projection import ConfigBackfillError
from presentation.qt.power_card import CHANNEL_NAMES, DEFAULT_POWER_ON_DELAY_S
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

    def test_the_card_view_is_the_main_area_and_has_no_form_below_it(self):
        """页面以通道卡片为主：卡片下方不再有「效率测量」表单。"""
        page = self.make_page()

        self.assertIsNotNone(page.card_wall)
        self.assertEqual(
            [cell.supply_id for cell in page.card_wall.cards()], ["DP8B1519"]
        )
        self.assertFalse(page.power_section.isVisibleTo(page))
        self.assertFalse(hasattr(page, "drain_channel_combo"))
        self.assertFalse(hasattr(page, "drain_channel_group"))

    def test_the_page_has_a_centered_power_configuration_title(self):
        """整屏步骤页：顶部是“电源配置”大标题，不是标签页。"""
        page = self.make_page()

        self.assertEqual(page.title_label.text(), "电源配置")
        self.assertEqual(page.title_label.property("role"), "pageTitle")
        self.assertAlmostEqual(
            page.title_label.mapTo(page, QPoint(0, 0)).x()
            + page.title_label.width() / 2.0,
            page.rect().center().x(),
            delta=2.0,
            msg="标题在页面里水平居中",
        )

    def test_footer_buttons_emit_navigation_signals(self):
        page = self.make_page()
        events: list[str] = []
        page.back_requested.connect(lambda: events.append("back"))
        page.next_requested.connect(lambda: events.append("next"))

        QTest.mouseClick(page.back_btn, Qt.LeftButton)
        QTest.mouseClick(page.next_btn, Qt.LeftButton)

        self.assertEqual(events, ["back", "next"])

    def test_card_morph_hides_then_reveals_the_title_and_form(self):
        """卡片展开期间标题/表单/导航先藏起来，落位后再次出现。"""
        page = self.make_page()
        start = QRect(40, 40, 170, 80)

        self.assertTrue(page.play_card_morph({"DP8B1519": start}))
        self.assertTrue(page.card_morph_running())
        self.assertFalse(page.intro_visible())
        self.assertFalse(page.next_btn.isEnabled())

        page.finish_card_morph()

        self.assertFalse(page.card_morph_running())
        self.assertTrue(page.intro_visible())
        self.assertTrue(page.next_btn.isEnabled())
        self.assertTrue(page.card_wall.paint_enabled())

    def _card(self, page):
        return page.card_wall.supply_card("DP8B1519")

    def _channel_controls(self, page, channel: str = "CH1"):
        return page.power_config_widgets["DP8B1519"]["channels"][channel]

    def test_channels_show_the_roles_saved_in_power_plans(self):
        page = self.make_page()
        card = self._card(page)

        for channel in CHANNEL_NAMES:
            with self.subTest(channel=channel):
                self.assertEqual(card.channel_mode(channel), "dut")
                self.assertTrue(card.channel_enabled(channel))
                self.assertTrue(
                    card.value_field(channel, "voltage").isEnabled()
                )
                self.assertTrue(
                    card.protection_check(channel, "voltage").isEnabled()
                )

    def test_first_enter_ignores_saved_power_channels_and_plans(self):
        """每次选择测试模式后的第一次进入：角色/工作值/保护/间隔全回到初始值。"""
        config = valid_config()
        channel = config["instruments"]["power_supplies"]["DP8B1519"][
            "channels"
        ]["CH1"]
        channel["voltage"]["protection_enabled"] = True
        channel["current"]["protection_enabled"] = True
        channel["power_on_delay_s"] = 5.0
        page = PowerConfigurationPage(config)
        page.resize(1100, 900)
        page.show()
        self.app.processEvents()
        self.addCleanup(self._destroy_page, page)

        page.enter()

        card = self._card(page)
        for channel in CHANNEL_NAMES:
            with self.subTest(channel=channel):
                self.assertEqual(card.channel_mode(channel), "off")
                self.assertFalse(card.channel_enabled(channel))
                self.assertEqual(
                    card.value_field(channel, "voltage").value(), 0.0
                )
                self.assertEqual(
                    card.value_field(channel, "current").value(), 0.0
                )
                self.assertFalse(
                    card.protection_check(channel, "voltage").isChecked()
                )
                self.assertFalse(
                    card.protection_check(channel, "current").isChecked()
                )
                self.assertAlmostEqual(
                    card.channel_power_delay(channel),
                    DEFAULT_POWER_ON_DELAY_S,
                    msg="未设置的通道间隔统一回到 2s",
                )
        self.assertEqual(page.read_drain_channels(), [])
        assignment = page.build_config()["power_supply_assignment"]
        for group in ("dut_amplifier", "driver_amplifier"):
            with self.subTest(group=group):
                self.assertEqual(
                    assignment[group]["power_plan"]["power_sequence"], []
                )

    def test_returning_to_the_page_keeps_in_session_edits(self):
        """本次会话内回排布页再进来：已填的角色/电压不被清空。"""
        page = self.make_page()
        page.enter()
        card = self._card(page)
        selector = card.channel_mode_selector("CH1")

        QTest.mouseClick(selector, Qt.LeftButton, pos=selector.rect().center())
        card.value_field("CH1", "voltage").setValue(3.3)

        page.enter()

        self.assertEqual(card.channel_mode("CH1"), "dut")
        self.assertAlmostEqual(card.value_field("CH1", "voltage").value(), 3.3)

    def test_selecting_the_test_mode_again_restarts_from_defaults(self):
        """重新选择测试模式等于重新载入配置，下一次进入必须再清空。"""
        page = self.make_page()
        page.enter()
        card = self._card(page)
        selector = card.channel_mode_selector("CH1")
        QTest.mouseClick(selector, Qt.LeftButton, pos=selector.rect().center())
        self.assertEqual(card.channel_mode("CH1"), "dut")

        page.set_test_mode(True)
        page.enter()

        self.assertEqual(card.channel_mode("CH1"), "off")
        self.assertAlmostEqual(card.value_field("CH1", "voltage").value(), 0.0)
        self.assertAlmostEqual(
            card.channel_power_delay("CH1"), DEFAULT_POWER_ON_DELAY_S
        )

    def test_clicking_the_reel_updates_both_plans_and_marks_dirty(self):
        page = self.make_page()
        card = self._card(page)
        changes: list[bool] = []
        page.fragment_changed.connect(lambda: changes.append(True))
        selector = card.channel_mode_selector("CH1")
        self.assertEqual(card.channel_mode("CH1"), "dut")

        # DUT → OFF：两份计划里都不再出现 CH1。
        QTest.mouseClick(selector, Qt.LeftButton, pos=selector.rect().center())

        self.assertEqual(card.channel_mode("CH1"), "off")
        assignment = page.build_config()["power_supply_assignment"]
        for group in ("dut_amplifier", "driver_amplifier"):
            routes = {
                (item["supply_id"], item["channel"])
                for item in assignment[group]["power_plan"]["power_sequence"]
            }
            self.assertNotIn(("DP8B1519", "CH1"), routes)
        self.assertEqual(changes, [True], "换档写计划，必须计入 dirty")
        self.assertTrue(page.is_dirty())

    def test_reel_cycle_skips_driver_when_driver_mode_is_off(self):
        page = self.make_page()
        card = self._card(page)
        selector = card.channel_mode_selector("CH2")

        # 当前 DUT；Driver 不可用，循环只走 DUT → OFF → DUT。
        QTest.mouseClick(selector, Qt.LeftButton, pos=selector.rect().center())
        self.assertEqual(card.channel_mode("CH2"), "off")
        QTest.mouseClick(selector, Qt.LeftButton, pos=selector.rect().center())
        self.assertEqual(card.channel_mode("CH2"), "dut")

    def test_selecting_driver_writes_the_driver_plan(self):
        config = valid_config()
        config["driver_mode"] = {"enabled": True}
        config["power_supply_assignment"]["dut_amplifier"]["power_plan"] = plan()
        page = PowerConfigurationPage(config)
        page.resize(1100, 900)
        page.show()
        self.app.processEvents()
        self.addCleanup(self._destroy_page, page)
        card = self._card(page)
        selector = card.channel_mode_selector("CH1")
        self.assertEqual(card.channel_mode("CH1"), "off")

        # 点一下先到 DUT，再点一下才到 Driver（循环是 OFF → DUT → Driver）。
        QTest.mouseClick(selector, Qt.LeftButton, pos=selector.rect().center())
        self.assertEqual(card.channel_mode("CH1"), "dut")
        QTest.mouseClick(selector, Qt.LeftButton, pos=selector.rect().center())

        self.assertEqual(card.channel_mode("CH1"), "driver")
        assignment = page.build_config()["power_supply_assignment"]
        driver_routes = {
            (item["supply_id"], item["channel"])
            for item in assignment["driver_amplifier"]["power_plan"][
                "power_sequence"
            ]
        }
        dut_routes = {
            (item["supply_id"], item["channel"])
            for item in assignment["dut_amplifier"]["power_plan"]["power_sequence"]
        }
        self.assertIn(("DP8B1519", "CH1"), driver_routes)
        self.assertNotIn(("DP8B1519", "CH1"), dut_routes)

    def test_cards_show_the_plan_order_and_default_delay(self):
        page = self.make_page()
        card = self._card(page)

        self.assertEqual(card.channel_power_order("CH1"), 1)
        self.assertEqual(card.channel_power_order("CH2"), 2)
        self.assertEqual(card.order_combo("CH1").count(), 2)
        self.assertAlmostEqual(
            card.channel_power_delay("CH1"),
            DEFAULT_POWER_ON_DELAY_S,
            msg="组默认间隔不再参与回落，未单独设置的通道统一 2s",
        )

    def test_selecting_order_on_the_card_reorders_the_plan(self):
        page = self.make_page()
        card = self._card(page)
        changes: list[bool] = []
        page.fragment_changed.connect(lambda: changes.append(True))

        card.order_combo("CH1").setCurrentIndex(1)

        plan_document = page.build_config()["power_supply_assignment"][
            "dut_amplifier"
        ]["power_plan"]
        self.assertEqual(
            [
                (item["supply_id"], item["channel"])
                for item in plan_document["power_sequence"]
            ],
            [("DP8B1519", "CH2"), ("DP8B1519", "CH1")],
        )
        self.assertEqual(card.channel_power_order("CH1"), 2)
        self.assertEqual(card.channel_power_order("CH2"), 1)
        self.assertEqual(changes, [True])
        self.assertTrue(page.is_dirty())

    def test_moving_an_order_up_on_the_card_reorders_the_plan(self):
        page = self.make_page()
        card = self._card(page)

        card.order_combo("CH2").setCurrentIndex(0)

        plan_document = page.build_config()["power_supply_assignment"][
            "dut_amplifier"
        ]["power_plan"]
        self.assertEqual(
            [
                (item["supply_id"], item["channel"])
                for item in plan_document["power_sequence"]
            ],
            [("DP8B1519", "CH2"), ("DP8B1519", "CH1")],
        )
        self.assertEqual(card.channel_power_order("CH2"), 1)

    def test_editing_the_delay_on_the_card_writes_the_channel_config(self):
        page = self.make_page()
        card = self._card(page)
        changes: list[bool] = []
        page.fragment_changed.connect(lambda: changes.append(True))

        card.delay_field("CH1").setValue(3.5)

        channel = page.build_config()["instruments"]["power_supplies"][
            "DP8B1519"
        ]["channels"]["CH1"]
        self.assertEqual(channel["power_on_delay_s"], 3.5)
        self.assertEqual(changes, [True])
        self.assertTrue(page.is_dirty())

    def test_delay_edit_survives_a_plan_reorder(self):
        page = self.make_page()
        card = self._card(page)

        card.delay_field("CH1").setValue(3.5)
        card.order_combo("CH1").setCurrentIndex(1)

        self.assertAlmostEqual(card.channel_power_delay("CH1"), 3.5)
        channel = page.build_config()["instruments"]["power_supplies"][
            "DP8B1519"
        ]["channels"]["CH1"]
        self.assertEqual(channel["power_on_delay_s"], 3.5)

    def test_edited_channel_delay_does_not_leak_to_the_other_channel(self):
        page = self.make_page()
        card = self._card(page)

        card.delay_field("CH1").setValue(5.0)

        self.assertAlmostEqual(card.channel_power_delay("CH1"), 5.0)
        self.assertAlmostEqual(
            card.channel_power_delay("CH2"), DEFAULT_POWER_ON_DELAY_S
        )

    def test_an_off_channel_has_the_order_and_delay_disabled(self):
        config = valid_config()
        config["power_supply_assignment"]["dut_amplifier"]["power_plan"] = plan(
            ("DP8B1519", "CH2"), delay=1.0
        )
        page = PowerConfigurationPage(config)
        page.resize(1100, 900)
        page.show()
        self.app.processEvents()
        self.addCleanup(self._destroy_page, page)

        card = self._card(page)
        self.assertEqual(card.channel_mode("CH1"), "off")
        self.assertFalse(card.order_combo("CH1").isEnabled())
        self.assertFalse(card.delay_field("CH1").isEnabled())
        self.assertIsNone(card.channel_power_order("CH1"))
        self.assertAlmostEqual(
            card.channel_power_delay("CH1"), DEFAULT_POWER_ON_DELAY_S
        )

    def test_apply_config_backfills_an_explicit_delay(self):
        config = valid_config()
        config["instruments"]["power_supplies"]["DP8B1519"]["channels"]["CH1"][
            "power_on_delay_s"
        ] = 4.0
        page = PowerConfigurationPage(config)
        page.resize(1100, 900)
        page.show()
        self.app.processEvents()
        self.addCleanup(self._destroy_page, page)

        self.assertAlmostEqual(self._card(page).channel_power_delay("CH1"), 4.0)

    def test_checking_a_plan_row_updates_the_card_role(self):
        page = self.make_page()
        card = self._card(page)
        # 先把 CH1 从 DUT 计划里移除，让卡片回到 OFF。
        page.power_section.dut_power_plan_editor.set_route_checked(
            ("DP8B1519", "CH1"), False
        )
        self.app.processEvents()
        self.assertEqual(card.channel_mode("CH1"), "off")

        page.power_section.dut_power_plan_editor.set_route_checked(
            ("DP8B1519", "CH1"), True
        )
        self.app.processEvents()

        self.assertEqual(card.channel_mode("CH1"), "dut")

    def test_checking_a_route_in_one_plan_unchecks_the_other(self):
        config = valid_config()
        config["driver_mode"] = {"enabled": True}
        page = PowerConfigurationPage(config)
        page.resize(1100, 900)
        page.show()
        self.app.processEvents()
        self.addCleanup(self._destroy_page, page)
        card = self._card(page)

        page.power_section.driver_power_plan_editor.set_route_checked(
            ("DP8B1519", "CH1"), True
        )
        self.app.processEvents()

        self.assertEqual(card.channel_mode("CH1"), "driver")
        dut_routes = {
            (item["supply_id"], item["channel"])
            for item in page.power_section.dut_power_plan_editor.read_plan()[
                "power_sequence"
            ]
        }
        self.assertNotIn(("DP8B1519", "CH1"), dut_routes)

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
        card.set_channel_mode("CH1", "dut")
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

    def test_calibration_marks_multiple_channels_and_sets_drain_routes(self):
        page = self.make_page()
        config = valid_config()
        config["dut_config"].pop("drain_channel", None)
        page.apply_config(config)
        card = self._card(page)
        events: list[tuple[str, str, bool]] = []
        page.efficiency_calibration_changed.connect(
            lambda *args: events.append(args)
        )
        changes: list[bool] = []
        page.fragment_changed.connect(lambda: changes.append(True))

        for channel in CHANNEL_NAMES:
            toggle = card.calibration_toggle(channel)
            QTest.mouseClick(toggle, Qt.LeftButton, pos=toggle.rect().center())

        self.assertEqual(
            events,
            [
                ("DP8B1519", "CH1", True),
                ("DP8B1519", "CH2", True),
            ],
        )
        self.assertEqual(
            page.read_efficiency_calibration_channels(),
            (("DP8B1519", "CH1"), ("DP8B1519", "CH2")),
        )
        self.assertEqual(
            page.read_drain_channels(),
            [
                {"supply_id": "DP8B1519", "channel": "CH1"},
                {"supply_id": "DP8B1519", "channel": "CH2"},
            ],
            "多选 Cal. 完整写进漏极集合",
        )
        self.assertEqual(changes, [True, True], "每次 Cal. 标记变化都写配置并计入 dirty")
        self.assertTrue(page.is_dirty())
        self.assertEqual(
            page.build_fragment()["dut_config"]["drain_channel"],
            [
                {"supply_id": "DP8B1519", "channel": "CH1"},
                {"supply_id": "DP8B1519", "channel": "CH2"},
            ],
            "多条漏极按路由列表写出，运行阶段求和",
        )

    def test_a_multi_drain_document_marks_every_cal_channel_and_round_trips(self):
        page = self.make_page()
        config = valid_config()
        config["dut_config"]["drain_channel"] = [
            {"supply_id": "DP8B1519", "channel": "CH1"},
            {"supply_id": "DP8B1519", "channel": "CH2"},
        ]

        page.apply_config(config)

        card = self._card(page)
        self.assertTrue(card.channel_efficiency_calibration("CH1"))
        self.assertTrue(card.channel_efficiency_calibration("CH2"))
        self.assertEqual(
            page.read_drain_channels(),
            [
                {"supply_id": "DP8B1519", "channel": "CH1"},
                {"supply_id": "DP8B1519", "channel": "CH2"},
            ],
        )
        self.assertEqual(
            page.build_fragment()["dut_config"]["drain_channel"],
            config["dut_config"]["drain_channel"],
        )

    def test_calibration_survives_a_card_wall_rebuild(self):
        page = self.make_page()
        card = self._card(page)
        toggle = card.calibration_toggle("CH1")
        QTest.mouseClick(toggle, Qt.LeftButton, pos=toggle.rect().center())

        page.set_rack_slots({"DP8B1519": 4})
        page.apply_device_bindings(power_supply_bindings("DP8B1519"))

        rebuilt = self._card(page)
        self.assertTrue(
            rebuilt.channel_efficiency_calibration("CH1"),
            "格位/绑定重建保留尚未提交的 Cal. 标记",
        )
        self.assertTrue(
            rebuilt.channel_efficiency_calibration("CH2"),
            "配置里已有漏极对应的标记也保留",
        )
        self.assertEqual(
            page.read_efficiency_calibration_channels(),
            (("DP8B1519", "CH1"), ("DP8B1519", "CH2")),
        )
        self.assertEqual(
            page.read_drain_channels(),
            [
                {"supply_id": "DP8B1519", "channel": "CH1"},
                {"supply_id": "DP8B1519", "channel": "CH2"},
            ],
            "重建后漏极集合与 Cal. 标记保持一致",
        )

    def test_calibration_resets_with_the_rest_of_the_power_page(self):
        page = self.make_page()
        card = self._card(page)
        toggle = card.calibration_toggle("CH1")
        QTest.mouseClick(toggle, Qt.LeftButton, pos=toggle.rect().center())
        self.assertTrue(card.channel_efficiency_calibration("CH1"))

        page.set_test_mode(True)
        page.enter()

        self.assertEqual(page.read_efficiency_calibration_channels(), ())

    def test_editing_a_card_value_writes_the_config_and_marks_dirty(self):
        page = self.make_page()
        card = self._card(page)
        card.set_channel_mode("CH1", "dut")

        card.value_field("CH1", "voltage").setValue(7.5)

        channel = page.build_config()["instruments"]["power_supplies"]["DP8B1519"][
            "channels"
        ]["CH1"]
        self.assertEqual(channel["voltage"]["value"], 7.5)
        self.assertEqual(channel["voltage"]["protection"], 8.25)
        self.assertTrue(page.is_dirty())

    def test_roles_survive_leaving_and_reentering_the_page(self):
        page = self.make_page()
        card = self._card(page)
        selector = card.channel_mode_selector("CH1")
        # DUT → OFF：真实点击会同时把 CH1 从计划里移除。
        QTest.mouseClick(selector, Qt.LeftButton, pos=selector.rect().center())
        self.assertEqual(card.channel_mode("CH1"), "off")

        page.hide()
        page.show()
        self.app.processEvents()

        self.assertEqual(self._card(page).channel_mode("CH1"), "off")
        self.assertEqual(self._card(page).channel_mode("CH2"), "dut")

    def test_read_only_or_morphing_page_does_not_toggle_from_the_card(self):
        page = self.make_page()
        card = self._card(page)
        card.set_channel_mode("CH1", "dut")
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
        """绑定只在上游“测试配置”页确认，电源页不再显示绑定状态框。"""
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

    def test_first_enter_keeps_newly_bound_supplies(self):
        """首次进入重置电源字段时不能把刚扫描到的绑定退回旧配置。

        回归：``enter`` 曾经用页面里尚未更新的配置副本重建绑定，排布了三台电源，
        进入电源页却只剩保存配置里的那一台。
        """
        page = self.make_page()
        page.apply_device_bindings(
            power_supply_bindings("DP8B1519", "DP8B0885", "DP8B1234")
        )

        page.enter()
        page.set_rack_slots(
            {"DP8B1519": 0, "DP8B0885": 1, "DP8B1234": 2}
        )

        self.assertEqual(
            [cell.supply_id for cell in page.card_wall.cards()],
            ["DP8B1519", "DP8B0885", "DP8B1234"],
        )
        fragment = page.build_fragment()
        self.assertEqual(
            set(fragment["instruments"]["power_supplies"]),
            {"DP8B1519", "DP8B0885", "DP8B1234"},
            "提交片段也必须包含本次会话新绑定的电源",
        )

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
        self.assertTrue(page.driver_power_plan_editor.is_active)
        self.assertNotIn("power_source", page.build_config()["driver_mode"])

        page.set_test_mode(False)
        self.assertFalse(page.driver_power_plan_editor.is_active)

    def test_driver_mode_emits_change_signal(self):
        page = self.make_page()
        changes = []
        page.driver_mode_changed.connect(changes.append)

        page.set_test_mode(False)
        self.assertEqual(changes[-1], False)
        page.set_test_mode(True)
        self.assertEqual(changes[-1], True)

    def test_no_driver_mode_writes_an_empty_driver_plan(self):
        page = self.make_page(driver_mode={"enabled": True})

        page.set_test_mode(False)

        self.assertFalse(hasattr(page, "driver_power_plan_box"))
        self.assertEqual(
            page.driver_power_plan_editor.read_active_plan()["power_sequence"],
            [],
        )

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
            plan(("DP8B1519", "CH1"), ("DP8B1519", "CH2")),
            "组默认间隔不再参与编辑，计划读回为 0.0",
        )
        self.assertEqual(
            page.read_drain_channels(),
            [{"supply_id": "DP8B1519", "channel": "CH2"}],
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
        expected_drain = page.read_drain_channels()

        page.apply_device_bindings(power_supply_bindings("DP8B1519", "DP8B0885"))

        self.assertEqual(
            page.power_config_widgets["DP8B0885"]["channels"]["CH1"]["voltage"].value(),
            12.5,
        )
        self.assertEqual(page.dut_power_plan_editor.read_plan(), expected_plan)
        self.assertEqual(page.read_drain_channels(), expected_drain)

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
            page.read_drain_channels(),
            [{"supply_id": "DP8B1519", "channel": "CH2"}],
        )

    def test_an_invalid_existing_drain_is_preserved_for_validation(self):
        page = self.make_page(
            dut_config={
                "power_supply_count": 1,
                "drain_channel": {"supply_id": "DP8B9999", "channel": "CH1"},
            }
        )

        self.assertEqual(
            page.read_drain_channels(),
            [{"supply_id": "DP8B9999", "channel": "CH1"}],
            "不在当前电源集合里的漏极不能被静默清空",
        )

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
        """正式校验按页面归属拆分：测试参数的问题由测试配置页报告。"""
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
