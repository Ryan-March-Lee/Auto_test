import unittest

from presentation.qt.measurement_controller_contract import (
    START_METHODS,
    ControllerState,
    MeasurementCommand,
    MeasurementControllerProtocol,
)
from presentation.qt.measurement_state import (
    MeasurementKind,
    MeasurementStatus,
    MeasurementViewState,
)


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots.remove(slot)

    def emit(self, *args):
        for slot in tuple(self.slots):
            slot(*args)


class _Signals:
    def __init__(self):
        for name in (
            "progress",
            "message",
            "data_update",
            "result",
            "finished",
            "stopped",
            "error",
            "step_pause",
            "rejected",
            "state_changed",
        ):
            setattr(self, name, _Signal())


class _Worker:
    def __init__(self):
        self.signals = _Signals()
        self.started = False
        self.stop_calls = 0
        self.emergency_stop_calls = 0

    def start(self):
        self.started = True

    def stop(self):
        self.stop_calls += 1

    def emergency_stop(self):
        self.emergency_stop_calls += 1


class _FakeController:
    """纯 Python fake，验证步骤 1.3 的行为语义而不依赖 Qt。"""

    def __init__(self):
        self.signals = _Signals()
        self._state = ControllerState()
        self.workers = []
        self.rejections = []
        self.signals.rejected.connect(self.rejections.append)

    @property
    def state(self):
        return self._state

    def _start(self, kind, command):
        if not isinstance(command, MeasurementCommand):
            raise TypeError("command 必须是 MeasurementCommand")
        if self.state.is_active:
            self.signals.rejected.emit("已有测量正在运行")
            return False
        worker = _Worker()
        self.workers.append((kind, worker))
        view_state = MeasurementViewState(kind).prepare().run()
        self._state = ControllerState(kind, MeasurementStatus.RUNNING, view_state)
        self.signals.state_changed.emit(self._state)
        worker.start()
        return True

    def start_cable_loss(self, command):
        return self._start(MeasurementKind.CABLE_LOSS, command)

    def start_driver_mapping(self, command):
        return self._start(MeasurementKind.DRIVER_MAPPING, command)

    def start_amplifier(self, command):
        return self._start(MeasurementKind.AMPLIFIER, command)

    def stop(self):
        if not self.state.is_active:
            self.signals.rejected.emit("当前没有可停止的测量")
            return False
        self.workers[-1][1].stop()
        return True

    def emergency_stop(self):
        if not self.state.is_active:
            self.signals.rejected.emit("当前没有可紧急停止的测量")
            return False
        self.workers[-1][1].emergency_stop()
        return True

    def continue_cable_loss(self):
        if self.state.status is not MeasurementStatus.WAITING_FOR_CONTINUE:
            self.signals.rejected.emit("线损测量当前不在等待继续状态")
            return False
        return True


class MeasurementControllerContractTests(unittest.TestCase):
    def test_fake_controller_implements_page_protocol(self):
        self.assertIsInstance(_FakeController(), MeasurementControllerProtocol)

    def test_start_entries_are_stable_and_reject_concurrent_start(self):
        controller = _FakeController()
        command = MeasurementCommand("config.json", options={"frequencies": [1, 2]})

        self.assertTrue(controller.start_cable_loss(command))
        for kind, method_name in START_METHODS.items():
            if kind is not MeasurementKind.CABLE_LOSS:
                self.assertFalse(getattr(controller, method_name)(command))

        self.assertEqual(controller.rejections, ["已有测量正在运行", "已有测量正在运行"])
        with self.assertRaises((TypeError, AttributeError)):
            command.options["frequencies"].append(3)

    def test_normal_and_emergency_stop_are_distinct_worker_operations(self):
        controller = _FakeController()
        controller.start_amplifier(MeasurementCommand("config.json"))
        worker = controller.workers[-1][1]

        self.assertTrue(controller.stop())
        self.assertTrue(controller.emergency_stop())
        self.assertEqual(worker.stop_calls, 1)
        self.assertEqual(worker.emergency_stop_calls, 1)

    def test_continue_is_rejected_outside_cable_loss_checkpoint(self):
        controller = _FakeController()
        self.assertFalse(controller.continue_cable_loss())
        self.assertEqual(controller.rejections, ["线损测量当前不在等待继续状态"])

    def test_controller_state_requires_matching_view_state(self):
        view_state = MeasurementViewState(MeasurementKind.CABLE_LOSS).prepare().run()
        valid = ControllerState(
            MeasurementKind.CABLE_LOSS,
            MeasurementStatus.RUNNING,
            view_state,
        )
        self.assertTrue(valid.is_active)

        with self.assertRaises(ValueError):
            ControllerState(MeasurementKind.CABLE_LOSS, MeasurementStatus.RUNNING)
        with self.assertRaises(ValueError):
            ControllerState(MeasurementKind.CABLE_LOSS, MeasurementStatus.FINISHED, view_state)

    def test_idle_state_has_no_measurement_payload(self):
        state = ControllerState()
        self.assertEqual(state.status, MeasurementStatus.IDLE)
        self.assertFalse(state.is_active)
        with self.assertRaises(ValueError):
            ControllerState(status=MeasurementStatus.RUNNING)

    def test_command_requires_non_empty_path_and_freezes_nested_options(self):
        with self.assertRaises(ValueError):
            MeasurementCommand(" ")
        command = MeasurementCommand("config.json", {"nested": {"items": [1]}})
        with self.assertRaises((TypeError, AttributeError)):
            command.options["nested"]["items"].append(2)


if __name__ == "__main__":
    unittest.main()
