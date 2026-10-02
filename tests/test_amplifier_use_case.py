import unittest
from pathlib import Path
from types import SimpleNamespace

from app.cancellation import CancellationToken, MeasurementCancelled
from application.dto import AmplifierMeasurementRequest
from application.measurements import AmplifierMeasurementUseCase
from config_models import (
    ChannelMapping,
    InstrumentMapping,
    PowerChannelPlan,
    RunConfiguration,
    RunResourceMapping,
    TestPlan,
)


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


def _request(instrument, *, token=None, repository=None):
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
        cancellation_token=token,
        result_repository=repository or _Repository(),
        loss_data={
            "cable_losses": {
                "1.0": {
                    "cable1": 0.0,
                    "cable2": 0.0,
                    "cable3": 0.0,
                    "cable4": 0.0,
                }
            }
        },
    )


class AmplifierMeasurementUseCaseTests(unittest.TestCase):
    def test_split_run_configuration_is_converted_to_service_contract(self):
        configuration = RunConfiguration(
            test_plan=TestPlan(
                frequencies=[1.0],
                start_power=-20,
                stop_power=-20,
                power_step=1,
                compression_point="1dB",
                attenuator_value=10,
                max_input_power=100,
                dut_power_channels={
                    "drain": PowerChannelPlan(role="drain", voltage=28)
                },
            ),
            run_mapping=RunResourceMapping(
                instruments={
                    "power_supply": InstrumentMapping(model="PS1")
                },
                dut_power_channels=[ChannelMapping(channel="CH2", role="drain")],
            ),
        )
        request = _request(_Instrument())
        request = AmplifierMeasurementRequest(
            configuration=configuration,
            context=request.context,
            run_directory=request.run_directory,
            measurement_port=request.measurement_port,
            result_repository=request.result_repository,
            loss_data=request.loss_data,
        )

        use_case = AmplifierMeasurementUseCase(request, sleep_fn=lambda _: None)

        self.assertEqual(use_case.config["test_frequencies"], [1.0])
        self.assertEqual(use_case.config["attenuator"], {"type": "10dB"})
        self.assertEqual(
            use_case.config["power_supply_assignment"]["dut_amplifier"]["supplies"]["PS1"],
            {"name": "PS1", "channel": ["CH2"]},
        )

    def test_completes_and_saves_structured_result(self):
        instrument = _Instrument()
        repository = _Repository()
        use_case = AmplifierMeasurementUseCase(
            _request(instrument, repository=repository), sleep_fn=lambda _: None
        )

        result = use_case.measure_all_frequencies()

        self.assertEqual(result, use_case.last_result)
        self.assertEqual(use_case.measurement_results, result["results"])
        self.assertEqual(repository.calls[0][1]["result_type"], "amplifier_measurement")
        self.assertEqual(use_case.run_directory, Path("saved-run"))
        self.assertEqual(instrument.close_calls, 1)

    def test_measurement_port_is_required_before_input_loading(self):
        request = _request(None)
        with self.assertRaisesRegex(ValueError, "注入 measurement_port"):
            AmplifierMeasurementUseCase(request)

    def test_cancelled_request_is_cleaned_by_service(self):
        instrument = _Instrument()
        token = CancellationToken()
        token.request_emergency_stop(reason="test")
        use_case = AmplifierMeasurementUseCase(
            _request(instrument, token=token), sleep_fn=lambda _: None
        )

        with self.assertRaises(MeasurementCancelled):
            use_case.measure_all_frequencies()

        self.assertEqual(instrument.measured, 0)


if __name__ == "__main__":
    unittest.main()
