"""防止鼠标滚轮误改数值的输入控件。

这些控件忽略滚轮事件：滚动页面时不会意外改变参数。滚轮事件被忽略后会继续
传递给父控件，因此页面仍可正常滚动。下拉框仍可通过鼠标点击展开或键盘选择。
"""

from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QSpinBox


class KeyboardOnlyDoubleSpinBox(QDoubleSpinBox):
    """浮点数值框：只接受键盘输入，忽略滚轮。"""

    def wheelEvent(self, event):
        event.ignore()


class KeyboardOnlySpinBox(QSpinBox):
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
