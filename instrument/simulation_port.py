"""仿真运行端口：用冻结读数包回答测量服务的读数请求。

仿真运行只替换 :class:`instrument.ports.MeasurementInstrumentPort` 的读数方法，
因此本模块提供的是“仪器本来会返回什么”，不是界面事件，也不是补偿后的结果：

- 控制方法（``set_*``、``rf_output_*``、``setup_*``、``power_on/off_*``）只记录
  状态与命令，不产生任何真实设备动作；功率记录的是信号源驱动实际下发的 6 位有效
  数字命令值（与真实仪器设定一致），记录点查找也使用该值；
- :meth:`SimulationRunPort.measure_power_with_average` 按当前激活阶段解释冻结读数：
  线损按有序序列逐项消费并校验频率；驱动映射与主功放按 ``(频率, 功率)`` 查找记录点，
  精确命中返回记录值，未命中在相邻记录点之间线性插值，禁止外推；
- :meth:`SimulationRunPort.read_voltage` / :meth:`SimulationRunPort.read_current`
  只绑定“最近一次成功消费的主功放测量点”。

端口是**运行级 router**：一次仿真运行只创建一个端口，构造时接收本次运行加载的冻结
读数包（单个阶段或按阶段索引的多份读数），``activate(measurement_type)`` 选择对应
读数包。生命周期与所有权（见 ``docs/仿真运行链路分阶段重构计划.md`` 第 3 节第 14 条）：

```text
控制器 begin_stage()  →  worker 工厂 activate(measurement_type)  →  worker.start()
成功：协调器 end_stage() 结算并保留端口供下一阶段复用
失败/取消：协调器 close_all() 关闭端口；下一次进入阶段必须重建端口
```

端口不读文件、不自行关闭、不在 worker 内被关闭；冻结读数由运行装配在进入仿真运行时
一次性加载并校验。本模块不导入 ``pyvisa``、``instrument.drivers`` 或
``instrument.transport``，因此不会打开 VISA 资源，也不构造驱动或传输层对象。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, TypeVar, Union

from application.dto.measurement_results import MeasurementType
from application.dto.simulation_readings import (
    SIMULATION_FREQUENCY_TOLERANCE_GHZ,
    SimulationAmplifierFrequency,
    SimulationAmplifierPayload,
    SimulationAmplifierPoint,
    SimulationCableLossPayload,
    SimulationDriverFrequency,
    SimulationDriverMappingPayload,
    SimulationDriverProbe,
    SimulationReadings,
)
from application.errors import ConfigurationError
from domain.measurement.values import Frequency
from infrastructure.logging.app_logging import get_logger


logger = get_logger(__name__)

#: 记录点（源功率 / 信号源设定功率）按数值匹配的容差，单位 dB。
SIMULATION_POWER_TOLERANCE_DBM = 1e-9
#: 信号源驱动把功率格式化成 6 位有效数字后再下发（``SignalGeneratorDriver`` 的
#: ``_format_number``）。端口按同一量化值查找记录：真实运行里请求 ``-69.752975``
#: 到达仪器时是 ``-69.753``，冻结读数记录的也是后者。这里只复现命令量化，
#: 不改变记录点容差，也不允许外推。
SIMULATION_POWER_COMMAND_FORMAT = "g"

__all__ = [
    "SIMULATION_POWER_COMMAND_FORMAT",
    "SIMULATION_POWER_TOLERANCE_DBM",
    "SimulationPortError",
    "SimulationPortStateError",
    "SimulationReadingsInput",
    "SimulationRunPort",
]


class SimulationPortError(ConfigurationError):
    """仿真读数无法回答本次请求，需要重新生成 ``readings.json``。

    适用场景都是“冻结读数与本次测量请求不匹配”：线损顺序/频率不符、驱动映射或
    主功放的功率点超出记录范围、通道集合缺少当前电源通道、严格模式下请求了非记录点。
    归入配置类异常，让调用方按“数据需要重建”处理，而不是当成可重试的仪器瞬时故障。
    """


class SimulationPortStateError(ConfigurationError, RuntimeError):
    """端口生命周期不允许本次动作：未激活、已关闭或阶段被重复激活。

    这是运行装配缺陷（worker 工厂没有先 ``activate()``、失败阶段复用已关闭端口），
    不是读数数据问题；消息会直接说明需要重新连接并重建端口。归入配置类异常，
    与 :class:`application.run_stages.StageInputUnavailable` 一致；同时保留
    ``RuntimeError`` 基类，使分类之前就存在的 ``except RuntimeError`` 处理器继续生效。
    """


_Record = TypeVar("_Record")

#: 端口接受的冻结读数：单个阶段（阶段隔离/调试）或按阶段索引的本轮读数包。
SimulationReadingsInput = Union[
    SimulationReadings, Mapping[Union[MeasurementType, str], SimulationReadings]
]


def _normalize_readings(
    value: SimulationReadingsInput,
) -> Mapping[MeasurementType, SimulationReadings]:
    """把构造输入收敛为“阶段 -> 冻结读数包”的只读映射。

    一个仿真运行只有一个端口，三个阶段共用一个端口；按阶段索引的映射是正式形状。
    单个读数包保留给阶段隔离测试与本地调试，不改变端口语义。
    """
    if isinstance(value, SimulationReadings):
        return MappingProxyType({value.measurement_type: value})
    if not isinstance(value, Mapping):
        raise TypeError(
            "SimulationRunPort 需要冻结读数包或按阶段索引的读数包映射，"
            f"收到 {type(value).__name__}"
        )
    normalized: dict[MeasurementType, SimulationReadings] = {}
    for key, readings in value.items():
        stage = _coerce_measurement_type(key)
        if not isinstance(readings, SimulationReadings):
            raise TypeError(
                f"阶段 {stage.value} 的读数必须是 SimulationReadings，"
                f"收到 {type(readings).__name__}"
            )
        if readings.measurement_type is not stage:
            raise SimulationPortError(
                f"读数包映射把 {readings.measurement_type.value} 读数登记在 "
                f"{stage.value} 阶段；请检查运行装配"
            )
        if stage in normalized:
            raise SimulationPortError(
                f"读数包映射重复登记了 {stage.value} 阶段；请检查运行装配"
            )
        normalized[stage] = readings
    if not normalized:
        raise SimulationPortError("仿真端口至少需要一个阶段的冻结读数包")
    return MappingProxyType(normalized)


def _finite_number(value: Any, description: str) -> float:
    """把控制参数收敛为有限浮点数；非数值或非有限数在触达状态前失败。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{description} 必须是数字，收到 {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{description} 必须是有限数字，收到 {value!r}")
    return number


def _positive_frequency(value: Any, description: str) -> float:
    frequency = _finite_number(value, description)
    if frequency <= 0:
        raise ValueError(f"{description} 必须是正数，收到 {frequency}")
    return frequency


def _quantize_power_command(power: float) -> float:
    """复现信号源下发的功率命令值：``f"{value:g}"`` 的 6 位有效数字。

    真实驱动（``SignalGeneratorDriver._format_number``）在写 SCPI 前把浮点功率
    格式化成 6 位有效数字，仪器实际设定值因此是量化值。端口按同一值查找冻结读数，
    否则记录点永远差最后几位；这不是放宽容差：量化后仍不在记录点上的请求照常失败。
    """
    return float(f"{power:{SIMULATION_POWER_COMMAND_FORMAT}}")


def _require_text(value: Any, description: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{description} 必须是非空字符串，收到 {value!r}")
    return value


def _coerce_measurement_type(value: Any) -> MeasurementType:
    """接受 application 的测量类型或其等值字符串，不依赖 Qt 的测量种类。"""
    if isinstance(value, MeasurementType):
        return value
    if not isinstance(value, str):
        raise TypeError(
            "measurement_type 必须是 MeasurementType 或等值字符串，"
            f"收到 {type(value).__name__}"
        )
    try:
        return MeasurementType(value)
    except ValueError as error:
        allowed = "、".join(kind.value for kind in MeasurementType)
        raise SimulationPortError(
            f"未知的仿真测量类型 {value!r}；可用类型: {allowed}"
        ) from error


@dataclass
class _SimulatedSignalGenerator:
    """占位信号源：只镜像 RF 状态，不动作。

    ``SafetyCleanupAdapter`` 用 ``instrument_control.signal_gen is not None`` 判断端口
    是否具备“关闭 RF”的能力。仿真端口没有真实设备，用一个只读镜像保持同一条清理
    路径，而不是让清理误以为“无 RF 可关”。
    """

    rf_enabled: bool = False


@dataclass(frozen=True)
class _AmplifierReading:
    """一次被消费的主功放测量点（精确命中或插值结果）。"""

    raw_output_dbm: float
    voltages: Mapping[str, float]
    currents: Mapping[str, float]


class SimulationRunPort:
    """用冻结的 :class:`SimulationReadings` 实现测量服务的仪器端口。

    ``strict`` 只用于阶段隔离测试：开启后驱动映射与主功放只接受读数里的记录点，
    任何需要插值的请求都直接失败并提示重新生成 ``readings.json``。生产/GUI 默认
    允许在记录区间内插值，但任何外推都会被拒绝。

    端口是运行级 router：构造时可以给一个读数包（阶段隔离/调试）或本次运行三个
    阶段的读数包映射；``activate()`` 选择对应包，一个端口因此可以连续服务
    线损 → 驱动映射 → 主功放。激活的读数包只按类型选择，不在运行期重新加载。
    """

    def __init__(
        self,
        readings: SimulationReadingsInput,
        *,
        strict: bool = False,
    ) -> None:
        if not isinstance(strict, bool):
            raise TypeError(f"strict 必须是布尔值，收到 {type(strict).__name__}")
        self._readings_by_stage = _normalize_readings(readings)
        self.strict = strict
        self.signal_gen = _SimulatedSignalGenerator()
        # 与真实适配器同名，安全清理与诊断代码不需要区分链路。
        self.signal_generator = self.signal_gen
        self._closed = False
        self._active_stage: MeasurementType | None = None
        self._active_readings: SimulationReadings | None = None
        self._frequency_ghz: float | None = None
        self._center_frequency_ghz: float | None = None
        self._span_mhz: float | None = None
        self._power_dbm: float | None = None
        self._rf_enabled = False
        self._driver_powered = False
        self._dut_powered = False
        self._cable_index = 0
        self._amplifier_point: _AmplifierReading | None = None
        self._driver_cache: dict[tuple[str, float], float] = {}
        self._interpolations: dict[Frequency, int] = {}
        self._commands: list[tuple[str, Any]] = []

    # -- 诊断 ---------------------------------------------------------------

    @property
    def closed(self) -> bool:
        """端口是否已被关闭；关闭后一切读数与控制都要求重新连接。"""
        return self._closed

    @property
    def readings(self) -> Mapping[MeasurementType, SimulationReadings]:
        """本次运行加载的冻结读数包，按阶段索引（只读）。"""
        return self._readings_by_stage

    @property
    def active_stage(self) -> MeasurementType | None:
        """当前激活的测量阶段；``None`` 表示尚未激活或已经结算。"""
        return self._active_stage

    @property
    def interpolation_counts(self) -> Mapping[Frequency, int]:
        """本次阶段按频点累计的插值次数（诊断与全链路测试使用）。"""
        return MappingProxyType(dict(self._interpolations))

    @property
    def recorded_commands(self) -> tuple[tuple[str, Any], ...]:
        """控制方法记录的命令序列；只用于审计与测试，不产生设备动作。"""
        return tuple(self._commands)

    # -- 生命周期 -----------------------------------------------------------

    def begin_stage(self) -> None:
        """复用前的就绪检查：端口未关闭且没有未结算的阶段。

        阶段绑定不在这一步完成；控制器不传测量类型，绑定由 worker 工厂在创建 worker
        之前调用 :meth:`activate` 完成。
        """
        self._require_open("开始阶段")
        if self._active_stage is not None:
            raise SimulationPortStateError(
                f"阶段 {self._active_stage.value} 尚未结算，不能开始下一阶段；"
                "成功阶段必须先 end_stage()，失败或取消由协调器关闭端口"
            )

    def activate(self, measurement_type: MeasurementType | str) -> None:
        """把端口绑定到一个测量阶段，并清空该阶段的游标与查找状态。

        先判生命周期、再查读数包：已有未结算阶段时，即使目标阶段没有加载，也按
        生命周期误用报告，而不是把问题说成“读数包缺失”。阶段绑定成功后清空上一阶段
        留下的频率/功率设定，避免下一阶段漏设 ``set_frequency`` / ``set_power`` 时
        悄悄复用旧值。
        """
        self._require_open("激活测量阶段")
        if self._active_stage is not None:
            raise SimulationPortStateError(
                f"阶段 {self._active_stage.value} 尚未结算，不能重复激活；"
                "成功阶段必须先 end_stage()，失败由协调器关闭端口"
            )
        stage = _coerce_measurement_type(measurement_type)
        package = self._readings_by_stage.get(stage)
        if package is None:
            available = "、".join(
                item.value for item in self._readings_by_stage
            ) or "无"
            raise SimulationPortError(
                f"本次仿真运行没有加载 {stage.value} 阶段的读数包，不能激活该阶段；"
                f"已加载: {available}；请检查运行装配"
            )
        self._active_stage = stage
        self._active_readings = package
        self._frequency_ghz = None
        self._center_frequency_ghz = None
        self._span_mhz = None
        self._power_dbm = None
        self._cable_index = 0
        self._amplifier_point = None
        self._driver_cache.clear()
        self._interpolations.clear()
        logger.info(
            "仿真端口激活阶段 %s（严格模式=%s）", stage.value, self.strict
        )

    def end_stage(self, *, timeout_s: float = 30.0) -> None:
        """成功阶段结算：关闭输出状态、解除阶段绑定并保留端口供下一阶段复用。

        幂等：重复调用、或在端口已经关闭后调用都不会抛错。阶段结束后读取电压/电流
        不再绑定上一个阶段的点，避免旧测量点跨阶段泄漏。
        """
        del timeout_s  # 仿真端口没有需要等待的设备动作，签名与真实端口保持一致。
        if self._closed:
            return
        stage = self._active_stage
        self._active_stage = None
        self._active_readings = None
        self._set_rf_enabled(False)
        self._driver_powered = False
        self._dut_powered = False
        self._amplifier_point = None
        self._driver_cache.clear()
        if stage is not None:
            if self._interpolations:
                total = sum(self._interpolations.values())
                detail = "、".join(
                    f"{frequency.key} GHz {count} 次"
                    for frequency, count in self._interpolations.items()
                )
                logger.info(
                    "仿真端口阶段 %s 结算：插值 %d 次（%s）",
                    stage.value,
                    total,
                    detail,
                )
            else:
                logger.info("仿真端口阶段 %s 结算：未发生插值", stage.value)

    def close_all(
        self, *, close_rf: bool = False, timeout_s: float = 30.0
    ) -> list[BaseException]:
        """关闭端口；幂等且始终返回空错误列表。

        仿真端口没有真实资源需要释放，因此“关闭”只清空状态。返回值类型与真实适配器
        一致，协调器会把空列表当成“已确认释放”。
        """
        del close_rf, timeout_s  # 已关闭的端口没有可复位的 RF 或设备连接。
        if self._closed:
            return []
        self._closed = True
        self._active_stage = None
        self._active_readings = None
        self._set_rf_enabled(False)
        self._driver_powered = False
        self._dut_powered = False
        self._amplifier_point = None
        self._driver_cache.clear()
        logger.info("仿真端口已关闭：不释放真实设备资源，需要重新连接时重建端口")
        return []

    # -- 控制面：只记录，不动作 ---------------------------------------------

    def set_power(self, power: float) -> None:
        """设置信号源输出功率（dBm）；记录驱动实际下发的量化命令值。"""
        self._require_open("设置输出功率")
        self._power_dbm = _quantize_power_command(
            _finite_number(power, "信号源输出功率")
        )
        self._record("set_power", self._power_dbm)

    def set_frequency(self, frequency: float) -> None:
        """设置信号源频率（GHz）；只记录设定值。"""
        self._require_open("设置频率")
        self._frequency_ghz = _positive_frequency(frequency, "信号源频率(GHz)")
        self._record("set_frequency", self._frequency_ghz)

    def set_center_frequency(self, frequency: float) -> None:
        """设置频谱仪中心频率（GHz）；只记录设定值。"""
        self._require_open("设置中心频率")
        self._center_frequency_ghz = _positive_frequency(
            frequency, "频谱仪中心频率(GHz)"
        )
        self._record("set_center_frequency", self._center_frequency_ghz)

    def set_span(self, span: float) -> None:
        """设置频谱仪跨度（MHz）；只记录设定值。"""
        self._require_open("设置扫描跨度")
        span_mhz = _finite_number(span, "频谱仪跨度(MHz)")
        if span_mhz <= 0:
            raise ValueError(f"频谱仪跨度(MHz) 必须是正数，收到 {span_mhz}")
        self._span_mhz = span_mhz
        self._record("set_span", span_mhz)

    def rf_output_on(self) -> None:
        """记录 RF 打开；重复打开是幂等动作。"""
        self._require_open("打开 RF 输出")
        self._set_rf_enabled(True)
        self._record("rf_output_on")

    def rf_output_off(self) -> None:
        """记录 RF 关闭；清理路径会尽力执行，因此重复关闭同样幂等。"""
        self._require_open("关闭 RF 输出")
        self._set_rf_enabled(False)
        self._record("rf_output_off")

    def setup_driver_amplifier_power(self) -> None:
        """记录驱动放大器电源设置；仿真运行不改变任何通道。"""
        self._require_open("设置驱动放大器电源")
        self._record("setup_driver_amplifier_power")

    def power_on_driver(self) -> None:
        """记录驱动放大器上电；保持可重复调用的幂等语义。"""
        self._require_open("驱动放大器上电")
        self._driver_powered = True
        self._record("power_on_driver")

    def power_off_driver(self) -> None:
        """记录驱动放大器下电；清理路径会尽力执行。"""
        self._require_open("驱动放大器下电")
        self._driver_powered = False
        self._record("power_off_driver")

    def setup_dut_power(self) -> None:
        """记录 DUT 电源设置；仿真运行不改变任何通道。"""
        self._require_open("设置 DUT 电源")
        self._record("setup_dut_power")

    def power_on_sequence(self) -> None:
        """记录 DUT 按序上电；不产生真实输出。"""
        self._require_open("DUT 上电")
        self._dut_powered = True
        self._record("power_on_sequence")

    def power_off_sequence(self) -> None:
        """记录 DUT 按序下电；清理路径会尽力执行。"""
        self._require_open("DUT 下电")
        self._dut_powered = False
        self._record("power_off_sequence")

    # -- 读数：按激活阶段解释冻结读数 ---------------------------------------

    def measure_power_with_average(self) -> float:
        """返回当前频点/功率对应的原始读数（dBm）。"""
        stage = self._require_stage(None, "读取平均功率")
        if stage is MeasurementType.CABLE_LOSS:
            return self._measure_cable_loss()
        if stage is MeasurementType.DRIVER_POWER_MAPPING:
            return self._measure_driver_mapping()
        return self._measure_amplifier()

    def read_voltage(self, supply_name: str, channel: str) -> float:
        """读取主功放最近一次消费测量点的通道电压（V）。"""
        self._require_stage(MeasurementType.AMPLIFIER, "读取电压")
        point = self._require_amplifier_point("电压")
        key = self._power_channel_key(supply_name, channel, "读取电压")
        if key not in point.voltages:
            raise SimulationPortError(
                f"主功放读数没有电源通道 {key!r} 的电压；可用通道: "
                f"{', '.join(sorted(point.voltages))}；请重新生成 readings.json"
            )
        return point.voltages[key]

    def read_current(self, supply_name: str, channel: str) -> float:
        """读取主功放最近一次消费测量点的通道电流（A）。"""
        self._require_stage(MeasurementType.AMPLIFIER, "读取电流")
        point = self._require_amplifier_point("电流")
        key = self._power_channel_key(supply_name, channel, "读取电流")
        if key not in point.currents:
            raise SimulationPortError(
                f"主功放读数没有电源通道 {key!r} 的电流；可用通道: "
                f"{', '.join(sorted(point.currents))}；请重新生成 readings.json"
            )
        return point.currents[key]

    # -- 线损 ---------------------------------------------------------------

    def _measure_cable_loss(self) -> float:
        """按 ``CableLossService.run()`` 的顺序消费有序测量序列。

        游标只在成功返回后才推进：读取失败不会让下一次调用错位。路径 1 → 路径 2 的
        切换由序列位置决定，频率校验同时覆盖“提前读取”“多余读取”和“停止后复用旧
        游标”三种情况。
        """
        payload = self._readings_payload(SimulationCableLossPayload, "线损")
        measurements = payload.measurements
        if self._cable_index >= len(measurements):
            raise SimulationPortError(
                f"线损读数已全部消费（共 {len(measurements)} 项），"
                f"收到第 {self._cable_index + 1} 次读数请求；"
                "请检查测量顺序或重新生成 readings.json"
            )
        item = measurements[self._cable_index]
        frequency = self._require_frequency("线损")
        if (
            abs(frequency.ghz - item.frequency_ghz)
            > SIMULATION_FREQUENCY_TOLERANCE_GHZ
        ):
            raise SimulationPortError(
                f"线损读数顺序不符：第 {self._cable_index + 1} 项应为路径 "
                f"{item.path} 的 {item.frequency_ghz:g} GHz，当前设置频率为 "
                f"{frequency.ghz:g} GHz；线损必须按 readings.json 的顺序"
                "（路径 1 全频点 → 路径 2 全频点）测量，停止后重跑需要重建端口"
            )
        path1_count = len(payload.path_frequencies(1))
        expected_path = 1 if self._cable_index < path1_count else 2
        if item.path != expected_path:
            raise SimulationPortError(
                f"线损读数第 {self._cable_index + 1} 项应属于路径 {expected_path}，"
                f"读数为路径 {item.path}；请重新生成 readings.json"
            )
        self._cable_index += 1
        return item.raw_dbm

    # -- 驱动映射 -----------------------------------------------------------

    def _measure_driver_mapping(self) -> float:
        """按 ``(频率, 源功率)`` 查找记录点；区间内插值、禁止外推。"""
        bucket = self._frequency_bucket(
            SimulationDriverMappingPayload,
            SimulationDriverFrequency,
            "驱动映射",
        )
        frequency = self._require_frequency("驱动映射")
        power = self._require_power("驱动映射")
        cache_key = (frequency.key, power)
        if cache_key in self._driver_cache:
            # 重复读取同一键返回同一结果，不推进任何游标。
            return self._driver_cache[cache_key]
        probes = bucket.probes
        exact = next(
            (
                probe
                for probe in probes
                if abs(probe.source_power_dbm - power)
                <= SIMULATION_POWER_TOLERANCE_DBM
            ),
            None,
        )
        if exact is not None:
            raw = exact.raw_output_dbm
        else:
            raw = self._interpolate_driver(frequency, probes, power)
        self._driver_cache[cache_key] = raw
        return raw

    def _interpolate_driver(
        self,
        frequency: Frequency,
        probes: tuple[SimulationDriverProbe, ...],
        power: float,
    ) -> float:
        lower, upper = self._surrounding_records(
            frequency,
            probes,
            power,
            stage="驱动映射",
            key=lambda probe: probe.source_power_dbm,
        )
        fraction = (power - lower.source_power_dbm) / (
            upper.source_power_dbm - lower.source_power_dbm
        )
        value = lower.raw_output_dbm + fraction * (
            upper.raw_output_dbm - lower.raw_output_dbm
        )
        self._record_interpolation(frequency, "驱动映射", power)
        return value

    # -- 主功放 -------------------------------------------------------------

    def _measure_amplifier(self) -> float:
        """按 ``(频率, 信号源设定功率)`` 查找扫描点，并绑定 V/I 供随后读取。"""
        bucket = self._frequency_bucket(
            SimulationAmplifierPayload,
            SimulationAmplifierFrequency,
            "主功放",
        )
        frequency = self._require_frequency("主功放")
        power = self._require_power("主功放")
        points = bucket.points
        exact = next(
            (
                point
                for point in points
                if abs(point.sg_power_dbm - power) <= SIMULATION_POWER_TOLERANCE_DBM
            ),
            None,
        )
        if exact is not None:
            reading = _AmplifierReading(
                raw_output_dbm=exact.raw_output_dbm,
                voltages=exact.voltages,
                currents=exact.currents,
            )
        else:
            reading = self._interpolate_amplifier(frequency, points, power)
        # 只有成功解析出测量点才更新绑定：失败的请求不会把 V/I 指向别处。
        self._amplifier_point = reading
        return reading.raw_output_dbm

    def _interpolate_amplifier(
        self,
        frequency: Frequency,
        points: tuple[SimulationAmplifierPoint, ...],
        power: float,
    ) -> _AmplifierReading:
        lower, upper = self._surrounding_records(
            frequency,
            points,
            power,
            stage="主功放",
            key=lambda point: point.sg_power_dbm,
        )
        fraction = (power - lower.sg_power_dbm) / (
            upper.sg_power_dbm - lower.sg_power_dbm
        )
        if set(lower.voltages) != set(upper.voltages):
            raise SimulationPortError(
                f"主功放读数 {frequency.key} GHz 的相邻记录点通道集合不一致；"
                "请重新生成 readings.json"
            )
        voltages = {
            channel: lower.voltages[channel]
            + fraction * (upper.voltages[channel] - lower.voltages[channel])
            for channel in lower.voltages
        }
        currents = {
            channel: lower.currents[channel]
            + fraction * (upper.currents[channel] - lower.currents[channel])
            for channel in lower.currents
        }
        self._record_interpolation(frequency, "主功放", power)
        return _AmplifierReading(
            raw_output_dbm=lower.raw_output_dbm
            + fraction * (upper.raw_output_dbm - lower.raw_output_dbm),
            voltages=MappingProxyType(voltages),
            currents=MappingProxyType(currents),
        )

    # -- 查找与校验 ---------------------------------------------------------

    def _readings_payload(self, expected_type: type, stage: str) -> Any:
        readings = self._active_readings
        if readings is None:
            raise SimulationPortStateError(
                f"{stage}阶段没有激活的冻结读数包；worker 工厂必须先调用 "
                "activate(measurement_type)"
            )
        payload = readings.payload
        if not isinstance(payload, expected_type):
            raise SimulationPortError(
                f"{stage}阶段拿到的是 {type(payload).__name__} 读数载荷；"
                "冻结读数与激活阶段不一致，请检查运行装配"
            )
        return payload

    def _frequency_bucket(
        self,
        payload_type: type,
        bucket_type: type,
        stage: str,
    ) -> Any:
        payload = self._readings_payload(payload_type, stage)
        frequencies: Mapping[Frequency, Any] = payload.frequencies
        frequency = self._require_frequency(stage)
        for key, value in frequencies.items():
            if abs(key.ghz - frequency.ghz) <= SIMULATION_FREQUENCY_TOLERANCE_GHZ:
                if not isinstance(value, bucket_type):
                    raise SimulationPortError(
                        f"{stage}读数 {key.key} GHz 的记录形状不正确"
                        f"（{type(value).__name__}）；请重新生成 readings.json"
                    )
                return value
        raise SimulationPortError(
            f"{stage}读数没有 {frequency.ghz:g} GHz 的记录点；读数覆盖: "
            f"{', '.join(sorted(key.key for key in frequencies))}；"
            "请重新生成 readings.json"
        )

    def _surrounding_records(
        self,
        frequency: Frequency,
        records: tuple[_Record, ...],
        power: float,
        *,
        stage: str,
        key: Callable[[_Record], float],
    ) -> tuple[_Record, _Record]:
        """返回夹住 ``power`` 的相邻记录点；记录点按功率严格递增。

        记录区间外一律失败：仿真运行禁止外推，也不会静默改用最近的记录值。
        """
        first = records[0]
        last = records[-1]
        lowest = key(first)
        highest = key(last)
        if power < lowest - SIMULATION_POWER_TOLERANCE_DBM or (
            power > highest + SIMULATION_POWER_TOLERANCE_DBM
        ):
            raise SimulationPortError(
                f"{stage}读数 {frequency.key} GHz 只记录 [{lowest:.9g}, "
                f"{highest:.9g}] dBm 的功率点，本次请求 {power:.9g} dBm 超出记录"
                "范围；仿真运行禁止外推，请重新生成 readings.json"
            )
        if self.strict:
            raise SimulationPortError(
                f"严格模式只接受记录点：{stage}读数 {frequency.key} GHz 没有 "
                f"{power:.9g} dBm 的精确记录；请重新生成 readings.json"
            )
        lower = None
        upper = None
        for record in records:
            value = key(record)
            if value <= power + SIMULATION_POWER_TOLERANCE_DBM:
                lower = record
            if value >= power - SIMULATION_POWER_TOLERANCE_DBM:
                upper = record
                break
        if (
            lower is None
            or upper is None
            or abs(key(upper) - key(lower)) <= SIMULATION_POWER_TOLERANCE_DBM
        ):
            raise SimulationPortError(
                f"{stage}读数 {frequency.key} GHz 无法在相邻记录点之间插值 "
                f"{power:.9g} dBm；请重新生成 readings.json"
            )
        return lower, upper

    def _require_open(self, action: str) -> None:
        if self._closed:
            raise SimulationPortStateError(
                f"仿真端口已关闭，不能{action}；仿真运行需要重建端口（重新连接）后才能继续"
            )

    def _require_stage(
        self, expected: MeasurementType | None, action: str
    ) -> MeasurementType:
        self._require_open(action)
        stage = self._active_stage
        if stage is None:
            raise SimulationPortStateError(
                f"仿真端口未激活测量阶段，不能{action}；worker 工厂必须在启动前调用 "
                "activate(measurement_type)"
            )
        if expected is not None and stage is not expected:
            raise SimulationPortStateError(
                f"当前处于 {stage.value} 阶段，不能{action}；该动作只属于 "
                f"{expected.value} 阶段"
            )
        return stage

    def _require_frequency(self, stage: str) -> Frequency:
        if self._frequency_ghz is None:
            raise SimulationPortStateError(
                f"{stage}阶段读取读数前必须先设置信号源频率（set_frequency）"
            )
        return Frequency.from_ghz(self._frequency_ghz)

    def _require_power(self, stage: str) -> float:
        if self._power_dbm is None:
            raise SimulationPortStateError(
                f"{stage}阶段读取读数前必须先设置输出功率（set_power）"
            )
        return self._power_dbm

    def _require_amplifier_point(self, label: str) -> _AmplifierReading:
        point = self._amplifier_point
        if point is None:
            raise SimulationPortStateError(
                f"主功放阶段还没有消费任何测量点，不能读取{label}；端口把 V/I 绑定到"
                "最近一次成功的 measure_power_with_average()"
            )
        return point

    def _power_channel_key(
        self, supply_name: Any, channel: Any, action: str
    ) -> str:
        """结果文档里的通道键是 ``<电源>_<通道>``，与主功放结果契约保持一致。"""
        name = _require_text(supply_name, f"{action}的电源名称")
        channel_name = _require_text(channel, f"{action}的通道名")
        return f"{name}_{channel_name}"

    def _set_rf_enabled(self, enabled: bool) -> None:
        self._rf_enabled = bool(enabled)
        self.signal_gen.rf_enabled = self._rf_enabled

    def _record(self, command: str, value: Any = None) -> None:
        self._commands.append((command, value))

    def _record_interpolation(
        self, frequency: Frequency, stage: str, power: float
    ) -> None:
        count = self._interpolations.get(frequency, 0) + 1
        self._interpolations[frequency] = count
        logger.info(
            "仿真%s在 %s GHz 按 %.9g dBm 插值（本阶段第 %d 次）",
            stage,
            frequency.key,
            power,
            count,
        )
