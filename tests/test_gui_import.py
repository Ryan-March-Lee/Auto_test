import importlib.util
import unittest
from unittest.mock import MagicMock, patch


def gui_dependencies_available():
    dependencies = ("PySide6", "matplotlib", "numpy", "pandas", "seaborn", "markdown", "requests", "pyvisa")
    return all(importlib.util.find_spec(name) is not None for name in dependencies)


def _coordinator(controller=None):
    from presentation.qt.application_coordinator import ApplicationCoordinator

    return ApplicationCoordinator(
        gui_data_service=MagicMock(),
        controller=controller or MagicMock(),
    )


@unittest.skipUnless(gui_dependencies_available(), "当前环境缺少完整 GUI 依赖")
class GuiImportSmokeTests(unittest.TestCase):
    def test_gui_module_import_does_not_open_instruments(self):
        with patch("pyvisa.ResourceManager") as resource_manager:
            import presentation.qt.main_window  # noqa: F401
            import presentation.qt.application_coordinator  # noqa: F401

            resource_manager.assert_not_called()

    def test_gui_composition_closes_handed_off_port(self):
        from presentation.qt.application_coordinator import close_instrument_port

        class _Port:
            def __init__(self):
                self.close_calls = []

            def close_all(self, *, close_rf=False):
                self.close_calls.append(close_rf)

        port = _Port()
        close_instrument_port(port)
        self.assertEqual(port.close_calls, [True])

    def test_gui_composition_falls_back_to_safe_shutdown(self):
        from presentation.qt.application_coordinator import close_instrument_port

        class _Port:
            def __init__(self):
                self.shutdown_calls = 0

            def safe_shutdown(self):
                self.shutdown_calls += 1

        port = _Port()
        close_instrument_port(port)
        self.assertEqual(port.shutdown_calls, 1)

    def test_measurement_thread_cleanup_releases_port_reference(self):
        from presentation.qt.measurement_state import MeasurementKind

        class _Port:
            def __init__(self):
                self.close_calls = []

            def close_all(self, *, close_rf=False):
                self.close_calls.append(close_rf)

        for kind in (
            MeasurementKind.CABLE_LOSS,
            MeasurementKind.DRIVER_MAPPING,
            MeasurementKind.AMPLIFIER,
        ):
            controller = MagicMock()
            controller.state.kind = kind
            coordinator = _coordinator(controller)
            port = _Port()
            coordinator.instrument_ctrl = port

            coordinator.release_port_after_measurement()

            self.assertIsNone(coordinator.instrument_ctrl)
            self.assertEqual(port.close_calls, [True])

    def test_driver_mapping_realtime_bridge_updates_chat_context(self):
        coordinator = _coordinator()

        self.assertTrue(
            coordinator.store_realtime_data(
                {"frequency": "2.4", "sweep_data": {"input_power_sg": [-10]}}
            )
        )

        self.assertEqual(coordinator.realtime_buffer.data["2.4"]["frequency"], 2.4)
        self.assertEqual(coordinator.realtime_buffer.frequencies, [2.4])
        self.assertEqual(coordinator.realtime_buffer.current_index, 0)

    def test_amplifier_realtime_bridge_updates_chat_context(self):
        coordinator = _coordinator()

        self.assertTrue(
            coordinator.store_realtime_data(
                {"frequency": "5.8", "sweep_data": {"output_power_dut": [10]}}
            )
        )

        self.assertEqual(coordinator.realtime_buffer.data["5.8"]["frequency"], 5.8)
        self.assertEqual(coordinator.realtime_buffer.frequencies, [5.8])
        self.assertEqual(coordinator.realtime_buffer.current_index, 0)

    def test_realtime_bridge_rejects_payloads_without_a_frequency(self):
        coordinator = _coordinator()

        self.assertFalse(coordinator.store_realtime_data({"sweep_data": {}}))
        self.assertFalse(coordinator.store_realtime_data({"frequency": "not-a-number"}))
        self.assertEqual(coordinator.realtime_buffer.frequencies, [])


if __name__ == "__main__":
    unittest.main()
