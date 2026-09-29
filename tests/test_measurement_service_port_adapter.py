import unittest

from instrument.measurement_adapter import PortMeasurementAdapter
from measurement_services import DriverPowerMappingService
from instrument.simulation import (
    CommandRecorder,
    SafetyInstrumentSession,
    SimulatedPowerSupply,
    SimulatedSignalGenerator,
    SimulatedSpectrumAnalyzer,
)


class MeasurementServicePortAdapterTests(unittest.TestCase):
    def make_adapter(self):
        recorder = CommandRecorder()
        sg = SimulatedSignalGenerator(recorder)
        sa = SimulatedSpectrumAnalyzer(readings=(-30.0,), recorder=recorder)
        ps = SimulatedPowerSupply(recorder)
        session = SafetyInstrumentSession(sg, sa, ps, {"gate": "A", "drain": "B"})
        session.validate()
        session.connect()
        session.prepare(frequency_hz=1e9, bandwidth_hz=10e6)
        adapter = PortMeasurementAdapter(
            session,
            dut_power_channels={"gate": "A", "drain": "B"},
            driver_power_channels={"gate": "C", "drain": "D"},
            power_settings={
                "gate": {"voltage_v": 2.8, "current_a": 0.1},
                "drain": {"voltage_v": 28.0, "current_a": 1.0},
            },
        )
        return adapter, session, recorder

    def test_legacy_service_capabilities_use_new_ports_and_units(self):
        adapter, session, recorder = self.make_adapter()
        adapter.set_power(-20)
        adapter.set_frequency(1.0)
        adapter.set_center_frequency(1.0)
        adapter.set_span(10)
        adapter.setup_dut_power()
        adapter.power_on_sequence()
        adapter.rf_output_on()
        self.assertEqual(adapter.measure_power_with_average(), -30.0)
        adapter.rf_output_off()
        adapter.power_off_sequence()
        adapter.close_all()
        self.assertEqual(session.state.value, "cleaned")
        self.assertIn(("signal_generator", "rf_on", None), recorder.commands)
        self.assertIn(("power_supply", "output_off", "B"), recorder.commands)

    def test_measurement_service_uses_adapter_and_cleans_up(self):
        adapter, session, recorder = self.make_adapter()
        result = DriverPowerMappingService(
            {
                "attenuator": {"type": "0dB"},
                "test_frequencies": [1.0],
                "signal_source": {"start_power": -20, "stop_power": -20, "step": 1},
            },
            adapter,
            {"cable_losses": {"1.0": {"cable2": 0.0}}},
            sleep_fn=lambda _: None,
            settle_delay_s=0,
        ).run()
        self.assertEqual(list(result["power_mapping"]), ["1.0"])
        self.assertEqual(session.state.value, "cleaned")
        self.assertIn(("signal_generator", "rf_off", None), recorder.commands)

    def test_measurement_service_failure_still_closes_session(self):
        adapter, session, recorder = self.make_adapter()
        adapter.spectrum_analyzer.inject_failure("measure_power_dbm", phase="query")
        with self.assertRaises(RuntimeError):
            DriverPowerMappingService(
                {
                    "attenuator": {"type": "0dB"},
                    "test_frequencies": [1.0],
                    "signal_source": {"start_power": -20, "stop_power": -20, "step": 1},
                },
                adapter,
                {"cable_losses": {"1.0": {"cable2": 0.0}}},
                sleep_fn=lambda _: None,
                settle_delay_s=0,
            ).run()
        self.assertEqual(session.state.value, "cleaned")
        self.assertIn(("signal_generator", "rf_off", None), recorder.commands)

    def test_driver_and_dut_power_channels_must_not_overlap(self):
        recorder = CommandRecorder()
        session = SafetyInstrumentSession(
            SimulatedSignalGenerator(recorder),
            SimulatedSpectrumAnalyzer(recorder=recorder),
            SimulatedPowerSupply(recorder),
            {"gate": "A", "drain": "B"},
        )
        with self.assertRaisesRegex(ValueError, "overlap"):
            PortMeasurementAdapter(
                session,
                dut_power_channels={"gate": "A", "drain": "B"},
                driver_power_channels={"gate": "A", "drain": "C"},
            )

    def test_close_all_is_idempotent_and_returns_cleanup_errors(self):
        adapter, session, _ = self.make_adapter()
        adapter.session.signal_generator.inject_failure("rf_off", phase="action")
        errors = adapter.close_all()
        self.assertEqual(len(errors), 1)
        adapter.session.signal_generator.fail_on = None
        self.assertEqual(adapter.close_all(), [])
        self.assertEqual(session.state.value, "cleaned")
        self.assertEqual(adapter.close_all(), [])


if __name__ == "__main__":
    unittest.main()
