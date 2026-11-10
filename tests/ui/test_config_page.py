import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel

from application.device_binding import ApplyDeviceSelectionRequest
from presentation.qt.power_sweep_plan_row import PowerSweepPlanRow
from presentation.qt.test_configuration_page import TestConfigurationPage


def _destroy_page(page):
    """按离屏用例的收尾规则销毁顶层页面，避免控件在后续用例中累积。"""
    page.close()
    page.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


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
        page = TestConfigurationPage(config)
        page.show()
        self.addCleanup(_destroy_page, page)
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

    def test_power_labels_describe_dut_input_power_plans(self):
        """步骤 3.2：功率扫描改为按段编辑，标签仍明确是 DUT 输入功率。"""
        page = self.make_page()

        texts = {label.text() for label in page.findChildren(QLabel)}

        self.assertIn("DUT功率扫描计划", texts)
        self.assertIn("起始功率 (dBm)", texts)
        self.assertIn("步长 (dB)", texts)
        self.assertIn("终止功率 (dBm)", texts)
        self.assertIn("驱动功放预估增益 (dB)", texts)
        self.assertIn("DUT 输入功率", page.start_power.toolTip())

    def test_empty_config_starts_with_one_default_power_scan_plan(self):
        page = self.make_page()

        self.assertEqual(len(page.power_scan_plan_rows), 1)
        row = page.power_scan_plan_rows[0]
        self.assertEqual(row.values(), (-38.0, 1.0, -16.0))
        self.assertEqual(row.number(), 1)
        self.assertFalse(
            row.delete_button.isEnabled(), "至少保留一行，不能把唯一的计划删掉"
        )
        self.assertFalse(page.remove_power_scan_plan(row))
        self.assertEqual(len(page.power_scan_plan_rows), 1)

    def test_add_power_scan_plan_continues_from_the_previous_stop(self):
        page = self.make_page()
        first = page.power_scan_plan_rows[0]
        first.set_values(-38.0, 1.0, -10.0)

        second = page.add_power_scan_plan()

        # 新段起点接续上一段终点，步长沿用，终点先等于起点：不凭空生成业务范围。
        self.assertEqual(second.values(), (-10.0, 1.0, -10.0))
        self.assertEqual([row.number() for row in page.power_scan_plan_rows], [1, 2])
        self.assertTrue(second.delete_button.isEnabled())

        third = page.add_power_scan_plan()
        self.assertEqual(third.values(), (-10.0, 1.0, -10.0))
        self.assertEqual(
            [row.number() for row in page.power_scan_plan_rows], [1, 2, 3]
        )

    def test_deleting_a_middle_plan_renumbers_and_keeps_the_other_rows(self):
        page = self.make_page()
        first = page.power_scan_plan_rows[0]
        first.set_values(-38.0, 1.0, -10.0)
        second = page.add_power_scan_plan()
        second.set_values(-10.0, 0.25, -2.0)
        third = page.add_power_scan_plan()
        third.set_values(-2.0, 0.1, 0.0)

        self.assertTrue(page.remove_power_scan_plan(second))

        self.assertEqual(
            [row.values() for row in page.power_scan_plan_rows],
            [(-38.0, 1.0, -10.0), (-2.0, 0.1, 0.0)],
        )
        self.assertEqual([row.number() for row in page.power_scan_plan_rows], [1, 2])
        self.assertEqual(
            page.build_config()["signal_source"]["power_scan_plans"],
            [
                {"start_power": -38.0, "step": 1.0, "stop_power": -10.0},
                {"start_power": -2.0, "step": 0.1, "stop_power": 0.0},
            ],
        )

    def test_add_button_appends_plans(self):
        page = self.make_page()

        page.add_power_scan_plan_btn.click()

        self.assertEqual(len(page.power_scan_plan_rows), 2)
        self.assertEqual(
            [row.number() for row in page.power_scan_plan_rows], [1, 2]
        )

    def test_rebuilding_plans_destroys_the_previous_rows(self):
        """整组回填后旧行被销毁：页面里不会累积上一次配置的动态控件。"""
        page = self.make_page()
        page.power_scan_plan_rows[0].set_values(-38.0, 1.0, -10.0)
        page.add_power_scan_plan()
        page.add_power_scan_plan()
        stale_names = set()
        for index, row in enumerate(page.power_scan_plan_rows, 1):
            name = f"stalePowerScanPlanRow{index}"
            row.setObjectName(name)
            stale_names.add(name)

        page.apply_config(
            {
                "test_frequencies": [1.0],
                "signal_source": {
                    "power_scan_plans": [
                        {"start_power": -20.0, "step": 0.5, "stop_power": -5.0}
                    ]
                },
            }
        )
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

        self.assertFalse(
            stale_names
            & {widget.objectName() for widget in QApplication.allWidgets()},
            "回填后旧计划行仍留在应用控件树里",
        )
        self.assertEqual(
            page.power_scan_plan_container.findChildren(PowerSweepPlanRow),
            page.power_scan_plan_rows,
        )

    def test_reset_restores_one_default_plan_and_refreshes_the_preview(self):
        page = self.make_page()
        page.power_scan_plan_rows[0].set_values(-38.0, 1.0, -10.0)
        page.add_power_scan_plan().set_values(-10.0, 0.25, -2.0)

        page.reset_power_scan_plans()

        self.assertEqual(len(page.power_scan_plan_rows), 1)
        row = page.power_scan_plan_rows[0]
        self.assertEqual(row.values(), (-38.0, 1.0, -16.0))
        self.assertEqual(row.number(), 1)
        self.assertFalse(row.delete_button.isEnabled())
        self.assertIn("共 23 点", row.preview.text())
        self.assertIn("共 23 个目标点", page.power_scan_summary.text())

    def test_power_scan_preview_updates_when_a_plan_field_changes(self):
        """步骤 3.3：改起始/步长/终止后，灰色点位预览立即按新值重算。"""
        page = self.make_page()
        row = page.power_scan_plan_rows[0]

        row.set_values(-38.0, 1.0, -10.0)
        self.assertIn("共 29 点", row.preview.text())

        row.step.setValue(2.0)
        self.assertIn("共 15 点", row.preview.text())

        row.stop_power.setValue(-9.0)
        self.assertIn("共 16 点", row.preview.text())

    def test_power_scan_preview_applies_the_dut_max_input_limit(self):
        page = self.make_page()
        page.max_input_power.setValue(3.0)
        row = page.power_scan_plan_rows[0]
        row.set_values(-38.0, 1.0, 10.0)

        self.assertIn("受 DUT 最大输入 3 dBm 限制", row.preview.text())
        self.assertIn("止于 3 dBm", row.preview.text())
        self.assertIn("42 个目标点", page.power_scan_summary.text())
        self.assertIn("受 DUT 最大输入 3 dBm 截断", page.power_scan_summary.text())

    def test_power_scan_summary_counts_the_merged_grid_once(self):
        page = self.make_page()
        first = page.power_scan_plan_rows[0]
        first.set_values(-38.0, 1.0, -10.0)
        second = page.add_power_scan_plan()
        second.set_values(-10.0, 0.25, -2.0)

        # 两段共享 -10 dBm，合并后只算一个目标点：29 + 33 - 1。
        self.assertIn("共 61 个目标点", page.power_scan_summary.text())
        self.assertIn("共 29 点", first.preview.text())
        self.assertIn("共 33 点", second.preview.text())
        self.assertIn("-38", page.power_scan_summary.text())
        self.assertIn("-2 dBm", page.power_scan_summary.text())

    def test_plan_gap_is_shown_on_the_next_row_and_blocks_saving(self):
        """空档标在后一段并汇总进 validation_failed；保存与运行入口都被阻止。"""
        page = self.make_page()
        first = page.power_scan_plan_rows[0]
        first.set_values(-38.0, 1.0, -10.0)
        second = page.add_power_scan_plan()
        second.set_values(-9.0, 0.25, -2.0)

        self.assertIn("空档", second.preview.text())
        self.assertIn("第 1 段", second.preview.text())
        self.assertIn("第 2 段", second.preview.text())
        self.assertEqual(second.preview.property("tone"), "warning")
        self.assertIn("1 个问题", page.power_scan_summary.text())

        failures = []
        saves = []
        finalizes = []
        page.validation_failed.connect(failures.append)
        page.save_requested.connect(lambda: saves.append(True))
        page.finalize_run_requested.connect(lambda: finalizes.append(True))

        page.request_save()
        page.request_finalize_run()

        self.assertEqual(saves, [])
        self.assertEqual(finalizes, [])
        self.assertIn("空档", "；".join(failures))

    def test_plan_overlap_is_shown_on_the_next_row(self):
        page = self.make_page()
        page.power_scan_plan_rows[0].set_values(-38.0, 1.0, -10.0)
        second = page.add_power_scan_plan()
        second.set_values(-11.0, 0.25, -2.0)

        self.assertIn("重叠", second.preview.text())
        self.assertIn("第 1 段", second.preview.text())
        self.assertIn("第 2 段", second.preview.text())

    def test_invalid_plan_replaces_the_old_preview_with_the_error(self):
        page = self.make_page()
        row = page.power_scan_plan_rows[0]
        row.set_values(-38.0, 1.0, -10.0)
        self.assertIn("共 29 点", row.preview.text())

        row.set_values(-5.0, 1.0, -10.0)

        self.assertIn("起始功率不能大于终止功率", row.preview.text())
        self.assertNotIn("共 ", row.preview.text())
        self.assertEqual(row.preview.property("tone"), "warning")

    def test_long_plan_preview_keeps_the_full_point_list_in_the_tooltip(self):
        page = self.make_page()
        row = page.power_scan_plan_rows[0]
        row.set_values(-38.0, 0.1, -10.0)

        self.assertIn("共 281 点", row.preview.text())
        self.assertIn("…", row.preview.text())
        self.assertIn("共 281 点", row.preview.toolTip())
        self.assertGreater(
            row.preview.toolTip().count("，"), row.preview.text().count("，")
        )

    def test_plan_preview_follows_backfill_and_row_removal(self):
        page = self.make_page()
        page.power_scan_plan_rows[0].set_values(-38.0, 1.0, -10.0)
        second = page.add_power_scan_plan()
        second.set_values(-10.0, 0.25, -2.0)

        page.apply_config(
            {
                "test_frequencies": [1.0],
                "signal_source": {
                    "power_scan_plans": [
                        {"start_power": -20.0, "step": 0.5, "stop_power": -5.0}
                    ]
                },
            }
        )

        self.assertEqual(len(page.power_scan_plan_rows), 1)
        self.assertIn("共 31 点", page.power_scan_plan_rows[0].preview.text())
        self.assertIn("共 31 个目标点", page.power_scan_summary.text())

        page.add_power_scan_plan()
        self.assertEqual(len(page.power_scan_plan_rows), 2)
        page.remove_power_scan_plan(page.power_scan_plan_rows[1])

        self.assertIn("共 31 点", page.power_scan_plan_rows[0].preview.text())

    def test_apply_config_restores_plans_in_order(self):
        page = self.make_page()
        plans = [
            {"start_power": -38.0, "step": 1.0, "stop_power": -10.0},
            {"start_power": -10.0, "step": 0.25, "stop_power": -2.0},
        ]

        page.apply_config(
            {"test_frequencies": [1.0], "signal_source": {"power_scan_plans": plans}}
        )

        self.assertEqual(
            [row.values() for row in page.power_scan_plan_rows],
            [(-38.0, 1.0, -10.0), (-10.0, 0.25, -2.0)],
        )
        self.assertEqual([row.number() for row in page.power_scan_plan_rows], [1, 2])
        self.assertIsNone(page.power_scan_import_error)
        self.assertEqual(
            page.build_config()["signal_source"]["power_scan_plans"], plans
        )

    def test_old_scalar_config_backfills_one_row_and_saves_as_plans(self):
        page = self.make_page()

        page.apply_config(
            {
                "test_frequencies": [1.0],
                "signal_source": {
                    "start_power": -20.0,
                    "stop_power": -5.0,
                    "step": 0.5,
                },
            }
        )

        self.assertEqual(len(page.power_scan_plan_rows), 1)
        self.assertEqual(page.power_scan_plan_rows[0].values(), (-20.0, 0.5, -5.0))
        self.assertEqual(
            page.build_config()["signal_source"]["power_scan_plans"],
            [{"start_power": -20.0, "step": 0.5, "stop_power": -5.0}],
        )

    def test_apply_empty_config_clears_rows_and_restores_one_default_plan(self):
        page = self.make_page()
        page.add_power_scan_plan()
        page.add_power_scan_plan()
        self.assertEqual(len(page.power_scan_plan_rows), 3)

        page.apply_config({})

        self.assertEqual(len(page.power_scan_plan_rows), 1)
        self.assertEqual(page.power_scan_plan_rows[0].values(), (-38.0, 1.0, -16.0))
        self.assertFalse(page.power_scan_plan_rows[0].delete_button.isEnabled())
        self.assertIsNone(page.power_scan_import_error)
        self.assertFalse(page.rebuild_power_scan_plan_btn.isVisibleTo(page))

    def test_backfill_from_multi_plan_then_empty_leaves_no_old_rows(self):
        """配置 A 回填后再回填空配置：A 的行、编号与预览都不能残留。"""
        page = self.make_page()
        page.apply_config(
            {
                "test_frequencies": [1.0],
                "instruments": {"power_supplies": {}},
                "signal_source": {
                    "power_scan_plans": [
                        {"start_power": -38.0, "step": 1.0, "stop_power": -10.0},
                        {"start_power": -10.0, "step": 0.25, "stop_power": -2.0},
                        {"start_power": -2.0, "step": 0.5, "stop_power": 0.0},
                    ]
                },
            }
        )
        self.assertEqual(len(page.power_scan_plan_rows), 3)
        self.assertIn("3 段", page.power_scan_summary.text())

        page.apply_config({})

        self.assertIsNone(page.power_scan_import_error)
        self.assertEqual(len(page.power_scan_plan_rows), 1)
        self.assertEqual(page.power_scan_plan_rows[0].values(), (-38.0, 1.0, -16.0))
        self.assertEqual(page.power_scan_plan_rows[0].number(), 1)
        self.assertIn("1 段", page.power_scan_summary.text())
        self.assertEqual(
            page.build_config()["signal_source"]["power_scan_plans"],
            [{"start_power": -38.0, "step": 1.0, "stop_power": -16.0}],
        )
        # 类型化快照同样只保留空配置的一行默认计划：A 的三段不能残留在视图模型里。
        self.assertEqual(
            [
                (sweep.start.dbm, sweep.step.db, sweep.stop.dbm)
                for sweep in page.view_model.state.draft.sweeps
            ],
            [(-38.0, 1.0, -16.0)],
        )
        self.assertFalse(page.is_dirty())

    def test_multi_plan_config_round_trips_through_save_and_reopen(self):
        """保存后重新打开：行数、顺序、数值与逐行预览保持一致。"""
        page = self.make_page()
        page.power_scan_plan_rows[0].set_values(-38.0, 1.0, -10.0)
        page.add_power_scan_plan().set_values(-10.0, 0.25, -2.0)
        saved = page.build_config()
        values = [row.values() for row in page.power_scan_plan_rows]
        previews = [row.preview.text() for row in page.power_scan_plan_rows]
        summary = page.power_scan_summary.text()

        reopened = self.make_page(**saved)

        self.assertEqual(
            [row.values() for row in reopened.power_scan_plan_rows], values
        )
        self.assertEqual([row.number() for row in reopened.power_scan_plan_rows], [1, 2])
        self.assertEqual(
            [row.preview.text() for row in reopened.power_scan_plan_rows], previews
        )
        self.assertEqual(reopened.power_scan_summary.text(), summary)
        self.assertEqual(
            reopened.build_config()["signal_source"]["power_scan_plans"],
            saved["signal_source"]["power_scan_plans"],
        )
        self.assertEqual(
            [
                (sweep.start.dbm, sweep.step.db, sweep.stop.dbm)
                for sweep in reopened.view_model.state.draft.sweeps
            ],
            values,
        )

    def test_plan_row_edits_drive_dirty_and_mark_clean(self):
        """dirty 以整组计划比较：改值变脏、改回变干净，增删空段也能复位。"""
        page = self.make_page()
        self.assertFalse(page.is_dirty())
        row = page.power_scan_plan_rows[0]

        row.set_values(-37.0, 1.0, -16.0)
        self.assertTrue(page.is_dirty())
        self.assertEqual(
            [sweep.start.dbm for sweep in page.view_model.state.draft.sweeps], [-37.0]
        )

        row.set_values(-38.0, 1.0, -16.0)
        self.assertFalse(page.is_dirty(), "计划改回基线后不应仍显示已修改")

        added = page.add_power_scan_plan()
        self.assertTrue(page.is_dirty())
        page.remove_power_scan_plan(added)
        self.assertFalse(page.is_dirty(), "删除刚加的空段后计划回到基线")

        row.set_values(-36.0, 1.0, -16.0)
        page.mark_clean()
        self.assertFalse(page.is_dirty())
        self.assertFalse(page.view_model.dirty)
        self.assertEqual(
            [sweep.start.dbm for sweep in page.view_model.state.draft.sweeps], [-36.0]
        )

    def test_config_with_signal_source_but_no_plans_is_an_import_error(self):
        """声明了扫描区却没有计划时，默认行只是占位，不能被当成可保存的配置。"""
        page = self.make_page()

        page.apply_config({"test_frequencies": [1.0], "signal_source": {}})

        self.assertIsNotNone(page.power_scan_import_error)
        self.assertIn("缺少", page.power_scan_import_error)
        self.assertEqual(len(page.power_scan_plan_rows), 1)
        self.assertTrue(page.rebuild_power_scan_plan_btn.isVisibleTo(page))
        self.assertFalse(page.validate())

    def test_scalar_and_plan_shapes_together_are_an_import_error(self):
        """功率扫描只能有一个来源；两种形状并存不能被静默择一。"""
        page = self.make_page()

        page.apply_config(
            {
                "test_frequencies": [1.0],
                "signal_source": {
                    "power_scan_plans": [
                        {"start_power": -20.0, "step": 1.0, "stop_power": -10.0}
                    ],
                    "start_power": -20.0,
                    "step": 1.0,
                    "stop_power": -10.0,
                },
            }
        )

        self.assertIsNotNone(page.power_scan_import_error)
        self.assertIn("只能有一个来源", page.power_scan_import_error)

    def test_incomplete_legacy_scalar_is_an_import_error_not_a_fabricated_plan(self):
        """旧标量缺字段时不能把缺失值换成默认值后当成有效计划。"""
        page = self.make_page()

        page.apply_config(
            {"test_frequencies": [1.0], "signal_source": {"start_power": -20.0}}
        )

        self.assertIsNotNone(page.power_scan_import_error)
        self.assertIn("不完整", page.power_scan_import_error)
        # 展示的默认行只是占位，不能把用户填的 -20.0 和凭空补的步长/终点凑成计划。
        self.assertEqual(page.power_scan_plan_rows[0].values(), (-38.0, 1.0, -16.0))

    def test_row_edits_do_not_clear_an_unreadable_plan_list_error(self):
        """能读出的段照常展示；只改一行不能证明其它读不出来的段已处理。"""
        page = self.make_page()

        page.apply_config(
            {
                "test_frequencies": [1.0],
                "signal_source": {
                    "power_scan_plans": [
                        {"start_power": -38.0, "step": 1.0, "stop_power": -10.0},
                        {"start_power": "bad", "step": 0.5, "stop_power": -2.0},
                    ]
                },
            }
        )

        self.assertIn("第 2 段", page.power_scan_import_error)
        self.assertEqual(
            [row.values() for row in page.power_scan_plan_rows],
            [(-38.0, 1.0, -10.0)],
        )

        page.power_scan_plan_rows[0].set_values(-37.0, 1.0, -10.0)
        page.add_power_scan_plan()

        self.assertIn("第 2 段", page.power_scan_import_error)
        self.assertTrue(page.rebuild_power_scan_plan_btn.isVisibleTo(page))
        # 可见行只是一个不完整的近似：汇总不能让人以为目标序列已经确定。
        self.assertIn("计划未完整读取", page.power_scan_summary.text())
        self.assertIn("只按当前可见行估算", page.power_scan_summary.text())
        self.assertEqual(page.power_scan_summary.property("tone"), "warning")
        self.assertIn("第 2 段", page.power_scan_summary.toolTip())
        self.assertTrue(
            any(
                "第 2 段" in message
                for message in page.form_adapter.validation_errors()
            ),
            "导入错误未解除前，表单校验必须继续阻止保存",
        )

    def test_rebuild_plan_accepts_the_visible_rows_and_clears_the_error(self):
        """用户明确重建后，以当前可见行作为完整计划，不再保留结构错误。"""
        page = self.make_page()

        page.apply_config(
            {
                "test_frequencies": [1.0],
                "signal_source": {
                    "power_scan_plans": [
                        {"start_power": -38.0, "step": 1.0, "stop_power": -10.0},
                        {"start_power": "bad", "step": 0.5, "stop_power": -2.0},
                    ]
                },
            }
        )

        page.rebuild_power_scan_plan_btn.click()

        self.assertIsNone(page.power_scan_import_error)
        self.assertFalse(page.rebuild_power_scan_plan_btn.isVisibleTo(page))
        # 结构错误解除后，可见行就是完整计划：汇总回到正常的点位数说明。
        self.assertNotIn("计划未完整读取", page.power_scan_summary.text())
        self.assertIn("个目标点", page.power_scan_summary.text())
        self.assertEqual(page.power_scan_summary.property("tone"), "muted")
        self.assertEqual(
            page.build_config()["signal_source"]["power_scan_plans"],
            [{"start_power": -38.0, "step": 1.0, "stop_power": -10.0}],
        )

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

    def test_driver_mode_value_is_parsed_the_same_way_by_construction_and_backfill(self):
        """构造与 ``apply_config`` 共用解析：非布尔值按缺失处理，不会一边启用一边禁用。"""
        document = {
            "test_frequencies": [1.0],
            "driver_mode": {"enabled": None},
            "instruments": {"power_supplies": {}},
        }
        constructed = TestConfigurationPage(document)
        page = self.make_page()

        page.apply_config(document)

        self.assertTrue(
            constructed.test_mode_enabled,
            "非布尔值按默认值（启用）处理，与回填一致",
        )
        self.assertTrue(constructed.driver_gain.isEnabled())
        self.assertEqual(page.test_mode_enabled, constructed.test_mode_enabled)
        self.assertEqual(
            page.driver_gain.isEnabled(), constructed.driver_gain.isEnabled()
        )

    def test_backfilled_driver_gain_keeps_the_full_precision(self):
        """回填不截断高精度增益，避免打开再保存时悄悄改写用户的配置。"""
        page = self.make_page(driver_mode={"enabled": True})
        page.apply_config(
            {
                "test_frequencies": [1.0],
                "driver_mode": {"enabled": True, "estimated_gain_db": 20.1234567},
                "instruments": {"power_supplies": {}},
            }
        )

        self.assertEqual(page.driver_gain.text(), "20.1234567")
        self.assertEqual(
            page.build_config()["driver_mode"]["estimated_gain_db"], 20.1234567
        )

    def test_invalid_driver_gain_is_rejected_by_the_config_dto(self):
        """页面不预判增益：非法文本进入文档，由正式校验给出“必须是有限数值”。"""
        page = self.make_page(driver_mode={"enabled": True})
        failures = []
        page.validation_failed.connect(failures.append)

        page.driver_gain.setText("not-a-gain")

        self.assertFalse(page.validate())
        self.assertIn("有限数值", "；".join(failures))

    def test_power_controls_live_on_the_independent_page(self):
        """步骤 12：电源控件与上电计划不再由测试配置页创建或读取。"""
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
        page = TestConfigurationPage(
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
                # 引用一台没有已应用设备的电源：这是电源页的问题，不应由测试配置页报告。
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
        page = TestConfigurationPage({"test_frequencies": [], "instruments": {}})
        page.show()
        self.app.processEvents()
        failures = []
        page.validation_failed.connect(failures.append)

        allowed = page.validate()

        self.assertFalse(allowed)
        self.assertIn("频率", failures[0])

    def test_build_config_keeps_the_power_section_owned_by_the_other_page(self):
        """测试配置页只读测试参数；电源字段由独立电源页提交，不被本页清空。"""
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

    def test_frequency_controls_share_one_row_in_requested_order(self):
        """频率四项同排（起始/步长/结束/增补），频点预览紧接在下一行。"""
        page = self.make_page()
        layout = page.test_parameters_group.layout()

        # 四个频率控件都在同一个嵌套行布局里，因此必然同排；预览是外层网格的下一行。
        row_layout = layout.itemAtPosition(0, 0).layout()
        preview_index = layout.indexOf(page.freq_preview)
        self.assertEqual(layout.getItemPosition(preview_index)[0], 1)

        order = []
        for index in range(row_layout.count()):
            widget = row_layout.itemAt(index).widget()
            if widget in (
                page.freq_start,
                page.freq_step,
                page.freq_stop,
                page.freq_extra,
            ):
                order.append(widget)
        self.assertEqual(
            order,
            [page.freq_start, page.freq_step, page.freq_stop, page.freq_extra],
        )

        texts = {label.text() for label in page.findChildren(QLabel)}
        self.assertIn("频率（GHz）：", texts)
        for text in ("起始", "步长", "结束", "增补"):
            self.assertIn(text, texts)

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
        page = TestConfigurationPage(config)
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

    def test_numeric_fields_ignore_mouse_wheel(self):
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

    def test_numeric_fields_are_plain_text_inputs_without_steppers(self):
        """频率、计划行与 DUT 上限的数值框都是纯文本框：没有步进按钮。"""
        from PySide6.QtWidgets import QLineEdit

        page = self.make_page()
        row = page.power_scan_plan_rows[0]
        widgets = [
            page.freq_start,
            page.freq_stop,
            page.freq_step,
            row.start_power,
            row.step,
            row.stop_power,
            page.max_input_power,
        ]
        for widget in widgets:
            self.assertIsInstance(widget, QLineEdit)
            self.assertFalse(hasattr(widget, "stepUp"))
            self.assertFalse(hasattr(widget, "stepDown"))
            self.assertFalse(hasattr(widget, "buttonSymbols"))

    def test_numeric_fields_accept_direct_typed_values(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        page = self.make_page()
        widget = page.start_power
        widget.setFocus()
        widget.clear()
        QTest.keyClicks(widget, "-12.5")

        self.assertEqual(widget.value(), -12.5)
        QTest.keyClick(widget, Qt.Key_Return)
        self.assertEqual(widget.text(), "-12.5")
        self.assertEqual(
            page.build_config()["signal_source"]["power_scan_plans"][0][
                "start_power"
            ],
            -12.5,
        )

    def test_typing_a_frequency_refreshes_the_preview_live(self):
        from PySide6.QtTest import QTest

        page = self.make_page()
        widget = page.freq_stop
        widget.setFocus()
        widget.clear()
        QTest.keyClicks(widget, "5.0")

        self.assertIn("11 个频点", page.freq_preview.text())

    def test_numeric_fields_ignore_keyboard_up_and_down(self):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        page = self.make_page()
        widget = page.start_power
        start = widget.value()
        widget.setFocus()

        QTest.keyClick(widget, Qt.Key_Up)
        QTest.keyClick(widget, Qt.Key_Down)

        self.assertEqual(widget.value(), start)

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
        page = TestConfigurationPage(config)
        page.show()
        self.addCleanup(_destroy_page, page)
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
