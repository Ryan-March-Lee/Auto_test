"""测量服务的历史兼容转发。

正式实现位于 :mod:`application.measurements.services`；本模块只重新导出历史
公开符号，不包含任何业务实现、设备动作或文件读写。新的生产代码应直接使用
``application.measurements.services``（或对应的 application 用例）。

受支持的兼容 API 是 ``__all__`` 中列出的服务层符号。历史版本偶然暴露的
app 契约（取消令牌、事件类型）、计算函数与端口协议这里仅作为别名转发以降低
破坏性，不再属于承诺的兼容契约；请改用 ``app.cancellation``、``app.events``、
``domain.measurement.calculations``、``instrument.flow.cleanup`` 等正式路径。
"""

from application.measurements.services import (
    AmplifierMeasurementService,
    CableLossService,
    DriverPowerMappingService,
    NullEventSink,
    ResultRepository,
    _Service,
    _publish,
)

# 历史实现细节：尽量保留为模块属性别名，避免外部脚本出现 ImportError，
# 但不在 __all__ 中被承诺，也不在此复制任何实现。
from app.cancellation import CancellationToken
from app.events import (
    CheckpointEvent,
    EventSink,
    MessageEvent,
    ProgressEvent,
    RealtimeDataEvent,
    StoppedEvent,
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
from instrument.flow.cleanup import cleanup_measurement
from instrument.ports import MeasurementInstrumentPort

__all__ = [
    "ResultRepository",
    "NullEventSink",
    "CableLossService",
    "DriverPowerMappingService",
    "AmplifierMeasurementService",
]
