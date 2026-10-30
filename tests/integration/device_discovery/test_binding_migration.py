"""旧绑定与旧配置的迁移：按身份改写，只有地址的旧记录要求重新扫描。"""

from .support import ConnectionPageTestCase


class DeviceBindingMigrationTests(ConnectionPageTestCase):
    """旧槽位键与旧地址绑定的迁移边界：能认出设备的迁移，认不出的失效。"""

    def test_legacy_slot_binding_is_migrated_to_its_supply_id(self):
        """旧绑定按序列号迁移：地址写进 ``supply_id`` 条目，旧槽位条目随之失效。"""
        page = self.make_page(
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
            }
        )

        self.assertEqual(set(page.device_bindings["power_supplies"]), {"DP8B1519"})
        supplies = page.build_config()["instruments"]["power_supplies"]
        self.assertEqual(supplies["DP8B1519"]["address"], "TCPIP0::192.168.1.102::inst0::INSTR")
        self.assertTrue(supplies["DP8B1519"]["enabled"])
        self.assertNotIn("address", supplies["PS3"])
        self.assertFalse(supplies["PS3"]["enabled"])
        self.assertEqual(page.get_enabled_power_supplies(), ["DP8B1519"])

    def test_legacy_power_binding_with_only_an_address_requires_a_rescan(self):
        """只有地址的旧绑定不是身份：不迁移，地址与启用状态一起失效。"""
        page = self.make_page(
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
            }
        )

        self.assertEqual(page.device_bindings, {})
        supply = page.build_config()["instruments"]["power_supplies"]["PS1"]
        self.assertNotIn("address", supply)
        self.assertFalse(supply["enabled"])
        self.assertEqual(page.get_enabled_power_supplies(), [])

    def test_legacy_config_address_is_migrated_as_an_unconfirmed_binding(self):
        """只有地址的旧配置：加载后仍可连接，并明确记一条“未扫描确认”的日志。"""
        page = self.make_page(
            {
                "test_frequencies": [1.0],
                "instruments": {
                    "signal_generator": {
                        "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                        "enabled": True,
                    },
                    "power_supplies": {},
                },
            }
        )

        self.assertEqual(
            page.device_bindings["signal_generator"]["source"], "legacy_config"
        )
        self.assertEqual(
            page.build_config()["instruments"]["signal_generator"]["address"],
            "TCPIP0::192.168.1.201::inst0::INSTR",
        )
        # 框里只放扫描到的设备：旧绑定改为一条日志，说明它没有被本次扫描确认。
        logs = []
        self.connection.log_message.connect(logs.append)
        self.connection.enter()
        self.assertTrue(any("未扫描确认" in line for line in logs))
        # 扫描框初值取自已保存的地址网段。
        self.assertEqual(self.connection.discovery_panel.subnet_edit.text(), "192.168.1.0/24")
