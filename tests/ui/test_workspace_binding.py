"""步骤 5 的接线契约：标签宿主与工作台外壳之间只有一条同步路径。

这里用真实 ``QTabWidget`` 和真实 ``MeasurementWorkspace``，不构造 ``MainWindow``
替身：绑定层本身就是被测对象，窗口只负责在 ``currentChanged`` 之后调用 ``sync``。
真实窗口的端到端行为在 ``tests/integration/test_main_window_flow.py``。

锁定的行为：

- ``navigate`` 只切页，不写页头；页头由切页后的 ``sync`` 写入；
- 未知 key、非字符串、不可见标签页都静默忽略，绝不抛异常打断信号分发；
- 阶段页点亮阶段；其余**已登记**页面按工具页显示自己的标签标题（不再抛
  ``KeyError``，新增报告/历史页无需改窗口）；
- 完全未登记的 widget 不改变页头，外壳留在上一个一致状态；
- 驱动映射的启用状态写穿到阶段条。
"""

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QTabWidget, QWidget  # noqa: E402

from presentation.qt.measurement_controller_contract import (  # noqa: E402
    ControllerState,
)
from presentation.qt.measurement_stage import (  # noqa: E402
    MEASUREMENT_STAGES,
    StageState,
)
from presentation.qt.measurement_state import (  # noqa: E402
    MeasurementKind,
    MeasurementResultReference,
    MeasurementStatus,
    MeasurementViewState,
)
from presentation.qt.measurement_workspace import (  # noqa: E402
    MeasurementWorkspace,
)
from presentation.qt.stage_summary import (  # noqa: E402
    NOT_STARTED,
    StageMetric,
    StageSummary,
)
from presentation.qt.workspace_binding import WorkspaceBinding  # noqa: E402


class _FakeSignal:
    """最小 Qt 信号替身：只记录槽并把载荷同步分发出去。"""

    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, value=None):
        for slot in tuple(self._slots):
            slot(value)


def _fake_controller(*, with_progress: bool = True):
    signals = SimpleNamespace(state_changed=_FakeSignal())
    if with_progress:
        signals.progress = _FakeSignal()
    return SimpleNamespace(signals=signals)


def _state(kind, status):
    """构造 controller 会发出的状态快照，供阶段状态接线使用。"""
    running = MeasurementViewState(kind).prepare().run()
    if status is MeasurementStatus.RUNNING:
        view = running
    elif status is MeasurementStatus.COMPLETED:
        view = running.finish(
            MeasurementResultReference("result-id", kind, {"value": 1})
        )
    elif status is MeasurementStatus.FAILED:
        view = running.fail("测量失败")
    elif status is MeasurementStatus.CANCELLED:
        view = running.stop("用户停止")
    else:
        raise AssertionError(f"未覆盖的状态: {status}")
    return ControllerState(kind, status, view)


def _destroy(widget):
    """关闭并销毁顶层控件，再投递排队的删除。"""
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


class _SummaryPage(QWidget):
    """带阶段摘要的最小页面替身：绑定层只依赖 ``stage_summary`` 与变更信号。"""

    def __init__(self, summary=None):
        super().__init__()
        self.stage_summary_changed = _FakeSignal()
        self._summary = summary or StageSummary.not_started()

    def set_summary(self, summary):
        self._summary = summary

    def stage_summary(self):
        return self._summary

    def notify_summary_changed(self):
        self.stage_summary_changed.emit()


class WorkspaceBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.workspace = MeasurementWorkspace(MEASUREMENT_STAGES)
        self.workspace.resize(960, 400)
        self.addCleanup(_destroy, self.workspace)

        self.tabs = QTabWidget()
        self.addCleanup(_destroy, self.tabs)

        self.config_page = QWidget()
        self.cable_loss_page = QWidget()
        self.driver_mapping_page = QWidget()
        self.amplifier_page = QWidget()
        # 已登记但不是阶段的页面：将来新增报告/历史页时的形态。
        self.report_page = QWidget()
        for page, title in (
            (self.config_page, "测试配置"),
            (self.cable_loss_page, "线损测量"),
            (self.driver_mapping_page, "驱动映射"),
            (self.amplifier_page, "功放测试"),
            (self.report_page, "测试报告"),
        ):
            self.tabs.addTab(page, title)
        self.tabs.setCurrentWidget(self.config_page)

        self.pages = {
            "test_configuration": self.config_page,
            "cable_loss": self.cable_loss_page,
            "driver_mapping": self.driver_mapping_page,
            "amplifier": self.amplifier_page,
            "test_report": self.report_page,
        }
        self.mode_badge = QLabel()
        self.addCleanup(_destroy, self.mode_badge)
        self.binding = WorkspaceBinding(
            self.workspace,
            self.tabs,
            self.pages,
            utility_subtitles={"test_configuration": "参数、频点与连接"},
            mode_badge=self.mode_badge,
        )

    def _select(self, page):
        self.tabs.setCurrentWidget(page)
        self.app.processEvents()

    # ---- 导航 ---------------------------------------------------------------

    def test_navigate_selects_the_matching_page_without_touching_the_header(self):
        self.binding.sync()
        self.assertTrue(self.workspace.is_utility_view())

        self.binding.navigate("amplifier")
        self.app.processEvents()

        self.assertIs(self.tabs.currentWidget(), self.amplifier_page)
        self.assertTrue(
            self.workspace.is_utility_view(),
            "导航只切页；页头要等切页后的 sync 才更新",
        )

        self.binding.sync()

        self.assertFalse(self.workspace.is_utility_view())
        self.assertEqual(self.workspace.current_stage(), "amplifier")

    def test_navigate_ignores_unknown_keys_hidden_tabs_and_non_strings(self):
        hidden_index = self.tabs.indexOf(self.amplifier_page)
        self.tabs.setTabVisible(hidden_index, False)

        for key in ("not_a_registered_page", "amplifier", None, 42):
            with self.subTest(key=key):
                self.binding.navigate(key)
                self.app.processEvents()
                self.assertIs(
                    self.tabs.currentWidget(),
                    self.config_page,
                    "未知阶段、不可见页面与非字符串都不能改变当前页",
                )

    # ---- 同步：阶段页 -------------------------------------------------------

    def test_sync_lights_the_stage_of_a_measurement_page(self):
        self._select(self.amplifier_page)

        self.binding.sync()

        rail = self.workspace.stage_rail()
        self.assertEqual(self.workspace.current_stage(), "amplifier")
        self.assertEqual(rail.current_key(), "amplifier")
        self.assertEqual(self.workspace.title_label.text(), "功放测试")
        self.assertIn("阶段 03 / 04", self.workspace.eyebrow_label.text())
        self.assertTrue(self.workspace.configuration_btn.isEnabled())

    # ---- 阶段摘要（步骤 15） -------------------------------------------------

    def _summary_binding(self, summary=None):
        page = _SummaryPage(
            summary
            or StageSummary("6 频点", (StageMetric("频点", "6"),))
        )
        tabs = QTabWidget()
        tabs.addTab(page, "线损测量")
        tabs.setCurrentWidget(page)
        self.addCleanup(_destroy, tabs)
        binding = WorkspaceBinding(
            self.workspace, tabs, {"cable_loss": page}
        )
        return page, binding

    def test_sync_pulls_the_page_stage_summary(self):
        _page, binding = self._summary_binding()

        binding.sync()

        self.assertEqual(self.workspace.current_stage(), "cable_loss")
        self.assertEqual(
            self.workspace.stage_rail().summary_for("cable_loss"), "6 频点"
        )
        self.assertIn("频点 6", self.workspace.summary_text())

    def test_page_summary_signal_updates_the_rail_without_navigating(self):
        page, binding = self._summary_binding()
        binding.sync()

        page.set_summary(StageSummary.not_started())
        page.notify_summary_changed()

        self.assertEqual(
            self.workspace.stage_rail().summary_for("cable_loss"),
            NOT_STARTED,
        )
        self.assertEqual(self.workspace.summary_text(), "")
        self.assertEqual(self.workspace.current_stage(), "cable_loss")

    def test_invalid_or_missing_summaries_leave_the_last_good_one(self):
        page, binding = self._summary_binding()
        binding.sync()

        page.set_summary("不是摘要")
        page.notify_summary_changed()

        def explode():
            raise RuntimeError("摘要读取失败")

        page.stage_summary = explode
        page.notify_summary_changed()
        page.stage_summary = None
        page.notify_summary_changed()
        binding.refresh_stage_summary("not_a_stage")
        binding.refresh_stage_summary(42)

        self.assertEqual(
            self.workspace.stage_rail().summary_for("cable_loss"), "6 频点"
        )

    def test_refresh_summaries_reads_every_stage_page(self):
        cable = _SummaryPage(
            StageSummary("6 频点", (StageMetric("频点", "6"),))
        )
        amplifier = _SummaryPage(
            StageSummary("2 频点", (StageMetric("频点", "2"),))
        )
        tabs = QTabWidget()
        tabs.addTab(cable, "线损测量")
        tabs.addTab(amplifier, "功放测试")
        tabs.setCurrentWidget(cable)
        self.addCleanup(_destroy, tabs)
        binding = WorkspaceBinding(
            self.workspace,
            tabs,
            {"cable_loss": cable, "amplifier": amplifier},
        )

        binding.refresh_summaries()

        rail = self.workspace.stage_rail()
        self.assertEqual(rail.summary_for("cable_loss"), "6 频点")
        self.assertEqual(rail.summary_for("amplifier"), "2 频点")

    # ---- 同步：工具页 -------------------------------------------------------

    def test_sync_shows_the_utility_header_for_the_configuration_page(self):
        self.binding.sync()

        self.assertTrue(self.workspace.is_utility_view())
        self.assertIsNone(self.workspace.current_stage())
        self.assertIsNone(self.workspace.stage_rail().current_key())
        self.assertEqual(
            self.workspace.title_label.text(),
            self.tabs.tabText(self.tabs.currentIndex()),
            "工具页标题取标签页已登记标题，不再写第二份字面量",
        )
        self.assertEqual(
            self.workspace.subtitle_label.text(), "参数、频点与连接"
        )

    def test_a_registered_non_stage_page_becomes_a_utility_page(self):
        """新增报告/历史这类页面时，页头用它自己的标签标题，不能抛 KeyError。"""
        self._select(self.report_page)

        self.binding.sync()

        self.assertTrue(self.workspace.is_utility_view())
        self.assertIsNone(self.workspace.current_stage())
        self.assertEqual(self.workspace.title_label.text(), "测试报告")
        self.assertEqual(
            self.workspace.subtitle_label.text(),
            "",
            "没有登记副标题的工具页只显示一行标题",
        )

    def test_an_unregistered_widget_leaves_the_header_untouched(self):
        self._select(self.cable_loss_page)
        self.binding.sync()
        before = (
            self.workspace.current_stage(),
            self.workspace.title_label.text(),
            self.workspace.eyebrow_label.text(),
        )

        stranger = QWidget()
        self.addCleanup(_destroy, stranger)
        self.tabs.addTab(stranger, "临时页")
        self._select(stranger)

        self.binding.sync()

        self.assertEqual(
            (
                self.workspace.current_stage(),
                self.workspace.title_label.text(),
                self.workspace.eyebrow_label.text(),
            ),
            before,
            "完全未登记的 widget 不能让外壳停在半更新的状态",
        )

    # ---- 阶段启用态 ---------------------------------------------------------

    def test_stage_enabled_is_written_through_to_the_rail(self):
        rail = self.workspace.stage_rail()

        self.binding.set_stage_enabled("driver_mapping", False)

        self.assertFalse(rail.is_stage_enabled("driver_mapping"))
        self.assertIs(
            rail.display_state_for("driver_mapping"), StageState.DISABLED
        )
        self.assertIsNone(
            self.workspace.current_stage(),
            "停用阶段不自动导航，也不写当前阶段",
        )

        self.binding.set_stage_enabled("driver_mapping", True)

        self.assertTrue(rail.is_stage_enabled("driver_mapping"))
        self.assertIs(
            rail.display_state_for("driver_mapping"), StageState.PENDING
        )

    # ---- 阶段运行状态 -------------------------------------------------------

    def _state_binding(self):
        controller = _fake_controller()
        binding = WorkspaceBinding(
            self.workspace, self.tabs, self.pages, controller=controller
        )
        return controller, binding

    def test_controller_states_are_written_to_the_rail(self):
        controller, binding = self._state_binding()
        rail = self.workspace.stage_rail()

        controller.signals.state_changed.emit(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.RUNNING)
        )
        self.assertIs(rail.state_for("cable_loss"), StageState.ACTIVE)

        controller.signals.state_changed.emit(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.COMPLETED)
        )
        self.assertIs(rail.state_for("cable_loss"), StageState.COMPLETED)
        self.assertIsNone(
            self.workspace.current_stage(),
            "阶段状态变化不改页头或导航",
        )
        self.assertEqual(binding.run_state().run_id(), None)

    def test_non_controller_payloads_are_ignored(self):
        controller, _binding = self._state_binding()
        rail = self.workspace.stage_rail()

        controller.signals.state_changed.emit(object())
        controller.signals.state_changed.emit(None)

        self.assertIs(rail.state_for("cable_loss"), StageState.PENDING)

    def test_measurement_activity_and_progress_drive_the_header_hairline(self):
        """步骤 14：发丝线跟随活动测量与已有进度信号，不新增数值来源。"""
        controller, _binding = self._state_binding()
        self.assertFalse(self.workspace.progress_active())

        controller.signals.state_changed.emit(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.RUNNING)
        )
        self.assertTrue(self.workspace.progress_active())

        controller.signals.progress.emit(37)
        self.assertEqual(self.workspace.progress_value(), 37)

        controller.signals.state_changed.emit(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.COMPLETED)
        )
        self.assertFalse(self.workspace.progress_active())
        self.assertEqual(self.workspace.progress_value(), 0)

    def test_invalid_progress_payloads_are_ignored(self):
        controller, _binding = self._state_binding()
        controller.signals.state_changed.emit(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.RUNNING)
        )

        for value in (None, "50", True, object()):
            with self.subTest(value=value):
                controller.signals.progress.emit(value)

        self.assertEqual(self.workspace.progress_value(), 0)

    def test_non_finite_progress_payloads_are_ignored(self):
        """信号槽不抛异常：NaN/±inf 在这里丢掉，发丝线保持上一个有效值。"""
        controller, _binding = self._state_binding()
        controller.signals.state_changed.emit(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.RUNNING)
        )
        controller.signals.progress.emit(42)

        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                controller.signals.progress.emit(value)

        self.assertEqual(self.workspace.progress_value(), 42)
        self.assertTrue(self.workspace.progress_active())

    def test_a_controller_without_progress_signal_does_not_raise(self):
        controller = _fake_controller(with_progress=False)
        WorkspaceBinding(
            self.workspace, self.tabs, self.pages, controller=controller
        )

        controller.signals.state_changed.emit(
            _state(MeasurementKind.AMPLIFIER, MeasurementStatus.RUNNING)
        )

        self.assertTrue(self.workspace.progress_active())
        self.assertIs(
            self.workspace.stage_rail().state_for("amplifier"),
            StageState.ACTIVE,
        )

    def test_starting_a_session_hides_the_hairline(self):
        _controller, binding = self._state_binding()
        binding.observe_measurement_state(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.RUNNING)
        )
        self.assertTrue(self.workspace.progress_active())

        binding.start_session(False)

        self.assertFalse(self.workspace.progress_active())
        self.assertEqual(self.workspace.progress_value(), 0)

    def test_new_run_clears_the_previous_stage_states(self):
        controller, binding = self._state_binding()
        rail = self.workspace.stage_rail()
        binding.bind_run_identity("run-1", True)
        controller.signals.state_changed.emit(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.COMPLETED)
        )
        self.assertIs(rail.state_for("cable_loss"), StageState.COMPLETED)

        binding.bind_run_identity("run-2", True)

        self.assertEqual(binding.run_state().run_id(), "run-2")
        self.assertIs(
            rail.state_for("cable_loss"),
            StageState.PENDING,
            "上一轮 run 的完成态不能带进新 run",
        )

    def test_refreshing_the_same_run_keeps_terminal_states(self):
        controller, binding = self._state_binding()
        rail = self.workspace.stage_rail()
        binding.bind_run_identity("run-1", True)
        controller.signals.state_changed.emit(
            _state(MeasurementKind.AMPLIFIER, MeasurementStatus.FAILED)
        )

        binding.sync_run_identity("run-1")

        self.assertIs(rail.state_for("amplifier"), StageState.FAILED)

    def test_a_stale_run_identity_clears_the_stage_states(self):
        controller, binding = self._state_binding()
        rail = self.workspace.stage_rail()
        binding.bind_run_identity("run-1", True)
        controller.signals.state_changed.emit(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.CANCELLED)
        )

        binding.sync_run_identity("run-old")

        self.assertEqual(self.workspace.run_identity(), (None, None))
        self.assertIsNone(binding.run_state().run_id())
        self.assertIs(rail.state_for("cable_loss"), StageState.PENDING)
        self.assertTrue(
            self._driver_tab_visible(),
            "阶段状态清空不能改变会话模式或驱动映射标签页",
        )

    def test_starting_a_session_clears_the_stage_states(self):
        controller, binding = self._state_binding()
        rail = self.workspace.stage_rail()
        binding.bind_run_identity("run-1", True)
        controller.signals.state_changed.emit(
            _state(MeasurementKind.CABLE_LOSS, MeasurementStatus.COMPLETED)
        )

        binding.start_session(False)

        self.assertIsNone(binding.run_state().run_id())
        self.assertIs(rail.state_for("cable_loss"), StageState.PENDING)

    # ---- 会话模式与运行身份 -------------------------------------------------

    def _driver_tab_visible(self):
        return self.tabs.isTabVisible(self.tabs.indexOf(self.driver_mapping_page))

    def test_start_session_writes_mode_to_badge_driver_tab_and_rail(self):
        rail = self.workspace.stage_rail()

        self.binding.start_session(False)

        self.assertEqual(self.mode_badge.text(), "无驱放测试")
        self.assertFalse(self._driver_tab_visible())
        self.assertFalse(rail.is_stage_enabled("driver_mapping"))
        self.assertIs(self.binding.driver_mode(), False)
        self.assertEqual(self.workspace.run_identity(), (None, None))

        self.binding.start_session(True)

        self.assertEqual(self.mode_badge.text(), "有驱放测试")
        self.assertTrue(self._driver_tab_visible())
        self.assertTrue(rail.is_stage_enabled("driver_mapping"))
        self.assertIs(self.binding.driver_mode(), True)

    def test_reset_identity_restores_the_unselected_state(self):
        rail = self.workspace.stage_rail()
        self.binding.bind_run_identity("run-1", True)

        self.binding.reset_identity()

        self.assertEqual(self.mode_badge.text(), "未选择测试模式")
        self.assertFalse(self._driver_tab_visible())
        self.assertFalse(rail.is_stage_enabled("driver_mapping"))
        self.assertIsNone(self.binding.driver_mode())
        self.assertEqual(self.workspace.run_identity(), (None, None))
        self.assertEqual(self.workspace.run_identity_label.text(), "未连接")

    def test_start_session_clears_a_previous_run_identity(self):
        self.binding.bind_run_identity("run-1", True)

        self.binding.start_session(False)

        self.assertEqual(self.workspace.run_identity(), (None, None))
        self.assertIs(self.binding.driver_mode(), False)

    def test_sync_run_identity_only_writes_the_associated_run(self):
        self.binding.bind_run_identity("run-1", True)

        self.binding.sync_run_identity("run-1")
        self.assertEqual(
            self.workspace.run_identity(), ("run-1", "有驱放测试")
        )

        self.binding.sync_run_identity("run-old")
        self.assertEqual(self.workspace.run_identity(), (None, None))
        self.assertTrue(
            self._driver_tab_visible(),
            "刷新页头身份不能改变会话模式或驱动映射标签页",
        )
        self.assertIs(self.binding.driver_mode(), True)

    def test_missing_badge_and_driver_page_do_not_raise(self):
        binding = WorkspaceBinding(
            self.workspace, self.tabs, {"cable_loss": self.cable_loss_page}
        )

        binding.start_session(True)
        binding.sync_run_identity("run-1")
        binding.reset_identity()

        self.assertIsNone(binding.driver_mode())
        self.assertEqual(self.workspace.run_identity(), (None, None))


if __name__ == "__main__":
    unittest.main()
