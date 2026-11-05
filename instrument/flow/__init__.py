"""可复用的设备安全流程。

流程层只组合 action，不拼接 SCPI。这里的流程可以使用真实 driver、仿真
driver 或测试 double，因此不会把安全策略绑定到 VISA 实现。
"""

from .safety_shutdown_flow import SafetyShutdownFlow, SafetyShutdownError
from .cleanup import SafetyCleanupAdapter, cleanup_measurement

__all__ = [
    "SafetyCleanupAdapter",
    "SafetyShutdownFlow",
    "SafetyShutdownError",
    "cleanup_measurement",
]
