import unittest
from unittest.mock import patch

from instrument_control import InstrumentControl


class MinimalVisaResource:
    def __init__(self, address):
        self.address = address
        self.writes = []
        self.closed = False

    def write(self, command):
        self.writes.append(command)

    def query(self, command):
        raise AssertionError(f"P-1 测试不应执行查询: {command}")

    def close(self):
        self.closed = True


class MinimalResourceManager:
    def __init__(self, addresses):
        self.resources = {address: MinimalVisaResource(address) for address in addresses}
        self.opened_addresses = []
        self.closed = False

    def open_resource(self, address):
        self.opened_addresses.append(address)
        return self.resources[address]

    def close(self):
        self.closed = True


class InstrumentControlInjectionTests(unittest.TestCase):
    ADDRESSES = (
        "TCPIP0::192.168.1.201::inst0::INSTR",
        "TCPIP0::192.168.1.202::inst0::INSTR",
        "TCPIP0::192.168.1.108::inst0::INSTR",
    )

    def make_manager(self):
        return MinimalResourceManager(self.ADDRESSES)

    def test_injected_resource_manager_prevents_real_visa_creation(self):
        manager = self.make_manager()
        with patch("instrument_control.pyvisa.ResourceManager") as resource_manager_factory:
            controller = InstrumentControl(resource_manager=manager, sleep_fn=lambda _: None)

        resource_manager_factory.assert_not_called()
        self.assertIs(controller.rm, manager)
        self.assertEqual(manager.opened_addresses, list(self.ADDRESSES))

    def test_default_resource_manager_path_remains_available_without_real_connection(self):
        manager = self.make_manager()
        with patch("instrument_control.pyvisa.ResourceManager", return_value=manager) as resource_manager_factory:
            controller = InstrumentControl(sleep_fn=lambda _: None)

        resource_manager_factory.assert_called_once_with()
        self.assertIs(controller.rm, manager)

    def test_legacy_single_path_constructor_remains_compatible(self):
        manager = self.make_manager()
        with patch("instrument_control.pyvisa.ResourceManager", return_value=manager):
            controller = InstrumentControl(None, manager)

        self.assertIs(controller.rm, manager)
        self.assertIs(controller.sleep_fn, __import__("time").sleep)

    def test_injected_sleep_function_receives_power_sequence_delays(self):
        manager = self.make_manager()
        delays = []
        controller = InstrumentControl(resource_manager=manager, sleep_fn=delays.append)

        controller.power_on_sequence()
        controller.power_off_sequence()

        self.assertIn(1.5, delays)
        self.assertIn(2, delays)
        self.assertGreaterEqual(delays.count(0.5), 4)
        self.assertNotIn(1, delays)

    def test_initialization_failure_preserves_original_error_and_closes_resources(self):
        manager = MinimalResourceManager((self.ADDRESSES[0],))
        with self.assertRaisesRegex(Exception, "Failed to initialize instruments"):
            InstrumentControl(resource_manager=manager, sleep_fn=lambda _: None)

        self.assertTrue(manager.resources[self.ADDRESSES[0]].closed)
        self.assertTrue(manager.closed)


if __name__ == "__main__":
    unittest.main()
