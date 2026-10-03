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


class PageControllerBindingMixin:
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


class BasePage(PageControllerBindingMixin, ABC):
    """可由纯 Python 页面继承的抽象页面基类。"""

    @abstractmethod
    def build_ui(self) -> Any:
        """创建并返回页面根 widget。"""


@dataclass(frozen=True)
class PageContext:
    """页面构造所需的窄组合接口。

    页面定义只消费这些回调和共享 controller，不需要知道主窗口的实现。
    """

    controller: Any
    config_path_provider: Callable[[], str]
    prepare_cable_loss: Callable[[], bool]
    confirm_cable_loss: Callable[[], bool]
    prepare_driver_mapping: Callable[[], bool]
    confirm_driver_mapping: Callable[[], bool]
    prepare_amplifier: Callable[[], bool]
    confirm_amplifier: Callable[[], bool]
    driver_mode_provider: Callable[[], bool]
    connection_dialog_factory: Callable[[str, Any], Any]
    plot_widget_factory: Callable[[Any], Any]
    load_results_callback: Callable[[], Any]
    log_callback: Callable[[str], Any]
    progress_callback: Callable[[int], Any]
    error_callback: Callable[[str], Any]
    driver_realtime_data_callback: Callable[[Any], Any]
    amplifier_realtime_data_callback: Callable[[Any], Any]
    clear_realtime_callback: Callable[[], Any]
    build_configuration: Callable[[], Any]
    build_visualization: Callable[[], Any]
    build_export: Callable[[], Any]


@dataclass(frozen=True)
class PageDefinition:
    key: str
    title: str
    builder: Callable[[Any], Any]


def _build_cable_loss_page(context: PageContext) -> Any:
    from .cable_loss_page import CableLossPage

    page = CableLossPage(
        config_path_provider=context.config_path_provider,
        prepare_run=context.prepare_cable_loss,
        confirm_wiring=context.confirm_cable_loss,
        connection_dialog_factory=context.connection_dialog_factory,
        load_results_callback=context.load_results_callback,
        log_callback=context.log_callback,
        progress_callback=context.progress_callback,
        error_callback=context.error_callback,
    )
    page.bind_controller(context.controller)
    return page


def _build_driver_mapping_page(context: PageContext) -> Any:
    from .driver_mapping_page import DriverMappingPage

    page = DriverMappingPage(
        config_path_provider=context.config_path_provider,
        prepare_run=context.prepare_driver_mapping,
        confirm_wiring=context.confirm_driver_mapping,
        connection_dialog_factory=context.connection_dialog_factory,
        plot_widget_factory=context.plot_widget_factory,
        realtime_data_callback=context.driver_realtime_data_callback,
        clear_realtime_callback=context.clear_realtime_callback,
        log_callback=context.log_callback,
        progress_callback=context.progress_callback,
        error_callback=context.error_callback,
    )
    page.bind_controller(context.controller)
    return page


def _build_amplifier_page(context: PageContext) -> Any:
    from .amplifier_page import AmplifierPage

    page = AmplifierPage(
        config_path_provider=context.config_path_provider,
        prepare_run=context.prepare_amplifier,
        confirm_wiring=context.confirm_amplifier,
        driver_mode_provider=context.driver_mode_provider,
        connection_dialog_factory=context.connection_dialog_factory,
        plot_widget_factory=context.plot_widget_factory,
        realtime_data_callback=context.amplifier_realtime_data_callback,
        clear_realtime_callback=context.clear_realtime_callback,
        log_callback=context.log_callback,
        progress_callback=context.progress_callback,
        error_callback=context.error_callback,
    )
    page.bind_controller(context.controller)
    return page


PAGE_DEFINITIONS = (
    PageDefinition("configuration", "仪器配置", lambda context: context.build_configuration()),
    PageDefinition("cable_loss", "线损测量", _build_cable_loss_page),
    PageDefinition("driver_mapping", "驱动映射", _build_driver_mapping_page),
    PageDefinition("amplifier", "功放测试", _build_amplifier_page),
    PageDefinition("visualization", "数据可视化", lambda context: context.build_visualization()),
    PageDefinition("export", "数据导出", lambda context: context.build_export()),
)


def build_pages(context: Any) -> list[Any]:
    """Build and return pages in the stable user-facing order."""
    pages = []
    for page in PAGE_DEFINITIONS:
        pages.append((page, page.builder(context)))
    return pages


__all__ = [
    "PageProtocol",
    "PageControllerBindingMixin",
    "BasePage",
    "PageDefinition",
    "PageContext",
    "PAGE_DEFINITIONS",
    "build_pages",
]
