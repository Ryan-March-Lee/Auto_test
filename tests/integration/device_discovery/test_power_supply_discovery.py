"""多台电源的发现结果：卡片写短标签，两台电源各自独立选中、同批独立交付。"""

from .support import DiscoveryPanelTestCase, two_supply_factory


class PowerSupplyDiscoveryTests(DiscoveryPanelTestCase):
    """多台电源：卡片显示短标签、各自独立选中与传递，身份仍只按 ``device_key`` 匹配。"""

    default_factory = staticmethod(two_supply_factory)

    def test_two_supplies_are_shown_with_their_frozen_short_labels(self):
        panel = self.make_panel()

        self.run_scan(panel)

        self.assertEqual(set(panel.card_headings()), {"DP8B1519", "DP8B0885"})
        for heading, card in zip(panel.card_headings(), panel.cards()):
            self.assertTrue(card.checkable)
            # 第一行只写仪器名（型号），最下面一行是短标签。
            self.assertEqual(card.name_label.text(), "DP832A")
            # 卡片正面的身份码就是短标签：不再拼 ``PS/型号/序列号``，电源没有“第几台”。
            self.assertEqual(card.code_label.text(), heading)
            self.assertFalse(card.code_label.text().startswith("PS/"))
        self.assertTrue(panel.apply_btn.isEnabled())

    def test_power_supply_tooltip_uses_the_domain_identity_text(self):
        """电源悬浮提示与标签页、日志共用领域层的身份文本，不另拼一套。"""
        panel = self.make_panel()

        self.run_scan(panel)

        tip = panel.card_tooltip("DP8B1519")
        self.assertIn("DP8B1519（DP832A）", tip)
        self.assertIn("序列号 DP8B214901519", tip)
        self.assertIn("设备键 rigol technologies:dp8b214901519", tip)
        # 地址只作为连接信息单独一行，不参与身份判定。
        self.assertIn("地址 TCPIP0::192.168.1.102::inst0::INSTR", tip)

    def test_power_supply_card_with_a_label_has_no_extra_status(self):
        """迁移后的设备表只说明类别与短标签：卡片不再提示“没有旧槽位”。"""
        panel = self.make_panel()

        self.run_scan(panel)

        self.assertEqual(panel.card_status("DP8B1519"), "")
        self.assertNotIn("没有短标签", panel.card_tooltip("DP8B0885"))

    def test_two_supplies_are_applied_independently_without_a_duplicate_role_error(self):
        panel = self.make_panel()
        self.run_scan(panel)
        for heading in panel.card_headings():
            panel.set_card_checked(heading, True)
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
        panel.set_card_checked(panel.card_headings()[0], True)
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
