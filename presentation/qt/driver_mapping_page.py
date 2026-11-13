"""独立的驱动映射测量页面。"""

from __future__ import annotations

from collections.abc import Mapping
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

from .measurement_start_facade import (
    MeasurementStartFacade,
    StartMeasurementRequest,
    is_busy,
)
from application.dto import DriverPowerMappingMeasurementResult
from application.dto.measurement_requests import StageResultDigest
from .archival_page import ArchivalPageMixin
from .measurement_controls import (
    MeasurementAction,
    MeasurementControlPresenter,
)
from .measurement_state import (
    DriverMappingPageResultState,
    MeasurementKind,
    StageResultSelection,
)
from .measurement_surface import (
    CONTROL_SURFACE,
    RESULT_SURFACE,
    build_surface,
    hairline,
    section_caption,
)
from .pages import PageControllerBindingMixin
from .realtime_buffer import RealtimeMeasurementBuffer
from .realtime_page import RealtimePageMixin
from .status_panel import StatusSlot
from .theme import (
    MEASUREMENT_LEFT_PANEL_MAX_WIDTH,
    MEASUREMENT_LEFT_PANEL_MIN_WIDTH,
    set_button_variant,
)


class DriverMappingPage(
    RealtimePageMixin, ArchivalPageMixin, PageControllerBindingMixin, QWidget
):
    """驱动映射输入、接线确认、进度和结构化结果展示。"""

    # 页面只发出“用户想加载线损结果”的意图；打开文件选择器和应用层校验都由窗口
    # 负责，页面不持有路径服务，也不读取 JSON。
    load_cable_loss_requested = Signal()

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
        # 用户在本次会话里显式选择的线损结果；页面切换不清除，只有重新选择才替换。
        self._cable_loss_selection: StageResultSelection | None = None
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

        # 左侧窄栏自上而下放控制面与状态面板（进度 + 实时日志）；右侧整块留给四张图。
        # 接线说明只保留连接图入口：测试者已知道仪器怎么接，按钮只用于防止忘记。
        # 宽度与另两个测量页共用 theme 里的同一组常量。
        left_panel = QWidget()
        left_panel.setMinimumWidth(MEASUREMENT_LEFT_PANEL_MIN_WIDTH)
        left_panel.setMaximumWidth(MEASUREMENT_LEFT_PANEL_MAX_WIDTH)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        control_surface = build_surface(CONTROL_SURFACE, title="测量控制")
        control_layout = control_surface.layout()
        self.driver_mapping_btn = QPushButton("开始驱动映射")
        set_button_variant(self.driver_mapping_btn, "primary")
        self.driver_mapping_btn.clicked.connect(self.start_measurement)
        self.driver_stop_btn = QPushButton("停止测量")
        set_button_variant(self.driver_stop_btn, "secondary")
        self.driver_stop_btn.clicked.connect(self.stop_measurement)
        self.show_driver_diagram_btn = QPushButton("查看连接图")
        set_button_variant(self.show_driver_diagram_btn, "secondary")
        self.show_driver_diagram_btn.clicked.connect(self.show_connection_diagram)
        self.load_cable_loss_btn = QPushButton("加载线损结果")
        set_button_variant(self.load_cable_loss_btn, "secondary")
        self.load_cable_loss_btn.clicked.connect(self.load_cable_loss_requested.emit)
        self.controls = MeasurementControlPresenter(
            MeasurementKind.DRIVER_MAPPING,
            {
                MeasurementAction.START: self.driver_mapping_btn,
                MeasurementAction.STOP: self.driver_stop_btn,
            },
        )
        self.controls.show_idle()
        # 开始是控制面唯一的主操作，独占整行；停止与连接图收成一行紧凑次操作。
        control_layout.addWidget(self.driver_mapping_btn)
        secondary_actions = QHBoxLayout()
        secondary_actions.setSpacing(8)
        for button in (self.driver_stop_btn, self.show_driver_diagram_btn):
            secondary_actions.addWidget(button, 1)
        control_layout.addLayout(secondary_actions)
        # 线损是驱动映射的前置输入：加载入口与来源文件、测量时间同属“校准输入”区块。
        # 未选择时这里给出明确空态，但“是否允许启动”的判断仍留在 start_measurement。
        control_layout.addWidget(hairline())
        control_layout.addWidget(section_caption("校准输入"))
        control_layout.addWidget(self.load_cable_loss_btn)
        self.cable_loss_source_label = QLabel()
        self.cable_loss_source_label.setObjectName("emptyHint")
        self.cable_loss_source_label.setWordWrap(True)
        control_layout.addWidget(self.cable_loss_source_label)
        left_layout.addWidget(control_surface)
        self._sync_cable_loss_label()
        self.archival_banner = self._build_archival_banner()
        left_layout.addWidget(self.archival_banner)
        # 进度与实时日志由窗口挂进这块容器，占满控制面以下的剩余高度。
        self.status_slot = StatusSlot()
        left_layout.addWidget(self.status_slot, 1)
        self.control_panel = left_panel
        layout.addWidget(left_panel)

        # 结果面只承载“测量结果”这一块内容：频点浏览与四张图由绘图控件自带。
        result_surface = build_surface(RESULT_SURFACE, title="测量结果")
        result_layout = result_surface.layout()
        self.driver_plot_widget = self._plot_widget_factory(result_surface)
        self._realtime_plot_widget = self.driver_plot_widget
        prev_clicked = getattr(self.driver_plot_widget, "prev_clicked", None)
        next_clicked = getattr(self.driver_plot_widget, "next_clicked", None)
        if prev_clicked is not None:
            prev_clicked.connect(self.previous_frequency)
        if next_clicked is not None:
            next_clicked.connect(self.next_frequency)
        result_layout.addWidget(self.driver_plot_widget, 1)
        layout.addWidget(result_surface, 1)
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
            lambda: StartMeasurementRequest(
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

    def show_loaded_cable_loss(
        self,
        *,
        path: str,
        filename: str,
        created_at: str,
        sha256: str,
        size_bytes: int,
    ) -> None:
        """记录用户显式选择的线损结果，并回显来源文件与测量时间。

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
        self._sync_cable_loss_label()
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

    def _sync_cable_loss_label(self) -> None:
        selection = self._cable_loss_selection
        label = getattr(self, "cable_loss_source_label", None)
        if label is None:
            return
        if selection is None:
            label.setText("未选择线损结果：开始前请先加载线损测量结果")
            return
        label.setText(
            f"线损来源：{selection.filename}\n测量时间：{selection.created_at}"
        )

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
