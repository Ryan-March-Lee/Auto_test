"""测量生命周期清理的历史兼容转发。

正式实现位于设备安全层 :func:`instrument.flow.cleanup.cleanup_measurement`。
本模块只重新导出历史公开符号，不包含任何清理实现、日志配置或设备状态。
"""

from instrument.flow.cleanup import cleanup_measurement

__all__ = ["cleanup_measurement"]
