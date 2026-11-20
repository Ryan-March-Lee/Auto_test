"""独立的驱动映射测量页面。"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .measurement_start_facade import (
    MeasurementStartFacade,
    StartMeasurementRequest,
    is_busy,
)
from application.dto import DriverPowerMappingMeasurementResult
from application.dto.measurement_requests import StageResultDigest
from .archival_page import ArchivalPageMixin
from .load_data_dialog import LoadTarget, load_tooltip, selection_source_text
from .measurement_console import (
    CheckState,
    ConsoleCheck,
    MeasurementRunConsole,
    archive_check,
    input_check,
    run_session_check,
    wiring_check,
)
from .measurement_controls import (
    MeasurementAction,
    MeasurementControlPresenter,
    state_is_active,
)
from .measurement_readout import MeasurementReadout, ReadoutMetric
from .measurement_state import (
    DriverMappingPageResultState,
    MeasurementKind,
    StageResultSelection,
)
from .measurement_surface import (
    CONTROL_SURFACE,
    RESULT_SURFACE,
    build_surface,
)
from .pages import PageControllerBindingMixin
from .realtime_buffer import RealtimeMeasurementBuffer
from .realtime_page import RealtimePageMixin, format_latest_number
from .realtime_plot import DRIVER_MAPPING_PANELS
from .stage_summary import (
    StageSummary,
    frequency_headline,
)
from .status_panel import StatusSlot
from .theme import (
    MEASUREMENT_LEFT_PANEL_WIDTH,
    set_button_variant,
)


class DriverMappingPage(
    RealtimePageMixin, ArchivalPageMixin, PageControllerBindingMixin, QWidget
):
    """驱动映射输入、接线确认、进度和结构化结果展示。"""

    # 页面只发出“用户想加载线损结果”的意图；打开文件选择器和应用层校验都由窗口
    # 负责，页面不持有路径服务，也不读取 JSON。
    load_cable_loss_requested = Signal()

    #: 页面已显示的数据变化（新频点、清空）时发出；工作台据此刷新阶段摘要。
    stage_summary_changed = Signal()

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
        error_callback: Callable[[str], Any] | None = None,
        archival_location_provider: Callable[[str, str | None], str | None] | None = None,
        archival_run_id_provider: Callable[[], str | None] | None = None,
    ) -> None:
        PageControllerBindingMixin.__init__(self)
        QWidget.__init__(self)
        self._run_provider = run_provider
        # 用户在本次会话里显式选择的线损结果；页面切换不清除，只有重新选择才替换。
        self._cable_loss_selection: StageResultSelection | None = None
        self._start_facade = MeasurementStartFacade(
            config_path_provider=config_path_provider,
        )
        self._connection_dialog_factory = connection_dialog_factory
        self._plot_widget_factory = plot_widget_factory
        self._realtime_data_callback = realtime_data_callback or (lambda _data: None)
        self._clear_realtime_callback = clear_realtime_callback or (lambda: None)
        self._log_callback = log_callback or (lambda _message: None)
        self._error_callback = error_callback or self._show_error
        # 批量载入结果期间先不逐行重算摘要，循环结束后只刷新一次。
        self._suspend_stage_summary = False
        # 上一份已展示的摘要：只有真正变化时才通知工作台刷新阶段条。
        self._last_stage_summary: StageSummary | None = None
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

        # 左侧窄栏自上而下放运行控制台、控制面与运行状态；右侧整块留给四张图。
        # 接线说明只保留连接图入口：测试者已知道仪器怎么接，按钮只用于防止忘记。
        # 宽度与另三个左栏页面共用 theme 里的同一个固定值。
        left_panel = QWidget()
        left_panel.setFixedWidth(MEASUREMENT_LEFT_PANEL_WIDTH)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        control_surface = build_surface(CONTROL_SURFACE, title="测量控制")
        control_layout = control_surface.layout()
        self.driver_mapping_btn = QPushButton("开始测试")
        set_button_variant(self.driver_mapping_btn, "primary")
        self.driver_mapping_btn.clicked.connect(self.start_measurement)
        self.driver_stop_btn = QPushButton("停止测量")
        set_button_variant(self.driver_stop_btn, "secondary")
        self.driver_stop_btn.clicked.connect(self.stop_measurement)
        self.show_driver_diagram_btn = QPushButton("查看连接图")
        set_button_variant(self.show_driver_diagram_btn, "secondary")
        self.show_driver_diagram_btn.clicked.connect(self.show_connection_diagram)
        # 一个“加载数据”入口：本页只有线损一种数据，点击直接进入文件选择器；
        # 已选来源放在按钮 tooltip 里，控制面不再是“按钮 + 来源文字”的纵向堆叠。
        self.load_data_btn = QPushButton("加载数据")
        set_button_variant(self.load_data_btn, "secondary")
        self.load_data_btn.clicked.connect(self.load_cable_loss_requested.emit)
        self.controls = MeasurementControlPresenter(
            MeasurementKind.DRIVER_MAPPING,
            {
                MeasurementAction.START: self.driver_mapping_btn,
                MeasurementAction.STOP: self.driver_stop_btn,
            },
        )
        self.controls.show_idle()
        # 第一行是运行控制（开始仍是唯一的主操作），第二行是加载数据与连接图：
        # 加载数据在左，查看连接图在右，与另两个测量页一致。
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        action_row.addWidget(self.driver_mapping_btn, 1)
        action_row.addWidget(self.driver_stop_btn, 1)
        control_layout.addLayout(action_row)
        load_row = QHBoxLayout()
        load_row.setContentsMargins(0, 0, 0, 0)
        load_row.setSpacing(8)
        load_row.addWidget(self.load_data_btn, 1)
        load_row.addWidget(self.show_driver_diagram_btn, 1)
        control_layout.addLayout(load_row)
        # 运行控制台放在左栏顶部：运行时间、线损输入是否已选、接线与归档位置。
        self.console = MeasurementRunConsole()
        left_layout.addWidget(self.console)
        left_layout.addWidget(control_surface)
        self._sync_load_tooltip()
        self._refresh_console()
        self.archival_banner = self._build_archival_banner()
        left_layout.addWidget(self.archival_banner)
        # 运行状态由窗口挂进这块容器，固定一行。
        self.status_slot = StatusSlot()
        left_layout.addWidget(self.status_slot, 1)
        self.control_panel = left_panel
        layout.addWidget(left_panel)

        # 结果面只承载“测量结果”这一块内容：读数条在上，频点浏览与四张图由绘图控件自带。
        result_surface = build_surface(RESULT_SURFACE, title="测量结果")
        result_layout = result_surface.layout()
        self.readout = MeasurementReadout(result_surface)
        result_layout.addWidget(self.readout)
        self.driver_plot_widget = self._plot_widget_factory(result_surface)
        self._realtime_plot_widget = self.driver_plot_widget
        # 驱动映射只测输入-输出功率与增益；效率与 DC 功耗没有测量数据，不画空图。
        set_panels = getattr(self.driver_plot_widget, "set_panels", None)
        if set_panels is not None:
            set_panels(DRIVER_MAPPING_PANELS)
        prev_clicked = getattr(self.driver_plot_widget, "prev_clicked", None)
        next_clicked = getattr(self.driver_plot_widget, "next_clicked", None)
        if prev_clicked is not None:
            prev_clicked.connect(self.previous_frequency)
        if next_clicked is not None:
            next_clicked.connect(self.next_frequency)
        # 胶片条点击是“切到已测频点”，不是开始/停止测量；只有真实绘图控件提供
        # 这个信号时接线，测试替身保持原样。
        frequency_selected = getattr(
            self.driver_plot_widget, "frequency_selected", None
        )
        if frequency_selected is not None:
            frequency_selected.connect(self.select_frequency)
        result_layout.addWidget(self.driver_plot_widget, 1)
        layout.addWidget(result_surface, 1)
        self.readout.set_reading("—", "", self._readout_metrics(), active=False)
        self._refresh_stage_summary()
        self._refresh_console()
        return self

    def _connect_controller_signals(self, controller: Any) -> None:
        signals = controller.signals
        self._connect_archival_signals(controller)
        self._connect_signal(signals.data_update, self.update_realtime)
        self._connect_signal(signals.result, self.show_result)
        self._connect_signal(signals.state_changed, self._on_state_changed)
        self._connect_signal(signals.error, self._on_error)
        self._connect_signal(signals.stopped, self._on_stopped)
        self._connect_signal(signals.finished, self._on_finished)

    def start_measurement(self) -> None:
        # 线损是驱动映射的前置输入：没有用户显式选择时不去读取运行目录或最新结果，
        # 直接在当前页面拦下，避免先弹接线确认再失败。页面只判断“是否选过”，文件
        # 内容与兼容性仍由应用层在测量开始前校验。
        #
        # 其它页面测量运行时，本页“开始”按钮仍可能被点到；此时先把顺序让给
        # facade 统一报告“已有测量正在运行”，不要用线损缺失掩盖更直接的原因。
        # 忙碌判据来自 facade 的 is_busy，页面不复制“什么算活动测量”。
        if (
            self.controller is not None
            and not is_busy(self.controller)
            and self._cable_loss_selection is None
        ):
            self._error_callback("请先加载驱动映射使用的线损测量结果")
            self._log_callback("未选择线损测量结果，驱动映射未启动")
            return
        # controller 只在 prepare 时读取一次：决策携带检查过的那个对象，接线对话框
        # 的嵌套事件循环返回后不会重新读取 self.controller。
        decision = self._start_facade.prepare(
            lambda: StartMeasurementRequest.from_session(
                MeasurementKind.DRIVER_MAPPING,
                self._run_provider(),
                cable_loss_path=self._selected_cable_loss_path(),
                cable_loss_digest=self._selected_cable_loss_digest(),
            ),
            controller=self.controller,
            confirm_connection=self._confirm_start_connection,
        )
        if not decision.ready:
            decision.report(self._log_callback, self._error_callback)
            return
        self._log_callback("驱动映射现场接线已确认")
        self.reset_realtime_data()
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

    def show_loaded_cable_loss(
        self,
        *,
        path: str,
        filename: str,
        created_at: str,
        sha256: str,
        size_bytes: int,
    ) -> None:
        """记录用户显式选择的线损结果，并更新“加载数据”的来源说明。

        文件内容、阶段目录边界与结果类型已由应用层校验；页面只保存窗口交回的事实，
        不读取文件，也不把这里的显示当成测量输入的第二次校验。``sha256`` 与
        ``size_bytes`` 是选择那一刻的文件事实，启动时由应用层复核文件没有被替换。
        日志只说“已选择”，不暗示正在运行（或下一次）测量已经消费了这份文件。
        """
        self._cable_loss_selection = StageResultSelection(
            path=path,
            filename=filename,
            created_at=created_at,
            digest=StageResultDigest(sha256=sha256, size_bytes=size_bytes),
        )
        self._sync_load_tooltip()
        self._refresh_console()
        self._log_callback(
            f"已选择驱动映射线损测量结果：{filename}"
            f"（测量时间：{created_at}）"
        )

    @property
    def cable_loss_selection(self) -> StageResultSelection | None:
        """当前显式选择的线损结果；未选择时为 ``None``。"""
        return self._cable_loss_selection

    def _selected_cable_loss_path(self) -> str | None:
        """启动时冻结的线损来源；页面没有选择时为 ``None``（由页面先行拦下）。"""
        selection = self._cable_loss_selection
        return None if selection is None else selection.path

    def _selected_cable_loss_digest(self) -> StageResultDigest | None:
        """启动时冻结的选择时文件事实；与路径一起进入启动请求。"""
        selection = self._cable_loss_selection
        return None if selection is None else selection.digest

    def _sync_load_tooltip(self) -> None:
        """把已选的线损来源写进“加载数据”的 tooltip：控制面不再常驻来源文字。"""
        button = getattr(self, "load_data_btn", None)
        if button is None:
            return
        target = LoadTarget(
            "cable_loss",
            "线损数据",
            selection_source_text(self._cable_loss_selection),
        )
        button.setToolTip(load_tooltip((target,)))

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
                    "gain": point.gain_db,
                }
                for point in value.raw_points
            )
        else:
            self._error_callback("驱动映射测量结果不是正式结果模型，无法显示")
            return
        invalid_rows = False
        self._suspend_stage_summary = True
        try:
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
                gains = list(existing.get("gain", ()))
                inputs.append(input_power)
                outputs.append(output_power)
                # 正式结果的增益已经由 DTO 校验为“输出功率 - 源功率”；旧的
                # 页面行数据没有 gain 字段时也在这里补算，保持历史结果显示兼容。
                try:
                    gain = float(row.get("gain", output_power - input_power))
                except (TypeError, ValueError):
                    invalid_rows = True
                    continue
                if not math.isfinite(gain):
                    invalid_rows = True
                    continue
                gains.append(gain)
                sweep["sweep_data"] = {
                    "input_power_sg": inputs,
                    "output_power_driver": outputs,
                    "gain": gains,
                }
                self.update_realtime(sweep)
        finally:
            self._suspend_stage_summary = False
            self._refresh_stage_summary()
        if invalid_rows:
            self._error_callback("驱动映射结果包含无效功率点，已跳过无效数据")

    def stage_summary(self) -> StageSummary:
        """从已显示的实时缓存里取摘要；不读结果 DTO，也不发起新的测量查询。"""
        frequencies = self.realtime_buffer.frequencies
        if not frequencies:
            return StageSummary.not_started()
        return StageSummary(frequency_headline(len(frequencies)))

    def _refresh_stage_summary(self) -> None:
        """把频点数摘要写进阶段条，并通知工作台刷新。

        同一份摘要重复写入时不发信号：批量载入已经暂停逐行刷新，普通刷新也只在该
        阶段摘要真的变化时才更新阶段条。
        """
        if self._suspend_stage_summary:
            return
        summary = self.stage_summary()
        if summary == self._last_stage_summary:
            return
        self._last_stage_summary = summary
        self.stage_summary_changed.emit()

    def _update_navigation(self) -> None:
        super()._update_navigation()
        self._refresh_stage_summary()

    def _update_plot(self, data: Mapping[str, Any]) -> None:
        update_plot = getattr(self.driver_plot_widget, "update_plot", None)
        if update_plot is not None:
            update_plot(dict(data))

    def _measurement_active(self) -> bool:
        controller = getattr(self, "controller", None)
        if controller is None:
            return False
        return state_is_active(getattr(controller, "state", None))

    def _accept_realtime_data(self) -> bool:
        """共享 controller 会广播所有阶段；驱动映射页只消费自己的数据。"""
        controller = getattr(self, "controller", None)
        kind = getattr(getattr(controller, "state", None), "kind", None)
        return kind is None or kind is MeasurementKind.DRIVER_MAPPING

    def _readout_metrics(
        self, data: Mapping[str, Any] | None = None
    ) -> tuple[ReadoutMetric, ...]:
        """驱动映射读数：输入/输出功率与两处点数，不推算增益或效率。"""
        if data is None:
            return (
                ReadoutMetric("输入功率", "—", "dBm"),
                ReadoutMetric("输出功率", "—", "dBm"),
                ReadoutMetric("扫频点", "—", "点"),
                ReadoutMetric("已测频点", "—", "个"),
            )
        sweep = data.get("sweep_data", {})
        if not isinstance(sweep, Mapping):
            sweep = {}
        inputs = sweep.get("input_power_sg", ())
        outputs = sweep.get("output_power_driver", ())
        points = max(
            len(inputs) if isinstance(inputs, (list, tuple)) else 0,
            len(outputs) if isinstance(outputs, (list, tuple)) else 0,
        )
        return (
            ReadoutMetric(
                "输入功率", format_latest_number(inputs, 1), "dBm"
            ),
            ReadoutMetric(
                "输出功率", format_latest_number(outputs, 1), "dBm"
            ),
            ReadoutMetric("扫频点", str(points) if points else "—", "点"),
            ReadoutMetric(
                "已测频点",
                str(len(self.realtime_buffer.frequencies)) or "—",
                "个",
            ),
        )

    def _update_readout(
        self, data: Mapping[str, Any], *, animate: bool = False
    ) -> None:
        readout = getattr(self, "readout", None)
        if readout is None:
            return
        frequency = str(data.get("frequency", "")).strip()
        readout.set_reading(
            frequency or "—",
            "GHz" if frequency else "",
            self._readout_metrics(data),
            active=self._measurement_active(),
            animate=animate,
        )

    def _reset_readout(self) -> None:
        readout = getattr(self, "readout", None)
        if readout is not None:
            readout.set_reading(
                "—",
                "",
                self._readout_metrics(),
                active=self._measurement_active(),
                animate=False,
            )

    def _on_state_changed(self, state: Any) -> None:
        self.controls.apply(state)
        readout = getattr(self, "readout", None)
        if readout is not None:
            readout.set_active(self._measurement_active())
        self._refresh_console()

    def on_activated(self) -> None:
        """回到本页时刷新运行事实：运行可能已重新连接，线损选择也可能已变化。"""
        super().on_activated()
        self._sync_load_tooltip()
        self._refresh_console()

    def _refresh_console(self) -> None:
        """把运行时间、线损选择与归档位置写进左栏控制台。"""
        console = getattr(self, "console", None)
        if console is None:
            return
        run_id = self._current_run_id()
        configured = self._archival_location_provider is not None
        directory = self._archival_directory(run_id)
        console.set_run_facts(
            run_id=run_id,
            archive=directory,
            archive_configured=configured,
        )
        console.set_checks(
            (
                run_session_check(run_id),
                input_check("线损结果", self._cable_loss_selection is not None),
                wiring_check(),
                archive_check(directory, configured=configured),
            )
        )

    def set_animations_enabled(self, enabled: bool) -> None:
        """外壳关闭动效时，读数条的数值淡入与呼吸点一起落到静态外观。"""
        self.readout.set_animations_enabled(enabled)

    def _on_error(self, message: str) -> None:
        if not self._archival_is_for_this_page():
            return
        self.controls.show_idle()
        if getattr(self, "readout", None) is not None:
            self.readout.set_active(False)
        self._archival_test_failed(message)
        self._error_callback(message)

    def _on_stopped(self, reason: str) -> None:
        if not self._archival_is_for_this_page():
            return
        self.controls.show_idle()
        if getattr(self, "readout", None) is not None:
            self.readout.set_active(False)
        self._archival_test_stopped(reason, self._partial_result_path())

    def _on_finished(self) -> None:
        if not self._archival_is_for_this_page():
            return
        self.controls.show_idle()
        if getattr(self, "readout", None) is not None:
            self.readout.set_active(False)
        self._archival_test_finished()

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "错误", message)


__all__ = ["DriverMappingPage"]
