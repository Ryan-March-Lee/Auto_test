"""显式选择阶段结果的加载与“选择时摘要”复核（步骤 14）。

覆盖：

- 摘要与大小与用户选择时一致时返回**同一次读取**的已加载结果；
- 省略摘要时仍完成完整阶段契约校验（旧调用方兼容）；
- 文件被替换成另一份同样合法的结果、大小变化或摘要变化都被拒绝；
- 拒绝发生在任何测量输入被构造之前，错误类型是 ``StageInputUnavailable``；
- 已加载的正式结果可直接转成校准输入，不再按路径二次读取文件。
"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from application.dto import (
    CableLossMeasurementResult,
    CableLossRow,
    DriverMappingRawPoint,
    DriverMappingTargetPoint,
    DriverPowerMappingMeasurementResult,
    generate_measurement_id,
)
from application.dto.measurement_requests import StageResultDigest
from application.inputs import ResultInputReader
from application.inputs.explicit_stage_input import (
    load_selected_stage_result,
    require_selected_result_unchanged,
)
from application.dto.measurement_results import measurement_result_from_document
from application.ports.stage_result_repository import MeasurementStage
from application.run_stages import StageInputUnavailable
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence.stage_result_repository import FileStageResultRepository


MOMENT = datetime(2026, 11, 11, 1, 2, 3, tzinfo=timezone.utc)
CABLE_LOSSES = {
    "2.4": {"cable1": 1.0, "cable2": 0.5, "cable3": 0.0, "cable4": 0.5}
}


def _cable_loss_result(identity=None, run_id="run-1", losses=None):
    return CableLossMeasurementResult(
        run_id=run_id,
        measurement_id=identity or generate_measurement_id(MOMENT, token="a1b2c3d4"),
        points=tuple(
            CableLossRow(frequency_ghz=str(frequency), cable_losses=values)
            for frequency, values in (losses or CABLE_LOSSES).items()
        ),
    )


def _driver_mapping_result(identity=None):
    return DriverPowerMappingMeasurementResult(
        run_id="run-1",
        measurement_id=identity or generate_measurement_id(MOMENT, token="ffee0011"),
        raw_points=(
            DriverMappingRawPoint(
                frequency_ghz="1.0",
                source_power_dbm=-20.0,
                driver_output_power_dbm=2.0,
            ),
        ),
        target_points=(
            DriverMappingTargetPoint(
                frequency_ghz="1.0",
                target_input_power_dbm=2.0,
                source_power_dbm=-20.0,
                driver_output_power_dbm=2.0,
            ),
        ),
    )


class ExplicitStageInputLoadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.paths = PathService(Path(temporary.name))
        self.repository = FileStageResultRepository(paths=self.paths)

    def _save(self, result=None):
        result = result or _cable_loss_result()
        reference = self.repository.save(MeasurementStage.CABLE_LOSS, result)
        return result, reference

    @staticmethod
    def _digest(reference):
        return StageResultDigest(
            sha256=reference.sha256, size_bytes=reference.size_bytes
        )

    def test_matching_digest_returns_the_loaded_result(self):
        _result, reference = self._save()

        loaded = load_selected_stage_result(
            self.repository,
            MeasurementStage.CABLE_LOSS,
            reference.path,
            digest=self._digest(reference),
            label="线损",
        )

        self.assertEqual(loaded.path, reference.path)
        self.assertEqual(loaded.sha256, reference.sha256)
        self.assertEqual(loaded.size_bytes, reference.size_bytes)

    def test_digest_is_optional_for_legacy_callers(self):
        _result, reference = self._save()

        loaded = load_selected_stage_result(
            self.repository,
            MeasurementStage.CABLE_LOSS,
            reference.path,
            label="线损",
        )

        self.assertEqual(loaded.path, reference.path)

    def test_file_replaced_by_an_equally_valid_result_is_rejected(self):
        result, reference = self._save()
        digest = self._digest(reference)
        # 同一路径换成同一测量身份、但内容不同的另一份合法结果：阶段契约、
        # 文件名与状态都继续通过，只有摘要/大小不同。
        replacement = _cable_loss_result(
            identity=result.measurement_id,
            run_id="run-2",
            losses={
                "2.4": {"cable1": 9.0, "cable2": 9.0, "cable3": 9.0, "cable4": 9.0}
            },
        )
        reference.path.write_text(
            json.dumps(replacement.to_dict()), encoding="utf-8"
        )

        with self.assertRaisesRegex(StageInputUnavailable, "已被修改或替换"):
            load_selected_stage_result(
                self.repository,
                MeasurementStage.CABLE_LOSS,
                reference.path,
                digest=digest,
                label="线损",
            )

    def test_missing_file_fails_before_any_input_is_built(self):
        _result, reference = self._save()
        digest = self._digest(reference)
        reference.path.unlink()

        with self.assertRaises(ValueError):
            load_selected_stage_result(
                self.repository,
                MeasurementStage.CABLE_LOSS,
                reference.path,
                digest=digest,
                label="线损",
            )

    def test_require_unchanged_rejects_a_size_only_mismatch(self):
        loaded = SimpleNamespace(sha256="a" * 64, size_bytes=10, path=Path("a.json"))

        with self.assertRaisesRegex(StageInputUnavailable, "已被修改或替换"):
            require_selected_result_unchanged(
                loaded, StageResultDigest(sha256="a" * 64, size_bytes=11), label="线损"
            )

    def test_require_unchanged_accepts_the_same_bytes(self):
        reference_digest = StageResultDigest(sha256="b" * 64, size_bytes=10)
        loaded = SimpleNamespace(
            sha256="b" * 64, size_bytes=10, path=Path("b.json")
        )

        self.assertIs(
            require_selected_result_unchanged(loaded, reference_digest, label="线损"),
            loaded,
        )


class LoadedResultConversionTests(unittest.TestCase):
    """已加载的正式结果直接转成校准输入，不再按路径读取文件。"""

    def _reader(self):
        repository = Mock()
        repository.load.side_effect = AssertionError("不得再按路径读取文件")
        return ResultInputReader(repository)

    def test_cable_loss_result_is_converted_without_reading_a_file(self):
        result = _cable_loss_result()

        data = self._reader().read_cable_loss_result(result)

        self.assertEqual(data["cable_losses"]["2.4"]["cable2"], 0.5)

    def test_driver_mapping_result_is_converted_without_reading_a_file(self):
        result = measurement_result_from_document(_driver_mapping_result().to_dict())

        target = self._reader().read_driver_mapping_result(result)

        self.assertEqual(len(target.target_points), 1)
        self.assertEqual(target.target_points[0].target_input_power_dbm, 2.0)

    def test_non_formal_values_are_rejected(self):
        reader = self._reader()
        for value in ({"cable_losses": {}}, None, "not-a-result"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                reader.read_cable_loss_result(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                reader.read_driver_mapping_result(value)


if __name__ == "__main__":
    unittest.main()
