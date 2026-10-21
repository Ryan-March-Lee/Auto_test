import unittest

from application.measurements.services import CableLossService
from app.events import CheckpointEvent, RealtimeDataEvent


class _Instrument:
    def __init__(self):
        self.measured = 0

    def set_frequency(self, frequency):
        pass

    def set_power(self, power):
        pass

    def rf_output_on(self):
        pass

    def set_center_frequency(self, frequency):
        pass

    def set_span(self, span):
        pass

    def measure_power_with_average(self):
        self.measured += 1
        return -10.0

    def rf_output_off(self):
        pass

    def close_all(self, close_rf=False):
        return []


class _Events:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class CableLossServiceTests(unittest.TestCase):
    def test_path2_confirmation_is_not_published_again_when_continuing(self):
        events = _Events()
        service = CableLossService(
            {"test_frequencies": [1.0], "attenuator": {"type": "10dB"}},
            _Instrument(),
            event_sink=events,
            sleep_fn=lambda _: None,
        )

        waiting = service.run(path2_confirmed=False)
        self.assertEqual(waiting["status"], "waiting")
        checkpoints = [event.checkpoint for event in events.events if isinstance(event, CheckpointEvent)]
        self.assertEqual(checkpoints, ["path1", "path2"])

        result = service.run(path2_confirmed=True)
        self.assertIn("cable_losses", result)
        checkpoints = [event.checkpoint for event in events.events if isinstance(event, CheckpointEvent)]
        self.assertEqual(checkpoints, ["path1", "path2"])

        realtime = [event for event in events.events if isinstance(event, RealtimeDataEvent)]
        self.assertEqual([event.data["path"] for event in realtime], [1, 2])
        self.assertEqual(realtime[-1].data["cable_losses"]["cable1"], 0.0)


class _CleanupObservableInstrument:
    """记录清理动作，并可在测量或关闭连接时注入失败。"""

    def __init__(self, *, close_error=None):
        self.signal_gen = object()
        self.rf_off_calls = 0
        self.close_calls = 0
        self.close_error = close_error

    def set_frequency(self, frequency):
        raise ValueError("measurement failed")

    def set_power(self, power):
        pass

    def rf_output_on(self):
        pass

    def set_center_frequency(self, frequency):
        pass

    def set_span(self, span):
        pass

    def measure_power_with_average(self):
        return -10.0

    def rf_output_off(self):
        self.rf_off_calls += 1

    def close_all(self, close_rf=False):
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error
        return []


def _make_service(instrument, **kwargs):
    return CableLossService(
        {"test_frequencies": [1.0], "attenuator": {"type": "10dB"}},
        instrument,
        sleep_fn=lambda _: None,
        **kwargs,
    )


class CableLossServiceCleanupFailureTests(unittest.TestCase):
    def test_measurement_error_is_preserved_when_cleanup_succeeds(self):
        instrument = _CleanupObservableInstrument()
        with self.assertRaisesRegex(ValueError, "measurement failed"):
            _make_service(instrument).run()
        self.assertEqual(instrument.rf_off_calls, 1)
        self.assertEqual(instrument.close_calls, 1)

    def test_cleanup_failure_surfaces_and_keeps_measurement_error_as_context(self):
        instrument = _CleanupObservableInstrument(close_error=RuntimeError("close"))
        with self.assertRaisesRegex(RuntimeError, "测量安全清理存在失败") as raised:
            _make_service(instrument).run()
        # 清理失败是安全关键错误，必须优先抛出；原始测量异常保留在上下文中。
        self.assertIsInstance(raised.exception.__context__, ValueError)
        self.assertEqual(instrument.close_calls, 1)

    def test_borrowed_port_is_not_closed_when_measurement_fails(self):
        instrument = _CleanupObservableInstrument()
        with self.assertRaisesRegex(ValueError, "measurement failed"):
            _make_service(instrument, owns_measurement_port=False).run()
        self.assertEqual(instrument.rf_off_calls, 1)
        self.assertEqual(instrument.close_calls, 0)


if __name__ == "__main__":
    unittest.main()
