"""阶段条控件的行为契约。

覆盖阶段按钮顺序、禁用态、键盘导航、状态/摘要/当前项独立更新与无当前项时的隐藏表现；
指示块动画和视觉强调的逐帧断言不再保留。
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
from presentation.qt.theme import (  # noqa: E402
    MOTION_BREATH_MS,
    MOTION_COMPLETE_MS,
    MOTION_STAGE_MS,
    TOKENS,
)


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
