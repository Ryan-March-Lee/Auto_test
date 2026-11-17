"""步骤 1 契约：Mock 数据路径、阶段索引与路径边界。"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from application.dto.measurement_results import MeasurementType
from application.ports.stage_result_repository import (
    MeasurementStage,
    measurement_type_for_stage,
)
from infrastructure.filesystem.paths import (
    DEFAULT_MOCK_DATA_PATHS,
    MOCK_STAGE_INDEX,
    MockDataPathError,
    MockDataPathService,
    MockStage,
    MockStageEntry,
    coerce_mock_stage,
    mock_measurement_type_for_stage,
    mock_stage_entry,
    mock_stage_for_measurement_type,
)


PROJECT_ROOT = Path(__file__).parents[1]
MOCK_RESULT_FILES = {
    MockStage.CABLE_LOSS: "20261117T060245Z_9b2e7d10.json",
    MockStage.DRIVER_MAPPING: "20261117T060600Z_5c1d9e42.json",
    MockStage.AMPLIFIER: "20261117T070237Z_7b3d9f21.json",
}


def _create_file_link(test_case, target, link):
    """把 ``link`` 建成指向 ``target`` 的文件符号链接；环境不支持时跳过用例。"""
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        test_case.skipTest("当前环境不允许创建文件符号链接")


def _create_directory_link(test_case, target, link):
    """把 ``link`` 建成指向 ``target`` 的目录链接；环境不支持时跳过用例。

    Windows 上普通账号通常不能创建目录符号链接，因此退回无需管理员权限的目录
    联接（junction）。两者都会让 ``Path.resolve()`` 落到真实目录，从而覆盖同一种
    越界风险。
    """
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode == 0 and link.exists():
            return
    test_case.skipTest("当前环境无法创建目录链接（符号链接或 junction）")


def _remove_link(path):
    """只删除链接本身，不触及链接目标。"""
    try:
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            path.rmdir()
    except OSError:
        pass


class MockStageIndexTests(unittest.TestCase):
    def test_index_covers_every_mock_stage(self):
        self.assertEqual(set(MOCK_STAGE_INDEX), set(MockStage))
        self.assertEqual(len(MOCK_STAGE_INDEX), 3)

    def test_index_maps_stage_directories_to_formal_measurement_types(self):
        self.assertEqual(
            {
                stage: entry.measurement_type
                for stage, entry in MOCK_STAGE_INDEX.items()
            },
            {
                MockStage.CABLE_LOSS: "cable_loss",
                MockStage.DRIVER_MAPPING: "driver_power_mapping",
                MockStage.AMPLIFIER: "amplifier",
            },
        )
        self.assertEqual(
            mock_measurement_type_for_stage(MockStage.DRIVER_MAPPING),
            "driver_power_mapping",
        )
        self.assertEqual(
            mock_measurement_type_for_stage("amplifier"),
            "amplifier",
        )
        self.assertEqual(
            mock_stage_for_measurement_type("driver_power_mapping"),
            MockStage.DRIVER_MAPPING,
        )

    def test_index_agrees_with_the_formal_stage_result_contract(self):
        """阶段目录名与正式测量类型以应用层契约为准，两边漂移必须被发现。"""
        self.assertEqual(
            [stage.value for stage in MockStage],
            [stage.value for stage in MeasurementStage],
        )
        self.assertEqual(
            {entry.measurement_type for entry in MOCK_STAGE_INDEX.values()},
            {kind.value for kind in MeasurementType},
        )
        for stage in MockStage:
            with self.subTest(stage=stage.value):
                self.assertEqual(
                    mock_measurement_type_for_stage(stage),
                    measurement_type_for_stage(stage.value).value,
                )

    def test_calibration_inputs_follow_the_measurement_order(self):
        self.assertEqual(
            mock_stage_entry(MockStage.CABLE_LOSS).calibration_inputs,
            (),
        )
        self.assertEqual(
            mock_stage_entry(MockStage.DRIVER_MAPPING).calibration_inputs,
            (MockStage.CABLE_LOSS,),
        )
        self.assertEqual(
            mock_stage_entry(MockStage.AMPLIFIER).calibration_inputs,
            (MockStage.CABLE_LOSS, MockStage.DRIVER_MAPPING),
        )

    def test_calibration_inputs_are_normalized_to_an_immutable_tuple(self):
        entry = MockStageEntry(
            stage=MockStage.AMPLIFIER,
            measurement_type="amplifier",
            calibration_inputs=[MockStage.CABLE_LOSS],
        )
        self.assertEqual(entry.calibration_inputs, (MockStage.CABLE_LOSS,))
        self.assertIsInstance(entry.calibration_inputs, tuple)
        with self.assertRaises(AttributeError):
            entry.calibration_inputs = ()

        self.assertEqual(
            mock_stage_entry(MockStage.AMPLIFIER).calibration_inputs,
            (MockStage.CABLE_LOSS, MockStage.DRIVER_MAPPING),
        )

    def test_calibration_inputs_reject_non_sequences(self):
        with self.assertRaises(MockDataPathError) as raised:
            MockStageEntry(MockStage.AMPLIFIER, "amplifier", None)
        self.assertIn("必须是 MockStage 序列", str(raised.exception))

        with self.assertRaises(MockDataPathError) as raised:
            MockStageEntry(MockStage.AMPLIFIER, "amplifier", "cable_loss")
        self.assertIn("不能是字符串", str(raised.exception))

    def test_coerce_accepts_enum_and_directory_name(self):
        self.assertIs(
            coerce_mock_stage(MockStage.AMPLIFIER),
            MockStage.AMPLIFIER,
        )
        self.assertIs(coerce_mock_stage(" amplifier "), MockStage.AMPLIFIER)

    def test_unknown_stage_has_readable_error(self):
        with self.assertRaises(MockDataPathError) as raised:
            coerce_mock_stage("unknown")
        self.assertIn("未知的 Mock 阶段", str(raised.exception))
        self.assertIn("cable_loss、driver_mapping、amplifier", str(raised.exception))

        with self.assertRaises(MockDataPathError) as raised:
            coerce_mock_stage(None)
        self.assertIn("必须是字符串或 MockStage", str(raised.exception))

        with self.assertRaises(MockDataPathError) as raised:
            coerce_mock_stage("   ")
        self.assertIn("不能为空", str(raised.exception))

    def test_unknown_measurement_type_has_readable_error(self):
        with self.assertRaises(MockDataPathError) as raised:
            mock_stage_for_measurement_type("amplifier_measurement")
        self.assertIn("未知的 Mock 测量类型", str(raised.exception))
        self.assertIn("driver_power_mapping", str(raised.exception))

        with self.assertRaises(MockDataPathError) as raised:
            mock_stage_for_measurement_type(None)
        self.assertIn("测量类型必须是字符串", str(raised.exception))

    def test_index_entries_reject_invalid_dependencies(self):
        with self.assertRaises(MockDataPathError) as raised:
            MockStageEntry(
                stage=MockStage.CABLE_LOSS,
                measurement_type="cable_loss",
                calibration_inputs=(MockStage.CABLE_LOSS,),
            )
        self.assertIn("不能依赖自身", str(raised.exception))

        with self.assertRaises(MockDataPathError) as raised:
            MockStageEntry(
                stage=MockStage.AMPLIFIER,
                measurement_type="amplifier",
                calibration_inputs=(MockStage.CABLE_LOSS, MockStage.CABLE_LOSS),
            )
        self.assertIn("校准输入阶段重复", str(raised.exception))


class MockDataPathServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.paths = MockDataPathService(self.root)

    def _create_file(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        return path

    def _outside_directory(self) -> Path:
        """项目根目录之外的临时目录；清理顺序由 ``addCleanup`` 保证。"""
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        return Path(outside.name)

    def test_paths_derive_from_project_root_without_creating_directories(self):
        self.assertEqual(self.paths.project_root, self.root.resolve())
        self.assertEqual(self.paths.mock_data_dir, self.root / "mock_data")
        self.assertEqual(self.paths.config_file, self.root / "config.mock.json")

        for stage in MockStage:
            with self.subTest(stage=stage.value):
                self.assertEqual(
                    self.paths.stage_dir(stage),
                    self.root / "mock_data" / stage.value,
                )
                self.assertEqual(
                    self.paths.scenario_path(stage),
                    self.root / "mock_data" / stage.value / "scenario.json",
                )
                self.assertEqual(
                    self.paths.result_path(stage, MOCK_RESULT_FILES[stage]),
                    self.root
                    / "mock_data"
                    / stage.value
                    / MOCK_RESULT_FILES[stage],
                )

        self.assertFalse(self.paths.mock_data_dir.exists())

    def test_temporary_root_resolves_three_scenarios_and_results(self):
        for stage, result_filename in MOCK_RESULT_FILES.items():
            self._create_file(self.paths.scenario_path(stage))
            self._create_file(self.paths.result_path(stage, result_filename))

        for stage, result_filename in MOCK_RESULT_FILES.items():
            with self.subTest(stage=stage.value):
                scenario = self.paths.require_scenario_path(stage)
                result = self.paths.require_result_path(stage, result_filename)
                self.assertTrue(scenario.is_file())
                self.assertTrue(result.is_file())
                self.assertEqual(scenario.parent, self.paths.stage_dir(stage))
                self.assertEqual(result.parent, self.paths.stage_dir(stage))
                self.assertEqual(scenario.name, "scenario.json")
                self.assertEqual(result.name, result_filename)

    def test_repository_mock_data_resolves_three_scenarios_and_results(self):
        repository_paths = MockDataPathService(PROJECT_ROOT)
        for stage, result_filename in MOCK_RESULT_FILES.items():
            with self.subTest(stage=stage.value):
                self.assertTrue(
                    repository_paths.require_scenario_path(stage).is_file()
                )
                self.assertTrue(
                    repository_paths.require_result_path(
                        stage, result_filename
                    ).is_file()
                )

    def test_resolved_paths_stay_inside_the_repository_mock_data_dir(self):
        mock_root = DEFAULT_MOCK_DATA_PATHS.mock_data_dir.resolve()
        for stage, result_filename in MOCK_RESULT_FILES.items():
            with self.subTest(stage=stage.value):
                scenario = DEFAULT_MOCK_DATA_PATHS.require_scenario_path(stage)
                result = DEFAULT_MOCK_DATA_PATHS.require_result_path(
                    stage, result_filename
                )
                self.assertEqual(scenario.parent, mock_root / stage.value)
                self.assertEqual(result.parent, mock_root / stage.value)

    def test_missing_scenario_has_readable_error_without_creating_directory(self):
        with self.assertRaises(MockDataPathError) as raised:
            self.paths.require_scenario_path(MockStage.CABLE_LOSS)
        message = str(raised.exception)
        self.assertIn("Mock cable_loss 场景文件", message)
        self.assertIn("不存在或不是普通文件", message)
        self.assertIn("scenario.json", message)
        self.assertFalse(self.paths.mock_data_dir.exists())

    def test_missing_result_has_readable_error(self):
        filename = MOCK_RESULT_FILES[MockStage.AMPLIFIER]
        with self.assertRaises(MockDataPathError) as raised:
            self.paths.require_result_path(MockStage.AMPLIFIER, filename)
        message = str(raised.exception)
        self.assertIn("Mock amplifier 正式结果文件", message)
        self.assertIn("不存在或不是普通文件", message)
        self.assertIn(filename, message)

    def test_missing_result_does_not_create_directories(self):
        filename = MOCK_RESULT_FILES[MockStage.DRIVER_MAPPING]
        with self.assertRaises(MockDataPathError):
            self.paths.require_result_path(MockStage.DRIVER_MAPPING, filename)
        self.assertFalse(self.paths.mock_data_dir.exists())

    def test_result_filename_cannot_escape_the_stage_directory(self):
        invalid_names = (
            "../outside.json",
            "..\\outside.json",
            "/absolute.json",
            "C:\\outside.json",
            "C:outside.json",
            ".",
            "..",
            "",
            " leading.json",
            "trailing.json ",
            None,
        )
        for filename in invalid_names:
            with self.subTest(filename=filename):
                with self.assertRaises(MockDataPathError):
                    self.paths.result_path(MockStage.CABLE_LOSS, filename)

    def test_result_filename_rejects_device_names_and_trailing_dot(self):
        invalid_names = (
            "CON.json",
            "con.json",
            "NUL",
            "COM1.json",
            "LPT9.json",
            "result.",
            "result.json.",
        )
        for filename in invalid_names:
            with self.subTest(filename=filename):
                with self.assertRaises(MockDataPathError):
                    self.paths.result_path(MockStage.CABLE_LOSS, filename)

    def test_result_filename_accepts_names_that_only_look_reserved(self):
        for filename in ("console.json", "com10.json", "lpt0.json"):
            with self.subTest(filename=filename):
                self.assertEqual(
                    self.paths.result_path(MockStage.CABLE_LOSS, filename).name,
                    filename,
                )

    def test_result_path_stays_in_the_stage_directory(self):
        target = self.paths.result_path(
            MockStage.DRIVER_MAPPING,
            "result.json",
        )
        self.assertEqual(
            target,
            self.root / "mock_data" / "driver_mapping" / "result.json",
        )

    def test_scenario_file_link_outside_project_is_rejected(self):
        outside = self._outside_directory()
        external_scenario = self._create_file(outside / "scenario.json")
        stage = self.paths.stage_dir(MockStage.CABLE_LOSS)
        stage.mkdir(parents=True)
        link = stage / "scenario.json"
        _create_file_link(self, external_scenario, link)
        self.addCleanup(_remove_link, link)

        with self.assertRaises(MockDataPathError) as raised:
            self.paths.require_scenario_path(MockStage.CABLE_LOSS)
        self.assertIn("必须直接位于", str(raised.exception))
        self.assertIn(str(outside), str(raised.exception))

    def test_result_file_link_outside_stage_directory_is_rejected(self):
        outside = self._outside_directory()
        external_result = self._create_file(outside / "result.json")
        stage = self.paths.stage_dir(MockStage.AMPLIFIER)
        stage.mkdir(parents=True)
        link = stage / "result.json"
        _create_file_link(self, external_result, link)
        self.addCleanup(_remove_link, link)

        with self.assertRaises(MockDataPathError) as raised:
            self.paths.require_result_path(MockStage.AMPLIFIER, "result.json")
        self.assertIn("必须直接位于", str(raised.exception))

    def test_entry_path_link_to_outside_directory_is_rejected(self):
        """即使目标被命名为 ``scenario.json``，指向阶段目录外也必须拒绝。"""
        outside = self._outside_directory()
        stage = self.paths.stage_dir(MockStage.CABLE_LOSS)
        stage.mkdir(parents=True)
        link = stage / "scenario.json"
        _create_directory_link(self, outside, link)
        self.addCleanup(_remove_link, link)

        with self.assertRaises(MockDataPathError) as raised:
            self.paths.require_scenario_path(MockStage.CABLE_LOSS)
        self.assertIn("必须直接位于", str(raised.exception))

    def test_stage_directory_link_outside_mock_data_is_rejected(self):
        outside = self._outside_directory()
        self._create_file(outside / "scenario.json")
        self._create_file(outside / "result.json")
        self.paths.mock_data_dir.mkdir(parents=True)
        link = self.paths.stage_dir(MockStage.DRIVER_MAPPING)
        _create_directory_link(self, outside, link)
        self.addCleanup(_remove_link, link)

        with self.assertRaises(MockDataPathError) as raised:
            self.paths.require_scenario_path(MockStage.DRIVER_MAPPING)
        self.assertIn("不能指向 mock_data 之外", str(raised.exception))

        with self.assertRaises(MockDataPathError):
            self.paths.require_result_path(MockStage.DRIVER_MAPPING, "result.json")

    def test_mock_data_directory_link_outside_project_is_rejected(self):
        outside = self._outside_directory()
        self._create_file(outside / "cable_loss" / "scenario.json")
        link = self.paths.mock_data_dir
        _create_directory_link(self, outside, link)
        self.addCleanup(_remove_link, link)

        with self.assertRaises(MockDataPathError) as raised:
            self.paths.require_scenario_path(MockStage.CABLE_LOSS)
        self.assertIn("不能指向项目根目录之外", str(raised.exception))

    def test_directory_target_is_rejected_as_not_a_regular_file(self):
        scenario = self.paths.scenario_path(MockStage.CABLE_LOSS)
        scenario.mkdir(parents=True)

        with self.assertRaises(MockDataPathError) as raised:
            self.paths.require_scenario_path(MockStage.CABLE_LOSS)
        self.assertIn("不存在或不是普通文件", str(raised.exception))

    def test_calibration_input_stages_are_exposed_by_the_service(self):
        self.assertEqual(
            self.paths.calibration_input_stages(MockStage.CABLE_LOSS),
            (),
        )
        self.assertEqual(
            self.paths.calibration_input_stages("driver_mapping"),
            (MockStage.CABLE_LOSS,),
        )
        self.assertEqual(
            self.paths.calibration_input_stages(MockStage.AMPLIFIER),
            (MockStage.CABLE_LOSS, MockStage.DRIVER_MAPPING),
        )

    def test_default_service_uses_the_repository_mock_data_directory(self):
        self.assertEqual(
            DEFAULT_MOCK_DATA_PATHS.mock_data_dir,
            PROJECT_ROOT / "mock_data",
        )
        self.assertEqual(
            DEFAULT_MOCK_DATA_PATHS.config_file,
            PROJECT_ROOT / "config.mock.json",
        )


class MockMeasurementTypePathTests(unittest.TestCase):
    """模拟会话按正式测量类型取路径，不自行拼接阶段目录名。"""

    def setUp(self):
        self.paths = MockDataPathService(PROJECT_ROOT)

    def test_scenario_and_result_paths_resolve_by_formal_measurement_type(self):
        for measurement_type, filename in (
            (MeasurementType.CABLE_LOSS.value, MOCK_RESULT_FILES[MockStage.CABLE_LOSS]),
            (
                MeasurementType.DRIVER_POWER_MAPPING.value,
                MOCK_RESULT_FILES[MockStage.DRIVER_MAPPING],
            ),
            (
                MeasurementType.AMPLIFIER.value,
                MOCK_RESULT_FILES[MockStage.AMPLIFIER],
            ),
        ):
            with self.subTest(measurement_type=measurement_type):
                scenario = self.paths.require_scenario_path_for(measurement_type)
                result = self.paths.require_result_path_for(
                    measurement_type, filename
                )
                self.assertTrue(scenario.is_file())
                self.assertTrue(result.is_file())
        self.assertEqual(
            self.paths.require_scenario_path_for("driver_power_mapping").parent.name,
            "driver_mapping",
        )

    def test_unknown_measurement_type_has_a_readable_error(self):
        with self.assertRaises(MockDataPathError):
            self.paths.require_scenario_path_for("not_a_measurement")

    def test_calibration_reference_resolves_inside_its_stage(self):
        reference = (
            "mock_data/cable_loss/20261117T060245Z_9b2e7d10.json"
        )
        resolved = self.paths.require_reference_path(reference)
        self.assertTrue(resolved.is_file())
        self.assertEqual(resolved.parent.name, "cable_loss")

    def test_calibration_reference_with_bad_shape_is_rejected(self):
        for reference in (
            "cable_loss/20261117T060245Z_9b2e7d10.json",
            "/mock_data/cable_loss/20261117T060245Z_9b2e7d10.json",
            "mock_data/cable_loss/../scenario.json",
            "mock_data/unknown/20261117T060245Z_9b2e7d10.json",
            " mock_data/cable_loss/20261117T060245Z_9b2e7d10.json",
        ):
            with self.subTest(reference=reference):
                with self.assertRaises(MockDataPathError):
                    self.paths.require_reference_path(reference)

    def test_calibration_reference_to_a_missing_file_is_rejected(self):
        with self.assertRaises(MockDataPathError):
            self.paths.require_reference_path("mock_data/cable_loss/missing.json")


if __name__ == "__main__":
    unittest.main()
