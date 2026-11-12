import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.cancellation import CancellationToken, MeasurementCancelled
from application.dto import (
    AmplifierCalibrationInputs,
    AmplifierMeasurementInputs,
    AmplifierMeasurementRequest,
)
from application.measurements import AmplifierMeasurementUseCase
from application.measurements.amplifier_test import AMPLIFIER_RESULT_PATH
from application.dto.measurement_requests import StageResultDigest
from application.dto.measurement_results import measurement_result_filename
from application.ports.stage_artifacts import (
    StageArtifactReference,
    stage_artifact_filename,
)
from application.ports.stage_result_repository import (
    MeasurementStage,
    StageResultReference,
)
from application.run_stages import StageInputUnavailable, StageResultNotRecorded
from domain.configuration.models import (
    RunConfiguration,
    RunResourceMapping,
    TestPlan,
)
from domain.measurement.values import CableLossTable, Decibels, Frequency, PowerDbm
from domain.run.models import STAGE_AMPLIFIER, ArtifactKind
from tests.contract_doubles import (
    MappingInputReader,
    RecordingStageRecorder,
    amplifier_inputs,
    loaded_stage_result,
)
from tests.fakes import safety_cleanup_for


class _Instrument:
    def __init__(self):
        self.measured = 0
        self.close_calls = 0

    def setup_dut_power(self): pass
    def power_on_sequence(self): pass
    def set_power(self, _power): pass
    def set_frequency(self, _frequency): pass
    def set_center_frequency(self, _frequency): pass
    def set_span(self, _span): pass
    def rf_output_on(self): pass
    def rf_output_off(self): pass
    def measure_power_with_average(self):
        self.measured += 1
        return 0.0
    def read_voltage(self, _name, _channel): return 10.0
    def read_current(self, _name, _channel): return 1.0
    def power_off_sequence(self): pass
    def setup_driver_amplifier_power(self): pass
    def power_on_driver(self): pass
    def power_off_driver(self): pass
    def close_all(self, close_rf=False):
        self.close_calls += 1
        return []


class _Repository:
    def __init__(self): self.calls = []
    def save(self, result, **kwargs):
        self.calls.append((result, kwargs))
        return SimpleNamespace(run_directory=Path("saved-run"))


CABLE_LOSSES = {
    "1.0": {"cable1": 0.0, "cable2": 0.0, "cable3": 0.0, "cable4": 0.0}
}
# 步骤 17：新测量链路只接受显式选择的阶段结果文件；用例替身默认给出一条
# 合法外观的阶段路径，具体阶段目录边界由真实仓库用例覆盖。
SELECTED_CABLE_LOSS = (
    Path("test_results") / "cable_loss" / "20260101T000000Z_abcd.json"
)


class _StageRepository:
    """阶段结果仓库替身：按阶段返回预置的已加载结果，记录加载与保存调用。"""

    def __init__(self, loaded=None):
        self.loaded = dict(loaded or {})
        self.loads = []
        self.saved = []

    def load(self, stage, path):
        self.loads.append((stage, Path(path)))
        if stage not in self.loaded:
            self.loaded[stage] = loaded_stage_result(
                stage, cable_losses=CABLE_LOSSES
            )
        return self.loaded[stage]

    def save(self, stage, result):
        target_stage = MeasurementStage(stage)
        reference = StageResultReference(
            stage=target_stage,
            measurement_id=result.measurement_id,
            path=(
                Path("test_results")
                / target_stage.value
                / measurement_result_filename(result.measurement_id)
            ),
            sha256="a" * 64,
            size_bytes=1,
        )
        self.saved.append((target_stage, result, reference))
        return reference


class _ArtifactStore:
    """阶段交付产物替身：按产物类型返回引用，可让指定类型抛错。"""

    def __init__(self, failing=(), error_type=OSError):
        self.failing = frozenset(failing)
        self.error_type = error_type
        self.calls = []

    def save(self, stage, measurement_id, kind, result):
        kind = ArtifactKind(kind)
        self.calls.append((stage, measurement_id, kind))
        if kind in self.failing:
            raise self.error_type(f"故意拒绝 {kind.value}")
        target_stage = MeasurementStage(stage)
        return StageArtifactReference(
            stage=target_stage,
            measurement_id=measurement_id,
            kind=kind,
            path=Path("test_results")
            / target_stage.value
            / stage_artifact_filename(measurement_id, kind),
            sha256="b" * 64,
            size_bytes=1,
        )


def _request(
    instrument,
    *,
    token=None,
    repository=None,
    recorder=None,
    reader=None,
    stage_repository=None,
    artifacts=None,
    cable_loss_path=SELECTED_CABLE_LOSS,
    cable_loss_digest=None,
    driver_mapping_path=None,
    driver_mapping_digest=None,
):
    configuration = {
        "test_frequencies": [1.0],
        "attenuator": {"type": "10dB"},
        "signal_source": {"start_power": -20, "stop_power": -20, "step": 1},
        "compression_point": {"type": "1dB"},
        "driver_mode": {"enabled": False},
        "power_supply_assignment": {"dut_amplifier": {"supplies": {}}},
        "dut_config": {"max_input_power": 100},
    }
    return AmplifierMeasurementRequest(
        configuration=configuration,
        context=SimpleNamespace(run_id="run-amplifier"),
        run_directory=Path("run-directory"),
        measurement_port=instrument,
        inputs=amplifier_inputs(),
        stage_recorder=recorder or RecordingStageRecorder("run-amplifier"),
        safety_cleanup=safety_cleanup_for(instrument),
        cancellation_token=token,
        result_repository=repository or _Repository(),
        input_reader=reader or MappingInputReader(cable_loss={"cable_losses": CABLE_LOSSES}),
        stage_result_repository=stage_repository or _StageRepository(),
        stage_artifact_repository=artifacts,
        cable_loss_path=cable_loss_path,
        cable_loss_digest=cable_loss_digest,
        driver_mapping_path=driver_mapping_path,
        driver_mapping_digest=driver_mapping_digest,
        safety_options={"owns_measurement_port": True},
    )


class AmplifierMeasurementUseCaseTests(unittest.TestCase):
    def test_split_run_configuration_is_converted_to_formal_inputs(self):
        configuration = RunConfiguration(
            test_plan=TestPlan(
                frequencies=[1.0],
                start_power=-20,
                stop_power=-20,
                power_step=1,
                power_reference="dut_input",
                compression_point="1dB",
                attenuator_value=10,
                max_input_power=100,
                drain_channel={"supply_id": "PS1", "channel": "CH2"},
                channel_settings=[
                    {
                        "supply_id": "PS1",
                        "channel": "CH2",
                        "voltage": 28,
                        "current": 1,
                        "voltage_protection": 30,
                        "current_protection": 2,
                        "unit_voltage": "V",
                        "unit_current": "A",
                    }
                ],
            ),
            run_mapping=RunResourceMapping(
                instruments={},
                dut_power_plan={
                    "power_sequence": [{"supply_id": "PS1", "channel": "CH2"}],
                    "inter_channel_delay_s": 0.0,
                },
            ),
        )
        request = _request(_Instrument())
        request = AmplifierMeasurementRequest(
            configuration=configuration,
            context=request.context,
            run_directory=request.run_directory,
            measurement_port=request.measurement_port,
            inputs=AmplifierMeasurementInputs.from_run_configuration(configuration),
            stage_recorder=request.stage_recorder,
            safety_cleanup=request.safety_cleanup,
            result_repository=request.result_repository,
            input_reader=request.input_reader,
            stage_result_repository=request.stage_result_repository,
            cable_loss_path=request.cable_loss_path,
        )

        use_case = AmplifierMeasurementUseCase(request, sleep_fn=lambda _: None)

        self.assertEqual(
            [item.ghz for item in use_case.inputs.frequencies], [1.0]
        )
        self.assertEqual(use_case.inputs.attenuator, Decibels.parse("10dB"))
        plan = use_case.inputs.power_scan_schedule.plans[0]
        self.assertEqual(plan.start, PowerDbm(-20))
        self.assertEqual(plan.stop, PowerDbm(-20))
        self.assertEqual(plan.step, Decibels(1))
        self.assertEqual(use_case.inputs.target_input_powers, (PowerDbm(-20),))
        self.assertEqual(
            use_case.inputs.dut_supplies(), {"PS1": ("CH2",)}
        )
        self.assertEqual(
            [route.token for route in use_case.inputs.dut_power_plan.power_sequence],
            ["PS1/CH2"],
        )
        self.assertEqual(
            use_case.inputs.drain_channel.routes[0].token, "PS1/CH2"
        )
        self.assertEqual(
            use_case.inputs.channel_settings[
                next(iter(use_case.inputs.channel_settings))
            ].current_a,
            1.0,
        )

    def test_calculate_actual_power_delegates_to_pure_function(self):
        instrument = _Instrument()
        use_case = AmplifierMeasurementUseCase(
            _request(instrument), sleep_fn=lambda _: None
        )

        with patch(
            "application.measurements.amplifier_test.measurement_calculations"
            ".compensate_amplifier_output_power",
            return_value=PowerDbm(123.4),
        ) as calculate:
            result = use_case.calculate_actual_power(1.0, -10.0)

        self.assertEqual(result, 123.4)
        calculate.assert_called_once_with(
            measured_power=PowerDbm(-10.0),
            frequency=Frequency.from_ghz(1.0),
            loss_table=CableLossTable.from_mapping(
                use_case.loss_data["cable_losses"], source="线损输入"
            ),
            attenuator_value=Decibels.parse("10dB"),
        )

    def test_preloaded_calibration_inputs_are_reused_without_reading_the_run(self):
        """组合入口预检过的校准输入随请求复用，不再二次读取运行目录。"""

        class _UnreadableReader:
            def read_cable_loss(self, path):
                raise AssertionError(f"不应再次读取线损: {path}")

            def read_driver_mapping(self, path):
                raise AssertionError(f"不应再次读取驱动映射: {path}")

        loss_data = {"cable_losses": CABLE_LOSSES}
        request = replace(
            _request(_Instrument(), reader=_UnreadableReader()),
            calibration_inputs=AmplifierCalibrationInputs(loss_data=loss_data),
        )

        use_case = AmplifierMeasurementUseCase(request, sleep_fn=lambda _: None)

        self.assertIs(use_case.loss_data, loss_data)
        self.assertIsNone(use_case.driver_mapping)

    def test_explicit_cable_loss_path_is_used_without_preloaded_inputs(self):
        """没有预检对象时，显式选择（含摘要）仍是校准输入的权威来源。"""
        reader = MappingInputReader(cable_loss={"cable_losses": CABLE_LOSSES})
        selected = Path("test_results") / "cable_loss" / "20260101T000000Z_abcd.json"
        digest = StageResultDigest(sha256="a" * 64, size_bytes=32)
        loaded = loaded_stage_result(
            MeasurementStage.CABLE_LOSS, sha256="a" * 64, size_bytes=32
        )
        stage_repository = _StageRepository(
            {MeasurementStage.CABLE_LOSS: loaded}
        )

        use_case = AmplifierMeasurementUseCase(
            _request(
                _Instrument(),
                reader=reader,
                stage_repository=stage_repository,
                cable_loss_path=selected,
                cable_loss_digest=digest,
            ),
            sleep_fn=lambda _: None,
        )

        self.assertEqual(use_case.loss_data, {"cable_losses": CABLE_LOSSES})
        self.assertIsNone(use_case.driver_mapping)
        self.assertEqual(
            stage_repository.loads,
            [(MeasurementStage.CABLE_LOSS, selected)],
        )
        self.assertEqual(reader.paths, [])
        self.assertEqual(reader.results, [("cable_loss", loaded.result)])

    def test_explicit_cable_loss_path_without_digest_still_uses_the_stage_repository(
        self,
    ):
        """省略选择时摘要只跳过内容比对，阶段契约仍由仓库完整校验。"""
        reader = MappingInputReader(cable_loss={"cable_losses": CABLE_LOSSES})
        stage_repository = _StageRepository()

        use_case = AmplifierMeasurementUseCase(
            _request(
                _Instrument(),
                reader=reader,
                stage_repository=stage_repository,
            ),
            sleep_fn=lambda _: None,
        )

        self.assertEqual(use_case.loss_data, {"cable_losses": CABLE_LOSSES})
        self.assertEqual(
            stage_repository.loads,
            [(MeasurementStage.CABLE_LOSS, SELECTED_CABLE_LOSS)],
        )
        self.assertEqual(reader.paths, [])
        self.assertEqual(len(reader.results), 1)

    def test_explicit_driver_mapping_path_is_used_without_preloaded_inputs(self):
        """没有预检对象时，显式选择的驱动映射同样是校准输入的权威来源。"""
        reader = MappingInputReader(
            cable_loss={"cable_losses": CABLE_LOSSES},
            driver_mapping={"1.0": {"-20.0": -20.0}},
        )
        selected = (
            Path("test_results")
            / "driver_mapping"
            / "20260102T000000Z_ef01.json"
        )
        digest = StageResultDigest(sha256="b" * 64, size_bytes=64)
        loaded = loaded_stage_result(
            MeasurementStage.DRIVER_MAPPING, sha256="b" * 64, size_bytes=64
        )
        stage_repository = _StageRepository(
            {MeasurementStage.DRIVER_MAPPING: loaded}
        )

        request = replace(
            _request(
                _Instrument(),
                reader=reader,
                stage_repository=stage_repository,
                driver_mapping_path=selected,
                driver_mapping_digest=digest,
            ),
            inputs=amplifier_inputs(driver_enabled=True),
        )

        use_case = AmplifierMeasurementUseCase(request, sleep_fn=lambda _: None)

        # 线损与映射都按各自阶段加载并核对摘要；映射结果直接转成正式输入。
        self.assertEqual(
            stage_repository.loads,
            [
                (MeasurementStage.CABLE_LOSS, SELECTED_CABLE_LOSS),
                (MeasurementStage.DRIVER_MAPPING, selected),
            ],
        )
        self.assertEqual(
            use_case.driver_mapping.raw_mapping(), {"1.0": {"-20.0": -20.0}}
        )
        self.assertEqual(reader.paths, [])
        self.assertEqual(
            [kind for kind, _result in reader.results],
            ["cable_loss", "driver_mapping"],
        )
        self.assertIs(reader.results[1][1], loaded.result)

    def test_replaced_explicit_driver_mapping_is_rejected_before_measurement(self):
        """显式驱动映射在选择后被替换时，构造阶段就失败，不消费被替换的内容。"""
        instrument = _Instrument()
        reader = MappingInputReader(
            cable_loss={"cable_losses": CABLE_LOSSES},
            driver_mapping={"1.0": {"-20.0": -20.0}},
        )
        selected = (
            Path("test_results")
            / "driver_mapping"
            / "20260102T000000Z_ef01.json"
        )
        digest = StageResultDigest(sha256="b" * 64, size_bytes=64)
        loaded = loaded_stage_result(
            MeasurementStage.DRIVER_MAPPING, sha256="c" * 64, size_bytes=64
        )
        stage_repository = _StageRepository(
            {MeasurementStage.DRIVER_MAPPING: loaded}
        )

        request = replace(
            _request(
                instrument,
                reader=reader,
                stage_repository=stage_repository,
                driver_mapping_path=selected,
                driver_mapping_digest=digest,
            ),
            inputs=amplifier_inputs(driver_enabled=True),
        )

        with self.assertRaisesRegex(StageInputUnavailable, "已被修改或替换"):
            AmplifierMeasurementUseCase(request, sleep_fn=lambda _: None)

        self.assertEqual(
            stage_repository.loads,
            [
                (MeasurementStage.CABLE_LOSS, SELECTED_CABLE_LOSS),
                (MeasurementStage.DRIVER_MAPPING, selected),
            ],
        )
        # 被替换的映射不会转成校准输入；线损仍按显式选择的阶段文件读取。
        self.assertEqual(
            [kind for kind, _result in reader.results], ["cable_loss"]
        )
        self.assertEqual(reader.paths, [])
        # 构造阶段的失败发生在任何仪器动作之前。
        self.assertEqual(instrument.measured, 0)
        self.assertEqual(instrument.close_calls, 0)

    def test_replaced_explicit_cable_loss_is_rejected_before_measurement(self):
        reader = MappingInputReader(cable_loss={"cable_losses": CABLE_LOSSES})
        selected = Path("test_results") / "cable_loss" / "20260101T000000Z_abcd.json"
        digest = StageResultDigest(sha256="a" * 64, size_bytes=32)
        loaded = loaded_stage_result(
            MeasurementStage.CABLE_LOSS, sha256="b" * 64, size_bytes=32
        )
        stage_repository = _StageRepository(
            {MeasurementStage.CABLE_LOSS: loaded}
        )

        with self.assertRaisesRegex(StageInputUnavailable, "已被修改或替换"):
            AmplifierMeasurementUseCase(
                _request(
                    _Instrument(),
                    reader=reader,
                    stage_repository=stage_repository,
                    cable_loss_path=selected,
                    cable_loss_digest=digest,
                ),
                sleep_fn=lambda _: None,
            )

        self.assertEqual(reader.paths, [])
        self.assertEqual(reader.results, [])

    def test_completes_and_saves_structured_result(self):
        instrument = _Instrument()
        recorder = RecordingStageRecorder("run-amplifier")
        stage_repository = _StageRepository()
        use_case = AmplifierMeasurementUseCase(
            _request(instrument, recorder=recorder, stage_repository=stage_repository),
            sleep_fn=lambda _: None,
        )

        result = use_case.measure_all_frequencies()

        self.assertEqual(result, use_case.last_result)
        self.assertEqual(
            use_case.measurement_results,
            {item.frequency_ghz: item for item in result.frequencies},
        )
        # 步骤 20：正式结果写入阶段目录，运行目录只登记引用与拆分审计文件。
        [(stage, saved_result, reference)] = stage_repository.saved
        self.assertIs(stage, MeasurementStage.AMPLIFIER)
        self.assertEqual(saved_result.measurement_id, result.measurement_id)
        self.assertEqual(
            recorder.result_paths(), {STAGE_AMPLIFIER: AMPLIFIER_RESULT_PATH}
        )
        self.assertEqual(recorder.completed_runs, 1)
        self.assertEqual(
            result.result_reference["archive_path"], str(reference.path)
        )
        self.assertEqual(use_case.last_stage_artifact_references, ())
        self.assertEqual(use_case.run_directory, Path("run-directory"))
        self.assertEqual(instrument.close_calls, 1)

    def test_stage_bookkeeping_failure_reports_the_saved_result(self):
        """阶段文件已提交后运行登记失败：保留结果并明确报告它的位置。"""

        class _FailingRecorder(RecordingStageRecorder):
            def complete_with_stage_result(self, *args, **kwargs):
                raise RuntimeError("manifest 不可写")

        stage_repository = _StageRepository()
        recorder = _FailingRecorder("run-amplifier")
        use_case = AmplifierMeasurementUseCase(
            _request(_Instrument(), recorder=recorder, stage_repository=stage_repository),
            sleep_fn=lambda _: None,
        )

        with self.assertRaises(StageResultNotRecorded) as caught:
            use_case.measure_all_frequencies()

        self.assertEqual(caught.exception.stage, MeasurementStage.AMPLIFIER)
        self.assertEqual(caught.exception.reference, stage_repository.saved[0][2])
        self.assertIn(str(stage_repository.saved[0][2].path), str(caught.exception))
        # 正式结果已提交：阶段仍记为失败，但结果文件没有被撤销或删除。
        self.assertEqual(len(stage_repository.saved), 1)
        self.assertEqual([stage for stage, _ in recorder.failed], [STAGE_AMPLIFIER])

    def test_run_completion_failure_keeps_the_stage_result(self):
        """阶段已登记、运行级完成写入失败时同样不能把测量报成“没有结果”。"""

        class _FailingCompleteRun(RecordingStageRecorder):
            def complete_run(self, *, now=None):
                raise RuntimeError("运行状态不可写")

        stage_repository = _StageRepository()
        recorder = _FailingCompleteRun("run-amplifier")
        use_case = AmplifierMeasurementUseCase(
            _request(_Instrument(), recorder=recorder, stage_repository=stage_repository),
            sleep_fn=lambda _: None,
        )

        with self.assertRaises(StageResultNotRecorded):
            use_case.measure_all_frequencies()

        self.assertEqual(len(stage_repository.saved), 1)
        self.assertEqual(len(recorder.stage_references), 1)
        # 阶段已登记完成，不能再把它改写成 failed。
        self.assertEqual(recorder.failed, [])

    def test_without_stage_repository_keeps_the_run_directory_contract(self):
        """未装配阶段结果仓库的旧兼容调用方仍把完整结果写进运行目录。"""
        recorder = RecordingStageRecorder("run-amplifier")
        request = replace(
            _request(_Instrument(), recorder=recorder),
            stage_result_repository=None,
            cable_loss_path=None,
            cable_loss_digest=None,
            calibration_inputs=AmplifierCalibrationInputs(
                loss_data={"cable_losses": CABLE_LOSSES}
            ),
        )
        use_case = AmplifierMeasurementUseCase(request, sleep_fn=lambda _: None)

        result = use_case.measure_all_frequencies()

        self.assertEqual(
            recorder.result_paths(), {STAGE_AMPLIFIER: AMPLIFIER_RESULT_PATH}
        )
        self.assertEqual(
            result.result_reference["archive_path"],
            str(Path("run-directory") / AMPLIFIER_RESULT_PATH),
        )
        self.assertIsNone(use_case.last_archival_outcome)
        self.assertEqual(use_case.last_stage_artifact_references, ())

    def test_artifact_failure_keeps_the_formal_result_and_tries_both_kinds(self):
        """产物是可选派生物：CSV 意外抛错也不影响正式结果与报告尝试。"""
        recorder = RecordingStageRecorder("run-amplifier")
        stage_repository = _StageRepository()
        artifacts = _ArtifactStore(
            failing={ArtifactKind.CSV}, error_type=TypeError
        )
        use_case = AmplifierMeasurementUseCase(
            _request(
                _Instrument(),
                recorder=recorder,
                stage_repository=stage_repository,
                artifacts=artifacts,
            ),
            sleep_fn=lambda _: None,
        )

        with self.assertLogs(
            "application.measurements.amplifier_test", level="ERROR"
        ):
            result = use_case.measure_all_frequencies()

        self.assertIs(result, use_case.last_result)
        self.assertEqual(
            [kind for _, _, kind in artifacts.calls],
            [ArtifactKind.CSV, ArtifactKind.REPORT],
        )
        self.assertEqual(
            [reference.kind for reference in use_case.last_stage_artifact_references],
            [ArtifactKind.REPORT],
        )
        self.assertEqual(recorder.completed_runs, 1)
        self.assertEqual(len(stage_repository.saved), 1)

    def test_measurement_port_is_required_before_input_loading(self):
        request = _request(None)
        with self.assertRaisesRegex(ValueError, "注入 measurement_port"):
            AmplifierMeasurementUseCase(request)

    def test_cancelled_request_is_cleaned_by_service(self):
        instrument = _Instrument()
        token = CancellationToken()
        token.request_stop(reason="test")
        use_case = AmplifierMeasurementUseCase(
            _request(instrument, token=token), sleep_fn=lambda _: None
        )

        with self.assertRaises(MeasurementCancelled):
            use_case.measure_all_frequencies()

        self.assertEqual(instrument.measured, 0)

    def test_stop_saves_only_frequencies_that_finished_the_sweep(self):
        token = CancellationToken()

        class _StoppingInstrument(_Instrument):
            def measure_power_with_average(self):
                value = super().measure_power_with_average()
                if self.measured == 1:
                    token.request_stop(reason="用户停止")
                return value

        inputs = replace(
            amplifier_inputs(),
            frequencies=(Frequency.from_ghz(1.0), Frequency.from_ghz(2.0)),
        )
        losses = {
            "1.0": {"cable1": 0.0, "cable2": 0.0, "cable3": 0.0, "cable4": 0.0},
            "2.0": {"cable1": 0.0, "cable2": 0.0, "cable3": 0.0, "cable4": 0.0},
        }
        recorder = RecordingStageRecorder("run-amplifier")
        request = replace(
            _request(
                _StoppingInstrument(),
                token=token,
                recorder=recorder,
                reader=MappingInputReader(cable_loss={"cable_losses": losses}),
            ),
            inputs=inputs,
        )
        use_case = AmplifierMeasurementUseCase(request, sleep_fn=lambda _: None)

        with self.assertRaises(MeasurementCancelled):
            use_case.measure_all_frequencies()

        self.assertEqual(len(recorder.partials), 1)
        stage, payload, path = recorder.partials[0]
        self.assertEqual(stage, STAGE_AMPLIFIER)
        self.assertEqual(path, "results/amplifier_partial.json")
        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(set(payload["results"]), {"1.0"})
        self.assertEqual(use_case.last_partial_result.status.value, "cancelled")
        self.assertEqual(len(use_case.last_partial_result.frequencies), 1)
        self.assertIn((STAGE_AMPLIFIER, "用户停止"), recorder.cancelled)

    def test_unexpected_partial_save_failure_still_cancels_the_stage(self):
        """部分结果保存抛出意外异常时，阶段仍必须落到 cancelled，不能停在 running。"""
        token = CancellationToken()

        class _FailingPartialRecorder(RecordingStageRecorder):
            def save_partial(self, *args, **kwargs):
                raise RuntimeError("partial registry down")

        class _StoppingInstrument(_Instrument):
            def measure_power_with_average(self):
                value = super().measure_power_with_average()
                if self.measured == 1:
                    token.request_stop(reason="用户停止")
                return value

        inputs = replace(
            amplifier_inputs(),
            frequencies=(Frequency.from_ghz(1.0), Frequency.from_ghz(2.0)),
        )
        losses = {
            "1.0": {"cable1": 0.0, "cable2": 0.0, "cable3": 0.0, "cable4": 0.0},
            "2.0": {"cable1": 0.0, "cable2": 0.0, "cable3": 0.0, "cable4": 0.0},
        }
        recorder = _FailingPartialRecorder("run-amplifier")
        request = replace(
            _request(
                _StoppingInstrument(),
                token=token,
                recorder=recorder,
                reader=MappingInputReader(cable_loss={"cable_losses": losses}),
            ),
            inputs=inputs,
        )
        use_case = AmplifierMeasurementUseCase(request, sleep_fn=lambda _: None)

        with self.assertRaisesRegex(RuntimeError, "partial registry down"):
            use_case.measure_all_frequencies()

        self.assertIn((STAGE_AMPLIFIER, "用户停止"), recorder.cancelled)
        self.assertEqual(recorder.failed, [])
        self.assertIsNone(use_case.last_partial_result)


if __name__ == "__main__":
    unittest.main()
