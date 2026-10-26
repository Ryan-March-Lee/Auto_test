"""步骤 5 测试：把驱动映射的输入和结果迁移到运行目录契约。

覆盖验收标准：

- 驱动映射只从当前运行目录读取已完成的 ``inputs/cable_loss.json``，当前运行
  缺失线损结果时在硬件动作前失败。
- 另一个运行目录或全局兼容副本的线损数据不会被自动使用。
- 映射结果写入当前运行的 ``inputs/driver_mapping.json``，更新 manifest，并可以
  通过当前运行目录单独加载。
"""

import json
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
from application.dto import DriverPowerMappingRequest
from application.inputs import ResultInputReader
from application.measurements import DriverPowerMappingUseCase
from application.run_stages import RunStageRecorder, StageInputUnavailable
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


RUN_ID = "20261022T153000Z_a1b2c3d4"
OTHER_RUN_ID = "20261022T160000Z_b2c3d4e5"
CREATED_AT = datetime(2026, 10, 22, 15, 30, tzinfo=timezone.utc)

CABLE_LOSSES = {"1.0": {"cable2": 2.0}}


def _fully_valid_gui_config() -> dict:
    """完整有效的旧版 GUI 组合配置（驱动模式关闭，DUT 通道已确认接线）。"""
    return {
        "test_frequencies": [2.4],
        "signal_source": {"start_power": -10.0, "stop_power": 0.0, "step": 1.0},
        "compression_point": {"type": "5dB"},
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


class _Instrument:
    """同时满足线损和驱动映射服务的最小仪器替身。"""

    def __init__(self, *, fail=False):
        self.measured = 0
        self.fail = fail
        self.rf_off_calls = 0
        self.driver_off_calls = 0

    def setup_driver_amplifier_power(self): pass
    def power_on_driver(self): pass
    def set_power(self, _power): pass
    def set_frequency(self, _frequency): pass
    def rf_output_on(self): pass
    def set_center_frequency(self, _frequency): pass
    def set_span(self, _span): pass
    def rf_output_off(self): self.rf_off_calls += 1
    def power_off_driver(self): self.driver_off_calls += 1

    def measure_power_with_average(self):
        if self.fail:
            raise RuntimeError("measurement failed")
        self.measured += 1
        return -10.0

    def close_all(self, close_rf=False):
        return []


class _Context:
    def __init__(self, run_id):
        self.run_id = run_id


class _UnwritableResultRepository:
    """正式运行只写运行目录结果；该对象不提供任何结果写入能力。"""


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
        self.repository = RunRepository(self.base / "runs")
        self.result_repository = FileMeasurementResultRepository(
            results_directory=self.base / "test_results"
        )

    def _create_run(self, run_id=RUN_ID):
        layout, _manifest = self.repository.create_run(
            {"run_id": run_id}, run_id=run_id, now=CREATED_AT
        )
        return layout

    def _complete_cable_loss(self, run_id=RUN_ID, *, cable_losses=CABLE_LOSSES):
        """在指定运行目录写入并登记已完成的线损输入。"""
        recorder = RunStageRecorder(self.repository, run_id)
        recorder.start(STAGE_CABLE_LOSS, now=CREATED_AT)
        recorder.complete(
            STAGE_CABLE_LOSS,
            {"run_id": run_id, "cable_losses": cable_losses},
            now=CREATED_AT,
        )
        return recorder

    def _request(
        self, run_id=RUN_ID, *, instrument=None, token=None, repository=None, loss_data=None
    ):
        layout = self.repository.layout(run_id)
        return DriverPowerMappingRequest(
            configuration={
                "test_frequencies": [1.0],
                "attenuator": {"type": "10dB"},
                "signal_source": {"start_power": -20, "stop_power": -20, "step": 1},
            },
            context=_Context(run_id),
            run_directory=layout.run_directory,
            measurement_port=instrument or _Instrument(),
            result_repository=repository or self.result_repository,
            input_reader=ResultInputReader(self.result_repository),
            stage_recorder=RunStageRecorder(self.repository, run_id),
            cancellation_token=token,
            loss_data=loss_data,
            safety_options={"owns_measurement_port": True},
        )

    # -- 结果归属当前运行 --------------------------------------------------

    def test_completed_mapping_belongs_to_current_run(self):
        layout = self._create_run()
        self._complete_cable_loss()
        use_case = DriverPowerMappingUseCase(self._request(), sleep_fn=lambda _: None)

        result = use_case.measure_all_frequencies()

        result_path = layout.run_directory / DRIVER_MAPPING_INPUT_PATH
        self.assertTrue(result_path.is_file())
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["run_id"], RUN_ID)
        self.assertIn("power_mapping", payload)

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.status, RunStatus.RUNNING)
        stage = manifest.stage(STAGE_DRIVER_MAPPING)
        self.assertEqual(stage.status, StageStatus.COMPLETED)
        self.assertEqual(stage.result, DRIVER_MAPPING_INPUT_PATH)

        registered = {item.path: item for item in manifest.inputs}
        self.assertIn(DRIVER_MAPPING_INPUT_PATH, registered)
        self.assertIsNotNone(registered[DRIVER_MAPPING_INPUT_PATH].sha256)
        self.assertEqual(
            registered[DRIVER_MAPPING_INPUT_PATH].size_bytes, result_path.stat().st_size
        )
        self.assertEqual(result.result_reference["archive_path"], str(result_path))
        # 线损阶段不受驱动映射结果写入影响。
        self.assertEqual(manifest.stage(STAGE_CABLE_LOSS).status, StageStatus.COMPLETED)

    def test_mapping_result_is_loadable_from_run_directory(self):
        layout = self._create_run()
        self._complete_cable_loss()
        DriverPowerMappingUseCase(self._request(), sleep_fn=lambda _: None).measure_all_frequencies()

        result_path = layout.run_directory / DRIVER_MAPPING_INPUT_PATH
        reader = ResultInputReader(self.result_repository)
        mapping = reader.read_driver_mapping(result_path)
        self.assertIn("1.0", mapping)
        self.assertEqual(len(mapping["1.0"]), 1)

    def test_current_run_input_does_not_use_deprecated_latest_lookup(self):
        self._create_run()
        self._complete_cable_loss()
        expected_path = self.repository.layout(RUN_ID).run_directory / CABLE_LOSS_INPUT_PATH
        loaded_paths = []
        original_load = self.result_repository.load

        def load(path):
            loaded_paths.append(Path(path))
            return original_load(path)

        with patch.object(self.result_repository, "load", side_effect=load):
            DriverPowerMappingUseCase(
                self._request(), sleep_fn=lambda _: None
            ).measure_all_frequencies()

        self.assertEqual(loaded_paths[0], expected_path)

    def test_newer_global_history_file_is_not_used_as_formal_input(self):
        """步骤 D：更新的全局历史副本不会被正式测量当作输入。

        与 ``test_current_run_input_does_not_use_deprecated_latest_lookup`` 相比，
        本用例先制造一个**比当前运行输入更新**的全局历史结果，再跑真实测量链，
        同时记录所有实际读取路径，证明正式测量只消费当前运行目录内的输入。
        """
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

        run_directory = self.repository.layout(RUN_ID).run_directory.resolve()
        self.assertTrue(loaded_paths)
        for path in loaded_paths:
            # 每个实际读取的输入都必须在当前运行目录内，且不是全局历史副本。
            path.relative_to(run_directory)
        self.assertNotIn(global_copy.resolve(), loaded_paths)

    def test_global_compatibility_copy_is_not_written(self):
        self._create_run()
        self._complete_cable_loss()
        DriverPowerMappingUseCase(self._request(), sleep_fn=lambda _: None).measure_all_frequencies()

        self.assertEqual(
            list((self.base / "test_results").glob("driver_power_mapping_*.json")), []
        )
        self.assertTrue(
            (self.base / "runs" / RUN_ID / DRIVER_MAPPING_INPUT_PATH).is_file()
        )

    # -- 输入隔离与前置失败 ------------------------------------------------

    def test_missing_current_run_cable_loss_fails_before_hardware(self):
        self._create_run()
        instrument = _Instrument()

        with self.assertRaisesRegex(StageInputUnavailable, "cable_loss"):
            DriverPowerMappingUseCase(
                self._request(instrument=instrument), sleep_fn=lambda _: None
            )

        self.assertEqual(instrument.measured, 0)
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_DRIVER_MAPPING).status, StageStatus.PENDING)
        self.assertEqual(manifest.stage(STAGE_CABLE_LOSS).status, StageStatus.PENDING)
        self.assertFalse(
            (self.base / "runs" / RUN_ID / DRIVER_MAPPING_INPUT_PATH).exists()
        )

    def test_explicit_loss_data_does_not_bypass_current_run_input(self):
        self._create_run()
        instrument = _Instrument()

        with self.assertRaises(StageInputUnavailable):
            DriverPowerMappingUseCase(
                self._request(instrument=instrument, loss_data={"cable_losses": CABLE_LOSSES}),
                sleep_fn=lambda _: None,
            )

        self.assertEqual(instrument.measured, 0)
        self.assertEqual(
            self.repository.load_manifest(RUN_ID).stage(STAGE_DRIVER_MAPPING).status,
            StageStatus.PENDING,
        )

    def test_current_run_input_takes_precedence_over_explicit_loss_data(self):
        self._create_run()
        self._complete_cable_loss()
        # 显式 loss_data 缺少 cable2：若被错误地优先使用，补偿计算会失败。
        incomplete = {"cable_losses": {"1.0": {}}}
        use_case = DriverPowerMappingUseCase(
            self._request(loss_data=incomplete), sleep_fn=lambda _: None
        )

        use_case.measure_all_frequencies()

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_DRIVER_MAPPING).status, StageStatus.COMPLETED)

    def test_other_run_cable_loss_is_not_used(self):
        self._create_run(RUN_ID)
        self._create_run(OTHER_RUN_ID)
        self._complete_cable_loss(OTHER_RUN_ID)
        instrument = _Instrument()

        with self.assertRaisesRegex(StageInputUnavailable, "cable_loss"):
            DriverPowerMappingUseCase(
                self._request(instrument=instrument), sleep_fn=lambda _: None
            )

        self.assertEqual(instrument.measured, 0)
        # 另一个运行目录的输入仍然属于它自己，未被当前运行消费或改写。
        other_manifest = self.repository.load_manifest(OTHER_RUN_ID)
        self.assertEqual(other_manifest.stage(STAGE_CABLE_LOSS).status, StageStatus.COMPLETED)
        self.assertFalse(
            (self.base / "runs" / RUN_ID / DRIVER_MAPPING_INPUT_PATH).exists()
        )

    def test_global_compatibility_result_is_not_used_as_input(self):
        compat = self.base / "test_results" / "cable_loss_results.json"
        compat.parent.mkdir(parents=True, exist_ok=True)
        compat.write_text(
            json.dumps({"run_id": RUN_ID, "cable_losses": CABLE_LOSSES}), encoding="utf-8"
        )
        self._create_run()

        with self.assertRaises(StageInputUnavailable):
            DriverPowerMappingUseCase(self._request(), sleep_fn=lambda _: None)

    def test_tampered_cable_loss_file_is_rejected(self):
        layout = self._create_run()
        self._complete_cable_loss()
        result_path = layout.run_directory / CABLE_LOSS_INPUT_PATH
        result_path.write_text(
            json.dumps(
                {"run_id": RUN_ID, "cable_losses": {"1.0": {"cable2": 99.0}}}
            ),
            encoding="utf-8",
        )

        with self.assertRaises(StageInputUnavailable):
            DriverPowerMappingUseCase(self._request(), sleep_fn=lambda _: None)

    def test_completed_stage_with_missing_file_is_rejected(self):
        layout = self._create_run()
        self._complete_cable_loss()
        (layout.run_directory / CABLE_LOSS_INPUT_PATH).unlink()

        with self.assertRaises(StageInputUnavailable):
            DriverPowerMappingUseCase(self._request(), sleep_fn=lambda _: None)

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
        self.assertFalse((layout.run_directory / DRIVER_MAPPING_INPUT_PATH).exists())

    def test_cancelled_token_marks_stage_cancelled_without_result_file(self):
        layout = self._create_run()
        self._complete_cable_loss()
        token = CancellationToken()
        token.request_emergency_stop(reason="test")
        use_case = DriverPowerMappingUseCase(
            self._request(token=token), sleep_fn=lambda _: None
        )

        with self.assertRaises(MeasurementCancelled):
            use_case.measure_all_frequencies()

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_DRIVER_MAPPING).status, StageStatus.CANCELLED)
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
        self.assertEqual(result.result_reference["archive_path"], str(result_path))
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
            str(config_path),
            prepared_run=prepared,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
        )
        cable_case.measure_all_frequencies()
        cable_case.continue_to_step2()

        mapping_case = create_driver_mapping_measurement(
            str(config_path),
            prepared_run=prepared,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
        )
        mapping_case.measure_all_frequencies()

        run_directory = prepared.run_directory
        self.assertTrue((run_directory / CABLE_LOSS_INPUT_PATH).is_file())
        mapping_path = run_directory / DRIVER_MAPPING_INPUT_PATH
        self.assertTrue(mapping_path.is_file())
        self.assertEqual(
            json.loads(mapping_path.read_text(encoding="utf-8"))["run_id"], RUN_ID
        )

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_CABLE_LOSS).status, StageStatus.COMPLETED)
        self.assertEqual(manifest.stage(STAGE_DRIVER_MAPPING).status, StageStatus.COMPLETED)

    def test_composition_checks_input_before_connecting_instruments(self):
        layout = self._create_run()
        prepared = SimpleNamespace(
            configuration=object(),
            context=SimpleNamespace(run_id=RUN_ID),
            run_directory=layout.run_directory,
            run_repository=self.repository,
            hardware_config_path=layout.run_directory / HARDWARE_CONFIG_INPUT_PATH,
        )

        with patch("app.gui_runtime.connect_instruments") as connect:
            with self.assertRaises(StageInputUnavailable):
                create_driver_mapping_measurement("config.json", prepared_run=prepared)

        connect.assert_not_called()

    def test_legacy_composition_requires_loss_path_before_connecting(self):
        """B1：旧兼容流程缺少显式线损路径时，在连接仪器前失败。"""
        prepared = SimpleNamespace(
            configuration={
                "test_frequencies": [1.0],
                "attenuator": {"type": "10dB"},
                "signal_source": {"start_power": -20, "stop_power": -20, "step": 1},
            },
            context=SimpleNamespace(run_id="run-compat-snapshot"),
            run_directory=self.base,
            hardware_config_path=self.base / HARDWARE_CONFIG_INPUT_PATH,
        )

        with patch("app.gui_runtime.connect_instruments") as connect:
            with self.assertRaisesRegex(ValueError, "必须显式提供 loss_data_path"):
                create_driver_mapping_measurement("config.json", prepared_run=prepared)

        connect.assert_not_called()

    def test_legacy_flow_still_reads_explicit_loss_data_path(self):
        loss_path = self.base / "legacy_cable_loss.json"
        loss_path.write_text(json.dumps({"cable_losses": CABLE_LOSSES}), encoding="utf-8")
        request = DriverPowerMappingRequest(
            configuration={
                "test_frequencies": [1.0],
                "attenuator": {"type": "10dB"},
                "signal_source": {"start_power": -20, "stop_power": -20, "step": 1},
            },
            context=_Context("run-compat-snapshot"),
            run_directory=self.base,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
            input_reader=ResultInputReader(self.result_repository),
            loss_data_path=loss_path,
            safety_options={"owns_measurement_port": True},
        )

        use_case = DriverPowerMappingUseCase(request, sleep_fn=lambda _: None)

        self.assertIsNone(use_case.stage_recorder)
        self.assertEqual(use_case.loss_data, {"cable_losses": CABLE_LOSSES})

    def test_legacy_compatibility_copy_is_marked_not_authoritative(self):
        self._create_run()
        self._complete_cable_loss()
        DriverPowerMappingUseCase(self._request(), sleep_fn=lambda _: None).measure_all_frequencies()

        authoritative = self.base / "runs" / RUN_ID / DRIVER_MAPPING_INPUT_PATH
        payload = json.loads(authoritative.read_text(encoding="utf-8"))
        self.assertNotIn("compatibility_copy", payload)


if __name__ == "__main__":
    unittest.main()
