"""独立的线损测量页面。"""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, Callable

from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .measurement_controller_contract import MeasurementCommand
from .measurement_state import CableLossPageResultState, MeasurementKind, MeasurementStatus
from .pages import PageControllerBindingMixin


class CableLossPage(PageControllerBindingMixin, QWidget):
    """线损测量输入、接线确认与结果展示。"""

    def __init__(
        self,
        *,
        config_path_provider: Callable[[], str],
        prepare_run: Callable[[], bool],
        confirm_wiring: Callable[[], bool],
        connection_dialog_factory: Callable[[str, QWidget], Any],
        load_results_callback: Callable[[], Mapping[str, Any]] | None = None,
        log_callback: Callable[[str], Any] | None = None,
        progress_callback: Callable[[int], Any] | None = None,
        error_callback: Callable[[str], Any] | None = None,
    ) -> None:
        PageControllerBindingMixin.__init__(self)
        QWidget.__init__(self)
        self._config_path_provider = config_path_provider
        self._prepare_run = prepare_run
        self._confirm_wiring = confirm_wiring
        self._connection_dialog_factory = connection_dialog_factory
        self._load_results_callback = load_results_callback
        self._log_callback = log_callback or (lambda _message: None)
        self._progress_callback = progress_callback or (lambda _value: None)
        self._error_callback = error_callback or self._show_error
        self.root = self.build_ui()

    def build_ui(self) -> QWidget:
        layout = QVBoxLayout(self)

        instruction_group = QGroupBox("连接说明")
        instruction_layout = QVBoxLayout(instruction_group)
        instruction_text = QTextEdit()
        instruction_text.setMaximumHeight(200)
        instruction_text.setReadOnly(True)
        instruction_text.setHtml("""
        <h3>线损测量连接说明:</h3>
        <p><b>步骤1 - 路径1测量:</b></p>
        <p>信号源 → 线缆① → 衰减器 → 线缆② → 频谱仪</p>
        <br>
        <p><b>步骤2 - 路径2测量:</b></p>
        <p>信号源 → 线缆① → 线缆③ → 线缆④ → 衰减器 → 线缆② → 频谱仪</p>
        <br>
        <p style="color: red;"><b>注意:</b> 每个步骤会提示您重新连接线缆，请按提示操作</p>
        """)
        instruction_layout.addWidget(instruction_text)
        diagram_layout = QHBoxLayout()
        self.show_path1_btn = QPushButton("查看路径1连接图")
        self.show_path2_btn = QPushButton("查看路径2连接图")
        self.show_path1_btn.clicked.connect(lambda: self._show_diagram("cable_loss_path1"))
        self.show_path2_btn.clicked.connect(lambda: self._show_diagram("cable_loss_path2"))
        diagram_layout.addWidget(self.show_path1_btn)
        diagram_layout.addWidget(self.show_path2_btn)
        instruction_layout.addLayout(diagram_layout)
        layout.addWidget(instruction_group)

        control_group = QGroupBox("测量控制")
        control_layout = QHBoxLayout(control_group)
        self.cable_loss_btn = QPushButton("开始线损测量")
        self.load_cable_results_btn = QPushButton("加载测量结果")
        self.stop_cable_loss_btn = QPushButton("停止测量")
        self.continue_cable_loss_btn = QPushButton("继续第二步测量")
        self.stop_cable_loss_btn.setEnabled(False)
        self.continue_cable_loss_btn.setEnabled(False)
        self.cable_loss_btn.clicked.connect(self.start_measurement)
        self.load_cable_results_btn.clicked.connect(self.load_results)
        self.stop_cable_loss_btn.clicked.connect(self.stop_measurement)
        self.continue_cable_loss_btn.clicked.connect(self.continue_measurement)
        control_layout.addWidget(self.cable_loss_btn)
        control_layout.addWidget(self.load_cable_results_btn)
        control_layout.addWidget(self.stop_cable_loss_btn)
        control_layout.addWidget(self.continue_cable_loss_btn)
        layout.addWidget(control_group)

        result_group = QGroupBox("测量结果")
        result_layout = QVBoxLayout(result_group)
        self.cable_loss_table = QTableWidget()
        self.cable_loss_table.setColumnCount(5)
        self.cable_loss_table.setHorizontalHeaderLabels(
            ["频率(GHz)", "线缆1(dB)", "线缆2(dB)", "线缆3(dB)", "线缆4(dB)"]
        )
        result_layout.addWidget(self.cable_loss_table)
        layout.addWidget(result_group)
        return self

    def _connect_controller_signals(self, controller: Any) -> None:
        signals = controller.signals
        self._connect_signal(signals.progress, self._progress_callback)
        self._connect_signal(signals.message, self._log_callback)
        self._connect_signal(signals.data_update, self.update_realtime)
        self._connect_signal(signals.result, self.show_result)
        self._connect_signal(signals.step_pause, self._on_step_pause)
        self._connect_signal(signals.state_changed, self._on_state_changed)
        self._connect_signal(signals.error, self._on_error)
        self._connect_signal(signals.stopped, self._on_stopped)
        self._connect_signal(signals.finished, self._on_finished)
        self._connect_signal(signals.rejected, self._log_callback)

    def start_measurement(self) -> None:
        controller = self.controller
        if controller is None:
            return
        if not self._prepare_run():
            return
        dialog = self._connection_dialog_factory("cable_loss_path1", self)
        if dialog.exec() != QDialog.Accepted:
            return
        if not self._confirm_wiring():
            return
        self.clear_results()
        self._log_callback("开始线损测量...")
        controller.start_cable_loss(MeasurementCommand(self._config_path_provider()))

    def stop_measurement(self) -> None:
        if self.controller is not None:
            self.controller.stop()

    def continue_measurement(self) -> None:
        if self.controller is not None:
            self.controller.continue_cable_loss()

    def _show_diagram(self, diagram_type: str) -> None:
        self._connection_dialog_factory(diagram_type, self).exec()

    def _on_step_pause(self, message: str) -> None:
        self._log_callback(message)
        if self.controller is None or self.controller.state.status is not MeasurementStatus.WAITING_FOR_CONTINUE:
            return
        dialog = self._connection_dialog_factory("cable_loss_path2", self)
        if dialog.exec() == QDialog.Accepted:
            self.controller.continue_cable_loss()
        else:
            self.controller.stop()
            self._log_callback("线损测量已取消")

    def _on_state_changed(self, state: Any) -> None:
        active = state.kind is MeasurementKind.CABLE_LOSS and state.is_active
        self.cable_loss_btn.setEnabled(not active)
        self.stop_cable_loss_btn.setEnabled(active)
        self.continue_cable_loss_btn.setEnabled(
            state.kind is MeasurementKind.CABLE_LOSS
            and state.status is MeasurementStatus.WAITING_FOR_CONTINUE
        )

    def _on_error(self, message: str) -> None:
        self.cable_loss_btn.setEnabled(True)
        self.stop_cable_loss_btn.setEnabled(False)
        self.continue_cable_loss_btn.setEnabled(False)
        self._error_callback(message)

    def _on_stopped(self, reason: str) -> None:
        self._log_callback(f"测量已停止: {reason}")
        self._progress_callback(0)

    def _on_finished(self) -> None:
        self._progress_callback(100)
        self._log_callback("测量完成！")

    def clear_results(self) -> None:
        self.cable_loss_table.setRowCount(0)
        self._log_callback("已清空上一次线损测量结果")

    def load_results(self) -> None:
        if self._load_results_callback is None:
            self._log_callback("当前没有可加载的线损结果")
            return
        try:
            payload = self._load_results_callback()
            self.clear_results()
            self.show_result(SimpleNamespace(kind=MeasurementKind.CABLE_LOSS, value=payload))
            self._log_callback(f"已加载线损测量结果，共 {self.cable_loss_table.rowCount()} 个频点")
        except FileNotFoundError:
            self._log_callback("未找到线损测量结果文件")
        except Exception as error:
            self._log_callback(f"加载线损测量结果失败: {error}")

    def update_realtime(self, data: Any) -> None:
        if not isinstance(data, Mapping) or "frequency" not in data:
            return
        frequency = str(data["frequency"])
        row = self._find_or_add_row(frequency)
        losses = data.get("cable_losses", {})
        values = [losses.get(f"cable{index}") for index in range(1, 5)] if losses else [None] * 4
        for column, value in enumerate(values, start=1):
            if value is not None:
                self.cable_loss_table.setItem(row, column, QTableWidgetItem(f"{value:.3f}"))
        self.cable_loss_table.resizeColumnsToContents()

    def show_result(self, reference: Any) -> None:
        if getattr(reference, "kind", None) is not MeasurementKind.CABLE_LOSS:
            return
        value = getattr(reference, "value", None)
        if isinstance(value, CableLossPageResultState):
            for item in value.rows:
                self.update_realtime(item)
        elif isinstance(value, Mapping):
            cable_losses = value.get("cable_losses", value)
            if isinstance(cable_losses, Mapping):
                for frequency, losses in cable_losses.items():
                    if isinstance(losses, Mapping):
                        self.update_realtime({"frequency": frequency, "cable_losses": losses})

    def _find_or_add_row(self, frequency: str) -> int:
        for row in range(self.cable_loss_table.rowCount()):
            item = self.cable_loss_table.item(row, 0)
            if item is not None and item.text() == frequency:
                return row
        row = self.cable_loss_table.rowCount()
        self.cable_loss_table.insertRow(row)
        self.cable_loss_table.setItem(row, 0, QTableWidgetItem(frequency))
        return row

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "错误", message)


__all__ = ["CableLossPage"]
