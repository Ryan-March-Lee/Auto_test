"""独立的主功放测量页面。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .measurement_controller_contract import MeasurementCommand
from application.dto.legacy_result_adapter import legacy_payload
from .measurement_state import AmplifierPageResultState, MeasurementKind
from .pages import PageControllerBindingMixin
from .realtime_buffer import RealtimeMeasurementBuffer
from .realtime_page import RealtimePageMixin


class AmplifierPage(RealtimePageMixin, PageControllerBindingMixin, QWidget):
    """主功放测量的输入、接线确认、进度和结果展示。"""

    def __init__(
        self,
        *,
        config_path_provider: Callable[[], str],
        prepare_run: Callable[[], bool],
        confirm_wiring: Callable[[], bool],
        driver_mode_provider: Callable[[], bool],
        connection_dialog_factory: Callable[[str, QWidget], Any],
        plot_widget_factory: Callable[[QWidget], QWidget],
        realtime_data_callback: Callable[[Mapping[str, Any]], Any] | None = None,
        clear_realtime_callback: Callable[[], Any] | None = None,
        log_callback: Callable[[str], Any] | None = None,
        progress_callback: Callable[[int], Any] | None = None,
        error_callback: Callable[[str], Any] | None = None,
    ) -> None:
        PageControllerBindingMixin.__init__(self)
        QWidget.__init__(self)
        self._config_path_provider = config_path_provider
        self._prepare_run = prepare_run
        self._confirm_wiring = confirm_wiring
        self._driver_mode_provider = driver_mode_provider
        self._connection_dialog_factory = connection_dialog_factory
        self._plot_widget_factory = plot_widget_factory
        self._realtime_data_callback = realtime_data_callback or (lambda _data: None)
        self._clear_realtime_callback = clear_realtime_callback or (lambda: None)
        self._log_callback = log_callback or (lambda _message: None)
        self._progress_callback = progress_callback or (lambda _value: None)
        self._error_callback = error_callback or self._show_error
        self.realtime_buffer = RealtimeMeasurementBuffer()
        self.root = self.build_ui()

    def build_ui(self) -> QWidget:
        layout = QVBoxLayout(self)
        instruction_group = QGroupBox("连接说明")
        instruction_layout = QVBoxLayout(instruction_group)
        self.instruction_text = QTextEdit()
        self.instruction_text.setMaximumHeight(120)
        self.instruction_text.setReadOnly(True)
        instruction_layout.addWidget(self.instruction_text)
        self.show_amp_diagram_btn = QPushButton("查看连接图")
        self.show_amp_diagram_btn.clicked.connect(self.show_connection_diagram)
        instruction_layout.addWidget(self.show_amp_diagram_btn)
        layout.addWidget(instruction_group)

        control_group = QGroupBox("测量控制")
        control_layout = QVBoxLayout(control_group)
        self.amplifier_test_btn = QPushButton("开始功放测试")
        self.amplifier_test_btn.clicked.connect(self.start_measurement)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_measurement)
        self.emergency_stop_btn = QPushButton("紧急停止")
        self.emergency_stop_btn.setStyleSheet(
            "QPushButton { background-color: red; color: white; font-weight: bold; }"
        )
        self.emergency_stop_btn.setEnabled(False)
        self.emergency_stop_btn.clicked.connect(self.emergency_stop_measurement)
        control_layout.addWidget(self.amplifier_test_btn)
        control_layout.addWidget(self.cancel_btn)
        control_layout.addWidget(self.emergency_stop_btn)
        layout.addWidget(control_group)

        result_group = QGroupBox("测量结果")
        result_layout = QVBoxLayout(result_group)
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(4)
        self.result_table.setHorizontalHeaderLabels(["频率(GHz)", "压缩点", "小信号增益(dB)", "是否达到压缩"])
        result_layout.addWidget(self.result_table)
        layout.addWidget(result_group)

        self.amplifier_plot_widget = self._plot_widget_factory(self)
        self._realtime_plot_widget = self.amplifier_plot_widget
        for signal_name, slot in (("prev_clicked", self.previous_frequency), ("next_clicked", self.next_frequency)):
            signal = getattr(self.amplifier_plot_widget, signal_name, None)
            if signal is not None:
                signal.connect(slot)
        layout.addWidget(self.amplifier_plot_widget)
        self.update_instruction_text()
        return self

    def _connect_controller_signals(self, controller: Any) -> None:
        signals = controller.signals
        self._connect_signal(signals.progress, self._progress_callback)
        self._connect_signal(signals.data_update, self.update_realtime)
        self._connect_signal(signals.result, self.show_result)
        self._connect_signal(signals.state_changed, self._on_state_changed)
        self._connect_signal(signals.error, self._on_error)
        self._connect_signal(signals.stopped, self._on_stopped)
        self._connect_signal(signals.finished, self._on_finished)

    def update_instruction_text(self) -> None:
        if self._driver_mode_provider():
            text = "<h3>主功放测试连接说明:</h3><p>信号源 → 线缆① → 驱动功放 → 线缆③ → 主功放 → 线缆④ → 衰减器 → 线缆② → 频谱仪</p>"
        else:
            text = "<h3>主功放测试连接说明（无驱动模式）:</h3><p>信号源 → 线缆① → 主功放 → 线缆④ → 衰减器 → 线缆② → 频谱仪</p>"
        self.instruction_text.setHtml(text)

    def start_measurement(self) -> None:
        controller = self.controller
        if controller is None:
            return
        if getattr(controller.state, "is_active", False):
            self._log_callback("已有测量正在运行")
            return
        if not self._prepare_run():
            return
        self.update_instruction_text()
        diagram = "amplifier_test" if self._driver_mode_provider() else "amplifier_test_no_driver"
        if self._connection_dialog_factory(diagram, self).exec() != QDialog.Accepted:
            return
        if not self._confirm_wiring():
            return
        self.reset_realtime_data()
        self.result_table.setRowCount(0)
        self._log_callback("开始主功放测试...")
        # 功放参数已在 prepare_run 中保存到运行配置；controller 只接收配置路径，
        # 避免携带但不会被 worker 消费的伪 options 接口。
        controller.start_amplifier(MeasurementCommand(self._config_path_provider()))

    def stop_measurement(self) -> None:
        if self.controller is not None:
            self.controller.stop()

    def cancel_measurement(self) -> None:
        if self.controller is not None:
            self.controller.cancel()

    def emergency_stop_measurement(self) -> None:
        if self.controller is None:
            return
        if QMessageBox.question(self, "紧急停止", "确定要紧急停止当前测试吗？", QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            self.controller.emergency_stop()
            self._log_callback("用户执行紧急停止！")

    def show_connection_diagram(self) -> None:
        diagram = "amplifier_test" if self._driver_mode_provider() else "amplifier_test_no_driver"
        self._connection_dialog_factory(diagram, self).exec()

    def show_result(self, reference: Any) -> None:
        if getattr(reference, "kind", None) is not MeasurementKind.AMPLIFIER:
            return
        value = getattr(reference, "value", None)
        if isinstance(value, AmplifierPageResultState):
            rows = value.rows
        else:
            value = legacy_payload(value)
            rows = self._result_rows(value.get("results", value))
        self.result_table.setRowCount(0)
        for row_data in rows:
            row = self.result_table.rowCount()
            self.result_table.insertRow(row)
            values = (row_data.get("frequency", ""), row_data.get("compression_point", ""), row_data.get("small_signal_gain", ""), row_data.get("compression_achieved", ""))
            for column, value_item in enumerate(values):
                self.result_table.setItem(row, column, QTableWidgetItem(str(value_item)))
            sweep = row_data.get("sweep_data")
            if isinstance(sweep, Mapping):
                self.update_realtime({"frequency": row_data.get("frequency", ""), "sweep_data": dict(sweep)})
        self.result_table.resizeColumnsToContents()

    @staticmethod
    def _result_rows(results: Any) -> tuple[Mapping[str, Any], ...]:
        if not isinstance(results, Mapping):
            return ()
        rows = []
        for frequency, result in results.items():
            if not isinstance(result, Mapping):
                continue
            point = result.get("compression_point")
            if isinstance(point, Mapping):
                point = point.get("output_power", point.get("input_power", ""))
            rows.append({"frequency": frequency, "compression_point": point, "small_signal_gain": result.get("small_signal_gain", ""), "compression_achieved": result.get("compression_achieved", ""), "sweep_data": result.get("sweep_data", {})})
        return tuple(rows)

    def _update_plot(self, data: Mapping[str, Any]) -> None:
        update_plot = getattr(self.amplifier_plot_widget, "update_plot", None)
        if update_plot is not None:
            update_plot(dict(data))

    def _on_state_changed(self, state: Any) -> None:
        active = state.kind is MeasurementKind.AMPLIFIER and state.is_active
        self.amplifier_test_btn.setEnabled(not active)
        self.emergency_stop_btn.setEnabled(active)
        self.cancel_btn.setEnabled(active)

    def _on_error(self, message: str) -> None:
        if self.controller is None or (
            self.controller.state.kind is not None
            and self.controller.state.kind is not MeasurementKind.AMPLIFIER
        ):
            return
        self.amplifier_test_btn.setEnabled(True)
        self.emergency_stop_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self._error_callback(message)

    def _on_stopped(self, reason: str) -> None:
        if self.controller is None or (
            self.controller.state.kind is not None
            and self.controller.state.kind is not MeasurementKind.AMPLIFIER
        ):
            return
        self.amplifier_test_btn.setEnabled(True)
        self.emergency_stop_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self._progress_callback(0)
        self._log_callback(f"测量已停止: {reason}")

    def _on_finished(self) -> None:
        if self.controller is None or (
            self.controller.state.kind is not None
            and self.controller.state.kind is not MeasurementKind.AMPLIFIER
        ):
            return
        self.amplifier_test_btn.setEnabled(True)
        self.emergency_stop_btn.setEnabled(False)
        self._progress_callback(100)
        self._log_callback("测量完成！")

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "错误", message)


__all__ = ["AmplifierPage"]
