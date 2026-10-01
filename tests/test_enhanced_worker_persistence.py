import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

from enhanced_workers import _LegacyResultAdapter
from application.ports.result_repository import SavedMeasurementResult
from infrastructure.persistence.result_repository import FileMeasurementResultRepository
from result_storage import load_json_result


class LegacyResultAdapterPersistenceTests(unittest.TestCase):
    def test_save_uses_injected_repository(self):
        repository = Mock()
        repository.new_run_id.return_value = "run-1"
        repository.create_legacy_run_snapshot.return_value = Path("run-1")
        repository.save.return_value = SavedMeasurementResult(
            archive_path=Path("run-1/result.json"),
            legacy_copy_path=Path("result.json"),
            run_directory=Path("run-1"),
            run_id="run-1",
        )
        adapter = _LegacyResultAdapter(
            {"test_frequencies": [1.0]}, result_repository=repository
        )

        adapter._save({"value": 1}, "cable_loss")

        repository.save.assert_called_once_with(
            {"value": 1},
            result_type="cable_loss",
            run_id="run-1",
            run_directory=Path("run-1"),
        )
        self.assertEqual(adapter.run_directory, Path("run-1"))

    def test_real_repository_preserves_legacy_copy_and_versioned_model(self):
        fixture = Path(__file__).parent / "fixtures" / "config_driver_enabled_no_assignment.json"
        config = json.loads(fixture.read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = FileMeasurementResultRepository(results_directory=root)
            with patch("result_storage.TEST_RESULTS_DIR", root):
                adapter = _LegacyResultAdapter(
                    config,
                    run_id="wrapper-run",
                    result_repository=repository,
                )
                adapter._save(
                    {
                        "cable_losses": {
                            "1.0": {
                                "total_path1": 1.0,
                                "total_path2": 2.0,
                                "cable1": 0.5,
                            }
                        }
                    },
                    "cable_loss",
                )

            archive = root / "wrapper-run" / "cable_loss_results.json"
            legacy_copy = root / "cable_loss_results.json"
            model = root / "wrapper-run" / "cable_loss_model.json"
            self.assertTrue(archive.is_file())
            self.assertTrue(legacy_copy.is_file())
            self.assertTrue(model.is_file())
            self.assertEqual(load_json_result(archive)["run_id"], "wrapper-run")
            self.assertEqual(load_json_result(legacy_copy), load_json_result(archive))
            self.assertEqual(load_json_result(model)["run_id"], "wrapper-run")

    def test_real_repository_rejects_archive_collision(self):
        fixture = Path(__file__).parent / "fixtures" / "config_driver_enabled_no_assignment.json"
        config = json.loads(fixture.read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = FileMeasurementResultRepository(results_directory=root)
            with patch("result_storage.TEST_RESULTS_DIR", root):
                adapter = _LegacyResultAdapter(
                    config,
                    run_id="collision-run",
                    result_repository=repository,
                )
                adapter._save({"cable_losses": {}}, "cable_loss")
                with self.assertRaises(FileExistsError):
                    adapter._save({"cable_losses": {}}, "cable_loss")

    def test_explicit_run_directory_is_used_by_wrapper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            explicit = root / "provided-run"
            repository = Mock()
            repository.new_run_id.return_value = "unused-run"
            repository.save.return_value = SavedMeasurementResult(
                archive_path=explicit / "result.json",
                legacy_copy_path=root / "result.json",
                run_directory=explicit,
                run_id="explicit-run",
            )
            adapter = _LegacyResultAdapter(
                {"test_frequencies": [1.0]},
                run_id="explicit-run",
                run_directory=explicit,
                result_repository=repository,
            )

            adapter._save({"value": 1}, "cable_loss")

            repository.create_legacy_run_snapshot.assert_not_called()
            repository.save.assert_called_once_with(
                {"value": 1},
                result_type="cable_loss",
                run_id="explicit-run",
                run_directory=explicit,
            )
            self.assertEqual(adapter.run_directory, explicit)


if __name__ == "__main__":
    unittest.main()
