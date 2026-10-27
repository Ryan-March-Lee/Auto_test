"""三类正式测量结果的唯一应用层契约。

结果模型只描述正式结果：

- 线损按频点保存四条线缆分摊损耗；
- 驱动映射按频点保存“输入功率 -> 输出功率”点；
- 主功放按频点保存压缩点、小信号增益和扫描数据。

结果不继承 ``Mapping``，也不保存服务层的原始载荷。``to_dict()`` 是唯一的结果
文档形状，``measurement_result_from_document()`` 是唯一读取入口：文档必须同时带
``measurement_type``、``status`` 和非空 ``run_id``，且只允许正式字段名。旧字段别名
（``legacy_payload``、``config``、``derived_metrics``、``resource_snapshot`` 等）、
行式 ``sweep_data``、测量类型与数据字段不匹配的文档一律 ``ValueError``，不存在按
字段猜测历史格式的兼容路径。

展示、报告与导出只使用本模块的显式属性（``report_rows()``、``sweep_rows()``、
``saturation_points()``），因此不需要把结果当成 ``Mapping[str, Any]`` 读取。

本模块是**序列化边界**：字段是 JSON/Qt 可直接消费的基本类型，单位由字段名表达。
dBm↔W、%↔比值等换算和 dB 运算只允许发生在
:mod:`domain.measurement.values` / :mod:`domain.measurement.calculations`，
DTO 与展示层只搬运数值，不重新计算；唯一的例外是
:class:`CableLossRow` 的线损拓扑，它保存值对象并在 ``to_mapping()`` 时展开。
读取结果文档时频率键的文本原样保留（``"2.40"`` 不会被改写），但解析走
:meth:`domain.measurement.values.Frequency.from_document_key`。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any

from domain.measurement.values import CableLossProfile, Frequency


class MeasurementType(str, Enum):
    CABLE_LOSS = "cable_loss"
    DRIVER_POWER_MAPPING = "driver_power_mapping"
    AMPLIFIER = "amplifier"


class MeasurementStatus(str, Enum):
    """统一的测量生命周期状态。"""

    IDLE = "idle"
    PREPARING = "preparing"
    RUNNING = "running"
    WAITING_FOR_CONTINUE = "waiting_for_continue"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EMERGENCY_STOPPED = "emergency_stopped"

    @property
    def is_terminal(self) -> bool:
        return self in {
            MeasurementStatus.COMPLETED,
            MeasurementStatus.FAILED,
            MeasurementStatus.CANCELLED,
            MeasurementStatus.EMERGENCY_STOPPED,
        }


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw(item) for item in value]
    if isinstance(value, frozenset):
        return [thaw(item) for item in value]
    return value


def normalize_frequency_key(value: Any) -> str | None:
    """把频率键规范化为 ``float`` 的字符串形式；无效值返回 ``None``。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return str(float(value))
    except (TypeError, ValueError):
        return None


# 结果文件/阶段使用的结果类型标签与正式测量类型的一一对应关系。
# ``amplifier_measurement`` 是运行目录沿用至今的存储标签，语义等同 ``amplifier``。
_RESULT_TYPE_LABELS: Mapping[str, MeasurementType] = MappingProxyType(
    {
        "cable_loss": MeasurementType.CABLE_LOSS,
        "driver_power_mapping": MeasurementType.DRIVER_POWER_MAPPING,
        "amplifier": MeasurementType.AMPLIFIER,
        "amplifier_measurement": MeasurementType.AMPLIFIER,
    }
)


def measurement_type_for_result_type(value: Any) -> MeasurementType | None:
    """把结果类型标签收敛为正式测量类型；无法识别时返回 ``None``。"""
    if isinstance(value, MeasurementType):
        return value
    if not isinstance(value, str):
        return None
    return _RESULT_TYPE_LABELS.get(value.strip())


# -- 结果数据 ---------------------------------------------------------------


@dataclass(frozen=True)
class CableLossRow:
    """单个频点的线缆分摊损耗。

    线损拓扑由 :class:`domain.measurement.values.CableLossProfile` 表达；``cable1``
    等字符串字段名只存在于值对象里。构造时接受正式结果文档映射，并立即收敛为
    值对象，未知字段、非数值或非有限数都会被拒绝。
    """

    frequency_ghz: str
    cable_losses: CableLossProfile = field(default_factory=CableLossProfile)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "frequency_ghz",
            _frequency_key(self.frequency_ghz, "线损结果"),
        )
        losses = self.cable_losses
        if not isinstance(losses, CableLossProfile):
            losses = CableLossProfile.from_mapping(losses, source="cable_losses")
        object.__setattr__(self, "cable_losses", losses)


@dataclass(frozen=True)
class DriverPowerPoint:
    """单个驱动映射点：信号源输入功率到 DUT 补偿后输出功率。"""

    frequency_ghz: str
    input_power_dbm: float
    output_power_dbm: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frequency_ghz", _frequency_key(self.frequency_ghz, "驱动映射结果")
        )
        object.__setattr__(self, "input_power_dbm", _float(self.input_power_dbm, "input_power_dbm"))
        object.__setattr__(self, "output_power_dbm", _float(self.output_power_dbm, "output_power_dbm"))


@dataclass(frozen=True)
class AmplifierCompressionPoint:
    """主功放的压缩点指标，字段缺失表示该指标未计算。

    所有字段都是 dB / dBm / % 的有限数值或 ``None``；构造时统一转换并拒绝非有限值。
    """

    input_power_dbm: float | None = None
    output_power_dbm: float | None = None
    gain_db: float | None = None
    efficiency_percent: float | None = None
    compression_db: float | None = None
    signal_generator_power_dbm: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "input_power_dbm",
            "output_power_dbm",
            "gain_db",
            "efficiency_percent",
            "compression_db",
            "signal_generator_power_dbm",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _float(value, name))


@dataclass(frozen=True)
class AmplifierFrequencyResult:
    """单个频点的主功放结果：压缩点、小信号增益与列式扫描数据。"""

    frequency_ghz: str
    compression_type: str | None = None
    compression_point: AmplifierCompressionPoint | None = None
    small_signal_gain_db: float | None = None
    compression_achieved: bool | None = None
    sweep_data: Mapping[str, tuple[Any, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "frequency_ghz", _frequency_key(self.frequency_ghz, "主功放结果")
        )
        if self.compression_point is not None and not isinstance(
            self.compression_point, AmplifierCompressionPoint
        ):
            raise ValueError("compression_point 必须是 AmplifierCompressionPoint")
        if self.small_signal_gain_db is not None:
            object.__setattr__(
                self,
                "small_signal_gain_db",
                _float(self.small_signal_gain_db, "small_signal_gain_db"),
            )
        if self.compression_achieved is not None and not isinstance(
            self.compression_achieved, bool
        ):
            raise ValueError("compression_achieved 必须是布尔值")
        object.__setattr__(self, "sweep_data", _columns(self.sweep_data, "sweep_data"))

    def sweep_rows(self) -> tuple[dict[str, Any], ...]:
        """把列式扫描数据展开为逐点行，供绘图和报告使用。"""
        columns = self.sweep_data
        count = len(next(iter(columns.values()))) if columns else 0
        rows = []
        for index in range(count):
            row: dict[str, Any] = {}
            for name, values in columns.items():
                value = values[index]
                if name == "voltages" and isinstance(value, Mapping):
                    row.update({f"V_{key}": item for key, item in value.items()})
                elif name == "currents" and isinstance(value, Mapping):
                    row.update({f"I_{key}": item for key, item in value.items()})
                else:
                    row[name] = value
            rows.append(row)
        return tuple(rows)


# -- 结果契约 ---------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class MeasurementResult:
    """跨三类测量共享的正式结果契约；子类补充测量类型特有字段。"""

    measurement_type: MeasurementType | str
    status: MeasurementStatus | str = MeasurementStatus.COMPLETED
    run_id: str
    error: str | None = None
    errors: tuple[str, ...] = ()
    cleanup_records: tuple[Mapping[str, Any], ...] = ()
    termination: Mapping[str, Any] | None = None
    configuration_snapshot: Mapping[str, Any] = field(default_factory=dict)
    device_summary: Mapping[str, Any] = field(default_factory=dict)
    raw_data_reference: Mapping[str, Any] = field(default_factory=dict)
    calculation_summary: Mapping[str, Any] = field(default_factory=dict)
    result_reference: Mapping[str, Any] = field(default_factory=dict)
    created_at: str | None = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    measurement_time: str | None = None
    source_filename: str | None = None

    def __post_init__(self) -> None:
        measurement_type = _enum_value(self.measurement_type, MeasurementType, "measurement_type")
        status = _enum_value(self.status, MeasurementStatus, "status")
        if not isinstance(self.run_id, str) or not self.run_id.strip():
            raise ValueError("run_id 不能为空")
        object.__setattr__(self, "measurement_type", measurement_type)
        object.__setattr__(self, "status", status)
        for name in (
            "configuration_snapshot",
            "device_summary",
            "raw_data_reference",
            "calculation_summary",
            "result_reference",
        ):
            object.__setattr__(self, name, freeze(getattr(self, name)))
        object.__setattr__(self, "errors", _errors(getattr(self, "errors")))
        object.__setattr__(self, "cleanup_records", _records(getattr(self, "cleanup_records")))
        object.__setattr__(self, "termination", self._frozen_termination())
        if status is MeasurementStatus.COMPLETED and self.termination is not None:
            raise ValueError("completed 结果不能包含 termination")
        if status is MeasurementStatus.EMERGENCY_STOPPED:
            _validate_termination(self.termination, "emergency_stop")
        if status is MeasurementStatus.CANCELLED:
            _validate_termination(self.termination, None)
        if status is MeasurementStatus.FAILED and not self.error:
            raise ValueError("failed 结果必须包含 error")

    def _frozen_termination(self) -> Mapping[str, Any] | None:
        if self.termination is None:
            return None
        return freeze(self.termination)

    def result_document(self) -> dict[str, Any]:
        """返回测量类型特有的正式结果字段；基类结果没有特有字段。"""
        return {}

    def to_dict(self) -> dict[str, Any]:
        """返回运行目录中唯一的结果文档形状。"""
        document = dict(self.result_document())
        document.update(
            {
                "measurement_type": self.measurement_type.value,
                "status": self.status.value,
                "run_id": self.run_id,
                "error": self.error,
                "termination": thaw(self.termination) if self.termination is not None else None,
                "configuration_snapshot": thaw(self.configuration_snapshot),
                "device_summary": thaw(self.device_summary),
                "raw_data_reference": thaw(self.raw_data_reference),
                "calculation_summary": thaw(self.calculation_summary),
                "created_at": self.created_at,
                "errors": list(self.errors),
                "cleanup_records": thaw(self.cleanup_records),
                "result_reference": thaw(self.result_reference),
                "measurement_time": self.measurement_time,
                "source_filename": self.source_filename,
            }
        )
        return document

    def report_rows(self) -> tuple[dict[str, Any], ...]:
        """报告与 CSV 使用的逐行数据；基类结果没有结果行。"""
        return ()

    def sweep_rows(self) -> Mapping[str, tuple[dict[str, Any], ...]]:
        """按频点分组的扫描行；没有扫描数据时为空。"""
        return {}

    def saturation_points(self) -> tuple[dict[str, Any], ...]:
        """压缩点/饱和点摘要；没有压缩点结果时为空。"""
        return ()

    @property
    def point_count(self) -> int:
        """结果行数；报告用它判断结果是否可绘制。"""
        return len(self.report_rows())

    @property
    def measurement_type_value(self) -> str:
        """测量类型的正式字符串值，供报告与导出使用。"""
        return self.measurement_type.value

    @property
    def status_value(self) -> str:
        """结果状态的正式字符串值，供报告与导出使用。"""
        return self.status.value

    @property
    def is_amplifier(self) -> bool:
        """是否为可生成功放报告的主功放结果。"""
        return self.measurement_type is MeasurementType.AMPLIFIER

    def with_saved_result(self, *, archive_path: str | None = None) -> "MeasurementResult":
        """登记运行目录中的权威结果路径。"""
        references = dict(self.result_reference)
        if archive_path is not None:
            references["archive_path"] = archive_path
        return replace(self, result_reference=references)

    @property
    def is_formal_result(self) -> bool:
        """是否为三个具体正式结果之一；共享基类本身不参与持久化与展示。"""
        return False


@dataclass(frozen=True, kw_only=True)
class CableLossMeasurementResult(MeasurementResult):
    """线损测量结果：每个频点的四段线缆损耗与衰减器取值。"""

    measurement_type: MeasurementType | str = MeasurementType.CABLE_LOSS
    attenuator_value: float | None = None
    points: tuple[CableLossRow, ...] = ()

    @property
    def is_formal_result(self) -> bool:
        return True

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.measurement_type is not MeasurementType.CABLE_LOSS:
            raise ValueError("CableLossMeasurementResult 的 measurement_type 必须为 cable_loss")
        if self.attenuator_value is not None:
            object.__setattr__(self, "attenuator_value", _float(self.attenuator_value, "attenuator_value"))
        object.__setattr__(self, "points", _rows(self.points, CableLossRow, "points"))

    @classmethod
    def from_service_payload(
        cls, payload: Mapping[str, Any], *, run_id: str
    ) -> "CableLossMeasurementResult":
        """把线损服务载荷转换为正式结果（服务载荷仍是本层唯一入口）。"""
        if not isinstance(payload, Mapping):
            raise ValueError("线损测量结果载荷必须是映射")
        return cls(
            run_id=run_id,
            status=_service_status(payload.get("status")),
            attenuator_value=_float_or_none(payload.get("attenuator_value")),
            points=_cable_loss_rows(payload.get("cable_losses")),
        )

    @classmethod
    def from_document(
        cls, document: Mapping[str, Any], *, run_id: str
    ) -> "CableLossMeasurementResult":
        """读取正式线损结果文档；缺少 ``cable_losses`` 时直接报错。"""
        if "cable_losses" not in document:
            raise ValueError("线损结果文档缺少 cable_losses 字段")
        return cls(
            run_id=run_id,
            status=_document_status(document),
            attenuator_value=_float_or_none(document.get("attenuator_value")),
            points=_cable_loss_rows(document.get("cable_losses")),
            created_at=_created_at(document),
            **_contract_fields(document),
        )

    def cable_losses(self) -> dict[str, Mapping[str, float]]:
        """按频点分组的线缆损耗表（结果文档形状），供报告与既有调用方读取。"""
        return {
            row.frequency_ghz: row.cable_losses.to_mapping() for row in self.points
        }

    def result_document(self) -> dict[str, Any]:
        return {
            "attenuator_value": self.attenuator_value,
            "cable_losses": {
                row.frequency_ghz: row.cable_losses.to_mapping() for row in self.points
            },
        }

    def report_rows(self) -> tuple[dict[str, Any], ...]:
        rows = []
        for row in self.points:
            entry: dict[str, Any] = {"frequency_ghz": row.frequency_ghz}
            entry.update(row.cable_losses.to_mapping())
            rows.append(entry)
        return tuple(rows)


@dataclass(frozen=True, kw_only=True)
class DriverPowerMappingMeasurementResult(MeasurementResult):
    """驱动映射结果：每个频点的输入功率到补偿后输出功率。"""

    measurement_type: MeasurementType | str = MeasurementType.DRIVER_POWER_MAPPING
    points: tuple[DriverPowerPoint, ...] = ()

    @property
    def is_formal_result(self) -> bool:
        return True

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.measurement_type is not MeasurementType.DRIVER_POWER_MAPPING:
            raise ValueError(
                "DriverPowerMappingMeasurementResult 的 measurement_type 必须为 driver_power_mapping"
            )
        object.__setattr__(self, "points", _rows(self.points, DriverPowerPoint, "points"))

    @classmethod
    def from_service_payload(
        cls, payload: Mapping[str, Any], *, run_id: str
    ) -> "DriverPowerMappingMeasurementResult":
        if not isinstance(payload, Mapping):
            raise ValueError("驱动映射结果载荷必须是映射")
        return cls(
            run_id=run_id,
            status=_service_status(payload.get("status")),
            points=_power_mapping_points(payload.get("power_mapping")),
        )

    @classmethod
    def from_document(
        cls, document: Mapping[str, Any], *, run_id: str
    ) -> "DriverPowerMappingMeasurementResult":
        if "power_mapping" not in document:
            raise ValueError("驱动映射结果文档缺少 power_mapping 字段")
        return cls(
            run_id=run_id,
            status=_document_status(document),
            points=_power_mapping_points(document.get("power_mapping")),
            created_at=_created_at(document),
            **_contract_fields(document),
        )

    def power_mapping(self) -> dict[str, dict[str, float]]:
        """按频点分组的“输入功率 -> 输出功率”表。"""
        return self.result_document()["power_mapping"]

    def result_document(self) -> dict[str, Any]:
        mapping: dict[str, dict[str, float]] = {}
        for point in self.points:
            mapping.setdefault(point.frequency_ghz, {})[
                str(point.input_power_dbm)
            ] = point.output_power_dbm
        return {"power_mapping": mapping}

    def report_rows(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "frequency_ghz": point.frequency_ghz,
                "input_power_dbm": point.input_power_dbm,
                "output_power_dbm": point.output_power_dbm,
                "gain_db": point.output_power_dbm - point.input_power_dbm,
            }
            for point in self.points
        )

    def sweep_rows(self) -> Mapping[str, tuple[dict[str, Any], ...]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for point in self.points:
            grouped.setdefault(point.frequency_ghz, []).append(
                {
                    "input_power_sg": point.input_power_dbm,
                    "output_power_driver": point.output_power_dbm,
                    "gain": point.output_power_dbm - point.input_power_dbm,
                }
            )
        return {frequency: tuple(rows) for frequency, rows in grouped.items()}


@dataclass(frozen=True, kw_only=True)
class AmplifierMeasurementResult(MeasurementResult):
    """主功放结果：每个频点的压缩点、小信号增益与扫描数据。"""

    measurement_type: MeasurementType | str = MeasurementType.AMPLIFIER
    frequencies: tuple[AmplifierFrequencyResult, ...] = ()

    @property
    def is_formal_result(self) -> bool:
        return True

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.measurement_type is not MeasurementType.AMPLIFIER:
            raise ValueError("AmplifierMeasurementResult 的 measurement_type 必须为 amplifier")
        object.__setattr__(
            self, "frequencies", _rows(self.frequencies, AmplifierFrequencyResult, "frequencies")
        )

    @classmethod
    def from_service_payload(
        cls, payload: Mapping[str, Any], *, run_id: str
    ) -> "AmplifierMeasurementResult":
        if not isinstance(payload, Mapping):
            raise ValueError("主功放结果载荷必须是映射")
        return cls(
            run_id=run_id,
            status=_service_status(payload.get("status")),
            frequencies=_amplifier_frequencies_from_service(payload.get("results")),
            configuration_snapshot=payload.get("configuration_snapshot", {}),
        )

    @classmethod
    def from_document(
        cls, document: Mapping[str, Any], *, run_id: str
    ) -> "AmplifierMeasurementResult":
        if "results" not in document:
            raise ValueError("主功放结果文档缺少 results 字段")
        return cls(
            run_id=run_id,
            status=_document_status(document),
            frequencies=_amplifier_frequencies_from_document(document.get("results")),
            created_at=_created_at(document),
            **_contract_fields(document),
        )

    def result_document(self) -> dict[str, Any]:
        return {
            "results": {
                item.frequency_ghz: _amplifier_document(item) for item in self.frequencies
            }
        }

    def report_rows(self) -> tuple[dict[str, Any], ...]:
        rows = []
        for item in self.frequencies:
            for sweep_row in item.sweep_rows():
                entry: dict[str, Any] = {"frequency_ghz": item.frequency_ghz}
                entry.update(sweep_row)
                rows.append(entry)
        return tuple(rows)

    def sweep_rows(self) -> Mapping[str, tuple[dict[str, Any], ...]]:
        return {
            item.frequency_ghz: item.sweep_rows()
            for item in self.frequencies
            if item.sweep_data
        }

    def saturation_points(self) -> tuple[dict[str, Any], ...]:
        points = []
        for item in self.frequencies:
            point = item.compression_point
            if point is None:
                continue
            points.append(
                {
                    "frequency": item.frequency_ghz,
                    "output_power": point.output_power_dbm,
                    "input_power": point.input_power_dbm,
                    "gain": point.gain_db,
                    "efficiency": point.efficiency_percent,
                    "sg_power": point.signal_generator_power_dbm,
                    "compression": point.compression_db,
                    "compression_achieved": item.compression_achieved,
                }
            )
        return tuple(points)


def measurement_result_from_document(document: Mapping[str, Any]) -> MeasurementResult:
    """把运行目录中的正式结果文档转换为显式结果 DTO。

    只接受 ``to_dict()`` 产生的正式 schema：

    - ``run_id``、``measurement_type``、``status`` 必须存在且有效；
    - 测量类型必须与结果数据字段一一对应（``cable_losses`` / ``power_mapping`` /
      ``results``），类型与数据不一致视为非正式文档；
    - 旧字段别名、``legacy_payload``、未知字段一律 ``ValueError``；
    - 写入层补充的 ``result_type`` / ``schema_version`` / ``saved_at`` 必须自洽，
      与 ``measurement_type`` 冲突时同样报错。

    不做按字段猜测历史格式的兼容推断。
    """
    if not isinstance(document, Mapping):
        raise ValueError("测量结果文档必须是映射对象")
    canonical = document.get("canonical_model")
    if canonical is not None:
        artifact_extras = set(document) - _CANONICAL_ARTIFACT_KEYS
        if artifact_extras:
            raise ValueError(
                "版本化模型工件包含未知字段: " + ", ".join(sorted(artifact_extras))
            )
        result = measurement_result_from_document(canonical)
        artifact_run_id = document.get("run_id")
        if artifact_run_id is not None and artifact_run_id != result.run_id:
            raise ValueError("版本化模型工件的 run_id 与 canonical_model 不一致")
        artifact_type = document.get("result_type")
        if artifact_type is not None and artifact_type != _CANONICAL_ARTIFACT_TYPE:
            raise ValueError(
                "版本化模型工件的 result_type 必须为 "
                f"{_CANONICAL_ARTIFACT_TYPE}: {artifact_type!r}"
            )
        return result
    run_id = document.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("正式测量结果必须包含非空 run_id")
    declared = document.get("measurement_type")
    if not isinstance(declared, str) or not declared.strip():
        raise ValueError("正式测量结果必须包含 measurement_type")
    try:
        kind = MeasurementType(declared)
    except ValueError as error:
        raise ValueError(f"无法识别的正式测量结果 measurement_type: {declared!r}") from error
    legacy = sorted(_LEGACY_DOCUMENT_KEYS & set(document))
    if legacy:
        raise ValueError(f"不是正式结果 schema：包含旧字段 {', '.join(legacy)}")
    data_fields = _TYPE_ALLOWED_FIELDS[kind]
    others = sorted(
        {
            field
            for other, fields in _TYPE_ALLOWED_FIELDS.items()
            if other is not kind
            for field in fields
        }
        & set(document)
    )
    if others:
        raise ValueError(
            f"{kind.value} 结果不能包含其它测量类型字段: {', '.join(others)}"
        )
    missing = sorted(field for field in _TYPE_REQUIRED_FIELDS[kind] if field not in document)
    if missing:
        raise ValueError(f"{kind.value} 结果缺少正式字段: {', '.join(missing)}")
    allowed = _COMMON_DOCUMENT_KEYS | data_fields | _ARTIFACT_METADATA_KEYS
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise ValueError(f"不是正式结果 schema：包含未知字段 {', '.join(unknown)}")
    _validate_artifact_metadata(document, kind)
    if kind is MeasurementType.CABLE_LOSS:
        return CableLossMeasurementResult.from_document(document, run_id=run_id)
    if kind is MeasurementType.DRIVER_POWER_MAPPING:
        return DriverPowerMappingMeasurementResult.from_document(document, run_id=run_id)
    return AmplifierMeasurementResult.from_document(document, run_id=run_id)


def require_formal_result(value: Any) -> MeasurementResult:
    """断言值是三个具体正式结果之一。

    共享基类 ``MeasurementResult`` 只是契约载体：它不描述任何真实测量，也不允许
    被持久化或展示。映射、字符串等原始文档必须先经过
    :func:`measurement_result_from_document`。
    """
    if isinstance(value, MeasurementResult) and value.is_formal_result:
        return value
    raise ValueError(
        "必须是正式测量结果 DTO（CableLoss/DriverPowerMapping/Amplifier）；"
        "原始文档请先调用 measurement_result_from_document()"
    )


# -- 内部工具 ---------------------------------------------------------------


_COMMON_DOCUMENT_KEYS = frozenset(
    {
        "measurement_type",
        "status",
        "run_id",
        "error",
        "errors",
        "cleanup_records",
        "termination",
        "configuration_snapshot",
        "device_summary",
        "raw_data_reference",
        "calculation_summary",
        "result_reference",
        "created_at",
        "measurement_time",
        "source_filename",
    }
)
_TYPE_REQUIRED_FIELDS: Mapping[MeasurementType, frozenset[str]] = MappingProxyType(
    {
        MeasurementType.CABLE_LOSS: frozenset({"cable_losses"}),
        MeasurementType.DRIVER_POWER_MAPPING: frozenset({"power_mapping"}),
        MeasurementType.AMPLIFIER: frozenset({"results"}),
    }
)
_TYPE_ALLOWED_FIELDS: Mapping[MeasurementType, frozenset[str]] = MappingProxyType(
    {
        MeasurementType.CABLE_LOSS: frozenset({"attenuator_value", "cable_losses"}),
        MeasurementType.DRIVER_POWER_MAPPING: frozenset({"power_mapping"}),
        MeasurementType.AMPLIFIER: frozenset({"results"}),
    }
)
# 结果文件由写入层补充的工件元数据；它们是正式字段，不是历史别名。
_ARTIFACT_METADATA_KEYS = frozenset({"schema_version", "result_type", "saved_at"})
# 正式结果文档当前唯一支持的 schema 版本。
_SUPPORTED_RESULT_SCHEMA_VERSIONS = frozenset({"1.0"})
# canonical model 工件（``*_model.json``）的外层结构。
_CANONICAL_ARTIFACT_TYPE = "versioned_measurement_result"
_CANONICAL_ARTIFACT_KEYS = frozenset(
    {"canonical_model", "run_id", "result_type", "schema_version", "saved_at"}
)
# 步骤 6 已退役的旧文档字段；出现即视为非正式 schema。
_LEGACY_DOCUMENT_KEYS = frozenset(
    {
        "legacy_payload",
        "payload",
        "summary",
        "data_reference",
        "config",
        "derived_metrics",
        "resource_snapshot",
        "plan_snapshot",
        "raw_readings",
        "metadata",
        "original_filename",
    }
)


def _frequency_key(value: Any, label: str) -> str:
    """校验频点标识：必须是能解析为正频率的字符串；保留原始文本形式。

    读取已有结果时保留文件里的键文本（``"2.40"`` 不会在读取时被悄悄改写成
    ``"2.4"``），解析本身走唯一的频率边界 ``Frequency.from_document_key``：
    无单位按 schema 约定取 GHz，带单位按声明单位，非字符串或非法数值一律拒绝。
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}的频点标识不能为空")
    frequency = Frequency.from_document_key(value)
    if frequency.ghz <= 0:
        raise ValueError(f"{label}的频点标识非法: {value!r}")
    return value


def _document_status(document: Mapping[str, Any]) -> MeasurementStatus:
    value = document.get("status")
    if value is None:
        raise ValueError("正式测量结果文档缺少 status 字段")
    return MeasurementStatus(str(value))


def _validate_artifact_metadata(document: Mapping[str, Any], kind: MeasurementType) -> None:
    """校验写入层补充的工件元数据与测量类型自洽。

    这些键由 ``save_json_result`` 追加，属于正式字段，因此必须与 ``measurement_type``
    一致；冲突的标签说明文件被错误分类，不能当作正式结果读取。
    """
    label = document.get("result_type")
    if label is not None:
        resolved = measurement_type_for_result_type(label)
        if resolved is None:
            raise ValueError(
                f"不是正式结果 schema：无法识别的 result_type {label!r}"
            )
        if resolved is not kind:
            raise ValueError(
                f"result_type 与 measurement_type 不一致: {label!r} != {kind.value!r}"
            )
    if "schema_version" in document:
        version = document.get("schema_version")
        text = str(version).strip() if version is not None else ""
        if text not in _SUPPORTED_RESULT_SCHEMA_VERSIONS:
            raise ValueError(f"不支持的测量结果 schema_version: {version!r}")
    if "saved_at" in document:
        saved_at = document.get("saved_at")
        if not isinstance(saved_at, str) or not saved_at.strip():
            raise ValueError("saved_at 必须是非空字符串")


def _service_status(value: Any) -> MeasurementStatus:
    """把服务层载荷状态收敛为正式状态；服务只使用 waiting/finished 两个别名。"""
    if isinstance(value, MeasurementStatus):
        return value
    if value is None:
        return MeasurementStatus.COMPLETED
    text = str(value)
    translated = {
        "waiting": MeasurementStatus.WAITING_FOR_CONTINUE.value,
        "finished": MeasurementStatus.COMPLETED.value,
    }.get(text, text)
    return MeasurementStatus(translated)


def _cable_loss_rows(value: Any) -> tuple[CableLossRow, ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValueError("cable_losses 必须是映射")
    rows = []
    for frequency, losses in value.items():
        if not isinstance(losses, Mapping):
            raise ValueError(f"线损结果 {frequency!r} 必须是映射")
        rows.append(CableLossRow(frequency_ghz=str(frequency), cable_losses=losses))
    return tuple(rows)


def _power_mapping_points(value: Any) -> tuple[DriverPowerPoint, ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValueError("power_mapping 必须是映射")
    points = []
    for frequency, bucket in value.items():
        if not isinstance(bucket, Mapping):
            raise ValueError(f"驱动映射结果 {frequency!r} 必须是映射")
        for input_power, output_power in bucket.items():
            points.append(
                DriverPowerPoint(
                    frequency_ghz=str(frequency),
                    input_power_dbm=input_power,
                    output_power_dbm=output_power,
                )
            )
    return tuple(points)


_AMPLIFIER_DOCUMENT_KEYS = frozenset(
    {
        "compression_type",
        "compression_point",
        "small_signal_gain_db",
        "compression_achieved",
        "sweep_data",
    }
)
_AMPLIFIER_COMPRESSION_DOCUMENT_KEYS = frozenset(
    {
        "input_power_dbm",
        "output_power_dbm",
        "gain_db",
        "efficiency_percent",
        "compression_db",
        "signal_generator_power_dbm",
    }
)


def _amplifier_frequencies_from_service(
    value: Any,
) -> tuple[AmplifierFrequencyResult, ...]:
    """读取测量服务载荷（``results``）；服务字段名只在本函数内翻译一次。"""
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValueError("results 必须是映射")
    results = []
    for frequency, item in value.items():
        if not isinstance(item, Mapping):
            raise ValueError(f"主功放结果 {frequency!r} 必须是映射")
        point = item.get("compression_point")
        results.append(
            AmplifierFrequencyResult(
                frequency_ghz=str(frequency),
                compression_type=_optional_text(item.get("compression_type")),
                compression_point=_compression_point(
                    point,
                    reader=_service_compression_point,
                    name="compression_point",
                ),
                small_signal_gain_db=_float_or_none(item.get("small_signal_gain")),
                compression_achieved=_optional_bool(item.get("compression_achieved")),
                sweep_data=item.get("sweep_data", {}),
            )
        )
    return tuple(results)


def _amplifier_frequencies_from_document(
    value: Any,
) -> tuple[AmplifierFrequencyResult, ...]:
    """读取正式结果文档（``results``）；只接受正式字段名。"""
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValueError("results 必须是映射")
    results = []
    for frequency, item in value.items():
        if not isinstance(item, Mapping):
            raise ValueError(f"主功放结果 {frequency!r} 必须是映射")
        unknown = sorted(set(item) - _AMPLIFIER_DOCUMENT_KEYS)
        if unknown:
            raise ValueError(
                f"主功放结果 {frequency!r} 包含未知字段: {', '.join(unknown)}"
            )
        results.append(
            AmplifierFrequencyResult(
                frequency_ghz=str(frequency),
                compression_type=_optional_text(item.get("compression_type")),
                compression_point=_compression_point(
                    item.get("compression_point"),
                    reader=_document_compression_point,
                    name=f"主功放结果 {frequency!r} 的 compression_point",
                ),
                small_signal_gain_db=_float_or_none(item.get("small_signal_gain_db")),
                compression_achieved=_optional_bool(item.get("compression_achieved")),
                sweep_data=item.get("sweep_data", {}),
            )
        )
    return tuple(results)


def _compression_point(
    value: Any,
    *,
    reader: Callable[[Mapping[str, Any], str], AmplifierCompressionPoint],
    name: str,
) -> AmplifierCompressionPoint | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} 必须是映射")
    if not value:
        return None
    return reader(value, name)


def _service_compression_point(
    value: Mapping[str, Any], name: str
) -> AmplifierCompressionPoint:
    return AmplifierCompressionPoint(
        input_power_dbm=_float_or_none(value.get("input_power")),
        output_power_dbm=_float_or_none(value.get("output_power")),
        gain_db=_float_or_none(value.get("gain")),
        efficiency_percent=_float_or_none(value.get("efficiency")),
        compression_db=_float_or_none(value.get("compression_dB")),
        signal_generator_power_dbm=_float_or_none(
            value.get("sg_power_at_compression")
        ),
    )


def _document_compression_point(
    value: Mapping[str, Any], name: str
) -> AmplifierCompressionPoint:
    unknown = sorted(set(value) - _AMPLIFIER_COMPRESSION_DOCUMENT_KEYS)
    if unknown:
        raise ValueError(f"{name} 包含未知字段: {', '.join(unknown)}")
    return AmplifierCompressionPoint(
        input_power_dbm=_float_or_none(value.get("input_power_dbm")),
        output_power_dbm=_float_or_none(value.get("output_power_dbm")),
        gain_db=_float_or_none(value.get("gain_db")),
        efficiency_percent=_float_or_none(value.get("efficiency_percent")),
        compression_db=_float_or_none(value.get("compression_db")),
        signal_generator_power_dbm=_float_or_none(
            value.get("signal_generator_power_dbm")
        ),
    )


def _amplifier_document(item: AmplifierFrequencyResult) -> dict[str, Any]:
    point = item.compression_point
    return {
        "compression_type": item.compression_type,
        "compression_point": {} if point is None else {
            "input_power_dbm": point.input_power_dbm,
            "output_power_dbm": point.output_power_dbm,
            "gain_db": point.gain_db,
            "efficiency_percent": point.efficiency_percent,
            "compression_db": point.compression_db,
            "signal_generator_power_dbm": point.signal_generator_power_dbm,
        },
        "small_signal_gain_db": item.small_signal_gain_db,
        "sweep_data": {
            name: [thaw(item) for item in values] for name, values in item.sweep_data.items()
        },
        "compression_achieved": item.compression_achieved,
    }


def _contract_fields(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "error": document.get("error"),
        "errors": document.get("errors", ()),
        "cleanup_records": document.get("cleanup_records", ()),
        "termination": document.get("termination"),
        "configuration_snapshot": document.get("configuration_snapshot", {}),
        "device_summary": document.get("device_summary", {}),
        "raw_data_reference": document.get("raw_data_reference", {}),
        "calculation_summary": document.get("calculation_summary", {}),
        "result_reference": document.get("result_reference", {}),
        "measurement_time": _optional_text(document.get("measurement_time")),
        "source_filename": _optional_text(document.get("source_filename")),
    }


def _created_at(document: Mapping[str, Any]) -> str | None:
    value = document.get("created_at")
    return value if isinstance(value, str) and value.strip() else None


def _enum_value(value: Any, enum_type: type[Enum], name: str) -> Enum:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(str(value))
    except ValueError as error:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{name} 无效: {value!r}，可选值: {allowed}") from error


def _float(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是数值: {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} 必须是数值: {value!r}") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须是有限数值: {value!r}")
    return number


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return _float(value, "数值字段")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_bool(value: Any) -> bool | None:
    """严格读取布尔值：只接受 bool、0/1 与 ``"true"``/``"false"``。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text == "true":
            return True
        if text == "false":
            return False
    raise ValueError(f"布尔字段取值非法: {value!r}")


def _float_mapping(value: Any, name: str) -> Mapping[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} 必须是映射")
    return freeze({str(key): _float(item, name) for key, item in value.items()})


def _columns(value: Any, name: str) -> Mapping[str, tuple[Any, ...]]:
    """校验列式扫描数据：各列等长，数值列必须是有限浮点。

    ``voltages`` / ``currents`` 是按供电通道分组的映射列，其叶值同样必须是有限浮点；
    其余列一律视为标量序列。结构化对象、字符串数字混排和 ``NaN`` / ``inf`` 都视为
    非正式结果，避免把无法绘图的载荷写进结果模型。
    """
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} 必须是列式映射")
    columns: dict[str, tuple[Any, ...]] = {}
    length: int | None = None
    for key, items in value.items():
        if not isinstance(items, (list, tuple)):
            raise ValueError(f"{name}.{key} 必须是扫描点序列")
        if length is None:
            length = len(items)
        elif len(items) != length:
            raise ValueError(f"{name} 的各列长度必须一致")
        column = str(key)
        if column in _SWEEP_MAPPING_COLUMNS:
            columns[column] = tuple(
                _float_mapping(item, f"{name}.{column}") for item in items
            )
        else:
            columns[column] = tuple(_float(item, f"{name}.{column}") for item in items)
    return MappingProxyType(columns)


_SWEEP_MAPPING_COLUMNS = frozenset({"voltages", "currents"})


def _rows(value: Any, expected: type, name: str) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} 必须是结果序列")
    if not all(isinstance(item, expected) for item in value):
        raise ValueError(f"{name} 中的每项必须是 {expected.__name__}")
    return tuple(value)


def _errors(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)):
        raise ValueError("errors 必须是字符串或字符串序列")
    return tuple(str(item) for item in value)


def _records(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        value = (value,)
    if not isinstance(value, (list, tuple)):
        raise ValueError("cleanup_records 必须是映射序列")
    records = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("cleanup_records 中的每项必须是映射")
        # 逐项深冻结：否则冻结 dataclass 的内部记录仍可被外部原始对象改写。
        records.append(freeze(item))
    return tuple(records)


def _validate_termination(value: Mapping[str, Any] | None, expected: str | None) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("终止结果必须包含 termination")
    intent = value.get("intent")
    if intent not in {"stop", "cancel", "emergency_stop"}:
        raise ValueError("termination.intent 无效")
    if expected is not None and intent != expected:
        raise ValueError(f"termination.intent 必须为 {expected}")
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("termination.reason 不能为空")


__all__ = [
    "MeasurementType",
    "MeasurementStatus",
    "MeasurementResult",
    "CableLossMeasurementResult",
    "DriverPowerMappingMeasurementResult",
    "AmplifierMeasurementResult",
    "CableLossRow",
    "DriverPowerPoint",
    "AmplifierCompressionPoint",
    "AmplifierFrequencyResult",
    "measurement_result_from_document",
    "measurement_type_for_result_type",
    "require_formal_result",
    "normalize_frequency_key",
    "freeze",
    "thaw",
]
