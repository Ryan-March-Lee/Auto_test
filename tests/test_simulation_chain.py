"""阶段三：无 GUI 的真实用例验证（重构计划步骤 10～14）。

测试按重构计划的三条约束组链：

- 只替换``MeasurementInstrumentPort`` 的读数，时钟用 ``sleep_fn`` 快进；
  用例、服务、阶段记录、manifest、归档与部分结果保存全部是正式代码；
- 所有运行、阶段结果与产物都落在本次临时根目录的 ``simulation_runs/`` 下，
  真实 ``runs/`` 与 ``test_results/`` 不参与；
- 阶段隔离用打包校准结果与端口严格模式；全链路使用本次新产生的校准结果，
  端口允许区间内插值、禁止外推，期望值由“同一份 raw + 新校准”重算。

覆盖验收：

- 步骤 10：临时根目录创建真实 ``PreparedRun``、阶段仓库与归档服务；
- 步骤 11：线损用例逐点对照打包正式结果与场景实时事件，停止后保存部分结果；
- 步骤 12：驱动映射 31 个目标、每频点 33 次探测，严格模式只用记录点；
- 步骤 13：主功放 27～29 点/频点，压缩点与打包结果一致，CSV/报告生成；
- 步骤 14：线损 → 驱动映射 → 主功放连续运行，raw 不变、按新校准重算。
"""

import bisect
import json
import shutil
import tempfile
import unittest
from collections.abc import Mapping
from operator import attrgetter
from pathlib import Path

from app.cancellation import CancellationToken, MeasurementCancelled
from app.gui_runtime import (
    create_amplifier_measurement,
    create_cable_loss_measurement,
    create_driver_mapping_measurement,
)
from app.run_context import create_prepared_run, environment_version
from application.configuration_service import load_for_run
from application.dto.measurement_results import MeasurementType
from application.dto.simulation_readings import SimulationReadings
from application.inputs.mock_result import load_mock_result
from application.inputs.mock_scenario import load_mock_scenario
from application.measurements.services import DRIVER_MAPPING_HIT_TOLERANCE_DB
from application.ports.stage_result_repository import MeasurementStage
from application.run_stages import RunStageRecorder
from domain.measurement.calculations import (
    calculate_compression_result,
    compensate_amplifier_output_power,
    compensate_driver_output_power,
)
from domain.measurement.values import (
    CableLossTable,
    Decibels,
    Frequency,
    Percent,
    PowerDbm,
)
from domain.run.models import (
    STAGE_AMPLIFIER,
    STAGE_CABLE_LOSS,
    STAGE_DRIVER_MAPPING,
    RunStatus,
    StageStatus,
    generate_run_id,
)
from infrastructure.config.json_config_repository import JsonConfigurationRepository
from infrastructure.filesystem.paths import (
    DEFAULT_MOCK_DATA_PATHS,
    DEFAULT_PATHS,
    MockStage,
    PathService,
)
from infrastructure.persistence.result_repository import FileMeasurementResultRepository
from infrastructure.persistence.run_repository import RunRepository
from infrastructure.persistence.stage_result_repository import FileStageResultRepository
from instrument.simulation_port import (
    SIMULATION_POWER_COMMAND_FORMAT,
    SimulationRunPort,
)


PROJECT_ROOT = Path(__file__).parents[1]
MOCK_CONFIG_PATH = PROJECT_ROOT / "config.mock.json"
MEASUREMENT_STAGES = (
    MeasurementStage.CABLE_LOSS,
    MeasurementStage.DRIVER_MAPPING,
    MeasurementStage.AMPLIFIER,
)
#: 打包驱动目标表来自更早的搜索实现；它只用于“结果仍落在历史数据附近”的迁移
#: 对照，不能复用服务的 ±0.2 dB 业务命中容差来解释数据差异。
PACKAGED_TARGET_DRIFT_TOLERANCE_DB = 0.2
#: 打包主功放结果按两位/三位小数保存；效率与 dc_power 允许文档精度内的差异。
PACKAGED_EFFICIENCY_TOLERANCE = 0.01
PACKAGED_DC_POWER_TOLERANCE = 1e-3


def _mock_stage(stage):
    return MockStage(stage.value if isinstance(stage, MeasurementStage) else stage)


def _frequency(value):
    return Frequency.from_ghz(float(value))


def _snapshot_tree(root):
    """记录目录的元数据指纹；用于证明真实 runs/ 与 test_results/ 未被改动。

    只读文件系统信息，不读取/散列文件内容：真实结果目录可能很大，而验收要证明的是
    “测试期间没有新增、删除或改写任何条目”。目录自身的 mtime 也纳入，空目录被创建
    时同样能发现。
    """
    root = Path(root)
    if not root.exists():
        return ()
    stat = root.stat()
    entries = [(".", "dir", 0, stat.st_mtime_ns)]
    for path in sorted(root.rglob("*")):
        item = path.stat()
        if path.is_dir():
            entries.append(
                (path.relative_to(root).as_posix(), "dir", 0, item.st_mtime_ns)
            )
        else:
            entries.append(
                (
                    path.relative_to(root).as_posix(),
                    "file",
                    item.st_size,
                    item.st_mtime_ns,
                )
            )
    return tuple(entries)


class _EventRecorder:
    """把正式事件回调收敛为可比较的列表；不改变页面/worker 的调用契约。"""

    def __init__(self):
        self.data = []
        self.progress = []
        self.messages = []

    def callbacks(self):
        return {
            "data_callback": self.data.append,
            "progress_callback": self.progress.append,
            "message_callback": self.messages.append,
        }


class SimulationChainTestCase(unittest.TestCase):
    """步骤 10 夹具：临时仿真根里的真实运行、阶段仓库和归档服务。"""

    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary.cleanup)
        self.base = Path(self._temporary.name)
        self.simulation_root = self.base / "simulation_runs"
        self.paths = PathService(self.simulation_root)
        self.runs = RunRepository(self.simulation_root / "runs")
        self.result_repository = FileMeasurementResultRepository(paths=self.paths)
        self.stage_repository = FileStageResultRepository(paths=self.paths)
        self._run_counter = 0
        self._readings = self._load_readings()
        self._real_roots = (
            ("runs", DEFAULT_PATHS.root / "runs"),
            ("test_results", DEFAULT_PATHS.test_results_dir),
        )
        self._real_snapshot = self._snapshot_real_directories()

    def tearDown(self):
        # 每个用例结束都复核真实数据根：仅比较路径对象相等不足以证明没有误写。
        try:
            self._assert_real_directories_untouched()
        finally:
            super().tearDown()

    # -- 夹具 ---------------------------------------------------------------

    def _load_readings(self):
        readings = {}
        for stage in MEASUREMENT_STAGES:
            path = DEFAULT_MOCK_DATA_PATHS.require_readings_path_for(
                stage.measurement_type
            )
            document = json.loads(path.read_text(encoding="utf-8"))
            readings[stage.measurement_type] = SimulationReadings.from_document(
                document
            )
        return readings

    def _packaged_result(self, stage):
        """读取打包正式结果；它是阶段隔离的期望输出/校准输入。"""
        mock_stage = _mock_stage(stage)
        scenario = load_mock_scenario(
            DEFAULT_MOCK_DATA_PATHS.require_scenario_path(mock_stage),
            expected_measurement_type=stage.measurement_type,
        )
        path = DEFAULT_MOCK_DATA_PATHS.require_result_path(
            mock_stage, scenario.result_filename
        )
        return path, load_mock_result(
            path, expected_measurement_type=stage.measurement_type
        )

    def new_run(self):
        """创建一个真实 PreparedRun；运行目录只落在临时仿真根。"""
        self._run_counter += 1
        run_id = generate_run_id(token=f"{self._run_counter:08x}")
        loaded = load_for_run(JsonConfigurationRepository(), str(MOCK_CONFIG_PATH))
        hardware_config = json.loads(
            MOCK_CONFIG_PATH.read_text(encoding="utf-8")
        )
        prepared = create_prepared_run(
            loaded,
            run_id=run_id,
            software_version=environment_version(),
            config_path=MOCK_CONFIG_PATH,
            paths=self.paths,
            run_repository=self.runs,
            hardware_config=hardware_config,
        )
        self.assertEqual(
            Path(prepared.run_directory).parent,
            self.simulation_root / "runs",
            "运行目录必须落在临时仿真根，不能写入真实 runs/",
        )
        self.assertTrue(prepared.has_run_contract)
        self.assertEqual(prepared.run_id, run_id)
        return prepared

    def _start_port(self, measurement_type, *, strict=False, all_stages=False):
        readings = (
            self._readings
            if all_stages
            else {measurement_type: self._readings[measurement_type]}
        )
        port = SimulationRunPort(readings, strict=strict)
        port.begin_stage()
        port.activate(measurement_type)
        return port

    def _activate(self, port, measurement_type):
        port.begin_stage()
        port.activate(measurement_type)

    def _seed_packaged_stage(self, stage):
        """把打包正式结果复制到仿真阶段目录，模拟“已完成的上游阶段”。"""
        source, _result = self._packaged_result(stage)
        directories = {
            MeasurementStage.CABLE_LOSS: self.paths.cable_loss_results_dir,
            MeasurementStage.DRIVER_MAPPING: self.paths.driver_mapping_results_dir,
            MeasurementStage.AMPLIFIER: self.paths.amplifier_results_dir,
        }
        target_directory = directories[stage]
        target_directory.mkdir(parents=True, exist_ok=True)
        target = target_directory / source.name
        shutil.copy2(source, target)
        return target

    def _complete_seeded_stage(self, prepared, stage, path):
        """把已复制的阶段结果登记为当前运行 completed。"""
        loaded = self.stage_repository.load(stage, path)
        recorder = RunStageRecorder(
            self.runs, prepared.run_id, stage_results=self.stage_repository
        )
        recorder.start(stage.value)
        recorder.complete_with_stage_result(stage.value, loaded.reference)
        return loaded

    def _cable_loss_case(self, prepared, port, events=None):
        return create_cable_loss_measurement(
            prepared_run=prepared,
            measurement_port=port,
            result_repository=self.result_repository,
            stage_result_repository=self.stage_repository,
            paths=self.paths,
            sleep_fn=lambda _seconds: None,
            **(events.callbacks() if events is not None else {}),
        )

    def _driver_mapping_case(self, prepared, port, cable_loss_path, events=None):
        return create_driver_mapping_measurement(
            prepared_run=prepared,
            cable_loss_path=cable_loss_path,
            measurement_port=port,
            result_repository=self.result_repository,
            stage_result_repository=self.stage_repository,
            paths=self.paths,
            sleep_fn=lambda _seconds: None,
            **(events.callbacks() if events is not None else {}),
        )

    def _amplifier_case(
        self, prepared, port, cable_loss_path, driver_mapping_path, events=None
    ):
        return create_amplifier_measurement(
            prepared_run=prepared,
            cable_loss_path=cable_loss_path,
            driver_mapping_path=driver_mapping_path,
            measurement_port=port,
            result_repository=self.result_repository,
            stage_result_repository=self.stage_repository,
            paths=self.paths,
            sleep_fn=lambda _seconds: None,
            **(events.callbacks() if events is not None else {}),
        )

    def _run_cable_loss(self, prepared, *, port=None, events=None):
        port = port or self._start_port(MeasurementType.CABLE_LOSS)
        case = self._cable_loss_case(prepared, port, events)
        try:
            case.measure_all_frequencies()
            result = case.continue_to_step2()
        finally:
            port.end_stage()
        return case, result

    def _run_driver_mapping(
        self, prepared, cable_loss_path, *, port=None, strict=True, events=None
    ):
        port = port or self._start_port(
            MeasurementType.DRIVER_POWER_MAPPING, strict=strict
        )
        case = self._driver_mapping_case(prepared, port, cable_loss_path, events)
        try:
            result = case.measure_all_frequencies()
        finally:
            port.end_stage()
        return case, result

    def _run_amplifier(
        self,
        prepared,
        cable_loss_path,
        driver_mapping_path,
        *,
        port=None,
        strict=True,
        events=None,
    ):
        port = port or self._start_port(MeasurementType.AMPLIFIER, strict=strict)
        case = self._amplifier_case(
            prepared, port, cable_loss_path, driver_mapping_path, events
        )
        try:
            result = case.measure_all_frequencies()
        finally:
            port.end_stage()
        return case, result

    def _manifest(self, prepared):
        return self.runs.load_manifest(prepared.run_id)

    def _stage_result(self, result):
        """正式结果自带的阶段文件路径；阶段隔离与全链路都用它做显式输入。"""
        path = result.result_reference.get("archive_path")
        self.assertIsNotNone(path)
        return Path(path)

    def _assert_under_simulation_root(self, path):
        self.assertTrue(
            Path(path).resolve().is_relative_to(self.simulation_root.resolve()),
            f"{path} 不在临时仿真根内",
        )

    def _snapshot_real_directories(self):
        return tuple(
            (label, _snapshot_tree(path)) for label, path in self._real_roots
        )

    def _assert_real_directories_untouched(self, run_id=None):
        if run_id is not None:
            self.assertFalse((DEFAULT_PATHS.root / "runs" / run_id).exists())
        self.assertNotEqual(
            self.paths.test_results_dir, DEFAULT_PATHS.test_results_dir
        )
        self.assertEqual(
            self._snapshot_real_directories(),
            self._real_snapshot,
            "真实 runs/ 或 test_results/ 在测试期间发生变化；仿真用例必须只写临时根",
        )

    # -- 比较工具 -----------------------------------------------------------

    def assert_mapping_close(self, actual, expected, *, label="", delta=1e-9, path=""):
        """递归比较事件/结果映射；浮点按 ``delta`` 比较。"""
        if isinstance(expected, Mapping):
            self.assertIsInstance(actual, Mapping, f"{label}{path} 不是映射")
            self.assertEqual(
                set(actual), set(expected), f"{label}{path} 字段集合不一致"
            )
            for key in expected:
                self.assert_mapping_close(
                    actual[key],
                    expected[key],
                    label=label,
                    delta=delta,
                    path=f"{path}.{key}",
                )
            return
        if isinstance(expected, float):
            self.assertAlmostEqual(
                float(actual), expected, delta=delta, msg=f"{label}{path}"
            )
            return
        self.assertEqual(actual, expected, f"{label}{path}")

    def _scenario(self, stage):
        return load_mock_scenario(
            DEFAULT_MOCK_DATA_PATHS.require_scenario_path(_mock_stage(stage)),
            expected_measurement_type=stage.measurement_type,
        )

    def _scenario_events(self, stage, event_kind):
        return tuple(
            event
            for phase in self._scenario(stage).phases
            for event in phase.events
            if type(event).__name__ == event_kind
        )

    def _cable_loss_table(self, result):
        table = CableLossTable(
            {
                _frequency(row.frequency_ghz): row.cable_losses
                for row in result.points
            }
        )
        return table, Decibels(float(result.attenuator_value))

    @staticmethod
    def _quantized_power(power_dbm):
        """复现信号源下发的 6 位有效数字命令值；与端口的查找值一致。"""
        return float(f"{float(power_dbm):{SIMULATION_POWER_COMMAND_FORMAT}}")

    @staticmethod
    def _frequency_bucket(payload, frequency_ghz):
        for frequency, bucket in payload.frequencies.items():
            if abs(frequency.ghz - float(frequency_ghz)) <= 1e-9:
                return bucket
        raise AssertionError(f"冻结读数缺少 {frequency_ghz} GHz 的记录")

    def _surrounding_indices(self, records, value, key, label):
        """按冻结读数的数值规则定位记录点；返回 (lower, upper, 是否插值)。"""
        values = [key(record) for record in records]
        index = bisect.bisect_left(values, value)
        for candidate in (index - 1, index):
            if 0 <= candidate < len(values) and abs(values[candidate] - value) <= 1e-9:
                return candidate, candidate, False
        self.assertGreater(index, 0, f"{label} 请求 {value} dBm 低于记录下界")
        self.assertLess(index, len(values), f"{label} 请求 {value} dBm 超出记录上界")
        return index - 1, index, True

    def _expected_driver_reading(self, frequency_ghz, power_dbm):
        """用冻结读数独立算出驱动映射 raw；只用于对照端口返回值。"""
        payload = self._readings[MeasurementType.DRIVER_POWER_MAPPING].payload
        bucket = self._frequency_bucket(payload, frequency_ghz)
        probes = bucket.probes
        power = self._quantized_power(power_dbm)
        lower_index, upper_index, interpolated = self._surrounding_indices(
            probes,
            power,
            lambda probe: probe.source_power_dbm,
            f"驱动映射 {frequency_ghz} GHz",
        )
        if not interpolated:
            return probes[lower_index].raw_output_dbm, False
        lower, upper = probes[lower_index], probes[upper_index]
        fraction = (power - lower.source_power_dbm) / (
            upper.source_power_dbm - lower.source_power_dbm
        )
        return (
            lower.raw_output_dbm
            + fraction * (upper.raw_output_dbm - lower.raw_output_dbm),
            True,
        )

    def _expected_amplifier_reading(self, frequency_ghz, power_dbm):
        """用冻结读数独立算主功放 raw 与 V/I；只用于对照端口返回值。"""
        payload = self._readings[MeasurementType.AMPLIFIER].payload
        bucket = self._frequency_bucket(payload, frequency_ghz)
        points = bucket.points
        power = self._quantized_power(power_dbm)
        lower_index, upper_index, interpolated = self._surrounding_indices(
            points,
            power,
            lambda point: point.sg_power_dbm,
            f"主功放 {frequency_ghz} GHz",
        )
        if not interpolated:
            point = points[lower_index]
            return (
                point.raw_output_dbm,
                dict(point.voltages),
                dict(point.currents),
                False,
            )
        lower, upper = points[lower_index], points[upper_index]
        fraction = (power - lower.sg_power_dbm) / (
            upper.sg_power_dbm - lower.sg_power_dbm
        )

        def interpolate(left, right):
            return left + fraction * (right - left)

        self.assertEqual(set(lower.voltages), set(upper.voltages))
        self.assertEqual(set(lower.currents), set(upper.currents))
        return (
            interpolate(lower.raw_output_dbm, upper.raw_output_dbm),
            {
                channel: interpolate(lower.voltages[channel], upper.voltages[channel])
                for channel in lower.voltages
            },
            {
                channel: interpolate(lower.currents[channel], upper.currents[channel])
                for channel in lower.currents
            },
            True,
        )

    def _expected_interpolation_counts(self, measurement_type, powers_by_frequency):
        """按“每个唯一命令功率是否精确命中记录点”独立重算端口插值次数。"""
        counts = {}
        for frequency_ghz, powers in powers_by_frequency.items():
            payload = self._readings[measurement_type].payload
            if measurement_type is MeasurementType.DRIVER_POWER_MAPPING:
                records = self._frequency_bucket(payload, frequency_ghz).probes
                key = attrgetter("source_power_dbm")
            else:
                records = self._frequency_bucket(payload, frequency_ghz).points
                key = attrgetter("sg_power_dbm")
            seen = set()
            count = 0
            for power in powers:
                quantized = self._quantized_power(power)
                if quantized in seen:
                    continue
                seen.add(quantized)
                _, _, interpolated = self._surrounding_indices(
                    records,
                    quantized,
                    key,
                    f"{measurement_type.value} {frequency_ghz} GHz",
                )
                count += int(interpolated)
            if count:
                counts[Frequency.from_ghz(float(frequency_ghz))] = count
        return counts


class SimulationChainFixtureTests(SimulationChainTestCase):
    """步骤 10：夹具本身的可验收性。"""

    def test_run_layout_matches_the_simulation_root_contract(self):
        prepared = self.new_run()

        run_directory = Path(prepared.run_directory)
        self.assertTrue((run_directory / "config.json").is_file())
        self.assertTrue((run_directory / "manifest.json").is_file())
        self.assertTrue(
            (run_directory / "inputs" / "hardware_config.json").is_file()
        )
        self.assertEqual(
            self.paths.test_results_dir, self.simulation_root / "test_results"
        )
        self.assertEqual(
            self.paths.test_results_dir.parent, self.simulation_root
        )
        self._assert_under_simulation_root(run_directory)
        self._assert_real_directories_untouched(prepared.run_id)

    def test_stage_result_repository_writes_inside_the_simulation_root(self):
        self.new_run()
        path = self._seed_packaged_stage(MeasurementStage.CABLE_LOSS)
        loaded = self.stage_repository.load(MeasurementStage.CABLE_LOSS, path)
        self.assertEqual(
            self.paths.cable_loss_results_dir.parent,
            self.paths.test_results_dir,
        )
        self.assertEqual(loaded.reference.path.parent, path.parent)
        self._assert_under_simulation_root(self.paths.cable_loss_results_dir)
        self._assert_under_simulation_root(path)


class CableLossSimulationChainTests(SimulationChainTestCase):
    """步骤 11：线损用例 + 回放端口的无 GUI 对照。"""

    def test_cable_loss_reproduces_packaged_result_and_realtime_events(self):
        prepared = self.new_run()
        events = _EventRecorder()
        case, result = self._run_cable_loss(prepared, events=events)

        self.assertEqual(result.status.value, "completed")
        self.assertEqual(len(result.points), 11)
        packaged_path, packaged = self._packaged_result(MeasurementStage.CABLE_LOSS)
        self.assertTrue(packaged_path.is_file())
        self.assert_mapping_close(
            result.cable_losses(),
            packaged.cable_losses(),
            label="线损结果",
        )
        self.assertEqual(
            case.last_archival_outcome.test_status.value, "partial"
        )
        self._assert_under_simulation_root(self._stage_result(result))
        self.assertTrue(self._stage_result(result).is_file())

        data_events = self._scenario_events(MeasurementStage.CABLE_LOSS, "MockDataEvent")
        self.assertEqual(len(events.data), len(data_events))
        for actual, expected in zip(events.data, data_events):
            self.assert_mapping_close(
                actual, expected.payload, label="线损实时数据"
            )
        progress_events = self._scenario_events(
            MeasurementStage.CABLE_LOSS, "MockProgressEvent"
        )
        self.assertEqual(
            events.progress, [event.percent for event in progress_events]
        )
        manifest = self._manifest(prepared)
        self.assertEqual(manifest.status, RunStatus.RUNNING)
        self.assertEqual(
            manifest.stage(STAGE_CABLE_LOSS).status, StageStatus.COMPLETED
        )
        self._assert_real_directories_untouched(prepared.run_id)

    def test_cable_loss_stop_saves_partial_result_after_complete_frequencies(self):
        prepared = self.new_run()
        token = CancellationToken()
        sleeps = {"count": 0}

        def sleep_fn(_seconds):
            sleeps["count"] += 1
            # 路径 1 共 11 点；路径 2 测完 2 个频点后请求停止。
            if sleeps["count"] >= 13:
                token.request_stop(reason="测试停止")

        port = self._start_port(MeasurementType.CABLE_LOSS)
        case = create_cable_loss_measurement(
            prepared_run=prepared,
            measurement_port=port,
            result_repository=self.result_repository,
            stage_result_repository=self.stage_repository,
            paths=self.paths,
            cancellation_token=token,
            sleep_fn=sleep_fn,
        )
        case.measure_all_frequencies()
        with self.assertRaises(MeasurementCancelled):
            case.continue_to_step2()
        port.end_stage()

        self.assertIsNotNone(case.last_partial_result)
        self.assertEqual(case.last_partial_result.status.value, "cancelled")
        self.assertEqual(len(case.last_partial_result.points), 2)
        manifest = self._manifest(prepared)
        self.assertEqual(
            manifest.stage(STAGE_CABLE_LOSS).status, StageStatus.CANCELLED
        )
        self.assertIsNone(manifest.stage(STAGE_CABLE_LOSS).result)
        self.assertEqual(
            [path.name for path in self.paths.cable_loss_results_dir.iterdir()]
            if self.paths.cable_loss_results_dir.is_dir()
            else [],
            [],
            "停止时不得生成 completed 阶段结果",
        )


class DriverMappingSimulationChainTests(SimulationChainTestCase):
    """步骤 12：驱动映射用例在严格模式下逐点对照。"""

    def test_driver_mapping_strict_replay_matches_packaged_grid(self):
        prepared = self.new_run()
        cable_loss_path = self._seed_packaged_stage(MeasurementStage.CABLE_LOSS)
        events = _EventRecorder()
        port = self._start_port(
            MeasurementType.DRIVER_POWER_MAPPING, strict=True
        )
        case = self._driver_mapping_case(
            prepared, port, cable_loss_path, events
        )
        result = case.measure_all_frequencies()
        interpolation_counts = port.interpolation_counts
        port.end_stage()

        self.assertEqual(interpolation_counts, {}, "严格模式不得发生插值")
        packaged_path, packaged = self._packaged_result(
            MeasurementStage.DRIVER_MAPPING
        )
        self.assertTrue(packaged_path.is_file())
        _, packaged_cable_loss = self._packaged_result(MeasurementStage.CABLE_LOSS)
        table, attenuator = self._cable_loss_table(packaged_cable_loss)
        frequencies = [
            Frequency.from_ghz(value)
            for value in self._scenario(
                MeasurementStage.DRIVER_MAPPING
            ).source_config.test_frequencies_ghz
        ]
        self.assertEqual(
            {point.frequency_ghz for point in result.raw_points},
            {frequency.key for frequency in frequencies},
        )
        packaged_by_frequency = {}
        for point in packaged.target_points:
            packaged_by_frequency.setdefault(point.frequency_ghz, {})[
                point.target_input_power_dbm
            ] = (point.source_power_dbm, point.driver_output_power_dbm)

        raw_per_frequency = {}
        for point in result.raw_points:
            raw_per_frequency.setdefault(point.frequency_ghz, []).append(point)
        target_per_frequency = {}
        for point in result.target_points:
            target_per_frequency.setdefault(point.frequency_ghz, []).append(point)

        # 严格模式下每个 raw 点都必须精确落在冻结读数记录上；用独立插值辅助
        # 从读数重算，而不是拿端口自己的返回值当期望值。
        for point in result.raw_points:
            frequency = _frequency(point.frequency_ghz)
            expected_raw, interpolated = self._expected_driver_reading(
                frequency.ghz, point.source_power_dbm
            )
            self.assertFalse(interpolated, "严格模式不得发生插值")
            expected_output = compensate_driver_output_power(
                PowerDbm(expected_raw), frequency, table, attenuator
            ).dbm
            self.assertAlmostEqual(
                point.driver_output_power_dbm,
                expected_output,
                delta=1e-9,
                msg=f"{frequency.key} GHz {point.source_power_dbm} dBm raw 重算不一致",
            )
            self.assertAlmostEqual(
                point.gain_db,
                point.driver_output_power_dbm - point.source_power_dbm,
                delta=1e-9,
            )

        self.assertEqual(len(raw_per_frequency), len(frequencies))
        self.assertEqual(len(target_per_frequency), len(frequencies))
        for frequency in frequencies:
            key = frequency.key
            self.assertEqual(len(raw_per_frequency[key]), 33)
            self.assertEqual(len(target_per_frequency[key]), 31)
            self.assertEqual(
                {point.target_input_power_dbm for point in target_per_frequency[key]},
                set(packaged_by_frequency[key]),
                f"{key} GHz 目标网格与打包结果不一致",
            )
            for point in target_per_frequency[key]:
                # 每个目标来自独立实测命中点；命中容差是 ±0.2 dB。
                self.assertLessEqual(
                    abs(point.driver_output_power_dbm - point.target_input_power_dbm),
                    DRIVER_MAPPING_HIT_TOLERANCE_DB + 1e-9,
                )
                packaged_source, packaged_output = packaged_by_frequency[key][
                    point.target_input_power_dbm
                ]
                # 历史兼容容差独立命名：打包曲线来自旧搜索，不能借用业务命中
                # 容差来掩盖目标表漂移。
                self.assertLessEqual(
                    abs(point.driver_output_power_dbm - packaged_output),
                    PACKAGED_TARGET_DRIFT_TOLERANCE_DB + 1e-9,
                    f"{key} GHz 目标 {point.target_input_power_dbm} dBm 与打包结果偏差过大",
                )
                self.assertLessEqual(
                    abs(point.source_power_dbm - packaged_source),
                    PACKAGED_TARGET_DRIFT_TOLERANCE_DB + 1e-9,
                )

        # 实时事件来自真实服务：每频点 33 条累计曲线，逐条必须等于结果 raw 前缀。
        self.assertEqual(len(events.data), 363)
        by_frequency = {}
        for payload in events.data:
            by_frequency.setdefault(payload["frequency"], []).append(payload)
        for frequency in frequencies:
            records = by_frequency[frequency.ghz]
            self.assertEqual(len(records), 33)
            self.assertEqual(
                [len(item["sweep_data"]["input_power_sg"]) for item in records],
                list(range(1, 34)),
            )
            expected = raw_per_frequency[frequency.key]
            for index, payload in enumerate(records, start=1):
                prefix = expected[:index]
                sweep = payload["sweep_data"]
                self.assertEqual(
                    sweep["input_power_sg"],
                    [point.source_power_dbm for point in prefix],
                )
                self.assertEqual(
                    sweep["output_power_driver"],
                    [point.driver_output_power_dbm for point in prefix],
                )
                self.assertEqual(
                    sweep["gain"],
                    [point.gain_db for point in prefix],
                )
                self.assertEqual(
                    payload["current_point"],
                    {
                        "input_power": prefix[-1].source_power_dbm,
                        "output_power": prefix[-1].driver_output_power_dbm,
                    },
                )
        progress_events = self._scenario_events(
            MeasurementStage.DRIVER_MAPPING, "MockProgressEvent"
        )
        self.assertEqual(
            events.progress, [event.percent for event in progress_events]
        )
        self.assertEqual(events.progress[-1], 100)
        self.assertEqual(
            self._manifest(prepared).stage(STAGE_DRIVER_MAPPING).status,
            StageStatus.COMPLETED,
        )
        self._assert_real_directories_untouched(prepared.run_id)


class AmplifierSimulationChainTests(SimulationChainTestCase):
    """步骤 13：主功放用例在严格模式下逐点对照。"""

    def test_amplifier_strict_replay_matches_packaged_metrics_and_archives(self):
        prepared = self.new_run()
        cable_loss_path = self._seed_packaged_stage(MeasurementStage.CABLE_LOSS)
        self._complete_seeded_stage(
            prepared, MeasurementStage.CABLE_LOSS, cable_loss_path
        )
        driver_mapping_path = self._seed_packaged_stage(
            MeasurementStage.DRIVER_MAPPING
        )
        self._complete_seeded_stage(
            prepared, MeasurementStage.DRIVER_MAPPING, driver_mapping_path
        )
        events = _EventRecorder()
        port = self._start_port(MeasurementType.AMPLIFIER, strict=True)
        case = self._amplifier_case(
            prepared,
            port,
            cable_loss_path,
            driver_mapping_path,
            events,
        )
        result = case.measure_all_frequencies()
        interpolation_counts = port.interpolation_counts
        port.end_stage()

        self.assertEqual(interpolation_counts, {}, "严格模式不得发生插值")
        packaged_path, packaged = self._packaged_result(MeasurementStage.AMPLIFIER)
        self.assertTrue(packaged_path.is_file())
        packaged_by_frequency = {
            item.frequency_ghz: item for item in packaged.frequencies
        }
        self.assertEqual(len(result.frequencies), 11)
        for item in result.frequencies:
            expected = packaged_by_frequency[item.frequency_ghz]
            point_count = len(item.sweep_data["sg_power"])
            self.assertGreaterEqual(point_count, 27)
            self.assertLessEqual(point_count, 29)
            self.assertEqual(
                item.compression_type, expected.compression_type
            )
            self.assertEqual(
                item.compression_achieved, expected.compression_achieved
            )
            self.assertAlmostEqual(
                item.small_signal_gain_db,
                expected.small_signal_gain_db,
                delta=1e-9,
            )
            for name in (
                "input_power_dbm",
                "output_power_dbm",
                "gain_db",
                "efficiency_percent",
                "compression_db",
                "signal_generator_power_dbm",
            ):
                self.assertAlmostEqual(
                    getattr(item.compression_point, name),
                    getattr(expected.compression_point, name),
                    delta=0.01 if name == "efficiency_percent" else 1e-9,
                    msg=f"{item.frequency_ghz} GHz compression.{name}",
                )
            # 不只比较点数：逐列核对扫描数据、V/I 与派生列。打包文档把效率
            # 保存到两位小数，dc_power 也受 V/I 量化影响，因此按文档精度对照。
            self.assertEqual(set(item.sweep_data), set(expected.sweep_data))
            for column, wanted_values in expected.sweep_data.items():
                actual_values = item.sweep_data[column]
                self.assertEqual(
                    len(actual_values),
                    len(wanted_values),
                    f"{item.frequency_ghz} GHz {column} 点数不一致",
                )
                delta = (
                    PACKAGED_EFFICIENCY_TOLERANCE
                    if column == "efficiency"
                    else (
                        PACKAGED_DC_POWER_TOLERANCE
                        if column == "dc_power"
                        else 1e-9
                    )
                )
                for index, (actual, wanted) in enumerate(
                    zip(actual_values, wanted_values)
                ):
                    if isinstance(wanted, Mapping):
                        self.assert_mapping_close(
                            actual,
                            wanted,
                            delta=delta,
                            label=(
                                f"{item.frequency_ghz} GHz {column}[{index}]"
                            ),
                        )
                    else:
                        self.assertAlmostEqual(
                            actual,
                            wanted,
                            delta=delta,
                            msg=f"{item.frequency_ghz} GHz {column}[{index}]",
                        )

        references = case.last_stage_artifact_references
        self.assertEqual(
            {reference.kind.value for reference in references}, {"csv", "report"}
        )
        for reference in references:
            self.assertTrue(reference.path.is_file())
            self._assert_under_simulation_root(reference.path)
            self.assertTrue(
                reference.path.resolve().is_relative_to(
                    self.paths.amplifier_results_dir.resolve()
                ),
                f"{reference.path} 不在仿真主功放结果目录",
            )

        # 实时数据逐条必须是结果扫描列的前缀；最后一条覆盖整个频点。
        self.assertEqual(len(events.data), 312)
        data_by_frequency = {}
        for payload in events.data:
            data_by_frequency.setdefault(payload["frequency"], []).append(payload)
        for item in result.frequencies:
            records = data_by_frequency[float(item.frequency_ghz)]
            self.assertEqual(
                len(records), len(item.sweep_data["sg_power"])
            )
            for index, payload in enumerate(records, start=1):
                self.assertEqual(payload["frequency"], float(item.frequency_ghz))
                sweep = payload["sweep_data"]
                self.assertEqual(set(sweep), set(item.sweep_data))
                for column, values in item.sweep_data.items():
                    self.assert_mapping_close(
                        sweep[column][:index],
                        list(values[:index]),
                        delta=1e-9,
                        label=f"{item.frequency_ghz} GHz 事件 {column}",
                    )
                last = {column: values[index - 1] for column, values in item.sweep_data.items()}
                self.assert_mapping_close(
                    payload["current_point"],
                    {
                        "sg_power": last["sg_power"],
                        "dut_input_power": last["input_power_dut"],
                        "output_power": last["output_power_dut"],
                        "gain": last["gain"],
                        "efficiency": last["efficiency"],
                        "dc_power": last["dc_power"],
                    },
                    delta=1e-9,
                    label=f"{item.frequency_ghz} GHz 事件 current_point",
                )

        # 主功放进度按整次测量推进：跨频点的整体 0～100，不在每个频点重新开始。
        planned = len(
            self._scenario(
                MeasurementStage.AMPLIFIER
            ).source_config.target_input_powers_dbm
        )
        frequencies = list(result.frequencies)
        total = len(frequencies)
        expected_progress = []
        for frequency_index, item in enumerate(frequencies):
            measured = len(item.sweep_data["sg_power"])
            for index in range(measured):
                fraction = (
                    frequency_index + (index + 1) / planned
                ) / total
                expected_progress.append(int(fraction * 100))
            if measured < planned:
                # 压缩提前结束：服务补发该频点的结尾进度，下个频点从正确起点继续。
                expected_progress.append(
                    int((frequency_index + 1) / total * 100)
                )
        self.assertEqual(events.progress, expected_progress)
        self.assertEqual(max(events.progress), 100)
        manifest = self._manifest(prepared)
        self.assertEqual(manifest.status, RunStatus.COMPLETED)
        self.assertEqual(
            manifest.stage(STAGE_AMPLIFIER).status, StageStatus.COMPLETED
        )
        self._assert_real_directories_untouched(prepared.run_id)


class FullSimulationChainTests(SimulationChainTestCase):
    """步骤 14：线损 → 驱动映射 → 主功放连续运行与重算。"""

    def test_full_chain_uses_new_calibration_and_recomputes_from_raw(self):
        prepared = self.new_run()
        port = self._start_port(
            MeasurementType.CABLE_LOSS, all_stages=True, strict=False
        )
        cable_case = self._cable_loss_case(prepared, port)
        cable_case.measure_all_frequencies()
        cable_result = cable_case.continue_to_step2()
        port.end_stage()
        cable_loss_path = self._stage_result(cable_result)
        self._assert_under_simulation_root(cable_loss_path)

        self._activate(port, MeasurementType.DRIVER_POWER_MAPPING)
        driver_case = self._driver_mapping_case(
            prepared, port, cable_loss_path
        )
        driver_result = driver_case.measure_all_frequencies()
        driver_interpolations = port.interpolation_counts
        port.end_stage()
        driver_mapping_path = self._stage_result(driver_result)
        self._assert_under_simulation_root(driver_mapping_path)

        self._activate(port, MeasurementType.AMPLIFIER)
        amplifier_case = self._amplifier_case(
            prepared, port, cable_loss_path, driver_mapping_path
        )
        amplifier_result = amplifier_case.measure_all_frequencies()
        amplifier_interpolations = port.interpolation_counts
        port.end_stage()

        manifest = self._manifest(prepared)
        self.assertEqual(manifest.status, RunStatus.COMPLETED)
        for stage in (STAGE_CABLE_LOSS, STAGE_DRIVER_MAPPING, STAGE_AMPLIFIER):
            self.assertEqual(manifest.stage(stage).status, StageStatus.COMPLETED)
        self._assert_real_directories_untouched(prepared.run_id)

        # 全链路用本次新产生的线损结果，而不是打包结果。
        table, attenuator = self._cable_loss_table(cable_result)
        self.assertEqual(len(cable_result.points), 11)

        # 驱动映射：raw 不变；用冻结读数独立重算每个实测点，而不是再让端口
        # 回答一次（否则期望值与实际值来自同一个实现，无法证明端口语义）。
        driver_powers_by_frequency = {}
        for point in driver_result.raw_points:
            frequency = _frequency(point.frequency_ghz)
            expected_raw, _interpolated = self._expected_driver_reading(
                frequency.ghz, point.source_power_dbm
            )
            compensated = compensate_driver_output_power(
                PowerDbm(expected_raw), frequency, table, attenuator
            ).dbm
            self.assertAlmostEqual(
                compensated,
                point.driver_output_power_dbm,
                delta=1e-9,
                msg=f"{frequency.key} GHz 驱动映射 raw 重算不一致",
            )
            driver_powers_by_frequency.setdefault(
                frequency.ghz, []
            ).append(point.source_power_dbm)
        self.assertEqual(
            dict(driver_interpolations),
            self._expected_interpolation_counts(
                MeasurementType.DRIVER_POWER_MAPPING,
                driver_powers_by_frequency,
            ),
            "驱动映射插值次数与“请求点是否命中记录点”的独立计算不一致",
        )

        # 目标网格逐点取自实测命中点，不允许外推或凭空生成。
        raw_index = {
            (
                point.frequency_ghz,
                round(point.source_power_dbm, 9),
            ): point
            for point in driver_result.raw_points
        }
        target_by_frequency = {}
        for point in driver_result.target_points:
            target_by_frequency.setdefault(point.frequency_ghz, set()).add(
                point.target_input_power_dbm
            )
            key = (
                point.frequency_ghz,
                round(point.source_power_dbm, 9),
            )
            self.assertIn(key, raw_index)
            self.assertAlmostEqual(
                raw_index[key].driver_output_power_dbm,
                point.driver_output_power_dbm,
                delta=1e-9,
            )
        self.assertEqual(
            {key: len(values) for key, values in target_by_frequency.items()},
            {row.frequency_ghz: 31 for row in cable_result.points},
        )

        # 主功放：用同一份 raw + 本次线损校准独立重算输出、V/I 与压缩点。
        amplifier_powers_by_frequency = {}
        for item in amplifier_result.frequencies:
            frequency = _frequency(item.frequency_ghz)
            rows = item.sweep_rows()
            for row in rows:
                expected_raw, expected_voltages, expected_currents, _ = (
                    self._expected_amplifier_reading(
                        frequency.ghz, row["sg_power"]
                    )
                )
                compensated = compensate_amplifier_output_power(
                    PowerDbm(expected_raw), frequency, table, attenuator
                ).dbm
                self.assertAlmostEqual(
                    compensated,
                    row["output_power_dut"],
                    delta=1e-9,
                    msg=f"{frequency.key} GHz 主功放 raw 重算不一致",
                )
                for channel_name, voltage in row.items():
                    if not channel_name.startswith("V_"):
                        continue
                    key = channel_name[2:]
                    self.assertIn(key, expected_voltages)
                    self.assertAlmostEqual(
                        voltage,
                        expected_voltages[key],
                        delta=1e-9,
                        msg=f"{frequency.key} GHz 主功放电压 {key} 重算不一致",
                    )
                for channel_name, current in row.items():
                    if not channel_name.startswith("I_"):
                        continue
                    key = channel_name[2:]
                    self.assertIn(key, expected_currents)
                    self.assertAlmostEqual(
                        current,
                        expected_currents[key],
                        delta=1e-9,
                        msg=f"{frequency.key} GHz 主功放电流 {key} 重算不一致",
                    )
                amplifier_powers_by_frequency.setdefault(
                    frequency.ghz, []
                ).append(row["sg_power"])

            compression = calculate_compression_result(
                [Decibels(row["gain"]) for row in rows],
                [PowerDbm(row["input_power_dut"]) for row in rows],
                [PowerDbm(row["output_power_dut"]) for row in rows],
                [Percent(row["efficiency"]) for row in rows],
                [PowerDbm(row["sg_power"]) for row in rows],
                Decibels(5.0),
            )
            point = item.compression_point
            self.assertAlmostEqual(
                point.input_power_dbm,
                compression.compression_point.input_power.dbm,
                delta=1e-9,
            )
            self.assertAlmostEqual(
                point.output_power_dbm,
                compression.compression_point.output_power.dbm,
                delta=1e-9,
            )
            self.assertAlmostEqual(
                point.gain_db,
                compression.compression_point.gain.db,
                delta=1e-9,
            )
            self.assertAlmostEqual(
                point.compression_db,
                compression.compression_point.compression.db,
                delta=1e-9,
            )
            self.assertAlmostEqual(
                item.small_signal_gain_db,
                compression.small_signal_gain.db,
                delta=1e-9,
            )
            self.assertEqual(
                item.compression_achieved,
                compression.compression_achieved,
            )

        # 插值次数由端口按频点记录；用“请求点是否命中记录点”的独立计算复核。
        # 端口本身禁止外推，任何越界请求都会让上面的用例直接失败。
        self.assertEqual(
            dict(amplifier_interpolations),
            self._expected_interpolation_counts(
                MeasurementType.AMPLIFIER, amplifier_powers_by_frequency
            ),
            "主功放插值次数与独立计算不一致",
        )


if __name__ == "__main__":
    unittest.main()
