"""旧设备控制入口的历史兼容转发。

正式设备控制实现位于 ``instrument`` 包：设备会话、供电安全顺序、安全清理、
driver 与 transport 分别由 ``instrument.session``、``instrument.power_control``、
``instrument.safety``、``instrument.drivers`` 与 ``instrument.transport`` 承担，
生产硬件路径经 ``instrument.measurement_factory.create_measurement_port()`` 装配。

本模块只重新导出历史公开符号，不包含任何业务实现、VISA/SCPI 逻辑、配置文件
读写、全局设备状态或隐式初始化。历史内部 patch 点（``pyvisa.ResourceManager``、
``load_config_file``）已随实现迁到 :mod:`instrument.legacy_control`，不再属于受
支持的公开契约。
"""

from instrument.legacy_control import InstrumentControl
from instrument.power_roles import resolve_power_channel_role

__all__ = ["InstrumentControl", "resolve_power_channel_role"]
