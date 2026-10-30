"""配置页设备发现面板（MVP）界面测试。

覆盖点击“获取设备”后的可见行为：

1. 扫描结果以卡片出现在面板里：一台设备一张圆角卡片，上方仪器图片，下方设备名、型号与
   角色徽标、缩减版设备码（``角色/型号/序列号``）。未登记设备标记为未登记，身份读不出来的
   地址标记为无法识别且不能选中；IP 地址不在卡片正面显示，只在悬浮提示里。
2. 测试角色来自设备表登记的 ``role``：点击卡片即选中，不需要用户选择角色。电源的旧槽位
   （``PS3``）折叠成 ``power_supply``，结果里带 ``label`` / ``supply_id`` / ``serial`` /
   ``device_key``，多台电源各自独立、互不覆盖。
3. 角色重复（仅信号源/频谱仪）、空扫描网段、身份缺失、设备未登记角色、电源缺少短标签、
   同一台设备被重复绑定都给出明确提示，不静默写入配置。
4. 电源绑定结构：``device_bindings.power_supplies[<supply_id>]``，每条记录带
   ``device_key``；一次应用多台电源得到多条独立绑定，清除后地址与启用状态一起失效。
"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from domain.instruments.models import DeviceStatus, InstrumentIdentity
from infrastructure.discovery.instrument_discovery import (
    SCAN_MODE_NETWORK,
    SCAN_MODE_SIMULATED,
    DiscoveredDevice,
    InstrumentDiscoveryError,
    ScanResult,
    build_scanner,
)
from infrastructure.discovery.simulated_network import demo_idn_responses
from infrastructure.persistence.instrument_registry import InstrumentRegistry
from presentation.qt.config_page import ConfigPage
from presentation.qt.device_cards import device_code, instrument_picture_path
from presentation.qt.device_discovery_panel import (
    DeviceDiscoveryPanel,
    binding_device_key,
    card_heading,
    card_model,
    card_status_text,
    device_heading,
    subnet_from_address,
)
from presentation.qt.theme import apply_theme

SMW_IDN = "Rohde&Schwarz,SMW200A,1412.0000K02/105462,5.20.043.47"
DP832A_IDN = "RIGOL TECHNOLOGIES,DP832A,DP8B214901519,00.01.16"
DP832A_SECOND_IDN = "RIGOL TECHNOLOGIES,DP832A,DP8B174400885,00.01.16"
CEYEAR_IDN = "Ceyear Technologies,4082F,QZNI000866,1.12.04"

# 设备表里登记的固定角色：界面不再让用户选择，发现结果直接按它显示与写入。
DEVICE_ROLES = {
    SMW_IDN: "signal_generator",
    DP832A_IDN: "PS3",
    CEYEAR_IDN: "spectrum_analyzer",
}

# 迁移到 schema 3 之后的电源记录：类别是 ``power_supply``，短标签已冻结，没有旧槽位。
MIGRATED_POWER_SUPPLIES = (
    {
        "name": "DP832A-1",
        "vendor": "RIGOL TECHNOLOGIES",
        "model": "DP832A",
        "model_index": 1,
        "serial": "DP8B214901519",
        "label": "DP8B1519",
        "role": "power_supply",
        "status": "online",
        "known_ips": [],
    },
    {
        "name": "DP832A-3",
        "vendor": "RIGOL TECHNOLOGIES",
        "model": "DP832A",
        "model_index": 3,
        "serial": "DP8B174400885",
        "label": "DP8B0885",
        "role": "power_supply",
        "status": "online",
        "known_ips": [],
    },
)


def _registry(roles=None):
    registry = InstrumentRegistry()
    for idn in (SMW_IDN, DP832A_IDN, CEYEAR_IDN):
        registry.upsert(InstrumentIdentity.from_idn(idn), role=(roles or DEVICE_ROLES)[idn])
    return registry


def _migrated_registry() -> InstrumentRegistry:
    """迁移后的设备表：两台电源都是 ``power_supply`` 带短标签，没有旧槽位。"""
    return InstrumentRegistry(
        document={"schema_version": 3, "instruments": [dict(item) for item in MIGRATED_POWER_SUPPLIES]}
    )


def _scanner_factory(mode):
    if mode == SCAN_MODE_NETWORK:
        raise AssertionError("测试不得发起真实网络扫描")
    return build_scanner(
        registry=_registry(),
        mode=SCAN_MODE_SIMULATED,
        responses=demo_idn_responses(),
        timeout_s=0.02,
    )


def _two_supply_factory(_mode):
    """两台电源都已登记为 ``power_supply`` 且带短标签（迁移后的形态）。"""
    return build_scanner(
        registry=_migrated_registry(),
        mode=SCAN_MODE_SIMULATED,
        responses={
            ("192.168.1.102", 5555): DP832A_IDN,
            ("192.168.1.108", 5555): DP832A_SECOND_IDN,
        },
        timeout_s=0.02,
    )


class SubnetSuggestionTests(unittest.TestCase):
    def test_visa_address_suggests_its_24_bit_subnet(self):
        self.assertEqual(
            subnet_from_address("TCPIP0::192.168.1.201::inst0::INSTR"), "192.168.1.0/24"
        )

    def test_missing_or_invalid_address_falls_back_to_none(self):
        for value in ("", None, "ASRL1::INSTR", 42, "TCPIP0::999.1.1.1::INSTR"):
            with self.subTest(value=value):
                self.assertIsNone(subnet_from_address(value))


class BindingKeyTests(unittest.TestCase):
    """身份键只由厂商 + 序列号决定；只有序列号时不做猜测。"""

    def test_device_key_is_used_when_present(self):
        self.assertEqual(
            binding_device_key({"device_key": "Rohde&Schwarz:105462"}),
            "rohde&schwarz:105462",
        )

    def test_vendor_and_serial_are_combined_when_key_is_missing(self):
        self.assertEqual(
            binding_device_key({"vendor": "Rohde&Schwarz", "serial": "105462"}),
            "rohde&schwarz:105462",
        )

    def test_serial_alone_or_malformed_input_yields_none(self):
        for record in ({"serial": "105462"}, {}, None, "not-a-record", {"device_key": "  "}):
            with self.subTest(record=record):
                self.assertIsNone(binding_device_key(record))


class DiscoveryPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self):
        panel = DeviceDiscoveryPanel(scanner_factory=_scanner_factory)
        panel.show()
        self.app.processEvents()
        self.addCleanup(panel.shutdown)
        return panel

    def run_scan(self, panel):
        panel.request_scan()
        self.assertTrue(panel.worker is not None)
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        self.assertIsNone(panel.worker)

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
            for label in (card.title_label, card.meta_label, card.code_label):
                self.assertNotIn("192.168.1.", label.text())

    def test_unknown_device_card_keeps_its_identity_in_the_code_and_tooltip(self):
        panel = self.make_panel()

        self.run_scan(panel)

        heading = "未知设备 (N9020A)"
        card = panel.card_for(heading)
        # 缩减版设备码：角色缩写 / 型号 / 序列号。
        self.assertEqual(card.code_label.text(), "NA/N9020A/MY51234567")
        self.assertEqual(card.meta_label.text(), "N9020A")
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

        panel = DeviceDiscoveryPanel(scanner_factory=factory)
        panel.show()
        self.app.processEvents()
        self.addCleanup(panel.shutdown)
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
        self.assertEqual(card.title_label.text(), "SMW200A-1")
        self.assertEqual(card.meta_label.text(), "SMW200A · 信号发生器")
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

        for target in (card.title_label, card.image, card.code_label):
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

        panel = DeviceDiscoveryPanel(scanner_factory=factory)
        panel.show()
        self.app.processEvents()
        self.addCleanup(panel.shutdown)
        self.run_scan(panel)
        for card in panel.cards():
            panel.set_card_checked(card.title_label.text(), True)
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
                registry=_registry(),
                mode=SCAN_MODE_SIMULATED,
                responses={("192.168.1.150", 5025): "GARBAGE"},
                timeout_s=0.02,
            )

        panel = DeviceDiscoveryPanel(scanner_factory=factory)
        panel.show()
        self.app.processEvents()
        self.addCleanup(panel.shutdown)
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

        panel = DeviceDiscoveryPanel(scanner_factory=factory)
        panel.show()
        self.app.processEvents()
        self.addCleanup(panel.shutdown)

        self.run_scan(panel)

        self.assertIn("无法开始扫描", panel.status_label.text())
        self.assertIn("设备表损坏", panel.status_label.text())
        self.assertTrue(panel.scan_btn.isEnabled())
        self.assertFalse(panel.apply_btn.isEnabled())

    def test_shutdown_during_scan_cancels_and_leaves_no_worker(self):
        panel = self.make_panel()
        panel.request_scan()

        self.assertTrue(panel.shutdown())

        self.assertIsNone(panel.worker)
        self.assertFalse(panel.is_scanning())


class PowerSupplyDiscoveryTests(unittest.TestCase):
    """多台电源：卡片显示短标签、各自独立选中与传递，身份仍只按 ``device_key`` 匹配。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self, factory=None, bindings=None):
        panel = DeviceDiscoveryPanel(scanner_factory=factory or _two_supply_factory)
        if bindings is not None:
            panel.set_device_bindings(bindings)
        panel.show()
        self.app.processEvents()
        self.addCleanup(panel.shutdown)
        return panel

    def run_scan(self, panel):
        panel.request_scan()
        self.assertTrue(panel.worker is not None)
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        self.assertIsNone(panel.worker)

    def test_two_supplies_are_shown_with_their_frozen_short_labels(self):
        panel = self.make_panel()

        self.run_scan(panel)

        self.assertEqual(set(panel.card_headings()), {"DP8B1519", "DP8B0885"})
        for card in panel.cards():
            self.assertTrue(card.checkable)
            self.assertEqual(card.meta_label.text(), "DP832A · 电源")
            self.assertTrue(card.code_label.text().startswith("PS/DP832A/"))
        self.assertTrue(panel.apply_btn.isEnabled())

    def test_power_supply_card_with_a_label_has_no_extra_status(self):
        """迁移后的设备表只说明类别与短标签：卡片不再提示“没有旧槽位”。"""
        panel = self.make_panel()

        self.run_scan(panel)

        self.assertEqual(panel.card_status("DP8B1519"), "")
        self.assertNotIn("没有短标签", panel.card_tooltip("DP8B0885"))

    def test_two_supplies_are_applied_independently_without_a_duplicate_role_error(self):
        panel = self.make_panel()
        self.run_scan(panel)
        for card in panel.cards():
            panel.set_card_checked(card.title_label.text(), True)
        applied = []
        panel.apply_requested.connect(applied.append)

        panel.request_apply()

        self.assertEqual(len(applied), 1, "两台电源要在同一批里一起交付，不是分两次")
        assignments = applied[0]
        self.assertEqual(len(assignments), 2)
        self.assertEqual({item["role"] for item in assignments}, {"power_supply"})
        self.assertEqual(
            {item["supply_id"] for item in assignments}, {"DP8B1519", "DP8B0885"}
        )
        self.assertEqual(
            {item["device_key"] for item in assignments},
            {
                "rigol technologies:dp8b214901519",
                "rigol technologies:dp8b174400885",
            },
        )
        self.assertNotIn("重复选择", panel.status_label.text())

    def test_power_supply_hands_over_its_supply_id(self):
        """迁移后的设备表：面板交付短标签与身份，配置页按 ``supply_id`` 落地址。"""
        panel = self.make_panel()
        self.run_scan(panel)
        panel.set_card_checked(panel.cards()[0].title_label.text(), True)
        applied = []
        panel.apply_requested.connect(applied.append)

        panel.request_apply()

        self.assertEqual(len(applied), 1)
        assignment = applied[0][0]
        self.assertNotIn("config_slot", assignment)
        self.assertIn(assignment["supply_id"], {"DP8B1519", "DP8B0885"})

    def test_two_supplies_are_preselected_by_identity_after_an_ip_change(self):
        panel = self.make_panel(
            bindings={
                "power_supplies": {
                    "DP8B0885": {
                        "device_key": "rigol technologies:dp8b174400885",
                        "visa_address": "TCPIP0::192.168.9.108::inst0::INSTR",
                    }
                }
            }
        )
        # 换一个网段再扫描：地址变了，身份（序列号）没变。
        panel.subnet_edit.setText("192.168.1.0/24")

        self.run_scan(panel)

        self.assertTrue(panel.card_checked("DP8B0885"))
        self.assertFalse(panel.card_checked("DP8B1519"))
        self.assertIn("预选 1 台", panel.status_label.text())


class CardPresentationTests(unittest.TestCase):
    """卡片外观：圆角、图片在上、名字与缩减设备码在下，并随面板宽度换行。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._previous_stylesheet = self.app.styleSheet()
        apply_theme(self.app)

    def tearDown(self):
        self.app.setStyleSheet(self._previous_stylesheet)

    def scan(self, panel):
        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()
        self.addCleanup(panel.shutdown)

    def test_cards_are_rounded_and_ordered_picture_name_code(self):
        panel = DeviceDiscoveryPanel(scanner_factory=_scanner_factory)
        panel.resize(760, 560)
        panel.show()
        self.app.processEvents()
        self.scan(panel)

        card = panel.card_for("SMW200A-1")
        self.assertEqual(card.objectName(), "instrumentCard")
        self.assertIn("border-radius: 12px;", self.app.styleSheet())
        # 自上而下：图片区在设备名之上，设备码在设备名之下。
        layout = card.layout()
        rows = [layout.itemAt(index).widget() for index in range(layout.count())]
        self.assertIs(rows[0], card.image)
        self.assertLess(rows.index(card.image), rows.index(card.title_label))
        self.assertGreater(rows.index(card.code_label), rows.index(card.title_label))
        for element in panel.cards():
            self.assertEqual(element.width(), card.width(), "卡片同宽同列")
            self.assertEqual(element.height(), card.height(), "同一行卡片高度一致")

    def test_cards_wrap_into_columns_instead_of_a_long_strip(self):
        panel = DeviceDiscoveryPanel(scanner_factory=_scanner_factory)
        panel.resize(520, 700)
        panel.show()
        self.app.processEvents()
        self.scan(panel)

        narrow = len({card.geometry().left() for card in panel.cards()})
        panel.resize(1200, 700)
        self.app.processEvents()
        self.app.processEvents()
        wide = len({card.geometry().left() for card in panel.cards()})

        # 卡片按可用宽度换行：窄面板换行排两列，宽面板排满四列，不排成一条长带子。
        self.assertGreaterEqual(narrow, 2, "窄面板要换行，不能挤成一列长条")
        self.assertEqual(wide, min(4, panel.card_count()))

    def test_selected_card_has_the_checked_property(self):
        panel = DeviceDiscoveryPanel(scanner_factory=_scanner_factory)
        panel.show()
        self.app.processEvents()
        self.scan(panel)
        card = panel.card_for("4082F-1")

        self.assertFalse(card.property("checked"))
        QTest.mouseClick(card, Qt.LeftButton)
        self.app.processEvents()
        self.assertTrue(card.property("checked"))

    def test_cards_leave_room_while_there_are_no_results(self):
        panel = DeviceDiscoveryPanel(scanner_factory=_scanner_factory)
        panel.show()
        self.app.processEvents()

        self.assertEqual(panel.card_count(), 0)
        self.assertGreater(panel.cards_area.height(), 0)

    def test_results_stay_collapsed_until_a_scan_starts(self):
        panel = DeviceDiscoveryPanel(scanner_factory=_scanner_factory)
        panel.show()
        self.app.processEvents()

        self.assertFalse(panel.is_open())
        self.assertFalse(panel.cards_area.isVisible())
        self.assertTrue(panel.result_hint.isVisible())
        self.assertFalse(panel.apply_btn.isVisible())
        self.assertFalse(panel.reopen_btn.isVisible())

    def test_collapsing_keeps_the_results_reachable_through_reopen(self):
        panel = DeviceDiscoveryPanel(scanner_factory=_scanner_factory)
        panel.resize(1200, 760)
        panel.show()
        self.app.processEvents()
        self.scan(panel)

        self.assertTrue(panel.is_open())
        self.assertTrue(panel.cards_area.isVisible())
        self.assertFalse(panel.reopen_btn.isVisible())

        panel.set_open(False)
        self.app.processEvents()
        self.app.processEvents()

        self.assertFalse(panel.cards_area.isVisible())
        self.assertFalse(panel.apply_btn.isVisible())
        self.assertTrue(panel.reopen_btn.isVisible())
        self.assertIn("已发现 4 台设备", panel.result_hint.text())

        QTest.mouseClick(panel.reopen_btn, Qt.LeftButton)
        self.app.processEvents()
        self.app.processEvents()
        self.assertTrue(panel.cards_area.isVisible())
        self.assertEqual(panel.card_count(), 4)


class DeviceHeadingTests(unittest.TestCase):
    """结果表的设备描述：电源用短标签（型号，电源），其他角色沿用“设备名（角色）”。"""

    def test_power_supply_shows_short_label_model_and_category(self):
        device = DiscoveredDevice(
            ip="10.0.0.1",
            port=5555,
            idn=DP832A_IDN,
            identity=InstrumentIdentity.from_idn(DP832A_IDN),
            known_name="DP832A-1",
            status=DeviceStatus.ONLINE,
            known_role="power_supply",
            known_label="DP8B1519",
        )

        self.assertEqual(device_heading(device), "DP8B1519（DP832A，电源）")
        self.assertEqual(device.supply_id, "DP8B1519")
        self.assertFalse(hasattr(device, "config_slot"), "旧槽位已从发现结果里删除")

    def test_legacy_slot_is_folded_into_the_power_supply_category(self):
        device = DiscoveredDevice(
            ip="10.0.0.1",
            port=5555,
            idn=DP832A_IDN,
            identity=InstrumentIdentity.from_idn(DP832A_IDN),
            known_name="DP832A-1",
            status=DeviceStatus.ONLINE,
            known_role="PS3",
        )

        self.assertEqual(device.role, "power_supply")
        self.assertFalse(hasattr(device, "config_slot"))
        self.assertIsNone(device.supply_id, "没有标签就没有 supply_id，发现过程不生成标签")
        self.assertEqual(device_heading(device), "DP832A-1（DP832A，电源）")

    def test_other_roles_and_unknown_devices_keep_their_heading(self):
        signal = DiscoveredDevice(
            ip="10.0.0.2",
            port=5025,
            idn=SMW_IDN,
            identity=InstrumentIdentity.from_idn(SMW_IDN),
            known_name="SMW200A-1",
            status=DeviceStatus.ONLINE,
            known_role="signal_generator",
        )
        unknown = DiscoveredDevice(
            ip="10.0.0.3",
            port=5025,
            idn=CEYEAR_IDN,
            identity=InstrumentIdentity.from_idn(CEYEAR_IDN),
            known_name=None,
            status=DeviceStatus.UNKNOWN,
        )

        self.assertEqual(device_heading(signal), "SMW200A-1（信号发生器）")
        self.assertEqual(device_heading(unknown), "未知设备 (4082F)")


class DeviceCardContentTests(unittest.TestCase):
    """卡片上的文字：设备名、型号与缩减版设备码都由发现结果直接生成。"""

    @staticmethod
    def device(**overrides):
        values = {
            "ip": "10.0.0.2",
            "port": 5025,
            "idn": SMW_IDN,
            "identity": InstrumentIdentity.from_idn(SMW_IDN),
            "known_name": "SMW200A-1",
            "status": DeviceStatus.ONLINE,
            "known_role": "signal_generator",
        }
        values.update(overrides)
        return DiscoveredDevice(**values)

    def test_device_code_uses_role_model_and_serial(self):
        code, detail = device_code(self.device())

        self.assertEqual(code, "SG/SMW200A/105462")
        self.assertIn("SG/SMW200A/105462", detail)
        self.assertIn("TCPIP0::10.0.0.2::inst0::INSTR", detail)

    def test_power_supply_and_spectrum_analyzer_have_their_own_prefixes(self):
        supply = self.device(
            identity=InstrumentIdentity.from_idn(DP832A_IDN),
            idn=DP832A_IDN,
            known_role="power_supply",
            known_name="DP832A-1",
        )
        spectrum = self.device(
            identity=InstrumentIdentity.from_idn(CEYEAR_IDN),
            idn=CEYEAR_IDN,
            known_role="spectrum_analyzer",
            known_name="4082F-1",
        )

        self.assertEqual(device_code(supply)[0], "PS/DP832A/DP8B214901519")
        self.assertEqual(device_code(spectrum)[0], "SA/4082F/QZNI000866")

    def test_unregistered_device_falls_back_to_the_unknown_role_code(self):
        device = self.device(known_name=None, known_role=None)

        self.assertEqual(device_code(device)[0], "NA/SMW200A/105462")

    def test_unidentified_address_gets_a_visitor_number_instead_of_a_code(self):
        device = self.device(
            idn=None,
            identity=None,
            known_name=None,
            known_role=None,
            status=DeviceStatus.UNKNOWN,
            error="GARBAGE",
        )

        code, detail = device_code(device, visitor_index=3)

        self.assertEqual(code, "未识别 #3")
        self.assertIn("GARBAGE", detail)
        self.assertEqual(card_heading(device, visitor_index=3), "未识别设备 #3")

    def test_card_heading_and_model_follow_the_device(self):
        device = self.device(
            identity=InstrumentIdentity.from_idn(DP832A_IDN),
            idn=DP832A_IDN,
            known_role="power_supply",
            known_name="DP832A-1",
            known_label="DP8B1519",
        )

        self.assertEqual(card_heading(device), "DP8B1519")
        self.assertEqual(card_model(device), "DP832A")
        self.assertEqual(card_status_text(device), "")

    def test_cards_explain_why_a_device_cannot_be_used(self):
        unregistered = self.device(known_name=None, known_role=None)
        no_role = self.device(known_role=None)

        self.assertEqual(card_status_text(unregistered), "未登记 · 不能应用")
        self.assertEqual(card_status_text(no_role), "未分配角色 · 不能应用")

    def test_pictures_are_chosen_by_role_then_by_model(self):
        self.assertEqual(
            instrument_picture_path("signal_generator", "").name, "signal_source.png"
        )
        self.assertEqual(
            instrument_picture_path("spectrum_analyzer", "").name, "spectrum_analyzer.png"
        )
        self.assertEqual(
            instrument_picture_path("power_supply", "").name, "DC_source.png"
        )
        # 未登记角色的设备只能按型号猜图；两个条件都不满足时没有图片（卡片留空底板）。
        self.assertEqual(instrument_picture_path("", "N9020A"), None)
        self.assertEqual(instrument_picture_path("", "DP832A").name, "DC_source.png")


class ConfigPageDiscoveryIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_page(self):
        page = ConfigPage(
            {"test_frequencies": [1.0], "instruments": {"power_supplies": {}}},
            scanner_factory=_scanner_factory,
        )
        page.show()
        self.app.processEvents()
        return page

    def test_page_builds_the_discovery_panel(self):
        page = self.make_page()
        self.assertIsInstance(page.discovery_panel, DeviceDiscoveryPanel)
        self.assertTrue(hasattr(page, "apply_discovered_devices"))

    def test_the_panel_is_never_squeezed_below_its_card_grid(self):
        """卡片展开后面板必须拿到足够高度：否则卡片区被裁掉并与按钮重叠。"""
        page = self.make_page()
        panel = page.discovery_panel

        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()

        self.assertTrue(panel.is_open())
        self.assertGreaterEqual(panel.height(), panel.minimumSizeHint().height())
        self.assertGreaterEqual(
            panel.cards_area.height(),
            panel.cards_area.minimumHeight(),
        )

    def test_scan_subnet_defaults_to_the_bound_device_address(self):
        page = ConfigPage(
            {
                "test_frequencies": [1.0],
                "instruments": {
                    "power_supplies": {},
                    "signal_generator": {
                        "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                        "enabled": True,
                    },
                },
                "device_bindings": {
                    "signal_generator": {
                        "visa_address": "TCPIP0::10.2.3.201::inst0::INSTR",
                        "device_key": "rohde&schwarz:105462",
                    }
                },
            },
            scanner_factory=_scanner_factory,
        )
        page.show()
        self.app.processEvents()
        self.addCleanup(page.close)

        self.assertEqual(page.discovery_panel.subnet_edit.text(), "10.2.3.0/24")

    def test_connection_box_has_no_manual_address_widgets(self):
        """仪器连接只有“扫描后勾选”一套逻辑：不再逐台填写地址或勾选启用。"""
        page = self.make_page()

        self.assertEqual(page.discovery_panel.title(), "仪器连接配置")
        for widget in (
            "sg_address", "sg_enabled", "sa_address", "sa_enabled",
            "ps1_address", "ps1_enabled", "ps4_address", "ps4_enabled",
        ):
            self.assertFalse(hasattr(page, widget), f"不应再存在手工地址控件 {widget}")

    def test_applied_device_writes_address_and_identity_into_config(self):
        page = self.make_page()

        page.apply_discovered_devices(
            [
                {
                    "role": "spectrum_analyzer",
                    "address": "TCPIP0::192.168.1.202::inst0::INSTR",
                    "name": "4082F-1",
                    "vendor": "Ceyear Technologies",
                    "model": "4082F",
                    "serial": "QZNI000866",
                    "device_key": "ceyear technologies:qzni000866",
                    "ip": "192.168.1.202",
                    "port": 5025,
                    "known": True,
                }
            ]
        )

        config = page.build_config()
        self.assertEqual(
            config["instruments"]["spectrum_analyzer"]["address"],
            "TCPIP0::192.168.1.202::inst0::INSTR",
        )
        self.assertTrue(config["instruments"]["spectrum_analyzer"]["enabled"])
        binding = config["device_bindings"]["spectrum_analyzer"]
        self.assertEqual(binding["serial"], "QZNI000866")
        self.assertEqual(binding["device_key"], "ceyear technologies:qzni000866")
        self.assertEqual(binding["last_ip"], "192.168.1.202")
        self.assertEqual(binding["source"], "scan")
        # 当前配置直接显示在连接框里，不必先重新扫描。
        self.assertIn("4082F-1", page.discovery_panel.bindings_label.text())

    def test_simulated_device_binding_is_marked_as_demo_data(self):
        page = self.make_page()

        page.apply_discovered_devices(
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "serial": "105462",
                    "device_key": "rohde&schwarz:105462",
                    "ip": "192.168.1.201",
                    "port": 5025,
                    "known": True,
                    "simulated": True,
                }
            ]
        )

        self.assertEqual(page.device_bindings["signal_generator"]["source"], "simulated")

    def test_power_supply_is_written_under_its_supply_id(self):
        page = self.make_page()

        page.apply_discovered_devices(
            [
                {
                    "role": "power_supply",
                    "label": "DP8B1519",
                    "supply_id": "DP8B1519",
                    "address": "TCPIP0::192.168.1.150::inst0::INSTR",
                    "name": None,
                    "vendor": "Keysight Technologies",
                    "model": "N9020A",
                    "serial": "MY51234567",
                    "device_key": "keysight technologies:my51234567",
                    "ip": "192.168.1.150",
                    "port": 5025,
                    "known": False,
                }
            ]
        )

        supply = page.build_config()["instruments"]["power_supplies"]["DP8B1519"]
        self.assertEqual(supply["address"], "TCPIP0::192.168.1.150::inst0::INSTR")
        self.assertTrue(supply["enabled"])
        self.assertEqual(supply["label"], "DP8B1519")
        self.assertEqual(supply["device_key"], "keysight technologies:my51234567")
        # 绑定集合的键就是引用 ID，记录内保留身份（序列号/设备键）。
        binding = page.device_bindings["power_supplies"]["DP8B1519"]
        self.assertFalse(binding["known"])
        self.assertEqual(binding["serial"], "MY51234567")
        self.assertIn("电源 DP8B1519", page.discovery_panel.status_label.text())

    def test_single_power_supply_binding_uses_its_supply_id(self):
        """一台电源：绑定键是短标签，地址写进同名的配置条目。"""
        page = self.make_page()

        page.apply_discovered_devices(
            [
                {
                    "role": "power_supply",
                    "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                    "name": "DP832A-1",
                    "vendor": "RIGOL TECHNOLOGIES",
                    "model": "DP832A",
                    "serial": "DP8B214901519",
                    "device_key": "rigol technologies:dp8b214901519",
                    "ip": "192.168.1.102",
                    "port": 5555,
                    "known": True,
                    "supply_id": "DP8B1519",
                }
            ]
        )

        self.assertEqual(set(page.device_bindings), {"power_supplies"})
        binding = page.device_bindings["power_supplies"]["DP8B1519"]
        self.assertEqual(binding["serial"], "DP8B214901519")
        self.assertEqual(binding["device_key"], "rigol technologies:dp8b214901519")
        self.assertEqual(binding["visa_address"], "TCPIP0::192.168.1.102::inst0::INSTR")
        self.assertEqual(page.get_enabled_power_supplies(), ["DP8B1519"])
        self.assertIn("DP8B1519", page.power_config_widgets, "新绑定的电源要有自己的参数页")
        self.assertEqual(
            page.build_config()["instruments"]["power_supplies"]["DP8B1519"]["address"],
            "TCPIP0::192.168.1.102::inst0::INSTR",
        )

    def test_power_supply_binding_summary_shows_the_short_label(self):
        """“当前配置”按 supply_id 显示每台电源，记录里不再重复写类别与标签。"""
        page = self.make_page()

        page.apply_discovered_devices(
            [
                {
                    "role": "power_supply",
                    "label": "DP8B1519",
                    "supply_id": "DP8B1519",
                    "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                    "name": "DP832A-1",
                    "vendor": "RIGOL TECHNOLOGIES",
                    "model": "DP832A",
                    "serial": "DP8B214901519",
                    "device_key": "rigol technologies:dp8b214901519",
                    "ip": "192.168.1.102",
                    "port": 5555,
                    "known": True,
                }
            ]
        )

        binding = page.device_bindings["power_supplies"]["DP8B1519"]
        for field in ("role", "label", "supply_id"):
            self.assertNotIn(field, binding, "集合的键就是引用 ID，记录里不再重复")
        summary = page.discovery_panel.bindings_label.text()
        self.assertIn("电源 ← DP8B1519", summary)
        self.assertNotIn("DP832A-1", summary)
        self.assertIn("电源 DP8B1519", page.discovery_panel.status_label.text())

    def test_two_power_supplies_become_two_independent_bindings_in_one_batch(self):
        """绑定两台电源后一次应用即得到两条独立绑定与两条独立地址。"""
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)
        first = {
            "role": "power_supply",
            "address": "TCPIP0::192.168.1.102::inst0::INSTR",
            "serial": "DP8B214901519",
            "name": "DP832A-1",
            "vendor": "RIGOL TECHNOLOGIES",
            "model": "DP832A",
            "device_key": "rigol technologies:dp8b214901519",
            "supply_id": "DP8B1519",
        }
        second = {
            "role": "power_supply",
            "address": "TCPIP0::192.168.1.108::inst0::INSTR",
            "serial": "DP8B174400885",
            "name": "DP832A-3",
            "vendor": "RIGOL TECHNOLOGIES",
            "model": "DP832A",
            "device_key": "rigol technologies:dp8b174400885",
            "supply_id": "DP8B0885",
        }

        page.apply_discovered_devices([first, second])

        self.assertEqual(messages, [])
        self.assertEqual(
            set(page.device_bindings["power_supplies"]), {"DP8B1519", "DP8B0885"}
        )
        supplies = page.build_config()["instruments"]["power_supplies"]
        self.assertEqual(supplies["DP8B1519"]["address"], "TCPIP0::192.168.1.102::inst0::INSTR")
        self.assertEqual(supplies["DP8B0885"]["address"], "TCPIP0::192.168.1.108::inst0::INSTR")
        self.assertTrue(supplies["DP8B1519"]["enabled"])
        self.assertTrue(supplies["DP8B0885"]["enabled"])
        # 每台电源各有独立的通道参数页，不按位置互相覆盖。
        self.assertEqual(set(page.power_config_widgets), {"DP8B1519", "DP8B0885"})
        self.assertEqual(
            sorted(page.get_enabled_power_supplies()), ["DP8B0885", "DP8B1519"]
        )

    def test_replacing_the_power_supply_leaves_the_assignment_empty_instead_of_repointing(self):
        """旧引用失效时保持空选择：更换电源不能让分配静默指向另一台。"""
        page = self.make_page()
        first = {
            "role": "power_supply",
            "supply_id": "DP8B1519",
            "address": "TCPIP0::192.168.1.102::inst0::INSTR",
            "serial": "DP8B214901519",
            "vendor": "RIGOL TECHNOLOGIES",
            "device_key": "rigol technologies:dp8b214901519",
        }
        second = {
            "role": "power_supply",
            "supply_id": "DP8B0885",
            "address": "TCPIP0::192.168.1.108::inst0::INSTR",
            "serial": "DP8B174400885",
            "vendor": "RIGOL TECHNOLOGIES",
            "device_key": "rigol technologies:dp8b174400885",
        }
        page.apply_discovered_devices([first, second])
        page.pa_unit1_power_combo.setCurrentIndex(
            page.pa_unit1_power_combo.findData("DP8B1519")
        )

        page.apply_discovered_devices([second])

        self.assertEqual(page.pa_unit1_power_combo.currentText(), "")
        self.assertEqual(page.pa_unit1_power_combo.currentIndex(), -1)

    def test_power_supply_without_a_label_is_rejected_with_the_reason(self):
        """没有短标签就没有电源引用：配置页明确拒绝，不把地址写进别的电源。"""
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices(
            [
                {
                    "role": "power_supply",
                    "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                    "serial": "DP8B214901519",
                    "vendor": "RIGOL TECHNOLOGIES",
                    "device_key": "rigol technologies:dp8b214901519",
                }
            ]
        )

        self.assertIn("还没有短标签", messages[-1])
        self.assertEqual(page.device_bindings, {})

    def test_the_same_device_cannot_be_bound_twice_as_a_power_supply(self):
        """同一台设备（device_key）不能出现在两条电源绑定里。"""
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)
        entry = {
            "role": "power_supply",
            "address": "TCPIP0::192.168.1.102::inst0::INSTR",
            "serial": "DP8B214901519",
            "vendor": "RIGOL TECHNOLOGIES",
            "device_key": "rigol technologies:dp8b214901519",
        }

        page.apply_discovered_devices(
            [dict(entry, supply_id="DP8B1519"), dict(entry, supply_id="DP8B1519-B")]
        )

        self.assertIn("同一台设备不能重复绑定", messages[-1])
        self.assertEqual(page.device_bindings, {})

    def test_unknown_role_is_rejected(self):
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices([{"role": "nope", "address": "TCPIP0::1.2.3.4::inst0::INSTR"}])

        self.assertIn("未知的测试角色", messages[-1])
        self.assertEqual(page.device_bindings, {})

    def test_empty_address_is_rejected(self):
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices([{"role": "signal_generator", "address": "  "}])

        self.assertIn("发现地址为空", messages[-1])
        self.assertNotIn("address", page.build_config()["instruments"]["signal_generator"])

    def test_invalid_entry_rejects_the_whole_batch(self):
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices(
            [
                {"role": "signal_generator", "address": "TCPIP0::192.168.1.201::inst0::INSTR"},
                {"role": "nope", "address": "TCPIP0::192.168.1.202::inst0::INSTR"},
            ]
        )

        self.assertIn("未知的测试角色", messages[-1])
        # 先校验后写入：第一项也不能被写进连接配置或绑定记录。
        self.assertNotIn("address", page.build_config()["instruments"]["signal_generator"])
        self.assertEqual(page.device_bindings, {})

    def test_duplicate_roles_in_one_batch_are_rejected(self):
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices(
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "serial": "105462",
                },
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.202::inst0::INSTR",
                    "serial": "QZNI000866",
                },
            ]
        )

        self.assertIn("重复指定", messages[-1])
        self.assertNotIn("address", page.build_config()["instruments"]["signal_generator"])
        self.assertEqual(page.device_bindings, {})

    def test_missing_serial_is_rejected(self):
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices(
            [{"role": "signal_generator", "address": "TCPIP0::192.168.1.201::inst0::INSTR"}]
        )

        self.assertIn("缺少设备身份", messages[-1])
        self.assertNotIn("address", page.build_config()["instruments"]["signal_generator"])

    def test_identity_disagreeing_with_serial_is_rejected(self):
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices(
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "vendor": "Rohde&Schwarz",
                    "serial": "105462",
                    "device_key": "keysight technologies:105462",
                }
            ]
        )

        self.assertIn("设备身份与序列号不一致", messages[-1])
        self.assertEqual(page.device_bindings, {})

    def test_clear_button_removes_bindings_and_invalidates_the_connection(self):
        page = self.make_page()
        page.apply_discovered_devices(
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "serial": "105462",
                    "device_key": "rohde&schwarz:105462",
                    "ip": "192.168.1.201",
                    "port": 5025,
                    "known": True,
                }
            ]
        )
        # 相当于连接前已经保存过一次：地址已随配置落盘。
        page.apply_config(page.build_config())

        page.discovery_panel.request_clear()

        self.assertEqual(page.device_bindings, {})
        config = page.build_config()
        self.assertNotIn("device_bindings", config)
        # 绑定是唯一连接来源：清除后地址与启用状态一起失效，不会打到旧设备。
        self.assertNotIn("address", config["instruments"]["signal_generator"])
        self.assertFalse(config["instruments"]["signal_generator"]["enabled"])

    def test_applying_a_subset_replaces_the_previous_bindings(self):
        page = self.make_page()
        page.apply_discovered_devices(
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "serial": "105462",
                    "device_key": "rohde&schwarz:105462",
                    "ip": "192.168.1.201",
                    "port": 5025,
                    "known": True,
                },
                {
                    "role": "power_supply",
                    "supply_id": "DP8B1519",
                    "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                    "serial": "DP8B214901519",
                    "vendor": "RIGOL TECHNOLOGIES",
                    "device_key": "rigol technologies:dp8b214901519",
                    "ip": "192.168.1.102",
                    "port": 5555,
                    "known": True,
                },
            ]
        )
        # 相当于保存过一次：两条绑定与地址已经写进配置。
        page.apply_config(page.build_config())
        self.assertEqual(set(page.device_bindings["power_supplies"]), {"DP8B1519"})

        page.apply_discovered_devices(
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.211::inst0::INSTR",
                    "serial": "105462",
                    "device_key": "rohde&schwarz:105462",
                    "ip": "192.168.1.211",
                    "port": 5025,
                    "known": True,
                }
            ]
        )

        # 整批替换：没勾选的电源被移除，地址与启用状态一起失效，不残留旧电源。
        self.assertEqual(set(page.device_bindings), {"signal_generator"})
        config = page.build_config()
        self.assertEqual(
            config["instruments"]["signal_generator"]["address"],
            "TCPIP0::192.168.1.211::inst0::INSTR",
        )
        supply = config["instruments"]["power_supplies"]["DP8B1519"]
        self.assertNotIn("address", supply)
        self.assertFalse(supply["enabled"])
        self.assertEqual(page.get_enabled_power_supplies(), [])
        self.assertIn("已移除", page.discovery_panel.status_label.text())

    def test_clearing_bindings_invalidates_every_bound_power_supply(self):
        """清除绑定后所有对应电源地址和启用状态同时失效。"""
        page = self.make_page()
        page.apply_discovered_devices(
            [
                {
                    "role": "power_supply",
                    "supply_id": "DP8B1519",
                    "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                    "serial": "DP8B214901519",
                    "vendor": "RIGOL TECHNOLOGIES",
                    "device_key": "rigol technologies:dp8b214901519",
                },
                {
                    "role": "power_supply",
                    "supply_id": "DP8B0885",
                    "address": "TCPIP0::192.168.1.108::inst0::INSTR",
                    "serial": "DP8B174400885",
                    "vendor": "RIGOL TECHNOLOGIES",
                    "device_key": "rigol technologies:dp8b174400885",
                },
            ]
        )
        page.apply_config(page.build_config())

        page.clear_device_bindings()

        config = page.build_config()
        self.assertNotIn("device_bindings", config)
        for supply_id in ("DP8B1519", "DP8B0885"):
            supply = config["instruments"]["power_supplies"][supply_id]
            self.assertNotIn("address", supply)
            self.assertFalse(supply["enabled"])
        self.assertEqual(page.get_enabled_power_supplies(), [])

    def test_legacy_slot_binding_is_migrated_to_its_supply_id(self):
        """旧绑定按序列号迁移：地址写进 ``supply_id`` 条目，旧槽位条目随之失效。"""
        page = ConfigPage(
            {
                "test_frequencies": [1.0],
                "instruments": {
                    "power_supplies": {
                        "PS3": {
                            "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                            "enabled": True,
                            "channels": {"CH1": {"role": "gate"}, "CH2": {"role": "drain"}},
                        }
                    }
                },
                "device_bindings": {
                    "PS3": {
                        "role": "power_supply",
                        "label": "DP8B1519",
                        "supply_id": "DP8B1519",
                        "serial": "DP8B214901519",
                        "device_key": "rigol technologies:dp8b214901519",
                        "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                        "source": "scan",
                    }
                },
            },
            scanner_factory=_scanner_factory,
        )
        page.show()
        self.app.processEvents()
        self.addCleanup(page.close)

        self.assertEqual(set(page.device_bindings["power_supplies"]), {"DP8B1519"})
        supplies = page.build_config()["instruments"]["power_supplies"]
        self.assertEqual(supplies["DP8B1519"]["address"], "TCPIP0::192.168.1.102::inst0::INSTR")
        self.assertTrue(supplies["DP8B1519"]["enabled"])
        self.assertNotIn("address", supplies["PS3"])
        self.assertFalse(supplies["PS3"]["enabled"])
        self.assertEqual(page.get_enabled_power_supplies(), ["DP8B1519"])

    def test_legacy_power_binding_with_only_an_address_requires_a_rescan(self):
        """只有地址的旧绑定不是身份：不迁移，地址与启用状态一起失效。"""
        page = ConfigPage(
            {
                "test_frequencies": [1.0],
                "instruments": {
                    "power_supplies": {
                        "PS1": {
                            "address": "TCPIP0::192.168.1.101::inst0::INSTR",
                            "enabled": True,
                            "channels": {"CH1": {"role": "gate"}, "CH2": {"role": "drain"}},
                        }
                    }
                },
                "device_bindings": {
                    "PS1": {
                        "visa_address": "TCPIP0::192.168.1.101::inst0::INSTR",
                        "source": "legacy_config",
                    }
                },
            },
            scanner_factory=_scanner_factory,
        )
        page.show()
        self.app.processEvents()
        self.addCleanup(page.close)

        self.assertEqual(page.device_bindings, {})
        supply = page.build_config()["instruments"]["power_supplies"]["PS1"]
        self.assertNotIn("address", supply)
        self.assertFalse(supply["enabled"])
        self.assertEqual(page.get_enabled_power_supplies(), [])

    def test_empty_batch_is_rejected_without_touching_existing_bindings(self):
        page = self.make_page()
        page.apply_discovered_devices(
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "serial": "105462",
                    "device_key": "rohde&schwarz:105462",
                    "ip": "192.168.1.201",
                    "port": 5025,
                    "known": True,
                }
            ]
        )
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices([])

        self.assertIn("请先在发现结果中勾选", messages[-1])
        self.assertIn("signal_generator", page.device_bindings)

    def test_legacy_config_address_is_migrated_as_an_unconfirmed_binding(self):
        """只有地址的旧配置：加载后仍可连接，但在面板上标明“未扫描确认”。"""
        page = ConfigPage(
            {
                "test_frequencies": [1.0],
                "instruments": {
                    "signal_generator": {
                        "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                        "enabled": True,
                    },
                    "power_supplies": {},
                },
            },
            scanner_factory=_scanner_factory,
        )
        page.show()
        self.app.processEvents()
        self.addCleanup(page.close)

        self.assertEqual(
            page.device_bindings["signal_generator"]["source"], "legacy_config"
        )
        self.assertEqual(
            page.build_config()["instruments"]["signal_generator"]["address"],
            "TCPIP0::192.168.1.201::inst0::INSTR",
        )
        self.assertIn("未扫描确认", page.discovery_panel.bindings_label.text())
        # 扫描框初值取自已保存的地址网段。
        self.assertEqual(page.discovery_panel.subnet_edit.text(), "192.168.1.0/24")

    def test_rejected_batch_is_also_reported_in_the_panel_status(self):
        """整批拒绝的原因要同时出现在面板状态里，不能只写日志。"""
        page = self.make_page()

        page.apply_discovered_devices(
            [{"role": "signal_generator", "address": "TCPIP0::192.168.1.201::inst0::INSTR"}]
        )

        self.assertIn("缺少设备身份", page.discovery_panel.status_label.text())

    def test_loading_a_config_restores_device_bindings(self):
        page = self.make_page()
        config = page.build_config()
        config["device_bindings"] = {
            "signal_generator": {"serial": "105462", "device_key": "rohde&schwarz:105462"}
        }

        page.apply_config(config)

        self.assertEqual(page.device_bindings["signal_generator"]["serial"], "105462")
        self.assertEqual(
            page.build_config()["device_bindings"]["signal_generator"]["serial"], "105462"
        )

    def test_clearing_bindings_drops_them_from_the_saved_config(self):
        page = self.make_page()
        page.apply_discovered_devices(
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "serial": "105462",
                    "device_key": "rohde&schwarz:105462",
                    "ip": "192.168.1.201",
                    "port": 5025,
                    "known": True,
                }
            ]
        )
        page.clear_device_bindings()

        self.assertNotIn("device_bindings", page.build_config())


    def test_power_supply_id_with_a_channel_separator_is_rejected(self):
        """引用会拼成 ``supply_id/CHx``：含分隔符的标签整批拒绝，不写进配置。"""
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices(
            [
                {
                    "role": "power_supply",
                    "supply_id": "DP8B/1519",
                    "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                    "vendor": "RIGOL TECHNOLOGIES",
                    "serial": "DP8B214901519",
                    "device_key": "rigol technologies:dp8b214901519",
                }
            ]
        )

        self.assertIn("不能作为配置引用", messages[-1])
        self.assertIn("DP8B/1519", messages[-1])
        self.assertEqual(page.device_bindings, {})
        self.assertEqual(page.build_config()["instruments"]["power_supplies"], {})

    def test_power_supply_without_an_identity_is_rejected_at_the_write_boundary(self):
        """没有 ``device_key`` 也没有厂商的电源不能写绑定：下一次扫描找不回它。"""
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        page.apply_discovered_devices(
            [
                {
                    "role": "power_supply",
                    "supply_id": "DP8B1519",
                    "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                    "serial": "DP8B214901519",
                }
            ]
        )

        self.assertIn("缺少可用于绑定的设备身份", messages[-1])
        self.assertEqual(page.device_bindings, {})

    def test_invalid_power_binding_is_reported_and_blocks_save(self):
        """读入时的坏绑定不能静默消失：校验失败并说明原因，而不是悄悄清掉地址。"""
        config = {
            "test_frequencies": [1.0],
            "instruments": {
                "power_supplies": {
                    "DP8B1519": {
                        "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                        "enabled": True,
                        "channels": {"CH1": {"role": "gate"}},
                    }
                }
            },
            "device_bindings": {
                "power_supplies": {
                    "DP8B1519": {"visa_address": "TCPIP0::192.168.1.102::inst0::INSTR"}
                }
            },
        }
        page = ConfigPage(config, scanner_factory=_scanner_factory)
        page.show()
        self.app.processEvents()
        self.addCleanup(page.close)
        messages = []
        page.validation_failed.connect(messages.append)

        # 坏绑定不进入可用电源，也不会被当成已连接设备。
        self.assertEqual(page.get_enabled_power_supplies(), [])
        self.assertEqual(page.device_bindings, {})
        self.assertIn("缺少设备身份", "；".join(page.binding_problems))
        self.assertIn("缺少设备身份", page.discovery_panel.status_label.text())
        self.assertFalse(page.validate())
        self.assertIn("设备绑定不可用", messages[-1])

    def test_applying_valid_devices_clears_a_recorded_binding_problem(self):
        """重新扫描并应用正确设备后，读入时记录的问题随之清除。"""
        config = {
            "test_frequencies": [1.0],
            "instruments": {"power_supplies": {}},
            "device_bindings": {
                "power_supplies": {
                    "DP8B1519": {"visa_address": "TCPIP0::192.168.1.102::inst0::INSTR"}
                }
            },
        }
        page = ConfigPage(config, scanner_factory=_scanner_factory)
        page.show()
        self.app.processEvents()
        self.addCleanup(page.close)
        self.assertTrue(page.binding_problems)

        page.apply_discovered_devices(
            [
                {
                    "role": "power_supply",
                    "supply_id": "DP8B1519",
                    "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                    "vendor": "RIGOL TECHNOLOGIES",
                    "model": "DP832A",
                    "serial": "DP8B214901519",
                    "device_key": "rigol technologies:dp8b214901519",
                }
            ]
        )

        self.assertEqual(page.binding_problems, [])


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


if __name__ == "__main__":
    unittest.main()
