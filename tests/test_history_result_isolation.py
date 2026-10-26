"""步骤 D/6 测试：历史查看/导出与正式运行输入的隔离。

覆盖三条独立职责：

- 历史查看：只读端口只能看到运行目录内已登记的结果，``test_results`` 根目录下的
  旧全局结果文件不再被发现。
- 迁移副本登记：本步骤之后生产代码不再写全局兼容副本；历史目录里的旧文件即使
  存在也不参与历史查看，更不会登记进运行 manifest。
- 正式运行输入：只从当前运行 manifest 校验过的相对路径读取，更新的全局历史
  文件不会被当作“最新结果”自动带入正式运行。

注意：这里的全局历史文件由测试**直接写入磁盘**，模拟步骤 3 之前已存在的旧数据。
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from application.inputs import ResultInputReader
from application.run_stages import RunStageRecorder
from application.ports.gui_data_service import RESULT_SOURCE_RUN
from domain.run.models import CABLE_LOSS_INPUT_PATH, STAGE_CABLE_LOSS
from infrastructure.filesystem.paths import PathService
from infrastructure.gui_data_service import ResultQueryAdapter
from infrastructure.persistence.result_repository import FileMeasurementResultRepository
from infrastructure.persistence.run_history_repository import FileRunHistoryRepository
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
        self.adapter = ResultQueryAdapter(
            self.paths,
            run_history=FileRunHistoryRepository(runs=self.runs),
            runs_root=self.base / "runs",
        )

    def _complete_cable_loss(self, *, cable_losses=RUN_LOSSES):
        layout, _manifest = self.runs.create_run(
            {"run_id": RUN_ID}, run_id=RUN_ID, now=CREATED_AT
        )
        recorder = RunStageRecorder(self.runs, RUN_ID)
        recorder.start(STAGE_CABLE_LOSS, now=CREATED_AT)
        recorder.complete(
            STAGE_CABLE_LOSS,
            {
                "run_id": RUN_ID,
                "measurement_type": "cable_loss",
                "status": "completed",
                "cable_losses": cable_losses,
            },
            now=CREATED_AT,
        )
        return layout, recorder

    def _write_global_history_copy(self, cable_losses=LEGACY_LOSSES):
        """直接写一份旧全局历史文件，模拟步骤 3 之前产生的兼容副本。"""
        results_dir = self.paths.test_results_dir
        results_dir.mkdir(parents=True, exist_ok=True)
        path = results_dir / "cable_loss_results.json"
        path.write_text(
            json.dumps(
                {
                    "run_id": RUN_ID,
                    "compatibility_copy": True,
                    "cable_losses": cable_losses,
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_global_history_copy_is_not_browsable_and_not_a_run_input(self):
        layout, _recorder = self._complete_cable_loss()

        copy_path = self._write_global_history_copy()

        # 迁移副本明确标记为非权威兼容结果，且不落在运行目录内。
        payload = json.loads(copy_path.read_text(encoding="utf-8"))
        self.assertTrue(payload["compatibility_copy"])
        self.assertEqual(payload["run_id"], RUN_ID)
        self.assertNotEqual(copy_path.parent.resolve(), layout.run_directory.resolve())

        # 历史查看只列运行目录结果：根目录的旧文件不在列表里。
        rows = self.adapter.list_results()
        self.assertEqual({row.source for row in rows}, {RESULT_SOURCE_RUN})
        self.assertNotIn(copy_path.name, [row.filename for row in rows])
        self.assertEqual(
            self.adapter.latest_result("cable_loss").result.cable_losses(),
            RUN_LOSSES,
        )

        # 它没有被登记进运行 manifest，也不是阶段输入。
        manifest = self.runs.load_manifest(RUN_ID)
        self.assertNotIn(
            "cable_loss_results.json", {item.path for item in manifest.inputs}
        )
        self.assertEqual(
            manifest.stage(STAGE_CABLE_LOSS).result, CABLE_LOSS_INPUT_PATH
        )

    def test_latest_run_result_is_already_the_formal_result(self):
        """历史查询返回的已经是正式结果 DTO，不需要再解析文档。"""
        from application.dto import CableLossMeasurementResult

        self._complete_cable_loss()

        result = self.adapter.latest_result("cable_loss").result

        self.assertIsInstance(result, CableLossMeasurementResult)
        self.assertEqual(result.run_id, RUN_ID)
        self.assertEqual(result.points[0].cable_losses["cable2"], 2.0)

    def test_run_manifest_input_path_stays_inside_the_current_run(self):
        """阶段输入路径始终归属于当前运行目录，不受更新的全局副本影响。

        真实测量链是否消费全局历史文件由
        ``tests/test_driver_mapping_run_stage.py::test_newer_global_history_file_is_not_used_as_formal_input``
        覆盖；这里只验证 manifest 登记的输入路径归属。
        """
        layout, recorder = self._complete_cable_loss()

        self._write_global_history_copy()

        verified = recorder.verified_result_path(STAGE_CABLE_LOSS)

        self.assertEqual(verified, layout.run_directory / CABLE_LOSS_INPUT_PATH)
        verified.relative_to(layout.run_directory)
        self.assertEqual(
            json.loads(verified.read_text(encoding="utf-8"))["cable_losses"],
            RUN_LOSSES,
        )


if __name__ == "__main__":
    unittest.main()
