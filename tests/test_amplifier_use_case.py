import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.cancellation import CancellationToken, MeasurementCancelled
from application.dto import (
    AmplifierCalibrationInputs,
    AmplifierMeasurementInputs,
    AmplifierMeasurementRequest,
)
from application.measurements import AmplifierMeasurementUseCase
from application.measurements.amplifier_test import AMPLIFIER_RESULT_PATH
from application.dto.measurement_requests import StageResultDigest
from application.run_stages import StageInputUnavailable
from domain.configuration.models import (
    RunConfiguration,
    RunResourceMapping,
    TestPlan,
)
from domain.measurement.values import CableLossTable, Decibels, Frequency, PowerDbm
from domain.run.models import STAGE_AMPLIFIER
from tests.contract_doubles import (
    MappingInputReader,
    RecordingStageRecorder,
    amplifier_inputs,
)
from tests.fakes import safety_cleanup_for


class _Instrument:
    def __init__(self):
        self.measured = 0
        self.close_calls = 0

    def setup_dut_power(self): pass
    def power_on_sequence(self): pass
    def set_power(self, _power): pass
    def set_frequency(self, _frequency): pass
    def set_center_frequency(self, _frequency): pass
    def set_span(self, _span): pass
    def rf_output_on(self): pass
    def rf_output_off(self): pass
    def measure_power_with_average(self):
        self.measured += 1
        return 0.0
    def read_voltage(self, _name, _channel): return 10.0
    def read_current(self, _name, _channel): return 1.0
    def power_off_sequence(self): pass
    def close_all(self, close_rf=False):
        self.close_calls += 1
        return []


class _Repository:
    def __init__(self): self.calls = []
    def save(self, result, **kwargs):
        self.calls.append((result, kwargs))
        return SimpleNamespace(run_directory=Path("saved-run"))


CABLE_LOSSES = {
    "1.0": {"cable1": 0.0, "cable2": 0.0, "cable3": 0.0, "cable4": 0.0}
}


def _request(
    instrument,
    *,
    token=None,
    repository=None,
    recorder=None,
    reader=None,
    stage_repository=None,
    cable_loss_path=None,
    cable_loss_digest=None,
    driver_mapping_path=None,
    driver_mapping_digest=None,
):
    configuration = {
        "test_frequencies": [1.0],
        "attenuator": {"type": "10dB"},
        "signal_source": {"start_power": -20, "stop_power": -20, "step": 1},
        "compression_point": {"type": "1dB"},
        "driver_mode": {"enabled": False},
        "power_supply_assignment": {"dut_amplifier": {"supplies": {}}},
        "dut_config": {"max_input_power": 100},
    }
    return AmplifierMeasurementRequest(
        configuration=configuration,
        context=SimpleNamespace(run_id="run-amplifier"),
        run_directory=Path("run-directory"),
        measurement_port=instrument,
        inputs=amplifier_inputs(),
        stage_recorder=recorder or RecordingStageRecorder("run-amplifier"),
        safety_cleanup=safety_cleanup_for(instrument),
        cancellation_token=token,
        result_repository=repository or _Repository(),
        input_reader=reader or MappingInputReader(cable_loss={"cable_losses": CABLE_LOSSES}),
        stage_result_repository=stage_repository,
        cable_loss_path=cable_loss_path,
        cable_loss_digest=cable_loss_digest,
        driver_mapping_path=driver_mapping_path,
        driver_mapping_digest=driver_mapping_digest,
        safety_options={"owns_measurement_port": True},
    )


class AmplifierMeasurementUseCaseTests(unittest.TestCase):
    def test_split_run_configuration_is_converted_to_formal_inputs(self):
        configuration = RunConfiguration(
            test_plan=TestPlan(
                frequencies=[1.0],
                start_power=-20,
                stop_power=-20,
                power_step=1,
                power_reference="dut_input",
                compression_point="1dB",
                attenuator_value=10,
                max_input_power=100,
                drain_channel={"supply_id": "PS1", "channel": "CH2"},
                channel_settings=[
                    {
                        "supply_id": "PS1",
                        "channel": "CH2",
                        "voltage": 28,
                        "current": 1,
                        "voltage_protection": 30,
                        "current_protection": 2,
                        "unit_voltage": "V",
                        "unit_current": "A",
                    }
                ],
            ),
            run_mapping=RunResourceMapping(
                instruments={},
                dut_power_plan={
                    "power_sequence": [{"supply_id": "PS1", "channel": "CH2"}],
                    "inter_channel_delay_s": 0.0,
                },
            ),
        )
        request = _request(_Instrument())
        request = AmplifierMeasurementRequest(
            configuration=configuration,
            context=request.context,
            run_directory=request.run_directory,
            measurement_port=request.measurement_port,
            inputs=AmplifierMeasurementInputs.from_run_configuration(configuration),
            stage_recorder=request.stage_recorder,
            safety_cleanup=request.safety_cleanup,
            result_repository=request.result_repository,
            input_reader=request.input_reader,
        )

        use_case = AmplifierMeasurementUseCase(request, sleep_fn=lambda _: None)

        self.assertEqual(
            [item.ghz for item in use_case.inputs.frequencies], [1.0]
        )
        self.assertEqual(use_case.inputs.attenuator, Decibels.parse("10dB"))
        plan = use_case.inputs.power_scan_schedule.plans[0]
        self.assertEqual(plan.start, PowerDbm(-20))
        self.assertEqual(plan.stop, PowerDbm(-20))
        self.assertEqual(plan.step, Decibels(1))
        self.assertEqual(use_case.inputs.target_input_powers, (PowerDbm(-20),))
        self.assertEqual(
            use_case.inputs.dut_supplies(), {"PS1": ("CH2",)}
        )
        self.assertEqual(
            [route.token for route in use_case.inputs.dut_power_plan.power_sequence],
            ["PS1/CH2"],
        )
        self.assertEqual(
            use_case.inputs.drain_channel.routes[0].token, "PS1/CH2"
        )
        self.assertEqual(
            use_case.inputs.channel_settings[
                next(iter(use_case.inputs.channel_settings))
            ].current_a,
            1.0,
        )

    def test_calculate_actual_power_delegates_to_pure_function(self):
        instrument = _Instrument()
        use_case = AmplifierMeasurementUseCase(
            _request(instrument), sleep_fn=lambda _: None
        )

        with patch(
            "application.measurements.amplifier_test.measurement_calculations"
            ".compensate_amplifier_output_power",
            return_value=PowerDbm(123.4),
        ) as calculate:
            result = use_case.calculate_actual_power(1.0, -10.0)

        self.assertEqual(result, 123.4)
        calculate.assert_called_once_with(
            measured_power=PowerDbm(-10.0),
            frequency=Frequency.from_ghz(1.0),
            loss_table=CableLossTable.from_mapping(
                use_case.loss_data["cable_losses"], source="线损输入"
            ),
            attenuator_value=Decibels.parse("10dB"),
        )

    def test_preloaded_calibration_inputs_are_reused_without_reading_the_run(self):
        """组合入口预检过的校准输入随请求复用，不再二次读取运行目录。"""

        class _UnreadableReader:
            def read_cable_loss(self, path):
                raise AssertionError(f"不应再次读取线损: {path}")

            def read_driver_mapping(self, path):
                raise AssertionError(f"不应再次读取驱动映射: {path}")

        loss_data = {"cable_losses": CABLE_LOSSES}
        request = replace(
            _request(_Instrument(), reader=_UnreadableReader()),
            calibration_inputs=AmplifierCalibrationInputs(loss_data=loss_data),
        )

        use_case = AmplifierMeasurementUseCase(request, sleep_fn=lambda _: None)

        self.assertIs(use_case.loss_data, loss_data)
        self.assertIsNone(use_case.driver_mapping)

    def test_explicit_cable_loss_path_is_used_without_preloaded_inputs(self):
        """没有预检对象时，显式选择（含摘要）仍是校准输入的权威来源。"""
        reader = MappingInputReader(cable_loss={"cable_losses": CABLE_LOSSES})
        selected = Path("test_results") / "cable_loss" / "20260101T000000Z_abcd.json"
        digest = StageResultDigest(sha256="a" * 64, size_bytes=32)
        sentinel = object()
        loaded = SimpleNamespace(
            sha256="a" * 64, size_bytes=32, path=selected, result=sentinel
        )
        stage_repository = SimpleNamespace(load=lambda stage, path: loaded)

        use_case = AmplifierMeasurementUseCase(
            _request(
                _Instrument(),
                reader=reader,
                stage_repository=stage_repository,
                cable_loss_path=selected,
                cable_loss_digest=digest,
            ),
            sleep_fn=lambda _: None,
        )

        self.assertEqual(use_case.loss_data, {"cable_losses": CABLE_LOSSES})
        self.assertIsNone(use_case.driver_mapping)
        self.assertEqual(reader.paths, [])
        self.assertEqual(reader.results, [("cable_loss", sentinel)])

    def test_replaced_explicit_cable_loss_is_rejected_before_measurement(self):
        reader = MappingInputReader(cable_loss={"cable_losses": CABLE_LOSSES})
        selected = Path("test_results") / "cable_loss" / "20260101T000000Z_abcd.json"
        digest = StageResultDigest(sha256="a" * 64, size_bytes=32)
        loaded = SimpleNamespace(
            sha256="b" * 64, size_bytes=32, path=selected, result=object()
        )
        stage_repository = SimpleNamespace(load=lambda stage, path: loaded)

        with self.assertRaisesRegex(StageInputUnavailable, "已被修改或替换"):
            AmplifierMeasurementUseCase(
                _request(
                    _Instrument(),
                    reader=reader,
                    stage_repository=stage_repository,
                    cable_loss_path=selected,
                    cable_loss_digest=digest,
                ),
                sleep_fn=lambda _: None,
            )

        self.assertEqual(reader.paths, [])
        self.assertEqual(reader.results, [])

    def test_completes_and_saves_structured_result(self):
        instrument = _Instrument()
        recorder = RecordingStageRecorder("run-amplifier")
        use_case = AmplifierMeasurementUseCase(
            _request(instrument, recorder=recorder), sleep_fn=lambda _: None
        )

        result = use_case.measure_all_frequencies()

        self.assertEqual(result, use_case.last_result)
        self.assertEqual(
            use_case.measurement_results,
            {item.frequency_ghz: item for item in result.frequencies},
        )
        # 结果只写当前运行：阶段提交到记录器，路径指向本次运行目录。
        self.assertEqual(
            recorder.result_paths(), {STAGE_AMPLIFIER: AMPLIFIER_RESULT_PATH}
        )
        self.assertEqual(recorder.completed_runs, 1)
        self.assertEqual(
            result.result_reference["archive_path"],
            str(Path("run-directory") / AMPLIFIER_RESULT_PATH),
        )
        self.assertEqual(use_case.run_directory, Path("run-directory"))
        self.assertEqual(instrument.close_calls, 1)

    def test_measurement_port_is_required_before_input_loading(self):
        request = _request(None)
        with self.assertRaisesRegex(ValueError, "注入 measurement_port"):
            AmplifierMeasurementUseCase(request)

    def test_cancelled_request_is_cleaned_by_service(self):
        instrument = _Instrument()
        token = CancellationToken()
        token.request_stop(reason="test")
        use_case = AmplifierMeasurementUseCase(
            _request(instrument, token=token), sleep_fn=lambda _: None
        )

        with self.assertRaises(MeasurementCancelled):
            use_case.measure_all_frequencies()

        self.assertEqual(instrument.measured, 0)

    def test_stop_saves_only_frequencies_that_finished_the_sweep(self):
        token = CancellationToken()

        class _StoppingInstrument(_Instrument):
            def measure_power_with_average(self):
                value = super().measure_power_with_average()
                if self.measured == 1:
                    token.request_stop(reason="用户停止")
                return value

        inputs = replace(
            amplifier_inputs(),
            frequencies=(Frequency.from_ghz(1.0), Frequency.from_ghz(2.0)),
        )
        losses = {
            "1.0": {"cable1": 0.0, "cable2": 0.0, "cable3": 0.0, "cable4": 0.0},
            "2.0": {"cable1": 0.0, "cable2": 0.0, "cable3": 0.0, "cable4": 0.0},
        }
        recorder = RecordingStageRecorder("run-amplifier")
        request = replace(
            _request(
                _StoppingInstrument(),
                token=token,
                recorder=recorder,
                reader=MappingInputReader(cable_loss={"cable_losses": losses}),
            ),
            inputs=inputs,
        )
        use_case = AmplifierMeasurementUseCase(request, sleep_fn=lambda _: None)

        with self.assertRaises(MeasurementCancelled):
            use_case.measure_all_frequencies()

        self.assertEqual(len(recorder.partials), 1)
        stage, payload, path = recorder.partials[0]
        self.assertEqual(stage, STAGE_AMPLIFIER)
        self.assertEqual(path, "results/amplifier_partial.json")
        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(set(payload["results"]), {"1.0"})
        self.assertEqual(use_case.last_partial_result.status.value, "cancelled")
        self.assertEqual(len(use_case.last_partial_result.frequencies), 1)
        self.assertIn((STAGE_AMPLIFIER, "用户停止"), recorder.cancelled)

    def test_unexpected_partial_save_failure_still_cancels_the_stage(self):
        """部分结果保存抛出意外异常时，阶段仍必须落到 cancelled，不能停在 running。"""
        token = CancellationToken()

        class _FailingPartialRecorder(RecordingStageRecorder):
            def save_partial(self, *args, **kwargs):
                raise RuntimeError("partial registry down")

        class _StoppingInstrument(_Instrument):
            def measure_power_with_average(self):
                value = super().measure_power_with_average()
                if self.measured == 1:
                    token.request_stop(reason="用户停止")
                return value

        inputs = replace(
            amplifier_inputs(),
            frequencies=(Frequency.from_ghz(1.0), Frequency.from_ghz(2.0)),
        )
        losses = {
            "1.0": {"cable1": 0.0, "cable2": 0.0, "cable3": 0.0, "cable4": 0.0},
            "2.0": {"cable1": 0.0, "cable2": 0.0, "cable3": 0.0, "cable4": 0.0},
        }
        recorder = _FailingPartialRecorder("run-amplifier")
        request = replace(
            _request(
                _StoppingInstrument(),
                token=token,
                recorder=recorder,
                reader=MappingInputReader(cable_loss={"cable_losses": losses}),
            ),
            inputs=inputs,
        )
        use_case = AmplifierMeasurementUseCase(request, sleep_fn=lambda _: None)

        with self.assertRaisesRegex(RuntimeError, "partial registry down"):
            use_case.measure_all_frequencies()

        self.assertIn((STAGE_AMPLIFIER, "用户停止"), recorder.cancelled)
        self.assertEqual(recorder.failed, [])
        self.assertIsNone(use_case.last_partial_result)


if __name__ == "__main__":
    unittest.main()
