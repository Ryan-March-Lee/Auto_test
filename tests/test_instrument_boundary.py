import unittest

from instrument.power_control import PowerController, PowerControlError
from instrument.simulation import CommandRecorder, SimulatedPowerSupply, SimulatedSignalGenerator, SimulatedSpectrumAnalyzer
from instrument.session import ManagedInstrumentSession


class InstrumentBoundaryTests(unittest.TestCase):
    def test_power_controller_uses_gate_then_drain_on_and_drain_then_gate_off(self):
        recorder = CommandRecorder()
        supply = SimulatedPowerSupply(recorder)
        supply.connect()
        controller = PowerController(supply, {"gate": "CH1", "drain": "CH2"})

        controller.power_on()
        controller.power_off()

        actions = [(action, value) for device, action, value in recorder.commands
                   if device == "power_supply" and action in {"output_on", "output_off"}]
        self.assertEqual(actions, [
            ("output_on", "CH1"), ("output_on", "CH2"),
            ("output_off", "CH2"), ("output_off", "CH1"),
        ])

    def test_power_controller_attempts_gate_after_drain_failure(self):
        recorder = CommandRecorder()
        supply = SimulatedPowerSupply(recorder)
        supply.connect()
        supply.inject_failure("output_off", once=True)
        controller = PowerController(supply, {"gate": "CH1", "drain": "CH2"})

        with self.assertRaises(PowerControlError):
            controller.power_off()

        self.assertIn(("power_supply", "output_off", "CH1"), recorder.commands)

    def test_formal_session_uses_explicit_resource_ownership(self):
        recorder = CommandRecorder()
        signal = SimulatedSignalGenerator(recorder)
        analyzer = SimulatedSpectrumAnalyzer(recorder=recorder)
        supply = SimulatedPowerSupply(recorder)
        session = ManagedInstrumentSession(
            signal, analyzer, supply, {"gate": "CH1", "drain": "CH2"},
            owns_signal_generator=False,
            owns_spectrum_analyzer=False,
            owns_power_supply=False,
        )
        session.validate()
        session.connect()
        session.prepare()
        session.close()

        self.assertTrue(signal.connected)
        self.assertTrue(analyzer.connected)
        self.assertTrue(supply.connected)
        self.assertEqual(session.state.value, "cleaned")


if __name__ == "__main__":
    unittest.main()
