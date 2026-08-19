import json
import tempfile
import unittest
from pathlib import Path

from instrument_control import InstrumentControl
from tests.fakes import FakeResourceManager, FakeVisaResource


def fake_power_config():
    return {
        "instruments": {
            "signal_generator": {"address": "FAKE::SG", "enabled": False},
            "spectrum_analyzer": {"address": "FAKE::SA", "enabled": False},
            "power_supplies": {
                "PS": {
                    "address": "FAKE::PS",
                    "enabled": True,
                    "channels": {
                        "CH1": {
                            "voltage": {"value": 2.8, "protection": 3.0, "protection_enabled": True},
                            "current": {"value": 0.1, "protection": 0.2, "protection_enabled": True},
                        },
                        "CH2": {
                            "voltage": {"value": 28.0, "protection": 30.0, "protection_enabled": False},
                            "current": {"value": 1.0, "protection": 1.2, "protection_enabled": False},
                        },
                    },
                }
            },
        },
        "driver_mode": {"enabled": False},
        "power_supply_assignment": {
            "driver_amplifier": {"supplies": {}},
            "dut_amplifier": {"supplies": {"dut": {"name": "PS", "channel": ["CH1", "CH2"]}}},
        },
    }


class InstrumentPowerControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temporary_directory.name) / "config.json"
        self.config_path.write_text(json.dumps(fake_power_config()), encoding="utf-8")
        self.power_supply = FakeVisaResource(
            "FAKE::PS",
            {
                ":MEASure:VOLTage? CH1": "2.800",
                ":MEASure:CURRent? CH1": "0.010",
            },
        )
        self.manager = FakeResourceManager({"FAKE::PS": self.power_supply})
        self.delays = []
        self.controller = InstrumentControl(
            self.config_path,
            resource_manager=self.manager,
            sleep_fn=self.delays.append,
        )
        self.assertEqual(self.manager.opened_addresses, ["FAKE::PS"])

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_initialization_sets_protection_state_before_protection_value(self):
        self.assertEqual(
            self.power_supply.writes,
            [
                "*RST",
                "*CLS",
                ":SOURce1:VOLTage:PROTection:STATe ON",
                ":SOURce1:CURRent:PROTection:STATe ON",
                ":SOURce1:VOLTage:PROTection 3.0",
                ":SOURce1:CURRent:PROTection 0.2",
                ":SOURce2:VOLTage:PROTection:STATe OFF",
                ":SOURce2:CURRent:PROTection:STATe OFF",
            ],
        )

    def test_set_voltage_and_current_emit_existing_scpi_commands(self):
        self.controller.set_voltage("PS", "CH1", 2.8)
        self.controller.set_current("PS", "CH2", 1.5)
        self.assertEqual(
            self.power_supply.writes[-2:],
            [":SOURce1:VOLTage 2.8", ":SOURce2:CURRent 1.5"],
        )

    def test_protection_methods_emit_existing_scpi_commands(self):
        self.controller.set_voltage_protection_state("PS", "CH1", False)
        self.controller.set_current_protection_state("PS", "CH2", True)
        self.controller.set_voltage_protection("PS", "CH1", 3.1)
        self.controller.set_current_protection("PS", "CH2", 1.3)
        self.assertEqual(
            self.power_supply.writes[-4:],
            [
                ":SOURce1:VOLTage:PROTection:STATe OFF",
                ":SOURce2:CURRent:PROTection:STATe ON",
                ":SOURce1:VOLTage:PROTection 3.1",
                ":SOURce2:CURRent:PROTection 1.3",
            ],
        )

    def test_read_voltage_and_current_use_configured_query_responses(self):
        self.assertEqual(self.controller.read_voltage("PS", "CH1"), 2.8)
        self.assertEqual(self.controller.read_current("PS", "CH1"), 0.01)
        self.assertEqual(
            self.power_supply.queries,
            [":MEASure:VOLTage? CH1", ":MEASure:CURRent? CH1"],
        )

    def test_non_numeric_measurement_response_is_not_silently_accepted(self):
        self.power_supply.responses[":MEASure:VOLTage? CH1"] = "not-a-number"
        with self.assertRaises(ValueError):
            self.controller.read_voltage("PS", "CH1")

    def test_channel_output_commands_use_original_channel_names(self):
        self.controller.power_supply_on("PS", "CH1")
        self.controller.power_supply_off("PS", "CH2")
        self.assertEqual(
            self.power_supply.writes[-2:],
            [":OUTPut CH1,ON", ":OUTPut CH2,OFF"],
        )

    def test_unknown_power_supply_is_rejected_before_writing(self):
        writes_before = list(self.power_supply.writes)
        with self.assertRaisesRegex(Exception, "PS_UNKNOWN is not connected"):
            self.controller.set_voltage("PS_UNKNOWN", "CH1", 1.0)
        self.assertEqual(self.power_supply.writes, writes_before)

    def test_write_and_query_failures_propagate(self):
        self.power_supply.fail_on_write = ":SOURce1:VOLTage 2.8"
        with self.assertRaisesRegex(RuntimeError, "仿真写入失败"):
            self.controller.set_voltage("PS", "CH1", 2.8)

        self.power_supply.fail_on_query = ":MEASure:VOLTage? CH1"
        with self.assertRaisesRegex(RuntimeError, "仿真查询失败"):
            self.controller.read_voltage("PS", "CH1")

    def test_setup_dut_power_uses_existing_assignment_and_no_real_delay(self):
        self.controller.setup_dut_power()
        self.assertEqual(
            self.power_supply.writes[-4:],
            [
                ":SOURce1:VOLTage 2.8",
                ":SOURce1:CURRent 0.1",
                ":SOURce2:VOLTage 28.0",
                ":SOURce2:CURRent 1.0",
            ],
        )
        self.assertEqual(self.delays, [])

    def test_power_on_sequence_records_current_gate_then_drain_order(self):
        self.power_supply.operations.clear()
        self.delays.clear()

        self.controller.power_on_sequence()

        output_operations = [
            operation
            for operation in self.power_supply.operations
            if operation[0] == "write" and operation[1].startswith(":OUTPut")
        ]
        self.assertEqual(
            output_operations,
            [
                ("write", ":OUTPut CH1,ON"),
                ("write", ":OUTPut CH2,ON"),
            ],
        )
        self.assertEqual(self.delays, [0.5, 1.5, 0.5])

    def test_power_off_sequence_records_current_drain_then_gate_order(self):
        self.power_supply.operations.clear()
        self.delays.clear()

        self.controller.power_off_sequence()

        output_operations = [
            operation
            for operation in self.power_supply.operations
            if operation[0] == "write" and operation[1].startswith(":OUTPut")
        ]
        self.assertEqual(
            output_operations,
            [
                ("write", ":OUTPut CH2,OFF"),
                ("write", ":OUTPut CH1,OFF"),
            ],
        )
        self.assertEqual(self.delays, [0.5, 2, 0.5])

    def test_power_off_sequence_continues_after_drain_channel_failure(self):
        self.power_supply.operations.clear()
        self.power_supply.fail_on_write = ":OUTPut CH2,OFF"

        with self.assertRaisesRegex(RuntimeError, "PS-CH2"):
            self.controller.power_off_sequence()

        output_operations = [
            operation
            for operation in self.power_supply.operations
            if operation[0] == "write" and operation[1].startswith(":OUTPut")
        ]
        self.assertEqual(
            output_operations,
            [
                ("write", ":OUTPut CH2,OFF"),
                ("write", ":OUTPut CH1,OFF"),
            ],
        )

    def test_safe_shutdown_turns_off_channels_after_power_on_failure(self):
        self.power_supply.operations.clear()
        self.power_supply.fail_on_write = ":OUTPut CH2,ON"
        with self.assertRaisesRegex(RuntimeError, "仿真写入失败"):
            self.controller.power_on_sequence()

        self.power_supply.fail_on_write = None
        self.controller.safe_shutdown()

        output_operations = [
            operation
            for operation in self.power_supply.operations
            if operation[0] == "write" and operation[1].startswith(":OUTPut")
        ]
        self.assertEqual(
            output_operations,
            [
                ("write", ":OUTPut CH1,ON"),
                ("write", ":OUTPut CH2,ON"),
                ("write", ":OUTPut CH2,OFF"),
                ("write", ":OUTPut CH1,OFF"),
            ],
        )
        self.assertTrue(self.power_supply.closed)
        self.assertTrue(self.manager.closed)

    def test_safe_shutdown_succeeds_without_a_signal_generator(self):
        self.controller.safe_shutdown()
        self.assertTrue(self.power_supply.closed)
        self.assertTrue(self.manager.closed)

    def test_safe_shutdown_does_not_send_duplicate_rf_off_commands(self):
        signal_generator = FakeVisaResource("FAKE::SG")
        manager = FakeResourceManager({"FAKE::PS": self.power_supply, "FAKE::SG": signal_generator})
        self.controller.signal_gen = signal_generator
        self.controller.rm = manager

        self.controller.safe_shutdown()

        self.assertEqual(signal_generator.writes, ["OUTP:STAT OFF", "POW -50dBm"])

    def test_safe_shutdown_closes_remaining_resources_when_close_fails(self):
        self.power_supply.fail_on_close = True
        self.manager.fail_on_close = True

        with self.assertRaisesRegex(RuntimeError, "安全关闭存在失败"):
            self.controller.safe_shutdown()

        self.assertEqual(self.power_supply.close_count, 1)
        self.assertEqual(self.manager.close_count, 1)

    def test_safe_shutdown_continues_power_cleanup_when_rf_off_fails(self):
        signal_generator = FakeVisaResource("FAKE::SG")
        signal_generator.fail_on_write = "OUTP:STAT OFF"
        manager = FakeResourceManager({"FAKE::PS": self.power_supply, "FAKE::SG": signal_generator})
        self.controller.signal_gen = signal_generator
        self.controller.rm = manager

        with self.assertRaisesRegex(RuntimeError, "安全关闭存在失败"):
            self.controller.safe_shutdown()

        output_operations = [
            operation
            for operation in self.power_supply.operations
            if operation[0] == "write" and operation[1].startswith(":OUTPut")
        ]
        self.assertEqual(
            output_operations,
            [("write", ":OUTPut CH2,OFF"), ("write", ":OUTPut CH1,OFF")],
        )
        self.assertTrue(self.power_supply.closed)
        self.assertTrue(manager.closed)

    def test_power_off_sequence_continues_after_gate_channel_failure(self):
        self.power_supply.operations.clear()
        self.power_supply.fail_on_write = ":OUTPut CH1,OFF"

        with self.assertRaisesRegex(RuntimeError, "PS-CH1"):
            self.controller.power_off_sequence()

        output_operations = [
            operation
            for operation in self.power_supply.operations
            if operation[0] == "write" and operation[1].startswith(":OUTPut")
        ]
        self.assertEqual(
            output_operations,
            [("write", ":OUTPut CH2,OFF"), ("write", ":OUTPut CH1,OFF")],
        )

    def test_repeated_safe_shutdown_does_not_raise_secondary_cleanup_error(self):
        self.controller.safe_shutdown()
        self.controller.safe_shutdown()
        self.assertTrue(self.power_supply.closed)
        self.assertTrue(self.manager.closed)


if __name__ == "__main__":
    unittest.main()
