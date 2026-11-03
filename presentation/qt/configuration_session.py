"""配置会话协调器（GUI 重构步骤 8）：连接页与配置页之间的窄接口。

“仪表配置”页（:mod:`presentation.qt.instrument_setup_page`）不再持有配置页实例，也不再读它的
``device_bindings`` / ``config``。它只依赖 :class:`application.ports.device_binding.DeviceBindingPort`：

- :meth:`ConfigurationSession.view_state` 转发配置页的只读视图
  :class:`application.configuration_view_model.ConfigurationViewState`；
- :meth:`ConfigurationSession.apply_selection` 把 ``ApplyDeviceSelectionRequest`` 交给
  配置页，由配置页委托视图模型与 ``DeviceBindingService`` 写入，并投影派生界面；
- 配置页发出 ``device_selection_finished(result, message)`` 后，本会话把提示文案与新的
  绑定集合回写给连接页的设备发现面板。面板归连接页所有，配置页不持有它。

本模块属于 presentation controller：只做装配与转发，不读控件、不定义设备绑定规则，也不
写文件或连接仪器。路径规划中的“MainWindow 或配置会话协调器负责把连接页结果交给
ViewModel”由这里承担；连接意图不再经过本会话，参数配置页的最终按钮直接发出最终动作。

依赖方向：本模块只认结构类型 :class:`ConfigurationBindingTarget` 与
:class:`DiscoveryPanelPort`（两个 Protocol），不把具体 ``ConfigurationPage`` / ``QWidget`` 写进
接口，因此会话可以用纯 Python 替身单独测试。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from application.configuration_view_model import ConfigurationViewState
from application.device_binding import ApplyDeviceSelectionRequest, DeviceBindingResult

__all__ = ["ConfigurationSession"]


class ConfigurationBindingTarget(Protocol):
    """配置页提供给会话的窄接口（结构类型，不绑定具体 QWidget）。

    ``discovery_message`` 与 ``device_selection_finished`` 是 Qt 信号；这里只声明它们存在，
    会话通过 ``connect`` / ``emit`` 使用，不需要导入 PySide6。
    """

    device_bindings: Mapping[str, Any]
    binding_problems: Sequence[str]
    discovery_message: Any
    device_selection_finished: Any

    def view_state(self) -> ConfigurationViewState:
        """返回连接页读取的只读配置视图。"""

    def apply_device_selection(
        self, request: ApplyDeviceSelectionRequest
    ) -> DeviceBindingResult:
        """写入一次勾选结果，返回新的绑定集合或结构化问题。"""


class DiscoveryPanelPort(Protocol):
    """连接页设备发现面板的窄接口：只用于回写状态，不涉及扫描逻辑。"""

    log_message: Any

    def set_device_bindings(self, bindings: Any) -> None:
        """登记当前绑定，供下一次扫描按身份预选。"""

    def show_message(self, message: str) -> None:
        """在结果区状态行显示一次写入结果。"""


class ConfigurationSession:
    """把连接页的窄接口转给配置页的视图模型与派生界面。"""

    def __init__(self, target: ConfigurationBindingTarget) -> None:
        self._target = target
        self._panel: DiscoveryPanelPort | None = None

    def view_state(self) -> ConfigurationViewState:
        """返回连接页读取的只读配置视图。"""
        return self._target.view_state()

    def apply_selection(
        self, request: ApplyDeviceSelectionRequest
    ) -> DeviceBindingResult:
        """应用一次设备勾选结果；业务规则仍只在 application 边界。"""
        if not isinstance(request, ApplyDeviceSelectionRequest):
            raise TypeError(
                "request 必须是 ApplyDeviceSelectionRequest，"
                f"收到 {type(request).__name__}"
            )
        return self._target.apply_device_selection(request)

    def attach_discovery_panel(self, panel: DiscoveryPanelPort) -> None:
        """登记连接页的设备发现面板，接上日志转发与写入结果回写。

        面板归连接页所有；本会话是唯一回写它的地方：扫描日志经配置页的
        ``discovery_message`` 转给窗口；配置页每次写入结束（成功或拒绝）都会发出
        ``device_selection_finished``，这里据此更新面板的绑定与状态行。同一个面板重复
        登记是幂等的，换成另一个面板实例会拒绝，避免同一个信号被接到两块面板上。
        """
        if panel is self._panel:
            return
        if self._panel is not None:
            raise RuntimeError("设备发现面板已经登记，不能换成另一个面板实例")
        self._panel = panel
        panel.log_message.connect(self._target.discovery_message.emit)
        panel.set_device_bindings(dict(self._target.device_bindings))
        if self._target.binding_problems:
            panel.show_message(
                "；".join(self._target.binding_problems)
                + "；请重新扫描并应用这些电源，或修正配置后再保存"
            )
        self._target.device_selection_finished.connect(self._on_selection_finished)

    def _on_selection_finished(self, result: DeviceBindingResult, message: str) -> None:
        """把配置页的写入结果回写到发现面板（只在成功时刷新绑定）。"""
        panel = self._panel
        if panel is None:
            return
        if result.ok:
            panel.set_device_bindings(dict(self._target.device_bindings))
        panel.show_message(message)
