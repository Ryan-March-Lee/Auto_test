"""测量服务使用的仪器端口。

端口只描述业务所需的能力。所有数值单位写在方法名或参数注释中：
频率为 Hz，功率为 dBm，电压为 V，电流为 A，超时为秒。
适配器必须在前置条件不满足、超时或设备拒绝命令时抛出异常；端口不
规定自动重试，调用者应根据测量步骤决定是否重试。
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable


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
    """电源端口；channel 使用现场提供的物理通道名，不使用固定角色名。"""

    def connect(self, *, timeout_s: float = 10.0) -> None:
        """连接设备；失败抛出异常。"""

    def set_voltage(self, channel: str, voltage_v: float, *, timeout_s: float = 5.0) -> None:
        """设置通道电压；要求已连接且输出关闭。"""

    def set_current_limit(self, channel: str, current_a: float, *, timeout_s: float = 5.0) -> None:
        """设置通道电流保护；要求已连接，current_a 必须为非负数。"""

    def set_output_enabled(self, channel: str, enabled: bool, *, timeout_s: float = 5.0) -> None:
        """切换输出；上电顺序由应用安全策略保证，关闭操作应幂等。"""

    def read_voltage(self, channel: str, *, timeout_s: float = 5.0) -> float:
        """读取通道电压，单位 V。"""

    def read_current(self, channel: str, *, timeout_s: float = 5.0) -> float:
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
    """Legacy-compatible measurement port used during the stage 3 migration.

    The concrete VISA controller is adapted structurally to this port.  The
    service layer therefore depends on capabilities, not on InstrumentControl
    or a Qt/VISA implementation.
    """

    def set_power(self, power: float) -> None: ...
    def set_frequency(self, frequency: float) -> None: ...
    def set_center_frequency(self, frequency: float) -> None: ...
    def set_span(self, span: float) -> None: ...
    def rf_output_on(self) -> None: ...
    def rf_output_off(self) -> None: ...
    def measure_power_with_average(self) -> float: ...
    def setup_driver_amplifier_power(self) -> None: ...
    def power_on_driver(self) -> None: ...
    def power_off_driver(self) -> None: ...
    def setup_dut_power(self) -> None: ...
    def power_on_sequence(self) -> None: ...
    def power_off_sequence(self) -> None: ...
    def read_voltage(self, supply_name: str, channel: str) -> float: ...
    def read_current(self, supply_name: str, channel: str) -> float: ...
    def close_all(self, *, close_rf: bool = False) -> list[BaseException]: ...
