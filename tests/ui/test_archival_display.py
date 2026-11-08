"""归档结果只写运行日志，不再占用测量页面的分组框（离屏 Qt 页面行为）。"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog, QGroupBox, QWidget

from application.dto.archival import (
    ArchivalOutcome,
    ArchivalTestStatus,
    ArchivedArtifact,
)
from domain.run.models import ArtifactKind
from presentation.qt.amplifier_page import AmplifierPage
from presentation.qt.archival_status import TONE_SUCCESS, TONE_WARNING
from presentation.qt.cable_loss_page import CableLossPage
from presentation.qt.driver_mapping_page import DriverMappingPage
from presentation.qt.measurement_controller import MeasurementController
from presentation.qt.measurement_controller_contract import MeasurementCommand
from presentation.qt.measurement_state import MeasurementKind, MeasurementStatus

from tests.contract_doubles import contract_run


RUN_ID = "20260101T000000Z_deadbeef"
OTHER_RUN_ID = "20260102T000000Z_0ther0id"
CSV_PATH = "artifacts/amplifier.csv"
REPORT_PATH = "artifacts/amplifier_report.html"
RUN_DIRECTORY = f"D:/runs/{RUN_ID}"

# 测量命令在构造期就要求有效运行契约（D1/D10）；本文件只验证归档展示，替身只提供契约。
_PREPARED_RUN = contract_run(RUN_ID)


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
        self.start_result = True
        self.start_error = None
        self.state = type(
            "State", (), {"kind": None, "status": MeasurementStatus.IDLE, "is_active": False}
        )()

    def _record(self, *call):
        self.calls.append(call)
        if self.start_error is not None:
            self.signals.error.emit(self.start_error)
            return False
        return self.start_result

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

    def continue_cable_loss_stage2(self):
        return self._record("continue_cable_loss_stage2")


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
        "run_directory": f"runs/{run_id}",
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


def _partial_outcome(stage: str, result_path: str) -> ArchivalOutcome:
    return ArchivalOutcome(
        run_id=RUN_ID,
        stage=stage,
        test_status=ArchivalTestStatus.PARTIAL,
        formal_result_path=result_path,
        artifacts=(
            ArchivedArtifact.not_applicable(ArtifactKind.CSV),
            ArchivedArtifact.not_applicable(ArtifactKind.REPORT),
        ),
    )


def _location_provider(locations):
    """把归档位置提供器固定为可断言替身。"""

    def provider(run_id, relative_path=None):
        locations.append((run_id, relative_path))
        return f"D:/runs/{run_id}/{relative_path or ''}"

    return provider


def _log_text(logs) -> str:
    return "\n".join(logs)


def _group_titles(page) -> list[str]:
    return [box.title() for box in page.findChildren(QGroupBox)]


class MeasurementPagesHaveNoArchiveBoxTests(unittest.TestCase):
    """三个测量页都不再保留“归档结果”分组框。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_archive_group_box_is_removed_from_all_measurement_pages(self):
        pages = (
            CableLossPage(
                config_path_provider=lambda: "config.json",
                run_provider=lambda: _run(),
                connection_dialog_factory=lambda *_: Dialog(),
            ),
            DriverMappingPage(
                config_path_provider=lambda: "config.json",
                run_provider=lambda: _run(),
                connection_dialog_factory=lambda *_: Dialog(),
                plot_widget_factory=lambda parent: Plot(parent),
            ),
            AmplifierPage(
                config_path_provider=lambda: "config.json",
                run_provider=lambda: _run(),
                driver_mode_provider=lambda: True,
                connection_dialog_factory=lambda *_: Dialog(),
                plot_widget_factory=lambda parent: Plot(parent),
            ),
        )
        for page in pages:
            with self.subTest(page=type(page).__name__):
                self.assertNotIn("归档结果", _group_titles(page))
                self.assertFalse(hasattr(page, "archival_view"))


class AmplifierPageArchivalLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = Controller()
        self.locations = []
        self.logs = []
        self.run = _run()
        self.page = AmplifierPage(
            config_path_provider=lambda: "config.json",
            run_provider=lambda: self.run,
            driver_mode_provider=lambda: True,
            connection_dialog_factory=lambda *_: Dialog(),
            plot_widget_factory=lambda parent: Plot(parent),
            log_callback=self.logs.append,
            error_callback=lambda _message: None,
            archival_location_provider=_location_provider(self.locations),
            archival_run_id_provider=lambda: self.run.run_id,
        )
        self.page.bind_controller(self.controller)

    def test_start_announces_automatic_archival(self):
        self.page.start_measurement()

        self.assertIn("测量结束后将自动归档结果", _log_text(self.logs))
        self.assertEqual(self.locations, [])

    def test_progress_until_scan_end_promises_saving_and_generation(self):
        self.page.start_measurement()
        self.controller.state = type(
            "State",
            (),
            {"kind": MeasurementKind.AMPLIFIER, "status": MeasurementStatus.RUNNING, "is_active": True},
        )()

        self.controller.signals.progress.emit(60)
        self.assertNotIn("正在保存结果", _log_text(self.logs))

        self.controller.signals.progress.emit(100)
        self.assertIn("正在保存结果", _log_text(self.logs))

    def test_completed_outcome_logs_success_and_the_run_directory(self):
        self.controller.signals.archival.emit(_amplifier_outcome())

        text = _log_text(self.logs)
        self.assertIn("主功放结果已自动归档", text)
        self.assertIn(CSV_PATH, text)
        self.assertIn(REPORT_PATH, text)
        self.assertIn(f"结果目录：{RUN_DIRECTORY}", text)
        self.assertEqual(self.page._archival_summary.tone, TONE_SUCCESS)

    def test_failed_artifact_logs_a_warning_instead_of_success(self):
        self.controller.signals.archival.emit(
            _amplifier_outcome(csv=ArchivedArtifact.failed(ArtifactKind.CSV, "写入失败"))
        )

        text = _log_text(self.logs)
        self.assertIn("[归档警告]", text)
        self.assertIn("部分", text)
        self.assertIn("写入失败", text)
        self.assertEqual(self.page._archival_summary.tone, TONE_WARNING)

    def test_outcome_of_another_stage_is_ignored(self):
        self.controller.signals.archival.emit(
            _partial_outcome("cable_loss", "inputs/cable_loss.json")
        )

        self.assertIsNone(self.page._archival_summary)
        self.assertEqual(self.logs, [])

    def test_finished_without_outcome_reports_unknown_status(self):
        self.page.start_measurement()
        self.logs.clear()
        self.controller.signals.finished.emit()

        self.assertIn("未收到归档结果", _log_text(self.logs))
        self.assertEqual(self.page._archival_summary.tone, TONE_WARNING)
        self.assertIn(RUN_ID, self.page._archival_summary.detail)
        self.assertFalse(self.page._archival_pending)

    def test_error_and_stop_show_distinct_prompts(self):
        self.page.start_measurement()
        self.controller.signals.error.emit("仪器无响应")

        self.assertIn("[归档错误]", _log_text(self.logs))
        self.assertIn("仪器无响应", _log_text(self.logs))
        self.assertFalse(self.page._archival_pending)

        self.logs.clear()
        self.page.start_measurement()
        self.controller.signals.stopped.emit("用户停止")

        self.assertIn("已停止", _log_text(self.logs))
        self.assertIn("用户停止", _log_text(self.logs))

    def test_stopped_summary_reports_the_saved_partial_result_path(self):
        partial_path = f"{RUN_DIRECTORY}/results/amplifier_partial.json"
        self.controller.state.view_state = SimpleNamespace(
            result=SimpleNamespace(
                value=SimpleNamespace(
                    result_reference={"archive_path": partial_path}
                )
            )
        )
        self.page.start_measurement()
        self.logs.clear()

        self.controller.signals.stopped.emit("用户停止")

        self.assertIn("已保存部分结果", _log_text(self.logs))
        self.assertIn(partial_path, _log_text(self.logs))

    def test_outcome_that_is_not_a_formal_dto_is_reported(self):
        self.page.start_measurement()
        self.logs.clear()
        bogus = type("Outcome", (), {"stage": "amplifier"})()
        self.controller.signals.archival.emit(bogus)

        self.assertIn("未收到归档结果", _log_text(self.logs))
        self.assertTrue(any("归档结果无法显示" in line for line in self.logs))

    def test_start_rejected_by_controller_leaves_no_waiting_log(self):
        self.controller.start_result = False

        self.page.start_measurement()

        self.assertNotIn("自动归档", _log_text(self.logs))
        self.assertIsNone(self.page._archival_summary)
        self.assertFalse(self.page._archival_pending)

    def test_start_failure_reported_by_controller_is_not_overwritten(self):
        self.controller.start_error = "用例构造失败: 缺少驱动映射输入"

        self.page.start_measurement()

        # controller 先发出 error 再返回 False：拒绝启动的中性提示不能覆盖失败原因。
        self.assertIn("测试异常", _log_text(self.logs))
        self.assertIn("用例构造失败", _log_text(self.logs))
        self.assertFalse(self.page._archival_pending)

    def test_rejected_start_keeps_the_previous_result_in_the_log(self):
        self.controller.signals.archival.emit(_amplifier_outcome())
        self.logs.clear()
        self.controller.start_result = False

        self.page.start_measurement()

        # 上一次测量的归档结果仍然成立，不能被一次被拒绝的启动清零。
        self.assertIsNotNone(self.page._archival_summary)
        self.assertIn("主功放测试未能启动", _log_text(self.logs))
        self.assertFalse(self.page._archival_pending)

    def test_switching_run_clears_the_previous_summary(self):
        self.page.start_measurement()
        self.controller.signals.archival.emit(_amplifier_outcome())
        self.logs.clear()

        self.run = _run(OTHER_RUN_ID)
        self.page.on_activated()

        self.assertIsNone(self.page._archival_summary)
        self.assertIn("已清除运行", _log_text(self.logs))


class CableLossPageArchivalLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = Controller()
        self.logs = []
        self.page = CableLossPage(
            config_path_provider=lambda: "config.json",
            run_provider=lambda: _run(),
            connection_dialog_factory=lambda *_: Dialog(),
            log_callback=self.logs.append,
            error_callback=lambda _message: None,
            archival_location_provider=_location_provider([]),
            archival_run_id_provider=lambda: RUN_ID,
        )
        self.page.bind_controller(self.controller)

    def test_stage_archive_is_announced_without_deliverables(self):
        self.page.start_measurement()
        self.controller.signals.archival.emit(
            _partial_outcome("cable_loss", "inputs/cable_loss.json")
        )

        text = _log_text(self.logs)
        self.assertIn("阶段结果已自动归档", text)
        self.assertIn("inputs/cable_loss.json", text)
        self.assertIn(f"结果目录：{RUN_DIRECTORY}", text)

    def test_loading_a_historical_result_clears_the_archive_summary(self):
        self.page.start_measurement()
        self.page.show_loaded_result(
            type("Reference", (), {"kind": MeasurementKind.CABLE_LOSS, "value": None})()
        )

        self.assertIsNone(self.page._archival_summary)

    def test_start_while_another_measurement_runs_keeps_the_page_neutral(self):
        self.controller.state = type(
            "State",
            (),
            {"kind": MeasurementKind.AMPLIFIER, "status": MeasurementStatus.RUNNING, "is_active": True},
        )()

        self.page.start_measurement()

        self.assertEqual(self.controller.calls, [])
        self.assertIsNone(self.page._archival_summary)
        self.assertIn("已有测量正在运行", self.logs)

    def test_rejected_start_does_not_leave_a_waiting_log(self):
        self.controller.start_result = False

        self.page.start_measurement()

        self.assertIsNone(self.page._archival_summary)
        self.assertFalse(self.page._archival_pending)
        self.assertIn("线损测量未能启动，未开始测量", self.logs)


class DriverMappingPageArchivalLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.controller = Controller()
        self.logs = []
        self.page = DriverMappingPage(
            config_path_provider=lambda: "config.json",
            run_provider=lambda: _run(),
            connection_dialog_factory=lambda *_: Dialog(),
            plot_widget_factory=lambda parent: Plot(parent),
            log_callback=self.logs.append,
            error_callback=lambda _message: None,
            archival_location_provider=_location_provider([]),
            archival_run_id_provider=lambda: RUN_ID,
        )
        self.page.bind_controller(self.controller)

    def test_success_and_failure_prompts_are_logged(self):
        self.controller.signals.archival.emit(
            _partial_outcome("driver_mapping", "inputs/driver_mapping.json")
        )
        self.assertIn("阶段结果已自动归档", _log_text(self.logs))

        self.logs.clear()
        self.controller.signals.error.emit("扫描失败")
        self.assertIn("测试异常", _log_text(self.logs))
        self.assertIn("扫描失败", _log_text(self.logs))

    def test_start_rejected_by_controller_leaves_no_waiting_log(self):
        self.controller.start_result = False

        self.page.start_measurement()

        self.assertIsNone(self.page._archival_summary)
        self.assertFalse(self.page._archival_pending)
        self.assertIn("驱动映射测试未能启动，未开始测量", self.logs)

    def test_progress_100_announces_saving_until_the_outcome_arrives(self):
        self.page.start_measurement()
        self.controller.state = type(
            "State",
            (),
            {"kind": MeasurementKind.DRIVER_MAPPING, "status": MeasurementStatus.RUNNING, "is_active": True},
        )()

        self.controller.signals.progress.emit(100)
        self.assertIn("正在保存结果", _log_text(self.logs))


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
            self.controller.start_amplifier(MeasurementCommand("config.json", _PREPARED_RUN))
        )
        amplifier_outcome = _amplifier_outcome()
        self.workers[-1].signals.archival.emit(amplifier_outcome)

        self.assertEqual(self.received, [amplifier_outcome])

        self.workers[-1].signals.archival.emit(
            _partial_outcome("cable_loss", "inputs/cable_loss.json")
        )
        self.assertEqual(len(self.received), 1)


if __name__ == "__main__":
    unittest.main()
