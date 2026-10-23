"""步骤 6 测试：把主功放的输入和结果迁移到运行目录契约。

覆盖验收标准：

- 主功放只从当前运行目录读取 ``inputs/cable_loss.json``，驱动模式启用时再读取
  ``inputs/driver_mapping.json``；当前运行缺少必需输入时在硬件动作前失败。
- 主功放不会扫描全局目录选择最新校准数据，另一个运行目录的输入不会被使用。
- 驱动模式关闭时不要求映射文件存在，映射阶段在 manifest 中标记为 ``skipped``。
- 所有最终结果与拆分文件写入当前运行的 ``results/``，都能从 manifest 找到，
  完成时阶段状态与运行状态更新为 ``completed``。
"""

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.cancellation import CancellationToken, MeasurementCancelled
from app.gui_runtime import (
    create_amplifier_measurement,
    create_cable_loss_measurement,
    prepare_configuration,
)
from application.dto import AmplifierMeasurementRequest
from application.inputs import ResultInputReader
from application.measurements import AmplifierCalibrationError, AmplifierMeasurementUseCase
from application.measurements.amplifier_test import AMPLIFIER_SPLIT_SCHEMA_VERSION
from application.run_stages import RunStageRecorder, StageInputUnavailable
from domain.run.models import (
    AMPLIFIER_RESULT_PATH,
    CABLE_LOSS_INPUT_PATH,
    DRIVER_MAPPING_INPUT_PATH,
    HARDWARE_CONFIG_INPUT_PATH,
    STAGE_AMPLIFIER,
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

CABLE_LOSSES = {"1.0": {"cable1": 0.0, "cable2": 0.0, "cable3": 0.0, "cable4": 0.0}}
POWER_MAPPING = {"1.0": {"-20.0": -10.0}}


def _fully_valid_gui_config(*, driver_enabled=False) -> dict:
    """完整有效的旧版 GUI 组合配置（默认驱动模式关闭，DUT 通道已确认接线）。"""
    return {
        "test_frequencies": [1.0],
        "signal_source": {"start_power": -20.0, "stop_power": -20.0, "step": 1.0},
        "compression_point": {"type": "1dB"},
        "driver_mode": {"enabled": driver_enabled},
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


def _amplifier_config(*, driver_enabled=False) -> dict:
    """主功放服务契约使用的组合配置（映射已解析为服务载荷形状）。"""
    return {
        "test_frequencies": [1.0],
        "attenuator": {"type": "10dB"},
        "signal_source": {"start_power": -20, "stop_power": -20, "step": 1},
        "compression_point": {"type": "1dB"},
        "driver_mode": {"enabled": driver_enabled},
        "power_supply_assignment": {
            "dut_amplifier": {"supplies": {"PS1": {"name": "PS1", "channel": ["CH2"]}}}
        },
        "dut_config": {"max_input_power": 100},
    }


class _Instrument:
    """同时满足线损、驱动映射和主功放服务的最小仪器替身。"""

    def __init__(self, *, fail=False):
        self.measured = 0
        self.fail = fail
        self.rf_off_calls = 0

    def setup_driver_amplifier_power(self): pass
    def power_on_driver(self): pass
    def setup_dut_power(self): pass
    def power_on_sequence(self): pass
    def power_off_sequence(self): pass
    def set_power(self, _power): pass
    def set_frequency(self, _frequency): pass
    def set_center_frequency(self, _frequency): pass
    def set_span(self, _span): pass
    def rf_output_on(self): pass
    def rf_output_off(self): self.rf_off_calls += 1
    def power_off_driver(self): pass
    def read_voltage(self, _name, _channel): return 28.0
    def read_current(self, _name, _channel): return 1.0

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


class _CompatFailingRepository:
    """兼容副本写入必定失败的替身，用于验证权威结果不受影响。"""

    def save_compatibility_copy(self, *args, **kwargs):
        raise OSError("compatibility copy failed")


class _FailingCommitRepository:
    """把主功放阶段提交为 completed 时的 manifest 写入注入失败。"""

    def __init__(self, repository):
        self.repository = repository

    def __getattr__(self, name):
        return getattr(self.repository, name)

    def update_manifest(self, run_id, manifest, *, now=None):
        if manifest.stage(STAGE_AMPLIFIER).status is StageStatus.COMPLETED:
            raise OSError("manifest commit failed")
        return self.repository.update_manifest(run_id, manifest, now=now)


class _FailingRunCompleteRepository:
    """把运行级状态写为 completed 时的 manifest 写入注入失败。"""

    def __init__(self, repository):
        self.repository = repository

    def __getattr__(self, name):
        return getattr(self.repository, name)

    def update_manifest(self, run_id, manifest, *, now=None):
        if manifest.status is RunStatus.COMPLETED:
            raise OSError("run complete failed")
        return self.repository.update_manifest(run_id, manifest, now=now)


class AmplifierRunStageTests(unittest.TestCase):
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
        recorder = RunStageRecorder(self.repository, run_id)
        recorder.start(STAGE_CABLE_LOSS, now=CREATED_AT)
        recorder.complete(
            STAGE_CABLE_LOSS,
            {"run_id": run_id, "cable_losses": cable_losses},
            now=CREATED_AT,
        )
        return recorder

    def _complete_driver_mapping(self, run_id=RUN_ID, *, mapping=POWER_MAPPING):
        recorder = RunStageRecorder(self.repository, run_id)
        recorder.start(STAGE_DRIVER_MAPPING, now=CREATED_AT)
        recorder.complete(
            STAGE_DRIVER_MAPPING,
            {"run_id": run_id, "power_mapping": mapping},
            now=CREATED_AT,
        )
        return recorder

    def _request(
        self,
        run_id=RUN_ID,
        *,
        instrument=None,
        token=None,
        repository=None,
        driver_enabled=False,
        loss_data=None,
        driver_mapping=None,
    ):
        layout = self.repository.layout(run_id)
        return AmplifierMeasurementRequest(
            configuration=_amplifier_config(driver_enabled=driver_enabled),
            context=_Context(run_id),
            run_directory=layout.run_directory,
            measurement_port=instrument or _Instrument(),
            result_repository=repository or self.result_repository,
            input_reader=ResultInputReader(self.result_repository),
            stage_recorder=RunStageRecorder(self.repository, run_id),
            cancellation_token=token,
            loss_data=loss_data,
            driver_mapping=driver_mapping,
            safety_options={"owns_measurement_port": True},
        )

    def _run_directory(self, run_id=RUN_ID):
        return self.repository.layout(run_id).run_directory

    # -- 结果归属当前运行 --------------------------------------------------

    def test_completed_result_belongs_to_current_run(self):
        layout = self._create_run()
        self._complete_cable_loss()
        use_case = AmplifierMeasurementUseCase(self._request(), sleep_fn=lambda _: None)

        result = use_case.measure_all_frequencies()

        result_path = layout.run_directory / AMPLIFIER_RESULT_PATH
        self.assertTrue(result_path.is_file())
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["run_id"], RUN_ID)
        self.assertIn("results", payload)

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.status, RunStatus.COMPLETED)
        stage = manifest.stage(STAGE_AMPLIFIER)
        self.assertEqual(stage.status, StageStatus.COMPLETED)
        self.assertEqual(stage.result, AMPLIFIER_RESULT_PATH)

        outputs = {item.path: item for item in manifest.outputs}
        self.assertIn(AMPLIFIER_RESULT_PATH, outputs)
        self.assertIsNotNone(outputs[AMPLIFIER_RESULT_PATH].sha256)
        self.assertEqual(
            outputs[AMPLIFIER_RESULT_PATH].size_bytes, result_path.stat().st_size
        )
        self.assertEqual(result.result_reference["archive_path"], str(result_path))
        self.assertEqual(manifest.stage(STAGE_CABLE_LOSS).status, StageStatus.COMPLETED)

    def test_split_files_are_written_and_registered(self):
        layout = self._create_run()
        self._complete_cable_loss()
        AmplifierMeasurementUseCase(self._request(), sleep_fn=lambda _: None).measure_all_frequencies()

        split_path = layout.run_directory / "results" / "amplifier_measurement_1.0.json"
        self.assertTrue(split_path.is_file())
        split = json.loads(split_path.read_text(encoding="utf-8"))
        self.assertEqual(split["schema_version"], AMPLIFIER_SPLIT_SCHEMA_VERSION)
        self.assertEqual(split["stage"], STAGE_AMPLIFIER)
        self.assertEqual(split["run_id"], RUN_ID)
        self.assertEqual(split["measurement_type"], "amplifier")
        self.assertEqual(split["frequency"], "1.0")
        self.assertIn("result", split)

        outputs = {item.path for item in self.repository.load_manifest(RUN_ID).outputs}
        self.assertIn(AMPLIFIER_RESULT_PATH, outputs)
        self.assertIn("results/amplifier_measurement_1.0.json", outputs)

    def test_result_is_loadable_from_run_directory(self):
        layout = self._create_run()
        self._complete_cable_loss()
        AmplifierMeasurementUseCase(self._request(), sleep_fn=lambda _: None).measure_all_frequencies()

        saved = json.loads(
            (layout.run_directory / AMPLIFIER_RESULT_PATH).read_text(encoding="utf-8")
        )
        self.assertIn("1.0", saved["results"])
        self.assertIn("sweep_data", saved["results"]["1.0"])

    def test_driver_mode_completes_with_mapping_from_current_run(self):
        self._create_run()
        self._complete_cable_loss()
        self._complete_driver_mapping()
        instrument = _Instrument()

        use_case = AmplifierMeasurementUseCase(
            self._request(instrument=instrument, driver_enabled=True),
            sleep_fn=lambda _: None,
        )
        # 驱动映射输入来自当前运行，服务使用插值后的 DUT 输入功率。
        self.assertEqual(use_case.driver_mapping, POWER_MAPPING)

        use_case.measure_all_frequencies()

        self.assertEqual(instrument.measured, 1)
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_DRIVER_MAPPING).status, StageStatus.COMPLETED)
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.COMPLETED)
        self.assertEqual(manifest.status, RunStatus.COMPLETED)

    def test_disabled_driver_mode_marks_mapping_stage_skipped(self):
        self._create_run()
        self._complete_cable_loss()
        instrument = _Instrument()

        AmplifierMeasurementUseCase(
            self._request(instrument=instrument, driver_enabled=False),
            sleep_fn=lambda _: None,
        ).measure_all_frequencies()

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_DRIVER_MAPPING).status, StageStatus.SKIPPED)
        self.assertFalse(
            (self._run_directory() / DRIVER_MAPPING_INPUT_PATH).exists()
        )
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.COMPLETED)

    # -- 输入隔离与前置失败 ------------------------------------------------

    def test_missing_current_run_cable_loss_fails_before_hardware(self):
        self._create_run()
        instrument = _Instrument()

        with self.assertRaisesRegex(StageInputUnavailable, "cable_loss"):
            AmplifierMeasurementUseCase(
                self._request(instrument=instrument), sleep_fn=lambda _: None
            )

        self.assertEqual(instrument.measured, 0)
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.PENDING)
        self.assertFalse((self._run_directory() / AMPLIFIER_RESULT_PATH).exists())

    def test_driver_mode_without_mapping_fails_before_hardware(self):
        self._create_run()
        self._complete_cable_loss()
        instrument = _Instrument()

        with self.assertRaisesRegex(StageInputUnavailable, "driver_mapping"):
            AmplifierMeasurementUseCase(
                self._request(instrument=instrument, driver_enabled=True),
                sleep_fn=lambda _: None,
            )

        self.assertEqual(instrument.measured, 0)
        self.assertEqual(
            self.repository.load_manifest(RUN_ID).stage(STAGE_DRIVER_MAPPING).status,
            StageStatus.PENDING,
        )

    def test_structurally_invalid_current_run_cable_loss_is_rejected(self):
        layout = self._create_run()
        recorder = RunStageRecorder(self.repository, RUN_ID)
        recorder.start(STAGE_CABLE_LOSS, now=CREATED_AT)
        # 缺少 cable1/cable4：哈希校验通过，但结构无法支撑计算。
        recorder.complete(
            STAGE_CABLE_LOSS,
            {"run_id": RUN_ID, "cable_losses": {"1.0": {"cable2": 0.0}}},
            now=CREATED_AT,
        )
        instrument = _Instrument()

        with self.assertRaises(AmplifierCalibrationError):
            AmplifierMeasurementUseCase(
                self._request(instrument=instrument), sleep_fn=lambda _: None
            )

        self.assertEqual(instrument.measured, 0)
        self.assertFalse((layout.run_directory / AMPLIFIER_RESULT_PATH).exists())

    def test_explicit_inputs_do_not_bypass_current_run_inputs(self):
        self._create_run()
        instrument = _Instrument()

        with self.assertRaises(StageInputUnavailable):
            AmplifierMeasurementUseCase(
                self._request(
                    instrument=instrument,
                    loss_data={"cable_losses": CABLE_LOSSES},
                    driver_mapping=POWER_MAPPING,
                ),
                sleep_fn=lambda _: None,
            )

        self.assertEqual(instrument.measured, 0)

    def test_current_run_cable_loss_takes_precedence_over_explicit_loss_data(self):
        self._create_run()
        self._complete_cable_loss()
        # 显式 loss_data 缺少 cable1：若被错误地优先使用，补偿计算会失败。
        incomplete = {"cable_losses": {"1.0": {}}}
        use_case = AmplifierMeasurementUseCase(
            self._request(loss_data=incomplete), sleep_fn=lambda _: None
        )

        use_case.measure_all_frequencies()

        self.assertEqual(
            self.repository.load_manifest(RUN_ID).stage(STAGE_AMPLIFIER).status,
            StageStatus.COMPLETED,
        )

    def test_other_run_cable_loss_is_not_used(self):
        self._create_run(RUN_ID)
        self._create_run(OTHER_RUN_ID)
        self._complete_cable_loss(OTHER_RUN_ID)
        instrument = _Instrument()

        with self.assertRaisesRegex(StageInputUnavailable, "cable_loss"):
            AmplifierMeasurementUseCase(
                self._request(instrument=instrument), sleep_fn=lambda _: None
            )

        self.assertEqual(instrument.measured, 0)
        self.assertEqual(
            self.repository.load_manifest(OTHER_RUN_ID).stage(STAGE_CABLE_LOSS).status,
            StageStatus.COMPLETED,
        )
        self.assertFalse((self._run_directory(RUN_ID) / AMPLIFIER_RESULT_PATH).exists())

    def test_global_compatibility_calibration_is_not_used_as_input(self):
        compat = self.base / "test_results" / "cable_loss_results.json"
        compat.parent.mkdir(parents=True, exist_ok=True)
        compat.write_text(
            json.dumps({"run_id": RUN_ID, "cable_losses": CABLE_LOSSES}), encoding="utf-8"
        )
        self._create_run()

        with self.assertRaises(StageInputUnavailable):
            AmplifierMeasurementUseCase(self._request(), sleep_fn=lambda _: None)

    def test_tampered_cable_loss_file_is_rejected(self):
        layout = self._create_run()
        self._complete_cable_loss()
        (layout.run_directory / CABLE_LOSS_INPUT_PATH).write_text(
            json.dumps({"run_id": RUN_ID, "cable_losses": {"1.0": {"cable1": 99.0}}}),
            encoding="utf-8",
        )

        with self.assertRaises(StageInputUnavailable):
            AmplifierMeasurementUseCase(self._request(), sleep_fn=lambda _: None)

    # -- 失败与取消 --------------------------------------------------------

    def test_measurement_failure_marks_stage_failed_without_result_files(self):
        layout = self._create_run()
        self._complete_cable_loss()
        use_case = AmplifierMeasurementUseCase(
            self._request(instrument=_Instrument(fail=True)), sleep_fn=lambda _: None
        )

        with self.assertRaisesRegex(RuntimeError, "measurement failed"):
            use_case.measure_all_frequencies()

        manifest = self.repository.load_manifest(RUN_ID)
        stage = manifest.stage(STAGE_AMPLIFIER)
        self.assertEqual(stage.status, StageStatus.FAILED)
        self.assertIn("measurement failed", stage.error)
        self.assertFalse((layout.run_directory / AMPLIFIER_RESULT_PATH).exists())
        self.assertTrue(manifest.outputs == ())
        # 运行随阶段失败同步收敛为 failed，不会长期停留在 running。
        self.assertEqual(manifest.status, RunStatus.FAILED)

    def test_run_completion_failure_is_surfaced_not_swallowed(self):
        layout = self._create_run()
        self._complete_cable_loss()
        request = replace(
            self._request(),
            stage_recorder=RunStageRecorder(
                _FailingRunCompleteRepository(self.repository), RUN_ID
            ),
        )
        use_case = AmplifierMeasurementUseCase(request, sleep_fn=lambda _: None)

        with self.assertRaisesRegex(OSError, "run complete failed"):
            use_case.measure_all_frequencies()

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.COMPLETED)
        # 阶段已完成但运行级更新失败：状态不一致必须可见，而不是伪装成完整成功。
        self.assertEqual(manifest.status, RunStatus.RUNNING)
        self.assertTrue((layout.run_directory / AMPLIFIER_RESULT_PATH).is_file())

    def test_cancelled_token_marks_stage_cancelled_without_result_files(self):
        layout = self._create_run()
        self._complete_cable_loss()
        token = CancellationToken()
        token.request_emergency_stop(reason="test")
        use_case = AmplifierMeasurementUseCase(
            self._request(token=token), sleep_fn=lambda _: None
        )

        with self.assertRaises(MeasurementCancelled):
            use_case.measure_all_frequencies()

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.CANCELLED)
        self.assertEqual(manifest.status, RunStatus.CANCELLED)
        self.assertFalse((layout.run_directory / AMPLIFIER_RESULT_PATH).exists())

    # -- 兼容副本与提交失败 ------------------------------------------------

    def test_compatibility_copy_failure_does_not_fail_the_measurement(self):
        layout = self._create_run()
        self._complete_cable_loss()
        use_case = AmplifierMeasurementUseCase(
            self._request(repository=_CompatFailingRepository()), sleep_fn=lambda _: None
        )

        result = use_case.measure_all_frequencies()

        result_path = layout.run_directory / AMPLIFIER_RESULT_PATH
        self.assertTrue(result_path.is_file())
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.COMPLETED)
        self.assertEqual(result.result_reference["archive_path"], str(result_path))
        self.assertNotIn("legacy_copy_path", result.result_reference)

    def test_commit_failure_rolls_back_results_and_keeps_stage_unfinished(self):
        layout = self._create_run()
        self._complete_cable_loss()
        recorder = RunStageRecorder(_FailingCommitRepository(self.repository), RUN_ID)
        recorder.start(STAGE_AMPLIFIER, now=CREATED_AT)

        with self.assertRaisesRegex(OSError, "manifest commit failed"):
            recorder.complete(
                STAGE_AMPLIFIER,
                {"run_id": RUN_ID, "results": {}},
                result_path=AMPLIFIER_RESULT_PATH,
                extra_files={"results/amplifier_measurement_1.0.json": {"frequency": 1.0}},
                now=CREATED_AT,
            )

        self.assertFalse((layout.run_directory / AMPLIFIER_RESULT_PATH).exists())
        self.assertFalse(
            (layout.run_directory / "results" / "amplifier_measurement_1.0.json").exists()
        )
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.RUNNING)
        self.assertEqual(manifest.outputs, ())

    # -- 组合入口端到端 ----------------------------------------------------

    def test_composition_entry_runs_cable_loss_then_amplifier(self):
        config_path = self.base / "config.json"
        config_path.write_text(json.dumps(_fully_valid_gui_config()), encoding="utf-8")
        prepared = prepare_configuration(
            str(config_path),
            run_id=RUN_ID,
            run_repository=self.repository,
        )

        cable_case = create_cable_loss_measurement(
            str(config_path),
            prepared_run=prepared,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
        )
        cable_case.measure_all_frequencies()
        cable_case.continue_to_step2()

        amplifier_case = create_amplifier_measurement(
            str(config_path),
            prepared_run=prepared,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
        )
        amplifier_case.measure_all_frequencies()

        run_directory = prepared.run_directory
        self.assertTrue((run_directory / CABLE_LOSS_INPUT_PATH).is_file())
        self.assertTrue((run_directory / AMPLIFIER_RESULT_PATH).is_file())

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.status, RunStatus.COMPLETED)
        self.assertEqual(manifest.stage(STAGE_CABLE_LOSS).status, StageStatus.COMPLETED)
        self.assertEqual(manifest.stage(STAGE_DRIVER_MAPPING).status, StageStatus.SKIPPED)
        self.assertEqual(manifest.stage(STAGE_AMPLIFIER).status, StageStatus.COMPLETED)

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
                create_amplifier_measurement("config.json", prepared_run=prepared)

        connect.assert_not_called()

    def test_composition_rejects_invalid_calibration_before_connecting(self):
        layout = self._create_run()
        recorder = RunStageRecorder(self.repository, RUN_ID)
        recorder.start(STAGE_CABLE_LOSS, now=CREATED_AT)
        recorder.complete(
            STAGE_CABLE_LOSS,
            {"run_id": RUN_ID, "cable_losses": {"1.0": {"cable2": 0.0}}},
            now=CREATED_AT,
        )
        prepared = SimpleNamespace(
            configuration=SimpleNamespace(
                test_plan=SimpleNamespace(driver_enabled=False, frequencies=[1.0])
            ),
            context=SimpleNamespace(run_id=RUN_ID),
            run_directory=layout.run_directory,
            run_repository=self.repository,
            hardware_config_path=layout.run_directory / HARDWARE_CONFIG_INPUT_PATH,
        )

        with patch("app.gui_runtime.connect_instruments") as connect:
            with self.assertRaises(AmplifierCalibrationError):
                create_amplifier_measurement("config.json", prepared_run=prepared)

        connect.assert_not_called()

    def test_composition_ignores_global_config_modified_after_run_creation(self):
        config_path = self.base / "config.json"
        config_path.write_text(json.dumps(_fully_valid_gui_config()), encoding="utf-8")
        prepared = prepare_configuration(
            str(config_path),
            run_id=RUN_ID,
            run_repository=self.repository,
        )
        cable_case = create_cable_loss_measurement(
            str(config_path),
            prepared_run=prepared,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
        )
        cable_case.measure_all_frequencies()
        cable_case.continue_to_step2()

        # 修改全局兼容配置：运行契约必须完全忽略它，改用自己的冻结硬件配置。
        modified = _fully_valid_gui_config()
        modified["test_frequencies"] = [2.0]
        config_path.write_text(json.dumps(modified), encoding="utf-8")

        with patch("app.gui_runtime.connect_instruments", return_value=_Instrument()) as connect, patch(
            "app.gui_runtime.AmplifierMeasurementUseCase", return_value=object()
        ):
            create_amplifier_measurement(str(config_path), prepared_run=prepared)

        connect.assert_called_once_with(str(prepared.hardware_config_path))

    def test_composition_requires_mapping_before_hardware_when_driver_enabled(self):
        layout = self._create_run()
        self._complete_cable_loss()
        prepared = SimpleNamespace(
            configuration=SimpleNamespace(
                test_plan=SimpleNamespace(driver_enabled=True)
            ),
            context=SimpleNamespace(run_id=RUN_ID),
            run_directory=layout.run_directory,
            run_repository=self.repository,
            hardware_config_path=layout.run_directory / HARDWARE_CONFIG_INPUT_PATH,
        )

        with patch("app.gui_runtime.connect_instruments") as connect:
            with self.assertRaises(StageInputUnavailable):
                create_amplifier_measurement("config.json", prepared_run=prepared)

        connect.assert_not_called()

    # -- 旧兼容流程 --------------------------------------------------------

    def test_legacy_flow_still_reads_explicit_loss_data(self):
        request = AmplifierMeasurementRequest(
            configuration=_amplifier_config(driver_enabled=False),
            context=_Context("run-legacy"),
            run_directory=self.base,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
            input_reader=ResultInputReader(self.result_repository),
            loss_data={"cable_losses": CABLE_LOSSES},
            safety_options={"owns_measurement_port": True},
        )

        use_case = AmplifierMeasurementUseCase(request, sleep_fn=lambda _: None)

        self.assertIsNone(use_case.stage_recorder)
        self.assertEqual(use_case.loss_data, {"cable_losses": CABLE_LOSSES})
        self.assertIsNone(use_case.driver_mapping)

    def test_legacy_flow_saves_through_result_repository(self):
        calls = []

        class _Recorder:
            def save(self, result, **kwargs):
                calls.append((result, kwargs))
                return SimpleNamespace(
                    run_directory=Path("saved-run"),
                    archive_path=Path("saved-run") / "amplifier_measurement.json",
                    legacy_copy_path=Path("test_results") / "amplifier_measurement_x.json",
                )

        request = AmplifierMeasurementRequest(
            configuration=_amplifier_config(driver_enabled=False),
            context=_Context("run-legacy"),
            run_directory=self.base / "legacy-run",
            measurement_port=_Instrument(),
            result_repository=_Recorder(),
            loss_data={"cable_losses": CABLE_LOSSES},
            safety_options={"owns_measurement_port": True},
        )

        AmplifierMeasurementUseCase(request, sleep_fn=lambda _: None).measure_all_frequencies()

        self.assertEqual(calls[0][1]["result_type"], "amplifier_measurement")
        self.assertEqual(calls[0][1]["run_id"], "run-legacy")
        self.assertNotIn("stage_recorder", calls[0][1])


if __name__ == "__main__":
    unittest.main()
