"""设备绑定端口（GUI 重构步骤 8）：连接页与配置会话之间的窄接口。

表现层的“仪表配置”页只需要两件事：

- 读取当前配置的只读视图 :class:`application.configuration_view_model.ConfigurationViewState`
  （绑定集合、配置里已保存的仪器地址、旧绑定条数），用于填扫描框初值与写日志；
- 提交本次勾选结果 :class:`application.device_binding.ApplyDeviceSelectionRequest`，
  拿到 :class:`application.device_binding.DeviceBindingResult`。

写入仍只由 ``DeviceBindingService`` 在 application 边界完成；本端口不读控件、不写文件、
不创建仪器，也不更新任何 Qt 控件。调用方（连接页）只依赖这两个方法，因此可以注入
替身单独测试，不再需要另一个页面实例。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from application.configuration_view_model import ConfigurationViewState
    from application.device_binding import ApplyDeviceSelectionRequest, DeviceBindingResult

__all__ = ["DeviceBindingPort"]


@runtime_checkable
class DeviceBindingPort(Protocol):
    """连接页需要的设备绑定窄接口。"""

    def view_state(self) -> ConfigurationViewState:
        """返回当前配置的只读视图（扫描框初值与旧绑定提示）。"""

    def apply_selection(self, request: ApplyDeviceSelectionRequest) -> DeviceBindingResult:
        """应用一次设备勾选结果，返回新的绑定集合或结构化问题。"""
