import unittest
import warnings
from pathlib import Path
from unittest.mock import Mock

from application.inputs import ResultInputReader
from application.ports.result_input_reader import ResultInputReader as ResultInputReaderPort


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

    def test_reads_cable_loss_from_explicit_repository_path(self):
        repository = Mock()
        repository.load.return_value = {"cable_losses": {"1.0": {"cable1": 0.5}}}
        reader = ResultInputReader(repository)

        result = reader.read_cable_loss(Path("runs/run-1/inputs/cable_loss.json"))

        self.assertEqual(result["cable_losses"]["1.0"]["cable1"], 0.5)
        repository.load.assert_called_once_with(
            Path("runs/run-1/inputs/cable_loss.json").resolve()
        )

    def test_explicit_driver_mapping_path_does_not_scan_latest(self):
        repository = Mock()
        repository.load.return_value = {"power_mapping": {"1.0": 2.0}}
        reader = ResultInputReader(repository)

        result = reader.read_driver_mapping(Path("provided/mapping.json"))

        self.assertEqual(result, {"1.0": 2.0})
        repository.load.assert_called_once_with((Path("provided/mapping.json")).resolve())

    def test_explicit_path_does_not_warn(self):
        repository = Mock()
        repository.load.return_value = {"power_mapping": {}}
        reader = ResultInputReader(repository)

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            reader.read_cable_loss("provided/loss.json")
            reader.read_driver_mapping("provided/mapping.json")

    def test_missing_path_is_rejected_instead_of_scanning_latest_results(self):
        repository = Mock()
        reader = ResultInputReader(repository)

        with self.assertRaisesRegex(ValueError, "必须显式提供"):
            reader.read_cable_loss(None)
        with self.assertRaisesRegex(ValueError, "必须显式提供"):
            reader.read_driver_mapping(None)

        repository.load.assert_not_called()

    def test_driver_mapping_requires_power_mapping_field(self):
        repository = Mock()
        repository.load.return_value = {"result": {}}

        with self.assertRaisesRegex(ValueError, "power_mapping"):
            ResultInputReader(repository).read_driver_mapping("mapping.json")


if __name__ == "__main__":
    unittest.main()
