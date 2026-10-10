import unittest
from pathlib import Path
from types import SimpleNamespace

from app.cancellation import CancellationToken, MeasurementCancelled
from application.dto import DriverPowerMappingRequest
from application.measurements import DriverPowerMappingUseCase
from app.events import RealtimeDataEvent


class _Instrument:
    def __init__(self):
        self.measured = 0
        self.rf_off_calls = 0
        self.driver_off_calls = 0

    def setup_driver_amplifier_power(self): pass
    def power_on_driver(self): pass
    def set_power(self, _power): pass
    def set_frequency(self, _frequency): pass
    def set_center_frequency(self, _frequency): pass
    def set_span(self, _span): pass
    def rf_output_on(self): pass

    def measure_power_with_average(self):
        self.measured += 1
        return -10.0

    def rf_output_off(self):
        self.rf_off_calls += 1

    def power_off_driver(self):
        self.driver_off_calls += 1

    def close_all(self, close_rf=False):
        return []


class _Events:
    def __init__(self): self.events = []
    def publish(self, event): self.events.append(event)


class _Reader:
    def __init__(self, loss_data=None): self.loss_data = loss_data
    def read_cable_loss(self, path=None):
        if self.loss_data is None:
            raise FileNotFoundError("missing cable loss")
        return self.loss_data


class _Repository:
    def __init__(self): self.calls = []
    def save(self, result, **kwargs):
        self.calls.append((result, kwargs))
        return SimpleNamespace(run_directory=Path("saved-run"))


def _request(instrument, *, loss_data=None, reader=None, events=None, token=None, repository=None):
    configuration = {
        "test_frequencies": [1.0],
        "attenuator": {"type": "10dB"},
        "signal_source": {"start_power": -20, "stop_power": -20, "step": 1},
    }
    return DriverPowerMappingRequest(
        configuration=configuration,
        context=SimpleNamespace(run_id="run-mapping"),
        run_directory=Path("run-directory"),
        measurement_port=instrument,
        event_sink=events,
        cancellation_token=token,
        result_repository=repository or _Repository(),
        input_reader=reader,
        loss_data=loss_data,
        safety_options={"owns_measurement_port": True},
    )


class DriverPowerMappingUseCaseTests(unittest.TestCase):
    def test_explicit_loss_data_completes_and_saves_result(self):
        instrument = _Instrument()
        events = _Events()
        repository = _Repository()
        use_case = DriverPowerMappingUseCase(
            _request(
                instrument,
                loss_data={"cable_losses": {"1.0": {"cable2": 2.0}}},
                events=events,
                repository=repository,
            ),
            sleep_fn=lambda _: None,
        )

        result = use_case.measure_all_frequencies()

        self.assertEqual(result, use_case.last_result)
        self.assertIn("1.0", use_case.power_mapping)
        self.assertEqual(repository.calls[0][1]["result_type"], "driver_power_mapping")
        self.assertEqual(use_case.run_directory, Path("saved-run"))
        self.assertTrue(any(isinstance(event, RealtimeDataEvent) for event in events.events))
        self.assertEqual(instrument.rf_off_calls, 1)
        self.assertEqual(instrument.driver_off_calls, 1)

    def test_missing_loss_input_is_rejected(self):
        with self.assertRaises(FileNotFoundError):
            DriverPowerMappingUseCase(
                _request(_Instrument(), reader=_Reader()), sleep_fn=lambda _: None
            )

    def test_measurement_port_is_required_before_input_loading(self):
        request = _request(
            None,
            loss_data={"cable_losses": {"1.0": {"cable2": 2.0}}},
        )
        with self.assertRaisesRegex(ValueError, "注入 measurement_port"):
            DriverPowerMappingUseCase(request)

    def test_reader_is_used_for_explicit_input(self):
        reader = _Reader({"cable_losses": {"1.0": {"cable2": 2.0}}})
        use_case = DriverPowerMappingUseCase(
            _request(_Instrument(), reader=reader), sleep_fn=lambda _: None
        )
        self.assertEqual(use_case.loss_data, {"cable_losses": {"1.0": {"cable2": 2.0}}})

    def test_cancelled_request_stops_before_measurement_and_cleans_up(self):
        instrument = _Instrument()
        token = CancellationToken()
        token.request_emergency_stop(reason="test")
        use_case = DriverPowerMappingUseCase(
            _request(instrument, loss_data={"cable_losses": {"1.0": {"cable2": 2.0}}}, token=token),
            sleep_fn=lambda _: None,
        )

        with self.assertRaises(MeasurementCancelled):
            use_case.measure_all_frequencies()

        self.assertEqual(instrument.measured, 0)
        self.assertEqual(instrument.driver_off_calls, 1)
        self.assertEqual(instrument.rf_off_calls, 0)


if __name__ == "__main__":
    unittest.main()
