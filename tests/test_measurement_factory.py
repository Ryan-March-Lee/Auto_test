import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from pyvisa import constants, errors

from application.errors import (
    DeviceCapabilityError,
    DeviceConnectionError,
    ErrorCategory,
    ProtocolError,
)
from instrument.measurement_adapter import PortMeasurementAdapter
from instrument.measurement_factory import create_measurement_port, _VisaSession
from instrument.ports import PowerSupplyPort
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
                            "CH1": {"role": "gate", "voltage": {"value": 2.8}, "current": {"value": 0.1}},
                            "CH2": {"role": "drain", "voltage": {"value": 28.0}, "current": {"value": 2.0}},
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

    def test_hardware_mode_rejects_duplicate_instrument_addresses_before_opening(self):
        config = _base_hardware_config()
        config["instruments"]["power_supplies"]["PS2"] = {
            "address": "PS",
            "enabled": True,
            "channels": {
                "CH1": {"role": "gate", "voltage": {"value": 5.0}, "current": {"value": 0.2}},
                "CH2": {"role": "drain", "voltage": {"value": 30.0}, "current": {"value": 1.0}},
            },
        }
        config["power_supply_assignment"] = {
            "dut_amplifier": {
                "supplies": {
                    "carrier": {"name": "PS1", "channel": ["CH1"]},
                    "peaking": {"name": "PS2", "channel": ["CH2"]},
                }
            },
            "driver_amplifier": {"supplies": {}},
        }
        manager = _FakeResourceManager()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with patch("instrument.measurement_factory.pyvisa.ResourceManager", return_value=manager):
                with self.assertRaisesRegex(ValueError, "地址重复"):
                    create_measurement_port(str(path), mode="hardware")
        self.assertEqual(manager.opened_addresses, [])

    def test_hardware_mode_requires_explicit_channel_role_before_opening(self):
        config = _base_hardware_config()
        del config["instruments"]["power_supplies"]["PS1"]["channels"]["CH1"]["role"]
        config["power_supply_assignment"] = {
            "dut_amplifier": {"supplies": {"carrier": {"name": "PS1", "channel": ["CH1", "CH2"]}}},
            "driver_amplifier": {"supplies": {}},
        }
        manager = _FakeResourceManager()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with patch("instrument.measurement_factory.pyvisa.ResourceManager", return_value=manager):
                with self.assertRaisesRegex(ValueError, "显式 gate/drain 角色"):
                    create_measurement_port(str(path), mode="hardware")
        self.assertEqual(manager.opened_addresses, [])

    def test_hardware_mode_rejects_duplicate_power_roles_before_opening(self):
        config = _base_hardware_config()
        config["instruments"]["power_supplies"]["PS1"]["channels"]["CH2"]["role"] = "gate"
        config["power_supply_assignment"] = {
            "dut_amplifier": {"supplies": {"carrier": {"name": "PS1", "channel": ["CH1", "CH2"]}}},
            "driver_amplifier": {"supplies": {}},
        }
        manager = _FakeResourceManager()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with patch("instrument.measurement_factory.pyvisa.ResourceManager", return_value=manager):
                with self.assertRaisesRegex(ValueError, "供电角色重复配置"):
                    create_measurement_port(str(path), mode="hardware")
        self.assertEqual(manager.opened_addresses, [])

    def test_hardware_mode_rejects_identical_signal_and_spectrum_address_before_opening(self):
        config = _base_hardware_config()
        config["instruments"]["spectrum_analyzer"]["address"] = "SG"
        config["power_supply_assignment"] = {
            "dut_amplifier": {"supplies": {"carrier": {"name": "PS1", "channel": ["CH1", "CH2"]}}},
            "driver_amplifier": {"supplies": {}},
        }
        manager = _FakeResourceManager()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with patch("instrument.measurement_factory.pyvisa.ResourceManager", return_value=manager):
                with self.assertRaisesRegex(ValueError, "信号源与频谱仪的 VISA 地址重复"):
                    create_measurement_port(str(path), mode="hardware")
        self.assertEqual(manager.opened_addresses, [])

    def test_hardware_mode_rejects_dut_and_driver_channel_overlap_before_opening(self):
        config = _base_hardware_config()
        config["power_supply_assignment"] = {
            "dut_amplifier": {"supplies": {"carrier": {"name": "PS1", "channel": ["CH1", "CH2"]}}},
            "driver_amplifier": {"supplies": {"main": {"name": "PS1", "channel": ["CH1", "CH2"]}}},
        }
        manager = _FakeResourceManager()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with patch("instrument.measurement_factory.pyvisa.ResourceManager", return_value=manager):
                with self.assertRaisesRegex(ValueError, "占用了同一个电源通道"):
                    create_measurement_port(str(path), mode="hardware")
        self.assertEqual(manager.opened_addresses, [])

    def test_hardware_mode_routes_explicit_supply_channel_tokens(self):
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

            port.set_voltage("PS1", "CH1", 2.8)
            self.assertIsInstance(port.power_supply, PowerSupplyPort)
            self.assertEqual(port.read_voltage("PS1", "CH1"), 2.8)
            self.assertEqual(port.read_current("PS1", "CH2"), 1.0)
            with self.assertRaisesRegex(ValueError, "未配置的生产电源通道"):
                port.set_voltage("PS9", "CH1", 2.8)

            resource = manager.resources[2]
            self.assertIn(("write", ":SOURce1:VOLTage 2.8"), resource.commands)
            self.assertIn(("query", ":MEASure:VOLTage? CH1"), resource.commands)
            self.assertIn(("query", ":MEASure:CURRent? CH2"), resource.commands)
            self.assertEqual(port.close_all(close_rf=True), [])

    def test_hardware_mode_emergency_power_off_uses_configured_channels(self):
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

            self.assertEqual(port.emergency_power_off_all(), [])
            self.assertEqual(
                port._last_emergency_power_off,
                [(0, "PS1/CH2"), (0, "PS1/CH1")],
            )
            commands = manager.resources[2].commands
            self.assertIn(("write", ":OUTPut CH2,OFF"), commands)
            self.assertIn(("write", ":OUTPut CH1,OFF"), commands)
            self.assertEqual(port.close_all(close_rf=True), [])

    def test_emergency_power_off_order_follows_roles_not_configuration_order(self):
        """通道在配置里的书写顺序不影响急停顺序：先漏极后栅极。"""
        config = _base_hardware_config()
        config["instruments"]["power_supplies"]["PS1"]["channels"] = {
            "CH2": {"role": "drain", "voltage": {"value": 28.0}, "current": {"value": 2.0}},
            "CH1": {"role": "gate", "voltage": {"value": 2.8}, "current": {"value": 0.1}},
        }
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

            self.assertEqual(port.emergency_power_off_all(), [])
            self.assertEqual(
                port._last_emergency_power_off,
                [(0, "PS1/CH2"), (0, "PS1/CH1")],
            )
            self.assertEqual(port.close_all(close_rf=True), [])

    def test_emergency_power_off_only_switches_assigned_channels(self):
        """急停覆盖全部已分配角色通道，未分配的通道不属于本次装配。"""
        config = _base_hardware_config()
        config["instruments"]["power_supplies"]["PS1"]["channels"]["CH3"] = {
            "voltage": {"value": 0.0}, "current": {"value": 0.0}
        }
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

            self.assertEqual(port.emergency_power_off_all(), [])
            self.assertEqual(
                [channel for _index, channel in port._last_emergency_power_off],
                ["PS1/CH2", "PS1/CH1"],
            )
            self.assertEqual(port.close_all(close_rf=True), [])


def _base_hardware_config():
    return {
        "instruments": {
            "signal_generator": {"address": "SG", "enabled": True},
            "spectrum_analyzer": {"address": "SA", "enabled": True},
            "power_supplies": {
                "PS1": {
                    "address": "PS", "enabled": True,
                    "channels": {
                        "CH1": {"role": "gate", "voltage": {"value": 2.8}, "current": {"value": 0.1}},
                        "CH2": {"role": "drain", "voltage": {"value": 28.0}, "current": {"value": 2.0}},
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
        if command == ":MEASure:VOLTage? CH1":
            return "2.8"
        if command == ":MEASure:CURRent? CH2":
            return "1.0"
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


class _IdentityFailingManager(_FakeResourceManager):
    """打开成功、身份查询失败：用于区分“连不上”和“命令层失败”。"""

    def open_resource(self, address):
        self.opened_addresses.append(address)
        resource = _IdentityFailingResource(address)
        self.resources.append(resource)
        return resource


class _IdentityFailingResource(_FakeResource):
    def query(self, command):
        if command == "*IDN?":
            raise errors.VisaIOError(constants.StatusCode.error_timeout)
        return super().query(command)


class _WrongModelManager(_FakeResourceManager):
    def open_resource(self, address):
        self.opened_addresses.append(address)
        resource = _WrongModelResource(address)
        self.resources.append(resource)
        return resource


class _WrongModelResource(_FakeResource):
    def query(self, command):
        if command == "*IDN?" and self.address == "PS":
            return "KEYSIGHT TECHNOLOGIES,E3631A,0,1.0"
        return super().query(command)


def _hardware_config_path(directory):
    config = _base_hardware_config()
    config["power_supply_assignment"] = {
        "dut_amplifier": {"supplies": {"carrier": {"name": "PS1", "channel": ["CH1", "CH2"]}}},
        "driver_amplifier": {"supplies": {}},
    }
    path = Path(directory) / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return str(path)


class ConnectionErrorClassificationTests(unittest.TestCase):
    """连接失败有独立分类，且失败时仍释放已经打开的资源。"""

    def test_unreachable_instrument_is_reported_as_device_connection_error(self):
        config = _base_hardware_config()
        config["power_supply_assignment"] = {
            "dut_amplifier": {"supplies": {"carrier": {"name": "PS1", "channel": ["CH1", "CH2"]}}},
            "driver_amplifier": {"supplies": {}},
        }
        manager = _FakeResourceManager()

        def fail_open(address):
            raise errors.VisaIOError(constants.StatusCode.error_resource_not_found)

        manager.open_resource = fail_open
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with patch(
                "instrument.measurement_factory.pyvisa.ResourceManager", return_value=manager
            ):
                with self.assertRaises(DeviceConnectionError) as raised:
                    create_measurement_port(str(path), mode="hardware")

        self.assertIs(raised.exception.category, ErrorCategory.CONNECTION)
        self.assertIsInstance(raised.exception.__cause__, errors.VisaIOError)
        self.assertTrue(manager.closed, "连接失败后仍必须释放已创建的 ResourceManager")

    def test_missing_visa_backend_is_reported_as_device_connection_error(self):
        """ResourceManager 自身创建失败也属于连接分类，不再是裸 pyvisa 异常。"""
        with tempfile.TemporaryDirectory() as directory:
            path = _hardware_config_path(directory)
            with patch(
                "instrument.measurement_factory.pyvisa.ResourceManager",
                side_effect=OSError("VISA library not found"),
            ):
                with self.assertRaises(DeviceConnectionError) as raised:
                    create_measurement_port(path, mode="hardware")

        self.assertIs(raised.exception.category, ErrorCategory.CONNECTION)
        self.assertIsInstance(raised.exception.__cause__, OSError)

    def test_identity_query_failure_is_reported_as_protocol_error(self):
        """已打开的仪器查询失败属于命令层失败，不伪装成连不上。"""
        manager = _IdentityFailingManager()
        with tempfile.TemporaryDirectory() as directory:
            path = _hardware_config_path(directory)
            with patch(
                "instrument.measurement_factory.pyvisa.ResourceManager", return_value=manager
            ):
                with self.assertRaises(ProtocolError) as raised:
                    create_measurement_port(path, mode="hardware")

        self.assertIs(raised.exception.category, ErrorCategory.PROTOCOL)
        self.assertIsInstance(raised.exception.__cause__, errors.VisaIOError)
        self.assertTrue(manager.closed, "装配失败后必须释放 ResourceManager")
        self.assertTrue(all(resource.closed for resource in manager.resources))

    def test_wrong_power_supply_model_is_reported_as_capability_error(self):
        manager = _WrongModelManager()
        with tempfile.TemporaryDirectory() as directory:
            path = _hardware_config_path(directory)
            with patch(
                "instrument.measurement_factory.pyvisa.ResourceManager", return_value=manager
            ):
                with self.assertRaises(DeviceCapabilityError) as raised:
                    create_measurement_port(path, mode="hardware")

        self.assertIs(raised.exception.category, ErrorCategory.CAPABILITY)
        self.assertIn("DP832A", str(raised.exception))
        self.assertTrue(manager.closed)


if __name__ == "__main__":
    unittest.main()
