r"""步骤 5 测试：按物理路由校验上电计划与漏极，不再校验供电角色。

覆盖 ``domain.configuration.rules.validate_power_plan`` 的规则 5/7/15：

1. 计划形状：``power_plan`` 对象、两个键必需、两段路由、间隔范围；
2. 引用有效性：电源存在且启用、通道已声明；
3. 跨组冲突与驱放一致性；
4. 漏极只能出现在 DUT 上电顺序中。

计划本身就是“哪些电源的哪些通道给哪个器件上电”的唯一来源，不再与固定的供电分配
比较通道集合。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_power_plan_rules -v
```
"""

import copy
import unittest

from domain.configuration.rules import validate_power_plan


def _document():
    return {
        "driver_mode": {"enabled": False},
        "dut_config": {
            "drain_channel": {"supply_id": "PS1", "channel": "CH2"},
        },
        "instruments": {
            "power_supplies": {
                "PS1": {
                    "enabled": True,
                    "channels": {"CH1": {}, "CH2": {}},
                }
            }
        },
        "power_supply_assignment": {
            "dut_amplifier": {
                "power_plan": {
                    "power_sequence": [
                        {"supply_id": "PS1", "channel": "CH1"},
                        {"supply_id": "PS1", "channel": "CH2"},
                    ],
                    "inter_channel_delay_s": 2.0,
                },
            },
            "driver_amplifier": {
                "power_plan": {"power_sequence": [], "inter_channel_delay_s": 0.0},
            },
        },
    }


def _paths(result):
    return {issue.path for issue in result.errors}


class PowerPlanValidationTests(unittest.TestCase):
    def test_complete_plan_and_drain_are_valid(self):
        result = validate_power_plan(_document())
        self.assertTrue(result.valid, result.errors)

    def test_missing_plan_is_left_to_authoring_until_the_next_step(self):
        """过渡：编写器还未写 ``power_plan`` 时不按“空计划”误判整份配置。"""
        document = _document()
        document["power_supply_assignment"]["dut_amplifier"].pop("power_plan")
        self.assertTrue(validate_power_plan(document).valid)

    def test_plan_sequence_is_the_only_channel_set(self):
        """计划里没有的通道不参与上电，也不再与任何“固定分配”比较。"""
        document = _document()
        document["dut_config"].pop("drain_channel")
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ] = [{"supply_id": "PS1", "channel": "CH1"}]

        self.assertTrue(validate_power_plan(document).valid)

    def test_sequence_may_not_repeat_a_route(self):
        document = _document()
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ].append({"supply_id": "PS1", "channel": "CH1"})

        result = validate_power_plan(document)

        self.assertTrue(
            any("不能重复" in issue.message for issue in result.errors), result.errors
        )

    def test_route_must_point_at_a_configured_enabled_supply_and_channel(self):
        document = _document()
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ] = [
            {"supply_id": "PS9", "channel": "CH1"},
            {"supply_id": "PS1", "channel": "CH9"},
        ]

        result = validate_power_plan(document)

        messages = [issue.message for issue in result.errors]
        self.assertTrue(any("不存在的电源" in message for message in messages), messages)
        self.assertTrue(any("CH9" in message for message in messages), messages)

    def test_disabled_supply_in_the_plan_is_rejected(self):
        document = _document()
        document["instruments"]["power_supplies"]["PS1"]["enabled"] = False

        result = validate_power_plan(document)

        self.assertTrue(
            any("未启用" in issue.message for issue in result.errors), result.errors
        )

    def test_duplicate_physical_route_is_rejected(self):
        document = _document()
        duplicate = {"supply_id": "PS1", "channel": "CH1"}
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ] = [duplicate, duplicate]

        result = validate_power_plan(document)

        self.assertIn(
            "power_supply_assignment.dut_amplifier.power_plan.power_sequence",
            _paths(result),
        )

    def test_delay_must_be_finite_and_within_range(self):
        for value in (-0.1, 60.1, float("inf"), float("nan"), True, "2"):
            with self.subTest(value=value):
                document = _document()
                document["power_supply_assignment"]["dut_amplifier"]["power_plan"][
                    "inter_channel_delay_s"
                ] = value
                result = validate_power_plan(document)
                self.assertIn(
                    "power_supply_assignment.dut_amplifier.power_plan.inter_channel_delay_s",
                    _paths(result),
                )

    def test_unknown_plan_fields_are_rejected(self):
        document = _document()
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "drain_channel"
        ] = {"supply_id": "PS1", "channel": "CH2"}

        result = validate_power_plan(document)

        self.assertIn("power_supply_assignment.dut_amplifier.power_plan", _paths(result))

    def test_plan_must_be_an_object(self):
        document = _document()
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"] = []

        result = validate_power_plan(document)

        self.assertIn("power_supply_assignment.dut_amplifier.power_plan", _paths(result))

    def test_plan_must_declare_its_sequence(self):
        document = _document()
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"].pop(
            "power_sequence"
        )

        result = validate_power_plan(document)

        self.assertIn(
            "power_supply_assignment.dut_amplifier.power_plan.power_sequence",
            _paths(result),
        )

    def test_plan_must_declare_its_inter_channel_delay(self):
        document = _document()
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"].pop(
            "inter_channel_delay_s"
        )

        result = validate_power_plan(document)

        self.assertIn(
            "power_supply_assignment.dut_amplifier.power_plan.inter_channel_delay_s",
            _paths(result),
        )

    def test_empty_plan_still_declares_both_keys(self):
        """空计划也必须显式写出两个键，不能被当成“未配置”而跳过形状校验。"""
        document = _document()
        document["dut_config"].pop("drain_channel")
        assignment = document["power_supply_assignment"]["dut_amplifier"]
        assignment["power_plan"] = {"power_sequence": []}

        result = validate_power_plan(document)

        self.assertIn(
            "power_supply_assignment.dut_amplifier.power_plan.inter_channel_delay_s",
            _paths(result),
        )

    def test_one_supply_may_serve_driver_and_dut_on_different_channels(self):
        """同一台电源 CH1 供驱放、CH2 供 DUT：计划已经表达清楚，不需要固定分配。"""
        document = _document()
        document["driver_mode"] = {"enabled": True, "power_source": "dc_supply"}
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ] = [{"supply_id": "PS1", "channel": "CH2"}]
        document["power_supply_assignment"]["driver_amplifier"]["power_plan"] = {
            "power_sequence": [{"supply_id": "PS1", "channel": "CH1"}],
            "inter_channel_delay_s": 0.0,
        }

        self.assertTrue(validate_power_plan(document).valid)

    def test_same_physical_route_cannot_appear_in_both_groups(self):
        document = _document()
        document["driver_mode"] = {"enabled": True, "power_source": "dc_supply"}
        document["instruments"]["power_supplies"]["PS2"] = {
            "enabled": True,
            "channels": {"CH1": {}},
        }
        document["power_supply_assignment"]["driver_amplifier"] = {
            "power_plan": {
                "power_sequence": [{"supply_id": "PS2", "channel": "CH1"}],
                "inter_channel_delay_s": 1.0,
            },
        }

        self.assertTrue(validate_power_plan(document).valid)

        document["power_supply_assignment"]["driver_amplifier"]["power_plan"][
            "power_sequence"
        ] = [{"supply_id": "PS1", "channel": "CH1"}]

        result = validate_power_plan(document)

        self.assertTrue(
            any("不能同时用于 DUT 与驱动功放" in issue.message for issue in result.errors),
            result.errors,
        )

    def test_driver_plan_must_match_enabled_and_power_source(self):
        document = _document()
        document["driver_mode"] = {"enabled": False}
        document["power_supply_assignment"]["driver_amplifier"]["power_plan"][
            "power_sequence"
        ] = [{"supply_id": "PS1", "channel": "CH1"}]

        result = validate_power_plan(document)

        self.assertIn(
            "power_supply_assignment.driver_amplifier.power_plan", _paths(result)
        )

    def test_external_driver_power_rejects_a_local_plan(self):
        document = _document()
        document["driver_mode"] = {"enabled": True, "power_source": "external_ac"}
        document["power_supply_assignment"]["driver_amplifier"]["power_plan"][
            "power_sequence"
        ] = [{"supply_id": "PS1", "channel": "CH1"}]

        result = validate_power_plan(document)

        self.assertTrue(
            any("自带供电" in issue.message for issue in result.errors), result.errors
        )

    def test_dc_driver_power_requires_a_non_empty_plan(self):
        document = _document()
        document["driver_mode"] = {"enabled": True, "power_source": "dc_supply"}
        document["power_supply_assignment"]["driver_amplifier"]["power_plan"] = {
            "power_sequence": [],
            "inter_channel_delay_s": 0.0,
        }

        result = validate_power_plan(document)

        self.assertTrue(
            any("必须配置驱动功放上电顺序" in issue.message for issue in result.errors),
            result.errors,
        )


class DrainReferenceTests(unittest.TestCase):
    def test_drain_is_optional(self):
        document = _document()
        document["dut_config"].pop("drain_channel")
        self.assertTrue(validate_power_plan(document).valid)

        document["dut_config"]["drain_channel"] = None
        self.assertTrue(validate_power_plan(document).valid)

    def test_drain_must_be_an_assigned_dut_route(self):
        document = _document()
        document["dut_config"]["drain_channel"] = {
            "supply_id": "PS1",
            "channel": "CH9",
        }

        result = validate_power_plan(document)

        self.assertIn("dut_config.drain_channel", _paths(result))

    def test_drain_must_be_in_the_dut_power_sequence(self):
        document = _document()
        document["power_supply_assignment"]["dut_amplifier"]["power_plan"][
            "power_sequence"
        ] = [{"supply_id": "PS1", "channel": "CH1"}]

        result = validate_power_plan(document)

        self.assertTrue(
            any(
                issue.path == "dut_config.drain_channel"
                and "上电顺序" in issue.message
                for issue in result.errors
            ),
            result.errors,
        )

    def test_drain_channel_may_not_be_a_driver_route(self):
        document = _document()
        document["driver_mode"] = {"enabled": True, "power_source": "dc_supply"}
        document["instruments"]["power_supplies"]["PS2"] = {
            "enabled": True,
            "channels": {"CH1": {}},
        }
        document["power_supply_assignment"]["driver_amplifier"] = {
            "power_plan": {
                "power_sequence": [{"supply_id": "PS2", "channel": "CH1"}],
                "inter_channel_delay_s": 0.0,
            },
        }
        document["dut_config"]["drain_channel"] = {
            "supply_id": "PS2",
            "channel": "CH1",
        }

        result = validate_power_plan(document)

        self.assertIn("dut_config.drain_channel", _paths(result))

    def test_drain_route_shape_is_validated(self):
        for value in (
            {"supply_id": "PS1"},
            {"supply_id": "PS1", "channel": "CH2", "role": "drain"},
            {"supply_id": "", "channel": "CH2"},
            {"supply_id": "PS1", "channel": "CH2/A"},
            "PS1/CH2",
        ):
            with self.subTest(value=value):
                document = _document()
                document["dut_config"]["drain_channel"] = value
                result = validate_power_plan(document)
                self.assertIn("dut_config.drain_channel", _paths(result))

    def test_role_free_channels_do_not_affect_the_plan(self):
        document = copy.deepcopy(_document())
        self.assertTrue(validate_power_plan(document).valid)
        # 通道条目本来就没有 role；再加一个旧 role 也不改变判定。
        document["instruments"]["power_supplies"]["PS1"]["channels"]["CH1"][
            "role"
        ] = "bias"
        self.assertTrue(validate_power_plan(document).valid)


if __name__ == "__main__":
    unittest.main()
