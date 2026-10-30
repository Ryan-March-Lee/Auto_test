"""防止鼠标滚轮误改数值的输入控件。

这些控件忽略滚轮事件：滚动页面时不会意外改变参数。滚轮事件被忽略后会继续
传递给父控件，因此页面仍可正常滚动。下拉框仍可通过鼠标点击展开或键盘选择。

样式表给数值框设置 ``padding`` 后，Qt 会把内部行编辑框铺满内容区，连步进按钮
一起盖住。上箭头正好画在被盖住的一段里，点击被行编辑框收下：数值框既收不到
按下事件（按钮没有下沉动画），也收不到步进，于是“上箭头点了没反应、下箭头
正常”。基类因此先把行编辑框收窄到按钮左侧，让点击重新落到数值框上，交给 Qt
原生处理；另外保留两道兜底，应对平台样式绘制与命中区域仍有几像素偏差的情况。

本模块只提供控件类本身（输入行为）；页面通用的数值框/下拉框构建在
:mod:`presentation.qt.config_page_widgets`，不在这里。
"""

from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QSpinBox, QStyle, QStyleOptionSpinBox,
)


class _SpinButtonStepFallback:
    """让步进按钮的点击、动画都不依赖样式表的布局结果。

    主要手段是把内部行编辑框收窄到步进按钮左侧，使按钮恢复成 Qt 原生的命中
    区域：按下有下沉动画，长按可以连加。

    另外两道兜底只处理“点击仍然落在行编辑框或按钮边缘”的偏差情形：
    落在按钮矩形内的按下事件直接换算成一次步进；按钮绘制位置与命中区域差几个
    像素时，按按钮矩形补一次步进。
    """

    # 按钮矩形向外放宽的容差，覆盖绘制与命中区域相差几个像素的情况。
    _HIT_MARGIN = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._spin_press_value = None
        self._spin_line_edit = self.lineEdit()
        if self._spin_line_edit is not None:
            self._spin_line_edit.installEventFilter(self)
            self._spin_fit_line_edit()

    def _spin_button_rects(self) -> tuple[QRect, QRect]:
        option = QStyleOptionSpinBox()
        option.initFrom(self)
        option.frame = True
        option.buttonSymbols = self.buttonSymbols()
        style = self.style()
        up = style.subControlRect(
            QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxUp, self
        )
        down = style.subControlRect(
            QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxDown, self
        )
        return up, down

    def _spin_button_direction(self, position: QPoint) -> int:
        """返回 1（向上）、-1（向下）或 0（不是按钮区域）。"""
        up, down = self._spin_button_rects()
        if up.isEmpty() or down.isEmpty():
            return 0
        buttons = up.united(down)
        if not buttons.adjusted(
            -self._HIT_MARGIN, -self._HIT_MARGIN,
            self._HIT_MARGIN, self._HIT_MARGIN,
        ).contains(position):
            # 按钮整体绘制位置偏移时，退回到控件右侧的按钮列。
            column = QRect(
                max(0, buttons.left() - self._HIT_MARGIN),
                0,
                buttons.width() + self._HIT_MARGIN * 2,
                self.height(),
            )
            if not column.contains(position):
                return 0
        up_distance = (position - up.center()).manhattanLength()
        down_distance = (position - down.center()).manhattanLength()
        return 1 if up_distance <= down_distance else -1

    def _spin_step(self, direction: int) -> None:
        if direction > 0:
            self.stepUp()
        else:
            self.stepDown()

    def _spin_fit_line_edit(self) -> None:
        """把行编辑框右边界收到步进按钮左侧，别让它盖住按钮。

        样式表给数值框加 ``padding`` 后，Qt 会把行编辑框铺满整个内容区，连按钮
        一起盖住：点击被行编辑框收下，数值框既收不到按下事件（没有下沉动画），
        也收不到步进。把行编辑框缩到按钮左边之后，点击重新落到数值框上，交给 Qt
        原生处理——步进、按下动画、长按连加都与未套样式时一致。
        """
        edit = self._spin_line_edit
        if edit is None:
            return
        up, _down = self._spin_button_rects()
        if up.isEmpty():
            return
        rect = edit.geometry()
        available = up.left() - rect.left()
        if available <= 0 or rect.width() <= available:
            return
        edit.setGeometry(rect.left(), rect.top(), available, rect.height())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._spin_fit_line_edit()

    def eventFilter(self, watched, event):
        if self._spin_line_edit is not None and watched is self._spin_line_edit:
            if event.type() == QEvent.Resize:
                # Qt 每次重新布局行编辑框都可能把它撑回按钮上方，这里再收一次。
                self._spin_fit_line_edit()
            elif event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
                if event.button() == Qt.LeftButton:
                    # 先换算到全局再映射回本控件：直接 mapFrom(行编辑框, …) 在
                    # Qt 6.11 + PySide6 下会得到错误的偏移量。
                    global_position = self._spin_line_edit.mapToGlobal(
                        event.position().toPoint()
                    )
                    direction = self._spin_button_direction(
                        self.mapFromGlobal(global_position)
                    )
                    if direction:
                        self._spin_step(direction)
                        return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        self._spin_press_value = self.value()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        pressed_value = self._spin_press_value
        self._spin_press_value = None
        if pressed_value is None or event.button() != Qt.LeftButton:
            return
        if self.value() != pressed_value:
            # Qt 已经把这次点击当作步进按钮处理，不重复步进。
            return
        direction = self._spin_button_direction(event.position().toPoint())
        if not direction:
            return
        self._spin_step(direction)
        event.accept()


class KeyboardOnlyDoubleSpinBox(_SpinButtonStepFallback, QDoubleSpinBox):
    """浮点数值框：只接受键盘输入，忽略滚轮。"""

    def wheelEvent(self, event):
        event.ignore()


class KeyboardOnlySpinBox(_SpinButtonStepFallback, QSpinBox):
    """整数数值框：只接受键盘输入，忽略滚轮。"""

    def wheelEvent(self, event):
        event.ignore()


class WheelSafeComboBox(QComboBox):
    """下拉框：只能通过下拉列表或键盘选择，滚轮不改变当前项。"""

    def wheelEvent(self, event):
        event.ignore()


__all__ = [
    "KeyboardOnlyDoubleSpinBox",
    "KeyboardOnlySpinBox",
    "WheelSafeComboBox",
]
