import json
import tempfile
import unittest
from pathlib import Path

from config_io import load_config_file
from instrument.ports import InstrumentState
from instrument.simulation import (
    CommandRecorder,
    SafetyInstrumentSession,
    SimulatedPowerSupply,
    SimulatedSignalGenerator,
    SimulatedSpectrumAnalyzer,
)
from result_storage import save_measurement_result


class NormalSimulatedMeasurementFlowTests(unittest.TestCase):
    """第二层模拟设备集成测试：验证一条完整的正常测量流程。"""

    def test_configure_measure_and_save_result_with_safe_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "simulation_config.json"
            result_path = root / "measurement.json"
            config_path.write_text(
                json.dumps(
                    {
                        "frequency_hz": 1_000_000_000,
                        "bandwidth_hz": 1_000_000,
                        "signal_power_dbm": -10.0,
                        "power_channels": {"gate": "A", "drain": "B"},
                    }
                ),
                encoding="utf-8",
            )

            config = load_config_file(config_path)
            recorder = CommandRecorder()
            signal_generator = SimulatedSignalGenerator(recorder)
            spectrum_analyzer = SimulatedSpectrumAnalyzer(
                readings=(-30.0, -25.0), recorder=recorder
            )
            power_supply = SimulatedPowerSupply(recorder)
            session = SafetyInstrumentSession(
                signal_generator,
                spectrum_analyzer,
                power_supply,
                config["power_channels"],
            )

            # validate -> connect -> prepare: RF and power must remain off.
            session.validate()
            self.assertEqual(session.state, InstrumentState.VALIDATED)
            session.connect()
            self.assertEqual(session.state, InstrumentState.CONNECTED)
            session.prepare(
                frequency_hz=config["frequency_hz"],
                bandwidth_hz=config["bandwidth_hz"],
            )
            self.assertEqual(session.state, InstrumentState.PREPARED)
            self.assertFalse(signal_generator.rf_enabled)
            self.assertEqual(power_supply.outputs, {})

            # Configure output while RF is off, then power on in gate/drain order.
            signal_generator.set_power_dbm(config["signal_power_dbm"])
            power_supply.set_voltage("A", 2.8)
            power_supply.set_current_limit("A", 0.1)
            power_supply.set_voltage("B", 28.0)
            power_supply.set_current_limit("B", 1.0)
            session.power_on()
            self.assertEqual(session.state, InstrumentState.POWERED)
            self.assertEqual(power_supply.outputs, {"A": True, "B": True})

            session.start_measurement()
            self.assertEqual(session.state, InstrumentState.MEASURING)
            session.set_rf_enabled(True)
            self.assertTrue(signal_generator.rf_enabled)
            readings = [spectrum_analyzer.measure_power_dbm() for _ in range(2)]
            self.assertEqual(readings, [-30.0, -25.0])

            result = {
                "frequency_hz": config["frequency_hz"],
                "input_power_dbm": config["signal_power_dbm"],
                "readings_dbm": readings,
            }
            run_directory = root / "run"
            archive_path, legacy_path = save_measurement_result(
                result,
                result_type="simulation_measurement",
                legacy_path=result_path,
                run_id="simulation-normal-flow",
                run_directory=run_directory,
            )
            self.assertEqual(json.loads(archive_path.read_text(encoding="utf-8")),
                             json.loads(legacy_path.read_text(encoding="utf-8")))
            self.assertEqual(json.loads(archive_path.read_text(encoding="utf-8"))["readings_dbm"],
                             readings)

            session.set_rf_enabled(False)
            session.power_off()
            session.close()

            self.assertEqual(session.state, InstrumentState.CLEANED)
            self.assertFalse(signal_generator.rf_enabled)
            self.assertFalse(any(power_supply.outputs.values()))
            self.assertFalse(signal_generator.connected)
            self.assertFalse(spectrum_analyzer.connected)
            self.assertFalse(power_supply.connected)

            commands = recorder.commands
            rf_off = commands.index(("signal_generator", "rf_off", None))
            drain_off = commands.index(("power_supply", "output_off", "B"))
            gate_off = commands.index(("power_supply", "output_off", "A"))
            close_indexes = [
                index for index, (device, action, _value) in enumerate(commands)
                if action == "close"
            ]
            self.assertLess(rf_off, drain_off)
            self.assertLess(drain_off, gate_off)
            self.assertTrue(close_indexes)
            self.assertGreater(min(close_indexes), gate_off)

            actions = [(device, action) for device, action, _value in commands]
            gate_on = commands.index(("power_supply", "output_on", "A"))
            drain_on = commands.index(("power_supply", "output_on", "B"))
            rf_on = commands.index(("signal_generator", "rf_on", None))
            self.assertLess(gate_on, drain_on)
            self.assertLess(drain_on, rf_on)


if __name__ == "__main__":
    unittest.main()
