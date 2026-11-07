import unittest

from application.dto import MeasurementStatus
from application.lifecycle import MeasurementLifecycle, StopIntent


class MeasurementLifecycleTests(unittest.TestCase):
    def test_normal_stop_completes_as_cancelled(self):
        lifecycle = MeasurementLifecycle().prepare().start().request_stop("用户停止")
        self.assertEqual(lifecycle.status, MeasurementStatus.STOPPING)
        stopped = lifecycle.worker_stopped("worker 已停止")
        self.assertEqual(stopped.status, MeasurementStatus.CANCELLED)
        self.assertEqual(stopped.stop_intent, StopIntent.NORMAL)

    def test_emergency_stop_cannot_be_downgraded_by_worker_reason(self):
        lifecycle = MeasurementLifecycle().prepare().start().request_stop("紧急停止", emergency=True)
        stopped = lifecycle.worker_stopped("用户停止")
        self.assertEqual(stopped.status, MeasurementStatus.EMERGENCY_STOPPED)
        self.assertEqual(stopped.stop_intent, StopIntent.EMERGENCY)

    def test_idle_is_not_terminal_and_terminal_states_cannot_continue(self):
        self.assertFalse(MeasurementStatus.IDLE.is_terminal)
        completed = MeasurementLifecycle().prepare().start().complete()
        with self.assertRaises(ValueError):
            completed.start()


if __name__ == "__main__":
    unittest.main()
