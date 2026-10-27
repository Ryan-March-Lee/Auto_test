"""测量会话端口与结构化清理记录的契约测试。"""

import unittest

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
                "succeeded": True,
            },
        )

        failed = CleanupRecord(
            rf_output=CleanupStepStatus.PERFORMED,
            power_supplies=CleanupStepStatus.FAILED,
            resources=CleanupStepStatus.PERFORMED,
            errors=("power",),
        )
        self.assertFalse(failed.succeeded)

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
