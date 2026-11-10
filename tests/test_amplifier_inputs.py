"""步骤 7 测试：主功放正式输入模型的字段、单位、必填性、非法值处理与运行配置转换。

覆盖四类契约：

1. **字段与单位**：频率是 ``Frequency``（GHz）、功率扫描起始/终止是 ``PowerDbm``、
   步进是 ``Decibels``、压缩目标与衰减器是 ``Decibels``、DUT 限制是 ``PowerDbm``，
   DUT 供电是「有序路由计划 + 每路由通道参数 + 可选漏极」。
2. **必填性**：字段都必填，缺字段是构造期的 ``TypeError``（dataclass 语义），
   显式传 ``None`` 是构造期的 ``AmplifierInputError``。
3. **非法值处理**：空/重复频率、起始大于终止、非正步进、不支持的压缩目标、负衰减器、
   非布尔驱动开关、空计划、缺参数、多余参数、漏极不在序列里都抛
   ``AmplifierInputError``，并且归类为配置失败。
4. **计划语义**：顺序原样保留，通道参数按物理路由索引，漏极精确指向一条路由；
   输入对象不可变，不提供字典键访问。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_amplifier_inputs -v
```
"""

import unittest
from collections.abc import Mapping
from dataclasses import FrozenInstanceError

from application.dto import (
    AMPLIFIER_COMPRESSION_TARGETS_DB,
    AmplifierInputError,
    AmplifierMeasurementInputs,
    ChannelSetting,
    DrainChannel,
    PowerPlan,
    PowerScanSchedule,
    PowerSweep,
    SupplyChannel,
)
from application.errors import ConfigurationError, ErrorCategory
from domain.configuration.models import SUPPORTED_COMPRESSION_POINTS
from domain.measurement.values import Decibels, Frequency, PowerDbm


def _route(supply_id="PS1", channel="CH1"):
    return SupplyChannel(supply_id=supply_id, channel=channel)


def _settings(*routes, voltage_v=28.0, current_a=1.0):
    return {
        route: ChannelSetting(voltage_v=voltage_v, current_a=current_a)
        for route in routes
    }


def _inputs(**overrides):
    """构造一份合法的输入，按需覆盖单个字段。"""
    routes = (_route("PS1", "CH1"), _route("PS1", "CH2"))
    values = {
        "frequencies": (Frequency.from_ghz(1.0), Frequency.from_ghz(2.4)),
        "power_scan_schedule": PowerScanSchedule(
            plans=(
                PowerSweep(
                    start=PowerDbm(-20.0), stop=PowerDbm(0.0), step=Decibels(1.0)
                ),
            )
        ),
        "compression_target": Decibels(1.0),
        "attenuator": Decibels(10.0),
        "max_dut_input_power": PowerDbm(30.0),
        "driver_enabled": False,
        "dut_power_plan": PowerPlan(power_sequence=routes),
        "channel_settings": _settings(*routes),
        "drain_channel": DrainChannel(routes=(routes[-1],)),
    }
    values.update(overrides)
    return AmplifierMeasurementInputs(**values)


class SupplyChannelTests(unittest.TestCase):
    def test_routing_token_is_parsed_into_explicit_parts(self):
        channel = SupplyChannel.from_routing("DP8B1519/CH1")

        self.assertEqual(channel.supply_id, "DP8B1519")
        self.assertEqual(channel.channel, "CH1")
        self.assertEqual(channel.token, "DP8B1519/CH1")
        self.assertEqual(str(channel), "DP8B1519/CH1")

    def test_parts_are_trimmed(self):
        channel = SupplyChannel.from_routing("  PS1 / CH3  ")

        self.assertEqual((channel.supply_id, channel.channel), ("PS1", "CH3"))

    def test_bare_channel_name_is_not_guessed(self):
        """裸通道名是旧快照写法，正式输入模型不按“唯一那台电源”补前缀。"""
        with self.assertRaises(AmplifierInputError) as raised:
            SupplyChannel.from_routing("CH1")

        self.assertIn("supply_id/通道名", str(raised.exception))

    def test_routing_must_have_both_parts(self):
        for token in ("PS1/", "/CH1", "/", "PS1", "", "   "):
            with self.subTest(token=token):
                with self.assertRaises(AmplifierInputError):
                    SupplyChannel.from_routing(token)

    def test_parts_must_be_non_empty_text_without_separator(self):
        with self.assertRaises(AmplifierInputError):
            SupplyChannel(supply_id="", channel="CH1")
        with self.assertRaises(AmplifierInputError):
            SupplyChannel(supply_id="PS1", channel="   ")
        with self.assertRaises(AmplifierInputError):
            SupplyChannel(supply_id="PS1/PS2", channel="CH1")
        with self.assertRaises(AmplifierInputError):
            SupplyChannel(supply_id="PS1", channel="CH1/A")
        with self.assertRaises(AmplifierInputError):
            SupplyChannel(supply_id=1, channel="CH1")

    def test_placeholder_values_are_rejected(self):
        """配置里的占位值（``无`` / ``待填写`` 等）不是实际电源或通道。"""
        for value in ("无", "待填写", "<channel_name>"):
            with self.subTest(value=value):
                with self.assertRaises(AmplifierInputError):
                    SupplyChannel(supply_id=value, channel="CH1")
                with self.assertRaises(AmplifierInputError):
                    SupplyChannel(supply_id="PS1", channel=value)


class ChannelSettingTests(unittest.TestCase):
    """每条物理路由的工作值/保护值；不携带顺序或角色。"""

    def test_document_reads_work_and_protection_values(self):
        setting = ChannelSetting.from_document(
            {
                "supply_id": "PS1",
                "channel": "CH2",
                "voltage": 28,
                "current": 1.5,
                "voltage_protection": 30,
                "current_protection": 2,
                "unit_voltage": "V",
                "unit_current": "A",
            }
        )

        self.assertEqual(setting.voltage_v, 28.0)
        self.assertEqual(setting.current_a, 1.5)
        self.assertEqual(setting.voltage_protection_v, 30.0)
        self.assertEqual(setting.current_protection_a, 2.0)

    def test_missing_work_value_is_rejected(self):
        for missing in ("voltage", "current"):
            with self.subTest(missing=missing):
                document = {
                    "supply_id": "PS1",
                    "channel": "CH1",
                    "voltage": 28,
                    "current": 1,
                }
                del document[missing]
                with self.assertRaises(AmplifierInputError):
                    ChannelSetting.from_document(document)

    def test_legacy_role_and_unknown_fields_are_rejected(self):
        for extra in ({"role": "drain"}, {"connection": "PS1-CH1 -> DUT 栅极"}, {"bogus": 1}):
            with self.subTest(extra=extra):
                document = {
                    "supply_id": "PS1",
                    "channel": "CH1",
                    "voltage": 28,
                    "current": 1,
                    **extra,
                }
                with self.assertRaises(AmplifierInputError):
                    ChannelSetting.from_document(document)

    def test_units_and_numeric_values_are_checked(self):
        base = {"supply_id": "PS1", "channel": "CH1", "voltage": 28, "current": 1}
        for overrides in (
            {"unit_voltage": "mV"},
            {"unit_current": "mA"},
            {"voltage": "28V"},
            {"current": True},
            {"voltage": float("nan")},
            {"current": float("inf")},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(AmplifierInputError):
                    ChannelSetting.from_document({**base, **overrides})

    def test_current_may_be_zero_but_never_negative(self):
        """与领域校验一致：电流允许 0，负数在直接构造与文档转换两条路径都失败。"""
        self.assertEqual(
            ChannelSetting(voltage_v=28.0, current_a=0.0).current_a, 0.0
        )

        for value in (-0.1, -1):
            with self.subTest(current=value):
                with self.assertRaises(AmplifierInputError) as raised:
                    ChannelSetting(voltage_v=28.0, current_a=value)
                self.assertIn("不能为负值", str(raised.exception))

        base = {"supply_id": "PS1", "channel": "CH1", "voltage": 28, "current": 1}
        for field in ("current", "current_protection"):
            with self.subTest(field=field):
                with self.assertRaises(AmplifierInputError):
                    ChannelSetting.from_document({**base, field: -1})

    def test_setting_is_immutable(self):
        setting = ChannelSetting(voltage_v=28.0, current_a=1.0)

        with self.assertRaises(FrozenInstanceError):
            setting.voltage_v = 30.0


class PowerSweepTests(unittest.TestCase):
    def test_units_are_expressed_by_value_objects(self):
        sweep = PowerSweep(
            start=PowerDbm(-20.0), stop=PowerDbm(0.0), step=Decibels(0.5)
        )

        self.assertEqual(sweep.start, PowerDbm(-20.0))
        self.assertEqual(sweep.stop, PowerDbm(0.0))
        self.assertEqual(sweep.step, Decibels(0.5))

    def test_bare_numbers_are_rejected_instead_of_being_guessed(self):
        for value in (-20.0, "-20", None, True):
            with self.subTest(value=value):
                with self.assertRaises(AmplifierInputError):
                    PowerSweep(start=value, stop=PowerDbm(0.0), step=Decibels(1.0))

    def test_step_must_be_positive(self):
        for step in (Decibels(0.0), Decibels(-1.0)):
            with self.subTest(step=step):
                with self.assertRaises(AmplifierInputError) as raised:
                    PowerSweep(
                        start=PowerDbm(-20.0), stop=PowerDbm(0.0), step=step
                    )
                self.assertIn("步进必须大于 0dB", str(raised.exception))

    def test_start_must_not_exceed_stop(self):
        with self.assertRaises(AmplifierInputError) as raised:
            PowerSweep(start=PowerDbm(0.0), stop=PowerDbm(-20.0), step=Decibels(1.0))

        self.assertIn("起始功率不能大于终止功率", str(raised.exception))

    def test_points_include_stop_when_interval_is_not_divisible(self):
        sweep = PowerSweep(
            start=PowerDbm(0.0), stop=PowerDbm(1.0), step=Decibels(0.6)
        )

        self.assertEqual([point.dbm for point in sweep.points()], [0.0, 0.6, 1.0])

    def test_single_point_sweep_has_one_point(self):
        sweep = PowerSweep(
            start=PowerDbm(-5.0), stop=PowerDbm(-5.0), step=Decibels(1.0)
        )

        self.assertEqual([point.dbm for point in sweep.points()], [-5.0])

    def test_points_include_stop_when_it_is_on_the_step_grid(self):
        sweep = PowerSweep(
            start=PowerDbm(0.0), stop=PowerDbm(0.3), step=Decibels(0.1)
        )

        self.assertAlmostEqual(sweep.points()[-1].dbm, 0.3)


class AmplifierMeasurementInputsTests(unittest.TestCase):
    def test_valid_input_exposes_business_fields_and_units(self):
        inputs = _inputs()

        self.assertEqual(
            [item.ghz for item in inputs.frequencies], [1.0, 2.4]
        )
        self.assertIsInstance(inputs.frequencies[0], Frequency)
        self.assertEqual(len(inputs.power_scan_schedule.plans), 1)
        self.assertEqual(inputs.power_scan_schedule.plans[0].start, PowerDbm(-20.0))
        self.assertEqual(inputs.power_scan_schedule.plans[0].step, Decibels(1.0))
        self.assertEqual(
            inputs.target_input_powers,
            tuple(PowerDbm(value) for value in range(-20, 1)),
        )
        self.assertEqual(inputs.compression_target, Decibels(1.0))
        self.assertEqual(inputs.attenuator, Decibels(10.0))
        self.assertEqual(inputs.max_dut_input_power, PowerDbm(30.0))
        self.assertFalse(inputs.driver_enabled)
        self.assertEqual(
            inputs.dut_power_plan.power_sequence,
            (_route("PS1", "CH1"), _route("PS1", "CH2")),
        )
        self.assertEqual(
            inputs.drain_channel.routes, (_route("PS1", "CH2"),)
        )

    def test_multiple_supplies_and_arbitrary_order_are_preserved(self):
        order = (
            _route("PS2", "CH2"),
            _route("PS1", "CH1"),
            _route("PS2", "CH1"),
        )
        settings = {
            order[0]: ChannelSetting(voltage_v=5.0, current_a=0.1),
            order[1]: ChannelSetting(voltage_v=28.0, current_a=1.0),
            order[2]: ChannelSetting(voltage_v=3.3, current_a=0.2),
        }
        inputs = _inputs(
            dut_power_plan=PowerPlan(power_sequence=order, inter_channel_delay_s=2.5),
            channel_settings=settings,
            drain_channel=DrainChannel(routes=(order[1],)),
        )

        self.assertEqual(inputs.dut_power_plan.power_sequence, order)
        self.assertEqual(inputs.dut_power_plan.inter_channel_delay_s, 2.5)
        self.assertEqual(inputs.channel_settings[order[2]].voltage_v, 3.3)
        self.assertEqual(
            inputs.dut_supplies(), {"PS2": ("CH2", "CH1"), "PS1": ("CH1",)}
        )

    def test_drain_route_is_located_even_when_it_is_not_the_first_channel(self):
        order = (_route("PS1", "CH1"), _route("PS2", "CH2"), _route("PS1", "CH2"))
        inputs = _inputs(
            dut_power_plan=PowerPlan(power_sequence=order),
            channel_settings=_settings(*order),
            drain_channel=DrainChannel(routes=(_route("PS2", "CH2"),)),
        )

        self.assertTrue(inputs.drain_channel.is_configured)
        self.assertEqual(
            inputs.drain_channel.routes, (_route("PS2", "CH2"),)
        )

    def test_drain_is_optional_for_the_dto(self):
        inputs = _inputs(drain_channel=DrainChannel())

        self.assertFalse(inputs.drain_channel.is_configured)
        self.assertEqual(inputs.drain_channel.routes, ())

    def test_supplies_are_grouped_by_supply_id(self):
        order = (
            _route("PS1", "CH1"),
            _route("PS2", "CH1"),
            _route("PS1", "CH2"),
        )
        inputs = _inputs(
            dut_power_plan=PowerPlan(power_sequence=order),
            channel_settings=_settings(*order),
            drain_channel=DrainChannel(),
        )

        supplies = inputs.dut_supplies()

        self.assertIsInstance(supplies, Mapping)
        self.assertEqual(supplies, {"PS1": ("CH1", "CH2"), "PS2": ("CH1",)})
        with self.assertRaises(TypeError):
            supplies["PS1"] = ()

    def test_all_fields_are_required(self):
        routes = (_route("PS1", "CH1"),)
        valid = {
            "frequencies": (Frequency.from_ghz(1.0),),
            "power_scan_schedule": PowerScanSchedule(
                plans=(
                    PowerSweep(
                        start=PowerDbm(-20.0),
                        stop=PowerDbm(0.0),
                        step=Decibels(1.0),
                    ),
                )
            ),
            "compression_target": Decibels(1.0),
            "attenuator": Decibels(10.0),
            "max_dut_input_power": PowerDbm(30.0),
            "driver_enabled": False,
            "dut_power_plan": PowerPlan(power_sequence=routes),
            "channel_settings": _settings(*routes),
            "drain_channel": DrainChannel(routes=(routes[0],)),
        }
        for name in valid:
            with self.subTest(missing=name):
                remaining = {key: value for key, value in valid.items() if key != name}
                with self.assertRaises(TypeError):
                    AmplifierMeasurementInputs(**remaining)

    def test_explicit_none_is_rejected(self):
        for name in (
            "frequencies",
            "power_scan_schedule",
            "compression_target",
            "attenuator",
            "max_dut_input_power",
            "dut_power_plan",
            "channel_settings",
            "drain_channel",
        ):
            with self.subTest(field=name):
                with self.assertRaises(AmplifierInputError):
                    _inputs(**{name: None})

    def test_frequencies_must_be_unique_and_positive(self):
        with self.assertRaises(AmplifierInputError) as empty:
            _inputs(frequencies=())
        self.assertIn("不能为空", str(empty.exception))

        with self.assertRaises(AmplifierInputError) as duplicate:
            _inputs(
                frequencies=(Frequency.from_ghz(1.0), Frequency.from_ghz(1.0))
            )
        self.assertIn("不能重复", str(duplicate.exception))

        with self.assertRaises(AmplifierInputError):
            _inputs(frequencies=(Frequency.from_ghz(0.0),))

        with self.assertRaises(AmplifierInputError):
            _inputs(frequencies=(1.0,))

        with self.assertRaises(AmplifierInputError):
            _inputs(frequencies="1.0")

    def test_compression_target_must_match_the_supported_set(self):
        allowed = {Decibels(value) for value in AMPLIFIER_COMPRESSION_TARGETS_DB}
        for value in allowed:
            with self.subTest(target=value):
                self.assertEqual(
                    _inputs(compression_target=value).compression_target, value
                )

        with self.assertRaises(AmplifierInputError) as raised:
            _inputs(compression_target=Decibels(2.0))
        self.assertIn("压缩目标必须是", str(raised.exception))

    def test_compression_targets_share_the_plan_schema_values(self):
        self.assertEqual(
            {Decibels.parse(text).db for text in SUPPORTED_COMPRESSION_POINTS},
            set(AMPLIFIER_COMPRESSION_TARGETS_DB),
        )

    def test_attenuator_must_not_be_negative(self):
        self.assertEqual(_inputs(attenuator=Decibels(0.0)).attenuator, Decibels(0.0))

        with self.assertRaises(AmplifierInputError) as raised:
            _inputs(attenuator=Decibels(-1.0))
        self.assertIn("不能为负值", str(raised.exception))

    def test_driver_mode_must_be_a_boolean(self):
        for value in (1, 0, "true", None):
            with self.subTest(value=value):
                with self.assertRaises(AmplifierInputError):
                    _inputs(driver_enabled=value)

        self.assertTrue(_inputs(driver_enabled=True).driver_enabled)

    def test_plan_must_be_non_empty_and_unique(self):
        with self.assertRaises(AmplifierInputError) as empty:
            _inputs(
                dut_power_plan=PowerPlan.empty(),
                channel_settings={},
                drain_channel=DrainChannel(),
            )
        self.assertIn("至少有一路", str(empty.exception))

        with self.assertRaises(AmplifierInputError):
            PowerPlan(
                power_sequence=(_route("PS1", "CH1"), _route("PS1", "CH1"))
            )

        with self.assertRaises(AmplifierInputError):
            PowerPlan(power_sequence=("PS1/CH1",))

    def test_missing_channel_setting_is_rejected(self):
        routes = (_route("PS1", "CH1"), _route("PS1", "CH2"))

        with self.assertRaises(AmplifierInputError) as raised:
            _inputs(
                dut_power_plan=PowerPlan(power_sequence=routes),
                channel_settings=_settings(routes[0]),
                drain_channel=DrainChannel(routes=(routes[0],)),
            )

        self.assertIn("缺少电压/电流设置", str(raised.exception))
        self.assertIn("PS1/CH2", str(raised.exception))

    def test_setting_outside_the_sequence_is_rejected(self):
        routes = (_route("PS1", "CH1"),)
        settings = _settings(*routes, _route("PS2", "CH1"))

        with self.assertRaises(AmplifierInputError) as raised:
            _inputs(
                dut_power_plan=PowerPlan(power_sequence=routes),
                channel_settings=settings,
                drain_channel=DrainChannel(routes=(routes[0],)),
            )

        self.assertIn("未列入上电顺序", str(raised.exception))

    def test_channel_settings_must_be_keyed_by_routes_and_settings(self):
        routes = (_route("PS1", "CH1"),)
        with self.assertRaises(AmplifierInputError):
            _inputs(channel_settings=[ChannelSetting(voltage_v=28, current_a=1)])
        with self.assertRaises(AmplifierInputError):
            _inputs(channel_settings={"PS1/CH1": ChannelSetting(voltage_v=28, current_a=1)})
        with self.assertRaises(AmplifierInputError):
            _inputs(channel_settings={routes[0]: {"voltage": 28, "current": 1}})

    def test_drain_must_be_in_the_power_sequence(self):
        routes = (_route("PS1", "CH1"), _route("PS1", "CH2"))

        with self.assertRaises(AmplifierInputError) as raised:
            _inputs(
                dut_power_plan=PowerPlan(power_sequence=routes),
                channel_settings=_settings(*routes),
                drain_channel=DrainChannel(routes=(_route("PS2", "CH1"),)),
            )

        self.assertIn("不在给定的通道集合中", str(raised.exception))

    def test_same_channel_number_on_different_supplies_is_allowed(self):
        order = (_route("PS1", "CH1"), _route("PS2", "CH1"))
        inputs = _inputs(
            dut_power_plan=PowerPlan(power_sequence=order),
            channel_settings=_settings(*order),
            drain_channel=DrainChannel(routes=(order[0],)),
        )

        self.assertEqual(inputs.dut_supplies(), {"PS1": ("CH1",), "PS2": ("CH1",)})

    def test_channel_settings_are_immutable(self):
        inputs = _inputs()

        with self.assertRaises(TypeError):
            inputs.channel_settings[_route("PS1", "CH1")] = ChannelSetting(
                voltage_v=1.0, current_a=1.0
            )

    def test_input_object_is_immutable(self):
        inputs = _inputs()

        with self.assertRaises(FrozenInstanceError):
            inputs.attenuator = Decibels(20.0)
        self.assertEqual(inputs.attenuator, Decibels(10.0))

    def test_invalid_input_fails_at_construction(self):
        """非法输入在构造期失败：不依赖任何设备动作或运行目录。"""
        with self.assertRaises(AmplifierInputError):
            _inputs(power_scan_schedule={"start_power": -20, "stop_power": 0, "step": 1})

        with self.assertRaises(AmplifierInputError):
            _inputs(power_scan_schedule=PowerSweep(
                start=PowerDbm(-20.0), stop=PowerDbm(0.0), step=Decibels(1.0)
            ))

        with self.assertRaises(AmplifierInputError):
            _inputs(max_dut_input_power="30dBm")


class PowerScanScheduleInputTests(unittest.TestCase):
    """步骤 4.1：正式输入从单段扫描迁移到多段计划集合，构造期确定目标点。"""

    @staticmethod
    def _schedule(*plans):
        return PowerScanSchedule(
            plans=tuple(
                PowerSweep(
                    start=PowerDbm(start),
                    stop=PowerDbm(stop),
                    step=Decibels(step),
                )
                for start, step, stop in plans
            )
        )

    def test_multiple_plans_keep_their_order_and_merge_boundaries_once(self):
        inputs = _inputs(
            power_scan_schedule=self._schedule(
                (-10.0, 2.0, -6.0),
                (-6.0, 1.0, -3.0),
            )
        )

        self.assertEqual(len(inputs.power_scan_schedule.plans), 2)
        self.assertEqual(
            [point.dbm for point in inputs.target_input_powers],
            [-10.0, -8.0, -6.0, -5.0, -4.0, -3.0],
        )
        # 目标点顺序与配置列表顺序一致；公共边界 -6 只出现一次。
        self.assertEqual(
            [plan.start.dbm for plan in inputs.power_scan_schedule.plans],
            [-10.0, -6.0],
        )

    def test_gap_between_plans_fails_before_any_device_action(self):
        with self.assertRaises(AmplifierInputError) as raised:
            _inputs(
                power_scan_schedule=self._schedule(
                    (-10.0, 1.0, -6.0),
                    (-5.0, 1.0, -3.0),
                )
            )

        self.assertIn("空档", str(raised.exception))

    def test_overlap_between_plans_fails_before_any_device_action(self):
        with self.assertRaises(AmplifierInputError) as raised:
            _inputs(
                power_scan_schedule=self._schedule(
                    (-10.0, 1.0, -5.0),
                    (-6.0, 1.0, -3.0),
                )
            )

        self.assertIn("重叠", str(raised.exception))

    def test_target_grid_is_truncated_at_the_dut_limit(self):
        inputs = _inputs(
            power_scan_schedule=self._schedule((-10.0, 1.0, 0.0)),
            max_dut_input_power=PowerDbm(-7.5),
        )

        self.assertEqual(
            [point.dbm for point in inputs.target_input_powers], [-10.0, -9.0, -8.0, -7.5]
        )

    def test_start_above_the_effective_limit_is_rejected(self):
        with self.assertRaises(AmplifierInputError) as raised:
            _inputs(
                power_scan_schedule=self._schedule((-5.0, 1.0, 0.0)),
                max_dut_input_power=PowerDbm(-6.0),
            )

        self.assertIn("有效上限", str(raised.exception))

    def test_empty_schedule_is_rejected(self):
        with self.assertRaises(AmplifierInputError):
            _inputs(power_scan_schedule=PowerScanSchedule(plans=()))

    def test_schedule_is_frozen_and_targets_are_read_only(self):
        inputs = _inputs()

        with self.assertRaises(FrozenInstanceError):
            inputs.power_scan_schedule = self._schedule((-10.0, 1.0, -5.0))
        with self.assertRaises(AttributeError):
            inputs.target_input_powers = ()


class RunConfigurationInputTests(unittest.TestCase):
    def _configuration(self):
        from domain.configuration.models import (
            RunConfiguration,
            RunResourceMapping,
            TestPlan,
        )

        return RunConfiguration(
            test_plan=TestPlan(
                frequencies=[1.0, 2.4],
                start_power=-20.0,
                stop_power=0.0,
                power_step=1.0,
                power_reference="dut_input",
                compression_point="3dB",
                attenuator_value=30,
                max_input_power=30.0,
                driver_enabled=True,
                driver_gain_estimate_db=20.5,
                drain_channel={"supply_id": "PS1", "channel": "CH2"},
                channel_settings=[
                    {
                        "supply_id": "PS1",
                        "channel": "CH1",
                        "voltage": 2.8,
                        "current": 0.1,
                        "voltage_protection": 1.0,
                        "current_protection": 1.0,
                        "unit_voltage": "V",
                        "unit_current": "A",
                    },
                    {
                        "supply_id": "PS1",
                        "channel": "CH2",
                        "voltage": 28.0,
                        "current": 2.0,
                        "voltage_protection": 30.0,
                        "current_protection": 2.5,
                        "unit_voltage": "V",
                        "unit_current": "A",
                    },
                ],
            ),
            run_mapping=RunResourceMapping(
                instruments={},
                power_supplies={},
                dut_power_plan={
                    "power_sequence": [
                        {"supply_id": "PS1", "channel": "CH2"},
                        {"supply_id": "PS1", "channel": "CH1"},
                    ],
                    "inter_channel_delay_s": 2.0,
                },
            ),
        )

    def test_typed_configuration_is_converted_to_formal_inputs(self):
        inputs = AmplifierMeasurementInputs.from_run_configuration(self._configuration())

        self.assertEqual([item.ghz for item in inputs.frequencies], [1.0, 2.4])
        plan = inputs.power_scan_schedule.plans[0]
        self.assertEqual(plan.start, PowerDbm(-20.0))
        self.assertEqual(plan.stop, PowerDbm(0.0))
        self.assertEqual(plan.step, Decibels(1.0))
        self.assertEqual(
            inputs.target_input_powers,
            tuple(PowerDbm(value) for value in range(-20, 1)),
        )
        self.assertEqual(inputs.compression_target, Decibels(3.0))
        self.assertEqual(inputs.attenuator, Decibels(30.0))
        self.assertEqual(inputs.max_dut_input_power, PowerDbm(30.0))
        self.assertTrue(inputs.driver_enabled)
        self.assertEqual(inputs.driver_gain_estimate, Decibels(20.5))
        self.assertEqual(
            inputs.dut_power_plan.power_sequence,
            (_route("PS1", "CH2"), _route("PS1", "CH1")),
        )
        self.assertEqual(inputs.dut_power_plan.inter_channel_delay_s, 2.0)
        self.assertEqual(
            inputs.drain_channel.routes, (_route("PS1", "CH2"),)
        )
        self.assertEqual(inputs.channel_settings[_route("PS1", "CH2")].voltage_v, 28.0)
        self.assertEqual(inputs.dut_supplies(), {"PS1": ("CH2", "CH1")})

    def _with_plans(self, *plans):
        """用 1.2 的 ``power_scan_plans`` 替换单段兼容标量。"""
        from dataclasses import replace

        from domain.configuration.models import PowerScanPlan

        configuration = self._configuration()
        return replace(
            configuration,
            test_plan=replace(
                configuration.test_plan,
                start_power=None,
                stop_power=None,
                power_step=None,
                power_scan_plans=tuple(
                    PowerScanPlan(start_power=start, step=step, stop_power=stop)
                    for start, step, stop in plans
                ),
            ),
        )

    def test_multiple_power_scan_plans_are_converted_in_order(self):
        configuration = self._with_plans(
            (-20.0, 2.0, -14.0),
            (-14.0, 1.0, -11.0),
        )

        inputs = AmplifierMeasurementInputs.from_run_configuration(configuration)

        self.assertEqual(
            [
                (plan.start.dbm, plan.step.db, plan.stop.dbm)
                for plan in inputs.power_scan_schedule.plans
            ],
            [(-20.0, 2.0, -14.0), (-14.0, 1.0, -11.0)],
        )
        self.assertEqual(
            [point.dbm for point in inputs.target_input_powers],
            [-20.0, -18.0, -16.0, -14.0, -13.0, -12.0, -11.0],
        )

    def test_missing_power_scan_plans_fails_before_any_device_action(self):
        configuration = self._with_plans()

        with self.assertRaises(AmplifierInputError) as raised:
            AmplifierMeasurementInputs.from_run_configuration(configuration)

        self.assertIn("power_scan_plans", str(raised.exception))

    def test_non_numeric_plan_field_reports_its_index_and_field(self):
        configuration = self._with_plans(("bad", 1.0, 0.0))

        with self.assertRaises(AmplifierInputError) as raised:
            AmplifierMeasurementInputs.from_run_configuration(configuration)

        self.assertIn("signal_source.power_scan_plans[0].start_power", str(raised.exception))

    def test_reversed_plan_range_reports_its_plan_index(self):
        """单段参数关系错误也要带上计划下标，而不是只报“起始大于终止”。"""
        configuration = self._with_plans((-20.0, 1.0, -25.0))

        with self.assertRaises(AmplifierInputError) as raised:
            AmplifierMeasurementInputs.from_run_configuration(configuration)

        message = str(raised.exception)
        self.assertIn("signal_source.power_scan_plans[0]", message)
        self.assertIn("起始功率", message)

    def test_non_positive_plan_step_reports_its_plan_index(self):
        configuration = self._with_plans(
            (-30.0, 1.0, -20.0),
            (-20.0, 0.0, -10.0),
        )

        with self.assertRaises(AmplifierInputError) as raised:
            AmplifierMeasurementInputs.from_run_configuration(configuration)

        message = str(raised.exception)
        self.assertIn("signal_source.power_scan_plans[1]", message)
        self.assertIn("步进", message)

    def test_gap_between_configured_plans_fails_before_any_device_action(self):
        configuration = self._with_plans(
            (-20.0, 1.0, -15.0),
            (-14.0, 1.0, -10.0),
        )

        with self.assertRaises(AmplifierInputError) as raised:
            AmplifierMeasurementInputs.from_run_configuration(configuration)

        self.assertIn("空档", str(raised.exception))

    def test_unconfirmed_power_range_semantics_is_rejected(self):
        """旧配置未确认功率语义时，不能进入正式输入构造。"""
        from dataclasses import replace

        configuration = self._configuration()
        configuration = replace(
            configuration,
            test_plan=replace(configuration.test_plan, power_reference=None),
        )

        with self.assertRaises(AmplifierInputError) as raised:
            AmplifierMeasurementInputs.from_run_configuration(configuration)

        self.assertIn("功率范围语义", str(raised.exception))

    def test_unknown_power_range_semantics_is_rejected(self):
        from dataclasses import replace

        configuration = self._configuration()
        configuration = replace(
            configuration,
            test_plan=replace(
                configuration.test_plan, power_reference="source_output"
            ),
        )

        with self.assertRaises(AmplifierInputError) as raised:
            AmplifierMeasurementInputs.from_run_configuration(configuration)

        self.assertIn("source_output", str(raised.exception))

    def test_driver_gain_estimate_is_optional_and_type_checked(self):
        inputs = _inputs(driver_gain_estimate=Decibels(20.5))
        self.assertEqual(inputs.driver_gain_estimate, Decibels(20.5))
        self.assertIsNone(_inputs().driver_gain_estimate)

        with self.assertRaises(AmplifierInputError):
            _inputs(driver_gain_estimate=20.5)

    def test_missing_run_configuration_fails_before_any_device_action(self):
        for value in (object(), None):
            with self.subTest(value=value):
                with self.assertRaises(AmplifierInputError):
                    AmplifierMeasurementInputs.from_run_configuration(value)

    def test_invalid_typed_configuration_is_reported_as_input_error(self):
        from dataclasses import replace

        configuration = self._configuration()
        configuration = replace(
            configuration,
            test_plan=replace(configuration.test_plan, compression_point="2dB"),
        )

        with self.assertRaises(AmplifierInputError):
            AmplifierMeasurementInputs.from_run_configuration(configuration)

    def test_legacy_role_based_configuration_is_not_used_to_infer_order(self):
        """1.0 的 ``dut_power_channels`` 不再被读成上电顺序。"""
        from dataclasses import replace

        from domain.configuration.models import (
            ChannelMapping,
            RunResourceMapping,
        )

        configuration = self._configuration()
        configuration = replace(
            configuration,
            run_mapping=RunResourceMapping(
                instruments={},
                power_supplies={},
                dut_power_channels=[
                    ChannelMapping(channel="PS1/CH1", role="gate"),
                    ChannelMapping(channel="PS1/CH2", role="drain"),
                ],
            ),
        )

        with self.assertRaises(AmplifierInputError) as raised:
            AmplifierMeasurementInputs.from_run_configuration(configuration)

        self.assertIn("至少有一路", str(raised.exception))

    def test_bare_channel_without_supply_identity_is_rejected(self):
        """缺少电源身份的裸通道必须在正式输入转换边界失败。"""
        from dataclasses import replace

        from domain.configuration.models import RunResourceMapping

        configuration = self._configuration()
        configuration = replace(
            configuration,
            run_mapping=RunResourceMapping(
                instruments={},
                power_supplies={},
                dut_power_plan={
                    "power_sequence": [{"supply_id": "PS1", "channel": "  "}],
                    "inter_channel_delay_s": 0.0,
                },
            ),
            test_plan=replace(
                configuration.test_plan,
                channel_settings=[
                    {"supply_id": "PS1", "channel": "  ", "voltage": 28, "current": 1}
                ],
                drain_channel={"supply_id": "PS1", "channel": "  "},
            ),
        )

        with self.assertRaises(AmplifierInputError):
            AmplifierMeasurementInputs.from_run_configuration(configuration)

    def test_channel_without_a_name_is_rejected(self):
        from dataclasses import replace

        configuration = self._configuration()
        configuration = replace(
            configuration,
            test_plan=replace(
                configuration.test_plan,
                channel_settings=[
                    {"supply_id": "PS1", "channel": "  ", "voltage": 28, "current": 1}
                ],
                drain_channel={"supply_id": "PS1", "channel": "  "},
            ),
        )

        with self.assertRaises(AmplifierInputError):
            AmplifierMeasurementInputs.from_run_configuration(configuration)


class AmplifierInputErrorTaxonomyTests(unittest.TestCase):
    def test_input_error_is_a_configuration_failure(self):
        error = AmplifierInputError("测试频率不能为空")

        self.assertIsInstance(error, ConfigurationError)
        self.assertIsInstance(error, ValueError)
        self.assertIs(error.category, ErrorCategory.CONFIGURATION)
        self.assertEqual(str(error), "测试频率不能为空")

    def test_model_is_exported_from_the_dto_package(self):
        import application.dto as dto

        for name in (
            "AMPLIFIER_COMPRESSION_TARGETS_DB",
            "AmplifierInputError",
            "AmplifierMeasurementInputs",
            "ChannelSetting",
            "DrainChannel",
            "INPUT_POWER_REFERENCE",
            "PowerPlan",
            "PowerScanSchedule",
            "PowerSweep",
            "SupplyChannel",
        ):
            with self.subTest(name=name):
                self.assertIn(name, dto.__all__)
                self.assertTrue(hasattr(dto, name))

    def test_model_does_not_require_a_mapping_as_input(self):
        """正式输入类型是冻结 DTO，不是 ``Mapping[str, Any]``。"""
        self.assertFalse(issubclass(AmplifierMeasurementInputs, Mapping))
        with self.assertRaises(TypeError):
            AmplifierMeasurementInputs(
                **{
                    "test_frequencies": [1.0],
                    "signal_source": {"start_power": -20, "stop_power": 0, "step": 1},
                }
            )


if __name__ == "__main__":
    unittest.main()
