"""独立的驱动映射测量页面。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .measurement_controller_contract import MeasurementCommand
from application.dto import DriverPowerMappingMeasurementResult
from .archival_page import ArchivalPageMixin
from .measurement_state import (
    DriverMappingPageResultState,
    MeasurementKind,
)
from .pages import PageControllerBindingMixin
from .run_contract import is_valid_prepared_run
from .realtime_buffer import RealtimeMeasurementBuffer
from .realtime_page import RealtimePageMixin
from .theme import set_button_variant


class DriverMappingPage(
    RealtimePageMixin, ArchivalPageMixin, PageControllerBindingMixin, QWidget
):
    """驱动映射输入、接线确认、进度和结构化结果展示。"""

    def __init__(
        self,
        *,
        config_path_provider: Callable[[], str],
        run_provider: Callable[[], Any],
        connection_dialog_factory: Callable[[str, QWidget], Any],
        plot_widget_factory: Callable[[QWidget], QWidget],
        realtime_data_callback: Callable[[Mapping[str, Any]], Any] | None = None,
        clear_realtime_callback: Callable[[], Any] | None = None,
        log_callback: Callable[[str], Any] | None = None,
        progress_callback: Callable[[int], Any] | None = None,
        error_callback: Callable[[str], Any] | None = None,
        archival_location_provider: Callable[[str, str | None], str | None] | None = None,
        archival_run_id_provider: Callable[[], str | None] | None = None,
    ) -> None:
        PageControllerBindingMixin.__init__(self)
        QWidget.__init__(self)
        self._config_path_provider = config_path_provider
        self._run_provider = run_provider
        self._connection_dialog_factory = connection_dialog_factory
        self._plot_widget_factory = plot_widget_factory
        self._realtime_data_callback = realtime_data_callback or (lambda _data: None)
        self._clear_realtime_callback = clear_realtime_callback or (lambda: None)
        self._log_callback = log_callback or (lambda _message: None)
        self._progress_callback = progress_callback or (lambda _value: None)
        self._error_callback = error_callback or self._show_error
        self._setup_archival(
            MeasurementKind.DRIVER_MAPPING,
            location_provider=archival_location_provider,
            run_id_provider=archival_run_id_provider,
            run_provider=run_provider,
        )
        self.realtime_buffer = RealtimeMeasurementBuffer()
        self.root = self.build_ui()

    def build_ui(self) -> QWidget:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(12)

        # 左侧只放连接说明和测量控制，宽度固定为窄栏；右侧整块留给四张图。
        left_panel = QWidget()
        left_panel.setMinimumWidth(400)
        left_panel.setMaximumWidth(430)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        instruction_group = QGroupBox("连接说明")
        instruction_layout = QVBoxLayout(instruction_group)
        instruction_layout.setSpacing(10)
        instruction_text = QTextEdit()
        instruction_text.setMaximumHeight(96)
        instruction_text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        instruction_text.setReadOnly(True)
        instruction_text.setHtml(
            "<p><b>驱动功放映射测量连接说明</b></p>"
            "<p>信号源 → 线缆① → 驱动功放 → 线缆③ → 衰减器 → 线缆② → 频谱仪</p>"
        )
        instruction_layout.addWidget(instruction_text)
        self.show_driver_diagram_btn = QPushButton("查看连接图")
        set_button_variant(self.show_driver_diagram_btn, "secondary")
        self.show_driver_diagram_btn.clicked.connect(self.show_connection_diagram)
        instruction_layout.addWidget(self.show_driver_diagram_btn)
        left_layout.addWidget(instruction_group)

        control_group = QGroupBox("测量控制")
        control_layout = QHBoxLayout(control_group)
        control_layout.setSpacing(8)
        self.driver_mapping_btn = QPushButton("开始驱动映射")
        set_button_variant(self.driver_mapping_btn, "primary")
        self.driver_mapping_btn.clicked.connect(self.start_measurement)
        self.driver_cancel_btn = QPushButton("取消")
        set_button_variant(self.driver_cancel_btn, "secondary")
        self.driver_cancel_btn.setEnabled(False)
        self.driver_cancel_btn.clicked.connect(self.cancel_measurement)
        self.driver_stop_btn = QPushButton("停止测量")
        set_button_variant(self.driver_stop_btn, "secondary")
        self.driver_stop_btn.setEnabled(False)
        self.driver_stop_btn.clicked.connect(self.stop_measurement)
        self.driver_emergency_stop_btn = QPushButton("紧急停止")
        set_button_variant(self.driver_emergency_stop_btn, "danger")
        self.driver_emergency_stop_btn.setEnabled(False)
        self.driver_emergency_stop_btn.clicked.connect(self.emergency_stop_measurement)
        # 测量控制按钮横向排列在同一行，减少纵向占用面积。
        for button in (
            self.driver_mapping_btn,
            self.driver_stop_btn,
            self.driver_cancel_btn,
            self.driver_emergency_stop_btn,
        ):
            control_layout.addWidget(button)
        left_layout.addWidget(control_group)

        self.archival_view = self._create_archival_view()
        left_layout.addWidget(self.archival_view)
        left_layout.addStretch(1)
        self.control_panel = left_panel
        layout.addWidget(left_panel)

        self.driver_plot_widget = self._plot_widget_factory(self)
        self._realtime_plot_widget = self.driver_plot_widget
        prev_clicked = getattr(self.driver_plot_widget, "prev_clicked", None)
        next_clicked = getattr(self.driver_plot_widget, "next_clicked", None)
        if prev_clicked is not None:
            prev_clicked.connect(self.previous_frequency)
        if next_clicked is not None:
            next_clicked.connect(self.next_frequency)
        layout.addWidget(self.driver_plot_widget, 1)
        return self

    def _connect_controller_signals(self, controller: Any) -> None:
        signals = controller.signals
        self._connect_signal(signals.progress, self._progress_callback)
        self._connect_archival_signals(controller)
        self._connect_signal(signals.data_update, self.update_realtime)
        self._connect_signal(signals.result, self.show_result)
        self._connect_signal(signals.state_changed, self._on_state_changed)
        self._connect_signal(signals.error, self._on_error)
        self._connect_signal(signals.stopped, self._on_stopped)
        self._connect_signal(signals.finished, self._on_finished)

    def start_measurement(self) -> None:
        controller = self.controller
        if controller is None:
            return
        if getattr(controller.state, "is_active", False):
            self._log_callback("已有测量正在运行")
            return
        # 只接受配置阶段创建（或按 run_id 重新打开）的当前运行契约。
        run = self._run_provider()
        if not is_valid_prepared_run(run):
            self._error_callback("正式驱动映射测量必须先创建或按 run_id 重新加载运行")
            return
        dialog = self._connection_dialog_factory("driver_mapping", self)
        if dialog.exec() != QDialog.Accepted:
            return
        self._log_callback("驱动映射现场接线已确认")
        self.reset_realtime_data()
        self._log_callback("开始驱动功放映射...")
        self._archival_try_start(
            lambda: controller.start_driver_mapping(
                MeasurementCommand(self._config_path_provider(), prepared_run=run)
            ),
            "驱动映射测试未能启动，未开始测量",
        )

    def stop_measurement(self) -> None:
        if self.controller is not None:
            self.controller.stop()

    def cancel_measurement(self) -> None:
        if self.controller is not None:
            self.controller.cancel()

    def emergency_stop_measurement(self) -> None:
        if self.controller is None:
            return
        if QMessageBox.question(
            self, "紧急停止", "确定要紧急停止当前驱动映射测试吗？",
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes:
            self.controller.emergency_stop()
            self._log_callback("用户执行驱动映射紧急停止！")

    def show_connection_diagram(self) -> None:
        self._connection_dialog_factory("driver_mapping", self).exec()

    @property
    def _realtime_data(self):
        return self.realtime_buffer.data

    @property
    def _frequency_list(self):
        return [str(value) for value in self.realtime_buffer.frequencies]

    def show_result(self, reference: Any) -> None:
        if getattr(reference, "kind", None) is not MeasurementKind.DRIVER_MAPPING:
            return
        value = getattr(reference, "value", None)
        if isinstance(value, DriverMappingPageResultState):
            rows = value.rows
        elif isinstance(value, DriverPowerMappingMeasurementResult):
            rows = (
                {
                    "frequency": point.frequency_ghz,
                    "input_power": point.input_power_dbm,
                    "output_power": point.output_power_dbm,
                }
                for point in value.points
            )
        else:
            self._error_callback("驱动映射测量结果不是正式结果模型，无法显示")
            return
        invalid_rows = False
        for row in rows:
            frequency = str(row.get("frequency", ""))
            if not frequency:
                invalid_rows = True
                continue
            try:
                input_power = float(row["input_power"])
                output_power = float(row["output_power"])
            except (KeyError, TypeError, ValueError):
                invalid_rows = True
                continue
            sweep = self._realtime_data.get(frequency, {}).copy()
            sweep["frequency"] = frequency
            existing = sweep.get("sweep_data", {})
            if not isinstance(existing, Mapping):
                existing = {}
            inputs = list(existing.get("input_power_sg", ()))
            outputs = list(existing.get("output_power_driver", ()))
            inputs.append(input_power)
            outputs.append(output_power)
            sweep["sweep_data"] = {
                "input_power_sg": inputs,
                "output_power_driver": outputs,
            }
            self.update_realtime(sweep)
        if invalid_rows:
            self._error_callback("驱动映射结果包含无效功率点，已跳过无效数据")

    def _update_plot(self, data: Mapping[str, Any]) -> None:
        update_plot = getattr(self.driver_plot_widget, "update_plot", None)
        if update_plot is not None:
            update_plot(dict(data))

    def _on_state_changed(self, state: Any) -> None:
        active = state.kind is MeasurementKind.DRIVER_MAPPING and state.is_active
        self.driver_mapping_btn.setEnabled(not active)
        self.driver_stop_btn.setEnabled(active)
        self.driver_emergency_stop_btn.setEnabled(active)
        self.driver_cancel_btn.setEnabled(active)

    def _on_error(self, message: str) -> None:
        if not self._archival_is_for_this_page():
            return
        self.driver_mapping_btn.setEnabled(True)
        self.driver_stop_btn.setEnabled(False)
        self.driver_emergency_stop_btn.setEnabled(False)
        self.driver_cancel_btn.setEnabled(False)
        self._archival_test_failed(message)
        self._error_callback(message)

    def _on_stopped(self, reason: str) -> None:
        if not self._archival_is_for_this_page():
            return
        self.driver_mapping_btn.setEnabled(True)
        self.driver_stop_btn.setEnabled(False)
        self.driver_emergency_stop_btn.setEnabled(False)
        self.driver_cancel_btn.setEnabled(False)
        self._archival_test_stopped(reason)
        self._log_callback(f"测量已停止: {reason}")
        self._progress_callback(0)

    def _on_finished(self) -> None:
        if not self._archival_is_for_this_page():
            return
        self.driver_mapping_btn.setEnabled(True)
        self.driver_stop_btn.setEnabled(False)
        self.driver_emergency_stop_btn.setEnabled(False)
        self._progress_callback(100)
        self._archival_test_finished()
        self._log_callback("测量完成！")

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "错误", message)


__all__ = ["DriverMappingPage"]
