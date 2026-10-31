"""步骤 7/8 测试：主功放正式输入模型的字段、单位、必填性、非法值处理与运行配置转换。

覆盖四类契约：

1. **字段与单位**：频率是 ``Frequency``（GHz）、功率扫描起始/终止是 ``PowerDbm``、
   步进是 ``Decibels``、压缩目标与衰减器是 ``Decibels``、DUT 限制是 ``PowerDbm``，
   DUT 供电通道是显式的 ``supply_id + channel`` 路由。
2. **必填性**：七个字段都必填，缺字段是构造期的 ``TypeError``（dataclass 语义），
   显式传 ``None`` 是构造期的 ``AmplifierInputError``。
3. **非法值处理**：空/重复频率、起始大于终止、非正步进、不支持的压缩目标、负衰减器、
   非布尔驱动开关、空/重复/裸通道路由都抛 ``AmplifierInputError``，并且归类为配置失败。
4. **不与旧字典形状绑定**：压缩目标允许值与测试方案 schema 同源；供电通道按电源分组；
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
    PowerSweep,
    SupplyChannel,
)
from application.errors import ConfigurationError, ErrorCategory
from domain.configuration.models import SUPPORTED_COMPRESSION_POINTS
from domain.measurement.values import Decibels, Frequency, PowerDbm


def _inputs(**overrides):
    """构造一份合法的输入，按需覆盖单个字段。"""
    values = {
        "frequencies": (Frequency.from_ghz(1.0), Frequency.from_ghz(2.4)),
        "sweep": PowerSweep(
            start=PowerDbm(-20.0), stop=PowerDbm(0.0), step=Decibels(1.0)
        ),
        "compression_target": Decibels(1.0),
        "attenuator": Decibels(10.0),
        "max_dut_input_power": PowerDbm(30.0),
        "driver_enabled": False,
        "dut_power_channels": (
            SupplyChannel(supply_id="PS1", channel="CH1"),
            SupplyChannel(supply_id="PS1", channel="CH2"),
        ),
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

    def test_points_do_not_cross_stop_when_interval_is_not_divisible(self):
        sweep = PowerSweep(
            start=PowerDbm(0.0), stop=PowerDbm(1.0), step=Decibels(0.6)
        )

        self.assertEqual([point.dbm for point in sweep.points()], [0.0, 0.6])

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
        self.assertEqual(inputs.sweep.start, PowerDbm(-20.0))
        self.assertEqual(inputs.sweep.step, Decibels(1.0))
        self.assertEqual(inputs.compression_target, Decibels(1.0))
        self.assertEqual(inputs.attenuator, Decibels(10.0))
        self.assertEqual(inputs.max_dut_input_power, PowerDbm(30.0))
        self.assertFalse(inputs.driver_enabled)
        self.assertEqual(
            [(item.supply_id, item.channel) for item in inputs.dut_power_channels],
            [("PS1", "CH1"), ("PS1", "CH2")],
        )

    def test_supplies_are_grouped_by_supply_id(self):
        inputs = _inputs(
            dut_power_channels=(
                SupplyChannel(supply_id="PS1", channel="CH1"),
                SupplyChannel(supply_id="PS2", channel="CH1"),
                SupplyChannel(supply_id="PS1", channel="CH2"),
            )
        )

        supplies = inputs.dut_supplies()

        self.assertIsInstance(supplies, Mapping)
        self.assertEqual(supplies, {"PS1": ("CH1", "CH2"), "PS2": ("CH1",)})
        with self.assertRaises(TypeError):
            supplies["PS1"] = ()

    def test_all_fields_are_required(self):
        valid = {
            "frequencies": (Frequency.from_ghz(1.0),),
            "sweep": PowerSweep(
                start=PowerDbm(-20.0), stop=PowerDbm(0.0), step=Decibels(1.0)
            ),
            "compression_target": Decibels(1.0),
            "attenuator": Decibels(10.0),
            "max_dut_input_power": PowerDbm(30.0),
            "driver_enabled": False,
            "dut_power_channels": (SupplyChannel(supply_id="PS1", channel="CH1"),),
        }
        for name in valid:
            with self.subTest(missing=name):
                remaining = {key: value for key, value in valid.items() if key != name}
                with self.assertRaises(TypeError):
                    AmplifierMeasurementInputs(**remaining)

    def test_explicit_none_is_rejected(self):
        for name in (
            "frequencies",
            "sweep",
            "compression_target",
            "attenuator",
            "max_dut_input_power",
            "dut_power_channels",
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

    def test_dut_power_channels_must_be_non_empty_and_unique(self):
        with self.assertRaises(AmplifierInputError) as empty:
            _inputs(dut_power_channels=())
        self.assertIn("至少有一路", str(empty.exception))

        with self.assertRaises(AmplifierInputError) as duplicate:
            _inputs(
                dut_power_channels=(
                    SupplyChannel(supply_id="PS1", channel="CH1"),
                    SupplyChannel(supply_id="PS1", channel="CH1"),
                )
            )
        self.assertIn("重复分配", str(duplicate.exception))

        with self.assertRaises(AmplifierInputError):
            _inputs(dut_power_channels=("PS1/CH1", {}))

    def test_same_channel_number_on_different_supplies_is_allowed(self):
        inputs = _inputs(
            dut_power_channels=(
                SupplyChannel(supply_id="PS1", channel="CH1"),
                SupplyChannel(supply_id="PS2", channel="CH1"),
            )
        )

        self.assertEqual(inputs.dut_supplies(), {"PS1": ("CH1",), "PS2": ("CH1",)})

    def test_input_object_is_immutable(self):
        inputs = _inputs()

        with self.assertRaises(FrozenInstanceError):
            inputs.attenuator = Decibels(20.0)
        self.assertEqual(inputs.attenuator, Decibels(10.0))

    def test_invalid_input_fails_at_construction(self):
        """非法输入在构造期失败：不依赖任何设备动作或运行目录。"""
        with self.assertRaises(AmplifierInputError):
            _inputs(sweep={"start_power": -20, "stop_power": 0, "step": 1})

        with self.assertRaises(AmplifierInputError):
            _inputs(max_dut_input_power="30dBm")


class RunConfigurationInputTests(unittest.TestCase):
    def _configuration(self):
        from domain.configuration.models import (
            ChannelMapping,
            InstrumentMapping,
            PowerChannelPlan,
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
                compression_point="3dB",
                attenuator_value=30,
                max_input_power=30.0,
                driver_enabled=True,
                dut_power_channels={
                    "gate": PowerChannelPlan(role="gate", voltage=2.8),
                    "drain": PowerChannelPlan(role="drain", voltage=28.0),
                },
            ),
            run_mapping=RunResourceMapping(
                instruments={},
                power_supplies={},
                dut_power_channels=[
                    ChannelMapping(channel="PS1/CH1", role="gate"),
                    ChannelMapping(channel="PS1/CH2", role="drain"),
                ],
            ),
        )

    def test_typed_configuration_is_converted_to_formal_inputs(self):
        inputs = AmplifierMeasurementInputs.from_run_configuration(self._configuration())

        self.assertEqual([item.ghz for item in inputs.frequencies], [1.0, 2.4])
        self.assertEqual(inputs.sweep.start, PowerDbm(-20.0))
        self.assertEqual(inputs.sweep.stop, PowerDbm(0.0))
        self.assertEqual(inputs.sweep.step, Decibels(1.0))
        self.assertEqual(inputs.compression_target, Decibels(3.0))
        self.assertEqual(inputs.attenuator, Decibels(30.0))
        self.assertEqual(inputs.max_dut_input_power, PowerDbm(30.0))
        self.assertTrue(inputs.driver_enabled)
        self.assertEqual(inputs.dut_supplies(), {"PS1": ("CH1", "CH2")})

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

    def test_legacy_bare_channel_uses_the_unique_legacy_supply(self):
        from dataclasses import replace

        from domain.configuration.models import (
            ChannelMapping,
            InstrumentMapping,
            RunResourceMapping,
        )

        configuration = self._configuration()
        configuration = replace(
            configuration,
            run_mapping=RunResourceMapping(
                instruments={"power_supply": InstrumentMapping(model="PS1")},
                power_supplies={},
                dut_power_channels=[ChannelMapping(channel="CH1", role="gate")],
            ),
        )

        inputs = AmplifierMeasurementInputs.from_run_configuration(configuration)

        self.assertEqual(inputs.dut_power_channels[0].supply_id, "PS1")
        self.assertEqual(inputs.dut_power_channels[0].channel, "CH1")

    def test_bare_channel_without_a_unique_legacy_supply_is_rejected(self):
        """无法唯一确定旧电源时不得编造供电标识，必须在转换边界失败。"""
        configuration = self._configuration()
        configuration.run_mapping.dut_power_channels[0] = type(
            "Channel", (), {"channel": "CH1"}
        )()

        with self.assertRaises(AmplifierInputError):
            AmplifierMeasurementInputs.from_run_configuration(configuration)

    def test_channel_without_a_name_is_rejected(self):
        configuration = self._configuration()
        configuration.run_mapping.dut_power_channels[0] = type(
            "Channel", (), {"channel": "  "}
        )()

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
