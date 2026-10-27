import tempfile
import unittest
from pathlib import Path

from infrastructure.filesystem.paths import (
    CABLE_LOSS_FILE,
    CONFIG_FILE,
    ICONS_DIR,
    IMAGES_DIR,
    PROJECT_ROOT,
    TEMP_DIR,
    TEST_RESULTS_DIR,
    PathService,
    ensure_directory,
    resolve_path,
)


class ProjectPathsTests(unittest.TestCase):
    def test_default_paths_are_under_project_root(self):
        self.assertEqual(CONFIG_FILE, PROJECT_ROOT / "config.json")
        self.assertEqual(CABLE_LOSS_FILE, TEST_RESULTS_DIR / "cable_loss_results.json")
        self.assertEqual(ICONS_DIR, PROJECT_ROOT / "assets" / "icons")
        self.assertEqual(IMAGES_DIR, PROJECT_ROOT / "assets" / "images")
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

    def test_path_service_can_be_injected_for_an_isolated_root(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = PathService(temporary_directory)
            self.assertEqual(paths.config_file, Path(temporary_directory) / "config.json")
            self.assertEqual(paths.test_results_dir, Path(temporary_directory) / "test_results")
            self.assertEqual(paths.logs_dir, Path(temporary_directory) / "logs")
            self.assertFalse(paths.test_results_dir.exists())


if __name__ == "__main__":
    unittest.main()
