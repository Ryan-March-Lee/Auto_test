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
from typing import Any, Callable, Mapping, Protocol

import numpy as np

from app.cancellation import CancellationToken
from app.events import CheckpointEvent, EventSink, MessageEvent, ProgressEvent, RealtimeDataEvent
from app.events import StoppedEvent
from application.ports.measurement_session import (
    CleanupRecord,
    MeasurementSessionBinding,
    MeasurementSessionPort,
    SafetyCleanupError,
    SafetyCleanupPort,
)
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
    DriverOutputTable,
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
    """

    measurement_type = "measurement"

    def __init__(self, config: Mapping[str, Any], instrument: MeasurementInstrumentPort, *, run_id: str,
                 safety_cleanup: SafetyCleanupPort,
                 event_sink: EventSink | None = None, cancellation_token: CancellationToken | None = None,
                 result_repository: ResultRepository | Callable[[Any], Any] | None = None,
                 sleep_fn: Callable[[float], None] | None = None,
                 settle_delay_s: float = 5.0,
                 owns_measurement_port: bool = True):
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("测量服务必须提供有效运行 ID")
        if not isinstance(config, MappingABC):
            raise TypeError("测量服务配置必须是 Mapping")
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
    def required_capabilities(cls, config: Mapping[str, Any]) -> tuple[str, ...]:
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
        super().__init__(config, instrument, **kwargs)
        self.attenuator_value = Decibels.parse(config["attenuator"]["type"])
        self._path1_losses: dict[float, float] = {}

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

    def cleanup(self) -> CleanupRecord:
        """Expose the common safety cleanup boundary to application callers."""
        return self._cleanup()

    def run(self, *, path2_confirmed: bool = True) -> dict[str, Any]:
        frequencies = tuple(
            Frequency.from_ghz(value) for value in self.config["test_frequencies"]
        )
        path1: dict[float, float] = dict(self._path1_losses)
        path2: dict[float, float] = {}
        waiting_for_path2 = False
        try:
            if not path1:
                _publish(self.event_sink, CheckpointEvent(self.run_id, checkpoint="path1", prompt="请连接路径1"))
                for index, frequency in enumerate(frequencies):
                    path1[frequency.ghz] = self.measure_path_loss(frequency.ghz)
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
                self._path1_losses = dict(path1)
            if not path2_confirmed:
                waiting_for_path2 = True
                _publish(self.event_sink, CheckpointEvent(self.run_id, checkpoint="path2", prompt="请连接路径2"))
                return {"path1_losses": path1, "path2_losses": path2, "status": "waiting"}
            # 路径2的确认由GUI在第一次运行返回waiting后完成。
            # 继续运行时不能再次发布检查点，否则GUI会把同一个确认事件
            # 当成新的弹窗请求，而路径2测量已经开始执行。
            for index, frequency in enumerate(frequencies):
                path2[frequency.ghz] = self.measure_path_loss(frequency.ghz)
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
    """Measure driver output mapping and publish each point as an event."""

    measurement_type = "driver_power_mapping"

    def __init__(self, config: Mapping[str, Any], instrument: MeasurementInstrumentPort,
                 loss_data: Mapping[str, Any], **kwargs: Any):
        super().__init__(config, instrument, **kwargs)
        # 输入文档在构造阶段收敛为值对象：缺失频率、未知字段或非数值在硬件动作前失败。
        # 服务只保留解析后的表，不再同时持有原始文档副本。
        self.loss_table = _loss_table(loss_data)
        self.attenuator_value = Decibels.parse(config["attenuator"]["type"])

    @classmethod
    def required_capabilities(cls, config: Mapping[str, Any]) -> tuple[str, ...]:
        return SIGNAL_MEASUREMENT_CAPABILITIES + DRIVER_POWER_CAPABILITIES

    def run(self) -> dict[str, Any]:
        mapping: dict[str, dict[str, float]] = {}
        try:
            self.inst_ctrl.setup_driver_amplifier_power()
            self.inst_ctrl.power_on_driver()
            frequencies = tuple(
                Frequency.from_ghz(value) for value in self.config["test_frequencies"]
            )
            for frequency_index, frequency in enumerate(frequencies):
                self._check_cancelled()
                self.inst_ctrl.set_power(-40)
                self.inst_ctrl.set_frequency(frequency.ghz)
                self.inst_ctrl.set_center_frequency(frequency.ghz)
                self.inst_ctrl.set_span(10)
                values: dict[str, float] = {}
                points = np.arange(self.config["signal_source"]["start_power"],
                                   self.config["signal_source"]["stop_power"] + self.config["signal_source"]["step"],
                                   self.config["signal_source"]["step"])
                self.inst_ctrl.rf_output_on()
                try:
                    for point_index, input_power in enumerate(points):
                        self._check_cancelled()
                        self.inst_ctrl.set_power(input_power)
                        self.sleep_fn(self.settle_delay_s)
                        raw = self.inst_ctrl.measure_power_with_average()
                        actual = compensate_driver_output_power(
                            PowerDbm(raw),
                            frequency,
                            self.loss_table,
                            self.attenuator_value,
                        ).dbm
                        values[str(input_power)] = actual
                        sweep_data = {
                            "input_power_sg": [float(key) for key in values],
                            "output_power_driver": list(values.values()),
                            "gain": [output - float(key) for key, output in values.items()],
                        }
                        _publish(self.event_sink, RealtimeDataEvent(self.run_id, measurement=self.measurement_type,
                            data={"frequency": frequency.ghz, "sweep_data": sweep_data,
                                  "current_point": {"input_power": input_power, "output_power": actual}}))
                        self._progress((frequency_index + (point_index + 1) / len(points)) / len(frequencies), "scan")
                finally:
                    self.inst_ctrl.rf_output_off()
                mapping[frequency.key] = values
            result = {"power_mapping": mapping}
            self._save(result)
            return result
        finally:
            self._cleanup(power_cleanup=self.inst_ctrl.power_off_driver)


class AmplifierMeasurementService(_Service):
    """Run the main amplifier sweep, including protection and compression logic."""

    measurement_type = "amplifier_measurement"

    def __init__(self, config: Mapping[str, Any], instrument: MeasurementInstrumentPort,
                 loss_data: Mapping[str, Any], driver_mapping: Mapping[str, Any] | None = None,
                 **kwargs: Any):
        super().__init__(config, instrument, **kwargs)
        # 校准输入在构造阶段收敛为值对象，缺字段或错误单位不会等到扫描中途才暴露；
        # 服务只保留解析后的表，不再同时持有原始文档副本。
        self.loss_table = _loss_table(loss_data)
        self.driver_table = (
            DriverOutputTable.from_mapping(driver_mapping, source="驱动映射输入")
            if driver_mapping
            else None
        )
        self.attenuator_value = Decibels.parse(config["attenuator"]["type"])

    @classmethod
    def required_capabilities(cls, config: Mapping[str, Any]) -> tuple[str, ...]:
        """主功放扫描必用 DUT 电源能力；驱动模式开启时才要求驱动电源能力。"""
        capabilities = SIGNAL_MEASUREMENT_CAPABILITIES + DUT_POWER_CAPABILITIES
        driver_mode = config.get("driver_mode", {})
        enabled = driver_mode.get("enabled") if isinstance(driver_mode, Mapping) else None
        if enabled:
            capabilities += DRIVER_POWER_CAPABILITIES
        return capabilities

    def perform_power_sweep(self, frequency: float) -> dict[str, Any]:
        compression_type = self.config["compression_point"]["type"]
        compression_value = Decibels.parse(compression_type)
        attenuator_value = self.attenuator_value
        frequency_value = Frequency.from_ghz(frequency)
        maximum = self.config.get("dut_config", {}).get("max_input_power", float("inf"))
        self.inst_ctrl.set_power(-40)
        self.inst_ctrl.set_frequency(frequency)
        self.inst_ctrl.set_center_frequency(frequency)
        self.inst_ctrl.set_span(10)
        supplies = self.config["power_supply_assignment"]["dut_amplifier"]["supplies"]
        data = {"input_power_dut": [], "output_power_dut": [], "gain": [], "sg_power": [],
                "voltages": [], "currents": [], "dc_power": [], "efficiency": []}
        points = np.arange(self.config["signal_source"]["start_power"],
                           self.config["signal_source"]["stop_power"] + self.config["signal_source"]["step"],
                           self.config["signal_source"]["step"])
        self.inst_ctrl.rf_output_on()
        try:
            for index, sg_power in enumerate(points):
                self._check_cancelled()
                dut_input = calculate_dut_input_power(
                    PowerDbm(sg_power),
                    frequency_value,
                    self.loss_table,
                    self.driver_table,
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
                self.inst_ctrl.set_power(sg_power)
                self.sleep_fn(self.settle_delay_s)
                raw = self.inst_ctrl.measure_power_with_average()
                output = compensate_amplifier_output_power(
                    PowerDbm(raw),
                    frequency_value,
                    self.loss_table,
                    attenuator_value,
                )
                voltage, current, dc_power = {}, {}, 0.0
                for supply_name, info in supplies.items():
                    for channel in info["channel"]:
                        v = self.inst_ctrl.read_voltage(info["name"], channel)
                        i = self.inst_ctrl.read_current(info["name"], channel)
                        dc_power += v * i
                        voltage[f"{supply_name}_{channel}"] = v
                        current[f"{supply_name}_{channel}"] = i
                gain = calculate_gain(output, dut_input)
                efficiency = calculate_efficiency(output, PowerWatt(dc_power))
                data["sg_power"].append(float(sg_power))
                data["input_power_dut"].append(dut_input.dbm)
                data["output_power_dut"].append(output.dbm)
                data["gain"].append(gain.db)
                data["voltages"].append(voltage); data["currents"].append(current)
                data["dc_power"].append(dc_power); data["efficiency"].append(efficiency.percent)
                _publish(self.event_sink, RealtimeDataEvent(self.run_id, measurement=self.measurement_type,
                    data={"frequency": frequency, "sweep_data": {key: list(value) for key, value in data.items()},
                          "current_point": {"sg_power": sg_power, "dut_input_power": dut_input.dbm,
                                            "output_power": output.dbm, "gain": gain.db,
                                            "efficiency": data["efficiency"][-1],
                                            "dc_power": dc_power}}))
                self._progress((index + 1) / max(len(points), 1), "scan")
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
        return {"compression_type": compression_type,
                "compression_point": result.compression_point.to_mapping(),
                "small_signal_gain": result.small_signal_gain.db, "sweep_data": data,
                "compression_achieved": result.compression_achieved}

    def run(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        try:
            if self.config["driver_mode"]["enabled"]:
                self.inst_ctrl.setup_driver_amplifier_power()
            self.inst_ctrl.setup_dut_power()
            self.inst_ctrl.power_on_sequence()
            self.sleep_fn(2)
            for frequency in self.config["test_frequencies"]:
                self._check_cancelled()
                results[Frequency.from_ghz(frequency).key] = self.perform_power_sweep(frequency)
            result = {"results": results, "configuration_snapshot": self.config}
            self._save(result)
            return result
        finally:
            self._cleanup(power_cleanup=self.inst_ctrl.power_off_sequence)


__all__ = [
    "ResultRepository",
    "NullEventSink",
    "CableLossService",
    "DriverPowerMappingService",
    "AmplifierMeasurementService",
]
