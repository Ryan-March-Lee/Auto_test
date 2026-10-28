"""独立的主功放测量页面。"""

from __future__ import annotations

from collections.abc import Mapping
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
from application.dto import AmplifierMeasurementResult
from .archival_status import (
    ArchivalSummary,
    summarize_archival,
    summarize_archival_missing,
    summarize_archival_running,
    summarize_archival_saving,
    summarize_test_cancelled,
    summarize_test_failure,
)
from .archival_view import ArchivalStatusView
from .measurement_state import AmplifierPageResultState, MeasurementKind
from .pages import PageControllerBindingMixin
from .run_contract import is_valid_prepared_run
from .realtime_buffer import RealtimeMeasurementBuffer
from .realtime_page import RealtimePageMixin


class AmplifierPage(RealtimePageMixin, PageControllerBindingMixin, QWidget):
    """主功放测量的输入、接线确认、进度和结果展示。"""

    def __init__(
        self,
        *,
        config_path_provider: Callable[[], str],
        run_provider: Callable[[], Any],
        confirm_wiring: Callable[[], bool],
        driver_mode_provider: Callable[[], bool],
        connection_dialog_factory: Callable[[str, QWidget], Any],
        plot_widget_factory: Callable[[QWidget], QWidget],
        realtime_data_callback: Callable[[Mapping[str, Any]], Any] | None = None,
        clear_realtime_callback: Callable[[], Any] | None = None,
        log_callback: Callable[[str], Any] | None = None,
        progress_callback: Callable[[int], Any] | None = None,
        error_callback: Callable[[str], Any] | None = None,
        archival_location_provider: Callable[[str, str | None], str | None] | None = None,
    ) -> None:
        PageControllerBindingMixin.__init__(self)
        QWidget.__init__(self)
        self._config_path_provider = config_path_provider
        self._run_provider = run_provider
        self._confirm_wiring = confirm_wiring
        self._driver_mode_provider = driver_mode_provider
        self._connection_dialog_factory = connection_dialog_factory
        self._plot_widget_factory = plot_widget_factory
        self._realtime_data_callback = realtime_data_callback or (lambda _data: None)
        self._clear_realtime_callback = clear_realtime_callback or (lambda: None)
        self._log_callback = log_callback or (lambda _message: None)
        self._progress_callback = progress_callback or (lambda _value: None)
        self._error_callback = error_callback or self._show_error
        self._archival_location_provider = archival_location_provider
        # 本次测量是否还在等待自动归档结果；用于提示“状态未知”而不是假装完成。
        self._archival_pending = False
        # 是否已经提示“正在保存/生成”，避免每次进度刷新都重复写日志。
        self._archival_saving = False
        self.realtime_buffer = RealtimeMeasurementBuffer()
        self.root = self.build_ui()

    def build_ui(self) -> QWidget:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        # 左侧窄栏：连接说明、测量控制、测量结果；右侧整块留给四张图。
        left_panel = QWidget()
        left_panel.setMinimumWidth(420)
        left_panel.setMaximumWidth(520)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        instruction_group = QGroupBox("连接说明")
        instruction_layout = QVBoxLayout(instruction_group)
        self.instruction_text = QTextEdit()
        self.instruction_text.setMaximumHeight(120)
        self.instruction_text.setReadOnly(True)
        instruction_layout.addWidget(self.instruction_text)
        self.show_amp_diagram_btn = QPushButton("查看连接图")
        self.show_amp_diagram_btn.clicked.connect(self.show_connection_diagram)
        instruction_layout.addWidget(self.show_amp_diagram_btn)
        left_layout.addWidget(instruction_group)

        control_group = QGroupBox("测量控制")
        control_layout = QHBoxLayout(control_group)
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
        # 测量控制按钮横向排列在同一行，减少纵向占用面积。
        for button in (
            self.amplifier_test_btn,
            self.cancel_btn,
            self.emergency_stop_btn,
        ):
            control_layout.addWidget(button)
        control_layout.addStretch(1)
        left_layout.addWidget(control_group)

        # 归档结果紧跟测量控制：测试结束后用户在当前页面即可确认结果是否已保存。
        self.archival_view = ArchivalStatusView(
            location_provider=self._archival_location_provider,
            log_callback=self._log_callback,
        )
        left_layout.addWidget(self.archival_view)

        result_group = QGroupBox("测量结果")
        result_layout = QVBoxLayout(result_group)
        self.result_table = QTableWidget()
        self.result_table.setColumnCount(4)
        self.result_table.setHorizontalHeaderLabels(["频率(GHz)", "压缩点", "小信号增益(dB)", "是否达到压缩"])
        result_layout.addWidget(self.result_table)
        # 结果表占据左栏剩余高度，但不再横向铺满整个页面。
        left_layout.addWidget(result_group, 1)
        self.control_panel = left_panel
        layout.addWidget(left_panel)

        self.amplifier_plot_widget = self._plot_widget_factory(self)
        self._realtime_plot_widget = self.amplifier_plot_widget
        for signal_name, slot in (("prev_clicked", self.previous_frequency), ("next_clicked", self.next_frequency)):
            signal = getattr(self.amplifier_plot_widget, signal_name, None)
            if signal is not None:
                signal.connect(slot)
        layout.addWidget(self.amplifier_plot_widget, 1)
        self.update_instruction_text()
        return self

    def _connect_controller_signals(self, controller: Any) -> None:
        signals = controller.signals
        self._connect_signal(signals.progress, self._progress_callback)
        self._connect_signal(signals.progress, self._on_progress_archival)
        self._connect_signal(signals.data_update, self.update_realtime)
        self._connect_signal(signals.result, self.show_result)
        self._connect_signal(signals.archival, self.show_archival)
        self._connect_signal(signals.state_changed, self._on_state_changed)
        self._connect_signal(signals.error, self._on_error)
        self._connect_signal(signals.stopped, self._on_stopped)
        self._connect_signal(signals.finished, self._on_finished)

    def update_instruction_text(self, driver_enabled: bool | None = None) -> None:
        """按驱动模式刷新连接说明。

        ``driver_enabled`` 不传时取运行契约中的驱动模式；配置页切换驱动模式时直接传入
        复选框状态，用户在创建运行前也能看到对应的接线说明。
        """
        if driver_enabled is None:
            driver_enabled = self._driver_mode_provider()
        if driver_enabled:
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
        # 只接受配置阶段创建（或按 run_id 重新打开）的当前运行契约；主功放参数已在
        # 该运行的冻结快照中，不再从 GUI 重新保存配置。
        run = self._run_provider()
        if not is_valid_prepared_run(run):
            self._error_callback("正式主功放测量必须先创建或按 run_id 重新加载运行")
            return
        self.update_instruction_text()
        diagram = "amplifier_test" if self._driver_mode_provider() else "amplifier_test_no_driver"
        if self._connection_dialog_factory(diagram, self).exec() != QDialog.Accepted:
            return
        if not self._confirm_wiring():
            return
        self.reset_realtime_data()
        self.result_table.setRowCount(0)
        # 归档进行中的状态：让用户知道结束后还要保存结果、生成产物。
        self._archival_pending = True
        self._archival_saving = False
        self._show_archival_summary(
            summarize_archival_running(MeasurementKind.AMPLIFIER)
        )
        self._log_callback("开始主功放测试...")
        controller.start_amplifier(
            MeasurementCommand(self._config_path_provider(), prepared_run=run)
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
        elif isinstance(value, AmplifierMeasurementResult):
            rows = tuple(_amplifier_row(item) for item in value.frequencies)
        else:
            self._error_callback("主功放测量结果不是正式结果模型，无法显示")
            return
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

    def _on_progress_archival(self, value: int) -> None:
        """进度到 100% 表示扫描结束，随后是保存结果与生成产物：明确告知用户。"""
        if not self._archival_pending or self._archival_saving:
            return
        if self.controller is None or self.controller.state.kind is not MeasurementKind.AMPLIFIER:
            return
        try:
            progress = int(value)
        except (TypeError, ValueError):
            return
        if progress < 100:
            return
        self._archival_saving = True
        self._show_archival_summary(
            summarize_archival_saving(MeasurementKind.AMPLIFIER)
        )

    def show_archival(self, outcome: Any) -> None:
        """显示自动归档结果：只渲染状态，不读取文件、不拼接导出规则。"""
        if getattr(outcome, "stage", None) != MeasurementKind.AMPLIFIER.value:
            return
        self._archival_pending = False
        try:
            summary = summarize_archival(outcome)
        except (TypeError, ValueError) as error:
            self._show_archival_summary(
                summarize_archival_missing(
                    MeasurementKind.AMPLIFIER, self._current_run_id()
                )
            )
            self._log_callback(f"归档结果无法显示: {error}")
            return
        self._show_archival_summary(summary)

    def _show_archival_summary(self, summary: ArchivalSummary) -> None:
        """把一条归档提示写到页面控件和日志里。"""
        self.archival_view.show_summary(summary)
        self._log_callback(f"{summary.headline}：{summary.detail}")

    def _current_run_id(self) -> str | None:
        """当前运行 ID，仅用于状态文案；不可用时返回 ``None``。"""
        run_id = getattr(self._run_provider(), "run_id", None)
        return run_id if isinstance(run_id, str) and run_id.strip() else None

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
        self._archival_pending = False
        self._show_archival_summary(
            summarize_test_failure(MeasurementKind.AMPLIFIER, message)
        )
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
        self._archival_pending = False
        self._show_archival_summary(
            summarize_test_cancelled(MeasurementKind.AMPLIFIER, reason)
        )
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
        if self._archival_pending:
            # 测试确实完成了，但没有收到归档结果：如实提示状态未知。
            self._archival_pending = False
            self._show_archival_summary(
                summarize_archival_missing(
                    MeasurementKind.AMPLIFIER, self._current_run_id()
                )
            )
        self._log_callback("测量完成！")

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "错误", message)


def _amplifier_row(item: Any) -> Mapping[str, Any]:
    """把一个频点的正式主功放结果转换为表格行。"""
    point = item.compression_point
    return {
        "frequency": item.frequency_ghz,
        "compression_point": "" if point is None or point.output_power_dbm is None
        else point.output_power_dbm,
        "small_signal_gain": ""
        if item.small_signal_gain_db is None
        else item.small_signal_gain_db,
        "compression_achieved": ""
        if item.compression_achieved is None
        else item.compression_achieved,
        "sweep_data": item.sweep_data,
    }


__all__ = ["AmplifierPage"]
