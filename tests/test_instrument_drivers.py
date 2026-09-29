import unittest
import math

from instrument.drivers import (
    ScpiPowerSupplyDriver,
    ScpiSignalGeneratorDriver,
    SignalGeneratorDriver,
    ScpiSpectrumAnalyzerDriver,
)
from instrument.transport import MockScpiTransport, ScpiTransportError, ScpiTransportTimeoutError
from instrument.ports import PowerSupplyPort, SignalGeneratorPort, SpectrumAnalyzerPort
from instrument.action import SignalGeneratorActions
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
