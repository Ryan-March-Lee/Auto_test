"""步骤 D 测试：历史查看/导出与正式运行输入的隔离。

覆盖三条独立职责：

- 历史查看：只读端口可以看到最新兼容副本，但读取不会改动文件。
- 迁移副本登记：兼容副本带 ``compatibility_copy`` 标记，可被历史查看，但不会
  登记进运行 manifest。
- 正式运行输入：只从当前运行 manifest 校验过的相对路径读取，更新的全局历史
  文件不会被当作“最新结果”自动带入正式运行。
"""

import json
import tempfile
import unittest
import warnings
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from application.inputs import ResultInputReader
from application.run_stages import RunStageRecorder
from domain.run.models import CABLE_LOSS_INPUT_PATH, STAGE_CABLE_LOSS
from infrastructure.filesystem.paths import PathService
from infrastructure.gui_data_service import ResultQueryAdapter
from infrastructure.persistence.result_history_repository import FileResultHistoryRepository
from infrastructure.persistence.result_repository import FileMeasurementResultRepository
from infrastructure.persistence.run_repository import RunRepository


RUN_ID = "20261023T090000Z_a1b2c3d4"
CREATED_AT = datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)
RUN_LOSSES = {"1.0": {"cable2": 2.0}}
LEGACY_LOSSES = {"1.0": {"cable2": 99.0}}


class HistoryIsolationTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.paths = PathService(self.base)
        self.runs = RunRepository(self.base / "runs")
        self.results = FileMeasurementResultRepository(
            results_directory=self.paths.test_results_dir
        )
        self.history = FileResultHistoryRepository(paths=self.paths)

    def _complete_cable_loss(self, *, cable_losses=RUN_LOSSES):
        layout, _manifest = self.runs.create_run(
            {"run_id": RUN_ID}, run_id=RUN_ID, now=CREATED_AT
        )
        recorder = RunStageRecorder(self.runs, RUN_ID)
        recorder.start(STAGE_CABLE_LOSS, now=CREATED_AT)
        recorder.complete(
            STAGE_CABLE_LOSS,
            {"run_id": RUN_ID, "cable_losses": cable_losses},
            now=CREATED_AT,
        )
        return layout, recorder

    def test_compatibility_copy_is_browsable_history_but_not_a_run_input(self):
        layout, _recorder = self._complete_cable_loss()

        copy_path = self.results.save_compatibility_copy(
            {"cable_losses": LEGACY_LOSSES},
            result_type="cable_loss",
            run_id=RUN_ID,
        )

        # 迁移副本明确标记为非权威兼容结果，且不落在运行目录内。
        payload = json.loads(copy_path.read_text(encoding="utf-8"))
        self.assertTrue(payload["compatibility_copy"])
        self.assertEqual(payload["run_id"], RUN_ID)
        self.assertNotEqual(copy_path.parent.resolve(), layout.run_directory.resolve())

        # 历史查看可以列出并读取兼容副本。
        adapter = ResultQueryAdapter(self.paths, self.history)
        self.assertIn(
            "cable_loss_results.json",
            [row.filename for row in adapter.list_results()],
        )
        self.assertEqual(
            adapter.latest_legacy_result("cable_loss").data["cable_losses"],
            LEGACY_LOSSES,
        )

        # 但它没有被登记进运行 manifest，也不是阶段输入。
        manifest = self.runs.load_manifest(RUN_ID)
        self.assertNotIn(
            "cable_loss_results.json", {item.path for item in manifest.inputs}
        )
        self.assertEqual(
            manifest.stage(STAGE_CABLE_LOSS).result, CABLE_LOSS_INPUT_PATH
        )

    def test_run_manifest_input_path_stays_inside_the_current_run(self):
        """阶段输入路径始终归属于当前运行目录，不受更新的全局副本影响。

        真实测量链是否消费全局历史文件由
        ``tests/test_driver_mapping_run_stage.py::test_newer_global_history_file_is_not_used_as_formal_input``
        覆盖；这里只验证 manifest 登记的输入路径归属。
        """
        layout, recorder = self._complete_cable_loss()

        self.results.save_compatibility_copy(
            {"cable_losses": LEGACY_LOSSES},
            result_type="cable_loss",
            run_id=RUN_ID,
        )

        verified = recorder.verified_result_path(STAGE_CABLE_LOSS)

        self.assertEqual(verified, layout.run_directory / CABLE_LOSS_INPUT_PATH)
        verified.relative_to(layout.run_directory)
        self.assertEqual(
            json.loads(verified.read_text(encoding="utf-8"))["cable_losses"],
            RUN_LOSSES,
        )

    def test_history_repository_exposes_read_only_operations_only(self):
        for name in (
            "save",
            "save_compatibility_copy",
            "latest_path",
            "legacy_path",
            "create_legacy_run_snapshot",
            "new_run_id",
        ):
            self.assertFalse(
                hasattr(FileResultHistoryRepository, name),
                f"历史结果仓储不得暴露写入能力: {name}",
            )

    def test_history_repository_cannot_read_run_directory_inputs(self):
        layout, _recorder = self._complete_cable_loss()
        input_path = layout.run_directory / CABLE_LOSS_INPUT_PATH

        for result_id in (str(input_path), f"runs/{RUN_ID}/{CABLE_LOSS_INPUT_PATH}"):
            with self.assertRaises(ValueError):
                self.history.load(result_id)
            with self.assertRaises(ValueError):
                self.history.read_bytes(result_id)

    def test_formal_input_reader_reads_manifest_path_without_latest_lookup(self):
        layout, recorder = self._complete_cable_loss()
        self.results.save_compatibility_copy(
            {"cable_losses": LEGACY_LOSSES},
            result_type="cable_loss",
            run_id=RUN_ID,
        )
        verified = recorder.verified_result_path(STAGE_CABLE_LOSS)
        reader = ResultInputReader(self.results)

        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always", DeprecationWarning)
            with patch.object(
                self.results,
                "latest_path",
                side_effect=AssertionError("正式输入不得按最新结果查找"),
            ):
                data = reader.read_cable_loss(verified)

        self.assertEqual(data["cable_losses"], RUN_LOSSES)
        self.assertEqual(verified, layout.run_directory / CABLE_LOSS_INPUT_PATH)
        self.assertFalse(
            [
                item
                for item in captured
                if issubclass(item.category, DeprecationWarning)
            ]
        )


if __name__ == "__main__":
    unittest.main()
