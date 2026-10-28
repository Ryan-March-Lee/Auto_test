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

from application.archival_service import (
    ArchivalRequestError,
    ResultArchivalService,
    archive_completed_stage,
)
from application.dto import (
    AmplifierMeasurementRequest,
    CableLossMeasurementRequest,
    DriverPowerMappingRequest,
)
from application.dto.archival import ArchivalStatus, ArchivalTestStatus
from application.inputs import ResultInputReader
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
from infrastructure.persistence.artifact_generator import ArchiveArtifactGenerator
from infrastructure.persistence.result_repository import FileMeasurementResultRepository
from infrastructure.persistence.run_repository import RunRepository
from presentation.qt.configuration_authoring import author_official_configuration
from tests.fakes import safety_cleanup_for


RUN_ID = "20261022T153000Z_a1b2c3d4"
CREATED_AT = datetime(2026, 10, 22, 15, 30, tzinfo=timezone.utc)
CSV_PATH = "artifacts/amplifier.csv"
REPORT_PATH = "artifacts/amplifier_report.html"
# 归档服务的日志挂在应用日志树（``pa_auto_test``）下，才进得了 ``setup_logging()``
# 的文件；这里按契约名断言，不跟随实现里的 logger 变量。
ARCHIVAL_LOGGER = "pa_auto_test.application.archival_service"

CABLE_LOSSES = {"1.0": {"cable1": 0.5, "cable2": 0.5, "cable3": 0.0, "cable4": 0.0}}


class _Instrument:
    """同时满足线损、驱动映射和主功放服务的最小仪器替身。"""

    def __init__(self):
        self.measured = 0

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

    def set_power(self, _power):
        pass

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
        return -10.0

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
            "test_frequencies": [1.0],
            "signal_source": {"start_power": -20.0, "stop_power": -20.0, "step": 1.0},
            "compression_point": {"type": "1dB"},
            "driver_mode": {"enabled": False},
            "attenuator": {"type": "30dB"},
            "dut_config": {"max_input_power": 30.0, "power_supply_count": 1},
            "instruments": {
                "signal_generator": {"address": "sim::sg", "model": "SG", "enabled": True},
                "spectrum_analyzer": {"address": "sim::sa", "model": "SA", "enabled": True},
                "power_supplies": {
                    "PS1": {
                        "address": "sim::ps1",
                        "enabled": True,
                        "channels": {
                            "CH1": {"voltage": {"value": 2.8}, "current": {"value": 0.1}},
                            "CH2": {"voltage": {"value": 28.0}, "current": {"value": 2.0}},
                        },
                    }
                },
            },
            "power_supply_assignment": {
                "driver_amplifier": {"power_supply_count": 0, "supplies": {}},
                "dut_amplifier": {
                    "power_supply_count": 1,
                    "supplies": {"carrier": {"name": "PS1", "channel": ["CH1", "CH2"]}},
                },
            },
            "wiring": {"confirmed": True, "connection_note": "现场接线已确认"},
        }
    )


class SuccessPathArchivalTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.repository = RunRepository(self.base / "runs")
        self.result_repository = FileMeasurementResultRepository(
            results_directory=self.base / "test_results"
        )

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
        recorder = RunStageRecorder(self.repository, RUN_ID)
        recorder.start(STAGE_CABLE_LOSS, now=CREATED_AT)
        recorder.complete(
            STAGE_CABLE_LOSS,
            {"run_id": RUN_ID, "cable_losses": CABLE_LOSSES},
            now=CREATED_AT,
        )

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
            stage_recorder=RunStageRecorder(self.repository, RUN_ID),
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
                "signal_source": {"start_power": -20, "stop_power": -20, "step": 1},
            },
            context=SimpleNamespace(run_id=RUN_ID),
            run_directory=layout.run_directory,
            measurement_port=instrument,
            safety_cleanup=safety_cleanup_for(instrument),
            result_repository=self.result_repository,
            input_reader=ResultInputReader(self.result_repository),
            stage_recorder=RunStageRecorder(self.repository, RUN_ID),
            archival_service=archival,
            safety_options={"owns_measurement_port": True},
        )

    def _amplifier_request(self, archival):
        layout = self.repository.layout(RUN_ID)
        instrument = _Instrument()
        return AmplifierMeasurementRequest(
            configuration=_amplifier_config(),
            context=SimpleNamespace(run_id=RUN_ID),
            run_directory=layout.run_directory,
            measurement_port=instrument,
            safety_cleanup=safety_cleanup_for(instrument),
            result_repository=self.result_repository,
            input_reader=ResultInputReader(self.result_repository),
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
            str(self.config_path),
            prepared_run=prepared,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
        )
        cable_case.measure_all_frequencies()
        cable_case.continue_to_step2()

        amplifier_case = create_amplifier_measurement(
            str(self.config_path),
            prepared_run=prepared,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
        )
        self.assertIsInstance(amplifier_case.archival_service, ResultArchivalService)
        amplifier_case.measure_all_frequencies()

        run_directory = Path(prepared.run_directory)
        self.assertTrue((run_directory / CSV_PATH).is_file())
        self.assertTrue((run_directory / REPORT_PATH).is_file())
        manifest = self.repository.load_manifest(RUN_ID)
        for kind, path in ((ArtifactKind.CSV, CSV_PATH), (ArtifactKind.REPORT, REPORT_PATH)):
            record = manifest.find_artifact(kind, source_result=AMPLIFIER_RESULT_PATH)
            self.assertIs(record.status, ArtifactStatus.COMPLETED)
            self.assertEqual(record.path, path)
        self.repository.verify_artifact_files(RUN_ID)

    def test_legacy_composition_does_not_archive(self):
        from app.gui_runtime import create_cable_loss_measurement

        prepared = SimpleNamespace(
            configuration={"test_frequencies": [1.0], "attenuator": {"type": "10dB"}},
            context=SimpleNamespace(run_id="run-compat-snapshot"),
            run_directory=self.base,
            hardware_config_path=self.base / "inputs" / "hardware_config.json",
        )
        instrument = _Instrument()
        case = create_cable_loss_measurement(
            str(self.config_path),
            prepared_run=prepared,
            measurement_port=instrument,
            result_repository=self.result_repository,
        )

        self.assertIsNone(case.archival_service)


if __name__ == "__main__":
    unittest.main()
