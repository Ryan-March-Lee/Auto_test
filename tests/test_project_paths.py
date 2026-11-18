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
            self.assertEqual(
                paths.cable_loss_file,
                paths.test_results_dir / "cable_loss_results.json",
            )
            self.assertFalse(paths.test_results_dir.exists())

    def test_stage_result_directories_derive_from_test_results_dir(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = PathService(temporary_directory)
            expected_root = Path(temporary_directory) / "test_results"

            self.assertEqual(paths.cable_loss_results_dir, expected_root / "cable_loss")
            self.assertEqual(paths.driver_mapping_results_dir, expected_root / "driver_mapping")
            self.assertEqual(paths.amplifier_results_dir, expected_root / "amplifier")
            self.assertFalse(paths.cable_loss_results_dir.exists())
            self.assertFalse(paths.driver_mapping_results_dir.exists())
            self.assertFalse(paths.amplifier_results_dir.exists())

    def test_simulation_root_derives_from_temp_dir_without_creating_it(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = PathService(temporary_directory)
            simulation_root = (
                Path(temporary_directory) / "temp" / "simulation_runs"
            )

            self.assertEqual(paths.simulation_root, simulation_root)
            self.assertEqual(
                paths.simulation_runs_root, simulation_root / "runs"
            )
            self.assertEqual(
                paths.simulation_configs_dir, simulation_root / "configs"
            )
            self.assertFalse(paths.simulation_root.exists())

    def test_simulation_paths_scope_every_result_directory_to_the_simulation_root(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = PathService(temporary_directory)
            scoped = paths.simulation_paths

            self.assertEqual(scoped.root, paths.simulation_root.resolve())
            self.assertEqual(
                scoped.test_results_dir, paths.simulation_root / "test_results"
            )
            self.assertEqual(
                scoped.cable_loss_results_dir,
                paths.simulation_root / "test_results" / "cable_loss",
            )
            self.assertEqual(
                scoped.configuration_snapshots_dir,
                paths.simulation_root / "test_results" / "configurations",
            )
            self.assertNotEqual(scoped.test_results_dir, paths.test_results_dir)

    def test_simulation_run_repository_lands_beside_simulation_test_results(self):
        """``RunRepository(root=...)`` 让 ``runs/`` 与 ``test_results/`` 平级。"""
        from infrastructure.persistence.run_repository import RunRepository

        run_id = "20261117T060200Z_c3d9a1f4"
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = PathService(temporary_directory)
            repository = RunRepository(root=paths.simulation_runs_root)
            self.assertEqual(
                repository.layout(run_id).run_directory,
                paths.simulation_root / "runs" / run_id,
            )
            self.assertEqual(
                repository.layout(run_id).manifest_path,
                paths.simulation_root / "runs" / run_id / "manifest.json",
            )
            # 传 paths= 会落到 test_results/runs，这是仿真运行必须避免的布局。
            defaulted = RunRepository(paths=paths)
            self.assertEqual(
                defaulted.layout(run_id).run_directory,
                paths.test_results_dir / "runs" / run_id,
            )


if __name__ == "__main__":
    unittest.main()
