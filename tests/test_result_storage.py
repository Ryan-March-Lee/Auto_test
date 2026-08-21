import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cable_loss_measurement import CableLossMeasurement
from result_storage import (
    create_run_directory,
    load_json_result,
    new_run_id,
    save_json_result,
    validate_run_id,
)


class ResultStorageTests(unittest.TestCase):
    def test_save_adds_metadata_and_loads_legacy_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "result.json"
            save_json_result(path, {"results": {"1.0": {"gain": 2}}}, result_type="amplifier")
            saved = load_json_result(path)

        self.assertEqual(saved["results"]["1.0"]["gain"], 2)
        self.assertEqual(saved["schema_version"], "1.0")
        self.assertEqual(saved["result_type"], "amplifier")
        self.assertIn("saved_at", saved)

    def test_failed_replace_does_not_leave_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            with patch("result_storage.os.replace", side_effect=OSError("替换失败")):
                with self.assertRaises(OSError):
                    save_json_result(path, {"value": 1}, result_type="test")
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(directory).glob(".*.tmp")), [])

    def test_run_directory_requires_unique_name(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("result_storage.TEST_RESULTS_DIR", Path(directory)):
                first = create_run_directory("run-1")
                self.assertEqual(first, Path(directory) / "run-1")
                with self.assertRaises(FileExistsError):
                    create_run_directory("run-1")

    def test_run_id_rejects_paths_and_accepts_generated_id(self):
        for value in ("../outside", "..\\outside", "C:\\outside", "", ".", "run/id"):
            with self.assertRaises(ValueError):
                validate_run_id(value)
        generated = new_run_id()
        self.assertEqual(validate_run_id(generated), generated)

    def test_measurement_result_is_archived_and_legacy_file_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_path = root / "cable_loss_results.json"
            with patch("cable_loss_measurement.CABLE_LOSS_FILE", legacy_path), patch(
                "result_storage.TEST_RESULTS_DIR", root / "test_results"
            ):
                measurement = CableLossMeasurement.__new__(CableLossMeasurement)
                measurement.attenuator_value = 30.0
                measurement.cable_losses = {1.0: {"cable1": 1.0}}
                measurement.run_id = "20260821-120000-test"
                measurement.run_directory = None
                measurement.save_results()

            self.assertTrue(legacy_path.exists())
            archived = root / "test_results" / measurement.run_id / "cable_loss_results.json"
            self.assertTrue(archived.exists())
            self.assertEqual(load_json_result(archived)["cable_losses"]["1.0"]["cable1"], 1.0)

    def test_archive_collision_does_not_overwrite_legacy_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy_path = root / "cable_loss_results.json"
            legacy_path.write_text(json.dumps({"old": True}), encoding="utf-8")
            with patch("cable_loss_measurement.CABLE_LOSS_FILE", legacy_path), patch(
                "result_storage.TEST_RESULTS_DIR", root / "test_results"
            ):
                first = CableLossMeasurement.__new__(CableLossMeasurement)
                first.attenuator_value = 30.0
                first.cable_losses = {1.0: {"cable1": 1.0}}
                first.run_id = "same-run"
                first.run_directory = None
                first.save_results()

                second = CableLossMeasurement.__new__(CableLossMeasurement)
                second.attenuator_value = 30.0
                second.cable_losses = {1.0: {"cable1": 2.0}}
                second.run_id = "same-run"
                second.run_directory = None
                with self.assertRaises(FileExistsError):
                    second.save_results()

            self.assertEqual(load_json_result(legacy_path)["cable_losses"]["1.0"]["cable1"], 1.0)

    def test_result_root_must_be_object(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps([]), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_json_result(path)


if __name__ == "__main__":
    unittest.main()
