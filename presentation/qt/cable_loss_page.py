"""独立的线损测量页面。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .measurement_start_facade import MeasurementStartFacade, StartMeasurementRequest
from application.dto import CableLossMeasurementResult
from .archival_page import ArchivalPageMixin
from .measurement_controls import (
    MeasurementAction,
    MeasurementControlPresenter,
)
from .measurement_state import CableLossPageResultState, MeasurementKind, MeasurementStatus
from .measurement_surface import (
    CONTROL_SURFACE,
    RESULT_SURFACE,
    StageMetricsRow,
    build_surface,
)
from .pages import PageControllerBindingMixin
from .stage_summary import (
    NOT_STARTED,
    StageMetric,
    StageSummary,
    count_text,
    frequency_headline,
    frequency_text,
)
from .status_panel import StatusSlot
from .theme import (
    MEASUREMENT_LEFT_PANEL_MAX_WIDTH,
    MEASUREMENT_LEFT_PANEL_MIN_WIDTH,
    set_button_variant,
)


class CableLossPage(ArchivalPageMixin, PageControllerBindingMixin, QWidget):
    """线损测量输入、接线确认与结果展示。"""

    load_result_requested = Signal()

    #: 页面已显示的数据变化（新频点、清空、加载结果）时发出；工作台据此刷新阶段摘要。
    stage_summary_changed = Signal()

    def __init__(
        self,
        *,
        config_path_provider: Callable[[], str],
        run_provider: Callable[[], Any],
        connection_dialog_factory: Callable[[str, QWidget], Any],
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
        self._log_callback = log_callback or (lambda _message: None)
        self._progress_callback = progress_callback or (lambda _value: None)
        self._error_callback = error_callback or self._show_error
        # 批量载入结果期间先不逐行重算摘要，循环结束后只刷新一次。
        self._suspend_stage_summary = False
        # 上一份已展示的摘要：只有真正变化时才重排 KPI 行并通知工作台。
        self._last_stage_summary: StageSummary | None = None
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

        # 左侧窄栏自上而下放控制面与状态面板（进度 + 实时日志）；右侧整块留给结果面。
        # 接线说明只由连接图承担。宽度与另两个测量页共用 theme 里的同一组常量。
        left_panel = QWidget()
        left_panel.setMinimumWidth(MEASUREMENT_LEFT_PANEL_MIN_WIDTH)
        left_panel.setMaximumWidth(MEASUREMENT_LEFT_PANEL_MAX_WIDTH)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        control_surface = build_surface(CONTROL_SURFACE, title="测量控制")
        control_layout = control_surface.layout()
        self.start_measurement_btn = QPushButton("开始测量")
        self.load_cable_results_btn = QPushButton("加载测量结果")
        self.stop_cable_loss_btn = QPushButton("停止测量")
        set_button_variant(self.start_measurement_btn, "primary")
        set_button_variant(self.stop_cable_loss_btn, "secondary")
        set_button_variant(self.load_cable_results_btn, "secondary")
        self.start_measurement_btn.clicked.connect(self.start_measurement)
        self.load_cable_results_btn.clicked.connect(self.load_result_requested.emit)
        self.stop_cable_loss_btn.clicked.connect(self.stop_measurement)
        # 接线说明只保留一个连接图入口：两条路径在同一窗口里上下展示，按钮只用于防止忘记。
        self.show_cable_loss_diagram_btn = QPushButton("查看连接图")
        set_button_variant(self.show_cable_loss_diagram_btn, "secondary")
        self.show_cable_loss_diagram_btn.clicked.connect(
            lambda: self._show_diagram("cable_loss_paths")
        )
        self.controls = MeasurementControlPresenter(
            MeasurementKind.CABLE_LOSS,
            {
                MeasurementAction.START: self.start_measurement_btn,
                MeasurementAction.STOP: self.stop_cable_loss_btn,
            },
        )
        self.controls.show_idle()
        # 开始是控制面唯一的主操作，独占整行；停止、加载与连接图收成一行紧凑次操作。
        control_layout.addWidget(self.start_measurement_btn)
        secondary_actions = QHBoxLayout()
        secondary_actions.setSpacing(8)
        for button in (
            self.stop_cable_loss_btn,
            self.load_cable_results_btn,
            self.show_cable_loss_diagram_btn,
        ):
            secondary_actions.addWidget(button, 1)
        control_layout.addLayout(secondary_actions)
        left_layout.addWidget(control_surface)
        self.archival_banner = self._build_archival_banner()
        left_layout.addWidget(self.archival_banner)
        # 进度与实时日志由窗口挂进这块容器，占满控制面以下的剩余高度。
        self.status_slot = StatusSlot()
        left_layout.addWidget(self.status_slot, 1)
        self.control_panel = left_panel
        layout.addWidget(left_panel)

        # 结果面只放“测量结果”这一块内容：来源行与空态提示在上，表格占满剩余高度。
        result_surface = build_surface(RESULT_SURFACE, title="测量结果")
        result_layout = result_surface.layout()
        # KPI 行只排版页面已有的数据：频点数、当前/最新频点与已显示线缆数。
        self.stage_metrics_row = StageMetricsRow(result_surface)
        result_layout.addWidget(self.stage_metrics_row)
        # 用户通过文件选择器加载结果时，在这里显示来源文件名；新测量或清空后隐藏。
        self.cable_loss_source_label = QLabel("")
        self.cable_loss_source_label.setObjectName("emptyHint")
        self.cable_loss_source_label.hide()
        self.cable_loss_table = QTableWidget()
        self.cable_loss_table.setColumnCount(5)
        self.cable_loss_table.setHorizontalHeaderLabels(
            ["频率(GHz)", "线缆1(dB)", "线缆2(dB)", "线缆3(dB)", "线缆4(dB)"]
        )
        self.cable_loss_table.verticalHeader().setVisible(False)
        self.cable_loss_table.setAlternatingRowColors(True)
        # 结果表只展示测量值：允许选中查看，不允许就地改写。
        self.cable_loss_table.setEditTriggers(QTableWidget.NoEditTriggers)
        # 选中行决定“当前频点”：选择变化时 KPI 行与页头必须跟着走，不能只在查询时读新值。
        self.cable_loss_table.itemSelectionChanged.connect(
            self._on_table_selection_changed
        )
        header = self.cable_loss_table.horizontalHeader()
        for column in range(5):
            header.setSectionResizeMode(column, QHeaderView.Stretch)
        self.cable_loss_empty_hint = QLabel("尚无测量结果：完成测量后每个频点会在此列出线缆损耗。")
        self.cable_loss_empty_hint.setObjectName("emptyHint")
        result_layout.addWidget(self.cable_loss_source_label)
        result_layout.addWidget(self.cable_loss_empty_hint)
        result_layout.addWidget(self.cable_loss_table, 1)
        layout.addWidget(result_surface, 1)
        self._refresh_stage_summary()
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
        # controller 只在 prepare 时读取一次：决策携带检查过的那个对象，接线对话框
        # 的嵌套事件循环返回后不会重新读取 self.controller。
        decision = self._start_facade.prepare(
            lambda: StartMeasurementRequest(
                MeasurementKind.CABLE_LOSS, self._run_provider()
            ),
            controller=self.controller,
            confirm_connection=self._confirm_start_connection,
        )
        if not decision.ready:
            decision.report(self._log_callback, self._error_callback)
            return
        self._log_callback("线损路径1现场接线已确认")
        self.clear_results()
        self._log_callback("开始线损测量...")
        self._archival_try_start(
            lambda: decision.launch(),
            "线损测量未能启动，未开始测量",
        )

    def _confirm_start_connection(self) -> bool:
        return (
            self._connection_dialog_factory("cable_loss_path1", self).exec()
            == QDialog.Accepted
        )

    def stop_measurement(self) -> None:
        if self.controller is not None:
            self.controller.stop()

    def _show_diagram(self, diagram_type: str) -> None:
        self._connection_dialog_factory(diagram_type, self).exec()

    def _on_step_pause(self, message: str) -> None:
        self._log_callback(message)
        if self.controller is None or self.controller.state.status is not MeasurementStatus.WAITING_FOR_CONTINUE:
            return
        dialog = self._connection_dialog_factory("cable_loss_path2", self)
        if dialog.exec() == QDialog.Accepted:
            self.controller.continue_cable_loss_stage2()
        else:
            self.controller.stop()
            self._log_callback("线损测量已停止")

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

    def clear_results(self) -> None:
        self.cable_loss_table.setRowCount(0)
        self._set_source_filename(None)
        self._clear_archival_summary()
        self._sync_empty_hint()
        self._refresh_stage_summary()
        self._log_callback("已清空上一次线损测量结果")

    def stage_summary(self) -> StageSummary:
        """从表格已经显示的数据里取阶段摘要与 KPI，不读取结果 DTO 或文件。

        频点数取表格行数；当前频点优先取选中行，未选中时取最后一行（实时测量按到达
        顺序追加）；线缆数统计已有非空测量值的列。没有行时统一显示“未开始”。
        """
        table = getattr(self, "cable_loss_table", None)
        rows = 0 if table is None else table.rowCount()
        if rows <= 0:
            return StageSummary.not_started()
        cables = sum(
            1
            for column in range(1, 5)
            if any(self._cell_text(row, column) for row in range(rows))
        )
        return StageSummary(
            frequency_headline(rows),
            (
                StageMetric("频点", count_text(rows)),
                StageMetric(
                    "当前频点",
                    frequency_text(self._current_frequency(rows)) or NOT_STARTED,
                ),
                StageMetric("线缆", count_text(cables)),
            ),
        )

    def _current_frequency(self, rows: int) -> str | None:
        """选中行优先，否则取最后一行；表格没有该行时返回 ``None``。"""
        table = getattr(self, "cable_loss_table", None)
        if table is None:
            return None
        selected = sorted({item.row() for item in table.selectedItems()})
        if selected:
            value = self._cell_text(selected[0], 0)
            if value:
                return value
        return self._cell_text(rows - 1, 0)

    def _cell_text(self, row: int, column: int) -> str:
        table = getattr(self, "cable_loss_table", None)
        if table is None:
            return ""
        item = table.item(row, column)
        return item.text().strip() if item is not None else ""

    def _on_table_selection_changed(self) -> None:
        """表格选中行变化时刷新摘要；只影响展示，不触发测量或结果重算。"""
        self._refresh_stage_summary()

    def _refresh_stage_summary(self) -> None:
        """把当前摘要写进结果面 KPI 行，并通知工作台刷新阶段条/页头。

        同一份摘要重复写入不重排控件、也不发信号：实时测量按频点追加数据时，
        阶段条和页头只在频点数、当前频点或线缆数真的变化时才需要更新。
        """
        if self._suspend_stage_summary:
            return
        summary = self.stage_summary()
        if summary == self._last_stage_summary:
            return
        self._last_stage_summary = summary
        row = getattr(self, "stage_metrics_row", None)
        if row is not None:
            row.set_metrics(summary.metrics)
        self.stage_summary_changed.emit()

    def _set_source_filename(self, filename: str | None) -> None:
        """显示/隐藏已加载结果的来源文件名；清空时不给旧文件名留残影。"""
        label = getattr(self, "cable_loss_source_label", None)
        if label is None:
            return
        text = str(filename).strip() if filename is not None else ""
        if text:
            label.setText(f"来源文件：{text}")
            label.show()
        else:
            label.clear()
            label.hide()

    def _sync_empty_hint(self) -> None:
        """空表时给出说明，避免用户把空白表格当成“数据没显示出来”。"""
        hint = getattr(self, "cable_loss_empty_hint", None)
        if hint is not None:
            hint.setVisible(self.cable_loss_table.rowCount() == 0)

    def show_loaded_result(self, reference: Any, *, filename: str | None = None) -> None:
        """显示由窗口/结果服务提供的结构化结果，不读取文件。

        ``filename`` 是用户通过文件选择器选中的来源文件；只在结果成功显示后保留，
        失败时既不显示文件名，也不留下“已加载”的假象。
        """
        value = getattr(reference, "value", None)
        if (
            getattr(reference, "kind", None) is not MeasurementKind.CABLE_LOSS
            or not isinstance(value, (CableLossPageResultState, CableLossMeasurementResult))
        ):
            # Validate before replacing the current selection. show_result emits the
            # established error for an unsupported payload; a different result kind
            # is simply ignored as before.
            self.show_result(reference)
            return
        self.clear_results()
        if not self.show_result(reference):  # defensive if the display contract changes
            return
        self._set_source_filename(filename)
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
                self.cable_loss_table.setItem(row, column, _read_only_item(f"{value:.3f}"))
        self._sync_empty_hint()
        self._refresh_stage_summary()

    def show_result(self, reference: Any) -> bool:
        if getattr(reference, "kind", None) is not MeasurementKind.CABLE_LOSS:
            return False
        value = getattr(reference, "value", None)
        if isinstance(value, CableLossPageResultState):
            self._suspend_stage_summary = True
            try:
                for item in value.rows:
                    self.update_realtime(item)
            finally:
                self._suspend_stage_summary = False
                self._refresh_stage_summary()
        elif isinstance(value, CableLossMeasurementResult):
            self._suspend_stage_summary = True
            try:
                for row in value.points:
                    self.update_realtime(
                        {
                            "frequency": row.frequency_ghz,
                            "cable_losses": row.cable_losses.to_mapping(),
                        }
                    )
            finally:
                self._suspend_stage_summary = False
                self._refresh_stage_summary()
        else:
            self._error_callback("线损测量结果不是正式结果模型，无法显示")
            return False
        return True

    def _find_or_add_row(self, frequency: str) -> int:
        for row in range(self.cable_loss_table.rowCount()):
            item = self.cable_loss_table.item(row, 0)
            if item is not None and item.text() == frequency:
                return row
        row = self.cable_loss_table.rowCount()
        self.cable_loss_table.insertRow(row)
        self.cable_loss_table.setItem(row, 0, _read_only_item(frequency))
        return row

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "错误", message)


def _read_only_item(text: str) -> QTableWidgetItem:
    """构造只读表格条目：展示文本不接受就地编辑，仍可选中与查看。"""
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
    return item


__all__ = ["CableLossPage"]
