r"""步骤 4 测试：有序通道计划与可选漏极路由的值对象。

覆盖四类契约：

1. **顺序保留**：``power_sequence`` 的列表顺序就是上电顺序，读写往返不排序、不合并。
2. **路由与重复**：只接受显式 ``supply_id + channel`` 路由；文档解析只接受两段对象，
   拼接串只能显式走 ``from_routing``；同一物理路由不能出现两次。
3. **间隔边界**：有限、``0 <= d <= MAX_INTER_CHANNEL_DELAY_S``，超界不裁剪、直接失败。
4. **漏极**：可选（未配置合法），已配置时必须属于本次配置里存在的通道；值对象不强制
   主功放运行必须有漏极。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_power_plan -v
```
"""

import unittest
from collections import deque
from dataclasses import FrozenInstanceError

from application.dto import (
    AMPLIFIER_COMPRESSION_TARGETS_DB,
    AmplifierInputError,
    AmplifierMeasurementInputs,
    DrainChannel,
    MAX_INTER_CHANNEL_DELAY_S,
    PowerPlan,
    PowerSweep,
    SupplyChannel,
)
from application.errors import ConfigurationError


def _route(supply_id, channel):
    return SupplyChannel(supply_id=supply_id, channel=channel)


class SupplyChannelDocumentTests(unittest.TestCase):
    """路由的两段对象是 v1.1 唯一正式形状，拼接串只在读取边界出现。"""

    def test_mapping_is_parsed_into_explicit_parts(self):
        route = SupplyChannel.from_document(
            {"supply_id": " DP8B1519 ", "channel": " CH2 "}
        )

        self.assertEqual(route, SupplyChannel("DP8B1519", "CH2"))
        self.assertEqual(route.to_document(), {"supply_id": "DP8B1519", "channel": "CH2"})

    def test_existing_route_object_is_accepted_for_reading(self):
        route = SupplyChannel("PS2", "CH3")

        self.assertIs(SupplyChannel.from_document(route), route)

    def test_joined_text_is_only_accepted_by_the_routing_entry(self):
        """规范文档解析不接受拼接串；历史运行映射写法只能显式走 from_routing。"""
        for value in ("PS1/CH1", " PS1 / CH1 "):
            with self.subTest(value=value):
                with self.assertRaises(AmplifierInputError):
                    SupplyChannel.from_document(value)

        self.assertEqual(
            SupplyChannel.from_routing("PS1/CH1"),
            SupplyChannel("PS1", "CH1"),
        )

    def test_mapping_must_have_exactly_the_two_route_fields(self):
        invalid = (
            {},
            {"supply_id": "PS1"},
            {"channel": "CH1"},
            {"supply_id": "PS1", "channel": "CH1", "role": "drain"},
            {"supply_id": "", "channel": "CH1"},
            {"supply_id": "PS1", "channel": "   "},
            {"supply_id": "PS1/PS2", "channel": "CH1"},
            {"supply_id": "PS1", "channel": "CH1/A"},
        )
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(AmplifierInputError):
                    SupplyChannel.from_document(value)

    def test_non_route_values_are_rejected(self):
        for value in (1, True, 1.0, ["PS1", "CH1"], ("PS1", "CH1")):
            with self.subTest(value=value):
                with self.assertRaises(AmplifierInputError):
                    SupplyChannel.from_document(value)


class PowerPlanTests(unittest.TestCase):
    """组计划：顺序、重复、间隔与文档形状。"""

    def test_sequence_order_is_preserved(self):
        plan = PowerPlan(
            power_sequence=(
                _route("PS2", "CH1"),
                _route("PS1", "CH2"),
                _route("PS1", "CH1"),
            ),
            inter_channel_delay_s=1.5,
        )

        self.assertEqual(
            plan.tokens, ("PS2/CH1", "PS1/CH2", "PS1/CH1")
        )
        self.assertEqual(plan.inter_channel_delay_s, 1.5)
        self.assertFalse(plan.is_empty)

    def test_plan_is_immutable(self):
        plan = PowerPlan(power_sequence=(_route("PS1", "CH1"),))

        with self.assertRaises(FrozenInstanceError):
            plan.power_sequence = ()

    def test_empty_plan_has_canonical_document(self):
        expected = {"power_sequence": [], "inter_channel_delay_s": 0.0}

        self.assertEqual(PowerPlan.empty().to_document(), expected)
        self.assertEqual(PowerPlan().to_document(), expected)
        self.assertEqual(PowerPlan.from_document(None).to_document(), expected)
        self.assertEqual(PowerPlan.from_document({}).to_document(), expected)
        self.assertTrue(PowerPlan.from_document(None).is_empty)

    def test_document_round_trip_keeps_order_and_normalizes_delay_to_float(self):
        document = {
            "power_sequence": [
                {"supply_id": "PS1", "channel": "CH2"},
                {"supply_id": "PS2", "channel": "CH1"},
            ],
            "inter_channel_delay_s": 2,
        }

        plan = PowerPlan.from_document(document)

        self.assertEqual(
            plan.power_sequence,
            (_route("PS1", "CH2"), _route("PS2", "CH1")),
        )
        self.assertIsInstance(plan.inter_channel_delay_s, float)
        self.assertEqual(plan.inter_channel_delay_s, 2.0)
        self.assertEqual(
            plan.to_document(),
            {
                "power_sequence": [
                    {"supply_id": "PS1", "channel": "CH2"},
                    {"supply_id": "PS2", "channel": "CH1"},
                ],
                "inter_channel_delay_s": 2.0,
            },
        )

    def test_missing_sequence_or_delay_is_normalized_without_inference(self):
        self.assertEqual(
            PowerPlan.from_document({"inter_channel_delay_s": 3}).to_document(),
            {"power_sequence": [], "inter_channel_delay_s": 3.0},
        )
        self.assertEqual(
            PowerPlan.from_document({"power_sequence": []}).to_document(),
            {"power_sequence": [], "inter_channel_delay_s": 0.0},
        )

    def test_duplicate_route_is_rejected(self):
        duplicate = _route("PS1", "CH1")

        with self.assertRaises(AmplifierInputError):
            PowerPlan(power_sequence=(duplicate, duplicate))

        with self.assertRaises(AmplifierInputError):
            PowerPlan.from_document(
                {
                    "power_sequence": [
                        {"supply_id": "PS1", "channel": "CH1"},
                        {"supply_id": "PS1", "channel": "CH1"},
                    ]
                }
            )

    def test_same_channel_name_on_different_supplies_is_not_duplicate(self):
        plan = PowerPlan(
            power_sequence=(_route("PS1", "CH1"), _route("PS2", "CH1"))
        )

        self.assertEqual(plan.tokens, ("PS1/CH1", "PS2/CH1"))

    def test_sequence_items_must_be_routes(self):
        for value in ("PS1/CH1", [{"supply_id": "PS1", "channel": "CH1"}]):
            with self.subTest(value=value):
                with self.assertRaises(AmplifierInputError):
                    PowerPlan(power_sequence=value)

        with self.assertRaises(AmplifierInputError):
            PowerPlan(power_sequence=(_route("PS1", "CH1"), "PS2/CH1"))

    def test_document_sequence_items_must_be_route_objects(self):
        """``power_sequence`` 的元素也只能是两段对象，拼接串不被隐式升级。"""
        for items in (
            ["PS1/CH1"],
            [{"supply_id": "PS1"}],
            [{"supply_id": "PS1", "channel": "CH1", "role": "drain"}],
            [1],
        ):
            with self.subTest(items=items):
                with self.assertRaises(AmplifierInputError):
                    PowerPlan.from_document({"power_sequence": items})

    def test_only_list_and_tuple_are_accepted_as_ordered_containers(self):
        """白名单是刻意的：其它容器必须由调用方显式转换，不能悄悄定序。"""
        route = _route("PS1", "CH1")
        for value in (
            {route},
            frozenset({route}),
            (route for _ in range(1)),
            deque([route]),
        ):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(AmplifierInputError):
                    PowerPlan(power_sequence=value)

        self.assertEqual(
            PowerPlan(power_sequence=[route]).power_sequence, (route,)
        )
        self.assertEqual(
            PowerPlan(power_sequence=(route,)).power_sequence, (route,)
        )

    def test_document_sequence_must_be_a_sequence_not_text(self):
        for value in (
            "PS1/CH1",
            {"supply_id": "PS1", "channel": "CH1"},
            {("PS1", "CH1")},
            iter([{"supply_id": "PS1", "channel": "CH1"}]),
            1,
        ):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(AmplifierInputError):
                    PowerPlan.from_document({"power_sequence": value})


    def test_delay_range_and_type_boundaries(self):
        for value in (0, 0.0, 1, 60, MAX_INTER_CHANNEL_DELAY_S):
            with self.subTest(value=value):
                plan = PowerPlan(inter_channel_delay_s=value)
                self.assertIsInstance(plan.inter_channel_delay_s, float)
                self.assertEqual(plan.inter_channel_delay_s, float(value))

        for value in (
            -0.001,
            60.001,
            float("inf"),
            float("nan"),
            True,
            "2",
            None,
        ):
            with self.subTest(value=value):
                with self.assertRaises(AmplifierInputError):
                    PowerPlan(inter_channel_delay_s=value)

    def test_single_channel_plan_ignores_delay_but_still_checks_range(self):
        plan = PowerPlan(
            power_sequence=(_route("PS1", "CH1"),),
            inter_channel_delay_s=MAX_INTER_CHANNEL_DELAY_S,
        )
        self.assertEqual(plan.inter_channel_delay_s, MAX_INTER_CHANNEL_DELAY_S)

        with self.assertRaises(AmplifierInputError):
            PowerPlan(
                power_sequence=(_route("PS1", "CH1"),),
                inter_channel_delay_s=MAX_INTER_CHANNEL_DELAY_S + 1,
            )

    def test_plan_does_not_require_a_drain(self):
        self.assertTrue(PowerPlan.from_document(None).is_empty)


class DrainChannelTests(unittest.TestCase):
    """漏极是可选测量路由；已配置时只校验“存在于本次配置的通道”。"""


    def test_document_round_trip_keeps_the_route(self):
        drain = DrainChannel.from_document(
            {"supply_id": " PS1 ", "channel": " CH2 "}
        )

        self.assertTrue(drain.is_configured)
        self.assertEqual(drain.routes, (_route("PS1", "CH2"),))
        self.assertEqual(
            drain.to_document(), {"supply_id": "PS1", "channel": "CH2"}
        )

    def test_multiple_routes_round_trip_as_a_list(self):
        drain = DrainChannel.from_document(
            [
                {"supply_id": "PS1", "channel": "CH2"},
                {"supply_id": "PS2", "channel": "CH1"},
            ]
        )

        self.assertTrue(drain.is_configured)
        self.assertEqual(
            drain.routes, (_route("PS1", "CH2"), _route("PS2", "CH1"))
        )
        self.assertEqual(
            drain.to_document(),
            [
                {"supply_id": "PS1", "channel": "CH2"},
                {"supply_id": "PS2", "channel": "CH1"},
            ],
        )


    def test_parsing_does_not_imply_membership_validation(self):
        """解析只做形状；归属判断必须由调用方显式调用 require_in_routes。"""
        drain = DrainChannel.from_document(
            {"supply_id": "PS9", "channel": "CH4"}
        )

        self.assertEqual(drain.routes, (_route("PS9", "CH4"),))
        with self.assertRaises(AmplifierInputError):
            drain.require_in_routes((_route("PS1", "CH1"),))

    def test_configured_drain_must_exist_in_given_routes(self):
        available = (_route("PS1", "CH1"), _route("PS2", "CH2"))

        accepted = DrainChannel.from_document(
            {"supply_id": "PS2", "channel": "CH2"}, available=available
        )
        self.assertEqual(accepted.routes, (_route("PS2", "CH2"),))

        with self.assertRaises(AmplifierInputError):
            DrainChannel.from_document(
                {"supply_id": "PS2", "channel": "CH1"}, available=available
            )
        with self.assertRaises(AmplifierInputError):
            DrainChannel(
                routes=(_route("PS2", "CH1"),)
            ).require_in_routes(available)

    def test_given_routes_must_be_a_route_collection(self):
        drain = DrainChannel(routes=(_route("PS1", "CH1"),))

        for value in ("PS1/CH1", {"supply_id": "PS1", "channel": "CH1"}, 1):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(AmplifierInputError):
                    drain.require_in_routes(value)

    def test_invalid_route_is_rejected(self):
        for value in (
            {},
            {"supply_id": "PS1"},
            {"supply_id": "PS1", "channel": "CH1", "role": "drain"},
            {"supply_id": "", "channel": "CH1"},
            "CH1",
            1,
        ):
            with self.subTest(value=value):
                with self.assertRaises(AmplifierInputError):
                    DrainChannel.from_document(value)

    def test_route_collection_requires_supply_channels(self):
        with self.assertRaises(AmplifierInputError):
            DrainChannel(routes=({"supply_id": "PS1", "channel": "CH1"},))
        with self.assertRaises(AmplifierInputError):
            DrainChannel(routes=("PS1/CH1",))

    def test_drain_is_immutable(self):
        drain = DrainChannel(routes=(_route("PS1", "CH1"),))

        with self.assertRaises(FrozenInstanceError):
            drain.routes = ()


class PowerPlanExportTests(unittest.TestCase):
    def test_value_objects_are_exported_from_the_dto_package(self):
        import application.dto as dto

        for name in (
            "AMPLIFIER_COMPRESSION_TARGETS_DB",
            "AmplifierInputError",
            "AmplifierMeasurementInputs",
            "ChannelSetting",
            "DrainChannel",
            "MAX_INTER_CHANNEL_DELAY_S",
            "PowerPlan",
            "PowerSweep",
            "SupplyChannel",
        ):
            with self.subTest(name=name):
                self.assertIn(name, dto.__all__)
                self.assertTrue(hasattr(dto, name))

    def test_plan_errors_are_configuration_failures(self):
        with self.assertRaises(ConfigurationError):
            PowerPlan(power_sequence=(_route("PS1", "CH1"),) * 2)

    def test_delay_limit_has_a_single_source_in_domain(self):
        """上限由 domain 拥有，application 只再导出，避免两处各写一个数值。"""
        from domain.configuration.models import (
            MAX_INTER_CHANNEL_DELAY_S as domain_limit,
        )

        self.assertIs(domain_limit, MAX_INTER_CHANNEL_DELAY_S)
        self.assertEqual(domain_limit, 60.0)


if __name__ == "__main__":
    unittest.main()
