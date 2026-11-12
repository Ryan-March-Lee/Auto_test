"""步骤 4：阶段结果仓库端口契约。

覆盖：

- 阶段枚举与正式测量类型的唯一对应（目录名不等于测量类型值）；
- 保存边界要求正式结果、类型与阶段相符、且带测量身份和保存时间；
- 显式加载路径只能直接位于对应阶段目录，越界/路径片段/错误后缀都有确定错误；
- 端口是可用测试替身实现的 ``runtime_checkable`` 协议，替身按契约拒绝类型错配
  与越界路径。

本文件只校验应用层契约，不依赖基础设施实现；原子写入与真实 JSON 解析由后续
步骤的适配器测试覆盖。
"""

import hashlib
import tempfile
import unittest
from pathlib import Path

from application.dto import (
    AmplifierMeasurementResult,
    CableLossMeasurementResult,
    DriverPowerMappingMeasurementResult,
    MeasurementResult,
    MeasurementStatus,
    MeasurementType,
    generate_measurement_id,
)
from application.errors import ConfigurationError, ErrorCategory
from application.ports.stage_result_repository import (
    LoadedStageResult,
    MeasurementStage,
    StageResultEntry,
    StageResultError,
    StageResultReference,
    StageResultRepository,
    coerce_measurement_stage,
    measurement_stage_for_type,
    measurement_type_for_stage,
    require_stage_result,
    require_stage_result_path,
)


NOW_ID = generate_measurement_id(token="a1b2c3d4")
DIGEST = "0" * 64


def cable_loss_result(measurement_id=NOW_ID, **overrides):
    values = {"run_id": "20261111T010203Z_a1b2c3d4", "measurement_id": measurement_id}
    values.update(overrides)
    return CableLossMeasurementResult(**values)


def amplifier_result(measurement_id=NOW_ID):
    return AmplifierMeasurementResult(
        run_id="20261111T010203Z_a1b2c3d4",
        measurement_id=measurement_id,
    )


def driver_mapping_result(measurement_id=NOW_ID):
    return DriverPowerMappingMeasurementResult(
        run_id="20261111T010203Z_a1b2c3d4",
        measurement_id=measurement_id,
    )


class MeasurementStageTests(unittest.TestCase):
    def test_stage_values_are_the_fixed_directory_names(self):
        self.assertEqual(
            {stage.value for stage in MeasurementStage},
            {"cable_loss", "driver_mapping", "amplifier"},
        )

    def test_stage_maps_to_its_formal_measurement_type(self):
        self.assertIs(
            measurement_type_for_stage(MeasurementStage.CABLE_LOSS),
            MeasurementType.CABLE_LOSS,
        )
        self.assertIs(
            measurement_type_for_stage(MeasurementStage.DRIVER_MAPPING),
            MeasurementType.DRIVER_POWER_MAPPING,
        )
        self.assertIs(
            measurement_type_for_stage(MeasurementStage.AMPLIFIER),
            MeasurementType.AMPLIFIER,
        )

    def test_formal_type_maps_back_to_its_stage(self):
        for stage in MeasurementStage:
            with self.subTest(stage=stage.value):
                self.assertIs(measurement_stage_for_type(stage.measurement_type), stage)

    def test_stage_accepts_its_text_value_and_strips_padding(self):
        self.assertIs(
            coerce_measurement_stage(" driver_mapping "),
            MeasurementStage.DRIVER_MAPPING,
        )

    def test_missing_or_unknown_stage_is_a_stage_error(self):
        for value in ("", "   ", "cable_losses", "runs", None, 3):
            with self.subTest(value=value):
                with self.assertRaises(StageResultError):
                    coerce_measurement_stage(value)


class StageResultErrorTests(unittest.TestCase):
    def test_error_is_a_configuration_error_and_value_error(self):
        error = StageResultError("阶段结果失败")
        self.assertIsInstance(error, ConfigurationError)
        self.assertIsInstance(error, ValueError)
        self.assertIs(error.category, ErrorCategory.CONFIGURATION)


class RequireStageResultTests(unittest.TestCase):
    def test_accepts_result_matching_the_stage(self):
        result = cable_loss_result()

        self.assertIs(
            require_stage_result(MeasurementStage.CABLE_LOSS, result),
            result,
        )
        self.assertIs(
            require_stage_result(
                MeasurementStage.DRIVER_MAPPING, driver_mapping_result()
            ).measurement_type,
            MeasurementType.DRIVER_POWER_MAPPING,
        )
        self.assertIs(
            require_stage_result(
                MeasurementStage.AMPLIFIER, amplifier_result()
            ).measurement_type,
            MeasurementType.AMPLIFIER,
        )

    def test_rejects_result_type_that_belongs_to_another_stage(self):
        for stage, result in (
            (MeasurementStage.CABLE_LOSS, amplifier_result()),
            (MeasurementStage.AMPLIFIER, cable_loss_result()),
            (MeasurementStage.DRIVER_MAPPING, cable_loss_result()),
        ):
            with self.subTest(stage=stage.value, result=type(result).__name__):
                with self.assertRaisesRegex(StageResultError, "与阶段不符"):
                    require_stage_result(stage, result)

    def test_rejects_shared_base_contract_and_raw_documents(self):
        base = MeasurementResult(
            measurement_type=MeasurementType.CABLE_LOSS,
            run_id="20261111T010203Z_a1b2c3d4",
            measurement_id=NOW_ID,
        )
        for value in (base, {}, None, "cable_loss"):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(StageResultError):
                    require_stage_result(MeasurementStage.CABLE_LOSS, value)

    def test_rejects_result_without_identity_or_save_time(self):
        with self.assertRaisesRegex(StageResultError, "身份"):
            require_stage_result(MeasurementStage.CABLE_LOSS, cable_loss_result(None))
        with self.assertRaisesRegex(StageResultError, "保存时间"):
            require_stage_result(
                MeasurementStage.CABLE_LOSS,
                cable_loss_result(created_at=None),
            )

    def test_rejects_unknown_stage_before_accepting_result(self):
        with self.assertRaises(StageResultError):
            require_stage_result("runs", cable_loss_result())


class StageResultReferenceTests(unittest.TestCase):
    def reference(self, **overrides):
        values = {
            "stage": MeasurementStage.CABLE_LOSS,
            "measurement_id": NOW_ID,
            "path": Path("C:/results/cable_loss") / f"{NOW_ID}.json",
            "sha256": DIGEST,
            "size_bytes": 42,
        }
        values.update(overrides)
        return StageResultReference(**values)

    def test_reference_collects_the_structured_file_facts(self):
        reference = self.reference()

        self.assertIs(reference.stage, MeasurementStage.CABLE_LOSS)
        self.assertEqual(reference.measurement_id, NOW_ID)
        self.assertEqual(reference.sha256, DIGEST)
        self.assertEqual(reference.size_bytes, 42)
        self.assertEqual(reference.path.name, f"{NOW_ID}.json")

    def test_reference_rejects_unsafe_identity(self):
        for value in ("", " ../escape", "a/b", "CON", None):
            with self.subTest(value=value):
                with self.assertRaises(StageResultError):
                    self.reference(measurement_id=value)

    def test_reference_rejects_invalid_digest_and_size(self):
        for digest in ("", "A" * 64, "0" * 63, None, 123):
            with self.subTest(digest=digest):
                with self.assertRaises(StageResultError):
                    self.reference(sha256=digest)
        for size in (-1, True, 1.5, "12"):
            with self.subTest(size=size):
                with self.assertRaises(StageResultError):
                    self.reference(size_bytes=size)

    def test_reference_rejects_empty_path(self):
        for value in ("", ".", None):
            with self.subTest(value=value):
                with self.assertRaises((StageResultError, TypeError)):
                    self.reference(path=value)


class StageResultEntryTests(unittest.TestCase):
    def reference(self, stage=MeasurementStage.AMPLIFIER):
        return StageResultReference(
            stage=stage,
            measurement_id=NOW_ID,
            path=Path("C:/results") / stage.value / f"{NOW_ID}.json",
            sha256=DIGEST,
            size_bytes=10,
        )

    def test_entry_carries_display_facts_and_delegates_file_facts(self):
        entry = StageResultEntry(
            reference=self.reference(),
            measurement_type=MeasurementType.AMPLIFIER,
            status=MeasurementStatus.COMPLETED,
            created_at="2026-11-11T01:02:03+00:00",
            run_id="20261111T010203Z_a1b2c3d4",
        )

        self.assertIs(entry.measurement_type, MeasurementType.AMPLIFIER)
        self.assertIs(entry.status, MeasurementStatus.COMPLETED)
        self.assertEqual(entry.measurement_id, NOW_ID)
        self.assertEqual(entry.sha256, DIGEST)
        self.assertEqual(entry.size_bytes, 10)
        self.assertEqual(entry.path.name, f"{NOW_ID}.json")

    def test_entry_rejects_type_that_does_not_match_the_stage(self):
        with self.assertRaisesRegex(StageResultError, "与阶段不符"):
            StageResultEntry(
                reference=self.reference(MeasurementStage.CABLE_LOSS),
                measurement_type=MeasurementType.AMPLIFIER,
                status=MeasurementStatus.COMPLETED,
                created_at="2026-11-11T01:02:03+00:00",
                run_id="20261111T010203Z_a1b2c3d4",
            )

    def test_entry_requires_save_time_and_source_run(self):
        for field, value in (
            ("created_at", ""),
            ("created_at", None),
            ("run_id", ""),
        ):
            with self.subTest(field=field, value=value):
                values = {
                    "reference": self.reference(),
                    "measurement_type": MeasurementType.AMPLIFIER,
                    "status": MeasurementStatus.COMPLETED,
                    "created_at": "2026-11-11T01:02:03+00:00",
                    "run_id": "20261111T010203Z_a1b2c3d4",
                }
                values[field] = value
                with self.assertRaises(StageResultError):
                    StageResultEntry(**values)

    def test_entry_rejects_non_completed_status(self):
        for status in (
            MeasurementStatus.FAILED,
            MeasurementStatus.CANCELLED,
            MeasurementStatus.RUNNING,
            "completed_pending",
        ):
            with self.subTest(status=status):
                with self.assertRaises(StageResultError):
                    StageResultEntry(
                        reference=self.reference(),
                        measurement_type=MeasurementType.AMPLIFIER,
                        status=status,
                        created_at="2026-11-11T01:02:03+00:00",
                        run_id="20261111T010203Z_a1b2c3d4",
                    )

    def test_entry_accepts_completed_status_as_text(self):
        entry = StageResultEntry(
            reference=self.reference(),
            measurement_type=MeasurementType.AMPLIFIER,
            status=" completed ",
            created_at="2026-11-11T01:02:03+00:00",
            run_id="20261111T010203Z_a1b2c3d4",
        )

        self.assertIs(entry.status, MeasurementStatus.COMPLETED)


class LoadedStageResultTests(unittest.TestCase):
    def reference(self, stage=MeasurementStage.CABLE_LOSS, measurement_id=NOW_ID):
        return StageResultReference(
            stage=stage,
            measurement_id=measurement_id,
            path=Path("C:/results") / stage.value / f"{measurement_id}.json",
            sha256=DIGEST,
            size_bytes=10,
        )

    def test_loaded_result_pairs_reference_and_formal_result(self):
        result = cable_loss_result()

        loaded = LoadedStageResult(reference=self.reference(), result=result)

        self.assertIs(loaded.result, result)
        self.assertEqual(loaded.measurement_id, NOW_ID)
        self.assertEqual(loaded.path.name, f"{NOW_ID}.json")

    def test_loaded_result_rejects_identity_mismatch(self):
        other_id = generate_measurement_id(token="deadbeef")

        with self.assertRaisesRegex(StageResultError, "measurement_id"):
            LoadedStageResult(
                reference=self.reference(),
                result=cable_loss_result(other_id),
            )

    def test_loaded_result_rejects_type_mismatch(self):
        with self.assertRaisesRegex(StageResultError, "与阶段不符"):
            LoadedStageResult(
                reference=self.reference(),
                result=amplifier_result(),
            )

    def test_loaded_result_rejects_non_completed_status(self):
        results = (
            cable_loss_result(status=MeasurementStatus.RUNNING),
            cable_loss_result(
                status=MeasurementStatus.FAILED,
                error="硬件断开",
            ),
            cable_loss_result(
                status=MeasurementStatus.CANCELLED,
                termination={"intent": "cancel", "reason": "用户取消"},
            ),
        )

        for result in results:
            with self.subTest(status=result.status.value):
                with self.assertRaisesRegex(StageResultError, "completed"):
                    LoadedStageResult(reference=self.reference(), result=result)

    def test_loaded_result_requires_identity_and_save_time(self):
        with self.assertRaisesRegex(StageResultError, "身份"):
            LoadedStageResult(
                reference=self.reference(),
                result=cable_loss_result(None),
            )
        with self.assertRaisesRegex(StageResultError, "保存时间"):
            LoadedStageResult(
                reference=self.reference(),
                result=cable_loss_result(created_at=None),
            )

    def test_loaded_result_accepts_only_bytes_matching_the_reference(self):
        raw = b'{"run_id": "x"}'
        reference = StageResultReference(
            stage=MeasurementStage.CABLE_LOSS,
            measurement_id=NOW_ID,
            path=Path("C:/results") / MeasurementStage.CABLE_LOSS.value
            / f"{NOW_ID}.json",
            sha256=hashlib.sha256(raw).hexdigest(),
            size_bytes=len(raw),
        )

        loaded = LoadedStageResult(
            reference=reference, result=cable_loss_result(), raw_bytes=raw
        )

        self.assertEqual(loaded.raw_bytes, raw)
        with self.assertRaisesRegex(StageResultError, "原始字节"):
            LoadedStageResult(
                reference=reference,
                result=cable_loss_result(),
                raw_bytes=b'{"run_id": "y"}',
            )
        with self.assertRaisesRegex(StageResultError, "bytes"):
            LoadedStageResult(
                reference=reference,
                result=cable_loss_result(),
                raw_bytes="not-bytes",
            )


class StageResultPathGuardTests(unittest.TestCase):
    def test_accepts_a_direct_json_file_in_the_stage_directory(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "cable_loss"
            path = directory / f"{NOW_ID}.json"

            resolved = require_stage_result_path(
                path,
                stage=MeasurementStage.CABLE_LOSS,
                stage_directory=directory,
            )

            self.assertEqual(resolved, path.resolve())

    def test_rejects_empty_path(self):
        with tempfile.TemporaryDirectory() as root:
            for value in (None, "", "   "):
                with self.subTest(value=value):
                    with self.assertRaises(StageResultError):
                        require_stage_result_path(
                            value,
                            stage=MeasurementStage.CABLE_LOSS,
                            stage_directory=Path(root),
                        )

    def test_rejects_paths_outside_the_stage_directory(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "cable_loss"
            outside = Path(root) / "amplifier" / f"{NOW_ID}.json"
            traversal = directory / ".." / "amplifier" / f"{NOW_ID}.json"

            for value in (outside, traversal, Path(root) / f"{NOW_ID}.json"):
                with self.subTest(value=str(value)):
                    with self.assertRaisesRegex(StageResultError, "阶段目录"):
                        require_stage_result_path(
                            value,
                            stage=MeasurementStage.CABLE_LOSS,
                            stage_directory=directory,
                        )

    def test_rejects_nested_directories_and_bad_file_names(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "cable_loss"
            for name, path in (
                ("nested", directory / "nested" / f"{NOW_ID}.json"),
                ("suffix", directory / f"{NOW_ID}.txt"),
                ("path fragment file", directory / "a..b.json"),
            ):
                with self.subTest(name=name):
                    with self.assertRaises(StageResultError):
                        require_stage_result_path(
                            path,
                            stage=MeasurementStage.CABLE_LOSS,
                            stage_directory=directory,
                        )


class _FakeStageResultRepository:
    """按端口语义实现的内存替身：证明契约可被测试实现。"""

    def __init__(self, root: Path):
        self.root = root
        self._saved: dict[Path, tuple[StageResultReference, MeasurementResult]] = {}

    def _directory(self, stage):
        return self.root / coerce_measurement_stage(stage).value

    def save(self, stage, result):
        target = coerce_measurement_stage(stage)
        formal = require_stage_result(target, result)
        path = self._directory(target) / f"{formal.measurement_id}.json"
        if path in self._saved or path.exists():
            raise StageResultError(f"阶段结果文件已存在，拒绝覆盖: {path}")
        reference = StageResultReference(
            stage=target,
            measurement_id=formal.measurement_id,
            path=path,
            sha256=DIGEST,
            size_bytes=0,
        )
        self._saved[path] = (reference, formal)
        return reference

    def load(self, stage, path):
        target = coerce_measurement_stage(stage)
        resolved = require_stage_result_path(
            path, stage=target, stage_directory=self._directory(target)
        )
        stored = self._saved.get(resolved)
        if stored is None:
            raise StageResultError(f"阶段结果文件不存在: {resolved}")
        reference, result = stored
        return LoadedStageResult(reference=reference, result=result)

    def list_results(self, stage):
        target = coerce_measurement_stage(stage)
        entries = []
        for reference, result in self._saved.values():
            if reference.stage is not target:
                continue
            entries.append(
                StageResultEntry(
                    reference=reference,
                    measurement_type=result.measurement_type,
                    status=result.status,
                    created_at=result.created_at,
                    run_id=result.run_id,
                )
            )
        return tuple(entries)


class StageResultPortContractTests(unittest.TestCase):
    def test_port_is_a_runtime_checkable_protocol(self):
        with tempfile.TemporaryDirectory() as root:
            repository = _FakeStageResultRepository(Path(root))

            self.assertIsInstance(repository, StageResultRepository)
            self.assertNotIsInstance(object(), StageResultRepository)

    def test_test_double_can_save_load_and_list_by_stage(self):
        with tempfile.TemporaryDirectory() as root:
            repository = _FakeStageResultRepository(Path(root))
            result = cable_loss_result()

            saved = repository.save(MeasurementStage.CABLE_LOSS, result)
            loaded = repository.load(MeasurementStage.CABLE_LOSS, saved.path)
            entries = repository.list_results(MeasurementStage.CABLE_LOSS)

            self.assertEqual(saved.stage, MeasurementStage.CABLE_LOSS)
            self.assertEqual(saved.measurement_id, NOW_ID)
            self.assertEqual(loaded.result, result)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].measurement_id, NOW_ID)
            self.assertIs(entries[0].status, MeasurementStatus.COMPLETED)
            self.assertEqual(
                repository.list_results(MeasurementStage.AMPLIFIER), ()
            )

    def test_test_double_rejects_type_mismatch_and_duplicate_identity(self):
        with tempfile.TemporaryDirectory() as root:
            repository = _FakeStageResultRepository(Path(root))

            with self.assertRaises(StageResultError):
                repository.save(MeasurementStage.CABLE_LOSS, amplifier_result())

            repository.save(MeasurementStage.CABLE_LOSS, cable_loss_result())
            with self.assertRaisesRegex(StageResultError, "拒绝覆盖"):
                repository.save(MeasurementStage.CABLE_LOSS, cable_loss_result())

    def test_test_double_rejects_path_outside_the_stage_directory(self):
        with tempfile.TemporaryDirectory() as root:
            repository = _FakeStageResultRepository(Path(root))
            repository.save(MeasurementStage.CABLE_LOSS, cable_loss_result())

            with self.assertRaises(StageResultError):
                repository.load(
                    MeasurementStage.CABLE_LOSS,
                    Path(root) / "amplifier" / f"{NOW_ID}.json",
                )


if __name__ == "__main__":
    unittest.main()
