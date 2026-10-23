import unittest
import warnings
from pathlib import Path
from unittest.mock import Mock

from application.inputs import ResultInputReader
from application.ports.result_input_reader import ResultInputReader as ResultInputReaderPort


class ResultInputReaderTests(unittest.TestCase):
    def test_reader_implements_application_input_port(self):
        self.assertIsInstance(ResultInputReader, type)
        self.assertTrue(issubclass(ResultInputReader, ResultInputReaderPort))

    def test_reads_cable_loss_from_repository_default_path(self):
        repository = Mock()
        repository.legacy_path.return_value = Path("results/cable_loss_results.json")
        repository.load.return_value = {"cable_losses": {"1.0": {"cable1": 0.5}}}
        reader = ResultInputReader(repository)

        result = reader.read_cable_loss()

        self.assertEqual(result["cable_losses"]["1.0"]["cable1"], 0.5)
        repository.legacy_path.assert_called_once_with("cable_loss")
        repository.load.assert_called_once_with(Path("results/cable_loss_results.json"))

    def test_explicit_driver_mapping_path_does_not_scan_latest(self):
        repository = Mock()
        repository.load.return_value = {"power_mapping": {"1.0": 2.0}}
        reader = ResultInputReader(repository)

        result = reader.read_driver_mapping(Path("provided/mapping.json"))

        self.assertEqual(result, {"1.0": 2.0})
        repository.load.assert_called_once_with((Path("provided/mapping.json")).resolve())
        repository.latest_path.assert_not_called()

    def test_implicit_driver_mapping_lookup_is_deprecated_compatibility(self):
        repository = Mock()
        repository.latest_path.return_value = Path("results/driver_power_mapping_old.json")
        reader = ResultInputReader(repository)

        with self.assertWarnsRegex(DeprecationWarning, "兼容读取已弃用"):
            result = reader.driver_mapping_path()

        self.assertEqual(result, Path("results/driver_power_mapping_old.json"))
        repository.latest_path.assert_called_once_with("driver_power_mapping")

    def test_explicit_driver_mapping_path_does_not_warn_or_scan_latest(self):
        repository = Mock()
        reader = ResultInputReader(repository)

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            result = reader.driver_mapping_path("provided/mapping.json")

        self.assertEqual(result, Path("provided/mapping.json").resolve())
        repository.latest_path.assert_not_called()

    def test_driver_mapping_requires_power_mapping_field(self):
        repository = Mock()
        repository.load.return_value = {"result": {}}

        with self.assertRaisesRegex(ValueError, "power_mapping"):
            ResultInputReader(repository).read_driver_mapping("mapping.json")


if __name__ == "__main__":
    unittest.main()
