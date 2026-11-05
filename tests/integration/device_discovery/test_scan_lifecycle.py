"""扫描生命周期：点“查找设备”之后结果怎么来，失败、关闭与选择怎么收尾。

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
from presentation.qt.instrument_discovery_panel import BOX_STATE_BUSY, BOX_STATE_IDLE

from .support import DP832A_IDN, SMW_IDN, DiscoveryPanelTestCase, registry


class DiscoveryPanelTests(DiscoveryPanelTestCase):
    """点“查找设备”之后的可见结果、失败收尾与每次查找的干净起点。"""

    def test_scan_shows_one_card_per_device(self):
        panel = self.make_panel()

        self.run_scan(panel)

        self.assertEqual(panel.card_count(), 4)
        self.assertEqual(len(panel.cards()), 4)
        names = set(panel.card_headings())
        # 卡片正面只写设备名：角色在型号旁边，未登记的设备显示兜底名。
        self.assertIn("SMW200A（105462）", names)
        # 电源还没有短标签时退回资产名，但仍然是类别“电源”，不是“电源 PS3”这种槽位编号。
        self.assertIn("DP832A（DP8B214901519）", names)
        self.assertIn("未知设备 (N9020A)", names)
        # 数量统计不再重复写进状态行：每张卡片自己写明登记与可用状态。
        self.assertEqual(panel.status_label.text(), "")
        self.assertFalse(panel.status_label.isVisible())

    def test_cards_are_ordered_signal_generator_power_supplies_spectrum_analyzer_unknown(self):
        """卡片顺序：信号源第一，频谱仪在已登记仪器最后，未登记设备在全部卡片最后。"""
        panel = self.make_panel()

        self.run_scan(panel)

        # 演示网络恰好四类都有：SMW200A（信号源）、DP832A（电源）、4082F（频谱仪）、
        # N9020A（未登记）。电源在中间，未登记设备仍然排最后。
        self.assertEqual(
            panel.card_headings(),
            [
                "SMW200A（105462）",
                "DP832A（DP8B214901519）",
                "4082F（QZNI000866）",
                "未知设备 (N9020A)",
            ],
        )

    def test_cards_without_signal_or_spectrum_keep_power_first_and_unknown_last(self):
        """没有信号源与频谱仪时不做特判：电源排最前，未登记设备仍排最后。"""

        def factory(_mode):
            return build_scanner(
                registry=registry(),
                mode=SCAN_MODE_SIMULATED,
                responses={
                    ("192.168.1.102", 5555): DP832A_IDN,
                    ("192.168.1.150", 5025): (
                        "Keysight Technologies,N9020A,MY51234567,A.18.21"
                    ),
                },
                timeout_s=0.02,
            )

        panel = self.make_panel(factory)

        self.run_scan(panel)

        self.assertEqual(
            panel.card_headings(),
            ["DP832A（DP8B214901519）", "未知设备 (N9020A)"],
        )

    def test_cards_show_the_ip_on_its_own_gray_line(self):
        """IP 只写在卡片最下面那一行的灰色小字里，不混进设备码或型号。"""
        panel = self.make_panel()

        self.run_scan(panel)

        for card in panel.cards():
            self.assertRegex(card.address_label.text(), r"^192\.168\.1\.\d+$")
            for label in (card.name_label, card.code_label):
                self.assertNotIn("192.168.1.", label.text())

    def test_unknown_device_card_keeps_its_identity_in_the_code_and_tooltip(self):
        panel = self.make_panel()

        self.run_scan(panel)

        heading = "未知设备 (N9020A)"
        card = panel.card_for(heading)
        # 第一行只写缩写码：未登记设备没有缩写码，退回完整设备码；型号行没有角色可写。
        self.assertEqual(card.code_label.text(), "MY51234567")
        self.assertEqual(card.name_label.text(), "N9020A")
        self.assertEqual(card.address_label.text(), "192.168.1.150")
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
        card = panel.card_for("SMW200A（105462）")
        self.assertEqual(card.code_label.text(), "105462")
        self.assertEqual(panel.card_status("SMW200A（105462）"), "未分配角色 · 不能应用")
        self.assertFalse(card.checkable)
        panel.set_card_checked("SMW200A（105462）", True)
        self.assertFalse(panel.card_checked("SMW200A（105462）"))
        self.assertEqual(panel.status_label.text(), "", "没有可说的提示时状态行保持空白")

    def test_power_supply_without_a_label_says_why_the_model_is_shown(self):
        """旧设备表还没有缩写设备码：卡片显示型号，提示里说明原因。

        这台电源有旧槽位，所以不该同时出现“地址无处可写”的过渡提示——两件事分开写，
        用户才知道该做什么。
        """
        panel = self.make_panel()

        self.run_scan(panel)

        tip = panel.card_tooltip("DP832A（DP8B214901519）")
        self.assertIn("缩写设备码", tip)
        self.assertIn("型号", tip)
        self.assertNotIn("没有旧槽位", tip)

    def test_device_card_carries_picture_name_and_device_code(self):
        panel = self.make_panel()

        self.run_scan(panel)

        card = panel.card_for("SMW200A（105462）")
        self.assertTrue(card.image.has_picture(), "信号源卡片应显示仪器图片")
        # 第一行只写缩写码：设备表还没有缩写码，退回完整设备码；型号后面接角色。
        self.assertEqual(card.name_label.text(), "SMW200A·信号发生器")
        self.assertEqual(card.code_label.text(), "105462")

    def test_selecting_a_card_is_enough_without_choosing_a_role(self):
        """角色来自设备表：点击卡片即选中，交付结果里不会出现“未选择测试角色”。"""
        panel = self.make_panel()
        self.run_scan(panel)

        panel.set_card_checked("SMW200A（105462）", True)
        assignments, problems = panel.selected_assignments()

        self.assertEqual(problems, [])
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["role"], "signal_generator")

    def test_clicking_a_card_toggles_its_selection(self):
        panel = self.make_panel()
        self.run_scan(panel)
        card = panel.card_for("4082F（QZNI000866）")

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
        card = panel.card_for("4082F（QZNI000866）")

        for target in (card.name_label, card.address_label, card.image, card.code_label):
            with self.subTest(target=type(target).__name__):
                card.set_checked(False, emit=False)
                QTest.mouseClick(target, Qt.LeftButton)
                self.app.processEvents()
                self.assertTrue(card.is_checked())

    def test_duplicate_roles_are_reported_by_the_selection(self):
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

        problems = panel.selected_assignments()[1]

        self.assertIn("重复选择", "；".join(problems))

    def test_selection_reports_role_and_identity(self):
        panel = self.make_panel()
        self.run_scan(panel)

        panel.set_card_checked("SMW200A（105462）", True)
        assignments, problems = panel.selected_assignments()

        self.assertEqual(problems, [])
        assignment = assignments[0]
        self.assertEqual(assignment["role"], "signal_generator")
        self.assertEqual(assignment["full_code"], "105462")
        self.assertEqual(assignment["address"], "TCPIP0::192.168.1.201::inst0::INSTR")
        self.assertTrue(assignment["known"])
        # 信号源没有现场短标签：``label`` / ``supply_id`` 为空，配置页不会拿它当电源引用。
        self.assertIsNone(assignment["label"])
        self.assertIsNone(assignment["supply_id"])

    def test_power_supply_without_a_label_cannot_be_applied(self):
        """旧设备表还没有缩写设备码：电源没有可写的配置引用，选择结果明确报告。"""
        panel = self.make_panel()
        self.run_scan(panel)

        panel.set_card_checked("DP832A（DP8B214901519）", True)
        assignments, problems = panel.selected_assignments()

        self.assertEqual(assignments, [])
        self.assertIn("缩写设备码", "；".join(problems))

    def test_invalid_subnet_reports_failure_without_crashing(self):
        panel = self.make_panel()
        panel.subnet_edit.setText("not-a-subnet")

        panel.request_scan()
        panel.worker.wait(5000)
        self.app.processEvents()
        self.app.processEvents()

        # 失败也要等这一轮动画走完（牌堆复原）才收走等待动画。
        self.assertEqual(panel.box_state(), BOX_STATE_BUSY)
        self.finish_scan(panel)

        self.assertIn("扫描失败", panel.status_label.text())
        self.assertEqual(panel.card_count(), 0)

    def test_shutdown_without_running_scan_is_ok(self):
        panel = self.make_panel()
        self.assertTrue(panel.shutdown())

    def test_scan_starts_with_nothing_selected(self):
        """每次点“查找设备”都是新的一次选择：新结果里的卡片一条都不选中。"""
        panel = self.make_panel()
        self.run_scan(panel)
        heading = "SMW200A（105462）"
        panel.set_card_checked(heading, True)
        self.assertTrue(panel.card_checked(heading))

        # 结果可交互时再查一次：上一轮的勾选不能带进新结果。
        self.run_scan(panel)

        for card in panel.cards():
            self.assertFalse(card.is_checked(), "新一次查找必须从一台都没选开始")

    def test_clearing_results_keeps_cards_owned_by_qt_until_deferred_delete(self):
        """清空结果时卡片仍必须挂在父控件下，不能先 setParent(None) 再 deleteLater。

        先交出父控件会把 C++ 所有权转给 Python；``self._rows`` 清空后包装对象被回收，
        C++ 控件立刻销毁，而 deleteLater 已经排进事件队列，随后访问已释放内存造成
        双重释放。它不会当场报错，往往在下一次“查找设备”创建扫描线程时以访问冲突
        闪退，表现为点查找设备就卡死。这里锁定父控件仍然存在这一必要条件。
        """
        panel = self.make_panel()
        self.run_scan(panel)
        cards = list(panel.cards())

        panel.clear_results()

        for card in cards:
            self.assertIsNotNone(
                card.parent(), "deleteLater 之前卡片必须仍由 Qt 父控件持有"
            )

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

    def test_scan_failure_restores_the_button_and_shows_the_reason(self):
        def factory(_mode):
            raise InstrumentDiscoveryError("设备表损坏")

        panel = self.make_panel(factory)

        self.run_scan(panel)

        self.assertIn("无法开始扫描", panel.status_label.text())
        self.assertIn("设备表损坏", panel.status_label.text())
        self.assertTrue(panel.scan_btn.isEnabled())
        # 失败后结果区里仍然没有结果：顶部的“查找设备”回来。
        self.assertTrue(panel.scan_btn.isVisible())
        self.assertEqual(panel.box_state(), BOX_STATE_IDLE)

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

    def test_default_factory_lists_every_registered_instrument_plus_one_unknown(self):
        """离线演示展示设备表里登记的全部仪器，并保留一台未登记设备。"""
        from presentation.qt.composition import build_device_scanner_factory

        registered = InstrumentRegistry.load()
        scanner = build_device_scanner_factory()(SCAN_MODE_SIMULATED)

        result = scanner.scan("192.168.1.0/24")

        self.assertEqual(
            {device.device_key for device in result.known},
            {instrument.device_key for instrument in registered.list()},
        )
        self.assertEqual(len(result.unknown), 1)
        self.assertEqual(result.unknown[0].identity.model, "N9020A")
