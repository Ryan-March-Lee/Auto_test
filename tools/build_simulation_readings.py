r"""从 Mock 场景与打包正式结果反算原始读数，生成 ``mock_data/<stage>/readings.json``。

仿真运行只替换 :class:`instrument.ports.MeasurementInstrumentPort` 的读数方法，所以
这里生成的是**频谱仪会返回的原始 dBm**，不是补偿后的结果：

```text
线损      raw_dbm = -total_path（服务执行 abs(0 - value)）
驱动映射  raw     = driver_output_power - (attenuator + cable2)
主功放    raw     = output_power_dut   - (cable4 + attenuator + cable2)
```

反算只从真实补偿函数取“这条路径的损耗”，不在本文件复制补偿公式；每一点都再正向
调用 ``compensate_driver_output_power`` / ``compensate_amplifier_output_power``
做往返断言，误差必须小于 ``1e-9 dB``。数据由命令生成而非手写，``--check`` 只生成到
内存并逐字节比对，因此场景或正式结果一变化就会被发现。

驱动映射还多做一步“按当前自适应搜索重放”：打包结果的原始点来自更早的等步进生成，
当前搜索会在相同记录区间内请求一些 4～6 位小数的功率点（其中上界多要 0.1~1 mdb）。
严格模式只接受记录点，因此生成器用冻结读数重放一次真实 ``DriverPowerMappingService``，
把搜索实际请求的点补进 ``readings.json``。补点的原始输出来自读数曲线在相邻记录点之间
的插值（仅上界补点用最后一段斜率外推不超过 ``REPLAY_TOP_MARGIN_DB``），不改变正式
结果的期望值；生成后还会用严格模式再重放一次，确认每频点探测数与目标网格与打包结果
一致。

```powershell
# 重建三份读数包
& "D:\Anaconda\envs\Auto_test\python.exe" tools/build_simulation_readings.py

# 只校验仓库文件是否仍可由数据重建
& "D:\Anaconda\envs\Auto_test\python.exe" tools/build_simulation_readings.py --check
```

参数 ``--stage`` 可以只处理一个阶段（可重复）。退出码：0 表示一致/写入成功，1 表示
数据漂移或读写失败。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    # 直接以 ``python tools/build_simulation_readings.py`` 运行时，仓库根不在 sys.path 上。
    sys.path.insert(0, str(PROJECT_ROOT))

from application.dto.measurement_results import MeasurementType  # noqa: E402
from application.dto.mock_scenario import (  # noqa: E402
    MockDataEvent,
    MockScenario,
)
from application.dto.simulation_readings import (  # noqa: E402
    SIMULATION_READINGS_KIND,
    SIMULATION_READINGS_SCENARIO_FILENAME,
    SIMULATION_READINGS_SCHEMA_VERSION,
    SimulationReadings,
)
from application.errors import ConfigurationError  # noqa: E402
from application.inputs.mock_result import load_mock_result  # noqa: E402
from application.inputs.mock_scenario import load_mock_scenario  # noqa: E402
from application.measurements.services import DriverPowerMappingService  # noqa: E402
from application.ports.stage_result_repository import (  # noqa: E402
    measurement_type_for_stage,
)
from domain.measurement.calculations import (  # noqa: E402
    compensate_amplifier_output_power,
    compensate_driver_output_power,
)
from domain.measurement.values import (  # noqa: E402
    CableLossTable,
    Decibels,
    Frequency,
    PowerDbm,
)
from infrastructure.filesystem.atomic_json import write_json_atomic  # noqa: E402
from infrastructure.filesystem.paths import (  # noqa: E402
    DEFAULT_MOCK_DATA_PATHS,
    MockDataPathService,
    MockStage,
    coerce_mock_stage,
)
from instrument.flow.cleanup import SafetyCleanupAdapter  # noqa: E402
from instrument.simulation_port import (  # noqa: E402
    SIMULATION_POWER_COMMAND_FORMAT,
    SimulationRunPort,
)


#: 生成文件的缩进；``--check`` 与写入必须使用同一格式，才能逐字节比较。
READINGS_INDENT = 2
#: 往返断言的容差（dB）。
ROUND_TRIP_TOLERANCE_DB = 1e-9
#: 原始读数保留的小数位；比往返容差更细，既去掉浮点尾巴又保证往返误差 < 1e-9。
READINGS_DECIMALS = 9
#: 与场景记录比较时的容差（dB / dBm）。
SCENARIO_TOLERANCE = 1e-9
#: 驱动映射补点用于覆盖搜索上界时允许外推的最大距离（dB）。
REPLAY_TOP_MARGIN_DB = 0.01
#: 主功放读数在打包扫描范围两端额外覆盖的余量（dB）。全链路使用本次新产生的
#: 驱动映射目标表，其源功率与打包目标表最多相差一个命中容差；两端各留 0.1 dB
#: 保证搜索/目标网格落在记录区间内，运行期仍然禁止外推。
AMPLIFIER_COVERAGE_MARGIN_DB = 0.1


def _raw_reading(value: float) -> float:
    """收敛反算出的原始读数，去掉 ``-29.881 - 40.921`` 这类浮点尾巴。"""
    return round(float(value), READINGS_DECIMALS)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _require_close(
    actual: float, expected: float, *, label: str, tolerance: float = ROUND_TRIP_TOLERANCE_DB
) -> None:
    if abs(actual - expected) > tolerance:
        raise ConfigurationError(
            f"{label} 往返不一致: {actual!r} != {expected!r}"
            f"（容差 {tolerance:g}；请重新生成 readings.json）"
        )


def _scenario_events(scenario: MockScenario) -> tuple[MockDataEvent, ...]:
    return tuple(
        event
        for phase in scenario.phases
        for event in phase.events
        if isinstance(event, MockDataEvent)
    )


def _latest_sweeps(scenario: MockScenario) -> dict[float, Any]:
    """每个频点最后一个 data 事件携带的累计曲线。"""
    sweeps: dict[float, Any] = {}
    for event in _scenario_events(scenario):
        sweep = event.payload.get("sweep_data")
        if sweep is not None:
            sweeps[float(event.payload["frequency"])] = sweep
    return sweeps


def _scenario_cable_totals(
    scenario: MockScenario,
) -> dict[tuple[int, float], float]:
    """场景记录的路径总损耗：``{(path, frequency): loss_db}``。"""
    totals: dict[tuple[int, float], float] = {}
    for event in _scenario_events(scenario):
        payload = event.payload
        path = payload.get("path")
        frequency = payload.get("frequency")
        if path == 1 and "path1_loss" in payload:
            totals[(1, float(frequency))] = float(payload["path1_loss"])
        elif path == 2 and "path2_loss" in payload:
            totals[(2, float(frequency))] = float(payload["path2_loss"])
    return totals


def _configured_frequencies(scenario: MockScenario) -> tuple[Frequency, ...]:
    return tuple(
        Frequency.from_ghz(value)
        for value in scenario.source_config.test_frequencies_ghz
    )


def _cable_loss_totals(result: Any) -> dict[Frequency, tuple[float, float]]:
    totals: dict[Frequency, tuple[float, float]] = {}
    for row in result.points:
        frequency = Frequency.from_ghz(float(row.frequency_ghz))
        profile = row.cable_losses
        if profile.total_path1 is None or profile.total_path2 is None:
            raise ConfigurationError(
                f"打包线损结果 {row.frequency_ghz} GHz 缺少 total_path1/total_path2"
            )
        totals[frequency] = (profile.total_path1.db, profile.total_path2.db)
    return totals


def _loss_inputs(
    scenario: MockScenario, *, mock_paths: MockDataPathService
) -> tuple[CableLossTable, Decibels]:
    """读取场景引用的打包线损结果与衰减器，构造补偿输入。"""
    reference = scenario.source_config.cable_loss_result
    if reference is None:
        raise ConfigurationError(
            f"{scenario.measurement_type.value} 场景缺少打包线损结果引用"
            "（source_config.cable_loss_result）"
        )
    path = mock_paths.require_reference_path(reference)
    result = load_mock_result(
        path, expected_measurement_type=MeasurementType.CABLE_LOSS
    )
    table = CableLossTable(
        {
            Frequency.from_ghz(float(row.frequency_ghz)): row.cable_losses
            for row in result.points
        }
    )
    attenuator = Decibels(float(scenario.source_config.attenuator_db))
    if result.attenuator_value is None:
        raise ConfigurationError(f"打包线损结果 {path.name} 缺少 attenuator_value")
    _require_close(
        float(result.attenuator_value),
        attenuator.db,
        label=f"打包线损结果 {path.name} 的衰减器",
        tolerance=SCENARIO_TOLERANCE,
    )
    return table, attenuator


def _total_loss(power_loss: PowerDbm) -> float:
    """把“0 dBm 补偿后的结果”读出这条路径的总损耗（dB）。"""
    return power_loss.dbm


def _cable_loss_payload(
    scenario: MockScenario, result: Any
) -> dict[str, Any]:
    frequencies = _configured_frequencies(scenario)
    totals = _cable_loss_totals(result)
    if set(totals) != set(frequencies):
        missing = sorted(item.key for item in set(frequencies) - set(totals))
        extra = sorted(item.key for item in set(totals) - set(frequencies))
        raise ConfigurationError(
            "打包线损结果的频点与场景配置不一致: "
            f"缺少 {missing or '无'}、多出 {extra or '无'}"
        )
    scenario_totals = _scenario_cable_totals(scenario)
    for frequency in frequencies:
        for path, index in ((1, 0), (2, 1)):
            expected = scenario_totals.get((path, frequency.ghz))
            if expected is None:
                raise ConfigurationError(
                    f"场景缺少 {frequency.key} GHz 路径 {path} 的总损耗记录"
                )
            _require_close(
                totals[frequency][index],
                expected,
                label=f"{frequency.key} GHz 路径 {path} 的正式结果总损耗",
                tolerance=SCENARIO_TOLERANCE,
            )
    # 顺序固定为 CableLossService.run() 的真实顺序：路径 1 全频点 → 路径 2 全频点。
    measurements: list[dict[str, Any]] = []
    for path, index in ((1, 0), (2, 1)):
        for frequency in frequencies:
            total = totals[frequency][index]
            raw = _raw_reading(-total)
            _require_close(
                abs(0.0 - raw),
                total,
                label=f"{frequency.key} GHz 路径 {path} 的线损读数",
            )
            measurements.append(
                {
                    "frequency_ghz": frequency.ghz,
                    "path": path,
                    "raw_dbm": raw,
                }
            )
    return {"measurements": measurements}


def _driver_service_config(scenario: MockScenario) -> dict[str, Any]:
    """把 Mock 场景的源配置收敛为驱动映射服务契约。"""
    source = scenario.source_config
    return {
        "test_frequencies": list(source.test_frequencies_ghz),
        "attenuator": {"type": f"{source.attenuator_db}dB"},
        "signal_source": {
            "power_scan_plans": [
                {
                    "start_power": plan.start_power_dbm,
                    "step": plan.step_db,
                    "stop_power": plan.stop_power_dbm,
                }
                for plan in source.power_scan_plans
            ]
        },
        "max_dut_input_power": source.dut_max_input_power_dbm,
        "driver_gain_estimate_db": source.driver_estimated_gain_db,
        "driver_max_input_power": source.driver_max_input_power_dbm,
    }


def _driver_document(
    payload: dict[str, Any],
    scenario: MockScenario,
    *,
    scenario_path: Path,
    mock_paths: MockDataPathService,
) -> dict[str, Any]:
    """把驱动映射 payload 包装成运行期契约能接受的读数文档。"""
    return {
        "schema_version": SIMULATION_READINGS_SCHEMA_VERSION,
        "kind": SIMULATION_READINGS_KIND,
        "measurement_type": MeasurementType.DRIVER_POWER_MAPPING.value,
        "source": _source_document(
            MockStage.DRIVER_MAPPING,
            scenario,
            scenario_path=scenario_path,
            mock_paths=mock_paths,
        ),
        "payload": payload,
    }


class _RecordingSimulationPort(SimulationRunPort):
    """离线生成器使用的端口：把每次读数请求与返回的原始值记录下来。

    生成器需要“当前搜索实际会请求哪些功率点”。端口本身不暴露请求历史，因此在工具
    侧包一层只读记录；控制方法与读数语义仍完全走 ``SimulationRunPort``，不会触达
    真实设备。
    """

    def __init__(self, readings: SimulationReadings) -> None:
        super().__init__(readings)
        self.requests: dict[str, dict[float, float]] = {}
        self._request_frequency_ghz: float | None = None
        self._request_power_dbm: float | None = None

    def set_frequency(self, frequency: float) -> None:
        """记录生成器看到的频率请求，不依赖端口内部状态。"""
        super().set_frequency(frequency)
        self._request_frequency_ghz = float(frequency)

    def set_power(self, power: float) -> None:
        """记录端口实际查找的量化功率命令值，不读取端口私有字段。"""
        super().set_power(power)
        self._request_power_dbm = float(
            f"{float(power):{SIMULATION_POWER_COMMAND_FORMAT}}"
        )

    def measure_power_with_average(self) -> float:
        frequency_ghz = self._request_frequency_ghz
        power_dbm = self._request_power_dbm
        raw = super().measure_power_with_average()
        if frequency_ghz is None or power_dbm is None:
            raise ConfigurationError(
                "驱动映射重放在设置频率/功率之前发起了读数请求"
            )
        key = Frequency.from_ghz(frequency_ghz).key
        self.requests.setdefault(key, {})[power_dbm] = raw
        return raw


def _extended_driver_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """在每条频点曲线上端补一个余量点，供搜索上界外推使用。

    当前自适应搜索可能向记录上界外多要 0.1~1 mdb 的点；运行期端口禁止外推，因此
    生成器先把曲线延长 ``REPLAY_TOP_MARGIN_DB``（按最后一段斜率线性外推），重放后
    只保留搜索真正请求的点。延长点不写入最终文件，除非搜索确实请求了它。
    """
    extended: dict[str, Any] = {}
    for key, bucket in payload["frequencies"].items():
        probes = [dict(item) for item in bucket["probes"]]
        if len(probes) < 2:
            raise ConfigurationError(
                f"驱动映射 {key} GHz 的读数少于两个记录点，无法补上界余量"
            )
        first, last = probes[-2], probes[-1]
        span = last["source_power_dbm"] - first["source_power_dbm"]
        if span <= 0:
            raise ConfigurationError(
                f"驱动映射 {key} GHz 的最后两个记录点功率没有递增"
            )
        slope = (
            last["raw_output_dbm"] - first["raw_output_dbm"]
        ) / span
        probes.append(
            {
                "source_power_dbm": _raw_reading(
                    last["source_power_dbm"] + REPLAY_TOP_MARGIN_DB
                ),
                "raw_output_dbm": _raw_reading(
                    last["raw_output_dbm"] + slope * REPLAY_TOP_MARGIN_DB
                ),
            }
        )
        extended[key] = {"probes": probes}
    return {"frequencies": extended}


def _run_driver_replay(
    *,
    payload: dict[str, Any],
    scenario: MockScenario,
    scenario_path: Path,
    mock_paths: MockDataPathService,
    strict: bool,
) -> tuple[dict[str, dict[float, float]], dict[str, Any]]:
    """用冻结读数重放一次真实驱动映射搜索。

    返回 ``(请求点, 服务结果)``；``strict=True`` 时端口只接受记录点，用于生成后的
    自检。任何失败都翻译为配置类错误，提示重新生成 ``readings.json``。
    """
    table, _attenuator = _loss_inputs(scenario, mock_paths=mock_paths)
    # 第一次重放允许读取“上界余量点”，让搜索能越过原记录上界；严格自检直接使用
    # 已补点的正式 payload，不再额外延长曲线。
    replay_payload = payload if strict else _extended_driver_payload(payload)
    document = _driver_document(
        replay_payload,
        scenario,
        scenario_path=scenario_path,
        mock_paths=mock_paths,
    )
    try:
        readings = SimulationReadings.from_document(document)
        port = _RecordingSimulationPort(readings)
        port.strict = strict
        port.begin_stage()
        port.activate(MeasurementType.DRIVER_POWER_MAPPING)
        service = DriverPowerMappingService(
            _driver_service_config(scenario),
            port,
            {"cable_losses": table.to_document()},
            run_id="simulation-readings-build",
            sleep_fn=lambda _seconds: None,
            settle_delay_s=0.0,
            safety_cleanup=SafetyCleanupAdapter(port),
            owns_measurement_port=False,
        )
        result = service.run()
    except ConfigurationError:
        raise
    except Exception as error:
        raise ConfigurationError(
            "无法按当前自适应搜索重放驱动映射读数："
            f"{error}；请重新生成 readings.json"
        ) from error
    return port.requests, result


def _augmented_driver_payload(
    payload: dict[str, Any],
    requests: dict[str, dict[float, float]],
) -> dict[str, Any]:
    """把搜索请求但尚未记录的功率点补进对应频点，按功率严格递增。"""
    augmented: dict[str, Any] = {}
    for key, bucket in payload["frequencies"].items():
        values = {
            round(item["source_power_dbm"], 9): item["raw_output_dbm"]
            for item in bucket["probes"]
        }
        for power, raw in requests.get(key, {}).items():
            rounded = round(power, 9)
            if any(abs(rounded - existing) <= 1e-9 for existing in values):
                continue
            values[rounded] = _raw_reading(raw)
        augmented[key] = {
            "probes": [
                {"source_power_dbm": power, "raw_output_dbm": values[power]}
                for power in sorted(values)
            ]
        }
    return {"frequencies": augmented}


def _assert_driver_replay_matches_packaged(
    *,
    payload: dict[str, Any],
    scenario: MockScenario,
    scenario_path: Path,
    mock_paths: MockDataPathService,
    expected_probe_counts: dict[str, int],
) -> None:
    """严格模式下重放补点后的读数，逐频点确认探测数与目标网格仍与打包结果一致。

    总数量相等不能发现“某频点少点、另一频点多点”，因此这里按频率比较，并确认
    目标表里的每个源功率都能在补点后的读数中找到记录（严格模式已经保证端口只会
    返回记录点，这里再守住生成物的数据形状）。
    """
    _requests, result = _run_driver_replay(
        payload=payload,
        scenario=scenario,
        scenario_path=scenario_path,
        mock_paths=mock_paths,
        strict=True,
    )
    expected_frequencies = {
        Frequency.from_ghz(value).key
        for value in scenario.source_config.test_frequencies_ghz
    }
    raw_points = result["raw_points"]
    target_mapping = result["target_mapping"]
    if set(raw_points) != expected_frequencies or set(target_mapping) != expected_frequencies:
        raise ConfigurationError(
            "驱动映射重放的频点与场景配置不一致："
            f"raw={sorted(raw_points)}、target={sorted(target_mapping)}、"
            f"期望={sorted(expected_frequencies)}；请重新生成 readings.json"
        )
    expected_targets = tuple(scenario.source_config.target_input_powers_dbm)
    for key in sorted(expected_frequencies):
        actual_count = len(raw_points[key])
        expected_count = expected_probe_counts.get(key)
        if expected_count is None or actual_count != expected_count:
            raise ConfigurationError(
                f"驱动映射重放 {key} GHz 的探测数与打包正式结果不一致："
                f"{actual_count} != {expected_count}；请重新生成 readings.json"
            )
        actual_targets = tuple(
            row["target_input_power_dbm"] for row in target_mapping[key]
        )
        if actual_targets != expected_targets:
            raise ConfigurationError(
                f"驱动映射重放 {key} GHz 的目标网格与场景配置不一致："
                f"{actual_targets} != {expected_targets}；请重新生成 readings.json"
            )
        recorded_sources = {
            round(probe["source_power_dbm"], 9)
            for probe in payload["frequencies"][key]["probes"]
        }
        for row in target_mapping[key]:
            # 服务记录的是未量化的源功率，端口按信号源实际下发的 6 位有效数字
            # 查找记录；对照前先复现同一条命令量化。
            source = round(
                float(
                    f"{row['source_power_dbm']:{SIMULATION_POWER_COMMAND_FORMAT}}"
                ),
                9,
            )
            if not any(
                abs(source - existing) <= 1e-9 for existing in recorded_sources
            ):
                raise ConfigurationError(
                    f"驱动映射重放 {key} GHz 的目标源功率 {source} dBm "
                    "没有对应的读数记录；请重新生成 readings.json"
                )


def _driver_payload(
    scenario: MockScenario,
    result: Any,
    *,
    scenario_path: Path,
    mock_paths: MockDataPathService,
) -> dict[str, Any]:
    table, attenuator = _loss_inputs(scenario, mock_paths=mock_paths)
    frequencies = _configured_frequencies(scenario)
    points_by_frequency: dict[Frequency, list[Any]] = {}
    for point in result.raw_points:
        frequency = Frequency.from_ghz(float(point.frequency_ghz))
        points_by_frequency.setdefault(frequency, []).append(point)
    if set(points_by_frequency) != set(frequencies):
        missing = sorted(item.key for item in set(frequencies) - set(points_by_frequency))
        extra = sorted(item.key for item in set(points_by_frequency) - set(frequencies))
        raise ConfigurationError(
            "打包驱动映射结果的频点与场景配置不一致: "
            f"缺少 {missing or '无'}、多出 {extra or '无'}"
        )
    sweeps = _latest_sweeps(scenario)
    payload: dict[str, Any] = {}
    for frequency in frequencies:
        points = sorted(
            points_by_frequency[frequency], key=lambda item: item.source_power_dbm
        )
        loss = _total_loss(
            compensate_driver_output_power(
                PowerDbm(0.0), frequency, table, attenuator
            )
        )
        probes: list[dict[str, float]] = []
        for point in points:
            raw = _raw_reading(point.driver_output_power_dbm - loss)
            _require_close(
                compensate_driver_output_power(
                    PowerDbm(raw), frequency, table, attenuator
                ).dbm,
                point.driver_output_power_dbm,
                label=(
                    f"{frequency.key} GHz 源功率 "
                    f"{point.source_power_dbm} dBm 的驱动映射读数"
                ),
            )
            probes.append(
                {
                    "source_power_dbm": point.source_power_dbm,
                    "raw_output_dbm": raw,
                }
            )
        sweep = sweeps.get(frequency.ghz)
        if sweep is None:
            raise ConfigurationError(
                f"场景缺少 {frequency.key} GHz 的驱动映射回放数据"
            )
        recorded_sources = [float(value) for value in sweep["input_power_sg"]]
        recorded_outputs = [float(value) for value in sweep["output_power_driver"]]
        if len(recorded_sources) != len(recorded_outputs):
            raise ConfigurationError(
                f"{frequency.key} GHz 场景源功率与补偿后输出数量不一致: "
                f"{len(recorded_sources)} != {len(recorded_outputs)}"
            )
        # 必须成对比较：分别排序两侧会掩盖“某个源功率配错输出”的错误，
        # 因为两组值的集合仍可能相同。
        recorded = sorted(
            zip(recorded_sources, recorded_outputs), key=lambda pair: pair[0]
        )
        expected = [
            (
                float(probe["source_power_dbm"]),
                float(point.driver_output_power_dbm),
            )
            for probe, point in zip(probes, points)
        ]
        if len(recorded) != len(expected):
            raise ConfigurationError(
                f"{frequency.key} GHz 场景记录点数与正式结果不一致: "
                f"{len(recorded)} != {len(expected)}"
            )
        for index, (actual, wanted) in enumerate(zip(recorded, expected)):
            for label, left, right in (
                ("源功率", actual[0], wanted[0]),
                ("补偿后输出", actual[1], wanted[1]),
            ):
                _require_close(
                    left,
                    right,
                    label=f"{frequency.key} GHz 场景{label}[{index}]",
                    tolerance=SCENARIO_TOLERANCE,
                )
        payload[frequency.key] = {"probes": probes}
    baseline = {"frequencies": payload}
    requests, _result = _run_driver_replay(
        payload=baseline,
        scenario=scenario,
        scenario_path=scenario_path,
        mock_paths=mock_paths,
        strict=False,
    )
    augmented = _augmented_driver_payload(baseline, requests)
    _assert_driver_replay_matches_packaged(
        payload=augmented,
        scenario=scenario,
        scenario_path=scenario_path,
        mock_paths=mock_paths,
        expected_probe_counts={
            key: len(bucket["probes"]) for key, bucket in payload.items()
        },
    )
    return augmented


def _amplifier_boundary_point(
    points: list[dict[str, Any]], source_power_dbm: float, *, at_start: bool
) -> dict[str, Any]:
    """按端点两个记录点线性延伸出一个覆盖用的边界点。

    主功放全链路使用本次新生成的驱动映射目标表，其源功率与打包目标表可能相差
    一个命中容差。边界点只用于让端口区间覆盖这些请求，运行时不会外推；读数与
    V/I 都按端点斜率延伸，保持曲线连续。
    """
    if len(points) < 2:
        raise ConfigurationError(
            "主功放读数少于两个记录点，无法补覆盖边界"
        )
    lower, upper = (points[0], points[1]) if at_start else (points[-2], points[-1])
    span = upper["sg_power_dbm"] - lower["sg_power_dbm"]
    if span <= 0:
        raise ConfigurationError("主功放读数的记录点功率没有递增")
    fraction = (source_power_dbm - lower["sg_power_dbm"]) / span

    def extrapolate(left: float, right: float) -> float:
        return left + fraction * (right - left)

    return {
        "sg_power_dbm": _raw_reading(source_power_dbm),
        "raw_output_dbm": _raw_reading(
            extrapolate(lower["raw_output_dbm"], upper["raw_output_dbm"])
        ),
        "voltages": {
            channel: _raw_reading(
                extrapolate(lower["voltages"][channel], upper["voltages"][channel])
            )
            for channel in lower["voltages"]
        },
        "currents": {
            channel: _raw_reading(
                extrapolate(lower["currents"][channel], upper["currents"][channel])
            )
            for channel in lower["currents"]
        },
    }


def _cover_amplifier_range(
    points: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """在打包扫描范围两端各补一个覆盖点，供全链路的新目标表使用。

    全链路使用本次新生成的驱动映射目标表；其源功率与打包目标表最多相差一个命中
    容差。两端各留 ``AMPLIFIER_COVERAGE_MARGIN_DB`` 让端口区间覆盖这些请求，运行期
    仍然禁止外推；边界点按端点斜率延伸，不改变打包记录点本身。
    """
    if len(points) < 2:
        return points
    desired_min = points[0]["sg_power_dbm"] - AMPLIFIER_COVERAGE_MARGIN_DB
    desired_max = points[-1]["sg_power_dbm"] + AMPLIFIER_COVERAGE_MARGIN_DB
    points.insert(
        0, _amplifier_boundary_point(points, desired_min, at_start=True)
    )
    points.append(
        _amplifier_boundary_point(points, desired_max, at_start=False)
    )
    points.sort(key=lambda item: item["sg_power_dbm"])
    return points


def _amplifier_payload(
    scenario: MockScenario,
    result: Any,
    *,
    mock_paths: MockDataPathService,
) -> dict[str, Any]:
    table, attenuator = _loss_inputs(scenario, mock_paths=mock_paths)
    frequencies = _configured_frequencies(scenario)
    results_by_frequency = {
        Frequency.from_ghz(float(item.frequency_ghz)): item
        for item in result.frequencies
    }
    if set(results_by_frequency) != set(frequencies):
        missing = sorted(item.key for item in set(frequencies) - set(results_by_frequency))
        extra = sorted(item.key for item in set(results_by_frequency) - set(frequencies))
        raise ConfigurationError(
            "打包主功放结果的频点与场景配置不一致: "
            f"缺少 {missing or '无'}、多出 {extra or '无'}"
        )
    sweeps = _latest_sweeps(scenario)
    payload: dict[str, Any] = {}
    for frequency in frequencies:
        item = results_by_frequency[frequency]
        sweep = item.sweep_data
        columns = {
            name: list(sweep[name])
            for name in ("sg_power", "output_power_dut", "voltages", "currents")
        }
        lengths = {len(values) for values in columns.values()}
        if len(lengths) != 1:
            raise ConfigurationError(
                f"{frequency.key} GHz 主功放扫描列长度不一致: {sorted(lengths)}"
            )
        loss = _total_loss(
            compensate_amplifier_output_power(
                PowerDbm(0.0), frequency, table, attenuator
            )
        )
        points: list[dict[str, Any]] = []
        point_count = lengths.pop()
        for index in range(point_count):
            raw = _raw_reading(columns["output_power_dut"][index] - loss)
            _require_close(
                compensate_amplifier_output_power(
                    PowerDbm(raw), frequency, table, attenuator
                ).dbm,
                columns["output_power_dut"][index],
                label=(
                    f"{frequency.key} GHz 信号源 "
                    f"{columns['sg_power'][index]} dBm 的主功放读数"
                ),
            )
            points.append(
                {
                    "sg_power_dbm": columns["sg_power"][index],
                    "raw_output_dbm": raw,
                    "voltages": {
                        str(key): float(value)
                        for key, value in columns["voltages"][index].items()
                    },
                    "currents": {
                        str(key): float(value)
                        for key, value in columns["currents"][index].items()
                    },
                }
            )
        recorded = sweeps.get(frequency.ghz)
        if recorded is None:
            raise ConfigurationError(
                f"场景缺少 {frequency.key} GHz 的主功放回放数据"
            )
        for name in ("sg_power", "output_power_dut"):
            actual = [float(value) for value in recorded[name]]
            expected = [float(value) for value in columns[name]]
            if len(actual) != len(expected):
                raise ConfigurationError(
                    f"{frequency.key} GHz 场景 {name} 数量与正式结果不一致: "
                    f"{len(actual)} != {len(expected)}"
                )
            for index, (left, right) in enumerate(zip(actual, expected)):
                _require_close(
                    left,
                    right,
                    label=f"{frequency.key} GHz 场景 {name}[{index}]",
                    tolerance=SCENARIO_TOLERANCE,
                )
        for name in ("voltages", "currents"):
            actual = [dict(value) for value in recorded[name]]
            expected = [dict(value) for value in columns[name]]
            if actual != expected:
                raise ConfigurationError(
                    f"{frequency.key} GHz 场景 {name} 与正式结果不一致"
                )
        points = _cover_amplifier_range(points)
        payload[frequency.key] = {"points": points}
    return {"frequencies": payload}


def _source_document(
    stage: MockStage,
    scenario: MockScenario,
    *,
    scenario_path: Path,
    mock_paths: MockDataPathService,
) -> dict[str, Any]:
    source: dict[str, Any] = {
        "scenario": scenario_path.name,
        "scenario_sha256": _sha256_file(scenario_path),
        "cable_loss_result": None,
        "cable_loss_sha256": None,
        "driver_mapping_result": None,
        "driver_mapping_sha256": None,
    }
    if stage is MockStage.CABLE_LOSS:
        return source
    cable_reference = scenario.source_config.cable_loss_result
    if cable_reference is None:
        raise ConfigurationError(
            f"{stage.value} 场景缺少打包线损结果引用"
        )
    cable_path = mock_paths.require_reference_path(cable_reference)
    source["cable_loss_result"] = cable_path.name
    source["cable_loss_sha256"] = _sha256_file(cable_path)
    if stage is not MockStage.AMPLIFIER:
        return source
    driver_reference = scenario.source_config.driver_mapping_result
    if scenario.source_config.driver_enabled:
        if driver_reference is None:
            raise ConfigurationError(
                "驱动模式的主功放场景必须引用打包驱动映射结果"
            )
        driver_path = mock_paths.require_reference_path(driver_reference)
        load_mock_result(
            driver_path,
            expected_measurement_type=MeasurementType.DRIVER_POWER_MAPPING,
        )
        source["driver_mapping_result"] = driver_path.name
        source["driver_mapping_sha256"] = _sha256_file(driver_path)
    elif driver_reference is not None:
        raise ConfigurationError(
            "驱动关闭的主功放场景不应引用打包驱动映射结果"
        )
    return source


def _build_document(
    stage: MockStage, *, mock_paths: MockDataPathService
) -> dict[str, Any]:
    measurement_type = measurement_type_for_stage(stage.value)
    scenario_path = mock_paths.require_scenario_path(stage)
    scenario = load_mock_scenario(
        scenario_path, expected_measurement_type=measurement_type
    )
    result_path = mock_paths.require_result_path(stage, scenario.result_filename)
    result = load_mock_result(result_path, expected_measurement_type=measurement_type)
    if stage is MockStage.CABLE_LOSS:
        payload = _cable_loss_payload(scenario, result)
    elif stage is MockStage.DRIVER_MAPPING:
        payload = _driver_payload(
            scenario,
            result,
            scenario_path=scenario_path,
            mock_paths=mock_paths,
        )
    else:
        payload = _amplifier_payload(scenario, result, mock_paths=mock_paths)
    document = {
        "schema_version": SIMULATION_READINGS_SCHEMA_VERSION,
        "kind": SIMULATION_READINGS_KIND,
        "measurement_type": measurement_type.value,
        "source": _source_document(
            stage,
            scenario,
            scenario_path=scenario_path,
            mock_paths=mock_paths,
        ),
        "payload": payload,
    }
    # 生成物必须直接被运行时契约接受：schema、字段集合、顺序与频率网格都在这里
    # 复核一次，避免“工具能写、运行期却读不了”。
    SimulationReadings.from_document(
        document,
        expected_measurement_type=measurement_type,
        expected_frequencies_ghz=scenario.source_config.test_frequencies_ghz,
    )
    return document


def build_readings_document(
    stage: MockStage | str, *, mock_paths: MockDataPathService = DEFAULT_MOCK_DATA_PATHS
) -> dict[str, Any]:
    """返回该阶段的 ``readings.json`` 文档；只读数据与内存，不写文件。"""
    target = coerce_mock_stage(stage)
    try:
        return _build_document(target, mock_paths=mock_paths)
    except ConfigurationError:
        raise
    except ValueError as error:
        raise ConfigurationError(
            f"无法从 Mock 数据反算 {target.value} 原始读数: {error}"
        ) from error


def render_readings(document: dict[str, Any]) -> bytes:
    """生成文件的规范字节；写入与 ``--check`` 共用同一序列化格式。"""
    return json.dumps(
        document, indent=READINGS_INDENT, ensure_ascii=False
    ).encode("utf-8")


def _lf_bytes(data: bytes) -> bytes:
    """把工作区换行规范成 LF（git 的 autocrlf 可能把检出写成 CRLF）。

    只规范换行本身：任何数据变化仍然逐字节可见；仓库里的其它 JSON 数据文件同样
    以 CRLF 检出、以 LF 入库，因此比较前统一换行才能让 ``--check`` 与本机/CI
    的检出方式无关。
    """
    return data.replace(b"\r\n", b"\n")


def build_readings_bytes(
    stage: MockStage | str, *, mock_paths: MockDataPathService = DEFAULT_MOCK_DATA_PATHS
) -> bytes:
    return render_readings(
        build_readings_document(stage, mock_paths=mock_paths)
    )


def write_readings(
    stage: MockStage | str, *, mock_paths: MockDataPathService = DEFAULT_MOCK_DATA_PATHS
) -> Path:
    """原子写入 ``mock_data/<stage>/readings.json``，返回目标路径。"""
    target = coerce_mock_stage(stage)
    document = build_readings_document(target, mock_paths=mock_paths)
    return write_json_atomic(
        mock_paths.readings_path(target),
        document,
        indent=READINGS_INDENT,
        ensure_ascii=False,
    )


def check_readings(
    stage: MockStage | str, *, mock_paths: MockDataPathService = DEFAULT_MOCK_DATA_PATHS
) -> tuple[bool, str]:
    """比较仓库文件与内存重建结果，返回 ``(是否一致, 说明)``。"""
    target = coerce_mock_stage(stage)
    path = mock_paths.readings_path(target)
    expected = build_readings_bytes(target, mock_paths=mock_paths)
    if not path.is_file():
        return False, (
            f"{path} 不存在；请运行 tools/build_simulation_readings.py 生成"
        )
    actual = _lf_bytes(path.read_bytes())
    if actual == expected:
        return True, f"{path} 与场景/正式结果一致"
    offset = next(
        (
            index
            for index, (left, right) in enumerate(zip(actual, expected))
            if left != right
        ),
        min(len(actual), len(expected)),
    )
    line = actual[:offset].count(b"\n") + 1
    return False, (
        f"{path} 与重建结果不一致（按 LF 规范化后第 {line} 行、第 {offset} 字节；"
        f"文件 {len(actual)} 字节，重建 {len(expected)} 字节）；"
        "请运行 tools/build_simulation_readings.py 更新"
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    mock_paths: MockDataPathService = DEFAULT_MOCK_DATA_PATHS,
) -> int:
    parser = argparse.ArgumentParser(
        description="从 Mock 场景与正式结果反算原始读数（readings.json）",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="只校验仓库文件能否由数据重建；不一致时退出码为 1",
    )
    parser.add_argument(
        "--stage",
        action="append",
        choices=[stage.value for stage in MockStage],
        default=None,
        help="只处理指定阶段（可重复）；默认处理全部三个阶段",
    )
    arguments = parser.parse_args(argv)
    stages = (
        [coerce_mock_stage(value) for value in arguments.stage]
        if arguments.stage
        else list(MockStage)
    )
    failed = False
    for stage in stages:
        try:
            if arguments.check:
                ok, message = check_readings(stage, mock_paths=mock_paths)
                print(f"{'OK  ' if ok else 'FAIL'} {message}")
                failed = failed or not ok
            else:
                print(
                    f"已写入 {write_readings(stage, mock_paths=mock_paths)}"
                )
        except (ConfigurationError, OSError) as error:
            print(f"FAIL {stage.value}: {error}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
