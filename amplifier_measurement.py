"""主功放测量历史入口的兼容转发。

正式测量实现位于 :class:`measurement_services.AmplifierMeasurementService` 与
:class:`application.measurements.amplifier_test.AmplifierMeasurementUseCase`；
历史入口的装配适配器位于
:class:`app.legacy_amplifier_measurement.AmplifierMeasurement`。本模块只重新导出
历史公开符号，不包含任何业务实现、文件读写或隐式初始化。
"""

from app.legacy_amplifier_measurement import AmplifierMeasurement

__all__ = ["AmplifierMeasurement"]
