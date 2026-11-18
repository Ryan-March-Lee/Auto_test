r"""工具契约：``tools/build_simulation_readings.py``（重构计划步骤 3）。

验收重点：

- 仓库里的三份 ``readings.json`` 必须能由场景 + 打包正式结果在内存中逐字节重建，
  数据漂移会在全量离线测试里直接失败，而不是只依赖手动执行 ``--check``；
- ``--check`` 在数据一致时退出 0，场景或正式结果变化时退出非 0；
- 反算出的原始读数正向补偿后回到正式结果的补偿后值，误差 ``< 1e-9 dB``；
- 线损顺序与 ``CableLossService.run()`` 完全一致（路径 1 全频点 → 路径 2 全频点）。

验证方式：

```powershell
& "D:\Anaconda\envs\Auto_test\python.exe" -m unittest tests.test_build_simulation_readings -v
```
"""

import bisect
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from application.dto.measurement_results import MeasurementType
from application.dto.simulation_readings import SimulationReadings
from application.errors import ConfigurationError
from application.inputs.mock_result import load_mock_result
from application.inputs.mock_scenario import load_mock_scenario
from application.ports.stage_result_repository import measurement_type_for_stage
from domain.measurement.calculations import (
    compensate_amplifier_output_power,
    compensate_driver_output_power,
)
from domain.measurement.values import (
    CableLossTable,
    Decibels,
    Frequency,
    PowerDbm,
)
from infrastructure.filesystem.paths import (
    DEFAULT_MOCK_DATA_PATHS,
    MockDataPathService,
    MockStage,
)
from tools.build_simulation_readings import (
    AMPLIFIER_COVERAGE_MARGIN_DB,
    READINGS_DECIMALS,
    REPLAY_TOP_MARGIN_DB,
    ROUND_TRIP_TOLERANCE_DB,
    _lf_bytes,
    build_readings_bytes,
    build_readings_document,
    check_readings,
    main,
    render_readings,
    write_readings,
)


PROJECT_ROOT = Path(__file__).parents[1]
TOLERANCE = ROUND_TRIP_TOLERANCE_DB


def _readings(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _frequency(value):
    return Frequency.from_ghz(float(value))


def _loss_table(paths, scenario):
    reference = scenario.source_config.cable_loss_result
    result = load_mock_result(
        paths.require_reference_path(reference),
        expected_measurement_type=MeasurementType.CABLE_LOSS,
    )
    return (
        CableLossTable(
            {
                _frequency(row.frequency_ghz): row.cable_losses
                for row in result.points
            }
        ),
        Decibels(float(scenario.source_config.attenuator_db)),
    )


class RepositoryReadingsTests(unittest.TestCase):
    """仓库数据必须与生成器完全一致。"""

    def test_check_mode_passes_on_the_repository(self):
        for stage in MockStage:
            with self.subTest(stage=stage.value):
                ok, message = check_readings(stage)
                self.assertTrue(ok, message)

    def test_cli_check_exits_zero(self):
        self.assertEqual(main(["--check"]), 0)

    def test_repository_files_equal_a_fresh_in_memory_build(self):
        for stage in MockStage:
            with self.subTest(stage=stage.value):
                built = build_readings_bytes(stage)
                stored = _lf_bytes(
                    DEFAULT_MOCK_DATA_PATHS.readings_path(stage).read_bytes()
                )
                self.assertEqual(
                    stored,
                    built,
                    "readings.json 与生成结果不一致：请运行 "
                    "tools/build_simulation_readings.py 更新",
                )

    def test_render_is_stable_and_matches_the_written_file(self):
        for stage in MockStage:
            with self.subTest(stage=stage.value):
                document = build_readings_document(stage)
                self.assertEqual(
                    render_readings(document), render_readings(build_readings_document(stage))
                )
                self.assertEqual(
                    render_readings(document),
                    build_readings_bytes(stage),
                )

    def test_generated_documents_satisfy_the_runtime_contract(self):
        for stage in MockStage:
            with self.subTest(stage=stage.value):
                document = build_readings_document(stage)
                scenario = load_mock_scenario(
                    DEFAULT_MOCK_DATA_PATHS.require_scenario_path(stage),
                    expected_measurement_type=measurement_type_for_stage(
                        stage.value
                    ),
                )
                readings = SimulationReadings.from_document(
                    document,
                    expected_measurement_type=measurement_type_for_stage(
                        stage.value
                    ),
                    expected_frequencies_ghz=scenario.source_config.test_frequencies_ghz,
                )
                self.assertEqual(
                    [frequency.ghz for frequency in readings.frequency_grid],
                    list(scenario.source_config.test_frequencies_ghz),
                )


class CableLossOrderTests(unittest.TestCase):
    """线损有序序列必须与服务真实扫描顺序一致。"""

    def test_measurements_are_path_one_then_path_two(self):
        document = build_readings_document(MockStage.CABLE_LOSS)
        measurements = document["payload"]["measurements"]
        frequencies = list(
            load_mock_scenario(
                DEFAULT_MOCK_DATA_PATHS.require_scenario_path(MockStage.CABLE_LOSS)
            ).source_config.test_frequencies_ghz
        )
        self.assertEqual(
            [item["path"] for item in measurements],
            [1] * len(frequencies) + [2] * len(frequencies),
        )
        self.assertEqual(
            [item["frequency_ghz"] for item in measurements],
            frequencies + frequencies,
        )

    def test_cable_loss_raw_is_the_negated_total_loss(self):
        document = build_readings_document(MockStage.CABLE_LOSS)
        paths = DEFAULT_MOCK_DATA_PATHS
        scenario = load_mock_scenario(paths.require_scenario_path(MockStage.CABLE_LOSS))
        result = load_mock_result(
            paths.require_result_path(MockStage.CABLE_LOSS, scenario.result_filename),
            expected_measurement_type=MeasurementType.CABLE_LOSS,
        )
        totals = {
            _frequency(row.frequency_ghz): (
                row.cable_losses.total_path1.db,
                row.cable_losses.total_path2.db,
            )
            for row in result.points
        }
        for item in document["payload"]["measurements"]:
            frequency = _frequency(item["frequency_ghz"])
            expected = totals[frequency][item["path"] - 1]
            self.assertAlmostEqual(item["raw_dbm"], -expected, delta=TOLERANCE)
            self.assertAlmostEqual(
                abs(0.0 - item["raw_dbm"]), expected, delta=TOLERANCE
            )


class RoundTripTests(unittest.TestCase):
    """原始读数正向补偿后必须回到正式结果的补偿后值。"""

    def test_driver_raw_compensates_back_to_the_recorded_output(self):
        paths = DEFAULT_MOCK_DATA_PATHS
        scenario = load_mock_scenario(paths.require_scenario_path(MockStage.DRIVER_MAPPING))
        table, attenuator = _loss_table(paths, scenario)
        result = load_mock_result(
            paths.require_result_path(
                MockStage.DRIVER_MAPPING, scenario.result_filename
            ),
            expected_measurement_type=MeasurementType.DRIVER_POWER_MAPPING,
        )
        recorded: dict[Frequency, list[tuple[float, float]]] = {}
        for point in result.raw_points:
            recorded.setdefault(_frequency(point.frequency_ghz), []).append(
                (point.source_power_dbm, point.driver_output_power_dbm)
            )
        for points in recorded.values():
            points.sort()
        document = build_readings_document(MockStage.DRIVER_MAPPING)
        for key, bucket in document["payload"]["frequencies"].items():
            frequency = _frequency(key)
            points = recorded[frequency]
            sources = [item[0] for item in points]
            for probe in bucket["probes"]:
                compensated = compensate_driver_output_power(
                    PowerDbm(probe["raw_output_dbm"]),
                    frequency,
                    table,
                    attenuator,
                ).dbm
                source = probe["source_power_dbm"]
                index = bisect.bisect_left(sources, source)
                if index < len(sources) and abs(sources[index] - source) <= TOLERANCE:
                    # 打包结果里的原始点：补偿后必须逐点回到记录值。
                    self.assertAlmostEqual(
                        compensated, points[index][1], delta=TOLERANCE
                    )
                    continue
                # 当前搜索请求的补点：必须落在打包曲线的相邻记录点之间（或上界
                # 余量内），原始读数与补偿后的曲线一致，不能凭空取值。
                self.assertGreater(index, 0, f"{key} GHz 出现低于记录下界的补点")
                if index == len(sources):
                    self.assertLessEqual(
                        source, sources[-1] + REPLAY_TOP_MARGIN_DB + TOLERANCE,
                        f"{key} GHz 的补点超出允许的上界余量",
                    )
                    index = len(sources) - 1
                lower, upper = points[index - 1], points[index]
                expected = lower[1] + (source - lower[0]) * (
                    upper[1] - lower[1]
                ) / (upper[0] - lower[0])
                self.assertAlmostEqual(compensated, expected, delta=TOLERANCE)

    def test_amplifier_raw_compensates_back_to_the_recorded_output(self):
        paths = DEFAULT_MOCK_DATA_PATHS
        scenario = load_mock_scenario(paths.require_scenario_path(MockStage.AMPLIFIER))
        table, attenuator = _loss_table(paths, scenario)
        result = load_mock_result(
            paths.require_result_path(MockStage.AMPLIFIER, scenario.result_filename),
            expected_measurement_type=MeasurementType.AMPLIFIER,
        )
        recorded: dict[Frequency, list[tuple[float, float]]] = {}
        for item in result.frequencies:
            sweep = item.sweep_data
            points = recorded.setdefault(_frequency(item.frequency_ghz), [])
            for index, sg_power in enumerate(sweep["sg_power"]):
                points.append((sg_power, sweep["output_power_dut"][index]))
        for points in recorded.values():
            points.sort()
        document = build_readings_document(MockStage.AMPLIFIER)
        for key, bucket in document["payload"]["frequencies"].items():
            frequency = _frequency(key)
            points = recorded[frequency]
            sources = [item[0] for item in points]
            for probe in bucket["points"]:
                compensated = compensate_amplifier_output_power(
                    PowerDbm(probe["raw_output_dbm"]),
                    frequency,
                    table,
                    attenuator,
                ).dbm
                source = probe["sg_power_dbm"]
                index = bisect.bisect_left(sources, source)
                if index < len(sources) and abs(sources[index] - source) <= TOLERANCE:
                    self.assertAlmostEqual(
                        compensated, points[index][1], delta=TOLERANCE
                    )
                    continue
                # 覆盖边界点：只能在打包扫描范围两端 ``AMPLIFIER_COVERAGE_MARGIN_DB``
                # 内，并且按端点斜率延伸，不能凭空取值。
                if index == 0:
                    self.assertGreaterEqual(
                        source,
                        sources[0] - AMPLIFIER_COVERAGE_MARGIN_DB - TOLERANCE,
                        f"{key} GHz 的边界点超出允许的覆盖余量",
                    )
                    index = 1
                elif index == len(sources):
                    self.assertLessEqual(
                        source,
                        sources[-1] + AMPLIFIER_COVERAGE_MARGIN_DB + TOLERANCE,
                        f"{key} GHz 的边界点超出允许的覆盖余量",
                    )
                    index = len(sources) - 1
                lower, upper = points[index - 1], points[index]
                expected = lower[1] + (source - lower[0]) * (
                    upper[1] - lower[1]
                ) / (upper[0] - lower[0])
                self.assertAlmostEqual(compensated, expected, delta=TOLERANCE)

    def test_raw_values_keep_the_declared_precision(self):
        for stage in MockStage:
            document = build_readings_document(stage)
            payload = document["payload"]
            raws = []
            if "measurements" in payload:
                raws = [item["raw_dbm"] for item in payload["measurements"]]
            else:
                for bucket in payload["frequencies"].values():
                    for item in bucket.get("probes", []) or bucket.get("points", []):
                        raws.append(item["raw_output_dbm"])
            for raw in raws:
                self.assertEqual(
                    raw,
                    round(raw, READINGS_DECIMALS),
                    "原始读数应已收敛到声明的小数位",
                )


class ScenarioPairingTests(unittest.TestCase):
    """场景的源功率与输出功率必须成对匹配正式结果，不能只比较两组值的集合。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        shutil.copytree(
            DEFAULT_MOCK_DATA_PATHS.mock_data_dir, self.root / "mock_data"
        )
        self.paths = MockDataPathService(self.root)

    def test_swapped_scenario_outputs_are_rejected(self):
        scenario_path = self.paths.require_scenario_path(MockStage.DRIVER_MAPPING)
        document = _readings(scenario_path)
        events = [
            event for phase in document["phases"] for event in phase["events"]
        ]
        latest_at: dict[float, int] = {}
        for index, event in enumerate(events):
            payload = event.get("payload") or {}
            if "sweep_data" in payload:
                latest_at[float(payload["frequency"])] = index
        sweep = events[latest_at[1.0]]["payload"]["sweep_data"]
        first, second = sweep["output_power_driver"][:2]
        self.assertNotEqual(first, second)
        # 只交换两个输出值：两组值的集合不变，配对关系错了。
        sweep["output_power_driver"][0] = second
        sweep["output_power_driver"][1] = first
        scenario_path.write_text(
            json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        with self.assertRaises(ConfigurationError) as raised:
            build_readings_document(MockStage.DRIVER_MAPPING, mock_paths=self.paths)
        self.assertIn("补偿后输出", str(raised.exception))


class DriftDetectionTests(unittest.TestCase):
    """数据漂移必须让 ``--check`` 退出非 0，而不是静默通过。"""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        shutil.copytree(
            DEFAULT_MOCK_DATA_PATHS.mock_data_dir, self.root / "mock_data"
        )
        self.paths = MockDataPathService(self.root)

    def test_changed_scenario_is_detected(self):
        scenario_path = self.paths.scenario_path(MockStage.CABLE_LOSS)
        original = scenario_path.read_text(encoding="utf-8-sig")
        scenario_path.write_text(original + "\n", encoding="utf-8")

        ok, message = check_readings(MockStage.CABLE_LOSS, mock_paths=self.paths)
        self.assertFalse(ok)
        self.assertIn("readings.json", message)
        self.assertEqual(main(["--check", "--stage", "cable_loss"], mock_paths=self.paths), 1)
        # 其余阶段仍一致：漂移只影响自己那一步。
        ok, message = check_readings(MockStage.DRIVER_MAPPING, mock_paths=self.paths)
        self.assertTrue(ok, message)

    def test_changed_formal_result_is_detected(self):
        """修改打包线损的一条线缆损耗会改变驱动/主功放的原始读数与摘要。"""
        result_path = self.paths.require_result_path(
            MockStage.CABLE_LOSS, "20261117T060245Z_9b2e7d10.json"
        )
        document = _readings(result_path)
        document["cable_losses"]["1.0"]["cable2"] = (
            document["cable_losses"]["1.0"]["cable2"] + 0.000001
        )
        result_path.write_text(
            json.dumps(document, indent=4, ensure_ascii=False), encoding="utf-8"
        )

        for stage in (MockStage.DRIVER_MAPPING, MockStage.AMPLIFIER):
            with self.subTest(stage=stage.value):
                ok, message = check_readings(stage, mock_paths=self.paths)
                self.assertFalse(ok, message)
                self.assertIn("不一致", message)
                self.assertEqual(
                    main(["--check", "--stage", stage.value], mock_paths=self.paths),
                    1,
                )

    def test_missing_readings_file_is_reported(self):
        for stage in MockStage:
            self.paths.readings_path(stage).unlink()
            with self.subTest(stage=stage.value):
                ok, message = check_readings(stage, mock_paths=self.paths)
                self.assertFalse(ok)
                self.assertIn("不存在", message)
        self.assertEqual(
            main(["--check"], mock_paths=self.paths),
            1,
        )

    def test_write_recreates_the_file_from_data(self):
        for stage in MockStage:
            self.paths.readings_path(stage).unlink()
            written = write_readings(stage, mock_paths=self.paths)
            self.assertTrue(written.is_file())
            ok, message = check_readings(stage, mock_paths=self.paths)
            self.assertTrue(ok, message)

    def test_rewriting_is_idempotent(self):
        for stage in MockStage:
            with self.subTest(stage=stage.value):
                before = self.paths.readings_path(stage).read_bytes()
                write_readings(stage, mock_paths=self.paths)
                after = self.paths.readings_path(stage).read_bytes()
                self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
