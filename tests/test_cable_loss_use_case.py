import unittest
from pathlib import Path
from types import SimpleNamespace

from app.cancellation import CancellationToken, MeasurementCancelled
from application.dto import CableLossMeasurementRequest, validate_generated_measurement_id
from application.measurements import CableLossUseCase
from application.ports.measurement_session import SafetyCleanupError
from application.ports.stage_result_repository import (
    MeasurementStage,
    StageResultReference,
)
from app.events import CheckpointEvent
from application.run_stages import StageResultNotRecorded
from domain.run.models import STAGE_CABLE_LOSS
from tests.contract_doubles import RecordingStageRecorder
from tests.fakes import safety_cleanup_for


class _Instrument:
    def __init__(self, *, fail=False):
        self.measured = 0
        self.close_calls = 0
        self.fail = fail

    def set_frequency(self, _frequency): pass
    def set_power(self, _power): pass
    def rf_output_on(self): pass
    def set_center_frequency(self, _frequency): pass
    def set_span(self, _span): pass

    def measure_power_with_average(self):
        if self.fail:
            raise RuntimeError("measurement failed")
        self.measured += 1
        return -10.0

    def rf_output_off(self): pass

    def close_all(self, close_rf=False):
        self.close_calls += 1
        return []


class _Events:
    def __init__(self): self.events = []
    def publish(self, event): self.events.append(event)


class _Repository:
    def __init__(self): self.calls = []
    def save(self, result, **kwargs):
        self.calls.append((result, kwargs))
        return SimpleNamespace(run_directory=Path("saved-run"))


class _StageRepository:
    """阶段结果仓库替身：记录正式结果，返回可构造的阶段引用。"""

    def __init__(self):
        self.calls = []

    def save(self, stage, result):
        self.calls.append((stage, result))
        return StageResultReference(
            stage=stage,
            measurement_id=result.measurement_id,
            path=Path("stage-results") / "cable_loss" / f"{result.measurement_id}.json",
            sha256="0" * 64,
            size_bytes=0,
        )


class _FailingCompleteRecorder(RecordingStageRecorder):
    """引用登记失败的记录器替身：阶段文件已写入，运行记账失败。"""

    def __init__(self, run_id, error):
        super().__init__(run_id)
        self.error = error

    def complete_with_stage_result(
        self, stage, reference, *, description=None, now=None
    ):
        raise self.error


def _request(
    instrument,
    *,
    events=None,
    token=None,
    repository=None,
    recorder=None,
    stage_repository=None,
    frequencies=(1.0,),
):
    configuration = {"test_frequencies": list(frequencies), "attenuator": {"type": "10dB"}}
    return CableLossMeasurementRequest(
        configuration=configuration,
        context=SimpleNamespace(run_id="run-cable"),
        run_directory=Path("run-directory"),
        measurement_port=instrument,
        stage_recorder=recorder or RecordingStageRecorder("run-cable"),
        safety_cleanup=safety_cleanup_for(instrument),
        event_sink=events,
        cancellation_token=token,
        result_repository=repository or _Repository(),
        stage_result_repository=stage_repository or _StageRepository(),
        safety_options={"owns_measurement_port": True},
    )


class CableLossUseCaseTests(unittest.TestCase):
    def test_borrowed_measurement_port_is_not_closed(self):
        instrument = _Instrument()
        request = _request(instrument)
        borrowed_request = CableLossMeasurementRequest(
            configuration=request.configuration,
            context=request.context,
            run_directory=request.run_directory,
            measurement_port=request.measurement_port,
            stage_recorder=request.stage_recorder,
            safety_cleanup=request.safety_cleanup,
            result_repository=request.result_repository,
            stage_result_repository=request.stage_result_repository,
            safety_options={"owns_measurement_port": False},
        )
        use_case = CableLossUseCase(borrowed_request, sleep_fn=lambda _: None)
        use_case.measure_all_frequencies()
        use_case.continue_to_step2()
        self.assertEqual(instrument.close_calls, 0)

    def test_waits_then_continues_and_saves_only_completed_result(self):
        instrument = _Instrument()
        events = _Events()
        recorder = RecordingStageRecorder("run-cable")
        stage_repository = _StageRepository()
        use_case = CableLossUseCase(
            _request(
                instrument,
                events=events,
                recorder=recorder,
                stage_repository=stage_repository,
            ),
            sleep_fn=lambda _: None,
        )

        waiting = use_case.measure_all_frequencies()
        self.assertEqual(waiting.status.value, "waiting_for_continue")
        self.assertEqual(waiting.points, ())
        self.assertEqual(instrument.measured, 1)
        self.assertEqual(recorder.completed, [])
        self.assertEqual(recorder.started, [STAGE_CABLE_LOSS])
        self.assertEqual(stage_repository.calls, [])

        result = use_case.continue_to_step2()
        self.assertIn("cable_losses", result.to_dict())
        self.assertTrue(result.points)
        self.assertEqual(instrument.measured, 2)
        # 正式结果先写入独立阶段目录：带测量身份，运行目录只登记引用。
        [(stage, saved)] = stage_repository.calls
        self.assertIs(stage, MeasurementStage.CABLE_LOSS)
        self.assertEqual(
            saved.measurement_id,
            validate_generated_measurement_id(result.measurement_id),
        )
        self.assertEqual(
            recorder.result_paths(), {STAGE_CABLE_LOSS: f"inputs/{STAGE_CABLE_LOSS}.json"}
        )
        [(reference_stage, reference)] = recorder.stage_references
        self.assertEqual(reference_stage, STAGE_CABLE_LOSS)
        self.assertEqual(reference.measurement_id, result.measurement_id)
        self.assertEqual(
            result.result_reference["archive_path"], str(reference.path)
        )
        self.assertEqual(instrument.close_calls, 1)
        self.assertEqual(use_case.run_directory, Path("run-directory"))
        self.assertEqual(
            [event.checkpoint for event in events.events if isinstance(event, CheckpointEvent)],
            ["path1", "path2"],
        )

    def test_stop_while_waiting_cleans_up_and_does_not_measure_path2(self):
        instrument = _Instrument()
        use_case = CableLossUseCase(_request(instrument), sleep_fn=lambda _: None)
        use_case.measure_all_frequencies()

        use_case.stop_measurement()

        self.assertEqual(instrument.measured, 1)
        self.assertEqual(instrument.close_calls, 1)
        use_case.stop_measurement()
        self.assertEqual(instrument.close_calls, 1)

    def test_step_pause_callback_is_called_once(self):
        instrument = _Instrument()
        prompts = []
        use_case = CableLossUseCase(_request(instrument), sleep_fn=lambda _: None)
        use_case.set_step_pause_callback(prompts.append)

        use_case.measure_all_frequencies()

        self.assertEqual(prompts, ["请连接路径2"])

    def test_stop_while_waiting_produces_a_cleanup_record(self):
        instrument = _Instrument()
        use_case = CableLossUseCase(_request(instrument), sleep_fn=lambda _: None)
        use_case.measure_all_frequencies()

        use_case.stop_measurement()

        record = use_case.last_cleanup_record
        self.assertIsNotNone(record)
        self.assertTrue(record.succeeded)
        self.assertEqual(instrument.close_calls, 1)

    def test_stop_cleanup_failure_is_an_explicit_terminal_error(self):
        class _CloseFailingInstrument(_Instrument):
            def close_all(self, close_rf=False):
                self.close_calls += 1
                raise RuntimeError("close")

        instrument = _CloseFailingInstrument()
        use_case = CableLossUseCase(_request(instrument), sleep_fn=lambda _: None)
        use_case.measure_all_frequencies()

        with self.assertRaisesRegex(SafetyCleanupError, "测量安全清理存在失败"):
            use_case.stop_measurement()

        record = use_case.last_cleanup_record
        self.assertIsNotNone(record)
        self.assertFalse(record.succeeded)
        self.assertIn("close", record.errors)

    def test_repeated_start_and_continue_are_rejected(self):
        use_case = CableLossUseCase(_request(_Instrument()), sleep_fn=lambda _: None)
        use_case.measure_all_frequencies()
        with self.assertRaisesRegex(RuntimeError, "已经开始"):
            use_case.measure_all_frequencies()
        use_case.continue_to_step2()
        with self.assertRaisesRegex(RuntimeError, "已经结束"):
            use_case.continue_to_step2()

    def test_path1_exception_makes_use_case_terminal(self):
        instrument = _Instrument(fail=True)
        use_case = CableLossUseCase(_request(instrument), sleep_fn=lambda _: None)

        with self.assertRaisesRegex(RuntimeError, "measurement failed"):
            use_case.measure_all_frequencies()
        with self.assertRaisesRegex(RuntimeError, "已经结束"):
            use_case.measure_all_frequencies()

    def test_result_repository_is_required(self):
        request = _request(_Instrument())
        request = CableLossMeasurementRequest(
            configuration=request.configuration,
            context=request.context,
            run_directory=request.run_directory,
            measurement_port=request.measurement_port,
            stage_recorder=request.stage_recorder,
            safety_cleanup=request.safety_cleanup,
        )
        with self.assertRaisesRegex(ValueError, "result_repository"):
            CableLossUseCase(request)

    def test_stage_result_repository_is_required(self):
        request = _request(_Instrument())
        request = CableLossMeasurementRequest(
            configuration=request.configuration,
            context=request.context,
            run_directory=request.run_directory,
            measurement_port=request.measurement_port,
            stage_recorder=request.stage_recorder,
            safety_cleanup=request.safety_cleanup,
            result_repository=request.result_repository,
        )
        with self.assertRaisesRegex(ValueError, "stage_result_repository"):
            CableLossUseCase(request)

    def test_measurement_port_is_required_before_other_dependencies(self):
        request = _request(_Instrument())
        request = CableLossMeasurementRequest(
            configuration=request.configuration,
            context=request.context,
            run_directory=request.run_directory,
            measurement_port=None,
            stage_recorder=request.stage_recorder,
            safety_cleanup=request.safety_cleanup,
        )
        with self.assertRaisesRegex(ValueError, "注入 measurement_port"):
            CableLossUseCase(request)

    def test_safety_cleanup_port_is_required(self):
        request = _request(_Instrument())
        request = CableLossMeasurementRequest(
            configuration=request.configuration,
            context=request.context,
            run_directory=request.run_directory,
            measurement_port=request.measurement_port,
            stage_recorder=request.stage_recorder,
            safety_cleanup=None,
            result_repository=request.result_repository,
        )
        with self.assertRaisesRegex(ValueError, "注入 safety_cleanup"):
            CableLossUseCase(request)

    def test_request_without_a_stage_recorder_is_rejected(self):
        """D1：结果只能落在运行目录，缺少阶段记录器的请求在构造期就失败。"""
        instrument = _Instrument()
        with self.assertRaisesRegex(ValueError, "stage_recorder"):
            CableLossMeasurementRequest(
                configuration={"test_frequencies": [1.0], "attenuator": {"type": "10dB"}},
                context=SimpleNamespace(run_id="run-cable"),
                run_directory=Path("run-directory"),
                measurement_port=instrument,
                stage_recorder=None,
                safety_cleanup=safety_cleanup_for(instrument),
            )

    def test_exception_is_cleaned_by_service_and_not_saved(self):
        instrument = _Instrument(fail=True)
        repository = _Repository()
        stage_repository = _StageRepository()
        use_case = CableLossUseCase(
            _request(instrument, repository=repository, stage_repository=stage_repository),
            sleep_fn=lambda _: None,
        )

        with self.assertRaisesRegex(RuntimeError, "measurement failed"):
            use_case.measure_all_frequencies()

        self.assertEqual(repository.calls, [])
        self.assertEqual(stage_repository.calls, [])
        self.assertEqual(instrument.close_calls, 1)

    def test_registration_failure_keeps_the_saved_stage_result(self):
        """阶段文件已提交后登记失败：不撤销正式结果，错误说明给出结果位置。"""
        stage_repository = _StageRepository()
        recorder = _FailingCompleteRecorder("run-cable", OSError("registry down"))
        use_case = CableLossUseCase(
            _request(_Instrument(), recorder=recorder, stage_repository=stage_repository),
            sleep_fn=lambda _: None,
        )
        use_case.measure_all_frequencies()

        with self.assertRaisesRegex(StageResultNotRecorded, "已保存到") as caught:
            use_case.continue_to_step2()

        # 正式结果确实已经写入阶段目录：登记失败不撤销这次保存，也不删除文件。
        [(stage, saved)] = stage_repository.calls
        self.assertIs(stage, MeasurementStage.CABLE_LOSS)
        self.assertEqual(
            caught.exception.reference.measurement_id, saved.measurement_id
        )
        self.assertIn(str(saved.measurement_id), str(caught.exception))
        self.assertIsNotNone(caught.exception.__cause__)
        # 运行阶段仍然收敛为 failed，错误说明里保留登记失败的原始原因。
        self.assertEqual([stage for stage, _error in recorder.failed], [STAGE_CABLE_LOSS])
        self.assertIn("registry down", recorder.failed[0][1])

    def test_path2_exception_leaves_use_case_terminal_and_does_not_clean_twice(self):
        instrument = _Instrument()
        repository = _Repository()
        stage_repository = _StageRepository()
        use_case = CableLossUseCase(
            _request(instrument, repository=repository, stage_repository=stage_repository),
            sleep_fn=lambda _: None,
        )
        use_case.measure_all_frequencies()
        instrument.fail = True

        with self.assertRaisesRegex(RuntimeError, "measurement failed"):
            use_case.continue_to_step2()

        self.assertEqual(stage_repository.calls, [])

        close_calls = instrument.close_calls
        use_case.stop_measurement()
        self.assertEqual(instrument.close_calls, close_calls)
        with self.assertRaisesRegex(RuntimeError, "已经结束"):
            use_case.continue_to_step2()
        with self.assertRaisesRegex(RuntimeError, "已经结束"):
            use_case.measure_all_frequencies()

    def test_cancelled_request_does_not_open_rf(self):
        instrument = _Instrument()
        token = CancellationToken()
        token.request_stop(reason="test")
        use_case = CableLossUseCase(_request(instrument, token=token), sleep_fn=lambda _: None)

        with self.assertRaises(MeasurementCancelled):
            use_case.measure_all_frequencies()

        self.assertEqual(instrument.measured, 0)
        self.assertEqual(instrument.close_calls, 1)

    def test_stop_saves_only_the_frequencies_with_both_paths(self):
        token = CancellationToken()

        class _StoppingInstrument(_Instrument):
            def measure_power_with_average(self):
                value = super().measure_power_with_average()
                if self.measured == 3:  # path1 两个频点 + path2 第一个频点
                    token.request_stop(reason="用户停止")
                return value

        instrument = _StoppingInstrument()
        recorder = RecordingStageRecorder("run-cable")
        use_case = CableLossUseCase(
            _request(
                instrument,
                token=token,
                recorder=recorder,
                frequencies=(1.0, 2.0),
            ),
            sleep_fn=lambda _: None,
        )
        use_case.measure_all_frequencies()

        with self.assertRaises(MeasurementCancelled):
            use_case.continue_to_step2()

        self.assertEqual(len(recorder.partials), 1)
        stage, payload, path = recorder.partials[0]
        self.assertEqual(stage, STAGE_CABLE_LOSS)
        self.assertEqual(path, "results/cable_loss_partial.json")
        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(payload["termination"]["intent"], "stop")
        self.assertEqual(set(payload["cable_losses"]), {"1.0"})
        self.assertEqual(use_case.last_partial_result.status.value, "cancelled")
        self.assertEqual(len(use_case.last_partial_result.points), 1)
        self.assertIn(
            (STAGE_CABLE_LOSS, "用户停止"),
            recorder.cancelled,
        )

    def test_unexpected_partial_save_failure_still_cancels_the_stage(self):
        """部分结果保存抛出意外异常时，停止语义与终态都不改变。"""
        token = CancellationToken()

        class _FailingPartialRecorder(RecordingStageRecorder):
            def save_partial(self, *args, **kwargs):
                raise RuntimeError("partial registry down")

        class _StoppingInstrument(_Instrument):
            def measure_power_with_average(self):
                value = super().measure_power_with_average()
                if self.measured == 3:  # path1 两个频点 + path2 第一个频点
                    token.request_stop(reason="用户停止")
                return value

        recorder = _FailingPartialRecorder("run-cable")
        use_case = CableLossUseCase(
            _request(
                _StoppingInstrument(),
                token=token,
                recorder=recorder,
                frequencies=(1.0, 2.0),
            ),
            sleep_fn=lambda _: None,
        )
        use_case.measure_all_frequencies()

        with self.assertRaisesRegex(RuntimeError, "partial registry down"):
            use_case.continue_to_step2()

        self.assertIn((STAGE_CABLE_LOSS, "用户停止"), recorder.cancelled)
        self.assertIsNone(use_case.last_partial_result)
        # 停止后的用例已经结束，不能继续。
        with self.assertRaisesRegex(RuntimeError, "已经结束"):
            use_case.continue_to_step2()


if __name__ == "__main__":
    unittest.main()
