"""扫描生命周期：点“查找设备”之后结果怎么来，失败、关闭与预选怎么收尾。

面板把结果渲染成卡片的样子见 ``test_panel_presentation.py``，把勾选写进配置见
``test_binding_writes.py``；这里只看扫描本身：开始、结束、失败、取消，以及没有设备
网络的开发机上扫描器必须默认走模拟数据。
"""

import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from domain.instruments.models import InstrumentIdentity
from infrastructure.discovery.instrument_discovery import (
    SCAN_MODE_SIMULATED,
    InstrumentDiscoveryError,
    build_scanner,
)
from infrastructure.persistence.instrument_registry import InstrumentRegistry
from presentation.qt.device_discovery_panel import BOX_STATE_IDLE

from .support import SMW_IDN, DiscoveryPanelTestCase, registry


class DiscoveryPanelTests(DiscoveryPanelTestCase):
    """点“查找设备”之后的可见结果、失败收尾与按配置预选。"""

    def test_scan_shows_one_card_per_device(self):
        panel = self.make_panel()

        self.run_scan(panel)

        self.assertEqual(panel.card_count(), 4)
        self.assertEqual(len(panel.cards()), 4)
        names = set(panel.card_headings())
        # 卡片正面只写设备名：角色在型号旁边，未登记的设备显示兜底名。
        self.assertIn("SMW200A-1", names)
        # 电源还没有短标签时退回资产名，但仍然是类别“电源”，不是“电源 PS3”这种槽位编号。
        self.assertIn("DP832A-1", names)
        self.assertIn("未知设备 (N9020A)", names)
        self.assertTrue(panel.apply_btn.isEnabled())
        text = panel.status_label.text()
        self.assertIn("已登记 3 台", text)
        self.assertIn("其中可应用 3 台", text)
        self.assertIn("未登记 1 台", text)

    def test_cards_do_not_show_addresses(self):
        """IP 只是本次可达的地址，不是用户需要关心的信息：卡片正面不展示。"""
        panel = self.make_panel()

        self.run_scan(panel)

        for card in panel.cards():
            for label in (card.name_label, card.code_label):
                self.assertNotIn("192.168.1.", label.text())

    def test_unknown_device_card_keeps_its_identity_in_the_code_and_tooltip(self):
        panel = self.make_panel()

        self.run_scan(panel)

        heading = "未知设备 (N9020A)"
        card = panel.card_for(heading)
        # 缩减版设备码：角色缩写 / 型号 / 序列号；第一行只写仪器名（型号）。
        self.assertEqual(card.code_label.text(), "NA/N9020A/MY51234567")
        self.assertEqual(card.name_label.text(), "N9020A")
        tip = panel.card_tooltip(heading)
        self.assertIn("N9020A / Keysight Technologies", tip)
        self.assertIn("MY51234567", tip)
        self.assertIn("192.168.1.150", tip)
        # 未登记设备没有登记角色，不能选中应用。
        self.assertFalse(card.checkable)
        self.assertEqual(panel.card_status(heading), "未登记 · 不能应用")

    def test_registered_device_without_role_cannot_be_selected(self):
        """设备表没登记角色的设备：能看到一张卡片，但不能选中，也不能被应用。"""
        def factory(_mode):
            registry = InstrumentRegistry()
            registry.upsert(InstrumentIdentity.from_idn(SMW_IDN), role=None)
            return build_scanner(
                registry=registry,
                mode=SCAN_MODE_SIMULATED,
                responses={("192.168.1.201", 5025): SMW_IDN},
                timeout_s=0.02,
            )

        panel = self.make_panel(factory)
        self.run_scan(panel)

        self.assertEqual(panel.card_count(), 1)
        card = panel.card_for("SMW200A-1")
        self.assertEqual(card.code_label.text(), "NA/SMW200A/105462")
        self.assertEqual(panel.card_status("SMW200A-1"), "未分配角色 · 不能应用")
        self.assertFalse(card.checkable)
        panel.set_card_checked("SMW200A-1", True)
        self.assertFalse(panel.card_checked("SMW200A-1"))
        self.assertFalse(panel.apply_btn.isEnabled())
        self.assertIn("未分配角色 1 台", panel.status_label.text())

    def test_power_supply_without_a_label_says_why_the_asset_name_is_shown(self):
        """旧设备表还没有短标签：卡片显示资产名，提示里说明原因。

        这台电源有旧槽位，所以不该同时出现“地址无处可写”的过渡提示——两件事分开写，
        用户才知道该做什么。
        """
        panel = self.make_panel()

        self.run_scan(panel)

        tip = panel.card_tooltip("DP832A-1")
        self.assertIn("短标签", tip)
        self.assertIn("资产名", tip)
        self.assertNotIn("没有旧槽位", tip)

    def test_device_card_carries_picture_name_and_device_code(self):
        panel = self.make_panel()

        self.run_scan(panel)

        card = panel.card_for("SMW200A-1")
        self.assertTrue(card.image.has_picture(), "信号源卡片应显示仪器图片")
        # 第一行只写仪器名（型号），不再带资产名里的 ``-1``。
        self.assertEqual(card.name_label.text(), "SMW200A")
        self.assertEqual(card.code_label.text(), "SG/SMW200A/105462")

    def test_selecting_a_card_is_enough_without_choosing_a_role(self):
        """角色来自设备表：点击卡片即选中并应用，不会出现“未选择测试角色”。"""
        panel = self.make_panel()
        self.run_scan(panel)
        applied = []
        panel.apply_requested.connect(applied.append)

        panel.set_card_checked("SMW200A-1", True)
        panel.request_apply()

        self.assertEqual(len(applied), 1)
        self.assertEqual(applied[0][0]["role"], "signal_generator")

    def test_clicking_a_card_toggles_its_selection(self):
        panel = self.make_panel()
        self.run_scan(panel)
        card = panel.card_for("4082F-1")

        QTest.mouseClick(card, Qt.LeftButton)
        self.app.processEvents()
        self.assertTrue(card.is_checked())
        self.assertEqual(
            [item.is_checked() for item in panel.cards()],
            [False, False, True, False],
        )

        QTest.mouseClick(card, Qt.LeftButton)
        self.app.processEvents()
        self.assertFalse(card.is_checked())

    def test_clicking_a_card_that_cannot_be_applied_does_nothing(self):
        panel = self.make_panel()
        self.run_scan(panel)
        card = panel.card_for("未知设备 (N9020A)")

        QTest.mouseClick(card, Qt.LeftButton)
        self.app.processEvents()

        self.assertFalse(card.is_checked())

    def test_clicking_the_picture_or_the_text_also_selects_the_card(self):
        """卡片上的文字和图片不吞点击：点到名字、图片或设备码同样算选中。"""
        panel = self.make_panel()
        self.run_scan(panel)
        card = panel.card_for("4082F-1")

        for target in (card.name_label, card.image, card.code_label):
            with self.subTest(target=type(target).__name__):
                card.set_checked(False, emit=False)
                QTest.mouseClick(target, Qt.LeftButton)
                self.app.processEvents()
                self.assertTrue(card.is_checked())

    def test_duplicate_roles_are_rejected(self):
        """两台设备在设备表里登记成同一角色时整批拒绝，而不是静默覆盖。"""
        second_smw = "Rohde&Schwarz,SMW200A,1412.0000K02/200001,5.20.043.47"

        def factory(_mode):
            registry = InstrumentRegistry()
            registry.upsert(InstrumentIdentity.from_idn(SMW_IDN), role="signal_generator")
            registry.upsert(InstrumentIdentity.from_idn(second_smw), role="signal_generator")
            return build_scanner(
                registry=registry,
                mode=SCAN_MODE_SIMULATED,
                responses={
                    ("192.168.1.201", 5025): SMW_IDN,
                    ("192.168.1.203", 5025): second_smw,
                },
                timeout_s=0.02,
            )

        panel = self.make_panel(factory)
        self.run_scan(panel)
        for heading in panel.card_headings():
            panel.set_card_checked(heading, True)
        applied = []
        panel.apply_requested.connect(applied.append)

        panel.request_apply()

        self.assertEqual(applied, [])
        self.assertIn("重复选择", panel.status_label.text())

    def test_apply_reports_selected_role_and_identity(self):
        panel = self.make_panel()
        self.run_scan(panel)
        applied = []
        panel.apply_requested.connect(applied.append)

        panel.set_card_checked("SMW200A-1", True)
        panel.request_apply()

        assignment = applied[0][0]
        self.assertEqual(assignment["role"], "signal_generator")
        self.assertEqual(assignment["serial"], "105462")
        self.assertEqual(assignment["address"], "TCPIP0::192.168.1.201::inst0::INSTR")
        self.assertTrue(assignment["known"])
        # 信号源没有现场短标签：``label`` / ``supply_id`` 为空，配置页不会拿它当电源引用。
        self.assertIsNone(assignment["label"])
        self.assertIsNone(assignment["supply_id"])

    def test_power_supply_without_a_label_cannot_be_applied(self):
        """旧设备表还没有短标签：电源没有可写的配置引用，面板明确拒绝。"""
        panel = self.make_panel()
        self.run_scan(panel)
        applied = []
        panel.apply_requested.connect(applied.append)

        panel.set_card_checked("DP832A-1", True)
        panel.request_apply()

        self.assertEqual(applied, [])
        self.assertIn("短标签", panel.status_label.text())

    def test_invalid_subnet_reports_failure_without_crashing(self):
        panel = self.make_panel()
        panel.subnet_edit.setText("not-a-subnet")

        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()

        self.assertIn("扫描失败", panel.status_label.text())
        self.assertEqual(panel.card_count(), 0)
        self.assertFalse(panel.apply_btn.isEnabled())

    def test_shutdown_without_running_scan_is_ok(self):
        panel = self.make_panel()
        self.assertTrue(panel.shutdown())

    def test_scan_preselects_devices_bound_in_configuration(self):
        panel = self.make_panel()
        panel.set_device_bindings(
            {"signal_generator": {"serial": "105462", "device_key": "rohde&schwarz:105462"}}
        )

        self.run_scan(panel)

        self.assertTrue(panel.card_checked("SMW200A-1"))
        self.assertIn("预选", panel.status_label.text())
        self.assertIn("已按配置预选", panel.card_status("SMW200A-1"))
        # 其他卡片不因预选被选上：用户仍可自由改。
        self.assertFalse(panel.card_checked("DP832A-1"))

    def test_a_card_unchecked_by_the_user_is_not_preselected_again(self):
        """用户取消勾选后，刷新卡片不能把配置里的预选重新加回来。"""
        panel = self.make_panel()
        panel.set_device_bindings(
            {"signal_generator": {"serial": "105462", "device_key": "rohde&schwarz:105462"}}
        )
        self.run_scan(panel)
        self.assertTrue(panel.card_checked("SMW200A-1"))

        panel.set_card_checked("SMW200A-1", False)

        self.assertFalse(panel.card_checked("SMW200A-1"))

    def test_preselection_matches_by_device_key_even_when_ip_changes(self):
        panel = self.make_panel()
        panel.set_device_bindings(
            {"spectrum_analyzer": {"device_key": "ceyear technologies:qzni000866"}}
        )
        # 换一个网段再扫描，设备 IP 变化但身份不变。
        panel.subnet_edit.setText("192.168.1.0/24")

        self.run_scan(panel)

        self.assertTrue(panel.card_checked("4082F-1"))
        self.assertNotIn("配置里是", panel.status_label.text())

    def test_preselection_reports_a_role_change_instead_of_silently_moving_the_device(self):
        """配置里的旧角色与设备表不一致时说明清楚：角色以设备表为准。"""
        panel = self.make_panel()
        panel.set_device_bindings(
            {"spectrum_analyzer": {"device_key": "rohde&schwarz:105462"}}
        )

        self.run_scan(panel)

        self.assertTrue(panel.card_checked("SMW200A-1"))
        text = panel.status_label.text()
        self.assertIn("角色以设备表为准", text)
        self.assertIn("配置里是 频谱分析仪", text)

    def test_legacy_slot_binding_preselects_a_power_supply_without_a_role_change(self):
        """旧绑定按 ``PS1`` 落键时不算“角色变了”：记录里的短标签折叠成同一台电源。"""
        panel = self.make_panel()
        panel.set_device_bindings(
            {
                "PS1": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "serial": "DP8B214901519",
                    "label": "DP8B1519",
                    "supply_id": "DP8B1519",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                }
            }
        )

        self.run_scan(panel)

        self.assertTrue(panel.card_checked("DP832A-1"))
        self.assertIn("预选", panel.status_label.text())
        self.assertNotIn("配置里是", panel.status_label.text())

    def test_preselection_uses_vendor_and_serial_when_key_is_missing(self):
        """旧配置只有厂商+序列号时重新拼出同一个身份键，仍然认得出设备。"""
        panel = self.make_panel()
        panel.set_device_bindings(
            {"signal_generator": {"vendor": "Rohde&Schwarz", "serial": "105462"}}
        )

        self.run_scan(panel)

        self.assertTrue(panel.card_checked("SMW200A-1"))

    def test_preselection_never_matches_on_serial_alone(self):
        """只有序列号时不做猜测：不同厂商可能有相同序列号。"""
        panel = self.make_panel()
        panel.set_device_bindings({"signal_generator": {"serial": "105462"}})

        self.run_scan(panel)

        for card in panel.cards():
            self.assertFalse(card.is_checked())
        self.assertNotIn("预选", panel.status_label.text())

    def test_unidentified_address_is_listed_but_cannot_be_applied(self):
        def factory(_mode):
            return build_scanner(
                registry=registry(),
                mode=SCAN_MODE_SIMULATED,
                responses={("192.168.1.150", 5025): "GARBAGE"},
                timeout_s=0.02,
            )

        panel = self.make_panel(factory)
        self.run_scan(panel)

        self.assertEqual(panel.card_count(), 1)
        self.assertEqual(panel.card_status("未识别设备 #1"), "无法识别")
        card = panel.card_for("未识别设备 #1")
        self.assertEqual(card.code_label.text(), "未识别 #1")
        self.assertFalse(card.checkable)
        self.assertIn("无法识别 1 个地址", panel.status_label.text())

    def test_scan_failure_restores_the_button_and_shows_the_reason(self):
        def factory(_mode):
            raise InstrumentDiscoveryError("设备表损坏")

        panel = self.make_panel(factory)

        self.run_scan(panel)

        self.assertIn("无法开始扫描", panel.status_label.text())
        self.assertIn("设备表损坏", panel.status_label.text())
        self.assertTrue(panel.scan_btn.isEnabled())
        # 失败后结果区里仍然没有结果：中央的“查找设备”回来。
        self.assertTrue(panel.scan_btn.isVisible())
        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)
        self.assertFalse(panel.apply_btn.isEnabled())

    def test_shutdown_during_scan_cancels_and_leaves_no_worker(self):
        panel = self.make_panel()
        panel.request_scan()

        self.assertTrue(panel.shutdown())

        self.assertIsNone(panel.worker)
        self.assertFalse(panel.is_scanning())


class DiscoveryEnabledOfflineDefaultsTests(unittest.TestCase):
    """没有设备网络的开发机上，默认扫描器必须是模拟数据，绝不偷偷发真实连接。"""

    def test_composition_factory_simulates_by_default_mode(self):
        from presentation.qt.composition import build_device_scanner_factory

        with patch(
            "infrastructure.discovery.instrument_discovery.TcpIdnProbe.query_idn",
            side_effect=AssertionError("模拟模式不得打开真实连接"),
        ):
            scanner = build_device_scanner_factory()(SCAN_MODE_SIMULATED)
            result = scanner.scan("192.168.1.200/30")

        # /30 的两个可用地址都有演示响应：SMW200A 与 4082F。
        self.assertEqual(len(result.devices), 2)
