import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from presentation.qt.config_page import ConfigPage


class ConfigPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_page(self, **extra):
        config = {"test_frequencies": [1.0], "instruments": {"power_supplies": {}}}
        config.update(extra)
        page = ConfigPage(config)
        page.show()
        self.app.processEvents()
        return page

    def test_pa_unit_visibility_tracks_count(self):
        page = self.make_page(dut_config={"power_supply_count": 1})
        self.assertFalse(page.pa_unit2_power_combo.isVisible())
        self.assertFalse(page.pa_unit3_power_combo.isVisible())
        page.pa_unit_count_combo.setCurrentText("2")
        self.assertTrue(page.pa_unit2_power_combo.isVisible())
        self.assertFalse(page.pa_unit3_power_combo.isVisible())
        page.pa_unit_count_combo.setCurrentText("3")
        self.assertTrue(page.pa_unit3_power_combo.isVisible())

    def test_driver_controls_follow_mode_and_power_switches(self):
        page = self.make_page()
        page.driver_mode_check.setChecked(False)
        self.assertFalse(page.driver_power_enabled.isEnabled())
        self.assertFalse(page.driver_power_combo.isEnabled())
        page.driver_mode_check.setChecked(True)
        page.driver_power_enabled.setChecked(False)
        self.assertFalse(page.driver_power_combo.isEnabled())
        page.driver_power_enabled.setChecked(True)
        self.assertTrue(page.driver_power_combo.isEnabled())

    def test_driver_mode_emits_change_signal(self):
        page = self.make_page()
        changes = []
        page.driver_mode_changed.connect(changes.append)
        page.driver_mode_check.setChecked(False)
        self.assertEqual(changes[-1], False)

    def test_invalid_frequency_prevents_save_request(self):
        page = self.make_page()
        calls = []
        page.save_requested.connect(lambda: calls.append(True))
        page.freq_edit.setText("not-a-list")
        page.request_save()
        self.assertEqual(calls, [])

    def test_malformed_nested_config_is_normalized(self):
        page = self.make_page(
            compression_point=[],
            attenuator=[],
            power_supply_assignment=[],
        )
        result = page.build_config()
        self.assertIsInstance(result["instruments"]["power_supplies"], dict)
        self.assertIn("wiring", result)


if __name__ == "__main__":
    unittest.main()
