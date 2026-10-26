"""防止鼠标滚轮误改数值的输入控件。

这些控件忽略滚轮事件：滚动页面时不会意外改变参数。滚轮事件被忽略后会继续
传递给父控件，因此页面仍可正常滚动。下拉框仍可通过鼠标点击展开或键盘选择。

另外，原生 Windows 样式与样式表混用时，Qt 计算出的步进按钮命中区域可能与
按钮实际绘制的位置不一致，表现为“点下箭头能改值、点上箭头没反应”。数值框
基类在这种情况下会按按钮的绘制矩形补一次步进，保证上下按钮行为对称。
"""

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QSpinBox, QStyle, QStyleOptionSpinBox,
)


class _SpinButtonStepFallback:
    """让上下步进按钮的点击不依赖平台样式的命中判定。

    默认行为仍交给 Qt：正常点击按钮时 Qt 会在按下时步进，这里不会重复处理。
    只有在一次左键点击结束后数值没有变化（即 Qt 没把这次点击当成步进按钮）
    时，才按按钮矩形计算方向并补一次步进。
    """

    # 按钮矩形向外放宽的容差，覆盖绘制与命中区域相差几个像素的情况。
    _HIT_MARGIN = 2

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

    def mousePressEvent(self, event):
        self._spin_press_value = self.value()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        pressed_value = getattr(self, "_spin_press_value", None)
        self._spin_press_value = None
        if pressed_value is None or event.button() != Qt.LeftButton:
            return
        if self.value() != pressed_value:
            # Qt 已经把这次点击当作步进按钮处理，不重复步进。
            return
        direction = self._spin_button_direction(event.position().toPoint())
        if not direction:
            return
        if direction > 0:
            self.stepUp()
        else:
            self.stepDown()
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
