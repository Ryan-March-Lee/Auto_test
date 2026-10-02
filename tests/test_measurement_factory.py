import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from instrument.measurement_adapter import PortMeasurementAdapter
from instrument.measurement_factory import create_measurement_port, _VisaSession
from instrument.simulation import SimulatedSignalGenerator, SimulatedSpectrumAnalyzer, SimulatedPowerSupply


class MeasurementFactoryTests(unittest.TestCase):
    def test_simulation_mode_assembles_new_session_and_adapter(self):
        port = create_measurement_port(
            mode="simulation",
            power_channels={"gate": "A", "drain": "B"},
            driver_power_channels={"gate": "C", "drain": "D"},
        )
        self.assertIsInstance(port, PortMeasurementAdapter)
        self.assertEqual(port.session.state.value, "prepared")
        port.close_all()
        self.assertEqual(port.session.state.value, "cleaned")

    def test_unknown_mode_is_rejected_before_hardware_creation(self):
        with self.assertRaisesRegex(ValueError, "不支持"):
            create_measurement_port(mode="unknown")

    def test_hardware_mode_requires_explicit_configuration(self):
        with self.assertRaisesRegex(ValueError, "config_path"):
            create_measurement_port(mode="hardware")

    def test_visa_manager_waits_for_session_cleanup_and_retries(self):
        manager = _CloseRecorder()
        signal = SimulatedSignalGenerator()
        analyzer = SimulatedSpectrumAnalyzer()
        power = SimulatedPowerSupply()
        session = _VisaSession(manager, signal, analyzer, power, {"gate": "A", "drain": "B"})
        session.validate()
        session.connect()
        session.prepare()
        signal.inject_failure("rf_off")

        with self.assertRaises(RuntimeError):
            session.close()
        self.assertEqual(manager.close_calls, 0)
        signal.fail_on = None
        session.close()
        self.assertEqual(manager.close_calls, 1)
        self.assertTrue(session.resources_closed)

    def test_hardware_mode_assembles_drivers_and_closes_owned_resources(self):
        config = {
            "instruments": {
                "signal_generator": {"address": "SG", "enabled": True},
                "spectrum_analyzer": {"address": "SA", "enabled": True},
                "power_supplies": {
                    "PS1": {
                        "address": "PS", "enabled": True,
                        "channels": {
                            "CH1": {"voltage": {"value": 2.8}, "current": {"value": 0.1}},
                            "CH2": {"voltage": {"value": 28.0}, "current": {"value": 2.0}},
                        },
                    }
                },
            },
            "power_supply_assignment": {
                "dut_amplifier": {"supplies": {"carrier": {"name": "PS1", "channel": ["CH1", "CH2"]}}},
                "driver_amplifier": {"supplies": {}},
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            manager = _FakeResourceManager()
            with patch("instrument.measurement_factory.pyvisa.ResourceManager", return_value=manager):
                port = create_measurement_port(str(path), mode="hardware")
            self.assertEqual(manager.opened_addresses, ["SG", "SA", "PS"])
            port.set_frequency(2.3)
            port.set_span(10)
            port.measure_power_with_average()
            errors = port.close_all(close_rf=True)
            self.assertEqual(errors, [])
            self.assertTrue(manager.closed)
        self.assertTrue(all(resource.closed for resource in manager.resources))

    def test_hardware_mode_uses_configured_supply_and_channel_only(self):
        config = _base_hardware_config()
        config["power_supply_assignment"] = {
            "dut_amplifier": {"supplies": {"carrier": {"name": "PS1", "channel": ["CH1", "CH2"]}}},
            "driver_amplifier": {"supplies": {}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            manager = _FakeResourceManager()
            with patch("instrument.measurement_factory.pyvisa.ResourceManager", return_value=manager):
                port = create_measurement_port(str(path), mode="hardware")
            self.assertEqual(manager.opened_addresses, ["SG", "SA", "PS"])
            port.set_voltage("PS1", "CH1", 2.8)
            port.power_on_sequence()
            resource = manager.resources[2]
            self.assertIn(("write", ":SOURce1:VOLTage 2.8"), resource.commands)
            self.assertIn(("write", ":OUTPut CH1,ON"), resource.commands)
            self.assertEqual(port.close_all(close_rf=True), [])
            self.assertTrue(all(resource.closed for resource in manager.resources))

    def test_hardware_mode_rejects_missing_explicit_supply_assignment(self):
        config = _base_hardware_config()
        config["instruments"]["power_supplies"]["PS2"] = {
            "address": "PS2", "enabled": True, "channels": {}
        }
        config["power_supply_assignment"] = {"dut_amplifier": {"supplies": {}}, "driver_amplifier": {"supplies": {}}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            manager = _DiscoveryResourceManager()
            with patch("instrument.measurement_factory.pyvisa.ResourceManager", return_value=manager):
                with self.assertRaisesRegex(ValueError, "必须显式配置电源角色"):
                    create_measurement_port(str(path), mode="hardware")

    def test_hardware_mode_rejects_unenabled_assigned_supply(self):
        config = _base_hardware_config()
        config["instruments"]["power_supplies"]["PS1"]["enabled"] = False
        config["power_supply_assignment"] = {
            "dut_amplifier": {"supplies": {"carrier": {"name": "PS1", "channel": ["CH1", "CH2"]}}},
            "driver_amplifier": {"supplies": {}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with patch("instrument.measurement_factory.pyvisa.ResourceManager", return_value=_FakeResourceManager()):
                with self.assertRaisesRegex(ValueError, "未启用"):
                    create_measurement_port(str(path), mode="hardware")

    def test_hardware_mode_rejects_incomplete_dut_mapping(self):
        config = _base_hardware_config()
        config["power_supply_assignment"] = {
            "dut_amplifier": {"supplies": {"carrier": {"name": "PS1", "channel": ["CH1"]}}},
            "driver_amplifier": {"supplies": {}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with patch("instrument.measurement_factory.pyvisa.ResourceManager", return_value=_FakeResourceManager()):
                with self.assertRaisesRegex(ValueError, "DUT 电源"):
                    create_measurement_port(str(path), mode="hardware")

    def test_hardware_mode_rejects_unknown_assigned_channel(self):
        config = _base_hardware_config()
        config["power_supply_assignment"] = {
            "dut_amplifier": {"supplies": {"carrier": {"name": "PS1", "channel": ["CH9"]}}},
            "driver_amplifier": {"supplies": {}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with patch("instrument.measurement_factory.pyvisa.ResourceManager", return_value=_FakeResourceManager()):
                with self.assertRaisesRegex(ValueError, "不存在的通道"):
                    create_measurement_port(str(path), mode="hardware")


def _base_hardware_config():
    return {
        "instruments": {
            "signal_generator": {"address": "SG", "enabled": True},
            "spectrum_analyzer": {"address": "SA", "enabled": True},
            "power_supplies": {
                "PS1": {
                    "address": "PS", "enabled": True,
                    "channels": {
                        "CH1": {"voltage": {"value": 2.8}, "current": {"value": 0.1}},
                        "CH2": {"voltage": {"value": 28.0}, "current": {"value": 2.0}},
                    },
                }
            },
        }
    }


class _FakeResource:
    def __init__(self, address):
        self.address = address
        self.timeout = None
        self.closed = False
        self.commands = []

    def write(self, command):
        self.commands.append(("write", command))

    def query(self, command):
        self.commands.append(("query", command))
        if command == "*IDN?" and self.address == "PS":
            return "RIGOL TECHNOLOGIES,DP832A,TEST,1.0"
        if command.startswith("OUTP?") and self.address == "PS":
            return "OFF"
        return "-30.0"

    def close(self):
        self.closed = True


class _FakeResourceManager:
    def __init__(self):
        self.resources = []
        self.opened_addresses = []
        self.closed = False

    def open_resource(self, address):
        self.opened_addresses.append(address)
        resource = _FakeResource(address)
        self.resources.append(resource)
        return resource

    def list_resources(self):
        return ("SG", "SA", "PS")

    def close(self):
        self.closed = True


class _CloseRecorder:
    def __init__(self):
        self.close_calls = 0

    def close(self):
        self.close_calls += 1


class _DiscoveryResourceManager(_FakeResourceManager):
    def __init__(self):
        super().__init__()
        self.resources_by_address = {}

    def list_resources(self):
        return ("SG", "SA", "PS-A", "PS-B")

    def open_resource(self, address):
        self.opened_addresses.append(address)
        resource = _DiscoveryResource(address)
        self.resources.append(resource)
        self.resources_by_address[address] = resource
        return resource


class _DiscoveryResource(_FakeResource):
    def query(self, command):
        self.commands.append(("query", command))
        if command == "*IDN?":
            return "RIGOL TECHNOLOGIES,DP832A,TEST,1.0" if self.address.startswith("PS-") else "TEST,DEVICE,1,1"
        if command.startswith("OUTP?"):
            return "OFF"
        return "-30.0"


if __name__ == "__main__":
    unittest.main()
