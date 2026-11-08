"""独立的驱动映射测量页面。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .measurement_start_facade import MeasurementStartFacade, StartMeasurementRequest
from application.dto import DriverPowerMappingMeasurementResult
from .archival_page import ArchivalPageMixin
from .measurement_controls import (
    MeasurementAction,
    MeasurementControlPresenter,
)
from .measurement_state import (
    DriverMappingPageResultState,
    MeasurementKind,
)
from .pages import PageControllerBindingMixin
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
        self._run_provider = run_provider
        self._start_facade = MeasurementStartFacade(
            config_path_provider=config_path_provider
        )
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

        # 左侧只放测量控制，宽度固定为窄栏；右侧整块留给四张图。
        # 接线说明只保留连接图入口：测试者已知道仪器怎么接，按钮只用于防止忘记。
        left_panel = QWidget()
        left_panel.setMinimumWidth(400)
        left_panel.setMaximumWidth(430)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        control_group = QGroupBox("测量控制")
        control_layout = QGridLayout(control_group)
        control_layout.setHorizontalSpacing(8)
        control_layout.setVerticalSpacing(8)
        self.driver_mapping_btn = QPushButton("开始驱动映射")
        set_button_variant(self.driver_mapping_btn, "primary")
        self.driver_mapping_btn.clicked.connect(self.start_measurement)
        self.driver_stop_btn = QPushButton("停止测量")
        set_button_variant(self.driver_stop_btn, "secondary")
        self.driver_stop_btn.clicked.connect(self.stop_measurement)
        self.show_driver_diagram_btn = QPushButton("查看连接图")
        set_button_variant(self.show_driver_diagram_btn, "secondary")
        self.show_driver_diagram_btn.clicked.connect(self.show_connection_diagram)
        self.controls = MeasurementControlPresenter(
            MeasurementKind.DRIVER_MAPPING,
            {
                MeasurementAction.START: self.driver_mapping_btn,
                MeasurementAction.STOP: self.driver_stop_btn,
            },
        )
        self.controls.show_idle()
        # 测量控制按钮横向排列在同一行，减少纵向占用面积；连接图入口单独占下一行。
        for column, button in enumerate(
            (
                self.driver_mapping_btn,
                self.driver_stop_btn,
            )
        ):
            control_layout.addWidget(button, 0, column)
        control_layout.addWidget(self.show_driver_diagram_btn, 1, 0, 1, 2)
        left_layout.addWidget(control_group)
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
        # controller 只在 prepare 时读取一次：决策携带检查过的那个对象，接线对话框
        # 的嵌套事件循环返回后不会重新读取 self.controller。
        decision = self._start_facade.prepare(
            lambda: StartMeasurementRequest(
                MeasurementKind.DRIVER_MAPPING, self._run_provider()
            ),
            controller=self.controller,
            confirm_connection=self._confirm_start_connection,
        )
        if not decision.ready:
            decision.report(self._log_callback, self._error_callback)
            return
        self._log_callback("驱动映射现场接线已确认")
        self.reset_realtime_data()
        self._log_callback("开始驱动功放映射...")
        self._archival_try_start(
            lambda: decision.launch(),
            "驱动映射测试未能启动，未开始测量",
        )

    def _confirm_start_connection(self) -> bool:
        return self._connection_dialog_factory("driver_mapping", self).exec() == QDialog.Accepted

    def stop_measurement(self) -> None:
        if self.controller is not None:
            self.controller.stop()

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
                    "input_power": point.source_power_dbm,
                    "output_power": point.driver_output_power_dbm,
                }
                for point in value.raw_points
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
        self.controls.apply(state)

    def _on_error(self, message: str) -> None:
        if not self._archival_is_for_this_page():
            return
        self.controls.show_idle()
        self._archival_test_failed(message)
        self._error_callback(message)

    def _on_stopped(self, reason: str) -> None:
        if not self._archival_is_for_this_page():
            return
        self.controls.show_idle()
        self._archival_test_stopped(reason, self._partial_result_path())
        self._log_callback(f"测量已停止: {reason}")
        self._progress_callback(0)

    def _on_finished(self) -> None:
        if not self._archival_is_for_this_page():
            return
        self.controls.show_idle()
        self._progress_callback(100)
        self._archival_test_finished()
        self._log_callback("测量完成！")

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "错误", message)


__all__ = ["DriverMappingPage"]
