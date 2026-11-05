"""步骤 11：独立电源表单的纯 UI 测试。

覆盖组上电计划的排序、启用勾选、组间隔、漏极选择、文档回填，以及绑定变化重建控件
时保留尚未提交的编辑。测试只构造 ``PowerFormSection`` 与它的控件，不启动整个配置页。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from presentation.qt.config_form_state import ConfigFormState
from presentation.qt.config_power_section import (
    POWER_SUPPLY_CHANNELS,
    PowerFormSection,
)

SUPPLIES = ("DP8B1519", "DP8B0885", "DP8B0950")


class _CountCombo:
    """PA 单元数量下拉框的最小替身。"""

    def __init__(self, text):
        self.text = text

    def currentText(self):
        return self.text


def _source(*supply_ids):
    return {
        supply_id: {
            "label": supply_id,
            "model": "DP832A",
            "channels": {channel: {} for channel in POWER_SUPPLY_CHANNELS},
        }
        for supply_id in supply_ids
    }


def _choices(*supply_ids):
    return {supply_id: {"label": supply_id, "model": "DP832A"} for supply_id in supply_ids}


def _assignment(supply_id, channels=None):
    return {
        "supply_id": supply_id,
        "channel": list(POWER_SUPPLY_CHANNELS if channels is None else channels),
    }


def _plan(*routes, delay=0.0):
    return {
        "power_sequence": [
            {"supply_id": supply_id, "channel": channel}
            for supply_id, channel in routes
        ],
        "inter_channel_delay_s": delay,
    }


def _section(
    *,
    plans=None,
    drain=None,
    supplies=("DP8B1519", "DP8B0885"),
    channels=None,
    count=2,
    driver_plan_active=False,
):
    channels = channels or {}
    assignment_entries = {
        f"pa_unit{index}_power": _assignment(supply_id, channels.get(index))
        for index, supply_id in enumerate(supplies, 1)
    }
    plans = plans or {}
    return PowerFormSection(
        sources=_source(*SUPPLIES),
        choices=_choices(*SUPPLIES),
        assignment_entries=assignment_entries,
        driver_power_source="直流电源",
        dut_supply_count=count,
        driver_plan_active=driver_plan_active,
        dut_power_plan=plans.get("dut"),
        driver_power_plan=plans.get("driver"),
        drain_channel=drain,
    )


def _check(editor, route, checked=True):
    for row in range(editor.list.count()):
        item = editor.list.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == tuple(route):
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            return
    raise AssertionError(f"计划列表里没有路由 {route}")


class PowerPlanWidgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_the_list_order_is_the_power_on_order(self):
        section = _section(
            plans={"dut": _plan(("DP8B0885", "CH2"), ("DP8B1519", "CH1"), delay=2.5)}
        )
        editor = section.dut_power_plan_editor

        self.assertEqual(
            editor.routes(),
            (
                ("DP8B0885", "CH2"),
                ("DP8B1519", "CH1"),
                ("DP8B1519", "CH2"),
                ("DP8B0885", "CH1"),
            ),
            "计划里的顺序先显示，未出现在计划里的候选路由按分配顺序补在后面",
        )
        self.assertAlmostEqual(editor.read_plan()["inter_channel_delay_s"], 2.5)

        editor.list.setCurrentRow(0)
        editor.move_current(1)

        self.assertEqual(
            [
                (item["supply_id"], item["channel"])
                for item in editor.read_plan()["power_sequence"]
            ],
            [("DP8B1519", "CH1"), ("DP8B0885", "CH2")],
            "移动一行后读出的上电顺序随之改变",
        )

    def test_unchecked_channels_are_not_part_of_the_plan(self):
        section = _section(
            plans={"dut": _plan(("DP8B1519", "CH1"), ("DP8B1519", "CH2"))}
        )
        editor = section.dut_power_plan_editor

        _check(editor, ("DP8B1519", "CH2"), False)
        _check(editor, ("DP8B0885", "CH1"), True)

        self.assertEqual(
            [
                (item["supply_id"], item["channel"])
                for item in editor.read_plan()["power_sequence"]
            ],
            [("DP8B1519", "CH1"), ("DP8B0885", "CH1")],
            "启用开关只把勾选的路由写进上电序列",
        )

    def test_a_missing_plan_starts_with_every_candidate_disabled(self):
        section = _section()

        self.assertEqual(
            section.dut_power_plan_editor.read_plan()["power_sequence"],
            [],
            "尚未确认顺序时不能把候选路由自动勾上",
        )
        self.assertIn("未确认", section.dut_power_plan_editor.status.text())

    def test_the_status_line_reports_incomplete_and_ready_plans(self):
        section = _section(channels={1: ["CH1"], 2: ["CH2"]})
        editor = section.dut_power_plan_editor

        _check(editor, ("DP8B1519", "CH1"))
        self.assertIn("未完成", editor.status.text())

        _check(editor, ("DP8B0885", "CH2"))
        self.assertIn("就绪", editor.status.text())

    def test_inactive_driver_group_reads_an_empty_plan(self):
        section = _section(
            plans={
                "driver": _plan(("DP8B0950", "CH1"), delay=3.0),
            },
            supplies=("DP8B1519",),
            driver_plan_active=False,
        )
        section.power_assignment_widgets["driver_power"].setCurrentIndex(
            section.power_assignment_widgets["driver_power"].findData("DP8B0950")
        )
        section.power_assignment_widgets["driver_power"].currentIndexChanged.emit(0)
        section.driver_power_plan_editor.set_route_checked(("DP8B0950", "CH1"), True)

        self.assertEqual(
            section.driver_power_plan_editor.read_active_plan(),
            {"power_sequence": [], "inter_channel_delay_s": 0.0},
            "驱放自带供电时不能把本机电源计划写回",
        )
        self.assertTrue(
            section.driver_power_plan_editor.read_plan()["power_sequence"],
            "切回直流供电后仍保留界面上未提交的勾选",
        )


class DrainSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_the_drain_selection_is_read_as_a_physical_route(self):
        section = _section(
            plans={"dut": _plan(("DP8B0885", "CH2"))},
            drain={"supply_id": "DP8B0885", "channel": "CH2"},
        )

        self.assertEqual(
            section.read_drain_channel(),
            {"supply_id": "DP8B0885", "channel": "CH2"},
        )
        options = [
            section.drain_channel_combo.itemData(index)
            for index in range(section.drain_channel_combo.count())
        ]
        self.assertIn(("DP8B0885", "CH2"), options)

    def test_an_unset_drain_reads_as_none(self):
        section = _section()

        self.assertIsNone(section.read_drain_channel())

    def test_an_invalid_existing_drain_stays_visible_for_validation(self):
        section = _section(drain={"supply_id": "DP8B9999", "channel": "CH1"})

        self.assertEqual(
            section.read_drain_channel(),
            {"supply_id": "DP8B9999", "channel": "CH1"},
            "不在当前候选里的漏极不能被静默清空",
        )
        self.assertIn("无效", section.drain_channel_combo.currentText())

    def test_drain_options_are_limited_to_the_active_plan(self):
        section = _section(
            plans={"dut": _plan(("DP8B0885", "CH2"))},
            channels={1: ["CH1"], 2: ["CH2"]},
        )

        options = [
            section.drain_channel_combo.itemData(index)
            for index in range(1, section.drain_channel_combo.count())
        ]

        self.assertEqual(options, [("DP8B0885", "CH2")])

    def test_unchecking_the_drain_channel_keeps_it_visible_as_invalid(self):
        section = _section(
            plans={"dut": _plan(("DP8B0885", "CH2"), ("DP8B1519", "CH1"))},
            channels={1: ["CH1"], 2: ["CH2"]},
            drain={"supply_id": "DP8B0885", "channel": "CH2"},
        )

        _check(section.dut_power_plan_editor, ("DP8B0885", "CH2"), False)

        self.assertEqual(
            section.read_drain_channel(),
            {"supply_id": "DP8B0885", "channel": "CH2"},
            "取消勾选不静默清空漏极，保留旧值交给校验",
        )
        self.assertIn("无效", section.drain_channel_combo.currentText())


class PowerPlanBackfillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_apply_config_backfills_order_interval_and_drain(self):
        section = _section()
        document = {
            "power_supply_assignment": {
                "dut_amplifier": {
                    "supplies": {
                        "carrier": _assignment("DP8B1519"),
                        "peaking": _assignment("DP8B0885"),
                    },
                    "power_plan": _plan(
                        ("DP8B0885", "CH2"), ("DP8B1519", "CH1"), delay=1.5
                    ),
                }
            },
            "dut_config": {"drain_channel": {"supply_id": "DP8B1519", "channel": "CH1"}},
        }

        section.apply_power_plan_config(document, driver_plan_active=False)

        plan = section.dut_power_plan_editor.read_plan()
        self.assertEqual(
            [(item["supply_id"], item["channel"]) for item in plan["power_sequence"]],
            [("DP8B0885", "CH2"), ("DP8B1519", "CH1")],
        )
        self.assertAlmostEqual(plan["inter_channel_delay_s"], 1.5)
        self.assertEqual(
            section.read_drain_channel(),
            {"supply_id": "DP8B1519", "channel": "CH1"},
        )

    def test_plan_membership_does_not_rewrite_the_assignment_channels(self):
        """计划只决定顺序/启用；分配通道集合仍是配置声明的集合，不会被隐式改写。"""
        section = _section(
            plans={"dut": _plan(("DP8B1519", "CH1"))},
            supplies=("DP8B1519", "DP8B0885"),
            channels={1: ["CH1"], 2: ["CH2"]},
            count=2,
        )
        section.test_mode_enabled = False
        section.pa_unit_count_combo = _CountCombo("2")
        config = {
            "power_supply_assignment": {
                "dut_amplifier": {
                    "supplies": {
                        "carrier": {"supply_id": "DP8B1519", "channel": ["CH1"]},
                        "peaking": {"supply_id": "DP8B0885", "channel": ["CH2"]},
                    }
                }
            }
        }

        assignment = ConfigFormState(config, section).read_power_assignment()[
            "power_supply_assignment"
        ]["dut_amplifier"]["supplies"]

        self.assertEqual(assignment["carrier"]["channel"], ["CH1"])
        self.assertEqual(
            assignment["peaking"]["channel"],
            ["CH2"],
            "未勾选的通道仍属于该 PA 单元；计划集合与分配集合的差异交给正式校验",
        )
        self.assertNotIn("name", assignment["carrier"], "1.1 只写 supply_id")

    def test_candidates_are_the_channels_declared_by_each_slot(self):
        """计划候选只来自分配声明的通道，不默认展开整台电源的 CH1/CH2。"""
        section = _section(
            supplies=("DP8B1519", "DP8B0885"),
            channels={1: ["CH1"], 2: ["CH2"]},
        )

        self.assertEqual(
            section.dut_power_plan_editor.routes(),
            (("DP8B1519", "CH1"), ("DP8B0885", "CH2")),
        )

    def test_two_slots_may_share_a_supply_without_copying_channels(self):
        """同一台电源供两个 PA 单元时，各槽位的通道不会互相复制。"""
        section = _section(
            supplies=("DP8B1519", "DP8B1519"),
            channels={1: ["CH1"], 2: ["CH2"]},
            count=2,
        )

        self.assertEqual(
            section.dut_power_plan_editor.routes(),
            (("DP8B1519", "CH1"), ("DP8B1519", "CH2")),
        )


class PowerFormRebuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_rebuild_keeps_uncommitted_channels_plan_and_drain(self):
        section = _section(
            plans={"dut": _plan(("DP8B0885", "CH2"), ("DP8B1519", "CH1"), delay=2.0)},
            drain={"supply_id": "DP8B0885", "channel": "CH2"},
        )
        section.power_config_widgets["DP8B0885"]["channels"]["CH1"]["voltage"].setValue(12.5)
        _check(section.dut_power_plan_editor, ("DP8B1519", "CH2"), True)
        section.dut_power_plan_editor.list.setCurrentRow(0)
        section.dut_power_plan_editor.move_current(1)
        preserved_channels = ConfigFormState({}, section).read_power_supply_config()[
            "instruments"
        ]["power_supplies"]
        expected_plan = section.dut_power_plan_editor.read_plan()
        expected_drain = section.read_drain_channel()

        section.rebuild(
            sources=_source(*SUPPLIES),
            preserved_channels=preserved_channels,
            preserve=True,
        )

        self.assertEqual(
            section.power_config_widgets["DP8B0885"]["channels"]["CH1"]["voltage"].value(),
            12.5,
            "绑定变化重建标签页时保留操作者刚填的通道参数",
        )
        self.assertEqual(section.dut_power_plan_editor.read_plan(), expected_plan)
        self.assertEqual(section.read_drain_channel(), expected_drain)


if __name__ == "__main__":
    unittest.main()
