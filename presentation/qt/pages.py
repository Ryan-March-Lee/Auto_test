"""Page registration and lifecycle contracts for Qt pages.

The legacy window still owns the widgets during the transition.  Keeping the
page list here makes the ownership explicit and gives later page extraction a
single integration point without changing the user workflow in this stage.

The contracts in this module deliberately do not import Qt, workers, runtime
assembly, or measurement services.  A page may therefore be tested with a
plain Python fake controller before it is connected to the real GUI.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable


@runtime_checkable
class PageProtocol(Protocol):
    """最小页面生命周期协议。

    页面实现必须把 controller signal 的连接和解除连接放在自己的生命周期
    内。``close`` 是销毁页面时的统一入口，必须是幂等操作。
    """

    def build_ui(self) -> Any:
        """创建并返回页面根 widget。"""

    def bind_controller(self, controller: Any) -> None:
        """绑定共享 controller，并连接页面所需的事件。"""

    def on_activated(self) -> None:
        """页面成为当前页面时调用。"""

    def on_deactivated(self) -> None:
        """页面离开当前页面时调用。"""

    def close(self) -> None:
        """解除 controller signal 连接并释放页面资源。"""


class BasePage(ABC):
    """页面生命周期的无 Qt 基类。

    子类只需实现 ``build_ui``，并在 ``_connect_controller_signals`` 中连接
    controller 事件。基类会在重新绑定、停用和关闭时统一解除已登记的
    signal 连接，避免页面销毁后继续接收测量事件。
    """

    def __init__(self) -> None:
        self._controller: Any | None = None
        self._controller_connections: list[tuple[Any, Callable[..., Any]]] = []
        self._closed = False
        self._deactivated = False

    @property
    def controller(self) -> Any | None:
        """当前绑定的 controller；页面未绑定时为 ``None``。"""
        return self._controller

    @abstractmethod
    def build_ui(self) -> Any:
        """创建并返回页面根 widget。"""

    def bind_controller(self, controller: Any) -> None:
        """绑定 controller，并让子类建立自己的 signal 连接。"""
        if controller is None:
            raise TypeError("controller 不能为 None")
        if self._closed:
            raise RuntimeError("页面已关闭，不能重新绑定 controller")
        cleanup_errors = self._disconnect_controller_signals()
        if cleanup_errors:
            raise self._cleanup_error("解除旧 controller signal 连接失败", cleanup_errors)
        self._controller = controller
        try:
            self._connect_controller_signals(controller)
        except Exception as error:
            rollback_errors = self._disconnect_controller_signals()
            self._controller = None
            if rollback_errors:
                raise self._cleanup_error(
                    f"绑定 controller 失败: {error}；回滚 signal 连接失败",
                    rollback_errors,
                ) from error
            raise

    def _connect_controller_signals(self, controller: Any) -> None:
        """子类覆盖此方法并通过 ``_connect_signal`` 登记连接。"""

    def _connect_signal(self, signal: Any, slot: Callable[..., Any]) -> None:
        """连接一个 signal，并登记其 slot 以便后续解除连接。"""
        connect = getattr(signal, "connect", None)
        disconnect = getattr(signal, "disconnect", None)
        if connect is None or disconnect is None:
            raise TypeError("controller signal 必须提供 connect/disconnect")
        connect(slot)
        self._controller_connections.append((signal, slot))

    def on_activated(self) -> None:
        """页面成为当前页面时的扩展点。"""

    def on_deactivated(self) -> None:
        """页面离开当前页面时的扩展点。"""

    def close(self) -> None:
        """幂等地解除所有 controller signal 连接。"""
        hook_errors: list[Exception] = []
        if not self._deactivated:
            self._deactivated = True
            try:
                self.on_deactivated()
            except Exception as error:
                hook_errors.append(error)
        cleanup_errors = self._disconnect_controller_signals()
        self._controller = None
        self._closed = True
        errors = hook_errors + cleanup_errors
        if errors:
            raise self._cleanup_error("页面关闭清理失败", errors)

    def _disconnect_controller_signals(self) -> list[Exception]:
        """尝试解除全部连接，并保留失败项以便后续重试。"""
        remaining: list[tuple[Any, Callable[..., Any]]] = []
        errors: list[Exception] = []
        connections = self._controller_connections
        self._controller_connections = []
        for signal, slot in connections:
            try:
                signal.disconnect(slot)
            except Exception as error:
                remaining.append((signal, slot))
                errors.append(error)
        self._controller_connections = remaining
        return errors

    @staticmethod
    def _cleanup_error(message: str, errors: list[Exception]) -> RuntimeError:
        details = "; ".join(str(error) or type(error).__name__ for error in errors)
        return RuntimeError(f"{message}: {details}")


@dataclass(frozen=True)
class PageDefinition:
    key: str
    title: str
    builder: Callable[[Any], Any]


PAGE_DEFINITIONS = (
    PageDefinition("configuration", "仪器配置", lambda window: window.create_config_tab()),
    PageDefinition("cable_loss", "线损测量", lambda window: window.create_cable_loss_tab()),
    PageDefinition("driver_mapping", "驱动映射", lambda window: window.create_driver_mapping_tab()),
    PageDefinition("amplifier", "功放测试", lambda window: window.create_amplifier_test_tab()),
    PageDefinition("visualization", "数据可视化", lambda window: window.create_visualization_tab()),
    PageDefinition("export", "数据导出", lambda window: window.create_data_export_tab()),
)


def build_pages(window: Any) -> None:
    """Build the pages owned by *window* in the stable user-facing order."""
    for page in PAGE_DEFINITIONS:
        page.builder(window)


__all__ = [
    "PageProtocol",
    "BasePage",
    "PageDefinition",
    "PAGE_DEFINITIONS",
    "build_pages",
]
