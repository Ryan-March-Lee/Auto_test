"""DUT 功率扫描计划的一行编辑器。

组件只负责一行：计划编号、起始功率/步长/终止功率三个输入框、删除按钮和灰色预览
标签。它不读取配置文件、不判断 DUT 上限，也不生成目标功率点；计划是否连续、预览
是否合法以及“至少保留一行”的删除规则都由父编辑器决定。

输入范围沿用配置页现有功率控件（:data:`POWER_RANGE_DBM` / :data:`POWER_STEP_RANGE_DB`，
与旧单计划控件共用同一份常量）。控件只提供输入体验，业务合法性仍由上层校验。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .config_page_widgets import POWER_RANGE_DBM, POWER_STEP_RANGE_DB, number_field
from .theme import set_button_variant, set_tone

__all__ = ["PowerSweepPlanRow"]

# 编号标签按四位计划数预留宽度：编号文本变化只改文字，不重新分配行内空间。
_NUMBER_LABEL_SAMPLE = "计划 0000"


class PowerSweepPlanRow(QWidget):
    """一行 DUT 功率扫描计划控件。

    删除是父编辑器的决定：点击删除只发出 :attr:`remove_requested`（携带本行），
    是否真的移除、至少保留一行等规则不在组件内实现。任一输入框数值变化时发出
    :attr:`values_changed`，供父编辑器刷新预览或汇总状态；:meth:`set_values` 的批量
    回填只补发一次。
    """

    #: 用户请求删除本行；参数是本行对象，父编辑器据此定位并决定是否允许删除。
    remove_requested = Signal(object)
    #: 起始/步长/终止任一数值变化；组件不解释变化的含义。
    values_changed = Signal()

    def __init__(self, start_power, step, stop_power, *, number=1, parent=None):
        super().__init__(parent)
        self._number = 0
        self.number_label = QLabel()
        self.number_label.setProperty("role", "fieldLabel")
        # 固定宽度而不是只设最小值：编号文本变长时标签的 sizeHint 会变，行内其它输入框
        # 就会跟着横移；计划数到三位以上时尤其明显。
        self.number_label.setFixedWidth(
            self.fontMetrics().horizontalAdvance(_NUMBER_LABEL_SAMPLE) + 8
        )

        # 显示顺序固定为“起始、步长、终止”，与用户填写顺序一致。
        self.start_power = number_field(start_power, *POWER_RANGE_DBM)
        self.start_power.setToolTip("该段 DUT 输入功率扫描起点（dBm）")
        self.step = number_field(step, *POWER_STEP_RANGE_DB)
        self.step.setToolTip("该段目标 DUT 输入功率网格的步进（dB）")
        self.stop_power = number_field(stop_power, *POWER_RANGE_DBM)
        self.stop_power.setToolTip("该段 DUT 输入功率扫描终点（dBm）")

        self.delete_button = QPushButton("删除")
        self.delete_button.setToolTip("删除这一段功率扫描计划")
        set_button_variant(self.delete_button, "ghost")

        self.preview = QLabel()
        self.preview.setProperty("role", "note")
        self.preview.setWordWrap(True)
        self.preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        set_tone(self.preview, "muted")

        self._build_layout()
        self.set_number(number)
        for widget in (self.start_power, self.step, self.stop_power):
            widget.valueChanged.connect(self._on_value_changed)
        self.delete_button.clicked.connect(self._request_remove)

    def _build_layout(self) -> None:
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        fields = QHBoxLayout()
        fields.setSpacing(6)
        fields.addWidget(self.number_label)
        for label_text, widget in (
            ("起始功率 (dBm)", self.start_power),
            ("步长 (dB)", self.step),
            ("终止功率 (dBm)", self.stop_power),
        ):
            label = QLabel(label_text)
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            fields.addWidget(label)
            fields.addWidget(widget)
        fields.addStretch(1)
        fields.addWidget(self.delete_button)
        column.addLayout(fields)
        column.addWidget(self.preview)

    def set_number(self, number: int) -> None:
        """刷新计划编号（从 1 开始）；编号只是显示文本，不参与业务判断。"""
        self._number = int(number)
        self.number_label.setText(f"计划 {self._number}")

    def number(self) -> int:
        """返回当前计划编号。"""
        return self._number

    def values(self) -> tuple[float, float, float]:
        """按显示顺序返回 ``(起始功率, 步长, 终止功率)``。"""
        return (
            self.start_power.value(),
            self.step.value(),
            self.stop_power.value(),
        )

    def set_values(self, start_power, step, stop_power) -> None:
        """批量回填三个输入框。

        回填是父编辑器的一次整体动作：期间屏蔽三个输入框的信号，只在最终数值确实变化
        时补发一次 :attr:`values_changed`。否则父编辑器会收到最多三次通知，还会看到
        “只回填了一半”的中间状态去刷新预览。
        """
        before = self.values()
        widgets = (self.start_power, self.step, self.stop_power)
        for widget in widgets:
            widget.blockSignals(True)
        try:
            self.start_power.setValue(start_power)
            self.step.setValue(step)
            self.stop_power.setValue(stop_power)
        finally:
            for widget in widgets:
                widget.blockSignals(False)
        if self.values() != before:
            self.values_changed.emit()

    def set_preview(self, text: str, *, tone: str = "muted") -> None:
        """设置灰色预览文本；``tone`` 只改变语义色，不解释文本内容。"""
        self.preview.setText(text)
        if self.preview.property("tone") != tone:
            set_tone(self.preview, tone)

    def _on_value_changed(self, *_args) -> None:
        self.values_changed.emit()

    def _request_remove(self) -> None:
        self.remove_requested.emit(self)
