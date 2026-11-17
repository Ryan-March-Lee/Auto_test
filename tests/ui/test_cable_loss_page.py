import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
)

from application.dto import CableLossMeasurementResult, CableLossRow
from application.dto.measurement_results import MeasurementType
from presentation.qt.cable_loss_page import CableLossPage
from presentation.qt.measurement_console import CheckState
from presentation.qt.measurement_controller_contract import (
    MeasurementCommand,
    MeasurementExecutionMode,
)
from presentation.qt.measurement_controls import MeasurementAction
from presentation.qt.measurement_state import (
    MeasurementKind,
    MeasurementResultReference,
    MeasurementStatus,
)
from presentation.qt.mock_replay import MOCK_SIMULATION_NOTICE, MockReplayCompletion
from presentation.qt.stage_summary import NOT_STARTED, StageMetric
from tests.support.simulation_sessions import simulation_session


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
            "archival", "simulation_completed",
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
        self.simulation = None
        self.run = type("PreparedRun", (), {
            "run_id": "run-cable-loss", "run_repository": object(),
            "run_directory": "runs/run-cable-loss",
            "hardware_connection_path": lambda self: None,
        })()
        self.page = CableLossPage(
            config_path_provider=lambda: "config.json",
            run_provider=lambda: (
                self.simulation if self.simulation is not None else self.run
            ),
            connection_dialog_factory=dialog_factory,
            simulation_session_provider=lambda: self.simulation,
            log_callback=self.logs.append,
            error_callback=self.errors.append,
            archival_run_id_provider=lambda: self.run.run_id,
        )
        self.page.bind_controller(self.controller)
        self.addCleanup(_destroy_widget, self.page)

        self.assertEqual(self.page.cable_loss_table.columnCount(), 5)
        self.assertEqual(self.page.start_measurement_btn.text(), "开始测量")
        self.assertFalse(self.page.stop_cable_loss_btn.isEnabled())
        self.assertFalse(hasattr(self.page, "stage2_measurement_btn"))

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

    def test_simulation_start_skips_wiring_confirmation_and_uses_the_session(self):
        self.simulation = simulation_session(MeasurementType.CABLE_LOSS)
        self.page.on_activated()
        self.page.start_measurement()

        self.assertEqual(self.dialog_types, [])
        command = self.controller.calls[0][1]
        self.assertIsInstance(command, MeasurementCommand)
        self.assertIs(command.mode, MeasurementExecutionMode.SIMULATION)
        self.assertIs(command.simulation_session, self.simulation)
        self.assertIsNone(command.prepared_run)
        self.assertEqual(self.page.console.mode_text(), "Mock 模拟回放")
        self.assertEqual(
            self.page.console.check_state("执行会话"), CheckState.READY
        )
        self.assertTrue(any("模拟回放开始" in line for line in self.logs))
        self.assertFalse(any("自动归档" in line for line in self.logs))
        self.assertTrue(
            any("线损测量仅展示" in line for line in self.logs)
        )

    def test_simulation_completion_shows_the_mock_notice_without_archival_wording(self):
        self.simulation = simulation_session(MeasurementType.CABLE_LOSS)
        self.page.start_measurement()
        completion = MockReplayCompletion(
            measurement_type=MeasurementType.CABLE_LOSS,
            title="线损模拟",
            result_filename="20261117T060245Z_9b2e7d10.json",
            result_path=Path("mock_data/cable_loss/20261117T060245Z_9b2e7d10.json"),
        )

        self.controller.signals.simulation_completed.emit(completion)

        self.assertTrue(any("模拟数据已加载" in line for line in self.logs))
        self.assertTrue(
            any(MOCK_SIMULATION_NOTICE in line for line in self.logs)
        )
        self.assertFalse(any("已自动归档" in line for line in self.logs))

    def test_simulation_completion_is_ignored_when_this_run_is_not_simulated(self):
        completion = MockReplayCompletion(
            measurement_type=MeasurementType.CABLE_LOSS,
            title="线损模拟",
            result_filename="20261117T060245Z_9b2e7d10.json",
            result_path=Path("mock_data/cable_loss/20261117T060245Z_9b2e7d10.json"),
        )

        self.controller.signals.simulation_completed.emit(completion)

        self.assertFalse(any("模拟数据已加载" in line for line in self.logs))
        self.assertFalse(self.page._archival_simulation)

    def test_simulation_stop_and_error_are_reported_as_mock(self):
        self.simulation = simulation_session(MeasurementType.CABLE_LOSS)
        self.page.start_measurement()

        self.controller.signals.stopped.emit("用户停止")
        self.controller.signals.error.emit("数据包损坏")

        self.assertTrue(any("模拟回放已停止" in line for line in self.logs))
        self.assertTrue(any("模拟回放失败" in line for line in self.logs))
        self.assertTrue(
            any(MOCK_SIMULATION_NOTICE in line for line in self.logs)
        )
        self.assertFalse(any("阶段结果已清理" in line for line in self.logs))
        self.assertFalse(any(line.startswith("[归档") for line in self.logs))


    def test_realtime_and_structured_result_fill_table(self):
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

        self.assertEqual(self.page.cable_loss_table.rowCount(), 1)
        self.assertEqual(self.page.cable_loss_table.item(0, 1).text(), "1.234")
        self.assertEqual(self.page.cable_loss_table.item(0, 2).text(), "2.345")

    def test_result_table_is_a_read_only_display_panel(self):
        self.controller.signals.data_update.emit({
            "frequency": "2.4",
            "cable_losses": {"cable1": 1.234},
        })

        table = self.page.cable_loss_table
        self.assertEqual(table.editTriggers(), QAbstractItemView.NoEditTriggers)
        for column in (0, 1):
            item = table.item(0, column)
            self.assertIsNotNone(item)
            self.assertFalse(item.flags() & Qt.ItemIsEditable)

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

        self.assertEqual(self.page.cable_loss_table.rowCount(), 1)
        self.assertEqual(self.page.cable_loss_table.item(0, 0).text(), "3.5")
        self.assertEqual(self.page.cable_loss_table.item(0, 1).text(), "1.111")
        self.assertEqual(self.page.cable_loss_table.item(0, 4).text(), "4.444")

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

    def test_invalid_loaded_result_preserves_the_current_selection(self):
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
            filename="valid-result.json",
        )

        self.page.show_loaded_result(
            MeasurementResultReference(
                "invalid-result", MeasurementKind.CABLE_LOSS, object(), source="stage_result"
            ),
            filename="invalid-result.json",
        )

        self.assertEqual(self.page.cable_loss_table.rowCount(), 1)
        self.assertEqual(self.page.cable_loss_table.item(0, 0).text(), "3.5")
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

        self.assertEqual(self.page.cable_loss_table.rowCount(), 0)
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





    def test_result_surface_holds_the_table_source_and_empty_state(self):
        from PySide6.QtWidgets import QFrame

        result = self.page.findChild(QFrame, "measurementResultSurface")
        self.assertIs(self.page.cable_loss_table.parentWidget(), result)
        self.assertIs(self.page.cable_loss_empty_hint.parentWidget(), result)
        self.assertIs(self.page.cable_loss_source_label.parentWidget(), result)
        # 空表也要显示表头：不能让用户以为表格没有加载。
        self.assertFalse(self.page.cable_loss_table.isHidden())
        self.assertFalse(self.page.cable_loss_empty_hint.isHidden())

    def test_view_diagram_button_opens_the_combined_diagram(self):
        self.page.show_cable_loss_diagram_btn.click()
        self.assertEqual(self.dialog_types, ["cable_loss_paths"])


    def test_stage_summary_starts_at_not_started_without_a_fake_zero(self):
        summary = self.page.stage_summary()

        self.assertFalse(summary.has_data)
        self.assertEqual(summary.headline, NOT_STARTED)
        self.assertEqual(
            summary.metrics, (StageMetric("频点", NOT_STARTED),)
        )
        self.assertEqual(self.page.stage_metrics_row.metrics(), summary.metrics)

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

    def test_readout_shows_the_delta_to_the_previous_frequency(self):
        for frequency, loss in (("2.4", 1.0), ("2.5", 1.2)):
            self.controller.signals.data_update.emit({
                "frequency": frequency,
                "cable_losses": {"cable1": loss},
            })

        metric = self.page.readout.metrics()[0]

        self.assertEqual(metric.value, "1.200")
        self.assertEqual(metric.delta, "▲ 0.200")

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
        self.assertIn("条长只表相对大小", summary.text())

    def test_result_summary_hides_with_the_cleared_table(self):
        self.controller.signals.data_update.emit({
            "frequency": "2.4",
            "cable_losses": {"cable1": 1.0},
        })

        self.page.clear_results()

        self.assertTrue(self.page.cable_loss_summary_label.isHidden())

    def test_stage_summary_follows_the_displayed_table(self):
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
        self.assertEqual(
            summary.metrics,
            (
                StageMetric("频点", "2"),
                StageMetric("当前频点", "3.5 GHz"),
                StageMetric("线缆", "2"),
            ),
        )
        self.assertEqual(self.page.stage_metrics_row.metrics(), summary.metrics)

    def test_selected_row_is_the_current_frequency(self):
        for frequency in ("2.4", "3.5"):
            self.controller.signals.data_update.emit({
                "frequency": frequency,
                "cable_losses": {"cable1": 1.0},
            })

        self.page.cable_loss_table.selectRow(0)

        self.assertEqual(
            self.page.stage_summary().metrics[1],
            StageMetric("当前频点", "2.4 GHz"),
        )

    def test_empty_table_returns_to_not_started(self):
        self.controller.signals.data_update.emit({
            "frequency": "2.4",
            "cable_losses": {"cable1": 1.234},
        })

        self.page.clear_results()

        summary = self.page.stage_summary()
        self.assertEqual(summary.headline, NOT_STARTED)
        self.assertEqual(
            self.page.stage_metrics_row.metrics(),
            (StageMetric("频点", NOT_STARTED),),
        )

    def test_data_changes_announce_the_stage_summary(self):
        events = []
        self.page.stage_summary_changed.connect(lambda: events.append(True))

        self.controller.signals.data_update.emit({
            "frequency": "2.4",
            "cable_losses": {"cable1": 1.234},
        })
        self.page.clear_results()

        self.assertEqual(len(events), 2)

    def test_selecting_a_row_updates_the_displayed_kpi_and_announces_it(self):
        for frequency in ("2.4", "3.5"):
            self.controller.signals.data_update.emit({
                "frequency": frequency,
                "cable_losses": {"cable1": 1.0},
            })
        events = []
        self.page.stage_summary_changed.connect(lambda: events.append(True))

        self.page.cable_loss_table.selectRow(0)

        self.assertEqual(
            self.page.stage_metrics_row.metrics()[1],
            StageMetric("当前频点", "2.4 GHz"),
        )
        self.assertTrue(events, "选中行改变当前频点时必须通知工作台")

    def test_clearing_the_selection_falls_back_to_the_last_frequency(self):
        for frequency in ("2.4", "3.5"):
            self.controller.signals.data_update.emit({
                "frequency": frequency,
                "cable_losses": {"cable1": 1.0},
            })
        self.page.cable_loss_table.selectRow(0)

        self.page.cable_loss_table.clearSelection()

        self.assertEqual(
            self.page.stage_metrics_row.metrics()[1],
            StageMetric("当前频点", "3.5 GHz"),
            "取消选择后回落到最后一行，与实时追加顺序一致",
        )


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
        self.assertEqual(
            self.page.stage_metrics_row.metrics(),
            (
                StageMetric("频点", "2"),
                StageMetric("当前频点", "3.5 GHz"),
                StageMetric("线缆", "1"),
            ),
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
        self.assertIs(
            self.page.console.check_state("结果归档"), CheckState.READY
        )


if __name__ == "__main__":
    unittest.main()
