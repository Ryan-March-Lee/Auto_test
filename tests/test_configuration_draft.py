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
    PowerAssignmentDraft,
    PowerChannelDraft,
    PowerSupplyDraft,
)
from application.configuration_editing import ValidationIssue, ValidationResult
from application.dto.amplifier_inputs import PowerSweep, SupplyChannel
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
        "driver_mode": {"enabled": True, "power_source": "dc_supply"},
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
                "power_supply_count": 1,
                "supplies": {
                    "main": {
                        "supply_id": "PSA",
                        "name": "PSA",
                        "channel": ["CH1"],
                    }
                },
            },
            "dut_amplifier": {
                "power_supply_count": 1,
                "supplies": {
                    "carrier": {
                        "supply_id": "PSA",
                        "name": "PSA",
                        "channel": ["CH1", "CH2"],
                    }
                },
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
            driver_power_source="dc_supply",
            dut_supply_count=1,
            dut_assignments=(
                PowerAssignmentDraft(
                    slot="carrier", supply_id="PSA", channels=("CH1", "CH2")
                ),
            ),
            power_supplies=(
                PowerSupplyDraft(
                    supply_id="PSA",
                    channels=(
                        PowerChannelDraft(channel="CH1", role="gate", voltage=2.8),
                        PowerChannelDraft(channel="CH2", role="drain", voltage=28.0),
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
        self.assertIsInstance(draft.dut_assignments, tuple)
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
            ConfigurationDraft(
                dut_assignments=(
                    PowerAssignmentDraft("carrier"),
                    PowerAssignmentDraft("carrier"),
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
        self.assertEqual(draft.driver_power_source, "dc_supply")
        self.assertEqual(draft.dut_supply_count, 1)
        self.assertEqual(
            draft.dut_channels,
            (SupplyChannel("PSA", "CH1"), SupplyChannel("PSA", "CH2")),
        )
        self.assertEqual(draft.driver_channels, (SupplyChannel("PSA", "CH1"),))
        self.assertEqual(draft.power_supplies[0].channel("CH1").role, "gate")
        self.assertEqual(
            draft.bindings.power_supplies["PSA"].device_key,
            "rigol technologies:dp8b214901519",
        )

    def test_document_round_trip_keeps_current_key_shape(self):
        first = ConfigurationDraft.from_document(_document())
        document = first.draft.to_document()
        second = ConfigurationDraft.from_document(document)

        self.assertTrue(second.valid, second.messages())
        self.assertEqual(second.draft, first.draft)
        self.assertEqual(document["test_frequencies"], [2.4, 5.8])
        self.assertEqual(
            document["signal_source"],
            {"start_power": -38.0, "stop_power": -16.0, "step": 1.0},
        )
        self.assertEqual(document["compression_point"], {"type": "5dB"})
        self.assertEqual(document["attenuator"], {"type": "40dB"})
        self.assertEqual(document["driver_mode"]["power_source"], "dc_supply")
        self.assertEqual(
            document["dut_config"],
            {"max_input_power": 33.5, "power_supply_count": 1},
        )
        self.assertEqual(
            document["power_supply_assignment"]["dut_amplifier"]["supplies"][
                "carrier"
            ],
            {"supply_id": "PSA", "name": "PSA", "channel": ["CH1", "CH2"]},
        )
        self.assertEqual(
            document["power_supply_assignment"]["driver_amplifier"]["supplies"][
                "main"
            ]["channel"],
            ["CH1"],
        )
        self.assertIn("device_bindings", document)
        # ``to_document`` 只输出编辑段；正式段与 schema 版本由配置编写服务补。
        for formal in ("test_plan", "run_mapping", "schema_version", "wiring"):
            self.assertNotIn(formal, document)

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
        self.assertIsNone(result.draft.driver_power_source)
        self.assertEqual(result.draft.dut_channels, ())
        self.assertEqual(result.draft.power_supplies, ())
        self.assertTrue(result.draft.bindings.is_empty())

        document = result.draft.to_document()
        self.assertEqual(document["test_frequencies"], [])
        self.assertEqual(
            document["signal_source"],
            {"start_power": None, "stop_power": None, "step": None},
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
            "driver_mode": {"enabled": "yes", "power_source": 1},
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
                    "supplies": {"carrier": {"supply_id": None, "channel": ["CH1"]}}
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

        # 非法值保持缺失状态，不用缺省测试频率或默认设备补齐。
        self.assertEqual(result.draft.frequencies, (Frequency.from_ghz(2.4),))
        self.assertIsNone(result.draft.sweep)
        self.assertIsNone(result.draft.compression_target)
        self.assertIsNone(result.draft.attenuator)
        self.assertIsNone(result.draft.max_dut_input_power)
        self.assertIsNone(result.draft.dut_supply_count)
        self.assertIsNone(result.draft.driver_enabled)
        self.assertIsNone(result.draft.driver_power_source)
        self.assertEqual(result.draft.dut_channels, ())
        self.assertEqual(
            result.draft.power_supplies[0].channel("CH1").role, None
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

    def test_assignment_reference_of_wrong_type_is_reported(self):
        document = {
            "power_supply_assignment": {
                "dut_amplifier": {
                    "supplies": {
                        "carrier": {"supply_id": 5, "channel": ["CH1"]}
                    }
                }
            }
        }
        result = ConfigurationDraft.from_document(document)

        self.assertFalse(result.valid)
        issue = next(
            issue
            for issue in result.issues
            if issue.code == "configuration.invalid_assignment"
        )
        self.assertEqual(
            issue.path,
            "power_supply_assignment.dut_amplifier.supplies.carrier.supply_id",
        )
        assignment = result.draft.dut_assignments[0]
        self.assertIsNone(assignment.supply_id)
        self.assertEqual(assignment.channels, ("CH1",))
        self.assertEqual(result.draft.dut_channels, ())

    def test_unbound_assignment_is_preserved_without_picking_a_supply(self):
        document = {
            "power_supply_assignment": {
                "dut_amplifier": {
                    "supplies": {
                        "carrier": {"supply_id": None, "channel": ["CH1"]}
                    }
                }
            }
        }
        result = ConfigurationDraft.from_document(document)

        self.assertTrue(result.valid, result.messages())
        assignment = result.draft.dut_assignments[0]
        self.assertIsNone(assignment.supply_id)
        self.assertEqual(assignment.channels, ("CH1",))
        self.assertEqual(result.draft.dut_channels, ())
        document_out = result.draft.to_document()
        entry = document_out["power_supply_assignment"]["dut_amplifier"]["supplies"][
            "carrier"
        ]
        self.assertIsNone(entry["supply_id"])
        self.assertNotIn("name", entry)

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
                    channels=(PowerChannelDraft(channel="CH1", role="gate"),),
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
