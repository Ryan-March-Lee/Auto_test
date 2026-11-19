"""独立的线损测量页面。

结果面与功放页同构：顶部线损总览监控台 + 2×2 四幅曲线图。线损的数据形状是“每个频点
× 四条线缆”，因此四幅图各画一条线缆的“线损 vs 频率”（横轴频率 GHz、纵轴线损 dB），
整条曲线一次看全；监控台把这条带等分成四格，每格给出该线缆本轮已测频点的
Max / Min / Avg。页面不持有测量状态机、不读结果文件、不发测量命令。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .cable_loss_monitor import CableLossMonitor, CableLossStats
from .measurement_console import (
    MeasurementRunConsole,
    archive_check,
    run_session_check,
    wiring_check,
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
    build_surface,
)
from .pages import PageControllerBindingMixin
from .realtime_buffer import normalize_frequency
from .realtime_plot import CABLE_LOSS_PANELS
from .stage_summary import (
    StageSummary,
    frequency_headline,
)
from .status_panel import StatusSlot
from .theme import (
    MEASUREMENT_LEFT_PANEL_MAX_WIDTH,
    MEASUREMENT_LEFT_PANEL_MIN_WIDTH,
    set_button_variant,
)

#: 四条线缆的展示顺序与读数标签；图表键名统一由下标派生，不重复写字面字段名。
_CABLE_LABELS = tuple(f"线缆{index}" for index in range(1, 5))


@dataclass
class _CableLossPoint:
    """一个频点已经显示的线缆损耗。

    ``losses`` 只保存已有数值的线缆下标（1–4）。路径1 阶段只有线缆1/2，路径2 完成后
    才补齐四条；缺项保持缺失，读数条写“—”，不按 0 处理。
    """

    frequency: float
    text: str
    losses: dict[int, float] = field(default_factory=dict)


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
        plot_widget_factory: Callable[[QWidget], QWidget],
        log_callback: Callable[[str], Any] | None = None,
        progress_callback: Callable[[int], Any] | None = None,
        error_callback: Callable[[str], Any] | None = None,
        archival_location_provider: Callable[[str, str | None], str | None] | None = None,
        archival_run_id_provider: Callable[[], str | None] | None = None,
    ) -> None:
        PageControllerBindingMixin.__init__(self)
        QWidget.__init__(self)
        self._run_provider = run_provider
        self._plot_widget_factory = plot_widget_factory
        self._start_facade = MeasurementStartFacade(
            config_path_provider=config_path_provider,
        )
        self._connection_dialog_factory = connection_dialog_factory
        self._log_callback = log_callback or (lambda _message: None)
        self._progress_callback = progress_callback or (lambda _value: None)
        self._error_callback = error_callback or self._show_error
        # 批量载入结果期间先不逐点重算摘要，循环结束后只刷新一次。
        self._suspend_stage_summary = False
        # 上一份已展示的摘要：只有真正变化时才通知工作台刷新阶段条。
        self._last_stage_summary: StageSummary | None = None
        # 已显示的频点，按到达顺序保存；绘图用的横轴排序在取序列时再做。
        self._points: list[_CableLossPoint] = []
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
        # 运行控制台回答“这是哪次运行、开始前还缺什么”，紧挨控制面放在日志上方。
        self.console = MeasurementRunConsole()
        left_layout.addWidget(self.console)
        self.archival_banner = self._build_archival_banner()
        left_layout.addWidget(self.archival_banner)
        # 进度与实时日志由窗口挂进这块容器，占满控制面以下的剩余高度。
        self.status_slot = StatusSlot()
        left_layout.addWidget(self.status_slot, 1)
        self.control_panel = left_panel
        layout.addWidget(left_panel)

        # 结果面与功放页同构：总览带在上，来源行依次向下，四幅线缆曲线占满剩余高度。
        # 总览带回答“四条线缆到目前为止最好/最差/平均是多少”，四幅图回答“损耗随频率
        # 怎么走”；最新频点的读数已经由绘图控件自带的胶片条承担，不再重复一遍。
        result_surface = build_surface(RESULT_SURFACE, title="测量结果")
        result_layout = result_surface.layout()
        self.cable_loss_monitor = CableLossMonitor(_CABLE_LABELS, result_surface)
        result_layout.addWidget(self.cable_loss_monitor)
        # 用户通过文件选择器加载结果时，在这里显示来源文件名；新测量或清空后隐藏。
        self.cable_loss_source_label = QLabel("")
        self.cable_loss_source_label.setObjectName("emptyHint")
        self.cable_loss_source_label.hide()
        result_layout.addWidget(self.cable_loss_source_label)
        # 线缆曲线：绘图控件按 CABLE_LOSS_PANELS 建四幅“线损 vs 频率”子图。
        self.cable_loss_plot = self._plot_widget_factory(result_surface)
        set_panels = getattr(self.cable_loss_plot, "set_panels", None)
        if set_panels is not None:
            set_panels(CABLE_LOSS_PANELS)
        result_layout.addWidget(self.cable_loss_plot, 1)
        # 结果面底部一句话：本段范围、最大值与数据来自哪些频点；不含表格条长的说明。
        self.cable_loss_summary_label = QLabel("")
        self.cable_loss_summary_label.setObjectName("measurementResultSummary")
        self.cable_loss_summary_label.setWordWrap(True)
        self.cable_loss_summary_label.hide()
        result_layout.addWidget(self.cable_loss_summary_label)
        layout.addWidget(result_surface, 1)
        self._refresh_stage_summary()
        self._refresh_console()
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
        # 前置检查与命令构造完全走 facade，页面不复制运行契约判据。
        # controller 只在 prepare 时读取一次：决策携带检查过的那个对象，接线对话框
        # 的嵌套事件循环返回后不会重新读取 self.controller。
        decision = self._start_facade.prepare(
            lambda: StartMeasurementRequest.from_session(
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
        self._refresh_console()

    def on_activated(self) -> None:
        """回到本页时刷新运行事实：运行可能已在配置页重新连接。"""
        super().on_activated()
        self._refresh_console()

    def _refresh_console(self) -> None:
        """把运行标识、归档位置与前置检查写进左栏控制台。

        所有输入都是无副作用的纯查询：``_current_run_id()`` 不回退到会弹窗的
        ``resolve_measurement_run()``；归档目录只做文案定位，不读文件。
        """
        console = getattr(self, "console", None)
        if console is None:
            return
        run_id = self._current_run_id()
        configured = self._archival_location_provider is not None
        directory = self._archival_directory(run_id)
        console.set_run_facts(
            run_id=run_id,
            mode=None,
            archive=directory,
            archive_configured=configured,
        )
        console.set_checks(
            (
                run_session_check(run_id),
                wiring_check(),
                archive_check(directory, configured=configured),
            )
        )

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
        """清空已显示的结果：图表、总览带、来源行与摘要一起回到空态。"""
        self._points.clear()
        clear_plot = getattr(self.cable_loss_plot, "clear_plots", None)
        if clear_plot is not None:
            clear_plot()
        self._set_source_filename(None)
        self._clear_archival_summary()
        self._refresh_monitor()
        self._refresh_result_summary()
        self._refresh_stage_summary()
        self._log_callback("已清空上一次线损测量结果")

    def point_count(self) -> int:
        """已显示的频点数（页面自己的数据，不读结果 DTO）。"""
        return len(self._points)

    def stage_summary(self) -> StageSummary:
        """从已显示的频点取阶段条摘要，不读取结果 DTO 或文件。

        频点数取页面保存的频点；没有数据时统一显示“未开始”。四条线缆的累计统计
        由结果面总览带承担，阶段摘要不再保存第二份。
        """
        count = len(self._points)
        if count <= 0:
            return StageSummary.not_started()
        return StageSummary(frequency_headline(count))

    def _cable_stats(self) -> tuple[CableLossStats, ...]:
        """按线缆汇总已显示频点：单次遍历分桶，缺项不按 0 计入。"""
        samples: list[list[float]] = [[] for _ in _CABLE_LABELS]
        for point in self._points:
            for index, bucket in enumerate(samples, start=1):
                value = point.losses.get(index)
                if value is not None:
                    bucket.append(value)
        return tuple(
            CableLossStats.from_values(bucket) for bucket in samples
        )

    def _refresh_monitor(self) -> None:
        """把四条线缆的累计统计写进总览带；清空后四格一起回到“—”。"""
        monitor = getattr(self, "cable_loss_monitor", None)
        if monitor is not None:
            monitor.set_stats(self._cable_stats())

    def _refresh_result_summary(self) -> None:
        """结果面底部一句话：频点数与全局峰值出现在哪条线缆、哪个频点。

        Max / Min / Avg 已经由顶部总览带按线缆给出，这里不再重复每条线缆的范围，
        只补上总览带没有的信息：整轮测量最大值的位置。
        """
        label = getattr(self, "cable_loss_summary_label", None)
        if label is None:
            return
        if not self._points:
            label.clear()
            label.hide()
            return
        parts = [f"{len(self._points)} 频点"]
        peak: float | None = None
        peak_cable = ""
        peak_text = ""
        for index, cable_label in enumerate(_CABLE_LABELS, start=1):
            for point in self._points:
                value = point.losses.get(index)
                if value is None or (peak is not None and value <= peak):
                    continue
                peak, peak_cable, peak_text = value, cable_label, point.text
        if peak is not None:
            suffix = f" @ {peak_text} GHz" if peak_text else ""
            parts.append(f"{peak_cable} 峰值 {peak:.3f} dB{suffix}")
        label.setText(" · ".join(parts))
        label.show()

    def _refresh_plot(self) -> None:
        """把已显示的频点整理成每幅图一条“线损 vs 频率”曲线并交给绘图控件。"""
        update_plot = getattr(self.cable_loss_plot, "update_plot", None)
        if update_plot is None:
            return
        update_plot({"series": self._plot_series()})

    def _plot_series(self) -> dict[str, tuple[list[float], list[float]]]:
        """按线缆拆成 (频率, 线损) 序列；只包含该线缆已有数值的频点。"""
        ordered = sorted(self._points, key=lambda point: point.frequency)
        series: dict[str, tuple[list[float], list[float]]] = {}
        for index in range(1, 5):
            xs = [
                point.frequency for point in ordered if index in point.losses
            ]
            ys = [point.losses[index] for point in ordered if index in point.losses]
            series[f"cable{index}"] = (xs, ys)
        return series

    def _refresh_display(self) -> None:
        """一次刷新图表、总览带、摘要与阶段条：数据到达与批量载入共用这一条路径。"""
        self._refresh_plot()
        self._refresh_monitor()
        self._refresh_result_summary()
        self._refresh_stage_summary()

    def _refresh_stage_summary(self) -> None:
        """把频点数摘要写进阶段条，并通知工作台刷新。

        同一份摘要重复出现时不发信号：实时测量按频点追加数据时，阶段条只在频点数
        真的变化时才需要更新。
        """
        if self._suspend_stage_summary:
            return
        summary = self.stage_summary()
        if summary == self._last_stage_summary:
            return
        self._last_stage_summary = summary
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
        self._log_callback(
            f"已加载线损测量结果，共 {len(self._points)} 个频点"
        )

    def update_realtime(self, data: Any) -> None:
        """接收一个频点的实时线缆损耗；同一频点再次到达时合并为一条曲线点。

        批量载入结果期间只累积数据，由调用方在循环结束后统一刷新一次：逐点重绘
        四幅图在几十个频点上会白付几十次整图绘制的钱。
        """
        if not isinstance(data, Mapping) or "frequency" not in data:
            return
        frequency = normalize_frequency(data.get("frequency"))
        if frequency is None:
            return
        losses = self._losses_from(data)
        if not losses:
            return
        self._store_point(frequency, str(data["frequency"]).strip(), losses)
        if self._suspend_stage_summary:
            return
        self._refresh_display()

    @staticmethod
    def _losses_from(data: Mapping[str, Any]) -> dict[int, float]:
        """读取 ``cable1``…``cable4`` 的有限数值；缺项或非数值保持缺失。"""
        raw = data.get("cable_losses")
        if not isinstance(raw, Mapping):
            return {}
        losses: dict[int, float] = {}
        for index in range(1, 5):
            try:
                value = float(raw.get(f"cable{index}"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                losses[index] = value
        return losses

    def _store_point(
        self, frequency: float, text: str, losses: dict[int, float]
    ) -> None:
        """按频率合并一个频点：已有则补齐/更新数值，没有则追加到末尾。"""
        for point in self._points:
            if point.frequency == frequency:
                point.losses.update(losses)
                if text:
                    point.text = text
                return
        self._points.append(
            _CableLossPoint(frequency=frequency, text=text, losses=dict(losses))
        )

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
                self._refresh_display()
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
                self._refresh_display()
        else:
            self._error_callback("线损测量结果不是正式结果模型，无法显示")
            return False
        return True

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "错误", message)


__all__ = ["CableLossPage"]
