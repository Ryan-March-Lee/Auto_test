"""``amplifier_measurement`` 历史入口的兼容转发回归。

正式实现位于 :class:`application.measurements.services.AmplifierMeasurementService`
与 :class:`application.measurements.amplifier_test.AmplifierMeasurementUseCase`；
历史装配适配器位于 :class:`app.legacy_amplifier_measurement.AmplifierMeasurement`。
``amplifier_measurement`` 根模块只允许重新导出历史符号，这些测试确认它没有
自己的扫描/计算实现，且保留的公开 API 仍按旧契约转发。
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from amplifier_measurement import AmplifierMeasurement


IMPLEMENTATION = "app.legacy_amplifier_measurement"

SAMPLE_LOSS_DATA = {
    "1.0": {
        "cable1": 1.625,
        "cable2": 1.625,
        "cable3": 0.095,
        "cable4": 0.095,
    }
}

SAMPLE_DRIVER_MAPPING = {
    "1.0": {
        "-50.0": 10.0,
        "-45.0": 15.0,
        "-40.0": 20.0,
        "-35.0": 25.0,
        "-30.0": 30.0,
    }
}


class AmplifierCompatibilityForwardingTests(unittest.TestCase):
    def test_calculate_actual_power_forwards_to_pure_function(self):
        measurement = AmplifierMeasurement.__new__(AmplifierMeasurement)
        measurement.config = {"attenuator": {"type": "30dB"}}
        measurement.loss_data = {"cable_losses": SAMPLE_LOSS_DATA}

        with patch(
            "domain.measurement.calculations.compensate_amplifier_output_power",
            return_value=123.4,
        ) as calculate:
            result = measurement.calculate_actual_power(1.0, -10.0)

        self.assertEqual(result, 123.4)
        calculate.assert_called_once_with(
            measured_power=-10.0,
            frequency=1.0,
            loss_data=SAMPLE_LOSS_DATA,
            attenuator_value=30.0,
        )

    def test_get_driver_output_power_forwards_to_interpolation(self):
        measurement = AmplifierMeasurement.__new__(AmplifierMeasurement)
        measurement.driver_mapping = SAMPLE_DRIVER_MAPPING

        with patch(
            "domain.measurement.calculations.interpolate_driver_output_power",
            return_value=21.5,
        ) as calculate:
            result = measurement.get_driver_output_power(1.0, -39.0)

        self.assertEqual(result, 21.5)
        calculate.assert_called_once_with(
            frequency=1.0,
            sg_power=-39.0,
            driver_mapping=SAMPLE_DRIVER_MAPPING,
        )

    def test_perform_power_sweep_forwards_to_service(self):
        measurement = AmplifierMeasurement.__new__(AmplifierMeasurement)
        measurement.config = {"attenuator": {"type": "0dB"}}
        measurement.loss_data = {"cable_losses": {}}
        measurement.driver_mapping = None
        measurement.inst_ctrl = object()

        with patch(f"{IMPLEMENTATION}.AmplifierMeasurementService") as service_cls:
            service_cls.return_value.perform_power_sweep.return_value = {"ok": True}
            result = measurement.perform_power_sweep(2.0)

        self.assertEqual(result, {"ok": True})
        service_cls.return_value.perform_power_sweep.assert_called_once_with(2.0)
        args, kwargs = service_cls.call_args
        self.assertEqual(
            args,
            (measurement.config, measurement.inst_ctrl, measurement.loss_data, None),
        )
        self.assertEqual(kwargs["run_id"], "run-legacy")


class AmplifierLegacyConstructorCompatibilityTests(unittest.TestCase):
    """通过真实构造器验证旧入口保留的装配与生命周期契约。"""

    CONFIG = {
        "attenuator": {"type": "0dB"},
        "driver_mode": {"enabled": False},
    }

    def _loader_patches(self, *, config=None, loss=None):
        return (
            patch(f"{IMPLEMENTATION}.load_config_file", return_value=config or self.CONFIG),
            patch(f"{IMPLEMENTATION}.load_json_result", return_value=loss if loss is not None else {}),
        )

    def test_constructor_keeps_injected_sleep_function(self):
        injected_sleep = lambda _: None
        config_patch, loss_patch = self._loader_patches()
        with config_patch, loss_patch, self.assertWarns(DeprecationWarning):
            measurement = AmplifierMeasurement(
                sleep_fn=injected_sleep, measurement_port=object()
            )
        self.assertIs(measurement.sleep_fn, injected_sleep)

    def test_missing_measurement_port_is_rejected_before_storage(self):
        with patch(f"{IMPLEMENTATION}.load_config_file", return_value=self.CONFIG), patch(
            f"{IMPLEMENTATION}.FileMeasurementResultRepository"
        ) as repository_cls:
            with self.assertRaisesRegex(ValueError, "必须显式传入 measurement_port"):
                AmplifierMeasurement()
        repository_cls.assert_not_called()

    def test_constructor_loads_config_loss_and_run_state(self):
        config_patch, loss_patch = self._loader_patches(
            loss={"cable_losses": SAMPLE_LOSS_DATA}
        )
        with config_patch as load_config, loss_patch as load_json:
            measurement = AmplifierMeasurement(run_id="run-1", measurement_port=object())

        self.assertEqual(measurement.config, self.CONFIG)
        self.assertEqual(measurement.run_id, "run-1")
        self.assertIsNone(measurement.run_directory)
        self.assertIsNone(measurement.driver_mapping)
        load_config.assert_called_once()
        self.assertEqual(load_json.call_count, 1)

    def test_measure_all_frequencies_forwards_to_service_and_saves(self):
        config_patch, loss_patch = self._loader_patches()
        with config_patch, loss_patch, self.assertWarns(DeprecationWarning), patch(
            f"{IMPLEMENTATION}.AmplifierMeasurementService"
        ) as service_cls:
            service_cls.return_value.run.return_value = {"results": {"1.0": {"gain": 20.0}}}
            measurement = AmplifierMeasurement(run_id="run-1", measurement_port=object())
            measurement.save_results = Mock()
            result = measurement.measure_all_frequencies()

        self.assertEqual(result, {"results": {"1.0": {"gain": 20.0}}})
        self.assertEqual(measurement.measurement_results, {"1.0": {"gain": 20.0}})
        service_cls.return_value.run.assert_called_once_with()
        measurement.save_results.assert_called_once_with()

    def test_save_results_uses_result_repository(self):
        config_patch, loss_patch = self._loader_patches()
        with config_patch, loss_patch, self.assertWarns(DeprecationWarning), patch(
            f"{IMPLEMENTATION}.FileMeasurementResultRepository"
        ) as repository_cls:
            saved = Mock()
            saved.archive_path.parent = Path("archive")
            saved.run_directory = Path("archive")
            repository_cls.return_value.save.return_value = saved
            measurement = AmplifierMeasurement(run_id="run-1", measurement_port=object())
            measurement.measurement_results = {"1.0": {"gain": 20.0}}
            measurement.save_results()

        _, save_kwargs = repository_cls.return_value.save.call_args
        self.assertEqual(save_kwargs["result_type"], "amplifier_measurement")
        self.assertEqual(save_kwargs["run_id"], "run-1")
        self.assertEqual(measurement.run_directory, Path("archive"))


if __name__ == "__main__":
    unittest.main()
