import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.runtime.runtime_contract import (
    current_runtime,
    load_env_value,
    runtime_id,
    validate_python_interpreter,
)


class RuntimeContractTests(unittest.TestCase):
    def test_env_file_value_is_used_when_environment_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("AUTO_TEST_PYTHON=\"C:/envs/Auto_test/python.exe\"\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(
                    load_env_value("AUTO_TEST_PYTHON", env_file),
                    "C:/envs/Auto_test/python.exe",
                )

    def test_explicit_environment_value_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("AUTO_TEST_RUN_ID=file-id\n", encoding="utf-8")
            with patch.dict(os.environ, {"AUTO_TEST_RUN_ID": "environment-id"}, clear=True):
                self.assertEqual(load_env_value("AUTO_TEST_RUN_ID", env_file), "environment-id")

    def test_interpreter_mismatch_is_reported(self):
        with patch.dict(os.environ, {"AUTO_TEST_PYTHON": "C:/different/python.exe"}, clear=True):
            with patch("infrastructure.runtime.runtime_contract.Path.is_file", return_value=True):
                valid, detail = validate_python_interpreter()
        self.assertFalse(valid)
        self.assertIn("解释器不一致", detail)

    def test_run_id_can_be_supplied_for_evidence_correlation(self):
        with patch.dict(os.environ, {"AUTO_TEST_RUN_ID": "现场-001"}, clear=True):
            self.assertEqual(runtime_id(), "现场-001")

    def test_runtime_metadata_contains_current_interpreter(self):
        metadata = current_runtime()
        self.assertEqual(metadata["python_executable"], str(Path(sys.executable).resolve()))
        self.assertIn("run_id", metadata)


if __name__ == "__main__":
    unittest.main()
