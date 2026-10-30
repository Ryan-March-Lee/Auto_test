"""门禁：正式运行映射的多电源集合（重构计划步骤 10）。

步骤 10 把 ``RunResourceMapping`` 的电源段从单数 ``instruments.power_supply`` 扩展为集合
``instruments.power_supplies``，并保留旧单数结构的读取兼容。本文件固定三件事：

- 新结构能表达多台电源，快照条目自带 ``label`` / ``model`` / ``serial`` / ``device_key`` /
  ``visa_address``，通道写成 ``supply_id/CHx``；
- 旧快照（单数电源条目、裸通道名）仍能解析、仍能通过校验——历史运行不能因为换了结构打不开；
- 新结构的冲突（地址重复、身份重复、路由指向不存在的电源、新旧写法混用）在连接前失败。

数据契约见 ``docs/电源设备身份与配置数据契约.md`` 第 7 节。
"""

import json
import unittest
from pathlib import Path

from domain.configuration.models import (
    RunConfiguration,
    RunResourceMapping,
    TestPlan,
    validate_run_configuration,
    validate_run_mapping,
)
from domain.models import RunContext


FIXTURE_DIR = Path(__file__).parents[1] / "tests" / "fixtures" / "power_supply_identity"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _plan() -> TestPlan:
    return TestPlan.from_dict(
        {
            "schema_version": "1.0",
            "template": False,
            "frequencies": {"values": [2.4], "unit": "GHz"},
            "signal_source": {"start_power": -10.0, "stop_power": 0.0, "step": 1.0, "unit": "dBm"},
            "compression_point": {"type": "5dB"},
            "attenuator": {"value": 30.0, "unit": "dB"},
            "dut": {
                "max_input_power": 30.0,
                "power_roles": {
                    "gate": {"role": "gate", "voltage": 2.8, "current": 0.1},
                    "drain": {"role": "drain", "voltage": 28.0, "current": 2.0},
                },
            },
            "driver_mode": {"enabled": False, "power_roles": {}},
            "other_parameters": {},
        }
    )


def two_supply_mapping() -> dict:
    """两台电源、gate 与 drain 来自不同设备的新结构运行映射。"""
    return {
        "schema_version": "1.0",
        "template": False,
        "instruments": {
            "signal_generator": {"model": "SG", "visa_address": "sim::sg"},
            "spectrum_analyzer": {"model": "SA", "visa_address": "sim::sa"},
            "power_supplies": {
                "DP8B1519": {
                    "label": "DP8B1519",
                    "model": "DP832A",
                    "serial": "DP8B214901519",
                    "device_key": "rigol technologies:dp8b214901519",
                    "visa_address": "sim::ps1",
                },
                "DP8B0885": {
                    "label": "DP8B0885",
                    "model": "DP832A",
                    "serial": "DP8B174400885",
                    "device_key": "rigol technologies:dp8b174400885",
                    "visa_address": "sim::ps2",
                },
            },
        },
        "driver_mode": {"enabled": False, "power_channels": []},
        "dut_power_channels": [
            {"channel": "DP8B1519/CH1", "role": "gate", "connection": "DP8B1519/CH1 -> DUT 栅极"},
            {"channel": "DP8B0885/CH2", "role": "drain", "connection": "DP8B0885/CH2 -> DUT 漏极"},
        ],
    }


class MultiSupplyMappingTests(unittest.TestCase):
    """新结构：多电源、自带身份、显式路由。"""

    def test_two_supplies_round_trip_with_identity(self):
        mapping = RunResourceMapping.from_dict(two_supply_mapping())

        self.assertEqual(sorted(mapping.power_supplies), ["DP8B0885", "DP8B1519"])
        self.assertEqual(mapping.power_supplies["DP8B0885"].serial, "DP8B174400885")
        self.assertEqual(
            mapping.power_supplies["DP8B0885"].device_key, "rigol technologies:dp8b174400885"
        )
        # 旧单数条目不再参与序列化：电源只有一个来源。
        payload = mapping.to_dict()
        self.assertNotIn("power_supply", payload["instruments"])
        self.assertEqual(sorted(payload["instruments"]["power_supplies"]), ["DP8B0885", "DP8B1519"])
        self.assertEqual(
            RunResourceMapping.from_dict(payload).power_supplies, mapping.power_supplies
        )

    def test_two_supplies_pass_full_run_validation(self):
        mapping = RunResourceMapping.from_dict(two_supply_mapping())

        self.assertTrue(validate_run_mapping(mapping).valid)
        configuration = RunConfiguration(_plan(), mapping)
        self.assertTrue(validate_run_configuration(configuration).valid)

    def test_gate_and_drain_may_come_from_different_supplies(self):
        mapping = RunResourceMapping.from_dict(two_supply_mapping())
        configuration = RunConfiguration(_plan(), mapping)

        result = validate_run_configuration(configuration)

        self.assertTrue(result.valid, result.errors)

    def test_documented_snapshot_fixture_is_valid(self):
        mapping = RunResourceMapping.from_dict(_fixture("run_mapping_snapshot_two_supplies.json"))

        self.assertTrue(validate_run_mapping(mapping).valid)
        self.assertEqual(len(mapping.power_supplies), 2)

    def test_duplicate_address_between_supplies_is_rejected(self):
        data = two_supply_mapping()
        data["instruments"]["power_supplies"]["DP8B0885"]["visa_address"] = "sim::ps1"

        paths = {issue.path for issue in validate_run_mapping(data).errors}

        self.assertIn("instruments.power_supplies.DP8B0885.visa_address", paths)

    def test_duplicate_address_with_another_instrument_is_rejected(self):
        data = two_supply_mapping()
        data["instruments"]["power_supplies"]["DP8B1519"]["visa_address"] = "sim::sg"

        paths = {issue.path for issue in validate_run_mapping(data).errors}

        self.assertIn("instruments.power_supplies.DP8B1519.visa_address", paths)

    def test_same_device_registered_twice_is_rejected(self):
        data = two_supply_mapping()
        data["instruments"]["power_supplies"]["DP8B0885"]["device_key"] = (
            "rigol technologies:dp8b214901519"
        )

        paths = {issue.path for issue in validate_run_mapping(data).errors}

        self.assertIn("instruments.power_supplies.DP8B0885.device_key", paths)

    def test_key_must_equal_the_label(self):
        data = two_supply_mapping()
        data["instruments"]["power_supplies"]["DP8B1519"]["label"] = "DP8B9999"

        paths = {issue.path for issue in validate_run_mapping(data).errors}

        self.assertIn("instruments.power_supplies.DP8B1519", paths)

    def test_every_supply_must_carry_its_identity(self):
        data = two_supply_mapping()
        for field in ("label", "model", "serial", "device_key", "visa_address"):
            data["instruments"]["power_supplies"]["DP8B0885"][field] = None

        paths = {issue.path for issue in validate_run_mapping(data).errors}

        for field in ("label", "model", "serial", "device_key", "visa_address"):
            self.assertIn(f"instruments.power_supplies.DP8B0885.{field}", paths)

    def test_channel_route_must_point_at_a_registered_supply(self):
        data = two_supply_mapping()
        data["dut_power_channels"][1]["channel"] = "DP8B0000/CH2"

        paths = {issue.path for issue in validate_run_mapping(data).errors}

        self.assertIn("dut_power_channels[1].channel", paths)

    def test_bare_channel_name_is_rejected_in_the_new_structure(self):
        data = two_supply_mapping()
        data["dut_power_channels"][0]["channel"] = "CH1"

        paths = {issue.path for issue in validate_run_mapping(data).errors}

        self.assertIn("dut_power_channels[0].channel", paths)

    def test_both_structures_at_once_are_rejected(self):
        data = two_supply_mapping()
        data["instruments"]["power_supply"] = {"model": "PS1", "visa_address": "sim::ps9"}

        paths = {issue.path for issue in validate_run_mapping(data).errors}

        self.assertIn("instruments.power_supply", paths)

    def test_empty_collection_is_rejected_with_the_new_path(self):
        data = two_supply_mapping()
        data["instruments"]["power_supplies"] = {}
        data["dut_power_channels"] = []

        paths = {issue.path for issue in validate_run_mapping(data).errors}

        self.assertIn("instruments.power_supplies", paths)

    def test_empty_collection_beside_a_legacy_entry_is_still_mixed(self):
        """混写判据是集合**键**在不在：``power_supplies: {}`` 不能把旧写法洗白。"""
        data = _fixture("run_mapping_legacy_single.json")
        data["instruments"]["power_supplies"] = {}

        paths = {issue.path for issue in validate_run_mapping(data).errors}

        self.assertIn("instruments.power_supply", paths)

    def test_the_collection_is_not_listed_as_an_instrument(self):
        """电源集合有自己的一层结构，不能在逐台仪器表里多出一条空仪器。"""
        mapping = RunResourceMapping.from_dict(two_supply_mapping())

        self.assertNotIn("power_supplies", mapping.instruments)
        self.assertTrue(mapping.declares_power_supplies())

    def test_legacy_snapshot_round_trip_keeps_the_legacy_shape(self):
        """旧快照读入再写回仍是旧形状，不会被补一个空集合变成混写文档。"""
        mapping = RunResourceMapping.from_dict(_fixture("run_mapping_legacy_single.json"))

        payload = mapping.to_dict()

        self.assertIn("power_supply", payload["instruments"])
        self.assertNotIn("power_supplies", payload["instruments"])
        self.assertFalse(mapping.declares_power_supplies())
        self.assertTrue(validate_run_mapping(payload).valid)

    def test_new_snapshot_round_trip_writes_only_the_collection(self):
        """新结构写回时旧单数条目被丢掉：电源只有一个来源。"""
        data = two_supply_mapping()
        data["instruments"]["power_supply"] = {"model": "PS1", "visa_address": "sim::ps9"}

        payload = RunResourceMapping.from_dict(data).to_dict()

        self.assertNotIn("power_supply", payload["instruments"])
        self.assertEqual(
            sorted(payload["instruments"]["power_supplies"]), ["DP8B0885", "DP8B1519"]
        )


class LegacySingleSupplyMappingTests(unittest.TestCase):
    """旧结构：单数电源条目、裸通道名，保持可读可校验。"""

    def test_legacy_fixture_still_parses_and_validates(self):
        mapping = RunResourceMapping.from_dict(_fixture("run_mapping_legacy_single.json"))

        self.assertIn("power_supply", mapping.instruments)
        self.assertEqual(mapping.power_supplies, {})
        self.assertTrue(validate_run_mapping(mapping).valid)
        # 裸通道名原样保留：旧快照按当时写下的字段显示，不在这里改写。
        self.assertEqual(
            [item.channel for item in mapping.dut_power_channels], ["CH1", "CH2"]
        )

    def test_legacy_mapping_reports_missing_supply(self):
        """旧快照只剩仪器条目时没有电源：报告“缺少电源”，而不是丢掉这件事。"""
        data = _fixture("run_mapping_legacy_single.json")
        del data["instruments"]["power_supply"]

        paths = {issue.path for issue in validate_run_mapping(data).errors}

        self.assertIn("instruments.power_supplies", paths)

    def test_incomplete_legacy_entry_is_reported_on_its_own_field(self):
        """旧快照的单数条目仍在时，缺字段报告落在旧键上，历史运行不受新结构影响。"""
        data = _fixture("run_mapping_legacy_single.json")
        data["instruments"]["power_supply"]["visa_address"] = None

        paths = {issue.path for issue in validate_run_mapping(data).errors}

        self.assertIn("instruments.power_supply.visa_address", paths)

    def test_legacy_accessor_exposes_the_only_supply(self):
        data = two_supply_mapping()
        data["instruments"]["power_supplies"] = {
            "DP8B1519": data["instruments"]["power_supplies"]["DP8B1519"]
        }
        data["dut_power_channels"] = [
            {"channel": "DP8B1519/CH1", "role": "gate", "connection": "gate"},
            {"channel": "DP8B1519/CH2", "role": "drain", "connection": "drain"},
        ]
        mapping = RunResourceMapping.from_dict(data)

        legacy = mapping.legacy_power_supply()

        self.assertIsNotNone(legacy)
        self.assertEqual(legacy.model, "DP832A")
        self.assertEqual(legacy.visa_address, "sim::ps1")
        # 多台电源时“那台电源”没有唯一答案，不猜。
        self.assertIsNone(
            RunResourceMapping.from_dict(two_supply_mapping()).legacy_power_supply()
        )


class RunContextSupplyTests(unittest.TestCase):
    """运行上下文：多电源进 ``power_supplies``，旧字段只在唯一一台时有值。"""

    def test_context_carries_every_supply_with_identity(self):
        context = RunContext.from_resource_mapping(two_supply_mapping(), run_id="run-1")

        self.assertEqual(sorted(context.power_supplies), ["DP8B0885", "DP8B1519"])
        self.assertEqual(context.power_supplies["DP8B1519"]["serial"], "DP8B214901519")
        # 多台电源时旧单数视图为空：它没有唯一答案。
        self.assertEqual(dict(context.power_supply), {})
        self.assertEqual(
            dict(context.power_channel_mapping)["drain"], "DP8B0885/CH2 -> DUT 漏极"
        )

    def test_context_keeps_the_legacy_single_supply_view_readable(self):
        context = RunContext.from_resource_mapping(
            _fixture("run_mapping_legacy_single.json"), run_id="run-1"
        )

        self.assertEqual(
            dict(context.power_supply),
            {"model": "PS1", "visa_address": "TCPIP0::192.168.1.101::inst0::INSTR"},
        )
        # 旧映射只有一个条目：按槽位名读成一台电源，历史上下文仍能指到它。
        self.assertEqual(sorted(context.power_supplies), ["PS1"])

    def test_context_dict_round_trips_the_supply_collection(self):
        context = RunContext.from_resource_mapping(two_supply_mapping(), run_id="run-1")

        payload = context.to_dict()

        self.assertEqual(sorted(payload["power_supplies"]), ["DP8B0885", "DP8B1519"])


if __name__ == "__main__":
    unittest.main()
