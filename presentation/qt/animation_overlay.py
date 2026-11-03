"""页面级动画覆盖层：铺满页面根容器，只负责覆盖绘制与鼠标穿透。

技术债重构步骤 6：发牌/翻牌原先画在结果区（``results_box``）里，于是动画坐标要跟着
``QScrollArea`` 的滚动、页头收放与结果区布局一起换算。这里把覆盖层挂到页面的稳定根
容器上，与普通内容同级：

.. code-block:: text

    ConnectionPage
    └── overlay_host
        ├── normal_content      # .ui 骨架（含滚动区、设备发现面板）
        └── animation_overlay   # 本模块，不进布局

两个约束是这一层存在的理由：

- **不进任何布局**：覆盖层自己钉在宿主的矩形上，不参与排版，因此它的出现、消失与
  几何变化都不会推动页面里的控件。
- **不吃鼠标、不占焦点**：``WA_TransparentForMouseEvents`` + ``NoFocus``，点击、悬停与
  键盘焦点都落到下面的普通内容；它只是画面。

覆盖层坐标系就是页面根容器的坐标系：卡片落点在动画开始前一次性换算到这里（见
:meth:`DeviceDiscoveryPanel._start_deal_reveal`），之后滚动或内部布局变化都不会再
影响已经开始的动画。宿主尺寸变化时发出 :attr:`resized`，宿主被藏起来时发出
:attr:`host_hidden`；页面把这些事件交给
:class:`presentation.qt.animation_lifecycle.OverlayLifecycle`，按“取消并恢复”的确定
策略收尾，而不是在动画中途逐帧追踪复杂布局。

跨分支换算必须用 :func:`widget_rect_in`：覆盖层与卡片位于页面根容器的两个分支，
``QWidget.mapTo()`` 只对祖先关系可靠，直接映射会得到屏幕坐标而不是覆盖层坐标。
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QRect, QRectF, Qt, Signal
from PySide6.QtWidgets import QWidget

__all__ = ["AnimationOverlay", "widget_rect_in"]


def widget_rect_in(
    widget: QWidget, target: QWidget, rect: QRect | None = None
) -> QRectF:
    """把 ``widget`` 的矩形换算到 ``target`` 的坐标系。

    ``QWidget.mapTo(target, ...)`` 只在 ``target`` 是 ``widget`` 的祖先时可靠；覆盖层
    与结果卡片分别挂在页面根容器的两个分支上，直接映射会返回屏幕坐标（窗口一移动
    落点就偏）。这里统一经屏幕坐标中转，结果只取决于两者的相对位置，与窗口在桌面
    上的位置无关。

    ``rect`` 省略时取 ``widget.rect()``；返回值是逻辑像素矩形，与 Qt 控件几何一致。
    """
    source = widget.rect() if rect is None else QRect(rect)
    origin = target.mapFromGlobal(widget.mapToGlobal(source.topLeft()))
    return QRectF(
        float(origin.x()),
        float(origin.y()),
        float(source.width()),
        float(source.height()),
    )


class AnimationOverlay(QWidget):
    """页面级覆盖画布：铺满宿主、鼠标穿透、不进布局。

    宿主（通常是页面根容器）拥有它，普通内容与它同级；覆盖层的几何始终等于
    :meth:`host` 的 ``rect()``。它自己什么也不画，由动画对象（目前是
    :class:`presentation.qt.card_deal_animation.CardDealAnimation`）作为子控件铺满它。
    """

    #: 宿主尺寸变了：已经换算好的动画坐标因此失效。
    resized = Signal()
    #: 宿主被藏起来了（切页、标签页切走、窗口最小化）：正在播的动画该收尾了。
    host_hidden = Signal()

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("pageAnimationOverlay")
        # 只是画面：点击、悬停与键盘焦点都留给下面的普通内容（该属性对子控件同样生效）。
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self._host = host
        host.installEventFilter(self)
        self.sync_geometry()

    # ---- 宿主 ---------------------------------------------------------------

    def host(self) -> QWidget:
        """覆盖层所在的页面根容器。"""
        return self._host

    def sync_geometry(self) -> None:
        """把覆盖层钉回宿主矩形；可见时压在普通内容之上。"""
        host = getattr(self, "_host", None)
        if host is None:
            return
        self.setGeometry(host.rect())
        if self.isVisible():
            self.raise_()

    # ---- 事件 ---------------------------------------------------------------

    def eventFilter(self, watched: Any, event: Any) -> bool:
        """宿主尺寸变化或藏起来：先跟上宿主，再通知页面发生了什么。

        ``_host`` 用 ``getattr`` 读：宿主销毁时 Qt 仍可能把事件送到这里，而此时 Python
        包装对象上的属性可能已经清掉，不能在这条路径上抛 ``AttributeError``。
        """
        host = getattr(self, "_host", None)
        if watched is host and event.type() == QEvent.Type.Resize:
            self.sync_geometry()
            self.resized.emit()
        elif watched is host and event.type() == QEvent.Type.Hide:
            self.host_hidden.emit()
        return super().eventFilter(watched, event)

    def showEvent(self, event):  # noqa: N802 - Qt 覆盖方法
        self.sync_geometry()
        self.raise_()
        super().showEvent(event)
