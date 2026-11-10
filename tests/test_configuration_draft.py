"""配置编辑草稿与结构化结果的纯 Python 测试（GUI 重构步骤 3）。

这些测试不启动 ``QApplication``，也不经过配置页控件：只构造/比较草稿，检查
``from_document`` / ``to_document`` 的字段形状，以及非法输入是否返回结构化问题而不是
悄悄挑选默认设备。
"""

import copy
import json
import unittest
from dataclasses import replace
from pathlib import Path

from application.configuration_draft import (
    ConfigurationDraft,
    ConfigurationDraftResult,
    DeviceBindingDraft,
    DeviceBindingsDraft,
    PowerChannelDraft,
    PowerSupplyDraft,
)
from application.configuration_editing import ValidationIssue, ValidationResult
from application.dto.amplifier_inputs import (
    MAX_INTER_CHANNEL_DELAY_S,
    PowerPlan,
    PowerSweep,
    SupplyChannel,
)
from domain.measurement.values import Decibels, Frequency, PowerDbm


def _binding(**overrides):
    record = {
        "vendor": "rigol technologies",
        "model": "DP832A",
        "label": "PSA",
        "full_code": "DP8B214901519",
        "device_key": "rigol technologies:dp8b214901519",
        "visa_address": "TCPIP0::192.168.1.102::inst0::INSTR",
        "last_ip": "192.168.1.102",
        "last_port": 5025,
        "known": True,
        "source": "scan",
    }
    record.update(overrides)
    return record


def _instrument_entry(address, model, device_key):
    return {
        "enabled": True,
        "address": address,
        "model": model,
        "label": device_key.rsplit(":", 1)[-1].upper(),
        "full_code": device_key.rsplit(":", 1)[-1].upper(),
        "device_key": device_key,
    }


def _document():
    """一份配置页当前写出的典型文档：两台设备角色 + 一台电源 + DUT/驱动分配。"""
    return {
        "schema_version": "1.0",
        "test_frequencies": [2.4, 5.8],
        "signal_source": {"start_power": -38.0, "stop_power": -16.0, "step": 1.0},
        "compression_point": {"type": "5dB"},
        "attenuator": {"type": "40dB"},
        "driver_mode": {"enabled": True},
        "dut_config": {"max_input_power": 33.5, "power_supply_count": 1},
        "instruments": {
            "signal_generator": _instrument_entry(
                "TCPIP0::192.168.1.201::inst0::INSTR",
                "SMW200A",
                "rohde&schwarz:105462",
            ),
            "spectrum_analyzer": _instrument_entry(
                "TCPIP0::192.168.1.202::inst0::INSTR",
                "4082F",
                "ceyear technologies:qzni000866",
            ),
            "power_supplies": {
                "PSA": {
                    **_binding(),
                    "address": "TCPIP0::192.168.1.102::inst0::INSTR",
                    "enabled": True,
                    "channels": {
                        "CH1": {
                            "role": "gate",
                            "voltage": {
                                "value": 2.8,
                                "protection": 3.0,
                                "protection_enabled": True,
                            },
                            "current": {
                                "value": 0.1,
                                "protection": 0.2,
                                "protection_enabled": True,
                            },
                        },
                        "CH2": {
                            "role": "drain",
                            "voltage": {
                                "value": 28.0,
                                "protection": 30.0,
                                "protection_enabled": True,
                            },
                            "current": {
                                "value": 2.0,
                                "protection": 2.5,
                                "protection_enabled": True,
                            },
                        },
                    },
                }
            },
        },
        "power_supply_assignment": {
            "driver_amplifier": {
                "power_plan": {
                    "power_sequence": [{"supply_id": "PSA", "channel": "CH1"}],
                    "inter_channel_delay_s": 0.0,
                }
            },
            "dut_amplifier": {
                "power_plan": {
                    "power_sequence": [
                        {"supply_id": "PSA", "channel": "CH1"},
                        {"supply_id": "PSA", "channel": "CH2"},
                    ],
                    "inter_channel_delay_s": 0.0,
                }
            },
        },
        "device_bindings": {
            "signal_generator": {
                "device_key": "rohde&schwarz:105462",
                "visa_address": "TCPIP0::192.168.1.201::inst0::INSTR",
                "model": "SMW200A",
                "known": True,
                "source": "scan",
            },
            "spectrum_analyzer": {
                "device_key": "ceyear technologies:qzni000866",
                "visa_address": "TCPIP0::192.168.1.202::inst0::INSTR",
                "model": "4082F",
                "known": True,
                "source": "scan",
            },
            "power_supplies": {"PSA": _binding()},
        },
        # 正式段不属于草稿，应当被忽略；保留它们确认读取不依赖正式输出。
        "test_plan": {"schema_version": "1.0"},
        "run_mapping": {"schema_version": "1.0"},
    }


class ConfigurationDraftModelTests(unittest.TestCase):
    def test_draft_can_be_constructed_copied_and_compared(self):
        draft = ConfigurationDraft(
            frequencies=(Frequency.from_ghz(2.4), Frequency.from_ghz(5.8)),
            sweep=PowerSweep(
                start=PowerDbm(-38.0),
                stop=PowerDbm(-16.0),
                step=Decibels(1.0),
            ),
            compression_target=Decibels(5.0),
            attenuator=Decibels(40.0),
            max_dut_input_power=PowerDbm(33.5),
            driver_enabled=True,
            dut_supply_count=1,
            dut_power_plan=PowerPlan(
                power_sequence=(
                    SupplyChannel("PSA", "CH1"),
                    SupplyChannel("PSA", "CH2"),
                )
            ),
            power_supplies=(
                PowerSupplyDraft(
                    supply_id="PSA",
                    channels=(
                        PowerChannelDraft(channel="CH1", voltage=2.8),
                        PowerChannelDraft(channel="CH2", voltage=28.0),
                    ),
                ),
            ),
            bindings=DeviceBindingsDraft(
                power_supply_bindings=(("PSA", DeviceBindingDraft(visa_address="sim::ps")),)
            ),
        )

        self.assertEqual(copy.copy(draft), draft)
        self.assertEqual(copy.deepcopy(draft), draft)
        self.assertEqual(replace(draft, driver_enabled=False).driver_enabled, False)
        self.assertIsInstance(draft.frequencies, tuple)
        self.assertIsInstance(draft.dut_power_plan, PowerPlan)
        self.assertEqual(
            draft.dut_channels,
            (SupplyChannel("PSA", "CH1"), SupplyChannel("PSA", "CH2")),
        )
        self.assertEqual(
            dict(draft.bindings.power_supplies)["PSA"].visa_address, "sim::ps"
        )

    def test_type_and_uniqueness_checks_reject_programmer_errors(self):
        with self.assertRaises(TypeError):
            ConfigurationDraft(frequencies=(1.0,))  # 裸数值不是 Frequency
        # 旧配置可能记录多于页面下拉框的电源数量：草稿只做结构校验，不改写旧值。
        self.assertEqual(
            ConfigurationDraft(dut_supply_count=5).dut_supply_count, 5
        )
        with self.assertRaises(ValueError):
            ConfigurationDraft(
                power_supplies=(
                    PowerSupplyDraft("PSA"),
                    PowerSupplyDraft("PSA"),
                )
            )
        with self.assertRaises(ValueError):
            DeviceBindingsDraft(
                power_supply_bindings=(
                    ("PSA", DeviceBindingDraft()),
                    ("PSA", DeviceBindingDraft()),
                )
            )

    def test_programmer_errors_are_rejected_without_defaults(self):
        with self.assertRaises(TypeError):
            ConfigurationDraft(driver_enabled="yes")
        with self.assertRaises(TypeError):
            PowerChannelDraft(channel="CH1", voltage_protection_enabled=1)
        with self.assertRaises(ValueError):
            PowerChannelDraft(
                channel="CH1", power_on_delay_s=MAX_INTER_CHANNEL_DELAY_S + 1
            )
        with self.assertRaises(ValueError):
            PowerChannelDraft(channel="CH1", power_on_delay_s=-1)
        # 直接构造与文档读取对数值有效性的要求一致：非有限值不能进草稿。
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                PowerChannelDraft(channel="CH1", voltage=value)
            with self.assertRaises(ValueError):
                PowerChannelDraft(channel="CH1", current_protection=value)


class ConfigurationDraftDocumentTests(unittest.TestCase):
    def test_from_document_reads_typed_fields(self):
        result = ConfigurationDraft.from_document(_document())

        self.assertTrue(result.valid, result.messages())
        draft = result.draft
        self.assertEqual(
            draft.frequencies,
            (Frequency.from_ghz(2.4), Frequency.from_ghz(5.8)),
        )
        self.assertEqual(
            draft.sweep,
            PowerSweep(
                start=PowerDbm(-38.0),
                stop=PowerDbm(-16.0),
                step=Decibels(1.0),
            ),
        )
        self.assertEqual(draft.compression_target, Decibels(5.0))
        self.assertEqual(draft.attenuator, Decibels(40.0))
        self.assertEqual(draft.max_dut_input_power, PowerDbm(33.5))
        self.assertTrue(draft.driver_enabled)
        self.assertEqual(draft.dut_supply_count, 1)
        self.assertEqual(
            draft.dut_channels,
            (SupplyChannel("PSA", "CH1"), SupplyChannel("PSA", "CH2")),
        )
        self.assertEqual(draft.driver_channels, (SupplyChannel("PSA", "CH1"),))
        self.assertEqual(draft.power_supplies[0].channel("CH1").voltage, 2.8)
        self.assertEqual(
            draft.bindings.power_supplies["PSA"].device_key,
            "rigol technologies:dp8b214901519",
        )

    def test_multiple_drains_round_trip_as_a_route_list(self):
        document = _document()
        document["dut_config"]["drain_channel"] = [
            {"supply_id": "PSA", "channel": "CH1"},
            {"supply_id": "PSA", "channel": "CH2"},
        ]

        result = ConfigurationDraft.from_document(document)

        self.assertTrue(result.valid, result.messages())
        self.assertEqual(
            result.draft.drain_channels,
            (SupplyChannel("PSA", "CH1"), SupplyChannel("PSA", "CH2")),
        )
        self.assertEqual(
            result.draft.to_document()["dut_config"]["drain_channel"],
            document["dut_config"]["drain_channel"],
        )

    def test_multiple_power_scan_plans_round_trip_in_order(self):
        document = _document()
        plans = [
            {"start_power": -38.0, "step": 1.0, "stop_power": -10.0},
            {"start_power": -10.0, "step": 0.25, "stop_power": -2.0},
        ]
        document["signal_source"] = {"power_scan_plans": plans}

        result = ConfigurationDraft.from_document(document)

        self.assertTrue(result.valid, result.messages())
        self.assertEqual(len(result.draft.sweeps), 2)
        self.assertIsNone(result.draft.sweep)
        self.assertEqual(result.draft.to_document()["signal_source"]["power_scan_plans"], plans)

    def test_plan_and_scalar_scan_fields_together_are_reported(self):
        document = _document()
        document["signal_source"]["power_scan_plans"] = [
            {"start_power": -38.0, "step": 1.0, "stop_power": -16.0}
        ]

        result = ConfigurationDraft.from_document(document)

        self.assertFalse(result.valid)
        self.assertIn("configuration.invalid_sweep", {issue.code for issue in result.issues})
        self.assertEqual(result.draft.sweeps, ())

    def test_empty_plan_list_is_reported_instead_of_defaulted(self):
        document = _document()
        document["signal_source"] = {"power_scan_plans": []}

        result = ConfigurationDraft.from_document(document)

        self.assertFalse(result.valid)
        self.assertEqual(result.draft.sweeps, ())
        self.assertIsNone(result.draft.to_document()["signal_source"]["power_scan_plans"])

    def test_explicit_null_plan_is_reported_instead_of_falling_back(self):
        """显式 ``null`` 是计划缺失：不能退回旧标量，也不能伪造一段。"""
        document = _document()
        document["signal_source"] = {
            "power_scan_plans": None,
            "start_power": -38.0,
            "stop_power": -16.0,
            "step": 1.0,
        }

        result = ConfigurationDraft.from_document(document)

        self.assertFalse(result.valid)
        self.assertEqual(result.draft.sweeps, ())
        self.assertIn(
            "signal_source",
            {issue.path for issue in result.issues},
        )

    def test_explicit_null_plan_alone_is_reported_too(self):
        document = _document()
        document["signal_source"] = {"power_scan_plans": None}

        result = ConfigurationDraft.from_document(document)

        self.assertFalse(result.valid)
        self.assertEqual(result.draft.sweeps, ())
        self.assertIn(
            "signal_source.power_scan_plans",
            {issue.path for issue in result.issues},
        )

    def test_invalid_plan_element_reports_its_index(self):
        document = _document()
        document["signal_source"] = {
            "power_scan_plans": [
                {"start_power": -38.0, "step": 1.0, "stop_power": -16.0},
                "not-a-plan",
            ]
        }

        result = ConfigurationDraft.from_document(document)

        self.assertIn(
            "signal_source.power_scan_plans[1]",
            {issue.path for issue in result.issues},
        )

    def test_plan_missing_required_fields_reports_each_key(self):
        """缺键必须在读取边界报出，不能在没有提示的情况下丢掉一段计划。"""
        document = _document()
        document["signal_source"] = {
            "power_scan_plans": [
                {"start_power": -38.0, "step": 1.0, "stop_power": -16.0},
                {"start_power": -16.0},
            ]
        }

        result = ConfigurationDraft.from_document(document)

        self.assertFalse(result.valid)
        paths = {issue.path for issue in result.issues}
        self.assertIn("signal_source.power_scan_plans[1].step", paths)
        self.assertIn("signal_source.power_scan_plans[1].stop_power", paths)
        # 合法段仍然可编辑，非法段不进入草稿。
        self.assertEqual(len(result.draft.sweeps), 1)

    def test_document_round_trip_keeps_current_key_shape(self):
        first = ConfigurationDraft.from_document(_document())
        document = first.draft.to_document()
        second = ConfigurationDraft.from_document(document)

        self.assertTrue(second.valid, second.messages())
        # 写出时补上新功率语义标记；草稿其余字段原样往返。
        self.assertEqual(
            second.draft,
            replace(first.draft, power_reference="dut_input"),
        )
        self.assertEqual(document["test_frequencies"], [2.4, 5.8])
        self.assertEqual(
            document["signal_source"],
            {
                "power_scan_plans": [
                    {"start_power": -38.0, "step": 1.0, "stop_power": -16.0}
                ],
                "power_reference": "dut_input",
            },
        )
        self.assertEqual(document["compression_point"], {"type": "5dB"})
        self.assertEqual(document["attenuator"], {"type": "40dB"})
        self.assertEqual(
            document["driver_mode"],
            {"enabled": True, "estimated_gain_db": None},
        )
        self.assertEqual(
            document["dut_config"],
            {"max_input_power": 33.5, "power_supply_count": 1},
        )
        self.assertEqual(
            document["power_supply_assignment"]["dut_amplifier"]["power_plan"][
                "power_sequence"
            ],
            [
                {"supply_id": "PSA", "channel": "CH1"},
                {"supply_id": "PSA", "channel": "CH2"},
            ],
        )
        self.assertEqual(
            document["power_supply_assignment"]["driver_amplifier"]["power_plan"][
                "power_sequence"
            ],
            [{"supply_id": "PSA", "channel": "CH1"}],
        )
        # 旧文档的通道 ``role`` 不进入草稿，也不重新写出；数值字段原样保留。
        channels = document["instruments"]["power_supplies"]["PSA"]["channels"]
        self.assertEqual(
            channels["CH1"],
            {
                "voltage": {
                    "value": 2.8,
                    "protection": 3.0,
                    "protection_enabled": True,
                },
                "current": {
                    "value": 0.1,
                    "protection": 0.2,
                    "protection_enabled": True,
                },
            },
        )
        self.assertEqual(channels["CH2"]["voltage"]["value"], 28.0)
        self.assertIn("device_bindings", document)
        # ``to_document`` 只输出编辑段；正式段与 schema 版本由配置编写服务补。
        for formal in ("test_plan", "run_mapping", "schema_version", "wiring"):
            self.assertNotIn(formal, document)

    def test_channel_power_on_delay_round_trips(self):
        document = _document()
        document["instruments"]["power_supplies"]["PSA"]["channels"]["CH1"][
            "power_on_delay_s"
        ] = 4.5

        result = ConfigurationDraft.from_document(document)

        self.assertTrue(result.valid, result.messages())
        self.assertEqual(
            result.draft.power_supplies[0].channel("CH1").power_on_delay_s, 4.5
        )
        written = result.draft.to_document()["instruments"]["power_supplies"][
            "PSA"
        ]["channels"]["CH1"]
        self.assertEqual(written["power_on_delay_s"], 4.5)

    def test_invalid_channel_power_on_delay_is_reported_and_dropped(self):
        for bad in ("2", None, -1, MAX_INTER_CHANNEL_DELAY_S + 1, True):
            with self.subTest(delay=bad):
                document = _document()
                if bad is None:
                    # 缺失不是错误：旧配置没有这个字段时保持不写。
                    document["instruments"]["power_supplies"]["PSA"][
                        "channels"
                    ]["CH1"].pop("power_on_delay_s", None)
                    result = ConfigurationDraft.from_document(document)
                    self.assertTrue(result.valid, result.messages())
                    self.assertIsNone(
                        result.draft.power_supplies[0]
                        .channel("CH1")
                        .power_on_delay_s
                    )
                    continue
                document["instruments"]["power_supplies"]["PSA"]["channels"][
                    "CH1"
                ]["power_on_delay_s"] = bad
                result = ConfigurationDraft.from_document(document)
                self.assertFalse(result.valid)
                self.assertIsNone(
                    result.draft.power_supplies[0].channel("CH1").power_on_delay_s
                )
                written = result.draft.to_document()["instruments"][
                    "power_supplies"
                ]["PSA"]["channels"]["CH1"]
                self.assertNotIn("power_on_delay_s", written)

    def test_retired_driver_power_source_is_ignored_and_dropped(self):
        """旧 1.1 文档的 ``power_source`` 不再读入，保存时也不再写出。"""
        document = _document()
        document["driver_mode"] = {"enabled": True, "power_source": "external_ac"}

        result = ConfigurationDraft.from_document(document)

        self.assertTrue(result.valid, result.messages())
        self.assertTrue(result.draft.driver_enabled)
        self.assertNotIn("power_source", result.draft.to_document()["driver_mode"])

    def test_legacy_channel_role_is_ignored_without_touching_settings(self):
        """旧通道 ``role`` 读入即忽略，写出不再出现；工作值/保护值原样保留。"""
        document = {
            "instruments": {
                "power_supplies": {
                    "PSA": {
                        "channels": {
                            "CH1": {
                                "role": "gate",
                                "voltage": {
                                    "value": 2.8,
                                    "protection": 3.0,
                                    "protection_enabled": True,
                                },
                                "current": {
                                    "value": 0.1,
                                    "protection": 0.2,
                                    "protection_enabled": False,
                                },
                            }
                        }
                    }
                }
            }
        }

        result = ConfigurationDraft.from_document(document)

        self.assertTrue(result.valid, result.messages())
        channel = result.draft.power_supplies[0].channel("CH1")
        self.assertFalse(hasattr(channel, "role"))
        self.assertEqual(
            channel,
            PowerChannelDraft(
                channel="CH1",
                voltage=2.8,
                voltage_protection=3.0,
                voltage_protection_enabled=True,
                current=0.1,
                current_protection=0.2,
                current_protection_enabled=False,
            ),
        )
        written = result.draft.to_document()["instruments"]["power_supplies"]["PSA"]
        self.assertNotIn("role", written["channels"]["CH1"])
        self.assertEqual(
            written["channels"]["CH1"],
            {
                "voltage": {
                    "value": 2.8,
                    "protection": 3.0,
                    "protection_enabled": True,
                },
                "current": {
                    "value": 0.1,
                    "protection": 0.2,
                    "protection_enabled": False,
                },
            },
        )

    def test_missing_sections_stay_explicitly_empty_without_issues(self):
        result = ConfigurationDraft.from_document({})

        self.assertTrue(result.valid, result.messages())
        self.assertEqual(result.draft.frequencies, ())
        self.assertIsNone(result.draft.sweep)
        self.assertIsNone(result.draft.compression_target)
        self.assertIsNone(result.draft.attenuator)
        self.assertIsNone(result.draft.max_dut_input_power)
        self.assertIsNone(result.draft.dut_supply_count)
        self.assertIsNone(result.draft.driver_enabled)
        self.assertEqual(result.draft.dut_channels, ())
        self.assertEqual(result.draft.power_supplies, ())
        self.assertTrue(result.draft.bindings.is_empty())

        document = result.draft.to_document()
        self.assertEqual(document["test_frequencies"], [])
        self.assertEqual(
            document["signal_source"],
            {
                "power_scan_plans": None,
                "power_reference": "dut_input",
            },
        )
        self.assertEqual(document["compression_point"], {"type": None})
        self.assertEqual(document["attenuator"], {"type": None})
        self.assertIsNone(document["dut_config"]["max_input_power"])

    def test_invalid_fields_return_issues_instead_of_defaults(self):
        document = {
            "test_frequencies": ["bad", 2.4],
            "signal_source": {"start_power": -10.0, "stop_power": 0.0, "step": 0},
            "compression_point": {"type": "bad"},
            "attenuator": {"type": 40},
            "dut_config": {"max_input_power": "30", "power_supply_count": "5"},
            "driver_mode": {"enabled": "yes"},
            "instruments": {
                "power_supplies": {
                    "PSA": {
                        "channels": {
                            "CH1": {"role": 1, "voltage": {"value": "2"}}
                        }
                    }
                }
            },
            "power_supply_assignment": {
                "dut_amplifier": {
                    "power_plan": {"power_sequence": []}
                }
            },
        }
        result = ConfigurationDraft.from_document(document)

        self.assertFalse(result.valid)
        by_code = {issue.code for issue in result.issues}
        self.assertIn("configuration.invalid_frequency", by_code)
        self.assertIn("configuration.invalid_sweep", by_code)
        self.assertIn("configuration.invalid_decibels", by_code)
        self.assertIn("configuration.invalid_type", by_code)
        self.assertIn("configuration.invalid_number", by_code)
        self.assertIn("configuration.invalid_dut_supply_count", by_code)
        self.assertIn("configuration.invalid_power_plan", by_code)

        # 非法值保持缺失状态，不用缺省测试频率或默认设备补齐。
        self.assertEqual(result.draft.frequencies, (Frequency.from_ghz(2.4),))
        self.assertIsNone(result.draft.sweep)
        self.assertIsNone(result.draft.compression_target)
        self.assertIsNone(result.draft.attenuator)
        self.assertIsNone(result.draft.max_dut_input_power)
        self.assertIsNone(result.draft.dut_supply_count)
        self.assertIsNone(result.draft.driver_enabled)
        self.assertEqual(result.draft.dut_channels, ())
        channel = result.draft.power_supplies[0].channel("CH1")
        self.assertIsNone(channel.voltage)
        # 旧字段 ``role``（这里还是非法类型）按契约忽略，不产生类型问题。
        self.assertFalse(
            any(issue.path.endswith(".role") for issue in result.issues)
        )

    def test_missing_booleans_stay_unset_instead_of_false(self):
        result = ConfigurationDraft.from_document(
            {
                "instruments": {
                    "power_supplies": {
                        "PSA": {
                            "channels": {
                                "CH1": {"voltage": {"value": 2.8}},
                            }
                        }
                    }
                }
            }
        )

        self.assertTrue(result.valid, result.messages())
        channel = result.draft.power_supplies[0].channel("CH1")
        self.assertIsNone(result.draft.driver_enabled)
        self.assertIsNone(channel.voltage_protection_enabled)
        document = result.draft.to_document()
        self.assertIsNone(document["driver_mode"]["enabled"])
        # 缺失状态可往返：再次读取不会被折叠成“明确关闭”。
        again = ConfigurationDraft.from_document(document).draft
        self.assertIsNone(again.driver_enabled)
        self.assertIsNone(
            again.power_supplies[0].channel("CH1").voltage_protection_enabled
        )

        explicit = ConfigurationDraft.from_document(
            {
                "driver_mode": {"enabled": False},
                "instruments": {
                    "power_supplies": {
                        "PSA": {
                            "channels": {
                                "CH1": {
                                    "voltage": {
                                        "value": 2.8,
                                        "protection_enabled": False,
                                    }
                                }
                            }
                        }
                    }
                },
            }
        )
        self.assertIs(explicit.draft.driver_enabled, False)
        explicit_channel = explicit.draft.power_supplies[0].channel("CH1")
        self.assertIs(explicit_channel.voltage_protection_enabled, False)

    def test_dut_supply_count_rejects_non_integer_types(self):
        for raw in (5.0, True, "5", [5]):
            result = ConfigurationDraft.from_document(
                {"dut_config": {"power_supply_count": raw}}
            )

            self.assertFalse(result.valid, raw)
            self.assertIn(
                "configuration.invalid_dut_supply_count",
                {issue.code for issue in result.issues},
                raw,
            )
            self.assertIsNone(result.draft.dut_supply_count)

    def test_plan_of_wrong_type_is_reported(self):
        document = {
            "power_supply_assignment": {
                "dut_amplifier": {
                    "power_plan": {"power_sequence": "CH1", "inter_channel_delay_s": 0.0}
                }
            }
        }
        result = ConfigurationDraft.from_document(document)

        self.assertFalse(result.valid)
        issue = next(
            issue
            for issue in result.issues
            if issue.code == "configuration.invalid_power_plan"
        )
        self.assertEqual(
            issue.path, "power_supply_assignment.dut_amplifier.power_plan"
        )
        self.assertIsNone(result.draft.dut_power_plan)
        self.assertEqual(result.draft.dut_channels, ())

    def test_plan_round_trips_as_the_only_power_usage_source(self):
        document = {
            "power_supply_assignment": {
                "dut_amplifier": {
                    "power_plan": {
                        "power_sequence": [{"supply_id": "PSA", "channel": "CH1"}],
                        "inter_channel_delay_s": 0.0,
                    }
                }
            }
        }
        result = ConfigurationDraft.from_document(document)

        self.assertTrue(result.valid, result.messages())
        self.assertEqual(
            result.draft.dut_channels, (SupplyChannel("PSA", "CH1"),)
        )
        document_out = result.draft.to_document()
        self.assertEqual(
            document_out["power_supply_assignment"]["dut_amplifier"]["power_plan"][
                "power_sequence"
            ],
            [{"supply_id": "PSA", "channel": "CH1"}],
        )
        self.assertNotIn(
            "supplies",
            document_out["power_supply_assignment"]["dut_amplifier"],
        )

    def test_existing_five_supply_fixture_is_read_without_rewriting_its_count(self):
        """旧配置记录的电源数量可以大于页面下拉框范围；草稿保留原值，不套用页面默认值。"""
        path = (
            Path(__file__).parents[1]
            / "tests"
            / "fixtures"
            / "power_supply_identity"
            / "config_five_supplies.json"
        )
        document = json.loads(path.read_text(encoding="utf-8"))
        result = ConfigurationDraft.from_document(document)

        self.assertTrue(result.valid, result.messages())
        self.assertEqual(result.draft.dut_supply_count, 5)
        self.assertEqual(len(result.draft.power_supplies), 5)
        self.assertEqual(
            result.draft.dut_channels,
            (SupplyChannel("DP8B1519", "CH1"), SupplyChannel("DP8B0885", "CH2")),
        )

    def test_legacy_binding_slots_are_reported_not_folded(self):
        document = {"device_bindings": {"PS1": {"device_key": "rigol:abc"}}}
        result = ConfigurationDraft.from_document(document)

        self.assertTrue(result.valid, "旧结构是警告，不应让转换结果无效")
        self.assertTrue(
            any(
                issue.code == "configuration.legacy_binding"
                and issue.severity == "warning"
                and issue.path == "device_bindings.PS1"
                for issue in result.issues
            ),
            result.messages(),
        )
        self.assertTrue(result.draft.bindings.is_empty())

    def test_power_reference_and_driver_gain_round_trip(self):
        document = _document()
        document["signal_source"]["power_reference"] = "dut_input"
        document["driver_mode"]["estimated_gain_db"] = 20.5

        result = ConfigurationDraft.from_document(document)

        self.assertTrue(result.valid, result.messages())
        self.assertEqual(result.draft.power_reference, "dut_input")
        self.assertEqual(result.draft.driver_gain_estimate, Decibels(20.5))
        written = result.draft.to_document()
        self.assertEqual(written["signal_source"]["power_reference"], "dut_input")
        self.assertEqual(written["driver_mode"]["estimated_gain_db"], 20.5)

    def test_unknown_power_reference_is_reported_and_not_reinterpreted(self):
        document = _document()
        document["signal_source"]["power_reference"] = "source_output"

        result = ConfigurationDraft.from_document(document)

        self.assertFalse(result.valid)
        self.assertIn(
            "signal_source.power_reference",
            {issue.path for issue in result.errors},
        )
        self.assertIsNone(result.draft.power_reference)
        self.assertIsNone(result.draft.sweep)

    def test_invalid_driver_gain_is_reported_and_dropped(self):
        for value in ("20dB", float("nan"), True):
            with self.subTest(value=value):
                document = _document()
                document["driver_mode"]["estimated_gain_db"] = value
                result = ConfigurationDraft.from_document(document)

                self.assertFalse(result.valid, value)
                self.assertIn(
                    "driver_mode.estimated_gain_db",
                    {issue.path for issue in result.errors},
                )
                self.assertIsNone(result.draft.driver_gain_estimate)

    def test_caller_can_supply_already_normalized_bindings(self):
        bindings = {
            "signal_generator": {"device_key": "vendor:sg", "visa_address": "sim::sg"},
            "power_supplies": {"PSA": _binding()},
        }
        result = ConfigurationDraft.from_document({}, bindings=bindings)

        self.assertTrue(result.valid, result.messages())
        self.assertEqual(result.draft.bindings.signal_generator.device_key, "vendor:sg")
        self.assertEqual(
            result.draft.bindings.power_supply("PSA").visa_address,
            "TCPIP0::192.168.1.102::inst0::INSTR",
        )

    def test_supply_without_binding_is_written_disabled(self):
        draft = ConfigurationDraft(
            power_supplies=(
                PowerSupplyDraft(
                    supply_id="PSA",
                    channels=(PowerChannelDraft(channel="CH1"),),
                ),
            )
        )
        entry = draft.to_document()["instruments"]["power_supplies"]["PSA"]

        self.assertFalse(entry["enabled"])
        self.assertNotIn("address", entry)
        self.assertEqual(entry["label"], "PSA")
        self.assertIn("CH1", entry["channels"])


class ValidationResultTests(unittest.TestCase):
    def test_errors_and_warnings_are_distinguished(self):
        result = ValidationResult(
            issues=[
                ValidationIssue("test_frequencies", "empty", "测试频率不能为空"),
                ValidationIssue(
                    "attenuator.type",
                    "legacy_text",
                    "请重新保存为 dB 文本",
                    severity="warning",
                ),
            ]
        )

        self.assertFalse(result.valid)
        self.assertEqual(len(result.errors), 1)
        self.assertEqual(len(result.warnings), 1)
        self.assertEqual(
            result.messages(),
            (
                "test_frequencies: 测试频率不能为空",
                "attenuator.type: 请重新保存为 dB 文本",
            ),
        )
        self.assertTrue(ValidationResult().valid)

    def test_issue_and_result_snapshots_are_immutable(self):
        issue = ValidationIssue("x", "code", "说明")
        result = ValidationResult(issues=[issue])

        self.assertIsInstance(result.issues, tuple)
        with self.assertRaises(TypeError):
            ValidationIssue(path=1, code="code", message="说明")
        with self.assertRaises(ValueError):
            ValidationIssue(path="x", code="code", message="说明", severity="fatal")
        with self.assertRaises(TypeError):
            ValidationResult(issues=["not an issue"])

    def test_draft_result_exposes_the_conversion_problems(self):
        draft = ConfigurationDraft()
        result = ConfigurationDraftResult(
            draft=draft,
            validation=ValidationResult(
                issues=[
                    ValidationIssue("x", "code", "说明", severity="warning"),
                ]
            ),
        )

        self.assertTrue(result.valid)
        self.assertEqual(len(result.issues), 1)
        self.assertEqual(result.messages(), ("x: 说明",))


if __name__ == "__main__":
    unittest.main()
