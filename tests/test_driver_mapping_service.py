"""步骤 6：驱动映射扫描的自适应起点与上推算法。

覆盖计划中的验收点：

- 起点是“最低目标输入 - 预估增益 - 10 dB”，并收敛到 -50～20 dBm；
- 线性增益与压缩曲线都能收敛到每个目标；
- 已命中读数不会复用于另一个目标，目标间隔小于容差时仍会新测；
- 命中容差为 ±0.2 dB（含边界）；
- 目标不可达或源功率到界时明确失败，不生成完整映射；
- 停止/异常路径仍然关闭 RF 并执行驱动电源清理。
"""

import math
import unittest
from unittest.mock import patch

from app.cancellation import CancellationToken, MeasurementCancelled
from application.measurements.services import (
    DRIVER_MAPPING_HIT_TOLERANCE_DB,
    DRIVER_MAPPING_SOURCE_MAX_DBM,
    DRIVER_MAPPING_SOURCE_MIN_DBM,
    DriverMappingError,
    DriverPowerMappingService,
)
from tests.fakes import safety_cleanup_for


class _FakeInstrument:
    """按给定响应曲线返回频谱仪读数；补偿后输出就是 response(source)。"""

    def __init__(self, response, *, compensation_db=10.0, on_measure=None):
        self.response = response
        self.compensation_db = compensation_db
        self.on_measure = on_measure
        self.power = 0.0
        self.frequency = None
        self.measurements = []
        self.rf_on_calls = 0
        self.rf_off_calls = 0
        self.driver_off_calls = 0

    def setup_driver_amplifier_power(self):
        pass

    def power_on_driver(self):
        pass

    def power_off_driver(self):
        self.driver_off_calls += 1

    def set_power(self, power):
        self.power = power

    def set_frequency(self, frequency):
        self.frequency = frequency

    def set_center_frequency(self, _frequency):
        pass

    def set_span(self, _span):
        pass

    def rf_output_on(self):
        self.rf_on_calls += 1

    def rf_output_off(self):
        self.rf_off_calls += 1

    def measure_power_with_average(self):
        output = self.response(self.power)
        self.measurements.append((self.frequency, self.power, output))
        if self.on_measure is not None:
            self.on_measure(self)
        return output - self.compensation_db

    def close_all(self, close_rf=False):
        return []


def _config(
    *,
    start=-20.0,
    stop=-20.0,
    step=1.0,
    max_input=100.0,
    gain=1.0,
    frequencies=(1.0,),
    attenuator="10dB",
):
    return {
        "test_frequencies": list(frequencies),
        "attenuator": {"type": attenuator},
        "signal_source": {
            "start_power": start,
            "stop_power": stop,
            "step": step,
        },
        "max_dut_input_power": max_input,
        "driver_gain_estimate_db": gain,
    }


def _loss_data(cable2=0.0):
    return {"cable_losses": {"1.0": {"cable2": cable2}}}


def _service(
    response,
    *,
    config=None,
    instrument=None,
    token=None,
    **instrument_kwargs,
):
    instrument = instrument or _FakeInstrument(response, **instrument_kwargs)
    return DriverPowerMappingService(
        config or _config(),
        instrument,
        _loss_data(),
        run_id="run-driver-mapping-service",
        safety_cleanup=safety_cleanup_for(instrument),
        cancellation_token=token,
        sleep_fn=lambda _seconds: None,
        settle_delay_s=0.0,
    )


def _points(result, frequency="1.0"):
    return result["raw_points"][frequency]


class DriverMappingStartPointTests(unittest.TestCase):
    def test_scan_starts_at_minimum_target_minus_gain_minus_margin(self):
        instrument = _FakeInstrument(lambda source: source + 1.0)
        service = _service(lambda source: source + 1.0, instrument=instrument)

        service.run()

        first_source = instrument.measurements[0][1]
        self.assertAlmostEqual(first_source, -20.0 - 1.0 - 10.0, places=9)

    def test_start_is_clamped_to_the_source_power_range(self):
        instrument = _FakeInstrument(lambda _source: 0.0)
        service = _service(
            lambda _source: 0.0,
            config=_config(start=-40.0, gain=1.0),
            instrument=instrument,
        )

        with self.assertRaises(DriverMappingError):
            service.run()

        # 公式得到 -51 dBm，但任何硬件动作都不得低于 -50 dBm。
        self.assertEqual(instrument.measurements[0][1], DRIVER_MAPPING_SOURCE_MIN_DBM)

    def test_target_grid_is_limited_by_dut_max_input(self):
        service = _service(
            lambda source: source + 1.0,
            config=_config(start=-20.0, stop=0.0, step=1.0, max_input=-18.0),
        )

        self.assertEqual(
            [target.dbm for target in service.target_powers],
            [-20.0, -19.0, -18.0],
        )
        result = service.run()
        outputs = [
            point["driver_output_power_dbm"] for point in _points(result)
        ]
        for target in (-20.0, -19.0, -18.0):
            self.assertTrue(
                any(
                    abs(output - target) <= DRIVER_MAPPING_HIT_TOLERANCE_DB
                    for output in outputs
                ),
                f"目标 {target} dBm 没有命中读数: {outputs}",
            )


class DriverMappingConvergenceTests(unittest.TestCase):
    def test_linear_gain_converges_and_keeps_the_start_probe(self):
        result = _service(lambda source: source + 1.0).run()

        points = _points(result)
        self.assertEqual(len(points), 2)
        self.assertAlmostEqual(points[0]["source_power_dbm"], -31.0)
        self.assertAlmostEqual(points[-1]["source_power_dbm"], -21.0)
        self.assertAlmostEqual(points[-1]["driver_output_power_dbm"], -20.0)
        self.assertAlmostEqual(points[-1]["gain_db"], 1.0)

    def test_compressed_curve_is_followed_without_requiring_constant_gain(self):
        def compressed(source):
            if source <= -20.0:
                return source + 10.0
            return -10.0 + 0.2 * (source + 20.0)

        result = _service(
            compressed,
            config=_config(start=-10.0, stop=-8.0, step=2.0, gain=5.0),
        ).run()

        points = _points(result)
        sources = [point["source_power_dbm"] for point in points]
        outputs = [point["driver_output_power_dbm"] for point in points]

        self.assertIn(-20.0, [round(value, 9) for value in sources])
        self.assertIn(-10.0, [round(value, 9) for value in sources])
        self.assertIn(-10.0, [round(value, 9) for value in outputs])
        self.assertIn(-8.0, [round(value, 9) for value in outputs])

    def test_initial_probe_can_already_be_the_first_target_hit(self):
        # 预估增益 10 dB、实际增益 20 dB：起点输出正好等于目标。
        result = _service(
            lambda source: source + 20.0,
            config=_config(gain=10.0),
        ).run()

        self.assertEqual(len(_points(result)), 1)
        self.assertAlmostEqual(_points(result)[0]["driver_output_power_dbm"], -20.0)

    def test_hit_tolerance_is_inclusive_at_plus_and_minus_boundary(self):
        # 起点 -31 dBm；响应斜率使第一点恰好落在目标 ±0.2 dB。
        cases = (
            (lambda source: source + 11.2, -20.0 + DRIVER_MAPPING_HIT_TOLERANCE_DB),
            (lambda source: source + 10.8, -20.0 - DRIVER_MAPPING_HIT_TOLERANCE_DB),
        )
        for response, expected in cases:
            with self.subTest(expected=expected):
                result = _service(response).run()

                points = _points(result)
                self.assertEqual(len(points), 1)
                self.assertAlmostEqual(
                    points[0]["driver_output_power_dbm"], expected, places=9
                )

    def test_targets_closer_than_tolerance_still_get_independent_readings(self):
        result = _service(
            lambda source: source + 1.0,
            config=_config(start=-20.0, stop=-19.9, step=0.1),
        ).run()

        points = _points(result)
        sources = [round(point["source_power_dbm"], 9) for point in points]
        for target in (-20.0, -19.9):
            self.assertTrue(
                any(
                    abs(point["driver_output_power_dbm"] - target)
                    <= DRIVER_MAPPING_HIT_TOLERANCE_DB
                    for point in points
                ),
                f"目标 {target} 没有命中读数",
            )

        # 两个目标即使落在同一容差带内，也不能复用同一个源功率读数：
        # -21.0 dBm 满足第一个目标，第二个目标必须再测 -20.9 dBm。
        self.assertIn(-21.0, sources)
        self.assertIn(-20.9, sources)


class DriverMappingFailureTests(unittest.TestCase):
    def test_unreachable_target_below_source_range_fails_without_result(self):
        instrument = _FakeInstrument(lambda _source: 0.0)
        service = _service(
            lambda _source: 0.0,
            config=_config(start=-40.0, gain=1.0),
            instrument=instrument,
        )

        with self.assertRaisesRegex(DriverMappingError, "下界") as caught:
            service.run()

        self.assertIn("无有效进展", str(caught.exception))
        self.assertEqual(instrument.measurements[0][1], DRIVER_MAPPING_SOURCE_MIN_DBM)
        self.assertEqual(service.partial_payload(), {"raw_points": {}})
        self.assertEqual(instrument.rf_off_calls, 1)
        self.assertEqual(instrument.driver_off_calls, 1)

    def test_target_above_source_range_fails_at_the_upper_bound(self):
        instrument = _FakeInstrument(lambda _source: 0.0)
        service = _service(
            lambda _source: 0.0,
            config=_config(start=10.0, stop=10.0, gain=0.0),
            instrument=instrument,
        )

        with self.assertRaisesRegex(DriverMappingError, "上界") as caught:
            service.run()

        self.assertIn("无有效进展", str(caught.exception))
        self.assertEqual(
            instrument.measurements[-1][1], DRIVER_MAPPING_SOURCE_MAX_DBM
        )
        self.assertEqual(service.partial_payload(), {"raw_points": {}})

    def test_per_target_measurement_limit_is_enforced(self):
        instrument = _FakeInstrument(lambda source: source + 0.5)
        service = _service(lambda source: source + 0.5, instrument=instrument)

        with patch(
            "application.measurements.services.DRIVER_MAPPING_MAX_ATTEMPTS_PER_TARGET",
            1,
        ):
            with self.assertRaisesRegex(DriverMappingError, "1 次测量内"):
                service.run()

        # 起点一次 + 目标搜索一次，然后触发明确上限而不是继续无限测量。
        self.assertEqual(len(instrument.measurements), 2)
        self.assertEqual(service.partial_payload(), {"raw_points": {}})

    def test_missing_gain_estimate_fails_before_hardware_action(self):
        config = _config()
        del config["driver_gain_estimate_db"]
        instrument = _FakeInstrument(lambda source: source + 1.0)

        with self.assertRaisesRegex(ValueError, "预估增益"):
            _service(
                lambda source: source + 1.0,
                config=config,
                instrument=instrument,
            )

        self.assertEqual(instrument.measurements, [])
        self.assertEqual(instrument.rf_on_calls, 0)

    def test_missing_dut_limit_fails_before_hardware_action(self):
        config = _config()
        del config["max_dut_input_power"]
        instrument = _FakeInstrument(lambda source: source + 1.0)

        with self.assertRaisesRegex(ValueError, "DUT 最大输入功率"):
            _service(
                lambda source: source + 1.0,
                config=config,
                instrument=instrument,
            )

        self.assertEqual(instrument.measurements, [])
        self.assertEqual(instrument.rf_on_calls, 0)


class DriverMappingCancellationTests(unittest.TestCase):
    def test_stop_between_measurements_closes_rf_and_driver_power(self):
        token = CancellationToken()

        def stop_after_first_measurement(_instrument):
            if not token.is_cancelled:
                token.request_stop(reason="用户停止")

        instrument = _FakeInstrument(
            lambda source: source + 1.0, on_measure=stop_after_first_measurement
        )
        service = _service(
            lambda source: source + 1.0,
            instrument=instrument,
            token=token,
        )

        with self.assertRaises(MeasurementCancelled):
            service.run()

        self.assertEqual(len(instrument.measurements), 1)
        self.assertEqual(instrument.rf_off_calls, 1)
        self.assertEqual(instrument.driver_off_calls, 1)

    def test_stop_during_the_hitting_measurement_is_not_committed(self):
        """取消恰好发生在产生命中的那次读数期间，也不能按成功完成提交。"""
        token = CancellationToken()

        def stop_when_target_is_hit(instrument):
            # 该替身的第三列读数经服务补偿 10 dB 后正好等于此值。
            if instrument.measurements[-1][2] == -20.0 and not token.is_cancelled:
                token.request_stop(reason="用户停止")

        instrument = _FakeInstrument(
            lambda source: source + 1.0, on_measure=stop_when_target_is_hit
        )
        service = _service(
            lambda source: source + 1.0,
            instrument=instrument,
            token=token,
        )

        with self.assertRaises(MeasurementCancelled):
            service.run()

        # 命中读数已经产生，但取消优先：该频点不算完整扫完，也不产出映射。
        self.assertEqual(len(instrument.measurements), 2)
        self.assertEqual(service.partial_payload(), {"raw_points": {}})
        self.assertEqual(instrument.rf_off_calls, 1)
        self.assertEqual(instrument.driver_off_calls, 1)

    def test_stop_after_the_last_frequency_is_not_committed_as_success(self):
        """最后一个频点扫完后、提交结果前到达的停止请求同样必须生效。"""
        token = CancellationToken()

        class _StoppingInstrument(_FakeInstrument):
            def rf_output_off(self):
                super().rf_output_off()
                if not token.is_cancelled:
                    token.request_stop(reason="用户停止")

        instrument = _StoppingInstrument(lambda source: source + 1.0)
        service = _service(
            lambda source: source + 1.0,
            instrument=instrument,
            token=token,
        )

        with self.assertRaises(MeasurementCancelled):
            service.run()

        # 该频点已完整扫完，可以进入部分结果，但完整结果不会被提交。
        self.assertEqual(set(service.partial_payload()["raw_points"]), {"1.0"})
        self.assertEqual(instrument.rf_off_calls, 1)
        self.assertEqual(instrument.driver_off_calls, 1)

    def test_unreachable_target_still_cleans_up(self):
        instrument = _FakeInstrument(lambda _source: math.inf)
        service = _service(
            lambda _source: math.inf,
            config=_config(start=10.0, stop=10.0, gain=0.0),
            instrument=instrument,
        )

        # 非有限读数在补偿阶段就被值对象拒绝；清理仍必须执行。
        with self.assertRaises(ValueError):
            service.run()

        self.assertEqual(instrument.rf_off_calls, 1)
        self.assertEqual(instrument.driver_off_calls, 1)


if __name__ == "__main__":
    unittest.main()
