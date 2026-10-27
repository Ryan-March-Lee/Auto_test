import unittest

from app.cancellation import CancellationToken, MeasurementCancelled
from application.measurements.services import CableLossService
from application.ports.measurement_session import CleanupStepStatus
from app.events import CheckpointEvent, RealtimeDataEvent
from tests.fakes import RecordingSafetyCleanup, safety_cleanup_for


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
        instrument = _Instrument()
        service = CableLossService(
            {"test_frequencies": [1.0], "attenuator": {"type": "10dB"}},
            instrument,
            run_id="run-cable-loss",
            safety_cleanup=safety_cleanup_for(instrument),
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
    kwargs.setdefault("safety_cleanup", safety_cleanup_for(instrument))
    return CableLossService(
        {"test_frequencies": [1.0], "attenuator": {"type": "10dB"}},
        instrument,
        run_id="run-cable-loss",
        sleep_fn=lambda _: None,
        **kwargs,
    )


class _NormalInstrument(_Instrument):
    """可正常测量、带信号源标记并记录清理动作的仪器替身。"""

    signal_gen = object()

    def __init__(self):
        super().__init__()
        self.rf_off_calls = 0
        self.close_calls = 0

    def rf_output_off(self):
        self.rf_off_calls += 1

    def close_all(self, close_rf=False):
        self.close_calls += 1
        return []


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


class CableLossServiceCleanupRecordTests(unittest.TestCase):
    """步骤 7：正常、取消、异常与紧急停止都产生结构化清理记录。"""

    def test_normal_run_records_every_cleanup_step(self):
        instrument = _NormalInstrument()
        service = _make_service(instrument)

        service.run()

        record = service.last_cleanup_record
        self.assertIsNotNone(record)
        self.assertTrue(record.succeeded)
        self.assertEqual(record.rf_output, CleanupStepStatus.PERFORMED)
        self.assertEqual(record.resources, CleanupStepStatus.PERFORMED)

    def test_measurement_error_still_produces_a_cleanup_record(self):
        instrument = _CleanupObservableInstrument()
        service = _make_service(instrument)

        with self.assertRaisesRegex(ValueError, "measurement failed"):
            service.run()

        record = service.last_cleanup_record
        self.assertIsNotNone(record)
        self.assertTrue(record.succeeded)

    def test_cleanup_failure_record_is_kept_on_the_service(self):
        instrument = _CleanupObservableInstrument(close_error=RuntimeError("close"))
        service = _make_service(instrument)

        with self.assertRaisesRegex(RuntimeError, "测量安全清理存在失败"):
            service.run()

        record = service.last_cleanup_record
        self.assertIsNotNone(record)
        self.assertFalse(record.succeeded)
        self.assertEqual(record.resources, CleanupStepStatus.FAILED)
        self.assertIn("close", record.errors)

    def test_cancelled_run_still_produces_a_cleanup_record(self):
        instrument = _NormalInstrument()
        token = CancellationToken()
        token.request_cancel(reason="任务已取消")
        service = _make_service(instrument, cancellation_token=token)

        with self.assertRaises(MeasurementCancelled):
            service.run()

        record = service.last_cleanup_record
        self.assertIsNotNone(record)
        self.assertTrue(record.succeeded)

    def test_borrowed_port_marks_resource_release_skipped(self):
        instrument = _NormalInstrument()
        service = _make_service(instrument, owns_measurement_port=False)

        service.run()

        record = service.last_cleanup_record
        self.assertEqual(record.resources, CleanupStepStatus.SKIPPED)
        self.assertEqual(instrument.close_calls, 0)

    def test_service_stores_the_injected_cleanup_record_and_ownership_flag(self):
        cleanup = RecordingSafetyCleanup()
        service = _make_service(_Instrument(), safety_cleanup=cleanup)

        service.run()

        self.assertIs(service.last_cleanup_record, cleanup.records[0])
        self.assertTrue(service.last_cleanup_record.succeeded)
        self.assertEqual(cleanup.calls[0][1], True)


if __name__ == "__main__":
    unittest.main()
