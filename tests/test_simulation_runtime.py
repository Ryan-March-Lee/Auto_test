"""阶段四：仿真运行装配与事务（重构计划步骤 15～17）。

覆盖：

- ``prepare_simulated_run`` 在临时仿真根创建真实 ``PreparedRun``、写入并登记标记、
  生成唯一配置草稿，读数冻结为 DTO；
- 读数频率网格、来源摘要在任何运行目录创建之前失败；
- 标记写入/登记失败时撤销本次新建运行与草稿，不留下半成品；
- 仿真作用域结果查询只看仿真根；
- 快放 ``sleep_fn`` 只缩短休眠。

真实 ``runs/`` 与 ``test_results/`` 在测试前后必须保持不变。
"""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.simulation_runtime import (
    DEFAULT_SIMULATION_TIME_SCALE,
    SimulationDiscardOutcome,
    discard_simulated_run,
    prepare_simulated_run,
    simulation_sleep_fn,
)
from application.configuration_authoring import (
    ConfigurationAuthoringService,
    ConfigurationEditingDocument,
)
from application.dto.measurement_results import MeasurementType
from application.dto.simulation_readings import SimulationReadingsError
from application.inputs.mock_scenario import load_mock_scenario
from application.mock_configuration_matcher import MockConfigurationMatcher
from application.simulation_run import SIMULATION_MARKER_PATH, read_simulation_marker
from infrastructure.filesystem.paths import DEFAULT_MOCK_DATA_PATHS, DEFAULT_PATHS, PathService
from infrastructure.persistence.run_repository import RunRepository

PROJECT_ROOT = Path(__file__).parents[1]
MOCK_CONFIG_PATH = PROJECT_ROOT / "config.mock.json"
MOCK_TYPES = (
    MeasurementType.CABLE_LOSS,
    MeasurementType.DRIVER_POWER_MAPPING,
    MeasurementType.AMPLIFIER,
)


def _snapshot_tree(root):
    """目录元数据指纹：证明真实数据根在测试期间没有新增/删除/改写条目。"""
    root = Path(root)
    if not root.exists():
        return ()
    stat = root.stat()
    entries = [(".", "dir", 0, stat.st_mtime_ns)]
    for path in sorted(root.rglob("*")):
        item = path.stat()
        entries.append(
            (
                path.relative_to(root).as_posix(),
                "dir" if path.is_dir() else "file",
                0 if path.is_dir() else item.st_size,
                item.st_mtime_ns,
            )
        )
    return tuple(entries)


def _candidate_and_match():
    raw = json.loads(MOCK_CONFIG_PATH.read_text(encoding="utf-8"))
    candidate = ConfigurationAuthoringService().author_document(
        ConfigurationEditingDocument.from_mapping(raw)
    )
    scenarios = tuple(
        load_mock_scenario(
            DEFAULT_MOCK_DATA_PATHS.require_scenario_path_for(kind.value),
            expected_measurement_type=kind,
        )
        for kind in MOCK_TYPES
    )
    return candidate, MockConfigurationMatcher(scenarios).match("Mock", candidate)


class SimulationRuntimeTestCase(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.paths = PathService(self.base)
        self.simulation_root = self.paths.simulation_root
        self.runs_root = self.paths.simulation_runs_root
        self.real_roots = (
            ("runs", DEFAULT_PATHS.root / "runs"),
            ("test_results", DEFAULT_PATHS.test_results_dir),
        )
        self.real_before = tuple(
            (name, _snapshot_tree(path)) for name, path in self.real_roots
        )

    def tearDown(self):
        try:
            for name, path in self.real_roots:
                self.assertEqual(
                    _snapshot_tree(path),
                    dict(self.real_before)[name],
                    f"真实 {name} 目录在仿真运行装配测试中被改动",
                )
        finally:
            super().tearDown()

    def _prepare(self, **overrides):
        candidate, match = _candidate_and_match()
        return prepare_simulated_run(
            candidate,
            match,
            mock_paths=DEFAULT_MOCK_DATA_PATHS,
            paths=self.paths,
            **overrides,
        )


class PrepareSimulatedRunTests(SimulationRuntimeTestCase):
    def test_prepare_creates_a_real_run_marker_and_frozen_readings(self):
        assembly = self._prepare()

        prepared = assembly.prepared_run
        self.assertTrue(prepared.has_run_contract)
        self.assertEqual(Path(prepared.run_directory).parent, self.runs_root)
        self.assertTrue(prepared.hardware_connection_path().is_file())
        self.assertTrue((Path(prepared.run_directory) / "config.json").is_file())

        self.assertEqual(
            set(assembly.readings), set(MOCK_TYPES)
        )
        for measurement_type, readings in assembly.readings.items():
            self.assertIs(readings.measurement_type, measurement_type)
        self.assertEqual(assembly.paths.root, self.simulation_root)
        self.assertIsNone(assembly.results.list_results() or None)

        marker_path = Path(prepared.run_directory) / SIMULATION_MARKER_PATH
        self.assertTrue(marker_path.is_file())
        marker = read_simulation_marker(assembly.run_repository, prepared.run_id)
        self.assertIsNotNone(marker)
        self.assertEqual(marker.run_id, prepared.run_id)
        self.assertEqual(marker.snapshot_name, "Mock")
        self.assertEqual(marker.time_scale, DEFAULT_SIMULATION_TIME_SCALE)
        self.assertEqual(
            {item.measurement_type for item in marker.readings}, set(MOCK_TYPES)
        )
        for item in marker.readings:
            self.assertTrue(item.readings_path.startswith("mock_data/"))
            self.assertTrue(item.scenario_path.endswith("scenario.json"))

        self.assertTrue(assembly.config_draft.is_file())
        self.assertEqual(assembly.config_draft.parent, self.paths.simulation_configs_dir)
        self.assertEqual(
            sorted(path.name for path in self.runs_root.iterdir()),
            [prepared.run_id],
        )

    def test_draft_names_are_unique_across_entries(self):
        first = self._prepare()
        second = self._prepare(now=None)
        self.assertNotEqual(first.config_draft, second.config_draft)
        self.assertNotEqual(
            first.prepared_run.run_id, second.prepared_run.run_id
        )
        self.assertEqual(
            len(list(self.paths.simulation_configs_dir.iterdir())), 2
        )

    def test_readings_grid_mismatch_fails_before_any_run_is_created(self):
        candidate, match = _candidate_and_match()
        with patch(
            "app.simulation_runtime._test_frequencies",
            return_value=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 9.9),
        ):
            with self.assertRaises(SimulationReadingsError):
                prepare_simulated_run(
                    candidate,
                    match,
                    mock_paths=DEFAULT_MOCK_DATA_PATHS,
                    paths=self.paths,
                )
        self.assertFalse((self.runs_root).exists())
        self.assertFalse(self.paths.simulation_configs_dir.exists())

    def test_source_digest_mismatch_fails_before_any_run_is_created(self):
        candidate, match = _candidate_and_match()
        from application.inputs import simulation_readings as reader

        original = reader.load_simulation_readings

        def corrupted(path, **kwargs):
            loaded = original(path, **kwargs)
            if loaded.readings.measurement_type is MeasurementType.CABLE_LOSS:
                source = replace(loaded.readings.source, scenario_sha256="0" * 64)
                readings = replace(loaded.readings, source=source)
                return replace(loaded, readings=readings)
            return loaded

        with patch(
            "app.simulation_runtime.load_simulation_readings", side_effect=corrupted
        ):
            with self.assertRaises(SimulationReadingsError):
                prepare_simulated_run(
                    candidate,
                    match,
                    mock_paths=DEFAULT_MOCK_DATA_PATHS,
                    paths=self.paths,
                )
        self.assertFalse((self.runs_root).exists())
        self.assertFalse(self.paths.simulation_configs_dir.exists())

    def test_marker_registration_failure_discards_the_run_and_draft(self):
        candidate, match = _candidate_and_match()
        with patch.object(
            RunRepository,
            "register_file",
            autospec=True,
            side_effect=OSError("manifest 写入失败"),
        ):
            with self.assertRaises(OSError):
                prepare_simulated_run(
                    candidate,
                    match,
                    mock_paths=DEFAULT_MOCK_DATA_PATHS,
                    paths=self.paths,
                )
        self.assertEqual(list(self.runs_root.iterdir()), [])
        self.assertEqual(list(self.paths.simulation_configs_dir.iterdir()), [])

    def test_time_scale_must_be_positive(self):
        candidate, match = _candidate_and_match()
        for value in (0, -1, float("inf"), True, "50"):
            with self.subTest(value=value):
                with self.assertRaises((TypeError, ValueError)):
                    prepare_simulated_run(
                        candidate,
                        match,
                        mock_paths=DEFAULT_MOCK_DATA_PATHS,
                        paths=self.paths,
                        time_scale=value,
                    )
        self.assertFalse(self.paths.simulation_root.exists())


class DiscardSimulatedRunTests(SimulationRuntimeTestCase):
    """提交失败补偿：结果必须能区分“已清理”与“仍有残留”。"""

    def test_discard_removes_the_run_and_the_draft(self):
        assembly = self._prepare()

        outcome = discard_simulated_run(assembly)

        self.assertIsInstance(outcome, SimulationDiscardOutcome)
        self.assertTrue(outcome.ok, outcome.describe())
        self.assertTrue(outcome.run_removed)
        self.assertTrue(outcome.draft_removed)
        self.assertEqual(outcome.errors, ())
        self.assertFalse(
            (self.runs_root / assembly.prepared_run.run_id).exists()
        )
        self.assertFalse(assembly.config_draft.exists())

    def test_run_removal_failure_is_reported_and_draft_still_attempted(self):
        assembly = self._prepare()

        with patch(
            "app.simulation_runtime.discard_unstarted_run",
            side_effect=OSError("manifest 被占用"),
        ):
            outcome = discard_simulated_run(assembly)

        self.assertFalse(outcome.ok)
        self.assertFalse(outcome.run_removed)
        self.assertTrue(outcome.draft_removed)
        self.assertEqual(len(outcome.errors), 1)
        self.assertIn("manifest 被占用", outcome.describe())
        # 草稿清理不因运行目录失败而跳过。
        self.assertFalse(assembly.config_draft.exists())
        self.assertTrue(
            (self.runs_root / assembly.prepared_run.run_id).exists()
        )

    def test_draft_removal_failure_is_reported_after_the_run_is_removed(self):
        assembly = self._prepare()
        # 用目录占住草稿路径：``unlink`` 在 Windows/POSIX 上都会以 OSError 失败，
        # 从而模拟“草稿被其它进程占用”的残留。
        assembly.config_draft.unlink()
        assembly.config_draft.mkdir()

        outcome = discard_simulated_run(assembly)

        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.run_removed)
        self.assertFalse(outcome.draft_removed)
        self.assertEqual(len(outcome.errors), 1)
        self.assertIn("输入草稿", outcome.describe())
        self.assertFalse(
            (self.runs_root / assembly.prepared_run.run_id).exists()
        )


class SimulationSleepTests(unittest.TestCase):
    def test_default_scale_shortens_sleep_without_changing_order(self):
        self.assertEqual(DEFAULT_SIMULATION_TIME_SCALE, 50)
        sleep = simulation_sleep_fn(DEFAULT_SIMULATION_TIME_SCALE)
        with patch("app.simulation_runtime.time.sleep") as sleeper:
            sleep(10)
            sleep(1)
        self.assertEqual(
            [call.args[0] for call in sleeper.call_args_list], [0.2, 0.02]
        )

    def test_non_positive_delay_does_not_sleep(self):
        sleep = simulation_sleep_fn(50)
        with patch("app.simulation_runtime.time.sleep") as sleeper:
            sleep(0)
            sleep(-3)
        sleeper.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
