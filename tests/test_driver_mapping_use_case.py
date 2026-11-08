import unittest
from pathlib import Path
from types import SimpleNamespace

from app.cancellation import CancellationToken, MeasurementCancelled
from application.dto import DriverPowerMappingRequest
from application.measurements import DriverPowerMappingUseCase
from application.run_stages import StageInputUnavailable
from app.events import RealtimeDataEvent
from domain.run.models import STAGE_CABLE_LOSS, STAGE_DRIVER_MAPPING
from tests.contract_doubles import MappingInputReader, RecordingStageRecorder
from tests.fakes import safety_cleanup_for


CABLE_LOSS = {"cable_losses": {"1.0": {"cable2": 2.0}}}


class _Instrument:
    def __init__(self):
        self.measured = 0
        self.power = 0.0
        self.frequency = None
        self.rf_off_calls = 0
        self.driver_off_calls = 0

    def setup_driver_amplifier_power(self): pass
    def power_on_driver(self): pass
    def set_power(self, power): self.power = power
    def set_frequency(self, frequency): self.frequency = frequency
    def set_center_frequency(self, _frequency): pass
    def set_span(self, _span): pass
    def rf_output_on(self): pass

    def measure_power_with_average(self):
        self.measured += 1
        # 补偿后输出 = 源功率 + 1 dB：线损补偿（衰减器 10dB + 线②2dB）由服务加回。
        return self.power + 1.0 - 12.0

    def rf_output_off(self):
        self.rf_off_calls += 1

    def power_off_driver(self):
        self.driver_off_calls += 1

    def close_all(self, close_rf=False):
        return []


class _Events:
    def __init__(self): self.events = []
    def publish(self, event): self.events.append(event)


class _Repository:
    def __init__(self): self.calls = []
    def save(self, result, **kwargs):
        self.calls.append((result, kwargs))
        return SimpleNamespace(run_directory=Path("saved-run"))


def _request(
    instrument,
    *,
    reader=None,
    recorder=None,
    events=None,
    token=None,
    repository=None,
    frequencies=(1.0,),
):
    configuration = {
        "test_frequencies": list(frequencies),
        "attenuator": {"type": "10dB"},
        "signal_source": {"start_power": -20, "stop_power": -20, "step": 1},
        "max_dut_input_power": 100.0,
        "driver_gain_estimate_db": 1.0,
    }
    return DriverPowerMappingRequest(
        configuration=configuration,
        context=SimpleNamespace(run_id="run-mapping"),
        run_directory=Path("run-directory"),
        measurement_port=instrument,
        stage_recorder=recorder or RecordingStageRecorder("run-mapping"),
        safety_cleanup=safety_cleanup_for(instrument),
        event_sink=events,
        cancellation_token=token,
        result_repository=repository or _Repository(),
        input_reader=reader or MappingInputReader(cable_loss=CABLE_LOSS),
        safety_options={"owns_measurement_port": True},
    )


class DriverPowerMappingUseCaseTests(unittest.TestCase):
    def test_current_run_loss_input_completes_and_saves_result(self):
        instrument = _Instrument()
        events = _Events()
        recorder = RecordingStageRecorder("run-mapping")
        use_case = DriverPowerMappingUseCase(
            _request(instrument, recorder=recorder, events=events),
            sleep_fn=lambda _: None,
        )

        result = use_case.measure_all_frequencies()

        self.assertEqual(result, use_case.last_result)
        self.assertIn("1.0", use_case.power_mapping)
        # 结果只写当前运行：阶段提交到记录器，路径指向本次运行目录。
        self.assertEqual(
            recorder.result_paths(),
            {STAGE_DRIVER_MAPPING: f"inputs/{STAGE_DRIVER_MAPPING}.json"},
        )
        self.assertEqual(
            result.result_reference["archive_path"],
            str(Path("run-directory") / "inputs" / f"{STAGE_DRIVER_MAPPING}.json"),
        )
        self.assertEqual(use_case.run_directory, Path("run-directory"))
        self.assertTrue(any(isinstance(event, RealtimeDataEvent) for event in events.events))
        self.assertEqual(instrument.rf_off_calls, 1)
        self.assertEqual(instrument.driver_off_calls, 1)

    def test_unfinished_cable_loss_stage_is_rejected_before_hardware_action(self):
        """前置阶段没有完成时，映射测量必须在任何硬件动作前失败。"""
        instrument = _Instrument()
        recorder = RecordingStageRecorder("run-mapping", missing_stages=(STAGE_CABLE_LOSS,))
        with self.assertRaises(StageInputUnavailable):
            DriverPowerMappingUseCase(
                _request(instrument, recorder=recorder), sleep_fn=lambda _: None
            )
        self.assertEqual(instrument.measured, 0)

    def test_measurement_port_is_required_before_input_loading(self):
        request = _request(None)
        with self.assertRaisesRegex(ValueError, "注入 measurement_port"):
            DriverPowerMappingUseCase(request)

    def test_reader_reads_the_current_run_input_path(self):
        reader = MappingInputReader(cable_loss=CABLE_LOSS)
        use_case = DriverPowerMappingUseCase(
            _request(_Instrument(), reader=reader), sleep_fn=lambda _: None
        )
        self.assertEqual(use_case.loss_data, CABLE_LOSS)
        self.assertEqual(
            reader.paths,
            [Path("runs") / "run-mapping" / "inputs" / f"{STAGE_CABLE_LOSS}.json"],
        )

    def test_missing_run_input_file_fails_before_hardware_action(self):
        """运行目录里的输入读不到时，映射测量必须在任何硬件动作前失败。"""
        instrument = _Instrument()
        with self.assertRaises(FileNotFoundError):
            DriverPowerMappingUseCase(
                _request(instrument, reader=MappingInputReader()), sleep_fn=lambda _: None
            )
        self.assertEqual(instrument.measured, 0)

    def test_cancelled_request_stops_before_measurement_and_cleans_up(self):
        instrument = _Instrument()
        token = CancellationToken()
        token.request_stop(reason="test")
        use_case = DriverPowerMappingUseCase(
            _request(instrument, token=token),
            sleep_fn=lambda _: None,
        )

        with self.assertRaises(MeasurementCancelled):
            use_case.measure_all_frequencies()

        self.assertEqual(instrument.measured, 0)
        self.assertEqual(instrument.driver_off_calls, 1)
        self.assertEqual(instrument.rf_off_calls, 0)

    def test_stop_saves_only_frequencies_that_finished_the_sweep(self):
        token = CancellationToken()

        class _StoppingInstrument(_Instrument):
            def set_frequency(self, frequency):
                super().set_frequency(frequency)
                if frequency == 2.0 and not token.is_cancelled:
                    token.request_stop(reason="用户停止")

        instrument = _StoppingInstrument()
        recorder = RecordingStageRecorder("run-mapping")
        use_case = DriverPowerMappingUseCase(
            _request(
                instrument,
                token=token,
                recorder=recorder,
                frequencies=(1.0, 2.0),
            ),
            sleep_fn=lambda _: None,
        )

        with self.assertRaises(MeasurementCancelled):
            use_case.measure_all_frequencies()

        self.assertEqual(len(recorder.partials), 1)
        stage, payload, path = recorder.partials[0]
        self.assertEqual(stage, STAGE_DRIVER_MAPPING)
        self.assertEqual(path, "results/driver_mapping_partial.json")
        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(set(payload["raw_points"]), {"1.0"})
        self.assertEqual(payload["target_mapping"], {})
        self.assertEqual(use_case.last_partial_result.status.value, "cancelled")
        self.assertEqual(len(use_case.last_partial_result.raw_points), 2)
        self.assertIn((STAGE_DRIVER_MAPPING, "用户停止"), recorder.cancelled)

    def test_unexpected_partial_save_failure_still_cancels_the_stage(self):
        """部分结果保存抛出意外异常时，阶段仍必须落到 cancelled，不能停在 running。"""
        token = CancellationToken()

        class _FailingPartialRecorder(RecordingStageRecorder):
            def save_partial(self, *args, **kwargs):
                raise RuntimeError("partial registry down")

        class _StoppingInstrument(_Instrument):
            def set_frequency(self, frequency):
                super().set_frequency(frequency)
                if frequency == 2.0 and not token.is_cancelled:
                    token.request_stop(reason="用户停止")

        recorder = _FailingPartialRecorder("run-mapping")
        use_case = DriverPowerMappingUseCase(
            _request(
                _StoppingInstrument(),
                token=token,
                recorder=recorder,
                frequencies=(1.0, 2.0),
            ),
            sleep_fn=lambda _: None,
        )

        with self.assertRaisesRegex(RuntimeError, "partial registry down"):
            use_case.measure_all_frequencies()

        self.assertIn((STAGE_DRIVER_MAPPING, "用户停止"), recorder.cancelled)
        self.assertEqual(recorder.failed, [])
        self.assertIsNone(use_case.last_partial_result)


if __name__ == "__main__":
    unittest.main()
