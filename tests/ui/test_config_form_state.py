"""配置表单状态适配器的测试：控件读数、配置合并与页面自己的校验说明。

设备绑定记录本身的读取与规范化（``supply_id`` 集合、旧结构折叠、不可用记录的问题
清单）在 ``test_config_form_bindings`` 里单独测试，不需要这里任何控件的 Qt 状态。
"""

import unittest

from domain.configuration.rules import assignment_supply_id
from presentation.qt.config_form_bindings import POWER_SUPPLY_BINDINGS
from presentation.qt.config_form_state import (
    ConfigFormState,
    parse_frequency_text,
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


def _form_widgets(**attributes):
    """电源相关字段之外的一组合法读数控件。

    ``formal_validation_errors`` 会按整张表单写出正式配置，因此只关心电源的测试也必须提供
    测试参数、压缩点、衰减器等读数；缺一项就是替身不完整，会让页面校验抛
    ``AttributeError``（不是要被吞掉的配置错误）。
    """
    widgets = type("Widgets", (), {})()
    widgets.freq_edit = _Text("[1.0]")
    widgets.start_power = type("V", (), {"value": lambda self: -10.0})()
    widgets.stop_power = type("V", (), {"value": lambda self: 0.0})()
    widgets.power_step = type("V", (), {"value": lambda self: 1.0})()
    widgets.max_input_power = type("V", (), {"value": lambda self: 30.0})()
    widgets.compression_combo = type("C", (), {"currentText": lambda self: "5dB"})()
    widgets.attenuator_combo = type("C", (), {"currentText": lambda self: "30dB"})()
    widgets.test_mode_enabled = False
    for name, value in attributes.items():
        setattr(widgets, name, value)
    return widgets


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
                    "full_code": "DP8B214901519",
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
        self.assertEqual(supply["full_code"], "DP8B214901519")
        self.assertEqual(supply["model"], "DP832A")
        # 没有绑定记录的角色不臆造地址，并显式写成“未启用”，避免旧地址继续被连接。
        self.assertNotIn("address", instruments["spectrum_analyzer"])
        self.assertFalse(instruments["spectrum_analyzer"]["enabled"])

    def test_signal_instrument_identity_comes_from_the_binding_record(self):
        """信号源/频谱仪的缩写码、完整设备码与设备键只从绑定记录写进配置条目。"""
        widgets = type("Widgets", (), {})()
        widgets.device_bindings = {
            "signal_generator": {
                "label": "5462",
                "model": "SMW200A",
                "full_code": "105462",
                "device_key": "rohde&schwarz:105462",
                "visa_address": "TCPIP0::192.168.1.201::inst0::INSTR",
            },
            "spectrum_analyzer": {
                "label": None,
                "model": "4082F",
                "full_code": "QZNI000866",
                "device_key": "ceyear technologies:qzni000866",
                "visa_address": "TCPIP0::192.168.1.202::inst0::INSTR",
            },
        }
        original = {
            "instruments": {
                "signal_generator": {"address": "old::sg", "label": "OLD", "model": "OLD"},
                "spectrum_analyzer": {"address": "old::sa", "label": "OLD-SA"},
            }
        }

        instruments = ConfigFormState(original, widgets).read_instrument_config()["instruments"]

        self.assertEqual(
            instruments["signal_generator"],
            {
                "label": "5462",
                "model": "SMW200A",
                "full_code": "105462",
                "device_key": "rohde&schwarz:105462",
                "enabled": True,
                "address": "TCPIP0::192.168.1.201::inst0::INSTR",
            },
        )
        # 记录声明了 label 但值为空：旧值必须删掉，不能留下“地址来自这台、身份来自上一台”。
        self.assertNotIn("label", instruments["spectrum_analyzer"])
        self.assertEqual(
            instruments["spectrum_analyzer"]["device_key"],
            "ceyear technologies:qzni000866",
        )

    def test_read_instrument_config_rewrites_every_supply_from_bindings(self):
        """两条电源绑定各写一条地址；未绑定的旧槽位条目地址与启用状态一起失效。"""
        widgets = type("Widgets", (), {})()
        widgets.device_bindings = {
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": {
                    "device_key": "rigol technologies:dp8b214901519",
                    "full_code": "DP8B214901519",
                    "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
                },
                "DP8B0885": {
                    "device_key": "rigol technologies:dp8b174400885",
                    "full_code": "DP8B174400885",
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

    def test_read_power_assignment_writes_only_plans(self):
        """??????? ``power_plan``???????????? ``supplies``?"""
        widgets = _form_widgets(
            power_config_widgets={},
            pa_unit_count_combo=_combo("1"),
            driver_power_plan_editor=_PlanEditor(
                [("DP8B0950", "CH1")]
            ),
            dut_power_plan_editor=_PlanEditor(
                [
                    ("DP8B1519", "CH1"),
                    ("DP8B0885", "CH2"),
                ]
            ),
        )

        assignment = ConfigFormState({}, widgets).read_power_assignment()[
            "power_supply_assignment"
        ]

        self.assertEqual(
            assignment["driver_amplifier"]["power_plan"]["power_sequence"],
            [{"supply_id": "DP8B0950", "channel": "CH1"}],
        )
        self.assertEqual(
            assignment["dut_amplifier"]["power_plan"]["power_sequence"],
            [
                {"supply_id": "DP8B1519", "channel": "CH1"},
                {"supply_id": "DP8B0885", "channel": "CH2"},
            ],
        )
        self.assertNotIn("supplies", assignment["dut_amplifier"])
        self.assertNotIn("power_supply_count", assignment["dut_amplifier"])

    def test_parameter_page_does_not_rewrite_the_power_section(self):
        """???????????????????????????"""
        existing = {
            "driver_amplifier": {
                "power_plan": {"power_sequence": [], "inter_channel_delay_s": 0.0}
            },
            "dut_amplifier": {
                "supplies": {"carrier": {"supply_id": "DP8B1519", "channel": ["CH1", "CH2"]}},
                "power_plan": {
                    "power_sequence": [
                        {"supply_id": "DP8B1519", "channel": "CH1"},
                        {"supply_id": "DP8B1519", "channel": "CH2"},
                    ],
                    "inter_channel_delay_s": 0.0,
                },
            },
        }
        widgets = _form_widgets(power_config_widgets={})
        self.assertEqual(ConfigFormState({"power_supply_assignment": existing}, widgets).read_power_assignment(), {})

    def test_assignment_reference_prefers_supply_id_over_the_legacy_name(self):
        """``supply_id`` ?????????????????? ``name``?"""
        self.assertEqual(assignment_supply_id({"name": "PS1"}), "PS1")
        self.assertEqual(
            assignment_supply_id({"supply_id": "DP8B1519", "name": "PS1"}), "DP8B1519"
        )
        # ??????``null`` / ??????????????? name ???????
        self.assertIsNone(assignment_supply_id({"supply_id": None, "name": "PS1"}))
        self.assertIsNone(assignment_supply_id({"supply_id": "  ", "name": "PS1"}))
        self.assertIsNone(assignment_supply_id({}))
        self.assertIsNone(assignment_supply_id(None))

    def test_assignment_reference_rule_lives_in_the_domain(self):
        """??????????? ``domain.configuration.rules.assignment_supply_id`` ?????"""
        from presentation.qt import config_form_state

        self.assertFalse(
            hasattr(config_form_state, "assignment_supply_id"),
            "?????????? 13 ????????? domain ??",
        )


class _PlanEditor:
    """计划控件替身：``read_plan`` / ``read_active_plan`` 返回同一份计划。"""

    def __init__(self, sequence=()):
        self._plan = {
            "power_sequence": [
                {"supply_id": supply_id, "channel": channel}
                for supply_id, channel in sequence
            ],
            "inter_channel_delay_s": 0.0,
        }

    def read_plan(self):
        return dict(self._plan)

    def read_active_plan(self):
        return dict(self._plan)


class _FormWidgets:
    """页面控件替身：一个 DUT 电源下拉框，其余输入控件给合法的最小值。

    正式运行校验会检查测试方案与运行映射，因此这里给的是“只有被测电源相关的字段不同”
    的一份合法表单：电源条目带 ``CH1`` 供 gate、``CH2`` 供 drain，下拉框把业务值放在
    ``itemData``（与真实控件一致）。
    """

    def __init__(self, *, test_mode_enabled, supply_id, bindings, driver_supply_id=None):
        self.freq_edit = _Text("[2.4]")
        self.start_power = type("V", (), {"value": lambda self: -10.0})()
        self.stop_power = type("V", (), {"value": lambda self: 0.0})()
        self.power_step = type("V", (), {"value": lambda self: 1.0})()
        self.max_input_power = type("V", (), {"value": lambda self: 30.0})()
        self.compression_combo = type("C", (), {"currentText": lambda self: "5dB"})()
        self.attenuator_combo = type("C", (), {"currentText": lambda self: "30dB"})()
        self.pa_unit_count_combo = _combo("1")
        self.test_mode_enabled = test_mode_enabled
        source = "直流电源" if driver_supply_id else "自带供电"
        self.driver_power_source_combo = type("C", (), {"currentText": lambda self: source})()
        self.device_bindings = bindings
        self.power_config_widgets = {
            supply_id: {
                "channels": {
                    "CH1": _channel_controls("gate"),
                    "CH2": _channel_controls("drain"),
                }
            }
        }
        # 上电计划是唯一的电源使用来源：DUT 计划覆盖两条通道；驱放计划只在给了
        # ``driver_supply_id`` 时占用同一台电源的通道。
        self.dut_power_plan_editor = _PlanEditor(
            [(supply_id, "CH1"), (supply_id, "CH2")]
        )
        self.driver_power_plan_editor = _PlanEditor(
            [(driver_supply_id, "CH1"), (driver_supply_id, "CH2")]
            if driver_supply_id
            else ()
        )


def _channel_controls(role):
    """一条通道的控件替身；供电角色是接线事实，由下拉框显式选择。"""
    return {
        "role": type("C", (), {"currentText": lambda self: role})(),
        "voltage": type("V", (), {"value": lambda self: 5.0})(),
        "voltage_protection": type("V", (), {"value": lambda self: 6.0})(),
        "voltage_protection_enabled": type("B", (), {"isChecked": lambda self: True})(),
        "current": type("V", (), {"value": lambda self: 1.0})(),
        "current_protection": type("V", (), {"value": lambda self: 1.2})(),
        "current_protection_enabled": type("B", (), {"isChecked": lambda self: True})(),
    }


def _bound(device_key, serial, model, address):
    return {
        "device_key": device_key,
        "full_code": serial,
        "model": model,
        "visa_address": address,
        "source": "scan",
    }


class PowerSupplyConnectionTests(unittest.TestCase):
    """步骤 11：页面的正式校验必须报出供电连接冲突。"""

    SG = "TCPIP0::192.168.1.201::inst0::INSTR"
    SA = "TCPIP0::192.168.1.202::inst0::INSTR"
    PS1 = "TCPIP0::192.168.1.102::inst0::INSTR"

    def _bindings(self, *, second_address=PS1):
        """信号源、频谱仪各一台，外加两台已绑定电源（默认第二台与第一台同地址）。"""
        return {
            "signal_generator": {"visa_address": self.SG, "source": "scan"},
            "spectrum_analyzer": {"visa_address": self.SA, "source": "scan"},
            POWER_SUPPLY_BINDINGS: {
                "DP8B1519": _bound(
                    "rigol technologies:dp8b214901519", "DP8B214901519", "DP832A", self.PS1
                ),
                "DP8B0885": _bound(
                    "rigol technologies:dp8b174400885", "DP8B174400885", "DP832A", second_address
                ),
            },
        }

    def test_reusing_the_same_physical_channel_is_reported_before_start(self):
        """同一物理通道同时排给 DUT 与驱动功放，页面校验必须失败。"""
        widgets = _FormWidgets(
            test_mode_enabled=True,
            supply_id="DP8B1519",
            bindings=self._bindings(second_address="TCPIP0::192.168.1.108::inst0::INSTR"),
            driver_supply_id="DP8B1519",
        )

        errors = ConfigFormState({}, widgets).formal_validation_errors()

        self.assertTrue(
            any("同一物理通道不能同时用于 DUT 与驱动功放" in error for error in errors),
            errors,
        )

    def test_two_bound_supplies_sharing_an_address_are_reported(self):
        """两台启用的电源共用同一个 VISA 地址：正式映射本身合法，硬件装配不合法。"""
        widgets = _FormWidgets(
            test_mode_enabled=False, supply_id="DP8B1519", bindings=self._bindings()
        )

        errors = ConfigFormState({}, widgets).formal_validation_errors()

        self.assertTrue(any("共用同一 VISA 地址" in error for error in errors), errors)

    def test_configuration_error_from_the_author_becomes_a_page_error(self):
        """配置作者判定“写不成正式映射”时，页面把它作为一条校验说明。"""
        from unittest import mock

        from application.errors import ConfigurationError

        widgets = _FormWidgets(
            test_mode_enabled=False,
            supply_id="DP8B1519",
            bindings=self._bindings(second_address="TCPIP0::192.168.1.108::inst0::INSTR"),
        )
        from application.configuration_authoring import ConfigurationAuthoringService

        with mock.patch.object(
            ConfigurationAuthoringService,
            "author_document",
            side_effect=ConfigurationError("缺少电源身份"),
        ):
            errors = ConfigFormState({}, widgets).formal_validation_errors()

        self.assertIn("缺少电源身份", "；".join(errors))

    def test_unexpected_failure_is_not_disguised_as_a_configuration_error(self):
        """程序缺陷不能被改写成“配置有问题”：否则页面会把 bug 报成用户的错。"""
        from unittest import mock

        widgets = _FormWidgets(
            test_mode_enabled=False,
            supply_id="DP8B1519",
            bindings=self._bindings(second_address="TCPIP0::192.168.1.108::inst0::INSTR"),
        )
        state = ConfigFormState({}, widgets)
        with mock.patch.object(
            state, "build_config", side_effect=AttributeError("start_power")
        ):
            with self.assertRaises(AttributeError):
                state.formal_validation_errors()

    def test_distinct_addresses_have_no_connection_error(self):
        widgets = _FormWidgets(
            test_mode_enabled=False,
            supply_id="DP8B1519",
            bindings=self._bindings(second_address="TCPIP0::192.168.1.108::inst0::INSTR"),
        )

        errors = ConfigFormState({}, widgets).formal_validation_errors()

        self.assertFalse([error for error in errors if "VISA 地址" in error], errors)


if __name__ == "__main__":
    unittest.main()
