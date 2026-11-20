"""独立的主功放测量页面。"""

from __future__ import annotations

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

from application.dto.measurement_requests import StageResultDigest
from .archival_page import ArchivalPageMixin
from .load_data_dialog import (
    LoadTarget,
    choose_load_target,
    load_tooltip,
    selection_source_text,
)
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
from .measurement_start_facade import (
    MeasurementStartFacade,
    StartMeasurementRequest,
    is_busy,
)
from .measurement_state import MeasurementKind, StageResultSelection
from .measurement_surface import CONTROL_SURFACE, RESULT_SURFACE, build_surface
from .pages import PageControllerBindingMixin
from .realtime_buffer import RealtimeMeasurementBuffer
from .realtime_page import RealtimePageMixin, format_latest_number
from .stage_summary import (
    StageSummary,
    frequency_headline,
)
from .status_panel import StatusSlot
from .theme import (
    MEASUREMENT_LEFT_PANEL_WIDTH,
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

    #: 页面已显示的数据变化（新频点、清空）时发出；工作台据此刷新阶段摘要。
    stage_summary_changed = Signal()

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
            config_path_provider=config_path_provider,
        )
        self._driver_mode_provider = driver_mode_provider
        self._connection_dialog_factory = connection_dialog_factory
        self._plot_widget_factory = plot_widget_factory
        self._realtime_data_callback = realtime_data_callback or (lambda _data: None)
        self._clear_realtime_callback = clear_realtime_callback or (lambda: None)
        self._log_callback = log_callback or (lambda _message: None)
        self._error_callback = error_callback or self._show_error
        self._setup_archival(
            MeasurementKind.AMPLIFIER,
            location_provider=archival_location_provider,
            run_id_provider=archival_run_id_provider,
            run_provider=run_provider,
        )
        self.realtime_buffer = RealtimeMeasurementBuffer()
        # 上一份已展示的摘要：只有真正变化时才通知工作台刷新阶段条。
        self._last_stage_summary: StageSummary | None = None
        self.root = self.build_ui()

    def build_ui(self) -> QWidget:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(12)

        # 左侧窄栏自上而下放运行控制台、控制面与运行状态；右侧整块留给结果面。
        # 测量结果由频点浏览图按频点展示，页面不再重复列一份结果表。
        # 接线说明只保留连接图入口：测试者已知道仪器怎么接，按钮只用于防止忘记。
        # 宽度与另三个左栏页面共用 theme 里的同一个固定值。
        left_panel = QWidget()
        left_panel.setFixedWidth(MEASUREMENT_LEFT_PANEL_WIDTH)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        control_surface = build_surface(CONTROL_SURFACE, title="测量控制")
        control_layout = control_surface.layout()
        self.amplifier_test_btn = QPushButton("开始测试")
        set_button_variant(self.amplifier_test_btn, "primary")
        self.amplifier_test_btn.clicked.connect(self.start_measurement)
        self.stop_btn = QPushButton("停止测量")
        set_button_variant(self.stop_btn, "secondary")
        self.stop_btn.clicked.connect(self.stop_measurement)
        self.show_amp_diagram_btn = QPushButton("查看连接图")
        set_button_variant(self.show_amp_diagram_btn, "secondary")
        self.show_amp_diagram_btn.clicked.connect(self.show_connection_diagram)
        # 一个“加载数据”入口：主功放要用的线损与驱动映射都在点击后的对话框里选，
        # 左栏因此与另两个测量页保持同一种两行动作布局。
        self.load_data_btn = QPushButton("加载数据")
        set_button_variant(self.load_data_btn, "secondary")
        self.load_data_btn.clicked.connect(self._on_load_data_clicked)
        self.controls = MeasurementControlPresenter(
            MeasurementKind.AMPLIFIER,
            {
                MeasurementAction.START: self.amplifier_test_btn,
                MeasurementAction.STOP: self.stop_btn,
            },
        )
        self.controls.show_idle()
        # 第一行是运行控制（开始仍是唯一的主操作），第二行是加载数据与连接图：
        # 加载数据在左，查看连接图在右，两个按钮等宽。
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        action_row.addWidget(self.amplifier_test_btn, 1)
        action_row.addWidget(self.stop_btn, 1)
        control_layout.addLayout(action_row)
        load_row = QHBoxLayout()
        load_row.setContentsMargins(0, 0, 0, 0)
        load_row.setSpacing(8)
        load_row.addWidget(self.load_data_btn, 1)
        load_row.addWidget(self.show_amp_diagram_btn, 1)
        control_layout.addLayout(load_row)
        # 运行控制台放在左栏顶部：运行时间、两类校准输入、接线与归档位置。
        self.console = MeasurementRunConsole()
        left_layout.addWidget(self.console)
        left_layout.addWidget(control_surface)
        self._sync_driver_input_availability()
        self._refresh_console()
        self.archival_banner = self._build_archival_banner()
        left_layout.addWidget(self.archival_banner)
        # 运行状态由窗口挂进这块容器，固定一行。
        self.status_slot = StatusSlot()
        left_layout.addWidget(self.status_slot, 1)
        self.control_panel = left_panel
        layout.addWidget(left_panel)

        # 结果面统一包住绘图控件：读数条在上，频点浏览仍由绘图控件自带。
        result_surface = build_surface(RESULT_SURFACE, title="测量结果")
        result_layout = result_surface.layout()
        self.readout = MeasurementReadout(result_surface)
        result_layout.addWidget(self.readout)
        self.amplifier_plot_widget = self._plot_widget_factory(result_surface)
        self._realtime_plot_widget = self.amplifier_plot_widget
        for signal_name, slot in (("prev_clicked", self.previous_frequency), ("next_clicked", self.next_frequency)):
            signal = getattr(self.amplifier_plot_widget, signal_name, None)
            if signal is not None:
                signal.connect(slot)
        # 胶片条点击切换到已测频点；页面只改浏览位置，不触发测量命令。
        frequency_selected = getattr(
            self.amplifier_plot_widget, "frequency_selected", None
        )
        if frequency_selected is not None:
            frequency_selected.connect(self.select_frequency)
        result_layout.addWidget(self.amplifier_plot_widget, 1)
        layout.addWidget(result_surface, 1)
        self.readout.set_reading("—", "", self._readout_metrics(), active=False)
        self._refresh_stage_summary()
        return self

    def _connect_controller_signals(self, controller: Any) -> None:
        signals = controller.signals
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
            lambda: StartMeasurementRequest.from_session(
                MeasurementKind.AMPLIFIER,
                self._run_provider(),
                cable_loss_path=self._selected_cable_loss_path(),
                driver_mapping_path=self._selected_driver_mapping_path(),
                cable_loss_digest=self._selected_cable_loss_digest(),
                driver_mapping_digest=self._selected_driver_mapping_digest(),
            ),
            controller=self.controller,
            confirm_connection=self._confirm_start_connection,
        )
        if not decision.ready:
            decision.report(self._log_callback, self._error_callback)
            return
        self._log_callback("主功放现场接线已确认")
        self.reset_realtime_data()
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
        """配置可能在别的页面被修改：回到本页时重新同步加载数据入口。"""
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

    def _load_targets(self) -> tuple[LoadTarget, ...]:
        """当前驱动模式下可加载的校准数据。

        无驱放模式只有线损一项；这时“加载数据”与另两个单来源页面一样直接打开
        文件选择器，不弹只剩一个选项的对话框。驱动映射的旧选择保留，但不作为
        本模式的加载目标。
        """
        targets = [
            LoadTarget(
                "cable_loss",
                "线损数据",
                selection_source_text(self._cable_loss_selection),
            )
        ]
        if self._driver_mode_provider():
            targets.append(
                LoadTarget(
                    "driver_mapping",
                    "驱放数据",
                    selection_source_text(self._driver_mapping_selection),
                )
            )
        return tuple(targets)

    def _on_load_data_clicked(self) -> None:
        """唯一的加载入口：两类数据选择其一，只有一项时直接进入文件选择。"""
        targets = self._load_targets()
        if not targets:
            return
        key = (
            targets[0].key
            if len(targets) == 1
            else choose_load_target(self, targets)
        )
        if key is not None:
            self._request_stage_result(key)

    def _request_stage_result(self, key: str) -> None:
        """把“想加载哪一类”的意图交给窗口；页面不打开文件选择器。"""
        if key == "cable_loss":
            self.load_cable_loss_requested.emit()
        elif key == "driver_mapping":
            self.load_driver_mapping_requested.emit()
        else:
            raise ValueError(f"未知的加载数据类型: {key!r}")

    def _sync_load_tooltip(self) -> None:
        """把已选的两份来源写进“加载数据”的 tooltip：常驻文字不进左栏。"""
        button = getattr(self, "load_data_btn", None)
        if button is not None:
            button.setToolTip(load_tooltip(self._load_targets()))

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
            f"已选择主功放线损测量结果：{filename}"
            f"（测量时间：{created_at}）"
        )

    def show_loaded_driver_mapping(
        self,
        *,
        path: str,
        filename: str,
        created_at: str,
        sha256: str,
        size_bytes: int,
    ) -> None:
        """记录用户显式选择的驱动映射结果；与线损选择互不覆盖。"""
        self._driver_mapping_selection = StageResultSelection(
            path=path,
            filename=filename,
            created_at=created_at,
            digest=StageResultDigest(sha256=sha256, size_bytes=size_bytes),
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

    def _selected_cable_loss_digest(self) -> StageResultDigest | None:
        selection = self._cable_loss_selection
        return None if selection is None else selection.digest

    def _selected_driver_mapping_digest(self) -> StageResultDigest | None:
        """与 ``_selected_driver_mapping_path`` 保持同一可见性规则。"""
        if not self._driver_mode_provider():
            return None
        selection = self._driver_mapping_selection
        return None if selection is None else selection.digest

    def _sync_driver_input_availability(self) -> None:
        """驱动关闭时“加载数据”里不再出现驱放一项，已选映射保留但不参与检查。"""
        self._sync_load_tooltip()
        self._refresh_console()

    def _refresh_console(self) -> None:
        """把运行时间、校准输入与归档位置写进左栏控制台。"""
        console = getattr(self, "console", None)
        if console is None:
            return
        driver_mode = bool(self._driver_mode_provider())
        run_id = self._current_run_id()
        configured = self._archival_location_provider is not None
        directory = self._archival_directory(run_id)
        console.set_run_facts(
            run_id=run_id,
            archive=directory,
            archive_configured=configured,
        )
        checks = [
            run_session_check(run_id),
            input_check("线损结果", self._cable_loss_selection is not None),
        ]
        if driver_mode:
            checks.append(
                input_check(
                    "驱动映射结果", self._driver_mapping_selection is not None
                )
            )
        else:
            checks.append(
                ConsoleCheck("驱动映射结果", CheckState.OPTIONAL, "无驱放模式不使用")
            )
        checks.append(wiring_check())
        checks.append(archive_check(directory, configured=configured))
        console.set_checks(tuple(checks))

    def stage_summary(self) -> StageSummary:
        """从已显示的实时缓存里取摘要；不读结果 DTO，也不发起新的测量查询。"""
        frequencies = self.realtime_buffer.frequencies
        if not frequencies:
            return StageSummary.not_started()
        return StageSummary(frequency_headline(len(frequencies)))

    def _refresh_stage_summary(self) -> None:
        """把频点数摘要写进阶段条，并通知工作台刷新。

        同一份摘要重复写入时不发信号：实时测量按频点刷新时，阶段条只在频点数真的
        变化时才需要更新。
        """
        summary = self.stage_summary()
        if summary == self._last_stage_summary:
            return
        self._last_stage_summary = summary
        self.stage_summary_changed.emit()

    def _update_navigation(self) -> None:
        super()._update_navigation()
        self._refresh_stage_summary()

    def _update_plot(self, data: Mapping[str, Any]) -> None:
        update_plot = getattr(self.amplifier_plot_widget, "update_plot", None)
        if update_plot is not None:
            update_plot(dict(data))

    def _measurement_active(self) -> bool:
        controller = getattr(self, "controller", None)
        if controller is None:
            return False
        return state_is_active(getattr(controller, "state", None))

    def _accept_realtime_data(self) -> bool:
        """共享 controller 会广播所有阶段；主功放页只消费自己的数据。"""
        controller = getattr(self, "controller", None)
        kind = getattr(getattr(controller, "state", None), "kind", None)
        return kind is None or kind is MeasurementKind.AMPLIFIER

    def _readout_metrics(
        self, data: Mapping[str, Any] | None = None
    ) -> tuple[ReadoutMetric, ...]:
        """主功放读数：输出功率、增益、效率与 DC 功耗，全部取扫频的最后一点。"""
        if data is None:
            return (
                ReadoutMetric("输出功率", "—", "dBm"),
                ReadoutMetric("增益", "—", "dB"),
                ReadoutMetric("效率", "—", "%"),
                ReadoutMetric("DC功耗", "—", "W"),
            )
        sweep = data.get("sweep_data", {})
        if not isinstance(sweep, Mapping):
            sweep = {}
        output = sweep.get("output_power_dut", sweep.get("output_power_driver"))
        return (
            ReadoutMetric("输出功率", format_latest_number(output, 1), "dBm"),
            ReadoutMetric("增益", format_latest_number(sweep.get("gain"), 1), "dB"),
            ReadoutMetric(
                "效率", format_latest_number(sweep.get("efficiency"), 1), "%"
            ),
            ReadoutMetric(
                "DC功耗", format_latest_number(sweep.get("dc_power"), 2), "W"
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
        # 测试确实完成了，但没有收到归档结果时如实提示状态未知。
        self._archival_test_finished()

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "错误", message)


__all__ = ["AmplifierPage"]
