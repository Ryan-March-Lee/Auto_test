"""阶段三步骤 7：Mock 回放时钟的时序、可中断等待与完成载荷。"""

import unittest
from pathlib import Path
from threading import Event

from application.dto.measurement_results import MeasurementType
from presentation.qt.mock_replay import (
    MOCK_DATA_POINT_INTERVAL_S,
    MOCK_SIMULATION_NOTICE,
    MockReplayClock,
    MockReplayCompletion,
)


class _WaitRecorder:
    """记录每次等待时长；返回预设结果，不真的 sleep。"""

    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)
        return self.result


class MockReplayClockTests(unittest.TestCase):
    def test_first_data_point_is_immediate_and_following_points_wait_half_a_second(self):
        recorder = _WaitRecorder()
        clock = MockReplayClock(wait_fn=recorder)

        self.assertEqual(clock.interval_s, 0.5)
        self.assertEqual(MOCK_DATA_POINT_INTERVAL_S, 0.5)
        for _ in range(4):
            self.assertTrue(clock.next_data_point())

        self.assertEqual(recorder.calls, [0.5, 0.5, 0.5])
        self.assertEqual(clock.emitted_data_points, 4)
        self.assertAlmostEqual(clock.elapsed_s, 1.5)

    def test_custom_interval_is_used_for_every_gap(self):
        recorder = _WaitRecorder()
        clock = MockReplayClock(interval_s=0.25, wait_fn=recorder)

        for _ in range(3):
            self.assertTrue(clock.next_data_point())

        self.assertEqual(recorder.calls, [0.25, 0.25])
        self.assertAlmostEqual(clock.elapsed_s, 0.5)

    def test_stop_event_interrupts_the_wait_and_blocks_later_points(self):
        stop_event = Event()
        recorded = []

        def wait(seconds):
            recorded.append(seconds)
            stop_event.set()
            return False

        clock = MockReplayClock(stop_event=stop_event, wait_fn=wait)
        self.assertTrue(clock.next_data_point())
        self.assertFalse(clock.next_data_point())
        self.assertFalse(clock.next_data_point())
        self.assertEqual(recorded, [0.5])
        self.assertEqual(clock.emitted_data_points, 1)
        self.assertEqual(clock.elapsed_s, 0.0)

    def test_stop_requested_before_playback_prevents_every_data_point(self):
        clock = MockReplayClock(wait_fn=_WaitRecorder())
        clock.request_stop()

        self.assertTrue(clock.is_stopped())
        self.assertFalse(clock.next_data_point())
        self.assertEqual(clock.emitted_data_points, 0)

    def test_an_already_set_stop_event_also_ends_a_completed_wait(self):
        stop_event = Event()

        def wait(_seconds):
            stop_event.set()
            return True

        clock = MockReplayClock(stop_event=stop_event, wait_fn=wait)
        self.assertTrue(clock.next_data_point())
        self.assertFalse(clock.next_data_point())
        self.assertTrue(clock.is_stopped())

    def test_reset_restarts_from_the_first_data_point(self):
        recorder = _WaitRecorder()
        clock = MockReplayClock(wait_fn=recorder)
        for _ in range(3):
            clock.next_data_point()

        clock.reset()
        self.assertEqual(clock.emitted_data_points, 0)
        self.assertEqual(clock.elapsed_s, 0.0)

        self.assertTrue(clock.next_data_point())
        self.assertEqual(recorder.calls, [0.5, 0.5], "重放首条仍应立即发出")

    def test_invalid_clock_inputs_are_rejected(self):
        for interval in (-1, -0.5):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    MockReplayClock(interval_s=interval)
        for interval in (True, "0.5", None):
            with self.subTest(interval=interval):
                with self.assertRaises(TypeError):
                    MockReplayClock(interval_s=interval)
        with self.assertRaises(ValueError):
            MockReplayClock(interval_s=float("inf"))
        with self.assertRaises(TypeError):
            MockReplayClock(stop_event=object())
        with self.assertRaises(TypeError):
            MockReplayClock(wait_fn=object())


class MockReplayCompletionTests(unittest.TestCase):
    def test_payload_is_read_only_and_marks_itself_as_simulated(self):
        payload = MockReplayCompletion(
            measurement_type=MeasurementType.CABLE_LOSS,
            title=" 线损模拟 ",
            result_filename="result.json",
            result_path=Path("mock_data/cable_loss/result.json"),
            archival_summary={"stage": "cable_loss", "artifacts": [{"kind": "csv"}]},
        )

        self.assertTrue(payload.simulated)
        self.assertEqual(payload.title, "线损模拟")
        self.assertEqual(payload.notice, MOCK_SIMULATION_NOTICE)
        self.assertIn("未写入正式结果", payload.notice)
        with self.assertRaises(TypeError):
            payload.archival_summary["stage"] = "other"
        with self.assertRaises(TypeError):
            payload.archival_summary["artifacts"][0]["kind"] = "report"

    def test_payload_rejects_invalid_fields(self):
        common = dict(
            measurement_type=MeasurementType.CABLE_LOSS,
            title="线损",
            result_filename="result.json",
            result_path=Path("mock_data/cable_loss/result.json"),
        )
        for filename in (
            "",
            " ",
            " result.json",
            "result.json ",
            "../result.json",
            "dir/result.json",
            "dir\\result.json",
            "result\x00.json",
            "CON.json",
            "result.json.",
        ):
            with self.subTest(filename=filename):
                with self.assertRaises(ValueError):
                    MockReplayCompletion(
                        **{**common, "result_filename": filename}
                    )
        with self.assertRaises(ValueError):
            MockReplayCompletion(**{**common, "title": " "})
        with self.assertRaises(TypeError):
            MockReplayCompletion(**{**common, "measurement_type": "cable_loss"})
        with self.assertRaises(TypeError):
            MockReplayCompletion(**{**common, "result_path": "mock_data/x.json"})
        with self.assertRaises(TypeError):
            MockReplayCompletion(**{**common, "archival_summary": ["not", "mapping"]})
        with self.assertRaisesRegex(ValueError, "文件名必须与 result_filename 一致"):
            MockReplayCompletion(
                **{
                    **common,
                    "result_path": Path("mock_data/cable_loss/other.json"),
                }
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
