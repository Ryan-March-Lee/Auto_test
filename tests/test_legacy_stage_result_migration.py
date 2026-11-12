"""步骤 21：旧运行结果复制迁移工具。

覆盖：

- 旧运行目录中的完整线损/驱动映射结果被复制到阶段目录，补齐测量身份并保留来源运行；
- 迁移结果可通过阶段结果仓库重新加载，源运行目录（含 manifest）逐字节不变；
- 重复迁移幂等：不产生重复文件、不覆盖已有阶段结果；
- 目标已存在且内容不同记为冲突，绝不覆盖；
- 阶段引用指针、未完成/取消结果、损坏 JSON、旧单层 ``power_mapping``、类型错配、
  ``run_id`` 不一致和缺少保存时间的文档都不会被复制；
- 报告可序列化为 JSON，命令行工具写入报告并返回正确退出码。
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from application.dto import (
    CableLossMeasurementResult,
    CableLossRow,
    DriverMappingRawPoint,
    DriverMappingTargetPoint,
    DriverPowerMappingMeasurementResult,
    MeasurementStatus,
)
from application.ports.stage_result_repository import MeasurementStage
from application.run_stages import STAGE_RESULT_REFERENCE_SCHEMA
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence.legacy_stage_result_migration import (
    LEGACY_MIGRATION_REPORT_SCHEMA,
    LEGACY_STAGE_INPUTS,
    LegacyMigrationOutcome,
    LegacyStageResultMigration,
    legacy_migration_measurement_id,
)
from infrastructure.persistence.run_repository import RunRepository
from infrastructure.persistence.stage_result_repository import FileStageResultRepository
from tools.migrate_legacy_results import main as migrate_main

RUN_ID = "20260101T000000Z_aaaaaaaa"
OTHER_RUN_ID = "20260102T000000Z_bbbbbbbb"
SOURCE_INPUTS = {stage: relative for stage, relative in LEGACY_STAGE_INPUTS}


def _cable_loss_document(run_id, *, cable1=0.5, identity=None):
    result = CableLossMeasurementResult(
        run_id=run_id,
        measurement_id=identity,
        points=(
            CableLossRow(frequency_ghz="1.0", cable_losses={"cable1": cable1}),
        ),
    )
    document = result.to_dict()
    if identity is None:
        document.pop("measurement_id", None)
    return document


def _driver_mapping_document(run_id, *, output=-20.0, identity=None):
    result = DriverPowerMappingMeasurementResult(
        run_id=run_id,
        measurement_id=identity,
        raw_points=(
            DriverMappingRawPoint(
                frequency_ghz="1.0",
                source_power_dbm=-21.0,
                driver_output_power_dbm=output,
            ),
        ),
        target_points=(
            DriverMappingTargetPoint(
                frequency_ghz="1.0",
                target_input_power_dbm=-20.0,
                source_power_dbm=-21.0,
                driver_output_power_dbm=output,
            ),
        ),
    )
    document = result.to_dict()
    if identity is None:
        document.pop("measurement_id", None)
    return document


class LegacyMigrationTestCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = PathService(self.root)
        self.stages = FileStageResultRepository(paths=self.paths)
        self.runs = RunRepository(paths=self.paths)

    def _create_run(self, run_id=RUN_ID):
        layout, _manifest = self.runs.create_run({}, run_id=run_id)
        return layout

    def _write_legacy(self, stage, document, *, run_id=RUN_ID):
        layout = self.runs.layout(run_id)
        if not layout.run_directory.is_dir():
            self._create_run(run_id)
        target = layout.run_directory / Path(*SOURCE_INPUTS[stage].split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(document, indent=4, ensure_ascii=False), encoding="utf-8"
        )
        return target

    def _migration(self):
        return LegacyStageResultMigration(
            stage_results=self.stages, run_repository=self.runs
        )

    def _snapshot(self, directory):
        return {
            path.relative_to(directory).as_posix(): path.read_bytes()
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        }

    # -- 复制与可读取 ------------------------------------------------------

    def test_copies_cable_loss_result_and_supplements_identity(self):
        layout = self._create_run()
        source = self._write_legacy(
            MeasurementStage.CABLE_LOSS, _cable_loss_document(RUN_ID)
        )
        before = self._snapshot(layout.run_directory)

        report = self._migration().run()

        self.assertTrue(report.ok)
        self.assertEqual(report.counts()["migrated"], 1)
        entry = report.entries[0]
        self.assertEqual(entry.outcome, LegacyMigrationOutcome.MIGRATED)
        self.assertIsNotNone(entry.target_path)
        self.assertTrue(entry.target_path.is_file())
        self.assertEqual(
            entry.target_path.parent, self.paths.cable_loss_results_dir
        )
        loaded = self.stages.load(MeasurementStage.CABLE_LOSS, entry.target_path)
        self.assertEqual(loaded.result.run_id, RUN_ID)
        self.assertEqual(loaded.result.measurement_id, entry.measurement_id)
        self.assertTrue(
            loaded.result.measurement_id.startswith("legacy_cable_loss_")
        )
        document = json.loads(entry.target_path.read_text(encoding="utf-8"))
        self.assertEqual(document["measurement_id"], entry.measurement_id)
        self.assertEqual(document["cable_losses"], {"1.0": {"cable1": 0.5}})
        # 只复制：源运行目录（含 manifest 与输入文件）逐字节不变。
        self.assertEqual(self._snapshot(layout.run_directory), before)
        self.assertEqual(source.read_bytes(), before["inputs/cable_loss.json"])

    def test_copies_driver_mapping_result_with_target_grid(self):
        layout = self._create_run()
        self._write_legacy(
            MeasurementStage.DRIVER_MAPPING, _driver_mapping_document(RUN_ID)
        )
        before = self._snapshot(layout.run_directory)

        report = self._migration().run()

        entry = report.entries[0]
        self.assertEqual(entry.outcome, LegacyMigrationOutcome.MIGRATED)
        loaded = self.stages.load(
            MeasurementStage.DRIVER_MAPPING, entry.target_path
        )
        self.assertEqual(loaded.result.measurement_id, entry.measurement_id)
        self.assertEqual(len(loaded.result.raw_points), 1)
        self.assertEqual(
            [point.target_input_power_dbm for point in loaded.result.target_points],
            [-20.0],
        )
        document = json.loads(entry.target_path.read_text(encoding="utf-8"))
        self.assertEqual(document["schema_version"], "2.0")
        self.assertEqual(self._snapshot(layout.run_directory), before)

    def test_repeated_migration_is_idempotent(self):
        self._create_run()
        self._write_legacy(
            MeasurementStage.CABLE_LOSS, _cable_loss_document(RUN_ID)
        )

        first = self._migration().run()
        target = first.entries[0].target_path
        first_bytes = target.read_bytes()

        second = self._migration().run()

        entry = second.entries[0]
        self.assertEqual(entry.outcome, LegacyMigrationOutcome.ALREADY_MIGRATED)
        self.assertEqual(entry.target_path, target)
        self.assertEqual(target.read_bytes(), first_bytes)
        self.assertEqual(
            len(self.stages.list_results(MeasurementStage.CABLE_LOSS)), 1
        )
        self.assertTrue(second.ok)

    def test_existing_identity_is_reused(self):
        identity = "legacy_cable_loss_manualidentity"
        self._create_run()
        self._write_legacy(
            MeasurementStage.CABLE_LOSS,
            _cable_loss_document(RUN_ID, identity=identity),
        )

        report = self._migration().run()

        entry = report.entries[0]
        self.assertEqual(entry.measurement_id, identity)
        self.assertEqual(entry.target_path.name, f"{identity}.json")

    def test_conflicting_target_is_not_overwritten(self):
        identity = "legacy_cable_loss_manualidentity"
        self._create_run()
        self._write_legacy(
            MeasurementStage.CABLE_LOSS,
            _cable_loss_document(RUN_ID, cable1=0.5, identity=identity),
        )
        first = self._migration().run()
        target = first.entries[0].target_path
        original = target.read_bytes()

        self._create_run(OTHER_RUN_ID)
        self._write_legacy(
            MeasurementStage.CABLE_LOSS,
            _cable_loss_document(OTHER_RUN_ID, cable1=9.0, identity=identity),
            run_id=OTHER_RUN_ID,
        )
        second = self._migration().run()

        conflict = [
            entry
            for entry in second.entries
            if entry.outcome is LegacyMigrationOutcome.CONFLICT
        ]
        self.assertEqual(len(conflict), 1)
        self.assertEqual(conflict[0].target_path, target)
        self.assertFalse(second.ok)
        self.assertEqual(target.read_bytes(), original)

    # -- 不迁移的文档 ------------------------------------------------------

    def test_reference_pointer_is_skipped(self):
        self._create_run()
        self._write_legacy(
            MeasurementStage.CABLE_LOSS,
            {
                "schema": STAGE_RESULT_REFERENCE_SCHEMA,
                "schema_version": 1,
                "stage": "cable_loss",
                "measurement_id": "20260101T000000Z_abcdef12",
            },
        )

        report = self._migration().run()

        self.assertEqual(report.entries[0].outcome, LegacyMigrationOutcome.SKIPPED)
        self.assertFalse(self.paths.cable_loss_results_dir.exists())

    def test_non_completed_result_is_skipped(self):
        self._create_run()
        document = _cable_loss_document(RUN_ID)
        document["status"] = MeasurementStatus.CANCELLED.value
        document["termination"] = {"intent": "stop", "reason": "用户停止"}
        self._write_legacy(MeasurementStage.CABLE_LOSS, document)

        report = self._migration().run()

        self.assertEqual(report.entries[0].outcome, LegacyMigrationOutcome.SKIPPED)
        self.assertFalse(self.paths.cable_loss_results_dir.exists())

    def test_corrupt_and_non_formal_documents_fail_without_writing(self):
        self._create_run()
        source = self.runs.layout(RUN_ID).run_directory / "inputs" / "cable_loss.json"
        source.write_text("{not json", encoding="utf-8")
        self._write_legacy(
            MeasurementStage.DRIVER_MAPPING,
            {
                "run_id": RUN_ID,
                "measurement_type": "driver_power_mapping",
                "status": "completed",
                "power_mapping": {"1.0": {"-21": -20.0}},
            },
        )

        report = self._migration().run()

        self.assertEqual(
            [entry.outcome for entry in report.entries],
            [LegacyMigrationOutcome.FAILED, LegacyMigrationOutcome.FAILED],
        )
        self.assertFalse(report.ok)
        self.assertFalse(self.paths.cable_loss_results_dir.exists())
        self.assertFalse(self.paths.driver_mapping_results_dir.exists())

    def test_type_mismatch_fails_without_writing(self):
        self._create_run()
        self._write_legacy(
            MeasurementStage.CABLE_LOSS, _driver_mapping_document(RUN_ID)
        )

        report = self._migration().run()

        self.assertEqual(report.entries[0].outcome, LegacyMigrationOutcome.FAILED)
        self.assertIn("类型与阶段不符", report.entries[0].detail)
        self.assertFalse(self.paths.cable_loss_results_dir.exists())

    def test_run_id_mismatch_fails_without_writing(self):
        self._create_run()
        self._write_legacy(
            MeasurementStage.CABLE_LOSS, _cable_loss_document(OTHER_RUN_ID)
        )

        report = self._migration().run()

        self.assertEqual(report.entries[0].outcome, LegacyMigrationOutcome.FAILED)
        self.assertIn("不一致", report.entries[0].detail)
        self.assertFalse(self.paths.cable_loss_results_dir.exists())

    def test_missing_created_at_fails_without_writing(self):
        self._create_run()
        document = _cable_loss_document(RUN_ID)
        document.pop("created_at")
        self._write_legacy(MeasurementStage.CABLE_LOSS, document)

        report = self._migration().run()

        self.assertEqual(report.entries[0].outcome, LegacyMigrationOutcome.FAILED)
        self.assertIn("created_at", report.entries[0].detail)
        self.assertFalse(self.paths.cable_loss_results_dir.exists())

    def test_symlinked_source_is_refused_without_reading_outside_the_run(self):
        self._create_run()
        outside = self.root / "outside-result.json"
        outside.write_text(
            json.dumps(_cable_loss_document(RUN_ID), ensure_ascii=False),
            encoding="utf-8",
        )
        source = self.runs.layout(RUN_ID).run_directory / "inputs" / "cable_loss.json"
        source.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(outside, source)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"当前环境不支持创建符号链接: {error}")

        report = self._migration().run()

        self.assertEqual(report.entries[0].outcome, LegacyMigrationOutcome.FAILED)
        self.assertIn("符号链接", report.entries[0].detail)
        self.assertFalse(self.paths.cable_loss_results_dir.exists())
        self.assertEqual(
            json.loads(outside.read_text(encoding="utf-8"))["run_id"], RUN_ID
        )

    def test_runs_without_legacy_inputs_produce_no_entries(self):
        self._create_run()

        report = self._migration().run()

        self.assertEqual(report.entries, ())
        self.assertTrue(report.ok)
        self.assertEqual(sum(report.counts().values()), 0)

    # -- 报告与命令行 ------------------------------------------------------

    def test_report_is_json_serializable(self):
        self._create_run()
        self._write_legacy(
            MeasurementStage.CABLE_LOSS, _cable_loss_document(RUN_ID)
        )
        report = self._migration().run()

        document = report.to_dict()
        self.assertEqual(document["schema"], LEGACY_MIGRATION_REPORT_SCHEMA)
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["counts"]["migrated"], 1)
        self.assertTrue(document["ok"])
        json.dumps(document)

    def test_legacy_identity_is_stable_and_content_sensitive(self):
        first = legacy_migration_measurement_id(
            MeasurementStage.CABLE_LOSS, RUN_ID, "a" * 64
        )
        again = legacy_migration_measurement_id(
            MeasurementStage.CABLE_LOSS, RUN_ID, "a" * 64
        )
        changed = legacy_migration_measurement_id(
            MeasurementStage.CABLE_LOSS, RUN_ID, "b" * 64
        )

        self.assertEqual(first, again)
        self.assertNotEqual(first, changed)
        self.assertTrue(first.startswith("legacy_cable_loss_"))

    def test_command_line_writes_report_and_is_idempotent(self):
        self._create_run()
        self._write_legacy(
            MeasurementStage.CABLE_LOSS, _cable_loss_document(RUN_ID)
        )
        report_path = self.root / "migration-report.json"

        stream = io.StringIO()
        with redirect_stdout(stream):
            exit_code = migrate_main(
                ["--data-root", str(self.root), "--report", str(report_path)]
            )

        self.assertEqual(exit_code, 0)
        document = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(document["counts"]["migrated"], 1)
        self.assertIn("迁移完成", stream.getvalue())

        with redirect_stdout(io.StringIO()):
            again = migrate_main(
                ["--data-root", str(self.root), "--report", str(report_path)]
            )

        self.assertEqual(again, 0)
        document = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(document["counts"]["already_migrated"], 1)
        self.assertEqual(
            len(self.stages.list_results(MeasurementStage.CABLE_LOSS)), 1
        )

    def test_command_line_returns_environment_error_when_report_cannot_be_written(self):
        self._create_run()
        report_path = self.root / "migration-report.json"

        with patch(
            "tools.migrate_legacy_results.write_json_atomic",
            side_effect=OSError("disk is read-only"),
        ), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as errors:
            exit_code = migrate_main(
                ["--data-root", str(self.root), "--report", str(report_path)]
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("disk is read-only", errors.getvalue())
        self.assertFalse(report_path.exists())


if __name__ == "__main__":
    unittest.main()
