import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import Mock

from application.dto import (
    CableLossMeasurementResult,
    CableLossRow,
    DriverMappingInput,
    DriverMappingRawPoint,
    DriverMappingTargetPoint,
    DriverPowerMappingMeasurementResult,
    generate_measurement_id,
)
from application.inputs import ResultInputReader
from application.ports.result_input_reader import ResultInputReader as ResultInputReaderPort
from application.ports.stage_result_repository import MeasurementStage
from infrastructure.filesystem.paths import PathService
from infrastructure.persistence.result_repository import FileMeasurementResultRepository
from infrastructure.persistence.stage_result_repository import FileStageResultRepository


def _driver_document(**overrides):
    document = {
        "run_id": "run-1",
        "measurement_type": "driver_power_mapping",
        "status": "completed",
        "schema_version": "2.0",
        "raw_points": {
            "1.0": [
                {
                    "source_power_dbm": -20.0,
                    "driver_output_power_dbm": 2.0,
                    "gain_db": 22.0,
                }
            ]
        },
        "target_mapping": {
            "1.0": [
                {
                    "target_input_power_dbm": 2.0,
                    "source_power_dbm": -20.0,
                    "driver_output_power_dbm": 2.0,
                }
            ]
        },
    }
    document.update(overrides)
    return document


def _cable_loss_result(run_id="run-1"):
    return CableLossMeasurementResult(
        run_id=run_id,
        measurement_id=generate_measurement_id(),
        attenuator_value=10.0,
        points=tuple(
            CableLossRow(
                frequency_ghz=frequency,
                cable_losses={
                    "cable1": value,
                    "cable2": value,
                    "cable3": value,
                    "cable4": value,
                },
            )
            for frequency, value in (("1.0", 0.5), ("2.4", 0.7))
        ),
    )


def _cable_loss_document():
    return {
        "run_id": "run-1",
        "measurement_type": "cable_loss",
        "status": "completed",
        "cable_losses": {"1.0": {"cable1": 0.5}},
    }


class ResultInputReaderTests(unittest.TestCase):
    def test_application_input_package_exports_only_the_formal_reader(self):
        import application.inputs as inputs

        self.assertEqual(inputs.__all__, ["ResultInputReader"])
        self.assertIsNotNone(inputs.ResultInputReader)
        self.assertFalse(hasattr(inputs, "LegacyResultInputReader"))

    def test_reader_implements_application_input_port(self):
        self.assertTrue(issubclass(ResultInputReader, ResultInputReaderPort))
        # 步骤 B3 已删除无路径兼容读取能力，正式读取器不得再暴露这些方法。
        self.assertFalse(hasattr(ResultInputReader, "read_legacy_cable_loss"))
        self.assertFalse(hasattr(ResultInputReader, "read_latest_legacy_driver_mapping"))

    def test_reads_legacy_run_payload_from_explicit_repository_path(self):
        """步骤 7 前没有 measurement_type 的旧线损载荷仍按只读兼容读取。"""

        repository = Mock()
        repository.load.return_value = {"cable_losses": {"1.0": {"cable1": 0.5}}}
        reader = ResultInputReader(repository)

        result = reader.read_cable_loss(Path("runs/run-1/inputs/cable_loss.json"))

        self.assertEqual(result["cable_losses"]["1.0"]["cable1"], 0.5)
        repository.load.assert_called_once_with(
            Path("runs/run-1/inputs/cable_loss.json").resolve()
        )

    def test_cable_loss_is_read_from_a_stage_result_file(self):
        """显式阶段文件经正式 DTO 校验后可作为线损输入。"""

        with tempfile.TemporaryDirectory() as temporary:
            paths = PathService(Path(temporary))
            stage_repository = FileStageResultRepository(paths=paths)
            reference = stage_repository.save(
                MeasurementStage.CABLE_LOSS, _cable_loss_result()
            )
            reader = ResultInputReader(FileMeasurementResultRepository(paths=paths))

            document = reader.read_cable_loss(reference.path)

            self.assertEqual(document["measurement_type"], "cable_loss")
            self.assertEqual(document["status"], "completed")
            self.assertEqual(document["cable_losses"]["1.0"]["cable1"], 0.5)
            self.assertEqual(document["cable_losses"]["2.4"]["cable4"], 0.7)
            self.assertEqual(document["measurement_id"], reference.measurement_id)

    def test_driver_mapping_is_read_from_a_stage_result_file(self):
        """显式阶段文件同样是驱动映射输入的正式来源。"""

        result = DriverPowerMappingMeasurementResult(
            run_id="run-1",
            measurement_id=generate_measurement_id(),
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
        with tempfile.TemporaryDirectory() as temporary:
            paths = PathService(Path(temporary))
            stage_repository = FileStageResultRepository(paths=paths)
            reference = stage_repository.save(
                MeasurementStage.DRIVER_MAPPING, result
            )
            reader = ResultInputReader(FileMeasurementResultRepository(paths=paths))

            driver_input = reader.read_driver_mapping(reference.path)

            self.assertEqual(
                driver_input.target_mapping(), {"1.0": {2.0: (-20.0, 2.0)}}
            )

    def test_cable_loss_reader_rejects_a_formal_document_of_another_type(self):
        repository = Mock()
        repository.load.return_value = _driver_document()

        with self.assertRaisesRegex(ValueError, "线损输入必须是 cable_loss"):
            ResultInputReader(repository).read_cable_loss("mapping.json")

    def test_driver_mapping_reader_rejects_a_cable_loss_document(self):
        repository = Mock()
        repository.load.return_value = _cable_loss_document()

        with self.assertRaisesRegex(ValueError, "驱动映射输入必须是"):
            ResultInputReader(repository).read_driver_mapping("cable_loss.json")

    def test_legacy_cable_loss_payload_requires_cable_losses(self):
        repository = Mock()
        repository.load.return_value = {"run_id": "run-1"}

        with self.assertRaisesRegex(ValueError, "cable_losses"):
            ResultInputReader(repository).read_cable_loss("loss.json")

    def test_legacy_cable_loss_payload_rejects_a_scalar_frequency_profile(self):
        """旧载荷的频点值必须是线缆字段映射，读取器边界就要失败。"""

        repository = Mock()
        repository.load.return_value = {"cable_losses": {"1.0": 0.5}}

        with self.assertRaisesRegex(ValueError, "线损输入"):
            ResultInputReader(repository).read_cable_loss("loss.json")

    def test_legacy_cable_loss_payload_rejects_unknown_cable_fields(self):
        repository = Mock()
        repository.load.return_value = {
            "cable_losses": {"1.0": {"cable1": 0.5, "cable9": 0.1}}
        }

        with self.assertRaisesRegex(ValueError, "未知线缆字段"):
            ResultInputReader(repository).read_cable_loss("loss.json")

    def test_formal_cable_loss_document_rejects_malformed_loss_table(self):
        """正式文档也必须通过线损表结构校验，不能只靠 measurement_type。"""

        document = _cable_loss_document()
        document["cable_losses"] = {"1.0": 0.5}
        repository = Mock()
        repository.load.return_value = document

        with self.assertRaises(ValueError):
            ResultInputReader(repository).read_cable_loss("loss.json")

    def test_formal_cable_loss_document_requires_identity_fields(self):
        document = _cable_loss_document()
        document.pop("status")
        repository = Mock()
        repository.load.return_value = document

        with self.assertRaises(ValueError):
            ResultInputReader(repository).read_cable_loss("loss.json")

    def test_corrupt_stage_file_fails_before_any_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            paths = PathService(Path(temporary))
            directory = paths.cable_loss_results_dir
            directory.mkdir(parents=True)
            path = directory / f"{generate_measurement_id()}.json"
            path.write_text(
                '{"measurement_type": "cable_loss", ', encoding="utf-8"
            )
            reader = ResultInputReader(FileMeasurementResultRepository(paths=paths))

            with self.assertRaises(ValueError):
                reader.read_cable_loss(path)

    def test_explicit_driver_mapping_path_returns_the_target_grid(self):
        repository = Mock()
        repository.load.return_value = _driver_document()
        reader = ResultInputReader(repository)

        result = reader.read_driver_mapping(Path("provided/mapping.json"))

        self.assertIsInstance(result, DriverMappingInput)
        # 正式输入的主视图是目标网格，不是原始“源功率 -> 输出”审计表。
        self.assertEqual(result.target_mapping(), {"1.0": {2.0: (-20.0, 2.0)}})
        self.assertEqual(result.raw_mapping(), {"1.0": {"-20.0": 2.0}})
        self.assertEqual(result.target_points[0].target_input_power_dbm, 2.0)
        repository.load.assert_called_once_with((Path("provided/mapping.json")).resolve())

    def test_empty_target_grid_is_not_a_formal_input(self):
        repository = Mock()
        repository.load.return_value = _driver_document(target_mapping={})

        with self.assertRaisesRegex(ValueError, "目标网格"):
            ResultInputReader(repository).read_driver_mapping("mapping.json")

    def test_target_frequency_must_have_raw_points(self):
        document = _driver_document()
        document["target_mapping"]["2.4"] = document["target_mapping"].pop("1.0")
        repository = Mock()
        repository.load.return_value = document

        with self.assertRaisesRegex(ValueError, "缺少原始实测点"):
            ResultInputReader(repository).read_driver_mapping("mapping.json")

    def test_target_source_power_must_stay_inside_the_raw_curve(self):
        document = _driver_document()
        document["target_mapping"]["1.0"][0]["source_power_dbm"] = -30.0
        repository = Mock()
        repository.load.return_value = document

        with self.assertRaisesRegex(ValueError, "超出原始映射覆盖范围"):
            ResultInputReader(repository).read_driver_mapping("mapping.json")

    def test_explicit_path_does_not_warn(self):
        repository = Mock()
        reader = ResultInputReader(repository)

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            repository.load.return_value = _cable_loss_document()
            reader.read_cable_loss("provided/loss.json")
            repository.load.return_value = _driver_document()
            reader.read_driver_mapping("provided/mapping.json")

    def test_missing_path_is_rejected_instead_of_scanning_latest_results(self):
        repository = Mock()
        reader = ResultInputReader(repository)

        with self.assertRaisesRegex(ValueError, "必须显式提供"):
            reader.read_cable_loss(None)
        with self.assertRaisesRegex(ValueError, "必须显式提供"):
            reader.read_driver_mapping(None)

        repository.load.assert_not_called()

    def test_legacy_power_mapping_document_is_rejected(self):
        repository = Mock()
        repository.load.return_value = {
            "run_id": "run-1",
            "measurement_type": "driver_power_mapping",
            "status": "completed",
            "power_mapping": {"1.0": {"-20.0": 2.0}},
        }

        with self.assertRaisesRegex(ValueError, "power_mapping"):
            ResultInputReader(repository).read_driver_mapping("mapping.json")

    def test_driver_mapping_requires_new_schema_version(self):
        repository = Mock()
        document = _driver_document()
        document.pop("schema_version")
        repository.load.return_value = document

        with self.assertRaisesRegex(ValueError, "schema_version"):
            ResultInputReader(repository).read_driver_mapping("mapping.json")


if __name__ == "__main__":
    unittest.main()
