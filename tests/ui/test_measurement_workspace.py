"""测量工作台外壳的功能契约。

覆盖阶段同步、自定义阶段集合、阶段摘要、工具页、内容槽替换、运行标识与阶段状态委托。
动画方向、逐帧过渡和像素稳定性不再纳入默认回归。
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


    def test_unset_run_identity_reads_as_unconnected(self):
        self.assertEqual(self.workspace.run_identity(), (None, None))
        self.assertEqual(
            self.workspace.run_identity_label.text(),
            "未连接",
            "没有运行时不能拼成“本次运行 未连接”这种自相矛盾的文案",
        )





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



    def test_clearing_a_summary_removes_it_from_the_rail(self):
        self.workspace.set_current_stage("cable_loss")
        self.workspace.set_stage_summary("cable_loss", self.SUMMARY)

        self.workspace.set_stage_summary("cable_loss", None)

        self.assertIsNone(self.workspace.stage_summary("cable_loss"))
        self.assertEqual(
            self.workspace.stage_rail().summary_for("cable_loss"), ""
        )


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



    def test_content_slot_rejects_non_widgets_and_the_shell_itself(self):
        for value in (42, "content", object()):
            with self.subTest(value=value):
                with self.assertRaises(TypeError):
                    self.workspace.set_content(value)
        with self.assertRaises(ValueError):
            self.workspace.set_content(self.workspace)
        self.assertIsNone(self.workspace.content_widget())




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






class ModeTextTests(unittest.TestCase):
    """驱动模式文案只有一份来源：页头与徽标不再各写一套字面量。"""

    def test_driver_mode_maps_to_the_shared_text(self):
        self.assertEqual(mode_text(True), "有驱放测试")
        self.assertEqual(mode_text(False), "无驱放测试")

    def test_unselected_mode_is_explicit(self):
        self.assertEqual(mode_text(None), "未选择测试模式")



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



class AnimationSwitchTests(_WorkspaceTestCase):

    def test_enabling_animations_does_not_change_the_current_stage(self):
        self.workspace.set_current_stage("cable_loss")
        self.workspace.set_animations_enabled(False)
        self.workspace.set_animations_enabled(True)

        self.assertTrue(self.workspace.stage_rail().animations_enabled())
        self.assertEqual(self.workspace.current_stage(), "cable_loss")



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








if __name__ == "__main__":
    unittest.main()
