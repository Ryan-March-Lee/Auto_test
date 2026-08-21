import unittest

from measurement_lifecycle import cleanup_measurement


class FakeController:
    def __init__(self):
        self.signal_gen = object()
        self.events = []
        self.rf_error = None
        self.close_error = None

    def rf_output_off(self):
        self.events.append("rf_off")
        if self.rf_error:
            raise self.rf_error

    def close_all(self, close_rf=True):
        self.events.append(("close", close_rf))
        if self.close_error:
            raise self.close_error
        return []


class MeasurementLifecycleTests(unittest.TestCase):
    def test_cleanup_preserves_rf_power_connection_order(self):
        controller = FakeController()
        cleanup_measurement(controller, power_cleanup=lambda: controller.events.append("power_off"))
        self.assertEqual(controller.events, ["rf_off", "power_off", ("close", False)])

    def test_cleanup_continues_after_rf_and_power_failures(self):
        controller = FakeController()
        controller.rf_error = RuntimeError("RF 关闭失败")
        events = []

        def power_cleanup():
            events.append("power_off")
            raise RuntimeError("电源关闭失败")

        with self.assertRaisesRegex(RuntimeError, "测量安全清理存在失败"):
            cleanup_measurement(controller, power_cleanup=power_cleanup)
        self.assertEqual(events, ["power_off"])
        self.assertEqual(controller.events[-1], ("close", False))

    def test_cleanup_continues_when_connection_close_fails(self):
        controller = FakeController()
        controller.close_error = RuntimeError("连接关闭失败")
        with self.assertRaisesRegex(RuntimeError, "连接关闭失败"):
            cleanup_measurement(controller)
        self.assertEqual(controller.events, ["rf_off", ("close", False)])


if __name__ == "__main__":
    unittest.main()
