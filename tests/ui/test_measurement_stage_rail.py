"""阶段条控件的契约（重构计划步骤 3）。

这里锁定用户能看见、后续外壳会依赖的六件事：

- 四个阶段按元数据顺序成为真实可聚焦控件，点击发出自己的 key；
- 未启用阶段继续显示原编号，但不可点击、不可聚焦，保存的运行状态不丢；
- Enter / Space 与 Tab 都能完成导航，没有鼠标也能切阶段；
- 状态、摘要、当前项三者可以独立更新，互不覆盖；
- 当前项让指示块出现，窗口缩放或关闭动效时它直接落到按钮中心；
- 配置页（``set_current(None)``）隐藏指示块并降低整条阶段条的强调。
"""

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (  # noqa: E402
    QCoreApplication,
    QEvent,
    QPointF,
    QRectF,
    Qt,
)
from PySide6.QtGui import QColor, QEnterEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from presentation.qt.measurement_stage import (  # noqa: E402
    MEASUREMENT_STAGES,
    StageState,
)
from presentation.qt.measurement_stage_rail import (  # noqa: E402
    MeasurementStageButton,
    MeasurementStageRail,
)
from presentation.qt.theme import MOTION_STAGE_MS, TOKENS  # noqa: E402


MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "presentation"
    / "qt"
    / "measurement_stage_rail.py"
)

RUN_STATES = (
    StageState.PENDING,
    StageState.ACTIVE,
    StageState.COMPLETED,
    StageState.STOPPED,
    StageState.FAILED,
)


def _hover(button):
    """把鼠标移进按钮；离屏环境没有真实的指针轨迹，直接送 enter 事件更确定。"""
    button.enterEvent(
        QEnterEvent(QPointF(5.0, 5.0), QPointF(5.0, 5.0), QPointF(5.0, 5.0))
    )


def _destroy(widget):
    """关闭并销毁顶层控件，再投递排队的删除（离屏用例的统一收尾方式）。"""
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class _RailTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.rail = MeasurementStageRail()
        self.rail.resize(720, self.rail.height())
        self.rail.show()
        self.app.processEvents()
        self.addCleanup(_destroy, self.rail)

    def center_of(self, key):
        button = self.rail.stage_button(key)
        return QRectF(button.geometry()).center().x()

    def rail_pixel(self, x, y):
        image = self.rail.grab().toImage()
        scale_x = image.width() / self.rail.width()
        scale_y = image.height() / self.rail.height()
        return QColor(image.pixel(int(x * scale_x), int(y * scale_y)))

    def darkest_title_pixel(self, key):
        """标题文字区域最暗的像素；字形实心笔画会落在纯文字色上。"""
        button = self.rail.stage_button(key)
        image = self.rail.grab().toImage()
        scale_x = image.width() / self.rail.width()
        scale_y = image.height() / self.rail.height()
        left = int((button.x() + 44) * scale_x)
        right = int((button.x() + button.width() - 32) * scale_x)
        top = int((button.y() + 8) * scale_y)
        bottom = int((button.y() + 26) * scale_y)
        darkest = QColor(255, 255, 255)
        for x in range(left, right):
            for y in range(top, bottom):
                colour = QColor(image.pixel(x, y))
                if colour.lightness() < darkest.lightness():
                    darkest = colour
        return darkest


class StageMetadataWiringTests(_RailTestCase):
    def test_every_stage_becomes_a_focusable_button_in_order(self):
        buttons = self.rail.stage_buttons()

        self.assertEqual(
            tuple(button.key() for button in buttons),
            tuple(stage.key for stage in MEASUREMENT_STAGES),
            "阶段条顺序必须与测量阶段元数据一致",
        )
        self.assertTrue(
            all(
                isinstance(button, MeasurementStageButton)
                for button in buttons
            )
        )
        for stage, button in zip(MEASUREMENT_STAGES, buttons):
            with self.subTest(key=stage.key):
                self.assertIs(button.descriptor(), stage)
                self.assertIs(self.rail.stage_button(stage.key), button)
                self.assertEqual(
                    button.focusPolicy(), Qt.FocusPolicy.StrongFocus
                )
                self.assertFalse(button.isCheckable())
                self.assertIn(f"{stage.order:02d}", button.accessibleName())
                self.assertIn(stage.title, button.accessibleName())

    def test_initial_state_has_no_current_stage_and_no_indicator(self):
        self.assertIsNone(self.rail.current_key())
        self.assertFalse(self.rail.indicator_visible())
        self.assertIsNone(self.rail.indicator_rect())
        self.assertTrue(
            all(
                not button.is_rail_emphasized()
                for button in self.rail.stage_buttons()
            ),
            "没有当前阶段时整条阶段条降低强调",
        )

    def test_programmatic_current_does_not_emit_navigation(self):
        events = []
        self.rail.stage_selected.connect(events.append)

        self.rail.set_current("amplifier")
        self.rail.set_current("cable_loss")
        self.rail.set_current(None)

        self.assertEqual(events, [], "set_current 是外部同步，不是用户导航")

    def test_constructor_rejects_an_empty_or_duplicated_stage_set(self):
        with self.assertRaises(ValueError):
            MeasurementStageRail(())
        with self.assertRaises(ValueError):
            MeasurementStageRail(
                (MEASUREMENT_STAGES[0], MEASUREMENT_STAGES[0])
            )


class InteractionTests(_RailTestCase):
    def test_clicking_a_stage_emits_its_key_without_writing_current(self):
        events = []
        self.rail.stage_selected.connect(events.append)
        button = self.rail.stage_button("amplifier")

        QTest.mouseClick(
            button, Qt.MouseButton.LeftButton, pos=button.rect().center()
        )

        self.assertEqual(events, ["amplifier"])
        self.assertIsNone(
            self.rail.current_key(),
            "阶段条是受控视图：点击只发导航请求，current 由窗口在切页后写入",
        )
        self.assertFalse(self.rail.indicator_visible())
        self.assertFalse(button.is_current())

        # 模拟窗口接受导航并完成切页后的同值同步。
        self.rail.set_current("amplifier")

        self.assertEqual(self.rail.current_key(), "amplifier")
        self.assertTrue(button.is_current())
        self.assertTrue(self.rail.indicator_visible())

    def test_rejected_navigation_keeps_the_previous_stage(self):
        events = []
        self.rail.stage_selected.connect(events.append)
        self.rail.set_animations_enabled(False)
        self.rail.set_current("cable_loss")
        amplifier = self.rail.stage_button("amplifier")

        QTest.mouseClick(
            amplifier, Qt.MouseButton.LeftButton, pos=amplifier.rect().center()
        )

        self.assertEqual(events, ["amplifier"])
        self.assertEqual(
            self.rail.current_key(),
            "cable_loss",
            "窗口没有接受导航时，阶段条不能预先指向别的页面",
        )
        self.assertTrue(self.rail.stage_button("cable_loss").is_current())
        self.assertFalse(amplifier.is_current())

    def test_a_disabled_stage_cannot_be_clicked(self):
        self.rail.set_stage_enabled("driver_mapping", False)
        events = []
        self.rail.stage_selected.connect(events.append)
        button = self.rail.stage_button("driver_mapping")

        QTest.mouseClick(
            button, Qt.MouseButton.LeftButton, pos=button.rect().center()
        )
        button.click()

        self.assertEqual(events, [])
        self.assertIsNone(self.rail.current_key())
        self.assertFalse(button.isEnabled())

    def test_a_disabled_stage_stays_visible_and_keeps_its_number(self):
        button = self.rail.stage_button("driver_mapping")
        before = button.sizeHint()

        self.rail.set_stage_enabled("driver_mapping", False)
        self.app.processEvents()

        self.assertTrue(button.isVisible(), "停用只是不可交互，不能被隐藏")
        self.assertEqual(button.sizeHint(), before, "停用不能改变阶段占位")
        self.assertEqual(button.descriptor().order, 2)
        self.assertIn("阶段 02", button.accessibleName())
        self.assertEqual(
            self.rail.display_state_for("driver_mapping"),
            StageState.DISABLED,
        )

    def test_cursor_follows_the_stage_enabled_state(self):
        button = self.rail.stage_button("driver_mapping")
        self.assertEqual(
            button.cursor().shape(), Qt.CursorShape.PointingHandCursor
        )

        self.rail.set_stage_enabled("driver_mapping", False)
        self.assertEqual(button.cursor().shape(), Qt.CursorShape.ArrowCursor)

        self.rail.set_stage_enabled("driver_mapping", True)
        self.assertEqual(
            button.cursor().shape(), Qt.CursorShape.PointingHandCursor
        )

    def test_stage_disable_intent_survives_a_parent_disable(self):
        self.rail.setEnabled(False)
        self.rail.set_stage_enabled("cable_loss", False)
        self.rail.setEnabled(True)
        self.app.processEvents()

        self.assertFalse(
            self.rail.is_stage_enabled("cable_loss"),
            "父级临时停用不能吞掉调用方的停用意图",
        )
        self.assertEqual(
            self.rail.display_state_for("cable_loss"),
            StageState.DISABLED,
        )

    def test_enter_and_space_activate_the_focused_stage(self):
        events = []
        self.rail.stage_selected.connect(events.append)
        cable = self.rail.stage_button("cable_loss")
        amplifier = self.rail.stage_button("amplifier")

        cable.setFocus()
        self.app.processEvents()
        self.assertTrue(cable.hasFocus())
        QTest.keyClick(cable, Qt.Key.Key_Return)
        self.assertEqual(events, ["cable_loss"])

        amplifier.setFocus()
        self.app.processEvents()
        QTest.keyClick(amplifier, Qt.Key.Key_Space)
        self.assertEqual(events, ["cable_loss", "amplifier"])

    def test_tab_moves_between_stages_and_skips_disabled_ones(self):
        cable = self.rail.stage_button("cable_loss")
        mapping = self.rail.stage_button("driver_mapping")
        amplifier = self.rail.stage_button("amplifier")

        cable.setFocus()
        self.app.processEvents()
        self.assertTrue(cable.hasFocus())

        QTest.keyClick(cable, Qt.Key.Key_Tab)
        self.app.processEvents()
        self.assertTrue(mapping.hasFocus(), "Tab 必须按显示顺序走到下一阶段")
        self.assertTrue(
            mapping.shows_focus_ring(),
            "键盘走到阶段按钮时必须画出焦点框",
        )

        self.rail.set_stage_enabled("driver_mapping", False)
        cable.setFocus()
        self.app.processEvents()
        QTest.keyClick(cable, Qt.Key.Key_Tab)
        self.app.processEvents()
        self.assertTrue(
            amplifier.hasFocus(), "未启用阶段不应成为键盘停靠点"
        )


class StateSummaryCurrentTests(_RailTestCase):
    def test_state_summary_and_current_update_independently(self):
        self.rail.set_current("amplifier")
        self.rail.set_state("amplifier", StageState.COMPLETED)
        self.rail.set_summary("amplifier", "6 频点")

        self.assertEqual(self.rail.current_key(), "amplifier")
        self.assertEqual(
            self.rail.state_for("amplifier"), StageState.COMPLETED
        )
        self.assertEqual(self.rail.summary_for("amplifier"), "6 频点")

        self.rail.set_state("cable_loss", StageState.FAILED)

        self.assertEqual(
            self.rail.current_key(), "amplifier", "改别的阶段不能移动当前项"
        )
        self.assertEqual(self.rail.summary_for("amplifier"), "6 频点")

        self.rail.set_summary("amplifier", "")

        self.assertEqual(self.rail.summary_for("amplifier"), "")
        self.assertEqual(
            self.rail.state_for("amplifier"), StageState.COMPLETED
        )

    def test_five_run_states_are_accepted_and_disabled_is_rejected(self):
        for state in RUN_STATES:
            with self.subTest(state=state):
                self.rail.set_state("cable_loss", state)
                self.assertEqual(
                    self.rail.state_for("cable_loss"), state
                )
                self.rail.set_state("cable_loss", state.value)
                self.assertEqual(
                    self.rail.state_for("cable_loss"), state
                )
                self.assertEqual(
                    self.rail.display_state_for("cable_loss"), state
                )

        last = self.rail.state_for("cable_loss")
        for value in (StageState.DISABLED, StageState.DISABLED.value):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.rail.set_state("cable_loss", value)
        self.assertEqual(
            self.rail.state_for("cable_loss"),
            last,
            "拒绝 DISABLED 后不能改掉已有运行状态",
        )

    def test_visualization_is_a_read_only_view_without_a_run_state(self):
        self.rail.set_animations_enabled(False)
        self.rail.set_current("visualization")
        self.app.processEvents()

        self.assertTrue(self.rail.indicator_visible())
        self.assertFalse(self.rail.participates_in_run("visualization"))
        self.assertFalse(
            self.rail.stage_button("visualization").shows_status_mark()
        )
        self.assertTrue(self.rail.participates_in_run("cable_loss"))
        self.assertTrue(
            self.rail.stage_button("cable_loss").shows_status_mark()
        )

        button = self.rail.stage_button("visualization")
        self.assertIn("只读", button.accessibleDescription())
        self.assertNotIn("未开始", button.accessibleDescription())

        for state in RUN_STATES:
            with self.subTest(state=state), self.assertRaises(ValueError):
                self.rail.set_state("visualization", state)
        self.assertEqual(
            self.rail.state_for("visualization"),
            StageState.PENDING,
            "只读阶段不保存运行状态",
        )

    def test_disabled_stage_keeps_its_saved_state(self):
        self.rail.set_state("driver_mapping", StageState.COMPLETED)
        self.rail.set_stage_enabled("driver_mapping", False)

        self.assertEqual(
            self.rail.state_for("driver_mapping"), StageState.COMPLETED
        )
        self.assertEqual(
            self.rail.display_state_for("driver_mapping"),
            StageState.DISABLED,
        )
        self.assertFalse(self.rail.is_stage_enabled("driver_mapping"))

        self.rail.set_stage_enabled("driver_mapping", True)

        self.assertTrue(self.rail.is_stage_enabled("driver_mapping"))
        self.assertEqual(
            self.rail.display_state_for("driver_mapping"),
            StageState.COMPLETED,
            "重新启用后必须恢复停用前的状态",
        )

    def test_disabling_the_current_stage_keeps_navigation_with_the_window(self):
        self.rail.set_animations_enabled(False)
        self.rail.set_current("driver_mapping")

        self.rail.set_stage_enabled("driver_mapping", False)

        self.assertEqual(
            self.rail.current_key(),
            "driver_mapping",
            "阶段条不能在停用时自行导航；窗口切换页面后再同步 current",
        )
        self.assertTrue(self.rail.indicator_visible())
        self.assertEqual(
            self.rail.display_state_for("driver_mapping"),
            StageState.DISABLED,
        )

        self.rail.set_current(None)
        self.assertFalse(self.rail.indicator_visible())

    def test_summary_changes_do_not_relayout_the_rail(self):
        self.rail.set_animations_enabled(False)
        self.rail.set_current("amplifier")
        self.app.processEvents()
        before_buttons = [
            button.geometry() for button in self.rail.stage_buttons()
        ]
        before_indicator = self.rail.indicator_rect()

        self.rail.set_summary("amplifier", "很长的阶段摘要" * 12)
        self.app.processEvents()

        self.assertEqual(
            [button.geometry() for button in self.rail.stage_buttons()],
            before_buttons,
            "摘要只是第二行文字，不能改变阶段按钮的布局",
        )
        self.assertEqual(
            self.rail.indicator_rect(),
            before_indicator,
            "摘要变化不能把指示块留在旧位置",
        )

    def test_accessible_text_reports_state_and_summary(self):
        button = self.rail.stage_button("amplifier")
        self.rail.set_state("amplifier", StageState.COMPLETED)
        self.rail.set_summary("amplifier", "6 频点")

        self.assertIn("阶段 03", button.accessibleName())
        self.assertIn("已完成", button.accessibleDescription())
        self.assertIn("6 频点", button.accessibleDescription())

    def test_accessible_text_marks_the_current_stage(self):
        amplifier = self.rail.stage_button("amplifier")
        cable = self.rail.stage_button("cable_loss")

        self.rail.set_current("amplifier")
        self.assertIn("当前阶段", amplifier.accessibleDescription())
        self.assertNotIn("当前阶段", cable.accessibleDescription())

        self.rail.set_current("cable_loss")
        self.assertNotIn("当前阶段", amplifier.accessibleDescription())
        self.assertIn("当前阶段", cable.accessibleDescription())

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(TypeError):
            self.rail.set_current(1)
        with self.assertRaises(KeyError):
            self.rail.set_current("unknown")
        with self.assertRaises(KeyError):
            self.rail.set_state("unknown", StageState.PENDING)
        with self.assertRaises(ValueError):
            self.rail.set_state("cable_loss", "waiting")
        with self.assertRaises(TypeError):
            self.rail.set_state("cable_loss", None)
        with self.assertRaises(TypeError):
            self.rail.set_summary("cable_loss", None)
        with self.assertRaises(KeyError):
            self.rail.set_summary("unknown", "x")
        with self.assertRaises(TypeError):
            self.rail.set_stage_enabled("cable_loss", 1)
        with self.assertRaises(KeyError):
            self.rail.set_stage_enabled("unknown", True)
        with self.assertRaises(TypeError):
            self.rail.set_animations_enabled(1)


class IndicatorTests(_RailTestCase):
    def test_no_current_stage_hides_the_indicator_and_dims_the_rail(self):
        self.rail.set_current("cable_loss")
        self.assertTrue(self.rail.indicator_visible())

        self.rail.set_current(None)

        self.assertIsNone(self.rail.current_key())
        self.assertFalse(self.rail.indicator_visible())
        self.assertIsNone(self.rail.indicator_rect())
        self.assertTrue(
            all(
                not button.is_rail_emphasized()
                for button in self.rail.stage_buttons()
            )
        )

    def test_indicator_sits_on_the_current_button_and_uses_theme_colors(self):
        self.rail.set_animations_enabled(False)
        self.rail.set_current("cable_loss")
        self.app.processEvents()

        button = self.rail.stage_button("cable_loss")
        rect = self.rail.indicator_rect()
        self.assertIsNotNone(rect)
        self.assertAlmostEqual(rect.center().x(), self.center_of("cable_loss"))

        # 指示块左边缘内侧、编号徽章左侧的空白：这里只应看到主色浅底。
        self.assertEqual(
            self.rail_pixel(
                button.x() + 6, button.y() + button.height() // 2
            ).name(),
            TOKENS["primary_soft"].lower(),
        )

    def test_indicator_moves_with_the_stage_animation(self):
        self.rail.set_animations_enabled(False)
        self.rail.set_current("cable_loss")
        self.rail.set_animations_enabled(True)

        self.rail.set_current("amplifier")
        animation = self.rail.indicator_animation()
        self.assertTrue(self.rail.is_animating())
        self.assertEqual(animation.duration(), MOTION_STAGE_MS)

        start = self.center_of("cable_loss")
        target = self.center_of("amplifier")
        animation.setCurrentTime(MOTION_STAGE_MS // 2)
        halfway = self.rail.indicator_rect().center().x()
        self.assertGreater(halfway, start)
        self.assertLess(halfway, target)

        animation.setCurrentTime(MOTION_STAGE_MS)
        self.assertFalse(self.rail.is_animating())
        self.assertAlmostEqual(
            self.rail.indicator_rect().center().x(), target
        )

    def test_same_current_does_not_interrupt_a_running_animation(self):
        self.rail.set_animations_enabled(False)
        self.rail.set_current("cable_loss")
        self.rail.set_animations_enabled(True)
        self.rail.set_current("amplifier")

        self.rail.set_current("amplifier")

        self.assertTrue(
            self.rail.is_animating(),
            "页面切换后的同值同步不能把指示块动画掐断",
        )

    def test_disabling_animations_lands_immediately(self):
        self.rail.set_animations_enabled(False)
        self.rail.set_current("driver_mapping")

        self.assertFalse(self.rail.is_animating())
        self.assertAlmostEqual(
            self.rail.indicator_rect().center().x(),
            self.center_of("driver_mapping"),
        )

    def test_disabling_animations_mid_flight_snaps_to_the_target(self):
        self.rail.set_animations_enabled(False)
        self.rail.set_current("cable_loss")
        self.rail.set_animations_enabled(True)
        self.rail.set_current("amplifier")
        self.assertTrue(self.rail.is_animating())

        self.rail.set_animations_enabled(False)

        self.assertFalse(self.rail.is_animating())
        self.assertAlmostEqual(
            self.rail.indicator_rect().center().x(),
            self.center_of("amplifier"),
        )

    def test_clearing_current_stops_a_running_animation(self):
        self.rail.set_animations_enabled(False)
        self.rail.set_current("cable_loss")
        self.rail.set_animations_enabled(True)
        self.rail.set_current("amplifier")
        self.assertTrue(self.rail.is_animating())

        self.rail.set_current(None)

        self.assertFalse(self.rail.is_animating())
        self.assertFalse(self.rail.indicator_visible())
        self.assertIsNone(self.rail.indicator_rect())

    def test_current_set_before_show_lands_on_the_first_layout(self):
        rail = MeasurementStageRail()
        self.addCleanup(_destroy, rail)
        rail.set_animations_enabled(False)
        rail.resize(720, rail.height())
        rail.set_current("amplifier")

        rail.show()
        self.app.processEvents()

        button = rail.stage_button("amplifier")
        self.assertIsNotNone(rail.indicator_rect())
        self.assertAlmostEqual(
            rail.indicator_rect().center().x(),
            QRectF(button.geometry()).center().x(),
            msg="首次显示时 showEvent 必须把指示块吸到当前阶段",
        )

    def test_resizing_keeps_the_indicator_on_the_current_button(self):
        self.rail.set_animations_enabled(False)
        self.rail.set_current("amplifier")
        self.app.processEvents()

        self.rail.resize(self.rail.width() + 180, self.rail.height())
        self.app.processEvents()

        button = self.rail.stage_button("amplifier")
        self.assertAlmostEqual(
            self.rail.indicator_rect().center().x(), self.center_of("amplifier")
        )
        self.assertAlmostEqual(
            self.rail.indicator_rect().width(),
            button.width() - 4.0,
            msg="指示块始终跟随按钮宽度，缩放后不能停在旧尺寸",
        )

    def test_resizing_during_animation_snaps_to_the_current_button(self):
        self.rail.set_animations_enabled(False)
        self.rail.set_current("cable_loss")
        self.rail.set_animations_enabled(True)
        self.rail.set_current("amplifier")
        self.assertTrue(self.rail.is_animating())

        self.rail.resize(self.rail.width() + 180, self.rail.height())
        self.app.processEvents()

        self.assertFalse(self.rail.is_animating())
        self.assertAlmostEqual(
            self.rail.indicator_rect().center().x(), self.center_of("amplifier")
        )


class VisualEmphasisTests(_RailTestCase):
    def test_no_current_stage_uses_secondary_text_without_transparency(self):
        self.rail.set_current(None)
        self.app.processEvents()

        self.assertEqual(
            self.darkest_title_pixel("driver_mapping").name(),
            TOKENS["text_secondary"].lower(),
            "无当前阶段时用令牌降级，不能把可点阶段的文字压到看不清",
        )

        self.rail.set_current("cable_loss")
        self.app.processEvents()

        self.assertEqual(
            self.darkest_title_pixel("cable_loss").name(),
            TOKENS["text"].lower(),
        )

    def test_hover_and_current_use_different_surface_tokens(self):
        self.rail.set_animations_enabled(False)
        self.rail.set_current("amplifier")
        self.app.processEvents()

        cable = self.rail.stage_button("cable_loss")
        amplifier = self.rail.stage_button("amplifier")
        _hover(cable)
        self.app.processEvents()

        self.assertEqual(
            self.rail_pixel(
                cable.x() + 6, cable.y() + cable.height() // 2
            ).name(),
            TOKENS["surface_sunken"].lower(),
            "悬停用中性浅底，不能和当前指示块撞色",
        )
        self.assertEqual(
            self.rail_pixel(
                amplifier.x() + 6, amplifier.y() + amplifier.height() // 2
            ).name(),
            TOKENS["primary_soft"].lower(),
        )


class StylingSourceTests(unittest.TestCase):
    def test_module_does_not_hardcode_colors(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotRegex(
            source,
            r"#[0-9A-Fa-f]{6}\b",
            "阶段条的颜色必须全部来自 theme.TOKENS",
        )


if __name__ == "__main__":
    unittest.main()
