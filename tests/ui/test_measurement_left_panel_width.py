"""门禁：四个左栏页面等宽，且宽度是固定值。

线损、驱动映射、功放与数据可视化共用同一个“左侧控制栏 + 右侧结果面”结构。四个
页面只要有一个按自己的 sizeHint 取宽度，切页时整栏就会横向跳动；功放页的校准
说明曾经把左栏推到 417，而其余页面停在 400。因此宽度只能来自
:mod:`presentation.qt.theme` 的单一常量，本模块在装好主题后按真实渲染宽度回归，
并覆盖窗口变宽、变窄两种情况。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from presentation.qt.amplifier_page import AmplifierPage
from presentation.qt.cable_loss_page import CableLossPage
from presentation.qt.driver_mapping_page import DriverMappingPage
from presentation.qt.history_view_model import HistoryViewModel
from presentation.qt.status_panel import StatusPanel
from presentation.qt.theme import (
    MEASUREMENT_LEFT_PANEL_WIDTH,
    apply_theme,
)
from presentation.qt.visualization_page import VisualizationPage


class _Dialog:
    def exec(self):
        return QDialog.Accepted


class _Plot(QWidget):
    def update_plot(self, data):
        pass


def _prepared_run():
    return type(
        "PreparedRun",
        (),
        {
            "run_id": "run-left-panel",
            "run_repository": object(),
            "run_directory": "runs/run-left-panel",
            "hardware_connection_path": lambda self: None,
        },
    )()


class MeasurementLeftPanelWidthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # 真实运行时装的是全局主题；不装主题时按钮与文字度量不同，功放页的宽度
        # 会被低估，门禁就看不到页面之间的差异。
        apply_theme(cls.app)

    def setUp(self):
        common = dict(
            config_path_provider=lambda: "config.json",
            run_provider=_prepared_run,
            connection_dialog_factory=lambda *_: _Dialog(),
            error_callback=lambda _message: None,
        )
        self.pages = {
            "cable_loss": CableLossPage(plot_widget_factory=_Plot, **common),
            "driver_mapping": DriverMappingPage(plot_widget_factory=_Plot, **common),
            "amplifier": AmplifierPage(
                driver_mode_provider=lambda: True,
                plot_widget_factory=_Plot,
                **common,
            ),
            "visualization": VisualizationPage(history=HistoryViewModel()),
        }
        self.status_panels = []
        for page in self.pages.values():
            slot = getattr(page, "status_slot", None)
            if slot is not None:
                panel = StatusPanel()
                slot.mount(panel)
                self.status_panels.append(panel)
            page.resize(1400, 900)
            page.show()
        self.app.processEvents()

    def tearDown(self):
        for page in self.pages.values():
            page.close()
            page.deleteLater()
        for panel in self.status_panels:
            panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_left_panels_render_at_the_same_fixed_width(self):
        widths = {name: page.control_panel.width() for name, page in self.pages.items()}

        self.assertEqual(len(set(widths.values())), 1, widths)
        self.assertEqual(set(widths.values()), {MEASUREMENT_LEFT_PANEL_WIDTH}, widths)

    def test_fixed_width_survives_window_resize(self):
        for size in ((1100, 800), (1400, 900), (1800, 1000)):
            for page in self.pages.values():
                page.resize(*size)
            self.app.processEvents()
            widths = {
                name: page.control_panel.width()
                for name, page in self.pages.items()
            }
            self.assertEqual(
                set(widths.values()), {MEASUREMENT_LEFT_PANEL_WIDTH}, (size, widths)
            )


if __name__ == "__main__":
    unittest.main()
