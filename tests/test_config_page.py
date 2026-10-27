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

    def test_driver_power_channel_follows_the_power_source(self):
        page = self.make_page(driver_mode={"enabled": True, "power_source": "dc_supply"})
        page.set_test_mode(True)
        self.assertEqual(page.driver_power_source_combo.currentText(), "直流电源")
        self.assertTrue(page.driver_power_combo.isEnabled())
        self.assertTrue(page.driver_power_hint.isHidden())

        page.driver_power_source_combo.setCurrentText("自带供电")
        self.assertFalse(page.driver_power_combo.isEnabled())
        self.assertTrue(page.driver_power_hint.isVisible())

        page.driver_power_source_combo.setCurrentText("直流电源")
        self.assertTrue(page.driver_power_combo.isEnabled())
        self.assertTrue(page.driver_power_hint.isHidden())

        page.set_test_mode(False)
        self.assertFalse(page.driver_power_combo.isEnabled())

    def test_driver_mode_emits_change_signal(self):
        page = self.make_page()
        changes = []
        page.driver_mode_changed.connect(changes.append)
        page.set_test_mode(False)
        self.assertEqual(changes[-1], False)
        page.set_test_mode(True)
        self.assertEqual(changes[-1], True)

    def test_no_driver_mode_hides_the_power_assignment_box(self):
        page = self.make_page(
            driver_mode={"enabled": True},
            power_supply_assignment={
                "driver_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {"main": {"name": "PS1", "channel": ["CH1", "CH2"]}},
                },
                "dut_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {"carrier": {"name": "PS1", "channel": ["CH1", "CH2"]}},
                },
            },
        )

        page.set_test_mode(False)

        self.assertFalse(page.power_assignment_group.isVisible())
        self.assertFalse(page.driver_mode_requested())
        self.assertEqual(page.build_config()["driver_mode"], {"enabled": False})
        assignment = page.build_config()["power_supply_assignment"]
        self.assertEqual(assignment["driver_amplifier"]["power_supply_count"], 0)
        self.assertEqual(assignment["driver_amplifier"]["supplies"], {})

    def test_driver_mode_keeps_the_box_and_infers_the_legacy_power_source(self):
        page = self.make_page(
            power_supply_assignment={
                "driver_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {"main": {"name": "PS1", "channel": ["CH1", "CH2"]}},
                }
            }
        )

        page.set_test_mode(True)

        self.assertTrue(page.power_assignment_group.isVisible())
        self.assertTrue(page.driver_mode_requested())
        # 旧配置没有 power_source，但有本机驱动通道，按直流电源推断并保留分配。
        self.assertEqual(page.driver_power_source_combo.currentText(), "直流电源")
        self.assertEqual(
            page.build_config()["power_supply_assignment"]["driver_amplifier"][
                "power_supply_count"
            ],
            1,
        )
        # 用户按现场情况改成自带供电后，不再占用本机驱动电源通道。
        page.driver_power_source_combo.setCurrentText("自带供电")
        self.assertEqual(
            page.build_config()["power_supply_assignment"]["driver_amplifier"][
                "power_supply_count"
            ],
            0,
        )
        self.assertEqual(page.build_config()["driver_mode"]["power_source"], "external_ac")


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

    @staticmethod
    def spin_button_rects(widget):
        from PySide6.QtWidgets import QStyle, QStyleOptionSpinBox

        option = QStyleOptionSpinBox()
        option.initFrom(widget)
        option.frame = True
        option.buttonSymbols = widget.buttonSymbols()
        style = widget.style()
        return (
            style.subControlRect(
                QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxUp, widget
            ),
            style.subControlRect(
                QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxDown, widget
            ),
        )

    def test_spin_buttons_step_up_and_down_exactly_once(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        page = self.make_page()
        widget = page.start_power
        up, down = self.spin_button_rects(widget)
        start = widget.value()

        QTest.mouseClick(widget, Qt.LeftButton, Qt.NoModifier, up.center())
        self.app.processEvents()
        self.assertEqual(widget.value(), start + 1)

        widget.setValue(start)
        QTest.mouseClick(widget, Qt.LeftButton, Qt.NoModifier, down.center())
        self.app.processEvents()
        self.assertEqual(widget.value(), start - 1)

    def test_spin_up_button_steps_when_hit_area_is_slightly_off(self):
        """按钮绘制位置与命中区域差 1px 时，点上箭头仍应增加数值。"""
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtTest import QTest

        page = self.make_page()
        widget = page.start_power
        up, _down = self.spin_button_rects(widget)
        probe = QPoint(up.center().x(), up.top() - 1)
        self.assertGreaterEqual(probe.y(), 0, "测试假设按钮上方仍有一点空白")
        start = widget.value()

        QTest.mouseClick(widget, Qt.LeftButton, Qt.NoModifier, probe)
        self.app.processEvents()
        self.assertEqual(widget.value(), start + 1)

    def test_spin_click_in_edit_field_does_not_change_value(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QStyle, QStyleOptionSpinBox

        page = self.make_page()
        widget = page.start_power
        option = QStyleOptionSpinBox()
        option.initFrom(widget)
        option.frame = True
        option.buttonSymbols = widget.buttonSymbols()
        edit = widget.style().subControlRect(
            QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxEditField, widget
        )
        start = widget.value()

        QTest.mouseClick(widget, Qt.LeftButton, Qt.NoModifier, edit.center())
        self.app.processEvents()
        self.assertEqual(widget.value(), start)

    @staticmethod
    def click_on_line_edit(widget, position_in_widget):
        """模拟行编辑框自己收到鼠标按下的事件路径（按钮被行编辑框覆盖时的情况）。"""
        from PySide6.QtCore import QEvent, QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        line_edit = widget.lineEdit()
        global_position = widget.mapToGlobal(position_in_widget)
        local_position = line_edit.mapFromGlobal(global_position)
        press = QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(local_position),
            QPointF(global_position),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
        QApplication.sendEvent(line_edit, press)

    def test_spin_up_clicked_through_line_edit_steps_up(self):
        """行编辑框盖住上按钮时，落在上箭头上的点击仍应加一。"""
        page = self.make_page()
        widget = page.start_power
        up, _down = self.spin_button_rects(widget)
        start = widget.value()

        self.click_on_line_edit(widget, up.center())
        self.app.processEvents()
        self.assertEqual(widget.value(), start + 1)

    def test_spin_down_clicked_through_line_edit_steps_down(self):
        page = self.make_page()
        widget = page.start_power
        _up, down = self.spin_button_rects(widget)
        start = widget.value()

        self.click_on_line_edit(widget, down.center())
        self.app.processEvents()
        self.assertEqual(widget.value(), start - 1)

    def test_spin_text_area_click_through_line_edit_keeps_value(self):
        """按钮区以外的行编辑框点击只移动光标，不改变数值。"""
        from PySide6.QtCore import QPoint

        page = self.make_page()
        widget = page.start_power
        up, _down = self.spin_button_rects(widget)
        start = widget.value()

        self.click_on_line_edit(widget, QPoint(up.center().x() - 50, up.center().y()))
        self.app.processEvents()
        self.assertEqual(widget.value(), start)

    def test_line_edit_stops_left_of_spin_buttons(self):
        """行编辑框不能盖住步进按钮，否则按钮收不到按下事件、没有下沉动画。"""
        page = self.make_page()
        widget = page.start_power
        up, _down = self.spin_button_rects(widget)
        self.assertLess(widget.lineEdit().geometry().right(), up.left())

    def test_line_edit_is_refitted_when_layout_expands_it(self):
        """样式表把行编辑框重新撑到按钮上时，控件会再收窄一次。"""
        page = self.make_page()
        widget = page.start_power
        up, _down = self.spin_button_rects(widget)
        line_edit = widget.lineEdit()

        line_edit.setGeometry(5, 5, up.right() + 5, line_edit.height())
        self.app.processEvents()

        self.assertLess(line_edit.geometry().right(), up.left())

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
