"""测量服务使用的仪器端口。

端口只描述业务所需的能力。所有数值单位写在方法名或参数注释中：
频率为 Hz，功率为 dBm，电压为 V，电流为 A，超时为秒。
适配器必须在前置条件不满足、超时或设备拒绝命令时抛出异常；端口不
规定自动重试，调用者应根据测量步骤决定是否重试。
"""

from __future__ import annotations

from enum import Enum
from typing import Iterable, Protocol, cast, runtime_checkable


__all__ = [
    "DRIVER_POWER_CAPABILITIES",
    "DUT_POWER_CAPABILITIES",
    "SIGNAL_MEASUREMENT_CAPABILITIES",
    "InstrumentCapabilityError",
    "InstrumentSession",
    "InstrumentState",
    "MeasurementInstrumentPort",
    "PowerSupplyPort",
    "SignalGeneratorPort",
    "SpectrumAnalyzerPort",
    "require_measurement_capabilities",
]


class InstrumentState(str, Enum):
    CREATED = "created"
    VALIDATED = "validated"
    CONNECTED = "connected"
    PREPARED = "prepared"
    POWERED = "powered"
    MEASURING = "measuring"
    STOPPING = "stopping"
    CLEANED = "cleaned"


@runtime_checkable
class SignalGeneratorPort(Protocol):
    """信号源端口；创建后 RF 必须保持关闭。"""

    rf_enabled: bool
    """当前 RF 输出状态；由 :meth:`set_rf_enabled` 维护。"""

    def connect(self, *, timeout_s: float = 10.0) -> None:
        """连接设备；失败抛出异常，不改变 RF 开关状态。"""

    def set_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None:
        """设置频率；要求已连接，frequency_hz 必须为正数。"""

    def set_power_dbm(self, power_dbm: float, *, timeout_s: float = 5.0) -> None:
        """设置功率；要求已连接且 RF 仍关闭，失败不得自动打开 RF。"""

    def set_rf_enabled(self, enabled: bool, *, timeout_s: float = 5.0) -> None:
        """切换 RF；开启前必须已连接且已完成准备，关闭操作应尽力执行。"""

    def close(self, *, timeout_s: float = 5.0) -> None:
        """关闭连接；实现应幂等，且关闭前尽力关闭 RF。"""


@runtime_checkable
class SpectrumAnalyzerPort(Protocol):
    """频谱仪端口。"""

    def connect(self, *, timeout_s: float = 10.0) -> None:
        """连接设备；超时或连接失败抛出异常。"""

    def configure_center_frequency_hz(self, frequency_hz: float, *, timeout_s: float = 5.0) -> None:
        """设置中心频率；要求已连接，frequency_hz 必须为正数。"""

    def configure_bandwidth_hz(self, bandwidth_hz: float, *, timeout_s: float = 5.0) -> None:
        """设置测量带宽；要求已连接，bandwidth_hz 必须为正数。"""

    def measure_power_dbm(self, *, timeout_s: float = 10.0) -> float:
        """读取功率；要求已连接并完成配置，读数失败抛出异常。"""

    def close(self, *, timeout_s: float = 5.0) -> None:
        """关闭连接；实现应幂等。"""


@runtime_checkable
class PowerSupplyPort(Protocol):
    """电源端口；本项目支持设备通道 ``CH1`` 和 ``CH2``。"""

    def connect(self, *, timeout_s: float = 10.0) -> None:
        """连接设备；失败抛出异常。"""

    def set_voltage(self, channel: str, voltage_v: float, *, timeout_s: float = 5.0) -> None:
        """设置通道电压；要求已连接且输出关闭。"""

    def set_voltage_v(self, channel: str, voltage_v: float, *, timeout_s: float = 5.0) -> None:
        """设置通道电压，单位 V；要求已连接且输出关闭。"""

    def set_current_limit(self, channel: str, current_a: float, *, timeout_s: float = 5.0) -> None:
        """设置通道电流保护；要求已连接，current_a 必须为非负数。"""

    def set_current_limit_a(self, channel: str, current_a: float, *, timeout_s: float = 5.0) -> None:
        """设置通道电流限值，单位 A；要求已连接。"""

    def set_voltage_protection_state(self, channel: str, enabled: bool, *, timeout_s: float = 5.0) -> None:
        """设置过压保护状态；不改变输出状态。"""

    def set_current_protection_state(self, channel: str, enabled: bool, *, timeout_s: float = 5.0) -> None:
        """设置过流保护状态；不改变输出状态。"""

    def set_voltage_protection(
        self,
        channel: str,
        voltage_protection_v: float,
        *,
        timeout_s: float = 5.0,
    ) -> None:
        """设置过压保护阈值，单位 V。"""

    def set_current_protection(
        self,
        channel: str,
        current_protection_a: float,
        *,
        timeout_s: float = 5.0,
    ) -> None:
        """设置过流保护阈值，单位 A。"""

    def set_output_enabled(self, channel: str, enabled: bool, *, timeout_s: float = 5.0) -> None:
        """切换输出；上电顺序由应用安全策略保证，关闭操作应幂等。"""

    def read_voltage(self, channel: str, *, timeout_s: float = 5.0) -> float:
        """读取通道电压，单位 V。"""

    def read_voltage_v(self, channel: str, *, timeout_s: float = 5.0) -> float:
        """读取通道电压，单位 V。"""

    def read_current(self, channel: str, *, timeout_s: float = 5.0) -> float:
        """读取通道电流，单位 A。"""

    def read_current_a(self, channel: str, *, timeout_s: float = 5.0) -> float:
        """读取通道电流，单位 A。"""

    def close(self, *, timeout_s: float = 5.0) -> None:
        """关闭连接；实现应幂等。"""


@runtime_checkable
class InstrumentSession(Protocol):
    """仪器集合的生命周期协议。

    实现必须按 ``created -> validated -> connected -> prepared`` 推进，
    测量期间可进入 ``powered``/``measuring``。close 必须幂等，并尽力按
    RF 关闭、供电关闭、连接关闭的顺序清理所有资源。
    """

    state: InstrumentState

    def validate(self, *, timeout_s: float = 10.0) -> None:
        """离线校验资源和端口；失败时不得连接、上电或打开 RF。"""

    def connect(self, *, timeout_s: float = 30.0) -> None:
        """连接全部端口；部分失败也必须允许后续 close 清理已连接资源。"""

    def prepare(self, *, timeout_s: float = 30.0) -> None:
        """配置端口但保持 RF 和电源输出关闭。"""

    def close(self, *, emergency: bool = False, timeout_s: float = 30.0) -> None:
        """执行幂等清理；emergency=True 时首先关闭 RF。"""


@runtime_checkable
class MeasurementInstrumentPort(Protocol):
    """三类正式测量服务共同使用的设备控制能力。

    实现可以是 VISA 控制器、:class:`instrument.measurement_adapter.PortMeasurementAdapter`
    或离线仿真端口；服务只依赖这里声明的方法，不再接受任意动态设备对象。
    单位沿用历史测量契约（适配器负责换算到各设备端口单位）：

    - ``set_frequency`` / ``set_center_frequency`` 接收 GHz；
    - ``set_span`` 接收 MHz；
    - ``set_power`` 接收 dBm，``measure_power_with_average`` 返回 dBm；
    - ``read_voltage`` 返回 V，``read_current`` 返回 A。

    实现必须在前置条件不满足、超时或设备拒绝命令时抛出异常；端口不规定自动
    重试，调用者按测量步骤决定。服务在构造阶段用
    :func:`require_measurement_capabilities` 校验所需能力，能力不足时抛出
    :class:`InstrumentCapabilityError`，不会等到测量途中才发现缺失方法。
    """

    def set_power(self, power: float) -> None:
        """设置信号源输出功率，单位 dBm。"""

    def set_frequency(self, frequency: float) -> None:
        """设置信号源频率，单位 GHz。"""

    def set_center_frequency(self, frequency: float) -> None:
        """设置频谱仪中心频率，单位 GHz。"""

    def set_span(self, span: float) -> None:
        """设置频谱仪扫描跨度，单位 MHz。"""

    def rf_output_on(self) -> None:
        """打开 RF 输出；失败不得留下部分开启状态。"""

    def rf_output_off(self) -> None:
        """关闭 RF 输出；安全清理会尽力执行。"""

    def measure_power_with_average(self) -> float:
        """读取平均功率，单位 dBm。"""

    def setup_driver_amplifier_power(self) -> None:
        """按配置设置驱动放大器电源通道，保持输出关闭。"""

    def power_on_driver(self) -> None:
        """按上电顺序打开驱动放大器电源输出。"""

    def power_off_driver(self) -> None:
        """关闭驱动放大器电源输出；实现应幂等。"""

    def setup_dut_power(self) -> None:
        """按配置设置 DUT 电源通道，保持输出关闭。"""

    def power_on_sequence(self) -> None:
        """按上电顺序打开 DUT 电源输出。"""

    def power_off_sequence(self) -> None:
        """按下电顺序关闭 DUT 电源输出；实现应幂等。"""

    def read_voltage(self, supply_name: str, channel: str) -> float:
        """读取指定电源通道的电压，单位 V。"""

    def read_current(self, supply_name: str, channel: str) -> float:
        """读取指定电源通道的电流，单位 A。"""

    def close_all(self, *, close_rf: bool = False) -> list[BaseException]:
        """释放全部连接并返回未能完成的清理错误。"""


class InstrumentCapabilityError(RuntimeError):
    """设备对象缺少测量服务必需的端口能力。"""

    def __init__(self, requirement: str, missing: Iterable[str]) -> None:
        self.requirement = requirement
        self.missing = tuple(missing)
        details = ", ".join(self.missing)
        super().__init__(
            f"{requirement} 缺少设备能力: {details}" if details else f"{requirement} 缺少设备能力"
        )


# 三类测量共用的信号通路能力；其余能力按服务显式追加。
SIGNAL_MEASUREMENT_CAPABILITIES: tuple[str, ...] = (
    "set_frequency",
    "set_power",
    "set_center_frequency",
    "set_span",
    "rf_output_on",
    "rf_output_off",
    "measure_power_with_average",
)

# 驱动放大器电源能力：驱动功率映射必用，主功放仅在驱动模式开启时必用。
DRIVER_POWER_CAPABILITIES: tuple[str, ...] = (
    "setup_driver_amplifier_power",
    "power_on_driver",
    "power_off_driver",
)

# DUT 电源能力：主功放扫描读取电压、电流并按下电顺序关闭输出。
DUT_POWER_CAPABILITIES: tuple[str, ...] = (
    "setup_dut_power",
    "power_on_sequence",
    "power_off_sequence",
    "read_voltage",
    "read_current",
)


def require_measurement_capabilities(
    instrument: object,
    *,
    requirement: str,
    capabilities: tuple[str, ...] = SIGNAL_MEASUREMENT_CAPABILITIES,
) -> MeasurementInstrumentPort:
    """校验设备对象具备测量所需能力并返回同一对象。

    能力缺失（含 ``None``）时抛出 :class:`InstrumentCapabilityError`，让服务在
    构造阶段失败，而不是在硬件动作中途抛 ``AttributeError``。
    """
    if instrument is None:
        raise InstrumentCapabilityError(requirement, capabilities)
    missing = tuple(
        name for name in capabilities if not callable(getattr(instrument, name, None))
    )
    if missing:
        raise InstrumentCapabilityError(requirement, missing)
    return cast(MeasurementInstrumentPort, instrument)
