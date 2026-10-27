"""测量会话端口与结构化清理记录的契约测试。"""

import unittest

from application.errors import FailureRecord
from application.measurements.services import CableLossService
from application.ports.measurement_session import (
    CleanupRecord,
    CleanupStepStatus,
    MeasurementSessionBinding,
    MeasurementSessionPort,
    SafetyCleanupError,
)
from tests.fakes import NullSafetyCleanup, safety_cleanup_for


class _Instrument:
    signal_gen = object()

    def set_frequency(self, _frequency):
        pass

    def set_power(self, _power):
        pass

    def set_center_frequency(self, _frequency):
        pass

    def set_span(self, _span):
        pass

    def rf_output_on(self):
        pass

    def measure_power_with_average(self):
        return -10.0

    def rf_output_off(self):
        pass

    def close_all(self, *, close_rf=False):
        return []


class MeasurementSessionBindingTests(unittest.TestCase):
    def test_binding_requires_both_ports(self):
        with self.assertRaisesRegex(ValueError, "instrument"):
            MeasurementSessionBinding(instrument=None, safety_cleanup=NullSafetyCleanup())
        with self.assertRaisesRegex(ValueError, "safety_cleanup"):
            MeasurementSessionBinding(instrument=_Instrument(), safety_cleanup=None)

    def test_binding_requires_a_callable_cleanup_port(self):
        # 清理是安全关键依赖：无 cleanup() 的对象必须在构造阶段被拒绝，
        # 不能等到测量动作结束、进入 finally 清理时才失败。
        with self.assertRaisesRegex(ValueError, "cleanup"):
            MeasurementSessionBinding(instrument=_Instrument(), safety_cleanup=object())

    def test_binding_satisfies_the_session_port_and_delegates_cleanup(self):
        cleanup = NullSafetyCleanup()
        instrument = _Instrument()
        binding = MeasurementSessionBinding(instrument=instrument, safety_cleanup=cleanup)

        self.assertIsInstance(binding, MeasurementSessionPort)
        self.assertIs(binding.instrument, instrument)
        record = binding.cleanup(release_resources=False)

        self.assertEqual(cleanup.calls, [record])
        self.assertEqual(record.resources, CleanupStepStatus.SKIPPED)

    def test_service_session_conforms_to_the_port(self):
        instrument = _Instrument()
        service = CableLossService(
            {"test_frequencies": [1.0], "attenuator": {"type": "10dB"}},
            instrument,
            run_id="run-port",
            safety_cleanup=safety_cleanup_for(instrument),
            sleep_fn=lambda _: None,
        )

        self.assertIsInstance(service.session, MeasurementSessionPort)
        self.assertIs(service.session.instrument, instrument)


class CleanupRecordTests(unittest.TestCase):
    def test_record_reports_success_only_without_errors(self):
        record = CleanupRecord(
            rf_output=CleanupStepStatus.PERFORMED,
            power_supplies=CleanupStepStatus.SKIPPED,
            resources=CleanupStepStatus.PERFORMED,
        )
        self.assertTrue(record.succeeded)
        self.assertEqual(
            record.to_dict(),
            {
                "rf_output": "performed",
                "power_supplies": "skipped",
                "resources": "performed",
                "errors": [],
                "failures": [],
                "succeeded": True,
            },
        )

        failed = CleanupRecord(
            rf_output=CleanupStepStatus.PERFORMED,
            power_supplies=CleanupStepStatus.FAILED,
            resources=CleanupStepStatus.PERFORMED,
            errors=("power",),
            failures=(FailureRecord("power_supplies", "power"),),
        )
        self.assertFalse(failed.succeeded)
        self.assertEqual(
            failed.to_dict()["failures"],
            [{"action": "power_supplies", "message": "power"}],
        )

    def test_cleanup_error_carries_the_record_and_keeps_the_message_prefix(self):
        record = CleanupRecord(
            rf_output=CleanupStepStatus.PERFORMED,
            power_supplies=CleanupStepStatus.FAILED,
            resources=CleanupStepStatus.PERFORMED,
            errors=("power", "close"),
        )

        error = SafetyCleanupError(record)

        self.assertIs(error.record, record)
        self.assertTrue(str(error).startswith("测量安全清理存在失败"))
        self.assertIn("power", str(error))
        self.assertIn("close", str(error))


if __name__ == "__main__":
    unittest.main()
