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


def fake_multi_power_config():
    """多电源配置：PS1 和 PS2 均启用，各含 CH1/CH2，分配给 DUT。"""
    return {
        "instruments": {
            "signal_generator": {"address": "FAKE::SG", "enabled": False},
            "spectrum_analyzer": {"address": "FAKE::SA", "enabled": False},
            "power_supplies": {
                "PS1": {
                    "address": "FAKE::PS1",
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
                },
                "PS2": {
                    "address": "FAKE::PS2",
                    "enabled": True,
                    "channels": {
                        "CH1": {
                            "voltage": {"value": 5.0, "protection": 6.0, "protection_enabled": True},
                            "current": {"value": 0.2, "protection": 0.3, "protection_enabled": True},
                        },
                        "CH2": {
                            "voltage": {"value": 30.0, "protection": 32.0, "protection_enabled": False},
                            "current": {"value": 2.0, "protection": 2.5, "protection_enabled": False},
                        },
                    },
                },
            },
        },
        "driver_mode": {"enabled": False},
        "power_supply_assignment": {
            "driver_amplifier": {"supplies": {}},
            "dut_amplifier": {
                "supplies": {
                    "carrier": {"name": "PS1", "channel": ["CH1", "CH2"]},
                    "peaking": {"name": "PS2", "channel": ["CH1", "CH2"]},
                }
            },
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

    def test_initialization_preserves_resource_open_order(self):
        """重构后初始化仍按 SG→SA→PS 顺序打开资源"""
        enabled_config = fake_power_config()
        enabled_config["instruments"]["signal_generator"]["enabled"] = True
        enabled_config["instruments"]["spectrum_analyzer"]["enabled"] = True
        enabled_config["instruments"]["signal_generator"]["address"] = "FAKE::SG"
        enabled_config["instruments"]["spectrum_analyzer"]["address"] = "FAKE::SA"
        config_path = Path(self.temporary_directory.name) / "enabled_config.json"
        config_path.write_text(json.dumps(enabled_config), encoding="utf-8")
        signal_generator = FakeVisaResource("FAKE::SG")
        spectrum_analyzer = FakeVisaResource("FAKE::SA")
        manager = FakeResourceManager({
            "FAKE::SG": signal_generator,
            "FAKE::SA": spectrum_analyzer,
            "FAKE::PS": self.power_supply,
        })

        controller = InstrumentControl(
            config_path,
            resource_manager=manager,
            sleep_fn=lambda _: None,
        )

        self.assertEqual(
            manager.opened_addresses,
            ["FAKE::SG", "FAKE::SA", "FAKE::PS"],
        )
        self.assertIs(controller.signal_gen, signal_generator)
        self.assertIs(controller.spectrum, spectrum_analyzer)
        self.assertIn("PS", controller.power_supplies)

    def test_initialization_failure_during_power_supply_still_closes_all(self):
        """电源初始化阶段失败后，已打开资源仍被关闭"""
        bad_config = fake_power_config()
        bad_config["instruments"]["power_supplies"]["PS"]["channels"]["CH1"]["voltage"] = {}
        config_path = Path(self.temporary_directory.name) / "bad_config.json"
        config_path.write_text(json.dumps(bad_config), encoding="utf-8")
        ps_resource = FakeVisaResource("FAKE::PS")
        manager = FakeResourceManager({"FAKE::PS": ps_resource})

        with self.assertRaisesRegex(Exception, "Failed to initialize instruments"):
            InstrumentControl(
                config_path,
                resource_manager=manager,
                sleep_fn=lambda _: None,
            )

        self.assertTrue(ps_resource.closed)
        self.assertTrue(manager.closed)


class MultiSupplyPowerOnTests(unittest.TestCase):
    """多电源多通道上电顺序测试"""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temporary_directory.name) / "config.json"
        self.config_path.write_text(json.dumps(fake_multi_power_config()), encoding="utf-8")
        self.ps1 = FakeVisaResource("FAKE::PS1")
        self.ps2 = FakeVisaResource("FAKE::PS2")
        self.manager = FakeResourceManager({"FAKE::PS1": self.ps1, "FAKE::PS2": self.ps2})
        self.delays = []
        self.controller = InstrumentControl(
            self.config_path,
            resource_manager=self.manager,
            sleep_fn=self.delays.append,
        )
        self.power_on_events = []
        original_power_supply_on = self.controller.power_supply_on

        def record_power_on(ps_name, channel):
            self.power_on_events.append((ps_name, channel))
            return original_power_supply_on(ps_name, channel)

        self.controller.power_supply_on = record_power_on

    def tearDown(self):
        self.temporary_directory.cleanup()

    def _output_writes(self, resource):
        return [w for w in resource.writes if w.startswith(":OUTPut")]

    def test_power_on_all_ch1_before_any_ch2(self):
        """所有 CH1,ON 必须先于所有 CH2,ON"""
        self.ps1.writes.clear()
        self.ps2.writes.clear()
        self.delays.clear()
        self.power_on_events.clear()

        self.controller.power_on_sequence()

        self.assertEqual(
            self.power_on_events,
            [
                ("PS1", "CH1"),
                ("PS2", "CH1"),
                ("PS1", "CH2"),
                ("PS2", "CH2"),
            ],
        )

        output_writes = self._output_writes(self.ps1) + self._output_writes(self.ps2)
        self.assertEqual(output_writes.count(":OUTPut CH1,ON"), 2)
        self.assertEqual(output_writes.count(":OUTPut CH2,ON"), 2)

        first_ch2_idx = next(i for i, (_, channel) in enumerate(self.power_on_events) if channel == "CH2")
        last_ch1_idx = max(i for i, (_, channel) in enumerate(self.power_on_events) if channel == "CH1")
        self.assertLess(last_ch1_idx, first_ch2_idx)

    def test_power_on_delays_match_baseline(self):
        """多电源上电延时仍为每通道 0.5 秒 + 阶段间 1.5 秒"""
        self.delays.clear()
        self.controller.power_on_sequence()
        self.assertEqual(self.delays, [0.5, 0.5, 1.5, 0.5, 0.5])

    def test_power_on_gate_phase_failure_propagates(self):
        """CH1 阶段写入失败时异常直接抛出，不进入 CH2 阶段"""
        self.ps1.fail_on_write = ":OUTPut CH1,ON"
        self.ps1.writes.clear()
        self.ps2.writes.clear()

        with self.assertRaisesRegex(RuntimeError, "仿真写入失败"):
            self.controller.power_on_sequence()

        ch2_writes = [w for w in self.ps1.writes + self.ps2.writes if "CH2,ON" in w]
        self.assertEqual(ch2_writes, [])

    def test_power_on_drain_phase_failure_propagates(self):
        """CH2 阶段写入失败时异常直接抛出"""
        self.ps1.fail_on_write = ":OUTPut CH2,ON"
        self.ps1.writes.clear()
        self.ps2.writes.clear()

        with self.assertRaisesRegex(RuntimeError, "仿真写入失败"):
            self.controller.power_on_sequence()

        ch1_writes = [w for w in self.ps1.writes + self.ps2.writes if "CH1,ON" in w]
        self.assertEqual(len(ch1_writes), 2)


class MultiSupplyPowerOffTests(unittest.TestCase):
    """多电源多通道掉电顺序和错误收集测试"""

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temporary_directory.name) / "config.json"
        self.config_path.write_text(json.dumps(fake_multi_power_config()), encoding="utf-8")
        self.ps1 = FakeVisaResource("FAKE::PS1")
        self.ps2 = FakeVisaResource("FAKE::PS2")
        self.manager = FakeResourceManager({"FAKE::PS1": self.ps1, "FAKE::PS2": self.ps2})
        self.delays = []
        self.controller = InstrumentControl(
            self.config_path,
            resource_manager=self.manager,
            sleep_fn=self.delays.append,
        )
        self.power_off_events = []
        original_power_supply_off = self.controller.power_supply_off

        def record_power_off(ps_name, channel):
            self.power_off_events.append((ps_name, channel))
            return original_power_supply_off(ps_name, channel)

        self.controller.power_supply_off = record_power_off

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_power_off_all_ch2_before_any_ch1(self):
        """所有 CH2,OFF 必须先于所有 CH1,OFF，且电源顺序反向"""
        self.ps1.writes.clear()
        self.ps2.writes.clear()
        self.delays.clear()
        self.power_off_events.clear()

        self.controller.power_off_sequence()

        self.assertEqual(
            self.power_off_events,
            [
                ("PS2", "CH2"),
                ("PS1", "CH2"),
                ("PS2", "CH1"),
                ("PS1", "CH1"),
            ],
        )

        first_ch1_idx = next(i for i, (_, ch) in enumerate(self.power_off_events) if ch == "CH1")
        last_ch2_idx = max(i for i, (_, ch) in enumerate(self.power_off_events) if ch == "CH2")
        self.assertLess(last_ch2_idx, first_ch1_idx)

    def test_power_off_delays_match_baseline(self):
        """多电源掉电延时仍为每通道 0.5 秒 + 阶段间 2 秒"""
        self.delays.clear()
        self.controller.power_off_sequence()
        self.assertEqual(self.delays, [0.5, 0.5, 2, 0.5, 0.5])

    def test_power_off_drain_failure_continues_to_gate(self):
        """CH2 阶段写入失败后仍继续关闭 CH1"""
        self.ps1.fail_on_write = ":OUTPut CH2,OFF"
        self.ps1.writes.clear()
        self.ps2.writes.clear()
        self.power_off_events.clear()

        with self.assertRaisesRegex(RuntimeError, "电源掉电序列存在失败"):
            self.controller.power_off_sequence()

        self.assertEqual(
            self.power_off_events,
            [
                ("PS2", "CH2"),
                ("PS1", "CH2"),
                ("PS2", "CH1"),
                ("PS1", "CH1"),
            ],
        )
        self.assertEqual(self.delays, [0.5, 0.5, 2, 0.5, 0.5])
        self.assertIn(":OUTPut CH2,OFF", self.ps2.writes)
        self.assertIn(":OUTPut CH2,OFF", self.ps1.writes)
        self.assertIn(":OUTPut CH1,OFF", self.ps1.writes)
        self.assertIn(":OUTPut CH1,OFF", self.ps2.writes)

    def test_power_off_gate_failure_still_reports_all_errors(self):
        """CH1 阶段写入失败后仍汇总错误"""
        self.ps1.fail_on_write = ":OUTPut CH1,OFF"
        self.ps1.writes.clear()
        self.ps2.writes.clear()
        self.power_off_events.clear()

        with self.assertRaisesRegex(RuntimeError, "PS1-CH1"):
            self.controller.power_off_sequence()

        ch2_events = [e for e in self.power_off_events if e[1] == "CH2"]
        self.assertEqual(len(ch2_events), 2)

    def test_power_off_both_phases_failure_reports_all(self):
        """CH2 和 CH1 阶段同时失败时错误仍汇总"""
        self.ps1.fail_on_write = lambda cmd: "OFF" in cmd
        self.ps1.writes.clear()
        self.ps2.writes.clear()
        self.power_off_events.clear()

        with self.assertRaisesRegex(RuntimeError, "电源掉电序列存在失败"):
            self.controller.power_off_sequence()

        all_off_events = self.power_off_events
        self.assertEqual(len(all_off_events), 4)


class SafeShutdownCompatTests(unittest.TestCase):
    """safe_shutdown 和构造函数兼容性测试"""

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

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_safe_shutdown_order_rf_then_power_then_close(self):
        """safe_shutdown 仍按 RF关闭→掉电→连接关闭顺序执行"""
        signal_generator = FakeVisaResource("FAKE::SG")
        manager = FakeResourceManager({
            "FAKE::PS": self.power_supply,
            "FAKE::SG": signal_generator,
        })
        self.controller.signal_gen = signal_generator
        self.controller.rm = manager
        self.power_supply.operations.clear()
        signal_generator.operations.clear()
        events = []

        original_rf_output_off = self.controller.rf_output_off
        original_power_supply_off = self.controller.power_supply_off
        original_signal_close = signal_generator.close
        original_power_close = self.power_supply.close
        original_manager_close = manager.close

        def record_rf_output_off():
            events.append(("rf_off", None))
            return original_rf_output_off()

        def record_power_supply_off(ps_name, channel):
            events.append(("power_off", (ps_name, channel)))
            return original_power_supply_off(ps_name, channel)

        def record_signal_close():
            events.append(("close", "FAKE::SG"))
            return original_signal_close()

        def record_power_close():
            events.append(("close", "FAKE::PS"))
            return original_power_close()

        def record_manager_close():
            events.append(("close", "resource_manager"))
            return original_manager_close()

        self.controller.rf_output_off = record_rf_output_off
        self.controller.power_supply_off = record_power_supply_off
        signal_generator.close = record_signal_close
        self.power_supply.close = record_power_close
        manager.close = record_manager_close

        self.controller.safe_shutdown()

        rf_indexes = [i for i, (kind, _) in enumerate(events) if kind == "rf_off"]
        power_indexes = [i for i, (kind, _) in enumerate(events) if kind == "power_off"]
        close_indexes = [i for i, (kind, _) in enumerate(events) if kind == "close"]
        self.assertTrue(rf_indexes, "信号源应先执行 RF 关闭")
        self.assertTrue(power_indexes, "电源应执行掉电")
        self.assertTrue(close_indexes, "资源应执行关闭")
        self.assertLess(max(rf_indexes), min(power_indexes))
        self.assertLess(max(power_indexes), min(close_indexes))
        self.assertTrue(signal_generator.closed, "信号源应被关闭")
        self.assertTrue(self.power_supply.closed, "电源应被关闭")
        self.assertTrue(manager.closed, "ResourceManager 应被关闭")

    def test_safe_shutdown_power_off_failure_still_closes_resources(self):
        """掉电失败后仍继续关闭 VISA 资源"""
        self.power_supply.fail_on_write = ":OUTPut CH2,OFF"
        self.power_supply.operations.clear()

        with self.assertRaisesRegex(RuntimeError, "安全关闭存在失败"):
            self.controller.safe_shutdown()

        self.assertTrue(self.power_supply.closed)
        self.assertTrue(self.manager.closed)

    def test_safe_shutdown_close_failure_still_reports(self):
        """VISA 资源关闭失败时仍汇总报告"""
        self.power_supply.fail_on_close = True
        self.manager.fail_on_close = True

        with self.assertRaisesRegex(RuntimeError, "安全关闭存在失败"):
            self.controller.safe_shutdown()

    def test_legacy_positional_constructor_remains_compatible(self):
        """旧的位置参数构造方式仍可用"""
        manager = FakeResourceManager({"FAKE::PS": self.power_supply})
        controller = InstrumentControl(self.config_path, manager)
        self.assertIs(controller.rm, manager)

    def test_keyword_constructor_remains_compatible(self):
        """关键字参数构造方式仍可用"""
        manager = FakeResourceManager({"FAKE::PS": self.power_supply})
        delays = []
        controller = InstrumentControl(
            self.config_path,
            resource_manager=manager,
            sleep_fn=delays.append,
        )
        self.assertIs(controller.rm, manager)
        controller.power_on_sequence()
        self.assertEqual(delays, [0.5, 1.5, 0.5])

    def test_close_all_does_not_manage_power_state(self):
        """close_all 不发送电源输出命令，只关闭连接"""
        self.power_supply.operations.clear()
        self.controller.close_all()
        output_ops = [
            op for op in self.power_supply.operations
            if op[0] == "write" and op[1].startswith(":OUTPut")
        ]
        self.assertEqual(output_ops, [])
        self.assertTrue(self.power_supply.closed)
        self.assertTrue(self.manager.closed)


if __name__ == "__main__":
    unittest.main()
