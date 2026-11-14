"""门禁：正式运行映射的多电源集合（重构计划步骤 10）。

``RunResourceMapping`` 的电源段是集合 ``instruments.power_supplies``，不再读取旧单数结构。
本文件固定三件事：

- 集合能表达多台电源，快照条目自带 ``label`` / ``model`` / ``full_code`` / ``device_key`` /
  ``visa_address``，通道写成 ``supply_id/CHx``；
- 旧单数条目、裸通道名与新旧混用都会被明确拒绝；
- 新结构的冲突（地址重复、身份重复、路由指向不存在的电源）在连接前失败。

数据契约见 ``domain/configuration/models.py`` 的 ``PowerSupplyMapping`` 与
``tests/fixtures/power_supply_identity/`` 里的样例。
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
            "signal_generator": {
                "label": "5462",
                "model": "SG",
                "full_code": "105462",
                "device_key": "rohde&schwarz:105462",
                "visa_address": "sim::sg",
            },
            "spectrum_analyzer": {
                "label": "QZNI0866",
                "model": "SA",
                "full_code": "QZNI000866",
                "device_key": "ceyear technologies:qzni000866",
                "visa_address": "sim::sa",
            },
            "power_supplies": {
                "DP8B1519": {
                    "label": "DP8B1519",
                    "model": "DP832A",
                    "full_code": "DP8B214901519",
                    "device_key": "rigol technologies:dp8b214901519",
                    "visa_address": "sim::ps1",
                },
                "DP8B0885": {
                    "label": "DP8B0885",
                    "model": "DP832A",
                    "full_code": "DP8B174400885",
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

    def test_reserialized_mapping_writes_only_full_code(self):
        """重新序列化的映射只有 ``full_code``：冲突只可能来自来源文档的键。"""
        data = two_supply_mapping()
        entry = data["instruments"]["signal_generator"]
        entry["serial"] = entry.pop("full_code")

        payload = RunResourceMapping.from_dict(data).to_dict()

        self.assertNotIn("serial", payload["instruments"]["signal_generator"])
        self.assertEqual(payload["instruments"]["signal_generator"]["full_code"], "105462")
        self.assertTrue(validate_run_mapping(payload).valid)

    def test_two_supplies_round_trip_with_identity(self):
        mapping = RunResourceMapping.from_dict(two_supply_mapping())

        self.assertEqual(sorted(mapping.power_supplies), ["DP8B0885", "DP8B1519"])
        self.assertEqual(mapping.power_supplies["DP8B0885"].full_code, "DP8B174400885")
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

    def test_snapshot_may_not_carry_serial_and_full_code_together(self):
        """完整设备码只有一个字段：同时出现 ``serial`` 与 ``full_code`` 无法判断身份。"""
        data = two_supply_mapping()
        data["instruments"]["signal_generator"]["serial"] = "105462"

        paths = {issue.path for issue in validate_run_mapping(data).errors}

        self.assertIn("instruments.signal_generator", paths)

    def test_supply_snapshot_may_not_carry_serial_and_full_code_together(self):
        data = two_supply_mapping()
        data["instruments"]["power_supplies"]["DP8B1519"]["serial"] = "DP8B214901519"

        paths = {issue.path for issue in validate_run_mapping(data).errors}

        self.assertIn("instruments.power_supplies.DP8B1519", paths)

    def test_every_supply_must_carry_its_identity(self):
        data = two_supply_mapping()
        for field in ("label", "model", "full_code", "device_key", "visa_address"):
            data["instruments"]["power_supplies"]["DP8B0885"][field] = None

        paths = {issue.path for issue in validate_run_mapping(data).errors}

        for field in ("label", "model", "full_code", "device_key", "visa_address"):
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

    def test_same_physical_channel_assigned_twice_is_rejected(self):
        """同一个 ``supply_id/CHx`` 出现两次：一条物理通道不可能同时供两个角色。"""
        data = two_supply_mapping()
        data["dut_power_channels"][1]["channel"] = "DP8B1519/CH1"

        paths = {issue.path for issue in validate_run_mapping(data).errors}

        self.assertIn("dut_power_channels[1].channel", paths)

    def test_duplicate_supply_role_is_no_longer_rejected(self):
        """步骤 5：通道不再按角色寻址，两台电源写同一个角色不再有意义。"""
        data = two_supply_mapping()
        data["dut_power_channels"][1]["role"] = "gate"

        result = validate_run_mapping(data)

        self.assertTrue(result.valid, result.errors)

    def test_one_supply_may_feed_both_roles_of_the_dut(self):
        """两台电源不是前提：同一台的两条通道分别供 gate 与 drain 仍然合法。"""
        data = two_supply_mapping()
        data["instruments"]["power_supplies"].pop("DP8B0885")
        data["dut_power_channels"] = [
            {"channel": "DP8B1519/CH1", "role": "gate", "connection": "DP8B1519/CH1 -> DUT 栅极"},
            {"channel": "DP8B1519/CH2", "role": "drain", "connection": "DP8B1519/CH2 -> DUT 漏极"},
        ]

        result = validate_run_mapping(data)

        self.assertTrue(result.valid, result.errors)

    def test_driver_and_dut_cannot_use_the_same_supply_channel(self):
        """同一物理通道不能既接 DUT 又接驱动功放。"""
        data = two_supply_mapping()
        data["driver_mode"] = {
            "enabled": True,
            "power_channels": [
                {"channel": "DP8B0885/CH2", "role": "driver", "connection": "驱动功放"}
            ],
        }

        result = validate_run_mapping(data)

        self.assertTrue(
            any("不能重复使用通道" in issue.message for issue in result.errors),
            [issue.message for issue in result.errors],
        )

    def test_driver_may_use_another_channel_of_an_assigned_supply(self):
        """驱动功放与 DUT 共用一台电源但通道不同是合法的（1 台 / 2 台同一路径）。"""
        data = two_supply_mapping()
        data["driver_mode"] = {
            "enabled": True,
            "power_channels": [
                {"channel": "DP8B0885/CH1", "role": "driver", "connection": "驱动功放"}
            ],
        }

        result = validate_run_mapping(data)

        self.assertTrue(result.valid, result.errors)

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


    def test_the_collection_is_not_listed_as_an_instrument(self):
        """电源集合有自己的一层结构，不能在逐台仪器表里多出一条空仪器。"""
        mapping = RunResourceMapping.from_dict(two_supply_mapping())

        self.assertNotIn("power_supplies", mapping.instruments)
        self.assertTrue(mapping.declares_power_supplies())


    def test_new_snapshot_round_trip_writes_only_the_collection(self):
        """新结构写回时旧单数条目被丢掉：电源只有一个来源。"""
        data = two_supply_mapping()
        data["instruments"]["power_supply"] = {"model": "PS1", "visa_address": "sim::ps9"}

        payload = RunResourceMapping.from_dict(data).to_dict()

        self.assertNotIn("power_supply", payload["instruments"])
        self.assertEqual(
            sorted(payload["instruments"]["power_supplies"]), ["DP8B0885", "DP8B1519"]
        )






class RunContextSupplyTests(unittest.TestCase):
    """运行上下文：多电源进 ``power_supplies``，旧字段只在唯一一台时有值。"""

    def test_context_carries_every_supply_with_identity(self):
        context = RunContext.from_resource_mapping(two_supply_mapping(), run_id="run-1")

        self.assertEqual(sorted(context.power_supplies), ["DP8B0885", "DP8B1519"])
        self.assertEqual(context.power_supplies["DP8B1519"]["full_code"], "DP8B214901519")
        # 多台电源时旧单数视图为空：它没有唯一答案。
        self.assertEqual(dict(context.power_supply), {})
        self.assertEqual(
            dict(context.power_channel_mapping)["drain"], "DP8B0885/CH2 -> DUT 漏极"
        )


    def test_context_freezes_instrument_identity(self):
        """信号源与频谱仪的运行快照自带身份，运行期间不回查注册表。"""
        context = RunContext.from_resource_mapping(two_supply_mapping(), run_id="run-1")

        self.assertEqual(
            dict(context.signal_generator),
            {
                "label": "5462",
                "model": "SG",
                "full_code": "105462",
                "device_key": "rohde&schwarz:105462",
                "visa_address": "sim::sg",
            },
        )


    def test_new_snapshot_never_writes_two_full_code_fields(self):
        """写出的快照只有 ``full_code``：旧键不会被回写，也不会出现双份身份。"""
        payload = RunContext.from_resource_mapping(
            two_supply_mapping(), run_id="run-1"
        ).to_dict()

        for name in ("signal_generator", "spectrum_analyzer"):
            with self.subTest(entry=name):
                self.assertNotIn("serial", payload[name])
        for supply_id, record in payload["power_supplies"].items():
            with self.subTest(supply_id=supply_id):
                self.assertNotIn("serial", record)

    def test_context_dict_round_trips_the_supply_collection(self):
        context = RunContext.from_resource_mapping(two_supply_mapping(), run_id="run-1")

        payload = context.to_dict()

        self.assertEqual(sorted(payload["power_supplies"]), ["DP8B0885", "DP8B1519"])


if __name__ == "__main__":
    unittest.main()
