import unittest
from pathlib import Path
from unittest.mock import Mock

from enhanced_workers import _LegacyResultAdapter
from application.ports.result_repository import SavedMeasurementResult


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


if __name__ == "__main__":
    unittest.main()
