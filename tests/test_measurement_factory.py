import unittest

from instrument.measurement_adapter import PortMeasurementAdapter
from instrument.measurement_factory import create_measurement_port


class MeasurementFactoryTests(unittest.TestCase):
    def test_legacy_mode_preserves_controller_fallback(self):
        self.assertIsNone(create_measurement_port(mode="legacy"))

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
            create_measurement_port(mode="hardware")


if __name__ == "__main__":
    unittest.main()
