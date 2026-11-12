"""步骤 4：把自动归档（CSV 生成）接入三类测试的成功完成路径。

覆盖重构计划「步骤 4：把 CSV 生成接入成功完成路径」的接线口径：

- 主功放阶段 ``complete()`` + ``complete_run()`` 都成功后自动归档，CSV 出现在运行目录
  ``artifacts/`` 的约定位置，路径登记在归档结果与 manifest 中；
- 线损与驱动映射的成功边界同样接入归档，但按契约把 CSV/报告登记为 ``not_applicable``；
- 产物失败（CSV 写不进去）只把该产物标为 ``failed``：正式 JSON、阶段状态和运行状态都不变；
- 归档本身的可预期失败（请求不合法、manifest 写回失败）只写错误日志，不改变测量结果；
- 未注入归档服务（旧兼容流程）时不产生任何交付产物。
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from application.archival_service import (
    ArchivalRequestError,
    ResultArchivalService,
    archive_completed_stage,
)
from application.dto import (
    AmplifierCalibrationInputs,
    AmplifierMeasurementRequest,
    CableLossMeasurementRequest,
    CableLossMeasurementResult,
    CableLossRow,
    DriverPowerMappingRequest,
    generate_measurement_id,
)
from application.dto.archival import ArchivalStatus, ArchivalTestStatus
from application.ports.stage_result_repository import MeasurementStage
from application.measurements import (
    AmplifierMeasurementUseCase,
    CableLossUseCase,
    DriverPowerMappingUseCase,
)
from application.run_stages import RunStageRecorder
from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    CABLE_LOSS_INPUT_PATH,
    DRIVER_MAPPING_INPUT_PATH,
    STAGE_AMPLIFIER,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
    ArtifactKind,
    ArtifactStatus,
    RunStatus,
    StageStatus,
)
from infrastructure.persistence import artifact_generator
from infrastructure.persistence.artifact_generator import ArchiveArtifactGenerator
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence.result_repository import FileMeasurementResultRepository
from infrastructure.persistence.run_repository import RunRepository
from infrastructure.persistence.stage_result_repository import FileStageResultRepository
from application.configuration_authoring import author_official_configuration
from tests.contract_doubles import MappingInputReader, amplifier_inputs
from tests.fakes import safety_cleanup_for


RUN_ID = "20261022T153000Z_a1b2c3d4"
CREATED_AT = datetime(2026, 10, 22, 15, 30, tzinfo=timezone.utc)
CSV_PATH = "artifacts/amplifier.csv"
REPORT_PATH = "artifacts/amplifier_report.html"
# 归档服务的日志挂在应用日志树（``pa_auto_test``）下，才进得了 ``setup_logging()``
# 的文件；这里按契约名断言，不跟随实现里的 logger 变量。
ARCHIVAL_LOGGER = "pa_auto_test.application.archival_service"

CABLE_LOSSES = {"1.0": {"cable1": 0.5, "cable2": 0.5, "cable3": 0.0, "cable4": 0.0}}


def _fake_report_plots(result, output_dir):
    """轻量图表替身：归档接线测试不真实渲染 matplotlib。"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "amplifier_p_in_vs_p_out.png"
    path.write_bytes(b"fake-plot")
    return {"p_in_vs_p_out": path}, ()


class _Instrument:
    """同时满足线损、驱动映射和主功放服务的最小仪器替身。"""

    def __init__(self):
        self.measured = 0
        self.power = 0.0

    def setup_driver_amplifier_power(self):
        pass

    def power_on_driver(self):
        pass

    def setup_dut_power(self):
        pass

    def power_on_sequence(self):
        pass

    def power_off_sequence(self):
        pass

    def power_off_driver(self):
        pass

    def set_power(self, power):
        self.power = power

    def set_frequency(self, _frequency):
        pass

    def set_center_frequency(self, _frequency):
        pass

    def set_span(self, _span):
        pass

    def rf_output_on(self):
        pass

    def rf_output_off(self):
        pass

    def read_voltage(self, _name, _channel):
        return 28.0

    def read_current(self, _name, _channel):
        return 1.0

    def measure_power_with_average(self):
        self.measured += 1
        # 补偿后输出跟随源功率（增益 1 dB），服务按配置加回线损补偿。
        return self.power + 1.0 - 12.0

    def close_all(self, close_rf=False):
        return []


class _BrokenArchivalService:
    """归档入口按注入的异常失败，用于验证“归档失败不改变测量结果”。"""

    def __init__(self, error: BaseException):
        self.error = error

    def archive(self, request, *, retry_failed=False, now=None):
        raise self.error


def _amplifier_config() -> dict:
    return {
        "test_frequencies": [1.0],
        "attenuator": {"type": "10dB"},
        "signal_source": {"start_power": -20, "stop_power": -20, "step": 1},
        "compression_point": {"type": "1dB"},
        "driver_mode": {"enabled": False},
        "power_supply_assignment": {
            "dut_amplifier": {"supplies": {"PS1": {"name": "PS1", "channel": ["CH2"]}}}
        },
        "dut_config": {"max_input_power": 100},
    }


def _fully_valid_gui_config() -> dict:
    """组合入口使用的完整 GUI 配置（驱动模式关闭）。"""
    return author_official_configuration(
        {
            "schema_version": "1.1",
            "test_frequencies": [1.0],
            "signal_source": {"start_power": -20.0, "stop_power": -20.0, "step": 1.0},
            "compression_point": {"type": "1dB"},
            "driver_mode": {"enabled": False},
            "attenuator": {"type": "30dB"},
            "dut_config": {
                "max_input_power": 30.0,
                "power_supply_count": 1,
                "drain_channel": {"supply_id": "PS1", "channel": "CH2"},
            },
            "instruments": {
                "signal_generator": {"address": "sim::sg", "model": "SG", "enabled": True},
                "spectrum_analyzer": {"address": "sim::sa", "model": "SA", "enabled": True},
                "power_supplies": {
                    "PS1": {
                        "address": "sim::ps1",
                        "label": "PS1",
                        "model": "DP832A",
                        "full_code": "SIM1519",
                        "device_key": "rigol technologies:ps1",
                        "enabled": True,
                        "channels": {
                            "CH1": {
                                "voltage": {"value": 2.8},
                                "current": {"value": 0.1},
                            },
                            "CH2": {
                                "voltage": {"value": 28.0},
                                "current": {"value": 2.0},
                            },
                        },
                    }
                },
            },
            "power_supply_assignment": {
                "driver_amplifier": {
                    "power_supply_count": 0,
                    "supplies": {},
                    "power_plan": {"power_sequence": [], "inter_channel_delay_s": 0.0},
                },
                "dut_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {"carrier": {"supply_id": "PS1", "channel": ["CH1", "CH2"]}},
                    "power_plan": {
                        "power_sequence": [
                            {"supply_id": "PS1", "channel": "CH1"},
                            {"supply_id": "PS1", "channel": "CH2"},
                        ],
                        "inter_channel_delay_s": 2.0,
                    },
                },
            },
        }
    )


class SuccessPathArchivalTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.paths = PathService(self.base)
        self.repository = RunRepository(self.base / "runs")
        self.result_repository = FileMeasurementResultRepository(
            results_directory=self.base / "test_results"
        )
        self.stage_repository = FileStageResultRepository(paths=self.paths)
        self._plot_patch = patch.object(
            artifact_generator, "_default_report_plots", _fake_report_plots
        )
        self._plot_patch.start()
        self.addCleanup(self._plot_patch.stop)

    # -- 夹具 ---------------------------------------------------------------

    def _create_run(self):
        layout, _manifest = self.repository.create_run(
            {"run_id": RUN_ID}, run_id=RUN_ID, now=CREATED_AT
        )
        return layout

    def _archival_service(self) -> ResultArchivalService:
        return ResultArchivalService(
            runs=self.repository, generator=ArchiveArtifactGenerator(self.repository)
        )

    def _complete_cable_loss(self):
        """写入一份正式线损阶段结果并登记运行引用，供后续校准输入显式选择。"""
        result = CableLossMeasurementResult(
            run_id=RUN_ID,
            measurement_id=generate_measurement_id(),
            points=tuple(
                CableLossRow(frequency_ghz=frequency, cable_losses=values)
                for frequency, values in CABLE_LOSSES.items()
            ),
        )
        reference = self.stage_repository.save(
            MeasurementStage.CABLE_LOSS, result
        )
        recorder = RunStageRecorder(
            self.repository, RUN_ID, stage_results=self.stage_repository
        )
        recorder.start(STAGE_CABLE_LOSS, now=CREATED_AT)
        recorder.complete_with_stage_result(
            STAGE_CABLE_LOSS, reference, now=CREATED_AT
        )
        self._cable_loss_reference = reference
        return reference

    def _run_directory(self) -> Path:
        return self.repository.layout(RUN_ID).run_directory

    def _cable_loss_request(self, archival):
        layout = self._create_run()
        instrument = _Instrument()
        return CableLossMeasurementRequest(
            configuration={"test_frequencies": [1.0], "attenuator": {"type": "10dB"}},
            context=SimpleNamespace(run_id=RUN_ID),
            run_directory=layout.run_directory,
            measurement_port=instrument,
            safety_cleanup=safety_cleanup_for(instrument),
            result_repository=self.result_repository,
            stage_result_repository=self.stage_repository,
            stage_recorder=RunStageRecorder(
                self.repository, RUN_ID, stage_results=self.stage_repository
            ),
            archival_service=archival,
            safety_options={"owns_measurement_port": True},
        )

    def _driver_mapping_request(self, archival):
        layout = self.repository.layout(RUN_ID)
        instrument = _Instrument()
        return DriverPowerMappingRequest(
            configuration={
                "test_frequencies": [1.0],
                "attenuator": {"type": "10dB"},
                "signal_source": {
                    "power_scan_plans": [
                        {"start_power": -20, "step": 1, "stop_power": -20}
                    ]
                },
                "max_dut_input_power": 100.0,
                "driver_gain_estimate_db": 1.0,
                "driver_max_input_power": 20.0,
            },
            context=SimpleNamespace(run_id=RUN_ID),
            run_directory=layout.run_directory,
            measurement_port=instrument,
            safety_cleanup=safety_cleanup_for(instrument),
            result_repository=self.result_repository,
            stage_result_repository=self.stage_repository,
            input_reader=MappingInputReader(
                cable_loss={"cable_losses": CABLE_LOSSES}
            ),
            cable_loss_path=self._cable_loss_reference.path,
            stage_recorder=RunStageRecorder(
                self.repository, RUN_ID, stage_results=self.stage_repository
            ),
            archival_service=archival,
            safety_options={"owns_measurement_port": True},
        )

    def _amplifier_request(self, archival):
        """旧兼容链路的请求：不注入阶段结果仓库，正式结果仍写在运行目录。"""
        layout = self.repository.layout(RUN_ID)
        instrument = _Instrument()
        return AmplifierMeasurementRequest(
            configuration=_amplifier_config(),
            context=SimpleNamespace(run_id=RUN_ID),
            run_directory=layout.run_directory,
            measurement_port=instrument,
            inputs=amplifier_inputs(),
            safety_cleanup=safety_cleanup_for(instrument),
            result_repository=self.result_repository,
            input_reader=MappingInputReader(
                cable_loss={"cable_losses": CABLE_LOSSES}
            ),
            # 阶段结果仓库缺席是“旧兼容写入”的显式信号：结果与产物归档都留在运行目录。
            calibration_inputs=AmplifierCalibrationInputs(
                loss_data={"cable_losses": CABLE_LOSSES}
            ),
            stage_recorder=RunStageRecorder(self.repository, RUN_ID),
            archival_service=archival,
            safety_options={"owns_measurement_port": True},
        )

    def _csv_file(self) -> Path:
        return self._run_directory() / CSV_PATH

    def _report_file(self) -> Path:
        return self._run_directory() / REPORT_PATH

    # -- 主功放：CSV 与报告自动生成 -------------------------------------------

    def test_amplifier_success_path_generates_the_csv_artifact(self):
        self._create_run()
        self._complete_cable_loss()
        use_case = AmplifierMeasurementUseCase(
            self._amplifier_request(self._archival_service()), sleep_fn=lambda _: None
        )

        result = use_case.measure_all_frequencies()

        outcome = use_case.last_archival_outcome
        self.assertIsNotNone(outcome)
        self.assertIs(outcome.test_status, ArchivalTestStatus.COMPLETED)
        self.assertIs(outcome.csv.status, ArtifactStatus.COMPLETED)
        self.assertEqual(outcome.csv.path, CSV_PATH)
        self.assertTrue(self._csv_file().is_file())
        csv_text = self._csv_file().read_text(encoding="utf-8-sig")
        self.assertIn(RUN_ID, csv_text)
        self.assertIn("amplifier", csv_text)
        # 完整成功同时产出测试报告（计划步骤 5）：所有适用产物完成后归档才算完成。
        self.assertIs(outcome.report.status, ArtifactStatus.COMPLETED)
        self.assertEqual(outcome.report.path, REPORT_PATH)
        self.assertTrue(self._report_file().is_file())
        self.assertIs(outcome.archive_status, ArchivalStatus.COMPLETED)

        manifest = self.repository.load_manifest(RUN_ID)
        record = manifest.find_artifact(
            ArtifactKind.CSV, source_result=AMPLIFIER_RESULT_PATH
        )
        self.assertIs(record.status, ArtifactStatus.COMPLETED)
        self.assertEqual(record.path, CSV_PATH)
        self.assertIsNotNone(record.sha256)
        report_record = manifest.find_artifact(
            ArtifactKind.REPORT, source_result=AMPLIFIER_RESULT_PATH
        )
        self.assertIs(report_record.status, ArtifactStatus.COMPLETED)
        self.assertEqual(report_record.path, REPORT_PATH)
        self.assertIsNotNone(report_record.sha256)
        self.repository.verify_artifact_files(RUN_ID)
        # 正式结果、阶段与运行状态不受归档影响。
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.COMPLETED)
        self.assertEqual(manifest.status, RunStatus.COMPLETED)
        self.assertEqual(
            result.result_reference["archive_path"],
            str(self._run_directory() / AMPLIFIER_RESULT_PATH),
        )

    def test_amplifier_success_path_without_archival_service_generates_nothing(self):
        self._create_run()
        self._complete_cable_loss()
        use_case = AmplifierMeasurementUseCase(
            self._amplifier_request(None), sleep_fn=lambda _: None
        )

        use_case.measure_all_frequencies()

        self.assertIsNone(use_case.last_archival_outcome)
        self.assertFalse(self._csv_file().exists())
        self.assertFalse(self._report_file().exists())
        self.assertEqual(self.repository.load_manifest(RUN_ID).artifacts, ())

    def test_csv_write_failure_does_not_change_the_measurement_status(self):
        self._create_run()
        self._complete_cable_loss()
        # 目标路径被同名目录占住：CSV 一定写不进去。
        self._csv_file().mkdir(parents=True)
        use_case = AmplifierMeasurementUseCase(
            self._amplifier_request(self._archival_service()), sleep_fn=lambda _: None
        )

        use_case.measure_all_frequencies()

        outcome = use_case.last_archival_outcome
        self.assertIs(outcome.csv.status, ArtifactStatus.FAILED)
        self.assertIn("CSV 写入失败", outcome.csv.reason)
        # 单个产物失败不影响另一份产物：报告照常生成，归档维度仍是 partial。
        self.assertIs(outcome.report.status, ArtifactStatus.COMPLETED)
        self.assertIs(outcome.archive_status, ArchivalStatus.PARTIAL)
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.COMPLETED)
        self.assertEqual(manifest.status, RunStatus.COMPLETED)
        self.assertTrue((self._run_directory() / AMPLIFIER_RESULT_PATH).is_file())
        record = manifest.find_artifact(
            ArtifactKind.CSV, source_result=AMPLIFIER_RESULT_PATH
        )
        self.assertIs(record.status, ArtifactStatus.FAILED)
        self.assertIsNone(record.path)

    # -- 线损 / 驱动映射：不适用产物 -----------------------------------------

    def test_cable_loss_success_path_registers_not_applicable_artifacts(self):
        use_case = CableLossUseCase(
            self._cable_loss_request(self._archival_service()), sleep_fn=lambda _: None
        )

        use_case.measure_all_frequencies()
        use_case.continue_to_step2()

        outcome = use_case.last_archival_outcome
        self.assertIs(outcome.test_status, ArchivalTestStatus.PARTIAL)
        self.assertEqual(outcome.formal_result_path, CABLE_LOSS_INPUT_PATH)
        self.assertIs(outcome.csv.status, ArtifactStatus.NOT_APPLICABLE)
        self.assertIs(outcome.report.status, ArtifactStatus.NOT_APPLICABLE)

        manifest = self.repository.load_manifest(RUN_ID)
        records = {
            record.kind: record
            for record in manifest.artifacts
            if record.source_result == CABLE_LOSS_INPUT_PATH
        }
        self.assertEqual(
            {kind: record.status for kind, record in records.items()},
            {
                ArtifactKind.CSV: ArtifactStatus.NOT_APPLICABLE,
                ArtifactKind.REPORT: ArtifactStatus.NOT_APPLICABLE,
            },
        )
        # 线损阶段完成但运行仍在进行：不被伪装成完整成功。
        self.assertEqual(manifest.status, RunStatus.RUNNING)

    def test_driver_mapping_success_path_registers_not_applicable_artifacts(self):
        self._create_run()
        self._complete_cable_loss()
        use_case = DriverPowerMappingUseCase(
            self._driver_mapping_request(self._archival_service()),
            sleep_fn=lambda _: None,
        )

        use_case.measure_all_frequencies()

        outcome = use_case.last_archival_outcome
        self.assertIs(outcome.csv.status, ArtifactStatus.NOT_APPLICABLE)
        self.assertIs(outcome.report.status, ArtifactStatus.NOT_APPLICABLE)
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(
            {
                record.status
                for record in manifest.artifacts
                if record.source_result == DRIVER_MAPPING_INPUT_PATH
            },
            {ArtifactStatus.NOT_APPLICABLE},
        )
        self.assertEqual(manifest.status, RunStatus.RUNNING)

    # -- 归档失败不影响测量 --------------------------------------------------

    def test_expected_archival_failure_only_logs_and_keeps_the_result(self):
        self._create_run()
        self._complete_cable_loss()
        use_case = AmplifierMeasurementUseCase(
            self._amplifier_request(_BrokenArchivalService(ArchivalRequestError("归档请求无效"))),
            sleep_fn=lambda _: None,
        )

        with self.assertLogs(ARCHIVAL_LOGGER, level="ERROR") as logs:
            result = use_case.measure_all_frequencies()

        self.assertIsNone(use_case.last_archival_outcome)
        self.assertTrue(any("自动归档失败" in line for line in logs.output))
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.COMPLETED)
        self.assertEqual(manifest.status, RunStatus.COMPLETED)
        self.assertTrue((self._run_directory() / AMPLIFIER_RESULT_PATH).is_file())
        self.assertEqual(
            result.result_reference["archive_path"],
            str(self._run_directory() / AMPLIFIER_RESULT_PATH),
        )

    def test_manifest_write_failure_during_archival_is_not_a_measurement_failure(self):
        self._create_run()
        self._complete_cable_loss()
        use_case = AmplifierMeasurementUseCase(
            self._amplifier_request(_BrokenArchivalService(OSError("manifest 不可写"))),
            sleep_fn=lambda _: None,
        )

        with self.assertLogs(ARCHIVAL_LOGGER, level="ERROR"):
            use_case.measure_all_frequencies()

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.COMPLETED)
        self.assertEqual(manifest.status, RunStatus.COMPLETED)

    def test_unexpected_archival_defect_propagates_after_the_result_is_saved(self):
        self._create_run()
        self._complete_cable_loss()
        use_case = AmplifierMeasurementUseCase(
            self._amplifier_request(_BrokenArchivalService(TypeError("端口实现缺陷"))),
            sleep_fn=lambda _: None,
        )

        with self.assertRaisesRegex(TypeError, "端口实现缺陷"):
            use_case.measure_all_frequencies()

        # 正式结果已提交，阶段与运行状态不受归档缺陷影响（缺陷本身必须暴露）。
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.COMPLETED)
        self.assertEqual(manifest.status, RunStatus.COMPLETED)
        self.assertTrue((self._run_directory() / AMPLIFIER_RESULT_PATH).is_file())

    def test_damaged_completed_artifact_only_logs_on_the_next_boundary(self):
        """已完成 CSV 被删除后再次触发归档：只写错误日志，测量结果与状态不变。"""
        self._create_run()
        self._complete_cable_loss()
        service = self._archival_service()
        AmplifierMeasurementUseCase(
            self._amplifier_request(service), sleep_fn=lambda _: None
        ).measure_all_frequencies()
        self._csv_file().unlink()

        with self.assertLogs(ARCHIVAL_LOGGER, level="ERROR") as logs:
            outcome = archive_completed_stage(
                service, run_id=RUN_ID, stage=STAGE_AMPLIFIER
            )

        self.assertIsNone(outcome)
        self.assertTrue(any("拒绝复用" in line for line in logs.output))
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.COMPLETED)
        self.assertEqual(manifest.status, RunStatus.COMPLETED)
        self.assertTrue((self._run_directory() / AMPLIFIER_RESULT_PATH).is_file())


class CompositionWiringTests(unittest.TestCase):
    """组合根必须为运行契约运行装配归档服务，旧兼容快照不装配。"""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.paths = PathService(self.base)
        self.repository = RunRepository(self.base / "runs")
        self.result_repository = FileMeasurementResultRepository(
            results_directory=self.base / "test_results"
        )
        self.config_path = self.base / "config.json"
        self.config_path.write_text(
            json.dumps(_fully_valid_gui_config()), encoding="utf-8"
        )

    def test_composition_generates_csv_after_a_successful_amplifier_run(self):
        from app.gui_runtime import (
            create_amplifier_measurement,
            create_cable_loss_measurement,
            prepare_configuration,
        )

        prepared = prepare_configuration(
            str(self.config_path), run_id=RUN_ID, run_repository=self.repository
        )
        cable_case = create_cable_loss_measurement(
            prepared_run=prepared,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
            paths=self.paths,
            sleep_fn=lambda _: None,
        )
        cable_case.measure_all_frequencies()
        cable_case.continue_to_step2()

        pointer = json.loads(
            (Path(prepared.run_directory) / CABLE_LOSS_INPUT_PATH).read_text(
                encoding="utf-8"
            )
        )
        amplifier_case = create_amplifier_measurement(
            prepared_run=prepared,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
            paths=self.paths,
            cable_loss_path=Path(pointer["path"]),
            sleep_fn=lambda _: None,
        )
        self.assertIsInstance(amplifier_case.archival_service, ResultArchivalService)
        result = amplifier_case.measure_all_frequencies()

        # 步骤 20：正式结果与交付产物在同一阶段目录，运行目录只保留引用。
        stage_path = Path(result.result_reference["archive_path"])
        self.assertTrue(stage_path.is_file())
        self.assertEqual(stage_path.parent.name, MeasurementStage.AMPLIFIER.value)
        csv_path = stage_path.with_suffix(".csv")
        report_path = stage_path.with_name(f"{stage_path.stem}_report.html")
        self.assertTrue(csv_path.is_file())
        self.assertTrue(report_path.is_file())

        run_directory = Path(prepared.run_directory)
        pointer = json.loads(
            (run_directory / AMPLIFIER_RESULT_PATH).read_text(encoding="utf-8")
        )
        self.assertEqual(pointer["schema"], "pa_auto_test.stage_result_reference")
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(
            tuple(
                record
                for record in manifest.artifacts
                if record.source_result == AMPLIFIER_RESULT_PATH
            ),
            (),
        )
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.COMPLETED)

    def test_composition_report_locator_finds_the_stage_report_without_run_page(self):
        """步骤 20：数据可视化按阶段结果直接打开同目录报告，不依赖历史运行页。"""
        from app.gui_runtime import (
            create_amplifier_measurement,
            create_cable_loss_measurement,
            prepare_configuration,
        )
        from application.run_history_service import RunHistoryService
        from application.dto.run_history import ResultReportState
        from infrastructure.gui_data_service import ResultQueryAdapter
        from infrastructure.persistence.run_history_repository import (
            FileRunHistoryRepository,
        )
        from infrastructure.persistence.stage_artifact_repository import (
            FileStageArtifactRepository,
        )
        from infrastructure.persistence.stage_result_repository import (
            FileStageResultRepository,
        )

        prepared = prepare_configuration(
            str(self.config_path), run_id=RUN_ID, run_repository=self.repository
        )
        cable_case = create_cable_loss_measurement(
            prepared_run=prepared,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
            paths=self.paths,
            sleep_fn=lambda _: None,
        )
        cable_case.measure_all_frequencies()
        cable_case.continue_to_step2()
        pointer = json.loads(
            (Path(prepared.run_directory) / CABLE_LOSS_INPUT_PATH).read_text(
                encoding="utf-8"
            )
        )
        create_amplifier_measurement(
            prepared_run=prepared,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
            paths=self.paths,
            cable_loss_path=Path(pointer["path"]),
            sleep_fn=lambda _: None,
        ).measure_all_frequencies()

        history = FileRunHistoryRepository(runs=self.repository)
        query = ResultQueryAdapter(
            self.paths,
            run_history=history,
            stage_results=FileStageResultRepository(paths=self.paths),
            # 与生产装配一致：产物定位由阶段产物仓库负责目录边界与身份派生。
            stage_artifacts=FileStageArtifactRepository(paths=self.paths),
        )
        [row] = [
            item
            for item in query.list_results()
            if item.result_id.startswith("stage::amplifier::")
        ]
        service = RunHistoryService(
            statuses=history, locations=history, results=query
        )

        report = service.report_for_result(row.result_id)

        self.assertIs(report.state, ResultReportState.AVAILABLE)
        self.assertTrue(Path(report.location).is_file())
        self.assertEqual(
            Path(report.location).parent, self.paths.amplifier_results_dir
        )

    def test_artifact_generation_failure_keeps_the_formal_result(self):
        """步骤 20：CSV/报告是可选派生物，生成失败不改变正式 JSON、阶段或运行状态。"""
        from app.gui_runtime import (
            create_amplifier_measurement,
            create_cable_loss_measurement,
            prepare_configuration,
        )

        class _BrokenArtifacts:
            def save(self, *args, **kwargs):
                raise OSError("磁盘已满")

        prepared = prepare_configuration(
            str(self.config_path), run_id=RUN_ID, run_repository=self.repository
        )
        cable_case = create_cable_loss_measurement(
            prepared_run=prepared,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
            paths=self.paths,
            sleep_fn=lambda _: None,
        )
        cable_case.measure_all_frequencies()
        cable_case.continue_to_step2()
        pointer = json.loads(
            (Path(prepared.run_directory) / CABLE_LOSS_INPUT_PATH).read_text(
                encoding="utf-8"
            )
        )
        amplifier_case = create_amplifier_measurement(
            prepared_run=prepared,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
            paths=self.paths,
            cable_loss_path=Path(pointer["path"]),
            stage_artifact_repository=_BrokenArtifacts(),
            sleep_fn=lambda _: None,
        )

        with self.assertLogs(
            "application.measurements.amplifier_test", level="ERROR"
        ):
            result = amplifier_case.measure_all_frequencies()

        stage_path = Path(result.result_reference["archive_path"])
        self.assertTrue(stage_path.is_file())
        self.assertEqual(amplifier_case.last_stage_artifact_references, ())
        self.assertFalse(stage_path.with_suffix(".csv").exists())
        self.assertFalse(
            stage_path.with_name(f"{stage_path.stem}_report.html").exists()
        )
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.COMPLETED)
        self.assertEqual(manifest.status, RunStatus.COMPLETED)

    def test_run_without_contract_is_rejected_before_assembly(self):
        """没有运行契约就没有运行目录：装配入口必须明确失败，而不是降级。"""
        from app.gui_runtime import create_cable_loss_measurement
        from application.run_service import RunNotResumable

        prepared = SimpleNamespace(
            configuration={"test_frequencies": [1.0], "attenuator": {"type": "10dB"}},
            context=SimpleNamespace(run_id="run-compat-snapshot"),
            run_directory=self.base,
            hardware_config_path=self.base / "inputs" / "hardware_config.json",
        )
        with self.assertRaises(RunNotResumable):
            create_cable_loss_measurement(
                prepared_run=prepared,
                measurement_port=_Instrument(),
                result_repository=self.result_repository,
            )


if __name__ == "__main__":
    unittest.main()
