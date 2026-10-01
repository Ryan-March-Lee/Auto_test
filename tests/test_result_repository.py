import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from infrastructure.persistence.result_repository import FileMeasurementResultRepository
from infrastructure.persistence.json_encoder import NumpyJSONEncoder
from application.ports.result_repository import SavedMeasurementResult
from result_storage import load_json_result


class FileMeasurementResultRepositoryTests(unittest.TestCase):
    def test_save_maps_result_type_and_returns_persistence_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            run_directory = Path(directory) / "run-1"
            expected_archive = run_directory / "cable_loss_results.json"
            expected_legacy = Path(directory) / "cable_loss_results.json"
            with patch(
                "infrastructure.persistence.result_repository.result_storage.save_measurement_result",
                return_value=(expected_archive, expected_legacy),
            ) as save:
                saved = FileMeasurementResultRepository(results_directory=directory).save(
                    {"value": 1},
                    result_type="cable_loss",
                    run_id="run-1",
                    run_directory=run_directory,
                )

        self.assertIsInstance(saved, SavedMeasurementResult)
        self.assertEqual(saved.archive_path, expected_archive)
        self.assertEqual(saved.legacy_copy_path, expected_legacy)
        self.assertEqual(saved.run_directory, run_directory)
        self.assertEqual(saved.run_id, "run-1")
        save.assert_called_once_with(
            {"value": 1},
            result_type="cable_loss",
            legacy_path=expected_legacy,
            run_id="run-1",
            run_directory=run_directory,
            encoder=NumpyJSONEncoder,
        )

    def test_custom_results_directory_controls_archive_and_legacy_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            saved = FileMeasurementResultRepository(results_directory=root).save(
                {"value": 1}, result_type="cable_loss", run_id="run-1"
            )

            self.assertEqual(saved.run_directory, root / "run-1")
            self.assertEqual(saved.archive_path, root / "run-1" / "cable_loss_results.json")
            self.assertEqual(saved.legacy_copy_path, root / "cable_loss_results.json")
            self.assertTrue(saved.archive_path.is_file())
            self.assertTrue(saved.legacy_copy_path.is_file())
            self.assertEqual(load_json_result(saved.archive_path)["run_id"], "run-1")

    def test_real_write_encodes_numpy_scalars_and_arrays(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = FileMeasurementResultRepository(results_directory=directory).save(
                {
                    "array": np.array([1, 2]),
                    "integer": np.int64(3),
                    "floating": np.float64(4.5),
                },
                result_type="cable_loss",
                run_id="numpy-run",
            )

            for path in (saved.archive_path, saved.legacy_copy_path):
                payload = load_json_result(path)
                self.assertEqual(payload["array"], [1, 2])
                self.assertEqual(payload["integer"], 3)
                self.assertEqual(payload["floating"], 4.5)

    def test_explicit_run_directory_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            explicit = root / "provided-run"
            saved = FileMeasurementResultRepository(results_directory=root / "results").save(
                {"value": 1},
                result_type="cable_loss",
                run_id="explicit-run",
                run_directory=explicit,
            )

            self.assertEqual(saved.run_directory, explicit)
            self.assertEqual(saved.archive_path, explicit / "cable_loss_results.json")
            self.assertTrue(saved.archive_path.is_file())

    def test_real_write_propagates_encoding_failures(self):
        class Unsupported:
            pass

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TypeError):
                FileMeasurementResultRepository(results_directory=directory).save(
                    {"value": Unsupported()},
                    result_type="cable_loss",
                    run_id="failed-run",
                )

    def test_timestamped_result_types_use_their_legacy_file_names(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = FileMeasurementResultRepository(results_directory=directory)
            with patch("infrastructure.persistence.result_repository.time.strftime", return_value="20261001_120000"):
                driver = repository.save(
                    {"power_mapping": {}},
                    result_type="driver_power_mapping",
                    run_id="driver-run",
                )
                amplifier = repository.save(
                    {"results": {}},
                    result_type="amplifier_measurement",
                    run_id="amplifier-run",
                )

        self.assertEqual(driver.legacy_copy_path.name, "driver_power_mapping_20261001_120000.json")
        self.assertEqual(amplifier.legacy_copy_path.name, "amplifier_measurement_20261001_120000.json")

    def test_save_passes_through_storage_failures(self):
        error = OSError("写入失败")
        with patch(
            "infrastructure.persistence.result_repository.result_storage.save_measurement_result",
            side_effect=error,
        ):
            with self.assertRaises(OSError) as raised:
                FileMeasurementResultRepository().save(
                    {"value": 1}, result_type="cable_loss", run_id="run-1"
                )

        self.assertIs(raised.exception, error)

    def test_load_delegates_to_legacy_storage(self):
        with patch(
            "infrastructure.persistence.result_repository.result_storage.load_json_result",
            return_value={"value": 1},
        ) as load:
            result = FileMeasurementResultRepository().load("result.json")

        self.assertEqual(result, {"value": 1})
        load.assert_called_once_with("result.json")

    def test_unknown_result_type_is_rejected_before_writing(self):
        with self.assertRaises(ValueError):
            FileMeasurementResultRepository().save({}, result_type="unknown", run_id="run-1")

    def test_legacy_paths_and_latest_path_are_resolved_by_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = FileMeasurementResultRepository(results_directory=root)
            self.assertEqual(
                repository.legacy_path("cable_loss"),
                root / "cable_loss_results.json",
            )
            first = root / "driver_power_mapping_20261001_100000.json"
            second = root / "driver_power_mapping_20261001_110000.json"
            first.write_text("{}", encoding="utf-8")
            second.write_text("{}", encoding="utf-8")
            model = root / "driver_power_mapping_model.json"
            model.write_text("{}", encoding="utf-8")
            self.assertEqual(repository.latest_path("driver_power_mapping"), second)

    def test_latest_path_reports_missing_timestamped_result(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = FileMeasurementResultRepository(results_directory=directory)
            with self.assertRaises(FileNotFoundError):
                repository.latest_path("driver_power_mapping")


if __name__ == "__main__":
    unittest.main()
