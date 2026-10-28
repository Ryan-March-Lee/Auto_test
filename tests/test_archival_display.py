"""步骤 6：主测试页面显示归档结果（离屏 Qt 页面行为）。"""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QWidget

from application.dto.archival import (
    ArchivalOutcome,
    ArchivalTestStatus,
    ArchivedArtifact,
)
from domain.run.models import ArtifactKind
from presentation.qt.amplifier_page import AmplifierPage
from presentation.qt.archival_status import TONE_SUCCESS, TONE_WARNING
from presentation.qt.archival_view import ArchivalStatusView
from presentation.qt.cable_loss_page import CableLossPage
from presentation.qt.driver_mapping_page import DriverMappingPage
from presentation.qt.measurement_controller import MeasurementController
from presentation.qt.measurement_controller_contract import MeasurementCommand
from presentation.qt.measurement_state import MeasurementKind, MeasurementStatus


RUN_ID = "20260101T000000Z_deadbeef"
CSV_PATH = "artifacts/amplifier.csv"
REPORT_PATH = "artifacts/amplifier_report.html"


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
            "progress", "message", "data_update", "result", "archival", "finished",
            "stopped", "error", "step_pause", "rejected", "state_changed",
        ):
            setattr(self, name, Signal())


class Controller:
    def __init__(self):
        self.signals = Signals()
        self.calls = []
        self.state = type(
            "State", (), {"kind": None, "status": MeasurementStatus.IDLE, "is_active": False}
        )()

    def _record(self, *call):
        self.calls.append(call)
        return True

    def start_amplifier(self, command):
        return self._record("start_amplifier", command)

    def start_cable_loss(self, command):
        return self._record("start_cable_loss", command)

    def start_driver_mapping(self, command):
        return self._record("start_driver_mapping", command)

    def stop(self):
        return self._record("stop")

    def cancel(self):
        return self._record("cancel")

    def emergency_stop(self):
        return self._record("emergency_stop")

    def continue_cable_loss(self):
        return self._record("continue_cable_loss")


class Dialog:
    def __init__(self, result=QDialog.Accepted):
        self.result = result

    def exec(self):
        return self.result


class Plot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

    def update_plot(self, _data):
        return None


def _run(run_id=RUN_ID):
    return type("PreparedRun", (), {
        "run_id": run_id, "run_repository": object(),
        "has_run_contract": True, "run_directory": f"runs/{run_id}",
        "hardware_connection_path": lambda self: None,
    })()


def _amplifier_outcome(
    *,
    csv: ArchivedArtifact | None = None,
    test_status: ArchivalTestStatus = ArchivalTestStatus.COMPLETED,
) -> ArchivalOutcome:
    return ArchivalOutcome(
        run_id=RUN_ID,
        stage="amplifier",
        test_status=test_status,
        formal_result_path="results/amplifier_measurement.json",
        artifacts=(
            csv or ArchivedArtifact.completed(ArtifactKind.CSV, CSV_PATH),
            ArchivedArtifact.completed(ArtifactKind.REPORT, REPORT_PATH),
        ),
        archived_at="2026-01-01T00:00:00+00:00",
    )


class _PublishedArchival:
    """页面测试共用的观察器：记录归档文案、按钮状态与打开位置调用。"""

    def __init__(self, page, locations=None):
        self.page = page
        self.locations = locations if locations is not None else []

    @property
    def view(self) -> ArchivalStatusView:
        return self.page.archival_view

    def headline(self):
        return self.view.headline_label.text()

    def detail(self):
        return self.view.detail_label.text()


def _location_provider(locations):
    """把归档位置提供器固定为可断言替身。"""

    def provider(run_id, relative_path=None):
        locations.append((run_id, relative_path))
        return f"/runs/{run_id}/{relative_path or ''}"

    return provider


class ArchivalStatusViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_actions_are_disabled_until_a_located_summary_arrives(self):
        view = ArchivalStatusView()
        self.assertFalse(view.open_location_btn.isEnabled())
        self.assertFalse(view.open_report_btn.isEnabled())
        self.assertIn("尚未执行归档", view.headline_label.text())

    def test_actions_follow_the_summary_and_use_the_injected_provider(self):
        locations = []

        def provider(run_id, relative_path=None):
            locations.append((run_id, relative_path))
            return f"/runs/{run_id}/{relative_path or ''}"

        view = ArchivalStatusView(location_provider=provider)
        from presentation.qt.archival_status import summarize_archival

        view.show_summary(summarize_archival(_amplifier_outcome()))
        self.assertTrue(view.open_location_btn.isEnabled())
        self.assertTrue(view.open_report_btn.isEnabled())

        with patch(
            "presentation.qt.archival_view.QDesktopServices.openUrl", return_value=True
        ) as open_url:
            self.assertTrue(view.open_report())
            self.assertTrue(view.open_location())

        self.assertEqual(open_url.call_count, 2)
        self.assertEqual(
            locations,
            [(RUN_ID, REPORT_PATH), (RUN_ID, None)],
        )

    def test_unavailable_location_is_reported_without_opening_anything(self):
        logs = []
        view = ArchivalStatusView(
            location_provider=lambda _run_id, _path=None: None,
            log_callback=logs.append,
        )
        from presentation.qt.archival_status import summarize_archival

        view.show_summary(summarize_archival(_amplifier_outcome()))
        with patch(
            "presentation.qt.archival_view.QDesktopServices.openUrl"
        ) as open_url:
            self.assertFalse(view.open_location())

        open_url.assert_not_called()
        self.assertEqual(len(logs), 1)
        self.assertIn(RUN_ID, logs[0])


class AmplifierPageArchivalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = Controller()
        self.locations = []
        self.page = AmplifierPage(
            config_path_provider=lambda: "config.json",
            run_provider=lambda: _run(),
            confirm_wiring=lambda: True,
            driver_mode_provider=lambda: True,
            connection_dialog_factory=lambda *_: Dialog(),
            plot_widget_factory=lambda parent: Plot(parent),
            error_callback=lambda _message: None,
            archival_location_provider=_location_provider(self.locations),
        )
        self.state = _PublishedArchival(self.page, self.locations)
        self.page.bind_controller(self.controller)

    def test_start_announces_automatic_archival(self):
        self.page.start_measurement()

        self.assertIn("自动归档", self.state.headline())
        self.assertFalse(self.state.view.open_location_btn.isEnabled())

    def test_progress_until_scan_end_promises_saving_and_generation(self):
        self.page.start_measurement()
        self.controller.state = type(
            "State",
            (),
            {"kind": MeasurementKind.AMPLIFIER, "status": MeasurementStatus.RUNNING, "is_active": True},
        )()

        self.controller.signals.progress.emit(60)
        self.assertIn("测量结束后将自动归档结果", self.state.headline())

        self.controller.signals.progress.emit(100)
        self.assertIn("正在保存结果", self.state.headline())
        self.assertFalse(self.state.view.open_location_btn.isEnabled())

    def test_completed_outcome_shows_success_and_enables_actions(self):
        self.controller.signals.archival.emit(_amplifier_outcome())

        self.assertIn("已自动归档", self.state.headline())
        self.assertEqual(self.state.view.summary.tone, TONE_SUCCESS)
        self.assertIn(RUN_ID, self.state.detail())
        self.assertTrue(self.state.view.open_location_btn.isEnabled())
        self.assertTrue(self.state.view.open_report_btn.isEnabled())

        with patch(
            "presentation.qt.archival_view.QDesktopServices.openUrl", return_value=True
        ):
            self.state.view.open_report_btn.click()
        self.assertEqual(self.state.locations, [(RUN_ID, REPORT_PATH)])

    def test_failed_artifact_keeps_a_misleading_success_away(self):
        self.controller.signals.archival.emit(
            _amplifier_outcome(csv=ArchivedArtifact.failed(ArtifactKind.CSV, "写入失败"))
        )

        self.assertIn("部分", self.state.headline())
        self.assertEqual(self.state.view.summary.tone, TONE_WARNING)
        self.assertIn(RUN_ID, self.state.detail())
        # 报告本身已生成完成，仍然可以打开；失败的是 CSV。
        self.assertTrue(self.state.view.open_report_btn.isEnabled())

    def test_outcome_of_another_stage_is_ignored(self):
        other = ArchivalOutcome(
            run_id=RUN_ID,
            stage="cable_loss",
            test_status=ArchivalTestStatus.PARTIAL,
            formal_result_path="inputs/cable_loss.json",
            artifacts=(
                ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
            ),
        )
        self.controller.signals.archival.emit(other)

        self.assertIn("尚未执行归档", self.state.headline())

    def test_finished_without_outcome_reports_unknown_status(self):
        self.page.start_measurement()
        self.controller.signals.finished.emit()

        self.assertIn("未收到归档结果", self.state.headline())
        self.assertEqual(self.state.view.summary.tone, TONE_WARNING)
        self.assertIn(RUN_ID, self.state.detail())
        self.assertFalse(self.page._archival_pending)

    def test_error_and_stop_show_distinct_prompts(self):
        self.page.start_measurement()
        self.controller.signals.error.emit("仪器无响应")

        self.assertIn("测试异常", self.state.headline())
        self.assertIn("仪器无响应", self.state.detail())
        self.assertFalse(self.page._archival_pending)

        self.page.start_measurement()
        self.controller.signals.stopped.emit("用户停止")

        self.assertIn("已停止", self.state.headline())
        self.assertIn("用户停止", self.state.detail())

    def test_outcome_that_is_not_a_formal_dto_is_reported(self):
        logs = []
        self.page._log_callback = logs.append
        self.page.archival_view._log_callback = logs.append
        self.page.start_measurement()
        bogus = type("Outcome", (), {"stage": "amplifier"})()
        self.controller.signals.archival.emit(bogus)

        self.assertIn("未收到归档结果", self.state.headline())
        self.assertTrue(any("归档结果无法显示" in line for line in logs))


class CableLossPageArchivalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = Controller()
        self.logs = []
        self.locations = []
        self.page = CableLossPage(
            config_path_provider=lambda: "config.json",
            run_provider=lambda: _run(),
            confirm_wiring=lambda: True,
            connection_dialog_factory=lambda *_: Dialog(),
            log_callback=self.logs.append,
            error_callback=lambda _message: None,
            archival_location_provider=_location_provider(self.locations),
        )
        self.state = _PublishedArchival(self.page, self.locations)
        self.page.bind_controller(self.controller)

    def test_stage_archive_is_announced_as_not_needing_deliverables(self):
        outcome = ArchivalOutcome(
            run_id=RUN_ID,
            stage="cable_loss",
            test_status=ArchivalTestStatus.PARTIAL,
            formal_result_path="inputs/cable_loss.json",
            artifacts=(
                ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
            ),
        )
        self.page.start_measurement()
        self.controller.signals.archival.emit(outcome)

        self.assertIn("阶段结果已自动归档", self.state.headline())
        self.assertFalse(self.state.view.open_report_btn.isEnabled())
        self.assertTrue(self.state.view.open_location_btn.isEnabled())

    def test_loading_a_historical_result_clears_the_archive_panel(self):
        self.page.start_measurement()
        self.page.show_loaded_result(
            type("Reference", (), {"kind": MeasurementKind.CABLE_LOSS, "value": None})()
        )

        self.assertIn("尚未执行归档", self.state.headline())


class DriverMappingPageArchivalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = Controller()
        self.locations = []
        self.page = DriverMappingPage(
            config_path_provider=lambda: "config.json",
            run_provider=lambda: _run(),
            confirm_wiring=lambda: True,
            connection_dialog_factory=lambda *_: Dialog(),
            plot_widget_factory=lambda parent: Plot(parent),
            error_callback=lambda _message: None,
            archival_location_provider=_location_provider(self.locations),
        )
        self.state = _PublishedArchival(self.page, self.locations)
        self.page.bind_controller(self.controller)

    def test_success_and_failure_prompts_are_displayed(self):
        outcome = ArchivalOutcome(
            run_id=RUN_ID,
            stage="driver_mapping",
            test_status=ArchivalTestStatus.PARTIAL,
            formal_result_path="inputs/driver_mapping.json",
            artifacts=(
                ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
            ),
        )
        self.controller.signals.archival.emit(outcome)
        self.assertIn("阶段结果已自动归档", self.state.headline())

        self.controller.signals.error.emit("扫描失败")
        self.assertIn("测试异常", self.state.headline())
        self.assertIn("扫描失败", self.state.detail())


class WorkerArchivalForwardingTests(unittest.TestCase):
    """worker 把应用用例的归档结果原样转发，没有结果时不发事件。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication

        cls.app = QCoreApplication.instance() or QCoreApplication([])

    class _Service:
        def __init__(self, outcome):
            self.last_result = {"results": {}}
            self.last_archival_outcome = outcome

        def measure_all_frequencies(self):
            return None

    def _run_worker(self, worker_type, factory_name, outcome):
        service = self._Service(outcome)
        worker = worker_type(
            "config.json",
            sleep_fn=lambda _seconds: None,
            prepared_run=_run(),
            measurement_factory=lambda *_args, **_kwargs: service,
        )
        received = []
        worker.signals.archival.connect(received.append)
        with patch(f"app.gui_runtime.{factory_name}"):
            worker.run()
        return received

    def test_worker_publishes_the_archival_outcome(self):
        from presentation.qt.workers import AmplifierWorker

        outcome = _amplifier_outcome()
        received = self._run_worker(
            AmplifierWorker, "create_amplifier_measurement", outcome
        )

        self.assertEqual(received, [outcome])

    def test_worker_without_archival_outcome_stays_silent(self):
        from presentation.qt.workers import AmplifierWorker

        received = self._run_worker(
            AmplifierWorker, "create_amplifier_measurement", None
        )

        self.assertEqual(received, [])


class ControllerArchivalForwardingTests(unittest.TestCase):
    """controller 只转发与当前测量类型对应的归档阶段。"""

    class _Worker:
        def __init__(self):
            self.signals = Signals()
            self.started = False

        def start(self):
            self.started = True

        def stop(self):
            return None

        def cancel(self):
            return None

        def emergency_stop(self):
            return None

    def setUp(self):
        self.workers = []

        def factory(_command, *, measurement_port=None):
            worker = self._Worker()
            self.workers.append(worker)
            return worker

        self.controller = MeasurementController(
            {kind: factory for kind in MeasurementKind}, lambda: "config.json"
        )
        self.received = []
        self.controller.signals.archival.connect(self.received.append)

    def test_matching_stage_is_forwarded_and_other_stages_are_dropped(self):
        self.assertTrue(
            self.controller.start_amplifier(MeasurementCommand("config.json"))
        )
        amplifier_outcome = _amplifier_outcome()
        self.workers[-1].signals.archival.emit(amplifier_outcome)

        self.assertEqual(self.received, [amplifier_outcome])

        self.workers[-1].signals.archival.emit(
            ArchivalOutcome(
                run_id=RUN_ID,
                stage="cable_loss",
                test_status=ArchivalTestStatus.PARTIAL,
                formal_result_path="inputs/cable_loss.json",
                artifacts=(
                    ArchivedArtifact.not_applicable(ArtifactKind.CSV),
                    ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
                ),
            )
        )
        self.assertEqual(len(self.received), 1)


if __name__ == "__main__":
    unittest.main()
