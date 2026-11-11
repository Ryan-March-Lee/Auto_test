import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.cancellation import CancellationToken, MeasurementCancelled
from application.dto import (
    DriverPowerMappingRequest,
    validate_generated_measurement_id,
)
from application.dto.measurement_requests import StageResultDigest
from application.measurements import DriverPowerMappingUseCase
from application.ports.stage_result_repository import (
    MeasurementStage,
    StageResultReference,
)
from application.run_stages import StageInputUnavailable, StageResultNotRecorded
from app.events import RealtimeDataEvent
from domain.run.models import STAGE_CABLE_LOSS, STAGE_DRIVER_MAPPING
from tests.contract_doubles import MappingInputReader, RecordingStageRecorder
from tests.fakes import safety_cleanup_for


CABLE_LOSS = {"cable_losses": {"1.0": {"cable2": 2.0}}}


class _Instrument:
    def __init__(self):
        self.measured = 0
        self.power = 0.0
        self.frequency = None
        self.rf_off_calls = 0
        self.driver_off_calls = 0

    def setup_driver_amplifier_power(self): pass
    def power_on_driver(self): pass
    def set_power(self, power): self.power = power
    def set_frequency(self, frequency): self.frequency = frequency
    def set_center_frequency(self, _frequency): pass
    def set_span(self, _span): pass
    def rf_output_on(self): pass

    def measure_power_with_average(self):
        self.measured += 1
        # 补偿后输出 = 源功率 + 1 dB：线损补偿（衰减器 10dB + 线②2dB）由服务加回。
        return self.power + 1.0 - 12.0

    def rf_output_off(self):
        self.rf_off_calls += 1

    def power_off_driver(self):
        self.driver_off_calls += 1

    def close_all(self, close_rf=False):
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
            path=Path("stage-results")
            / MeasurementStage.DRIVER_MAPPING.value
            / f"{result.measurement_id}.json",
            sha256="0" * 64,
            size_bytes=0,
        )


class _FailingStageRepository:
    """阶段结果仓库替身：正式结果写入阶段目录时失败。"""

    def __init__(self, error):
        self.error = error
        self.calls = []

    def save(self, stage, result):
        self.calls.append((stage, result))
        raise self.error


class _OrderedStageRepository(_StageRepository):
    """记录“阶段目录写入”在保存顺序中的位置。"""

    def __init__(self, order):
        super().__init__()
        self.order = order

    def save(self, stage, result):
        self.order.append("stage-save")
        return super().save(stage, result)


class _OrderedCompleteRecorder(RecordingStageRecorder):
    """记录“运行引用登记”在保存顺序中的位置。"""

    def __init__(self, run_id, order):
        super().__init__(run_id)
        self.order = order

    def complete_with_stage_result(
        self, stage, reference, *, description=None, now=None
    ):
        self.order.append("register-reference")
        return super().complete_with_stage_result(
            stage, reference, description=description, now=now
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
    reader=None,
    recorder=None,
    events=None,
    token=None,
    repository=None,
    stage_repository=None,
    frequencies=(1.0,),
    loss_data=None,
    cable_loss_path=None,
    cable_loss_digest=None,
):
    configuration = {
        "test_frequencies": list(frequencies),
        "attenuator": {"type": "10dB"},
        "signal_source": {
            "power_scan_plans": [
                {"start_power": -20, "step": 1, "stop_power": -20}
            ]
        },
        "max_dut_input_power": 100.0,
        "driver_gain_estimate_db": 1.0,
    }
    return DriverPowerMappingRequest(
        configuration=configuration,
        context=SimpleNamespace(run_id="run-mapping"),
        run_directory=Path("run-directory"),
        measurement_port=instrument,
        stage_recorder=recorder or RecordingStageRecorder("run-mapping"),
        safety_cleanup=safety_cleanup_for(instrument),
        event_sink=events,
        cancellation_token=token,
        result_repository=repository or _Repository(),
        stage_result_repository=stage_repository or _StageRepository(),
        input_reader=reader or MappingInputReader(cable_loss=CABLE_LOSS),
        cable_loss_path=cable_loss_path,
        loss_data=loss_data,
        cable_loss_digest=cable_loss_digest,
        safety_options={"owns_measurement_port": True},
    )


class DriverPowerMappingUseCaseTests(unittest.TestCase):
    def test_explicit_cable_loss_path_replaces_the_run_reference(self):
        """显式选择的线损直接作为输入来源，不读当前运行登记的引用。"""
        reader = MappingInputReader(cable_loss=CABLE_LOSS)
        recorder = RecordingStageRecorder("run-mapping")
        selected = Path("test_results") / "cable_loss" / "20260101T000000Z_abcd.json"
        use_case = DriverPowerMappingUseCase(
            _request(
                _Instrument(),
                reader=reader,
                recorder=recorder,
                cable_loss_path=selected,
            ),
            sleep_fn=lambda _: None,
        )

        self.assertEqual(reader.paths, [selected])
        self.assertEqual(use_case.loss_data, CABLE_LOSS)
        self.assertEqual(recorder.verified, [])

    def test_pinned_cable_loss_path_is_verified_and_read_once(self):
        """带选择时摘要的显式路径：仓库加载 + 摘要核对 + 同一份结果转成输入。"""
        reader = MappingInputReader(cable_loss=CABLE_LOSS)
        recorder = RecordingStageRecorder("run-mapping")
        selected = Path("test_results") / "cable_loss" / "20260101T000000Z_abcd.json"
        digest = StageResultDigest(sha256="a" * 64, size_bytes=32)
        sentinel = object()
        loaded = SimpleNamespace(
            sha256="a" * 64, size_bytes=32, path=selected, result=sentinel
        )
        stage_repository = SimpleNamespace(load=lambda stage, path: loaded)

        use_case = DriverPowerMappingUseCase(
            _request(
                _Instrument(),
                reader=reader,
                recorder=recorder,
                stage_repository=stage_repository,
                cable_loss_path=selected,
                cable_loss_digest=digest,
            ),
            sleep_fn=lambda _: None,
        )

        self.assertEqual(use_case.loss_data, CABLE_LOSS)
        self.assertEqual(reader.paths, [])
        self.assertEqual(reader.results, [("cable_loss", sentinel)])
        self.assertEqual(recorder.verified, [])

    def test_replaced_pinned_cable_loss_is_rejected_before_measurement(self):
        """选择后文件内容被替换：拒绝消费，且没有进入任何测量步骤。"""
        reader = MappingInputReader(cable_loss=CABLE_LOSS)
        instrument = _Instrument()
        selected = Path("test_results") / "cable_loss" / "20260101T000000Z_abcd.json"
        digest = StageResultDigest(sha256="a" * 64, size_bytes=32)
        loaded = SimpleNamespace(
            sha256="b" * 64, size_bytes=32, path=selected, result=object()
        )
        stage_repository = SimpleNamespace(load=lambda stage, path: loaded)

        with self.assertRaisesRegex(StageInputUnavailable, "已被修改或替换"):
            DriverPowerMappingUseCase(
                _request(
                    instrument,
                    reader=reader,
                    stage_repository=stage_repository,
                    cable_loss_path=selected,
                    cable_loss_digest=digest,
                ),
                sleep_fn=lambda _: None,
            )

        self.assertEqual(reader.paths, [])
        self.assertEqual(reader.results, [])
        self.assertEqual(instrument.measured, 0)

    def test_preloaded_loss_data_is_reused_without_reading_a_file(self):
        """组合入口预检读取的线损文档直接复用，避免第二次读取与来源漂移。"""
        reader = MappingInputReader(cable_loss=CABLE_LOSS)
        preloaded = {"cable_losses": {"1.0": {"cable2": 2.0}}}
        use_case = DriverPowerMappingUseCase(
            _request(_Instrument(), reader=reader, loss_data=preloaded),
            sleep_fn=lambda _: None,
        )

        self.assertIs(use_case.loss_data, preloaded)
        self.assertEqual(reader.paths, [])

    def test_current_run_loss_input_completes_and_saves_result(self):
        instrument = _Instrument()
        events = _Events()
        recorder = RecordingStageRecorder("run-mapping")
        stage_repository = _StageRepository()
        use_case = DriverPowerMappingUseCase(
            _request(
                instrument,
                recorder=recorder,
                events=events,
                stage_repository=stage_repository,
            ),
            sleep_fn=lambda _: None,
        )

        result = use_case.measure_all_frequencies()

        self.assertEqual(result, use_case.last_result)
        self.assertIn("1.0", use_case.power_mapping)
        # 正式结果先写入独立阶段目录：带测量身份，运行目录只登记引用。
        [(stage, saved)] = stage_repository.calls
        self.assertIs(stage, MeasurementStage.DRIVER_MAPPING)
        self.assertEqual(
            saved.measurement_id,
            validate_generated_measurement_id(result.measurement_id),
        )
        self.assertEqual(
            recorder.result_paths(),
            {STAGE_DRIVER_MAPPING: f"inputs/{STAGE_DRIVER_MAPPING}.json"},
        )
        [(reference_stage, reference)] = recorder.stage_references
        self.assertEqual(reference_stage, STAGE_DRIVER_MAPPING)
        self.assertEqual(reference.measurement_id, result.measurement_id)
        self.assertEqual(
            result.result_reference["archive_path"], str(reference.path)
        )
        self.assertEqual(use_case.run_directory, Path("run-directory"))
        self.assertTrue(any(isinstance(event, RealtimeDataEvent) for event in events.events))
        self.assertEqual(instrument.rf_off_calls, 1)
        self.assertEqual(instrument.driver_off_calls, 1)

    def test_completed_result_contains_the_target_grid(self):
        """步骤 4.2：完整映射写入目标表，正式测量可直接消费。"""
        result = DriverPowerMappingUseCase(
            _request(_Instrument()), sleep_fn=lambda _: None
        ).measure_all_frequencies()

        self.assertEqual(
            result.to_dict()["target_mapping"],
            {
                "1.0": [
                    {
                        "target_input_power_dbm": -20.0,
                        "source_power_dbm": -21.0,
                        "driver_output_power_dbm": -20.0,
                    }
                ]
            },
        )

    def test_unfinished_cable_loss_stage_is_rejected_before_hardware_action(self):
        """前置阶段没有完成时，映射测量必须在任何硬件动作前失败。"""
        instrument = _Instrument()
        recorder = RecordingStageRecorder("run-mapping", missing_stages=(STAGE_CABLE_LOSS,))
        with self.assertRaises(StageInputUnavailable):
            DriverPowerMappingUseCase(
                _request(instrument, recorder=recorder), sleep_fn=lambda _: None
            )
        self.assertEqual(instrument.measured, 0)

    def test_measurement_port_is_required_before_input_loading(self):
        request = _request(None)
        with self.assertRaisesRegex(ValueError, "注入 measurement_port"):
            DriverPowerMappingUseCase(request)

    def test_stage_result_repository_is_required(self):
        request = _request(_Instrument())
        request = DriverPowerMappingRequest(
            configuration=request.configuration,
            context=request.context,
            run_directory=request.run_directory,
            measurement_port=request.measurement_port,
            stage_recorder=request.stage_recorder,
            safety_cleanup=request.safety_cleanup,
            result_repository=request.result_repository,
            input_reader=request.input_reader,
        )
        with self.assertRaisesRegex(ValueError, "stage_result_repository"):
            DriverPowerMappingUseCase(request)

    def test_registration_failure_keeps_the_saved_stage_result(self):
        """阶段文件已提交后登记失败：不撤销正式结果，错误说明给出结果位置。"""
        stage_repository = _StageRepository()
        recorder = _FailingCompleteRecorder("run-mapping", OSError("registry down"))
        use_case = DriverPowerMappingUseCase(
            _request(
                _Instrument(),
                recorder=recorder,
                stage_repository=stage_repository,
            ),
            sleep_fn=lambda _: None,
        )

        with self.assertRaisesRegex(StageResultNotRecorded, "已保存到") as caught:
            use_case.measure_all_frequencies()

        reference = caught.exception.reference
        self.assertIs(reference.stage, MeasurementStage.DRIVER_MAPPING)
        self.assertEqual(
            reference.measurement_id,
            validate_generated_measurement_id(reference.measurement_id),
        )
        # 正式结果没有被删除：仓库替身记录了实际写入的阶段结果。
        [(_stage, saved)] = stage_repository.calls
        self.assertEqual(saved.measurement_id, reference.measurement_id)
        # 运行阶段没有停在 running，也没有被误记成 completed。
        self.assertEqual(
            [stage for stage, _error in recorder.failed], [STAGE_DRIVER_MAPPING]
        )
        self.assertEqual(recorder.completed, [])

    def test_stage_directory_save_failure_does_not_register_or_archive(self):
        """阶段目录写入失败时，没有正式结果可登记，也不应触发归档。"""
        recorder = RecordingStageRecorder("run-mapping")
        stage_repository = _FailingStageRepository(OSError("disk full"))
        archived = []

        with patch(
            "application.measurements.driver_mapping.archive_completed_stage",
            side_effect=lambda *args, **kwargs: archived.append((args, kwargs)),
        ):
            with self.assertRaisesRegex(OSError, "disk full"):
                DriverPowerMappingUseCase(
                    _request(
                        _Instrument(),
                        recorder=recorder,
                        stage_repository=stage_repository,
                    ),
                    sleep_fn=lambda _: None,
                ).measure_all_frequencies()

        [(stage, saved)] = stage_repository.calls
        self.assertIs(stage, MeasurementStage.DRIVER_MAPPING)
        # 身份在写盘前已分配，因此失败调用也携带可追溯的正式身份。
        self.assertEqual(
            saved.measurement_id,
            validate_generated_measurement_id(saved.measurement_id),
        )
        # 没有阶段文件，就不允许登记引用，也不允许把阶段收敛为 completed。
        self.assertEqual(recorder.stage_references, [])
        self.assertEqual(recorder.completed, [])
        self.assertEqual(
            [stage for stage, _error in recorder.failed], [STAGE_DRIVER_MAPPING]
        )
        self.assertEqual(archived, [])

    def test_formal_result_is_saved_before_registration_and_archival(self):
        """保存顺序固定：阶段目录写入 → 运行引用登记 → 自动归档。"""
        order = []
        stage_repository = _OrderedStageRepository(order)
        recorder = _OrderedCompleteRecorder("run-mapping", order)

        def archive(*_args, **_kwargs):
            order.append("archive")
            return None

        with patch(
            "application.measurements.driver_mapping.archive_completed_stage",
            side_effect=archive,
        ):
            DriverPowerMappingUseCase(
                _request(
                    _Instrument(),
                    recorder=recorder,
                    stage_repository=stage_repository,
                ),
                sleep_fn=lambda _: None,
            ).measure_all_frequencies()

        self.assertEqual(order, ["stage-save", "register-reference", "archive"])

    def test_reader_reads_the_current_run_input_path(self):
        reader = MappingInputReader(cable_loss=CABLE_LOSS)
        use_case = DriverPowerMappingUseCase(
            _request(_Instrument(), reader=reader), sleep_fn=lambda _: None
        )
        self.assertEqual(use_case.loss_data, CABLE_LOSS)
        self.assertEqual(
            reader.paths,
            [Path("runs") / "run-mapping" / "inputs" / f"{STAGE_CABLE_LOSS}.json"],
        )

    def test_missing_run_input_file_fails_before_hardware_action(self):
        """运行目录里的输入读不到时，映射测量必须在任何硬件动作前失败。"""
        instrument = _Instrument()
        with self.assertRaises(FileNotFoundError):
            DriverPowerMappingUseCase(
                _request(instrument, reader=MappingInputReader()), sleep_fn=lambda _: None
            )
        self.assertEqual(instrument.measured, 0)

    def test_cancelled_request_stops_before_measurement_and_cleans_up(self):
        instrument = _Instrument()
        token = CancellationToken()
        token.request_stop(reason="test")
        use_case = DriverPowerMappingUseCase(
            _request(instrument, token=token),
            sleep_fn=lambda _: None,
        )

        with self.assertRaises(MeasurementCancelled):
            use_case.measure_all_frequencies()

        self.assertEqual(instrument.measured, 0)
        self.assertEqual(instrument.driver_off_calls, 1)
        self.assertEqual(instrument.rf_off_calls, 0)

    def test_stop_saves_only_frequencies_that_finished_the_sweep(self):
        token = CancellationToken()

        class _StoppingInstrument(_Instrument):
            def set_frequency(self, frequency):
                super().set_frequency(frequency)
                if frequency == 2.0 and not token.is_cancelled:
                    token.request_stop(reason="用户停止")

        instrument = _StoppingInstrument()
        recorder = RecordingStageRecorder("run-mapping")
        use_case = DriverPowerMappingUseCase(
            _request(
                instrument,
                token=token,
                recorder=recorder,
                frequencies=(1.0, 2.0),
            ),
            sleep_fn=lambda _: None,
        )

        with self.assertRaises(MeasurementCancelled):
            use_case.measure_all_frequencies()

        self.assertEqual(len(recorder.partials), 1)
        stage, payload, path = recorder.partials[0]
        self.assertEqual(stage, STAGE_DRIVER_MAPPING)
        self.assertEqual(path, "results/driver_mapping_partial.json")
        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(set(payload["raw_points"]), {"1.0"})
        self.assertEqual(payload["target_mapping"], {})
        self.assertEqual(use_case.last_partial_result.status.value, "cancelled")
        self.assertEqual(len(use_case.last_partial_result.raw_points), 2)
        self.assertIn((STAGE_DRIVER_MAPPING, "用户停止"), recorder.cancelled)

    def test_unexpected_partial_save_failure_still_cancels_the_stage(self):
        """部分结果保存抛出意外异常时，阶段仍必须落到 cancelled，不能停在 running。"""
        token = CancellationToken()

        class _FailingPartialRecorder(RecordingStageRecorder):
            def save_partial(self, *args, **kwargs):
                raise RuntimeError("partial registry down")

        class _StoppingInstrument(_Instrument):
            def set_frequency(self, frequency):
                super().set_frequency(frequency)
                if frequency == 2.0 and not token.is_cancelled:
                    token.request_stop(reason="用户停止")

        recorder = _FailingPartialRecorder("run-mapping")
        use_case = DriverPowerMappingUseCase(
            _request(
                _StoppingInstrument(),
                token=token,
                recorder=recorder,
                frequencies=(1.0, 2.0),
            ),
            sleep_fn=lambda _: None,
        )

        with self.assertRaisesRegex(RuntimeError, "partial registry down"):
            use_case.measure_all_frequencies()

        self.assertIn((STAGE_DRIVER_MAPPING, "用户停止"), recorder.cancelled)
        self.assertEqual(recorder.failed, [])
        self.assertIsNone(use_case.last_partial_result)


if __name__ == "__main__":
    unittest.main()
