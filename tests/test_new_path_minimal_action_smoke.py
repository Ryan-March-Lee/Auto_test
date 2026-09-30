import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hardware.new_path_minimal_action_smoke import run_new_path_minimal_action
from hardware.read_only_smoke import SmokeExecutionError


class FakeTransport:
    def __init__(self, identity, output="0"):
        self.identity = identity
        self.output = output
        self.operations = []

    def set_timeout_s(self, timeout_s):
        self.operations.append(("timeout", timeout_s))

    def query(self, command):
        self.operations.append(("query", command))
        if command == "*IDN?":
            return self.identity
        if command == "OUTP?":
            return self.output
        if command in {"OUTP? CH1", "OUTP? CH2"}:
            return "0"
        if command == "CALC:MARK1:Y?":
            return "-50"
        raise KeyError(command)

    def write(self, command):
        self.operations.append(("write", command))
        if command == "OUTP:STAT ON":
            self.output = "1"
        elif command == "OUTP:STAT OFF":
            self.output = "0"


class FakeDriver:
    def __init__(self, identity, output="0"):
        self.transport = FakeTransport(identity, output)
        self.rf_enabled = False
        self.closed = False

    def set_frequency_hz(self, value, *, timeout_s):
        self.transport.write(f"FREQ {value:g}")

    def set_power_dbm(self, value, *, timeout_s):
        self.transport.write(f"POW:LEV {value:g}")

    def set_rf_enabled(self, enabled, *, timeout_s):
        self.transport.write(f"OUTP:STAT {'ON' if enabled else 'OFF'}")
        self.rf_enabled = enabled

    def configure_center_frequency_hz(self, value, *, timeout_s):
        self.transport.write(f"FREQ:CENT {value:g}")

    def configure_bandwidth_hz(self, value, *, timeout_s):
        self.transport.write(f"FREQ:SPAN {value:g}")

    def measure_power_dbm(self, *, timeout_s):
        self.transport.write("CALC:MARK1:MAX")
        return float(self.transport.query("CALC:MARK1:Y?"))


class FakePowerDriver:
    def __init__(self):
        self.transport = FakeTransport("RIGOL,DP832A,1,1")
        self.closed = False
        self.off_calls = []

    def set_output_enabled(self, channel, enabled, *, timeout_s):
        self.off_calls.append((channel, enabled))


class FakeSession:
    resources_closed = False


class FakePort:
    def __init__(self, *, rf_output="0", measurement_error=None, cleanup_errors=()):
        self.signal_generator = FakeDriver("Rohde&Schwarz,SMW200A,1,1", rf_output)
        self.spectrum_analyzer = FakeDriver("Ceyear,4082F,1,1")
        self.power_supply = type("Power", (), {"supplies": [FakePowerDriver()]})()
        self.session = FakeSession()
        self.measurement_error = measurement_error
        self.cleanup_errors = list(cleanup_errors)

    def set_frequency(self, value, *, timeout_s):
        self.signal_generator.set_frequency_hz(value * 1e9, timeout_s=timeout_s)
        self.spectrum_analyzer.configure_center_frequency_hz(value * 1e9, timeout_s=timeout_s)

    def set_span(self, value, *, timeout_s):
        self.spectrum_analyzer.configure_bandwidth_hz(value * 1e6, timeout_s=timeout_s)

    def set_power(self, value, *, timeout_s):
        self.signal_generator.set_power_dbm(value, timeout_s=timeout_s)

    def start_measurement(self):
        pass

    def rf_output_on(self, *, timeout_s):
        self.signal_generator.set_rf_enabled(True, timeout_s=timeout_s)

    def rf_output_off(self, *, timeout_s):
        self.signal_generator.set_rf_enabled(False, timeout_s=timeout_s)

    def measure_power_with_average(self, *, timeout_s):
        if self.measurement_error:
            raise self.measurement_error
        return self.spectrum_analyzer.measure_power_dbm(timeout_s=timeout_s)

    def emergency_power_off_all(self, *, timeout_s):
        for supply in self.power_supply.supplies:
            for channel in ("CH2", "CH1"):
                supply.set_output_enabled(channel, False, timeout_s=timeout_s)
        return []

    def close_all(self, *, close_rf, timeout_s):
        self.signal_generator.closed = True
        self.spectrum_analyzer.closed = True
        for supply in self.power_supply.supplies:
            supply.closed = True
        self.session.resources_closed = not self.cleanup_errors
        return self.cleanup_errors


def smoke_config():
    return {
        "smoke_mode": "minimal_action", "max_action_count": 1,
        "max_duration_s": 30, "action_timeout_s": 1, "cleanup_timeout_s": 2,
        "devices": {
            "signal_generator": {
                "address": "SG", "model": "SMW200A",
                "rf_parameters": {"frequency_hz": 2300000000, "power_dbm": -30},
                "state_queries": [{"command": "OUTP?", "expected": "0"}],
            },
            "spectrum_analyzer": {
                "address": "SA", "model": "4082F",
                "setup_commands": ["FREQ:SPAN 10000000"],
                "measurement_queries": [{"command": "CALC:MARK1:Y?", "min_dbm": -60, "max_dbm": 0}],
            },
            "power_supply": {
                "model": "DP832A", "state_queries": [
                    {"command": "OUTP? CH2", "expected": "0"},
                    {"command": "OUTP? CH1", "expected": "0"},
                ],
            },
        },
    }


class NewPathMinimalActionTests(unittest.TestCase):
    def app_config(self, root):
        path = root / "config.json"
        path.write_text(json.dumps({"instruments": {
            "signal_generator": {"address": "SG"},
            "spectrum_analyzer": {"address": "SA"},
        }}), encoding="utf-8")
        return path

    def run_smoke(self, port, config=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("hardware.new_path_minimal_action_smoke.connect_instruments", return_value=port):
                return run_new_path_minimal_action(config or smoke_config(), self.app_config(root))

    def test_success_records_one_action_and_real_cleanup(self):
        port = FakePort()
        report = self.run_smoke(port)
        self.assertEqual(report["action_count"], 1)
        self.assertTrue(report["resources_closed"])
        self.assertTrue(report["devices"]["signal_generator"]["safe_after_cleanup"])
        self.assertEqual(port.power_supply.supplies[0].off_calls, [("CH2", False), ("CH1", False)])
        self.assertLess(
            port.spectrum_analyzer.transport.operations.index(("write", "CALC:MARK1:MAX")),
            port.spectrum_analyzer.transport.operations.index(("query", "CALC:MARK1:Y?")),
        )

    def test_initial_rf_on_is_rejected_before_action(self):
        with self.assertRaises(SmokeExecutionError):
            self.run_smoke(FakePort(rf_output="1"))

    def test_measurement_failure_still_turns_rf_off_and_reports_failure(self):
        port = FakePort(measurement_error=RuntimeError("measurement failed"))
        with self.assertRaises(SmokeExecutionError):
            self.run_smoke(port)
        self.assertIn(("write", "OUTP:STAT OFF"), port.signal_generator.transport.operations)

    def test_cleanup_errors_are_not_lost(self):
        port = FakePort(cleanup_errors=[RuntimeError("close failed")])
        with self.assertRaises(SmokeExecutionError) as caught:
            self.run_smoke(port)
        self.assertIn("close failed", " ".join(caught.exception.report["cleanup_errors"]))

    def test_application_and_smoke_addresses_must_match(self):
        config = smoke_config()
        config["devices"]["signal_generator"]["address"] = "OTHER"
        with self.assertRaises(ValueError):
            self.run_smoke(FakePort(), config)


if __name__ == "__main__":
    unittest.main()
