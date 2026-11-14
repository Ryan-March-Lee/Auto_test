import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from infrastructure.runtime.runtime_contract import load_env_value, runtime_id


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

    def test_run_id_can_be_supplied_for_evidence_correlation(self):
        with patch.dict(os.environ, {"AUTO_TEST_RUN_ID": "现场-001"}, clear=True):
            self.assertEqual(runtime_id(), "现场-001")


if __name__ == "__main__":
    unittest.main()
