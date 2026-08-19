import tempfile
import unittest
from pathlib import Path

from project_paths import CONFIG_FILE, PROJECT_ROOT, TEMP_DIR, ensure_directory, resolve_path


class ProjectPathsTests(unittest.TestCase):
    def test_default_paths_are_under_project_root(self):
        self.assertEqual(CONFIG_FILE, PROJECT_ROOT / "config.json")
        self.assertEqual(TEMP_DIR, PROJECT_ROOT / "temp")

    def test_absolute_path_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "config.json"
            self.assertEqual(resolve_path(path, CONFIG_FILE), path)

    def test_relative_explicit_path_keeps_current_path_semantics(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            current_directory = Path.cwd()
            try:
                import os

                os.chdir(temporary_directory)
                self.assertEqual(resolve_path("config.json", CONFIG_FILE), Path(temporary_directory) / "config.json")
            finally:
                os.chdir(current_directory)

    def test_ensure_directory_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "nested" / "output"
            self.assertEqual(ensure_directory(target), target)
            self.assertTrue(target.is_dir())
            self.assertEqual(ensure_directory(target), target)


if __name__ == "__main__":
    unittest.main()
