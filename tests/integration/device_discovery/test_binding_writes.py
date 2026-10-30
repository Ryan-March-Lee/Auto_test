"""勾选结果写进配置：地址、身份与绑定记录，整批替换/清除之后地址与启用一起失效。"""

from .support import ConnectionPageTestCase


class DeviceBindingWriteTests(ConnectionPageTestCase):
    """``apply_discovered_devices`` 写入契约：写什么、换一批之后旧绑定怎么收尾。"""

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
        # 绑定已经写进配置页：再次进入本页时按身份预选同一台设备。
        self.assertIn(
            "spectrum_analyzer", self.connection.discovery_panel.device_bindings
        )

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
        self.assertIn("电源 DP8B1519", self.connection.discovery_panel.status_label.text())

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

    def test_power_supply_binding_record_uses_the_short_label_as_key(self):
        """电源绑定按 supply_id 存一条记录，记录里不再重复写类别与标签。"""
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
        self.assertEqual(binding["device_key"], "rigol technologies:dp8b214901519")
        self.assertIn("电源 DP8B1519", self.connection.discovery_panel.status_label.text())

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

        self.connection.discovery_panel.request_clear()

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
        self.assertIn("已移除", self.connection.discovery_panel.status_label.text())

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
        page = self.make_page(config)
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
