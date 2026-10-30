"""步骤 3 测试：把配置保存改为创建运行快照。"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.run_context import create_prepared_run, load_prepared_run, prepare_run
from application.ports.config_repository import ConfigurationLoadResult
from application.run_service import (
    RunNotResumable,
    RunSnapshot,
    create_run_snapshot,
    find_run_hardware_config,
    load_hardware_config,
    load_run_configuration,
    inspect_run,
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
from domain.run.models import (
    HARDWARE_CONFIG_INPUT_PATH,
    RunStatus,
    StageStatus,
    default_manifest,
)
from infrastructure.config.json_config_repository import ConfigurationRepository
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence.run_repository import RunRepository
from presentation.qt.configuration_authoring import author_official_configuration


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
    )
    return RunConfiguration(test_plan=plan, run_mapping=mapping)


def _loaded(configuration: RunConfiguration) -> ConfigurationLoadResult:
    return ConfigurationLoadResult(configuration, validate_run_configuration(configuration))


def _gui_config(*, start_power: float = -10.0) -> dict:
    """GUI 保存的组合配置（含正式 test_plan / run_mapping 段）。"""
    return author_official_configuration({
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
                "DP8B1519": {
                    "label": "DP8B1519",
                    "model": "DP832A",
                    "serial": "DP8B214901519",
                    "device_key": "rigol technologies:dp8b214901519",
                    "address": "sim::ps1",
                    "enabled": True,
                    "channels": {
                        "CH1": {
                            "role": "gate",
                            "voltage": {"value": 2.8},
                            "current": {"value": 0.1},
                        },
                        "CH2": {
                            "role": "drain",
                            "voltage": {"value": 28.0},
                            "current": {"value": 2.0},
                        },
                    },
                }
            },
        },
        "power_supply_assignment": {
            "driver_amplifier": {"power_supply_count": 0, "supplies": {}},
            "dut_amplifier": {
                "power_supply_count": 1,
                "supplies": {
                    "carrier": {
                        "supply_id": "DP8B1519",
                        "name": "DP8B1519",
                        "channel": ["CH1", "CH2"],
                    }
                },
            },
        },
    })


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
            hardware_config=_gui_config(),
            now=CREATED_AT,
        )

        self.assertEqual(snapshot.run_id, RUN_ID)
        self.assertEqual(snapshot.run_directory, self.repository.root / RUN_ID)
        self.assertTrue(snapshot.config_path.is_file())
        # 运行契约快照必定携带冻结的硬件配置文件。
        self.assertEqual(
            snapshot.hardware_config_path,
            snapshot.run_directory / HARDWARE_CONFIG_INPUT_PATH,
        )
        self.assertTrue(snapshot.hardware_config_path.is_file())
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
        # 现场接线不再是配置字段：快照里只冻结参数与仪器映射。
        self.assertNotIn("wiring", payload["run_mapping"])

    def test_invalid_configuration_cannot_create_a_runnable_snapshot(self):
        invalid = ConfigurationLoadResult(
            _valid_configuration(),
            ConfigValidationResult(
                errors=[ConfigIssue("error", "frequencies.values", "必须是非空列表")],
                warnings=[],
            ),
        )

        with self.assertRaisesRegex(ValueError, "frequencies.values"):
            create_run_snapshot(
                invalid,
                run_repository=self.repository,
                run_id=RUN_ID,
                hardware_config=_gui_config(),
                now=CREATED_AT,
            )

        self.assertFalse((self.repository.root / RUN_ID).exists())
        self.assertEqual(self.repository.list_run_ids(), ())

    def test_stage_valid_but_incomplete_run_is_rejected(self):
        # 阶段专用校验通过（loaded.valid 为真），但完整运行配置不合法：
        # 缺少频率、DUT 通道和仪器映射。
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
                hardware_config=_gui_config(),
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
            hardware_config=_gui_config(start_power=-10.0),
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
            hardware_config=_gui_config(),
            now=CREATED_AT,
        )

        restored = load_run_configuration(self.repository, RUN_ID)
        self.assertTrue(restored.valid, restored.validation.errors)
        self.assertEqual(restored.configuration.test_plan.frequencies, [2.2, 2.4])
        self.assertEqual(restored.configuration.test_plan.start_power, -10.0)
        self.assertEqual(
            restored.configuration.run_mapping.instruments["signal_generator"].visa_address,
            "sim::sg",
        )
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
            hardware_config=_gui_config(),
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

    def test_create_run_snapshot_requires_hardware_config(self):
        # 缺少 hardware_config 的调用必须立即失败：
        # - 显式传入 None 在入口被拒绝（签名缺省会直接 TypeError）；
        # - 两种失败都不允许创建任何运行目录。
        with self.assertRaisesRegex(ValueError, "hardware_config"):
            create_run_snapshot(
                self.loaded,
                run_repository=self.repository,
                run_id=RUN_ID,
                hardware_config=None,
                now=CREATED_AT,
            )
        with self.assertRaises(TypeError):
            create_run_snapshot(
                self.loaded,
                run_repository=self.repository,
                run_id=RUN_ID,
                now=CREATED_AT,
            )

        self.assertFalse((self.repository.root / RUN_ID).exists())
        self.assertEqual(self.repository.list_run_ids(), ())

    def test_successful_run_snapshot_is_resumable(self):
        # 成功创建的运行一定登记 inputs/hardware_config.json，
        # 并且可以被 load_prepared_run() 按当前恢复规则重新加载。
        snapshot = create_run_snapshot(
            self.loaded,
            run_repository=self.repository,
            run_id=RUN_ID,
            hardware_config=_gui_config(),
            now=CREATED_AT,
        )
        manifest = self.repository.load_manifest(RUN_ID)
        registered = {item.path for item in manifest.inputs}
        self.assertIn(HARDWARE_CONFIG_INPUT_PATH, registered)
        self.assertTrue(snapshot.hardware_config_path.is_file())

        reopened = load_prepared_run(RUN_ID, run_repository=self.repository)
        self.assertTrue(reopened.has_run_contract)
        self.assertEqual(reopened.run_id, RUN_ID)
        self.assertEqual(reopened.run_directory, snapshot.run_directory)
        self.assertEqual(reopened.hardware_config_path, snapshot.hardware_config_path)
        self.assertEqual(
            reopened.hardware_connection_path(), snapshot.hardware_config_path
        )
        # 重新加载得到的内容副本与冻结写入的硬件配置一致。
        self.assertEqual(reopened.hardware_config, _gui_config())

    def test_non_mapping_hardware_config_is_rejected_without_creating_a_run(self):
        # 非法参数（非映射）在创建运行目录之前就被拒绝，不依赖补偿清理。
        with self.assertRaisesRegex(ValueError, "配置映射"):
            create_run_snapshot(
                self.loaded,
                run_repository=self.repository,
                run_id=RUN_ID,
                hardware_config=["not", "a", "mapping"],
                now=CREATED_AT,
            )

        self.assertFalse((self.repository.root / RUN_ID).exists())
        self.assertEqual(self.repository.list_run_ids(), ())

    def test_run_snapshot_rejects_empty_hardware_config_path(self):
        # RunSnapshot 的运行时契约：即便显式传入 None 也不能构造出
        # 缺少硬件配置路径的运行契约快照。
        manifest = default_manifest(RUN_ID, created_at="2026-10-22T15:30:00Z")
        with self.assertRaisesRegex(ValueError, "hardware_config_path"):
            RunSnapshot(
                run_id=RUN_ID,
                run_directory=Path("runs") / RUN_ID,
                config_path=Path("runs") / RUN_ID / "config.json",
                manifest=manifest,
                hardware_config_path=None,
            )

    def test_legacy_prepare_run_remains_non_contract_compatible(self):
        # 旧版 prepare_run 保留为兼容快照：它写兼容目录、不冻结硬件配置，
        # 也不属于可恢复运行契约，load_prepared_run 无法按 run_id 打开它。
        run_id = "20261022T153000Z_legacy0"
        prepared = prepare_run(
            self.loaded,
            run_id=run_id,
            software_version="test-runner",
            config_path=self.root / "config.json",
            paths=PathService(self.root),
        )

        self.assertFalse(prepared.has_run_contract)
        self.assertIsNone(prepared.run_repository)
        self.assertIsNone(prepared.hardware_config_path)
        # 旧兼容快照的硬件连接回退到创建时的源配置路径。
        self.assertEqual(
            prepared.hardware_connection_path(), self.root / "config.json"
        )
        # 它没有创建 runs/<run_id>/ 运行契约目录，因此不能重新加载。
        with self.assertRaises(FileNotFoundError):
            load_prepared_run(run_id, run_repository=self.repository)


SECOND_RUN_ID = "20261022T160000Z_b2c3d4e5"


class RunSnapshotIsolationTests(unittest.TestCase):
    """步骤 G：运行之间完全隔离，快照篡改与全局配置变化不相互串扰。"""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.repository = RunRepository(self.root / "runs")

    def _hardware_config(self, *, start_power: float, address: str) -> dict:
        hardware = _gui_config(start_power=start_power)
        hardware["instruments"]["signal_generator"]["address"] = address
        return hardware

    def _create(self, run_id: str, *, start_power: float, address: str) -> RunSnapshot:
        return create_run_snapshot(
            _loaded(_valid_configuration(start_power=start_power)),
            run_repository=self.repository,
            run_id=run_id,
            hardware_config=self._hardware_config(start_power=start_power, address=address),
            now=CREATED_AT,
        )

    def test_offline_inspection_accepts_created_run(self):
        self._create(RUN_ID, start_power=-10.0, address="sim::sg-a")
        before = (self.repository.root / RUN_ID / "manifest.json").read_bytes()
        result = inspect_run(self.repository, RUN_ID)
        self.assertTrue(result.resumable, result.reason)
        self.assertEqual(result.manifest.status, RunStatus.CREATED)
        self.assertEqual((self.repository.root / RUN_ID / "manifest.json").read_bytes(), before)

    def test_offline_inspection_rejects_running_run_without_mutation(self):
        self._create(RUN_ID, start_power=-10.0, address="sim::sg-a")
        manifest = self.repository.load_manifest(RUN_ID).with_status(RunStatus.RUNNING)
        self.repository.update_manifest(RUN_ID, manifest)
        before = (self.repository.root / RUN_ID / "manifest.json").read_bytes()
        result = inspect_run(self.repository, RUN_ID)
        self.assertFalse(result.resumable)
        self.assertIn("running", result.reason)
        self.assertEqual((self.repository.root / RUN_ID / "manifest.json").read_bytes(), before)

    def test_offline_inspection_reports_tampered_hardware_snapshot(self):
        snapshot = self._create(RUN_ID, start_power=-10.0, address="sim::sg-a")
        snapshot.hardware_config_path.write_text("{}", encoding="utf-8")
        result = inspect_run(self.repository, RUN_ID, verify_files=True)
        self.assertFalse(result.resumable)
        self.assertTrue(any(not item.valid for item in result.files))

    def test_offline_inspection_reports_tampered_domain_snapshot(self):
        snapshot = self._create(RUN_ID, start_power=-10.0, address="sim::sg-a")
        snapshot.config_path.write_text("{}", encoding="utf-8")
        result = inspect_run(self.repository, RUN_ID)
        self.assertFalse(result.resumable)
        self.assertTrue(any(item.path == "config.json" and not item.valid for item in result.files))

    def test_two_contract_runs_keep_independent_snapshots(self):
        first = self._create(RUN_ID, start_power=-10.0, address="sim::sg-a")
        second = self._create(SECOND_RUN_ID, start_power=-20.0, address="sim::sg-b")

        # 两个运行都被登记且目录互不覆盖。
        self.assertEqual(self.repository.list_run_ids(), (RUN_ID, SECOND_RUN_ID))
        self.assertNotEqual(first.run_directory, second.run_directory)

        # 领域配置快照各自独立。
        first_domain = load_run_configuration(self.repository, RUN_ID)
        second_domain = load_run_configuration(self.repository, SECOND_RUN_ID)
        self.assertEqual(first_domain.configuration.test_plan.start_power, -10.0)
        self.assertEqual(second_domain.configuration.test_plan.start_power, -20.0)
        self.assertEqual(first_domain.configuration.test_plan.frequencies, [2.2, 2.4])

        # 硬件装配快照各自独立，不会串用另一个运行的仪器地址。
        first_hardware = load_hardware_config(self.repository, RUN_ID)
        second_hardware = load_hardware_config(self.repository, SECOND_RUN_ID)
        self.assertEqual(
            first_hardware["instruments"]["signal_generator"]["address"],
            "sim::sg-a",
        )
        self.assertEqual(
            second_hardware["instruments"]["signal_generator"]["address"],
            "sim::sg-b",
        )

        second_config_before = second.config_path.read_bytes()
        second_hardware_before = second.hardware_config_path.read_bytes()
        second_manifest_before = (second.run_directory / "manifest.json").read_bytes()

        # 修改运行 A 的快照文件不会改变运行 B 自己的快照、manifest 或可恢复性。
        first_config_payload = json.loads(first.config_path.read_text(encoding="utf-8"))
        first_config_payload["test_plan"]["signal_source"]["start_power"] = -99.0
        first.config_path.write_text(json.dumps(first_config_payload), encoding="utf-8")
        first_hardware_payload = json.loads(
            first.hardware_config_path.read_text(encoding="utf-8")
        )
        first_hardware_payload["instruments"]["signal_generator"]["address"] = "sim::tampered"
        first.hardware_config_path.write_text(
            json.dumps(first_hardware_payload), encoding="utf-8"
        )

        self.assertEqual(second.config_path.read_bytes(), second_config_before)
        self.assertEqual(second.hardware_config_path.read_bytes(), second_hardware_before)
        self.assertEqual(
            (second.run_directory / "manifest.json").read_bytes(), second_manifest_before
        )
        reopened_second = load_prepared_run(
            SECOND_RUN_ID, run_repository=self.repository
        )
        self.assertEqual(reopened_second.configuration.test_plan.start_power, -20.0)
        self.assertEqual(
            reopened_second.hardware_config["instruments"]["signal_generator"]["address"],
            "sim::sg-b",
        )

        # 撤销运行 A 后，运行 B 仍独立存在并可恢复。
        self.repository.discard_run(RUN_ID)
        self.assertEqual(self.repository.list_run_ids(), (SECOND_RUN_ID,))
        self.assertFalse(first.run_directory.exists())
        self.assertTrue(second.run_directory.exists())
        self.assertEqual(
            load_run_configuration(self.repository, SECOND_RUN_ID)
            .configuration.test_plan.start_power,
            -20.0,
        )

    def test_global_config_change_does_not_affect_created_run(self):
        global_config_path = self.root / "config.json"
        source = self._hardware_config(start_power=-10.0, address="sim::sg-frozen")
        global_config_path.write_text(json.dumps(source), encoding="utf-8")
        loaded = ConfigurationRepository().load_for_run(global_config_path)
        self.assertTrue(loaded.valid, loaded.validation.errors)
        snapshot = create_run_snapshot(
            loaded,
            run_repository=self.repository,
            run_id=RUN_ID,
            hardware_config=source,
            now=CREATED_AT,
        )
        frozen_domain = snapshot.config_path.read_text(encoding="utf-8")
        frozen_hardware = snapshot.hardware_config_path.read_text(encoding="utf-8")

        # 修改磁盘上的全局兼容配置，再按 run_id 恢复时必须只使用运行目录快照。
        changed_global_config = self._hardware_config(
            start_power=-20.0, address="sim::sg-changed"
        )
        changed_global_config["test_frequencies"] = [9.9]
        global_config_path.write_text(json.dumps(changed_global_config), encoding="utf-8")
        reopened = load_prepared_run(
            RUN_ID,
            config_path=global_config_path,
            run_repository=self.repository,
        )

        self.assertEqual(reopened.configuration.test_plan.start_power, -10.0)
        self.assertEqual(reopened.hardware_config["test_frequencies"], [2.4])
        self.assertEqual(
            reopened.hardware_config["instruments"]["signal_generator"]["address"],
            "sim::sg-frozen",
        )
        self.assertEqual(reopened.hardware_connection_path(), snapshot.hardware_config_path)
        self.assertEqual(
            load_hardware_config(self.repository, RUN_ID)["instruments"]["signal_generator"]["address"],
            "sim::sg-frozen",
        )
        self.assertEqual(snapshot.config_path.read_text(encoding="utf-8"), frozen_domain)
        self.assertEqual(
            snapshot.hardware_config_path.read_text(encoding="utf-8"), frozen_hardware
        )

    def test_tampered_hardware_snapshot_is_rejected(self):
        snapshot = self._create(RUN_ID, start_power=-10.0, address="sim::sg-a")
        frozen = snapshot.hardware_config_path
        payload = json.loads(frozen.read_text(encoding="utf-8"))
        payload["test_frequencies"] = [9.9]
        # 只改文件、不更新 manifest：SHA-256 不再匹配。
        frozen.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaises(RunNotResumable):
            load_hardware_config(self.repository, RUN_ID)
        with self.assertRaises(RunNotResumable):
            find_run_hardware_config(self.repository, RUN_ID)
        with self.assertRaisesRegex(RunNotResumable, "硬件配置快照"):
            load_prepared_run(RUN_ID, run_repository=self.repository)

    def test_tampered_domain_snapshot_is_rejected(self):
        snapshot = self._create(RUN_ID, start_power=-10.0, address="sim::sg-a")
        domain_snapshot = snapshot.run_directory / "config.json"
        payload = json.loads(domain_snapshot.read_text(encoding="utf-8"))
        payload["test_plan"]["signal_source"]["start_power"] = -99.0
        domain_snapshot.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "SHA-256"):
            load_run_configuration(self.repository, RUN_ID)
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            load_prepared_run(RUN_ID, run_repository=self.repository)


class CreatePreparedRunTests(unittest.TestCase):
    def test_create_prepared_run_carries_the_new_run_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = RunRepository(Path(directory) / "runs")
            prepared = create_prepared_run(
                _loaded(_valid_configuration()),
                run_id=RUN_ID,
                software_version="test-runner",
                run_repository=repository,
                hardware_config=_gui_config(),
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
                hardware_config=_gui_config(),
            )
            self.assertFalse((prepared.run_directory / "test_plan_snapshot.json").exists())
            self.assertFalse((prepared.run_directory / "run_mapping_snapshot.json").exists())

    def test_create_prepared_run_rejects_power_connection_conflicts(self):
        """供电连接冲突（步骤 11）必须发生在写任何运行目录之前。"""
        conflicting = _gui_config()
        # 两台会被连接的电源抢同一个 VISA 地址：正式 run_mapping 只看冻结快照里的条目，
        # 但硬件装配读的是这一段，因此必须在创建运行前失败。
        conflicting["instruments"]["power_supplies"]["DP8B0885"] = {
            "label": "DP8B0885",
            "model": "DP832A",
            "serial": "DP8B174400885",
            "device_key": "rigol technologies:dp8b174400885",
            "address": "sim::ps1",
            "enabled": True,
            "channels": {"CH1": {"voltage": {"value": 5.0}, "current": {"value": 0.5}}},
        }
        with tempfile.TemporaryDirectory() as directory:
            repository = RunRepository(Path(directory) / "runs")
            with self.assertRaisesRegex(ValueError, "共用同一 VISA 地址"):
                create_prepared_run(
                    _loaded(_valid_configuration()),
                    run_id=RUN_ID,
                    run_repository=repository,
                    hardware_config=conflicting,
                )
            self.assertEqual(repository.list_run_ids(), ())

    def test_create_prepared_run_does_not_treat_missing_field_roles_as_a_conflict(self):
        """门禁只拦连接冲突：通道没声明供电角色时不得被拒成“连接冲突”。"""
        incomplete = _gui_config()
        for supply in incomplete["instruments"]["power_supplies"].values():
            for channel in supply.get("channels", {}).values():
                channel.pop("role", None)
        with tempfile.TemporaryDirectory() as directory:
            repository = RunRepository(Path(directory) / "runs")
            prepared = create_prepared_run(
                _loaded(_valid_configuration()),
                run_id=RUN_ID,
                run_repository=repository,
                hardware_config=incomplete,
            )
            self.assertTrue((prepared.run_directory / "config.json").is_file())

    def test_create_prepared_run_requires_hardware_config(self):
        # 运行契约入口同样拒绝缺少硬件配置的创建请求，且不产生运行目录。
        with tempfile.TemporaryDirectory() as directory:
            repository = RunRepository(Path(directory) / "runs")
            with self.assertRaisesRegex(ValueError, "hardware_config"):
                create_prepared_run(
                    _loaded(_valid_configuration()),
                    run_id=RUN_ID,
                    run_repository=repository,
                    hardware_config=None,
                )
            with self.assertRaises(TypeError):
                create_prepared_run(
                    _loaded(_valid_configuration()),
                    run_id=RUN_ID,
                    run_repository=repository,
                )
            self.assertEqual(repository.list_run_ids(), ())


if __name__ == "__main__":
    unittest.main()
