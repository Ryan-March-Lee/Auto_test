"""Qt-independent measurement services used by the application use cases.

These services hold the measurement orchestration that historically lived in a
root-level module. That root module has been retired in refactor step 16, so
these symbols are only importable here. Their device dependency is the explicit
:class:`instrument.ports.MeasurementInstrumentPort` protocol; the VISA adapters
stay decoupled and UI code only translates events.

设备能力边界：三个服务在构造阶段按各自实际使用的方法校验
``MeasurementInstrumentPort`` 能力（信号通路、驱动电源、DUT 电源），能力不足时
立刻抛 :class:`instrument.ports.InstrumentCapabilityError`，不接受未声明的动态
设备对象，也不会在测量途中才发现缺失方法。

安全清理边界：服务只保存注入的测量会话协议
(:class:`application.ports.measurement_session.MeasurementSessionPort`)，由组装层
把具体设备安全流程适配成 ``SafetyCleanupPort``。application 不导入
``instrument.flow`` 的具体实现；每次清理都会产生结构化 ``CleanupRecord``，
清理失败时以 ``SafetyCleanupError`` 携带记录抛出，作为明确的运行终态。

单位边界：服务只把配置与输入文档当作**字符串/JSON 边界**，在构造或每次扫描前
转换成 :mod:`domain.measurement.values` 的值对象（``Frequency`` / ``Decibels`` /
``PowerDbm`` / ``PowerWatt`` / ``Percent``），计算完成后只把数值写回结果文档，
不再向纯计算函数传递裸浮点数。
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

import numpy as np

from app.cancellation import CancellationToken
from app.events import CheckpointEvent, EventSink, MessageEvent, ProgressEvent, RealtimeDataEvent
from app.events import StoppedEvent
from application.errors import MeasurementError
from application.ports.measurement_session import (
    CleanupRecord,
    MeasurementSessionBinding,
    MeasurementSessionPort,
    SafetyCleanupError,
    SafetyCleanupPort,
)
from application.dto.amplifier_inputs import (
    AmplifierInputError,
    AmplifierMeasurementInputs,
    PowerScanSchedule,
    PowerSweep,
    SupplyChannel,
    target_input_power_grid,
)
from application.dto.measurement_results import DriverMappingInput
from domain.measurement.calculations import (
    calculate_cable_losses,
    calculate_compression_result,
    calculate_dut_input_power,
    calculate_efficiency,
    calculate_gain,
    compensate_amplifier_output_power,
    compensate_driver_output_power,
)
from domain.measurement.values import (
    CableLossTable,
    Decibels,
    Frequency,
    Percent,
    PowerDbm,
    PowerWatt,
)
from instrument.ports import (
    DRIVER_POWER_CAPABILITIES,
    DUT_POWER_CAPABILITIES,
    SIGNAL_MEASUREMENT_CAPABILITIES,
    MeasurementInstrumentPort,
    require_measurement_capabilities,
)


# 驱动映射扫描的收敛与安全边界（重构步骤 6）。
#
# 起点公式来自重构决定：最低目标输入 - 驱动功放预估增益 - 安全余量。扫描在
# 该起点向上自适应推进；源功率无论起点推算结果如何都不得越过硬件可设范围。
DRIVER_MAPPING_SAFETY_MARGIN_DB = 10.0
DRIVER_MAPPING_HIT_TOLERANCE_DB = 0.2
DRIVER_MAPPING_MAX_ATTEMPTS_PER_TARGET = 32
DRIVER_MAPPING_MIN_STEP_DB = 0.05
DRIVER_MAPPING_MAX_STEP_DB = 10.0
DRIVER_MAPPING_MIN_SLOPE = 0.05
DRIVER_MAPPING_SOURCE_MIN_DBM = -50.0
DRIVER_MAPPING_SOURCE_MAX_DBM = 20.0
_DRIVER_MAPPING_SOURCE_EPSILON_DB = 1e-9


class DriverMappingError(MeasurementError):
    """驱动映射扫描无法在源功率范围内为某个目标取得实测命中点。"""


@dataclass
class _DriverMappingProbe:
    """单个频点的一次实测：源功率、补偿后驱放输出与是否已分配给目标。

    原始点必须保留每一次测量，命中读数只标记 ``used``，不从曲线中删除；同一读数
    不能再次满足另一个目标。
    """

    source_power: PowerDbm
    output_power: PowerDbm
    used: bool = False


class ResultRepository(Protocol):
    """可选的结果仓储注入。

    ``None`` 表示调用方负责持久化，服务只返回原始负载；提供该对象时，服务在
    构造阶段校验它可写入，并把保存动作固定在构造结果里。
    """

    def save(self, result: Any) -> Any:
        """Persist a service result and return the repository-specific value."""


class NullEventSink:
    def publish(self, event: Any) -> None:
        pass


def _publish(sink: EventSink | None, event: Any) -> None:
    if sink is not None:
        sink.publish(event)


def _require_event_sink(sink: EventSink | None) -> EventSink | None:
    """校验可选事件输出；缺陷在构造时暴露，而不是测量途中。"""
    if sink is None:
        return None
    if not callable(getattr(sink, "publish", None)):
        raise TypeError("event_sink 必须提供 publish(event) 方法")
    return sink


def _require_sleep_fn(sleep: Callable[[float], None] | None) -> Callable[[float], None]:
    if sleep is None:
        return time.sleep
    if not callable(sleep):
        raise TypeError("sleep_fn 必须是可调用对象")
    return sleep


def _require_result_saver(
    repository: ResultRepository | Callable[[Any], Any] | None,
) -> Callable[[Any], Any] | None:
    """把显式注入的结果仓储解析为单一写入函数。"""
    if repository is None:
        return None
    save = getattr(repository, "save", None)
    if callable(save):
        return save
    if callable(repository):
        return repository
    raise TypeError("result_repository 必须提供 save(result) 方法或本身可调用")


def _loss_table(loss_data: Any) -> CableLossTable:
    """把线损输入文档（``{"cable_losses": {频率键: {...}}}``）解析为值对象表。

    解析发生在测量服务构造阶段：缺频率、未知线缆字段或非数值会在任何设备动作之前
    失败，不再等到扫描中途查表才暴露。
    """
    if not isinstance(loss_data, MappingABC):
        raise TypeError("线损输入必须是 Mapping")
    cable_losses = loss_data.get("cable_losses")
    if not isinstance(cable_losses, MappingABC):
        raise TypeError("线损输入缺少 cable_losses 子对象")
    return CableLossTable.from_mapping(cable_losses, source="线损输入")


def _mapping_config(config: Any) -> Mapping[str, Any]:
    """历史配置形状的服务（线损/驱动映射）仍以映射为输入契约。"""
    if not isinstance(config, MappingABC):
        raise TypeError("测量服务配置必须是 Mapping")
    return config


def _config_number(config: Mapping[str, Any], key: str, label: str) -> float:
    """读取驱动映射必需的有限数值配置；缺失/非数值在硬件动作前失败。"""
    value = config.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"驱动映射配置缺少有效的{label}: {key}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"驱动映射配置的{label}必须是有限数值: {key}={value!r}")
    return number


def _power_scan_schedule(config: Mapping[str, Any]) -> PowerScanSchedule:
    """把驱动映射配置的 ``signal_source.power_scan_plans`` 收敛为计划集合。

    驱动映射的目标网格与配置页预览、主功放正式输入共用
    :func:`target_input_power_grid`，因此这里只负责把内部映射配置逐段读成
    :class:`PowerSweep`，不另写点位算法。列表顺序就是执行顺序；空列表、非列表、
    非对象元素以及段内非数值都在构造阶段失败，不会带着猜测值打开仪器。

    正式运行配置由 :func:`application.measurements.driver_mapping._service_config`
    从 1.2 ``TestPlan.power_scan_plans`` 生成，不再写旧单段标量；这里只接受计划
    列表，避免“有列表却仍沿旧标量”的第二来源。
    """
    source = config.get("signal_source")
    if not isinstance(source, MappingABC):
        raise ValueError("驱动映射配置缺少 signal_source 功率扫描计划")
    plans = source.get("power_scan_plans")
    if not isinstance(plans, (list, tuple)):
        raise ValueError(
            "驱动映射配置缺少 signal_source.power_scan_plans 列表："
            "目标网格必须来自 DUT 功率扫描计划"
        )
    if not plans:
        raise ValueError(
            "驱动映射配置的 signal_source.power_scan_plans 至少要有一段计划"
        )
    sweeps: list[PowerSweep] = []
    for index, item in enumerate(plans):
        path = f"signal_source.power_scan_plans[{index}]"
        if not isinstance(item, MappingABC):
            raise ValueError(
                f"驱动映射配置的 {path} 必须是 "
                "{start_power, step, stop_power} 对象"
            )
        try:
            sweeps.append(
                PowerSweep(
                    start=PowerDbm(
                        _config_number(item, "start_power", f"{path} 起始功率")
                    ),
                    stop=PowerDbm(
                        _config_number(item, "stop_power", f"{path} 终止功率")
                    ),
                    step=Decibels(
                        _config_number(item, "step", f"{path} 步进")
                    ),
                )
            )
        except AmplifierInputError as error:
            # 单段参数关系错误本身不带计划下标；补上配置页使用的路径，硬件动作前
            # 的失败信息才能直接对应到具体计划行。
            raise ValueError(f"{path} {error}") from error
    return PowerScanSchedule(plans=tuple(sweeps))


class _Service:
    """测量服务基类。

    ``run_id`` 是必需的运行标识：技术债步骤 4 已删除旧入口的默认运行 ID 兜底，
    测量事件与结果登记都必须携带真实运行 ID，不再有隐式兜底。

    设备依赖是显式的 :class:`instrument.ports.MeasurementInstrumentPort`：构造
    阶段按 ``required_capabilities`` 校验能力，缺失时抛
    ``InstrumentCapabilityError``；服务不再通过 ``getattr`` 猜测缺失的可选属性。

    依赖边界：

    - 安全清理端口在 :class:`MeasurementSessionBinding` 构造时校验 ``cleanup()``；
    - 结果持久化由提交通路决定：正式用例负责写入运行目录/仓储，服务收到
      ``result_repository=None`` 时只返回原始负载；提供仓储时保存动作在构造阶段
      一次解析，测量中途不再猜测；
    - 运行 ID、配置、事件输出、睡眠函数、取消令牌和稳定延时都在构造阶段校验，
      无效依赖不会等到硬件动作之后才暴露。

    ``config`` 由各服务按自己的输入契约解释：线损/驱动映射仍是历史映射，主功放
    使用正式输入模型 :class:`AmplifierMeasurementInputs`（技术债步骤 8）。
    """

    measurement_type = "measurement"

    def __init__(self, config: Any, instrument: MeasurementInstrumentPort, *, run_id: str,
                 safety_cleanup: SafetyCleanupPort,
                 event_sink: EventSink | None = None, cancellation_token: CancellationToken | None = None,
                 result_repository: ResultRepository | Callable[[Any], Any] | None = None,
                 sleep_fn: Callable[[float], None] | None = None,
                 settle_delay_s: float = 5.0,
                 owns_measurement_port: bool = True):
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("测量服务必须提供有效运行 ID")
        if isinstance(settle_delay_s, bool) or not isinstance(settle_delay_s, (int, float)):
            raise TypeError("settle_delay_s 必须是数值")
        if not math.isfinite(settle_delay_s) or settle_delay_s < 0:
            raise ValueError("settle_delay_s 必须是有限非负数")
        if cancellation_token is not None and not callable(
            getattr(cancellation_token, "raise_if_cancelled", None)
        ):
            raise TypeError("cancellation_token 必须提供 raise_if_cancelled() 方法")
        self.config = config
        self.run_id = run_id
        # 服务只依赖测量会话协议：设备端口与安全清理端口在同一生命周期边界内，
        # 具体设备 flow 由组装层适配，application 不感知。
        self.inst_ctrl: MeasurementInstrumentPort = require_measurement_capabilities(
            instrument,
            requirement=type(self).__name__,
            capabilities=self.required_capabilities(config),
        )
        self.session: MeasurementSessionPort = MeasurementSessionBinding(
            instrument=self.inst_ctrl, safety_cleanup=safety_cleanup
        )
        self.event_sink = _require_event_sink(event_sink)
        self.cancellation_token = cancellation_token or CancellationToken()
        self.result_repository = result_repository
        self._save_result = _require_result_saver(result_repository)
        self.sleep_fn = _require_sleep_fn(sleep_fn)
        self.settle_delay_s = settle_delay_s
        self.owns_measurement_port = owns_measurement_port
        self.last_cleanup_record: CleanupRecord | None = None

    @classmethod
    def required_capabilities(cls, config: Any) -> tuple[str, ...]:
        """返回本服务实际使用的设备能力；子类按测量类型追加。"""
        return SIGNAL_MEASUREMENT_CAPABILITIES

    def _check_cancelled(self) -> None:
        self.cancellation_token.raise_if_cancelled()

    def _message(self, message: str) -> None:
        _publish(self.event_sink, MessageEvent(self.run_id, message=message))

    def _progress(self, fraction: float, stage: str) -> None:
        _publish(self.event_sink, ProgressEvent(self.run_id, fraction=fraction, stage=stage))

    def _save(self, result: Any) -> Any:
        if self._save_result is None:
            return result
        return self._save_result(result)

    def _cleanup(self, power_cleanup: Callable[[], None] | None = None) -> CleanupRecord:
        """执行安全清理并保存结构化记录；失败记录同样保留在服务上。"""
        try:
            record = self.session.cleanup(
                power_cleanup=power_cleanup,
                release_resources=self.owns_measurement_port,
            )
        except SafetyCleanupError as error:
            self.last_cleanup_record = error.record
            raise
        self.last_cleanup_record = record
        return record


class CableLossService(_Service):
    """Measure both cable paths and apply the single cable-loss calculation."""

    measurement_type = "cable_loss"

    def __init__(self, config: Mapping[str, Any], instrument: MeasurementInstrumentPort, **kwargs: Any):
        super().__init__(_mapping_config(config), instrument, **kwargs)
        self.attenuator_value = Decibels.parse(config["attenuator"]["type"])
        self._path1_losses: dict[float, float] = {}
        self._path2_losses: dict[float, float] = {}

    def measure_path_loss(self, frequency: float) -> float:
        self._check_cancelled()
        self.inst_ctrl.set_frequency(frequency)
        self.inst_ctrl.set_power(0)
        self.inst_ctrl.rf_output_on()
        try:
            self.inst_ctrl.set_center_frequency(frequency)
            self.inst_ctrl.set_span(10)
            value = self.inst_ctrl.measure_power_with_average()
            self.sleep_fn(1)
            return abs(0 - value)
        finally:
            self.inst_ctrl.rf_output_off()

    @property
    def path1_losses(self) -> dict[float, float]:
        """Return the path-1 measurements collected so far."""
        return dict(self._path1_losses)

    def partial_payload(self) -> dict[str, Any]:
        """停止时返回已经完整测完两条路径的频点。

        线损只有在同一频点的路径 1 与路径 2 都完成后才有正式线缆损耗；停在
        路径 1 或路径 2 中途时，没有完整频点就返回空 ``cable_losses``。
        """
        frequencies = tuple(
            Frequency.from_ghz(value) for value in self.config["test_frequencies"]
        )
        losses = CableLossTable(
            {
                frequency: calculate_cable_losses(
                    Decibels(self._path1_losses[frequency.ghz]),
                    Decibels(self._path2_losses[frequency.ghz]),
                    self.attenuator_value,
                )
                for frequency in frequencies
                if frequency.ghz in self._path1_losses
                and frequency.ghz in self._path2_losses
            }
        )
        return {
            "attenuator_value": self.attenuator_value.db,
            "cable_losses": losses.to_document(),
        }

    def cleanup(self) -> CleanupRecord:
        """Expose the common safety cleanup boundary to application callers."""
        return self._cleanup()

    def run(self, *, path2_confirmed: bool = True) -> dict[str, Any]:
        frequencies = tuple(
            Frequency.from_ghz(value) for value in self.config["test_frequencies"]
        )
        path1: dict[float, float] = dict(self._path1_losses)
        path2: dict[float, float] = dict(self._path2_losses)
        waiting_for_path2 = False
        try:
            if not path1:
                _publish(self.event_sink, CheckpointEvent(self.run_id, checkpoint="path1", prompt="请连接路径1"))
                for index, frequency in enumerate(frequencies):
                    path1[frequency.ghz] = self.measure_path_loss(frequency.ghz)
                    self._path1_losses = dict(path1)
                    profile = calculate_cable_losses(
                        Decibels(path1[frequency.ghz]),
                        Decibels(path1[frequency.ghz]),
                        self.attenuator_value,
                    )
                    _publish(
                        self.event_sink,
                        RealtimeDataEvent(
                            self.run_id,
                            measurement=self.measurement_type,
                            data={
                                "frequency": frequency.ghz,
                                "path": 1,
                                "path1_loss": path1[frequency.ghz],
                                "cable_losses": profile.to_mapping(),
                            },
                        ),
                    )
                    self._progress((index + 1) / (2 * len(frequencies)), "path1")
            if not path2_confirmed:
                waiting_for_path2 = True
                _publish(self.event_sink, CheckpointEvent(self.run_id, checkpoint="path2", prompt="请连接路径2"))
                return {"path1_losses": path1, "path2_losses": path2, "status": "waiting"}
            # 路径2的确认由GUI在第一次运行返回waiting后完成。
            # 继续运行时不能再次发布检查点，否则GUI会把同一个确认事件
            # 当成新的弹窗请求，而路径2测量已经开始执行。
            for index, frequency in enumerate(frequencies):
                path2[frequency.ghz] = self.measure_path_loss(frequency.ghz)
                self._path2_losses = dict(path2)
                profile = calculate_cable_losses(
                    Decibels(path1[frequency.ghz]),
                    Decibels(path2[frequency.ghz]),
                    self.attenuator_value,
                )
                _publish(
                    self.event_sink,
                    RealtimeDataEvent(
                        self.run_id,
                        measurement=self.measurement_type,
                        data={
                            "frequency": frequency.ghz,
                            "path": 2,
                            "path1_loss": path1[frequency.ghz],
                            "path2_loss": path2[frequency.ghz],
                            "cable_losses": profile.to_mapping(),
                        },
                    ),
                )
                self._progress(0.5 + (index + 1) / (2 * len(frequencies)), "path2")
            losses = CableLossTable(
                {
                    frequency: calculate_cable_losses(
                        Decibels(path1[frequency.ghz]),
                        Decibels(path2[frequency.ghz]),
                        self.attenuator_value,
                    )
                    for frequency in frequencies
                }
            )
            result = {"attenuator_value": self.attenuator_value.db,
                      "cable_losses": losses.to_document()}
            self._save(result)
            self._progress(1.0, "complete")
            return result
        finally:
            if not waiting_for_path2:
                self._cleanup()


class DriverPowerMappingService(_Service):
    """自适应测量驱放曲线，为每个目标输入功率找到独立实测命中点。

    起点与推进规则（重构步骤 6）：

    - 每个频点从 ``最低目标输入 - 预估增益 - 10 dB`` 起步（源功率始终限制在
      -50～20 dBm）；
    - 依据当前实测输出与目标的误差调整源功率，接近目标时步长自然收缩；
    - 已形成目标两侧区间后改用区间二分，不再按固定步长扫完整范围；
    - 每个目标要求一次独立实测输出落在 ±0.2 dB 内，已命中读数不会再次分配给
      另一个目标；目标间隔小于容差时仍会新测一个源功率；
    - 每个目标有固定测量上限；到界、无有效进展或达到上限仍未命中时直接失败，
      不返回可供正式测试使用的完整映射；
    - 停止请求在测量返回、命中分配和完整结果提交三个边界都会生效，被取消的频点
      不计入完整映射。

    结果包含两层：``raw_points`` 保留每一次实测（审计用），``target_mapping`` 按
    目标 DUT 输入功率给出正式测试消费的目标表。目标表的每一行都直接来自该目标的
    独立实测命中点，因此目标网格必定被完整覆盖，也不会出现外推；主键始终是目标
    DUT 输入功率，计划编号不会进入结果 schema。
    """

    measurement_type = "driver_power_mapping"

    def __init__(self, config: Mapping[str, Any], instrument: MeasurementInstrumentPort,
                 loss_data: Mapping[str, Any], **kwargs: Any):
        super().__init__(_mapping_config(config), instrument, **kwargs)
        # 输入文档在构造阶段收敛为值对象：缺失频率、未知字段或非数值在硬件动作前失败。
        # 服务只保留解析后的表，不再同时持有原始文档副本。
        self.loss_table = _loss_table(loss_data)
        self.attenuator_value = Decibels.parse(config["attenuator"]["type"])
        self.gain_estimate = Decibels(
            _config_number(config, "driver_gain_estimate_db", "驱动功放预估增益")
        )
        # 目标网格与主功放正式测试共用同一个纯计算入口（步骤 1）：扫描结束与 DUT
        # 最大输入取较小值，非整步上限补入最后一点。
        self.target_powers = self._target_powers(config)
        # 每个完整频点的原始映射点；停止时只保存已经完整扫完的频点。
        self._mapping: dict[str, list[dict[str, float]]] = {}

    @staticmethod
    def _target_powers(config: Mapping[str, Any]) -> tuple[PowerDbm, ...]:
        """配置计划 -> 全部频点共用的目标 DUT 输入功率网格。

        每个频点都复用构造期算出的同一份 ``target_powers``：段数、顺序、公共边界
        去重和 DUT 上限截断都只发生一次，不会因为频点不同而漂移。空档/重叠由共享
        入口拒绝，因此不会带着不完整的计划进入硬件动作。
        """
        schedule = _power_scan_schedule(config)
        limit = PowerDbm(
            _config_number(config, "max_dut_input_power", "DUT 最大输入功率")
        )
        return target_input_power_grid(schedule, limit)

    def partial_payload(self) -> dict[str, Any]:
        """返回已经完整扫完的驱动映射频点（原始实测点）。

        停止路径只保存审计数据，不生成目标表：只有完整覆盖全部目标点的映射才能
        作为正式输入，避免把“部分频点”伪装成可执行的目标网格。
        """
        return {
            "raw_points": {
                key: [dict(point) for point in points]
                for key, points in self._mapping.items()
            }
        }

    @classmethod
    def required_capabilities(cls, config: Mapping[str, Any]) -> tuple[str, ...]:
        return SIGNAL_MEASUREMENT_CAPABILITIES + DRIVER_POWER_CAPABILITIES

    def run(self) -> dict[str, Any]:
        mapping = self._mapping
        # 目标表只登记完整扫完的频点；键是目标 DUT 输入功率，不是源功率。
        target_mapping: dict[str, list[dict[str, float]]] = {}
        try:
            # 停止请求优先于任何设备动作：已经取消的运行不再给驱放上电。
            self._check_cancelled()
            self.inst_ctrl.setup_driver_amplifier_power()
            self.inst_ctrl.power_on_driver()
            frequencies = tuple(
                Frequency.from_ghz(value) for value in self.config["test_frequencies"]
            )
            for frequency_index, frequency in enumerate(frequencies):
                self._check_cancelled()
                raw_rows, target_rows = self._measure_frequency(
                    frequency,
                    frequency_index=frequency_index,
                    frequency_count=len(frequencies),
                )
                # 两个视图同进同退：一个频点只有完整覆盖全部目标后才会被登记，
                # 取消或失败不会留下“有原始点但没有目标表”的半成品。
                mapping[frequency.key] = raw_rows
                target_mapping[frequency.key] = target_rows
            # 提交完整结果前的最后一道门：最后一个频点完成后到达的停止请求不能被
            # 当成正常完成，映射不得进入正式测试输入。
            self._check_cancelled()
            result = {
                "raw_points": {
                    key: [dict(point) for point in points]
                    for key, points in mapping.items()
                },
                "target_mapping": {
                    key: [dict(point) for point in points]
                    for key, points in target_mapping.items()
                },
            }
            self._save(result)
            return result
        finally:
            self._cleanup(power_cleanup=self.inst_ctrl.power_off_driver)

    # -- 单频点扫描 --------------------------------------------------------

    def _measure_frequency(
        self,
        frequency: Frequency,
        *,
        frequency_index: int,
        frequency_count: int,
    ) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
        """扫描一个频点，返回（原始实测点, 目标表）。

        目标表与原始点来自同一次扫描：每个目标命中后立即把该命中点写成一行
        ``target_input_power_dbm / source_power_dbm / driver_output_power_dbm``。
        命中容差意味着实测输出不一定精确等于目标值，因此不能靠“从原始曲线反插值”
        生成目标表（曲线端点可能覆盖不到目标），直接采用实际命中点既保证覆盖全部
        目标，也保证源功率一定落在已实测范围内。
        """
        probes: list[_DriverMappingProbe] = []
        target_rows: list[dict[str, float]] = []
        self.inst_ctrl.set_power(-40)
        self.inst_ctrl.set_frequency(frequency.ghz)
        self.inst_ctrl.set_center_frequency(frequency.ghz)
        self.inst_ctrl.set_span(10)
        self.inst_ctrl.rf_output_on()
        try:
            self._measure_probe(
                frequency,
                self._initial_source_power(),
                probes,
                frequency_index=frequency_index,
                frequency_count=frequency_count,
                completed_targets=0,
            )
            for target_index, target in enumerate(self.target_powers):
                self._check_cancelled()
                hit = self._find_unused_hit(probes, target.dbm)
                if hit is None:
                    hit = self._search_target(
                        frequency,
                        target.dbm,
                        probes,
                        frequency_index=frequency_index,
                        frequency_count=frequency_count,
                        completed_targets=target_index,
                    )
                # 分配命中是状态转移：读数返回后到达的停止请求不得让最后一个目标
                # 以“已完成”的身份进入结果。
                self._check_cancelled()
                hit.used = True
                target_rows.append(
                    {
                        "target_input_power_dbm": target.dbm,
                        "source_power_dbm": hit.source_power.dbm,
                        "driver_output_power_dbm": hit.output_power.dbm,
                    }
                )
                self._progress(
                    (
                        frequency_index
                        + (target_index + 1) / len(self.target_powers)
                    )
                    / frequency_count,
                    "scan",
                )
            return [
                {
                    "source_power_dbm": probe.source_power.dbm,
                    "driver_output_power_dbm": probe.output_power.dbm,
                    "gain_db": probe.output_power.dbm - probe.source_power.dbm,
                }
                for probe in probes
            ], target_rows
        finally:
            self.inst_ctrl.rf_output_off()

    def _initial_source_power(self) -> float:
        """最低目标输入 - 预估增益 - 安全余量，并收敛到硬件可设范围。"""
        value = (
            self.target_powers[0].dbm
            - self.gain_estimate.db
            - DRIVER_MAPPING_SAFETY_MARGIN_DB
        )
        return min(
            max(value, DRIVER_MAPPING_SOURCE_MIN_DBM),
            DRIVER_MAPPING_SOURCE_MAX_DBM,
        )

    def _measure_probe(
        self,
        frequency: Frequency,
        source_power: float,
        probes: list[_DriverMappingProbe],
        *,
        frequency_index: int,
        frequency_count: int,
        completed_targets: int,
    ) -> _DriverMappingProbe:
        self._check_cancelled()
        source = PowerDbm(source_power)
        self.inst_ctrl.set_power(source.dbm)
        self.sleep_fn(self.settle_delay_s)
        raw = self.inst_ctrl.measure_power_with_average()
        output = compensate_driver_output_power(
            PowerDbm(raw),
            frequency,
            self.loss_table,
            self.attenuator_value,
        )
        probe = _DriverMappingProbe(source, output)
        probes.append(probe)
        sweep_data = {
            "input_power_sg": [point.source_power.dbm for point in probes],
            "output_power_driver": [point.output_power.dbm for point in probes],
            "gain": [
                point.output_power.dbm - point.source_power.dbm for point in probes
            ],
        }
        _publish(
            self.event_sink,
            RealtimeDataEvent(
                self.run_id,
                measurement=self.measurement_type,
                data={
                    "frequency": frequency.ghz,
                    "sweep_data": sweep_data,
                    "current_point": {
                        "input_power": source.dbm,
                        "output_power": output.dbm,
                    },
                },
            ),
        )
        self._progress(
            (
                frequency_index
                + completed_targets / len(self.target_powers)
            )
            / frequency_count,
            "scan",
        )
        # 读数已经产生，但停止请求在测量期间到达时必须在这里生效：命中判定与
        # 后续目标都不得越过取消边界，当前频点也不会被计入完整结果。
        self._check_cancelled()
        return probe

    def _search_target(
        self,
        frequency: Frequency,
        target_dbm: float,
        probes: list[_DriverMappingProbe],
        *,
        frequency_index: int,
        frequency_count: int,
        completed_targets: int,
    ) -> _DriverMappingProbe:
        attempts = 0
        tolerance = DRIVER_MAPPING_HIT_TOLERANCE_DB + _DRIVER_MAPPING_SOURCE_EPSILON_DB
        while attempts < DRIVER_MAPPING_MAX_ATTEMPTS_PER_TARGET:
            source = self._next_source(frequency, target_dbm, probes)
            probe = self._measure_probe(
                frequency,
                source,
                probes,
                frequency_index=frequency_index,
                frequency_count=frequency_count,
                completed_targets=completed_targets,
            )
            attempts += 1
            if abs(probe.output_power.dbm - target_dbm) <= tolerance:
                return probe
        lower, upper = self._bracket(probes, target_dbm)
        if lower is not None and upper is not None:
            interval = (
                f"目标已被源功率 [{lower.source_power.dbm}, "
                f"{upper.source_power.dbm}] dBm 包围"
            )
        else:
            interval = "目标仍未被实测输出包围"
        raise DriverMappingError(
            f"驱动映射 {frequency.key} GHz 在 "
            f"{DRIVER_MAPPING_MAX_ATTEMPTS_PER_TARGET} 次测量内未为 "
            f"{target_dbm} dBm 目标找到 ±{DRIVER_MAPPING_HIT_TOLERANCE_DB} dB "
            f"命中点（{interval}）"
        )

    def _next_source(
        self,
        frequency: Frequency,
        target_dbm: float,
        probes: list[_DriverMappingProbe],
    ) -> float:
        lower, upper = self._bracket(probes, target_dbm)
        candidates: list[float] = []
        if lower is not None and upper is not None:
            # 目标两侧都有实测点：在区间内二分，接近目标时区间会自然收缩。
            middle = (lower.source_power.dbm + upper.source_power.dbm) / 2.0
            candidates = [
                middle,
                (lower.source_power.dbm + middle) / 2.0,
                (middle + upper.source_power.dbm) / 2.0,
            ]
        else:
            if lower is not None:
                reference = lower
            elif upper is not None:
                reference = upper
            else:
                reference = min(
                    probes,
                    key=lambda point: abs(point.output_power.dbm - target_dbm),
                )
            error = target_dbm - reference.output_power.dbm
            step = abs(error) / self._slope_hint(probes)
            step = min(
                max(step, DRIVER_MAPPING_MIN_STEP_DB),
                DRIVER_MAPPING_MAX_STEP_DB,
            )
            direction = 1.0 if error > 0 else -1.0
            candidates = [
                reference.source_power.dbm + direction * step * factor
                for factor in (1.0, 0.5, 1.5)
            ]
        for candidate in candidates:
            bounded = min(
                max(candidate, DRIVER_MAPPING_SOURCE_MIN_DBM),
                DRIVER_MAPPING_SOURCE_MAX_DBM,
            )
            if not self._is_measured(probes, bounded):
                return bounded
        raise DriverMappingError(
            self._no_progress_reason(frequency, target_dbm, candidates)
        )

    @staticmethod
    def _no_progress_reason(
        frequency: Frequency, target_dbm: float, candidates: list[float]
    ) -> str:
        """区分“源功率到界”和一般无进展，两种失败都必须可诊断。"""
        boundary: str | None = None
        for candidate in candidates:
            if candidate <= (
                DRIVER_MAPPING_SOURCE_MIN_DBM + _DRIVER_MAPPING_SOURCE_EPSILON_DB
            ):
                boundary = f"下界 {DRIVER_MAPPING_SOURCE_MIN_DBM:g} dBm"
                break
            if candidate >= (
                DRIVER_MAPPING_SOURCE_MAX_DBM - _DRIVER_MAPPING_SOURCE_EPSILON_DB
            ):
                boundary = f"上界 {DRIVER_MAPPING_SOURCE_MAX_DBM:g} dBm"
                break
        if boundary is not None:
            return (
                f"驱动映射 {frequency.key} GHz 的 {target_dbm} dBm 目标在源功率"
                f"{boundary} 已无有效进展：目标超出可设源功率范围，"
                "未生成完整映射"
            )
        return (
            f"驱动映射 {frequency.key} GHz 无法在源功率 "
            f"[{DRIVER_MAPPING_SOURCE_MIN_DBM}, {DRIVER_MAPPING_SOURCE_MAX_DBM}] dBm "
            f"内为 {target_dbm} dBm 目标找到新的可测点（无有效进展）"
        )

    @staticmethod
    def _is_measured(probes: list[_DriverMappingProbe], source_power: float) -> bool:
        return any(
            abs(point.source_power.dbm - source_power)
            <= _DRIVER_MAPPING_SOURCE_EPSILON_DB
            for point in probes
        )

    @staticmethod
    def _bracket(
        probes: list[_DriverMappingProbe], target_dbm: float
    ) -> tuple[_DriverMappingProbe | None, _DriverMappingProbe | None]:
        lower: _DriverMappingProbe | None = None
        upper: _DriverMappingProbe | None = None
        for probe in probes:
            if probe.output_power.dbm < target_dbm:
                if (
                    lower is None
                    or probe.output_power.dbm > lower.output_power.dbm
                    or (
                        probe.output_power.dbm == lower.output_power.dbm
                        and probe.source_power.dbm > lower.source_power.dbm
                    )
                ):
                    lower = probe
            elif probe.output_power.dbm > target_dbm:
                if (
                    upper is None
                    or probe.output_power.dbm < upper.output_power.dbm
                    or (
                        probe.output_power.dbm == upper.output_power.dbm
                        and probe.source_power.dbm < upper.source_power.dbm
                    )
                ):
                    upper = probe
        return lower, upper

    def _slope_hint(self, probes: list[_DriverMappingProbe]) -> float:
        ordered = sorted(probes, key=lambda point: point.source_power.dbm)
        for left, right in reversed(list(zip(ordered, ordered[1:]))):
            delta_source = right.source_power.dbm - left.source_power.dbm
            if delta_source <= _DRIVER_MAPPING_SOURCE_EPSILON_DB:
                continue
            slope = (
                right.output_power.dbm - left.output_power.dbm
            ) / delta_source
            if slope > 0.0:
                return max(slope, DRIVER_MAPPING_MIN_SLOPE)
        return max(abs(self.gain_estimate.db), DRIVER_MAPPING_MIN_SLOPE)

    @staticmethod
    def _find_unused_hit(
        probes: list[_DriverMappingProbe], target_dbm: float
    ) -> _DriverMappingProbe | None:
        tolerance = DRIVER_MAPPING_HIT_TOLERANCE_DB + _DRIVER_MAPPING_SOURCE_EPSILON_DB
        candidates = [
            probe
            for probe in probes
            if not probe.used
            and abs(probe.output_power.dbm - target_dbm) <= tolerance
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda point: abs(point.output_power.dbm - target_dbm),
        )


class AmplifierMeasurementService(_Service):
    """Run the main amplifier sweep, including protection and compression logic.

    输入是技术债步骤 7 定义的正式模型 :class:`AmplifierMeasurementInputs`：服务不再
    读取历史配置字典，频率、扫描点、压缩目标、衰减器、DUT 输入限制、驱动模式与供电
    通道全部来自同一对象的类型化字段。

    效率计算只读用户指定的漏极路由（步骤 10）：每条漏极的电压 × 电流相加得到
    ``dc_power``（多漏极就是把多台漏极的直流功率求和），其余 DUT 通道仍逐通道采样
    并写入 ``voltages`` / ``currents``，但不计入效率。漏极不参与上电、间隔或关断，
    只是测量数据来源。
    """

    measurement_type = "amplifier_measurement"

    def __init__(self, inputs: AmplifierMeasurementInputs, instrument: MeasurementInstrumentPort,
                 loss_data: Mapping[str, Any],
                 driver_input: DriverMappingInput | None = None,
                 *, configuration_snapshot: Mapping[str, Any] | None = None, **kwargs: Any):
        if not isinstance(inputs, AmplifierMeasurementInputs):
            raise TypeError(
                "AmplifierMeasurementService 输入必须是 AmplifierMeasurementInputs，"
                f"收到 {type(inputs).__name__}"
            )
        # 主功放测量需要效率，因此至少要有一条漏极。配置/运行校验（domain）已经在
        # 创建运行前拦截；这里再守一道，保证直接构造服务时也不会静默把 dc_power 算成
        # 0。漏极只用于读数，不会因此改变上电序列。
        if not inputs.drain_channel.is_configured:
            raise AmplifierInputError(
                "主功放测量必须指定至少一个漏极通道（drain_channel）："
                "效率计算读取这些物理通道的电压/电流并求和"
            )
        self.inputs = inputs
        super().__init__(inputs, instrument, **kwargs)
        # 校准输入在构造阶段收敛为值对象，缺字段或错误单位不会等到扫描中途才暴露；
        # 服务只保留解析后的表，不再同时持有原始文档副本。
        self.loss_table = _loss_table(loss_data)
        # 驱动模式只消费目标网格（目标 DUT 输入 -> 源功率 + 插值驱放输出）；
        # 原始曲线留在结果文档中供审计，不再参与主功放扫描。
        self.driver_input = driver_input
        # 正式输入已经支持多段计划（步骤 4.1），但无驱动分支仍按单段计划的点列设置
        # 信号源功率；把目标 DUT 输入功率按线损反算成源功率属于步骤 4.3。在反算接入
        # 之前拒绝多段计划，避免只跑第一段却表现得像跑完了全部计划。构造期失败，
        # 因此这里不会产生任何仪器动作。
        if driver_input is None and len(inputs.power_scan_schedule.plans) != 1:
            raise AmplifierInputError(
                "无驱动模式目前只支持单段 DUT 功率扫描计划："
                "多段计划的目标输入功率反算尚未接入正式测量（步骤 4.3），"
                f"当前计划为 {len(inputs.power_scan_schedule.plans)} 段"
            )
        self.attenuator_value = inputs.attenuator
        self.compression_value = inputs.compression_target
        self.configuration_snapshot = dict(configuration_snapshot or {})
        # 完整频点的结果；停止时只保存已经完整扫完的频点。
        self._results: dict[str, Any] = {}

    def partial_payload(self) -> dict[str, Any]:
        """返回已经完整扫完的主功放频点结果。"""
        return {
            "results": dict(self._results),
            "configuration_snapshot": dict(self.configuration_snapshot),
        }

    @classmethod
    def required_capabilities(cls, inputs: Any) -> tuple[str, ...]:
        """主功放扫描必用 DUT 电源能力；驱动模式开启时才要求驱动电源能力。

        输入契约与构造器一致：独立调用本方法（例如装配层查询能力）也必须传入正式
        输入模型，不能因为类型不符而静默少报驱动电源能力。
        """
        if not isinstance(inputs, AmplifierMeasurementInputs):
            raise TypeError(
                "AmplifierMeasurementService.required_capabilities 的输入必须是 "
                f"AmplifierMeasurementInputs，收到 {type(inputs).__name__}"
            )
        capabilities = SIGNAL_MEASUREMENT_CAPABILITIES + DUT_POWER_CAPABILITIES
        if inputs.driver_enabled:
            capabilities += DRIVER_POWER_CAPABILITIES
        return capabilities

    def perform_power_sweep(self, frequency: float) -> dict[str, Any]:
        compression_value = self.compression_value
        attenuator_value = self.attenuator_value
        frequency_value = Frequency.from_ghz(frequency)
        maximum = self.inputs.max_dut_input_power.dbm
        self.inst_ctrl.set_power(-40)
        self.inst_ctrl.set_frequency(frequency)
        self.inst_ctrl.set_center_frequency(frequency)
        self.inst_ctrl.set_span(10)
        supplies = self.inputs.dut_supplies()
        # 效率只读用户指定的漏极路由（来自冻结的 test_plan.drain_channel）。多条
        # 漏极各自取电压 × 电流后相加；其它 DUT 通道照常逐通道采样并写入结果，但
        # 不参与 dc_power 求和。
        drain_routes = frozenset(self.inputs.drain_channel.routes)
        if not drain_routes:  # 构造阶段已拒绝；保留防御，避免静默算出 0 效率
            raise AmplifierInputError(
                "主功放测量必须指定至少一个漏极通道（drain_channel）："
                "效率计算读取这些物理通道的电压/电流并求和"
            )
        data = {"input_power_dut": [], "output_power_dut": [], "gain": [], "sg_power": [],
                "voltages": [], "currents": [], "dc_power": [], "efficiency": []}
        # 驱动模式：正式扫描直接按目标网格执行，源功率和 DUT 输入功率都来自映射
        # 结果，不再对原始曲线做二次插值。无驱动模式仍以单段计划（构造期已保证只有
        # 一段）的点列作为信号源扫描点，逐点用 DUT 输入保护拦截越限；把目标 DUT 输入
        # 功率按线损反算成源功率属于步骤 4.3，届时这里改读
        # ``self.inputs.target_input_powers`` 并在 RF 输出前预检源功率范围。
        if self.driver_input is None:
            scan_points = tuple(
                (point, None, None)
                for point in self.inputs.power_scan_schedule.plans[0].points()
            )
        else:
            targets = sorted(
                (
                    point
                    for point in self.driver_input.target_points
                    if Frequency.from_document_key(point.frequency_ghz)
                    == frequency_value
                ),
                key=lambda point: point.target_input_power_dbm,
            )
            if not targets:
                raise AmplifierInputError(
                    f"驱动映射目标表缺少测试频率 {frequency_value.key} 的目标点"
                )
            scan_points = tuple(
                (
                    PowerDbm(point.target_input_power_dbm),
                    PowerDbm(point.source_power_dbm),
                    PowerDbm(point.driver_output_power_dbm),
                )
                for point in targets
            )
        self.inst_ctrl.rf_output_on()
        try:
            for index, (sweep_point, driver_source, driver_output) in enumerate(scan_points):
                self._check_cancelled()
                if driver_output is not None and driver_source is not None:
                    sg_power = driver_source
                    dut_input = driver_output
                else:
                    sg_power = sweep_point
                    dut_input = calculate_dut_input_power(
                        sg_power,
                        frequency_value,
                        self.loss_table,
                        None,
                    )
                if dut_input.dbm > maximum:
                    self._message(
                        f"DUT 输入保护触发: {frequency} GHz, {dut_input.dbm:.2f} dBm > {maximum:.2f} dBm"
                    )
                    _publish(
                        self.event_sink,
                        StoppedEvent(self.run_id, reason="DUT 输入功率保护", emergency=True),
                    )
                    break
                self.inst_ctrl.set_power(sg_power.dbm)
                self.sleep_fn(self.settle_delay_s)
                raw = self.inst_ctrl.measure_power_with_average()
                output = compensate_amplifier_output_power(
                    PowerDbm(raw),
                    frequency_value,
                    self.loss_table,
                    attenuator_value,
                )
                voltage, current = {}, {}
                drain_power: dict[SupplyChannel, float] = {}
                for supply_id, channels in supplies.items():
                    for channel in channels:
                        route = SupplyChannel(supply_id=supply_id, channel=channel)
                        measured_voltage = self.inst_ctrl.read_voltage(supply_id, channel)
                        measured_current = self.inst_ctrl.read_current(supply_id, channel)
                        # 结果键沿用历史 ``supply_id_channel`` 拼接格式（结果兼容）；
                        # 但物理路由选择只比较 SupplyChannel：路由两段都允许下划线，
                        # 不同路由可能拼出同一个键，绝不能用键反推漏极。
                        key = f"{supply_id}_{channel}"
                        voltage[key] = measured_voltage
                        current[key] = measured_current
                        if route in drain_routes:
                            drain_power[route] = measured_voltage * measured_current
                # dc_power 的语义是「全部漏极的直流功率之和」：其余通道只进结果列。
                missing = drain_routes.difference(drain_power)
                if missing:
                    tokens = "、".join(sorted(route.token for route in missing))
                    raise AmplifierInputError(
                        "漏极通道 {routes} 不在此次 DUT 采样通道中，无法计算效率".format(
                            routes=tokens
                        )
                    )
                dc_power = sum(drain_power.values())
                gain = calculate_gain(output, dut_input)
                efficiency = calculate_efficiency(output, PowerWatt(dc_power))
                data["sg_power"].append(sg_power.dbm)
                data["input_power_dut"].append(dut_input.dbm)
                data["output_power_dut"].append(output.dbm)
                data["gain"].append(gain.db)
                data["voltages"].append(voltage); data["currents"].append(current)
                data["dc_power"].append(dc_power); data["efficiency"].append(efficiency.percent)
                _publish(self.event_sink, RealtimeDataEvent(self.run_id, measurement=self.measurement_type,
                    data={"frequency": frequency, "sweep_data": {key: list(value) for key, value in data.items()},
                          "current_point": {"sg_power": sg_power.dbm, "dut_input_power": dut_input.dbm,
                                            "output_power": output.dbm, "gain": gain.db,
                                            "efficiency": data["efficiency"][-1],
                                            "dc_power": dc_power}}))
                self._progress((index + 1) / max(len(scan_points), 1), "scan")
                if index >= 3 and (
                    Decibels(float(np.mean(data["gain"][:3]))) - gain
                ).db >= compression_value.db:
                    break
        finally:
            self.inst_ctrl.rf_output_off()
        result = calculate_compression_result(
            [Decibels(value) for value in data["gain"]],
            [PowerDbm(value) for value in data["input_power_dut"]],
            [PowerDbm(value) for value in data["output_power_dut"]],
            [Percent(value) for value in data["efficiency"]],
            [PowerDbm(value) for value in data["sg_power"]],
            compression_value,
            small_gain_points=3,
        )
        return {"compression_type": f"{compression_value.db:g}dB",
                "compression_point": result.compression_point.to_mapping(),
                "small_signal_gain": result.small_signal_gain.db, "sweep_data": data,
                "compression_achieved": result.compression_achieved}

    def run(self) -> dict[str, Any]:
        results = self._results
        try:
            if self.inputs.driver_enabled:
                self.inst_ctrl.setup_driver_amplifier_power()
            self.inst_ctrl.setup_dut_power()
            self.inst_ctrl.power_on_sequence()
            self.sleep_fn(2)
            for frequency in self.inputs.frequencies:
                self._check_cancelled()
                results[frequency.key] = self.perform_power_sweep(frequency.ghz)
            result = {"results": results, "configuration_snapshot": self.configuration_snapshot}
            self._save(result)
            return result
        finally:
            self._cleanup(power_cleanup=self.inst_ctrl.power_off_sequence)


__all__ = [
    "ResultRepository",
    "NullEventSink",
    "CableLossService",
    "DriverMappingError",
    "DriverPowerMappingService",
    "AmplifierMeasurementService",
]
