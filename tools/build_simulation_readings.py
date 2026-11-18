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


#: 生成文件的缩进；``--check`` 与写入必须使用同一格式，才能逐字节比较。
READINGS_INDENT = 2
#: 往返断言的容差（dB）。
ROUND_TRIP_TOLERANCE_DB = 1e-9
#: 原始读数保留的小数位；比往返容差更细，既去掉浮点尾巴又保证往返误差 < 1e-9。
READINGS_DECIMALS = 9
#: 与场景记录比较时的容差（dB / dBm）。
SCENARIO_TOLERANCE = 1e-9


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


def _driver_payload(
    scenario: MockScenario,
    result: Any,
    *,
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
    return {"frequencies": payload}


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
        payload = _driver_payload(scenario, result, mock_paths=mock_paths)
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
