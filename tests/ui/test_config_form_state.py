import unittest

from presentation.qt.config_form_state import (
    POWER_SUPPLY_BINDINGS,
    LEGACY_BINDING_SOURCE,
    ConfigFormState,
    assignment_declared,
    assignment_supply_id,
    bound_power_supplies,
    effective_device_bindings,
    parse_frequency_text,
    power_supply_binding_problems,
    power_supply_bindings,
    supply_id_problem,
)


class _Text:
    def __init__(self, value):
        self.value = value

    def text(self):
        return self.value


def _combo(value):
    """电源分配下拉框的替身：可见文本与真实控件一样，业务值放在 ``itemData``。

    真实控件显示 ``短标签（型号）``，``itemData`` 才是 ``supply_id``；替身省略显示标签，
    但同样只通过 ``itemData`` 读出业务值（只给 ``currentText`` 的替身按未选择处理）。
    """
    return type(
        "C",
        (),
        {
            "currentText": lambda self: value or "",
            "currentIndex": lambda self: 0 if value else -1,
            "itemData": lambda self, index: value if value else None,
        },
    )()


def bound_supplies(*supply_ids):
    """按给定 ``supply_id`` 造一份电源绑定集合（每台各带地址与身份）。"""
    return {
        POWER_SUPPLY_BINDINGS: {
            supply_id: {
                "device_key": f"rigol technologies:{supply_id.casefold()}",
                "visa_address": f"TCPIP0::192.168.1.{index}::inst0::INSTR",
            }
            for index, supply_id in enumerate(supply_ids, 1)
        }
    }


class ConfigFormStateTests(unittest.TestCase):
    def test_frequency_parser_accepts_numbers_without_eval(self):
        self.assertEqual(parse_frequency_text("[1, 2.5]"), [1.0, 2.5])
        self.assertIsNone(parse_frequency_text("__import__('os').system('x')"))
        self.assertIsNone(parse_frequency_text("[1, 'bad']"))

    def test_build_preserves_unknown_fields_and_drops_legacy_wiring(self):
        widgets = type("Widgets", (), {})()
        widgets.freq_edit = _Text("[1, 2]")
        widgets.start_power = type("V", (), {"value": lambda self: -10})()
        widgets.stop_power = type("V", (), {"value": lambda self: 10})()
        widgets.power_step = type("V", (), {"value": lambda self: 1})()
        widgets.compression_combo = type("C", (), {"currentText": lambda self: "5dB"})()
        widgets.attenuator_combo = type("C", (), {"currentText": lambda self: "30dB"})()
        widgets.driver_mode_check = type("B", (), {"isChecked": lambda self: False})()
        widgets.max_input_power = type("V", (), {"value": lambda self: 30})()
        widgets.pa_unit_count_combo = type("C", (), {"currentText": lambda self: "1"})()
        widgets.power_config_widgets = {}
        widgets.power_assignment_widgets = {
            "driver_enabled": type("B", (), {"isChecked": lambda self: False})(),
            "driver_power": type("C", (), {"currentText": lambda self: "DP8B1519"})(),
            "pa_unit1_power": type("C", (), {"currentText": lambda self: "DP8B1519"})(),
            "pa_unit2_power": type("C", (), {"currentText": lambda self: "DP8B0885"})(),
            "pa_unit3_power": type("C", (), {"currentText": lambda self: "DP8B1191"})(),
        }
        # 仪器连接只来自设备发现结果：绑定记录就是唯一的地址来源，界面没有地址控件。
        widgets.device_bindings = {
            "signal_generator": {"visa_address": "TCPIP0::192.168.1.201::inst0::INSTR"},
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "serial": "DP8B214901519",
                    "vendor": "RIGOL TECHNOLOGIES",
                    "model": "DP832A",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                }
            },
        }

        original = {"top": "keep", "wiring": {"confirmed": True}, "dut_config": {"extra": 1}}
        result = ConfigFormState(original, widgets).build_config()
        self.assertEqual(result["top"], "keep")
        self.assertEqual(result["dut_config"]["extra"], 1)
        self.assertNotIn("wiring", result)
        instruments = result["instruments"]
        self.assertEqual(
            instruments["signal_generator"]["address"],
            "TCPIP0::192.168.1.201::inst0::INSTR",
        )
        self.assertTrue(instruments["signal_generator"]["enabled"])
        # 电源按 supply_id 落键，身份字段来自绑定记录，集合键同时写成同名 label。
        supply = instruments["power_supplies"]["DP8B1519"]
        self.assertEqual(supply["address"], "TCPIP0::192.168.1.102::inst0::INSTR")
        self.assertTrue(supply["enabled"])
        self.assertEqual(supply["label"], "DP8B1519")
        self.assertEqual(supply["device_key"], "rigol technologies:dp8b214901519")
        self.assertEqual(supply["serial"], "DP8B214901519")
        self.assertEqual(supply["model"], "DP832A")
        # 没有绑定记录的角色不臆造地址，并显式写成“未启用”，避免旧地址继续被连接。
        self.assertNotIn("address", instruments["spectrum_analyzer"])
        self.assertFalse(instruments["spectrum_analyzer"]["enabled"])

    def test_read_instrument_config_rewrites_every_supply_from_bindings(self):
        """两条电源绑定各写一条地址；未绑定的旧槽位条目地址与启用状态一起失效。"""
        widgets = type("Widgets", (), {})()
        widgets.device_bindings = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "serial": "DP8B214901519",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                },
                "DP8B0885": {
                    "device_key": "rigol technologies:dp8b174400885",
                    "serial": "DP8B174400885",
                    "visa_address": "TCPIP0::192.168.1.108::inst0::INSTR",
                },
            }
        }
        config = {
            "instruments": {
                "power_supplies": {
                    "PS1": {
                        "address": "TCPIP0::192.168.1.101::inst0::INSTR",
                        "enabled": True,
                        "channels": {"CH1": {"role": "gate"}},
                        "unknown": "preserved",
                    }
                }
            }
        }

        supplies = ConfigFormState(config, widgets).read_instrument_config()["instruments"][
            "power_supplies"
        ]

        self.assertEqual(
            supplies["DP8B1519"]["address"], "TCPIP0::192.168.1.102::inst0::INSTR"
        )
        self.assertTrue(supplies["DP8B1519"]["enabled"])
        self.assertEqual(
            supplies["DP8B0885"]["address"], "TCPIP0::192.168.1.108::inst0::INSTR"
        )
        self.assertTrue(supplies["DP8B0885"]["enabled"])
        # 旧槽位条目不再被当作已绑定设备：参数保留，地址与启用状态失效。
        self.assertNotIn("address", supplies["PS1"])
        self.assertFalse(supplies["PS1"]["enabled"])
        self.assertEqual(supplies["PS1"]["channels"], {"CH1": {"role": "gate"}})
        self.assertEqual(supplies["PS1"]["unknown"], "preserved")

    def test_clearing_bindings_invalidates_every_supply_address(self):
        widgets = type("Widgets", (), {})()
        widgets.device_bindings = {}
        config = {
            "instruments": {
                "power_supplies": {
                    "DP8B1519": {
                        "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                        "enabled": True,
                        "channels": {"CH1": {"role": "gate"}},
                    }
                }
            }
        }

        supply = ConfigFormState(config, widgets).read_instrument_config()["instruments"][
            "power_supplies"
        ]["DP8B1519"]

        self.assertNotIn("address", supply)
        self.assertFalse(supply["enabled"])

    def test_legacy_config_addresses_become_unconfirmed_bindings_for_single_devices(self):
        """只有地址的旧配置迁移成信号源/频谱仪绑定；电源地址不是身份，要求重新扫描。"""
        config = {
            "instruments": {
                "signal_generator": {"address": "TCPIP0::192.168.1.201::inst0::INSTR"},
                "spectrum_analyzer": {
                    "address": "TCPIP0::192.168.1.202::inst0::INSTR",
                    "enabled": False,
                },
                "power_supplies": {
                    "PS1": {"address": "TCPIP0::192.168.1.101::inst0::INSTR", "enabled": True},
                    "PS2": {"address": "TCPIP0::192.168.1.102::inst0::INSTR", "enabled": False},
                },
            }
        }

        bindings = effective_device_bindings(config)

        # 启用且地址非空的项被迁移；禁用项不会被迁移动作重新启用。
        self.assertEqual(
            bindings["signal_generator"]["visa_address"], "TCPIP0::192.168.1.201::inst0::INSTR"
        )
        self.assertEqual(bindings["signal_generator"]["source"], LEGACY_BINDING_SOURCE)
        self.assertNotIn("spectrum_analyzer", bindings)
        self.assertNotIn(POWER_SUPPLY_BINDINGS, bindings)
        self.assertEqual(bound_power_supplies(bindings), [])

    def test_legacy_power_bindings_migrate_by_identity_and_label(self):
        """步骤 5 的过渡绑定（按旧槽位落键、记录内带短标签与身份）折叠成 supply_id 集合。"""
        bindings = effective_device_bindings(
            {
                "device_bindings": {
                    "PS3": {
                        "role": "power_supply",
                        "label": "DP8B1519",
                        "supply_id": "DP8B1519",
                        "serial": "DP8B214901519",
                        "device_key": "rigol technologies:dp8b214901519",
                        "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                        "source": "scan",
                    },
                    "PS1": {
                        # 只有地址的旧绑定：没有序列号也没有短标签，不迁移。
                        "visa_address": "TCPIP0::192.168.1.101::inst0::INSTR",
                        "source": LEGACY_BINDING_SOURCE,
                    },
                }
            }
        )

        self.assertEqual(list(bindings[POWER_SUPPLY_BINDINGS]), ["DP8B1519"])
        record = bindings[POWER_SUPPLY_BINDINGS]["DP8B1519"]
        self.assertEqual(record["device_key"], "rigol technologies:dp8b214901519")
        # 集合的键就是引用 ID：记录里不再重复写 role/label/supply_id。
        for field in ("role", "label", "supply_id"):
            self.assertNotIn(field, record)
        self.assertEqual(bound_power_supplies(bindings), ["DP8B1519"])

    def test_explicit_empty_bindings_do_not_resurrect_legacy_addresses(self):
        """配置里已有（空的）device_bindings 时，旧地址不得再被当成绑定。"""
        config = {
            "device_bindings": {},
            "instruments": {
                "signal_generator": {"address": "TCPIP0::192.168.1.201::inst0::INSTR"}
            },
        }

        self.assertEqual(effective_device_bindings(config), {})

    def test_duplicate_power_bindings_for_one_device_keep_the_first(self):
        """同一台设备不能出现两次：手改文件造出的重复身份只保留第一条。"""
        supplies = power_supply_bindings(
            {
                POWER_SUPPLY_BINDINGS: {
                    "DP8B1519": {
                        "device_key": "rigol technologies:dp8b214901519",
                        "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                    },
                    "DP8B1519-COPY": {
                        "device_key": "RIGOL TECHNOLOGIES:DP8B214901519",
                        "visa_address": "TCPIP0::192.168.1.109::inst0::INSTR",
                    },
                }
            }
        )

        self.assertEqual(list(supplies), ["DP8B1519"])

    def test_power_assignment_must_reference_a_bound_supply(self):
        widgets = type("Widgets", (), {})()
        widgets.freq_edit = _Text("[1.0]")
        widgets.device_bindings = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                }
            }
        }
        widgets.power_config_widgets = {}
        widgets.pa_unit_count_combo = type("C", (), {"currentText": lambda self: "2"})()
        widgets.power_assignment_widgets = {
            "driver_source": type("C", (), {"currentText": lambda self: "自带供电"})(),
            "driver_power": _combo(""),
            "pa_unit1_power": _combo("DP8B1519"),
            "pa_unit2_power": _combo("PS3"),
        }

        errors = ConfigFormState({}, widgets).validation_errors()

        joined = "；".join(errors)
        self.assertIn("PS3", joined)
        self.assertIn("没有已应用的设备", joined)

    def test_a_combo_without_item_data_is_reported_as_unselected(self):
        """只有可见文本（没有 ``itemData``）的控件不能被当成已选电源。

        显示文本可能是 ``DP8B1519（DP832A）``，把它原样写成 ``supply_id`` 就等于从显示
        字符串反解析业务值；这里要求按“未选择电源”报告，交给操作员重新选择。
        """
        widgets = type("Widgets", (), {})()
        widgets.freq_edit = _Text("[1.0]")
        widgets.device_bindings = bound_supplies("DP8B1519")
        widgets.power_config_widgets = {}
        widgets.pa_unit_count_combo = _combo("1")
        widgets.power_assignment_widgets = {
            "driver_source": type("C", (), {"currentText": lambda self: "自带供电"})(),
            "pa_unit1_power": type(
                "C", (), {"currentText": lambda self: "DP8B1519（DP832A）"}
            )(),
        }

        state = ConfigFormState({}, widgets)
        errors = state.validation_errors()
        written = state.read_power_assignment()["power_supply_assignment"][
            "dut_amplifier"
        ]["supplies"]["carrier"]

        self.assertIn("未选择电源", "；".join(errors))
        self.assertIsNone(written["supply_id"])
        self.assertNotIn("name", written)

    def test_power_assignment_without_any_supply_is_reported(self):
        widgets = type("Widgets", (), {})()
        widgets.freq_edit = _Text("[1.0]")
        widgets.device_bindings = {}
        widgets.power_config_widgets = {}
        widgets.pa_unit_count_combo = type("C", (), {"currentText": lambda self: "1"})()
        widgets.power_assignment_widgets = {
            "driver_source": type("C", (), {"currentText": lambda self: "自带供电"})(),
            "driver_power": type("C", (), {"currentText": lambda self: ""})(),
            "pa_unit1_power": type("C", (), {"currentText": lambda self: ""})(),
        }

        errors = ConfigFormState({}, widgets).validation_errors()

        self.assertIn("未选择电源", "；".join(errors))

    def test_power_binding_without_identity_is_not_a_bound_supply(self):
        """新结构里的记录也必须带身份：只有地址的记录不能成为可分配电源。"""
        bindings = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {"visa_address": "TCPIP0::192.168.1.102::inst0::INSTR"}
            }
        }

        self.assertEqual(power_supply_bindings(bindings), {})
        self.assertEqual(bound_power_supplies(bindings), [])
        problems = power_supply_binding_problems(bindings)
        self.assertEqual(len(problems), 1)
        self.assertIn("DP8B1519", problems[0])
        self.assertIn("缺少设备身份", problems[0])

    def test_power_binding_identity_must_match_vendor_and_serial(self):
        """``device_key`` 与厂商/序列号矛盾时不得成为可用电源。"""
        bindings = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {
                    "device_key": "rigol technologies:dp8b174400885",
                    "vendor": "RIGOL TECHNOLOGIES",
                    "serial": "DP8B214901519",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                }
            }
        }

        self.assertEqual(power_supply_bindings(bindings), {})
        problems = power_supply_binding_problems(bindings)
        self.assertIn("设备身份与厂商/序列号不一致", "；".join(problems))

    def test_power_binding_key_must_be_a_usable_supply_id(self):
        """含空白或通道分隔符的键不能当引用：它会被 ``supply_id/CHx`` 路由截断。"""
        bindings = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B 1519": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                },
                "DP8B/1519": {
                    "device_key": "rigol technologies:dp8b174400885",
                    "visa_address": "TCPIP0::192.168.1.108::inst0::INSTR",
                },
            }
        }

        self.assertEqual(power_supply_bindings(bindings), {})
        problems = "；".join(power_supply_binding_problems(bindings))
        self.assertIn("不得含空白", problems)
        self.assertIn("不得含通道分隔符", problems)
        self.assertEqual(supply_id_problem("DP8B1519"), None)
        self.assertIn("不得含空白", supply_id_problem("DP8B 1519"))
        # 首尾空白不裁剪：写错的引用必须报出来，不能被“洗干净”后当成另一台设备。
        self.assertIn("首尾空白", supply_id_problem(" DP8B1519"))
        self.assertEqual(
            power_supply_bindings(
                {
                    POWER_SUPPLY_BINDINGS: {
                        " DP8B1519": {
                            "device_key": "rigol technologies:dp8b214901519",
                            "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                        }
                    }
                }
            ),
            {},
        )

    def test_duplicate_power_binding_is_reported_not_only_dropped(self):
        """重复身份仍然只保留第一条，但必须能说出被丢掉的是哪一条。"""
        bindings = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                },
                "DP8B1519-COPY": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "visa_address": "TCPIP0::192.168.1.109::inst0::INSTR",
                },
            }
        }

        self.assertEqual(list(power_supply_bindings(bindings)), ["DP8B1519"])
        problems = "；".join(power_supply_binding_problems(bindings))
        self.assertIn("同一台设备只能有一条电源绑定", problems)
        self.assertIn("DP8B1519-COPY", problems)

    def test_bound_supply_identity_comes_from_the_binding_not_the_config(self):
        """绑定是身份的权威来源：记录里没有的字段不能沿用配置里的旧值。"""
        widgets = type("Widgets", (), {})()
        widgets.device_bindings = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "serial": "DP8B214901519",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                }
            }
        }
        config = {
            "instruments": {
                "power_supplies": {
                    "DP8B1519": {
                        "address": "TCPIP0::192.168.1.101::inst0::INSTR",
                        "enabled": True,
                        "device_key": "rigol technologies:dp8b174400885",
                        "serial": "DP8B174400885",
                        "model": "OLD-MODEL",
                        "channels": {"CH1": {"role": "gate"}},
                    }
                }
            }
        }

        supply = ConfigFormState(config, widgets).read_instrument_config()["instruments"][
            "power_supplies"
        ]["DP8B1519"]

        self.assertEqual(supply["device_key"], "rigol technologies:dp8b214901519")
        self.assertEqual(supply["serial"], "DP8B214901519")
        self.assertEqual(supply["address"], "TCPIP0::192.168.1.102::inst0::INSTR")
        # device_key/serial 已由绑定改写，model 缺失时保留同一台电源配置里的型号：
        # 正式运行映射要求型号是快照身份的一部分（步骤 10）。
        self.assertEqual(supply["model"], "OLD-MODEL")
        # 通道参数不属于身份，原样保留。
        self.assertEqual(supply["channels"], {"CH1": {"role": "gate"}})

    def test_bound_power_supplies_follow_the_bound_collection_size(self):
        """绑定 1、2、5 台电源时，可用集合就是这 1、2、5 个 ``supply_id``。"""
        self.assertEqual(bound_power_supplies(bound_supplies("DP8B1519")), ["DP8B1519"])
        self.assertEqual(
            bound_power_supplies(bound_supplies("DP8B1519", "DP8B0885")),
            ["DP8B1519", "DP8B0885"],
        )
        five = ("DP8B1519", "DP8B0885", "DP8B0950", "DP8B1191", "DP8B1216")
        self.assertEqual(bound_power_supplies(bound_supplies(*five)), list(five))
        # 没有地址的绑定连不上，也不算可用电源；集合大小仍由绑定决定，不看静态启用标志。
        bindings = bound_supplies("DP8B1519", "DP8B0885")
        bindings[POWER_SUPPLY_BINDINGS]["DP8B0885"].pop("visa_address")
        self.assertEqual(bound_power_supplies(bindings), ["DP8B1519"])

    def test_replaced_power_supply_leaves_the_old_reference_unbound(self):
        """换电源后旧 ID 只是“没有绑定”，不会被解释成新绑定的那台设备。"""
        widgets = type("Widgets", (), {})()
        widgets.freq_edit = _Text("[1.0]")
        widgets.device_bindings = bound_supplies("DP8B0885")
        widgets.power_config_widgets = {}
        widgets.test_mode_enabled = False
        widgets.pa_unit_count_combo = _combo("1")
        widgets.power_assignment_widgets = {
            "driver_source": _combo("自带供电"),
            "pa_unit1_power": _combo("DP8B1519"),
        }

        message = "；".join(ConfigFormState({}, widgets).validation_errors())

        self.assertIn("DP8B1519", message)
        self.assertIn("没有已应用的设备", message)
        # 新绑定的那台只是在候选里，不会被当成旧引用的替代品。
        self.assertNotIn("DP8B0885", message)

    def test_assignment_reference_prefers_supply_id_over_the_legacy_name(self):
        """``supply_id`` 是主字段；只有完全没有它时才读旧字段 ``name``。"""
        self.assertEqual(assignment_supply_id({"name": "PS1"}), "PS1")
        self.assertEqual(
            assignment_supply_id({"supply_id": "DP8B1519", "name": "PS1"}), "DP8B1519"
        )
        # 出现即为准：``null`` / 空白是“引用未绑定”，不回落到 name 去猜一台电源。
        self.assertIsNone(assignment_supply_id({"supply_id": None, "name": "PS1"}))
        self.assertIsNone(assignment_supply_id({"supply_id": "  ", "name": "PS1"}))
        self.assertIsNone(assignment_supply_id({}))
        self.assertIsNone(assignment_supply_id(None))

    def test_read_power_assignment_writes_supply_id_with_the_legacy_name_alias(self):
        """引用写成 ``supply_id``，过渡期同写同名 ``name`` 供旧读取方使用。"""
        widgets = type("Widgets", (), {})()
        widgets.test_mode_enabled = True
        widgets.pa_unit_count_combo = _combo("2")
        widgets.power_assignment_widgets = {
            "driver_source": _combo("直流电源"),
            "driver_power": _combo("DP8B0950"),
            "pa_unit1_power": _combo("DP8B1519"),
            "pa_unit2_power": _combo("DP8B0885"),
        }

        assignment = ConfigFormState({}, widgets).read_power_assignment()[
            "power_supply_assignment"
        ]

        main = assignment["driver_amplifier"]["supplies"]["main"]
        self.assertEqual(main["supply_id"], "DP8B0950")
        self.assertEqual(main["name"], "DP8B0950")
        self.assertEqual(main["channel"], ["CH1", "CH2"])
        self.assertEqual(
            {
                role: entry["supply_id"]
                for role, entry in assignment["dut_amplifier"]["supplies"].items()
            },
            {"carrier": "DP8B1519", "peaking": "DP8B0885"},
        )

    def test_unbound_power_assignment_is_written_as_a_null_reference(self):
        """没有选到电源时写 ``supply_id: null``，不写 name，也不临时挑一台可用电源。"""
        widgets = type("Widgets", (), {})()
        widgets.freq_edit = _Text("[1.0]")
        widgets.device_bindings = bound_supplies("DP8B1519")
        widgets.power_config_widgets = {}
        widgets.test_mode_enabled = True
        widgets.pa_unit_count_combo = _combo("1")
        widgets.power_assignment_widgets = {
            "driver_source": _combo("直流电源"),
            "driver_power": _combo(""),
            "pa_unit1_power": _combo(""),
        }

        state = ConfigFormState({}, widgets)
        assignment = state.read_power_assignment()["power_supply_assignment"]

        main = assignment["driver_amplifier"]["supplies"]["main"]
        self.assertIsNone(main["supply_id"])
        self.assertNotIn("name", main)
        self.assertIsNone(
            assignment["dut_amplifier"]["supplies"]["carrier"]["supply_id"]
        )
        self.assertIn("未选择电源", "；".join(state.validation_errors()))

    def test_assignment_declared_separates_missing_from_unbound(self):
        """“没有这条分配”与“声明了但未绑定”必须能分开，否则回填会补一台电源。"""
        # 键缺失或不是对象：配置里没有这条分配，缺省规则适用。
        self.assertFalse(assignment_declared(None))
        self.assertFalse(assignment_declared("PS1"))
        self.assertFalse(assignment_declared(["PS1"]))
        # 记录存在就是声明过：即使引用为空，也不是“配置缺失”。
        self.assertTrue(assignment_declared({}))
        self.assertTrue(assignment_declared({"supply_id": None}))
        self.assertTrue(assignment_declared({"name": "PS1"}))
        # 两种状态都会让引用读成 None，只有 assignment_declared 能区分。
        self.assertIsNone(assignment_supply_id({"supply_id": None}))
        self.assertIsNone(assignment_supply_id(None))


if __name__ == "__main__":
    unittest.main()
