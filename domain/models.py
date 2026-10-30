"""阶段 1 冻结的运行与方案模型。

模型不读取文件、不连接硬件，也不依赖 Qt、PyVISA 或绘图库。
这里的 ``TestPlan`` 是内部领域模型；正式配置对象由 ``domain.configuration.models``
定义，二者之间的转换留给后续领域收敛阶段。

测量结果的唯一正式契约在 ``application.dto.measurement_results``；本模块不再定义
结果模型、结果状态或结果元数据，避免同一份结果存在两套模型。
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
    power_supplies: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
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
        object.__setattr__(
            self,
            "power_supplies",
            MappingProxyType(
                {
                    str(supply_id): MappingProxyType(dict(record))
                    for supply_id, record in dict(self.power_supplies).items()
                }
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "operator": self.operator,
            "software_version": self.software_version,
            "signal_generator": dict(self.signal_generator),
            "spectrum_analyzer": dict(self.spectrum_analyzer),
            "power_supply": dict(self.power_supply),
            "power_supplies": {
                supply_id: dict(record) for supply_id, record in self.power_supplies.items()
            },
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
        supplies = _power_supplies(mapping, power_supply)
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
            # 旧字段保留：多电源之下它是“唯一那台”，没有唯一答案时为空。
            power_supply=_legacy_power_supply(supplies),
            power_supplies=supplies,
            power_channel_mapping=channels,
            wiring_confirmed=bool(wiring.get("confirmed", False)),
            notes=mapping.get("notes") or wiring.get("connection_note"),
        )


def _instrument_snapshot(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    result = {}
    for key in ("model", "visa_address"):
        item = value.get(key)
        if item is not None:
            result[key] = str(item)
    return result


def _power_supplies(
    mapping: Mapping[str, Any], legacy_power_supply: Mapping[str, Any]
) -> dict[str, dict[str, str]]:
    """把运行映射里的电源集合转成 ``supply_id -> {label, model, serial, device_key, visa_address}``。

    新快照直接读 ``instruments.power_supplies``；仍然只有单数 ``instruments.power_supply``
    的旧映射按该条目的 ``model``（旧槽位名）读成一个条目，让历史运行的上下文保持可读。
    """
    instruments = mapping.get("instruments", {})
    instruments = instruments if isinstance(instruments, Mapping) else {}
    supplies = instruments.get("power_supplies")
    result: dict[str, dict[str, str]] = {}
    if isinstance(supplies, Mapping):
        for supply_id, record in supplies.items():
            if isinstance(record, Mapping):
                result[str(supply_id)] = _supply_snapshot(record)
        return result
    if legacy_power_supply:
        model = legacy_power_supply.get("model")
        if isinstance(model, str) and model.strip():
            record = dict(legacy_power_supply)
            record.setdefault("label", model)
            record.setdefault("device_key", model)
            result[model] = _supply_snapshot(record)
    return result


def _supply_snapshot(value: Mapping[str, Any]) -> dict[str, str]:
    result = {}
    for key in ("label", "model", "serial", "device_key", "visa_address"):
        item = value.get(key)
        if item is not None:
            result[key] = str(item)
    return result


def _legacy_power_supply(supplies: Mapping[str, Mapping[str, str]]) -> dict[str, str]:
    """旧的单数 ``power_supply`` 视图：恰好一台电源时给出它的型号与地址，否则为空。"""
    if len(supplies) != 1:
        return {}
    record = next(iter(supplies.values()))
    return _instrument_snapshot(record)


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
