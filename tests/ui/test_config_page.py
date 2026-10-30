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

    @staticmethod
    def power_supply_bindings(*supply_ids):
        """按给定短标签造一份电源绑定集合（每台各带地址与身份）。"""
        return {
            "power_supplies": {
                supply_id: {
                    "device_key": f"rigol technologies:{supply_id.casefold()}",
                    "visa_address": f"TCPIP0::192.168.1.{index}::inst0::INSTR",
                }
                for index, supply_id in enumerate(supply_ids, 1)
            }
        }

    def test_unbound_power_reference_does_not_select_another_supply(self):
        """载入配置时旧引用失效就保持空选择，不能停在下拉框第一项。"""
        page = self.make_page(
            device_bindings=self.power_supply_bindings("DP8B0885"),
            dut_config={"power_supply_count": 1},
            power_supply_assignment={
                "dut_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {"carrier": {"name": "DP8B1519", "channel": ["CH1", "CH2"]}},
                }
            },
        )

        self.assertEqual(page.pa_unit1_power_combo.currentText(), "")
        self.assertEqual(page.pa_unit1_power_combo.currentIndex(), -1)
        # 保存的是“引用未绑定”，不是另一台电源。
        entry = page.build_config()["power_supply_assignment"]["dut_amplifier"][
            "supplies"
        ]["carrier"]
        self.assertIsNone(entry["supply_id"])

    def test_power_assignment_reference_is_read_from_supply_id(self):
        """读配置时 ``supply_id`` 优先，旧字段 ``name`` 不参与选择。"""
        page = self.make_page(
            device_bindings=self.power_supply_bindings("DP8B1519", "DP8B0885"),
            dut_config={"power_supply_count": 1},
            power_supply_assignment={
                "dut_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {
                        "carrier": {
                            "supply_id": "DP8B0885",
                            "name": "DP8B1519",
                            "channel": ["CH1", "CH2"],
                        }
                    },
                }
            },
        )

        self.assertEqual(page.pa_unit1_power_combo.currentData(), "DP8B0885")

    def test_power_assignment_options_are_the_bound_supplies(self):
        """下拉框只列本次绑定的电源：1 台、2 台、5 台都只是集合长度不同。"""
        for supply_ids in (
            ("DP8B1519",),
            ("DP8B1519", "DP8B0885"),
            ("DP8B1519", "DP8B0885", "DP8B0950", "DP8B1191", "DP8B1216"),
        ):
            with self.subTest(count=len(supply_ids)):
                page = self.make_page(
                    device_bindings=self.power_supply_bindings(*supply_ids),
                    dut_config={"power_supply_count": 1},
                )
                options = [
                    page.pa_unit1_power_combo.itemText(index)
                    for index in range(page.pa_unit1_power_combo.count())
                ]
                self.assertEqual(options, list(supply_ids))
                self.assertEqual(page.get_enabled_power_supplies(), list(supply_ids))

    def test_explicitly_unbound_reference_stays_empty(self):
        """配置明确写 ``supply_id: null`` 时不能按缺省规则补上第一台可用电源。"""
        page = self.make_page(
            device_bindings=self.power_supply_bindings("DP8B0885"),
            dut_config={"power_supply_count": 1},
            power_supply_assignment={
                "dut_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {
                        "carrier": {"supply_id": None, "channel": ["CH1", "CH2"]}
                    },
                }
            },
        )

        self.assertEqual(page.pa_unit1_power_combo.currentIndex(), -1)
        self.assertEqual(page.pa_unit1_power_combo.currentText(), "")
        entry = page.build_config()["power_supply_assignment"]["dut_amplifier"][
            "supplies"
        ]["carrier"]
        self.assertIsNone(entry["supply_id"])
        self.assertNotIn("name", entry)

    def test_missing_assignment_defaults_to_the_first_bound_supply(self):
        """配置里没有这条分配时按缺省规则选第一台可用电源（缺失 ≠ 未绑定）。"""
        page = self.make_page(
            device_bindings=self.power_supply_bindings("DP8B1519", "DP8B0885"),
            dut_config={"power_supply_count": 1},
        )

        self.assertEqual(page.pa_unit1_power_combo.currentData(), "DP8B1519")

    def test_applied_config_keeps_an_explicitly_unbound_reference_empty(self):
        """回填路径（apply_config）与初次建页共用同一判定：显式 null 仍是空选择。"""
        page = self.make_page()

        page.apply_config(
            {
                "test_frequencies": [1.0],
                "instruments": {"power_supplies": {}},
                "device_bindings": self.power_supply_bindings("DP8B0885"),
                "driver_mode": {"enabled": True, "power_source": "dc_supply"},
                "dut_config": {"power_supply_count": 1},
                "power_supply_assignment": {
                    "driver_amplifier": {
                        "power_supply_count": 1,
                        "supplies": {"main": {"supply_id": None}},
                    },
                    "dut_amplifier": {
                        "power_supply_count": 1,
                        "supplies": {"carrier": {"supply_id": None}},
                    },
                },
            }
        )

        self.assertEqual(page.pa_unit1_power_combo.currentIndex(), -1)
        self.assertEqual(page.driver_power_combo.currentIndex(), -1)

    def test_applied_config_reference_follows_supply_id_not_name(self):
        """回填时 ``supply_id`` 为准：与之冲突的旧字段 ``name`` 不参与选择。"""
        page = self.make_page()

        def applied(carrier):
            page.apply_config(
                {
                    "test_frequencies": [1.0],
                    "instruments": {"power_supplies": {}},
                    "device_bindings": self.power_supply_bindings(
                        "DP8B1519", "DP8B0885"
                    ),
                    "dut_config": {"power_supply_count": 1},
                    "power_supply_assignment": {
                        "dut_amplifier": {
                            "power_supply_count": 1,
                            "supplies": {"carrier": carrier},
                        }
                    },
                }
            )
            return page.pa_unit1_power_combo.currentData() or ""

        # supply_id 指向已绑定电源：即使 name 是另一台，也选 supply_id 指定的那台。
        self.assertEqual(applied({"supply_id": "DP8B0885", "name": "DP8B1519"}), "DP8B0885")
        # 反过来：supply_id 指向未绑定的电源时保持空选择，不被 name 改指另一台。
        self.assertEqual(applied({"supply_id": "DP8B9999", "name": "DP8B1519"}), "")

    def test_power_assignment_shows_the_label_with_model_and_keeps_supply_id_as_data(self):
        """步骤 9：下拉显示 ``DP8B1519（DP832A）``，``itemData`` 保存 ``supply_id``。"""
        bindings = self.power_supply_bindings("DP8B1519", "DP8B0885")
        bindings["power_supplies"]["DP8B1519"]["model"] = "DP832A"
        bindings["power_supplies"]["DP8B0885"]["model"] = "DP832A"
        page = self.make_page(
            device_bindings=bindings, dut_config={"power_supply_count": 1}
        )

        combo = page.pa_unit1_power_combo
        self.assertEqual(
            [combo.itemText(index) for index in range(combo.count())],
            ["DP8B1519（DP832A）", "DP8B0885（DP832A）"],
        )
        # 两台型号相同、显示文本只差短标签：业务值必须来自 itemData。
        self.assertEqual(
            [combo.itemData(index) for index in range(combo.count())],
            ["DP8B1519", "DP8B0885"],
        )
        self.assertEqual(combo.currentData(), "DP8B1519")

    def test_saving_reads_the_supply_id_from_item_data_not_the_display_text(self):
        """保存的引用来自 ``itemData``：显示文本带型号（含全角括号）也不会被写进配置。"""
        bindings = self.power_supply_bindings("DP8B1519", "DP8B0885")
        bindings["power_supplies"]["DP8B0885"]["model"] = "DP832A"
        page = self.make_page(
            device_bindings=bindings,
            dut_config={"power_supply_count": 1},
            power_supply_assignment={
                "dut_amplifier": {"supplies": {"carrier": {"supply_id": "DP8B0885"}}}
            },
        )
        self.assertEqual(page.pa_unit1_power_combo.currentText(), "DP8B0885（DP832A）")

        entry = page.build_config()["power_supply_assignment"]["dut_amplifier"][
            "supplies"
        ]["carrier"]

        self.assertEqual(entry["supply_id"], "DP8B0885")
        self.assertEqual(entry["name"], "DP8B0885")

    def test_refreshing_the_options_keeps_the_selection_by_supply_id(self):
        """刷新选项后仍停在原来那台电源上：按数据恢复，不按位置也不按显示文本。"""
        page = self.make_page(
            device_bindings=self.power_supply_bindings("DP8B1519", "DP8B0885"),
            dut_config={"power_supply_count": 1},
            power_supply_assignment={
                "dut_amplifier": {"supplies": {"carrier": {"supply_id": "DP8B0885"}}}
            },
        )
        self.assertEqual(page.pa_unit1_power_combo.currentData(), "DP8B0885")

        page.update_power_supply_options()

        self.assertEqual(page.pa_unit1_power_combo.currentData(), "DP8B0885")

    def test_refreshing_after_a_supply_was_removed_clears_only_that_slot(self):
        """某台电源不再绑定时，只清掉引用它的槽位，其它槽位保持原选择。"""
        page = self.make_page(
            device_bindings=self.power_supply_bindings("DP8B1519", "DP8B0885"),
            dut_config={"power_supply_count": 2},
            driver_mode={"enabled": True, "power_source": "dc_supply"},
            power_supply_assignment={
                "driver_amplifier": {"supplies": {"main": {"supply_id": "DP8B1519"}}},
                "dut_amplifier": {
                    "power_supply_count": 2,
                    "supplies": {
                        "carrier": {"supply_id": "DP8B0885"},
                        "peaking": {"supply_id": "DP8B0885"},
                    },
                },
            },
        )
        page.device_bindings = self.power_supply_bindings("DP8B1519")

        page.update_power_supply_options()

        self.assertEqual(page.driver_power_combo.currentData(), "DP8B1519")
        self.assertEqual(page.pa_unit1_power_combo.currentIndex(), -1)
        self.assertEqual(page.pa_unit2_power_combo.currentIndex(), -1)

    def test_power_tabs_are_titled_with_the_label_and_model(self):
        """电源标签页按配置与绑定动态创建，标题是 ``短标签（型号）``。"""
        bindings = self.power_supply_bindings("DP8B1519", "DP8B0885")
        bindings["power_supplies"]["DP8B0885"]["model"] = "DP832A"
        page = self.make_page(
            device_bindings=bindings,
            instruments={"power_supplies": {"DP8B1519": {"model": "DP832A"}}},
            dut_config={"power_supply_count": 1},
        )

        titles = [
            page.power_tabs.tabText(index) for index in range(page.power_tabs.count())
        ]
        self.assertEqual(titles, ["DP8B1519（DP832A）", "DP8B0885（DP832A）"])
        self.assertEqual(set(page.power_config_widgets), {"DP8B1519", "DP8B0885"})

    def test_power_tab_tooltips_carry_the_full_identity_for_audit(self):
        """标签页悬浮提示给详情与审计：短标签 · 型号 · 完整序列号 · 设备键。

        卡面标题只有 ``短标签（型号）``（标题栏很窄），追溯时把指针停在标签页上就能读到
        这台设备的完整序列号；两处文本同源（``domain.instruments.labels``），不会各说一套。
        """
        bindings = self.power_supply_bindings("DP8B1519")
        bindings["power_supplies"]["DP8B1519"].update(
            {
                "model": "DP832A",
                "vendor": "Rigol Technologies",
                "serial": "DP8B214901519",
                "device_key": "rigol technologies:dp8b214901519",
            }
        )
        page = self.make_page(
            device_bindings=bindings,
            instruments={"power_supplies": {}},
            dut_config={"power_supply_count": 1},
        )

        self.assertEqual(page.power_tabs.tabText(0), "DP8B1519（DP832A）")
        self.assertEqual(
            page.power_tabs.tabToolTip(0),
            "DP8B1519（DP832A） · 序列号 DP8B214901519"
            " · 设备键 rigol technologies:dp8b214901519",
        )

    def test_old_slot_entry_tooltip_does_not_invent_a_label(self):
        """旧槽位条目按旧字段显示：标题与提示都只有 ``PS1``，没有序列号也没有新标签。"""
        page = self.make_page(
            instruments={"power_supplies": {"PS1": {"model": "PS1", "channels": {}}}},
            dut_config={"power_supply_count": 1},
        )

        self.assertEqual(page.power_tabs.tabText(0), "PS1")
        self.assertEqual(page.power_tabs.tabToolTip(0), "PS1")
        self.assertNotIn("序列号", page.power_tabs.tabToolTip(0))

    def test_power_tabs_follow_the_bound_supply_count(self):
        """电源数量变化时页面动态增减标签页：1、2、5 台都不需要改常量。"""
        for supply_ids in (
            ("DP8B1519",),
            ("DP8B1519", "DP8B0885"),
            ("DP8B1519", "DP8B0885", "DP8B0950", "DP8B1191", "DP8B1216"),
        ):
            with self.subTest(count=len(supply_ids)):
                page = self.make_page(
                    device_bindings=self.power_supply_bindings(*supply_ids),
                    dut_config={"power_supply_count": 1},
                )
                self.assertEqual(page.power_tabs.count(), len(supply_ids))
                self.assertEqual(
                    list(page.power_config_widgets), list(supply_ids)
                )

    def test_power_tabs_are_indexed_by_key_not_by_position(self):
        """回填按电源键建页：两台电源各自的通道参数不会互换。"""
        page = self.make_page()
        page.apply_config(
            {
                "test_frequencies": [1.0],
                "instruments": {
                    "power_supplies": {
                        "DP8B1519": {
                            "model": "DP832A",
                            "channels": {"CH1": {"voltage": {"value": 12.0}}},
                        },
                        "DP8B0885": {
                            "model": "DP832A",
                            "channels": {"CH1": {"voltage": {"value": 3.3}}},
                        },
                    }
                },
                "device_bindings": self.power_supply_bindings(
                    "DP8B1519", "DP8B0885"
                ),
                "dut_config": {"power_supply_count": 1},
            }
        )

        self.assertEqual(
            page.power_config_widgets["DP8B1519"]["channels"]["CH1"]["voltage"].value(),
            12.0,
        )
        self.assertEqual(
            page.power_config_widgets["DP8B0885"]["channels"]["CH1"]["voltage"].value(),
            3.3,
        )

    def test_rebuilding_power_tabs_keeps_channel_values_and_the_selected_supply(self):
        """重建（如再次应用绑定）不丢通道参数，且仍停留在原来那台电源的标签页。

        回填（``preserve=False``）本来就按新配置重建控件，这里覆盖的是“电源数量变化但
        用户刚填的值还要保留”的重建路径。
        """
        page = self.make_page(
            device_bindings=self.power_supply_bindings("DP8B1519", "DP8B0885"),
            dut_config={"power_supply_count": 1},
        )
        page.power_config_widgets["DP8B0885"]["channels"]["CH2"]["voltage"].setValue(28.0)
        page.power_tabs.setCurrentIndex(
            page.power_tabs.indexOf(page.power_config_widgets["DP8B0885"]["page"])
        )
        self.assertIs(page._current_power_tab_key(), "DP8B0885")

        page._rebuild_power_tabs(preserve=True)

        self.assertEqual(
            page.power_config_widgets["DP8B0885"]["channels"]["CH2"]["voltage"].value(),
            28.0,
        )
        self.assertEqual(page._current_power_tab_key(), "DP8B0885")

    def test_rebuilding_keeps_values_the_operator_just_edited(self):
        """配置里已有通道参数时，``preserve=True`` 的重建仍以控件里刚填的值为准。

        刷新发生在保存之前：如果重建无条件回到配置里的旧值，操作者刚改的参数会被悄悄
        改回去，这正是“刷新不丢通道参数”要避免的情况。
        """
        page = self.make_page(
            device_bindings=self.power_supply_bindings("DP8B1519", "DP8B0885"),
            instruments={
                "power_supplies": {
                    "DP8B1519": {"channels": {"CH1": {"voltage": {"value": 5.0}}}},
                    "DP8B0885": {"channels": {"CH1": {"voltage": {"value": 9.0}}}},
                }
            },
            dut_config={"power_supply_count": 1},
        )
        self.assertEqual(
            page.power_config_widgets["DP8B0885"]["channels"]["CH1"]["voltage"].value(),
            9.0,
        )
        page.power_config_widgets["DP8B0885"]["channels"]["CH1"]["voltage"].setValue(15.0)
        page.power_config_widgets["DP8B0885"]["channels"]["CH1"][
            "current_protection_enabled"
        ].setChecked(True)

        page._rebuild_power_tabs(preserve=True)

        widgets = page.power_config_widgets
        self.assertEqual(widgets["DP8B0885"]["channels"]["CH1"]["voltage"].value(), 15.0)
        self.assertTrue(
            widgets["DP8B0885"]["channels"]["CH1"][
                "current_protection_enabled"
            ].isChecked()
        )
        # 参数按电源键合并，另一台电源的值不会串过来，也不会被这次重建改掉。
        self.assertEqual(widgets["DP8B1519"]["channels"]["CH1"]["voltage"].value(), 5.0)

    def test_backfill_rebuild_uses_the_new_configuration_not_the_old_widgets(self):
        """``preserve=False`` 是回填路径：结果等于新配置，不沿用重建前的控件值。"""
        page = self.make_page(
            device_bindings=self.power_supply_bindings("DP8B1519"),
            instruments={
                "power_supplies": {"DP8B1519": {"channels": {"CH1": {"voltage": {"value": 5.0}}}}}
            },
            dut_config={"power_supply_count": 1},
        )
        page.power_config_widgets["DP8B1519"]["channels"]["CH1"]["voltage"].setValue(20.0)

        page._rebuild_power_tabs(
            sources={
                "DP8B1519": {"channels": {"CH1": {"voltage": {"value": 3.3}}}}
            },
            preserve=False,
        )

        self.assertEqual(
            page.power_config_widgets["DP8B1519"]["channels"]["CH1"]["voltage"].value(),
            3.3,
        )

    def test_pa_unit_count_and_power_supply_count_stay_separate(self):
        """PA 单元数量只控制分配槽位可见性与 DUT 分配条数，不增减电源标签页。"""
        page = self.make_page(
            device_bindings=self.power_supply_bindings("DP8B1519", "DP8B0885"),
            dut_config={"power_supply_count": 1},
        )
        self.assertEqual(page.power_tabs.count(), 2)

        page.pa_unit_count_combo.setCurrentText("3")

        self.assertEqual(page.power_tabs.count(), 2)
        built = page.build_config()["dut_config"]
        self.assertEqual(built["power_supply_count"], 3)

    def test_legacy_slot_supplies_still_get_their_own_tabs(self):
        """尚未迁移的旧槽位条目各占一页（参数保留），并被保存校验明确报告。"""
        page = self.make_page(
            instruments={
                "power_supplies": {
                    "PS1": {"channels": {"CH1": {"voltage": {"value": 5.0}}}}
                }
            },
            dut_config={"power_supply_count": 1},
        )

        self.assertEqual(page.power_tabs.count(), 1)
        self.assertEqual(
            page.power_config_widgets["PS1"]["channels"]["CH1"]["voltage"].value(), 5.0
        )
        # 旧槽位不是可用的电源引用：分配不会选中它，校验也不会凭空补一台。
        self.assertEqual(page.pa_unit1_power_combo.currentIndex(), -1)

    def test_power_tabs_keep_the_configuration_of_removed_supplies(self):
        """再次应用绑定后标签页跟着新绑定走，但配置里已保存的另一台参数不被丢掉。"""
        page = self.make_page(
            device_bindings=self.power_supply_bindings("DP8B1519", "DP8B0885"),
            instruments={
                "power_supplies": {
                    "DP8B1519": {},
                    "DP8B0885": {"channels": {"CH1": {"voltage": {"value": 9.0}}}},
                }
            },
            dut_config={"power_supply_count": 1},
        )

        def assignment(supply_id, index):
            return {
                "role": "power_supply",
                "supply_id": supply_id,
                "name": supply_id,
                "model": "DP832A",
                "serial": f"DP8B{index}",
                "vendor": "RIGOL Technologies",
                "address": f"TCPIP0::192.168.1.{index}::inst0::INSTR",
                "ip": f"192.168.1.{index}",
            }

        page.apply_discovered_devices([assignment("DP8B1519", 1)])

        # 本次绑定的电源有当前参数页；已保存的另一台仍是“标签页 + 参数”，只是地址
        # 随绑定失效——这样“清除绑定”不会抹掉现场已调试好的通道参数。
        self.assertEqual(
            page.power_config_widgets["DP8B0885"]["channels"]["CH1"]["voltage"].value(),
            9.0,
        )
        # 分配下拉框只列本次绑定的电源；引用了被移除的那台时保持空选择。
        options = [
            page.pa_unit1_power_combo.itemData(index)
            for index in range(page.pa_unit1_power_combo.count())
        ]
        self.assertEqual(options, ["DP8B1519"])

    def _assignment_selections(self, page):
        """读出四个电源分配槽位当前选中的 ``supply_id``，用于两条回填路径的一致性比对。"""
        return {
            key: page._combo_supply_id(page.power_assignment_widgets[key])
            for key in (
                "driver_power",
                "pa_unit1_power",
                "pa_unit2_power",
                "pa_unit3_power",
            )
        }

    def test_missing_driver_assignment_uses_the_same_default_on_both_paths(self):
        """驱动功放与 PA 单元共用缺省规则：初次建页与 apply_config 结果必须一致。"""
        config = {
            "test_frequencies": [1.0],
            "instruments": {"power_supplies": {}},
            "device_bindings": self.power_supply_bindings("DP8B0885", "DP8B0950"),
            "driver_mode": {"enabled": True, "power_source": "dc_supply"},
            "dut_config": {"power_supply_count": 1},
        }

        built = self._assignment_selections(self.make_page(**config))
        page = self.make_page()
        page.apply_config(config)

        self.assertEqual(built["driver_power"], "DP8B0885")
        self.assertEqual(self._assignment_selections(page), built)

    def test_applied_config_does_not_reuse_the_previous_selection(self):
        """投影不受上一次控件残留值影响：只按当前绑定集合取第一台。"""
        page = self.make_page()

        page.apply_config(
            {
                "test_frequencies": [1.0],
                "instruments": {"power_supplies": {}},
                "device_bindings": self.power_supply_bindings("DP8B1519", "DP8B0885"),
                "dut_config": {"power_supply_count": 1},
                "power_supply_assignment": {
                    "dut_amplifier": {
                        "power_supply_count": 1,
                        "supplies": {"carrier": {"supply_id": "DP8B0885"}},
                    }
                },
            }
        )
        self.assertEqual(page.pa_unit1_power_combo.currentData(), "DP8B0885")
        # 用户手动换过选择，随后载入一份没有这条分配的配置：不能沿用 DP8B0885。
        page.pa_unit1_power_combo.setCurrentIndex(
            page.pa_unit1_power_combo.findData("DP8B1519")
        )

        page.apply_config(
            {
                "test_frequencies": [1.0],
                "instruments": {"power_supplies": {}},
                "device_bindings": self.power_supply_bindings("DP8B0950", "DP8B0885"),
                "dut_config": {"power_supply_count": 1},
            }
        )

        self.assertEqual(page.pa_unit1_power_combo.currentData(), "DP8B0950")

    def test_external_driver_power_still_clears_the_driver_slot(self):
        """统一缺省规则不改变页面状态：外部供电时驱动功放槽位仍被清空并禁用。"""
        config = {
            "test_frequencies": [1.0],
            "instruments": {"power_supplies": {}},
            "device_bindings": self.power_supply_bindings("DP8B0885"),
            "driver_mode": {"enabled": True, "power_source": "external_ac"},
            "dut_config": {"power_supply_count": 1},
        }

        page = self.make_page(**config)
        page.set_test_mode(True)

        self.assertEqual(page.driver_power_source_combo.currentText(), "自带供电")
        self.assertEqual(page.driver_power_combo.currentIndex(), -1)
        self.assertFalse(page.driver_power_combo.isEnabled())
        # PA 单元不受驱放供电方式影响。
        self.assertEqual(page.pa_unit1_power_combo.currentData(), "DP8B0885")


if __name__ == "__main__":
    unittest.main()
