"""线损页的离屏行为：图代替格、读数条跟随最新频点、阶段摘要与来源行。

页面用假绘图控件记录被推入的曲线数据，测试直接断言“页面已经显示的数据”，
不依赖 matplotlib 渲染细节；绘图控件本身的渲染行为由
``tests/ui/test_realtime_plot.py`` 覆盖。
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QWidget

from application.dto import CableLossMeasurementResult, CableLossRow
from presentation.qt.cable_loss_page import CableLossPage
from presentation.qt.measurement_console import CheckState
from presentation.qt.measurement_controller_contract import MeasurementCommand
from presentation.qt.measurement_controls import MeasurementAction
from presentation.qt.measurement_state import (
    MeasurementKind,
    MeasurementResultReference,
    MeasurementStatus,
)
from presentation.qt.realtime_plot import CABLE_LOSS_PANELS
from presentation.qt.stage_summary import NOT_STARTED


def _destroy_widget(widget):
    """销毁离屏顶层控件，避免 Qt 对象跨用例累积。"""
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)

    def emit(self, *args):
        for slot in tuple(self.slots):
            slot(*args)


class Signals:
    def __init__(self):
        for name in (
            "progress", "message", "data_update", "result", "finished",
            "stopped", "error", "step_pause", "rejected", "state_changed",
            "archival",
        ):
            setattr(self, name, Signal())


class Controller:
    def __init__(self):
        self.signals = Signals()
        self.calls = []
        self.state = type("State", (), {"kind": None, "status": MeasurementStatus.IDLE, "is_active": False})()

    def start_cable_loss(self, command):
        self.calls.append(("start", command))
        return True

    def stop(self):
        self.calls.append(("stop",))
        return True

    def continue_cable_loss_stage2(self):
        self.calls.append(("continue",))
        return True


class Dialog:
    def __init__(self, result):
        self.result = result

    def exec(self):
        return self.result


class Plot(QWidget):
    """记录页面推入的曲线数据与清空次数，代替真实 matplotlib 控件。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.updates = []
        self.clear_calls = 0
        self.panels = None

    def set_panels(self, panels):
        self.panels = tuple(panels)

    def update_plot(self, data):
        self.updates.append(data)

    def clear_plots(self):
        self.clear_calls += 1

    def latest_series(self):
        return self.updates[-1]["series"] if self.updates else {}


class CableLossPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = Controller()
        self.dialog_results = [QDialog.Accepted]
        self.dialog_types = []

        def dialog_factory(diagram_type, _parent):
            self.dialog_types.append(diagram_type)
            return Dialog(self.dialog_results.pop(0))

        self.logs = []
        self.errors = []
        self.run = type("PreparedRun", (), {
            "run_id": "run-cable-loss", "run_repository": object(),
            "run_directory": "runs/run-cable-loss",
            "hardware_connection_path": lambda self: None,
        })()
        self.page = CableLossPage(
            config_path_provider=lambda: "config.json",
            run_provider=lambda: self.run,
            connection_dialog_factory=dialog_factory,
            plot_widget_factory=lambda parent: Plot(parent),
            log_callback=self.logs.append,
            error_callback=self.errors.append,
            archival_run_id_provider=lambda: self.run.run_id,
        )
        self.page.bind_controller(self.controller)
        self.addCleanup(_destroy_widget, self.page)

        self.assertTrue(self.page.start_measurement_btn.text() == "开始测量")
        self.assertFalse(self.page.stop_cable_loss_btn.isEnabled())
        self.assertFalse(hasattr(self.page, "stage2_measurement_btn"))
        self.assertFalse(hasattr(self.page, "cable_loss_table"))
        self.assertEqual(self.page.cable_loss_plot.panels, CABLE_LOSS_PANELS)

    def test_start_is_forwarded_as_measurement_command(self):
        self.page.start_measurement()

        self.assertEqual(self.controller.calls[0][0], "start")
        command = self.controller.calls[0][1]
        self.assertIsInstance(command, MeasurementCommand)
        self.assertEqual(command.config_path, "config.json")
        self.assertIs(command.prepared_run, self.run)

    def test_legacy_run_does_not_start_measurement(self):
        self.page._run_provider = lambda: type(
            "LegacyRun", (), {"run_id": "legacy", "run_repository": object()}
        )()
        self.page.start_measurement()
        self.assertEqual(self.controller.calls, [])

    def test_realtime_and_structured_result_fill_the_same_frequency_point(self):
        """实时与结构化结果按频率合并：同一频点的四条线缆进入同一组曲线点。"""
        self.controller.signals.data_update.emit({
            "frequency": "2.4",
            "cable_losses": {"cable1": 1.234},
        })
        reference = MeasurementResultReference(
            "result-1",
            MeasurementKind.CABLE_LOSS,
            CableLossMeasurementResult(
                run_id="run-cable-loss",
                status=MeasurementStatus.COMPLETED,
                points=(
                    CableLossRow(
                        frequency_ghz="2.4", cable_losses={"cable2": 2.345}
                    ),
                ),
            ),
        )
        self.controller.signals.result.emit(reference)

        self.assertEqual(self.page.point_count(), 1)
        series = self.page.cable_loss_plot.latest_series()
        self.assertEqual(series["cable1"], ([2.4], [1.234]))
        self.assertEqual(series["cable2"], ([2.4], [2.345]))
        self.assertEqual(series["cable3"], ([], []))
        self.assertEqual(series["cable4"], ([], []))

    def test_plot_grows_one_curve_per_frequency(self):
        for frequency, loss in ((2.4, 1.0), (2.5, 1.2)):
            self.controller.signals.data_update.emit({
                "frequency": frequency,
                "cable_losses": {"cable1": loss},
            })

        series = self.page.cable_loss_plot.latest_series()

        self.assertEqual(series["cable1"], ([2.4, 2.5], [1.0, 1.2]))
        self.assertEqual(self.page.point_count(), 2)

    def test_same_frequency_updates_in_place(self):
        for loss in (1.0, 1.5):
            self.controller.signals.data_update.emit({
                "frequency": "2.4",
                "cable_losses": {"cable1": loss},
            })

        self.assertEqual(self.page.point_count(), 1)
        self.assertEqual(
            self.page.cable_loss_plot.latest_series()["cable1"], ([2.4], [1.5])
        )

    def test_pause_accept_continues_and_reject_stops(self):
        self.controller.state = type(
            "State", (), {"kind": MeasurementKind.CABLE_LOSS, "status": MeasurementStatus.WAITING_FOR_CONTINUE}
        )()
        self.controller.signals.step_pause.emit("请重新接线")
        self.assertEqual(self.controller.calls, [("continue",)])

        self.dialog_results.append(QDialog.Rejected)
        self.controller.signals.step_pause.emit("请重新接线")
        self.assertEqual(self.controller.calls, [("continue",), ("stop",)])

    def test_load_results_requests_window_owned_structured_result(self):
        requests = []
        self.page.load_result_requested.connect(lambda: requests.append(True))
        self.page.load_cable_results_btn.click()
        self.assertEqual(requests, [True])
        self.page.show_loaded_result(MeasurementResultReference(
            "loaded-result",
            MeasurementKind.CABLE_LOSS,
            CableLossMeasurementResult(
                run_id="run-cable-loss",
                status=MeasurementStatus.COMPLETED,
                points=(
                    CableLossRow(
                        frequency_ghz="3.5",
                        cable_losses={"cable1": 1.111, "cable4": 4.444},
                    ),
                ),
            ),
            source="result_service",
        ))

        self.assertEqual(self.page.point_count(), 1)
        series = self.page.cable_loss_plot.latest_series()
        self.assertEqual(series["cable1"], ([3.5], [1.111]))
        self.assertEqual(series["cable4"], ([3.5], [4.444]))

    def test_loaded_result_shows_the_selected_source_filename(self):
        self.page.show_loaded_result(
            MeasurementResultReference(
                "loaded-result",
                MeasurementKind.CABLE_LOSS,
                CableLossMeasurementResult(
                    run_id="run-cable-loss",
                    status=MeasurementStatus.COMPLETED,
                    points=(CableLossRow(frequency_ghz="3.5"),),
                ),
                source="stage_result",
            ),
            filename="20261111T010203Z_a1b2c3d4.json",
        )

        self.assertFalse(self.page.cable_loss_source_label.isHidden())
        self.assertIn(
            "20261111T010203Z_a1b2c3d4.json",
            self.page.cable_loss_source_label.text(),
        )

    def test_invalid_loaded_result_preserves_the_current_display(self):
        self.page.show_loaded_result(
            MeasurementResultReference(
                "loaded-result",
                MeasurementKind.CABLE_LOSS,
                CableLossMeasurementResult(
                    run_id="run-cable-loss",
                    status=MeasurementStatus.COMPLETED,
                    points=(CableLossRow(frequency_ghz="3.5", cable_losses={"cable1": 1.5}),),
                ),
                source="stage_result",
            ),
            filename="valid-result.json",
        )

        self.page.show_loaded_result(
            MeasurementResultReference(
                "invalid-result", MeasurementKind.CABLE_LOSS, object(), source="stage_result"
            ),
            filename="invalid-result.json",
        )

        self.assertEqual(self.page.point_count(), 1)
        self.assertEqual(
            self.page.cable_loss_plot.latest_series()["cable1"], ([3.5], [1.5])
        )
        self.assertEqual(self.page.cable_loss_source_label.text(), "来源文件：valid-result.json")
        self.assertIn("不是正式结果模型", self.errors[-1])

    def test_new_measurement_clears_the_selected_source_filename(self):
        self.page.show_loaded_result(
            MeasurementResultReference(
                "loaded-result",
                MeasurementKind.CABLE_LOSS,
                CableLossMeasurementResult(
                    run_id="run-cable-loss",
                    status=MeasurementStatus.COMPLETED,
                    points=(CableLossRow(frequency_ghz="3.5"),),
                ),
                source="stage_result",
            ),
            filename="20261111T010203Z_a1b2c3d4.json",
        )

        self.page.clear_results()

        self.assertTrue(self.page.cable_loss_source_label.isHidden())
        self.assertEqual(self.page.cable_loss_source_label.text(), "")

    def test_non_formal_result_is_reported_and_not_displayed(self):
        self.page.show_loaded_result(MeasurementResultReference(
            "legacy-result",
            MeasurementKind.CABLE_LOSS,
            {"cable_losses": {"3.5": {"cable1": 1.111}}},
            source="result_service",
        ))

        self.assertEqual(self.page.point_count(), 0)
        self.assertEqual(self.errors, ["线损测量结果不是正式结果模型，无法显示"])

    def test_connection_diagram_button_lives_in_measurement_controls(self):
        """接线说明只剩一个连接图入口：页面上不再有线路文字。"""
        from PySide6.QtWidgets import QFrame, QGroupBox, QTextEdit

        self.assertEqual(self.page.findChildren(QGroupBox), [])
        control = self.page.findChild(QFrame, "measurementSurface")
        self.assertIsNotNone(control)
        self.assertIs(
            self.page.show_cable_loss_diagram_btn.parentWidget(), control
        )
        self.assertFalse(hasattr(self.page, "show_path1_btn"))
        self.assertFalse(hasattr(self.page, "show_path2_btn"))
        self.assertEqual(self.page.findChildren(QTextEdit), [])

    def test_result_surface_holds_the_plot_source_and_source_label(self):
        from PySide6.QtWidgets import QFrame

        result = self.page.findChild(QFrame, "measurementResultSurface")
        self.assertIs(self.page.cable_loss_plot.parentWidget(), result)
        self.assertIs(self.page.cable_loss_source_label.parentWidget(), result)
        self.assertIs(self.page.readout.parentWidget(), result)
        self.assertFalse(self.page.cable_loss_plot.isHidden())

    def test_view_diagram_button_opens_the_combined_diagram(self):
        self.page.show_cable_loss_diagram_btn.click()
        self.assertEqual(self.dialog_types, ["cable_loss_paths"])

    def test_stage_summary_starts_at_not_started_without_a_fake_zero(self):
        summary = self.page.stage_summary()

        self.assertFalse(summary.has_data)
        self.assertEqual(summary.headline, NOT_STARTED)

    def test_readout_follows_the_latest_frequency_and_cable_values(self):
        self.controller.signals.data_update.emit({
            "frequency": "2.4",
            "cable_losses": {"cable1": 1.234},
        })

        readout = self.page.readout

        self.assertEqual(readout.frequency_text(), "2.4")
        self.assertEqual(readout.unit_text(), "GHz")
        self.assertEqual(
            [metric.value for metric in readout.metrics()],
            ["1.234", "—", "—", "—"],
        )
        self.assertEqual(
            [metric.label for metric in readout.metrics()],
            ["线缆1", "线缆2", "线缆3", "线缆4"],
        )

    def test_readout_moves_to_the_newest_frequency(self):
        for frequency, loss in (("2.4", 1.0), ("3.5", 3.0)):
            self.controller.signals.data_update.emit({
                "frequency": frequency,
                "cable_losses": {"cable1": loss},
            })

        self.assertEqual(self.page.readout.frequency_text(), "3.5")
        self.assertEqual(self.page.readout.metrics()[0].value, "3.000")

    def test_readout_shows_the_delta_to_the_previous_frequency(self):
        for frequency, loss in (("2.4", 1.0), ("2.5", 1.2)):
            self.controller.signals.data_update.emit({
                "frequency": frequency,
                "cable_losses": {"cable1": loss},
            })

        metric = self.page.readout.metrics()[0]

        self.assertEqual(metric.value, "1.200")
        self.assertEqual(metric.delta, "▲ 0.200")

    def test_no_delta_on_the_first_frequency(self):
        self.controller.signals.data_update.emit({
            "frequency": "2.4",
            "cable_losses": {"cable1": 1.0},
        })

        self.assertEqual(self.page.readout.metrics()[0].delta, "")

    def test_result_summary_reports_ranges_and_the_maximum(self):
        for frequency, loss in (("2.4", 1.0), ("2.5", 1.2)):
            self.controller.signals.data_update.emit({
                "frequency": frequency,
                "cable_losses": {"cable1": loss},
            })

        summary = self.page.cable_loss_summary_label

        self.assertFalse(summary.isHidden())
        self.assertIn("2 频点", summary.text())
        self.assertIn("线缆1 1.000–1.200 dB", summary.text())
        self.assertIn("最大 1.200 dB @ 2.5 GHz", summary.text())
        self.assertNotIn("条长", summary.text(), "表格的条长说明不该留在图表下面")

    def test_result_summary_hides_with_the_cleared_plot(self):
        self.controller.signals.data_update.emit({
            "frequency": "2.4",
            "cable_losses": {"cable1": 1.0},
        })

        self.page.clear_results()

        self.assertTrue(self.page.cable_loss_summary_label.isHidden())

    def test_stage_summary_follows_the_displayed_points(self):
        self.controller.signals.data_update.emit({
            "frequency": "2.4",
            "cable_losses": {"cable1": 1.234},
        })
        self.controller.signals.data_update.emit({
            "frequency": "3.5",
            "cable_losses": {"cable2": 2.345},
        })

        summary = self.page.stage_summary()

        self.assertEqual(summary.headline, "2 频点")

    def test_clear_results_empties_the_plot_and_returns_to_not_started(self):
        self.controller.signals.data_update.emit({
            "frequency": "2.4",
            "cable_losses": {"cable1": 1.234},
        })

        self.page.clear_results()

        self.assertEqual(self.page.point_count(), 0)
        self.assertEqual(self.page.cable_loss_plot.clear_calls, 1)
        summary = self.page.stage_summary()
        self.assertEqual(summary.headline, NOT_STARTED)

    def test_data_changes_announce_the_stage_summary(self):
        events = []
        self.page.stage_summary_changed.connect(lambda: events.append(True))

        self.controller.signals.data_update.emit({
            "frequency": "2.4",
            "cable_losses": {"cable1": 1.234},
        })
        self.page.clear_results()

        self.assertEqual(len(events), 2)

    def test_repeated_frequency_does_not_announce_a_new_headline(self):
        self.controller.signals.data_update.emit({
            "frequency": "2.4",
            "cable_losses": {"cable1": 1.0},
        })
        events = []
        self.page.stage_summary_changed.connect(lambda: events.append(True))

        self.controller.signals.data_update.emit({
            "frequency": "2.4",
            "cable_losses": {"cable1": 1.1},
        })

        self.assertEqual(events, [], "频点数没变，阶段条摘要不需要重发")

    def test_batch_result_loading_refreshes_the_summary_once(self):
        events = []
        self.page.stage_summary_changed.connect(lambda: events.append(True))
        reference = MeasurementResultReference(
            "result-batch",
            MeasurementKind.CABLE_LOSS,
            CableLossMeasurementResult(
                run_id="run-batch",
                status=MeasurementStatus.COMPLETED,
                points=(
                    CableLossRow(
                        frequency_ghz="2.4", cable_losses={"cable1": 1.0}
                    ),
                    CableLossRow(
                        frequency_ghz="3.5", cable_losses={"cable1": 2.0}
                    ),
                ),
            ),
        )

        self.controller.signals.result.emit(reference)

        self.assertEqual(len(events), 1, "批量载入只在循环结束后刷新一次")
        self.assertEqual(self.page.stage_summary().headline, "2 频点")
        self.assertEqual(
            self.page.cable_loss_plot.latest_series()["cable1"],
            ([2.4, 3.5], [1.0, 2.0]),
        )

    def test_run_console_shows_the_run_and_the_remaining_prerequisites(self):
        console = self.page.console

        self.assertEqual(console.run_id_text(), "run-cable-loss")
        self.assertIsNone(console.mode_text())
        self.assertEqual(console.archive_text(), "未配置")
        self.assertIs(console.check_state("执行会话"), CheckState.READY)
        self.assertIs(console.check_state("接线确认"), CheckState.OPTIONAL)
        self.assertIs(console.check_state("结果归档"), CheckState.WAITING)

    def test_console_follows_a_reconnected_run_on_activation(self):
        self.run = type("PreparedRun", (), {
            "run_id": "run-cable-loss-2", "run_repository": object(),
            "run_directory": "runs/run-cable-loss-2",
            "hardware_connection_path": lambda self: None,
        })()

        self.page.on_activated()

        self.assertEqual(self.page.console.run_id_text(), "run-cable-loss-2")

    def test_console_archive_check_turns_ready_when_a_location_is_configured(self):
        self.page._archival_location_provider = (
            lambda run_id, relative: f"runs/{run_id}"
        )

        self.page._refresh_console()

        self.assertEqual(
            self.page.console.archive_text(), "runs/run-cable-loss"
        )


if __name__ == "__main__":
    unittest.main()
