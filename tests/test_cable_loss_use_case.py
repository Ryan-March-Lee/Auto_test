import unittest
from pathlib import Path
from types import SimpleNamespace

from app.cancellation import CancellationToken, MeasurementCancelled
from application.dto import CableLossMeasurementRequest
from application.measurements import CableLossUseCase
from app.events import CheckpointEvent


class _Instrument:
    def __init__(self, *, fail=False):
        self.measured = 0
        self.close_calls = 0
        self.fail = fail

    def set_frequency(self, _frequency): pass
    def set_power(self, _power): pass
    def rf_output_on(self): pass
    def set_center_frequency(self, _frequency): pass
    def set_span(self, _span): pass

    def measure_power_with_average(self):
        if self.fail:
            raise RuntimeError("measurement failed")
        self.measured += 1
        return -10.0

    def rf_output_off(self): pass

    def close_all(self, close_rf=False):
        self.close_calls += 1
        return []


class _Events:
    def __init__(self): self.events = []
    def publish(self, event): self.events.append(event)


class _Repository:
    def __init__(self): self.calls = []
    def save(self, result, **kwargs):
        self.calls.append((result, kwargs))
        return SimpleNamespace(run_directory=Path("saved-run"))


def _request(instrument, *, events=None, token=None, repository=None):
    configuration = {"test_frequencies": [1.0], "attenuator": {"type": "10dB"}}
    return CableLossMeasurementRequest(
        configuration=configuration,
        context=SimpleNamespace(run_id="run-cable"),
        run_directory=Path("run-directory"),
        measurement_port=instrument,
        event_sink=events,
        cancellation_token=token,
        result_repository=repository or _Repository(),
    )


class CableLossUseCaseTests(unittest.TestCase):
    def test_waits_then_continues_and_saves_only_completed_result(self):
        instrument = _Instrument()
        events = _Events()
        repository = _Repository()
        use_case = CableLossUseCase(_request(instrument, events=events, repository=repository), sleep_fn=lambda _: None)

        waiting = use_case.measure_all_frequencies()
        self.assertEqual(waiting["status"], "waiting")
        self.assertEqual(instrument.measured, 1)
        self.assertEqual(repository.calls, [])

        result = use_case.continue_to_step2()
        self.assertIn("cable_losses", result)
        self.assertEqual(instrument.measured, 2)
        self.assertEqual(repository.calls[0][1], {
            "result_type": "cable_loss",
            "run_id": "run-cable",
            "run_directory": Path("run-directory"),
        })
        self.assertEqual(instrument.close_calls, 1)
        self.assertEqual(use_case.run_directory, Path("saved-run"))
        self.assertEqual(
            [event.checkpoint for event in events.events if isinstance(event, CheckpointEvent)],
            ["path1", "path2"],
        )

    def test_stop_while_waiting_cleans_up_and_does_not_measure_path2(self):
        instrument = _Instrument()
        use_case = CableLossUseCase(_request(instrument), sleep_fn=lambda _: None)
        use_case.measure_all_frequencies()

        use_case.stop_measurement()

        self.assertEqual(instrument.measured, 1)
        self.assertEqual(instrument.close_calls, 1)
        use_case.stop_measurement()
        self.assertEqual(instrument.close_calls, 1)

    def test_step_pause_callback_is_called_once(self):
        instrument = _Instrument()
        prompts = []
        use_case = CableLossUseCase(_request(instrument), sleep_fn=lambda _: None)
        use_case.set_step_pause_callback(prompts.append)

        use_case.measure_all_frequencies()

        self.assertEqual(prompts, ["请连接路径2"])

    def test_repeated_start_and_continue_are_rejected(self):
        use_case = CableLossUseCase(_request(_Instrument()), sleep_fn=lambda _: None)
        use_case.measure_all_frequencies()
        with self.assertRaisesRegex(RuntimeError, "已经开始"):
            use_case.measure_all_frequencies()
        use_case.continue_to_step2()
        with self.assertRaisesRegex(RuntimeError, "已经结束"):
            use_case.continue_to_step2()

    def test_path1_exception_makes_use_case_terminal(self):
        instrument = _Instrument(fail=True)
        use_case = CableLossUseCase(_request(instrument), sleep_fn=lambda _: None)

        with self.assertRaisesRegex(RuntimeError, "measurement failed"):
            use_case.measure_all_frequencies()
        with self.assertRaisesRegex(RuntimeError, "已经结束"):
            use_case.measure_all_frequencies()

    def test_result_repository_is_required(self):
        request = _request(_Instrument())
        request = CableLossMeasurementRequest(
            configuration=request.configuration,
            context=request.context,
            run_directory=request.run_directory,
            measurement_port=request.measurement_port,
        )
        with self.assertRaisesRegex(ValueError, "result_repository"):
            CableLossUseCase(request)

    def test_exception_is_cleaned_by_service_and_not_saved(self):
        instrument = _Instrument(fail=True)
        repository = _Repository()
        use_case = CableLossUseCase(_request(instrument, repository=repository), sleep_fn=lambda _: None)

        with self.assertRaisesRegex(RuntimeError, "measurement failed"):
            use_case.measure_all_frequencies()

        self.assertEqual(repository.calls, [])
        self.assertEqual(instrument.close_calls, 1)

    def test_path2_exception_leaves_use_case_terminal_and_does_not_clean_twice(self):
        instrument = _Instrument()
        repository = _Repository()
        use_case = CableLossUseCase(_request(instrument, repository=repository), sleep_fn=lambda _: None)
        use_case.measure_all_frequencies()
        instrument.fail = True

        with self.assertRaisesRegex(RuntimeError, "measurement failed"):
            use_case.continue_to_step2()

        close_calls = instrument.close_calls
        use_case.stop_measurement()
        self.assertEqual(instrument.close_calls, close_calls)
        with self.assertRaisesRegex(RuntimeError, "已经结束"):
            use_case.continue_to_step2()
        with self.assertRaisesRegex(RuntimeError, "已经结束"):
            use_case.measure_all_frequencies()

    def test_cancelled_request_does_not_open_rf(self):
        instrument = _Instrument()
        token = CancellationToken()
        token.request_emergency_stop(reason="test")
        use_case = CableLossUseCase(_request(instrument, token=token), sleep_fn=lambda _: None)

        with self.assertRaises(MeasurementCancelled):
            use_case.measure_all_frequencies()

        self.assertEqual(instrument.measured, 0)
        self.assertEqual(instrument.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
