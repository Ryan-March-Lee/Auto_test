"""步骤 10：用户显式选择阶段结果文件的读取边界。

文件选择器只负责给出路径；阶段目录边界、JSON、正式 DTO、类型、状态与身份由
阶段结果仓库校验。本模块验证结果查询适配器把这份能力交给页面时，既不扫描目录，
也不在失败时回退到“最新结果”。
"""

import json
import tempfile
import unittest
from pathlib import Path

from application.dto import (
    CableLossMeasurementResult,
    CableLossRow,
    generate_measurement_id,
)
from application.ports.stage_result_repository import (
    MeasurementStage,
    StageResultError,
)
from infrastructure.filesystem.paths import PathService
from infrastructure.gui_data_service import ResultQueryAdapter
from infrastructure.persistence.stage_result_repository import FileStageResultRepository


RUN_ID = "20261111T010203Z_a1b2c3d4"


class StageResultFileSelectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.paths = PathService(Path(temporary.name))
        self.repository = FileStageResultRepository(paths=self.paths)

    def _adapter(self, *, with_stage_results=True):
        return ResultQueryAdapter(
            self.paths,
            run_history=None,
            stage_results=self.repository if with_stage_results else None,
        )

    def _saved_cable_loss(self):
        result = CableLossMeasurementResult(
            run_id=RUN_ID,
            measurement_id=generate_measurement_id(),
            points=(
                CableLossRow(
                    frequency_ghz="3.5", cable_losses={"cable1": 1.111}
                ),
            ),
        )
        return self.repository.save(MeasurementStage.CABLE_LOSS, result)

    def test_selected_stage_file_is_loaded_with_its_filename(self):
        reference = self._saved_cable_loss()

        loaded = self._adapter().load_stage_result(
            MeasurementStage.CABLE_LOSS, str(reference.path)
        )

        self.assertIsInstance(loaded.result, CableLossMeasurementResult)
        self.assertEqual(loaded.result.measurement_id, reference.measurement_id)
        self.assertEqual(loaded.result.cable_losses()["3.5"]["cable1"], 1.111)
        self.assertEqual(loaded.filename, reference.path.name)
        self.assertIn(reference.measurement_id, loaded.result_id)

    def test_a_file_outside_the_stage_directory_is_rejected(self):
        self._saved_cable_loss()
        outside = self.paths.test_results_dir / "cable_loss_results.json"
        outside.write_text("{}", encoding="utf-8")

        with self.assertRaisesRegex(StageResultError, "阶段目录"):
            self._adapter().load_stage_result(
                MeasurementStage.CABLE_LOSS, str(outside)
            )

    def test_a_corrupted_selected_file_is_rejected(self):
        identity = generate_measurement_id()
        self.paths.cable_loss_results_dir.mkdir(parents=True)
        broken = self.paths.cable_loss_results_dir / f"{identity}.json"
        broken.write_text(json.dumps({"broken": True}), encoding="utf-8")

        with self.assertRaises(ValueError):
            self._adapter().load_stage_result(
                MeasurementStage.CABLE_LOSS, str(broken)
            )

    def test_a_missing_stage_repository_rejects_the_selection(self):
        reference = self._saved_cable_loss()

        with self.assertRaisesRegex(ValueError, "阶段结果仓储未装配"):
            self._adapter(with_stage_results=False).load_stage_result(
                MeasurementStage.CABLE_LOSS, str(reference.path)
            )


if __name__ == "__main__":
    unittest.main()
