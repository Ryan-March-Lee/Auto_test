import unittest

from instrument.ports import InstrumentState
from instrument.action import SpectrumAnalyzerActions
from instrument.simulation import (
    CommandRecorder,
    FailureInjector,
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

    def test_ordinary_stop_requires_measurement_but_emergency_stop_accepts_prepared_state(self):
        self._prepare()
        with self.assertRaisesRegex(ValueError, "ordinary stop is invalid"):
            self.session.stop()
        self.assertEqual(self.session.state, InstrumentState.PREPARED)
        self.assertTrue(self.sg.connected)

        self.session.stop(emergency=True)
        self.assertEqual(self.session.state, InstrumentState.CLEANED)
        self.assertFalse(self.sg.connected)
        self.assertFalse(self.sa.connected)
        self.assertFalse(self.ps.connected)

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

    def test_signal_generator_and_power_supply_connection_failures_are_cleaned(self):
        for device_name in ("signal_generator", "power_supply"):
            with self.subTest(device=device_name):
                self.setUp()
                device = getattr(self, "sg" if device_name == "signal_generator" else "ps")
                device.fail_on = "connect"
                self.session.validate()
                with self.assertRaisesRegex(RuntimeError, "failure: connect"):
                    self.session.connect()
                self.assertEqual(self.session.state, InstrumentState.CLEANED)
                self.assertFalse(self.sg.connected)
                self.assertFalse(self.sa.connected)
                self.assertFalse(self.ps.connected)

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

    def test_rf_enable_failure_closes_resources_and_never_marks_rf_enabled(self):
        self._prepare()
        self.session.power_on()
        self.session.start_measurement()
        self.sg.inject_failure("rf_on")
        with self.assertRaisesRegex(RuntimeError, "failure: rf_on"):
            self.session.set_rf_enabled(True)
        self.session.close()
        self.assertFalse(self.sg.rf_enabled)
        self.assertFalse(any(self.ps.outputs.values()))
        self.assertFalse(self.sa.connected)
        self.assertEqual(self.session.state, InstrumentState.CLEANED)

    def test_spectrum_analyzer_action_accepts_simulated_driver(self):
        analyzer = SimulatedSpectrumAnalyzer((-17.5,), self.recorder)
        actions = SpectrumAnalyzerActions(analyzer)
        actions.connect()
        actions.set_center_frequency_hz(1e9)
        actions.set_span_hz(1e6)
        self.assertEqual(actions.measure_peak_power_dbm(), -17.5)
        actions.close()

    def test_query_failure_during_measurement_is_followed_by_safe_cleanup(self):
        self._prepare()
        self.session.power_on()
        self.session.start_measurement()
        self.session.set_rf_enabled(True)
        self.sa.inject_failure("measure_power_dbm", phase="query")
        with self.assertRaisesRegex(RuntimeError, "query failure"):
            self.sa.measure_power_dbm()

        self.session.close()
        self.assertFalse(self.sg.rf_enabled)
        self.assertFalse(any(self.ps.outputs.values()))
        self.assertFalse(self.sg.connected)
        self.assertFalse(self.sa.connected)
        self.assertFalse(self.ps.connected)

    def test_drain_and_gate_shutdown_failures_still_attempt_later_cleanup(self):
        for failed_channel in ("B", "A"):
            with self.subTest(failed_channel=failed_channel):
                self.setUp()
                self._prepare()
                self.session.power_on()
                self.session.set_rf_enabled(True)
                failed = {"done": False}
                def fail_selected_shutdown(action):
                    if action != "output_off":
                        return False
                    failed["done"] = not failed["done"]
                    return failed["done"] if failed_channel == "B" else not failed["done"]

                self.ps.inject_failure(fail_selected_shutdown)
                with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                    self.session.close()

                off_channels = [
                    value for device, action, value in self.recorder.commands
                    if device == "power_supply" and action == "output_off"
                ]
                self.assertEqual(off_channels, ["B", "A"])
                self.assertFalse(self.sg.rf_enabled)
                self.assertFalse(self.sg.connected)
                self.assertFalse(self.sa.connected)
                self.assertTrue(self.ps.connected)
                self.session.close()
                self.assertFalse(self.ps.connected)

    def test_connection_close_failure_keeps_resource_for_retry_after_other_cleanup(self):
        self._prepare()
        self.session.power_on()
        self.ps.inject_failure("close", phase="cleanup")
        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            self.session.close()

        self.assertFalse(self.sg.connected)
        self.assertFalse(self.sa.connected)
        self.assertTrue(self.ps.connected)
        self.assertEqual(self.session.state, InstrumentState.STOPPING)

        self.session.close()
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

    def test_recorded_sequence_asserts_order_across_interleaved_commands(self):
        self.recorder.commands = [
            ("power_supply", "set_voltage", ("A", 2.8)),
            ("power_supply", "output_on", "A"),
            ("spectrum_analyzer", "measure_power_dbm", None),
            ("power_supply", "output_on", "B"),
        ]
        sequence = RecordedSequence.from_recorder(self.recorder)

        sequence.assert_order(
            ("power_supply", "output_on", "A"),
            ("power_supply", "output_on", "B"),
        )
        sequence.assert_action_order(
            ("power_supply", "output_on"),
            ("spectrum_analyzer", "measure_power_dbm"),
        )

        with self.assertRaisesRegex(AssertionError, "nearby commands"):
            sequence.assert_order(
                ("power_supply", "output_on", "B"),
                ("power_supply", "output_on", "A"),
            )

        with self.assertRaisesRegex(ValueError, "3-item tuples"):
            sequence.assert_order(("power_supply", "output_on"))
        with self.assertRaisesRegex(ValueError, "2-item tuples"):
            sequence.assert_action_order(("power_supply",))

    def test_all_devices_share_one_step_failure_injection_contract(self):
        for device, action in (
            (self.sg, "connect"),
            (self.sa, "connect"),
            (self.ps, "connect"),
        ):
            with self.subTest(device=device.device_name):
                device.inject_failure(action)
                with self.assertRaisesRegex(RuntimeError, action):
                    device.connect()
                device.connect()
                self.assertEqual(
                    [entry[1] for entry in self.recorder.commands[-2:]],
                    [action, action],
                )

    def test_query_and_cleanup_failures_are_injected_independently(self):
        self.sg.connect()
        self.sg.inject_failure("close", phase="cleanup")
        with self.assertRaisesRegex(RuntimeError, "close"):
            self.sg.close()
        self.assertTrue(self.sg.connected)
        self.sg.close()
        self.assertFalse(self.sg.connected)

        self.sa.connect()
        self.sa.configure_center_frequency_hz(1e9)
        self.sa.inject_failure("measure_power_dbm", phase="query")
        with self.assertRaisesRegex(RuntimeError, "query failure"):
            self.sa.measure_power_dbm()
        self.assertEqual(self.sa.measure_power_dbm(), -30.0)

    def test_failure_injector_rejects_unknown_phase(self):
        with self.assertRaises(ValueError):
            FailureInjector().inject("connect", phase="unknown")

    def test_persistent_action_failure_remains_active(self):
        device = SimulatedSignalGenerator(self.recorder)
        device.inject_failure("connect", once=False)
        with self.assertRaisesRegex(RuntimeError, "connect"):
            device.connect()
        with self.assertRaisesRegex(RuntimeError, "connect"):
            device.connect()


if __name__ == "__main__":
    unittest.main()
