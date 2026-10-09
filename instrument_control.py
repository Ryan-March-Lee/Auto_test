"""旧设备控制入口的兼容门面。

正式设备控制实现位于 ``instrument`` 包。该模块只保留历史导入路径、构造
参数和测试 patch 点，不再新增 VISA/SCPI 或安全控制逻辑。
"""

import pyvisa

from config_io import load_config_file
from project_paths import CONFIG_FILE, resolve_path
from app_logging import get_logger
from instrument.power_roles import resolve_power_channel_role
from instrument.legacy_control import InstrumentControl as _InstrumentControl


class InstrumentControl(_InstrumentControl):
    """兼容旧 ``instrument_control.InstrumentControl`` 的转发类。"""

    def __init__(self, *args, **kwargs):
        # 保留历史测试和外部集成对旧模块 patch 路径的支持；业务实现仍
        # 只存在于 instrument.legacy_control，并最终迁移到正式工厂。
        import instrument.legacy_control as implementation

        implementation.pyvisa = pyvisa
        implementation.load_config_file = load_config_file
        implementation.CONFIG_FILE = CONFIG_FILE
        implementation.resolve_path = resolve_path
        implementation.resolve_power_channel_role = resolve_power_channel_role
        implementation.logger = get_logger(implementation.__name__)
        super().__init__(*args, **kwargs)


__all__ = ["InstrumentControl", "resolve_power_channel_role"]
