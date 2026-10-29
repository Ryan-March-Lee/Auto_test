"""独立的线损测量页面。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .measurement_controller_contract import MeasurementCommand
from application.dto import CableLossMeasurementResult
from .archival_page import ArchivalPageMixin
from .measurement_state import CableLossPageResultState, MeasurementKind, MeasurementStatus
from .pages import PageControllerBindingMixin
from .run_contract import is_valid_prepared_run
from .theme import set_button_variant


class CableLossPage(ArchivalPageMixin, PageControllerBindingMixin, QWidget):
    """线损测量输入、接线确认与结果展示。"""

    load_result_requested = Signal()

    def __init__(
        self,
        *,
        config_path_provider: Callable[[], str],
        run_provider: Callable[[], Any],
        confirm_wiring: Callable[[], bool],
        connection_dialog_factory: Callable[[str, QWidget], Any],
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
        self._confirm_wiring = confirm_wiring
        self._connection_dialog_factory = connection_dialog_factory
        self._log_callback = log_callback or (lambda _message: None)
        self._progress_callback = progress_callback or (lambda _value: None)
        self._error_callback = error_callback or self._show_error
        self._setup_archival(
            MeasurementKind.CABLE_LOSS,
            location_provider=archival_location_provider,
            run_id_provider=archival_run_id_provider,
            run_provider=run_provider,
        )
        self.root = self.build_ui()

    def build_ui(self) -> QWidget:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(12)

        # 左侧窄栏：连接说明、测量控制、归档结果；右侧整块留给结果表。
        left_panel = QWidget()
        left_panel.setMinimumWidth(420)
        left_panel.setMaximumWidth(520)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        instruction_group = QGroupBox("连接说明")
        instruction_layout = QVBoxLayout(instruction_group)
        instruction_layout.setSpacing(10)
        instruction_text = QTextEdit()
        instruction_text.setMaximumHeight(158)
        instruction_text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        instruction_text.setReadOnly(True)
        instruction_text.setHtml("""
        <p><b>线损测量连接说明</b></p>
        <p><b>步骤1 · 路径1测量</b><br>信号源 → 线缆① → 衰减器 → 线缆② → 频谱仪</p>
        <p><b>步骤2 · 路径2测量</b><br>信号源 → 线缆① → 线缆③ → 线缆④ → 衰减器 → 线缆② → 频谱仪</p>
        <p style="color: #B91C1C;"><b>注意：</b>每个步骤会提示您重新连接线缆，请按提示操作</p>
        """)
        instruction_layout.addWidget(instruction_text)
        diagram_layout = QHBoxLayout()
        diagram_layout.setSpacing(10)
        self.show_path1_btn = QPushButton("查看路径1连接图")
        self.show_path2_btn = QPushButton("查看路径2连接图")
        set_button_variant(self.show_path1_btn, "secondary")
        set_button_variant(self.show_path2_btn, "secondary")
        self.show_path1_btn.clicked.connect(lambda: self._show_diagram("cable_loss_path1"))
        self.show_path2_btn.clicked.connect(lambda: self._show_diagram("cable_loss_path2"))
        diagram_layout.addWidget(self.show_path1_btn)
        diagram_layout.addWidget(self.show_path2_btn)
        diagram_layout.addStretch(1)
        instruction_layout.addLayout(diagram_layout)
        left_layout.addWidget(instruction_group)

        control_group = QGroupBox("测量控制")
        control_layout = QGridLayout(control_group)
        control_layout.setHorizontalSpacing(10)
        control_layout.setVerticalSpacing(8)
        self.cable_loss_btn = QPushButton("开始线损测量")
        self.load_cable_results_btn = QPushButton("加载测量结果")
        self.stop_cable_loss_btn = QPushButton("停止测量")
        self.continue_cable_loss_btn = QPushButton("继续第二步测量")
        set_button_variant(self.cable_loss_btn, "primary")
        set_button_variant(self.continue_cable_loss_btn, "primary")
        set_button_variant(self.stop_cable_loss_btn, "secondary")
        set_button_variant(self.load_cable_results_btn, "secondary")
        self.stop_cable_loss_btn.setEnabled(False)
        self.continue_cable_loss_btn.setEnabled(False)
        self.cable_loss_btn.clicked.connect(self.start_measurement)
        self.load_cable_results_btn.clicked.connect(self.load_result_requested.emit)
        self.stop_cable_loss_btn.clicked.connect(self.stop_measurement)
        self.cancel_cable_loss_btn = QPushButton("取消")
        set_button_variant(self.cancel_cable_loss_btn, "secondary")
        self.cancel_cable_loss_btn.setEnabled(False)
        self.cancel_cable_loss_btn.clicked.connect(self.cancel_measurement)
        self.continue_cable_loss_btn.clicked.connect(self.continue_measurement)
        control_layout.addWidget(self.cable_loss_btn, 0, 0)
        control_layout.addWidget(self.stop_cable_loss_btn, 0, 1)
        control_layout.addWidget(self.continue_cable_loss_btn, 1, 0)
        control_layout.addWidget(self.cancel_cable_loss_btn, 1, 1)
        control_layout.addWidget(self.load_cable_results_btn, 2, 0, 1, 2)
        control_layout.setColumnStretch(0, 1)
        control_layout.setColumnStretch(1, 1)
        left_layout.addWidget(control_group)

        self.archival_view = self._create_archival_view()
        left_layout.addWidget(self.archival_view)
        left_layout.addStretch(1)
        layout.addWidget(left_panel)

        result_group = QGroupBox("测量结果")
        result_layout = QVBoxLayout(result_group)
        self.cable_loss_table = QTableWidget()
        self.cable_loss_table.setColumnCount(5)
        self.cable_loss_table.setHorizontalHeaderLabels(
            ["频率(GHz)", "线缆1(dB)", "线缆2(dB)", "线缆3(dB)", "线缆4(dB)"]
        )
        self.cable_loss_table.verticalHeader().setVisible(False)
        self.cable_loss_table.setAlternatingRowColors(True)
        header = self.cable_loss_table.horizontalHeader()
        for column in range(5):
            header.setSectionResizeMode(column, QHeaderView.Stretch)
        self.cable_loss_empty_hint = QLabel("尚无测量结果：完成测量后每个频点会在此列出线缆损耗。")
        self.cable_loss_empty_hint.setObjectName("emptyHint")
        result_layout.addWidget(self.cable_loss_table, 1)
        result_layout.addWidget(self.cable_loss_empty_hint)
        layout.addWidget(result_group, 1)
        return self

    def _connect_controller_signals(self, controller: Any) -> None:
        signals = controller.signals
        self._connect_signal(signals.progress, self._progress_callback)
        self._connect_archival_signals(controller)
        self._connect_signal(signals.data_update, self.update_realtime)
        self._connect_signal(signals.result, self.show_result)
        self._connect_signal(signals.step_pause, self._on_step_pause)
        self._connect_signal(signals.state_changed, self._on_state_changed)
        self._connect_signal(signals.error, self._on_error)
        self._connect_signal(signals.stopped, self._on_stopped)
        self._connect_signal(signals.finished, self._on_finished)

    def start_measurement(self) -> None:
        controller = self.controller
        if controller is None:
            return
        # 已有测量运行时不得进入“等待归档”外观：线损页面此前缺少这道保护，
        # 启动被拒绝后归档分组会一直停留在“结束后将自动归档”。
        if getattr(controller.state, "is_active", False):
            self._log_callback("已有测量正在运行")
            return
        # 只接受配置阶段创建（或按 run_id 重新打开）的当前运行契约；不再在开始前
        # 重新保存 GUI 配置，因此已冻结快照不会被覆盖。
        run = self._run_provider()
        if not is_valid_prepared_run(run):
            self._error_callback("正式线损测量必须先创建或按 run_id 重新加载运行")
            return
        dialog = self._connection_dialog_factory("cable_loss_path1", self)
        if dialog.exec() != QDialog.Accepted:
            return
        if not self._confirm_wiring():
            return
        self.clear_results()
        self._log_callback("开始线损测量...")
        self._archival_try_start(
            lambda: controller.start_cable_loss(
                MeasurementCommand(self._config_path_provider(), prepared_run=run)
            ),
            "线损测量未能启动，未开始测量",
        )

    def stop_measurement(self) -> None:
        if self.controller is not None:
            self.controller.stop()

    def cancel_measurement(self) -> None:
        if self.controller is not None:
            self.controller.cancel()

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
        self.cancel_cable_loss_btn.setEnabled(active)
        self.continue_cable_loss_btn.setEnabled(
            state.kind is MeasurementKind.CABLE_LOSS
            and state.status is MeasurementStatus.WAITING_FOR_CONTINUE
        )

    def _on_error(self, message: str) -> None:
        if not self._archival_is_for_this_page():
            return
        self.cable_loss_btn.setEnabled(True)
        self.stop_cable_loss_btn.setEnabled(False)
        self.cancel_cable_loss_btn.setEnabled(False)
        self.continue_cable_loss_btn.setEnabled(False)
        self._archival_test_failed(message)
        self._error_callback(message)

    def _on_stopped(self, reason: str) -> None:
        if not self._archival_is_for_this_page():
            return
        self._archival_test_stopped(reason)
        self._log_callback(f"测量已停止: {reason}")
        self._progress_callback(0)

    def _on_finished(self) -> None:
        if not self._archival_is_for_this_page():
            return
        self._progress_callback(100)
        self._archival_test_finished()
        self._log_callback("测量完成！")

    def clear_results(self) -> None:
        self.cable_loss_table.setRowCount(0)
        self.archival_view.clear()
        self._archival_pending = False
        self._archival_saving = False
        self._sync_empty_hint()
        self._log_callback("已清空上一次线损测量结果")

    def _sync_empty_hint(self) -> None:
        """空表时给出说明，避免用户把空白表格当成“数据没显示出来”。"""
        hint = getattr(self, "cable_loss_empty_hint", None)
        if hint is not None:
            hint.setVisible(self.cable_loss_table.rowCount() == 0)

    def show_loaded_result(self, reference: Any) -> None:
        """显示由窗口/结果服务提供的结构化结果，不读取文件。"""
        self.clear_results()
        self.show_result(reference)
        self._log_callback(f"已加载线损测量结果，共 {self.cable_loss_table.rowCount()} 个频点")

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
        self._sync_empty_hint()

    def show_result(self, reference: Any) -> None:
        if getattr(reference, "kind", None) is not MeasurementKind.CABLE_LOSS:
            return
        value = getattr(reference, "value", None)
        if isinstance(value, CableLossPageResultState):
            for item in value.rows:
                self.update_realtime(item)
        elif isinstance(value, CableLossMeasurementResult):
            for row in value.points:
                self.update_realtime(
                    {
                        "frequency": row.frequency_ghz,
                        "cable_losses": row.cable_losses.to_mapping(),
                    }
                )
        else:
            self._error_callback("线损测量结果不是正式结果模型，无法显示")

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
