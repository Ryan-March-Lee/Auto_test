"""独立的主功放测量页面。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .archival_page import ArchivalPageMixin
from .measurement_controls import (
    MeasurementAction,
    MeasurementControlPresenter,
)
from .measurement_start_facade import (
    MeasurementStartFacade,
    StartMeasurementRequest,
    is_busy,
)
from .measurement_state import MeasurementKind, StageResultSelection
from .pages import PageControllerBindingMixin
from .realtime_buffer import RealtimeMeasurementBuffer
from .realtime_page import RealtimePageMixin
from .status_panel import StatusSlot
from .theme import (
    MEASUREMENT_LEFT_PANEL_MAX_WIDTH,
    MEASUREMENT_LEFT_PANEL_MIN_WIDTH,
    set_button_variant,
)


class AmplifierPage(
    RealtimePageMixin, ArchivalPageMixin, PageControllerBindingMixin, QWidget
):
    """主功放测量的输入、接线确认、进度和实时曲线展示。"""

    # 页面只发出“用户想加载某类校准输入”的意图；打开文件选择器和应用层校验都由
    # 窗口负责，页面不持有路径服务，也不读取 JSON。
    load_cable_loss_requested = Signal()
    load_driver_mapping_requested = Signal()

    def __init__(
        self,
        *,
        config_path_provider: Callable[[], str],
        run_provider: Callable[[], Any],
        driver_mode_provider: Callable[[], bool],
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
        # 用户在本次会话里显式选择的校准输入；页面切换不清除，只有重新选择才替换。
        # 两个来源互不关联：可以来自不同日期、不同运行，兼容性由应用层校验。
        self._cable_loss_selection: StageResultSelection | None = None
        self._driver_mapping_selection: StageResultSelection | None = None
        self._start_facade = MeasurementStartFacade(
            config_path_provider=config_path_provider
        )
        self._driver_mode_provider = driver_mode_provider
        self._connection_dialog_factory = connection_dialog_factory
        self._plot_widget_factory = plot_widget_factory
        self._realtime_data_callback = realtime_data_callback or (lambda _data: None)
        self._clear_realtime_callback = clear_realtime_callback or (lambda: None)
        self._log_callback = log_callback or (lambda _message: None)
        self._progress_callback = progress_callback or (lambda _value: None)
        self._error_callback = error_callback or self._show_error
        self._setup_archival(
            MeasurementKind.AMPLIFIER,
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

        # 左侧窄栏自上而下放测量控制与状态面板（进度 + 实时日志）；右侧整块留给四张图。
        # 测量结果由频点浏览图按频点展示，页面不再重复列一份结果表。
        # 接线说明只保留连接图入口：测试者已知道仪器怎么接，按钮只用于防止忘记。
        # 宽度与另两个测量页共用 theme 里的同一组常量。
        left_panel = QWidget()
        left_panel.setMinimumWidth(MEASUREMENT_LEFT_PANEL_MIN_WIDTH)
        left_panel.setMaximumWidth(MEASUREMENT_LEFT_PANEL_MAX_WIDTH)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        control_group = QGroupBox("测量控制")
        control_layout = QGridLayout(control_group)
        control_layout.setHorizontalSpacing(8)
        control_layout.setVerticalSpacing(8)
        self.amplifier_test_btn = QPushButton("开始功放测试")
        set_button_variant(self.amplifier_test_btn, "primary")
        self.amplifier_test_btn.clicked.connect(self.start_measurement)
        self.stop_btn = QPushButton("停止测量")
        set_button_variant(self.stop_btn, "secondary")
        self.stop_btn.clicked.connect(self.stop_measurement)
        self.show_amp_diagram_btn = QPushButton("查看连接图")
        set_button_variant(self.show_amp_diagram_btn, "secondary")
        self.show_amp_diagram_btn.clicked.connect(self.show_connection_diagram)
        self.load_cable_loss_btn = QPushButton("加载线损结果")
        set_button_variant(self.load_cable_loss_btn, "secondary")
        self.load_cable_loss_btn.clicked.connect(self.load_cable_loss_requested.emit)
        self.load_driver_mapping_btn = QPushButton("加载驱动映射结果")
        set_button_variant(self.load_driver_mapping_btn, "secondary")
        self.load_driver_mapping_btn.clicked.connect(
            self.load_driver_mapping_requested.emit
        )
        self.controls = MeasurementControlPresenter(
            MeasurementKind.AMPLIFIER,
            {
                MeasurementAction.START: self.amplifier_test_btn,
                MeasurementAction.STOP: self.stop_btn,
            },
        )
        self.controls.show_idle()
        # 测量控制按钮横向排列在同一行，连接图入口单独占下一行。
        for column, button in enumerate(
            (
                self.amplifier_test_btn,
                self.stop_btn,
            )
        ):
            control_layout.addWidget(button, 0, column)
        control_layout.addWidget(self.show_amp_diagram_btn, 1, 0, 1, 2)
        control_layout.addWidget(self.load_cable_loss_btn, 2, 0, 1, 2)
        control_layout.addWidget(self.load_driver_mapping_btn, 3, 0, 1, 2)
        left_layout.addWidget(control_group)
        # 两类校准输入是主功放测量的前置条件：这里持续回显来源文件与测量时间。
        # 驱动关闭时不需要驱动映射，入口与来源行随驱动模式一起隐藏。
        self.cable_loss_source_label = QLabel()
        self.cable_loss_source_label.setObjectName("emptyHint")
        self.cable_loss_source_label.setWordWrap(True)
        left_layout.addWidget(self.cable_loss_source_label)
        self.driver_mapping_source_label = QLabel()
        self.driver_mapping_source_label.setObjectName("emptyHint")
        self.driver_mapping_source_label.setWordWrap(True)
        left_layout.addWidget(self.driver_mapping_source_label)
        self._sync_cable_loss_label()
        self._sync_driver_mapping_label()
        self._sync_driver_input_availability()
        self.archival_banner = self._build_archival_banner()
        left_layout.addWidget(self.archival_banner)
        # 进度与实时日志由窗口挂进这块容器，占满控制框以下的剩余高度。
        self.status_slot = StatusSlot()
        left_layout.addWidget(self.status_slot, 1)
        self.control_panel = left_panel
        layout.addWidget(left_panel)

        self.amplifier_plot_widget = self._plot_widget_factory(self)
        self._realtime_plot_widget = self.amplifier_plot_widget
        for signal_name, slot in (("prev_clicked", self.previous_frequency), ("next_clicked", self.next_frequency)):
            signal = getattr(self.amplifier_plot_widget, signal_name, None)
            if signal is not None:
                signal.connect(slot)
        layout.addWidget(self.amplifier_plot_widget, 1)
        return self

    def _connect_controller_signals(self, controller: Any) -> None:
        signals = controller.signals
        self._connect_signal(signals.progress, self._progress_callback)
        self._connect_archival_signals(controller)
        self._connect_signal(signals.data_update, self.update_realtime)
        self._connect_signal(signals.state_changed, self._on_state_changed)
        self._connect_signal(signals.error, self._on_error)
        self._connect_signal(signals.stopped, self._on_stopped)
        self._connect_signal(signals.finished, self._on_finished)

    def start_measurement(self) -> None:
        # 校准输入是主功放测量的前置条件：驱动关闭只要求线损，驱动开启要求线损
        # 和驱动映射。这里在每次点击时按当前驱动模式重新判定，配置在页面外被修改
        # 后不会沿用上一次的结论；未选择时直接拦下，不打开接线确认、不提交
        # controller，因此不会启动仪器连接。页面只判断“是否选过”，文件内容与
        # 兼容性仍由应用层在测量开始前校验。
        #
        # 其它页面测量运行时先让 facade 统一报告“已有测量正在运行”，不要用
        # 输入缺失掩盖更直接的原因；忙碌判据来自 facade 的 is_busy。
        if self.controller is not None and not is_busy(self.controller):
            self._sync_driver_input_availability()
            missing = self._missing_input_message()
            if missing is not None:
                self._error_callback(missing)
                self._log_callback("未选择主功放必需的校准输入，主功放测试未启动")
                return
        # controller 只在 prepare 时读取一次：决策携带检查过的那个对象，接线对话框
        # 的嵌套事件循环返回后不会重新读取 self.controller。
        decision = self._start_facade.prepare(
            lambda: StartMeasurementRequest(
                MeasurementKind.AMPLIFIER,
                self._run_provider(),
                cable_loss_path=self._selected_cable_loss_path(),
                driver_mapping_path=self._selected_driver_mapping_path(),
            ),
            controller=self.controller,
            confirm_connection=self._confirm_start_connection,
        )
        if not decision.ready:
            decision.report(self._log_callback, self._error_callback)
            return
        self._log_callback("主功放现场接线已确认")
        self.reset_realtime_data()
        self._log_callback("开始主功放测试...")
        # “等待归档”只在 controller 接受启动之后出现。
        self._archival_try_start(
            lambda: decision.launch(),
            "主功放测试未能启动，未开始测量",
        )

    def _missing_input_message(self) -> str | None:
        """按当前驱动模式返回缺失的必需输入；齐备时返回 ``None``。"""
        missing = []
        if self._cable_loss_selection is None:
            missing.append("线损测量结果")
        if self._driver_mode_provider() and self._driver_mapping_selection is None:
            missing.append("驱动映射测量结果")
        if not missing:
            return None
        return "请先加载主功放使用的" + "和".join(missing)

    def on_activated(self) -> None:
        """配置可能在别的页面被修改：回到本页时重新同步驱动映射入口。"""
        super().on_activated()
        self._sync_driver_input_availability()

    def _confirm_start_connection(self) -> bool:
        """按驱动模式选择连接图，接受才算完成接线确认。"""
        diagram = (
            "amplifier_test"
            if self._driver_mode_provider()
            else "amplifier_test_no_driver"
        )
        return self._connection_dialog_factory(diagram, self).exec() == QDialog.Accepted

    def stop_measurement(self) -> None:
        if self.controller is not None:
            self.controller.stop()

    def show_connection_diagram(self) -> None:
        diagram = "amplifier_test" if self._driver_mode_provider() else "amplifier_test_no_driver"
        self._connection_dialog_factory(diagram, self).exec()

    def show_loaded_cable_loss(
        self, *, path: str, filename: str, created_at: str
    ) -> None:
        """记录用户显式选择的线损结果，并回显来源文件与测量时间。

        文件内容、阶段目录边界与结果类型已由应用层校验；页面只保存窗口交回的事实，
        不读取文件，也不把这里的显示当成测量输入的第二次校验。日志只说“已选择”，
        不暗示正在运行（或下一次）测量已经消费了这份文件。
        """
        self._cable_loss_selection = StageResultSelection(
            path=path, filename=filename, created_at=created_at
        )
        self._sync_cable_loss_label()
        self._log_callback(
            f"已选择主功放线损测量结果：{filename}"
            f"（测量时间：{created_at}）"
        )

    def show_loaded_driver_mapping(
        self, *, path: str, filename: str, created_at: str
    ) -> None:
        """记录用户显式选择的驱动映射结果；与线损选择互不覆盖。"""
        self._driver_mapping_selection = StageResultSelection(
            path=path, filename=filename, created_at=created_at
        )
        self._sync_driver_input_availability()
        self._log_callback(
            f"已选择主功放驱动映射测量结果：{filename}"
            f"（测量时间：{created_at}）"
        )

    @property
    def cable_loss_selection(self) -> StageResultSelection | None:
        """当前显式选择的线损结果；未选择时为 ``None``。"""
        return self._cable_loss_selection

    @property
    def driver_mapping_selection(self) -> StageResultSelection | None:
        """当前显式选择的驱动映射结果；未选择时为 ``None``。"""
        return self._driver_mapping_selection

    def _selected_cable_loss_path(self) -> str | None:
        selection = self._cable_loss_selection
        return None if selection is None else selection.path

    def _selected_driver_mapping_path(self) -> str | None:
        """只有驱动模式启用时才把驱动映射选择当成本次输入。

        驱动关闭时页面上可能还留着旧选择；该模式下映射不参与测量，也不参与硬件前
        校验，因此启动意图里保持 ``None``，避免把未使用的文件伪装成本次输入。
        """
        if not self._driver_mode_provider():
            return None
        selection = self._driver_mapping_selection
        return None if selection is None else selection.path

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

    def _sync_driver_mapping_label(self) -> None:
        selection = self._driver_mapping_selection
        label = getattr(self, "driver_mapping_source_label", None)
        if label is None:
            return
        if selection is None:
            label.setText("未选择驱动映射结果：开始前请先加载驱动映射测量结果")
            return
        label.setText(
            f"驱动映射来源：{selection.filename}\n测量时间：{selection.created_at}"
        )

    def _sync_driver_input_availability(self) -> None:
        """驱动关闭时隐藏驱动映射入口：该模式下它既不需要，也不会被检查。"""
        required = bool(self._driver_mode_provider())
        button = getattr(self, "load_driver_mapping_btn", None)
        label = getattr(self, "driver_mapping_source_label", None)
        if button is not None:
            button.setVisible(required)
        if label is not None:
            label.setVisible(required)
            if required:
                self._sync_driver_mapping_label()
            else:
                label.setText("无驱放测试不需要驱动映射结果")

    def _update_plot(self, data: Mapping[str, Any]) -> None:
        update_plot = getattr(self.amplifier_plot_widget, "update_plot", None)
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
        self._progress_callback(0)
        self._archival_test_stopped(reason, self._partial_result_path())
        self._log_callback(f"测量已停止: {reason}")

    def _on_finished(self) -> None:
        if not self._archival_is_for_this_page():
            return
        self.controls.show_idle()
        self._progress_callback(100)
        # 测试确实完成了，但没有收到归档结果时如实提示状态未知。
        self._archival_test_finished()
        self._log_callback("测量完成！")

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "错误", message)


__all__ = ["AmplifierPage"]
