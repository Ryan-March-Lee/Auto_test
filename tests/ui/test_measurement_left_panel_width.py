"""门禁：三个测量页左栏等宽。

线损、驱动映射、功放共用同一个“测量控制 + 状态面板”窄栏。三者宽度一旦各写各的
数字，切页时整栏就会横向跳动；宽度只能来自 :mod:`presentation.qt.theme` 的共用
常量，本模块按真实挂载状态面板后的渲染宽度做回归。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from presentation.qt.amplifier_page import AmplifierPage
from presentation.qt.cable_loss_page import CableLossPage
from presentation.qt.driver_mapping_page import DriverMappingPage
from presentation.qt.status_panel import StatusPanel
from presentation.qt.theme import (
    MEASUREMENT_LEFT_PANEL_MAX_WIDTH,
    MEASUREMENT_LEFT_PANEL_MIN_WIDTH,
)


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

    def setUp(self):
        common = dict(
            config_path_provider=lambda: "config.json",
            run_provider=_prepared_run,
            connection_dialog_factory=lambda *_: _Dialog(),
            error_callback=lambda _message: None,
        )
        self.pages = {
            "cable_loss": CableLossPage(**common),
            "driver_mapping": DriverMappingPage(plot_widget_factory=_Plot, **common),
            "amplifier": AmplifierPage(
                driver_mode_provider=lambda: True,
                plot_widget_factory=_Plot,
                **common,
            ),
        }
        self.status_panels = []
        for page in self.pages.values():
            panel = StatusPanel()
            page.status_slot.mount(panel)
            page.resize(1400, 900)
            page.show()
            self.status_panels.append(panel)
        self.app.processEvents()

    def tearDown(self):
        for page in self.pages.values():
            page.close()
            page.deleteLater()
        for panel in self.status_panels:
            panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_left_panels_render_at_the_same_width(self):
        widths = {name: page.control_panel.width() for name, page in self.pages.items()}

        self.assertEqual(len(set(widths.values())), 1, widths)
        self.assertEqual(set(widths.values()), {MEASUREMENT_LEFT_PANEL_MIN_WIDTH}, widths)



if __name__ == "__main__":
    unittest.main()
