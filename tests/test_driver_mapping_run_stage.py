"""步骤 8 测试：驱动映射结果写入独立阶段目录。

覆盖验收标准：

- 省略显式来源时，驱动映射只从当前运行目录读取已完成的
  ``inputs/cable_loss.json``，当前运行缺失线损结果时在硬件动作前失败；
- 另一个运行目录或全局兼容副本的线损数据不会被自动使用；用户显式选择其它运行的
  阶段结果（含选择时摘要）后才会消费它（步骤 14）。
- 完成的映射结果写入 ``test_results/driver_mapping/<measurement_id>.json``；
  运行目录只保存引用文档并登记 manifest，结果脱离原运行目录仍可读取。
- 旧版单层 ``power_mapping`` 仍由正式结果读取器按既有规则拒绝。
"""

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.cancellation import CancellationToken, MeasurementCancelled
from app.gui_runtime import (
    create_cable_loss_measurement,
    create_driver_mapping_measurement,
    prepare_configuration,
)
from application.dto import (
    CableLossMeasurementResult,
    CableLossRow,
    DriverPowerMappingRequest,
    generate_measurement_id,
    measurement_result_from_document,
    validate_generated_measurement_id,
)
from application.dto.measurement_requests import StageResultDigest
from application.inputs import ResultInputReader
from application.measurements import DriverPowerMappingUseCase
from application.ports.stage_result_repository import MeasurementStage
from application.run_stages import (
    STAGE_RESULT_REFERENCE_SCHEMA,
    RunStageRecorder,
    StageInputUnavailable,
    StageResultNotRecorded,
)
from domain.run.models import (
    CABLE_LOSS_INPUT_PATH,
    DRIVER_MAPPING_INPUT_PATH,
    HARDWARE_CONFIG_INPUT_PATH,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
    RunStatus,
    StageStatus,
)
from infrastructure.persistence.result_repository import FileMeasurementResultRepository
from infrastructure.persistence.run_repository import RunRepository
from infrastructure.persistence.stage_result_repository import FileStageResultRepository
from infrastructure.filesystem.paths import PathService
from application.configuration_authoring import author_official_configuration
from tests.fakes import safety_cleanup_for


RUN_ID = "20261022T153000Z_a1b2c3d4"
OTHER_RUN_ID = "20261022T160000Z_b2c3d4e5"
CREATED_AT = datetime(2026, 10, 22, 15, 30, tzinfo=timezone.utc)

CABLE_LOSSES = {"1.0": {"cable2": 2.0}}

# 步骤 17：新测量链路只消费显式选择的阶段结果或组合入口预检对象。运行阶段用例用
# 预检对象代替真实文件读取，阶段目录边界另由显式输入用例覆盖。
_UNSET = object()


def _fully_valid_gui_config(*, power_scan_plans=None) -> dict:
    """完整有效的 GUI 组合配置（驱动模式开启，DUT 通道已确认接线）。

    ``power_scan_plans`` 传入 ``[{"start_power", "step", "stop_power"}, ...]`` 时
    使用多段 1.2 计划；省略时沿用单段 1.1 标量（保存时由 authoring 包装成一段）。
    """
    signal_source = (
        {"power_scan_plans": power_scan_plans}
        if power_scan_plans is not None
        else {"start_power": -10.0, "stop_power": 0.0, "step": 1.0}
    )
    return author_official_configuration({
        "schema_version": "1.1",
        "test_frequencies": [2.4],
        "signal_source": signal_source,
        "compression_point": {"type": "5dB"},
        "driver_mode": {
            "enabled": True,
            "estimated_gain_db": 1.0,
            "max_input_power": 20.0,
        },
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
    })


class _Instrument:
    """同时满足线损和驱动映射服务的最小仪器替身。"""

    def __init__(self, *, fail=False):
        self.measured = 0
        self.fail = fail
        self.power = 0.0
        self.frequency = None
        self.rf_off_calls = 0
        self.driver_off_calls = 0

    def setup_driver_amplifier_power(self): pass
    def power_on_driver(self): pass
    def set_power(self, power): self.power = power
    def set_frequency(self, frequency): self.frequency = frequency
    def rf_output_on(self): pass
    def set_center_frequency(self, _frequency): pass
    def set_span(self, _span): pass
    def rf_output_off(self): self.rf_off_calls += 1
    def power_off_driver(self): self.driver_off_calls += 1

    def measure_power_with_average(self):
        if self.fail:
            raise RuntimeError("measurement failed")
        self.measured += 1
        # 补偿后输出 = 源功率 + 1 dB；线损补偿由服务按当前配置加回。
        return self.power + 1.0 - 12.0

    def close_all(self, close_rf=False):
        return []


class _Context:
    def __init__(self, run_id):
        self.run_id = run_id


class _UnwritableResultRepository:
    """正式运行只写运行目录结果；该对象不提供任何结果写入能力。"""


class _FailingStageResultRepository:
    """把正式结果写入阶段目录时失败；不产生任何阶段文件。"""

    def __init__(self, error):
        self.error = error

    def save(self, _stage, _result):
        raise self.error


class _FailingCommitRepository:
    """把驱动映射阶段提交为 completed 时的 manifest 写入注入失败。"""

    def __init__(self, repository):
        self.repository = repository

    def __getattr__(self, name):
        return getattr(self.repository, name)

    def update_manifest(self, run_id, manifest, *, now=None):
        if manifest.stage(STAGE_DRIVER_MAPPING).status is StageStatus.COMPLETED:
            raise OSError("manifest commit failed")
        return self.repository.update_manifest(run_id, manifest, now=now)


class DriverMappingRunStageTests(unittest.TestCase):
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

    def _create_run(self, run_id=RUN_ID):
        layout, _manifest = self.repository.create_run(
            {"run_id": run_id}, run_id=run_id, now=CREATED_AT
        )
        return layout

    def _recorder(self, run_id=RUN_ID, *, repository=None):
        return RunStageRecorder(
            repository or self.repository,
            run_id,
            stage_results=self.stage_repository,
        )

    def _complete_cable_loss(self, run_id=RUN_ID, *, cable_losses=CABLE_LOSSES):
        """在指定运行目录写入并登记已完成的线损输入。"""
        recorder = self._recorder(run_id)
        recorder.start(STAGE_CABLE_LOSS, now=CREATED_AT)
        recorder.complete(
            STAGE_CABLE_LOSS,
            {"run_id": run_id, "cable_losses": cable_losses},
            now=CREATED_AT,
        )
        if run_id == RUN_ID:
            self._seeded_loss_data = {"cable_losses": cable_losses}
        return recorder

    def _saved_stage_cable_loss(self, *, run_id=RUN_ID, cable_losses=CABLE_LOSSES):
        """把一份正式线损结果写入阶段目录（不登记运行引用）。"""
        result = CableLossMeasurementResult(
            run_id=run_id,
            measurement_id=generate_measurement_id(),
            points=tuple(
                CableLossRow(frequency_ghz=frequency, cable_losses=values)
                for frequency, values in cable_losses.items()
            ),
        )
        return self.stage_repository.save(MeasurementStage.CABLE_LOSS, result)

    def _stage_result_files(self):
        directory = self.paths.driver_mapping_results_dir
        if not directory.is_dir():
            return []
        return sorted(path.name for path in directory.iterdir())

    def _stage_result_path(self, measurement_id):
        return self.paths.driver_mapping_results_dir / f"{measurement_id}.json"

    def _read_mapping_stage_document(self, run_directory):
        """跟随运行目录的引用文档读取阶段目录中的权威映射结果。"""
        pointer = json.loads(
            (run_directory / DRIVER_MAPPING_INPUT_PATH).read_text(encoding="utf-8")
        )
        self.assertEqual(pointer["schema"], STAGE_RESULT_REFERENCE_SCHEMA)
        self.assertNotIn("target_mapping", pointer)
        return json.loads(Path(pointer["path"]).read_text(encoding="utf-8"))

    def _request(
        self,
        run_id=RUN_ID,
        *,
        instrument=None,
        token=None,
        repository=None,
        recorder=None,
        stage_repository=None,
        cable_loss_path=None,
        cable_loss_digest=None,
        loss_data=_UNSET,
    ):
        layout = self.repository.layout(run_id)
        instrument = instrument or _Instrument()
        if loss_data is _UNSET and cable_loss_path is None:
            loss_data = getattr(self, "_seeded_loss_data", None)
        elif loss_data is _UNSET:
            loss_data = None
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
            context=_Context(run_id),
            run_directory=layout.run_directory,
            measurement_port=instrument,
            safety_cleanup=safety_cleanup_for(instrument),
            result_repository=repository or self.result_repository,
            input_reader=ResultInputReader(self.result_repository),
            stage_result_repository=stage_repository or self.stage_repository,
            stage_recorder=recorder or self._recorder(run_id),
            cancellation_token=token,
            cable_loss_path=cable_loss_path,
            cable_loss_digest=cable_loss_digest,
            loss_data=loss_data,
            safety_options={"owns_measurement_port": True},
        )

    def _contract_prepared(self, layout, *, run_id=RUN_ID):
        """运行契约替身：冻结快照解析为运行目录内的固定路径。

        组合入口测试只验证“在连接仪器前失败”，因此替身直接给出运行目录内的硬件
        快照路径，不触发 manifest 校验（那部分由 test_run_service 与门禁覆盖）。
        """
        return SimpleNamespace(
            configuration=object(),
            context=SimpleNamespace(run_id=run_id),
            run_id=run_id,
            run_directory=layout.run_directory,
            run_repository=self.repository,
            hardware_config_path=layout.run_directory / HARDWARE_CONFIG_INPUT_PATH,
            hardware_connection_path=(
                lambda self=None: layout.run_directory / HARDWARE_CONFIG_INPUT_PATH
            ),
        )

    # -- 结果归属独立阶段目录 ----------------------------------------------

    def test_completed_mapping_belongs_to_the_stage_directory(self):
        layout = self._create_run()
        self._complete_cable_loss()
        use_case = DriverPowerMappingUseCase(self._request(), sleep_fn=lambda _: None)

        result = use_case.measure_all_frequencies()

        identity = validate_generated_measurement_id(result.measurement_id)
        stage_path = self._stage_result_path(identity)
        self.assertTrue(stage_path.is_file())
        document = json.loads(stage_path.read_text(encoding="utf-8"))
        self.assertEqual(document["run_id"], RUN_ID)
        self.assertEqual(document["measurement_id"], identity)
        self.assertIn("raw_points", document)
        self.assertIn("target_mapping", document)
        self.assertEqual(document["schema_version"], "2.0")
        # 双层契约都保留：目标网格是正式输入，原始点是审计依据。
        self.assertEqual(
            document["target_mapping"]["1.0"],
            [
                {
                    "target_input_power_dbm": -20.0,
                    "source_power_dbm": -21.0,
                    "driver_output_power_dbm": -20.0,
                }
            ],
        )
        parsed = measurement_result_from_document(document)
        self.assertEqual(parsed.measurement_id, identity)
        self.assertIs(
            parsed.measurement_type,
            MeasurementStage.DRIVER_MAPPING.measurement_type,
        )

        # 运行目录只有引用文档：路径、身份与摘要指向阶段目录文件，不含结果正文。
        reference_path = layout.run_directory / DRIVER_MAPPING_INPUT_PATH
        self.assertTrue(reference_path.is_file())
        pointer = json.loads(reference_path.read_text(encoding="utf-8"))
        self.assertEqual(pointer["schema"], STAGE_RESULT_REFERENCE_SCHEMA)
        self.assertEqual(pointer["stage"], MeasurementStage.DRIVER_MAPPING.value)
        self.assertEqual(pointer["measurement_id"], identity)
        self.assertEqual(Path(pointer["path"]), stage_path)
        self.assertNotIn("raw_points", pointer)
        self.assertNotIn("target_mapping", pointer)

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.status, RunStatus.RUNNING)
        stage = manifest.stage(STAGE_DRIVER_MAPPING)
        self.assertEqual(stage.status, StageStatus.COMPLETED)
        self.assertEqual(stage.result, DRIVER_MAPPING_INPUT_PATH)

        registered = {item.path: item for item in manifest.inputs}
        self.assertIn(DRIVER_MAPPING_INPUT_PATH, registered)
        self.assertIsNotNone(registered[DRIVER_MAPPING_INPUT_PATH].sha256)
        self.assertEqual(
            registered[DRIVER_MAPPING_INPUT_PATH].size_bytes,
            reference_path.stat().st_size,
        )
        self.assertEqual(
            result.result_reference["archive_path"], str(stage_path)
        )
        # 线损阶段不受驱动映射结果写入影响。
        self.assertEqual(manifest.stage(STAGE_CABLE_LOSS).status, StageStatus.COMPLETED)

    def test_completed_mapping_is_a_formal_input(self):
        """步骤 4.2：完整扫描生成目标表，可直接作为正式驱动输入读取。"""

        self._create_run()
        self._complete_cable_loss()
        DriverPowerMappingUseCase(self._request(), sleep_fn=lambda _: None).measure_all_frequencies()

        result_path = self._recorder().verified_result_path(STAGE_DRIVER_MAPPING)
        driver_input = ResultInputReader(self.result_repository).read_driver_mapping(
            result_path
        )

        # 目标表主键是目标 DUT 输入功率；源功率和输出来自该目标的实测命中点。
        self.assertEqual(
            driver_input.target_mapping(), {"1.0": {-20.0: (-21.0, -20.0)}}
        )
        self.assertEqual(
            [point.target_input_power_dbm for point in driver_input.target_points],
            [-20.0],
        )

    def test_stage_reference_resolves_back_to_the_stage_file(self):
        self._create_run()
        self._complete_cable_loss()
        result = DriverPowerMappingUseCase(
            self._request(), sleep_fn=lambda _: None
        ).measure_all_frequencies()

        verified = self._recorder().verified_result_path(STAGE_DRIVER_MAPPING)

        self.assertEqual(verified, self._stage_result_path(result.measurement_id))
        loaded = self.stage_repository.load(
            MeasurementStage.DRIVER_MAPPING, verified
        )
        self.assertEqual(loaded.result.measurement_id, result.measurement_id)

    def test_stage_result_survives_run_directory_removal(self):
        """验收标准：结果脱离原运行目录仍可读取。"""
        layout = self._create_run()
        self._complete_cable_loss()
        result = DriverPowerMappingUseCase(
            self._request(), sleep_fn=lambda _: None
        ).measure_all_frequencies()
        stage_path = self._stage_result_path(result.measurement_id)

        shutil.rmtree(layout.run_directory)

        loaded = self.stage_repository.load(
            MeasurementStage.DRIVER_MAPPING, stage_path
        )
        self.assertEqual(loaded.result.measurement_id, result.measurement_id)
        self.assertEqual(loaded.result.run_id, RUN_ID)
        self.assertEqual(loaded.result.status.value, "completed")
        self.assertTrue(loaded.result.target_points)
        self.assertTrue(loaded.result.raw_points)

    def test_legacy_power_mapping_document_is_rejected_as_input(self):
        """旧版单层 ``power_mapping`` 仍按既有规则拒绝，不当作目标网格。"""
        legacy = self.base / "legacy_driver_mapping.json"
        legacy.write_text(
            json.dumps(
                {
                    "run_id": RUN_ID,
                    "measurement_type": "driver_power_mapping",
                    "power_mapping": {"1.0": {"-20": -20.0}},
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "power_mapping"):
            ResultInputReader(self.result_repository).read_driver_mapping(legacy)

    def test_replaced_stage_file_is_rejected_before_consumption(self):
        self._create_run()
        self._complete_cable_loss()
        result = DriverPowerMappingUseCase(
            self._request(), sleep_fn=lambda _: None
        ).measure_all_frequencies()
        stage_path = self._stage_result_path(result.measurement_id)
        stage_path.write_text(
            json.dumps(
                {
                    "measurement_type": "driver_power_mapping",
                    "status": "completed",
                    "run_id": RUN_ID,
                    "measurement_id": result.measurement_id,
                    "created_at": "2026-10-22T15:30:00+00:00",
                    "raw_points": {},
                    "target_mapping": {},
                    "schema_version": "2.0",
                }
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(StageInputUnavailable, "已被替换"):
            self._recorder().verified_result_path(STAGE_DRIVER_MAPPING)

    def test_legacy_run_result_document_keeps_its_run_path(self):
        """旧运行目录中的完整结果文档没有引用标记，仍按原路径返回。"""
        layout = self._create_run()
        recorder = RunStageRecorder(self.repository, RUN_ID)
        recorder.start(STAGE_DRIVER_MAPPING, now=CREATED_AT)
        recorder.complete(
            STAGE_DRIVER_MAPPING,
            {
                "run_id": RUN_ID,
                "measurement_type": "driver_power_mapping",
                "status": "completed",
                "schema_version": "2.0",
                "raw_points": {},
                "target_mapping": {},
            },
            now=CREATED_AT,
        )

        verified = recorder.verified_result_path(STAGE_DRIVER_MAPPING)

        self.assertEqual(verified, layout.run_directory / DRIVER_MAPPING_INPUT_PATH)

    def test_preloaded_input_avoids_the_run_directory_lookup(self):
        """步骤 17：组合入口交出的预检对象直接消费，不再读取运行目录。"""
        self._create_run()
        self._complete_cable_loss()
        loaded_paths = []
        original_load = self.result_repository.load

        def load(path):
            loaded_paths.append(Path(path))
            return original_load(path)

        with patch.object(self.result_repository, "load", side_effect=load):
            DriverPowerMappingUseCase(
                self._request(), sleep_fn=lambda _: None
            ).measure_all_frequencies()

        self.assertEqual(loaded_paths, [])

    def test_newer_global_history_file_is_not_used_as_formal_input(self):
        """步骤 D/17：正式测量不消费全局历史副本，也不查找最新结果。"""
        self._create_run()
        self._complete_cable_loss()
        global_copy = (
            self.base / "test_results" / "driver_power_mapping_20300101_000000.json"
        )
        global_copy.parent.mkdir(parents=True, exist_ok=True)
        global_copy.write_text(
            json.dumps(
                {
                    "run_id": OTHER_RUN_ID,
                    "power_mapping": {"1.0": {"-40": -999.0}},
                }
            ),
            encoding="utf-8",
        )

        loaded_paths = []
        original_load = self.result_repository.load

        def load(path):
            loaded_paths.append(Path(path).resolve())
            return original_load(path)

        with patch.object(self.result_repository, "load", side_effect=load):
            DriverPowerMappingUseCase(
                self._request(), sleep_fn=lambda _: None
            ).measure_all_frequencies()

        self.assertEqual(loaded_paths, [])
        self.assertNotIn(global_copy.resolve(), loaded_paths)

    def test_global_compatibility_copy_is_not_written(self):
        self._create_run()
        self._complete_cable_loss()
        result = DriverPowerMappingUseCase(
            self._request(), sleep_fn=lambda _: None
        ).measure_all_frequencies()

        self.assertEqual(
            list((self.base / "test_results").glob("driver_power_mapping_*.json")), []
        )
        self.assertTrue(
            (self.base / "runs" / RUN_ID / DRIVER_MAPPING_INPUT_PATH).is_file()
        )
        self.assertEqual(
            self._stage_result_files(), [f"{result.measurement_id}.json"]
        )

    # -- 输入隔离与前置失败 ------------------------------------------------

    def test_missing_explicit_cable_loss_fails_before_hardware(self):
        self._create_run()
        instrument = _Instrument()

        with self.assertRaisesRegex(StageInputUnavailable, "显式选择线损"):
            DriverPowerMappingUseCase(
                self._request(instrument=instrument, loss_data=None),
                sleep_fn=lambda _: None,
            )

        self.assertEqual(instrument.measured, 0)
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_DRIVER_MAPPING).status, StageStatus.PENDING)
        self.assertEqual(manifest.stage(STAGE_CABLE_LOSS).status, StageStatus.PENDING)
        self.assertFalse(
            (self.base / "runs" / RUN_ID / DRIVER_MAPPING_INPUT_PATH).exists()
        )

    def test_preloaded_loss_data_is_consumed_without_the_run_input(self):
        """组合入口预检过的线损文档随请求复用，用例不再要求当前运行登记线损。

        这正是组装层必须在连接仪器前完成显式文件校验的原因：一旦交出 ``loss_data``，
        用例就不再读取运行引用。
        """
        self._create_run()
        instrument = _Instrument()
        loss_data = {"cable_losses": CABLE_LOSSES}

        use_case = DriverPowerMappingUseCase(
            self._request(instrument=instrument, loss_data=loss_data),
            sleep_fn=lambda _: None,
        )

        self.assertIs(use_case.loss_data, loss_data)
        use_case.measure_all_frequencies()
        self.assertEqual(
            self.repository.load_manifest(RUN_ID).stage(STAGE_CABLE_LOSS).status,
            StageStatus.PENDING,
        )

    def test_other_run_cable_loss_is_not_auto_selected(self):
        self._create_run(RUN_ID)
        self._create_run(OTHER_RUN_ID)
        self._complete_cable_loss(OTHER_RUN_ID)
        instrument = _Instrument()

        with self.assertRaisesRegex(StageInputUnavailable, "显式选择线损"):
            DriverPowerMappingUseCase(
                self._request(instrument=instrument, loss_data=None),
                sleep_fn=lambda _: None,
            )

        self.assertEqual(instrument.measured, 0)
        # 另一个运行目录的输入仍然属于它自己，未被当前运行消费或改写。
        other_manifest = self.repository.load_manifest(OTHER_RUN_ID)
        self.assertEqual(other_manifest.stage(STAGE_CABLE_LOSS).status, StageStatus.COMPLETED)
        self.assertFalse(
            (self.base / "runs" / RUN_ID / DRIVER_MAPPING_INPUT_PATH).exists()
        )

    def test_explicitly_selected_other_run_cable_loss_is_consumed(self):
        """显式选择另一个运行的阶段结果是唯一的跨运行入口（步骤 14）。

        没有显式路径时当前运行不会自动使用其它运行的数据（上一个用例）；显式选择
        线损阶段文件并给出选择时摘要后按该路径加载并核对，当前运行自己的线损阶段仍
        保持原状态。
        """
        self._create_run()
        reference = self._saved_stage_cable_loss(run_id=OTHER_RUN_ID)
        digest = StageResultDigest(reference.sha256, reference.size_bytes)
        instrument = _Instrument()

        use_case = DriverPowerMappingUseCase(
            self._request(
                instrument=instrument,
                cable_loss_path=reference.path,
                cable_loss_digest=digest,
            ),
            sleep_fn=lambda _: None,
        )

        self.assertEqual(use_case.loss_data["cable_losses"], CABLE_LOSSES)
        result = use_case.measure_all_frequencies()
        self.assertEqual(result.run_id, RUN_ID)
        self.assertEqual(
            self.repository.load_manifest(RUN_ID).stage(STAGE_CABLE_LOSS).status,
            StageStatus.PENDING,
        )

    def test_replaced_other_run_cable_loss_is_rejected_before_measurement(self):
        """选择后阶段文件被替换：摘要不再匹配，测量开始前就失败。"""
        self._create_run()
        reference = self._saved_stage_cable_loss(run_id=OTHER_RUN_ID)
        digest = StageResultDigest(reference.sha256, reference.size_bytes)
        document = json.loads(reference.path.read_text(encoding="utf-8"))
        document["cable_losses"] = {
            "1.0": {"cable1": 9.0, "cable2": 9.0, "cable3": 9.0, "cable4": 9.0}
        }
        reference.path.write_text(json.dumps(document), encoding="utf-8")
        instrument = _Instrument()

        with self.assertRaisesRegex(StageInputUnavailable, "已被修改或替换"):
            DriverPowerMappingUseCase(
                self._request(
                    instrument=instrument,
                    cable_loss_path=reference.path,
                    cable_loss_digest=digest,
                ),
                sleep_fn=lambda _: None,
            )

        self.assertEqual(instrument.measured, 0)

    def test_global_compatibility_result_is_not_used_as_input(self):
        compat = self.base / "test_results" / "cable_loss_results.json"
        compat.parent.mkdir(parents=True, exist_ok=True)
        compat.write_text(
            json.dumps({"run_id": RUN_ID, "cable_losses": CABLE_LOSSES}), encoding="utf-8"
        )
        self._create_run()

        with self.assertRaises(StageInputUnavailable):
            DriverPowerMappingUseCase(self._request(), sleep_fn=lambda _: None)

    def test_tampered_run_directory_copy_no_longer_feeds_the_measurement(self):
        """步骤 17：运行目录的固定校准文件不再参与新测量输入。"""
        layout = self._create_run()
        self._complete_cable_loss()
        result_path = layout.run_directory / CABLE_LOSS_INPUT_PATH
        result_path.write_text(
            json.dumps(
                {"run_id": RUN_ID, "cable_losses": {"1.0": {"cable2": 99.0}}}
            ),
            encoding="utf-8",
        )

        result = DriverPowerMappingUseCase(
            self._request(), sleep_fn=lambda _: None
        ).measure_all_frequencies()

        self.assertTrue(result.raw_points)

    def test_missing_run_directory_copy_no_longer_blocks_the_measurement(self):
        """步骤 17：运行目录的固定校准文件缺失不影响已显式提供的输入。"""
        layout = self._create_run()
        self._complete_cable_loss()
        (layout.run_directory / CABLE_LOSS_INPUT_PATH).unlink()

        result = DriverPowerMappingUseCase(
            self._request(), sleep_fn=lambda _: None
        ).measure_all_frequencies()

        self.assertTrue(result.raw_points)

    # -- 失败与取消 --------------------------------------------------------

    def test_measurement_failure_marks_stage_failed_without_result_file(self):
        layout = self._create_run()
        self._complete_cable_loss()
        use_case = DriverPowerMappingUseCase(
            self._request(instrument=_Instrument(fail=True)), sleep_fn=lambda _: None
        )

        with self.assertRaisesRegex(RuntimeError, "measurement failed"):
            use_case.measure_all_frequencies()

        manifest = self.repository.load_manifest(RUN_ID)
        stage = manifest.stage(STAGE_DRIVER_MAPPING)
        self.assertEqual(stage.status, StageStatus.FAILED)
        self.assertIn("measurement failed", stage.error)
        self.assertEqual(self._stage_result_files(), [])
        self.assertFalse((layout.run_directory / DRIVER_MAPPING_INPUT_PATH).exists())

    def test_cancelled_token_marks_stage_cancelled_without_result_file(self):
        layout = self._create_run()
        self._complete_cable_loss()
        token = CancellationToken()
        token.request_stop(reason="test")
        use_case = DriverPowerMappingUseCase(
            self._request(token=token), sleep_fn=lambda _: None
        )

        with self.assertRaises(MeasurementCancelled):
            use_case.measure_all_frequencies()

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_DRIVER_MAPPING).status, StageStatus.CANCELLED)
        self.assertEqual(self._stage_result_files(), [])
        self.assertFalse((layout.run_directory / DRIVER_MAPPING_INPUT_PATH).exists())

    def test_completed_stage_is_not_overwritten_by_later_failure_or_cancel(self):
        self._create_run()
        recorder = self._complete_cable_loss()
        mapping_recorder = RunStageRecorder(self.repository, RUN_ID)
        request = self._request()
        use_case = DriverPowerMappingUseCase(request, sleep_fn=lambda _: None)
        use_case.measure_all_frequencies()

        mapping_recorder.fail(STAGE_DRIVER_MAPPING, "迟到的失败")
        mapping_recorder.cancel(STAGE_DRIVER_MAPPING, "迟到的取消")

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_DRIVER_MAPPING).status, StageStatus.COMPLETED)
        with self.assertRaisesRegex(ValueError, "终态"):
            mapping_recorder.complete(STAGE_DRIVER_MAPPING, {"power_mapping": {}})

    # -- 兼容副本与提交失败 ------------------------------------------------

    def test_run_contract_never_touches_the_compatibility_copy_api(self):
        layout = self._create_run()
        self._complete_cable_loss()
        request = self._request(repository=_UnwritableResultRepository())
        use_case = DriverPowerMappingUseCase(request, sleep_fn=lambda _: None)

        result = use_case.measure_all_frequencies()

        result_path = layout.run_directory / DRIVER_MAPPING_INPUT_PATH
        self.assertTrue(result_path.is_file())
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_DRIVER_MAPPING).status, StageStatus.COMPLETED)
        self.assertEqual(
            result.result_reference["archive_path"],
            str(self._stage_result_path(result.measurement_id)),
        )
        self.assertNotIn("legacy_copy_path", result.result_reference)

    def test_commit_failure_rolls_back_result_and_keeps_stage_unfinished(self):
        layout = self._create_run()
        self._complete_cable_loss()
        recorder = RunStageRecorder(_FailingCommitRepository(self.repository), RUN_ID)
        recorder.start(STAGE_DRIVER_MAPPING, now=CREATED_AT)

        with self.assertRaisesRegex(OSError, "manifest commit failed"):
            recorder.complete(
                STAGE_DRIVER_MAPPING, {"power_mapping": {}}, now=CREATED_AT
            )

        self.assertFalse((layout.run_directory / DRIVER_MAPPING_INPUT_PATH).exists())
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_DRIVER_MAPPING).status, StageStatus.RUNNING)

    def test_stage_directory_save_failure_marks_stage_failed_without_result(self):
        """阶段目录写入失败：不产生正式结果、不登记引用、阶段收敛为 failed。"""
        layout = self._create_run()
        self._complete_cable_loss()
        use_case = DriverPowerMappingUseCase(
            self._request(
                stage_repository=_FailingStageResultRepository(OSError("disk full"))
            ),
            sleep_fn=lambda _: None,
        )

        with self.assertRaisesRegex(OSError, "disk full"):
            use_case.measure_all_frequencies()

        self.assertEqual(self._stage_result_files(), [])
        self.assertFalse((layout.run_directory / DRIVER_MAPPING_INPUT_PATH).exists())
        manifest = self.repository.load_manifest(RUN_ID)
        stage = manifest.stage(STAGE_DRIVER_MAPPING)
        self.assertEqual(stage.status, StageStatus.FAILED)
        self.assertIn("disk full", stage.error)
        self.assertNotIn(
            DRIVER_MAPPING_INPUT_PATH, [item.path for item in manifest.inputs]
        )

    def test_registration_failure_keeps_the_formal_result_and_reports_it(self):
        """阶段文件已保存、运行引用登记失败：保留正式结果，错误说明给出位置。"""
        layout = self._create_run()
        self._complete_cable_loss()
        recorder = self._recorder(repository=_FailingCommitRepository(self.repository))
        use_case = DriverPowerMappingUseCase(
            self._request(recorder=recorder), sleep_fn=lambda _: None
        )

        with self.assertRaisesRegex(StageResultNotRecorded, "已保存到") as caught:
            use_case.measure_all_frequencies()

        reference = caught.exception.reference
        self.assertIs(reference.stage, MeasurementStage.DRIVER_MAPPING)
        self.assertEqual(
            reference.path, self._stage_result_path(reference.measurement_id)
        )
        # 已提交的正式结果没有被删除或截断，阶段仓库仍能按正式契约读回。
        self.assertTrue(reference.path.is_file())
        loaded = self.stage_repository.load(
            MeasurementStage.DRIVER_MAPPING, reference.path
        )
        self.assertEqual(loaded.result.measurement_id, reference.measurement_id)
        self.assertEqual(loaded.result.status.value, "completed")

        # 运行阶段仍收敛为 failed，但说明里给出阶段文件位置；引用文档已回滚。
        manifest = self.repository.load_manifest(RUN_ID)
        stage = manifest.stage(STAGE_DRIVER_MAPPING)
        self.assertEqual(stage.status, StageStatus.FAILED)
        self.assertIn(str(reference.path), stage.error)
        self.assertFalse((layout.run_directory / DRIVER_MAPPING_INPUT_PATH).exists())
        self.assertNotIn(
            DRIVER_MAPPING_INPUT_PATH, [item.path for item in manifest.inputs]
        )

    def test_unregistered_stage_result_is_discoverable_in_the_stage_directory(self):
        """登记失败的正式结果不依赖 manifest 也能在阶段目录中被发现和读取。"""
        self._create_run()
        self._complete_cable_loss()
        recorder = self._recorder(repository=_FailingCommitRepository(self.repository))
        use_case = DriverPowerMappingUseCase(
            self._request(recorder=recorder), sleep_fn=lambda _: None
        )

        with self.assertRaises(StageResultNotRecorded) as caught:
            use_case.measure_all_frequencies()

        reference = caught.exception.reference
        entries = self.stage_repository.list_results(
            MeasurementStage.DRIVER_MAPPING
        )
        self.assertEqual(
            [entry.reference.measurement_id for entry in entries],
            [reference.measurement_id],
        )
        self.assertEqual(entries[0].reference.path, reference.path)
        self.assertEqual(entries[0].run_id, RUN_ID)
        self.assertEqual(entries[0].status.value, "completed")

    def test_repeated_measurements_do_not_overwrite_old_results(self):
        self._create_run(RUN_ID)
        self._complete_cable_loss(RUN_ID)
        first_result = DriverPowerMappingUseCase(
            self._request(RUN_ID), sleep_fn=lambda _: None
        ).measure_all_frequencies()

        self._create_run(OTHER_RUN_ID)
        self._complete_cable_loss(OTHER_RUN_ID)
        second_result = DriverPowerMappingUseCase(
            self._request(OTHER_RUN_ID), sleep_fn=lambda _: None
        ).measure_all_frequencies()

        self.assertNotEqual(first_result.measurement_id, second_result.measurement_id)
        self.assertEqual(
            self._stage_result_files(),
            sorted(
                [
                    f"{first_result.measurement_id}.json",
                    f"{second_result.measurement_id}.json",
                ]
            ),
        )
        first_document = json.loads(
            self._stage_result_path(first_result.measurement_id).read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(first_document["run_id"], RUN_ID)

    # -- 组合入口端到端 ----------------------------------------------------

    def test_composition_entry_runs_cable_loss_then_mapping(self):
        config_path = self.base / "config.json"
        config_path.write_text(json.dumps(_fully_valid_gui_config()), encoding="utf-8")
        prepared = prepare_configuration(
            str(config_path),
            run_id=RUN_ID,
            run_repository=self.repository,
        )

        self.assertTrue(prepared.has_run_contract)
        self.assertEqual(prepared.run_id, RUN_ID)

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
        mapping_case = create_driver_mapping_measurement(
            prepared_run=prepared,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
            paths=self.paths,
            cable_loss_path=Path(pointer["path"]),
            sleep_fn=lambda _: None,
        )
        mapping_case.measure_all_frequencies()

        run_directory = prepared.run_directory
        self.assertTrue((run_directory / CABLE_LOSS_INPUT_PATH).is_file())
        self.assertTrue((run_directory / DRIVER_MAPPING_INPUT_PATH).is_file())
        payload = self._read_mapping_stage_document(run_directory)
        self.assertEqual(payload["run_id"], RUN_ID)
        self.assertEqual(
            payload["measurement_id"],
            validate_generated_measurement_id(payload["measurement_id"]),
        )
        # 端到端：正式 1.2 配置 -> 映射服务 -> 目标表覆盖同一套目标功率点。
        self.assertEqual(
            [
                row["target_input_power_dbm"]
                for row in payload["target_mapping"]["2.4"]
            ],
            [float(value) for value in range(-10, 1)],
        )
        self.assertTrue(
            self._stage_result_path(payload["measurement_id"]).is_file()
        )

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_CABLE_LOSS).status, StageStatus.COMPLETED)
        self.assertEqual(manifest.stage(STAGE_DRIVER_MAPPING).status, StageStatus.COMPLETED)

    def test_composition_entry_uses_the_full_multi_plan_target_grid(self):
        """步骤 5.3：GUI 多段计划经 authoring 冻结后，映射目标表覆盖全部目标点。"""

        plans = [
            {"start_power": -10.0, "step": 2.0, "stop_power": -6.0},
            {"start_power": -6.0, "step": 1.0, "stop_power": -3.0},
        ]
        config_path = self.base / "config.json"
        config_path.write_text(
            json.dumps(_fully_valid_gui_config(power_scan_plans=plans)),
            encoding="utf-8",
        )
        prepared = prepare_configuration(
            str(config_path),
            run_id=RUN_ID,
            run_repository=self.repository,
        )
        cable_reference = self._saved_stage_cable_loss(
            cable_losses={"2.4": {"cable2": 2.0}}
        )

        mapping_case = create_driver_mapping_measurement(
            prepared_run=prepared,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
            paths=self.paths,
            cable_loss_path=cable_reference.path,
            sleep_fn=lambda _: None,
        )
        mapping_case.measure_all_frequencies()

        payload = self._read_mapping_stage_document(prepared.run_directory)
        rows = payload["target_mapping"]["2.4"]
        self.assertEqual(
            [row["target_input_power_dbm"] for row in rows],
            [-10.0, -8.0, -6.0, -5.0, -4.0, -3.0],
        )
        # 公共边界 -6 dBm 只出现一次，粗段与细段都进入同一张目标表。
        self.assertEqual(
            len({row["target_input_power_dbm"] for row in rows}),
            len(rows),
        )

    def test_service_config_passes_multi_segment_plans_in_order(self):
        """步骤 4.2：运行配置的 1.2 计划列表原样交给映射服务，不再写旧标量。"""
        from application.measurements.driver_mapping import _service_config
        from domain.configuration.models import TestPlan

        plan = TestPlan.from_dict(
            {
                "schema_version": "1.2",
                "frequencies": {"values": [1.0, 2.4], "unit": "GHz"},
                "signal_source": {
                    "power_scan_plans": [
                        {"start_power": -20.0, "step": 2.0, "stop_power": -16.0},
                        {"start_power": -16.0, "step": 1.0, "stop_power": -13.0},
                    ],
                    "unit": "dBm",
                    "power_reference": "dut_input",
                },
                "compression_point": {"type": "1dB"},
                "attenuator": {"value": 10.0, "unit": "dB"},
                "dut": {"max_input_power": 30.0},
                "driver_mode": {
                    "enabled": True,
                    "estimated_gain_db": 1.0,
                    "max_input_power": 20.0,
                },
            }
        )

        config = _service_config(SimpleNamespace(test_plan=plan))

        self.assertEqual(
            config["signal_source"]["power_scan_plans"],
            [
                {"start_power": -20.0, "step": 2.0, "stop_power": -16.0},
                {"start_power": -16.0, "step": 1.0, "stop_power": -13.0},
            ],
        )
        self.assertNotIn("start_power", config["signal_source"])
        self.assertNotIn("stop_power", config["signal_source"])
        self.assertNotIn("step", config["signal_source"])
        self.assertEqual(config["max_dut_input_power"], 30.0)
        self.assertEqual(config["driver_gain_estimate_db"], 1.0)
        self.assertEqual(config["driver_max_input_power"], 20.0)

    def test_composition_checks_input_before_connecting_instruments(self):
        layout = self._create_run()
        prepared = self._contract_prepared(layout)

        with patch("app.gui_runtime.connect_instruments") as connect:
            with self.assertRaises(StageInputUnavailable):
                create_driver_mapping_measurement(prepared_run=prepared)

        connect.assert_not_called()

    def test_legacy_compatibility_copy_is_marked_not_authoritative(self):
        self._create_run()
        self._complete_cable_loss()
        DriverPowerMappingUseCase(self._request(), sleep_fn=lambda _: None).measure_all_frequencies()

        payload = self._read_mapping_stage_document(
            self.repository.layout(RUN_ID).run_directory
        )
        self.assertNotIn("compatibility_copy", payload)


if __name__ == "__main__":
    unittest.main()
