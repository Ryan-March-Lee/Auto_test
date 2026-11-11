import unittest

from presentation.qt.measurement_state import (
    CableLossPageResultState,
    DriverMappingPageResultState,
    MeasurementKind,
    MeasurementResultReference,
    MeasurementStatus,
    MeasurementViewState,
    StageResultSelection,
)
from application.dto import MeasurementStatus as ApplicationMeasurementStatus
from application.dto.measurement_requests import StageResultDigest


class MeasurementStateTests(unittest.TestCase):
    def test_lifecycle_status_is_shared_with_application_dto(self):
        self.assertIs(MeasurementStatus, ApplicationMeasurementStatus)
        self.assertEqual(MeasurementStatus.COMPLETED.value, "completed")
        self.assertEqual(MeasurementStatus.CANCELLED.value, "cancelled")
        self.assertEqual(MeasurementStatus.EMERGENCY_STOPPED.value, "emergency_stopped")
        self.assertTrue(MeasurementStatus.EMERGENCY_STOPPED.is_terminal)
        self.assertFalse(MeasurementStatus.IDLE.is_terminal)

    def test_cable_loss_wait_continue_and_finish(self):
        state = MeasurementViewState(MeasurementKind.CABLE_LOSS)
        state = state.prepare().run()
        waiting = state.wait_for_continue("请确认路径 2 接线")
        self.assertEqual(waiting.status, MeasurementStatus.WAITING_FOR_CONTINUE)
        finished = waiting.continue_running().finish(
            MeasurementResultReference("run-1", MeasurementKind.CABLE_LOSS, {"rows": ()})
        )
        self.assertEqual(finished.status, MeasurementStatus.COMPLETED)
        self.assertEqual(finished.progress, 100)

    def test_stop_and_failure_preserve_explicit_reason(self):
        state = MeasurementViewState(MeasurementKind.AMPLIFIER).prepare().run()
        stopping = state.stop("用户请求停止", stopping=True)
        stopped = stopping.stop("用户请求停止")
        self.assertEqual(stopped.status, MeasurementStatus.CANCELLED)
        self.assertEqual(stopped.stop_reason, "用户请求停止")

        failed = MeasurementViewState(MeasurementKind.DRIVER_MAPPING).prepare().run().fail("worker 异常")
        self.assertEqual(failed.error_text, "worker 异常")

    def test_illegal_transitions_and_mismatched_result_are_rejected(self):
        state = MeasurementViewState(MeasurementKind.CABLE_LOSS)
        with self.assertRaises(ValueError):
            state.run()
        with self.assertRaises(ValueError):
            state.prepare().run().finish(
                MeasurementResultReference("run-1", MeasurementKind.AMPLIFIER, object())
            )
        with self.assertRaises(ValueError):
            state.prepare().run().fail("")

    def test_runtime_types_are_checked(self):
        with self.assertRaises(TypeError):
            MeasurementViewState("cable_loss")
        with self.assertRaises(TypeError):
            MeasurementViewState(MeasurementKind.CABLE_LOSS, status="running")
        with self.assertRaises(TypeError):
            MeasurementResultReference("run-1", "cable_loss", object())
        with self.assertRaises(TypeError):
            MeasurementResultReference(1, MeasurementKind.CABLE_LOSS, object())
        with self.assertRaises(TypeError):
            MeasurementViewState(MeasurementKind.CABLE_LOSS).transition("running")

    def test_specialized_page_result_rejects_wrong_kind(self):
        with self.assertRaises(ValueError):
            CableLossPageResultState(kind=MeasurementKind.AMPLIFIER)

        result = CableLossPageResultState(
            rows=({"nested": [{"value": 1}]},),
            metadata={"labels": ["gain"]},
        )
        with self.assertRaises(TypeError):
            result.rows[0]["nested"][0]["value"] = 2
        with self.assertRaises((AttributeError, TypeError)):
            result.metadata["labels"].append("power")

    def test_result_reference_value_is_recursively_read_only(self):
        reference = MeasurementResultReference(
            "run-1",
            MeasurementKind.CABLE_LOSS,
            {"rows": [{"frequency_hz": 1.0}]},
        )
        with self.assertRaises(TypeError):
            reference.value["rows"][0]["frequency_hz"] = 2.0

    def test_page_results_are_structured_and_read_only(self):
        result_types = (
            CableLossPageResultState,
            DriverMappingPageResultState,
        )
        for result_type in result_types:
            result = result_type(rows=({"frequency_hz": 1.0},))
            self.assertEqual(len(result.rows), 1)
            with self.assertRaises(TypeError):
                result.rows[0]["frequency_hz"] = 2.0

    def test_stopping_and_terminal_states_have_explicit_paths(self):
        state = MeasurementViewState(MeasurementKind.CABLE_LOSS).prepare()
        with self.assertRaises(ValueError):
            state.stop("用户请求停止").finish(
                MeasurementResultReference("run-1", MeasurementKind.CABLE_LOSS, {})
            )

        stopped = state.stop("用户请求停止", stopping=True).stop("用户请求停止")
        self.assertEqual(stopped.status, MeasurementStatus.CANCELLED)
        with self.assertRaises(ValueError):
            stopped.run()


    def test_stage_result_selection_requires_every_fact(self):
        selection = StageResultSelection(
            path="test_results/cable_loss/a.json",
            filename="a.json",
            created_at="2026-11-11T01:02:03+00:00",
            digest=StageResultDigest(sha256="a" * 64, size_bytes=12),
        )
        self.assertEqual(selection.filename, "a.json")
        self.assertEqual(selection.digest.size_bytes, 12)
        for name in ("path", "filename", "created_at"):
            with self.subTest(field=name), self.assertRaises(ValueError):
                StageResultSelection(
                    **{**selection.__dict__, name: "   "}
                )
            with self.subTest(field=name, kind="type"), self.assertRaises(TypeError):
                StageResultSelection(
                    **{**selection.__dict__, name: None}
                )
        with self.assertRaises(TypeError):
            StageResultSelection(
                path=selection.path,
                filename=selection.filename,
                created_at=selection.created_at,
                digest=None,
            )

    def test_selection_digest_must_come_from_the_stage_repository(self):
        with self.assertRaises(ValueError):
            StageResultDigest(sha256="not-a-digest", size_bytes=1)
        with self.assertRaises(ValueError):
            StageResultDigest(sha256="a" * 64, size_bytes=-1)


if __name__ == "__main__":
    unittest.main()
