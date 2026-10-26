"""三类正式测量结果的应用层契约。

结果模型只描述正式结果：

- 线损按频点保存四条线缆分摊损耗；
- 驱动映射按频点保存“输入功率 -> 输出功率”点；
- 主功放按频点保存压缩点、小信号增益和扫描数据。

结果不再继承 ``Mapping``，也不再保存服务层的原始载荷；``to_dict`` 是唯一的结果
文档形状，``measurement_result_from_document`` 只接受该形状，其它 schema 直接
报错，不存在按字段猜测历史格式的兼容路径。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any


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


# -- 结果数据 ---------------------------------------------------------------


@dataclass(frozen=True)
class CableLossRow:
    """单个频点的线缆分摊损耗，键为 ``cable1`` 至 ``cable4`` 与两条路径总损耗。"""

    frequency_ghz: str
    cable_losses: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.frequency_ghz, str) or not self.frequency_ghz.strip():
            raise ValueError("线损结果的频点标识不能为空")
        object.__setattr__(
            self, "cable_losses", _float_mapping(self.cable_losses, "cable_losses")
        )


@dataclass(frozen=True)
class DriverPowerPoint:
    """单个驱动映射点：信号源输入功率到 DUT 补偿后输出功率。"""

    frequency_ghz: str
    input_power_dbm: float
    output_power_dbm: float

    def __post_init__(self) -> None:
        if not isinstance(self.frequency_ghz, str) or not self.frequency_ghz.strip():
            raise ValueError("驱动映射结果的频点标识不能为空")
        object.__setattr__(self, "input_power_dbm", _float(self.input_power_dbm, "input_power_dbm"))
        object.__setattr__(self, "output_power_dbm", _float(self.output_power_dbm, "output_power_dbm"))


@dataclass(frozen=True)
class AmplifierCompressionPoint:
    """主功放的压缩点指标，字段缺失表示该指标未计算。"""

    input_power_dbm: float | None = None
    output_power_dbm: float | None = None
    gain_db: float | None = None
    efficiency_percent: float | None = None
    compression_db: float | None = None
    signal_generator_power_dbm: float | None = None


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
        if not isinstance(self.frequency_ghz, str) or not self.frequency_ghz.strip():
            raise ValueError("主功放结果的频点标识不能为空")
        if self.compression_point is not None and not isinstance(
            self.compression_point, AmplifierCompressionPoint
        ):
            raise ValueError("compression_point 必须是 AmplifierCompressionPoint")
        object.__setattr__(self, "sweep_data", _columns(self.sweep_data, "sweep_data"))


# -- 结果契约 ---------------------------------------------------------------


@dataclass(frozen=True)
class MeasurementResult:
    """跨三类测量共享的正式结果契约；子类补充测量类型特有字段。"""

    measurement_type: MeasurementType | str
    status: MeasurementStatus | str
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
            }
        )
        return document

    def with_saved_result(self, *, archive_path: str | None = None) -> "MeasurementResult":
        """登记运行目录中的权威结果路径。"""
        references = dict(self.result_reference)
        if archive_path is not None:
            references["archive_path"] = archive_path
        return replace(self, result_reference=references)


@dataclass(frozen=True, kw_only=True)
class CableLossMeasurementResult(MeasurementResult):
    """线损测量结果：每个频点的四段线缆损耗与衰减器取值。"""

    measurement_type: MeasurementType | str = MeasurementType.CABLE_LOSS
    attenuator_value: float | None = None
    points: tuple[CableLossRow, ...] = ()

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
        """按频点分组的线缆损耗表，供报告与既有调用方读取。"""
        return {row.frequency_ghz: row.cable_losses for row in self.points}

    def result_document(self) -> dict[str, Any]:
        return {
            "attenuator_value": self.attenuator_value,
            "cable_losses": {
                row.frequency_ghz: dict(row.cable_losses) for row in self.points
            },
        }


@dataclass(frozen=True, kw_only=True)
class DriverPowerMappingMeasurementResult(MeasurementResult):
    """驱动映射结果：每个频点的输入功率到补偿后输出功率。"""

    measurement_type: MeasurementType | str = MeasurementType.DRIVER_POWER_MAPPING
    points: tuple[DriverPowerPoint, ...] = ()

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


@dataclass(frozen=True, kw_only=True)
class AmplifierMeasurementResult(MeasurementResult):
    """主功放结果：每个频点的压缩点、小信号增益与扫描数据。"""

    measurement_type: MeasurementType | str = MeasurementType.AMPLIFIER
    frequencies: tuple[AmplifierFrequencyResult, ...] = ()

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
            frequencies=_amplifier_frequencies(payload.get("results")),
            configuration_snapshot=payload.get("config", {}),
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
            frequencies=_amplifier_frequencies(document.get("results")),
            created_at=_created_at(document),
            **_contract_fields(document),
        )

    def result_document(self) -> dict[str, Any]:
        return {
            "results": {
                item.frequency_ghz: _amplifier_document(item) for item in self.frequencies
            }
        }


def measurement_result_from_document(document: Mapping[str, Any]) -> MeasurementResult:
    """把运行目录中的正式结果文档转换为显式结果 DTO。

    只接受 ``to_dict`` 产生的正式 schema：缺少 ``run_id`` 或测量类型无法识别时抛出
    ``ValueError``，不会按字段猜测历史格式。
    """
    if not isinstance(document, Mapping):
        raise ValueError("测量结果文档必须是映射对象")
    run_id = document.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("正式测量结果必须包含非空 run_id")
    kind = str(document.get("measurement_type") or _infer_measurement_type(document))
    if kind in {MeasurementType.CABLE_LOSS.value} or "cable_losses" in document:
        return CableLossMeasurementResult.from_document(document, run_id=run_id)
    if kind in {MeasurementType.DRIVER_POWER_MAPPING.value} or "power_mapping" in document:
        return DriverPowerMappingMeasurementResult.from_document(document, run_id=run_id)
    if kind in {MeasurementType.AMPLIFIER.value, "amplifier_measurement"} or "results" in document:
        return AmplifierMeasurementResult.from_document(document, run_id=run_id)
    raise ValueError(f"无法识别的正式测量结果 schema: measurement_type={kind!r}")


# -- 内部工具 ---------------------------------------------------------------


def _infer_measurement_type(document: Mapping[str, Any]) -> str:
    if "cable_losses" in document:
        return MeasurementType.CABLE_LOSS.value
    if "power_mapping" in document:
        return MeasurementType.DRIVER_POWER_MAPPING.value
    if "results" in document:
        return MeasurementType.AMPLIFIER.value
    return ""


def _document_status(document: Mapping[str, Any]) -> MeasurementStatus:
    value = document.get("status")
    if value is None:
        raise ValueError("正式测量结果文档缺少 status 字段")
    return MeasurementStatus(str(value))


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


def _amplifier_frequencies(value: Any) -> tuple[AmplifierFrequencyResult, ...]:
    if value is None:
        return ()
    if not isinstance(value, Mapping):
        raise ValueError("results 必须是映射")
    results = []
    for frequency, item in value.items():
        if not isinstance(item, Mapping):
            raise ValueError(f"主功放结果 {frequency!r} 必须是映射")
        results.append(
            AmplifierFrequencyResult(
                frequency_ghz=str(frequency),
                compression_type=_optional_text(item.get("compression_type")),
                compression_point=_compression_point(item.get("compression_point")),
                small_signal_gain_db=_float_or_none(item.get("small_signal_gain")),
                compression_achieved=_optional_bool(item.get("compression_achieved")),
                sweep_data=item.get("sweep_data", {}),
            )
        )
    return tuple(results)


def _compression_point(value: Any) -> AmplifierCompressionPoint | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("compression_point 必须是映射")
    if not value:
        return None
    return AmplifierCompressionPoint(
        input_power_dbm=_float_or_none(value.get("input_power")),
        output_power_dbm=_float_or_none(value.get("output_power")),
        gain_db=_float_or_none(value.get("gain")),
        efficiency_percent=_float_or_none(value.get("efficiency")),
        compression_db=_float_or_none(value.get("compression_dB", value.get("compression"))),
        signal_generator_power_dbm=_float_or_none(
            value.get("sg_power_at_compression", value.get("sg_power"))
        ),
    )


def _amplifier_document(item: AmplifierFrequencyResult) -> dict[str, Any]:
    point = item.compression_point
    return {
        "compression_type": item.compression_type,
        "compression_point": {} if point is None else {
            "input_power": point.input_power_dbm,
            "output_power": point.output_power_dbm,
            "gain": point.gain_db,
            "efficiency": point.efficiency_percent,
            "compression_dB": point.compression_db,
            "sg_power_at_compression": point.signal_generator_power_dbm,
        },
        "small_signal_gain": item.small_signal_gain_db,
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
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} 必须是数值: {value!r}") from error


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return _float(value, "数值字段")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _float_mapping(value: Any, name: str) -> Mapping[str, float]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} 必须是映射")
    return freeze({str(key): _float(item, name) for key, item in value.items()})


def _columns(value: Any, name: str) -> Mapping[str, tuple[Any, ...]]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} 必须是列式映射")
    columns = {}
    for key, items in value.items():
        if not isinstance(items, (list, tuple)):
            raise ValueError(f"{name}.{key} 必须是扫描点序列")
        columns[str(key)] = tuple(freeze(item) for item in items)
    return MappingProxyType(columns)


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
    if not all(isinstance(item, Mapping) for item in value):
        raise ValueError("cleanup_records 中的每项必须是映射")
    return tuple(value)


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
    "freeze",
    "thaw",
]
