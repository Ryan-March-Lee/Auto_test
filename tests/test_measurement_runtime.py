import unittest

from application.dto.measurement_results import MeasurementStatus
from application.lifecycle import MeasurementRuntimeCoordinator, StopIntent


class MeasurementRuntimeCoordinatorTests(unittest.TestCase):
    def test_emergency_stop_has_priority_over_late_finished_event(self):
        runtime = MeasurementRuntimeCoordinator()
        runtime.request(StopIntent.EMERGENCY_STOP)
        decision = runtime.finished({"value": 1})
        self.assertEqual(decision.status, MeasurementStatus.EMERGENCY_STOPPED)
        self.assertEqual(decision.reason, "紧急停止")

    def test_finished_without_result_is_failed(self):
        decision = MeasurementRuntimeCoordinator().finished(None)
        self.assertEqual(decision.status, MeasurementStatus.FAILED)
        self.assertEqual(decision.error, "worker 完成但未提供测量结果")

    def test_stop_reason_is_user_stop(self):
        runtime = MeasurementRuntimeCoordinator()
        runtime.request(StopIntent.STOP)
        decision = runtime.stopped()
        self.assertEqual(decision.status, MeasurementStatus.CANCELLED)
        self.assertEqual(decision.reason, "用户停止")


if __name__ == "__main__":
    unittest.main()
