"""整批拒绝：缺身份、重复角色、坏引用一律不写，原因同时出现在面板状态里。"""

from .support import InstrumentSetupPageTestCase, apply_devices


class DeviceBindingRejectionTests(InstrumentSetupPageTestCase):
    """不合法或有歧义的勾选结果必须整批拒绝，且不留下半份绑定。"""

    def test_power_supply_without_a_label_is_rejected_with_the_reason(self):
        """没有缩写设备码就没有电源引用：配置页明确拒绝，不把地址写进别的电源。"""
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        apply_devices(page,
            [
                {
                    "role": "power_supply",
                    "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                    "full_code": "DP8B214901519",
                    "vendor": "RIGOL TECHNOLOGIES",
                    "device_key": "rigol technologies:dp8b214901519",
                }
            ]
        )

        self.assertIn("还没有缩写设备码", messages[-1])
        self.assertEqual(page.device_bindings, {})

    def test_the_same_device_cannot_be_bound_twice_as_a_power_supply(self):
        """同一台设备（device_key）不能出现在两条电源绑定里。"""
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)
        entry = {
            "role": "power_supply",
            "address": "TCPIP0::192.168.1.102::inst0::INSTR",
            "full_code": "DP8B214901519",
            "vendor": "RIGOL TECHNOLOGIES",
            "device_key": "rigol technologies:dp8b214901519",
        }

        apply_devices(page,
            [dict(entry, supply_id="DP8B1519"), dict(entry, supply_id="DP8B1519-B")]
        )

        self.assertIn("同一台设备不能重复绑定", messages[-1])
        self.assertEqual(page.device_bindings, {})

    def test_unknown_role_is_rejected(self):
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        apply_devices(page, [{"role": "nope", "address": "TCPIP0::1.2.3.4::inst0::INSTR"}])

        self.assertIn("未知的测试角色", messages[-1])
        self.assertEqual(page.device_bindings, {})

    def test_empty_address_is_rejected(self):
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        apply_devices(page, [{"role": "signal_generator", "address": "  "}])

        self.assertIn("发现地址为空", messages[-1])
        self.assertNotIn("address", page.build_config()["instruments"]["signal_generator"])

    def test_invalid_entry_rejects_the_whole_batch(self):
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        apply_devices(page,
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

        apply_devices(page,
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "full_code": "105462",
                },
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.202::inst0::INSTR",
                    "full_code": "QZNI000866",
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

        applied = apply_devices(page,
            [{"role": "signal_generator", "address": "TCPIP0::192.168.1.201::inst0::INSTR"}]
        )

        self.assertFalse(applied.ok)
        self.assertIn("缺少设备身份", messages[-1])
        self.assertNotIn("address", page.build_config()["instruments"]["signal_generator"])

    def test_identity_disagreeing_with_full_code_is_rejected(self):
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        apply_devices(page,
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "vendor": "Rohde&Schwarz",
                    "full_code": "105462",
                    "device_key": "keysight technologies:105462",
                }
            ]
        )

        self.assertIn("设备身份与完整设备码不一致", messages[-1])
        self.assertEqual(page.device_bindings, {})

    def test_empty_batch_is_rejected_without_touching_existing_bindings(self):
        page = self.make_page()
        apply_devices(page,
            [
                {
                    "role": "signal_generator",
                    "address": "TCPIP0::192.168.1.201::inst0::INSTR",
                    "full_code": "105462",
                    "device_key": "rohde&schwarz:105462",
                    "ip": "192.168.1.201",
                    "port": 5025,
                    "known": True,
                }
            ]
        )
        messages = []
        page.validation_failed.connect(messages.append)

        apply_devices(page, [])

        self.assertIn("请先在发现结果中勾选", messages[-1])
        self.assertIn("signal_generator", page.device_bindings)

    def test_a_rejected_batch_leaves_the_previous_bindings_and_power_ui_untouched(self):
        """拒绝时页面不投影新状态：旧绑定、电源标签页与分配可选项都保持原样。"""
        page = self.make_page()
        apply_devices(page,
            [
                {
                    "role": "power_supply",
                    "supply_id": "DP8B1519",
                    "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                    "vendor": "RIGOL TECHNOLOGIES",
                    "model": "DP832A",
                    "full_code": "DP8B214901519",
                    "device_key": "rigol technologies:dp8b214901519",
                }
            ]
        )
        options_before = list(self.power_page.get_enabled_power_supplies())
        tabs_before = set(self.power_page.power_config_widgets)

        apply_devices(page, [{"role": "nope", "address": "TCPIP0::1.2.3.4::inst0::INSTR"}])

        self.assertEqual(set(page.device_bindings["power_supplies"]), {"DP8B1519"})
        self.assertEqual(self.power_page.get_enabled_power_supplies(), options_before)
        self.assertEqual(set(self.power_page.power_config_widgets), tabs_before)

    def test_rejected_batch_is_also_reported_in_the_panel_status(self):
        """整批拒绝的原因要同时出现在面板状态里，不能只写日志。"""
        page = self.make_page()

        apply_devices(page,
            [{"role": "signal_generator", "address": "TCPIP0::192.168.1.201::inst0::INSTR"}]
        )

        self.assertIn("缺少设备身份", self.connection.discovery_panel.status_label.text())

    def test_power_supply_id_with_a_channel_separator_is_rejected(self):
        """引用会拼成 ``supply_id/CHx``：含分隔符的标签整批拒绝，不写进配置。"""
        page = self.make_page()
        messages = []
        page.validation_failed.connect(messages.append)

        apply_devices(page,
            [
                {
                    "role": "power_supply",
                    "supply_id": "DP8B/1519",
                    "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                    "vendor": "RIGOL TECHNOLOGIES",
                    "full_code": "DP8B214901519",
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

        apply_devices(page,
            [
                {
                    "role": "power_supply",
                    "supply_id": "DP8B1519",
                    "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                    "full_code": "DP8B214901519",
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
        page = self.make_page(config)
        messages = []
        page.validation_failed.connect(messages.append)

        # 坏绑定不进入可用电源，也不会被当成已连接设备。
        self.assertEqual(self.power_page.get_enabled_power_supplies(), [])
        self.assertEqual(page.device_bindings, {})
        self.assertIn("缺少设备身份", "；".join(page.binding_problems))
        self.assertIn("缺少设备身份", self.connection.discovery_panel.status_label.text())
        self.assertFalse(page.validate())
        self.assertIn("设备绑定不可用", messages[-1])
