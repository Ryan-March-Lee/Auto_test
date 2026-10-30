"""步骤 15：供电分配解析是一份可独立测试的纯路由计算。

固定的行为：

- :func:`instrument.power_routing.resolve_supply_keys` 把分配里的引用解析成
  ``power_supplies`` 的集合键：``supply_id`` 直接命中，旧 ``label`` 与残留的
  ``visual`` 槽位必须唯一指向**一台已启用**的电源，任何歧义都明确失败；
- :func:`instrument.power_routing.resolve_channel_routing` 把分配解析成角色 token：
  角色只认通道上显式声明的 ``gate``/``drain``，token 只由集合键拼成，日志写法
  （``短标签/CHx``）在同一处生成，急停先漏后栅；
- 模块本身不接触设备库：没有 pyvisa、没有地址、没有资源，因此可以在连接任何
  仪器之前单独测试。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_power_routing -v
```
"""

import unittest
from pathlib import Path

from instrument.power_routing import (
    POWER_ROLES,
    assigned_supply_references,
    declared_channels,
    resolve_channel_routing,
    resolve_supply_keys,
    supply_short_label,
)

ROOT = Path(__file__).parents[1]
ROUTING_SOURCE = ROOT / "instrument" / "power_routing.py"

SUPPLY_A = "TCPIP0::192.168.1.102::inst0::INSTR"
SUPPLY_B = "TCPIP0::192.168.1.103::inst0::INSTR"
SUPPLY_C = "TCPIP0::192.168.1.104::inst0::INSTR"


def _supply(*, address, label=None, visual=None, enabled=True, extra_channels=()):
    channels = {
        "CH1": {"role": "gate", "voltage": {"value": 2.8}, "current": {"value": 0.1}},
        "CH2": {"role": "drain", "voltage": {"value": 28.0}, "current": {"value": 2.0}},
    }
    for channel in extra_channels:
        channels[channel] = {"voltage": {"value": 0.0}, "current": {"value": 0.0}}
    item = {"address": address, "enabled": enabled, "channels": channels}
    if label is not None:
        item["label"] = label
    if visual is not None:
        item["visual"] = visual
    return item


def _assignment(*, supply_id, channels=("CH1", "CH2")):
    return {"supply_id": supply_id, "name": supply_id, "channel": list(channels)}


def _power_configs(**overrides):
    supplies = {
        "DP8B1519": _supply(address=SUPPLY_A),
        "DP8B0950": _supply(address=SUPPLY_B, label="DP8B0950"),
        "DP8B0885": _supply(address=SUPPLY_C, label="DP8B0885", visual="PS3"),
    }
    supplies.update(overrides)
    return supplies


def _assignments(dut="DP8B1519", driver="DP8B0950"):
    dut_supplies = {} if dut is None else {"carrier": _assignment(supply_id=dut)}
    driver_supplies = {} if driver is None else {"main": _assignment(supply_id=driver)}
    return (
        {"supplies": dut_supplies},
        {"supplies": driver_supplies},
    )


def _routing(power_configs=None, *, dut="DP8B1519", driver="DP8B0950", enabled=None):
    configs = power_configs if power_configs is not None else _power_configs()
    dut_assignment, driver_assignment = _assignments(dut, driver)
    enabled_ids = enabled if enabled is not None else [name for name in configs]
    resolution = resolve_supply_keys(
        configs,
        assigned_supply_references(dut_assignment, driver_assignment),
        enabled_ids,
    )
    return resolution, resolve_channel_routing(
        dut_assignment=dut_assignment,
        driver_assignment=driver_assignment,
        power_configs=configs,
        resolution=resolution,
    )


class RoutingModuleIsPureTests(unittest.TestCase):
    """路由模块不接触设备库，也不解释仪器地址。"""

    def test_module_does_not_import_the_visa_library(self):
        source = ROUTING_SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("pyvisa", source)
        self.assertNotIn("open_resource", source)
        self.assertNotIn("list_resources", source)

    def test_module_does_not_interpret_instrument_addresses(self):
        # 地址是装配计划的事：路由只认集合键、角色与通道。
        source = ROUTING_SOURCE.read_text(encoding="utf-8")
        self.assertNotIn('"address"', source)
        self.assertNotIn("signal_generator", source)


class ReferenceResolutionTests(unittest.TestCase):
    """分配引用只解析到唯一一台已启用的电源。"""

    def test_supply_id_resolves_to_itself(self):
        resolution, _routing_result = _routing()
        self.assertEqual(resolution.supply_keys["DP8B1519"], "DP8B1519")
        self.assertEqual(resolution.supply_ids, ("DP8B0950", "DP8B1519"))

    def test_label_resolves_to_the_supply_that_carries_it(self):
        resolution = resolve_supply_keys(
            _power_configs(), ["DP8B0950"], list(_power_configs())
        )
        self.assertEqual(resolution.supply_keys["DP8B0950"], "DP8B0950")
        self.assertEqual(resolution.supply_labels, {"DP8B0950": "DP8B0950"})

    def test_legacy_visual_slot_resolves_by_identity(self):
        resolution = resolve_supply_keys(
            _power_configs(), ["PS3"], list(_power_configs())
        )
        self.assertEqual(resolution.supply_keys["PS3"], "DP8B0885")

    def test_unknown_reference_fails(self):
        with self.assertRaisesRegex(ValueError, "不存在的电源"):
            resolve_supply_keys(_power_configs(), ["PS9"], list(_power_configs()))

    def test_a_name_that_is_both_key_and_alias_fails(self):
        configs = _power_configs(DP8B0885=_supply(address=SUPPLY_C, label="DP8B0950"))
        with self.assertRaisesRegex(ValueError, "同时是另一台电源的 label/visual"):
            resolve_supply_keys(configs, ["DP8B0950"], list(configs))

    def test_a_name_pointing_at_two_supplies_fails(self):
        configs = _power_configs(
            DP8B0950=_supply(address=SUPPLY_B, visual="DUP"),
            DP8B0885=_supply(address=SUPPLY_C, visual="DUP"),
        )
        with self.assertRaisesRegex(ValueError, "对应多台电源"):
            resolve_supply_keys(configs, ["DUP"], list(configs))

    def test_disabled_supply_is_not_a_resolution_target(self):
        configs = _power_configs()
        with self.assertRaisesRegex(ValueError, "未启用的电源"):
            resolve_supply_keys(configs, ["DP8B1519"], ["DP8B0950"])

    def test_references_are_collected_without_trimming(self):
        dut_assignment, driver_assignment = _assignments(dut=" DP8B1519 ")
        references = assigned_supply_references(dut_assignment, driver_assignment)
        self.assertEqual(references, (" DP8B1519 ", "DP8B0950"))
        with self.assertRaisesRegex(ValueError, "不存在的电源"):
            resolve_supply_keys(_power_configs(), references, list(_power_configs()))

    def test_assignment_entry_must_be_an_object(self):
        with self.assertRaisesRegex(ValueError, "供电分配条目必须是对象"):
            assigned_supply_references({"supplies": {"carrier": "DP8B1519"}})

    def test_assignment_must_provide_a_supplies_object(self):
        with self.assertRaisesRegex(ValueError, "供电分配必须提供 supplies 对象"):
            assigned_supply_references({"supplies": ["DP8B1519"]})


class ChannelRoutingTests(unittest.TestCase):
    """角色 token、设置、日志写法与急停顺序。"""

    def test_roles_become_explicit_supply_id_tokens(self):
        _resolution, routing = _routing()

        self.assertEqual(
            routing.dut_channels, {"gate": "DP8B1519/CH1", "drain": "DP8B1519/CH2"}
        )
        self.assertEqual(
            routing.driver_channels, {"gate": "DP8B0950/CH1", "drain": "DP8B0950/CH2"}
        )
        self.assertEqual(routing.power_settings["drain"]["voltage_v"], 28.0)

    def test_log_text_uses_the_short_label_and_routing_keeps_the_token(self):
        configs = _power_configs(DP8B1519=_supply(address=SUPPLY_A, label="现场 A 电源"))
        _resolution, routing = _routing(configs, driver=None)

        self.assertEqual(routing.channel_labels["DP8B1519/CH1"], "现场 A 电源/CH1")
        self.assertEqual(routing.all_channels["gate"], "DP8B1519/CH1")

    def test_shutdown_switches_assigned_roles_drain_before_gate(self):
        _resolution, routing = _routing()

        self.assertEqual(
            routing.emergency_channels,
            (
                (0, "DP8B0950/CH2"),
                (1, "DP8B1519/CH2"),
                (0, "DP8B0950/CH1"),
                (1, "DP8B1519/CH1"),
            ),
        )

    def test_unassigned_channels_are_not_routed(self):
        configs = _power_configs(
            DP8B1519=_supply(address=SUPPLY_A, extra_channels=("CH3",))
        )
        _resolution, routing = _routing(configs, driver=None)

        self.assertNotIn("DP8B1519/CH3", routing.channel_labels)
        self.assertEqual(
            [token for _index, token in routing.emergency_channels],
            ["DP8B1519/CH2", "DP8B1519/CH1"],
        )

    def test_missing_role_fails(self):
        configs = _power_configs()
        del configs["DP8B1519"]["channels"]["CH1"]["role"]
        with self.assertRaisesRegex(ValueError, "显式 gate/drain 角色"):
            _routing(configs, driver=None)

    def test_duplicate_role_fails(self):
        configs = _power_configs()
        configs["DP8B1519"]["channels"]["CH2"]["role"] = "gate"
        with self.assertRaisesRegex(ValueError, "供电角色重复配置"):
            _routing(configs, driver=None)

    def test_incomplete_role_pair_fails(self):
        configs = _power_configs()
        dut_assignment = {
            "supplies": {"carrier": _assignment(supply_id="DP8B1519", channels=["CH1"])}
        }
        resolution = resolve_supply_keys(configs, ["DP8B1519"], list(configs))
        with self.assertRaisesRegex(ValueError, "必须同时配置 gate 和 drain"):
            resolve_channel_routing(
                dut_assignment=dut_assignment,
                driver_assignment={"supplies": {}},
                power_configs=configs,
                resolution=resolution,
            )

    def test_unknown_channel_fails(self):
        dut_assignment = {
            "supplies": {"carrier": _assignment(supply_id="DP8B1519", channels=["CH9"])}
        }
        configs = _power_configs()
        resolution = resolve_supply_keys(configs, ["DP8B1519"], ["DP8B1519"])
        with self.assertRaisesRegex(ValueError, "不存在的通道"):
            resolve_channel_routing(
                dut_assignment=dut_assignment,
                driver_assignment={"supplies": {}},
                power_configs=configs,
                resolution=resolution,
            )

    def test_dut_and_driver_cannot_share_a_channel(self):
        with self.assertRaisesRegex(ValueError, "占用了同一个电源通道"):
            _routing(driver="DP8B1519")


class SharedRuleTests(unittest.TestCase):
    """短标签与声明通道各只有一份实现。"""

    def test_short_label_prefers_the_configured_label(self):
        self.assertEqual(supply_short_label(" DP8B1519 ", "key"), "DP8B1519")
        self.assertEqual(supply_short_label("", "key"), "key")

    def test_power_roles_are_the_two_declared_roles(self):
        self.assertEqual(POWER_ROLES, ("gate", "drain"))

    def test_declared_channels_requires_a_non_empty_object(self):
        with self.assertRaisesRegex(ValueError, "必须配置非空通道对象"):
            declared_channels({"channels": {}}, "DP8B1519")


if __name__ == "__main__":
    unittest.main()
