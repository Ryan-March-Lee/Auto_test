"""通用过渡与 Overlay 约定（技术债重构步骤 12）。

仪器卡片重构里真正重复出现的只有四件事，这一步只把它们固定下来，不引入通用动画
框架：

- **覆盖层生命周期**：页面级覆盖层
  （:class:`~presentation.qt.animation_overlay.AnimationOverlay`）铺满页面根容器、
  不进布局、不吃鼠标，层级与几何都由宿主决定，页面不再各自计算。
- **取消协议**：动画对象只回答 ``is_running()`` 与 ``cancel(*, reveal, reason)``，
  ``cancel()`` 自己停掉时间轴；页面登记的收尾入口另外回答“这一次是否真的收尾了”，
  由它恢复真实控件与流程状态。收尾原因使用本模块的固定名称，日志与测试因此能按同
  一个词复现（发牌层的字段见 :mod:`presentation.qt.animation_diagnostics`）。
- **完成信号**：自然播完时恰好发一次“整段结束”（发牌层是
  :attr:`~presentation.qt.card_deal_animation.CardDealAnimation.finished`，扫描等待
  牌堆是 :attr:`~presentation.qt.scan_card.ScanWaitStack.cycle_finished`）。被取消时
  动画不会重复发结束信号，由页面登记的收尾入口统一恢复真实控件与流程；页面只按这两条
  路径收尾，不读动画内部阶段。
- **生命周期**：宿主隐藏（切页、标签页切走、窗口最小化）时自动收尾；宿主尺寸或滚动
  变化时按“取消并恢复”的确定策略结束，而不是在动画中途追踪布局。定时器始终由动画
  自己停，页面不再逐个清理。

约定出现两次以上才会被抽到这里。欢迎页的按钮动画保持原样；扫描等待牌堆仍然自己
管自己的计时器——它只需要遵守“显示时走、隐藏时停”，不需要接入
:class:`OverlayLifecycle`。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from PySide6.QtCore import QObject

from .animation_overlay import AnimationOverlay

__all__ = [
    "CANCEL_REASON_PAGE_HIDDEN",
    "CANCEL_REASON_PAGE_SHUTDOWN",
    "CANCEL_REASON_SURFACE_CHANGED",
    "CancelableAnimation",
    "CancelEntry",
    "OverlayLifecycle",
]

#: 宿主尺寸或滚动变化：开始前冻结的动画坐标已失效。
CANCEL_REASON_SURFACE_CHANGED = "surface-changed"
#: 宿主隐藏或所在窗口最小化：看不见的过渡不该继续走。
CANCEL_REASON_PAGE_HIDDEN = "page-hidden"
#: 页面关闭：收尾但不恢复即将销毁的控件。
CANCEL_REASON_PAGE_SHUTDOWN = "page-shutdown"


@runtime_checkable
class CancelableAnimation(Protocol):
    """一次性过渡对象自身的能力（发牌/翻牌这类画面）。

    它只负责画面：``is_running()`` 说明是否还在播；``cancel(reveal=..., reason=...)``
    收尾并停掉自己的时间轴，不碰真实控件、也不改页面流程。收尾时它通过自己的信号把
    每一张交回页面（发牌层是 ``card_revealed``），自然播完时另发一次完成信号；页面只
    按这两条路径收尾，不读动画内部阶段。

    页面不应该把动画对象直接登记给 :class:`OverlayLifecycle`：页面还需要在收尾之后
    恢复卡片与流程状态，登记的是 :class:`CancelEntry`。
    """

    def is_running(self) -> bool:
        """这一次过渡是否还在走。"""

    def cancel(self, *, reveal: bool = True, reason: str = "unspecified") -> None:
        """中途收尾并停掉时间轴；``reason`` 取本模块的固定名称。"""


@runtime_checkable
class CancelEntry(Protocol):
    """页面登记的收尾入口（``InstrumentDiscoveryPanel.cancel_deal_reveal`` 实现它）。

    比动画对象多两件事：调用动画的 ``cancel(...)`` 之后恢复真实控件与流程；返回
    “这一次是否真的收尾了一个还在播的过渡”。没有在播时返回 ``False``，因此页面隐藏
    与面板隐藏两条路径先后触发时，后一次是幂等的空操作。
    """

    def __call__(
        self, *, reveal: bool = True, reason: str = "unspecified"
    ) -> bool:
        """收尾一次过渡；返回是否真的收尾了一个还在播的过渡。"""


class OverlayLifecycle(QObject):
    """页面级覆盖层的生命周期：取消协议 + “宿主变化就收尾”的规则。

    页面在构造时创建一份并登记收尾入口：

    .. code-block:: python

        self.animation_lifecycle = OverlayLifecycle(self.animation_overlay, parent=self)
        self.animation_lifecycle.attach(self.discovery_panel.cancel_deal_reveal)
        self.animation_lifecycle.watch_scrolling(self.scroll_area)

    此后页面不再自己接 resize / 滚动 / 隐藏事件，也不再自己判断定时器什么时候停：

    - 宿主尺寸变化（:attr:`AnimationOverlay.resized`）→ ``surface-changed``；
    - 登记过的滚动区滚动 → ``surface-changed``；
    - 宿主隐藏或所在窗口最小化（:attr:`AnimationOverlay.host_hidden`）→
      ``page-hidden``；
    - :meth:`shutdown`（页面关闭）→ ``page-shutdown``，不恢复即将销毁的控件。

    覆盖层层级（铺满宿主、不进布局、不吃鼠标、压在普通内容之上）由
    :class:`AnimationOverlay` 自己保证，页面不参与。
    """

    def __init__(
        self, overlay: AnimationOverlay, parent: QObject | None = None
    ) -> None:
        super().__init__(parent if parent is not None else overlay)
        self._overlay = overlay
        self._cancel: CancelEntry | None = None
        self._bars: list[Any] = []
        self._closed = False
        overlay.resized.connect(self._on_surface_changed)
        overlay.host_hidden.connect(self._on_host_hidden)

    # ---- 装配 ---------------------------------------------------------------

    def overlay(self) -> AnimationOverlay:
        """本生命周期看护的页面级覆盖层。"""
        return self._overlay

    def attach(self, entry: CancelEntry) -> None:
        """登记页面的收尾入口（:class:`CancelEntry`）。

        传 :meth:`InstrumentDiscoveryPanel.cancel_deal_reveal` 这类“页面自己的收尾入口”，
        而不是动画对象本身：页面还需要在收尾之后恢复卡片与流程状态，并且要回答这一次
        是否真的收尾了一个还在播的过渡。
        """
        if not callable(entry):
            raise TypeError(
                "收尾入口必须可调用：entry(*, reveal, reason) -> bool"
            )
        self._cancel = entry

    def watch_scrolling(self, *areas: Any) -> None:
        """登记滚动区：它们滚动会挪动已经冻结的覆盖层坐标。

        ``QScrollArea`` / ``QAbstractScrollArea`` 的纵横两个滚动条都接上，不假设页面
        将来只竖向滚动。
        """
        for area in areas:
            for bar in (area.verticalScrollBar(), area.horizontalScrollBar()):
                self._bars.append(bar)
                bar.valueChanged.connect(self._on_surface_changed)

    # ---- 收尾 ---------------------------------------------------------------

    def cancel(
        self, *, reveal: bool = True, reason: str = CANCEL_REASON_SURFACE_CHANGED
    ) -> bool:
        """按取消协议收尾；没有登记入口、已经关闭或没有在播时返回 ``False``。"""
        if self._closed:
            return False
        entry = self._cancel
        if entry is None:
            return False
        return bool(entry(reveal=reveal, reason=reason))

    def shutdown(self) -> bool:
        """页面关闭：按 ``page-shutdown`` 收尾并断开事件源；幂等。"""
        if self._closed:
            return False
        cancelled = self.cancel(reveal=False, reason=CANCEL_REASON_PAGE_SHUTDOWN)
        self._closed = True
        self._disconnect_sources()
        return cancelled

    def _on_surface_changed(self, *_args: Any) -> None:
        """宿主尺寸或登记过的滚动区变了：取消这一次过渡，不在中途追踪布局。"""
        if self._closed:
            return
        self.cancel(reveal=True, reason=CANCEL_REASON_SURFACE_CHANGED)

    def _on_host_hidden(self) -> None:
        """宿主被藏起来：看不见的过渡立即收尾，计时器由动画自己停。"""
        if self._closed:
            return
        self.cancel(reveal=True, reason=CANCEL_REASON_PAGE_HIDDEN)

    def _disconnect_sources(self) -> None:
        """断开已经登记的事件源；控件可能先于页面销毁，只吞掉“已断开”这一种失败。"""
        for signal, slot in (
            (self._overlay.resized, self._on_surface_changed),
            (self._overlay.host_hidden, self._on_host_hidden),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                continue
        for bar in self._bars:
            try:
                bar.valueChanged.disconnect(self._on_surface_changed)
            except (RuntimeError, TypeError):
                continue
        self._bars.clear()
