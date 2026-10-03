import unittest

from presentation.qt.measurement_controller import MeasurementController
from presentation.qt.measurement_controller_contract import MeasurementCommand
from presentation.qt.measurement_state import MeasurementKind, MeasurementStatus


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
            "stopped", "error", "step_pause",
        ):
            setattr(self, name, Signal())


class Worker:
    def __init__(self):
        self.signals = Signals()
        self.start_calls = 0
        self.stop_calls = 0
        self.emergency_stop_calls = 0
        self.continue_calls = 0

    def start(self):
        self.start_calls += 1

    def stop(self):
        self.stop_calls += 1

    def emergency_stop(self):
        self.emergency_stop_calls += 1

    def continue_measurement(self):
        self.continue_calls += 1


class MeasurementControllerTests(unittest.TestCase):
    def setUp(self):
        self.workers = []
        self.factories = {
            kind: self._factory for kind in MeasurementKind
        }
        self.port = object()
        self.controller = MeasurementController(
            self.factories, lambda: "runtime-config.json", measurement_port=self.port
        )
        self.errors = []
        self.rejections = []
        self.controller.signals.error.connect(self.errors.append)
        self.controller.signals.rejected.connect(self.rejections.append)

    def _factory(self, command, *, measurement_port=None):
        worker = Worker()
        worker.command = command
        worker.measurement_port = measurement_port
        self.workers.append(worker)
        return worker

    def test_selects_factory_and_cleans_worker_after_finished(self):
        command = MeasurementCommand("page-config.json")
        self.assertTrue(self.controller.start_cable_loss(command))
        worker = self.workers[-1]
        self.assertEqual(worker.command.config_path, "runtime-config.json")
        self.assertIs(worker.measurement_port, self.port)
        self.assertEqual(self.controller.state.status, MeasurementStatus.RUNNING)

        worker.signals.result.emit({"value": 1})
        worker.signals.finished.emit()

        self.assertEqual(self.controller.state.status, MeasurementStatus.FINISHED)
        self.assertIsNone(self.controller.current_worker)
        self.assertTrue(self.controller.start_amplifier(command))

    def test_runtime_config_provider_is_the_worker_config_authority(self):
        self.assertTrue(self.controller.start_amplifier(MeasurementCommand("stale.json")))
        self.assertEqual(self.workers[-1].command.config_path, "runtime-config.json")

    def test_rejects_concurrent_start(self):
        command = MeasurementCommand("config.json")
        self.assertTrue(self.controller.start_driver_mapping(command))
        self.assertFalse(self.controller.start_amplifier(command))
        self.assertEqual(self.rejections, ["已有测量正在运行"])
        self.assertEqual(len(self.workers), 1)

    def test_error_cleans_worker_and_is_restartable(self):
        command = MeasurementCommand("config.json")
        self.assertTrue(self.controller.start_amplifier(command))
        self.workers[-1].signals.error.emit("测量失败")
        self.assertEqual(self.controller.state.status, MeasurementStatus.FAILED)
        self.assertEqual(self.errors, ["测量失败"])
        self.assertIsNone(self.controller.current_worker)
        self.assertTrue(self.controller.start_cable_loss(command))

    def test_cable_loss_pause_and_continue_are_controller_operations(self):
        command = MeasurementCommand("config.json")
        self.assertTrue(self.controller.start_cable_loss(command))
        worker = self.workers[-1]
        worker.signals.step_pause.emit("请接线")
        self.assertEqual(self.controller.state.status, MeasurementStatus.WAITING_FOR_CONTINUE)
        self.assertTrue(self.controller.continue_cable_loss())
        self.assertEqual(worker.continue_calls, 1)
        self.assertEqual(self.controller.state.status, MeasurementStatus.RUNNING)

    def test_stop_and_emergency_stop_remain_distinct(self):
        command = MeasurementCommand("config.json")
        self.assertTrue(self.controller.start_amplifier(command))
        worker = self.workers[-1]
        self.assertTrue(self.controller.stop())
        self.assertEqual(worker.stop_calls, 1)
        self.assertEqual(worker.emergency_stop_calls, 0)
        worker.signals.stopped.emit("用户停止")
        self.assertEqual(self.controller.state.status, MeasurementStatus.STOPPED)
        self.assertTrue(self.controller.start_amplifier(command))
        worker = self.workers[-1]
        self.assertTrue(self.controller.emergency_stop())
        self.assertEqual(worker.stop_calls, 0)
        self.assertEqual(worker.emergency_stop_calls, 1)

    def test_factory_failure_does_not_start_worker(self):
        def broken_factory(command, *, measurement_port=None):
            raise RuntimeError("构造失败")

        controller = MeasurementController(
            {MeasurementKind.AMPLIFIER: broken_factory}, lambda: "config.json"
        )
        controller.signals.error.connect(self.errors.append)
        self.assertFalse(controller.start_amplifier(MeasurementCommand("config.json")))
        self.assertEqual(controller.state.status, MeasurementStatus.FAILED)
        self.assertEqual(self.errors, ["构造失败"])
        self.assertIsNone(controller.current_worker)

    def test_start_failure_cleans_worker_and_allows_restart(self):
        calls = 0
        def factory(command, *, measurement_port=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("线程启动失败")
            worker = Worker()
            self.workers.append(worker)
            return worker

        controller = MeasurementController(
            {MeasurementKind.AMPLIFIER: factory}, lambda: "config.json"
        )
        errors = []
        controller.signals.error.connect(errors.append)
        command = MeasurementCommand("config.json")

        self.assertFalse(controller.start_amplifier(command))
        self.assertEqual(controller.state.status, MeasurementStatus.FAILED)
        self.assertEqual(errors, ["线程启动失败"])
        self.assertIsNone(controller.current_worker)

        self.assertTrue(controller.start_amplifier(command))

    def test_cancel_signal_cleans_worker_and_allows_restart(self):
        command = MeasurementCommand("config.json")
        self.assertTrue(self.controller.start_driver_mapping(command))
        self.workers[-1].signals.stopped.emit("任务已取消")

        self.assertEqual(self.controller.state.status, MeasurementStatus.STOPPED)
        self.assertEqual(self.controller.state.view_state.stop_reason, "任务已取消")
        self.assertIsNone(self.controller.current_worker)
        self.assertTrue(self.controller.start_amplifier(command))

    def test_waiting_cable_loss_can_be_stopped(self):
        self.assertTrue(
            self.controller.start_cable_loss(MeasurementCommand("config.json"))
        )
        worker = self.workers[-1]
        stopped = []
        self.controller.signals.stopped.connect(stopped.append)
        worker.signals.step_pause.emit("请接线")

        self.assertTrue(self.controller.stop())
        self.assertEqual(worker.stop_calls, 1)
        self.assertEqual(worker.emergency_stop_calls, 0)
        self.assertEqual(self.controller.state.status, MeasurementStatus.STOPPING)
        worker.signals.stopped.emit("用户停止")
        self.assertEqual(self.controller.state.status, MeasurementStatus.STOPPED)
        self.assertEqual(stopped, ["用户停止"])
        self.assertIsNone(self.controller.current_worker)

    def test_stop_failure_is_reported_and_does_not_escape_to_page(self):
        class StopFailWorker(Worker):
            def stop(self):
                raise RuntimeError("停止失败")

        controller = MeasurementController(
            {MeasurementKind.AMPLIFIER: lambda command, measurement_port=None: StopFailWorker()},
            lambda: "config.json",
        )
        errors = []
        controller.signals.error.connect(errors.append)
        self.assertTrue(controller.start_amplifier(MeasurementCommand("config.json")))
        self.assertFalse(controller.stop())
        self.assertEqual(errors, ["停止失败"])
        self.assertEqual(controller.state.status, MeasurementStatus.FAILED)
        self.assertIsNone(controller.current_worker)

    def test_finished_without_result_is_a_failure(self):
        self.assertTrue(self.controller.start_amplifier(MeasurementCommand("config.json")))
        self.workers[-1].signals.finished.emit()
        self.assertEqual(self.controller.state.status, MeasurementStatus.FAILED)
        self.assertEqual(self.errors, ["worker 完成但未提供测量结果"])


if __name__ == "__main__":
    unittest.main()
