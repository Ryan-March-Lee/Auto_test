import unittest
import warnings
from pathlib import Path
from unittest.mock import Mock

from application.inputs import LegacyResultInputReader, ResultInputReader
from application.ports.legacy_result_input_reader import (
    LegacyResultInputReader as LegacyResultInputReaderPort,
)
from application.ports.result_input_reader import ResultInputReader as ResultInputReaderPort


class ResultInputReaderTests(unittest.TestCase):
    def test_reader_implements_application_input_port(self):
        self.assertTrue(issubclass(ResultInputReader, ResultInputReaderPort))
        self.assertFalse(
            issubclass(ResultInputReader, LegacyResultInputReaderPort),
            "正式输入读取器不得暴露无路径兼容读取能力",
        )

    def test_reads_cable_loss_from_explicit_repository_path(self):
        repository = Mock()
        repository.load.return_value = {"cable_losses": {"1.0": {"cable1": 0.5}}}
        reader = ResultInputReader(repository)

        result = reader.read_cable_loss(Path("runs/run-1/inputs/cable_loss.json"))

        self.assertEqual(result["cable_losses"]["1.0"]["cable1"], 0.5)
        repository.load.assert_called_once_with(
            Path("runs/run-1/inputs/cable_loss.json").resolve()
        )
        repository.legacy_path.assert_not_called()
        repository.latest_path.assert_not_called()

    def test_explicit_driver_mapping_path_does_not_scan_latest(self):
        repository = Mock()
        repository.load.return_value = {"power_mapping": {"1.0": 2.0}}
        reader = ResultInputReader(repository)

        result = reader.read_driver_mapping(Path("provided/mapping.json"))

        self.assertEqual(result, {"1.0": 2.0})
        repository.load.assert_called_once_with((Path("provided/mapping.json")).resolve())
        repository.latest_path.assert_not_called()

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

        repository.legacy_path.assert_not_called()
        repository.latest_path.assert_not_called()
        repository.load.assert_not_called()

    def test_driver_mapping_requires_power_mapping_field(self):
        repository = Mock()
        repository.load.return_value = {"result": {}}

        with self.assertRaisesRegex(ValueError, "power_mapping"):
            ResultInputReader(repository).read_driver_mapping("mapping.json")


class LegacyResultInputReaderTests(unittest.TestCase):
    def test_legacy_reader_implements_both_ports(self):
        self.assertTrue(issubclass(LegacyResultInputReader, ResultInputReaderPort))
        self.assertTrue(issubclass(LegacyResultInputReader, LegacyResultInputReaderPort))

    def test_legacy_cable_loss_reads_fixed_compatibility_path_with_warning(self):
        repository = Mock()
        repository.legacy_path.return_value = Path("results/cable_loss_results.json")
        repository.load.return_value = {"cable_losses": {"1.0": {"cable1": 0.5}}}
        reader = LegacyResultInputReader(repository)

        with self.assertWarnsRegex(DeprecationWarning, "兼容读取已弃用"):
            result = reader.read_legacy_cable_loss()

        self.assertEqual(result["cable_losses"]["1.0"]["cable1"], 0.5)
        repository.legacy_path.assert_called_once_with("cable_loss")
        repository.load.assert_called_once_with(Path("results/cable_loss_results.json"))

    def test_legacy_driver_mapping_reads_latest_compatibility_result(self):
        repository = Mock()
        repository.latest_path.return_value = Path("results/driver_power_mapping_old.json")
        repository.load.return_value = {"power_mapping": {"1.0": 2.0}}
        reader = LegacyResultInputReader(repository)

        with self.assertWarnsRegex(DeprecationWarning, "兼容读取已弃用"):
            result = reader.read_latest_legacy_driver_mapping()

        self.assertEqual(result, {"1.0": 2.0})
        repository.latest_path.assert_called_once_with("driver_power_mapping")

    def test_legacy_reader_still_supports_explicit_paths_without_warning(self):
        repository = Mock()
        repository.load.return_value = {"power_mapping": {"1.0": 2.0}}
        reader = LegacyResultInputReader(repository)

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            result = reader.read_driver_mapping("provided/mapping.json")

        self.assertEqual(result, {"1.0": 2.0})
        repository.latest_path.assert_not_called()


if __name__ == "__main__":
    unittest.main()
