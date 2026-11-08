import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from application.device_binding import ApplyDeviceSelectionRequest
from presentation.qt.configuration_page import ConfigurationPage


def apply_devices(page, assignments):
    """提交一次设备勾选；页面只接受命令 DTO（步骤 13 删除字典列表兼容入口）。"""
    return page.apply_device_selection(
        ApplyDeviceSelectionRequest.from_assignments(assignments)
    )


class ConfigurationPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_page(self, **extra):
        config = {"test_frequencies": [1.0], "instruments": {"power_supplies": {}}}
        config.update(extra)
        page = ConfigurationPage(config)
        page.show()
        self.app.processEvents()
        return page

    def test_a_read_only_document_blocks_editing_and_saving(self):
        """1.0 编辑文档只读：提示可见、参数与保存入口禁用，回退入口保持可用。"""
        page = self.make_page()
        saves = []
        page.save_requested.connect(lambda: saves.append(True))

        page.set_read_only("1.0 配置只能查看，不能保存；请先运行迁移器升级到 1.1")

        self.assertTrue(page.read_only_notice.isVisibleTo(page))
        self.assertIn("迁移器", page.read_only_notice.text())
        self.assertFalse(page.test_parameters_group.isEnabled())
        self.assertFalse(page.save_config_btn.isEnabled())
        self.assertFalse(page.finalize_run_btn.isEnabled())
        self.assertTrue(page.return_welcome_btn.isEnabled())
        page.request_save()
        self.assertEqual(saves, [])

        page.set_read_only(None)
        self.assertFalse(page.read_only_notice.isVisibleTo(page))
        self.assertTrue(page.test_parameters_group.isEnabled())
        self.assertTrue(page.save_config_btn.isEnabled())

    def test_driver_mode_emits_change_signal(self):
        page = self.make_page()
        changes = []
        page.driver_mode_changed.connect(changes.append)
        page.set_test_mode(False)
        self.assertEqual(changes[-1], False)
        page.set_test_mode(True)
        self.assertEqual(changes[-1], True)

    def test_power_labels_describe_main_amplifier_input_power(self):
        """步骤 3：起止功率与步长都表示主功放输入功率，不再是信号源输出功率。"""
        page = self.make_page()

        texts = {label.text() for label in page.findChildren(QLabel)}

        self.assertIn("起始主功放输入功率 (dBm)", texts)
        self.assertIn("结束主功放输入功率 (dBm)", texts)
        self.assertIn("输入功率步长 (dB)", texts)
        self.assertIn("驱动功放预估增益 (dB)", texts)
        self.assertIn("主功放输入功率", page.start_power.toolTip())

    def test_driver_gain_control_follows_the_driver_mode(self):
        page = self.make_page()
        page.driver_gain.setText("20.5")

        page.set_test_mode(False)

        self.assertFalse(page.driver_gain.isEnabled())
        self.assertFalse(page.driver_gain_label.isEnabled())
        self.assertEqual(page.driver_gain.text(), "20.5", "禁用时保留已填写的值")

        page.set_test_mode(True)

        self.assertTrue(page.driver_gain.isEnabled())
        self.assertTrue(page.driver_gain_label.isEnabled())
        self.assertEqual(page.driver_gain.text(), "20.5")

    def test_driver_gain_round_trips_through_save_and_backfill(self):
        page = self.make_page(driver_mode={"enabled": True})
        self.assertEqual(page.driver_gain.text(), "", "缺失的增益不伪造默认值")

        page.driver_gain.setText("20.5")
        self.assertEqual(
            page.build_config()["driver_mode"]["estimated_gain_db"], 20.5
        )

        page.driver_gain.setText("")
        self.assertIsNone(
            page.build_config()["driver_mode"]["estimated_gain_db"]
        )

        page.apply_config(
            {
                "test_frequencies": [1.0],
                "driver_mode": {"enabled": True, "estimated_gain_db": 31.5},
                "instruments": {"power_supplies": {}},
            }
        )

        self.assertEqual(page.driver_gain.text(), "31.5")
        self.assertTrue(page.driver_gain.isEnabled())

    def test_invalid_driver_gain_is_rejected_by_the_config_dto(self):
        """页面不预判增益：非法文本进入文档，由正式校验给出“必须是有限数值”。"""
        page = self.make_page(driver_mode={"enabled": True})
        failures = []
        page.validation_failed.connect(failures.append)

        page.driver_gain.setText("not-a-gain")

        self.assertFalse(page.validate())
        self.assertIn("有限数值", "；".join(failures))

    def test_power_controls_live_on_the_independent_page(self):
        """步骤 12：电源控件与上电计划不再由参数配置页创建或读取。"""
        page = self.make_page()
        for name in (
            "power_section",
            "power_tabs",
            "power_config_widgets",
            "power_assignment_group",
            "dut_power_plan_editor",
        ):
            with self.subTest(name=name):
                self.assertFalse(hasattr(page, name))

    def test_validation_does_not_report_power_section_errors_owned_by_the_power_page(self):
        """正式校验按页面归属拆分：供电分配/计划/漏极的问题归独立电源页。"""
        page = ConfigurationPage(
            {
                "test_frequencies": [2.4],
                "signal_source": {"start_power": -10.0, "stop_power": 0.0, "step": 1.0},
                "compression_point": {"type": "5dB"},
                "attenuator": {"type": "30dB"},
                "driver_mode": {"enabled": False},
                "dut_config": {"max_input_power": 30.0, "power_supply_count": 1},
                "instruments": {
                    "signal_generator": {
                        "address": "sim::sg",
                        "model": "SG",
                        "enabled": True,
                    },
                    "spectrum_analyzer": {
                        "address": "sim::sa",
                        "model": "SA",
                        "enabled": True,
                    },
                    "power_supplies": {
                        "DP8B1519": {
                            "address": "sim::ps1",
                            "model": "DP832A",
                            "enabled": True,
                            "channels": {"CH1": {"voltage": {"value": 2.8}}},
                        }
                    },
                },
                "device_bindings": {
                    "signal_generator": {"visa_address": "sim::sg"},
                    "spectrum_analyzer": {"visa_address": "sim::sa"},
                    "power_supplies": {
                        "DP8B1519": {
                            "visa_address": "sim::ps1",
                            "model": "DP832A",
                            "device_key": "rigol technologies:dp8b214901519",
                            "full_code": "DP8B214901519",
                        }
                    },
                },
                # 引用一台没有已应用设备的电源：这是电源页的问题，不应由参数页报告。
                "power_supply_assignment": {
                    "dut_amplifier": {
                        "power_plan": {
                            "power_sequence": [
                                {"supply_id": "PS9", "channel": "CH1"}
                            ],
                            "inter_channel_delay_s": 0.0,
                        },
                    }
                },
            }
        )
        page.show()
        self.app.processEvents()
        failures = []
        page.validation_failed.connect(failures.append)

        allowed = page.validate()

        self.assertTrue(allowed)
        self.assertEqual(failures, [])

    def test_validation_still_reports_parameter_errors_on_the_parameter_page(self):
        page = ConfigurationPage({"test_frequencies": [], "instruments": {}})
        page.show()
        self.app.processEvents()
        failures = []
        page.validation_failed.connect(failures.append)

        allowed = page.validate()

        self.assertFalse(allowed)
        self.assertIn("频率", failures[0])

    def test_build_config_keeps_the_power_section_owned_by_the_other_page(self):
        """参数页只读测试参数；电源字段由独立电源页提交，不被本页清空。"""
        assignment = {
            "dut_amplifier": {
                "power_plan": {
                    "power_sequence": [{"supply_id": "DP8B1519", "channel": "CH1"}],
                    "inter_channel_delay_s": 1.0,
                },
            }
        }
        page = self.make_page(
            dut_config={
                "power_supply_count": 1,
                "drain_channel": {"supply_id": "DP8B1519", "channel": "CH1"},
            },
            power_supply_assignment=assignment,
        )

        built = page.build_config()

        self.assertEqual(built["power_supply_assignment"], assignment)
        self.assertEqual(
            built["dut_config"]["drain_channel"],
            {"supply_id": "DP8B1519", "channel": "CH1"},
        )

    def test_frequency_sweep_controls_generate_the_saved_list(self):
        page = self.make_page()

        page.freq_start.setValue(2.3)
        page.freq_stop.setValue(2.5)
        page.freq_step.setValue(0.1)
        page.freq_extra.setText("2.4")

        self.assertEqual(
            page.build_config()["test_frequencies"], [2.3, 2.4, 2.5]
        )
        self.assertIn("3 个频点", page.freq_preview.text())

    def test_non_uniform_loaded_frequencies_are_rejected_without_rewriting_the_list(self):
        config = {
            "test_frequencies": [4.0, 4.4, 4.8, 5.2, 5.6, 5.8],
            "instruments": {"power_supplies": {}},
        }
        page = ConfigurationPage(config)
        page.show()
        self.app.processEvents()

        self.assertIn("不是严格等步长", page.freq_preview.text())
        self.assertFalse(page.validate())
        # 原列表仍留在基线文档里，页面没有把它猜成另一组频点。
        self.assertEqual(
            page.build_config()["test_frequencies"], config["test_frequencies"]
        )

    def test_invalid_frequency_prevents_save_request(self):
        page = self.make_page()
        calls = []
        page.save_requested.connect(lambda: calls.append(True))
        page.freq_extra.setText("not-a-number")
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
        self.assertNotIn("wiring", result)

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
        """行编辑框盖住下按钮时，落在下箭头上的点击仍应减一。"""
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

        from presentation.qt.theme import PAGE_CONTENT_MAX_WIDTH

        page = self.make_page()
        page.resize(2400, 900)
        self.app.processEvents()
        widths = [box.width() for box in page.findChildren(QGroupBox) if box.isVisible()]
        self.assertTrue(widths)
        # 分组框不随窗口宽度无限拉伸：内容宽度由跨页面共用的主题令牌收口。
        self.assertLessEqual(max(widths), PAGE_CONTENT_MAX_WIDTH)

    def test_instrument_connection_is_its_own_page(self):
        """仪表配置已独立成整屏页面：配置页只留“上一步”入口与最终动作。"""
        page = self.make_page()
        # 面板只归连接页所有：配置页既不创建也不登记它。
        self.assertFalse(hasattr(page, "discovery_panel"))
        self.assertFalse(hasattr(page, "attach_discovery_panel"))
        steps = []
        page.connection_step_requested.connect(lambda: steps.append(True))

        page.previous_step_btn.click()

        self.assertEqual(steps, [True])
        self.assertFalse(hasattr(page, "sg_address"))
        # 旧连接入口（按钮、信号、请求方法）都已删除；连接只由最终按钮发起。
        self.assertFalse(hasattr(page, "connect_btn"))
        self.assertFalse(hasattr(page, "connect_requested"))
        self.assertFalse(hasattr(page, "request_connect"))
        self.assertEqual(page.finalize_run_btn.text(), "完成配置并连接仪器")
        self.assertIn(
            "冻结待提交运行",
            page.finalize_run_btn.toolTip(),
            "最终按钮文案必须说明连接成功前运行只是待提交对象",
        )

    def test_reconnect_mode_only_changes_the_final_button_semantics(self):
        page = self.make_page()

        page.set_reconnect_required(True)

        self.assertEqual(page.finalize_run_btn.text(), "重新连接仪器")
        self.assertIn("不创建新运行", page.finalize_run_btn.toolTip())

        page.set_reconnect_required(False)

        self.assertEqual(page.finalize_run_btn.text(), "完成配置并连接仪器")

    def test_finalizing_disables_the_page_and_restores_previous_states(self):
        """最终动作只临时禁用控件，退出时恢复各控件原来的可用状态。"""
        page = self.make_page()
        page.save_config_btn.setEnabled(False)

        page.set_finalizing(True)

        self.assertFalse(page.finalize_run_btn.isEnabled())
        self.assertFalse(page.test_parameters_group.isEnabled())
        self.assertFalse(page.previous_step_btn.isEnabled())

        page.set_finalizing(False)

        self.assertTrue(page.finalize_run_btn.isEnabled())
        self.assertTrue(page.test_parameters_group.isEnabled())
        self.assertTrue(page.previous_step_btn.isEnabled())
        self.assertFalse(page.save_config_btn.isEnabled(), "进入前就禁用的控件保持禁用")

    def test_finalize_request_is_ignored_while_a_finalize_is_running(self):
        page = self.make_page()
        requested = []
        page.finalize_run_requested.connect(lambda: requested.append(True))

        page.set_finalizing(True)
        page.request_finalize_run()

        self.assertEqual(requested, [])


class ConfigurationPageViewModelTests(unittest.TestCase):
    """步骤 6：页面业务状态由 ConfigurationViewModel 持有，页面只按状态渲染。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_page(self, **extra):
        config = {"test_frequencies": [1.0], "instruments": {"power_supplies": {}}}
        config.update(extra)
        page = ConfigurationPage(config)
        page.show()
        self.app.processEvents()
        return page

    def test_test_parameters_are_loaded_into_the_view_model(self):
        page = self.make_page()

        state = page.view_model.state
        self.assertEqual([frequency.ghz for frequency in state.draft.frequencies], [1.0])
        self.assertFalse(state.dirty)

    def test_test_edits_mark_the_view_model_dirty_and_mark_clean_resets(self):
        page = self.make_page()
        self.assertFalse(page.is_dirty())

        page.freq_start.setValue(2.4)
        page.freq_stop.setValue(2.6)
        page.freq_step.setValue(0.2)
        self.assertTrue(page.is_dirty())
        self.assertEqual(
            [frequency.ghz for frequency in page.view_model.state.draft.frequencies],
            [2.4, 2.6],
        )

        page.mark_clean()
        self.assertFalse(page.is_dirty())
        self.assertFalse(page.view_model.dirty)

    def test_invalid_frequency_is_reported_by_the_page_and_does_not_guess(self):
        page = self.make_page()
        page.freq_extra.setText("not-a-frequency")

        self.assertFalse(page.validate())

        state = page.view_model.state
        self.assertEqual(state.draft.frequencies, ())
        self.assertTrue(state.dirty)
        # 页面片段只带转换成功的字段；空频率仍会被正式校验拦下。
        self.assertFalse(page.view_model.validate().valid)

    def test_run_status_is_rendered_from_the_view_model(self):
        page = self.make_page()

        page.set_run_status("当前运行: run-20261102")

        self.assertEqual(page.run_status_label.text(), "当前运行: run-20261102")
        self.assertEqual(
            page.view_model.state.run_status, "当前运行: run-20261102"
        )

    def test_applying_devices_writes_the_binding_into_the_view_model(self):
        page = self.make_page()
        assignment = {
            "role": "power_supply",
            "supply_id": "DP8B1519",
            "label": "DP8B1519",
            "address": "TCPIP0::192.168.1.102::inst0::INSTR",
            "vendor": "RIGOL TECHNOLOGIES",
            "model": "DP832A",
            "full_code": "DP8B214901519",
            "device_key": "rigol technologies:dp8b214901519",
            "ip": "192.168.1.102",
            "port": 5555,
            "known": True,
        }

        self.assertTrue(apply_devices(page, [assignment]).ok)
        self.assertEqual(
            page.view_model.state.bindings_snapshot(), page.device_bindings
        )
        self.assertEqual(list(page.device_bindings["power_supplies"]), ["DP8B1519"])

    def test_a_rejected_device_selection_leaves_the_view_model_unchanged(self):
        page = self.make_page()
        before = page.view_model.state

        self.assertFalse(apply_devices(page, []).ok)

        self.assertEqual(
            page.view_model.state.bindings_snapshot(), before.bindings_snapshot()
        )
        self.assertFalse(page.view_model.dirty)

    def test_backfill_resets_the_view_model_to_a_clean_state(self):
        page = self.make_page()
        page.freq_start.setValue(9.9)
        self.assertTrue(page.is_dirty())

        page.apply_config(
            {
                "test_frequencies": [3.5],
                "instruments": {"power_supplies": {}},
            }
        )

        self.assertFalse(page.is_dirty())
        self.assertEqual(page.freq_start.value(), 3.5)
        self.assertEqual(page.freq_stop.value(), 3.5)
        self.assertEqual(page.freq_step.value(), 0.4)
        self.assertEqual(page.freq_extra.text(), "")
        state = page.view_model.state
        self.assertEqual([frequency.ghz for frequency in state.draft.frequencies], [3.5])
        self.assertFalse(state.dirty)


if __name__ == "__main__":
    unittest.main()
