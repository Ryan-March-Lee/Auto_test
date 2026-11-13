"""测量工作台外壳的契约（重构计划步骤 4）。

这里锁定用户能看见、步骤 5 会依赖的九件事：

- 外壳能独立构造：阶段条、页头与内容槽都在，内容由窗口注入而不是外壳自建；
- ``set_current_stage`` 同时同步阶段条与页头摘要，阶段条仍是受控视图；
- 当前阶段只在构造时传入的阶段集合里查找，自定义阶段也能同步；
- ``has_stage`` 用同一份阶段集合回答“这是不是阶段”，窗口据此分流页头；
- 点击阶段只转发导航请求，不预先改写当前阶段；
- ``show_utility`` 不点亮任何阶段，页头不显示工具页标题，并禁用无去处
  的“配置”动作；
- 内容槽只接受一个 widget，替换时只把旧 widget 从布局摘掉，不销毁它；
- 内容槽与右侧状态区拒绝外壳自己的结构控件，避免把常驻框架拆开；
- 运行标识、右侧状态区、阶段状态与动效开关都由调用方写入，外壳只展示。

离屏环境只检查结构、文本与几何，不做整窗像素对比：像素级动效属于步骤 2/3 的
``test_motion`` / ``test_measurement_stage_rail``。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (  # noqa: E402
    QCoreApplication,
    QEvent,
    QRectF,
    Qt,
)
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QWidget  # noqa: E402

from presentation.qt.measurement_stage import (  # noqa: E402
    MEASUREMENT_STAGES,
    StageDescriptor,
    StageState,
)
from presentation.qt.measurement_stage_rail import (  # noqa: E402
    MeasurementStageRail,
)
from presentation.qt.measurement_workspace import (  # noqa: E402
    MeasurementWorkspace,
    mode_text,
)
from presentation.qt.motion import RevealDirection, SlideFadeEffect  # noqa: E402
from presentation.qt.stage_summary import (  # noqa: E402
    NOT_STARTED,
    StageMetric,
    StageSummary,
)
from presentation.qt.theme import MOTION_SLIDE_PX, TOKENS  # noqa: E402


def _destroy(widget):
    """关闭并销毁顶层控件，再投递排队的删除（离屏用例的统一收尾方式）。"""
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class _WorkspaceTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.workspace = self.create_workspace()
        self.workspace.resize(960, 640)
        self.workspace.show()
        self.app.processEvents()
        self.addCleanup(_destroy, self.workspace)

    def create_workspace(self):
        """构造被测外壳；需要自定义阶段集合的子类覆盖这里。"""
        return MeasurementWorkspace()

    def settle(self):
        """连续投递几轮事件，让标签文字变化后的布局尺寸真正落定。"""
        for _ in range(3):
            self.app.processEvents()

    def click_stage(self, key):
        button = self.workspace.stage_rail().stage_button(key)
        QTest.mouseClick(
            button, Qt.MouseButton.LeftButton, pos=button.rect().center()
        )
        self.app.processEvents()


class ConstructionTests(_WorkspaceTestCase):
    def test_shell_has_stage_rail_header_and_empty_content_slot(self):
        self.assertIsInstance(
            self.workspace.stage_rail(), MeasurementStageRail
        )
        self.assertEqual(
            tuple(
                button.key()
                for button in self.workspace.stage_rail().stage_buttons()
            ),
            tuple(stage.key for stage in MEASUREMENT_STAGES),
        )
        self.assertIsNone(self.workspace.current_stage())
        self.assertIsNone(self.workspace.content_widget())
        self.assertEqual(self.workspace.right_widgets(), ())
        self.assertEqual(self.workspace.content_host.layout().count(), 0)
        self.assertEqual(self.workspace.configuration_btn.text(), "配置")
        self.assertFalse(
            type(self.workspace).__name__.endswith("Page"),
            "外壳不是页面，类名不能触发页面注册门禁",
        )

    def test_header_no_longer_renders_the_three_line_text_group(self):
        for name in ("eyebrow_label", "title_label", "subtitle_label"):
            with self.subTest(name=name):
                self.assertFalse(
                    hasattr(self.workspace, name),
                    "阶段编号/标题/副标题只由阶段条表达，页头不再保留这三行",
                )
        self.assertEqual(
            self.workspace.configuration_btn.property("variant"), "ghost",
            "配置入口是低强调的工具动作，不是第五个阶段",
        )

    def test_unset_run_identity_reads_as_unconnected(self):
        self.assertEqual(self.workspace.run_identity(), (None, None))
        self.assertEqual(
            self.workspace.run_identity_label.text(),
            "未连接",
            "没有运行时不能拼成“本次运行 未连接”这种自相矛盾的文案",
        )

    def test_stage_rail_and_status_cluster_share_one_top_bar(self):
        self.settle()

        self.assertIs(
            self.workspace.stage_rail().parentWidget(),
            self.workspace.top_bar,
        )
        self.assertIs(
            self.workspace.header.parentWidget(), self.workspace.top_bar
        )
        self.assertIs(
            self.workspace.top_bar.layout().itemAt(
                self.workspace.top_bar.layout().count() - 1
            ).widget(),
            self.workspace.header,
            "状态簇必须是顶栏的最后一项",
        )
        self.assertEqual(
            self.workspace.top_bar.height(),
            self.workspace.stage_rail().height(),
            "状态簇与阶段条共用一行，不能再多占一行高度",
        )
        self.assertLessEqual(
            self.workspace.header.height(),
            self.workspace.stage_rail().height(),
        )

    def test_run_identity_does_not_repeat_the_mode(self):
        self.workspace.set_run_identity("run-1", "有驱放测试")

        self.assertEqual(
            self.workspace.run_identity_label.text(), "本次运行 run-1"
        )
        self.assertNotIn(
            "有驱放测试",
            self.workspace.run_identity_label.text(),
            "模式只由徽标显示，运行标识不再重复一遍",
        )

    def test_a_long_run_identity_is_elided_with_the_full_value_in_tooltip(self):
        run_id = "20261113T010203Z_" + "a" * 80
        self.workspace.set_run_identity(run_id, None)
        self.settle()

        label = self.workspace.run_identity_label
        self.assertIn("…", label.text(), "超出上限的 run id 必须中间省略")
        self.assertEqual(label.full_text(), f"本次运行 {run_id}")
        self.assertEqual(label.toolTip(), f"本次运行 {run_id}")


class CurrentStageTests(_WorkspaceTestCase):
    def test_set_current_stage_syncs_the_rail(self):
        self.workspace.set_current_stage("cable_loss")

        rail = self.workspace.stage_rail()
        self.assertEqual(rail.current_key(), "cable_loss")
        self.assertTrue(rail.indicator_visible())
        self.assertEqual(self.workspace.current_stage(), "cable_loss")
        self.assertFalse(self.workspace.is_utility_view())
        self.assertIsNone(self.workspace.stage_summary("cable_loss"))

        self.workspace.set_current_stage("amplifier")

        self.assertEqual(rail.current_key(), "amplifier")
        self.assertIsNone(self.workspace.stage_summary("amplifier"))

    def test_visualization_is_shown_as_a_read_only_view(self):
        self.workspace.set_current_stage("visualization")

        self.assertFalse(
            self.workspace.stage_rail().participates_in_run("visualization")
        )

    def test_stage_membership_query_covers_exactly_the_configured_stages(self):
        for stage in MEASUREMENT_STAGES:
            with self.subTest(key=stage.key):
                self.assertTrue(self.workspace.has_stage(stage.key))
        for key in ("test_configuration", "not_a_stage", ""):
            with self.subTest(key=key):
                self.assertFalse(self.workspace.has_stage(key))

    def test_stage_membership_query_rejects_non_strings(self):
        with self.assertRaises(TypeError):
            self.workspace.has_stage(None)

    def test_unknown_stage_key_is_rejected_without_touching_the_top_bar(self):
        self.workspace.set_current_stage("cable_loss")
        self.workspace.set_stage_summary(
            "cable_loss", StageSummary("6 频点", (StageMetric("频点", "6"),))
        )
        before = (
            self.workspace.current_stage(),
            self.workspace.stage_rail().current_key(),
            self.workspace.stage_summary("cable_loss"),
        )

        with self.assertRaises(KeyError):
            self.workspace.set_current_stage("not_a_stage")

        self.assertEqual(
            (
                self.workspace.current_stage(),
                self.workspace.stage_rail().current_key(),
                self.workspace.stage_summary("cable_loss"),
            ),
            before,
            "未知阶段不能把顶栏留在半更新状态",
        )

    def test_programmatic_sync_does_not_emit_navigation(self):
        events = []
        self.workspace.stage_selected.connect(events.append)

        self.workspace.set_current_stage("cable_loss")
        self.workspace.set_current_stage("amplifier")
        self.workspace.set_current_stage(None)

        self.assertEqual(events, [], "set_current_stage 是外部同步，不是用户导航")

    def test_stage_click_is_forwarded_without_writing_current(self):
        self.workspace.set_current_stage("cable_loss")
        events = []
        self.workspace.stage_selected.connect(events.append)

        self.click_stage("amplifier")

        self.assertEqual(events, ["amplifier"])
        self.assertEqual(
            self.workspace.stage_rail().current_key(),
            "cable_loss",
            "外壳只转发导航请求；窗口真正切页后才同步 current",
        )

        # 模拟窗口接受导航并完成切页后的同步。
        self.workspace.set_current_stage("amplifier")

        self.assertEqual(
            self.workspace.stage_rail().current_key(), "amplifier"
        )
        self.assertEqual(self.workspace.current_stage(), "amplifier")


class StageSubsetTests(_WorkspaceTestCase):
    """外壳的当前阶段只在构造时传入的阶段集合里查找。"""

    #: 只在本用例里存在的阶段：它不在 ``MEASUREMENT_STAGES`` 里，用来锁住
    #: “外壳不会偷偷回落到全局阶段表”。
    CUSTOM = StageDescriptor(
        key="overview",
        order=1,
        title="总览",
        subtitle="自定义阶段的展示文案",
        kind=None,
    )

    def create_workspace(self):
        return MeasurementWorkspace((self.CUSTOM,))

    def test_custom_stages_can_be_synced_to_the_rail(self):
        self.workspace.set_current_stage("overview")

        self.assertEqual(
            self.workspace.stage_rail().current_key(), "overview"
        )
        self.assertEqual(self.workspace.current_stage(), "overview")
        self.assertFalse(self.workspace.is_utility_view())
        self.assertIsNone(self.workspace.stage_summary("overview"))

    def test_default_stage_keys_are_unknown_in_a_custom_stage_set(self):
        for key in ("cable_loss", "visualization"):
            with self.subTest(key=key):
                self.assertFalse(self.workspace.has_stage(key))
                with self.assertRaises(KeyError):
                    self.workspace.set_current_stage(key)
                self.assertIsNone(
                    self.workspace.current_stage(),
                    "阶段集合之外的 key 不能留下半更新的顶栏",
                )
                self.assertIsNone(self.workspace.stage_summary(key))


class StageSummaryTests(_WorkspaceTestCase):
    """步骤 15：摘要由页面传入，外壳只排版到阶段条与页头。"""

    SUMMARY = StageSummary(
        "6 频点",
        (
            StageMetric("频点", "6"),
            StageMetric("当前频点", "3.5 GHz"),
            StageMetric("线缆", "4"),
        ),
    )

    def test_summary_is_written_to_the_rail_only(self):
        self.workspace.set_current_stage("cable_loss")

        self.workspace.set_stage_summary("cable_loss", self.SUMMARY)

        rail = self.workspace.stage_rail()
        self.assertEqual(rail.summary_for("cable_loss"), "6 频点")
        self.assertIs(self.workspace.stage_summary("cable_loss"), self.SUMMARY)
        self.assertFalse(
            hasattr(self.workspace, "summary_label"),
            "顶部只能有一个摘要展示位：页头副本已经删除",
        )

    def test_summary_is_kept_for_a_stage_that_is_not_current(self):
        self.workspace.set_current_stage("amplifier")

        self.workspace.set_stage_summary("cable_loss", self.SUMMARY)

        self.assertEqual(
            self.workspace.stage_rail().summary_for("cable_loss"), "6 频点"
        )
        self.assertIs(
            self.workspace.stage_summary("cable_loss"),
            self.SUMMARY,
            "没有显示的阶段也要保留摘要，切回时不用窗口再推一次",
        )

        self.workspace.set_current_stage("cable_loss")

        self.assertEqual(
            self.workspace.stage_rail().summary_for("cable_loss"), "6 频点"
        )
        self.assertEqual(self.workspace.current_stage(), "cable_loss")

    def test_not_started_summary_still_reads_as_not_started(self):
        self.workspace.set_current_stage("cable_loss")

        self.workspace.set_stage_summary(
            "cable_loss", StageSummary.not_started()
        )

        self.assertEqual(
            self.workspace.stage_rail().summary_for("cable_loss"), NOT_STARTED
        )
        summary = self.workspace.stage_summary("cable_loss")
        self.assertIsNotNone(summary)
        self.assertEqual(summary.headline, NOT_STARTED)

    def test_clearing_a_summary_removes_it_from_the_rail(self):
        self.workspace.set_current_stage("cable_loss")
        self.workspace.set_stage_summary("cable_loss", self.SUMMARY)

        self.workspace.set_stage_summary("cable_loss", None)

        self.assertIsNone(self.workspace.stage_summary("cable_loss"))
        self.assertEqual(
            self.workspace.stage_rail().summary_for("cable_loss"), ""
        )

    def test_utility_view_shows_no_stage_but_keeps_the_stored_summary(self):
        self.workspace.set_current_stage("cable_loss")
        self.workspace.set_stage_summary("cable_loss", self.SUMMARY)

        self.workspace.show_utility()

        self.assertIsNone(self.workspace.stage_rail().current_key())
        self.assertIs(self.workspace.stage_summary("cable_loss"), self.SUMMARY)

    def test_unknown_stage_and_invalid_payloads_are_rejected(self):
        with self.assertRaises(KeyError):
            self.workspace.set_stage_summary("not_a_stage", self.SUMMARY)
        with self.assertRaises(TypeError):
            self.workspace.set_stage_summary(42, self.SUMMARY)
        with self.assertRaises(TypeError):
            self.workspace.set_stage_summary("cable_loss", "6 频点")


class UtilityViewTests(_WorkspaceTestCase):
    def test_show_utility_lights_no_stage(self):
        self.workspace.set_current_stage("amplifier")

        self.workspace.show_utility()

        rail = self.workspace.stage_rail()
        self.assertIsNone(rail.current_key())
        self.assertFalse(rail.indicator_visible())
        self.assertTrue(
            all(not button.is_current() for button in rail.stage_buttons()),
            "测试配置不是第五个阶段，不能点亮任何阶段",
        )
        self.assertIsNone(self.workspace.current_stage())
        self.assertTrue(self.workspace.is_utility_view())

    def test_returning_from_a_utility_view_restores_the_stage_header(self):
        self.workspace.set_current_stage("cable_loss")
        self.workspace.show_utility()

        self.workspace.set_current_stage("cable_loss")

        self.assertFalse(self.workspace.is_utility_view())
        self.assertEqual(self.workspace.current_stage(), "cable_loss")

    def test_utility_view_disables_the_configuration_action(self):
        self.workspace.set_current_stage("cable_loss")
        self.assertTrue(self.workspace.configuration_btn.isEnabled())

        self.workspace.show_utility()

        self.assertFalse(
            self.workspace.configuration_btn.isEnabled(),
            "已经站在工具页上时“配置”没有去处，不能留下可点的空动作",
        )

        self.workspace.set_current_stage("cable_loss")

        self.assertTrue(self.workspace.configuration_btn.isEnabled())

    def test_disabled_configuration_action_emits_nothing(self):
        events = []
        self.workspace.configuration_requested.connect(
            lambda: events.append("configuration")
        )
        self.workspace.show_utility()

        self.workspace.configuration_btn.click()

        self.assertEqual(events, [])


class ContentSlotTests(_WorkspaceTestCase):
    def test_set_content_places_a_single_widget_in_the_slot(self):
        content = QLabel("占位内容")

        self.workspace.set_content(content)
        self.app.processEvents()

        self.assertIs(self.workspace.content_widget(), content)
        self.assertIs(content.parentWidget(), self.workspace.content_host)
        self.assertTrue(content.isVisible())
        self.assertEqual(self.workspace.content_host.layout().count(), 1)
        self.assertIsNone(
            self.workspace.current_stage(), "放内容不能顺带点亮阶段"
        )

    def test_replacing_content_detaches_the_old_widget_without_destroying_it(self):
        first = QLabel("第一块")
        second = QLabel("第二块")
        self.workspace.set_content(first)
        self.app.processEvents()

        self.workspace.set_content(second)
        self.app.processEvents()

        self.assertIs(self.workspace.content_widget(), second)
        self.assertIs(second.parentWidget(), self.workspace.content_host)
        self.assertIsNone(
            first.parentWidget(),
            "旧内容必须从布局与父级摘掉，留在别处时不能继续占位",
        )
        self.assertTrue(first.isHidden())
        self.assertEqual(
            first.text(), "第一块", "摘掉的是布局位置，不是页面本身"
        )
        self.assertEqual(self.workspace.content_host.layout().count(), 1)

    def test_clearing_content_detaches_it_and_accepts_it_back(self):
        content = QLabel("可复用内容")
        self.workspace.set_content(content)
        self.app.processEvents()

        self.workspace.set_content(None)
        self.app.processEvents()

        self.assertIsNone(self.workspace.content_widget())
        self.assertIsNone(content.parentWidget())
        self.assertEqual(self.workspace.content_host.layout().count(), 0)

        self.workspace.set_content(content)
        self.app.processEvents()

        self.assertIs(self.workspace.content_widget(), content)
        self.assertIs(content.parentWidget(), self.workspace.content_host)
        self.assertTrue(content.isVisible())

    def test_setting_the_same_content_twice_is_idempotent(self):
        content = QLabel("同一块")
        self.workspace.set_content(content)
        self.app.processEvents()

        self.workspace.set_content(content)
        self.app.processEvents()

        self.assertEqual(self.workspace.content_host.layout().count(), 1)
        self.assertIs(self.workspace.content_widget(), content)

    def test_content_slot_rejects_non_widgets_and_the_shell_itself(self):
        for value in (42, "content", object()):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    self.workspace.set_content(value)
        with self.assertRaises(ValueError):
            self.workspace.set_content(self.workspace)
        self.assertIsNone(self.workspace.content_widget())

    def test_content_slot_rejects_the_shell_structure(self):
        content = QLabel("已在槽里")
        self.workspace.set_content(content)
        self.app.processEvents()

        for part in (
            self.workspace.stage_rail(),
            self.workspace.header,
            self.workspace.progress_hairline,
            self.workspace.content_host,
            self.workspace.run_identity_label,
        ):
            with self.subTest(part=part.objectName()):
                with self.assertRaises(ValueError):
                    self.workspace.set_content(part)
                self.assertIs(
                    self.workspace.content_widget(),
                    content,
                    "拒绝之后原内容必须还留在槽里",
                )
                self.assertEqual(
                    self.workspace.content_host.layout().count(), 1
                )
                self.assertTrue(
                    self.workspace.isAncestorOf(part),
                    "结构控件必须留在外壳原位",
                )

    def test_a_parent_of_the_workspace_is_not_accepted(self):
        outer = QWidget()
        self.addCleanup(_destroy, outer)
        inner = MeasurementWorkspace(parent=outer)

        for action in (inner.set_content, inner.add_right_widget):
            with self.subTest(action=action.__name__):
                with self.assertRaises(ValueError):
                    action(outer)
        self.assertIsNone(inner.content_widget())


class RightStatusAreaTests(_WorkspaceTestCase):
    def test_added_widgets_keep_order_before_the_configuration_action(self):
        badge = QLabel("有驱放测试")
        hint = QLabel("依赖就绪")

        self.workspace.add_right_widget(badge)
        self.workspace.add_right_widget(hint)
        self.app.processEvents()

        self.assertEqual(self.workspace.right_widgets(), (badge, hint))
        status_area = self.workspace.run_identity_label.parentWidget()
        layout = status_area.layout()
        self.assertIs(badge.parentWidget(), status_area)
        self.assertIs(hint.parentWidget(), status_area)
        self.assertLess(
            layout.indexOf(badge), layout.indexOf(hint)
        )
        self.assertLess(
            layout.indexOf(hint), layout.indexOf(self.workspace.configuration_btn)
        )
        self.assertTrue(badge.isVisible())

    def test_re_adding_the_same_widget_does_not_duplicate_it(self):
        badge = QLabel("有驱放测试")
        self.workspace.add_right_widget(badge)
        count = self.workspace.run_identity_label.parentWidget().layout().count()

        self.workspace.add_right_widget(badge)

        self.assertEqual(
            self.workspace.run_identity_label.parentWidget().layout().count(),
            count,
        )
        self.assertEqual(self.workspace.right_widgets(), (badge,))

    def test_non_widget_and_self_are_rejected(self):
        with self.assertRaises(TypeError):
            self.workspace.add_right_widget("badge")
        with self.assertRaises(ValueError):
            self.workspace.add_right_widget(self.workspace)

    def test_right_area_rejects_the_shell_structure(self):
        badge = QLabel("有驱放测试")
        self.workspace.add_right_widget(badge)
        content = QLabel("内容")
        self.workspace.set_content(content)

        for part in (
            self.workspace.stage_rail(),
            self.workspace.header,
            self.workspace.progress_hairline,
            self.workspace.content_host,
            self.workspace.configuration_btn,
            content,
        ):
            with self.subTest(part=part.objectName()):
                with self.assertRaises(ValueError):
                    self.workspace.add_right_widget(part)
                self.assertEqual(
                    self.workspace.right_widgets(),
                    (badge,),
                    "拒绝之后右侧状态区不能多出控件",
                )
                self.assertIs(self.workspace.content_widget(), content)

    def test_configuration_action_emits_its_request(self):
        events = []
        self.workspace.configuration_requested.connect(
            lambda: events.append("configuration")
        )

        self.workspace.configuration_btn.click()
        self.app.processEvents()

        self.assertEqual(events, ["configuration"])


class ModeTextTests(unittest.TestCase):
    """驱动模式文案只有一份来源：页头与徽标不再各写一套字面量。"""

    def test_driver_mode_maps_to_the_shared_text(self):
        self.assertEqual(mode_text(True), "有驱放测试")
        self.assertEqual(mode_text(False), "无驱放测试")

    def test_unselected_mode_is_explicit(self):
        self.assertEqual(mode_text(None), "未选择测试模式")

    def test_mode_text_rejects_other_values(self):
        with self.assertRaises(TypeError):
            mode_text("有驱放测试")


class RunIdentityTests(_WorkspaceTestCase):
    def test_run_identity_shows_the_run_id_while_mode_stays_in_the_badge(self):
        self.workspace.set_run_identity("20261112T101010", "有驱放测试")

        self.assertEqual(
            self.workspace.run_identity(),
            ("20261112T101010", "有驱放测试"),
        )
        text = self.workspace.run_identity_label.text()
        self.assertIn("20261112T101010", text)
        self.assertNotIn("有驱放测试", text, "模式只由徽标显示")
        self.assertNotIn("未连接", text)

    def test_clearing_run_identity_falls_back_to_unconnected(self):
        self.workspace.set_run_identity("20261112T101010", "无驱放测试")

        self.workspace.set_run_identity(None, None)

        self.assertEqual(self.workspace.run_identity(), (None, None))
        self.assertEqual(self.workspace.run_identity_label.text(), "未连接")

    def test_run_identity_does_not_change_the_current_stage(self):
        self.workspace.set_current_stage("amplifier")

        self.workspace.set_run_identity("run-1", "有驱放测试")

        self.assertEqual(self.workspace.current_stage(), "amplifier")
        self.assertEqual(
            self.workspace.stage_rail().current_key(), "amplifier"
        )

    def test_run_identity_rejects_non_string_values(self):
        with self.assertRaises(TypeError):
            self.workspace.set_run_identity(123, "有驱放测试")
        with self.assertRaises(TypeError):
            self.workspace.set_run_identity("run-1", 123)


class StageStateDelegationTests(_WorkspaceTestCase):
    def test_stage_state_is_written_through_to_the_rail(self):
        self.workspace.set_stage_state("amplifier", StageState.ACTIVE)
        rail = self.workspace.stage_rail()

        self.assertIs(rail.state_for("amplifier"), StageState.ACTIVE)
        self.assertIs(
            rail.display_state_for("amplifier"), StageState.ACTIVE
        )

        self.workspace.set_stage_state("amplifier", "completed")

        self.assertIs(rail.state_for("amplifier"), StageState.COMPLETED)

    def test_disabled_and_read_only_stages_cannot_receive_run_states(self):
        with self.assertRaises(ValueError):
            self.workspace.set_stage_state("amplifier", StageState.DISABLED)
        with self.assertRaises(ValueError):
            self.workspace.set_stage_state(
                "visualization", StageState.PENDING
            )

    def test_disabling_a_stage_is_derived_as_disabled_not_a_run_state(self):
        self.workspace.set_stage_state("driver_mapping", StageState.COMPLETED)
        self.workspace.set_stage_enabled("driver_mapping", False)

        rail = self.workspace.stage_rail()
        self.assertFalse(rail.is_stage_enabled("driver_mapping"))
        self.assertIs(
            rail.display_state_for("driver_mapping"), StageState.DISABLED
        )
        self.assertIs(
            rail.state_for("driver_mapping"),
            StageState.COMPLETED,
            "停用只影响显示与交互，保存的运行状态不能丢",
        )


class AnimationSwitchTests(_WorkspaceTestCase):
    def test_disabling_animations_snaps_the_indicator_to_the_stage(self):
        self.workspace.set_animations_enabled(False)
        self.workspace.set_current_stage("cable_loss")
        self.workspace.set_current_stage("amplifier")
        self.app.processEvents()

        rail = self.workspace.stage_rail()
        self.assertFalse(rail.animations_enabled())
        self.assertFalse(rail.is_animating())
        indicator = rail.indicator_rect()
        self.assertIsNotNone(indicator)
        center = QRectF(rail.stage_button("amplifier").geometry()).center().x()
        self.assertAlmostEqual(indicator.center().x(), center, places=3)

    def test_enabling_animations_does_not_change_the_current_stage(self):
        self.workspace.set_current_stage("cable_loss")
        self.workspace.set_animations_enabled(False)
        self.workspace.set_animations_enabled(True)

        self.assertTrue(self.workspace.stage_rail().animations_enabled())
        self.assertEqual(self.workspace.current_stage(), "cable_loss")

    def test_animation_switch_rejects_non_bool(self):
        with self.assertRaises(TypeError):
            self.workspace.set_animations_enabled("yes")


class ProgressHairlineTests(_WorkspaceTestCase):
    """步骤 14：页头下方的 2px 发丝线只在活动测量时出现，不推动常驻框架。"""

    def setUp(self):
        super().setUp()
        self.settle()

    def hairline_pixel(self, ratio=0.5):
        hairline = self.workspace.progress_hairline
        image = hairline.grab().toImage()
        x = max(0, min(hairline.width() - 1, int(hairline.width() * ratio)))
        return QColor(image.pixel(x, 1))

    def test_idle_hairline_reserves_two_pixels_and_draws_nothing(self):
        hairline = self.workspace.progress_hairline
        self.assertEqual(hairline.height(), 2)
        self.assertFalse(self.workspace.progress_active())
        self.assertEqual(self.workspace.progress_value(), 0)
        self.assertEqual(hairline.fill_width(), 0)

        image = hairline.grab().toImage()
        colours = {
            QColor(image.pixel(x, 1)).name() for x in range(image.width())
        }
        self.assertEqual(
            len(colours),
            1,
            "空闲时整条发丝线只有一个颜色：没有已走段，也没有轨道",
        )
        self.assertNotEqual(
            self.hairline_pixel().name(),
            TOKENS["primary"].lower(),
            "空闲时不能画出一条正在推进的线",
        )

    def test_active_measurement_shows_progress_and_idle_hides_it(self):
        hairline = self.workspace.progress_hairline

        self.workspace.set_measurement_active(True)
        self.workspace.set_progress(62)
        self.settle()

        self.assertTrue(self.workspace.progress_active())
        self.assertEqual(self.workspace.progress_value(), 62)
        self.assertEqual(
            hairline.fill_width(),
            round(hairline.width() * 62 / 100.0),
        )
        self.assertEqual(
            self.hairline_pixel().name(),
            TOKENS["primary"].lower(),
            "活动测量时发丝线在页头下方真正画出来",
        )

        self.workspace.set_measurement_active(False)
        self.settle()

        self.assertFalse(self.workspace.progress_active())
        self.assertEqual(self.workspace.progress_value(), 0)
        self.assertNotEqual(
            self.hairline_pixel().name(), TOKENS["primary"].lower()
        )

    def test_hairline_sits_between_the_top_bar_and_the_content(self):
        top_bar = self.workspace.top_bar
        hairline = self.workspace.progress_hairline
        content = self.workspace.content_host

        top_bar_bottom = top_bar.geometry().bottom()
        self.assertGreaterEqual(hairline.geometry().top(), top_bar_bottom)
        self.assertLessEqual(hairline.geometry().bottom(), content.geometry().top())

    def test_toggling_activity_and_progress_does_not_move_the_frame(self):
        self.workspace.set_measurement_active(True)
        self.workspace.set_progress(40)
        self.settle()
        frame = (
            self.workspace.stage_rail().geometry(),
            self.workspace.header.geometry(),
            self.workspace.content_host.geometry(),
        )

        self.workspace.set_measurement_active(False)
        self.settle()
        self.assertEqual(
            (
                self.workspace.stage_rail().geometry(),
                self.workspace.header.geometry(),
                self.workspace.content_host.geometry(),
            ),
            frame,
            "发丝线不能因为显示/隐藏推动阶段条、状态簇或内容槽",
        )

    def test_progress_is_clamped_and_rejects_non_numbers(self):
        hairline = self.workspace.progress_hairline
        self.workspace.set_measurement_active(True)

        self.workspace.set_progress(-20)
        self.assertEqual(self.workspace.progress_value(), 0)

        self.workspace.set_progress(150)
        self.assertEqual(self.workspace.progress_value(), 100)

        for value in (True, "50", None, object()):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    self.workspace.set_progress(value)
        self.assertEqual(hairline.value(), 100, "非法载荷不能改写已有进度")

    def test_non_finite_progress_is_rejected_without_touching_the_value(self):
        hairline = self.workspace.progress_hairline
        self.workspace.set_measurement_active(True)
        self.workspace.set_progress(55)

        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.workspace.set_progress(value)

        self.assertEqual(
            hairline.value(), 55, "NaN/±inf 无法夹紧，只能拒绝且不改写已有进度"
        )

    def test_activation_restarts_the_line_from_zero(self):
        self.workspace.set_measurement_active(True)
        self.workspace.set_progress(100)
        self.workspace.set_measurement_active(False)

        self.workspace.set_measurement_active(True)

        self.assertEqual(
            self.workspace.progress_value(),
            0,
            "下一次测量不能先显示上一轮的 100%",
        )

    def test_late_progress_while_idle_is_ignored(self):
        self.workspace.set_measurement_active(True)
        self.workspace.set_progress(70)
        self.workspace.set_measurement_active(False)

        self.workspace.set_progress(100)

        self.assertEqual(
            self.workspace.progress_value(),
            0,
            "迟到的进度不能把已经收起的发丝线重新点亮",
        )
        self.assertFalse(self.workspace.progress_active())


class StageRevealDirectionTests(_WorkspaceTestCase):
    """步骤 13：切页进入方向只由阶段顺序决定，非阶段切换不播放过渡。"""

    def test_forward_switch_enters_from_the_right(self):
        self.assertIs(
            self.workspace.stage_reveal_direction("cable_loss", "amplifier"),
            RevealDirection.FROM_RIGHT,
            "向后切页（序号增大）从右侧轻移进来",
        )
        self.assertIs(
            self.workspace.stage_reveal_direction(
                "driver_mapping", "visualization"
            ),
            RevealDirection.FROM_RIGHT,
            "跨阶段跳转同样按序号而不是相邻关系判断方向",
        )

    def test_backward_switch_enters_from_the_left(self):
        self.assertIs(
            self.workspace.stage_reveal_direction("visualization", "amplifier"),
            RevealDirection.FROM_LEFT,
        )
        self.assertIs(
            self.workspace.stage_reveal_direction("amplifier", "cable_loss"),
            RevealDirection.FROM_LEFT,
        )

    def test_non_stage_switches_have_no_transition(self):
        for previous, current in (
            (None, "cable_loss"),
            ("cable_loss", None),
            ("test_configuration", "cable_loss"),
            ("cable_loss", "test_configuration"),
            ("cable_loss", "cable_loss"),
            ("not_a_stage", "amplifier"),
            (42, "amplifier"),
        ):
            with self.subTest(previous=previous, current=current):
                self.assertIsNone(
                    self.workspace.stage_reveal_direction(previous, current),
                    "首次显示、配置页与未知 key 都必须瞬时切换，且不能抛异常",
                )

    def test_direction_uses_the_shells_own_stage_set(self):
        workspace = MeasurementWorkspace(
            (
                StageDescriptor("first", 1, "第一段", "先做第一件事", None),
                StageDescriptor("second", 2, "第二段", "再做第二件事", None),
            )
        )
        workspace.resize(800, 400)
        self.addCleanup(_destroy, workspace)

        self.assertIs(
            workspace.stage_reveal_direction("first", "second"),
            RevealDirection.FROM_RIGHT,
        )
        self.assertIsNone(
            workspace.stage_reveal_direction("cable_loss", "amplifier"),
            "自定义阶段集合里查不到全局阶段，不能落到另一套顺序上",
        )


class PageRevealTests(_WorkspaceTestCase):
    """步骤 13：过渡只作用于内容槽里进入的那一页，且可被一键关闭。"""

    def setUp(self):
        super().setUp()
        self.content = QWidget()
        self.workspace.set_content(self.content)
        self.settle()

    def test_reveal_current_page_fades_in_the_entering_page(self):
        self.workspace.set_current_stage("cable_loss")
        self.workspace.set_current_stage("amplifier")

        handle = self.workspace.reveal_current_page()

        effect = self.content.graphicsEffect()
        self.assertIsInstance(effect, SlideFadeEffect)
        self.assertIs(handle.effect(), effect)
        self.assertIs(self.workspace.page_reveal_handle(), handle)
        self.assertEqual(effect.offset(), float(MOTION_SLIDE_PX))
        self.assertEqual(
            effect.background().name(),
            TOKENS["canvas"].lower(),
            "淡入要先铺页面底色，matplotlib 画布才不会透出黑底",
        )
        self.assertTrue(handle.is_running())
        self.assertIsNone(
            self.workspace.stage_rail().graphicsEffect(),
            "常驻阶段条不参与过渡",
        )

        handle.cancel()
        self.assertIsNone(self.content.graphicsEffect())

    def test_repeated_sync_does_not_replay_the_transition(self):
        self.workspace.set_current_stage("cable_loss")
        self.workspace.set_current_stage("amplifier")
        first = self.workspace.reveal_current_page()

        second = self.workspace.reveal_current_page()

        self.assertIsNone(second, "同一页被重复同步时不再重播")
        self.assertFalse(first.is_running())
        self.assertIsNone(self.content.graphicsEffect())

    def test_first_display_and_configuration_entries_have_no_transition(self):
        self.workspace.set_current_stage("cable_loss")
        self.assertIsNone(self.workspace.reveal_current_page())

        self.workspace.show_utility()
        self.workspace.set_current_stage("amplifier")

        self.assertIsNone(
            self.workspace.reveal_current_page(),
            "从测试配置页进入测量区不重演配置阶段的过场",
        )
        self.assertIsNone(self.content.graphicsEffect())

    def test_animation_switch_and_active_measurement_skip_the_transition(self):
        self.workspace.set_current_stage("cable_loss")
        self.workspace.set_current_stage("amplifier")
        self.assertIsNone(self.workspace.reveal_current_page(animate=False))

        self.workspace.set_animations_enabled(False)

        self.assertIsNone(self.workspace.reveal_current_page())
        self.assertIsNone(self.content.graphicsEffect())

    def test_replacing_content_stops_the_running_transition(self):
        self.workspace.set_current_stage("cable_loss")
        self.workspace.set_current_stage("amplifier")
        handle = self.workspace.reveal_current_page()

        replacement = QWidget()
        self.workspace.set_content(replacement)

        self.assertFalse(handle.is_running())
        self.assertIsNone(self.content.graphicsEffect())
        self.assertIsNone(self.workspace.page_reveal_handle())


class StableFrameTests(_WorkspaceTestCase):
    def test_rail_and_header_do_not_move_when_the_stage_changes(self):
        self.workspace.set_current_stage("cable_loss")
        self.settle()
        rail_geometry = self.workspace.stage_rail().geometry()
        header_geometry = self.workspace.header.geometry()
        content_geometry = self.workspace.content_host.geometry()

        self.workspace.set_current_stage("amplifier")
        self.settle()

        self.assertEqual(self.workspace.stage_rail().geometry(), rail_geometry)
        self.assertEqual(self.workspace.header.geometry(), header_geometry)
        self.assertEqual(
            self.workspace.content_host.geometry(),
            content_geometry,
            "阶段切换只换内容，不能把内容槽也推动",
        )

    def test_replacing_content_does_not_move_the_frame(self):
        first = QWidget()
        self.workspace.set_content(first)
        self.settle()
        rail_geometry = self.workspace.stage_rail().geometry()
        header_geometry = self.workspace.header.geometry()

        self.workspace.set_content(QWidget())
        self.settle()

        self.assertEqual(self.workspace.stage_rail().geometry(), rail_geometry)
        self.assertEqual(self.workspace.header.geometry(), header_geometry)


if __name__ == "__main__":
    unittest.main()
