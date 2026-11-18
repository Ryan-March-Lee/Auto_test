"""阶段四：仿真运行标记与真机连接/加载守卫（重构计划步骤 16）。

覆盖：

- 仿真运行在解析硬件快照、打开任何 VISA 资源之前被 ``connect_instruments`` 拒绝；
- ``load_prepared_run`` 同样拒绝仿真运行；
- 标记缺失、删除、损坏、未登记与“只有目录没有标记”的崩溃窗口；
- 真实运行不受守卫影响；
- 自定义注入根以标记为准，生产默认仿真根以目录为准。
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.gui_runtime import connect_instruments
from app.run_context import create_prepared_run, environment_version, load_prepared_run
from app.simulation_runtime import prepare_simulated_run
from application.configuration_authoring import (
    ConfigurationAuthoringService,
    ConfigurationEditingDocument,
)
from application.dto.measurement_results import MeasurementType
from application.inputs.mock_scenario import load_mock_scenario
from application.mock_configuration_matcher import MockConfigurationMatcher
from application.run_service import RunNotResumable
from application.simulation_run import (
    SIMULATION_MARKER_PATH,
    is_simulated_run,
    read_simulation_marker,
    reject_simulated_prepared_run,
)
from domain.run.models import generate_run_id
from infrastructure.config.json_config_repository import load_combined_configuration
from infrastructure.filesystem.paths import DEFAULT_MOCK_DATA_PATHS, PathService
from infrastructure.persistence.run_repository import RunRepository
from tests.config_fixtures import complete_combined_config

PROJECT_ROOT = Path(__file__).parents[1]
MOCK_CONFIG_PATH = PROJECT_ROOT / "config.mock.json"
MOCK_TYPES = (
    MeasurementType.CABLE_LOSS,
    MeasurementType.DRIVER_POWER_MAPPING,
    MeasurementType.AMPLIFIER,
)


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


class SimulatedRunGuardTestCase(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.paths = PathService(self.base)
        self.runs_root = self.paths.simulation_runs_root

    def _prepare_simulated(self):
        candidate, match = _candidate_and_match()
        return prepare_simulated_run(
            candidate,
            match,
            mock_paths=DEFAULT_MOCK_DATA_PATHS,
            paths=self.paths,
        )

    def _prepare_formal(self):
        """在临时根创建一份不带仿真标记的真实运行。"""
        document = complete_combined_config()
        loaded = load_combined_configuration(document)
        self.assertTrue(loaded.valid, loaded.validation)
        repository = RunRepository(root=self.base / "formal_runs")
        return create_prepared_run(
            loaded,
            run_id=generate_run_id(token="abcd1234"),
            software_version=environment_version(),
            paths=self.paths,
            run_repository=repository,
            hardware_config=document,
        )


class ConnectInstrumentsGuardTests(SimulatedRunGuardTestCase):
    def test_simulated_run_is_rejected_before_any_port_is_created(self):
        assembly = self._prepare_simulated()
        with patch("app.gui_runtime.create_measurement_port") as factory:
            with self.assertRaises(RunNotResumable):
                connect_instruments(assembly.prepared_run)
        factory.assert_not_called()

    def test_real_run_still_reaches_the_measurement_port_factory(self):
        prepared = self._prepare_formal()
        sentinel = object()
        with patch(
            "app.gui_runtime.create_measurement_port", return_value=sentinel
        ) as factory:
            self.assertIs(connect_instruments(prepared), sentinel)
        factory.assert_called_once()

    def test_marker_deleted_is_fail_closed(self):
        assembly = self._prepare_simulated()
        marker = Path(assembly.prepared_run.run_directory) / SIMULATION_MARKER_PATH
        marker.unlink()
        with patch("app.gui_runtime.create_measurement_port") as factory:
            with self.assertRaises(RunNotResumable):
                connect_instruments(assembly.prepared_run)
        factory.assert_not_called()

    def test_corrupted_marker_is_fail_closed(self):
        assembly = self._prepare_simulated()
        marker = Path(assembly.prepared_run.run_directory) / SIMULATION_MARKER_PATH
        marker.write_text("{not-json", encoding="utf-8")
        with self.assertRaises(RunNotResumable):
            read_simulation_marker(assembly.run_repository, assembly.prepared_run.run_id)
        with self.assertRaises(RunNotResumable):
            reject_simulated_prepared_run(assembly.prepared_run)

    def test_unregistered_marker_is_not_a_simulation_marker(self):
        prepared = self._prepare_formal()
        marker = Path(prepared.run_directory) / SIMULATION_MARKER_PATH
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
        self.assertFalse(
            is_simulated_run(prepared.run_repository, prepared.run_id)
        )
        sentinel = object()
        with patch(
            "app.gui_runtime.create_measurement_port", return_value=sentinel
        ):
            self.assertIs(connect_instruments(prepared), sentinel)


class LoadPreparedRunGuardTests(SimulatedRunGuardTestCase):
    def test_load_prepared_run_refuses_a_simulated_run(self):
        assembly = self._prepare_simulated()
        with self.assertRaises(RunNotResumable):
            load_prepared_run(
                assembly.prepared_run.run_id,
                run_repository=assembly.run_repository,
            )

    def test_load_prepared_run_still_opens_a_real_run(self):
        prepared = self._prepare_formal()
        loaded = load_prepared_run(
            prepared.run_id, run_repository=prepared.run_repository
        )
        self.assertEqual(loaded.run_id, prepared.run_id)


class DirectoryOnlyCrashWindowTests(SimulatedRunGuardTestCase):
    def test_run_directory_under_the_default_simulation_root_is_rejected(self):
        # 模拟“运行目录已创建、标记尚未登记”的窗口：只凭目录就按仿真运行拒绝。
        run_directory = self.base / "simulation_runs" / "runs" / "crash-window"
        run_directory.mkdir(parents=True)
        repository = RunRepository(root=self.base / "simulation_runs" / "runs")
        self.assertTrue(
            is_simulated_run(
                repository,
                "crash-window",
                run_directory=run_directory,
                default_simulation_root=self.base / "simulation_runs",
            )
        )

    def test_custom_root_without_marker_is_not_simulated(self):
        prepared = self._prepare_formal()
        self.assertFalse(
            is_simulated_run(
                prepared.run_repository,
                prepared.run_id,
                run_directory=prepared.run_directory,
                default_simulation_root=self.base / "simulation_runs",
            )
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
