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
from presentation.qt.application_coordinator import ApplicationCoordinator
from presentation.qt.composition import build_device_scanner_factory, default_paths
from presentation.qt.config_page import ConfigPage
from presentation.qt.connection_dialog import ConnectionDialog
from presentation.qt.connection_page import ConnectionPage
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

        # 仪表配置是欢迎页之后的整屏页面，不登记进标签栏：先选设备、连接，再配测试参数。
        # 它只把设备绑定写进配置页，页面切换由本窗口负责。
        self.connection_page = ConnectionPage(
            self.config_page,
            scanner_factory=build_device_scanner_factory(self.paths),
        )
        self.connection_page.next_requested.connect(self._open_configuration_page)
        self.connection_page.return_welcome_requested.connect(self._return_to_welcome)
        self.connection_page.log_message.connect(self.add_log_message)
        self.view_stack.insertWidget(1, self.connection_page)

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
        self.config_page.apply_config(config)
        self.config_page.set_test_mode(driver_enabled)
        self._apply_driver_mode_to_pages(driver_enabled)
        self.mode_badge.setText("有驱放测试" if driver_enabled else "无驱放测试")
        self.tab_widget.setCurrentIndex(0)
        # 选好测试模式后先配置仪器连接：应用设备后由“下一步”进入仪器配置页。
        self.connection_page.enter()
        self.view_stack.setCurrentWidget(self.connection_page)
        self._sync_status_panel_visibility()

    def _open_configuration_page(self) -> None:
        """仪表配置完成：进入工作区，当前页面仍是“仪器配置”。

        “下一步”只做页面切换，不写配置：设备绑定已经记在配置页上，保存与创建运行
        时随配置一起落盘/冻结，因此中途返回欢迎页也不会留下半保存的配置。
        """
        self.tab_widget.setCurrentIndex(0)
        self.view_stack.setCurrentWidget(self.workspace_page)
        self._sync_status_panel_visibility()

    def _return_to_connection_page(self) -> None:
        """回上一步：重新扫描/更换设备；已填的测试参数留在内存里，回来时仍在。"""
        self.connection_page.enter()
        self.view_stack.setCurrentWidget(self.connection_page)
        self._sync_status_panel_visibility()

    def _apply_driver_mode_to_pages(self, driver_enabled: bool) -> None:
        """无驱放测试没有驱动功放，只隐藏驱动映射标签页；页面实例与绑定保留。"""
        index = getattr(self, "_driver_mapping_tab_index", -1)
        if index >= 0:
            self.tab_widget.setTabVisible(index, bool(driver_enabled))

    def _return_to_welcome(self) -> None:
        if confirm_leave_configuration(self, self.config_page.is_dirty()):
            self.mode_badge.setText("未选择测试模式")
            self.view_stack.setCurrentWidget(self.welcome_page)

    def _register_pages(self):
        """构造页面并登记 tab；测量页面通过共享 controller 连接。"""
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
        config_page = getattr(self, "config_page", None)
        current = self.tab_widget.currentWidget()
        should_hide = config_page is not None and current is config_page
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
        tab = ConfigPage(self.config, self)
        tab.save_requested.connect(self.save_config)
        tab.connect_requested.connect(self.connect_instruments)
        tab.create_run_requested.connect(self.coordinator.create_prepared_run)
        tab.load_run_requested.connect(self.coordinator.load_prepared_run)
        tab.return_welcome_requested.connect(self._return_to_welcome)
        tab.connection_step_requested.connect(self._return_to_connection_page)
        tab.validation_failed.connect(
            lambda message: self.add_log_message(f"配置校验失败: {message}")
        )
        tab.driver_mode_changed.connect(self._refresh_amplifier_instruction_text)
        self.config_page = tab
        self.coordinator.bind_config_page(tab)
        return tab

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
        page = getattr(self, "config_page", None)
        if page is not None:
            page.mark_clean()
        self.add_log_message("配置已保存")
        if self.prepared_run is not None:
            self.add_log_message(
                f"注意：当前运行 {self.prepared_run.run_id} 的配置已冻结；"
                "若要使用本次修改，请重新“校验并创建运行”"
            )
        QMessageBox.information(self, "保存成功", "配置文件已保存")

    # ---- 仪器连接（端口与 worker 归协调器，窗口只连接信号） ----

    def _set_connect_enabled(self, enabled: bool) -> None:
        """连接期间禁用/恢复唯一那个连接入口。

        连接按钮在“仪表配置”页（欢迎页之后的第一步），不在标签页里；窗口只负责
        它在连接过程中的可用状态，不自己持有按钮。
        """
        page = getattr(self, "connection_page", None)
        button = getattr(page, "connect_btn", None)
        if button is not None:
            button.setEnabled(enabled)

    def connect_instruments(self):
        """连接仪器；连接成功前按钮保持禁用。"""
        self._set_connect_enabled(False)
        if not self.coordinator.connect_instruments(self._bind_instrument_worker_signals):
            self._set_connect_enabled(True)

    def _bind_instrument_worker_signals(self, worker):
        worker.signals.finished.connect(self.on_instrument_connected)
        worker.signals.result.connect(self.on_instrument_controller_ready)
        worker.signals.stopped.connect(self.on_instrument_stopped)
        worker.signals.error.connect(self.on_instrument_error)
        worker.signals.message.connect(self.add_log_message)
        worker.signals.progress.connect(self.progress_bar.setValue)

    def on_instrument_controller_ready(self, controller):
        """端口交接：协调器保存端口并注入 controller。"""
        if not self.coordinator.accept_instrument_controller(controller):
            self.add_log_message(
                "上一次连接的仪器清理未完成，已登记为未决清理；请现场确认后重试"
            )

    def on_instrument_connected(self):
        """仪器连接完成"""
        self._set_connect_enabled(True)
        self.progress_bar.setValue(0)
        self.add_log_message("所有仪器连接成功！")

    def on_instrument_error(self, error_message):
        """处理仪器连接 worker 的错误。"""
        self.add_log_message(f"错误: {error_message}")
        QMessageBox.critical(self, "错误", error_message)
        # 连接 worker 失败时只恢复连接控件；测量页面由 controller 状态驱动。
        self._set_connect_enabled(True)
        self.progress_bar.setValue(0)
        self.coordinator.release_instrument_port()

    def on_instrument_stopped(self, reason):
        """处理仪器连接 worker 的停止。"""
        self.add_log_message(f"仪器连接线程已停止: {reason}")
        self._set_connect_enabled(True)
        self.progress_bar.setValue(0)
        self.coordinator.release_instrument_port()

    def _on_measurement_controller_thread_finished(self):
        """测量线程结束后释放端口；端口所有权在协调器。"""
        self.coordinator.release_port_after_measurement()

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

    def _refresh_amplifier_instruction_text(self, driver_enabled):
        """驱动模式变化后刷新功放页连接说明。

        说明文本归功放页面所有（``AmplifierPage.update_instruction_text``），窗口只把
        配置页的意图转发过去；配置页总是先于其他页面登记，取页面时允许尚未登记。
        """
        page = getattr(self, "pages", {}).get("amplifier")
        if page is not None:
            page.update_instruction_text(driver_enabled)

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
        """窗口关闭：先完成全部确认，再提交不可逆的停止与清理动作。

        确认只在开头做一次：一旦用户同意退出，停止测量与连接线程就是已提交的
        动作，不会在后续步骤里再被“取消退出”打断（否则会出现“测量已停、窗口还在”
        的半提交状态）。之后只剩两类明确的失败询问：停止超时与清理未完成。

        顺序固定为“硬件安全清理 → 关闭页面 → 接受关闭”，仪器端口的安全释放
        绝不能因为后续步骤失败而被跳过。
        """
        measurement_active = self.measurement_controller.state.is_active
        worker_running = self.coordinator.instrument_worker_running()
        if (measurement_active or worker_running) and not self._confirm_exit():
            event.ignore()
            return
        if measurement_active and not self.measurement_controller.shutdown(timeout_ms=5000):
            self.add_log_message("测量线程未能在关闭期限内停止；窗口保持打开")
            event.ignore()
            return
        if worker_running and not self.coordinator.stop_instrument_worker(5000):
            event.ignore()
            return
        self.coordinator.release_instrument_port()
        if self.coordinator.has_unreleased_ports() and not self._confirm_exit_with_unreleased_ports():
            event.ignore()
            return
        self._disconnect_page_signals()
        for page in getattr(self, "pages", {}).values():
            page.close()
        connection_page = getattr(self, "connection_page", None)
        if connection_page is not None:
            connection_page.close()
        event.accept()

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

    def _confirm_exit_with_unreleased_ports(self) -> bool:
        """清理未完成时的二次确认：明确告知可能仍有 RF/电源输出。"""
        reply = QMessageBox.question(
            self,
            "退出",
            "部分仪器未能完成安全清理，可能仍有 RF 或电源输出。\n"
            "确定仍然退出吗？退出后请现场手动确认仪器状态。",
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
