"""阶段三步骤 8：Mock 逐点回放 worker 的事件顺序、结果与停止语义。"""

import json
import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from application.dto.measurement_results import MeasurementType
from application.dto.mock_scenario import MockDataEvent, MockProgressEvent
from application.inputs.mock_scenario import load_mock_scenario
from presentation.qt.mock_replay import MockReplayClock
from presentation.qt.mock_replay_worker import MockReplayWorker
from presentation.qt.simulation_session import SimulationStage

PROJECT_ROOT = Path(__file__).parents[2]
MOCK_DATA = PROJECT_ROOT / "mock_data"
_STAGE_DIRECTORIES = {
    MeasurementType.CABLE_LOSS: "cable_loss",
    MeasurementType.DRIVER_POWER_MAPPING: "driver_mapping",
    MeasurementType.AMPLIFIER: "amplifier",
}


def _stage(measurement_type):
    directory = _STAGE_DIRECTORIES[measurement_type]
    scenario_path = MOCK_DATA / directory / "scenario.json"
    scenario = load_mock_scenario(
        scenario_path, expected_measurement_type=measurement_type
    )
    return SimulationStage(
        measurement_type=measurement_type,
        scenario=scenario,
        scenario_path=scenario_path,
        result_path=MOCK_DATA / directory / scenario.result_filename,
    )


def _instant_clock(stop_event):
    return MockReplayClock(interval_s=0, stop_event=stop_event)


class _Recorder:
    """按固定间隔放行的 fake 时钟，记录每次等待时长。"""

    def __init__(self):
        self.intervals = []

    def __call__(self, seconds):
        self.intervals.append(seconds)
        return True


class MockReplayWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)

    def _worker(self, stage, *, clock_factory=_instant_clock):
        return MockReplayWorker(stage=stage, clock_factory=clock_factory)

    def test_all_three_scenarios_emit_data_progress_messages_and_results(self):
        cases = (
            MeasurementType.CABLE_LOSS,
            MeasurementType.DRIVER_POWER_MAPPING,
            MeasurementType.AMPLIFIER,
        )
        for measurement_type in cases:
            with self.subTest(measurement_type=measurement_type.value):
                stage = _stage(measurement_type)
                scenario = stage.scenario
                recorder = _Recorder()
                worker = self._worker(
                    stage,
                    clock_factory=lambda stop_event, recorder=recorder: MockReplayClock(
                        stop_event=stop_event, wait_fn=recorder
                    ),
                )
                messages = []
                progresses = []
                data = []
                results = []
                completions = []
                archived = []
                finished = []
                stopped = []
                worker.signals.message.connect(messages.append)
                # 线损场景在路径2前有 user_confirm 检查点；自动确认以覆盖完整回放。
                worker.signals.step_pause.connect(
                    lambda _text, worker=worker: (
                        worker.continue_stage2_measurement()
                    )
                )
                worker.signals.progress.connect(progresses.append)
                worker.signals.data_update.connect(data.append)
                worker.signals.result.connect(results.append)
                worker.signals.simulation_completed.connect(completions.append)
                worker.signals.archival.connect(archived.append)
                worker.signals.finished.connect(lambda: finished.append(True))
                worker.signals.stopped.connect(stopped.append)

                worker.run()

                expected_data = sum(
                    1
                    for phase in scenario.phases
                    for event in phase.events
                    if isinstance(event, MockDataEvent)
                )
                expected_progress = [
                    event.percent
                    for phase in scenario.phases
                    for event in phase.events
                    if isinstance(event, MockProgressEvent)
                ]
                self.assertEqual(len(data), expected_data)
                self.assertEqual(len(data), scenario.data_event_count)
                self.assertEqual(progresses, expected_progress)
                self.assertEqual(
                    recorder.intervals, [0.5] * (expected_data - 1)
                )
                self.assertTrue(all("frequency" in item for item in data))
                self.assertEqual(len(results), 1)
                self.assertIs(results[0].measurement_type, measurement_type)
                self.assertEqual(len(completions), 1)
                payload = completions[0]
                self.assertEqual(payload.measurement_type, measurement_type)
                self.assertEqual(payload.result_filename, scenario.result_filename)
                self.assertEqual(payload.result_path, stage.result_path)
                self.assertIsNotNone(payload.archival_summary)
                self.assertTrue(payload.simulated)
                self.assertIn("未写入正式结果", payload.notice)
                self.assertEqual(archived, [], "模拟链路不得发送正式归档结果")
                self.assertEqual(finished, [True])
                self.assertEqual(stopped, [])

    def _wait_for_checkpoint(self, worker):
        """worker 线程里的 Qt 信号在主线程排队；测试直接轮询等待标志。"""
        deadline = time.monotonic() + 5
        while not worker._waiting_for_continue and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(worker._waiting_for_continue, "回放没有停在路径2确认点")

    def test_cable_loss_waits_at_the_path2_checkpoint_until_confirmed(self):
        stage = _stage(MeasurementType.CABLE_LOSS)
        data = []
        results = []
        finished = []
        worker = self._worker(stage)
        worker.signals.data_update.connect(data.append)
        worker.signals.result.connect(results.append)
        worker.signals.finished.connect(lambda: finished.append(True))

        worker.start()
        try:
            self._wait_for_checkpoint(worker)
            self.assertEqual(finished, [])

            worker.continue_stage2_measurement()
            self.assertTrue(worker.wait(5000))
            QCoreApplication.processEvents()
        finally:
            if worker.isRunning():
                worker.stop()
                worker.wait(5000)

        self.assertEqual(len(data), 22)
        self.assertEqual(len(results), 1)
        self.assertEqual(finished, [True])

    def test_stopping_at_the_checkpoint_leaves_partial_data_without_finished(self):
        stage = _stage(MeasurementType.CABLE_LOSS)
        data = []
        results = []
        finished = []
        stopped = []
        worker = self._worker(stage)
        worker.signals.data_update.connect(data.append)
        worker.signals.result.connect(results.append)
        worker.signals.finished.connect(lambda: finished.append(True))
        worker.signals.stopped.connect(stopped.append)

        worker.start()
        try:
            self._wait_for_checkpoint(worker)
            worker.stop()
            self.assertTrue(worker.wait(5000))
            QCoreApplication.processEvents()
        finally:
            if worker.isRunning():
                worker.stop()
                worker.wait(5000)

        self.assertEqual(len(data), 11)
        self.assertEqual(results, [])
        self.assertEqual(finished, [])
        self.assertEqual(stopped, ["用户停止"])

    def test_stop_interrupts_the_data_point_wait_without_a_false_finish(self):
        stage = _stage(MeasurementType.CABLE_LOSS)
        worker = self._worker(stage)
        data = []
        results = []
        finished = []
        stopped = []
        worker.signals.data_update.connect(data.append)
        worker.signals.result.connect(results.append)
        worker.signals.finished.connect(lambda: finished.append(True))
        worker.signals.stopped.connect(stopped.append)

        def interrupt(_seconds):
            worker.stop()
            return False

        worker._clock_factory = lambda stop_event: MockReplayClock(
            stop_event=stop_event, wait_fn=interrupt
        )
        worker.run()

        self.assertEqual(len(data), 1)
        self.assertEqual(results, [])
        self.assertEqual(finished, [])
        self.assertEqual(stopped, ["用户停止"])

    def test_stop_requested_before_playback_sends_no_data_or_finish(self):
        worker = self._worker(_stage(MeasurementType.CABLE_LOSS))
        data = []
        finished = []
        stopped = []
        worker.signals.data_update.connect(data.append)
        worker.signals.finished.connect(lambda: finished.append(True))
        worker.signals.stopped.connect(stopped.append)
        worker.stop()

        worker.run()

        self.assertEqual(data, [])
        self.assertEqual(finished, [])
        self.assertEqual(stopped, ["用户停止"])

    def test_corrupted_scenario_fails_before_any_playback(self):
        stage = _corrupted_scenario_stage(Path(self._temporary.name))
        errors = []
        data = []
        finished = []
        worker = self._worker(stage)
        worker.signals.error.connect(errors.append)
        worker.signals.data_update.connect(data.append)
        worker.signals.finished.connect(lambda: finished.append(True))

        worker.run()

        self.assertEqual(data, [])
        self.assertEqual(finished, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("模拟回放失败", errors[0])
        self.assertIn("不是有效的 JSON", errors[0])

    def test_corrupted_formal_result_is_reported_after_playback(self):
        stage = _corrupted_result_stage(Path(self._temporary.name))
        errors = []
        results = []
        completions = []
        finished = []
        worker = self._worker(stage)
        worker.signals.step_pause.connect(
            lambda _text: worker.continue_stage2_measurement()
        )
        worker.signals.error.connect(errors.append)
        worker.signals.result.connect(results.append)
        worker.signals.simulation_completed.connect(completions.append)
        worker.signals.finished.connect(lambda: finished.append(True))

        worker.run()

        self.assertEqual(results, [])
        self.assertEqual(completions, [])
        self.assertEqual(finished, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("正式结果契约", errors[0])

    def test_result_file_must_match_the_scenario_completion(self):
        stage = _stage(MeasurementType.CABLE_LOSS)
        stage = type(stage)(
            measurement_type=stage.measurement_type,
            scenario=stage.scenario,
            scenario_path=stage.scenario_path,
            result_path=stage.scenario_path,
        )
        errors = []
        data = []
        worker = self._worker(stage)
        worker.signals.error.connect(errors.append)
        worker.signals.data_update.connect(data.append)

        worker.run()

        self.assertEqual(data, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("结果文件与模拟会话不一致", errors[0])

    def test_worker_requires_a_simulation_stage(self):
        with self.assertRaises(TypeError):
            MockReplayWorker(stage=object())


def _corrupted_scenario_stage(directory):
    stage = _stage(MeasurementType.CABLE_LOSS)
    path = directory / "scenario.json"
    path.write_text("{ not valid json", encoding="utf-8")
    return SimulationStage(
        measurement_type=stage.measurement_type,
        scenario=stage.scenario,
        scenario_path=path,
        result_path=stage.result_path,
    )


def _corrupted_result_stage(directory):
    stage = _stage(MeasurementType.CABLE_LOSS)
    path = directory / stage.scenario.result_filename
    path.write_text(json.dumps({"unexpected": True}), encoding="utf-8")
    return SimulationStage(
        measurement_type=stage.measurement_type,
        scenario=stage.scenario,
        scenario_path=stage.scenario_path,
        result_path=path,
    )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
