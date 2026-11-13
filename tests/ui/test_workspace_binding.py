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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget  # noqa: E402

from presentation.qt.measurement_stage import (  # noqa: E402
    MEASUREMENT_STAGES,
    StageState,
)
from presentation.qt.measurement_workspace import (  # noqa: E402
    MeasurementWorkspace,
)
from presentation.qt.workspace_binding import WorkspaceBinding  # noqa: E402


def _destroy(widget):
    """关闭并销毁顶层控件，再投递排队的删除。"""
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


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
        self.binding = WorkspaceBinding(
            self.workspace,
            self.tabs,
            self.pages,
            utility_subtitles={"test_configuration": "参数、频点与连接"},
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


if __name__ == "__main__":
    unittest.main()
