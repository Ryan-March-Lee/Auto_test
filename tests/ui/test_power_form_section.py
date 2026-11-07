"""步骤 11：独立电源表单的纯 UI 测试。

覆盖组上电计划的成员/顺序、漏极路由状态、文档回填，以及绑定变化刷新候选时
保留尚未提交的计划与漏极。计划不再有独立的编辑框：成员与顺序都由卡片设置，这里只
验证 ``PowerPlanState`` 的读数与页面依赖的信号。测试只构造 ``PowerFormSection``
与它的内部状态，不启动整个配置页；通道参数已融合到电源卡片，可见控件归卡片。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from presentation.qt.config_form_state import ConfigFormState
from presentation.qt.config_power_section import (
    POWER_SUPPLY_CHANNELS,
    PowerFormSection,
)

SUPPLIES = ("DP8B1519", "DP8B0885", "DP8B0950")


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


def _plan(*routes, delay=0.0):
    return {
        "power_sequence": [
            {"supply_id": supply_id, "channel": channel}
            for supply_id, channel in routes
        ],
        "inter_channel_delay_s": delay,
    }


def _routes(editor):
    return tuple(
        (item["supply_id"], item["channel"])
        for item in editor.read_plan()["power_sequence"]
    )


def _section(
    *,
    plans=None,
    drain=None,
    bound=SUPPLIES,
    driver_plan_active=False,
):
    """一个独立电源表单；``bound`` 控制本次已绑定的电源（计划候选来源）。"""
    plans = plans or {}
    return PowerFormSection(
        sources=_source(*SUPPLIES),
        choices=_choices(*bound),
        driver_plan_active=driver_plan_active,
        dut_power_plan=plans.get("dut"),
        driver_power_plan=plans.get("driver"),
        drain_channel=drain,
    )


class PowerSectionLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_the_section_has_no_visible_controls(self):
        section = _section()
        self.addCleanup(section.deleteLater)

        self.assertFalse(hasattr(section, "drain_channel_group"))
        self.assertFalse(hasattr(section, "drain_channel_combo"))
        self.assertFalse(hasattr(section, "power_plan_group"))
        self.assertFalse(hasattr(section, "driver_power_plan_box"))
        for editor in (
            section.dut_power_plan_editor,
            section.driver_power_plan_editor,
        ):
            self.assertFalse(hasattr(editor, "list"))
            self.assertFalse(hasattr(editor, "status"))
            self.assertFalse(hasattr(editor, "delay_spin"))


class PowerPlanStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_the_plan_order_is_the_power_on_order(self):
        section = _section(
            plans={"dut": _plan(("DP8B0885", "CH2"), ("DP8B1519", "CH1"), delay=2.5)}
        )
        self.addCleanup(section.deleteLater)
        editor = section.dut_power_plan_editor

        self.assertEqual(
            editor.checked_routes(),
            (("DP8B0885", "CH2"), ("DP8B1519", "CH1")),
            "计划里的顺序就是上电顺序",
        )
        self.assertEqual(
            editor.routes()[:2],
            (("DP8B0885", "CH2"), ("DP8B1519", "CH1")),
            "计划里的路由先按顺序显示",
        )
        self.assertAlmostEqual(editor.read_plan()["inter_channel_delay_s"], 0.0)

        self.assertTrue(editor.set_route_order(("DP8B0885", "CH2"), 2))

        self.assertEqual(
            _routes(editor),
            (("DP8B1519", "CH1"), ("DP8B0885", "CH2")),
            "重排后读出的上电顺序随之改变",
        )
        self.assertFalse(editor.set_route_order(("DP8B0950", "CH1"), 1))

    def test_membership_is_added_at_the_end_and_removed_without_reordering(self):
        section = _section(
            plans={"dut": _plan(("DP8B1519", "CH1"), ("DP8B1519", "CH2"))}
        )
        self.addCleanup(section.deleteLater)
        editor = section.dut_power_plan_editor

        self.assertTrue(editor.set_route_checked(("DP8B0885", "CH1"), True))
        self.assertEqual(
            editor.checked_routes(),
            (
                ("DP8B1519", "CH1"),
                ("DP8B1519", "CH2"),
                ("DP8B0885", "CH1"),
            ),
            "新加入的通道追加到计划末尾",
        )

        self.assertTrue(editor.set_route_checked(("DP8B1519", "CH1"), False))
        self.assertEqual(
            editor.checked_routes(),
            (("DP8B1519", "CH2"), ("DP8B0885", "CH1")),
            "移出一条路由时其余路由保持相对顺序",
        )
        self.assertFalse(editor.set_route_checked(("DP8B9999", "CH1"), True))

    def test_a_missing_plan_starts_empty(self):
        section = _section()
        self.addCleanup(section.deleteLater)

        self.assertEqual(
            section.dut_power_plan_editor.read_plan()["power_sequence"],
            [],
            "尚未确认顺序时不能把候选路由自动加入",
        )

    def test_membership_change_emits_once(self):
        section = _section()
        self.addCleanup(section.deleteLater)
        events = []
        section.plan_changed.connect(lambda *args: events.append(args))

        section.dut_power_plan_editor.set_route_checked(("DP8B1519", "CH1"), True)
        section.dut_power_plan_editor.set_route_checked(("DP8B1519", "CH1"), True)

        self.assertEqual(events, [("dut", "DP8B1519", "CH1", True)])

    def test_inactive_driver_group_reads_an_empty_plan(self):
        section = _section(
            plans={
                "driver": _plan(("DP8B0950", "CH1"), delay=3.0),
            },
            driver_plan_active=False,
        )
        self.addCleanup(section.deleteLater)
        editor = section.driver_power_plan_editor
        editor.set_route_checked(("DP8B0950", "CH1"), True)

        self.assertEqual(
            editor.read_active_plan(),
            {"power_sequence": [], "inter_channel_delay_s": 0.0},
            "驱放模式关闭时不能把本机电源计划写回",
        )
        self.assertTrue(
            editor.read_plan()["power_sequence"],
            "重新开启驱放模式后仍保留界面上未提交的成员",
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
        self.addCleanup(section.deleteLater)

        self.assertEqual(
            section.read_drain_channels(),
            [{"supply_id": "DP8B0885", "channel": "CH2"}],
        )

    def test_multiple_drains_are_read_in_order(self):
        section = _section(
            plans={"dut": _plan(("DP8B0885", "CH1"), ("DP8B0885", "CH2"))},
            drain=[
                {"supply_id": "DP8B0885", "channel": "CH2"},
                {"supply_id": "DP8B0885", "channel": "CH1"},
            ],
        )
        self.addCleanup(section.deleteLater)

        self.assertEqual(
            section.read_drain_channels(),
            [
                {"supply_id": "DP8B0885", "channel": "CH2"},
                {"supply_id": "DP8B0885", "channel": "CH1"},
            ],
        )

    def test_selecting_multiple_drains_replaces_the_set(self):
        section = _section(plans={"dut": _plan(("DP8B0885", "CH1"), ("DP8B0885", "CH2"))})
        self.addCleanup(section.deleteLater)

        self.assertTrue(
            section.select_drain_routes(
                (("DP8B0885", "CH1"), ("DP8B0885", "CH2"))
            )
        )
        self.assertEqual(
            section.read_drain_channels(),
            [
                {"supply_id": "DP8B0885", "channel": "CH1"},
                {"supply_id": "DP8B0885", "channel": "CH2"},
            ],
        )
        self.assertTrue(section.select_drain_routes(()), "没有标记时要能清空")
        self.assertEqual(section.read_drain_channels(), [])

    def test_an_invalid_multi_drain_is_preserved_for_validation(self):
        section = _section(
            plans={"dut": _plan(("DP8B0885", "CH1"))},
            drain=[
                {"supply_id": "DP8B0885", "channel": "CH1"},
                {"supply_id": "DP8B9999", "channel": "CH4"},
            ],
        )
        self.addCleanup(section.deleteLater)

        self.assertEqual(
            section.read_drain_channels(),
            [
                {"supply_id": "DP8B0885", "channel": "CH1"},
                {"supply_id": "DP8B9999", "channel": "CH4"},
            ],
            "无效漏极不能被静默清空，交给正式校验",
        )

    def test_an_unset_drain_reads_as_an_empty_route_list(self):
        section = _section()
        self.addCleanup(section.deleteLater)

        self.assertEqual(section.read_drain_channels(), [])

    def test_an_invalid_existing_drain_is_preserved_for_validation(self):
        section = _section(drain={"supply_id": "DP8B9999", "channel": "CH1"})
        self.addCleanup(section.deleteLater)

        self.assertEqual(
            section.read_drain_channels(),
            [{"supply_id": "DP8B9999", "channel": "CH1"}],
            "不在当前候选里的漏极仍保留给正式校验",
        )

    def test_removing_a_route_from_the_plan_preserves_the_drain_for_validation(self):
        section = _section(
            plans={"dut": _plan(("DP8B0885", "CH2"), ("DP8B1519", "CH1"))},
            drain={"supply_id": "DP8B0885", "channel": "CH2"},
        )
        self.addCleanup(section.deleteLater)

        section.dut_power_plan_editor.set_route_checked(("DP8B0885", "CH2"), False)

        self.assertEqual(
            section.read_drain_channels(),
            [{"supply_id": "DP8B0885", "channel": "CH2"}],
            "移出计划不静默清空漏极，保留原值交给校验",
        )


class PowerPlanBackfillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_apply_config_backfills_order_and_drain(self):
        section = _section()
        self.addCleanup(section.deleteLater)
        document = {
            "power_supply_assignment": {
                "dut_amplifier": {
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
            _routes(section.dut_power_plan_editor),
            (("DP8B0885", "CH2"), ("DP8B1519", "CH1")),
        )
        self.assertEqual(
            plan["inter_channel_delay_s"],
            0.0,
            "组默认间隔不再由编辑器写入",
        )
        self.assertEqual(
            section.read_drain_channels(),
            [{"supply_id": "DP8B1519", "channel": "CH1"}],
        )

    def test_reading_the_form_writes_all_drains_as_a_list(self):
        section = _section(
            plans={"dut": _plan(("DP8B0885", "CH1"), ("DP8B0885", "CH2"))},
            drain=[
                {"supply_id": "DP8B0885", "channel": "CH1"},
                {"supply_id": "DP8B0885", "channel": "CH2"},
            ],
        )
        self.addCleanup(section.deleteLater)
        state = ConfigFormState({}, section)

        self.assertEqual(
            state.read_power_fragment()["dut_config"]["drain_channel"],
            [
                {"supply_id": "DP8B0885", "channel": "CH1"},
                {"supply_id": "DP8B0885", "channel": "CH2"},
            ],
        )

    def test_reading_the_form_writes_plans_and_not_supply_lists(self):
        """计划是唯一来源：读回只剩 ``power_plan``，不再补按器件固定分配的表。"""
        section = _section(plans={"dut": _plan(("DP8B1519", "CH1"))})
        self.addCleanup(section.deleteLater)
        config = {
            "power_supply_assignment": {
                "dut_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {"carrier": {"supply_id": "DP8B1519"}},
                }
            }
        }

        assignment = ConfigFormState(config, section).read_power_assignment()[
            "power_supply_assignment"
        ]

        self.assertEqual(
            assignment["dut_amplifier"]["power_plan"],
            _plan(("DP8B1519", "CH1")),
        )
        self.assertEqual(set(assignment["dut_amplifier"]), {"power_plan"})
        self.assertEqual(set(assignment["driver_amplifier"]), {"power_plan"})

    def test_candidates_are_the_channels_of_every_bound_supply(self):
        """候选来自绑定集合，不默认展开整台电源以外的通道，也不按 PA 单元裁剪。"""
        section = _section(bound=("DP8B1519", "DP8B0885"))
        self.addCleanup(section.deleteLater)

        self.assertEqual(
            section.dut_power_plan_editor.routes(),
            (
                ("DP8B1519", "CH1"),
                ("DP8B1519", "CH2"),
                ("DP8B0885", "CH1"),
                ("DP8B0885", "CH2"),
            ),
        )
        self.assertEqual(
            section.driver_power_plan_editor.routes(),
            section.dut_power_plan_editor.routes(),
        )

    def test_one_supply_can_serve_driver_and_dut_on_different_channels(self):
        """同一台电源 CH1 供驱放、CH2 供 DUT：计划路由已经表达清楚，不需要固定分配。"""
        section = _section(
            bound=("DP8B1519",),
            plans={
                "dut": _plan(("DP8B1519", "CH2")),
                "driver": _plan(("DP8B1519", "CH1")),
            },
            driver_plan_active=True,
        )
        self.addCleanup(section.deleteLater)

        self.assertEqual(
            section.dut_power_plan_editor.read_plan()["power_sequence"],
            [{"supply_id": "DP8B1519", "channel": "CH2"}],
        )
        self.assertEqual(
            section.driver_power_plan_editor.read_active_plan()["power_sequence"],
            [{"supply_id": "DP8B1519", "channel": "CH1"}],
        )


class PowerFormRebuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_set_power_choices_keeps_uncommitted_plan_and_drain(self):
        section = _section(
            plans={"dut": _plan(("DP8B0885", "CH2"), ("DP8B1519", "CH1"), delay=2.0)},
            drain={"supply_id": "DP8B0885", "channel": "CH2"},
            bound=("DP8B1519", "DP8B0885"),
        )
        self.addCleanup(section.deleteLater)
        section.dut_power_plan_editor.set_route_checked(("DP8B1519", "CH2"), True)
        section.dut_power_plan_editor.set_route_order(("DP8B1519", "CH2"), 1)
        expected_plan = section.dut_power_plan_editor.read_plan()
        expected_drain = section.read_drain_channels()

        section.set_power_choices(_source(*SUPPLIES), _choices(*SUPPLIES))

        self.assertEqual(
            section.dut_power_plan_editor.read_plan(),
            expected_plan,
            "绑定变化刷新候选时保留操作者尚未保存的上电计划",
        )
        self.assertEqual(section.read_drain_channels(), expected_drain)


if __name__ == "__main__":
    unittest.main()
