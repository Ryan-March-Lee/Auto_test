import unittest

from instrument.measurement_adapter import PortMeasurementAdapter
from application.measurements.services import DriverPowerMappingService
from instrument.flow.cleanup import SafetyCleanupAdapter
from instrument.simulation import (
    CommandRecorder,
    SafetyInstrumentSession,
    SimulatedPowerSupply,
    SimulatedSignalGenerator,
    SimulatedSpectrumAnalyzer,
)
from infrastructure.logging import app_logging

# 模块 logger 挂在专用 ``pa_auto_test`` 下：assertLogs 必须点名同一个 logger。
ADAPTER_LOGGER = app_logging.get_logger("instrument.measurement_adapter").name


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
            run_id="run-driver-mapping",
            safety_cleanup=SafetyCleanupAdapter(adapter),
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
                run_id="run-driver-mapping",
                safety_cleanup=SafetyCleanupAdapter(adapter),
                sleep_fn=lambda _: None,
                settle_delay_s=0,
            ).run()
        self.assertEqual(session.state.value, "cleaned")
        self.assertIn(("signal_generator", "rf_off", None), recorder.commands)

    def test_failed_power_change_does_not_reenable_rf(self):
        adapter, session, recorder = self.make_adapter()
        adapter.power_on_sequence()
        adapter.rf_output_on()
        adapter.signal_generator.inject_failure("set_power_dbm")

        with self.assertRaisesRegex(RuntimeError, "set_power_dbm"):
            adapter.set_power(-10)

        self.assertFalse(adapter.signal_generator.rf_enabled)
        self.assertEqual(
            recorder.commands.count(("signal_generator", "rf_on", None)),
            1,
        )

    def test_default_emergency_channels_follow_declared_roles(self):
        """未显式注入急停通道时，按声明角色先漏后栅关闭，不猜通道名。"""
        adapter, _session, recorder = self.make_adapter()

        self.assertEqual(
            adapter.emergency_channels,
            ((0, "D"), (0, "B"), (0, "C"), (0, "A")),
        )
        self.assertEqual(adapter.emergency_power_off_all(), [])
        self.assertEqual(
            [item[2] for item in recorder.commands if item[:2] == ("power_supply", "output_off")],
            ["D", "B", "C", "A"],
        )

    def test_emergency_log_names_the_short_label_and_keeps_the_routing_token(self):
        """急停日志写 ``短标签/CHx``；安全记录仍存路由 token，供程序再对一次。"""
        recorder = CommandRecorder()
        session = SafetyInstrumentSession(
            SimulatedSignalGenerator(recorder),
            SimulatedSpectrumAnalyzer(recorder=recorder),
            SimulatedPowerSupply(recorder),
            {"gate": "DP8B1519/CH1", "drain": "DP8B1519/CH2"},
        )
        session.validate()
        session.connect()
        session.prepare()
        adapter = PortMeasurementAdapter(
            session,
            dut_power_channels={"gate": "DP8B1519/CH1", "drain": "DP8B1519/CH2"},
            power_channel_labels={
                "DP8B1519/CH1": "现场 A 电源/CH1",
                "DP8B1519/CH2": "现场 A 电源/CH2",
            },
        )

        with self.assertLogs(ADAPTER_LOGGER, level="INFO") as captured:
            self.assertEqual(adapter.emergency_power_off_all(), [])

        self.assertIn("紧急关闭电源通道 现场 A 电源/CH2", captured.output[0])
        self.assertNotIn("DP8B1519/CH2", "\n".join(captured.output))
        self.assertEqual(
            adapter._last_emergency_power_off,
            [(0, "DP8B1519/CH2"), (0, "DP8B1519/CH1")],
        )

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

    def test_close_all_honors_explicit_close_rf_flag(self):
        adapter, session, recorder = self.make_adapter()
        adapter.power_on_sequence()
        adapter.rf_output_on()
        adapter.close_all(close_rf=True)
        self.assertEqual(session.state.value, "cleaned")
        rf_actions = [item for item in recorder.commands if item[:2] == ("signal_generator", "rf_off")]

        legacy_adapter, legacy_session, legacy_recorder = self.make_adapter()
        legacy_adapter.power_on_sequence()
        legacy_adapter.rf_output_on()
        legacy_adapter.close_all(close_rf=False)
        self.assertEqual(legacy_session.state.value, "cleaned")
        legacy_rf_actions = [
            item for item in legacy_recorder.commands if item[:2] == ("signal_generator", "rf_off")
        ]
        self.assertEqual(len(rf_actions), len(legacy_rf_actions) + 1)


if __name__ == "__main__":
    unittest.main()
