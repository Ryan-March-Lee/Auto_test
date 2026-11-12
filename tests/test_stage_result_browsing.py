"""步骤 19：数据可视化改为浏览阶段目录。

覆盖验收标准：

- ``list_results()`` 跨运行列出线损、驱动映射和主功放三类正式结果；
- 新结果标识编码阶段与 ``measurement_id``，``load()`` / ``read_bytes()`` 读取的是
  阶段目录中的权威文件；
- 损坏、未完成和类型不匹配的阶段文件被跳过，不影响其余结果；
- 运行目录里的阶段结果引用指针不再重复出现在列表中；
- 旧运行目录中的完整正式结果文档仍按只读兼容分支可见、可加载；
- 列表行显示类型、保存时间、文件名与来源运行；绘图视图可直接消费阶段结果。
"""

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from application.dto import (
    AmplifierFrequencyResult,
    AmplifierMeasurementResult,
    CableLossMeasurementResult,
    DriverMappingRawPoint,
    DriverPowerMappingMeasurementResult,
    MeasurementStatus,
    generate_measurement_id,
)
from application.ports.gui_data_service import (
    RESULT_SOURCE_RUN,
    RESULT_SOURCE_STAGE,
)
from application.ports.stage_result_repository import MeasurementStage
from application.run_stages import RunStageRecorder
from domain.run.models import STAGE_AMPLIFIER, STAGE_CABLE_LOSS, STAGE_DRIVER_MAPPING
from infrastructure.filesystem.paths import PathService
from infrastructure.gui_data_service import ResultQueryAdapter
from infrastructure.persistence.run_deletion import FileRunDeletion
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
from infrastructure.persistence.run_repository import RunRepository
from infrastructure.persistence.stage_result_repository import FileStageResultRepository
from presentation.qt.history_view_model import HistoryViewModel


CABLE_RUN = "20261112T010000Z_aaaabbbb"
DRIVER_RUN = "20261112T020000Z_ccccdddd"
AMPLIFIER_RUN = "20261112T030000Z_eeeeffff"
LEGACY_RUN = "20261112T040000Z_11112222"

MOMENT = datetime(2026, 11, 12, 1, 0, tzinfo=timezone.utc)


def _identity(token: str, moment: datetime = MOMENT) -> str:
    return generate_measurement_id(moment, token=token)


CABLE_ID = _identity("a1b2c3d4")
DRIVER_ID = _identity("ffee0011", datetime(2026, 11, 12, 2, 0, tzinfo=timezone.utc))
AMPLIFIER_ID = _identity(
    "0badcafe", datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc)
)


def _cable_loss_result(identity: str = CABLE_ID) -> CableLossMeasurementResult:
    return CableLossMeasurementResult(
        run_id=CABLE_RUN,
        measurement_id=identity,
        created_at="2026-11-12T01:00:00+00:00",
    )


def _driver_mapping_result(
    identity: str = DRIVER_ID,
) -> DriverPowerMappingMeasurementResult:
    return DriverPowerMappingMeasurementResult(
        run_id=DRIVER_RUN,
        measurement_id=identity,
        created_at="2026-11-12T02:00:00+00:00",
        raw_points=(
            DriverMappingRawPoint(
                frequency_ghz="5.2",
                source_power_dbm=-10.0,
                driver_output_power_dbm=0.0,
            ),
        ),
    )


def _amplifier_result(identity: str = AMPLIFIER_ID) -> AmplifierMeasurementResult:
    return AmplifierMeasurementResult(
        run_id=AMPLIFIER_RUN,
        measurement_id=identity,
        created_at="2026-11-12T03:00:00+00:00",
        frequencies=(
            AmplifierFrequencyResult(
                frequency_ghz="5.2",
                sweep_data={
                    "input_power_dut": [-10.0],
                    "output_power_dut": [0.0],
                    "gain": [10.0],
                },
            ),
        ),
    )


class _ReplacingStageResults:
    """委托真实阶段仓库，但在加载之后替换磁盘文件，用于验证导出复用已校验字节。"""

    def __init__(self, inner, replacement: bytes, *, keep_bytes: bool = True):
        self.inner = inner
        self.replacement = replacement
        self.keep_bytes = keep_bytes
        self.replaced = False

    def save(self, stage, result):
        return self.inner.save(stage, result)

    def list_results(self, stage):
        return self.inner.list_results(stage)

    def load(self, stage, path):
        loaded = self.inner.load(stage, path)
        if not self.keep_bytes:
            loaded = replace(loaded, raw_bytes=None)
        if not self.replaced:
            Path(loaded.path).write_bytes(self.replacement)
            self.replaced = True
        return loaded


class StageResultBrowsingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.paths = PathService(self.root)
        self.runs = RunRepository(self.paths.test_results_dir / "runs")
        self.stage_repository = FileStageResultRepository(paths=self.paths)
        self.adapter = ResultQueryAdapter(
            self.paths,
            run_history=FileRunHistoryRepository(runs=self.runs),
            stage_results=self.stage_repository,
        )

    def _save_all_stages(self):
        return {
            stage: self.stage_repository.save(stage, result)
            for stage, result in (
                (MeasurementStage.CABLE_LOSS, _cable_loss_result()),
                (MeasurementStage.DRIVER_MAPPING, _driver_mapping_result()),
                (MeasurementStage.AMPLIFIER, _amplifier_result()),
            )
        }

    def test_list_results_scans_each_stage_directory_across_runs(self):
        self._save_all_stages()

        rows = self.adapter.list_results()

        self.assertEqual({row.source for row in rows}, {RESULT_SOURCE_STAGE})
        self.assertEqual(len(rows), 3)
        # 三类结果都来自不同运行，按保存时间从新到旧。
        self.assertEqual(
            [row.result_id for row in rows],
            [
                f"stage::amplifier::{AMPLIFIER_ID}",
                f"stage::driver_mapping::{DRIVER_ID}",
                f"stage::cable_loss::{CABLE_ID}",
            ],
        )
        self.assertEqual(
            [row.file_type for row in rows],
            ["功放测试", "驱动映射", "线损数据"],
        )
        self.assertEqual(
            [row.filename for row in rows],
            [
                f"{AMPLIFIER_ID}.json",
                f"{DRIVER_ID}.json",
                f"{CABLE_ID}.json",
            ],
        )
        self.assertEqual(
            [row.run_id for row in rows],
            [AMPLIFIER_RUN, DRIVER_RUN, CABLE_RUN],
        )
        self.assertEqual(
            [row.modified_at for row in rows],
            [
                datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc),
                datetime(2026, 11, 12, 2, 0, tzinfo=timezone.utc),
                datetime(2026, 11, 12, 1, 0, tzinfo=timezone.utc),
            ],
        )

    def test_load_and_read_bytes_use_the_stage_authoritative_file(self):
        references = self._save_all_stages()

        loaded = self.adapter.load(f"stage::amplifier::{AMPLIFIER_ID}")
        raw = self.adapter.read_bytes(f"stage::cable_loss::{CABLE_ID}")

        self.assertEqual(loaded.result.measurement_id, AMPLIFIER_ID)
        self.assertEqual(loaded.result.measurement_type.value, "amplifier")
        self.assertEqual(loaded.filename, f"{AMPLIFIER_ID}.json")
        self.assertEqual(loaded.run_id, AMPLIFIER_RUN)
        self.assertEqual(loaded.sha256, references[MeasurementStage.AMPLIFIER].sha256)
        self.assertEqual(
            raw, references[MeasurementStage.CABLE_LOSS].path.read_bytes()
        )
        self.assertIn(b"cable_losses", raw)

    def test_plotting_view_consumes_a_stage_result(self):
        self._save_all_stages()

        view = HistoryViewModel(self.adapter)
        rows = view.reload()
        amplitude_index = next(
            index for index, row in enumerate(rows) if row.file_type == "功放测试"
        )
        view.select(amplitude_index)
        loaded = view.load_selected()

        self.assertEqual(loaded.result.measurement_id, AMPLIFIER_ID)
        self.assertEqual(loaded.frequencies, (5.2,))
        self.assertEqual(
            loaded.plot_record(0)["sweep_data"]["gain"],
            [10.0],
        )

    def test_corrupt_incomplete_and_mismatched_stage_files_are_skipped(self):
        self.stage_repository.save(MeasurementStage.CABLE_LOSS, _cable_loss_result())
        directory = self.paths.cable_loss_results_dir

        corrupt_id = _identity("deadbeef")
        (directory / f"{corrupt_id}.json").write_text("{not json", encoding="utf-8")

        pending_id = _identity("badc0de0")
        pending = CableLossMeasurementResult(
            run_id=CABLE_RUN,
            measurement_id=pending_id,
            status=MeasurementStatus.RUNNING,
            created_at="2026-11-12T01:30:00+00:00",
        )
        (directory / f"{pending_id}.json").write_text(
            json.dumps(pending.to_dict()), encoding="utf-8"
        )

        mismatched_id = _identity("0ddba11")
        mismatched = _amplifier_result().to_dict()
        mismatched["measurement_id"] = mismatched_id
        (directory / f"{mismatched_id}.json").write_text(
            json.dumps(mismatched), encoding="utf-8"
        )

        rows = self.adapter.list_results()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].filename, f"{CABLE_ID}.json")
        for identity in (corrupt_id, pending_id, mismatched_id):
            with self.subTest(measurement_id=identity):
                with self.assertRaises(ValueError):
                    self.adapter.load(f"stage::cable_loss::{identity}")

    def test_stage_reference_pointer_is_not_listed_a_second_time(self):
        reference = self.stage_repository.save(
            MeasurementStage.DRIVER_MAPPING, _driver_mapping_result()
        )
        self.runs.create_run({"run_id": DRIVER_RUN}, run_id=DRIVER_RUN)
        recorder = RunStageRecorder(
            self.runs, DRIVER_RUN, stage_results=self.stage_repository
        )
        recorder.complete_with_stage_result(
            STAGE_DRIVER_MAPPING, reference, now=MOMENT
        )

        rows = self.adapter.list_results()

        self.assertEqual([row.result_id for row in rows], [
            f"stage::driver_mapping::{DRIVER_ID}"
        ])
        # 运行目录里的引用指针仍可按旧标识读取（只读兼容），但不进入列表。
        compatibility = self.adapter.load(
            f"run::{DRIVER_RUN}::inputs/driver_mapping.json"
        )
        self.assertEqual(compatibility.result.measurement_id, DRIVER_ID)

    def test_legacy_run_result_is_still_listed_and_loadable(self):
        self.runs.create_run({"run_id": LEGACY_RUN}, run_id=LEGACY_RUN)
        recorder = RunStageRecorder(self.runs, LEGACY_RUN)
        recorder.complete(
            STAGE_CABLE_LOSS,
            {
                "run_id": LEGACY_RUN,
                "measurement_type": "cable_loss",
                "status": "completed",
                "cable_losses": {"1.0": {"cable1": 1.0}},
            },
        )
        self.stage_repository.save(MeasurementStage.AMPLIFIER, _amplifier_result())

        rows = self.adapter.list_results()

        legacy_rows = [row for row in rows if row.source == RESULT_SOURCE_RUN]
        self.assertEqual(len(legacy_rows), 1)
        legacy = legacy_rows[0]
        self.assertEqual(legacy.run_id, LEGACY_RUN)
        self.assertEqual(legacy.filename, "cable_loss.json")
        self.assertEqual(legacy.file_type, "线损数据")
        self.assertIsNotNone(legacy.source_result)

        loaded = self.adapter.load(legacy.result_id)
        self.assertEqual(loaded.result.cable_losses()["1.0"]["cable1"], 1.0)
        self.assertIn(b"cable_losses", self.adapter.read_bytes(legacy.result_id))

    def test_unreadable_run_directory_does_not_hide_stage_results(self):
        """阶段结果是浏览主来源：旧运行目录不可读时仍完整列出。"""
        self._save_all_stages()

        class _BrokenRuns:
            def list_runs(self):
                raise OSError("运行目录不可读")

        adapter = ResultQueryAdapter(
            self.paths,
            run_history=_BrokenRuns(),
            stage_results=self.stage_repository,
        )

        with self.assertLogs("infrastructure.gui_data_service", level="WARNING"):
            rows = adapter.list_results()

        self.assertEqual(len(rows), 3)
        self.assertEqual({row.source for row in rows}, {RESULT_SOURCE_STAGE})

    def test_browsing_loading_and_exporting_survive_run_deletion(self):
        """阶段文件是权威来源：删除来源运行目录后仍可列出、加载和导出。"""
        reference = self.stage_repository.save(
            MeasurementStage.DRIVER_MAPPING, _driver_mapping_result()
        )
        self.runs.create_run({"run_id": DRIVER_RUN}, run_id=DRIVER_RUN)
        recorder = RunStageRecorder(
            self.runs, DRIVER_RUN, stage_results=self.stage_repository
        )
        recorder.complete_with_stage_result(
            STAGE_DRIVER_MAPPING, reference, now=MOMENT
        )
        recorder.skip(STAGE_CABLE_LOSS, now=MOMENT)
        recorder.skip(STAGE_AMPLIFIER, now=MOMENT)
        recorder.complete_run(now=MOMENT)

        FileRunDeletion(runs=self.runs).delete_run(DRIVER_RUN)
        self.assertFalse(self.runs.layout(DRIVER_RUN).run_directory.exists())

        rows = self.adapter.list_results()

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.run_id, DRIVER_RUN)
        loaded = self.adapter.load(row.result_id)
        self.assertEqual(loaded.result.measurement_id, DRIVER_ID)
        self.assertEqual(
            self.adapter.read_bytes(row.result_id), reference.path.read_bytes()
        )

    def test_export_uses_the_bytes_validated_by_the_load(self):
        """导出返回加载时校验过的字节，不重读已被替换的文件。"""
        reference = self.stage_repository.save(
            MeasurementStage.CABLE_LOSS, _cable_loss_result()
        )
        original = reference.path.read_bytes()
        replacement = b'{"run_id": "replaced"}'
        replacing = _ReplacingStageResults(self.stage_repository, replacement)
        adapter = ResultQueryAdapter(
            self.paths,
            run_history=FileRunHistoryRepository(runs=self.runs),
            stage_results=replacing,
        )

        raw = adapter.read_bytes(f"stage::cable_loss::{CABLE_ID}")

        self.assertTrue(replacing.replaced)
        self.assertEqual(raw, original)
        self.assertEqual(reference.path.read_bytes(), replacement)

    def test_export_refuses_a_file_changed_after_a_plain_load(self):
        """实现没有返回已校验字节时，重读内容必须与加载时的摘要一致。"""
        self.stage_repository.save(
            MeasurementStage.CABLE_LOSS, _cable_loss_result()
        )
        replacing = _ReplacingStageResults(
            self.stage_repository, b'{"run_id": "replaced"}', keep_bytes=False
        )
        adapter = ResultQueryAdapter(
            self.paths,
            run_history=FileRunHistoryRepository(runs=self.runs),
            stage_results=replacing,
        )

        with self.assertRaisesRegex(ValueError, "发生变化"):
            adapter.read_bytes(f"stage::cable_loss::{CABLE_ID}")

    def test_extreme_and_naive_save_times_do_not_break_listing(self):
        """极端但合法的保存时间与无时区文本不会让整轮列举失败。"""
        directory = self.paths.cable_loss_results_dir
        directory.mkdir(parents=True, exist_ok=True)
        extreme_id = _identity("11112222")
        naive_id = _identity("33334444")
        for identity, created_at in (
            (extreme_id, "0001-01-01T00:00:00+00:00"),
            (naive_id, "2026-11-12T05:00:00"),
        ):
            document = _cable_loss_result(identity).to_dict()
            document["created_at"] = created_at
            (directory / f"{identity}.json").write_text(
                json.dumps(document), encoding="utf-8"
            )

        rows = self.adapter.list_results()

        # 无时区时间按 UTC 解释，因此 05:00 比 0001 年更新；两者都不会抛错。
        self.assertEqual(
            [row.filename for row in rows],
            [f"{naive_id}.json", f"{extreme_id}.json"],
        )

    def test_malformed_stage_result_ids_are_rejected(self):
        self.stage_repository.save(MeasurementStage.CABLE_LOSS, _cable_loss_result())
        missing = _identity("99998888")

        for result_id in (
            "stage::unknown::" + CABLE_ID,
            "stage::cable_loss::../escape",
            f"stage::cable_loss::{missing}",
            "stage::cable_loss::",
        ):
            with self.subTest(result_id=result_id):
                with self.assertRaises(ValueError):
                    self.adapter.load(result_id)
                with self.assertRaises(ValueError):
                    self.adapter.read_bytes(result_id)

    def test_without_a_stage_repository_only_legacy_run_results_are_listed(self):
        self.stage_repository.save(MeasurementStage.CABLE_LOSS, _cable_loss_result())
        self.runs.create_run({"run_id": LEGACY_RUN}, run_id=LEGACY_RUN)
        RunStageRecorder(self.runs, LEGACY_RUN).complete(
            STAGE_CABLE_LOSS,
            {
                "run_id": LEGACY_RUN,
                "measurement_type": "cable_loss",
                "status": "completed",
                "cable_losses": {"1.0": {"cable1": 1.0}},
            },
        )
        adapter = ResultQueryAdapter(
            self.paths,
            run_history=FileRunHistoryRepository(runs=self.runs),
        )

        rows = adapter.list_results()

        self.assertEqual({row.source for row in rows}, {RESULT_SOURCE_RUN})
        with self.assertRaises(ValueError):
            adapter.load(f"stage::cable_loss::{CABLE_ID}")


if __name__ == "__main__":
    unittest.main()
