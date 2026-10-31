"""Explicit inputs assembled for application measurement use cases.

The request objects are deliberately independent of Qt and file-format details.
The composition root owns configuration loading, run identity, input paths and
resource ownership; a measurement use case only receives this contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Mapping

from app.cancellation import CancellationToken
from app.events import EventSink
from domain.models import RunContext
from domain.configuration.models import RunConfiguration
from application.ports.result_repository import MeasurementResultRepository
from application.ports.result_input_reader import ResultInputReader
from application.ports.measurement_session import SafetyCleanupPort
from .measurement_results import MeasurementType, freeze
from .amplifier_inputs import AmplifierMeasurementInputs

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注，避免运行时循环导入
    from application.archival_service import ResultArchivalService
    from application.run_stages import RunStageRecorder
    from instrument.ports import MeasurementInstrumentPort


@dataclass(frozen=True)
class MeasurementRequest:
    """Common, fully assembled input for one measurement execution.

    ``measurement_port`` 只是 :class:`instrument.ports.MeasurementInstrumentPort`
    的类型契约；运行时能力校验由测量服务在构造阶段完成，请求 DTO 不导入具体
    设备实现，也不把类型标注当作运行时保证。

    ``stage_recorder`` 是**必填关键字字段**：正式测量只允许在 ``runs/<run_id>/``
    运行目录内产生结果，没有阶段记录器的请求在构造期就被拒绝（D1）。
    """

    configuration: RunConfiguration
    context: RunContext
    run_directory: Path
    measurement_port: MeasurementInstrumentPort
    # 运行目录阶段记录器：结果写入当前运行并更新 manifest，因此不可省略。
    stage_recorder: "RunStageRecorder" = field(kw_only=True)
    safety_cleanup: SafetyCleanupPort | None = None
    measurement_type: MeasurementType | str = "measurement"
    configuration_snapshot: Mapping[str, Any] | None = None
    frequency_range: tuple[float, ...] = ()
    power_range: tuple[float | None, float | None, float | None] = (None, None, None)
    safety_options: Mapping[str, Any] = field(default_factory=dict)
    event_sink: EventSink | None = None
    cancellation_token: CancellationToken | None = None
    result_repository: MeasurementResultRepository | None = None
    input_reader: ResultInputReader | None = None
    # 阶段成功提交后的自动归档服务；组装层总是注入，省略时只是不生成交付产物。
    archival_service: "ResultArchivalService | None" = None
    _expected_measurement_type: ClassVar[MeasurementType | None] = None

    @property
    def run_id(self) -> str:
        return self.context.run_id

    def __post_init__(self) -> None:
        if self.stage_recorder is None:
            raise ValueError(
                "测量请求必须注入运行阶段记录器 stage_recorder："
                "没有运行目录契约的测量不属于正式测量链路"
            )
        if not str(self.measurement_type).strip():
            raise ValueError("measurement_type 不能为空")
        if not isinstance(self.measurement_type, MeasurementType):
            try:
                object.__setattr__(self, "measurement_type", MeasurementType(str(self.measurement_type)))
            except ValueError as error:
                raise ValueError(f"不支持的 measurement_type: {self.measurement_type}") from error
        if (
            self._expected_measurement_type is not None
            and self.measurement_type is not self._expected_measurement_type
        ):
            raise ValueError(
                f"{type(self).__name__} 的 measurement_type 必须是 "
                f"{self._expected_measurement_type.value}"
            )
        if not self.frequency_range:
            plan = getattr(self.configuration, "test_plan", None)
            frequencies = getattr(plan, "frequencies", ())
            if not frequencies and isinstance(self.configuration, Mapping):
                frequencies = self.configuration.get("test_frequencies", ())
            object.__setattr__(self, "frequency_range", tuple(frequencies))
        if self.power_range == (None, None, None):
            plan = getattr(self.configuration, "test_plan", None)
            if plan is None and isinstance(self.configuration, Mapping):
                source = self.configuration.get("signal_source", {})
                source = source if isinstance(source, Mapping) else {}
                values = (
                    source.get("start_power"),
                    source.get("stop_power"),
                    source.get("step"),
                )
            else:
                values = (
                    getattr(plan, "start_power", None),
                    getattr(plan, "stop_power", None),
                    getattr(plan, "power_step", None),
                )
            object.__setattr__(
                self,
                "power_range",
                values,
            )
        if self.configuration_snapshot is None:
            snapshot = getattr(self.configuration, "to_dict", None)
            if callable(snapshot):
                snapshot = snapshot()
            elif isinstance(self.configuration, Mapping):
                snapshot = dict(self.configuration)
            else:
                snapshot = {}
            object.__setattr__(self, "configuration_snapshot", snapshot)
        object.__setattr__(self, "configuration_snapshot", freeze(self.configuration_snapshot or {}))
        object.__setattr__(self, "safety_options", freeze(self.safety_options or {
            "owns_measurement_port": False,
            "close_rf_on_cleanup": True,
            "power_cleanup_required": True,
        }))


@dataclass(frozen=True)
class CableLossMeasurementRequest(MeasurementRequest):
    """Inputs for the two-step cable-loss measurement."""

    measurement_type: MeasurementType | str = MeasurementType.CABLE_LOSS
    _expected_measurement_type: ClassVar[MeasurementType] = MeasurementType.CABLE_LOSS


@dataclass(frozen=True)
class DriverPowerMappingRequest(MeasurementRequest):
    """Inputs for driver-power mapping; its cable-loss input comes from the run."""

    measurement_type: MeasurementType | str = MeasurementType.DRIVER_POWER_MAPPING
    _expected_measurement_type: ClassVar[MeasurementType] = MeasurementType.DRIVER_POWER_MAPPING


@dataclass(frozen=True)
class AmplifierMeasurementRequest(MeasurementRequest):
    """Inputs for amplifier measurement; its calibration inputs come from the run.

    ``inputs`` 是主功放唯一的正式输入表示（技术债步骤 7/8）：频率、功率扫描、压缩
    目标、衰减器、DUT 输入限制、驱动模式与 DUT 供电通道都在这里类型化表达，用例不再
    读取历史配置字典。构造期即拒绝缺失或类型不符的输入。
    """

    measurement_type: MeasurementType | str = MeasurementType.AMPLIFIER
    _expected_measurement_type: ClassVar[MeasurementType] = MeasurementType.AMPLIFIER
    inputs: AmplifierMeasurementInputs = field(kw_only=True)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.inputs, AmplifierMeasurementInputs):
            raise ValueError(
                "AmplifierMeasurementRequest.inputs 必须是 "
                f"AmplifierMeasurementInputs，收到 {type(self.inputs).__name__}"
            )
