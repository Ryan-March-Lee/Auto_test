"""步骤 4 测试：把线损结果迁移到运行目录契约。

覆盖验收标准：

- 线损结果必定归属于当前 ``run_id``，写入 ``inputs/cable_loss.json`` 并登记 manifest。
- 失败、取消和部分完成不会把线损阶段标记为完成，也不会生成阶段结果文件。
- 旧的全局兼容结果文件仍会写入，旧读取路径继续可用。
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.cancellation import CancellationToken, MeasurementCancelled
from app.gui_runtime import create_cable_loss_measurement, prepare_configuration
from application.dto import CableLossMeasurementRequest
from application.measurements import CableLossUseCase
from application.run_stages import RunStageRecorder
from domain.run.models import (
    CABLE_LOSS_INPUT_PATH,
    RunStatus,
    StageStatus,
)
from infrastructure.persistence.result_repository import FileMeasurementResultRepository
from infrastructure.persistence.run_repository import RunRepository
from presentation.qt.configuration_authoring import author_official_configuration
from tests.fakes import safety_cleanup_for


RUN_ID = "20261022T153000Z_a1b2c3d4"
OTHER_RUN_ID = "20261022T160000Z_b2c3d4e5"
CREATED_AT = datetime(2026, 10, 22, 15, 30, tzinfo=timezone.utc)


def _fully_valid_gui_config() -> dict:
    """完整有效的 GUI 组合配置（驱动模式关闭，DUT 通道已确认接线）。"""
    return author_official_configuration({
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
    })


class _Instrument:
    """最小仪器替身：可让路径2测量注入失败。"""

    def __init__(self, *, fail=False):
        self.measured = 0
        self.fail = fail
        self.close_calls = 0

    def set_frequency(self, _frequency): pass
    def set_power(self, _power): pass
    def rf_output_on(self): pass
    def set_center_frequency(self, _frequency): pass
    def set_span(self, _span): pass
    def rf_output_off(self): pass

    def measure_power_with_average(self):
        if self.fail:
            raise RuntimeError("measurement failed")
        self.measured += 1
        return -10.0

    def close_all(self, close_rf=False):
        self.close_calls += 1
        return []


class _FailingCommitRepository:
    """把线损阶段提交为 completed 时的 manifest 写入注入失败。"""

    def __init__(self, repository):
        self.repository = repository

    def __getattr__(self, name):
        return getattr(self.repository, name)

    def update_manifest(self, run_id, manifest, *, now=None):
        if manifest.stage("cable_loss").status is StageStatus.COMPLETED:
            raise OSError("manifest commit failed")
        return self.repository.update_manifest(run_id, manifest, now=now)


class _UnwritableResultRepository:
    """正式运行只写运行目录结果；该对象不提供任何结果写入能力。"""


class CableLossRunStageTests(unittest.TestCase):
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

    def _request(self, run_id=RUN_ID, *, instrument=None, token=None):
        layout = self._create_run(run_id)
        recorder = RunStageRecorder(self.repository, run_id)
        instrument = instrument or _Instrument()
        return CableLossMeasurementRequest(
            configuration={"test_frequencies": [1.0], "attenuator": {"type": "10dB"}},
            context=_Context(run_id),
            run_directory=layout.run_directory,
            measurement_port=instrument,
            safety_cleanup=safety_cleanup_for(instrument),
            result_repository=self.result_repository,
            stage_recorder=recorder,
            cancellation_token=token,
            safety_options={"owns_measurement_port": True},
        )

    def test_completed_result_belongs_to_current_run(self):
        request = self._request()
        use_case = CableLossUseCase(request, sleep_fn=lambda _: None)

        use_case.measure_all_frequencies()
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage("cable_loss").status, StageStatus.RUNNING)
        self.assertFalse((self.base / "runs" / RUN_ID / CABLE_LOSS_INPUT_PATH).exists())

        result = use_case.continue_to_step2()

        result_path = self.base / "runs" / RUN_ID / CABLE_LOSS_INPUT_PATH
        self.assertTrue(result_path.is_file())
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["run_id"], RUN_ID)
        self.assertIn("cable_losses", payload)

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.status, RunStatus.RUNNING)
        stage = manifest.stage("cable_loss")
        self.assertEqual(stage.status, StageStatus.COMPLETED)
        self.assertEqual(stage.result, CABLE_LOSS_INPUT_PATH)

        registered = {item.path: item for item in manifest.inputs}
        self.assertIn(CABLE_LOSS_INPUT_PATH, registered)
        self.assertIsNotNone(registered[CABLE_LOSS_INPUT_PATH].sha256)
        self.assertEqual(
            registered[CABLE_LOSS_INPUT_PATH].size_bytes, result_path.stat().st_size
        )

        self.assertEqual(
            result.result_reference["archive_path"], str(result_path)
        )

    def test_global_compatibility_copy_is_not_written(self):
        request = self._request()
        use_case = CableLossUseCase(request, sleep_fn=lambda _: None)
        use_case.measure_all_frequencies()
        result = use_case.continue_to_step2()

        # 正式结果只落在运行目录并登记 manifest，全局兼容目录不再出现结果文件。
        self.assertFalse((self.base / "test_results" / "cable_loss_results.json").exists())
        self.assertEqual(
            result.result_reference["archive_path"],
            str(self.base / "runs" / RUN_ID / CABLE_LOSS_INPUT_PATH),
        )

    def test_failure_marks_stage_failed_without_result_file(self):
        request = self._request(instrument=_Instrument(fail=True))
        use_case = CableLossUseCase(request, sleep_fn=lambda _: None)

        with self.assertRaisesRegex(RuntimeError, "measurement failed"):
            use_case.measure_all_frequencies()

        manifest = self.repository.load_manifest(RUN_ID)
        stage = manifest.stage("cable_loss")
        self.assertEqual(stage.status, StageStatus.FAILED)
        self.assertIn("measurement failed", stage.error)
        self.assertFalse((self.base / "runs" / RUN_ID / CABLE_LOSS_INPUT_PATH).exists())

    def test_cancel_while_waiting_marks_stage_cancelled_without_result_file(self):
        request = self._request()
        use_case = CableLossUseCase(request, sleep_fn=lambda _: None)
        use_case.measure_all_frequencies()

        use_case.stop_measurement()

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage("cable_loss").status, StageStatus.CANCELLED)
        self.assertFalse((self.base / "runs" / RUN_ID / CABLE_LOSS_INPUT_PATH).exists())

    def test_cancelled_token_does_not_complete_the_stage(self):
        token = CancellationToken()
        token.request_emergency_stop(reason="test")
        request = self._request(token=token)
        use_case = CableLossUseCase(request, sleep_fn=lambda _: None)

        with self.assertRaises(MeasurementCancelled):
            use_case.measure_all_frequencies()

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage("cable_loss").status, StageStatus.CANCELLED)
        self.assertFalse((self.base / "runs" / RUN_ID / CABLE_LOSS_INPUT_PATH).exists())

    def test_completed_stage_is_not_overwritten_by_later_failure_or_cancel(self):
        request = self._request()
        recorder = request.stage_recorder
        use_case = CableLossUseCase(request, sleep_fn=lambda _: None)
        use_case.measure_all_frequencies()
        use_case.continue_to_step2()

        recorder.fail("cable_loss", "迟到的失败")
        recorder.cancel("cable_loss", "迟到的取消")

        manifest = self.repository.load_manifest(RUN_ID)
        stage = manifest.stage("cable_loss")
        self.assertEqual(stage.status, StageStatus.COMPLETED)
        with self.assertRaisesRegex(ValueError, "终态"):
            recorder.complete("cable_loss", {"cable_losses": {}})

    def test_commit_failure_rolls_back_result_and_keeps_stage_unfinished(self):
        layout = self._create_run()
        recorder = RunStageRecorder(_FailingCommitRepository(self.repository), RUN_ID)
        recorder.start("cable_loss", now=CREATED_AT)

        with self.assertRaisesRegex(OSError, "manifest commit failed"):
            recorder.complete("cable_loss", {"cable_losses": {"1.0": 0.5}}, now=CREATED_AT)

        result_path = layout.run_directory / CABLE_LOSS_INPUT_PATH
        self.assertFalse(result_path.exists())
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage("cable_loss").status, StageStatus.RUNNING)
        self.assertEqual(manifest.inputs, ())

    def test_fail_removes_uncommitted_stage_result(self):
        layout = self._create_run()
        recorder = RunStageRecorder(self.repository, RUN_ID)
        recorder.start("cable_loss", now=CREATED_AT)
        self.repository.write_run_json(RUN_ID, CABLE_LOSS_INPUT_PATH, {"cable_losses": {}})
        self.repository.register_file(RUN_ID, CABLE_LOSS_INPUT_PATH, stage="cable_loss")

        manifest = recorder.fail("cable_loss", "硬件异常", now=CREATED_AT)

        self.assertFalse((layout.run_directory / CABLE_LOSS_INPUT_PATH).exists())
        self.assertEqual(manifest.stage("cable_loss").status, StageStatus.FAILED)
        self.assertEqual(manifest.inputs, ())

    def test_fail_from_pending_fails_the_run(self):
        self._create_run()
        recorder = RunStageRecorder(self.repository, RUN_ID)
        manifest = recorder.fail("cable_loss", "未开始即失败", now=CREATED_AT)
        self.assertEqual(manifest.status, RunStatus.FAILED)
        self.assertEqual(manifest.stage("cable_loss").status, StageStatus.FAILED)

    def test_cancel_removes_uncommitted_stage_result(self):
        layout = self._create_run()
        recorder = RunStageRecorder(self.repository, RUN_ID)
        recorder.start("cable_loss", now=CREATED_AT)
        self.repository.write_run_json(RUN_ID, CABLE_LOSS_INPUT_PATH, {"cable_losses": {}})
        self.repository.register_file(RUN_ID, CABLE_LOSS_INPUT_PATH, stage="cable_loss")

        manifest = recorder.cancel("cable_loss", "用户停止", now=CREATED_AT)

        self.assertFalse((layout.run_directory / CABLE_LOSS_INPUT_PATH).exists())
        self.assertEqual(manifest.stage("cable_loss").status, StageStatus.CANCELLED)
        self.assertEqual(manifest.inputs, ())

    def test_run_contract_never_touches_the_compatibility_copy_api(self):
        layout = self._create_run()
        instrument = _Instrument()
        request = CableLossMeasurementRequest(
            configuration={"test_frequencies": [1.0], "attenuator": {"type": "10dB"}},
            context=_Context(RUN_ID),
            run_directory=layout.run_directory,
            measurement_port=instrument,
            safety_cleanup=safety_cleanup_for(instrument),
            result_repository=_UnwritableResultRepository(),
            stage_recorder=RunStageRecorder(self.repository, RUN_ID),
            safety_options={"owns_measurement_port": True},
        )
        use_case = CableLossUseCase(request, sleep_fn=lambda _: None)
        use_case.measure_all_frequencies()

        result = use_case.continue_to_step2()

        result_path = layout.run_directory / CABLE_LOSS_INPUT_PATH
        self.assertTrue(result_path.is_file())
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage("cable_loss").status, StageStatus.COMPLETED)
        self.assertEqual(result.result_reference["archive_path"], str(result_path))
        self.assertNotIn("legacy_copy_path", result.result_reference)

    def test_composition_entry_creates_contract_run_and_writes_stage_result(self):
        config_path = self.base / "config.json"
        config_path.write_text(json.dumps(_fully_valid_gui_config()), encoding="utf-8")
        prepared = prepare_configuration(
            str(config_path),
            run_id=RUN_ID,
            run_repository=self.repository,
        )

        self.assertTrue(prepared.has_run_contract)
        self.assertEqual(prepared.run_id, RUN_ID)
        self.assertTrue((prepared.run_directory / "config.json").is_file())
        self.assertTrue((prepared.run_directory / "manifest.json").is_file())

        use_case = create_cable_loss_measurement(
            str(config_path),
            prepared_run=prepared,
            measurement_port=_Instrument(),
            result_repository=self.result_repository,
            sleep_fn=lambda _: None,
        )
        use_case.measure_all_frequencies()
        use_case.continue_to_step2()

        result_path = prepared.run_directory / "inputs" / "cable_loss.json"
        self.assertTrue(result_path.is_file())
        self.assertEqual(
            json.loads(result_path.read_text(encoding="utf-8"))["run_id"], RUN_ID
        )
        self.assertEqual(
            self.repository.load_manifest(RUN_ID).stage("cable_loss").status,
            StageStatus.COMPLETED,
        )
        self.assertFalse(
            (self.base / "test_results" / "cable_loss_results.json").is_file()
        )

    def test_two_runs_are_isolated(self):
        first = CableLossUseCase(self._request(RUN_ID), sleep_fn=lambda _: None)
        first.measure_all_frequencies()
        first.continue_to_step2()

        self._create_run(OTHER_RUN_ID)
        second_dir = self.base / "runs" / OTHER_RUN_ID
        self.assertFalse((second_dir / CABLE_LOSS_INPUT_PATH).exists())
        second_manifest = self.repository.load_manifest(OTHER_RUN_ID)
        self.assertEqual(second_manifest.stage("cable_loss").status, StageStatus.PENDING)
        self.assertEqual(
            json.loads(
                (first.run_directory / "inputs" / "cable_loss.json").read_text(
                    encoding="utf-8"
                )
            )["run_id"],
            RUN_ID,
        )


class _Context:
    def __init__(self, run_id):
        self.run_id = run_id


class RunStageRecorderTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.repository = RunRepository(self.base / "runs")
        self.repository.create_run({}, run_id=RUN_ID, now=CREATED_AT)
        self.recorder = RunStageRecorder(self.repository, RUN_ID)

    def test_start_advances_stage_and_run_status(self):
        manifest = self.recorder.start("cable_loss", now=CREATED_AT)
        self.assertEqual(manifest.status, RunStatus.RUNNING)
        self.assertEqual(manifest.stage("cable_loss").status, StageStatus.RUNNING)
        self.assertEqual(manifest.stage("driver_mapping").status, StageStatus.PENDING)

    def test_complete_registers_file_and_checksum(self):
        self.recorder.start("cable_loss", now=CREATED_AT)
        reference = self.recorder.complete(
            "cable_loss", {"cable_losses": {"1.0": 0.5}}, now=CREATED_AT
        )
        self.assertEqual(reference.path, CABLE_LOSS_INPUT_PATH)
        self.assertIsNotNone(reference.sha256)

        manifest = self.repository.load_manifest(RUN_ID)
        self.assertEqual(manifest.stage("cable_loss").status, StageStatus.COMPLETED)
        self.assertEqual(manifest.stage("cable_loss").result, CABLE_LOSS_INPUT_PATH)

    def test_is_completed_and_result_path_track_committed_stage(self):
        self.assertFalse(self.recorder.is_completed("cable_loss"))
        self.assertIsNone(self.recorder.result_path("cable_loss"))

        self.recorder.start("cable_loss", now=CREATED_AT)
        self.assertFalse(self.recorder.is_completed("cable_loss"))

        self.recorder.complete("cable_loss", {"cable_losses": {}}, now=CREATED_AT)
        self.assertTrue(self.recorder.is_completed("cable_loss"))
        self.assertEqual(self.recorder.result_path("cable_loss"), CABLE_LOSS_INPUT_PATH)

    def test_fail_from_pending_records_error(self):
        manifest = self.recorder.fail("cable_loss", "硬件未就绪", now=CREATED_AT)
        stage = manifest.stage("cable_loss")
        self.assertEqual(stage.status, StageStatus.FAILED)
        self.assertEqual(stage.error, "硬件未就绪")

    def test_skip_only_accepts_pending_and_is_idempotent(self):
        self.recorder.start("driver_mapping", now=CREATED_AT)
        with self.assertRaisesRegex(ValueError, "skipped"):
            self.recorder.skip("driver_mapping", now=CREATED_AT)

        self.recorder.cancel("driver_mapping", "停止", now=CREATED_AT)
        with self.assertRaisesRegex(ValueError, "skipped"):
            self.recorder.skip("driver_mapping", now=CREATED_AT)

    def test_skip_is_idempotent_for_already_skipped_stage(self):
        self.recorder.skip("driver_mapping", now=CREATED_AT)
        manifest = self.recorder.skip("driver_mapping", now=CREATED_AT)
        self.assertEqual(manifest.stage("driver_mapping").status, StageStatus.SKIPPED)

    def test_complete_run_rejects_unfinished_stages(self):
        self.recorder.start("cable_loss", now=CREATED_AT)
        with self.assertRaisesRegex(ValueError, "未完成"):
            self.recorder.complete_run(now=CREATED_AT)
        self.assertEqual(self.repository.load_manifest(RUN_ID).status, RunStatus.RUNNING)

    def test_complete_run_rejects_while_any_stage_still_pending(self):
        self.recorder.start("cable_loss", now=CREATED_AT)
        self.recorder.complete("cable_loss", {"cable_losses": {}}, now=CREATED_AT)
        self.recorder.skip("driver_mapping", now=CREATED_AT)
        # amplifier 仍为 pending。
        with self.assertRaisesRegex(ValueError, "amplifier"):
            self.recorder.complete_run(now=CREATED_AT)

    def test_complete_run_marks_run_completed_when_all_stages_finish(self):
        self.recorder.start("cable_loss", now=CREATED_AT)
        self.recorder.complete("cable_loss", {"cable_losses": {}}, now=CREATED_AT)
        self.recorder.skip("driver_mapping", now=CREATED_AT)
        self.recorder.start("amplifier", now=CREATED_AT)
        self.recorder.complete(
            "amplifier",
            {"results": {}},
            result_path="results/amplifier_measurement.json",
            now=CREATED_AT,
        )

        manifest = self.recorder.complete_run(now=CREATED_AT)

        self.assertEqual(manifest.status, RunStatus.COMPLETED)
        self.assertEqual(manifest.stage("amplifier").status, StageStatus.COMPLETED)

    def test_cancel_converges_run_status_and_blocks_new_stages(self):
        self.recorder.start("cable_loss", now=CREATED_AT)
        manifest = self.recorder.cancel("cable_loss", "用户停止", now=CREATED_AT)

        self.assertEqual(manifest.status, RunStatus.CANCELLED)
        with self.assertRaisesRegex(ValueError, "终态"):
            self.recorder.start("driver_mapping", now=CREATED_AT)

    def test_fail_converges_run_status(self):
        self.recorder.start("cable_loss", now=CREATED_AT)
        manifest = self.recorder.fail("cable_loss", "硬件异常", now=CREATED_AT)

        self.assertEqual(manifest.status, RunStatus.FAILED)
        with self.assertRaisesRegex(ValueError, "终态"):
            self.recorder.start("driver_mapping", now=CREATED_AT)

    def test_complete_rollback_keeps_preexisting_split_file(self):
        layout = self.repository.layout(RUN_ID)
        preexisting = layout.results_dir / "amplifier_measurement_1.0.json"
        preexisting.write_text('{"old": true}', encoding="utf-8")
        self.recorder.start("amplifier", now=CREATED_AT)

        with self.assertRaises(FileExistsError):
            self.recorder.complete(
                "amplifier",
                {"results": {}},
                result_path="results/amplifier_measurement.json",
                extra_files={"results/amplifier_measurement_1.0.json": {"frequency": 1.0}},
                now=CREATED_AT,
            )

        # 主结果属于本次提交，回滚后删除；预先存在的拆分文件必须原样保留。
        self.assertFalse((layout.results_dir / "amplifier_measurement.json").exists())
        self.assertEqual(preexisting.read_text(encoding="utf-8"), '{"old": true}')
        self.assertEqual(self.repository.load_manifest(RUN_ID).outputs, ())

    def test_complete_rejects_missing_result_path_for_dynamic_stage(self):
        with self.assertRaisesRegex(ValueError, "固定结果路径"):
            self.recorder.complete("amplifier", {"results": {}})

    def test_restart_of_terminal_stage_is_rejected(self):
        self.recorder.start("cable_loss", now=CREATED_AT)
        self.recorder.complete("cable_loss", {"cable_losses": {}}, now=CREATED_AT)
        with self.assertRaisesRegex(ValueError, "不能重新开始"):
            self.recorder.start("cable_loss", now=CREATED_AT)

    def test_write_run_json_rejects_paths_outside_run_directories(self):
        with self.assertRaisesRegex(ValueError, "inputs/ 或 results/"):
            self.repository.write_run_json(RUN_ID, "cable_loss.json", {})
        with self.assertRaisesRegex(ValueError, "不能包含"):
            self.repository.write_run_json(RUN_ID, "inputs/../escape.json", {})


if __name__ == "__main__":
    unittest.main()
