"""主窗口：Qt 窗口组装、页面注册和信号连接。

配置读写、运行创建/加载、仪器连接和端口生命周期由
:class:`presentation.qt.application_coordinator.ApplicationCoordinator` 持有；
窗口只负责把用户意图转发给协调器、连接 Qt 信号并把结果展示给用户。
"""

import logging
import sys
from datetime import datetime, timezone

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from infrastructure.filesystem.paths import DEFAULT_PATHS, PathService
from infrastructure.logging.app_logging import get_logger, setup_logging
from presentation.qt.application_coordinator import ApplicationCoordinator
from presentation.qt.assistant.chat_panel import ChatPanel
from presentation.qt.config_page import ConfigPage
from presentation.qt.connection_dialog import ConnectionDialog
from presentation.qt.export_page import ExportPage
from presentation.qt.history_view_model import HistoryViewModel
from presentation.qt.measurement_state import MeasurementKind, MeasurementResultReference
from presentation.qt.pages import PageContext, build_pages
from presentation.qt.realtime_plot import RealTimePlotWidget
from presentation.qt.status_panel import StatusPanel
from presentation.qt.visualization_page import VisualizationPage
from presentation.qt.welcome_page import WelcomePage, apply_test_mode, confirm_leave_configuration

logger = get_logger(__name__)

APPLICATION_STYLE = """
QMainWindow { background-color: white; }
QDialog { background-color: white; }
QTabWidget::pane { border: 1px solid #c0c0c0; background-color: white; }
QTabBar::tab { background-color: #e0e0e0; color: black; padding: 8px 16px; margin-right: 2px; }
QTabBar::tab:selected { background-color: white; color: black; border-bottom: 2px solid #0078d4; }
QTabBar::tab:hover { background-color: #f5f5f5; color: black; }
QGroupBox { background-color: white; color: black; font-weight: bold; border: 2px solid #c0c0c0; border-radius: 5px; margin-top: 1ex; padding-top: 10px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px 0 5px; color: black; }
QLabel { background-color: transparent; color: black; }
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox { background-color: white; color: black; border: 1px solid #ccc; padding: 4px; border-radius: 3px; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border: 1px solid #0078d4; }
QComboBox QAbstractItemView { background-color: white; color: black; selection-background-color: #0078d4; selection-color: white; border: 1px solid #ccc; }
QCheckBox { background-color: transparent; color: black; }
QTextEdit, QPlainTextEdit { background-color: white; color: black; border: 1px solid #ccc; border-radius: 3px; }
QTextEdit:focus, QPlainTextEdit:focus { border: 1px solid #0078d4; }
QProgressBar { background-color: #f0f0f0; border: 1px solid #ccc; border-radius: 3px; text-align: center; color: black; height: 20px; }
QProgressBar::chunk { background-color: #0078d4; border-radius: 2px; }
QTableWidget { background-color: white; color: black; gridline-color: #e0e0e0; border: 1px solid #ccc; }
QTableWidget::item { color: black; padding: 4px; }
QTableWidget::item:selected { background-color: #0078d4; color: white; }
QHeaderView::section { background-color: #f5f5f5; color: black; border: 1px solid #d0d0d0; padding: 4px; }
QScrollArea { background-color: white; border: none; }
QPushButton { background-color: #0078d4; color: white; border: none; padding: 8px 16px; border-radius: 4px; font-weight: bold; }
QPushButton:hover { background-color: #106ebe; }
QPushButton:pressed { background-color: #005a9e; }
QPushButton:disabled { background-color: #cccccc; color: #666666; }
"""


def _light_palette() -> QPalette:
    """与样式表一致的浅色调色板。"""
    palette = QPalette()
    white = QColor("#ffffff")
    black = QColor("#000000")
    palette.setColor(QPalette.Window, white)
    palette.setColor(QPalette.WindowText, black)
    palette.setColor(QPalette.Base, white)
    palette.setColor(QPalette.AlternateBase, QColor("#f5f5f5"))
    palette.setColor(QPalette.Text, black)
    palette.setColor(QPalette.Button, QColor("#f0f0f0"))
    palette.setColor(QPalette.ButtonText, black)
    palette.setColor(QPalette.BrightText, white)
    palette.setColor(QPalette.Highlight, QColor("#0078d4"))
    palette.setColor(QPalette.HighlightedText, white)
    palette.setColor(QPalette.ToolTipBase, white)
    palette.setColor(QPalette.ToolTipText, black)
    palette.setColor(QPalette.PlaceholderText, QColor("#777777"))
    palette.setColor(QPalette.Link, QColor("#0078d4"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        palette.setColor(QPalette.Disabled, role, QColor("#9a9a9a"))
    return palette


def apply_light_theme(app: QApplication) -> None:
    """固定界面浅色配色，不受系统深色模式影响。

    Windows 处于深色模式时，Qt 6 会给出深色调色板；而样式表只覆盖了部分控件，
    未被覆盖的容器（滚动区内容、分组框边距、QFrame 边框、布局间隙）会露出深色
    底，界面上表现为大量“黑框”。这里在安装样式表之前强制浅色配色与浅色调色板，
    保证未单独设置样式的区域也是白底黑字。
    """
    hints = app.styleHints()
    set_color_scheme = getattr(hints, "setColorScheme", None)
    if set_color_scheme is not None:  # Qt >= 6.8 才能主动指定配色方案
        set_color_scheme(Qt.ColorScheme.Light)
    if app.palette().color(QPalette.Window).lightness() < 128:
        app.setPalette(_light_palette())


class MainWindow(QMainWindow):
    """主窗口：只组装 Qt 界面、注册页面并连接信号。"""

    # 助手线程关闭的等待上限：助手是可选能力，等不到就保持窗口打开让用户重试。
    ASSISTANT_SHUTDOWN_TIMEOUT_MS = 3000

    def __init__(self, config_path=None, *, paths: PathService | None = None):
        super().__init__()
        self.paths = paths or DEFAULT_PATHS
        self.setWindowTitle("PA自动测试系统 v1.0")
        self.setGeometry(100, 100, 1600, 1000)
        self.coordinator = ApplicationCoordinator(
            log=self.add_log_message,
            warn=lambda title, message: QMessageBox.warning(self, title, message),
            error=self.on_measurement_error,
            paths=self.paths,
            config_path=config_path,
            confirm_run_wiring=self._confirm_create_run_wiring,
        )
        self.measurement_controller.signals.thread_finished.connect(
            self._on_measurement_controller_thread_finished
        )
        self.measurement_controller.signals.finished.connect(self.refresh_file_list)
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
        """初始化用户界面"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        self.view_stack = QStackedWidget()
        self.welcome_page = WelcomePage()
        self.welcome_page.mode_selected.connect(self._enter_test_mode)
        self.view_stack.addWidget(self.welcome_page)

        main_splitter = QSplitter(Qt.Horizontal)
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        self.tab_widget = QTabWidget()
        left_layout.addWidget(self.tab_widget)
        self._register_pages()
        self.create_status_panel(left_layout)
        main_splitter.addWidget(left_widget)

        self.chat_panel = ChatPanel(self, paths=self.paths)
        self.chat_panel.setMinimumWidth(350)
        self.chat_panel.setMaximumWidth(600)
        main_splitter.addWidget(self.chat_panel)

        main_splitter.setSizes([900, 350])
        main_splitter.setStretchFactor(0, 3)
        main_splitter.setStretchFactor(1, 1)

        self.workspace_page = QWidget()
        workspace_layout = QVBoxLayout(self.workspace_page)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.addWidget(main_splitter)
        self.view_stack.addWidget(self.workspace_page)

        central_layout = QVBoxLayout(central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(self.view_stack)

        self.chat_panel.hide()
        self.chat_visible = False

        self.view_stack.setCurrentWidget(self.welcome_page)

    def _enter_test_mode(self, driver_enabled: bool) -> None:
        config = apply_test_mode(dict(self.coordinator.load_configuration()), driver_enabled)
        self.coordinator.config = config
        self.config_page.apply_config(config)
        self.config_page.set_test_mode(driver_enabled)
        self._apply_driver_mode_to_pages(driver_enabled)
        self.tab_widget.setCurrentIndex(0)
        self.view_stack.setCurrentWidget(self.workspace_page)

    def _apply_driver_mode_to_pages(self, driver_enabled: bool) -> None:
        """无驱放测试没有驱动功放，只隐藏驱动映射标签页；页面实例与绑定保留。"""
        index = getattr(self, "_driver_mapping_tab_index", -1)
        if index >= 0:
            self.tab_widget.setTabVisible(index, bool(driver_enabled))

    def _return_to_welcome(self) -> None:
        if confirm_leave_configuration(self, self.config_page.is_dirty()):
            self.view_stack.setCurrentWidget(self.welcome_page)

    def _register_pages(self):
        """构造页面并登记 tab；测量页面通过共享 controller 连接。"""
        context = PageContext(
            controller=self.measurement_controller,
            config_path_provider=self.coordinator.measurement_config_path,
            run_provider=self.coordinator.resolve_measurement_run,
            confirm_cable_loss=self._confirm_cable_loss_wiring,
            confirm_driver_mapping=self._confirm_driver_mapping_wiring,
            confirm_amplifier=self._confirm_amplifier_wiring,
            driver_mode_provider=self.coordinator.amplifier_driver_mode,
            connection_dialog_factory=lambda kind, parent: ConnectionDialog(kind, parent),
            plot_widget_factory=lambda _parent: RealTimePlotWidget(show_nav_buttons=True),
            log_callback=self.add_log_message,
            progress_callback=self._set_progress,
            error_callback=lambda message: self.on_measurement_error(message),
            driver_realtime_data_callback=self.coordinator.store_realtime_data,
            amplifier_realtime_data_callback=self.coordinator.store_realtime_data,
            clear_realtime_callback=self.clear_real_time_data,
            build_configuration=self.create_config_tab,
            build_visualization=self.create_visualization_tab,
            build_export=self.create_data_export_tab,
        )
        self.pages = {}
        for definition, page in build_pages(context):
            self.pages[definition.key] = page
            self.tab_widget.addTab(page, definition.title)
        self._active_page_index = self.tab_widget.currentIndex()
        self.tab_widget.currentChanged.connect(self._on_page_changed)
        self._page_signals_connected = True
        pages = list(self.pages.values())
        if 0 <= self._active_page_index < len(pages):
            pages[self._active_page_index].on_activated()
        self.cable_loss_page = self.pages["cable_loss"]
        self.driver_mapping_page = self.pages["driver_mapping"]
        self.amplifier_page = self.pages["amplifier"]
        # 驱动映射页只属于有驱放测试；欢迎页选择模式后再决定是否显示。
        self._driver_mapping_tab_index = self.tab_widget.indexOf(self.driver_mapping_page)
        self.cable_loss_page.load_result_requested.connect(self._load_cable_loss_result)

    def _on_page_changed(self, index):
        """Keep page activation hooks aligned with the visible tab."""
        previous = getattr(self, "_active_page_index", -1)
        if previous == index:
            return
        pages = list(getattr(self, "pages", {}).values())
        if 0 <= previous < len(pages):
            pages[previous].on_deactivated()
        if 0 <= index < len(pages):
            pages[index].on_activated()
        self._active_page_index = index

    def create_config_tab(self):
        """创建仪器配置选项卡；配置页与协调器在这里互相登记。"""
        tab = ConfigPage(self.config, self)
        tab.save_requested.connect(self.save_config)
        tab.connect_requested.connect(self.connect_instruments)
        tab.create_run_requested.connect(self.coordinator.create_prepared_run)
        tab.load_run_requested.connect(self.coordinator.load_prepared_run)
        tab.return_welcome_requested.connect(self._return_to_welcome)
        tab.validation_failed.connect(
            lambda message: self.add_log_message(f"配置校验失败: {message}")
        )
        tab.driver_mode_changed.connect(self._refresh_amplifier_instruction_text)
        self.config_page = tab
        self.coordinator.bind_config_page(tab)
        return tab

    def create_visualization_tab(self):
        """创建数据可视化选项卡"""
        self.visualization_page = VisualizationPage(
            # 历史查看状态归只读视图模型：列表、选中项、已加载结果与频率浏览。
            history=HistoryViewModel(self.gui_data_service.results),
            chooser=self.gui_data_service.results.choose_and_load,
            config_provider=lambda: self.config,
            report_service=self.gui_data_service.reports.create_summary_report,
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

    def create_data_export_tab(self):
        """创建数据导出选项卡"""
        self.export_page = ExportPage(
            history=HistoryViewModel(self.gui_data_service.results),
            export_service=self.gui_data_service.export_requests,
            log_callback=self.add_log_message,
            parent=self,
        )
        self.file_table = self.export_page.file_table
        self.export_json_btn = self.export_page.export_json_btn
        self.export_csv_btn = self.export_page.export_csv_btn
        self.export_report_btn = self.export_page.export_report_btn
        return self.export_page

    def create_status_panel(self, main_layout):
        """创建状态面板"""
        self.status_panel = StatusPanel(
            chat_toggle_callback=self.toggle_chat_panel,
            parent=self,
        )
        self.progress_bar = self.status_panel.progress_bar
        self.log_text = self.status_panel.log_text
        self.ai_assistant_btn = self.status_panel.ai_assistant_btn
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

    def _confirm_create_run_wiring(self, config) -> bool:
        """创建运行前确认现场接线，并把确认固化到即将冻结的配置中。"""
        driver_enabled = self.config_page.driver_mode_requested()
        diagram = "amplifier_test" if driver_enabled else "amplifier_test_no_driver"
        if ConnectionDialog(diagram, self).exec() != QDialog.Accepted:
            return False
        config.setdefault("wiring", {}).update({
            "confirmed": True,
            "connection_note": "已通过创建运行连接确认对话框确认现场接线",
            "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "confirmation_source": "create_run_dialog",
        })
        self.config_page.config = config
        return True

    def _confirm_cable_loss_wiring(self):
        """线损测量前的现场接线确认；运行已冻结，不再改写配置。"""
        self.add_log_message("线损路径1现场接线已确认")
        return True

    def _confirm_driver_mapping_wiring(self):
        """驱动映射测量前的现场接线确认；运行已冻结，不再改写配置。"""
        self.add_log_message("驱动映射现场接线已确认")
        return True

    def _confirm_amplifier_wiring(self):
        """主功放测量前的现场接线确认；运行已冻结，不再改写配置。"""
        self.add_log_message("主功放现场接线已确认")
        return True

    # ---- 仪器连接（端口与 worker 归协调器，窗口只连接信号） ----

    def connect_instruments(self):
        """连接仪器；连接成功前按钮保持禁用。"""
        self.connect_btn.setEnabled(False)
        if not self.coordinator.connect_instruments(self._bind_instrument_worker_signals):
            self.connect_btn.setEnabled(True)

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
        self.connect_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.add_log_message("所有仪器连接成功！")

    def on_instrument_error(self, error_message):
        """处理仪器连接 worker 的错误。"""
        self.add_log_message(f"错误: {error_message}")
        QMessageBox.critical(self, "错误", error_message)
        # 连接 worker 失败时只恢复连接控件；测量页面由 controller 状态驱动。
        self.connect_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.coordinator.release_instrument_port()

    def on_instrument_stopped(self, reason):
        """处理仪器连接 worker 的停止。"""
        self.add_log_message(f"仪器连接线程已停止: {reason}")
        self.connect_btn.setEnabled(True)
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

    def refresh_file_list(self):
        """刷新文件列表"""
        return self.export_page.refresh_file_list()

    def toggle_chat_panel(self):
        """切换聊天面板显示/隐藏；欢迎语由面板自己负责。"""
        visible = not self.chat_visible
        if visible:
            self.chat_panel.show()
            self.chat_panel.show_welcome_message()
        else:
            self.chat_panel.hide()
        self.ai_assistant_btn.setChecked(visible)
        self.ai_assistant_btn.setText("💬 隐藏CHAT" if visible else "💬 CHAT")
        self.chat_visible = visible

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
        的半提交状态）。之后只剩三类明确的失败询问：停止超时、清理未完成与助手
        线程仍在生成回复。

        顺序固定为“硬件安全清理 → 助手线程收尾 → 关闭页面 → 接受关闭”：助手是
        可选能力，它的收尾失败绝不能让仪器端口的安全释放被跳过，但也不能在助手
        线程仍在运行时销毁窗口（Qt 会带着运行中的 QThread 一起析构）。
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
        if not self._shutdown_assistant():
            event.ignore()
            return
        self._disconnect_page_signals()
        for page in getattr(self, "pages", {}).values():
            page.close()
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

    def _shutdown_assistant(self) -> bool:
        """协作关闭助手回复线程；失败时保持窗口打开以便重试。"""
        if self.chat_panel.shutdown(self.ASSISTANT_SHUTDOWN_TIMEOUT_MS):
            return True
        self.add_log_message(
            "助手回复仍在生成，未能在关闭期限内停止；窗口保持打开。"
            "请等当前回复结束后重试关闭（单次请求最长约 60 秒）。"
        )
        return False

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


def build_application(argv=None, *, paths: PathService | None = None):
    """创建配置完成的 Qt 应用，不创建窗口或连接仪器。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv if argv is None else argv)
    try:
        setup_logging(paths=paths or DEFAULT_PATHS)
    except OSError as logging_error:
        # 日志目录不可写时仍要启动界面。这里不能用 ``get_logger``：正式日志尚未初始化，
        # 那条链路上的 NullHandler 会把消息吞掉；标准 logger 至少能落到 stderr。
        logging.getLogger(__name__).warning(
            "日志初始化失败（继续启动）: %s", logging_error
        )
    app.setApplicationName("PA自动测试系统")
    app.setApplicationVersion("1.0")
    app.setOrganizationName("PA Test Lab")
    apply_light_theme(app)
    app.setStyleSheet(APPLICATION_STYLE)
    return app


def run_application(argv=None, window_factory=MainWindow):
    """创建并运行窗口，返回 Qt 事件循环退出码。"""
    app = build_application(argv)
    window = window_factory()
    window.show()
    return app.exec()


def main():
    raise SystemExit(run_application())


__all__ = [
    "MainWindow", "build_application", "run_application", "main",
    "APPLICATION_STYLE", "apply_light_theme",
]
