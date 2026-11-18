"""仿真运行原始读数包的纯 Python 契约：``mock_data/<stage>/readings.json``。

本模块定义仿真运行唯一的读数来源形状。仿真运行只替换
:class:`instrument.ports.MeasurementInstrumentPort` 的读数方法，因此这里描述的是
“仪器会返回什么原始值”，不是界面事件、不是补偿后的结果，也不是运行记录：

- 线损保存**有序测量序列**，顺序与 ``CableLossService.run()`` 完全一致
  （路径 1 全频点 → 路径 2 全频点），端口按顺序消费；
- 驱动映射与主功放按频点保存**按功率键查找**的记录点（源功率 / 信号源设定功率
  严格递增、不得重复），端口按数值查找，区间内允许插值、禁止外推；
- 主功放每个记录点同时保存 ``voltages`` / ``currents``，同一频点内各点的通道集合
  必须完全一致，端口把电压/电流读取绑定到最近一次成功消费的测量点。

``source`` 是生成事实（场景与打包校准结果的文件名 + SHA-256），只用于审计与
完整性核对；它**不是**“本次选中校准结果”的摘要。``readings.json`` 自身的摘要由
读取入口计算后写入运行标记，不写在文件里。

严格约束：

- ``kind`` 只能是 ``simulation_readings``，``schema_version`` 只接受 ``1``；未知版本、
  未知字段、非有限数、空点集一律拒绝，不做静默降级；
- ``measurement_type`` 必须与该文件所在阶段一致，并与运行时激活的阶段一致；
- ``payload`` 形状由 ``measurement_type`` 分派，三者不能混写；
- 频率键统一复用 :meth:`domain.measurement.values.Frequency.from_document_key`
  的规范化规则，按数值匹配时容差 ``1e-9 GHz``，不按原始字符串匹配。

模块只做结构与数值校验：不读文件、不判断路径边界、不接触端口。文件读取入口与
运行期冻结由后续的仿真运行装配负责。
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar, Union

from application.dto.measurement_results import MeasurementType
from application.errors import ConfigurationError
from domain.measurement.values import Frequency


SIMULATION_READINGS_KIND = "simulation_readings"
SIMULATION_READINGS_SCHEMA_VERSION = 1
SIMULATION_READINGS_SCHEMA_VERSIONS = (SIMULATION_READINGS_SCHEMA_VERSION,)
#: ``source.scenario`` 只能是阶段目录内固定的场景文件名。
SIMULATION_READINGS_SCENARIO_FILENAME = "scenario.json"
#: 频率按数值匹配时的容差（GHz）；不按原始字符串匹配。
SIMULATION_FREQUENCY_TOLERANCE_GHZ = 1e-9

_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_TOP_LEVEL_FIELDS = (
    "schema_version",
    "kind",
    "measurement_type",
    "source",
    "payload",
)
_SOURCE_FIELDS = (
    "scenario",
    "scenario_sha256",
    "cable_loss_result",
    "cable_loss_sha256",
    "driver_mapping_result",
    "driver_mapping_sha256",
)
# Windows 保留设备名：纯文件名校验与 Mock 场景保持一致（即使带扩展名也会被系统
# 解析成设备），路径边界仍由 MockDataPathService 在读取前再确认一次。
_RESERVED_WINDOWS_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


class SimulationReadingsError(ConfigurationError):
    """``readings.json`` 不满足仿真读数契约。

    归入配置类异常（同时保留 :class:`ValueError`），使进入仿真运行前的读取失败与
    Mock 场景/配置校验使用同一错误类别，调用方不需要靠字符串匹配判断原因。
    """


def _type_name(value: Any) -> str:
    return type(value).__name__


def _require_mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SimulationReadingsError(
            f"{description} 必须是 JSON 对象，收到 {_type_name(value)}"
        )
    return value


def _require_object(
    value: Any,
    *,
    description: str,
    required: Sequence[str],
    optional: Sequence[str] = (),
) -> Mapping[str, Any]:
    """校验 JSON 对象：必需字段齐全，且不接受未声明字段。"""
    document = _require_mapping(value, description)
    missing = [field for field in required if field not in document]
    if missing:
        raise SimulationReadingsError(
            f"{description} 缺少必需字段: {', '.join(missing)}"
        )
    allowed = set(required) | set(optional)
    unknown = sorted(key for key in document if key not in allowed)
    if unknown:
        raise SimulationReadingsError(
            f"{description} 包含未知字段: {', '.join(unknown)}；"
            f"允许字段: {', '.join(sorted(allowed))}"
        )
    return document


def _require_sequence(value: Any, description: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(
        value, (list, tuple)
    ):
        raise SimulationReadingsError(
            f"{description} 必须是数组，收到 {_type_name(value)}"
        )
    return value


def _require_text(
    value: Any, description: str, *, allow_empty: bool = False
) -> str:
    if not isinstance(value, str):
        raise SimulationReadingsError(
            f"{description} 必须是字符串，收到 {_type_name(value)}"
        )
    if not allow_empty and not value.strip():
        raise SimulationReadingsError(f"{description} 不能为空")
    return value


def _require_identifier(value: Any, description: str) -> str:
    text = _require_text(value, description)
    if text != text.strip():
        raise SimulationReadingsError(
            f"{description} 不能包含首尾空白: {value!r}"
        )
    return text


def _require_plain_file_name(value: Any, description: str) -> str:
    """要求纯文件名：拒绝路径分隔、盘符、相对目录标记和 Windows 设备名。"""
    text = _require_identifier(value, description)
    if text in (".", ".."):
        raise SimulationReadingsError(
            f"{description} 不能是相对目录标记: {text!r}"
        )
    for separator in ("/", "\\", ":", "\0"):
        if separator in text:
            raise SimulationReadingsError(
                f"{description} 必须是纯文件名，不能包含路径分隔符或盘符: {text!r}"
            )
    if text.endswith("."):
        raise SimulationReadingsError(f"{description} 不能以点结尾: {text!r}")
    if text.split(".", 1)[0].upper() in _RESERVED_WINDOWS_NAMES:
        raise SimulationReadingsError(
            f"{description} 不能是 Windows 保留设备名: {text!r}"
        )
    return text


def _require_sha256(value: Any, description: str) -> str:
    text = _require_text(value, description)
    if not _SHA256_PATTERN.fullmatch(text):
        raise SimulationReadingsError(
            f"{description} 必须是 64 位小写十六进制 SHA-256，收到 {value!r}"
        )
    return text


def _require_number(
    value: Any,
    description: str,
    *,
    exclusive_minimum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SimulationReadingsError(
            f"{description} 必须是数字，收到 {_type_name(value)}"
        )
    try:
        number = float(value)
    except (OverflowError, ValueError) as error:
        raise SimulationReadingsError(f"{description} 必须是有限数字") from error
    if not math.isfinite(number):
        raise SimulationReadingsError(f"{description} 必须是有限数字")
    if exclusive_minimum is not None and number <= exclusive_minimum:
        raise SimulationReadingsError(
            f"{description} 必须大于 {exclusive_minimum}，收到 {number}"
        )
    return number


def _require_int(
    value: Any, description: str, *, minimum: int | None = None
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SimulationReadingsError(
            f"{description} 必须是整数，收到 {_type_name(value)}"
        )
    if minimum is not None and value < minimum:
        raise SimulationReadingsError(
            f"{description} 不能小于 {minimum}，收到 {value}"
        )
    return value


def _coerce_measurement_type(
    value: Any, description: str = "measurement_type"
) -> MeasurementType:
    if isinstance(value, MeasurementType):
        return value
    text = _require_identifier(value, description)
    try:
        return MeasurementType(text)
    except ValueError as error:
        allowed = "、".join(kind.value for kind in MeasurementType)
        raise SimulationReadingsError(
            f"{description} 非法: {value!r}；可用测量类型: {allowed}"
        ) from error


def _coerce_frequency(value: Any, description: str) -> Frequency:
    """把文档频率（键或字段）解析为 :class:`Frequency`，错误信息带字段名。"""
    try:
        frequency = Frequency.from_document_key(value)
    except (TypeError, ValueError) as error:
        raise SimulationReadingsError(
            f"{description} 不是有效频率: {value!r}（{error}）"
        ) from error
    if frequency.ghz <= 0:
        raise SimulationReadingsError(
            f"{description} 必须是正频率，收到 {value!r}"
        )
    return frequency


def _same_frequency(left: float, right: float) -> bool:
    return abs(left - right) <= SIMULATION_FREQUENCY_TOLERANCE_GHZ


def _require_strictly_increasing(
    values: Sequence[float], description: str
) -> None:
    previous: float | None = None
    for index, value in enumerate(values):
        if previous is not None and value <= previous:
            raise SimulationReadingsError(
                f"{description}[{index}] 必须严格递增且不得重复: "
                f"{previous} -> {value}"
            )
        previous = value


def _require_channel_map(value: Any, description: str) -> Mapping[str, float]:
    document = _require_mapping(value, description)
    if not document:
        raise SimulationReadingsError(f"{description} 不能为空对象")
    channels: dict[str, float] = {}
    for key, item in document.items():
        name = _require_identifier(key, f"{description} 的通道键")
        channels[name] = _require_number(item, f"{description}.{name}")
    return MappingProxyType(channels)


@dataclass(frozen=True)
class SimulationReadingsSource:
    """读数包的上游事实：场景与打包校准结果的文件名和 SHA-256。

    每个 ``*_result`` 与对应 ``*_sha256`` 必须同时给出或同时为 ``null``；文件名是
    阶段目录内的纯文件名，运行时由 ``MockDataPathService`` 在对应阶段目录解析。
    """

    scenario: str
    scenario_sha256: str
    cable_loss_result: str | None = None
    cable_loss_sha256: str | None = None
    driver_mapping_result: str | None = None
    driver_mapping_sha256: str | None = None

    def __post_init__(self) -> None:
        scenario = _require_plain_file_name(self.scenario, "source.scenario")
        if scenario != SIMULATION_READINGS_SCENARIO_FILENAME:
            raise SimulationReadingsError(
                "source.scenario 必须是阶段目录内的 "
                f"{SIMULATION_READINGS_SCENARIO_FILENAME!r}，收到 {scenario!r}"
            )
        object.__setattr__(self, "scenario", scenario)
        object.__setattr__(
            self,
            "scenario_sha256",
            _require_sha256(self.scenario_sha256, "source.scenario_sha256"),
        )
        for label in ("cable_loss", "driver_mapping"):
            filename = getattr(self, f"{label}_result")
            digest = getattr(self, f"{label}_sha256")
            if filename is None and digest is None:
                continue
            if filename is None or digest is None:
                raise SimulationReadingsError(
                    f"source.{label}_result 与 source.{label}_sha256 必须"
                    "同时给出或同时为 null"
                )
            object.__setattr__(
                self,
                f"{label}_result",
                _require_plain_file_name(filename, f"source.{label}_result"),
            )
            object.__setattr__(
                self,
                f"{label}_sha256",
                _require_sha256(digest, f"source.{label}_sha256"),
            )


@dataclass(frozen=True)
class SimulationCableLossMeasurement:
    """线损有序序列中的一项：一个频点、一条路径的原始读数（dBm）。"""

    frequency_ghz: float
    path: int
    raw_dbm: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "frequency_ghz",
            _require_number(
                self.frequency_ghz,
                "payload.measurements.frequency_ghz",
                exclusive_minimum=0.0,
            ),
        )
        path = _require_int(self.path, "payload.measurements.path")
        if path not in (1, 2):
            raise SimulationReadingsError(
                f"payload.measurements.path 只能是 1 或 2，收到 {path}"
            )
        object.__setattr__(self, "path", path)
        object.__setattr__(
            self, "raw_dbm", _require_number(self.raw_dbm, "payload.measurements.raw_dbm")
        )


def _require_measurement_points(
    values: Sequence[Any],
) -> tuple[SimulationCableLossMeasurement, ...]:
    measurements: list[SimulationCableLossMeasurement] = []
    for index, item in enumerate(values):
        description = f"payload.measurements[{index}]"
        if isinstance(item, SimulationCableLossMeasurement):
            measurements.append(item)
            continue
        document = _require_object(
            item,
            description=description,
            required=("frequency_ghz", "path", "raw_dbm"),
        )
        try:
            measurements.append(
                SimulationCableLossMeasurement(
                    frequency_ghz=document["frequency_ghz"],
                    path=document["path"],
                    raw_dbm=document["raw_dbm"],
                )
            )
        except SimulationReadingsError as error:
            raise SimulationReadingsError(f"{description}: {error}") from error
    return tuple(measurements)


@dataclass(frozen=True)
class SimulationCableLossPayload:
    """线损读数：与 ``CableLossService.run()`` 完全一致的有序测量序列。"""

    measurements: tuple[SimulationCableLossMeasurement, ...]

    def __post_init__(self) -> None:
        document = _require_sequence(
            self.measurements, "payload.measurements"
        )
        if not document:
            raise SimulationReadingsError("payload.measurements 不能为空数组")
        measurements = _require_measurement_points(document)

        path1 = [item for item in measurements if item.path == 1]
        path2 = [item for item in measurements if item.path == 2]
        if tuple(measurements) != tuple(path1 + path2):
            raise SimulationReadingsError(
                "payload.measurements 必须先是路径 1 的全部频点，再是路径 2 的全部"
                "频点；不允许交错或从路径 2 回到路径 1"
            )
        if not path1 or not path2:
            raise SimulationReadingsError(
                "payload.measurements 必须同时覆盖路径 1 与路径 2，"
                f"当前路径 1 有 {len(path1)} 项、路径 2 有 {len(path2)} 项"
            )
        _require_strictly_increasing(
            [item.frequency_ghz for item in path1],
            "payload.measurements（路径 1）",
        )
        _require_strictly_increasing(
            [item.frequency_ghz for item in path2],
            "payload.measurements（路径 2）",
        )
        if len(path1) != len(path2):
            raise SimulationReadingsError(
                "路径 1 与路径 2 的频点数量必须一致: "
                f"{len(path1)} != {len(path2)}"
            )
        for index, (first, second) in enumerate(zip(path1, path2)):
            if not _same_frequency(first.frequency_ghz, second.frequency_ghz):
                raise SimulationReadingsError(
                    "路径 1 与路径 2 的频点必须逐点一致: "
                    f"第 {index} 项 {first.frequency_ghz} != {second.frequency_ghz}"
                )
        object.__setattr__(self, "measurements", measurements)

    def path_frequencies(self, path: int) -> tuple[Frequency, ...]:
        """返回某条路径的频点（按文件顺序，即扫描顺序）。"""
        return tuple(
            Frequency.from_ghz(item.frequency_ghz)
            for item in self.measurements
            if item.path == path
        )


@dataclass(frozen=True)
class SimulationDriverProbe:
    """驱动映射的一个记录点：源功率 -> 频谱仪原始读数（dBm）。"""

    source_power_dbm: float
    raw_output_dbm: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_power_dbm",
            _require_number(
                self.source_power_dbm, "payload.frequencies.probes.source_power_dbm"
            ),
        )
        object.__setattr__(
            self,
            "raw_output_dbm",
            _require_number(
                self.raw_output_dbm, "payload.frequencies.probes.raw_output_dbm"
            ),
        )


@dataclass(frozen=True)
class SimulationDriverFrequency:
    """单个频点的驱动映射记录点；源功率严格递增、不得重复。"""

    probes: tuple[SimulationDriverProbe, ...]

    def __post_init__(self) -> None:
        document = _require_sequence(self.probes, "payload.frequencies.probes")
        if not document:
            raise SimulationReadingsError("payload.frequencies.probes 不能为空数组")
        probes: list[SimulationDriverProbe] = []
        for index, item in enumerate(document):
            description = f"payload.frequencies.probes[{index}]"
            if isinstance(item, SimulationDriverProbe):
                probes.append(item)
                continue
            fields = _require_object(
                item,
                description=description,
                required=("source_power_dbm", "raw_output_dbm"),
            )
            try:
                probes.append(
                    SimulationDriverProbe(
                        source_power_dbm=fields["source_power_dbm"],
                        raw_output_dbm=fields["raw_output_dbm"],
                    )
                )
            except SimulationReadingsError as error:
                raise SimulationReadingsError(f"{description}: {error}") from error
        _require_strictly_increasing(
            [probe.source_power_dbm for probe in probes],
            "payload.frequencies.probes 的 source_power_dbm",
        )
        object.__setattr__(self, "probes", tuple(probes))


@dataclass(frozen=True)
class SimulationDriverMappingPayload:
    """驱动映射读数：按频点保存源功率 -> 原始读数的记录点。"""

    frequencies: Mapping[Frequency, SimulationDriverFrequency]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "frequencies",
            _require_frequency_mapping(
                self.frequencies,
                description="payload.frequencies",
                expected_type=SimulationDriverFrequency,
            ),
        )


@dataclass(frozen=True)
class SimulationAmplifierPoint:
    """主功放的一个扫描点：信号源设定功率、原始读数与逐通道 V/I。"""

    sg_power_dbm: float
    raw_output_dbm: float
    voltages: Mapping[str, float]
    currents: Mapping[str, float]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "sg_power_dbm",
            _require_number(
                self.sg_power_dbm, "payload.frequencies.points.sg_power_dbm"
            ),
        )
        object.__setattr__(
            self,
            "raw_output_dbm",
            _require_number(
                self.raw_output_dbm, "payload.frequencies.points.raw_output_dbm"
            ),
        )
        voltages = _require_channel_map(
            self.voltages, "payload.frequencies.points.voltages"
        )
        currents = _require_channel_map(
            self.currents, "payload.frequencies.points.currents"
        )
        if set(voltages) != set(currents):
            missing = sorted(set(voltages) - set(currents))
            extra = sorted(set(currents) - set(voltages))
            details = []
            if missing:
                details.append(f"currents 缺少 {', '.join(missing)}")
            if extra:
                details.append(f"currents 多出 {', '.join(extra)}")
            raise SimulationReadingsError(
                "payload.frequencies.points 的 voltages 与 currents 通道集合必须"
                "一致: " + "；".join(details)
            )
        object.__setattr__(self, "voltages", voltages)
        object.__setattr__(self, "currents", currents)


@dataclass(frozen=True)
class SimulationAmplifierFrequency:
    """单个频点的主功放扫描点；同一频点内通道集合必须完全一致。"""

    points: tuple[SimulationAmplifierPoint, ...]

    def __post_init__(self) -> None:
        document = _require_sequence(self.points, "payload.frequencies.points")
        if not document:
            raise SimulationReadingsError("payload.frequencies.points 不能为空数组")
        points: list[SimulationAmplifierPoint] = []
        for index, item in enumerate(document):
            description = f"payload.frequencies.points[{index}]"
            if isinstance(item, SimulationAmplifierPoint):
                points.append(item)
                continue
            fields = _require_object(
                item,
                description=description,
                required=(
                    "sg_power_dbm",
                    "raw_output_dbm",
                    "voltages",
                    "currents",
                ),
            )
            try:
                points.append(
                    SimulationAmplifierPoint(
                        sg_power_dbm=fields["sg_power_dbm"],
                        raw_output_dbm=fields["raw_output_dbm"],
                        voltages=fields["voltages"],
                        currents=fields["currents"],
                    )
                )
            except SimulationReadingsError as error:
                raise SimulationReadingsError(f"{description}: {error}") from error
        _require_strictly_increasing(
            [point.sg_power_dbm for point in points],
            "payload.frequencies.points 的 sg_power_dbm",
        )
        reference_channels = set(points[0].voltages)
        for index, point in enumerate(points):
            if set(point.voltages) != reference_channels:
                missing = sorted(reference_channels - set(point.voltages))
                extra = sorted(set(point.voltages) - reference_channels)
                details = []
                if missing:
                    details.append(f"缺少 {', '.join(missing)}")
                if extra:
                    details.append(f"多出 {', '.join(extra)}")
                raise SimulationReadingsError(
                    "payload.frequencies.points 同一频点内各点的通道集合必须完全"
                    f"一致: 第 {index} 点（" + "；".join(details) + "）"
                )
        object.__setattr__(self, "points", tuple(points))


@dataclass(frozen=True)
class SimulationAmplifierPayload:
    """主功放读数：按频点保存扫描点与逐通道 V/I。"""

    frequencies: Mapping[Frequency, SimulationAmplifierFrequency]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "frequencies",
            _require_frequency_mapping(
                self.frequencies,
                description="payload.frequencies",
                expected_type=SimulationAmplifierFrequency,
            ),
        )


SimulationReadingsPayload = Union[
    SimulationCableLossPayload,
    SimulationDriverMappingPayload,
    SimulationAmplifierPayload,
]

_PAYLOAD_TYPES: Mapping[MeasurementType, type] = MappingProxyType(
    {
        MeasurementType.CABLE_LOSS: SimulationCableLossPayload,
        MeasurementType.DRIVER_POWER_MAPPING: SimulationDriverMappingPayload,
        MeasurementType.AMPLIFIER: SimulationAmplifierPayload,
    }
)


def _require_frequency_mapping(
    value: Any,
    *,
    description: str,
    expected_type: type,
) -> Mapping[Frequency, Any]:
    """把 ``{频率键: 载荷}`` 规范化为按 :class:`Frequency` 索引的只读映射。"""
    document = _require_mapping(value, description)
    if not document:
        raise SimulationReadingsError(f"{description} 不能为空对象")
    normalized: dict[Frequency, Any] = {}
    seen: list[tuple[Frequency, Any]] = []
    for key, item in document.items():
        frequency = _coerce_frequency(key, f"{description} 的键")
        # 端口按数值在 1e-9 GHz 容差内匹配频率：两个键相距不超过容差就是同一频率，
        # 否则同一查找会命中两份读数。文本不同（``1.0`` / ``1.00``）与浮点噪声
        # （``1.0`` / ``1.0000000005``）都按重复处理。
        for existing, existing_key in seen:
            if not _same_frequency(frequency.ghz, existing.ghz):
                continue
            if frequency == existing:
                raise SimulationReadingsError(
                    f"{description} 含重复频率 {frequency.key}（键 {key!r}）"
                )
            raise SimulationReadingsError(
                f"{description} 含容差内重复频率 {existing.key}（键 {existing_key!r}）"
                f" 与 {frequency.key}（键 {key!r}）：两者相差不超过 "
                f"{SIMULATION_FREQUENCY_TOLERANCE_GHZ:g} GHz，端口无法区分"
            )
        if not isinstance(item, expected_type):
            raise SimulationReadingsError(
                f"{description}[{key!r}] 必须是 {expected_type.__name__}，"
                f"收到 {_type_name(item)}"
            )
        normalized[frequency] = item
        seen.append((frequency, key))
    return MappingProxyType(normalized)


def _parse_source(value: Any) -> SimulationReadingsSource:
    document = _require_object(
        value,
        description="source",
        required=_SOURCE_FIELDS,
    )
    return SimulationReadingsSource(
        scenario=document["scenario"],
        scenario_sha256=document["scenario_sha256"],
        cable_loss_result=document["cable_loss_result"],
        cable_loss_sha256=document["cable_loss_sha256"],
        driver_mapping_result=document["driver_mapping_result"],
        driver_mapping_sha256=document["driver_mapping_sha256"],
    )


def _parse_payload(
    value: Any, measurement_type: MeasurementType
) -> SimulationReadingsPayload:
    if measurement_type is MeasurementType.CABLE_LOSS:
        document = _require_object(
            value,
            description="payload",
            required=("measurements",),
        )
        return SimulationCableLossPayload(measurements=document["measurements"])
    if measurement_type is MeasurementType.DRIVER_POWER_MAPPING:
        document = _require_object(
            value,
            description="payload",
            required=("frequencies",),
        )
        buckets = _require_mapping(document["frequencies"], "payload.frequencies")
        driver_frequencies: dict[Any, SimulationDriverFrequency] = {}
        for key, item in buckets.items():
            bucket = _require_object(
                item,
                description=f"payload.frequencies[{key!r}]",
                required=("probes",),
            )
            driver_frequencies[key] = SimulationDriverFrequency(
                probes=bucket["probes"]
            )
        return SimulationDriverMappingPayload(frequencies=driver_frequencies)
    document = _require_object(
        value,
        description="payload",
        required=("frequencies",),
    )
    buckets = _require_mapping(document["frequencies"], "payload.frequencies")
    amplifier_frequencies: dict[Any, SimulationAmplifierFrequency] = {}
    for key, item in buckets.items():
        bucket = _require_object(
            item,
            description=f"payload.frequencies[{key!r}]",
            required=("points",),
        )
        amplifier_frequencies[key] = SimulationAmplifierFrequency(
            points=bucket["points"]
        )
    return SimulationAmplifierPayload(frequencies=amplifier_frequencies)


@dataclass(frozen=True)
class SimulationReadings:
    """一份通过校验的仿真读数包；运行时作为不可变对象随运行上下文冻结。"""

    kind: ClassVar[str] = SIMULATION_READINGS_KIND

    schema_version: int
    measurement_type: MeasurementType | str
    source: SimulationReadingsSource
    payload: SimulationReadingsPayload

    def __post_init__(self) -> None:
        version = _require_int(self.schema_version, "schema_version")
        if version not in SIMULATION_READINGS_SCHEMA_VERSIONS:
            supported = "、".join(
                str(item) for item in SIMULATION_READINGS_SCHEMA_VERSIONS
            )
            raise SimulationReadingsError(
                f"不支持的仿真读数 schema_version: {version!r}；当前支持: {supported}"
            )
        object.__setattr__(self, "schema_version", version)
        measurement_type = _coerce_measurement_type(self.measurement_type)
        object.__setattr__(self, "measurement_type", measurement_type)
        if not isinstance(self.source, SimulationReadingsSource):
            raise SimulationReadingsError(
                "仿真读数的 source 必须是 SimulationReadingsSource，"
                f"收到 {_type_name(self.source)}"
            )
        expected_payload = _PAYLOAD_TYPES[measurement_type]
        if not isinstance(self.payload, expected_payload):
            raise SimulationReadingsError(
                f"{measurement_type.value} 的 payload 必须是 "
                f"{expected_payload.__name__}，收到 {_type_name(self.payload)}"
            )
        self._require_source_matches_measurement_type()

    def _require_source_matches_measurement_type(self) -> None:
        """上游引用必须与该阶段实际消费的打包结果一致。"""
        source = self.source
        measurement_type = self.measurement_type
        if measurement_type is MeasurementType.CABLE_LOSS:
            if (
                source.cable_loss_result is not None
                or source.driver_mapping_result is not None
            ):
                raise SimulationReadingsError(
                    "线损读数没有上游校准结果，source.cable_loss_result 与"
                    " source.driver_mapping_result 必须为 null"
                )
            return
        if source.cable_loss_result is None:
            raise SimulationReadingsError(
                f"{measurement_type.value} 读数必须引用生成它的打包线损结果"
                "（source.cable_loss_result）"
            )
        if measurement_type is MeasurementType.DRIVER_POWER_MAPPING:
            if source.driver_mapping_result is not None:
                raise SimulationReadingsError(
                    "驱动映射读数的上游只有线损，source.driver_mapping_result"
                    " 必须为 null"
                )
            return
        # 主功放：驱动模式记录打包驱动映射摘要，驱动关闭时为 null；两者都合法，
        # “是否必须给出”由运行装配按冻结配置判断。

    @property
    def frequency_grid(self) -> tuple[Frequency, ...]:
        """读数覆盖的频点，按 GHz 升序；线损取路径 1 的扫描顺序。"""
        payload = self.payload
        if isinstance(payload, SimulationCableLossPayload):
            return payload.path_frequencies(1)
        return tuple(sorted(payload.frequencies))

    @classmethod
    def from_document(
        cls,
        document: Any,
        *,
        expected_measurement_type: MeasurementType | str | None = None,
        expected_frequencies_ghz: Sequence[Any] | None = None,
    ) -> "SimulationReadings":
        """严格解析一份已解析的 JSON 文档并返回 :class:`SimulationReadings`。

        ``expected_measurement_type`` 由调用方给出该文件应当服务的阶段；
        ``expected_frequencies_ghz`` 是冻结配置的测试频率，必须与读数覆盖的频点
        逐点一致（多一个或少一个都失败）。
        """
        root = _require_object(
            document,
            description="仿真读数包",
            required=_TOP_LEVEL_FIELDS,
        )
        kind = _require_identifier(root["kind"], "kind")
        if kind != SIMULATION_READINGS_KIND:
            raise SimulationReadingsError(
                f"仿真读数的 kind 必须是 {SIMULATION_READINGS_KIND!r}，收到 {kind!r}"
            )
        measurement_type = _coerce_measurement_type(root["measurement_type"])
        readings = cls(
            schema_version=root["schema_version"],
            measurement_type=measurement_type,
            source=_parse_source(root["source"]),
            payload=_parse_payload(root["payload"], measurement_type),
        )
        if expected_measurement_type is not None:
            expected = _coerce_measurement_type(
                expected_measurement_type, "期望的仿真测量类型"
            )
            if readings.measurement_type is not expected:
                raise SimulationReadingsError(
                    "仿真读数的测量类型与目标阶段不一致: "
                    f"{readings.measurement_type.value} != {expected.value}"
                )
        if expected_frequencies_ghz is not None:
            _require_matching_grid(
                readings.frequency_grid, expected_frequencies_ghz
            )
        return readings


def _coerce_expected_frequencies(value: Sequence[Any]) -> tuple[Frequency, ...]:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(
        value, (list, tuple)
    ):
        raise SimulationReadingsError(
            "期望的测试频率必须是数组，收到 " + _type_name(value)
        )
    frequencies = tuple(
        Frequency.from_ghz(
            _require_number(
                item, f"期望的测试频率[{index}]", exclusive_minimum=0.0
            )
        )
        for index, item in enumerate(value)
    )
    if not frequencies:
        raise SimulationReadingsError("期望的测试频率不能为空数组")
    return frequencies


def _require_matching_grid(
    actual: tuple[Frequency, ...], expected: Sequence[Any]
) -> None:
    expected_frequencies = _coerce_expected_frequencies(expected)
    if len(actual) != len(expected_frequencies):
        raise SimulationReadingsError(
            "仿真读数的频点数与冻结配置不一致: "
            f"{len(actual)} != {len(expected_frequencies)}；"
            "多一个或少一个都失败，请重新生成 readings.json"
        )
    for index, (actual_frequency, expected_frequency) in enumerate(
        zip(actual, expected_frequencies)
    ):
        if not _same_frequency(actual_frequency.ghz, expected_frequency.ghz):
            raise SimulationReadingsError(
                "仿真读数的频点与冻结配置不一致: "
                f"第 {index} 项 {actual_frequency.key} != "
                f"{expected_frequency.key}；请重新生成 readings.json"
            )


def simulation_readings_from_document(
    document: Any,
    *,
    expected_measurement_type: MeasurementType | str | None = None,
    expected_frequencies_ghz: Sequence[Any] | None = None,
) -> SimulationReadings:
    """模块级解析入口；与 :meth:`SimulationReadings.from_document` 等价。"""
    return SimulationReadings.from_document(
        document,
        expected_measurement_type=expected_measurement_type,
        expected_frequencies_ghz=expected_frequencies_ghz,
    )


__all__ = [
    "SIMULATION_FREQUENCY_TOLERANCE_GHZ",
    "SIMULATION_READINGS_KIND",
    "SIMULATION_READINGS_SCENARIO_FILENAME",
    "SIMULATION_READINGS_SCHEMA_VERSION",
    "SIMULATION_READINGS_SCHEMA_VERSIONS",
    "SimulationAmplifierFrequency",
    "SimulationAmplifierPayload",
    "SimulationAmplifierPoint",
    "SimulationCableLossMeasurement",
    "SimulationCableLossPayload",
    "SimulationDriverFrequency",
    "SimulationDriverMappingPayload",
    "SimulationDriverProbe",
    "SimulationReadings",
    "SimulationReadingsError",
    "SimulationReadingsPayload",
    "SimulationReadingsSource",
    "simulation_readings_from_document",
]
