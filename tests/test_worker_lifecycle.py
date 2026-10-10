import unittest

from presentation.qt.worker_lifecycle import WorkerLifecycleCoordinator


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


class _Worker:
    def __init__(self, *, thread_signal=True):
        self.signals = type("Signals", (), {})()
        self.signals.progress = _Signal()
        self.signals.message = _Signal()
        self.signals.finished = _Signal()
        if thread_signal:
            self.finished = _Signal()


class WorkerLifecycleCoordinatorTests(unittest.TestCase):
    def test_binds_optional_worker_signals_and_thread_finished(self):
        events = []
        coordinator = WorkerLifecycleCoordinator(
            {"progress": events.append, "message": lambda _: events.append("message")},
            lambda: events.append("thread"),
        )
        worker = _Worker()
        coordinator.bind(worker)
        worker.signals.progress.emit(10)
        worker.finished.emit()
        self.assertEqual(events, [10, "thread"])

    def test_disconnect_is_idempotent_and_rebinding_does_not_duplicate_slots(self):
        events = []
        coordinator = WorkerLifecycleCoordinator({"progress": events.append}, lambda: None)
        worker = _Worker()
        coordinator.bind(worker)
        coordinator.bind(worker)
        worker.signals.progress.emit(10)
        self.assertEqual(events, [10])
        coordinator.disconnect()
        coordinator.disconnect()
        worker.signals.progress.emit(20)
        self.assertEqual(events, [10])

    def test_disconnect_continues_after_one_signal_failure(self):
        events = []
        coordinator = WorkerLifecycleCoordinator({"progress": events.append}, lambda: None)
        worker = _Worker()
        coordinator.bind(worker)
        original = worker.signals.progress.disconnect
        worker.signals.progress.disconnect = lambda _slot: (_ for _ in ()).throw(RuntimeError("gone"))
        coordinator.disconnect()
        self.assertEqual(worker.signals.message.slots, [])
        worker.signals.progress.disconnect = original
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
