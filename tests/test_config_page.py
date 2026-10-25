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

    def test_numeric_spin_boxes_ignore_mouse_wheel(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QWheelEvent

        page = self.make_page()
        widget = page.start_power
        before = widget.value()
        event = QWheelEvent(
            QPoint(0, 0), QPoint(0, 0), QPoint(0, 0), QPoint(0, 120),
            Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False,
        )
        self.app.sendEvent(widget, event)
        self.assertEqual(widget.value(), before)

    def test_combo_boxes_ignore_mouse_wheel(self):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QWheelEvent

        page = self.make_page()
        combos = (page.compression_combo, page.attenuator_combo, page.pa_unit_count_combo)
        before = [combo.currentText() for combo in combos]
        event = QWheelEvent(
            QPoint(0, 0), QPoint(0, 0), QPoint(0, 0), QPoint(0, 120),
            Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False,
        )
        for combo in combos:
            self.app.sendEvent(combo, event)
        self.assertEqual([combo.currentText() for combo in combos], before)

    def test_combo_boxes_still_selectable_explicitly(self):
        page = self.make_page()
        page.compression_combo.setCurrentText("3dB")
        page.attenuator_combo.setCurrentText("30dB")
        page.pa_unit_count_combo.setCurrentText("3")
        result = page.build_config()
        self.assertEqual(result["compression_point"]["type"], "3dB")
        self.assertEqual(result["attenuator"]["type"], "30dB")
        self.assertEqual(result["dut_config"]["power_supply_count"], 3)

    def test_wide_window_keeps_group_boxes_compact(self):
        from PySide6.QtWidgets import QGroupBox

        page = self.make_page()
        page.resize(2400, 900)
        self.app.processEvents()
        widths = [box.width() for box in page.findChildren(QGroupBox) if box.isVisible()]
        self.assertTrue(widths)
        # 分组框不应随窗口宽度无限拉伸，避免出现很长的灰色边框。
        self.assertLessEqual(max(widths), 900)

    def test_config_sections_use_two_columns(self):
        from PySide6.QtWidgets import QGroupBox

        page = self.make_page()
        page.resize(1400, 900)
        self.app.processEvents()
        titles = {box.title(): box for box in page.findChildren(QGroupBox) if box.isVisible()}
        connection = titles["仪器连接配置"]
        power_detail = titles["电源详细配置"]
        # 两栏布局：仪器/参数在左，电源配置在右，而不是单列纵向堆叠。
        self.assertLess(connection.x() + connection.width(), power_detail.x())


if __name__ == "__main__":
    unittest.main()
