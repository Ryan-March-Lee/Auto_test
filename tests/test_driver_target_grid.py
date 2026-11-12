"""步骤 4.4：驱动模式目标表与配置生成的目标 DUT 点一致性。

正式主功放驱动模式只消费驱动映射阶段已经生成的 ``target_points``；这些点必须与
配置的 DUT 功率扫描计划在数量、顺序、频率和功率唯一性上逐点一致。本文件只验证纯
校验入口，不启动 Qt、不连接仪器。
"""

import unittest
from dataclasses import replace

from application.dto import (
    AmplifierInputError,
    DriverMappingInput,
    DriverMappingRawPoint,
    DriverMappingTargetPoint,
)
from application.measurements.driver_target_grid import (
    _duplicate_power,
    require_driver_target_grid,
)
from domain.measurement.values import Frequency, PowerDbm
from tests.contract_doubles import amplifier_inputs


def _mapping(targets_by_frequency):
    """构造覆盖目标源功率的正式驱动映射输入（原始曲线与目标表两层）。"""
    raw_points = []
    target_points = []
    for frequency, values in targets_by_frequency.items():
        for source in (-40.0, 0.0):
            raw_points.append(
                DriverMappingRawPoint(
                    frequency_ghz=frequency,
                    source_power_dbm=source,
                    driver_output_power_dbm=source + 30.0,
                )
            )
        for value in values:
            target_points.append(
                DriverMappingTargetPoint(
                    frequency_ghz=frequency,
                    target_input_power_dbm=value,
                    source_power_dbm=-20.0,
                    driver_output_power_dbm=value,
                )
            )
    return DriverMappingInput(
        raw_points=tuple(raw_points),
        target_points=tuple(target_points),
    )


def _inputs(*frequencies):
    inputs = amplifier_inputs(
        driver_enabled=True,
        power_scan_plans=((-10.0, 1.0, -8.0),),
    )
    if len(frequencies) == 1:
        return replace(
            inputs, frequencies=(Frequency.from_ghz(frequencies[0]),)
        )
    return replace(
        inputs, frequencies=tuple(Frequency.from_ghz(item) for item in frequencies)
    )


class DriverTargetGridTests(unittest.TestCase):
    def test_consistent_target_table_is_accepted_in_original_order(self):
        inputs = _inputs(1.0)
        mapping = _mapping({"1.0": [-10.0, -9.0, -8.0]})

        grouped = require_driver_target_grid(inputs, mapping)

        self.assertEqual(
            [point.target_input_power_dbm for point in grouped[Frequency.from_ghz(1.0)]],
            [-10.0, -9.0, -8.0],
        )

    def test_missing_target_point_is_rejected(self):
        inputs = _inputs(1.0)
        mapping = _mapping({"1.0": [-10.0, -9.0]})

        with self.assertRaisesRegex(AmplifierInputError, "数量|缺少"):
            require_driver_target_grid(inputs, mapping)

    def test_extra_target_point_is_rejected(self):
        inputs = _inputs(1.0)
        mapping = _mapping({"1.0": [-10.0, -9.0, -8.0, -7.0]})

        with self.assertRaisesRegex(AmplifierInputError, "数量|多出"):
            require_driver_target_grid(inputs, mapping)

    def test_wrong_target_power_is_rejected(self):
        inputs = _inputs(1.0)
        mapping = _mapping({"1.0": [-10.0, -9.0, -7.0]})

        with self.assertRaisesRegex(AmplifierInputError, "第 3 个目标点|不一致"):
            require_driver_target_grid(inputs, mapping)

    def test_out_of_order_target_points_are_not_sorted_silently(self):
        inputs = _inputs(1.0)
        mapping = _mapping({"1.0": [-8.0, -9.0, -10.0]})

        with self.assertRaisesRegex(AmplifierInputError, "第 1 个目标点|不一致"):
            require_driver_target_grid(inputs, mapping)

    def test_multi_frequency_table_in_configured_order_is_accepted(self):
        inputs = _inputs(1.0, 2.0)
        mapping = _mapping(
            {"1.0": [-10.0, -9.0, -8.0], "2.0": [-10.0, -9.0, -8.0]}
        )

        grouped = require_driver_target_grid(inputs, mapping)

        self.assertEqual(
            [frequency.key for frequency in grouped], ["1.0", "2.0"]
        )

    def test_reordered_frequency_groups_are_not_silently_regrouped(self):
        """整组频点错位同样破坏逐点契约，不能被"按频率重新分组"掩盖。"""

        inputs = _inputs(1.0, 2.0)
        mapping = _mapping(
            {"2.0": [-10.0, -9.0, -8.0], "1.0": [-10.0, -9.0, -8.0]}
        )

        with self.assertRaisesRegex(AmplifierInputError, "属于测试频率|目标点应属于"):
            require_driver_target_grid(inputs, mapping)

    def test_target_table_with_unconfigured_frequency_is_rejected(self):
        inputs = _inputs(1.0)
        mapping = _mapping({"1.0": [-10.0, -9.0, -8.0], "2.0": [-10.0, -9.0, -8.0]})

        with self.assertRaisesRegex(AmplifierInputError, "未配置的测试频率"):
            require_driver_target_grid(inputs, mapping)

    def test_target_table_missing_a_configured_frequency_is_rejected(self):
        inputs = _inputs(1.0, 2.0)
        mapping = _mapping({"1.0": [-10.0, -9.0, -8.0]})

        with self.assertRaisesRegex(AmplifierInputError, "缺少测试频率"):
            require_driver_target_grid(inputs, mapping)

    def test_target_source_power_above_driver_max_input_is_rejected(self):
        inputs = replace(
            _inputs(1.0), driver_max_input_power=PowerDbm(-25.0)
        )
        mapping = _mapping({"1.0": [-10.0, -9.0, -8.0]})

        with self.assertRaisesRegex(AmplifierInputError, "驱放最大输入功率"):
            require_driver_target_grid(inputs, mapping)

    def test_duplicate_target_powers_are_detected(self):
        self.assertEqual(_duplicate_power([-10.0, -9.0, -10.0]), -10.0)
        self.assertIsNone(_duplicate_power([-10.0, -9.0, -8.0]))

    def test_driver_disabled_does_not_consume_a_target_table(self):
        inputs = replace(_inputs(1.0), driver_enabled=False)
        mapping = _mapping({"1.0": [-10.0, -9.0, -8.0]})

        self.assertEqual(require_driver_target_grid(inputs, mapping), {})

    def test_driver_enabled_without_a_target_table_is_rejected(self):
        inputs = replace(_inputs(1.0), driver_enabled=True)

        with self.assertRaisesRegex(AmplifierInputError, "缺少 target_points|目标映射"):
            require_driver_target_grid(inputs, None)

    def test_non_input_object_is_rejected(self):
        with self.assertRaises(TypeError):
            require_driver_target_grid(object(), None)


if __name__ == "__main__":
    unittest.main()
