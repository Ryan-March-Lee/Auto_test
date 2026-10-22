import importlib.util
import unittest
from unittest.mock import patch


def gui_dependencies_available():
    dependencies = ("PySide6", "matplotlib", "numpy", "pandas", "seaborn", "markdown", "requests", "pyvisa")
    return all(importlib.util.find_spec(name) is not None for name in dependencies)


@unittest.skipUnless(gui_dependencies_available(), "当前环境缺少完整 GUI 依赖")
class GuiImportSmokeTests(unittest.TestCase):
    def test_gui_module_import_does_not_open_instruments(self):
        with patch("pyvisa.ResourceManager") as resource_manager:
            import presentation.qt.main_window  # noqa: F401

            resource_manager.assert_not_called()

    def test_gui_composition_controller_closes_handed_off_port(self):
        from presentation.qt.main_window import MainWindow

        class _Port:
            def __init__(self):
                self.close_calls = []

            def close_all(self, *, close_rf=False):
                self.close_calls.append(close_rf)

        port = _Port()
        self.assertIsNone(MainWindow._close_instrument_port(port))
        self.assertEqual(port.close_calls, [True])

    def test_measurement_thread_cleanup_releases_port_reference(self):
        from presentation.qt.main_window import MainWindow, MeasurementKind

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
            class _Window:
                instrument_ctrl = _Port()
                measurement_controller = type(
                    "Controller", (), {
                        "state": type("State", (), {"kind": kind})()
                    }
                )()

                @staticmethod
                def _close_instrument_port(port):
                    return MainWindow._close_instrument_port(port)

                def add_log_message(self, message):
                    pass

            window = _Window()
            port = window.instrument_ctrl
            MainWindow._on_measurement_controller_thread_finished(window)
            self.assertIsNone(window.instrument_ctrl)
            self.assertEqual(port.close_calls, [True])

    def test_driver_mapping_realtime_bridge_updates_chat_context(self):
        from presentation.qt.main_window import MainWindow

        class _Window:
            real_time_data = {}
            rt_frequency_list = []
            rt_user_browsing = False
            rt_current_freq_index = 0

        window = _Window()
        MainWindow._store_driver_mapping_realtime_data(
            window,
            {"frequency": "2.4", "sweep_data": {"input_power_sg": [-10]}},
        )
        self.assertEqual(window.real_time_data["2.4"]["frequency"], "2.4")
        self.assertEqual(window.rt_frequency_list, [2.4])
        self.assertEqual(window.rt_current_freq_index, 0)

    def test_amplifier_realtime_bridge_updates_chat_context(self):
        from presentation.qt.main_window import MainWindow

        class _Window:
            real_time_data = {}
            rt_frequency_list = []
            rt_user_browsing = False
            rt_current_freq_index = 0

        window = _Window()
        MainWindow._store_amplifier_realtime_data(
            window,
            {"frequency": "5.8", "sweep_data": {"output_power_dut": [10]}},
        )
        self.assertEqual(window.real_time_data["5.8"]["frequency"], "5.8")
        self.assertEqual(window.rt_frequency_list, [5.8])
        self.assertEqual(window.rt_current_freq_index, 0)


if __name__ == "__main__":
    unittest.main()
