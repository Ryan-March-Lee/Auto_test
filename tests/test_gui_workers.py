import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication

from presentation.qt.workers import CableLossWorker


class _CableService:
    def __init__(self, path1_done):
        self.path1_done = path1_done
        self.continue_called = threading.Event()
        self.stop_called = threading.Event()

    def set_step_pause_callback(self, callback):
        self.step_pause_callback = callback

    def measure_all_frequencies(self):
        self.path1_done.set()

    def continue_to_step2(self):
        self.continue_called.set()

    def stop_measurement(self):
        self.stop_called.set()


class GuiWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def test_cable_loss_second_step_runs_in_worker_thread(self):
        path1_done = threading.Event()
        service = _CableService(path1_done)
        worker = CableLossWorker("config.json", sleep_fn=lambda _: None)

        prepared = object()
        with patch("app.gui_runtime.prepare_configuration", return_value=prepared), \
                patch("app.gui_runtime.create_cable_loss_measurement", return_value=service) as factory:
            worker.start()
            self.assertTrue(path1_done.wait(1))
            deadline = time.monotonic() + 1
            while not worker._waiting_for_continue and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(worker.isRunning())
            worker.continue_measurement()
            self.assertTrue(service.continue_called.wait(1))
            worker.wait(1000)

        self.assertIs(factory.call_args.kwargs["prepared_run"], prepared)
        self.assertTrue(service.continue_called.is_set())
        self.assertFalse(worker.isRunning())

    def test_stopping_at_cable_loss_checkpoint_releases_worker(self):
        path1_done = threading.Event()
        service = _CableService(path1_done)
        worker = CableLossWorker("config.json", sleep_fn=lambda _: None)

        prepared = object()
        with patch("app.gui_runtime.prepare_configuration", return_value=prepared), \
                patch("app.gui_runtime.create_cable_loss_measurement", return_value=service):
            worker.start()
            self.assertTrue(path1_done.wait(1))
            worker.stop()
            worker.wait(1000)

        self.assertTrue(service.stop_called.is_set())
        self.assertFalse(worker.isRunning())

    def test_preflight_failure_prevents_measurement_service_creation(self):
        worker = CableLossWorker("config.json", sleep_fn=lambda _: None)

        with patch(
            "app.gui_runtime.prepare_configuration",
            side_effect=OSError("snapshot failed"),
        ), patch("app.gui_runtime.create_cable_loss_measurement") as factory:
            worker.run()

        factory.assert_not_called()
        self.assertIsNone(worker.service)


if __name__ == "__main__":
    unittest.main()
