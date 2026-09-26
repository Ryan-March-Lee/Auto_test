"""阶段 1 冻结的运行、方案和结果模型。

模型不读取文件、不连接硬件，也不依赖 Qt、PyVISA 或绘图库。
这里的 ``TestPlan`` 是内部领域模型；``config_models.TestPlan`` 继续负责
现有 JSON 配置输入，二者之间的转换留给后续配置迁移阶段。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping
from uuid import uuid4


SCHEMA_VERSION = "1.0"
METHOD_VERSION = "1.0"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class TestPlan:
    """稳定测试方法，不包含现场设备地址或物理通道名。"""

    frequencies_hz: tuple[float, ...] = ()
    start_power_dbm: float | None = None
    stop_power_dbm: float | None = None
    power_step_db: float | None = None
    compression_point_db: float | None = None
    attenuator_loss_db: float = 0.0
    max_input_power_dbm: float | None = None
    driver_enabled: bool = False
    schema_version: str = SCHEMA_VERSION
    method_version: str = METHOD_VERSION

    def __post_init__(self) -> None:
        if any(frequency <= 0 for frequency in self.frequencies_hz):
            raise ValueError("frequencies_hz 必须为正数")
        if self.power_step_db is not None and self.power_step_db <= 0:
            raise ValueError("power_step_db 必须为正数")
        if self.start_power_dbm is not None and self.stop_power_dbm is not None and self.start_power_dbm > self.stop_power_dbm:
            raise ValueError("start_power_dbm 不能大于 stop_power_dbm")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["frequencies_hz"] = list(self.frequencies_hz)
        return value


@dataclass(frozen=True)
class RunContext:
    """一次现场运行的资源快照；物理通道只存在于 mapping 中。"""

    run_id: str = field(default_factory=lambda: str(uuid4()))
    operator: str | None = None
    software_version: str = "unknown"
    signal_generator: Mapping[str, str] = field(default_factory=dict)
    spectrum_analyzer: Mapping[str, str] = field(default_factory=dict)
    power_supply: Mapping[str, str] = field(default_factory=dict)
    power_channel_mapping: Mapping[str, str] = field(default_factory=dict)
    wiring_confirmed: bool = False
    notes: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id 不能为空")
        for name in (
            "signal_generator",
            "spectrum_analyzer",
            "power_supply",
            "power_channel_mapping",
        ):
            value = getattr(self, name)
            object.__setattr__(self, name, MappingProxyType(dict(value)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "operator": self.operator,
            "software_version": self.software_version,
            "signal_generator": dict(self.signal_generator),
            "spectrum_analyzer": dict(self.spectrum_analyzer),
            "power_supply": dict(self.power_supply),
            "power_channel_mapping": dict(self.power_channel_mapping),
            "wiring_confirmed": self.wiring_confirmed,
            "notes": self.notes,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_resource_mapping(
        cls,
        mapping: Mapping[str, Any],
        *,
        run_id: str | None = None,
        software_version: str = "unknown",
    ) -> "RunContext":
        """Build the runtime contract from a validated resource mapping.

        The conversion deliberately keeps device addresses and physical channel
        names inside the context.  Domain services can therefore receive one
        immutable snapshot without embedding site-specific names in their logic.
        """
        instruments = mapping.get("instruments", {})
        instruments = instruments if isinstance(instruments, Mapping) else {}
        power_supply = instruments.get("power_supply", {})
        power_supply = power_supply if isinstance(power_supply, Mapping) else {}
        channels = {}
        for section in ("dut_power_channels", "driver_mode"):
            values = mapping.get(section, [])
            if isinstance(values, Mapping):
                values = values.get("power_channels", [])
            if not isinstance(values, (list, tuple)):
                continue
            for item in values:
                if isinstance(item, Mapping):
                    role = item.get("role")
                    connection = item.get("connection") or item.get("channel")
                    if role and connection:
                        channels[str(role)] = str(connection)
        wiring = mapping.get("wiring", {})
        wiring = wiring if isinstance(wiring, Mapping) else {}
        return cls(
            run_id=run_id or str(mapping.get("run_id") or uuid4()),
            operator=mapping.get("operator"),
            software_version=software_version,
            signal_generator=_instrument_snapshot(instruments.get("signal_generator")),
            spectrum_analyzer=_instrument_snapshot(instruments.get("spectrum_analyzer")),
            power_supply=_instrument_snapshot(power_supply),
            power_channel_mapping=channels,
            wiring_confirmed=bool(wiring.get("confirmed", False)),
            notes=mapping.get("notes") or wiring.get("connection_note"),
        )


class ResultStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class MeasurementState(str, Enum):
    CREATED = "created"
    VALIDATED = "validated"
    CONNECTED = "connected"
    PREPARED = "prepared"
    POWERED = "powered"
    MEASURING = "measuring"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"
    CLEANED = "cleaned"


@dataclass(frozen=True)
class ResultMetadata:
    """跨结果类型共享的可追溯信息。"""

    run_id: str = "legacy"
    result_type: str = "unknown"
    saved_at: str | None = None
    measurement_time: str | None = None
    schema_version: str = SCHEMA_VERSION
    method_version: str = METHOD_VERSION

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id 不能为空")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScanPoint:
    """单个扫描点，区分原始读数、补偿读数和派生指标。"""

    frequency_hz: float
    input_power_dbm: float | None = None
    raw_output_power_dbm: float | None = None
    compensated_output_power_dbm: float | None = None
    voltage_v: float | None = None
    current_a: float | None = None
    derived: Mapping[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MeasurementResult:
    run_id: str
    measurement_type: str
    points: tuple[ScanPoint, ...] = ()
    status: ResultStatus = ResultStatus.COMPLETED
    raw_readings: Mapping[str, Any] = field(default_factory=dict)
    derived_metrics: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = SCHEMA_VERSION
    method_version: str = METHOD_VERSION
    plan_snapshot: Mapping[str, Any] = field(default_factory=dict)
    resource_snapshot: Mapping[str, Any] = field(default_factory=dict)
    metadata: ResultMetadata = field(default_factory=ResultMetadata)

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id 不能为空")
        if self.metadata.run_id == "legacy" and self.run_id != "legacy":
            object.__setattr__(self, "metadata", ResultMetadata(
                run_id=self.run_id,
                result_type=self.measurement_type,
                saved_at=self.metadata.saved_at,
                measurement_time=self.metadata.measurement_time,
                schema_version=self.metadata.schema_version,
                method_version=self.metadata.method_version,
            ))

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["points"] = [point.to_dict() for point in self.points]
        return value


def _instrument_snapshot(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result = {}
    for key in ("model", "visa_address"):
        item = value.get(key)
        if item is not None:
            result[key] = str(item)
    return result


@dataclass(frozen=True)
class CableLossPoint:
    frequency_hz: float
    # 保留旧位置参数；线损结果没有 dBm 输出功率，这两个字段应为 None。
    path1_output_power_dbm: float | None = None
    path2_output_power_dbm: float | None = None
    path1_loss_db: float = 0.0
    path2_loss_db: float = 0.0
    cable_losses_db: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.frequency_hz <= 0:
            raise ValueError("frequency_hz 必须为正数")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DriverPowerMappingPoint:
    frequency_hz: float
    input_power_dbm: float
    raw_output_power_dbm: float
    compensated_output_power_dbm: float

    def __post_init__(self) -> None:
        if self.frequency_hz <= 0:
            raise ValueError("frequency_hz 必须为正数")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AmplifierScanPoint:
    frequency_hz: float
    input_power_dbm: float
    output_power_dbm: float
    gain_db: float
    dc_voltage_v: float | None = None
    dc_current_a: float | None = None
    dc_power_w: float | None = None
    efficiency_percent: float | None = None
    voltages_v: Mapping[str, float] = field(default_factory=dict)
    currents_a: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.frequency_hz <= 0:
            raise ValueError("frequency_hz 必须为正数")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompressionPoint:
    output_power_dbm: float | None = None
    input_power_dbm: float | None = None
    gain_db: float | None = None
    efficiency_percent: float | None = None
    signal_generator_power_dbm: float | None = None
    compression_db: float | None = None
    achieved: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CableLossResult(MeasurementResult):
    points: tuple[CableLossPoint, ...] = ()
    measurement_type: str = "cable_loss"


@dataclass(frozen=True)
class DriverPowerMappingResult(MeasurementResult):
    points: tuple[DriverPowerMappingPoint, ...] = ()
    measurement_type: str = "driver_power_mapping"


@dataclass(frozen=True)
class AmplifierMeasurementResult(MeasurementResult):
    points: tuple[AmplifierScanPoint, ...] = ()
    measurement_type: str = "amplifier"
    compression_points_dbm: Mapping[str, float | None] = field(default_factory=dict)
    compression_points: Mapping[str, CompressionPoint] = field(default_factory=dict)


@dataclass
class MeasurementSession:
    """一次运行的生命周期记录，不负责执行硬件动作。"""

    run_id: str
    state: MeasurementState = MeasurementState.CREATED
    started_at: datetime | None = None
    ended_at: datetime | None = None
    stop_reason: str | None = None
    error: str | None = None
    events: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def _require(self, *allowed: MeasurementState) -> None:
        if self.state not in allowed:
            names = ", ".join(item.value for item in allowed)
            raise ValueError(f"状态 {self.state.value} 不允许此操作，需要: {names}")

    def validate(self) -> None:
        self._require(MeasurementState.CREATED)
        self.state = MeasurementState.VALIDATED

    def connect(self) -> None:
        self._require(MeasurementState.VALIDATED)
        self.state = MeasurementState.CONNECTED

    def prepare(self) -> None:
        self._require(MeasurementState.CONNECTED)
        self.state = MeasurementState.PREPARED

    def power_on(self) -> None:
        self._require(MeasurementState.PREPARED)
        self.state = MeasurementState.POWERED

    def start(self) -> None:
        self._require(MeasurementState.POWERED, MeasurementState.PREPARED)
        if self.started_at is None:
            self.started_at = _utc_now()
        self.state = MeasurementState.MEASURING

    def stop(self, reason: str, *, emergency: bool = False) -> None:
        self._require(
            MeasurementState.PREPARED,
            MeasurementState.POWERED,
            MeasurementState.MEASURING,
        )
        self.state = MeasurementState.STOPPING
        self.stop_reason = ("emergency: " if emergency else "") + reason

    def complete(self) -> None:
        self._require(MeasurementState.MEASURING)
        self.state = MeasurementState.COMPLETED
        self.ended_at = _utc_now()

    def fail(self, error: BaseException | str) -> None:
        if self.state is MeasurementState.CLEANED:
            raise ValueError("已清理的会话不能标记失败")
        self.state = MeasurementState.FAILED
        self.error = str(error)
        self.ended_at = _utc_now()

    def clean(self) -> None:
        self._require(
            MeasurementState.STOPPING,
            MeasurementState.COMPLETED,
            MeasurementState.FAILED,
            MeasurementState.CREATED,
            MeasurementState.VALIDATED,
            MeasurementState.CONNECTED,
            MeasurementState.PREPARED,
            MeasurementState.POWERED,
            MeasurementState.MEASURING,
        )
        self.state = MeasurementState.CLEANED
        if self.ended_at is None:
            self.ended_at = _utc_now()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        value["started_at"] = self.started_at.isoformat() if self.started_at else None
        value["ended_at"] = self.ended_at.isoformat() if self.ended_at else None
        return value
