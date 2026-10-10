import unittest

from instrument.action import PowerSupplyActions
from instrument.flow import (
    PowerOffFlow,
    PowerOffFlowError,
    PowerOnFlow,
    PowerOnFlowError,
    PowerChannelSetup,
    SafetyShutdownError,
    SafetyShutdownFlow,
)
from measurement_lifecycle import cleanup_measurement
from instrument.simulation import CommandRecorder, SimulatedPowerSupply, SimulatedSignalGenerator


class SafetyFlowTests(unittest.TestCase):
    def setUp(self):
        self.recorder = CommandRecorder()
        self.supply = SimulatedPowerSupply(self.recorder)
        self.supply.connect()
        self.gate = PowerSupplyActions(self.supply, channel="A")
        self.drain = PowerSupplyActions(self.supply, channel="B")
        self.delays = []
        self.events = []

    def test_power_on_configures_before_gate_then_drain(self):
        flow = PowerOnFlow(
            {"gate": self.gate, "drain": self.drain},
            sleep_fn=self.delays.append,
            settle_time_s=1.5,
            event_sink=lambda step, status: self.events.append((step, status)),
        )
        flow.run({
            "gate": PowerChannelSetup(2.8, 0.1),
            "drain": {"voltage_v": 28.0, "current_a": 1.0},
        })
        actions = [(action, value) for device, action, value in self.recorder.commands
                   if device == "power_supply" and action != "connect"]
        self.assertEqual(actions, [
            ("set_voltage", ("A", 2.8)),
            ("set_current_limit", ("A", 0.1)),
            ("set_voltage", ("B", 28.0)),
            ("set_current_limit", ("B", 1.0)),
            ("output_on", "A"),
            ("output_on", "B"),
        ])
        self.assertEqual(self.delays, [1.5])
        self.assertEqual(self.events[-1], ("drain.output_on", "completed"))

    def test_power_on_failure_attempts_both_outputs_off(self):
        self.supply.inject_failure("output_on", once=True)
        flow = PowerOnFlow({"gate": self.gate, "drain": self.drain})
        with self.assertRaises(PowerOnFlowError) as raised:
            flow.run({"gate": {"voltage_v": 2, "current_a": 1}, "drain": {"voltage_v": 20, "current_a": 1}})
        self.assertEqual(len(raised.exception.errors), 1)
        self.assertFalse(any(self.supply.outputs.values()))

    def test_power_off_continues_after_drain_failure_and_can_repeat(self):
        flow = PowerOnFlow({"gate": self.gate, "drain": self.drain})
        flow.run({"gate": {"voltage_v": 2, "current_a": 1}, "drain": {"voltage_v": 20, "current_a": 1}})
        self.supply.inject_failure("output_off", once=True)
        off = PowerOffFlow({"gate": self.gate, "drain": self.drain})
        with self.assertRaises(PowerOffFlowError):
            off.run()
        self.assertEqual(
            [value for device, action, value in self.recorder.commands if device == "power_supply" and action == "output_off"],
            ["B", "A"],
        )
        off.run()
        self.assertFalse(any(self.supply.outputs.values()))

    def test_shutdown_aggregates_errors_and_is_idempotent_after_success(self):
        signal = SimulatedSignalGenerator(self.recorder)
        signal.connect()
        signal.prepared = True
        signal.set_rf_enabled(True)
        events = []
        flow = SafetyShutdownFlow(
            rf_off=lambda: events.append("rf_off"),
            power_off=lambda: events.append("power_off"),
            close=lambda **_flags: events.append("close") or [],
        )
        flow.run()
        flow.run()
        self.assertEqual(events, ["rf_off", "power_off", "close"])

    def test_shutdown_reports_all_failures(self):
        flow = SafetyShutdownFlow(
            rf_off=lambda: (_ for _ in ()).throw(RuntimeError("rf")),
            power_off=lambda: (_ for _ in ()).throw(RuntimeError("power")),
            close=lambda **_flags: [RuntimeError("close")],
        )
        with self.assertRaises(SafetyShutdownError) as raised:
            flow.run()
        self.assertEqual([str(error) for error in raised.exception.errors], ["rf", "power", "close"])

    def test_shutdown_can_leave_borrowed_resource_open(self):
        events = []
        flow = SafetyShutdownFlow(
            rf_off=lambda: events.append("rf_off"),
            power_off=lambda: events.append("power_off"),
            close=lambda **_flags: events.append("close") or [],
        )
        flow.run(close_resource=False)
        self.assertEqual(events, ["rf_off", "power_off"])

    def test_cleanup_measurement_passes_borrowed_resource_boundary_to_flow(self):
        events = []
        flow = SafetyShutdownFlow(
            rf_off=lambda: events.append("rf_off"),
            power_off=lambda: events.append("power_off"),
            close=lambda **_flags: events.append("close") or [],
        )
        cleanup_measurement(object(), safety_flow=flow, close_resource=False)
        self.assertEqual(events, ["rf_off", "power_off"])

    def test_shutdown_still_closes_legacy_connections_after_power_failure(self):
        events = []

        def power_off():
            events.append("power_off")
            raise RuntimeError("power")

        def legacy_close(*, close_rf):
            events.append(("close", close_rf))
            return []

        flow = SafetyShutdownFlow(
            rf_off=lambda: events.append("rf_off"),
            power_off=power_off,
            close=legacy_close,
        )
        with self.assertRaises(SafetyShutdownError):
            flow.run()
        self.assertEqual(events, ["rf_off", "power_off", ("close", False)])


if __name__ == "__main__":
    unittest.main()
