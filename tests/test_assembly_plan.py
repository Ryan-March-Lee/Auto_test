"""步骤 15：硬件装配计划是一份可独立测试的纯配置解析。

固定的行为：

- :func:`instrument.assembly_plan.hardware_assembly_plan` 只读配置、只做校验，
  返回一份完整的构造计划（地址、通道 token、日志文本、急停顺序），
  **不导入 pyvisa**、不打开任何资源、不构造驱动；
- 校验失败在连接任何仪器之前发生：地址重复、角色缺失/重复、通道不存在、
  DUT 与驱动功放抢同一个通道、短标签重复；
- 急停顺序由声明角色决定（先漏后栅），且只覆盖被分配的通道；
- 短标签只有一个来源（``supply_short_label``）：配置里的 ``label`` 优先，
  缺失时退回集合键，不推导、不猜测。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_assembly_plan -v
```
"""

import unittest
from pathlib import Path

from instrument.assembly_plan import (
    POWER_ROLES,
    configured_channels,
    hardware_assembly_plan,
    supply_short_label,
)

ROOT = Path(__file__).parents[1]
PLAN_SOURCE = ROOT / "instrument" / "assembly_plan.py"

SIGNAL = "TCPIP0::192.168.1.111::inst0::INSTR"
SPECTRUM = "TCPIP0::192.168.1.112::inst0::INSTR"
SUPPLY_A = "TCPIP0::192.168.1.102::inst0::INSTR"
SUPPLY_B = "TCPIP0::192.168.1.103::inst0::INSTR"


def _supply(address, *, label=None, gate="CH1", drain="CH2", enabled=True):
    item = {
        "address": address,
        "enabled": enabled,
        "channels": {
            gate: {"role": "gate", "voltage": {"value": 2.8}, "current": {"value": 0.1}},
            drain: {"role": "drain", "voltage": {"value": 28.0}, "current": {"value": 2.0}},
        },
    }
    if label is not None:
        item["label"] = label
    return item


def _config(*, dut="DP8B1519", driver=None) -> dict:
    supplies = {
        "DP8B1519": _supply(SUPPLY_A),
        "DP8B0950": _supply(SUPPLY_B, label="DP8B0950"),
    }
    assignment = {
        "dut_amplifier": {
            "supplies": {
                "carrier": {
                    "supply_id": dut,
                    "name": dut,
                    "channel": ["CH1", "CH2"],
                }
            }
        },
        "driver_amplifier": {"supplies": {}},
    }
    if driver is not None:
        assignment["driver_amplifier"]["supplies"]["main"] = {
            "supply_id": driver,
            "name": driver,
            "channel": ["CH1", "CH2"],
        }
    return {
        "instruments": {
            "signal_generator": {"address": SIGNAL, "enabled": True},
            "spectrum_analyzer": {"address": SPECTRUM, "enabled": True},
            "power_supplies": supplies,
        },
        "power_supply_assignment": assignment,
    }


class AssemblyPlanIsPureTests(unittest.TestCase):
    """计划模块不接触设备库：没有 pyvisa，也就没有资源可泄漏。"""

    def test_plan_module_does_not_import_the_visa_library(self):
        source = PLAN_SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("pyvisa", source)
        self.assertNotIn("open_resource", source)
        self.assertNotIn("list_resources", source)

    def test_plan_carries_routing_tokens_and_log_texts(self):
        plan = hardware_assembly_plan(_config())

        self.assertEqual(
            plan["all_channels"],
            {"gate": "DP8B1519/CH1", "drain": "DP8B1519/CH2"},
        )
        self.assertEqual(plan["supply_names"], ["DP8B1519"])
        self.assertEqual(plan["supply_labels"], {"DP8B1519": "DP8B1519"})
        self.assertEqual(
            plan["channel_labels"],
            {
                "DP8B1519/CH1": "DP8B1519/CH1",
                "DP8B1519/CH2": "DP8B1519/CH2",
            },
        )
        self.assertEqual(
            plan["signal_address"], SIGNAL
        )
        self.assertEqual(plan["power_settings"]["drain"]["voltage_v"], 28.0)


class AssemblyPlanValidationTests(unittest.TestCase):
    """配置错误在连接任何仪器之前失败。"""

    def test_missing_channel_role_fails(self):
        config = _config()
        del config["instruments"]["power_supplies"]["DP8B1519"]["channels"]["CH1"]["role"]
        with self.assertRaisesRegex(ValueError, "显式 gate/drain 角色"):
            hardware_assembly_plan(config)

    def test_duplicate_role_across_channels_fails(self):
        config = _config()
        config["instruments"]["power_supplies"]["DP8B1519"]["channels"]["CH2"]["role"] = "gate"
        with self.assertRaisesRegex(ValueError, "供电角色重复配置"):
            hardware_assembly_plan(config)

    def test_duplicate_instrument_address_fails(self):
        config = _config(driver="DP8B0950")
        config["instruments"]["power_supplies"]["DP8B0950"]["address"] = SIGNAL
        with self.assertRaisesRegex(ValueError, "地址重复"):
            hardware_assembly_plan(config)

    def test_dut_and_driver_cannot_share_a_channel(self):
        with self.assertRaisesRegex(ValueError, "占用了同一个电源通道"):
            hardware_assembly_plan(_config(driver="DP8B1519"))

    def test_duplicate_short_labels_fail(self):
        # 引用只写 supply_id，两台电源挂同一个显示名：日志无法指向唯一设备。
        config = _config(driver="DP8B0950")
        for name in ("DP8B1519", "DP8B0950"):
            config["instruments"]["power_supplies"][name]["label"] = "现场 A 电源"
        with self.assertRaisesRegex(ValueError, "短标签重复"):
            hardware_assembly_plan(config)

    def test_missing_assignment_is_rejected(self):
        config = _config()
        config["power_supply_assignment"] = {
            "dut_amplifier": {"supplies": {}},
            "driver_amplifier": {"supplies": {}},
        }
        with self.assertRaisesRegex(ValueError, "必须显式配置电源角色"):
            hardware_assembly_plan(config)


class EmergencyChannelOrderTests(unittest.TestCase):
    """急停只关闭被分配的角色通道，先漏后栅，按电源顺序编号。"""

    def test_two_supplies_shut_down_drain_before_gate(self):
        plan = hardware_assembly_plan(_config(driver="DP8B0950"))

        self.assertEqual(
            plan["emergency_channels"],
            (
                (0, "DP8B0950/CH2"),
                (1, "DP8B1519/CH2"),
                (0, "DP8B0950/CH1"),
                (1, "DP8B1519/CH1"),
            ),
        )

    def test_unassigned_channel_is_not_part_of_the_plan(self):
        config = _config()
        config["instruments"]["power_supplies"]["DP8B1519"]["channels"]["CH3"] = {
            "voltage": {"value": 0.0},
            "current": {"value": 0.0},
        }
        plan = hardware_assembly_plan(config)

        self.assertEqual(
            [token for _index, token in plan["emergency_channels"]],
            ["DP8B1519/CH2", "DP8B1519/CH1"],
        )


class SharedHelperTests(unittest.TestCase):
    """短标签与通道判据各只有一份实现。"""

    def test_short_label_prefers_the_configured_label(self):
        self.assertEqual(supply_short_label("  DP8B1519 ", "key"), "DP8B1519")
        self.assertEqual(supply_short_label("   ", "key"), "key")
        self.assertEqual(supply_short_label(None, "key"), "key")
        self.assertEqual(supply_short_label({"label": "x"}, "key"), "key")

    def test_power_roles_are_the_two_declared_roles(self):
        self.assertEqual(POWER_ROLES, ("gate", "drain"))

    def test_configured_channels_requires_a_non_empty_object(self):
        with self.assertRaisesRegex(ValueError, "必须配置非空通道对象"):
            configured_channels({"channels": {}}, "DP8B1519")
        with self.assertRaisesRegex(ValueError, "必须配置非空通道对象"):
            configured_channels({}, "DP8B1519")
        self.assertEqual(
            configured_channels({"channels": {"CH1": {}}}, "DP8B1519"), {"CH1": {}}
        )


if __name__ == "__main__":
    unittest.main()
