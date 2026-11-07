"""主窗口：Qt 窗口组装、页面注册和信号连接。

配置读写、运行创建/加载、仪器连接和端口生命周期由
:class:`presentation.qt.application_coordinator.ApplicationCoordinator` 持有；
窗口只负责把用户意图转发给协调器、连接 Qt 信号并把结果展示给用户。
"""

import logging
import sys
from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from infrastructure.logging.app_logging import get_logger, setup_logging
from presentation.qt.application_coordinator import ApplicationCoordinator, RunFlowState
from presentation.qt.composition import build_device_scanner_factory, default_paths
from presentation.qt.configuration_editor import ConfigurationEditor
from presentation.qt.configuration_page import ConfigurationPage
from presentation.qt.configuration_session import ConfigurationSession
from presentation.qt.connection_dialog import ConnectionDialog
from presentation.qt.instrument_setup_page import InstrumentSetupPage
from presentation.qt.power_configuration_page import PowerConfigurationPage
from presentation.qt.rack_overview_page import RackOverviewPage
from presentation.qt.run_history_page import build_run_history_page
from presentation.qt.history_view_model import HistoryViewModel
from presentation.qt.measurement_state import MeasurementKind, MeasurementResultReference
from presentation.qt.pages import PageContext, build_pages
from presentation.qt.realtime_plot import RealTimePlotWidget
from presentation.qt.status_panel import StatusPanel
from presentation.qt.theme import apply_theme, build_stylesheet
from presentation.qt.visualization_page import VisualizationPage
from presentation.qt.welcome_page import WelcomePage, apply_test_mode, confirm_leave_configuration
from presentation.qt.window_geometry import fit_window_to_screen

logger = get_logger(__name__)

# 全局样式表来自统一主题层：颜色、字号与控件外观只在 presentation.qt.theme 定义一次。
APPLICATION_STYLE = build_stylesheet()


def apply_light_theme(app: QApplication) -> None:
    """固定界面浅色配色，不受系统深色模式影响。

    Windows 处于深色模式时，Qt 6 会给出深色调色板；而样式表只覆盖了部分控件，
    未被覆盖的容器（滚动区内容、分组框边距、QFrame 边框、布局间隙）会露出深色
    底，界面上表现为大量“黑框”。这里在安装样式表之前强制浅色配色、浅色调色板与
    基准字体，保证未单独设置样式的区域也是浅底深字。
    """
    apply_theme(app)


class MainWindow(QMainWindow):
    """主窗口：只组装 Qt 界面、注册页面并连接信号。"""

    def __init__(self, config_path=None, *, paths: Any | None = None):
        super().__init__()
        self.paths = paths or default_paths()
        self.setWindowTitle("PA自动测试系统 v1.0")
        # 初始几何按屏幕可用区域计算（居中、避开任务栏），不再写死 100,100,1600,1000
        # 而落到屏幕右下角、被边缘和任务栏切掉一部分；包体尺寸见 window_geometry。
        fit_window_to_screen(self)
        self.coordinator = ApplicationCoordinator(
            log=self.add_log_message,
            warn=lambda title, message: QMessageBox.warning(self, title, message),
            error=self.on_measurement_error,
            paths=self.paths,
            config_path=config_path,
        )
        self.measurement_controller.signals.thread_finished.connect(
            self._on_measurement_controller_thread_finished
        )
        self.measurement_controller.signals.finished.connect(self.refresh_history)
        self.measurement_controller.signals.message.connect(self.add_log_message)
        self.measurement_controller.signals.rejected.connect(self.add_log_message)
        self.coordinator.load_configuration()
        self.init_ui()
        self.log_timer = QTimer()
        self.log_timer.timeout.connect(self.update_status)
        self.log_timer.start(100)  # 100ms更新一次

    # ---- 协调器持有的状态（只读转发，保证窗口与协调器只有一个数据源） ----

    @property
    def measurement_controller(self):
        return self.coordinator.controller

    @property
    def gui_data_service(self):
        return self.coordinator.gui_data_service

    @property
    def config(self):
        return self.coordinator.config

    @property
    def prepared_run(self):
        return self.coordinator.prepared_run

    @property
    def loaded_data(self):
        return self.visualization_page.loaded_data

    @property
    def frequency_list(self):
        return self.visualization_page.frequency_list

    @property
    def current_freq_index(self):
        return self.visualization_page.current_freq_index

    @property
    def real_time_data(self):
        return self.coordinator.realtime_buffer.data

    @property
    def rt_frequency_list(self):
        return self.coordinator.realtime_buffer.frequencies

    @property
    def rt_current_freq_index(self):
        return self.coordinator.realtime_buffer.current_index

    def init_ui(self):
        """初始化用户界面：欢迎页/工作区堆栈 + 右侧纵向状态面板（标签栏承载模式徽标）。"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        self.view_stack = QStackedWidget()
        self.welcome_page = WelcomePage()
        self.welcome_page.mode_selected.connect(self._enter_test_mode)
        self.view_stack.addWidget(self.welcome_page)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(16, 12, 12, 12)
        left_layout.setSpacing(8)
        self.tab_widget = QTabWidget()
        self.tab_widget.setObjectName("mainTabs")
        self.tab_widget.setDocumentMode(True)
        left_layout.addWidget(self.tab_widget)
        self._register_pages()
        # 电源配置是整屏步骤：在标签页之外创建，进入工作区前单独切页；参数页与它由
        # 同一个编辑器装配成共享草稿。
        self.power_page = self.create_power_page()
        # 页面一建好就对齐只读态：绕过欢迎页也不能先呈现为可编辑。
        self.configuration_editor.set_read_only(
            self.coordinator.editing_read_only_reason()
        )
        # 测试模式徽标并入标签栏右侧：系统名与版本号已由窗口标题栏显示，不再重复一行。
        self.tab_widget.setCornerWidget(
            self._build_mode_badge(), Qt.Corner.TopRightCorner
        )
        # 分段选择器样式的标签栏不要底边基线：Qt 会在标签栏矩形上描一圈 1px 浅灰框，
        # 标签栏铺满整行时看不出来，宽度变化后就会露出一个空的长灰框。
        self.tab_widget.tabBar().setDrawBase(False)

        self.workspace_page = QWidget()
        workspace_layout = QVBoxLayout(self.workspace_page)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        # 左右分栏：左侧标签页占满剩余宽度，右侧状态面板纵向铺满并保持自身宽度。
        # 分隔条让用户可以按需拉宽日志栏；子控件不可折叠，避免拖出隐藏区域。
        self.workspace_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_splitter.setObjectName("workspaceSplitter")
        self.workspace_splitter.setHandleWidth(6)
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.addWidget(left_widget)
        self.create_status_panel(self.workspace_splitter)
        self.workspace_splitter.setStretchFactor(0, 1)
        self.workspace_splitter.setStretchFactor(1, 0)
        self.workspace_splitter.setSizes([1180, 420])
        # 面板分栏宽度：默认 1180/420，用户拖动后由下一次隐藏更新。窗口还没布局时
        # ``sizes()`` 只是按当时可用宽度缩放的结果，不能拿它当恢复值。
        self._status_panel_sizes = [1180, 420]
        workspace_layout.addWidget(self.workspace_splitter, 1)
        self.view_stack.addWidget(self.workspace_page)

        # 仪表配置是欢迎页之后的整屏页面，不登记进标签栏：先选设备，再配测试参数，
        # 最后由参数配置页的“完成配置并连接仪器”提交最终动作。仪表配置页只依赖配置
        # 会话的窄接口，不持有配置页实例；面板回写由窗口装配给会话。
        self.configuration_session = ConfigurationSession(self.config_page)
        self.connection_page = InstrumentSetupPage(
            self.configuration_session,
            scanner_factory=build_device_scanner_factory(self.paths),
        )
        self.connection_page.next_requested.connect(self._open_rack_overview_page)
        self.connection_page.return_welcome_requested.connect(self._return_to_welcome)
        self.connection_page.log_message.connect(self.add_log_message)
        self.config_page.discovery_message.connect(self.connection_page.log_message.emit)
        self.configuration_session.attach_discovery_panel(
            self.connection_page.discovery_panel
        )
        self.view_stack.insertWidget(1, self.connection_page)
        self.rack_page = RackOverviewPage(self)
        self.rack_page.next_requested.connect(self._open_configuration_page)
        self.rack_page.back_requested.connect(
            lambda: self._return_to_connection_page(keep_results=True)
        )
        self.view_stack.insertWidget(2, self.rack_page)
        self.power_page.back_requested.connect(self._return_to_rack_page)
        self.power_page.next_requested.connect(self._open_workspace_configuration)
        self.view_stack.insertWidget(3, self.power_page)

        central_layout = QVBoxLayout(central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.view_stack)

        self.view_stack.setCurrentWidget(self.welcome_page)
        # 初始标签页就是“仪器配置”：进入工作区后右侧不应出现进度与日志。
        self._sync_status_panel_visibility()

    def _build_mode_badge(self):
        """当前测试模式徽标（只读展示），显示在标签栏最右侧。"""
        self.mode_badge = QLabel("未选择测试模式")
        self.mode_badge.setObjectName("appModeBadge")
        return self.mode_badge

    def _enter_test_mode(self, driver_enabled: bool) -> None:
        config = apply_test_mode(dict(self.coordinator.load_configuration()), driver_enabled)
        self.coordinator.config = config
        self.configuration_editor.apply_config(config)
        self.configuration_editor.set_test_mode(driver_enabled)
        read_only_reason = self.coordinator.editing_read_only_reason()
        self.configuration_editor.set_read_only(read_only_reason)
        if read_only_reason:
            self.add_log_message(f"配置只读: {read_only_reason}")
        self._apply_driver_mode_to_pages(driver_enabled)
        self.mode_badge.setText("有驱放测试" if driver_enabled else "无驱放测试")
        self.tab_widget.setCurrentIndex(0)
        self.coordinator.begin_device_selection()
        self.connection_page.enter()
        self.view_stack.setCurrentWidget(self.connection_page)
        self._sync_status_panel_visibility()

    def _open_rack_overview_page(self) -> None:
        """仪表配置完成：先在“仪器排布”确认电源架位，再进入参数页。"""
        cards = [card for card in self.connection_page.discovery_panel.cards()
                 if card.is_checked()]
        self.view_stack.setCurrentWidget(self.rack_page)
        entries = RackOverviewPage.entries_from_cards(cards)
        self.rack_page.enter(
            entries, driver_enabled=self.coordinator.amplifier_driver_mode()
        )

    def _open_configuration_page(self) -> None:
        """排布完成：进整屏电源配置页，把排布页的电源卡片展开成通道卡片。"""
        self.coordinator.begin_configuration()
        # 新测试会话的第一次进入从电源初始值开始；本次会话内返回再进入保留已填参数。
        self.power_page.enter()
        self.power_page.set_rack_slots(self.rack_page.slot_assignments())
        self.view_stack.setCurrentWidget(self.power_page)
        self._sync_status_panel_visibility()
        self._sync_finalize_entry()
        self.power_page.play_card_morph(self.rack_page.power_tile_rects_global())

    def _open_workspace_configuration(self) -> None:
        """电源配置完成：回工作区的“仪器配置”标签页，继续参数与连接。

        电源配置本身是整屏步骤，不占标签；工作区的标签栏与右侧状态面板到这里才
        重新出现。
        """
        self.tab_widget.setCurrentIndex(self.tab_widget.indexOf(self.config_page))
        self.view_stack.setCurrentWidget(self.workspace_page)
        self._sync_status_panel_visibility()
        self._sync_finalize_entry()

    def _return_to_rack_page(self) -> None:
        """电源配置页“上一步”：回仪器排布页，保留已摆放的格位与卡片。"""
        self.power_page.finish_card_morph()
        self.rack_page.restore_after_next()
        self.view_stack.setCurrentWidget(self.rack_page)
        self._sync_status_panel_visibility()
        self._sync_finalize_entry()

    def _return_to_connection_page(self, *, keep_results: bool = False) -> None:
        """回上一步：参数配置页重新查找；排布页保留本次选择结果（``keep_results``）。"""
        enter = self.connection_page.resume if keep_results else self.connection_page.enter
        enter()
        self.view_stack.setCurrentWidget(self.connection_page)
        self._sync_status_panel_visibility()

    def _apply_driver_mode_to_pages(self, driver_enabled: bool) -> None:
        """无驱放测试没有驱动功放，只隐藏驱动映射标签页；页面实例与绑定保留。"""
        index = getattr(self, "_driver_mapping_tab_index", -1)
        if index >= 0:
            self.tab_widget.setTabVisible(index, bool(driver_enabled))

    def _return_to_welcome(self) -> None:
        if confirm_leave_configuration(self, self.configuration_editor.is_dirty()):
            self.mode_badge.setText("未选择测试模式")
            self.view_stack.setCurrentWidget(self.welcome_page)

    def _register_pages(self):
        """构造标签页并登记；测量页面通过共享 controller 连接。

        电源配置不在标签页里：它由 :meth:`create_power_page` 单独创建，作为排布页
        之后的整屏步骤。
        """
        context = PageContext(
            controller=self.measurement_controller,
            config_path_provider=self.coordinator.measurement_config_path,
            run_provider=self.coordinator.resolve_measurement_run,
            driver_mode_provider=self.coordinator.amplifier_driver_mode,
            connection_dialog_factory=lambda kind, parent: ConnectionDialog(kind, parent),
            plot_widget_factory=lambda _parent: RealTimePlotWidget(show_nav_buttons=True),
            log_callback=self.add_log_message,
            progress_callback=self._set_progress,
            error_callback=lambda message: self.on_measurement_error(message),
            archival_location_provider=self.coordinator.archival_location,
            archival_run_id_provider=self.coordinator.current_run_id,
            driver_realtime_data_callback=self.coordinator.store_realtime_data,
            amplifier_realtime_data_callback=self.coordinator.store_realtime_data,
            clear_realtime_callback=self.clear_real_time_data,
            build_configuration=self.create_config_tab,
            build_visualization=self.create_visualization_tab,
            build_run_history=self.create_run_history_tab,
        )
        self.pages = {}
        for definition, page in build_pages(context):
            self.pages[definition.key] = page
            self.tab_widget.addTab(page, definition.title)
        self._active_page_index = self.tab_widget.currentIndex()
        self.tab_widget.currentChanged.connect(self._on_page_changed)
        self._page_signals_connected = True
        pages = list(self.pages.values())
        if 0 <= self._active_page_index < len(pages) and hasattr(
            pages[self._active_page_index], "on_activated"
        ):
            pages[self._active_page_index].on_activated()
        self.cable_loss_page = self.pages["cable_loss"]
        self.driver_mapping_page = self.pages["driver_mapping"]
        self.amplifier_page = self.pages["amplifier"]
        # 驱动映射页只属于有驱放测试；欢迎页选择模式后再决定是否显示。
        self._driver_mapping_tab_index = self.tab_widget.indexOf(self.driver_mapping_page)
        self.cable_loss_page.load_result_requested.connect(self._load_cable_loss_result)

    def _on_page_changed(self, index):
        """Keep page activation hooks aligned with the visible tab.

        并不是每个页面都实现激活钩子（例如只读的可视化页）；缺少钩子时直接跳过，
        而不是让每次切换标签页都抛 ``AttributeError``。
        """
        previous = getattr(self, "_active_page_index", -1)
        if previous == index:
            return
        pages = list(getattr(self, "pages", {}).values())
        if 0 <= previous < len(pages) and hasattr(pages[previous], "on_deactivated"):
            pages[previous].on_deactivated()
        if 0 <= index < len(pages) and hasattr(pages[index], "on_activated"):
            pages[index].on_activated()
        self._active_page_index = index
        self._sync_status_panel_visibility()

    def _sync_status_panel_visibility(self) -> None:
        """按当前页面决定右侧状态面板是否出现。

        “仪器配置”页只做配置与连接，不需要盯测量进度与实时日志，右侧面板在该页
        隐藏、把宽度让给表单；面板本身仍是全局唯一的进度与日志接收方，日志照常
        入队，切回测量/结果页时按上次的分栏宽度原样恢复。
        """
        panel = getattr(self, "status_panel", None)
        splitter = getattr(self, "workspace_splitter", None)
        if panel is None or splitter is None:
            return
        current = self.tab_widget.currentWidget()
        editor = getattr(self, "configuration_editor", None)
        config_page = getattr(self, "config_page", None)
        should_hide = current is not None and (
            current is config_page or (editor is not None and editor.contains(current))
        )
        if should_hide == panel.isHidden():
            return
        if should_hide:
            # 只有布局完成的可见分栏才记录真实宽度：初始化阶段的 sizes() 是按当时
            # 可用宽度缩放出来的，记下来会让面板第一次出现时被压到最小宽度。
            if splitter.isVisible():
                self._status_panel_sizes = splitter.sizes()
            panel.hide()
            return
        panel.show()
        sizes = getattr(self, "_status_panel_sizes", None)
        if sizes:
            splitter.setSizes(sizes)

    def create_config_tab(self):
        """创建仪器配置选项卡；配置页与协调器在这里互相登记。"""
        tab = ConfigurationPage(self.config, self)
        tab.save_requested.connect(self.save_config)
        tab.finalize_run_requested.connect(self._finalize_run)
        tab.load_run_requested.connect(self.coordinator.load_prepared_run)
        tab.load_run_requested.connect(lambda _run_id: self._sync_finalize_entry())
        tab.return_welcome_requested.connect(self._return_to_welcome)
        tab.connection_step_requested.connect(self._return_to_connection_page)
        tab.validation_failed.connect(
            lambda message: self.add_log_message(f"配置校验失败: {message}")
        )
        self.config_page = tab
        self.coordinator.bind_config_page(tab)
        return tab

    def create_power_page(self):
        """创建整屏电源配置页，并把它接入共享编辑状态。

        电源配置是排布页之后的整屏步骤，不再登记进工作区标签栏；它和参数页仍由
        同一个 :class:`ConfigurationEditor` 装配成一份共享草稿。
        """
        page = PowerConfigurationPage(self.config, self)
        self.power_page = page
        page.bind_controller(self.measurement_controller)
        self.coordinator.bind_power_page(page)
        self.configuration_editor = ConfigurationEditor(
            self.config_page,
            page,
            log=self.add_log_message,
        )
        return page

    def create_visualization_tab(self):
        """创建数据可视化选项卡（浏览历史结果与生成报告副本）"""
        self.visualization_page = VisualizationPage(
            # 历史查看状态归只读视图模型：列表、选中项、已加载结果与频率浏览。
            history=HistoryViewModel(self.gui_data_service.results),
            chooser=self.gui_data_service.results.choose_and_load,
            # 报告只有一个应用层实现：页面只提交“对哪条结果生成报告”的导出请求。
            export_service=self.gui_data_service.export_requests,
            log_callback=self.add_log_message,
            parent=self,
        )
        self.load_data_btn = self.visualization_page.load_data_btn
        self.generate_report_btn = self.visualization_page.generate_report_btn
        self.freq_prev_btn = self.visualization_page.freq_prev_btn
        self.freq_label = self.visualization_page.freq_label
        self.freq_next_btn = self.visualization_page.freq_next_btn
        self.data_plot_widget = self.visualization_page.data_plot_widget
        return self.visualization_page

    def create_run_history_tab(self):
        """创建历史运行/结果管理选项卡；页面内部状态由页面自己持有。"""
        self.run_history_page = build_run_history_page(
            self.gui_data_service,
            log_callback=self.add_log_message,
            parent=self,
        )
        return self.run_history_page

    def create_status_panel(self, main_layout):
        """创建右侧状态面板（纵向铺满），并暴露进度条与日志控件。"""
        self.status_panel = StatusPanel(parent=self)
        self.progress_bar = self.status_panel.progress_bar
        self.log_text = self.status_panel.log_text
        main_layout.addWidget(self.status_panel)

    # ---- 配置与运行入口（逻辑在协调器，窗口只转发） ----

    def save_config(self):
        """保存配置：文件写入由协调器完成，提示与对话框留在窗口。"""
        self.coordinator.update_configuration_from_ui()
        if not self.coordinator.save_configuration():
            error = self.coordinator.last_save_error
            self.add_log_message(f"配置保存失败: {error}")
            QMessageBox.warning(self, "保存失败", f"配置保存失败: {error}")
            return
        editor = getattr(self, "configuration_editor", None)
        if editor is not None:
            editor.mark_clean()
        self.add_log_message("配置已保存")
        if self.prepared_run is not None:
            self.add_log_message(
                f"注意：当前运行 {self.prepared_run.run_id} 的配置已冻结；"
                "若要使用本次修改，请重新“完成配置并连接仪器”"
            )
        QMessageBox.information(self, "保存成功", "配置文件已保存")

    def _finalize_run(self):
        """执行一次最终化事务；连接成功且端口提交后才进入测量区。"""
        page = getattr(self, "config_page", None)
        if page is None or self.coordinator.state is RunFlowState.FINALIZING:
            return
        editor = getattr(self, "configuration_editor", None)
        if editor is not None:
            editor.set_finalizing(True)
        started = self.coordinator.finalize_configuration(
            self._bind_instrument_worker_signals
        )
        if not started:
            self._restore_config_page()

    def _restore_config_page(self):
        """恢复参数页控件并按协调器状态同步最终按钮语义。"""
        editor = getattr(self, "configuration_editor", None)
        if editor is not None:
            editor.set_finalizing(False)
        self._sync_finalize_entry()

    def _sync_finalize_entry(self):
        page = getattr(self, "config_page", None)
        if page is not None:
            page.set_reconnect_required(
                self.coordinator.state is RunFlowState.RECONNECT_REQUIRED
            )

    def _bind_instrument_worker_signals(self, worker):
        worker.signals.finished.connect(self.on_instrument_connected)
        worker.signals.result.connect(self.on_instrument_controller_ready)
        worker.signals.stopped.connect(self.on_instrument_stopped)
        worker.signals.error.connect(self.on_instrument_error)
        worker.signals.message.connect(self.add_log_message)
        worker.signals.progress.connect(self.progress_bar.setValue)

    def on_instrument_controller_ready(self, controller):
        """连接结果：由协调器原子提交新端口与待提交运行。"""
        if not self.coordinator.commit_finalization(controller):
            self.add_log_message("仪器端口提交失败；最终动作未生效，请重试")

    def on_instrument_connected(self):
        """连接完成：只有协调器已提交运行和端口才进入测量区，不自动开始测量。"""
        self.progress_bar.setValue(0)
        if self.coordinator.state is not RunFlowState.CONNECTED:
            self._restore_config_page()
            self.add_log_message("仪器连接结束但最终动作未提交；配置页保留，可重试")
            return
        self.add_log_message("所有仪器连接成功！")
        self._restore_config_page()
        self.tab_widget.setCurrentIndex(0)
        self.view_stack.setCurrentWidget(self.workspace_page)
        self._sync_status_panel_visibility()

    def on_instrument_error(self, error_message):
        self.add_log_message(f"错误: {error_message}")
        QMessageBox.critical(self, "错误", error_message)
        self.progress_bar.setValue(0)
        self.coordinator.fail_finalization(error_message)
        self._restore_config_page()

    def on_instrument_stopped(self, reason):
        self.add_log_message(f"仪器连接线程已停止: {reason}")
        self.progress_bar.setValue(0)
        self.coordinator.cancel_finalization(reason)
        self._restore_config_page()

    def _on_measurement_controller_thread_finished(self):
        """测量线程结束后释放端口；端口所有权在协调器。"""
        self.coordinator.release_port_after_measurement()
        self._sync_finalize_entry()

    def _load_cable_loss_result(self):
        """读取最新正式运行目录结果，仅供历史查看；不会成为任何运行的输入。

        只读取运行 manifest 登记并通过校验的线损阶段结果，没有可用运行结果时提示
        未找到，不回退到旧的全局兼容副本。可预期的失败只有两类：没有可用结果
        （``FileNotFoundError``）与结果不可读/非正式 schema（``ValueError``/``OSError``）；
        两者都通过状态栏呈现，不吞掉其他程序错误。
        """
        try:
            reference = MeasurementResultReference(
                result_id=f"loaded-{datetime.now(timezone.utc).isoformat()}",
                kind=MeasurementKind.CABLE_LOSS,
                value=self.gui_data_service.results.latest_result("cable_loss").result,
                source="result_service",
            )
        except FileNotFoundError:
            self.add_log_message("未找到线损测量结果文件")
            return
        except (OSError, ValueError) as error:
            logger.warning("加载线损测量结果失败: %s", error)
            self.add_log_message(f"加载线损测量结果失败: {error}")
            return
        self.cable_loss_page.show_loaded_result(reference)

    def refresh_history(self):
        """测量结束后刷新历史运行与结果列表：页面自己容忍读取失败。"""
        page = getattr(self, "run_history_page", None)
        return None if page is None else page.refresh()

    def clear_real_time_data(self):
        """清除实时测量的历史数据（两个实时测量页面都提供同名缓存入口）。"""
        self.coordinator.clear_realtime_data()
        self.driver_mapping_page.clear_realtime_buffer()
        self.amplifier_page.clear_realtime_buffer()
        self.add_log_message("已清除实时测量历史数据")

    def add_log_message(self, message: str):
        self.status_panel.add_log_message(message)

    def _set_progress(self, value: int) -> None:
        """把测量进度写到状态面板进度条；控件可能尚未创建。"""
        progress_bar = getattr(self, "progress_bar", None)
        if progress_bar is not None:
            progress_bar.setValue(value)

    def update_status(self):
        self.status_panel.update_status()

    def on_measurement_error(self, error_message):
        """显示 controller 转发的测量错误；端口生命周期由 controller 统一收尾。"""
        self.add_log_message(f"错误: {error_message}")
        QMessageBox.critical(self, "错误", error_message)

    def closeEvent(self, event):
        """窗口关闭：确认一次后停止线程、撤销未开始运行并安全清理端口。

        连接线程未停止、待提交运行撤销失败或端口未确认清理时窗口保持打开：没有
        “确认后带着未清理端口退出”的旁路，也不会把未完成清理报告成成功。收尾顺序为
        测量线程 → 连接 worker（含已跑完但未收尾的引用）→ 最终动作补偿 → 残留待提交
        运行 → 端口 → 未决清理；后一步以前一步完成为前提，因此撤销运行目录只发生在
        连接线程停止之后。
        """
        coordinator = self.coordinator
        measurement_active = self.measurement_controller.state.is_active
        needs_attention = (
            measurement_active
            or coordinator.has_instrument_worker()
            or coordinator.state is RunFlowState.FINALIZING
            or coordinator.has_pending_run()
        )
        if needs_attention and not self._confirm_exit():
            event.ignore()
            return
        if measurement_active and not self.measurement_controller.shutdown(timeout_ms=5000):
            return self._keep_window_open(event, "测量线程未能在关闭期限内停止；窗口保持打开")
        if not coordinator.stop_instrument_worker(5000):
            return self._keep_window_open(event, "仪器连接线程未能在关闭期限内停止；窗口保持打开")
        # 重新读取状态：确认框期间最终动作可能已自行完成，已提交的运行不能被放弃流程删掉。
        if coordinator.state is RunFlowState.FINALIZING and not (
            coordinator.cancel_finalization("窗口关闭")
        ):
            return self._keep_window_open(event, "最终动作未能安全撤销；请完成仪器清理后再退出")
        if coordinator.has_pending_run() and not coordinator.abandon_pending_run():
            return self._keep_window_open(event, "待提交运行未能撤销；请完成仪器清理后再退出")
        if not coordinator.release_instrument_port():
            return self._keep_window_open(event, "仪器端口未能确认清理；请在现场确认后重试清理")
        if coordinator.has_unreleased_ports():
            return self._keep_window_open(event, "仍有未完成的仪器清理；清理完成前不能退出")
        self._disconnect_page_signals()
        for page in getattr(self, "pages", {}).values():
            page.close()
        connection_page = getattr(self, "connection_page", None)
        if connection_page is not None:
            connection_page.close()
        event.accept()

    def _keep_window_open(self, event, message: str) -> None:
        """记录关闭被拒绝的原因并保持窗口打开。"""
        self.add_log_message(message)
        event.ignore()

    def _disconnect_page_signals(self) -> None:
        """幂等断开页签切换信号；重复关闭不再触发 libpyside 断开警告。"""
        if getattr(self, "_page_signals_connected", True) is False:
            return
        self._page_signals_connected = False
        if not hasattr(self, "tab_widget"):
            return
        try:
            self.tab_widget.currentChanged.disconnect(self._on_page_changed)
        except (RuntimeError, TypeError):
            pass

    def _confirm_exit(self) -> bool:
        """退出前的唯一确认点；返回 True 表示后续停止动作已获授权。"""
        reply = QMessageBox.question(
            self, "退出", "测试正在进行中，确定要退出吗？",
            QMessageBox.Yes | QMessageBox.No,
        )
        return reply == QMessageBox.Yes


def build_application(argv=None, *, paths: Any | None = None):
    """创建配置完成的 Qt 应用，不创建窗口或连接仪器。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv if argv is None else argv)
    try:
        setup_logging(paths=paths or default_paths())
    except OSError as logging_error:
        # 日志目录不可写时仍要启动界面。这里不能用 ``get_logger``：正式日志尚未初始化，
        # 那条链路上的 NullHandler 会把消息吞掉；标准 logger 至少能落到 stderr。
        logging.getLogger(__name__).warning(
            "日志初始化失败（继续启动）: %s", logging_error
        )
    app.setApplicationName("PA自动测试系统")
    app.setApplicationVersion("1.0")
    app.setOrganizationName("PA Test Lab")
    # apply_light_theme 已经安装 build_stylesheet() 的结果（与 APPLICATION_STYLE 同源），
    # 这里不再重复 setStyleSheet：第二次应用会让 Qt 把所有存活控件再 polish 一遍。
    apply_light_theme(app)
    return app


def run_application(argv=None, window_factory=MainWindow):
    """创建并运行窗口，返回 Qt 事件循环退出码。"""
    app = build_application(argv)
    window = window_factory()
    window.show()
    # 显示后再定位一次：此时已能拿到真实屏幕（多屏环境的窗口所在屏），
    # 保证窗口完整落在可用区域内、不被任务栏遮挡。
    fit_window_to_screen(window)
    return app.exec()


def main():
    raise SystemExit(run_application())


__all__ = [
    "MainWindow", "build_application", "run_application", "main",
    "APPLICATION_STYLE", "apply_light_theme", "fit_window_to_screen",
]
