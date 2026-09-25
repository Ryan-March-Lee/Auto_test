import unittest

from instrument.ports import InstrumentState
from instrument.simulation import (
    CommandRecorder,
    RecordedSequence,
    SafetyInstrumentSession,
    SimulatedPowerSupply,
    SimulatedSignalGenerator,
    SimulatedSpectrumAnalyzer,
)


class SimulationLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.recorder = CommandRecorder()
        self.sg = SimulatedSignalGenerator(self.recorder)
        self.sa = SimulatedSpectrumAnalyzer((-30.0, -25.0), self.recorder)
        self.ps = SimulatedPowerSupply(self.recorder)
        self.session = SafetyInstrumentSession(
            self.sg, self.sa, self.ps, {"gate": "A", "drain": "B"}
        )

    def _prepare(self):
        self.session.validate()
        self.session.connect()
        self.session.prepare(frequency_hz=1e9, bandwidth_hz=1e6)

    def test_normal_completion_has_rf_off_then_drain_gate_then_close(self):
        self._prepare()
        self.session.power_on()
        self.session.start_measurement()
        self.session.set_rf_enabled(True)
        self.assertEqual(self.sa.measure_power_dbm(), -30.0)
        self.assertEqual(self.sa.measure_power_dbm(), -25.0)
        self.session.close()
        actions = [(device, action, value) for device, action, value in self.recorder.commands]
        rf_off = actions.index(("signal_generator", "rf_off", None))
        drain_off = actions.index(("power_supply", "output_off", "B"))
        gate_off = actions.index(("power_supply", "output_off", "A"))
        self.assertLess(rf_off, drain_off)
        self.assertLess(drain_off, gate_off)
        self.assertEqual(self.session.state, InstrumentState.CLEANED)
        self.assertFalse(self.sg.rf_enabled)
        self.assertFalse(any(self.ps.outputs.values()))

    def test_ordinary_stop_emergency_stop_and_window_close_all_clean_idempotently(self):
        for emergency in (False, True):
            with self.subTest(emergency=emergency):
                self.setUp()
                self._prepare()
                self.session.power_on()
                self.session.set_rf_enabled(True)
                if emergency:
                    self.session.stop(emergency=True)
                else:
                    self.session.start_measurement()
                    self.session.stop()
                count = len(self.recorder.commands)
                self.session.close(emergency=emergency)
                self.assertEqual(len(self.recorder.commands), count)
                self.assertEqual(self.session.state, InstrumentState.CLEANED)

    def test_rf_is_default_off_and_power_order_is_gate_then_drain(self):
        self._prepare()
        self.assertFalse(self.sg.rf_enabled)
        self.session.power_on()
        outputs = [(action, value) for device, action, value in self.recorder.commands
                   if device == "power_supply" and action == "output_on"]
        self.assertEqual(outputs, [("output_on", "A"), ("output_on", "B")])

    def test_incomplete_power_mapping_is_rejected_before_connect(self):
        invalid = SafetyInstrumentSession(
            self.sg, self.sa, self.ps, {"gate": "A"}
        )
        with self.assertRaisesRegex(ValueError, "mapped together"):
            invalid.validate()
        self.assertEqual(self.recorder.commands, [])

    def test_rf_off_failure_keeps_signal_generator_open_for_retry(self):
        self._prepare()
        self.session.power_on()
        self.session.set_rf_enabled(True)
        self.sg.fail_on = "rf_off"
        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            self.session.close()
        self.assertTrue(self.sg.connected)
        self.assertFalse(self.ps.connected)
        self.sg.fail_on = None
        self.session.close()
        self.assertFalse(self.sg.connected)

    def test_connection_failure_still_closes_already_connected_resources(self):
        self.sa.fail_on = "connect"
        self.session.validate()
        with self.assertRaisesRegex(RuntimeError, "spectrum analyzer failure"):
            self.session.connect()
        self.assertFalse(self.sg.connected)
        self.assertEqual(self.session.state, InstrumentState.CLEANED)

    def test_setting_reading_and_shutdown_failures_are_injectable_and_cleanup_continues(self):
        self._prepare()
        self.sa.fail_on = "measure_power_dbm"
        with self.assertRaisesRegex(RuntimeError, "spectrum analyzer failure"):
            self.sa.measure_power_dbm()
        self.ps.fail_on = "output_off"
        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            self.session.close()
        self.assertFalse(self.sg.connected)
        self.assertFalse(self.sa.connected)
        self.assertTrue(self.ps.connected)
        self.ps.fail_on = None
        self.session.close()
        self.assertFalse(self.ps.connected)

    def test_prepare_failure_cleans_connected_resources(self):
        self.session.validate()
        self.session.connect()
        self.sa.fail_on = "center_frequency_hz"
        with self.assertRaisesRegex(RuntimeError, "spectrum analyzer failure"):
            self.session.prepare(frequency_hz=1e9)
        self.assertFalse(self.sg.connected)
        self.assertFalse(self.sa.connected)
        self.assertFalse(self.ps.connected)
        self.assertEqual(self.session.state, InstrumentState.CLEANED)

    def test_power_on_failure_cleans_partial_power(self):
        self._prepare()
        self.session.power_on()
        self.assertEqual(self.ps.outputs, {"A": True, "B": True})
        self.ps.fail_on = "output_on"
        with self.assertRaisesRegex(RuntimeError, "simulated power supply failure"):
            self.session.power_on()
        self.assertFalse(self.sg.connected)
        self.assertFalse(self.ps.connected)
        self.assertFalse(any(self.ps.outputs.values()))

    def test_power_driven_readings_and_power_readings(self):
        sg = SimulatedSignalGenerator(self.recorder)
        sa = SimulatedSpectrumAnalyzer(
            recorder=self.recorder,
            reading_model=lambda power: power - 3.0,
            input_power_source=sg,
        )
        ps = SimulatedPowerSupply(
            self.recorder, voltage_readings={"A": 2.8}, current_readings={"A": 0.1}
        )
        sg.connect()
        sg.prepared = True
        sg.set_power_dbm(10.0)
        sa.connect()
        sa.configure_center_frequency_hz(1e9)
        self.assertEqual(sa.measure_power_dbm(), 7.0)
        ps.connect()
        ps.set_output_enabled("A", True)
        self.assertEqual(ps.read_voltage("A"), 2.8)
        self.assertEqual(ps.read_current("A"), 0.1)

    def test_recorded_sequence_round_trips_as_json(self):
        self.recorder.record("signal_generator", "set_power_dbm", 10.0)
        sequence = RecordedSequence.from_recorder(self.recorder)
        restored = RecordedSequence.from_json(sequence.to_json())
        replayed = []
        restored.replay(lambda *command: replayed.append(command))
        self.assertEqual(replayed, [("signal_generator", "set_power_dbm", 10.0)])


if __name__ == "__main__":
    unittest.main()
