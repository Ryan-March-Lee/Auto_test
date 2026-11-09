"""配置页的输入控件：可直输的数值框与滚轮安全的下拉框。

数值输入有两类：

- :class:`NumericTextField`：测试配置页的数值输入。只有文本输入，没有步进按钮，
  也不响应键盘方向键；用户直接键入数字。读取方仍使用 ``value()`` / ``setValue()``，
  与原来的数值框保持同一读写口径，表单读取与回填不必为控件类型分叉。
- :class:`KeyboardOnlyDoubleSpinBox`：电源卡片使用的浮点数值框。保留 Qt 的数值输入
  能力，但隐藏步进按钮并忽略滚轮。

下拉框仍可通过鼠标点击展开或键盘选择，滚轮不改变当前项。
"""

from __future__ import annotations

import math

from PySide6.QtCore import Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (
    QAbstractSpinBox, QComboBox, QDoubleSpinBox, QLineEdit,
)


class NumericTextField(QLineEdit):
    """纯文本数值输入框：只能直接键入，没有步进按钮或方向键调整。

    控件只负责输入体验（范围钳制、显示精度、可解析文本），业务合法性仍由上层校验。
    ``valueChanged`` 在用户键入一个范围内可解析的数值时发出，供页面实时刷新派生
    状态；读取走 ``value()``，回填走 ``setValue()``。
    """

    valueChanged = Signal(float)

    def __init__(self, value, minimum, maximum, *, decimals=2, parent=None):
        super().__init__(parent)
        self._minimum = float(minimum)
        self._maximum = float(maximum)
        self._decimals = int(decimals)
        self._value = self._clamp(value)
        self._writing = False
        self.setValidator(
            QDoubleValidator(self._minimum, self._maximum, self._decimals, self)
        )
        self._write_text(self._value)
        self.textChanged.connect(self._on_text_changed)
        self.editingFinished.connect(self._on_editing_finished)

    def minimum(self) -> float:
        return self._minimum

    def maximum(self) -> float:
        return self._maximum

    def decimals(self) -> int:
        return self._decimals

    def value(self) -> float:
        return self._value

    def setValue(self, value) -> None:
        number = self._clamp(value)
        changed = number != self._value
        self._value = number
        # 程序回填也要把文本规范化；``_writing`` 阻止这次写入反过来触发 valueChanged。
        self._write_text(number)
        if changed:
            self.valueChanged.emit(number)

    def _clamp(self, value) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = self._minimum
        if not math.isfinite(number):
            number = self._minimum
        return round(min(self._maximum, max(self._minimum, number)), self._decimals)

    def _parse(self, text) -> float | None:
        try:
            number = float(text.strip())
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    def _format(self, value) -> str:
        text = f"{float(value):.{self._decimals}f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text if text not in ("", "-0") else "0"

    def _write_text(self, value) -> None:
        self._writing = True
        try:
            self.setText(self._format(value))
        finally:
            self._writing = False

    def _on_text_changed(self, text: str) -> None:
        if self._writing:
            return
        number = self._parse(text)
        # 输入过程中的空文本、负号、小数点等中间态不改变已提交的数值。
        if (
            number is None
            or number < self._minimum
            or number > self._maximum
        ):
            return
        number = round(number, self._decimals)
        if number != self._value:
            self._value = number
            self.valueChanged.emit(number)

    def _on_editing_finished(self) -> None:
        number = self._parse(self.text())
        self.setValue(self._value if number is None else number)


class KeyboardOnlyDoubleSpinBox(QDoubleSpinBox):
    """浮点数值框：隐藏步进按钮，只接受直接输入，忽略滚轮。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)

    def wheelEvent(self, event):
        event.ignore()


class WheelSafeComboBox(QComboBox):
    """下拉框：只能通过下拉列表或键盘选择，滚轮不改变当前项。"""

    def wheelEvent(self, event):
        event.ignore()


__all__ = [
    "KeyboardOnlyDoubleSpinBox",
    "NumericTextField",
    "WheelSafeComboBox",
]
