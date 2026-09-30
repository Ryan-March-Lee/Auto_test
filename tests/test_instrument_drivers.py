import unittest
import math

from instrument.drivers import (
    ScpiPowerSupplyDriver,
    ScpiSignalGeneratorDriver,
    SignalGeneratorDriver,
    ScpiSpectrumAnalyzerDriver,
    SpectrumAnalyzerDeviceError,
    SpectrumAnalyzerMeasurementError,
)
from instrument.transport import MockScpiTransport, ScpiTransportError, ScpiTransportTimeoutError
from instrument.ports import PowerSupplyPort, SignalGeneratorPort, SpectrumAnalyzerPort
from instrument.action import (
    ActionContext,
    PowerSupplyActions,
    SignalGeneratorActions,
    SpectrumAnalyzerActions,
    SpectrumAnalyzerPortAdapter,
)
from instrument.signal_generator_factory import create_signal_generator_driver


class InstrumentDriverTests(unittest.TestCase):
    def test_drivers_implement_the_declared_ports(self):
        self.assertIsInstance(ScpiSignalGeneratorDriver(MockScpiTransport()), SignalGeneratorPort)
        self.assertIsInstance(ScpiSpectrumAnalyzerDriver(MockScpiTransport()), SpectrumAnalyzerPort)
        self.assertIsInstance(ScpiPowerSupplyDriver(MockScpiTransport()), PowerSupplyPort)

    def test_signal_generator_formats_units_and_enforces_rf_state(self):
        transport = MockScpiTransport()
        driver = ScpiSignalGeneratorDriver(transport)
        driver.connect()
        driver.set_frequency_hz(1e9)
        driver.set_power_dbm(-10.5)
        driver.set_rf_enabled(True)
        self.assertEqual(transport.writes, ["FREQ 1e+09", "POW:LEV -10.5", "OUTP:STAT ON"])
        with self.assertRaises(RuntimeError):
            driver.set_power_dbm(-20)
        driver.set_rf_enabled(False)
        self.assertEqual(transport.writes[-1], "OUTP:STAT OFF")

    def test_signal_generator_rejects_invalid_frequency_before_write(self):
        transport = MockScpiTransport()
        driver = ScpiSignalGeneratorDriver(transport)
        driver.connect()
        with self.assertRaises(ValueError):
            driver.set_frequency_hz(0)
        self.assertEqual(transport.writes, [])

        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    driver.set_frequency_hz(value)
        self.assertEqual(transport.writes, [])
        with self.assertRaises(RuntimeError):
            driver.set_rf_enabled(True)
        self.assertEqual(transport.writes, [])

    def test_signal_generator_enforces_configured_ranges_before_transport(self):
        transport = MockScpiTransport()
        driver = SignalGeneratorDriver(transport, min_frequency_hz=1e6,
                                       max_frequency_hz=3e9, min_power_dbm=-40,
                                       max_power_dbm=5)
        driver.connect()
        for operation in (lambda: driver.set_frequency_hz(999999),
                          lambda: driver.set_frequency_hz(3e9 + 1),
                          lambda: driver.set_power_dbm(-41),
                          lambda: driver.set_power_dbm(6)):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(transport.operations, [])

    def test_signal_generator_rejects_invalid_ranges_at_construction(self):
        for limits in (
            {"min_frequency_hz": 0},
            {"min_frequency_hz": -1},
            {"max_frequency_hz": 0},
            {"max_frequency_hz": -1},
            {"min_frequency_hz": 2e9, "max_frequency_hz": 1e9},
            {"min_power_dbm": 1, "max_power_dbm": 0},
            {"max_power_dbm": math.inf},
        ):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                SignalGeneratorDriver(MockScpiTransport(), **limits)

    def test_signal_generator_factory_maps_explicit_model_limits(self):
        transport = MockScpiTransport()
        driver = create_signal_generator_driver(
            transport,
            {
                "min_frequency_hz": 1e6,
                "max_frequency_hz": 3e9,
                "min_power_dbm": -40,
                "max_power_dbm": 5,
            },
        )
        self.assertEqual(driver.min_frequency_hz, 1e6)
        self.assertEqual(driver.max_frequency_hz, 3e9)
        self.assertEqual(driver.min_power_dbm, -40)
        self.assertEqual(driver.max_power_dbm, 5)
        self.assertEqual(transport.operations, [])

    def test_signal_generator_public_name_and_legacy_alias(self):
        self.assertIs(SignalGeneratorDriver, ScpiSignalGeneratorDriver)
        self.assertEqual(SignalGeneratorDriver.__name__, "SignalGeneratorDriver")

    def test_signal_generator_action_delegates_without_scpi_knowledge(self):
        transport = MockScpiTransport()
        driver = SignalGeneratorDriver(transport)
        actions = SignalGeneratorActions(driver)
        actions.connect()
        actions.set_frequency_hz(2.4e9)
        actions.set_power_dbm(-20)
        actions.set_rf_enabled(False)
        self.assertEqual(transport.writes,
                         ["FREQ 2.4e+09", "POW:LEV -20", "OUTP:STAT OFF"])
        actions.close()
        self.assertTrue(transport.closed)

    def test_spectrum_analyzer_formats_commands_and_parses_peak(self):
        transport = MockScpiTransport({"CALC:MARK1:Y?": " -23.75\n"})
        driver = ScpiSpectrumAnalyzerDriver(transport)
        driver.connect()
        driver.set_center_frequency_hz(2.4e9)
        driver.configure_bandwidth_hz(1e6)
        self.assertEqual(driver.measure_peak_power_dbm(), -23.75)
        self.assertEqual(
            transport.writes,
            ["FREQ:CENT 2.4e+09", "FREQ:SPAN 1e+06", "CALC:MARK1:MAX"],
        )

    def test_spectrum_analyzer_supports_optional_bandwidth_configuration(self):
        transport = MockScpiTransport({"CALC:MARK1:Y?": "-12.5"})
        driver = ScpiSpectrumAnalyzerDriver(transport)
        driver.connect()
        driver.set_center_frequency_hz(2.4e9)
        driver.set_span_hz(1e6)
        driver.set_resolution_bandwidth_hz(10e3)
        driver.set_video_bandwidth_hz(30e3)
        self.assertEqual(driver.measure_peak_power_dbm(), -12.5)
        self.assertEqual(transport.writes, [
            "FREQ:CENT 2.4e+09", "FREQ:SPAN 1e+06", "BAND:RES 10000",
            "BAND:VID 30000", "CALC:MARK1:MAX",
        ])

    def test_spectrum_analyzer_action_delegates_without_scpi_knowledge(self):
        transport = MockScpiTransport({"CALC:MARK1:Y?": "-8"})
        actions = SpectrumAnalyzerActions(ScpiSpectrumAnalyzerDriver(transport))
        actions.connect()
        actions.set_center_frequency_hz(1e9)
        actions.set_span_hz(1000)
        self.assertEqual(actions.measure_peak_power_dbm(), -8.0)
        actions.close()
        self.assertTrue(transport.closed)

    def test_spectrum_analyzer_action_adapts_legacy_port_explicitly(self):
        transport = MockScpiTransport({"CALC:MARK1:Y?": "-6.5"})
        driver = ScpiSpectrumAnalyzerDriver(transport)
        actions = SpectrumAnalyzerActions(SpectrumAnalyzerPortAdapter(driver))
        actions.connect()
        actions.set_center_frequency_hz(1e9)
        actions.set_span_hz(2e6)
        self.assertEqual(actions.measure_peak_power_dbm(), -6.5)

    def test_spectrum_analyzer_rejects_bad_reading_and_unconfigured_measurement(self):
        transport = MockScpiTransport({"CALC:MARK1:Y?": "not-a-number"})
        driver = ScpiSpectrumAnalyzerDriver(transport)
        driver.connect()
        with self.assertRaises(RuntimeError):
            driver.measure_peak_power_dbm()
        driver.set_center_frequency_hz(1e9)
        driver.configure_bandwidth_hz(1000)
        with self.assertRaisesRegex(ValueError, "非数字"):
            driver.measure_peak_power_dbm()

    def test_spectrum_analyzer_rejects_empty_reading(self):
        transport = MockScpiTransport({"CALC:MARK1:Y?": "  \n"})
        driver = ScpiSpectrumAnalyzerDriver(transport)
        driver.connect()
        driver.set_center_frequency_hz(1e9)
        driver.set_span_hz(1000)
        with self.assertRaises(SpectrumAnalyzerMeasurementError):
            driver.measure_peak_power_dbm()

    def test_spectrum_analyzer_rejects_invalid_config_before_write(self):
        transport = MockScpiTransport()
        driver = ScpiSpectrumAnalyzerDriver(transport, max_span_hz=1e6)
        driver.connect()
        with self.assertRaises(ValueError):
            driver.set_span_hz(1e6 + 1)
        self.assertEqual(transport.writes, [])

    def test_spectrum_analyzer_normalizes_and_validates_limit_types(self):
        driver = ScpiSpectrumAnalyzerDriver(MockScpiTransport(), min_span_hz="1000")
        self.assertEqual(driver._range_limits["span_hz"], (1000.0, None))
        for value in (object(), [], math.nan, math.inf, 0):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ScpiSpectrumAnalyzerDriver(MockScpiTransport(), min_span_hz=value)

    def test_spectrum_analyzer_preserves_state_after_configuration_write_failure(self):
        transport = MockScpiTransport(fail_on_write="FREQ:SPAN 1000")
        driver = ScpiSpectrumAnalyzerDriver(transport)
        driver.connect()
        driver.set_center_frequency_hz(1e9)
        with self.assertRaises(ScpiTransportError):
            driver.set_span_hz(1000)
        with self.assertRaises(SpectrumAnalyzerMeasurementError):
            driver.measure_peak_power_dbm()

    def test_spectrum_analyzer_preserves_transport_timeout_errors(self):
        transport = MockScpiTransport(
            {"CALC:MARK1:Y?": "-10"}, timeout_on_query="CALC:MARK1:Y?"
        )
        driver = ScpiSpectrumAnalyzerDriver(transport)
        driver.connect()
        driver.set_center_frequency_hz(1e9)
        driver.set_span_hz(1000)
        with self.assertRaises(ScpiTransportTimeoutError):
            driver.measure_peak_power_dbm()

    def test_spectrum_analyzer_rejects_non_numeric_and_non_finite_readings(self):
        for response in ("nan", "inf", "-inf"):
            with self.subTest(response=response):
                transport = MockScpiTransport({"CALC:MARK1:Y?": response})
                driver = ScpiSpectrumAnalyzerDriver(transport)
                driver.connect()
                driver.set_center_frequency_hz(1e9)
                driver.set_span_hz(1000)
                with self.assertRaises(SpectrumAnalyzerMeasurementError):
                    driver.measure_peak_power_dbm()

    def test_spectrum_analyzer_converts_scpi_device_errors(self):
        transport = MockScpiTransport({"CALC:MARK1:Y?": '-200,"Execution error"'})
        driver = ScpiSpectrumAnalyzerDriver(transport)
        driver.connect()
        driver.set_center_frequency_hz(1e9)
        driver.set_span_hz(1000)
        with self.assertRaisesRegex(SpectrumAnalyzerDeviceError, "-200"):
            driver.measure_peak_power_dbm()

    def test_spectrum_analyzer_requires_center_frequency_and_bandwidth(self):
        transport = MockScpiTransport({"CALC:MARK1:Y?": "-10"})
        driver = ScpiSpectrumAnalyzerDriver(transport)
        driver.connect()
        driver.configure_bandwidth_hz(1000)
        with self.assertRaises(RuntimeError):
            driver.measure_power_dbm()
        driver.configure_center_frequency_hz(1e9)
        self.assertEqual(driver.measure_power_dbm(), -10.0)

    def test_driver_preserves_query_failures_from_transport(self):
        transport = MockScpiTransport()
        driver = ScpiSpectrumAnalyzerDriver(transport)
        driver.connect()
        driver.configure_center_frequency_hz(1e9)
        driver.configure_bandwidth_hz(1000)
        with self.assertRaisesRegex(ScpiTransportError, "未配置 SCPI 查询响应"):
            driver.measure_peak_power_dbm()

    def test_power_supply_formats_channel_commands_and_parses_readings(self):
        transport = MockScpiTransport({":MEASure:VOLTage? CH1": "12.50", ":MEASure:CURRent? CH1": "0.125"})
        driver = ScpiPowerSupplyDriver(transport)
        driver.connect()
        driver.set_voltage_v("CH1", 12.5)
        driver.set_current_limit_a("CH1", 0.2)
        driver.set_output_enabled("CH1", True)
        self.assertEqual(driver.read_voltage_v("CH1"), 12.5)
        self.assertEqual(driver.read_current_a("CH1"), 0.125)
        self.assertEqual(transport.writes, [":SOURce1:VOLTage 12.5", ":SOURce1:CURRent 0.2", ":OUTPut CH1,ON"])

    def test_power_supply_rejects_invalid_values_before_write(self):
        transport = MockScpiTransport()
        driver = ScpiPowerSupplyDriver(transport)
        driver.connect()
        with self.assertRaises(ValueError):
            driver.set_current_limit_a("CH1", -0.1)
        with self.assertRaises(ValueError):
            driver.set_voltage_v("", 1)
        self.assertEqual(transport.writes, [])

        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    driver.set_current_limit_a("CH1", value)
        self.assertEqual(transport.writes, [])

    def test_power_supply_does_not_change_voltage_while_output_is_on(self):
        transport = MockScpiTransport()
        driver = ScpiPowerSupplyDriver(transport)
        driver.connect()
        driver.set_output_enabled("CH1", True)
        with self.assertRaises(RuntimeError):
            driver.set_voltage("CH1", 12.0)
        self.assertEqual(transport.writes, [":OUTPut CH1,ON"])

    def test_power_supply_configures_protection_and_keeps_channels_independent(self):
        transport = MockScpiTransport({
            ":MEASure:VOLTage? CH2": "28.0",
            ":MEASure:CURRent? CH1": "0.250",
        })
        driver = ScpiPowerSupplyDriver(transport)
        driver.connect()
        driver.set_voltage_protection_state("CH1", True)
        driver.set_current_protection_state("CH1", False)
        driver.set_voltage_protection("CH1", 3.3)
        driver.set_current_protection("CH1", 0.25)
        driver.set_voltage_v("CH1", 2.8)
        driver.set_current_limit_a("CH2", 1.0)
        driver.set_output_enabled("CH1", False)
        driver.set_output_enabled("CH2", False)
        self.assertEqual(driver.read_voltage_v("CH2"), 28.0)
        self.assertEqual(driver.read_current_a("CH1"), 0.25)
        self.assertEqual(transport.writes, [
            ":SOURce1:VOLTage:PROTection:STATe ON",
            ":SOURce1:CURRent:PROTection:STATe OFF",
            ":SOURce1:VOLTage:PROTection 3.3",
            ":SOURce1:CURRent:PROTection 0.25",
            ":SOURce1:VOLTage 2.8",
            ":SOURce2:CURRent 1",
            ":OUTPut CH1,OFF",
            ":OUTPut CH2,OFF",
        ])

    def test_power_supply_rejects_invalid_channel_before_transport(self):
        transport = MockScpiTransport()
        driver = ScpiPowerSupplyDriver(transport)
        driver.connect()
        for operation in (
            lambda: driver.set_voltage_v("gate", 1),
            lambda: driver.set_current_limit_a("CH0", 1),
            lambda: driver.set_output_enabled("ch1", False),
            lambda: driver.set_output_enabled("CH 1", False),
            lambda: driver.set_output_enabled("CH001", False),
            lambda: driver.set_output_enabled("CHx", False),
            lambda: driver.read_voltage_v(""),
        ):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual(transport.writes, [])
        self.assertEqual(transport.queries, [])

    def test_power_supply_rejects_negative_readback(self):
        transport = MockScpiTransport({
            ":MEASure:VOLTage? CH1": "-0.1",
            ":MEASure:CURRent? CH2": "-0.01",
        })
        driver = ScpiPowerSupplyDriver(transport)
        driver.connect()
        with self.assertRaisesRegex(ValueError, "负的 voltage_v"):
            driver.read_voltage_v("CH1")
        with self.assertRaisesRegex(ValueError, "负的 current_a"):
            driver.read_current_a("CH2")

    def test_power_supply_repeated_output_off_is_safe(self):
        transport = MockScpiTransport()
        driver = ScpiPowerSupplyDriver(transport)
        driver.connect()
        driver.set_output_enabled("CH1", False)
        driver.set_output_enabled("CH1", False)
        self.assertEqual(transport.writes, [":OUTPut CH1,OFF", ":OUTPut CH1,OFF"])

    def test_power_supply_action_delegates_without_scpi_knowledge(self):
        transport = MockScpiTransport({":MEASure:VOLTage? CH1": "5.0"})
        actions = PowerSupplyActions(ScpiPowerSupplyDriver(transport), channel="CH1")
        actions.connect()
        actions.set_voltage_v(5)
        actions.set_voltage_protection_state(True)
        actions.set_current_protection(0.5)
        actions.set_output_enabled(False)
        self.assertEqual(actions.read_voltage_v(), 5.0)
        actions.close()
        actions.close()
        self.assertEqual(transport.close_count, 1)

    def test_action_logger_receives_context_and_failure_without_swallowing_it(self):
        transport = MockScpiTransport(fail_on_write="FREQ 1e+09")
        events = []
        context = ActionContext(instrument_id="sg-1", measurement_id="measurement-7")
        actions = SignalGeneratorActions(
            SignalGeneratorDriver(transport), context=context,
            logger=lambda name, ctx, values, error: events.append((name, ctx, values, error)),
        )
        actions.connect()
        with self.assertRaises(ScpiTransportError):
            actions.set_frequency_hz(1e9)
        self.assertEqual(events[0], ("connect", context, {"timeout_s": 10.0}, None))
        self.assertEqual(events[1][0:3], ("set_frequency_hz", context, {"timeout_s": 5.0, "frequency_hz": 1e9}))
        self.assertIsInstance(events[1][3], ScpiTransportError)

    def test_action_logger_failure_never_masks_driver_result(self):
        transport = MockScpiTransport()
        actions = SignalGeneratorActions(
            SignalGeneratorDriver(transport),
            logger=lambda *_args: (_ for _ in ()).throw(RuntimeError("logger failed")),
        )
        actions.connect()
        actions.set_frequency_hz(1e9)
        self.assertEqual(transport.writes, ["FREQ 1e+09"])

    def test_power_supply_action_binds_channel_and_context(self):
        transport = MockScpiTransport()
        context = ActionContext(instrument_id="psu-a")
        actions = PowerSupplyActions(ScpiPowerSupplyDriver(transport), channel="CH2", context=context)
        self.assertEqual(actions.context.channel_id, "CH2")
        actions.connect()
        actions.set_voltage_v(5.0)
        self.assertEqual(transport.writes, [":SOURce2:VOLTage 5"])
        with self.assertRaises(ValueError):
            PowerSupplyActions(ScpiPowerSupplyDriver(MockScpiTransport()), channel="CH1",
                               context=ActionContext(channel_id="CH2"))

    def test_spectrum_analyzer_basic_action_does_not_require_optional_bandwidth(self):
        class BasicAnalyzer:
            def connect(self, *, timeout_s=10.0): pass
            def set_center_frequency_hz(self, value, *, timeout_s=5.0): pass
            def set_span_hz(self, value, *, timeout_s=5.0): pass
            def measure_peak_power_dbm(self, *, timeout_s=10.0): return -1.0
            def close(self, *, timeout_s=5.0): pass

        actions = SpectrumAnalyzerActions(BasicAnalyzer())
        actions.connect()
        self.assertEqual(actions.measure_peak_power_dbm(), -1.0)
        with self.assertRaises(NotImplementedError):
            actions.set_resolution_bandwidth_hz(1000)

    def test_instrument_package_exports_all_action_entry_points(self):
        import instrument

        self.assertIs(instrument.SignalGeneratorActions, SignalGeneratorActions)
        self.assertIs(instrument.SpectrumAnalyzerActions, SpectrumAnalyzerActions)
        self.assertIs(instrument.PowerSupplyActions, PowerSupplyActions)

    def test_signal_generator_close_turns_rf_off_before_closing_transport(self):
        transport = MockScpiTransport()
        driver = ScpiSignalGeneratorDriver(transport)
        driver.connect()
        driver.set_frequency_hz(1e9)
        driver.set_rf_enabled(True)
        driver.close()
        self.assertEqual(transport.writes[-1], "OUTP:STAT OFF")
        self.assertTrue(transport.closed)
        self.assertFalse(driver.rf_enabled)

    def test_signal_generator_keeps_transport_open_when_rf_off_fails(self):
        transport = MockScpiTransport(fail_on_write="OUTP:STAT OFF")
        driver = ScpiSignalGeneratorDriver(transport)
        driver.connect()
        driver.set_frequency_hz(1e9)
        driver.set_rf_enabled(True)
        with self.assertRaises(RuntimeError):
            driver.close()
        self.assertFalse(transport.closed)
        self.assertTrue(driver.connected)
        self.assertTrue(driver.rf_enabled)
        transport.fail_on_write = None
        driver.close()
        self.assertTrue(transport.closed)

    def test_operation_timeout_is_applied_to_transport(self):
        transport = MockScpiTransport()
        driver = ScpiSignalGeneratorDriver(transport)
        driver.connect(timeout_s=2.5)
        self.assertEqual(transport.timeout_s, 2.5)
        driver.set_frequency_hz(1e9, timeout_s=1.25)
        self.assertEqual(transport.timeout_s, 1.25)

    def test_query_timeout_is_preserved_and_close_is_idempotent(self):
        transport = MockScpiTransport(timeout_on_query=":MEASure:VOLTage? CH1")
        driver = ScpiPowerSupplyDriver(transport)
        driver.connect()
        with self.assertRaises(ScpiTransportTimeoutError):
            driver.read_voltage_v("CH1")
        driver.close()
        driver.close()
        self.assertEqual(transport.close_count, 1)
        with self.assertRaises(RuntimeError):
            driver.set_output_enabled("CH1", False)


if __name__ == "__main__":
    unittest.main()
