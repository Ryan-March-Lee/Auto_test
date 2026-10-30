"""步骤 15：硬件装配计划是一份可独立测试的纯配置解析。

固定的行为：

- :func:`instrument.assembly_plan.hardware_assembly_plan` 只读配置、只做校验，
  返回一份**只读的计划对象**（仪器地址与能力参数、每台电源的地址与声明通道、
  通道 token、日志文本、急停顺序），**不导入 pyvisa**、不打开任何资源、不构造驱动；
- 计划里没有原始配置段：配置 schema 的解释到计划为止，装配方按计划取事实，
  不回读 ``["address"]`` / ``["channels"]`` / ``["serial"]`` 这类配置键；
- 校验失败在连接任何仪器之前发生：地址重复、角色缺失/重复、通道不存在、
  DUT 与驱动功放抢同一个通道、短标签重复；
- 急停顺序由声明角色决定（先漏后栅），且只覆盖被分配的通道；
- 短标签只有一个来源（``supply_short_label``）：配置里的 ``label`` 优先，
  缺失时退回集合键，不推导、不猜测。

供电分配的引用解析与 token 拼装在 :mod:`instrument.power_routing`，本文件只从
计划的角度验证它的结果；那个模块的纯路由行为由 ``tests/test_power_routing.py``
单独固定。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_assembly_plan -v
```
"""

import unittest
from pathlib import Path

from instrument.assembly_plan import HardwareAssemblyPlan, hardware_assembly_plan
from instrument.power_routing import POWER_ROLES, declared_channels, supply_short_label

ROOT = Path(__file__).parents[1]
PLAN_SOURCE = ROOT / "instrument" / "assembly_plan.py"
FACTORY_SOURCE = ROOT / "instrument" / "measurement_factory.py"

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

        self.assertIsInstance(plan, HardwareAssemblyPlan)
        self.assertEqual(plan.signal.address, SIGNAL)
        self.assertEqual(plan.spectrum.address, SPECTRUM)
        self.assertEqual(
            plan.all_channels,
            {"gate": "DP8B1519/CH1", "drain": "DP8B1519/CH2"},
        )
        self.assertEqual([supply.supply_id for supply in plan.supplies], ["DP8B1519"])
        self.assertEqual(plan.supply_labels, {"DP8B1519": "DP8B1519"})
        self.assertEqual(
            plan.channel_labels,
            {
                "DP8B1519/CH1": "DP8B1519/CH1",
                "DP8B1519/CH2": "DP8B1519/CH2",
            },
        )
        self.assertEqual(plan.power_settings["drain"]["voltage_v"], 28.0)


class AssemblyPlanBoundaryTests(unittest.TestCase):
    """计划的边界：配置 schema 的解释到计划为止，装配方只读计划。"""

    def test_plan_carries_no_raw_configuration_section(self):
        plan = hardware_assembly_plan(_config())

        # 计划只带显式事实；原始配置段（信号源配置、电源配置）留在配置边界内。
        for name in ("power_configs", "signal_config", "spectrum_config", "supply_details"):
            with self.subTest(field=name):
                self.assertFalse(hasattr(plan, name))

    def test_every_supply_plan_carries_what_construction_needs(self):
        plan = hardware_assembly_plan(_config())

        (supply,) = plan.supplies
        self.assertEqual(supply.supply_id, "DP8B1519")
        self.assertEqual(supply.address, SUPPLY_A)
        self.assertEqual(supply.label, "DP8B1519")
        self.assertEqual(supply.declared_channels, ("CH1", "CH2"))
        # 详情/审计文本在计划里算一次；身份字段本身也带上，供身份核对使用。
        self.assertEqual(supply.identity_text, "DP8B1519")
        self.assertIsNone(supply.serial)

    def test_plan_is_read_only(self):
        plan = hardware_assembly_plan(_config())

        with self.assertRaises(AttributeError):
            plan.supplies[0].address = "TCPIP0::0.0.0.0::INSTR"
        with self.assertRaises(TypeError):
            plan.all_channels["gate"] = "DP8B1519/CH9"

    def test_factory_never_reads_configuration_keys(self):
        # 构造方只消费计划：一旦它自己解释配置字段，这条边界就破了。
        source = FACTORY_SOURCE.read_text(encoding="utf-8")
        for forbidden in (
            '["channels"]',
            '["address"]',
            "power_configs",
            "signal_config",
            "configured_channels",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


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

    def test_missing_power_address_fails(self):
        config = _config()
        del config["instruments"]["power_supplies"]["DP8B1519"]["address"]
        with self.assertRaisesRegex(ValueError, "生产电源 DP8B1519 必须配置 address"):
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
            plan.emergency_channels,
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
            [token for _index, token in plan.emergency_channels],
            ["DP8B1519/CH2", "DP8B1519/CH1"],
        )
        # 但初始输出检查要覆盖**声明**的全部通道：CH3 也在供电前被查一遍。
        self.assertEqual(plan.supplies[0].declared_channels, ("CH1", "CH2", "CH3"))


class SharedHelperTests(unittest.TestCase):
    """短标签与通道判据各只有一份实现。"""

    def test_short_label_prefers_the_configured_label(self):
        self.assertEqual(supply_short_label("  DP8B1519 ", "key"), "DP8B1519")
        self.assertEqual(supply_short_label("   ", "key"), "key")
        self.assertEqual(supply_short_label(None, "key"), "key")
        self.assertEqual(supply_short_label({"label": "x"}, "key"), "key")

    def test_power_roles_are_the_two_declared_roles(self):
        self.assertEqual(POWER_ROLES, ("gate", "drain"))

    def test_declared_channels_requires_a_non_empty_object(self):
        with self.assertRaisesRegex(ValueError, "必须配置非空通道对象"):
            declared_channels({"channels": {}}, "DP8B1519")
        with self.assertRaisesRegex(ValueError, "必须配置非空通道对象"):
            declared_channels({}, "DP8B1519")
        self.assertEqual(
            declared_channels({"channels": {"CH1": {}}}, "DP8B1519"), {"CH1": {}}
        )


if __name__ == "__main__":
    unittest.main()
