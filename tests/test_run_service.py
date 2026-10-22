"""步骤 3 测试：把配置保存改为创建运行快照。"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.run_context import create_prepared_run
from application.ports.config_repository import ConfigurationLoadResult
from application.run_service import (
    create_run_snapshot,
    load_run_configuration,
)
from domain.configuration.models import (
    ChannelMapping,
    InstrumentMapping,
    PowerChannelPlan,
    RunConfiguration,
    RunResourceMapping,
    TestPlan,
    validate_run_configuration,
)
from domain.configuration.types import ConfigIssue, ConfigValidationResult
from domain.run.models import HARDWARE_CONFIG_INPUT_PATH, RunStatus, StageStatus
from infrastructure.config.json_config_repository import ConfigurationRepository
from infrastructure.persistence.run_repository import RunRepository


CREATED_AT = datetime(2026, 10, 22, 15, 30, tzinfo=timezone.utc)
RUN_ID = "20261022T153000Z_a1b2c3d4"


def _valid_configuration(*, start_power: float = -10.0) -> RunConfiguration:
    plan = TestPlan(
        frequencies=[2.2, 2.4],
        start_power=start_power,
        stop_power=0.0,
        power_step=1.0,
        compression_point="5dB",
        attenuator_value=30.0,
        max_input_power=30.0,
        dut_power_channels={"gate": PowerChannelPlan(role="gate", voltage=2.0, current=1.0)},
        driver_enabled=False,
    )
    mapping = RunResourceMapping(
        instruments={
            "signal_generator": InstrumentMapping("SG", "sim::sg"),
            "spectrum_analyzer": InstrumentMapping("SA", "sim::sa"),
            "power_supply": InstrumentMapping("PS", "sim::ps"),
        },
        dut_power_channels=[ChannelMapping(channel="CH1", role="gate", connection="gate")],
        wiring_confirmed=True,
        connection_note="测试接线已确认",
    )
    return RunConfiguration(test_plan=plan, run_mapping=mapping)


def _loaded(configuration: RunConfiguration) -> ConfigurationLoadResult:
    return ConfigurationLoadResult(configuration, validate_run_configuration(configuration))


def _gui_config(*, start_power: float = -10.0) -> dict:
    """GUI 保存的组合配置（旧版格式），可转换为完整有效运行配置。"""
    return {
        "test_frequencies": [2.4],
        "signal_source": {"start_power": start_power, "stop_power": 0.0, "step": 1.0},
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


class CreateRunSnapshotTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.repository = RunRepository(self.root / "runs")
        self.loaded = _loaded(_valid_configuration())
        self.assertTrue(self.loaded.valid, self.loaded.validation.errors)

    def test_create_run_snapshot_freezes_validated_configuration(self):
        snapshot = create_run_snapshot(
            self.loaded,
            run_repository=self.repository,
            run_id=RUN_ID,
            software_version="test-runner",
            notes="现场调试",
            product_name="PA-100",
            now=CREATED_AT,
        )

        self.assertEqual(snapshot.run_id, RUN_ID)
        self.assertEqual(snapshot.run_directory, self.repository.root / RUN_ID)
        self.assertTrue(snapshot.config_path.is_file())
        self.assertEqual(snapshot.manifest.status, RunStatus.CREATED)
        self.assertEqual(snapshot.manifest.product_name, "PA-100")
        self.assertEqual(snapshot.manifest.notes, "现场调试")
        self.assertTrue(
            all(item.status is StageStatus.PENDING for item in snapshot.manifest.stages.values())
        )

        payload = json.loads(snapshot.config_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["run_id"], RUN_ID)
        self.assertEqual(payload["created_at"], "2026-10-22T15:30:00Z")
        self.assertEqual(payload["test_plan"]["frequencies"]["values"], [2.2, 2.4])
        self.assertEqual(payload["run_mapping"]["wiring"]["confirmed"], True)

    def test_invalid_configuration_cannot_create_a_runnable_snapshot(self):
        invalid = ConfigurationLoadResult(
            _valid_configuration(),
            ConfigValidationResult(
                errors=[ConfigIssue("error", "wiring.confirmed", "运行前必须确认现场接线")],
                warnings=[],
            ),
        )

        with self.assertRaisesRegex(ValueError, "wiring.confirmed"):
            create_run_snapshot(
                invalid,
                run_repository=self.repository,
                run_id=RUN_ID,
                now=CREATED_AT,
            )

        self.assertFalse((self.repository.root / RUN_ID).exists())
        self.assertEqual(self.repository.list_run_ids(), ())

    def test_stage_valid_but_incomplete_run_is_rejected(self):
        # 阶段专用校验通过（loaded.valid 为真），但完整运行配置不合法：
        # 缺少频率、DUT 通道、仪器映射和接线确认。
        stage_only = ConfigurationLoadResult(
            RunConfiguration(TestPlan(), RunResourceMapping()),
            ConfigValidationResult(errors=[], warnings=[]),
        )
        self.assertTrue(stage_only.valid)
        self.assertFalse(validate_run_configuration(stage_only.configuration).valid)

        with self.assertRaisesRegex(ValueError, "完整运行配置校验失败"):
            create_run_snapshot(
                stage_only,
                run_repository=self.repository,
                run_id=RUN_ID,
                now=CREATED_AT,
            )
        self.assertFalse((self.repository.root / RUN_ID).exists())

    def test_snapshot_is_immutable_after_gui_config_is_modified_and_saved(self):
        config_path = self.root / "config.json"
        config_path.write_text(json.dumps(_gui_config(start_power=-10.0)), encoding="utf-8")
        first = ConfigurationRepository().load_for_run(config_path)
        self.assertTrue(first.valid, first.validation.errors)
        create_run_snapshot(
            first,
            run_repository=self.repository,
            run_id=RUN_ID,
            now=CREATED_AT,
        )
        frozen = (self.repository.root / RUN_ID / "config.json").read_text(encoding="utf-8")

        # 模拟用户在 GUI 修改配置并“保存配置”。
        config_path.write_text(json.dumps(_gui_config(start_power=-20.0)), encoding="utf-8")
        second = ConfigurationRepository().load_for_run(config_path)
        self.assertTrue(second.valid, second.validation.errors)
        self.assertEqual(second.configuration.test_plan.start_power, -20.0)

        # 已创建的运行快照不受 GUI 配置修改影响。
        self.assertEqual(
            (self.repository.root / RUN_ID / "config.json").read_text(encoding="utf-8"),
            frozen,
        )
        restored = load_run_configuration(self.repository, RUN_ID)
        self.assertEqual(restored.configuration.test_plan.start_power, -10.0)

    def test_snapshot_can_be_loaded_and_revalidated_without_gui(self):
        create_run_snapshot(
            self.loaded,
            run_repository=self.repository,
            run_id=RUN_ID,
            now=CREATED_AT,
        )

        restored = load_run_configuration(self.repository, RUN_ID)
        self.assertTrue(restored.valid, restored.validation.errors)
        self.assertEqual(restored.configuration.test_plan.frequencies, [2.2, 2.4])
        self.assertEqual(restored.configuration.test_plan.start_power, -10.0)
        self.assertTrue(restored.configuration.run_mapping.wiring_confirmed)
        self.assertEqual(
            restored.configuration.run_mapping.dut_power_channels[0].connection, "gate"
        )

    def test_load_rejects_unsupported_snapshot_schema(self):
        self.repository.create_run(
            {"schema_version": 99, "test_plan": {}, "run_mapping": {}},
            run_id=RUN_ID,
            now=CREATED_AT,
        )
        with self.assertRaisesRegex(ValueError, "schema_version"):
            load_run_configuration(self.repository, RUN_ID)

    def test_manifest_records_the_config_snapshot_checksum(self):
        snapshot = create_run_snapshot(
            self.loaded,
            run_repository=self.repository,
            run_id=RUN_ID,
            now=CREATED_AT,
        )
        manifest = self.repository.load_manifest(RUN_ID)
        self.assertIsNotNone(manifest.config)
        self.assertEqual(manifest.config.path, "config.json")
        self.assertEqual(manifest.config.size_bytes, snapshot.config_path.stat().st_size)

    def test_hardware_config_is_frozen_and_registered(self):
        hardware = {
            "instruments": {"signal_generator": {"address": "sim::sg", "enabled": True}},
            "power_supply_assignment": {},
        }

        snapshot = create_run_snapshot(
            self.loaded,
            run_repository=self.repository,
            run_id=RUN_ID,
            hardware_config=hardware,
            now=CREATED_AT,
        )

        self.assertIsNotNone(snapshot.hardware_config_path)
        self.assertTrue(snapshot.hardware_config_path.is_file())
        self.assertEqual(
            json.loads(snapshot.hardware_config_path.read_text(encoding="utf-8")),
            hardware,
        )
        manifest = self.repository.load_manifest(RUN_ID)
        registered = {item.path: item for item in manifest.inputs}
        self.assertIn(HARDWARE_CONFIG_INPUT_PATH, registered)
        self.assertIsNotNone(registered[HARDWARE_CONFIG_INPUT_PATH].sha256)
        self.assertEqual(
            registered[HARDWARE_CONFIG_INPUT_PATH].size_bytes,
            snapshot.hardware_config_path.stat().st_size,
        )

    def test_hardware_config_write_failure_discards_run(self):
        class _FailingWriteRepository(RunRepository):
            def write_run_json(self, run_id, relative_path, payload, **kwargs):
                raise OSError("disk full")

        repository = _FailingWriteRepository(self.root / "runs")
        with self.assertRaisesRegex(OSError, "disk full"):
            create_run_snapshot(
                self.loaded,
                run_repository=repository,
                run_id=RUN_ID,
                hardware_config={"instruments": {}},
                now=CREATED_AT,
            )

        self.assertFalse((repository.root / RUN_ID).exists())
        self.assertEqual(repository.list_run_ids(), ())

    def test_hardware_config_write_failure_after_side_effects_discards_run(self):
        # 文件已真实落盘后才失败：补偿清理仍必须撤销整个运行目录，
        # 而不是只删掉硬件快照文件、留下“看似有效”的运行。
        class _WriteThenFailRepository(RunRepository):
            file_existed_at_failure = False

            def write_run_json(self, run_id, relative_path, payload, **kwargs):
                super().write_run_json(run_id, relative_path, payload, **kwargs)
                self.file_existed_at_failure = (
                    self.root / run_id / relative_path
                ).is_file()
                raise OSError("write failed after flushing")

        repository = _WriteThenFailRepository(self.root / "runs")
        with self.assertRaisesRegex(OSError, "write failed after flushing"):
            create_run_snapshot(
                self.loaded,
                run_repository=repository,
                run_id=RUN_ID,
                hardware_config={"instruments": {}},
                now=CREATED_AT,
            )

        self.assertTrue(repository.file_existed_at_failure)
        self.assertFalse((repository.root / RUN_ID).exists())
        self.assertEqual(repository.list_run_ids(), ())

    def test_hardware_config_register_failure_discards_run(self):
        class _FailingRegisterRepository(RunRepository):
            def register_file(self, run_id, path, **kwargs):
                raise OSError("register failed")

        repository = _FailingRegisterRepository(self.root / "runs")
        with self.assertRaisesRegex(OSError, "register failed"):
            create_run_snapshot(
                self.loaded,
                run_repository=repository,
                run_id=RUN_ID,
                hardware_config={"instruments": {}},
                now=CREATED_AT,
            )

        self.assertFalse((repository.root / RUN_ID).exists())
        self.assertEqual(repository.list_run_ids(), ())

    def test_hardware_config_manifest_failure_discards_run(self):
        class _FailingManifestRepository(RunRepository):
            def update_manifest(self, run_id, manifest, **kwargs):
                raise OSError("manifest write failed")

        repository = _FailingManifestRepository(self.root / "runs")
        with self.assertRaisesRegex(OSError, "manifest write failed"):
            create_run_snapshot(
                self.loaded,
                run_repository=repository,
                run_id=RUN_ID,
                hardware_config={"instruments": {}},
                now=CREATED_AT,
            )

        self.assertFalse((repository.root / RUN_ID).exists())
        self.assertEqual(repository.list_run_ids(), ())

    def test_hardware_config_manifest_failure_after_side_effects_discards_run(self):
        # manifest 已真实更新后才失败（例如登记后的收尾步骤抛错）：
        # 运行目录同样必须被整体撤销，不遗留登记了一半的 manifest。
        class _RegisterThenFailRepository(RunRepository):
            registered_at_failure = False

            def register_file(self, run_id, path, **kwargs):
                reference = super().register_file(run_id, path, **kwargs)
                self.registered_at_failure = (
                    self.load_manifest(run_id).inputs[-1].path == reference.path
                )
                raise OSError("failed after manifest update")

        repository = _RegisterThenFailRepository(self.root / "runs")
        with self.assertRaisesRegex(OSError, "failed after manifest update"):
            create_run_snapshot(
                self.loaded,
                run_repository=repository,
                run_id=RUN_ID,
                hardware_config={"instruments": {}},
                now=CREATED_AT,
            )

        self.assertTrue(repository.registered_at_failure)
        self.assertFalse((repository.root / RUN_ID).exists())
        self.assertEqual(repository.list_run_ids(), ())

    def test_original_creation_error_is_preserved(self):
        # 写入和补偿清理都失败时，调用方看到的仍然是原始创建错误，
        # 清理异常不允许覆盖它；清理失败上下文必须进入项目 logger 树，
        # 以便运维从 setup_logging 的日志文件中发现残留的半成品运行。
        class _FailingWriteAndCleanupRepository(RunRepository):
            def write_run_json(self, run_id, relative_path, payload, **kwargs):
                raise OSError("original write error")

            def discard_run(self, run_id):
                raise RuntimeError("cleanup also failed")

        repository = _FailingWriteAndCleanupRepository(self.root / "runs")
        with self.assertLogs("pa_auto_test", level="ERROR") as captured:
            with self.assertRaisesRegex(OSError, "original write error"):
                create_run_snapshot(
                    self.loaded,
                    run_repository=repository,
                    run_id=RUN_ID,
                    hardware_config={"instruments": {}},
                    now=CREATED_AT,
                )

        self.assertEqual(len(captured.records), 1)
        record = captured.records[0]
        self.assertEqual(record.levelname, "ERROR")
        # 清理失败日志挂在项目专用 logger 树下，携带运行 ID 与清理异常。
        self.assertTrue(record.name.startswith("pa_auto_test."))
        self.assertIn(RUN_ID, record.getMessage())
        self.assertIsNotNone(record.exc_info)
        self.assertIn("cleanup also failed", str(record.exc_info[1]))


class CreatePreparedRunTests(unittest.TestCase):
    def test_create_prepared_run_carries_the_new_run_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = RunRepository(Path(directory) / "runs")
            prepared = create_prepared_run(
                _loaded(_valid_configuration()),
                run_id=RUN_ID,
                software_version="test-runner",
                run_repository=repository,
            )
            self.assertEqual(prepared.run_id, RUN_ID)
            self.assertEqual(prepared.run_directory, repository.root / RUN_ID)
            self.assertTrue((prepared.run_directory / "config.json").is_file())
            self.assertEqual(repository.read_config(RUN_ID)["run_id"], RUN_ID)
            # 运行契约 PreparedRun 携带创建它的仓储，供步骤 4 之后的阶段记录使用。
            self.assertTrue(prepared.has_run_contract)
            self.assertIs(prepared.run_repository, repository)

    def test_create_prepared_run_does_not_use_the_legacy_snapshot_names(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = RunRepository(Path(directory) / "runs")
            prepared = create_prepared_run(
                _loaded(_valid_configuration()),
                run_id=RUN_ID,
                run_repository=repository,
            )
            self.assertFalse((prepared.run_directory / "test_plan_snapshot.json").exists())
            self.assertFalse((prepared.run_directory / "run_mapping_snapshot.json").exists())


if __name__ == "__main__":
    unittest.main()
