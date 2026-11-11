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
from domain.run.models import SHA256_PATTERN
from application.ports.result_repository import MeasurementResultRepository
from application.ports.result_input_reader import ResultInputReader
from application.ports.measurement_session import SafetyCleanupPort
from .measurement_results import DriverMappingInput, MeasurementType, freeze
from .amplifier_inputs import AmplifierMeasurementInputs

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注，避免运行时循环导入
    from application.archival_service import ResultArchivalService
    from application.ports.stage_result_repository import StageResultRepository
    from application.run_stages import RunStageRecorder
    from instrument.ports import MeasurementInstrumentPort


def coerce_input_path(value: Any, field_name: str) -> Path | None:
    """把请求中的显式输入路径规范化为 :class:`pathlib.Path`。

    显式路径是用户/页面选择的结果文件，**不**能由 ``run_id`` 推断，因此这里只做
    类型与空值校验，不查找、不扫描、也不改写调用方给出的位置：

    - 省略（``None``）表示没有显式输入，保持 ``None``；
    - 字符串去掉首尾空白后必须非空，并转成 ``Path``；
    - 空路径、目录根等没有文件名的值直接拒绝，避免把“没有选择”伪装成路径；
    - 文件是否存在、是否位于阶段目录、JSON 与结果契约由加载边界校验。

    组合根在连接仪器**之前**调用本函数，使空路径或类型错误在硬件动作前失败。
    """
    if value is None:
        return None
    if isinstance(value, Path):
        candidate = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(
                f"{field_name} 不能为空字符串；没有显式输入时应省略该字段"
            )
        candidate = Path(text)
    else:
        raise ValueError(
            f"{field_name} 必须是 pathlib.Path 或字符串，"
            f"收到 {type(value).__name__}"
        )
    if candidate == Path(".") or not candidate.name:
        raise ValueError(
            f"{field_name} 必须是结果文件路径，不能是空路径或目录根: {candidate!r}"
        )
    return candidate


@dataclass(frozen=True)
class StageResultDigest:
    """用户**选择阶段结果那一刻**观察到的文件事实。

    显式输入路径只能表达“选过哪个文件”，无法表达“选的是哪一份内容”：同目录下的
    合法结果可以被另一份同样合法的结果替换。因此显式输入还要携带选择时的
    SHA-256 与文件大小，启动时由组装层重新加载并核对；不一致就拒绝启动，而不是
    消费用户没有选择的内容。摘要与大小都来自阶段结果仓库的同一次读取，UI 不自行
    计算，也不用修改时间参与判断。
    """

    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        if not isinstance(self.sha256, str) or not SHA256_PATTERN.fullmatch(
            self.sha256
        ):
            raise ValueError("阶段结果摘要必须是 64 位小写十六进制 sha256")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("阶段结果大小必须是非负整数")


def coerce_stage_digest(
    value: Any,
    path: Path | None,
    *,
    field_name: str,
    path_name: str,
) -> StageResultDigest | None:
    """校验显式输入的选择时摘要；摘要不能脱离它所属的路径单独存在。

    省略摘要表示旧调用方只锁定路径（启动时仍会完整校验阶段契约，但不比较选择时
    的文件事实）。给出摘要时必须同时给出路径，否则“这份摘要属于哪个文件”无从谈起。
    """
    if value is None:
        return None
    if not isinstance(value, StageResultDigest):
        raise ValueError(
            f"{field_name} 必须是 StageResultDigest 或 None，"
            f"收到 {type(value).__name__}"
        )
    if path is None:
        raise ValueError(f"{field_name} 只能在同时给出 {path_name} 时使用")
    return value


@dataclass(frozen=True)
class MeasurementRequest:
    """Common, fully assembled input for one measurement execution.

    ``measurement_port`` 只是 :class:`instrument.ports.MeasurementInstrumentPort`
    的类型契约；运行时能力校验由测量服务在构造阶段完成，请求 DTO 不导入具体
    设备实现，也不把类型标注当作运行时保证。

    ``stage_recorder`` 是**必填关键字字段**：正式测量必须绑定到一次运行，由它登记
    阶段状态与结果引用；没有阶段记录器的请求在构造期就被拒绝（D1）。阶段 7 起线损、
    阶段 8 起驱动映射的正式结果正文写入独立阶段目录，运行目录只保存引用文档。

    ``cable_loss_path`` / ``driver_mapping_path`` 是用户在页面上显式选择的阶段结果
    文件。它们与 ``run_id`` 无关，也不会由运行目录推断；组装层在连接仪器前按该路径
    加载并校验输入（步骤 14、15），省略时保持 ``None`` 并沿用运行引用兼容路径。

    ``cable_loss_digest`` / ``driver_mapping_digest`` 是用户选择那一刻的文件事实
    （SHA-256 与大小）：给出时组装层必须在启动前重新加载并核对，被替换或改动的文件
    不会被消费。摘要只能与对应路径一起使用；省略摘要表示旧调用方只锁定路径。
    """

    configuration: RunConfiguration
    context: RunContext
    run_directory: Path
    measurement_port: MeasurementInstrumentPort
    # 运行目录阶段记录器：登记阶段状态与结果引用并更新 manifest，因此不可省略。
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
    # 显式输入来源（步骤 13）：页面选择的阶段结果文件，与运行身份无关。
    cable_loss_path: Path | None = None
    driver_mapping_path: Path | None = None
    # 选择时的文件事实：启动前复核同一份内容，防止“选的是 A、测的是 B”。
    cable_loss_digest: StageResultDigest | None = None
    driver_mapping_digest: StageResultDigest | None = None
    # 阶段正式结果的独立仓库：步骤 7 起线损、步骤 8 起驱动映射的结果分别写入
    # ``test_results/cable_loss/``、``test_results/driver_mapping/``，运行目录只
    # 登记引用。组装层必须为会产生正式结果的用例注入它。
    stage_result_repository: "StageResultRepository | None" = None
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
                plans = source.get("power_scan_plans")
                if isinstance(plans, list) and len(plans) == 1:
                    single = plans[0] if isinstance(plans[0], Mapping) else {}
                    values = (
                        single.get("start_power"),
                        single.get("stop_power"),
                        single.get("step"),
                    )
                else:
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
        object.__setattr__(
            self,
            "cable_loss_path",
            coerce_input_path(self.cable_loss_path, "cable_loss_path"),
        )
        object.__setattr__(
            self,
            "driver_mapping_path",
            coerce_input_path(self.driver_mapping_path, "driver_mapping_path"),
        )
        object.__setattr__(
            self,
            "cable_loss_digest",
            coerce_stage_digest(
                self.cable_loss_digest,
                self.cable_loss_path,
                field_name="cable_loss_digest",
                path_name="cable_loss_path",
            ),
        )
        object.__setattr__(
            self,
            "driver_mapping_digest",
            coerce_stage_digest(
                self.driver_mapping_digest,
                self.driver_mapping_path,
                field_name="driver_mapping_digest",
                path_name="driver_mapping_path",
            ),
        )


@dataclass(frozen=True)
class CableLossMeasurementRequest(MeasurementRequest):
    """Inputs for the two-step cable-loss measurement."""

    measurement_type: MeasurementType | str = MeasurementType.CABLE_LOSS
    _expected_measurement_type: ClassVar[MeasurementType] = MeasurementType.CABLE_LOSS


@dataclass(frozen=True)
class DriverPowerMappingRequest(MeasurementRequest):
    """Inputs for driver-power mapping.

    ``cable_loss_path`` 是页面显式选择的线损结果文件；``cable_loss_digest`` 是选择
    那一刻的 SHA-256 与文件大小；``loss_data`` 是组装层在连接仪器前按该路径加载、
    核对摘要并校验过的同一份线损文档。三者同时出现时用例直接复用 ``loss_data``，
    不再读文件；只有 ``cable_loss_path`` 而无摘要时按旧调用方处理；省略显式来源时
    仍按当前运行登记的引用读取。
    """

    measurement_type: MeasurementType | str = MeasurementType.DRIVER_POWER_MAPPING
    _expected_measurement_type: ClassVar[MeasurementType] = MeasurementType.DRIVER_POWER_MAPPING
    # 组合入口预检读取的线损文档：与 cable_loss_path 指向同一份文件，避免二次读取。
    loss_data: Mapping[str, Any] | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.loss_data is not None and not isinstance(self.loss_data, Mapping):
            raise ValueError(
                "DriverPowerMappingRequest.loss_data 必须是预检读取的线损文档（映射）"
            )


@dataclass(frozen=True)
class AmplifierCalibrationInputs:
    """本次运行已经读取并结构校验过的主功放校准输入。

    组合入口在连接仪器前必须读取 ``inputs/cable_loss.json``（以及驱动模式下的
    ``inputs/driver_mapping.json``）来完成预检。步骤 7 起线损的这些运行路径登记的是
    指向阶段目录权威结果的引用，预检按引用读取并核对阶段文件。把这些对象随请求交给
    用例，入口预检与实际测量消费的就是同一份对象：既不会重复读取同一个运行文件，
    也不会出现“入口校验的是 A、测量消费的是 B”的漂移。

    只允许组合入口在完成运行目录校验（``verified_result_path`` 与校验值比对）后填写；
    独立构造请求的调用方省略本对象，用例按运行目录自行读取。
    """

    loss_data: Mapping[str, Any]
    driver_mapping: DriverMappingInput | None = None


@dataclass(frozen=True)
class AmplifierMeasurementRequest(MeasurementRequest):
    """Inputs for amplifier measurement; its calibration inputs come from the run.

    ``inputs`` 是主功放唯一的正式输入表示（技术债步骤 7/8）：频率、功率扫描、压缩
    目标、衰减器、DUT 输入限制、驱动模式与 DUT 供电通道都在这里类型化表达，用例不再
    读取历史配置字典。构造期即拒绝缺失或类型不符的输入。

    ``calibration_inputs`` 是组合入口预检时已读取的同一份线损/驱动映射；省略时用例
    仍按当前运行目录读取，行为与旧调用方一致。

    ``cable_loss_path`` / ``driver_mapping_path`` 是显式校准输入来源，
    ``*_digest`` 是选择那一刻的文件事实；组合入口按它们加载、核对摘要并在连接仪器
    前完成结构、频率与目标网格校验，省略时保持运行目录兼容。
    """

    measurement_type: MeasurementType | str = MeasurementType.AMPLIFIER
    _expected_measurement_type: ClassVar[MeasurementType] = MeasurementType.AMPLIFIER
    inputs: AmplifierMeasurementInputs = field(kw_only=True)
    calibration_inputs: AmplifierCalibrationInputs | None = field(
        default=None, kw_only=True
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.inputs, AmplifierMeasurementInputs):
            raise ValueError(
                "AmplifierMeasurementRequest.inputs 必须是 "
                f"AmplifierMeasurementInputs，收到 {type(self.inputs).__name__}"
            )
        if self.calibration_inputs is not None and not isinstance(
            self.calibration_inputs, AmplifierCalibrationInputs
        ):
            raise ValueError(
                "AmplifierMeasurementRequest.calibration_inputs 必须是 "
                "AmplifierCalibrationInputs 或 None，收到 "
                f"{type(self.calibration_inputs).__name__}"
            )
