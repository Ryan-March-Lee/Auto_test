"""设备发现端口：扫描模式词表、演示网段与“发现无法继续”的失败类型。

表现层（设备发现面板）只需要这三样东西：下拉框里的模式名、模拟扫描的默认网段，
以及把失败显示给用户的异常类型。具体扫描器由装配根构造后注入，基础设施实现本端口；
面板不 import 任何基础设施模块，离线测试与现场扫描共用同一套界面逻辑。
"""

from __future__ import annotations

# 扫描模式：网络扫描探测现场网段，模拟扫描回放演示设备表。
SCAN_MODE_NETWORK = "network"
SCAN_MODE_SIMULATED = "simulated"
# 离线演示使用的固定 /24 网段；只用于模拟模式，不会被当成现场扫描结果。
DEMO_SUBNET = "192.168.1.0/24"


class DeviceDiscoveryError(RuntimeError):
    """设备发现无法继续：扫描参数非法、设备表不可读或扫描器拒绝执行。"""


__all__ = [
    "DEMO_SUBNET",
    "DeviceDiscoveryError",
    "SCAN_MODE_NETWORK",
    "SCAN_MODE_SIMULATED",
]
