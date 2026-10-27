import unittest

from application.ports.measurement_session import (
    CleanupRecord,
    CleanupStepStatus,
    SafetyCleanupError,
)
from instrument.action import PowerSupplyActions
from instrument.flow import (
    PowerOffFlow,
    PowerOffFlowError,
    PowerOnFlow,
    PowerOnFlowError,
    PowerChannelSetup,
    SafetyCleanupAdapter,
    SafetyShutdownError,
    SafetyShutdownFlow,
    cleanup_measurement,
)
from instrument.simulation import CommandRecorder, SimulatedPowerSupply, SimulatedSignalGenerator
from instrument.safety import (
    InstrumentSafetyError,
    close_accepts_safety_flags,
    shutdown_instruments,
)


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

    def test_cleanup_measurement_closes_legacy_controller_after_power_failure(self):
        events = []

        class _Controller:
            signal_gen = object()

            def rf_output_off(self):
                events.append("rf_off")

            def close_all(self, *, close_rf):
                events.append(("close", close_rf))
                return []

        def power_cleanup():
            events.append("power_off")
            raise RuntimeError("power")

        with self.assertRaisesRegex(RuntimeError, "测量安全清理存在失败"):
            cleanup_measurement(_Controller(), power_cleanup=power_cleanup)
        self.assertEqual(events, ["rf_off", "power_off", ("close", False)])

    def test_cleanup_measurement_skips_rf_when_signal_generator_is_absent(self):
        events = []

        class _Controller:
            def close_all(self, *, close_rf):
                events.append(("close", close_rf))
                return []

        cleanup_measurement(_Controller())
        self.assertEqual(events, [("close", False)])

    def test_cleanup_measurement_can_leave_borrowed_controller_open(self):
        events = []

        class _Controller:
            signal_gen = object()

            def rf_output_off(self):
                events.append("rf_off")

            def close_all(self, *, close_rf):  # pragma: no cover - must not be called
                events.append("close")
                return []

        cleanup_measurement(_Controller(), close_resource=False)
        self.assertEqual(events, ["rf_off"])

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


class SafetyCloseCallFormTests(unittest.TestCase):
    """关闭回调的调用形式按签名判定，不靠捕获 TypeError 猜测签名。"""

    def test_close_accepts_safety_flags_detects_both_conventions(self):
        self.assertTrue(close_accepts_safety_flags(lambda **_: None))
        self.assertTrue(close_accepts_safety_flags(lambda *, rf_safe: None))
        self.assertTrue(close_accepts_safety_flags(lambda *, power_safe: None))
        self.assertFalse(close_accepts_safety_flags(lambda *, close_rf: None))
        self.assertFalse(close_accepts_safety_flags(lambda: None))

    def test_flagged_close_type_error_is_reported_once_without_retry(self):
        calls = []

        def close(*, rf_safe, power_safe):
            calls.append((rf_safe, power_safe))
            raise TypeError("device write rejected")

        flow = SafetyShutdownFlow(rf_off=lambda: None, close=close)
        with self.assertRaises(SafetyShutdownError) as raised:
            flow.run()

        self.assertEqual(
            [str(error) for error in raised.exception.errors], ["device write rejected"]
        )
        self.assertEqual(calls, [(True, True)])

    def test_legacy_close_still_receives_close_rf_flag(self):
        calls = []

        flow = SafetyShutdownFlow(
            rf_off=lambda: None, close=lambda *, close_rf: calls.append(close_rf) or []
        )
        flow.run()

        self.assertEqual(calls, [False])

    def test_legacy_close_is_skipped_when_rf_close_failed(self):
        calls = []

        def close(*, close_rf):
            calls.append(close_rf)
            return []

        flow = SafetyShutdownFlow(
            rf_off=lambda: (_ for _ in ()).throw(RuntimeError("rf")), close=close
        )
        with self.assertRaises(SafetyShutdownError):
            flow.run()

        self.assertEqual(calls, [])

    def test_legacy_close_type_error_is_reported_as_is(self):
        def close(*, close_rf):
            raise TypeError("close_all rejected")

        flow = SafetyShutdownFlow(close=close)
        with self.assertRaises(SafetyShutdownError) as raised:
            flow.run()

        self.assertEqual([str(error) for error in raised.exception.errors], ["close_all rejected"])


class ShutdownInstrumentCallFormTests(unittest.TestCase):
    def test_flagged_close_type_error_is_not_retried(self):
        calls = []

        def close(*, rf_safe, power_safe):
            calls.append((rf_safe, power_safe))
            raise TypeError("device rejected")

        with self.assertRaises(InstrumentSafetyError) as raised:
            shutdown_instruments(rf_off=lambda: None, close=close)

        self.assertEqual(calls, [(True, True)])
        self.assertEqual([str(error) for error in raised.exception.errors], ["device rejected"])

    def test_close_without_flags_is_called_once(self):
        calls = []

        def close():
            calls.append("closed")
            return []

        shutdown_instruments(rf_off=lambda: None, close=close)

        self.assertEqual(calls, ["closed"])


class _CleanupController:
    """只实现安全清理所需能力，不构造任何 VISA 对象。"""

    def __init__(self, *, has_signal_generator=True, rf_error=None, close_errors=(), close_error=None):
        if has_signal_generator:
            self.signal_gen = object()
        self.rf_error = rf_error
        self.close_errors = list(close_errors)
        self.close_error = close_error
        self.events = []

    def rf_output_off(self):
        self.events.append("rf_off")
        if self.rf_error is not None:
            raise self.rf_error

    def close_all(self, *, close_rf):
        self.events.append(("close", close_rf))
        if self.close_error is not None:
            raise self.close_error
        return list(self.close_errors)


class SafetyCleanupAdapterTests(unittest.TestCase):
    def test_cleanup_reports_each_step_in_rf_power_resource_order(self):
        controller = _CleanupController()
        record = SafetyCleanupAdapter(controller).cleanup(
            power_cleanup=lambda: controller.events.append("power_off")
        )

        self.assertIsInstance(record, CleanupRecord)
        self.assertTrue(record.succeeded)
        self.assertEqual(record.rf_output, CleanupStepStatus.PERFORMED)
        self.assertEqual(record.power_supplies, CleanupStepStatus.PERFORMED)
        self.assertEqual(record.resources, CleanupStepStatus.PERFORMED)
        self.assertEqual(controller.events, ["rf_off", "power_off", ("close", False)])

    def test_power_failure_is_recorded_and_resource_release_continues(self):
        controller = _CleanupController()

        def power_off():
            controller.events.append("power_off")
            raise RuntimeError("power")

        with self.assertRaises(SafetyCleanupError) as raised:
            SafetyCleanupAdapter(controller).cleanup(power_cleanup=power_off)

        record = raised.exception.record
        self.assertFalse(record.succeeded)
        self.assertEqual(record.rf_output, CleanupStepStatus.PERFORMED)
        self.assertEqual(record.power_supplies, CleanupStepStatus.FAILED)
        self.assertEqual(record.resources, CleanupStepStatus.PERFORMED)
        self.assertEqual(record.errors, ("power",))
        self.assertEqual(controller.events, ["rf_off", "power_off", ("close", False)])

    def test_rf_failure_keeps_connection_open_and_marks_resource_skipped(self):
        controller = _CleanupController(rf_error=RuntimeError("rf"))

        with self.assertRaises(SafetyCleanupError) as raised:
            SafetyCleanupAdapter(controller).cleanup()

        record = raised.exception.record
        self.assertEqual(record.rf_output, CleanupStepStatus.FAILED)
        self.assertEqual(record.resources, CleanupStepStatus.SKIPPED)
        self.assertIn("rf", record.errors)
        self.assertEqual(controller.events, ["rf_off"])

    def test_connection_errors_are_reported_as_failed_resources(self):
        controller = _CleanupController(close_errors=[RuntimeError("close")])

        with self.assertRaises(SafetyCleanupError) as raised:
            SafetyCleanupAdapter(controller).cleanup()

        record = raised.exception.record
        self.assertEqual(record.resources, CleanupStepStatus.FAILED)
        self.assertEqual(record.errors, ("close",))

    def test_close_all_type_error_is_reported_verbatim_without_retry(self):
        controller = _CleanupController(close_error=TypeError("device write rejected"))

        with self.assertRaises(SafetyCleanupError) as raised:
            SafetyCleanupAdapter(controller).cleanup()

        record = raised.exception.record
        self.assertEqual(record.resources, CleanupStepStatus.FAILED)
        self.assertEqual(record.errors, ("device write rejected",))
        self.assertEqual(controller.events, ["rf_off", ("close", False)])

    def test_borrowed_resource_and_absent_devices_are_marked_skipped(self):
        controller = _CleanupController(has_signal_generator=False)

        record = SafetyCleanupAdapter(controller).cleanup(release_resources=False)

        self.assertTrue(record.succeeded)
        self.assertEqual(record.rf_output, CleanupStepStatus.SKIPPED)
        self.assertEqual(record.power_supplies, CleanupStepStatus.SKIPPED)
        self.assertEqual(record.resources, CleanupStepStatus.SKIPPED)
        self.assertEqual(controller.events, [])

    def test_record_document_is_plain_and_serializable(self):
        record = SafetyCleanupAdapter(_CleanupController()).cleanup()
        self.assertEqual(
            record.to_dict(),
            {
                "rf_output": "performed",
                "power_supplies": "skipped",
                "resources": "performed",
                "errors": [],
                "succeeded": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
