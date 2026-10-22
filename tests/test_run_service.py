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
from domain.run.models import RunStatus, StageStatus
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
