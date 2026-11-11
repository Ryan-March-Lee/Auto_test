"""步骤 5、6：阶段结果原子保存与显式加载适配器。

覆盖：

- 三类正式结果分别落入 ``test_results/<stage>/``，目录按需创建；
- 文件名由测量身份构造，返回的引用路径、摘要和大小与实际文件一致；
- 已有目标、损坏/非正式 DTO、阶段类型错配与非法身份都在写盘前失败；
- 序列化、替换等写入失败不留下半份结果或临时文件；
- 引用摘要/大小在发布前从暂存文件读取，发布后不再触碰文件系统；
- 独占创建在“先检查后写入”的竞态下仍然拒绝覆盖；
- 显式路径加载按“阶段目录边界 → 文件存在 → JSON → 正式 DTO → 类型/状态/身份”
  校验，空路径、越界、损坏、错误类型、未完成和旧结果缺身份都被拒绝；
- 越界路径即使文件名非法也先报告阶段目录边界错误；
- 摘要/大小与解析使用同一份字节，重复加载稳定，文件变化后摘要随之变化；
- 列举跳过损坏/未完成/类型不匹配文件，只返回 ``completed`` 结果并按保存时间倒序；
- 极端但可解析的保存时间不会让列举失败，而是稳定排在末尾。
"""

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from application.dto import (
    AmplifierMeasurementResult,
    CableLossMeasurementResult,
    DriverPowerMappingMeasurementResult,
    MeasurementResult,
    MeasurementStatus,
    generate_measurement_id,
    measurement_result_from_document,
)
from application.ports.stage_result_repository import (
    MeasurementStage,
    StageResultError,
)
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence import stage_result_repository
from infrastructure.persistence.stage_result_repository import FileStageResultRepository


RUN_ID = "20261111T010203Z_a1b2c3d4"
MOMENT = datetime(2026, 11, 11, 1, 2, 3, tzinfo=timezone.utc)


def measurement_id(token: str) -> str:
    return generate_measurement_id(MOMENT, token=token)


CABLE_ID = measurement_id("a1b2c3d4")
DRIVER_ID = measurement_id("ffee0011")
AMPLIFIER_ID = measurement_id("0badcafe")


def cable_loss_result(identity=CABLE_ID, **overrides):
    values = {"run_id": RUN_ID, "measurement_id": identity}
    values.update(overrides)
    return CableLossMeasurementResult(**values)


def driver_mapping_result(identity=DRIVER_ID, **overrides):
    values = {"run_id": RUN_ID, "measurement_id": identity}
    values.update(overrides)
    return DriverPowerMappingMeasurementResult(**values)


def amplifier_result(identity=AMPLIFIER_ID, **overrides):
    values = {"run_id": RUN_ID, "measurement_id": identity}
    values.update(overrides)
    return AmplifierMeasurementResult(**values)


class StageResultSaveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = PathService(self.root)
        self.repository = FileStageResultRepository(paths=self.paths)

    def assert_reference_matches_file(self, reference):
        path = reference.path
        self.assertTrue(path.is_file(), path)
        self.assertEqual(reference.sha256, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(reference.size_bytes, path.stat().st_size)
        self.assertEqual(path.name, f"{reference.measurement_id}.json")

    def test_each_stage_writes_to_its_own_directory(self):
        cases = (
            (
                MeasurementStage.CABLE_LOSS,
                cable_loss_result(),
                self.paths.cable_loss_results_dir,
            ),
            (
                MeasurementStage.DRIVER_MAPPING,
                driver_mapping_result(),
                self.paths.driver_mapping_results_dir,
            ),
            (
                MeasurementStage.AMPLIFIER,
                amplifier_result(),
                self.paths.amplifier_results_dir,
            ),
        )

        for stage, result, expected_directory in cases:
            with self.subTest(stage=stage.value):
                reference = self.repository.save(stage, result)

                self.assertIs(reference.stage, stage)
                self.assertEqual(reference.measurement_id, result.measurement_id)
                self.assertEqual(reference.path.parent, expected_directory)
                self.assertEqual(
                    reference.path.name, f"{result.measurement_id}.json"
                )
                self.assert_reference_matches_file(reference)

    def test_stage_directory_is_created_on_demand(self):
        self.assertFalse(self.paths.cable_loss_results_dir.exists())

        reference = self.repository.save(
            MeasurementStage.CABLE_LOSS, cable_loss_result()
        )

        self.assertTrue(self.paths.cable_loss_results_dir.is_dir())
        self.assertTrue(reference.path.is_file())
        self.assertFalse(self.paths.driver_mapping_results_dir.exists())
        self.assertFalse(self.paths.amplifier_results_dir.exists())

    def test_saved_document_is_the_formal_result_and_can_be_reparsed(self):
        result = driver_mapping_result()

        reference = self.repository.save(MeasurementStage.DRIVER_MAPPING, result)

        document = json.loads(reference.path.read_text(encoding="utf-8"))
        self.assertEqual(document, result.to_dict())
        loaded = measurement_result_from_document(document)
        self.assertEqual(loaded.to_dict(), result.to_dict())

    def test_existing_target_is_not_overwritten(self):
        first = self.repository.save(
            MeasurementStage.CABLE_LOSS, cable_loss_result(attenuator_value=10.0)
        )
        original_bytes = first.path.read_bytes()

        with self.assertRaisesRegex(StageResultError, "不允许覆盖"):
            self.repository.save(
                MeasurementStage.CABLE_LOSS,
                cable_loss_result(attenuator_value=20.0),
            )

        self.assertEqual(first.path.read_bytes(), original_bytes)
        self.assertEqual(
            sorted(entry.name for entry in first.path.parent.iterdir()),
            [first.path.name],
        )

    def test_exclusive_create_rejects_a_target_that_appeared_after_the_check(self):
        result = cable_loss_result()
        target = self.paths.cable_loss_results_dir / f"{result.measurement_id}.json"
        existing = b'{"existing": true}'
        target.parent.mkdir(parents=True)
        target.write_bytes(existing)

        real_exists = Path.exists

        def pretend_target_is_missing(candidate):
            if candidate == target:
                return False
            return real_exists(candidate)

        with patch.object(Path, "exists", pretend_target_is_missing):
            with self.assertRaisesRegex(StageResultError, "不允许覆盖"):
                self.repository.save(MeasurementStage.CABLE_LOSS, result)

        self.assertEqual(target.read_bytes(), existing)
        self.assertEqual(
            sorted(entry.name for entry in target.parent.iterdir()),
            [target.name],
        )

    def test_stage_and_type_mismatch_is_rejected_before_creating_the_directory(self):
        with self.assertRaisesRegex(StageResultError, "结果类型与阶段不符"):
            self.repository.save(
                MeasurementStage.CABLE_LOSS, amplifier_result()
            )

        self.assertFalse(self.paths.cable_loss_results_dir.exists())

    def test_unknown_stage_is_rejected_without_writing(self):
        with self.assertRaises(StageResultError):
            self.repository.save("runs", cable_loss_result())

        self.assertFalse(self.paths.test_results_dir.exists())

    def test_result_without_measurement_identity_is_rejected(self):
        with self.assertRaisesRegex(StageResultError, "measurement_id"):
            self.repository.save(
                MeasurementStage.CABLE_LOSS, cable_loss_result(identity=None)
            )

        self.assertFalse(self.paths.cable_loss_results_dir.exists())

    def test_shared_base_result_is_rejected(self):
        with self.assertRaisesRegex(StageResultError, "正式测量结果"):
            self.repository.save(
                MeasurementStage.CABLE_LOSS,
                MeasurementResult(measurement_type="cable_loss", run_id=RUN_ID),
            )

        self.assertFalse(self.paths.cable_loss_results_dir.exists())

    def test_identity_that_cannot_become_a_filename_is_rejected(self):
        with self.assertRaisesRegex(StageResultError, "文件名"):
            self.repository.save(
                MeasurementStage.CABLE_LOSS, cable_loss_result(identity="CON")
            )

        self.assertFalse(self.paths.cable_loss_results_dir.exists())

    def test_serialization_failure_leaves_no_partial_file_or_temporary_file(self):
        result = cable_loss_result(configuration_snapshot={"broken": object()})

        with self.assertRaises(TypeError):
            self.repository.save(MeasurementStage.CABLE_LOSS, result)

        directory = self.paths.cable_loss_results_dir
        self.assertTrue(directory.is_dir())
        self.assertEqual(list(directory.iterdir()), [])

    def test_publish_failure_removes_the_placeholder_and_temporary_file(self):
        target = self.paths.cable_loss_results_dir / f"{CABLE_ID}.json"
        real_replace = os.replace

        def fail_only_for_target(source, destination):
            if Path(destination) == target:
                raise OSError("替换失败")
            return real_replace(source, destination)

        with patch(
            "infrastructure.persistence.stage_result_repository.os.replace",
            side_effect=fail_only_for_target,
        ):
            with self.assertRaisesRegex(OSError, "替换失败"):
                self.repository.save(
                    MeasurementStage.CABLE_LOSS, cable_loss_result()
                )

        self.assertFalse(target.exists())
        self.assertEqual(list(target.parent.iterdir()), [])

    def test_temporary_file_cleanup_failure_does_not_mask_the_write_failure(self):
        target = self.paths.cable_loss_results_dir / f"{CABLE_ID}.json"
        real_replace = os.replace

        def fail_only_for_target(source, destination):
            if Path(destination) == target:
                raise OSError("替换失败")
            return real_replace(source, destination)

        with patch(
            "infrastructure.persistence.stage_result_repository.os.replace",
            side_effect=fail_only_for_target,
        ), patch.object(Path, "unlink", side_effect=RuntimeError("清理失败")):
            with self.assertLogs(
                "infrastructure.persistence.stage_result_repository", level="WARNING"
            ) as logs:
                with self.assertRaisesRegex(OSError, "替换失败"):
                    self.repository.save(
                        MeasurementStage.CABLE_LOSS, cable_loss_result()
                    )

        self.assertTrue(any("清理失败" in line for line in logs.output), logs.output)

    def test_reference_facts_are_read_before_publish_and_from_the_staging_file(self):
        target = self.paths.cable_loss_results_dir / f"{CABLE_ID}.json"
        events = []
        real_facts = stage_result_repository._file_facts
        real_replace = os.replace

        def track_facts(path):
            events.append(("facts", Path(path)))
            return real_facts(path)

        def track_replace(source, destination):
            result = real_replace(source, destination)
            if Path(destination) == target:
                events.append(("publish", Path(destination)))
            return result

        with patch(
            "infrastructure.persistence.stage_result_repository._file_facts",
            side_effect=track_facts,
        ), patch(
            "infrastructure.persistence.stage_result_repository.os.replace",
            side_effect=track_replace,
        ):
            reference = self.repository.save(
                MeasurementStage.CABLE_LOSS, cable_loss_result()
            )

        # 摘要/大小必须在独占占位与发布之前读取，且读的是同目录暂存文件。
        self.assertEqual([kind for kind, _ in events], ["facts", "publish"])
        facts_path = events[0][1]
        self.assertEqual(facts_path.parent, target.parent)
        self.assertNotEqual(facts_path, target)
        self.assertTrue(facts_path.name.startswith(f".{target.name}."))
        self.assertEqual(events[1][1], target)
        self.assert_reference_matches_file(reference)

    def test_reference_facts_failure_leaves_no_file_or_temporary_file(self):
        target = self.paths.cable_loss_results_dir / f"{CABLE_ID}.json"

        with patch(
            "infrastructure.persistence.stage_result_repository._file_facts",
            side_effect=OSError("读取暂存文件失败"),
        ):
            with self.assertRaisesRegex(OSError, "读取暂存文件失败"):
                self.repository.save(
                    MeasurementStage.CABLE_LOSS, cable_loss_result()
                )

        self.assertFalse(target.exists())
        self.assertEqual(list(target.parent.iterdir()), [])

    def test_write_failure_keeps_previous_results_intact(self):
        first = self.repository.save(
            MeasurementStage.CABLE_LOSS, cable_loss_result()
        )
        original_bytes = first.path.read_bytes()
        second = cable_loss_result(
            identity=measurement_id("1234abcd"), attenuator_value=30.0
        )

        with patch(
            "infrastructure.persistence.stage_result_repository.write_json_atomic",
            side_effect=OSError("磁盘已满"),
        ):
            with self.assertRaisesRegex(OSError, "磁盘已满"):
                self.repository.save(MeasurementStage.CABLE_LOSS, second)

        self.assertEqual(first.path.read_bytes(), original_bytes)
        self.assertEqual(
            sorted(entry.name for entry in first.path.parent.iterdir()),
            [first.path.name],
        )


class StageResultLoadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = PathService(self.root)
        self.repository = FileStageResultRepository(paths=self.paths)

    def write_bytes(self, directory: Path, name: str, payload: bytes) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(payload)
        return path

    def write_document(self, directory: Path, identity: str, document) -> Path:
        payload = json.dumps(document, ensure_ascii=False).encode("utf-8")
        return self.write_bytes(directory, f"{identity}.json", payload)

    def test_load_returns_the_saved_result_and_file_facts(self):
        result = cable_loss_result()
        saved = self.repository.save(MeasurementStage.CABLE_LOSS, result)

        loaded = self.repository.load(MeasurementStage.CABLE_LOSS, saved.path)

        self.assertEqual(loaded.result.to_dict(), result.to_dict())
        self.assertEqual(loaded.path, saved.path)
        self.assertEqual(loaded.measurement_id, result.measurement_id)
        self.assertEqual(loaded.reference.stage, MeasurementStage.CABLE_LOSS)
        self.assertEqual(loaded.sha256, saved.sha256)
        self.assertEqual(loaded.size_bytes, saved.size_bytes)
        self.assertEqual(
            loaded.sha256, hashlib.sha256(saved.path.read_bytes()).hexdigest()
        )

    def test_each_stage_loads_its_own_formal_result(self):
        cases = (
            (MeasurementStage.CABLE_LOSS, cable_loss_result()),
            (MeasurementStage.DRIVER_MAPPING, driver_mapping_result()),
            (MeasurementStage.AMPLIFIER, amplifier_result()),
        )

        for stage, result in cases:
            with self.subTest(stage=stage.value):
                saved = self.repository.save(stage, result)
                loaded = self.repository.load(stage, saved.path)

                self.assertEqual(loaded.result.to_dict(), result.to_dict())
                self.assertIs(loaded.reference.stage, stage)

    def test_repeated_loads_are_stable(self):
        saved = self.repository.save(
            MeasurementStage.CABLE_LOSS, cable_loss_result()
        )

        first = self.repository.load(MeasurementStage.CABLE_LOSS, saved.path)
        second = self.repository.load(MeasurementStage.CABLE_LOSS, saved.path)

        self.assertEqual(first.reference, second.reference)
        self.assertEqual(first.result.to_dict(), second.result.to_dict())

    def test_reference_digest_changes_when_the_file_changes(self):
        saved = self.repository.save(
            MeasurementStage.CABLE_LOSS, cable_loss_result()
        )
        first = self.repository.load(MeasurementStage.CABLE_LOSS, saved.path)

        saved.path.write_bytes(saved.path.read_bytes() + b"\n")
        second = self.repository.load(MeasurementStage.CABLE_LOSS, saved.path)

        self.assertEqual(second.result.to_dict(), first.result.to_dict())
        self.assertNotEqual(second.sha256, first.sha256)
        self.assertEqual(second.size_bytes, first.size_bytes + 1)
        self.assertEqual(
            second.sha256, hashlib.sha256(saved.path.read_bytes()).hexdigest()
        )

    def test_empty_path_is_rejected(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                with self.assertRaisesRegex(StageResultError, "不能为空"):
                    self.repository.load(MeasurementStage.CABLE_LOSS, value)

    def test_path_outside_the_stage_directory_is_rejected(self):
        saved = self.repository.save(
            MeasurementStage.AMPLIFIER, amplifier_result()
        )

        for stage, path in (
            (MeasurementStage.CABLE_LOSS, saved.path),
            (
                MeasurementStage.CABLE_LOSS,
                self.paths.cable_loss_results_dir
                / ".."
                / "amplifier"
                / saved.path.name,
            ),
        ):
            with self.subTest(stage=stage.value, path=str(path)):
                with self.assertRaisesRegex(StageResultError, "阶段目录"):
                    self.repository.load(stage, path)

    def test_boundary_error_wins_over_invalid_filename(self):
        outside = self.paths.test_results_dir / "notes.txt"

        with self.assertRaisesRegex(StageResultError, "阶段目录"):
            self.repository.load(MeasurementStage.CABLE_LOSS, outside)

        inside = self.paths.cable_loss_results_dir / "notes.txt"
        inside.parent.mkdir(parents=True, exist_ok=True)
        inside.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(StageResultError, "文件名"):
            self.repository.load(MeasurementStage.CABLE_LOSS, inside)

    def test_missing_file_is_rejected_without_creating_the_directory(self):
        path = self.paths.cable_loss_results_dir / f"{CABLE_ID}.json"

        with self.assertRaisesRegex(StageResultError, "不存在"):
            self.repository.load(MeasurementStage.CABLE_LOSS, path)

        self.assertFalse(self.paths.cable_loss_results_dir.exists())

    def test_corrupt_json_is_rejected(self):
        directory = self.paths.cable_loss_results_dir
        for name, payload in (
            ("truncated", b'{"measurement_type": "cable_loss"'),
            ("not json", b"not json at all"),
            ("not utf8", b"\xff\xfe{}\x00"),
            ("root list", b"[]"),
            ("root null", b"null"),
        ):
            with self.subTest(name=name):
                path = self.write_bytes(directory, f"{CABLE_ID}.json", payload)
                with self.assertRaises(StageResultError):
                    self.repository.load(MeasurementStage.CABLE_LOSS, path)

    def test_result_type_that_belongs_to_another_stage_is_rejected(self):
        path = self.write_document(
            self.paths.cable_loss_results_dir,
            AMPLIFIER_ID,
            amplifier_result().to_dict(),
        )

        with self.assertRaisesRegex(StageResultError, "与阶段不符"):
            self.repository.load(MeasurementStage.CABLE_LOSS, path)

    def test_non_completed_result_is_rejected(self):
        failed = cable_loss_result(
            status=MeasurementStatus.FAILED, error="硬件断开"
        )
        path = self.write_document(
            self.paths.cable_loss_results_dir, CABLE_ID, failed.to_dict()
        )

        with self.assertRaisesRegex(StageResultError, "completed"):
            self.repository.load(MeasurementStage.CABLE_LOSS, path)

    def test_legacy_run_result_without_measurement_identity_is_rejected(self):
        legacy = cable_loss_result(identity=None)
        self.assertNotIn("measurement_id", legacy.to_dict())
        path = self.write_document(
            self.paths.cable_loss_results_dir, CABLE_ID, legacy.to_dict()
        )

        with self.assertRaisesRegex(StageResultError, "measurement_id"):
            self.repository.load(MeasurementStage.CABLE_LOSS, path)

    def test_document_identity_must_match_the_file_name(self):
        other_id = measurement_id("1234abcd")
        document = cable_loss_result(identity=other_id).to_dict()
        path = self.write_document(
            self.paths.cable_loss_results_dir, CABLE_ID, document
        )

        with self.assertRaisesRegex(StageResultError, "measurement_id"):
            self.repository.load(MeasurementStage.CABLE_LOSS, path)

    def test_unsupported_schema_version_is_rejected(self):
        document = driver_mapping_result().to_dict()
        document["schema_version"] = "1.0"
        path = self.write_document(
            self.paths.driver_mapping_results_dir, DRIVER_ID, document
        )

        with self.assertRaises(StageResultError):
            self.repository.load(MeasurementStage.DRIVER_MAPPING, path)

    def test_unknown_stage_is_rejected(self):
        with self.assertRaises(StageResultError):
            self.repository.load("runs", self.paths.cable_loss_results_dir / "x.json")


class StageResultListTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = PathService(self.root)
        self.repository = FileStageResultRepository(paths=self.paths)

    def write_bytes(self, directory: Path, name: str, payload: bytes) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(payload)
        return path

    def write_document(self, directory: Path, identity: str, document) -> Path:
        payload = json.dumps(document, ensure_ascii=False).encode("utf-8")
        return self.write_bytes(directory, f"{identity}.json", payload)

    def test_missing_directory_returns_empty_without_creating_it(self):
        self.assertEqual(self.repository.list_results(MeasurementStage.AMPLIFIER), ())
        self.assertFalse(self.paths.amplifier_results_dir.exists())

    def test_lists_only_the_requested_stage_newest_first(self):
        older = cable_loss_result(
            identity=measurement_id("aaaa0001"),
            created_at="2026-11-11T01:00:00+00:00",
        )
        newer = cable_loss_result(
            identity=measurement_id("aaaa0002"),
            created_at="2026-11-11T03:00:00+00:00",
        )
        self.repository.save(MeasurementStage.CABLE_LOSS, older)
        self.repository.save(MeasurementStage.CABLE_LOSS, newer)
        self.repository.save(MeasurementStage.AMPLIFIER, amplifier_result())

        entries = self.repository.list_results(MeasurementStage.CABLE_LOSS)

        self.assertEqual(
            [entry.measurement_id for entry in entries],
            [newer.measurement_id, older.measurement_id],
        )
        for entry in entries:
            self.assertIs(entry.measurement_type, older.measurement_type)
            self.assertIs(entry.status, MeasurementStatus.COMPLETED)
            self.assertEqual(entry.run_id, RUN_ID)
            self.assertEqual(
                entry.sha256, hashlib.sha256(entry.path.read_bytes()).hexdigest()
            )
            self.assertEqual(entry.size_bytes, entry.path.stat().st_size)

    def test_extreme_created_at_sorts_last_without_failing(self):
        normal = cable_loss_result(
            identity=measurement_id("cccc0001"),
            created_at="2026-11-11T03:00:00+00:00",
        )
        extreme = cable_loss_result(
            identity=measurement_id("cccc0002"),
            created_at="0001-01-01T00:00:00",
        )
        self.repository.save(MeasurementStage.CABLE_LOSS, normal)
        self.repository.save(MeasurementStage.CABLE_LOSS, extreme)

        entries = self.repository.list_results(MeasurementStage.CABLE_LOSS)

        self.assertEqual(
            [entry.measurement_id for entry in entries],
            [normal.measurement_id, extreme.measurement_id],
        )

    def test_skips_corrupt_incomplete_and_mismatched_files(self):
        good = self.repository.save(
            MeasurementStage.CABLE_LOSS, cable_loss_result()
        )
        directory = self.paths.cable_loss_results_dir
        self.write_bytes(directory, f"{measurement_id('bbbb0001')}.json", b"{")
        self.write_document(
            directory,
            measurement_id("bbbb0002"),
            cable_loss_result(
                identity=measurement_id("bbbb0002"),
                status=MeasurementStatus.FAILED,
                error="硬件断开",
            ).to_dict(),
        )
        self.write_document(
            directory,
            measurement_id("bbbb0003"),
            amplifier_result(identity=measurement_id("bbbb0003")).to_dict(),
        )
        self.write_bytes(directory, "notes.txt", b"not a result")
        (directory / f"{measurement_id('bbbb0004')}.json").mkdir()

        entries = self.repository.list_results(MeasurementStage.CABLE_LOSS)

        self.assertEqual([entry.path for entry in entries], [good.path])

    def test_legacy_stage_file_without_identity_is_not_listed(self):
        directory = self.paths.cable_loss_results_dir
        self.write_document(
            directory,
            CABLE_ID,
            cable_loss_result(identity=None).to_dict(),
        )

        self.assertEqual(self.repository.list_results(MeasurementStage.CABLE_LOSS), ())

    def test_unknown_stage_is_rejected(self):
        with self.assertRaises(StageResultError):
            self.repository.list_results("runs")


if __name__ == "__main__":
    unittest.main()
