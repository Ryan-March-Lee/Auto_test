"""主窗口和 Qt 应用组装。"""

import sys
import os
import json
from datetime import datetime, timezone
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QTabWidget, QLabel, QLineEdit, QPushButton, QTextEdit, QGroupBox,
    QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox, QProgressBar,
    QSplitter, QFrame, QGridLayout, QMessageBox, QFormLayout, QScrollArea,
    QDialog, QListWidget
)
from PySide6.QtCore import Qt, QTimer

# 导入我们的测试模块和连接图
from data_visualization import DataVisualization
from presentation.qt.pages import PageContext, build_pages
from presentation.qt.measurement_controller import MeasurementController
from presentation.qt.measurement_state import MeasurementKind, MeasurementResultReference
from presentation.qt.measurement_worker_factories import build_measurement_worker_factories
from presentation.qt.workers import BaseWorker, InstrumentWorker
import sys
import os
# 保持从旧入口启动时的项目根目录导入行为。
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
from infrastructure.filesystem.paths import (
    CABLE_LOSS_FILE,
    CONFIG_FILE,
    ICONS_DIR,
    PROJECT_ROOT,
    TEMP_DIR,
    TEST_RESULTS_DIR,
)
from infrastructure.config.json_io import load_config_file
from infrastructure.persistence.json_result_repository import load_json_result
from infrastructure.logging.app_logging import setup_logging
from assistant.storage import (
    has_current_history,
    list_history_files,
    load_history_file,
    load_search_api_config,
    save_search_api_config,
)
















# Phase 3.1 compatibility exports.  MainWindow and legacy callers use the
# extracted components; the legacy implementations above remain private during
# the incremental migration and are not part of the public API.
from presentation.qt.connection_dialog import ConnectionDialog
from presentation.qt.realtime_plot import RealTimePlotWidget
from presentation.qt.assistant.chat_panel import ChatPanel
from presentation.qt.assistant.chat_worker import UnavailableAssistant as _UnavailableAssistant
from presentation.qt.assistant.chat_worker import ChatWorker
from presentation.qt.assistant.chat_dialogs import ChatHistoryDialog, ChatSettingsDialog
from presentation.qt.config_form_state import ConfigFormState
from presentation.qt.config_page import ConfigPage
from presentation.qt.visualization_page import VisualizationPage
from presentation.qt.export_page import ExportPage
from presentation.qt.status_panel import StatusPanel
from presentation.qt.realtime_buffer import RealtimeMeasurementBuffer, normalize_frequency


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


class MainWindow(QMainWindow):
    """主窗口类"""

    def __init__(self, config_path=None):
        super().__init__()
        self.config_path = Path(config_path or CONFIG_FILE)
        self.setWindowTitle("PA自动测试系统 v1.0")
        self.setGeometry(100, 100, 1600, 1000)
        
        # 初始化变量
        self.config = {}
        self.instrument_ctrl = None
        self.instrument_worker = None
        self.measurement_controller = MeasurementController(
            build_measurement_worker_factories(), lambda: str(self.config_path)
        )
        self.measurement_controller.signals.thread_finished.connect(
            self._on_measurement_controller_thread_finished
        )
        self.measurement_controller.signals.finished.connect(self.refresh_file_list)
        self.measurement_controller.signals.message.connect(self.add_log_message)
        self.measurement_controller.signals.rejected.connect(self.add_log_message)
        
        # 实时绘图由页面拥有；这里仅保留聊天上下文所需的统一浏览缓存。
        self.realtime_buffer = RealtimeMeasurementBuffer()
        
        # 加载配置
        self.load_config()
        
        # 初始化UI
        self.init_ui()
        
        # 设置定时器用于日志更新
        self.log_timer = QTimer()
        self.log_timer.timeout.connect(self.update_status)
        self.log_timer.start(100)  # 100ms更新一次
        
    @property
    def loaded_data(self):
        return self.visualization_page.loaded_data

    @loaded_data.setter
    def loaded_data(self, value):
        self.visualization_page.loaded_data = value

    @property
    def frequency_list(self):
        return self.visualization_page.frequency_list

    @frequency_list.setter
    def frequency_list(self, value):
        self.visualization_page.frequency_list = value

    @property
    def current_freq_index(self):
        return self.visualization_page.current_freq_index

    @current_freq_index.setter
    def current_freq_index(self, value):
        self.visualization_page.current_freq_index = value

    @property
    def loaded_filename(self):
        return self.visualization_page.loaded_filename

    @loaded_filename.setter
    def loaded_filename(self, value):
        self.visualization_page.loaded_filename = value

    @property
    def real_time_data(self):
        return self.realtime_buffer.data

    @property
    def rt_frequency_list(self):
        return self.realtime_buffer.frequencies

    @property
    def rt_current_freq_index(self):
        return self.realtime_buffer.current_index

    @rt_current_freq_index.setter
    def rt_current_freq_index(self, value):
        self.realtime_buffer.current_index = value

    @property
    def rt_user_browsing(self):
        return self.realtime_buffer.user_browsing

    @rt_user_browsing.setter
    def rt_user_browsing(self, value):
        self.realtime_buffer.user_browsing = value

    @property
    def log_user_scrolling(self):
        return self.status_panel.log_user_scrolling

    @log_user_scrolling.setter
    def log_user_scrolling(self, value):
        self.status_panel.log_user_scrolling = value

    def load_config(self):
        """加载配置文件"""
        try:
            self.config = load_config_file(self.config_path)
        except Exception as e:
            QMessageBox.warning(self, "配置加载", f"无法加载配置文件: {e}")
            self.config = {}
    
    def init_ui(self):
        """初始化用户界面"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        # 主布局改为水平分割器
        main_splitter = QSplitter(Qt.Horizontal)
        
        # 左侧主要内容区域
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        
        # 创建选项卡
        self.tab_widget = QTabWidget()
        left_layout.addWidget(self.tab_widget)
        
        # 页面由 presentation 层构造；窗口只登记页面并提供全局 UI 回调。
        self._register_pages()
        
        # 状态栏和控制面板
        self.create_status_panel(left_layout)
        
        # 添加左侧到分割器
        main_splitter.addWidget(left_widget)
        
        # 右侧聊天面板
        self.chat_panel = ChatPanel(self)
        self.chat_panel.setMinimumWidth(350)
        self.chat_panel.setMaximumWidth(600)
        main_splitter.addWidget(self.chat_panel)
        
        # 设置分割器比例 (主要内容:聊天面板 = 3:1)
        main_splitter.setSizes([900, 350])
        main_splitter.setStretchFactor(0, 3)
        main_splitter.setStretchFactor(1, 1)
        
        # 设置中央布局
        central_layout = QVBoxLayout(central_widget)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(main_splitter)
        
        # 初始化聊天面板状态（默认隐藏）
        self.chat_panel.hide()
        self.chat_visible = False

    def _register_pages(self):
        """构造页面并登记 tab；测量页面通过共享 controller 连接。"""
        context = PageContext(
            controller=self.measurement_controller,
            config_path_provider=lambda: str(self.config_path),
            prepare_cable_loss=self._prepare_cable_loss_run,
            confirm_cable_loss=self._confirm_cable_loss_wiring,
            prepare_driver_mapping=self._prepare_driver_mapping_run,
            confirm_driver_mapping=self._confirm_driver_mapping_wiring,
            prepare_amplifier=self._prepare_amplifier_run,
            confirm_amplifier=self._confirm_amplifier_wiring,
            driver_mode_provider=lambda: self.config_page.driver_mode_check.isChecked(),
            connection_dialog_factory=lambda kind, parent: ConnectionDialog(kind, parent),
            plot_widget_factory=lambda _parent: RealTimePlotWidget(show_nav_buttons=True),
            log_callback=self.add_log_message,
            progress_callback=lambda value: getattr(self, "progress_bar", None)
            and self.progress_bar.setValue(value),
            error_callback=lambda message: self.on_measurement_error(message),
            driver_realtime_data_callback=self._store_driver_mapping_realtime_data,
            amplifier_realtime_data_callback=self._store_amplifier_realtime_data,
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
        pages = list(self.pages.values())
        if 0 <= self._active_page_index < len(pages):
            pages[self._active_page_index].on_activated()
        self.cable_loss_page = self.pages["cable_loss"]
        self.driver_mapping_page = self.pages["driver_mapping"]
        self.amplifier_page = self.pages["amplifier"]
        self.cable_loss_btn = self.cable_loss_page.cable_loss_btn
        self.cable_loss_table = self.cable_loss_page.cable_loss_table
        self.driver_mapping_btn = self.driver_mapping_page.driver_mapping_btn
        self.driver_stop_btn = self.driver_mapping_page.driver_stop_btn
        self.driver_emergency_stop_btn = self.driver_mapping_page.driver_emergency_stop_btn
        self.driver_plot_widget = self.driver_mapping_page.driver_plot_widget
        self.instruction_text = self.amplifier_page.instruction_text
        self.amplifier_test_btn = self.amplifier_page.amplifier_test_btn
        self.emergency_stop_btn = self.amplifier_page.emergency_stop_btn
        self.amplifier_plot_widget = self.amplifier_page.amplifier_plot_widget
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

    def _load_cable_loss_result(self):
        """由窗口协调结果服务读取，再把结构化引用交给页面显示。"""
        try:
            reference = MeasurementResultReference(
                result_id=f"loaded-{datetime.now(timezone.utc).isoformat()}",
                kind=MeasurementKind.CABLE_LOSS,
                value=load_json_result(CABLE_LOSS_FILE),
                source="result_service",
            )
            self.cable_loss_page.show_loaded_result(reference)
        except FileNotFoundError:
            self.add_log_message("未找到线损测量结果文件")
        except Exception as error:
            self.add_log_message(f"加载线损测量结果失败: {error}")

    def toggle_chat_panel(self):
        """切换聊天面板显示/隐藏"""
        if self.chat_visible:
            self.chat_panel.hide()
            self.ai_assistant_btn.setChecked(False)
            self.ai_assistant_btn.setText("💬 CHAT")
            self.chat_visible = False
        else:
            self.chat_panel.show()
            self.ai_assistant_btn.setChecked(True)
            self.ai_assistant_btn.setText("💬 隐藏CHAT")
            self.chat_visible = True
            # 欢迎消息
            if hasattr(self.chat_panel, 'chat_display'):
                # 动态获取当前使用的模型名称
                model_name = getattr(self.chat_panel.llm_chat, 'model_name', '').lower()
                if 'qwen' in model_name:
                    model_display_name = 'Qwen'
                elif 'deepseek' in model_name:
                    model_display_name = 'DeepSeek'
                else:
                    model_display_name = getattr(self.chat_panel.llm_chat, 'model_name', 'AI')
                    
                welcome_msg = f"你好！我是{model_display_name} AI助手，专门为功放测试系统提供技术支持。我可以帮你：\n\n• 分析测试数据和结果\n• 诊断测试过程中的问题\n• 提供功放设计和测试建议\n• 解答技术疑问\n\n有什么需要帮助的吗？"
                self.chat_panel.add_assistant_message(welcome_msg)
        
    def create_config_tab(self):
        """创建仪器配置选项卡"""
        tab = ConfigPage(self.config, self)
        tab.save_requested.connect(self.save_config)
        tab.connect_requested.connect(self.connect_instruments)
        tab.validation_failed.connect(lambda message: self.add_log_message(f"配置校验失败: {message}"))
        tab.driver_mode_changed.connect(lambda _checked: self.update_amplifier_instruction_text())
        self.config_page = tab
        self._install_config_compatibility_aliases(tab)
        return tab

    def _install_config_compatibility_aliases(self, page):
        """兼容旧测试和外部入口；配置控件的所有权仍属于 ConfigPage。"""
        for name in (
            'sg_address', 'sg_enabled', 'sa_address', 'sa_enabled',
            'ps1_address', 'ps1_enabled', 'ps2_address', 'ps2_enabled',
            'ps3_address', 'ps3_enabled', 'ps4_address', 'ps4_enabled',
            'freq_edit', 'start_power', 'stop_power', 'power_step',
            'compression_combo', 'attenuator_combo', 'driver_mode_check',
            'max_input_power', 'pa_unit_count_combo', 'power_config_widgets',
            'power_assignment_widgets', 'driver_power_enabled',
            'driver_power_combo', 'driver_power_label', 'pa_unit1_power_combo',
            'pa_unit2_power_combo', 'pa_unit2_label', 'pa_unit3_power_combo',
            'pa_unit3_label', 'connect_btn', 'save_config_btn',
        ):
            setattr(self, name, getattr(page, name))

        # 以下旧实现只保留为兼容入口，具体状态逻辑由页面持有。
        return page

        '''Legacy configuration-page implementation retained temporarily for source compatibility.
        layout = QVBoxLayout(tab)
        
        # 创建滚动区域
        scroll = QScrollArea()
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        
        # 仪器连接组
        connection_group = QGroupBox("仪器连接配置")
        connection_layout = QGridLayout(connection_group)
        
        # 设置列宽比例 - 标签、地址输入框、启用复选框
        connection_layout.setColumnStretch(0, 1)  # 标签列
        connection_layout.setColumnStretch(1, 3)  # 输入框列，更宽
        connection_layout.setColumnStretch(2, 0)  # 复选框列，固定宽度
        
        # 信号源配置
        connection_layout.addWidget(QLabel("信号发生器地址:"), 0, 0)
        self.sg_address = QLineEdit(self.config.get('instruments', {}).get('signal_generator', {}).get('address', ''))
        self.sg_address.setMinimumWidth(350)  # 设置最小宽度
        connection_layout.addWidget(self.sg_address, 0, 1)
        self.sg_enabled = QCheckBox("启用")
        self.sg_enabled.setChecked(self.config.get('instruments', {}).get('signal_generator', {}).get('enabled', True))  # 从配置加载启用状态
        self.sg_enabled.toggled.connect(self.on_instrument_enabled_changed)
        connection_layout.addWidget(self.sg_enabled, 0, 2)
        
        # 频谱仪配置  
        connection_layout.addWidget(QLabel("频谱分析仪地址:"), 1, 0)
        self.sa_address = QLineEdit(self.config.get('instruments', {}).get('spectrum_analyzer', {}).get('address', ''))
        self.sa_address.setMinimumWidth(350)
        connection_layout.addWidget(self.sa_address, 1, 1)
        self.sa_enabled = QCheckBox("启用")
        self.sa_enabled.setChecked(self.config.get('instruments', {}).get('spectrum_analyzer', {}).get('enabled', True))  # 从配置加载启用状态
        self.sa_enabled.toggled.connect(self.on_instrument_enabled_changed)
        connection_layout.addWidget(self.sa_enabled, 1, 2)
        
        # 电源配置
        connection_layout.addWidget(QLabel("电源1地址:"), 2, 0)
        self.ps1_address = QLineEdit(self.config.get('instruments', {}).get('power_supplies', {}).get('PS1', {}).get('address', ''))
        self.ps1_address.setMinimumWidth(350)
        connection_layout.addWidget(self.ps1_address, 2, 1)
        self.ps1_enabled = QCheckBox("启用")
        self.ps1_enabled.setChecked(self.config.get('instruments', {}).get('power_supplies', {}).get('PS1', {}).get('enabled', True))
        self.ps1_enabled.toggled.connect(self.on_instrument_enabled_changed)
        connection_layout.addWidget(self.ps1_enabled, 2, 2)
        
        connection_layout.addWidget(QLabel("电源2地址:"), 3, 0)
        self.ps2_address = QLineEdit(self.config.get('instruments', {}).get('power_supplies', {}).get('PS2', {}).get('address', ''))
        self.ps2_address.setMinimumWidth(350)
        connection_layout.addWidget(self.ps2_address, 3, 1)
        self.ps2_enabled = QCheckBox("启用")
        self.ps2_enabled.setChecked(self.config.get('instruments', {}).get('power_supplies', {}).get('PS2', {}).get('enabled', True))
        self.ps2_enabled.toggled.connect(self.on_instrument_enabled_changed)
        connection_layout.addWidget(self.ps2_enabled, 3, 2)
        
        connection_layout.addWidget(QLabel("电源3地址:"), 4, 0)
        self.ps3_address = QLineEdit(self.config.get('instruments', {}).get('power_supplies', {}).get('PS3', {}).get('address', ''))
        self.ps3_address.setMinimumWidth(350)
        connection_layout.addWidget(self.ps3_address, 4, 1)
        self.ps3_enabled = QCheckBox("启用")
        self.ps3_enabled.setChecked(self.config.get('instruments', {}).get('power_supplies', {}).get('PS3', {}).get('enabled', False))
        self.ps3_enabled.toggled.connect(self.on_instrument_enabled_changed)
        connection_layout.addWidget(self.ps3_enabled, 4, 2)
        
        connection_layout.addWidget(QLabel("电源4地址:"), 5, 0)
        self.ps4_address = QLineEdit(self.config.get('instruments', {}).get('power_supplies', {}).get('PS4', {}).get('address', ''))
        self.ps4_address.setMinimumWidth(350)
        connection_layout.addWidget(self.ps4_address, 5, 1)
        self.ps4_enabled = QCheckBox("启用")
        self.ps4_enabled.setChecked(self.config.get('instruments', {}).get('power_supplies', {}).get('PS4', {}).get('enabled', False))
        self.ps4_enabled.toggled.connect(self.on_instrument_enabled_changed)
        connection_layout.addWidget(self.ps4_enabled, 5, 2)
        
        # 连接按钮
        self.connect_btn = QPushButton("连接仪器")
        self.connect_btn.clicked.connect(tab.connect_requested.emit)
        connection_layout.addWidget(self.connect_btn, 6, 0, 1, 2)
        
        # 创建主要内容的水平布局
        main_content_widget = QWidget()
        main_content_layout = QHBoxLayout(main_content_widget)
        main_content_layout.setContentsMargins(0, 0, 0, 0)
        
        # 左侧区域 - 包含仪器连接和测试参数
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 10, 0)
        
        left_layout.addWidget(connection_group)
        
        # 测试参数配置组
        params_group = QGroupBox("测试参数配置")
        params_layout = QGridLayout(params_group)
        
        # 设置列宽比例 - 与上面的连接配置保持一致
        params_layout.setColumnStretch(0, 1)  # 标签列
        params_layout.setColumnStretch(1, 3)  # 输入框列，更宽
        
        # 测试频率
        params_layout.addWidget(QLabel("测试频率 (GHz):"), 0, 0)
        self.freq_edit = QLineEdit(str(self.config.get('test_frequencies', [4.0, 4.4, 4.8, 5.2, 5.6, 5.8])))
        self.freq_edit.setMinimumWidth(350)  # 保持与上面一致的宽度
        params_layout.addWidget(self.freq_edit, 0, 1)
        
        # 功率范围
        params_layout.addWidget(QLabel("起始功率 (dBm):"), 1, 0)
        self.start_power = QDoubleSpinBox()
        self.start_power.setRange(-50.0, 20.0)
        self.start_power.setDecimals(1)  # 支持小数点后一位
        self.start_power.setValue(self.config.get('signal_source', {}).get('start_power', -38))
        self.start_power.setMinimumWidth(150)
        params_layout.addWidget(self.start_power, 1, 1)
        
        params_layout.addWidget(QLabel("结束功率 (dBm):"), 2, 0)
        self.stop_power = QDoubleSpinBox()
        self.stop_power.setRange(-50.0, 20.0)
        self.stop_power.setDecimals(1)  # 支持小数点后一位
        self.stop_power.setValue(self.config.get('signal_source', {}).get('stop_power', -16))
        self.stop_power.setMinimumWidth(150)
        params_layout.addWidget(self.stop_power, 2, 1)
        
        params_layout.addWidget(QLabel("功率步长 (dB):"), 3, 0)
        self.power_step = QDoubleSpinBox()
        self.power_step.setRange(0.1, 5.0)
        self.power_step.setValue(self.config.get('signal_source', {}).get('step', 1))
        self.power_step.setMinimumWidth(150)
        params_layout.addWidget(self.power_step, 3, 1)
        
        # 压缩点选择
        params_layout.addWidget(QLabel("压缩点:"), 4, 0)
        self.compression_combo = QComboBox()
        self.compression_combo.addItems(["1dB", "3dB", "5dB"])
        self.compression_combo.setCurrentText(self.config.get('compression_point', {}).get('type', '5dB'))
        self.compression_combo.setMinimumWidth(150)
        params_layout.addWidget(self.compression_combo, 4, 1)
        
        # 衰减器选择
        params_layout.addWidget(QLabel("衰减器:"), 5, 0)
        self.attenuator_combo = QComboBox()
        self.attenuator_combo.addItems(["30dB", "40dB"])
        self.attenuator_combo.setCurrentText(self.config.get('attenuator', {}).get('type', '40dB'))
        self.attenuator_combo.setMinimumWidth(150)
        params_layout.addWidget(self.attenuator_combo, 5, 1)
        
        # 驱动模式
        self.driver_mode_check = QCheckBox("启用驱动功放模式")
        self.driver_mode_check.setChecked(self.config.get('driver_mode', {}).get('enabled', True))
        self.driver_mode_check.toggled.connect(self.on_driver_mode_toggled)
        params_layout.addWidget(self.driver_mode_check, 6, 0, 1, 2)
        
        # DUT最大输入功率保护
        params_layout.addWidget(QLabel("DUT最大输入功率 (dBm):"), 7, 0)
        self.max_input_power = QDoubleSpinBox()
        self.max_input_power.setRange(0, 50)
        self.max_input_power.setValue(self.config.get('dut_config', {}).get('max_input_power', 33.5))
        self.max_input_power.setMinimumWidth(150)
        params_layout.addWidget(self.max_input_power, 7, 1)
        
        # PA单元数量配置
        params_layout.addWidget(QLabel("PA单元数量:"), 8, 0)
        self.pa_unit_count_combo = QComboBox()
        self.pa_unit_count_combo.addItems(["1", "2", "3"])
        # 从配置中获取当前的PA单元数量，默认为2
        current_count = self.config.get('dut_config', {}).get('power_supply_count', 2)
        self.pa_unit_count_combo.setCurrentText(str(current_count))
        self.pa_unit_count_combo.currentTextChanged.connect(self.on_pa_unit_count_changed)
        self.pa_unit_count_combo.setMinimumWidth(150)
        params_layout.addWidget(self.pa_unit_count_combo, 8, 1)
        
        left_layout.addWidget(params_group)
        
        # 添加左侧区域到主布局
        main_content_layout.addWidget(left_widget)
        
        # 右侧电源配置区域
        right_power_widget = QWidget()
        right_power_layout = QVBoxLayout(right_power_widget)
        right_power_layout.setContentsMargins(10, 10, 10, 10)
        
        # 电源配置组
        self.create_power_supply_config(right_power_layout)
        
        # 电源分配组  
        self.create_power_assignment_config(right_power_layout)
        
        # 添加右侧区域到主布局
        main_content_layout.addWidget(right_power_widget)
        
        # 将主内容添加到滚动布局
        scroll_layout.addWidget(main_content_widget)
        
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)
        
        return tab'''
    
    '''Legacy configuration helpers removed in Phase 3.2; ConfigPage owns this UI.

    def create_power_supply_config(self, parent_layout):
        """创建电源详细配置"""
        power_group = QGroupBox("电源详细配置")
        power_layout = QVBoxLayout(power_group)
        
        # 创建电源配置的标签页
        power_tabs = QTabWidget()
        
        # 存储电源配置控件的字典
        self.power_config_widgets = {}
        
        # 设置电源配置的白色背景样式
        power_tabs.setStyleSheet("""
            QTabWidget::pane {
                background-color: white;
                border: 1px solid #ccc;
            }
            QTabBar::tab {
                background-color: #f0f0f0;
                color: black;
                padding: 8px 16px;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background-color: white;
                color: black;
                border-bottom: 2px solid #0078d4;
            }
            QTabBar::tab:hover {
                background-color: #e0e0e0;
            }
        """)
        
        for ps_name in ['PS1', 'PS2', 'PS3', 'PS4']:
            tab_widget = QWidget()
            tab_widget.setStyleSheet("""
                QWidget {
                    background-color: white;
                    color: black;
                }
                QLabel {
                    color: black;
                }
                QGroupBox {
                    background-color: #f9f9f9;
                    border: 1px solid #ccc;
                    border-radius: 5px;
                    margin-top: 10px;
                    padding-top: 10px;
                    color: black;
                    font-weight: bold;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 10px;
                    padding: 0 5px 0 5px;
                }
            """)
            tab_layout = QVBoxLayout(tab_widget)
            
            # 电源地址配置
            addr_layout = QHBoxLayout()
            addr_layout.addWidget(QLabel(f"{ps_name}地址:"))
            addr_edit = QLineEdit(self.config.get('instruments', {}).get('power_supplies', {}).get(ps_name, {}).get('address', ''))
            addr_edit.setMinimumWidth(300)
            addr_edit.setReadOnly(True)  # 设置为只读
            addr_edit.setStyleSheet("background-color: #f0f0f0; color: #555; border: 1px solid #ccc; border-radius: 3px; padding: 2px;")
            
            # 绑定主界面地址框的更改信号，实现实时单向同步
            main_addr_widget = getattr(self, f"{ps_name.lower()}_address", None)
            if main_addr_widget:
                addr_edit.setText(main_addr_widget.text())
                main_addr_widget.textChanged.connect(addr_edit.setText)
                
            addr_layout.addWidget(addr_edit)
            addr_layout.addStretch()
            tab_layout.addLayout(addr_layout)
            
            # 通道配置
            channels_group = QGroupBox("通道配置")
            channels_layout = QVBoxLayout(channels_group)
            
            ps_config = {}
            ps_config['address'] = addr_edit
            ps_config['channels'] = {}
            
            for ch_name in ['CH1', 'CH2']:
                ch_group = QGroupBox(f"通道 {ch_name}")
                ch_layout = QGridLayout(ch_group)
                
                ch_config = self.config.get('instruments', {}).get('power_supplies', {}).get(ps_name, {}).get('channels', {}).get(ch_name, {})
                
                # 电压配置
                ch_layout.addWidget(QLabel("电压 (V):"), 0, 0)
                voltage_spin = QDoubleSpinBox()
                voltage_spin.setRange(0, 50)
                voltage_spin.setDecimals(2)
                voltage_spin.setValue(ch_config.get('voltage', {}).get('value', 0))
                ch_layout.addWidget(voltage_spin, 0, 1)
                
                ch_layout.addWidget(QLabel("保护电压 (V):"), 0, 2)
                voltage_prot_spin = QDoubleSpinBox()
                voltage_prot_spin.setRange(0, 50)
                voltage_prot_spin.setDecimals(2)
                voltage_prot_spin.setValue(ch_config.get('voltage', {}).get('protection', 0))
                ch_layout.addWidget(voltage_prot_spin, 0, 3)
                
                voltage_prot_check = QCheckBox("启用电压保护")
                voltage_prot_check.setChecked(ch_config.get('voltage', {}).get('protection_enabled', False))
                ch_layout.addWidget(voltage_prot_check, 0, 4)
                
                # 电流配置
                ch_layout.addWidget(QLabel("电流 (A):"), 1, 0)
                current_spin = QDoubleSpinBox()
                current_spin.setRange(0, 10)
                current_spin.setDecimals(2)
                current_spin.setValue(ch_config.get('current', {}).get('value', 0))
                ch_layout.addWidget(current_spin, 1, 1)
                
                ch_layout.addWidget(QLabel("保护电流 (A):"), 1, 2)
                current_prot_spin = QDoubleSpinBox()
                current_prot_spin.setRange(0, 10)
                current_prot_spin.setDecimals(2)
                current_prot_spin.setValue(ch_config.get('current', {}).get('protection', 0))
                ch_layout.addWidget(current_prot_spin, 1, 3)
                
                current_prot_check = QCheckBox("启用电流保护")
                current_prot_check.setChecked(ch_config.get('current', {}).get('protection_enabled', False))
                ch_layout.addWidget(current_prot_check, 1, 4)
                
                channels_layout.addWidget(ch_group)
                
                # 保存通道控件引用
                ps_config['channels'][ch_name] = {
                    'voltage': voltage_spin,
                    'voltage_protection': voltage_prot_spin,
                    'voltage_protection_enabled': voltage_prot_check,
                    'current': current_spin,
                    'current_protection': current_prot_spin,
                    'current_protection_enabled': current_prot_check
                }
            
            tab_layout.addWidget(channels_group)
            tab_layout.addStretch()
            
            power_tabs.addTab(tab_widget, ps_name)
            
            # 保存电源配置控件引用
            self.power_config_widgets[ps_name] = ps_config
        
        power_layout.addWidget(power_tabs)
        parent_layout.addWidget(power_group)
    
    def create_power_assignment_config(self, parent_layout):
        """创建电源分配配置"""
        assignment_group = QGroupBox("电源分配设置")
        assignment_group.setStyleSheet("""
            QGroupBox {
                background-color: white;
                border: 1px solid #ccc;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                color: black;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
            QLabel {
                color: black;
            }
        """)
        assignment_layout = QVBoxLayout(assignment_group)
        
        # 驱动功放电源配置
        driver_group = QGroupBox("驱动功放电源")
        driver_group.setStyleSheet("""
            QGroupBox {
                background-color: white;
                border: 1px solid #ccc;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
                color: black;
                font-weight: bold;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px 0 5px;
            }
            QGroupBox:disabled {
                color: #999;
                border-color: #ddd;
            }
            QLabel {
                color: black;
            }
            QLabel:disabled {
                color: #999;
            }
        """)
        driver_layout = QGridLayout(driver_group)
        
        # 驱动功放电源启用选项
        self.driver_power_enabled = QCheckBox("启用驱动功放电源")
        driver_assignment = self.config.get('power_supply_assignment', {}).get('driver_amplifier', {})
        enabled = driver_assignment.get('power_supply_count', 0) > 0
        self.driver_power_enabled.setChecked(enabled)
        self.driver_power_enabled.toggled.connect(self.on_driver_power_toggled)
        driver_layout.addWidget(self.driver_power_enabled, 0, 0, 1, 2)
        
        # 驱动功放电源选择
        self.driver_power_label = QLabel("驱动功放电源:")
        driver_layout.addWidget(self.driver_power_label, 1, 0)
        self.driver_power_combo = QComboBox()
        self.driver_power_combo.addItems(["PS1", "PS2", "PS3", "PS4"])
        current_driver_ps = driver_assignment.get('supplies', {}).get('main', {}).get('name', 'PS1')
        self.driver_power_combo.setCurrentText(current_driver_ps)
        driver_layout.addWidget(self.driver_power_combo, 1, 1)
        
        assignment_layout.addWidget(driver_group)
        
        # DUT电源配置
        dut_group = QGroupBox("DUT功放电源分配")
        dut_layout = QGridLayout(dut_group)
        
        dut_assignment = self.config.get('power_supply_assignment', {}).get('dut_amplifier', {})
        
        # PA Unit1电源
        dut_layout.addWidget(QLabel("PA Unit1电源:"), 0, 0)
        self.pa_unit1_power_combo = QComboBox()
        self.pa_unit1_power_combo.addItems(["PS1", "PS2", "PS3", "PS4"])
        unit1_ps = dut_assignment.get('supplies', {}).get('carrier', {}).get('name', 'PS2')
        self.pa_unit1_power_combo.setCurrentText(unit1_ps)
        dut_layout.addWidget(self.pa_unit1_power_combo, 0, 1)
        
        # PA Unit2电源
        self.pa_unit2_label = QLabel("PA Unit2电源:")
        dut_layout.addWidget(self.pa_unit2_label, 1, 0)
        self.pa_unit2_power_combo = QComboBox()
        self.pa_unit2_power_combo.addItems(["PS1", "PS2", "PS3", "PS4"])
        unit2_ps = dut_assignment.get('supplies', {}).get('peaking', {}).get('name', 'PS3')
        self.pa_unit2_power_combo.setCurrentText(unit2_ps)
        dut_layout.addWidget(self.pa_unit2_power_combo, 1, 1)
        
        # PA Unit3电源（3个PA单元时使用）
        self.pa_unit3_label = QLabel("PA Unit3电源:")
        dut_layout.addWidget(self.pa_unit3_label, 2, 0)
        self.pa_unit3_power_combo = QComboBox()
        self.pa_unit3_power_combo.addItems(["PS1", "PS2", "PS3", "PS4"])
        # 从配置中读取peaking2的设置，如果存在的话
        unit3_ps = dut_assignment.get('supplies', {}).get('peaking2', {}).get('name', 'PS4')
        self.pa_unit3_power_combo.setCurrentText(unit3_ps)
        dut_layout.addWidget(self.pa_unit3_power_combo, 2, 1)
        
        assignment_layout.addWidget(dut_group)
        
        # 保存电源分配控件引用
        self.power_assignment_widgets = {
            'driver_enabled': self.driver_power_enabled,
            'driver_power': self.driver_power_combo,
            'pa_unit1_power': self.pa_unit1_power_combo,
            'pa_unit2_power': self.pa_unit2_power_combo,
            'pa_unit2_label': self.pa_unit2_label,
            'pa_unit3_power': self.pa_unit3_power_combo,
            'pa_unit3_label': self.pa_unit3_label
        }
        
        parent_layout.addWidget(assignment_group)
        
        # 保存配置按钮
        self.save_config_btn = QPushButton("保存配置")
        self.save_config_btn.clicked.connect(tab.save_requested.emit)
        self.save_config_btn.setMinimumHeight(35)
        parent_layout.addWidget(self.save_config_btn)
        
        # 初始化界面状态
        self.update_pa_unit_ui()
        self.update_driver_power_ui()
        self.update_power_supply_options()
    
    def on_pa_unit_count_changed(self, count_str):
        """PA单元数量改变时的处理"""
        self.update_pa_unit_ui()
    
    def update_pa_unit_ui(self):
        """更新PA单元相关的UI状态"""
        pa_unit_count = int(self.pa_unit_count_combo.currentText())
        
        # 根据PA单元数量显示/隐藏对应的电源选项
        if hasattr(self, 'power_assignment_widgets'):
            # PA Unit2在单元数量>=2时显示
            show_unit2 = pa_unit_count >= 2
            self.power_assignment_widgets['pa_unit2_power'].setVisible(show_unit2)
            self.power_assignment_widgets['pa_unit2_label'].setVisible(show_unit2)
            
            # PA Unit3在单元数量>=3时显示
            show_unit3 = pa_unit_count >= 3
            self.power_assignment_widgets['pa_unit3_power'].setVisible(show_unit3)
            self.power_assignment_widgets['pa_unit3_label'].setVisible(show_unit3)
    
    def on_driver_mode_toggled(self, checked):
        """驱动功放模式切换时的处理"""
        self.update_driver_power_ui()
        # 更新功放测试连接说明
        if hasattr(self, 'instruction_text'):
            self.update_amplifier_instruction_text()
    
    def on_driver_power_toggled(self, checked):
        """驱动功放电源启用切换时的处理"""
        self.update_driver_power_ui()
    
    def update_driver_power_ui(self):
        """更新驱动功放电源相关的UI状态"""
        # 首先检查驱动功放模式是否启用
        driver_mode_enabled = self.driver_mode_check.isChecked()
        driver_power_enabled = self.driver_power_enabled.isChecked()
        
        # 只有在驱动功放模式启用时，驱动功放电源配置才能生效
        final_enabled = driver_mode_enabled and driver_power_enabled
        
        # 设置驱动功放电源相关控件的启用状态
        # 驱动功放电源启用勾选框本身受驱动功放模式控制
        self.driver_power_enabled.setEnabled(driver_mode_enabled)
        
        # 驱动功放电源选择受两个条件控制
        self.driver_power_combo.setEnabled(final_enabled)
        self.driver_power_label.setEnabled(final_enabled)
        
        # 如果驱动功放电源被禁用，清空选择
        if not final_enabled:
            self.driver_power_combo.setCurrentIndex(-1)  # 清空选择
        
        # 注意：我们不再禁用电源详细配置界面，因为那些电源可能被其他功放使用
        # 电源详细配置界面保持启用状态，让用户可以配置用于待测功放的电源
    
    def on_instrument_enabled_changed(self):
        """仪器启用状态改变时的处理"""
        self.update_power_supply_options()
        
    def get_enabled_power_supplies(self):
        """获取已启用的电源列表"""
        enabled_ps = []
        for ps_name, checkbox in [('PS1', self.ps1_enabled), ('PS2', self.ps2_enabled), 
                                  ('PS3', self.ps3_enabled), ('PS4', self.ps4_enabled)]:
            if checkbox.isChecked():
                enabled_ps.append(ps_name)
        return enabled_ps
    
    def update_power_supply_options(self):
        """更新电源分配下拉列表的选项"""
        enabled_ps = self.get_enabled_power_supplies()
        
        if hasattr(self, 'power_assignment_widgets'):
            # 更新驱动功放电源选择
            current_driver = self.power_assignment_widgets['driver_power'].currentText()
            self.power_assignment_widgets['driver_power'].clear()
            self.power_assignment_widgets['driver_power'].addItems(enabled_ps)
            
            # 只有在驱动功放电源启用时才恢复选择
            driver_mode_enabled = self.driver_mode_check.isChecked()
            driver_power_enabled = self.driver_power_enabled.isChecked()
            if driver_mode_enabled and driver_power_enabled and current_driver in enabled_ps:
                self.power_assignment_widgets['driver_power'].setCurrentText(current_driver)
            
            # 更新PA Unit电源选择
            current_unit1 = self.power_assignment_widgets['pa_unit1_power'].currentText()
            self.power_assignment_widgets['pa_unit1_power'].clear()
            self.power_assignment_widgets['pa_unit1_power'].addItems(enabled_ps)
            if current_unit1 in enabled_ps:
                self.power_assignment_widgets['pa_unit1_power'].setCurrentText(current_unit1)
                
            current_unit2 = self.power_assignment_widgets['pa_unit2_power'].currentText()
            self.power_assignment_widgets['pa_unit2_power'].clear()
            self.power_assignment_widgets['pa_unit2_power'].addItems(enabled_ps)
            if current_unit2 in enabled_ps:
                self.power_assignment_widgets['pa_unit2_power'].setCurrentText(current_unit2)
                
            current_unit3 = self.power_assignment_widgets['pa_unit3_power'].currentText()
            self.power_assignment_widgets['pa_unit3_power'].clear()
            self.power_assignment_widgets['pa_unit3_power'].addItems(enabled_ps)
            if current_unit3 in enabled_ps:
                self.power_assignment_widgets['pa_unit3_power'].setCurrentText(current_unit3)
        
    '''
    def _prepare_cable_loss_run(self):
        if self.instrument_ctrl is None:
            self.on_measurement_error("请先连接仪器；上一次测量结束后端口已安全释放")
            return False
        return self.update_and_save_config()

    def _confirm_cable_loss_wiring(self):
        self.config.setdefault('wiring', {})
        self.config['wiring'].update({
            'confirmed': True,
            'connection_note': '已通过线损测量连接确认对话框确认现场接线',
            'confirmed_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'confirmation_source': 'cable_loss_path1_dialog',
        })
        if self._save_config_file():
            self.add_log_message("现场接线确认已保存")
            self.measurement_controller.set_measurement_port(self.instrument_ctrl)
            return True
        self.add_log_message(f"配置保存失败: {self._last_save_error}")
        return False

    def _on_measurement_controller_thread_finished(self):
        if self.measurement_controller.state.kind not in {
            MeasurementKind.CABLE_LOSS,
            MeasurementKind.DRIVER_MAPPING,
            MeasurementKind.AMPLIFIER,
        }:
            return
        if self.instrument_ctrl is not None:
            try:
                self._close_instrument_port(self.instrument_ctrl)
            except Exception as error:
                self.add_log_message(f"仪器清理失败: {error}")
            self.instrument_ctrl = None
        
    def _store_driver_mapping_realtime_data(self, data):
        """保留聊天上下文需要的实时数据，不接管页面绘图。"""
        if not isinstance(data, dict) or "frequency" not in data:
            return
        buffer = getattr(self, "realtime_buffer", None)
        if buffer is None:
            frequency_value = normalize_frequency(data["frequency"])
            if frequency_value is None:
                return
            frequency = str(frequency_value)
            self.real_time_data[frequency] = data.copy()
            if frequency not in [str(value) for value in self.rt_frequency_list]:
                self.rt_frequency_list.append(frequency_value)
                self.rt_frequency_list.sort()
                if not self.rt_user_browsing:
                    self.rt_current_freq_index = len(self.rt_frequency_list) - 1
            return
        buffer.store(data)

    def _prepare_driver_mapping_run(self):
        if self.instrument_ctrl is None:
            self.on_measurement_error("请先连接仪器；上一次测量结束后端口已安全释放")
            return False
        return self.update_and_save_config()

    def _confirm_driver_mapping_wiring(self):
        self.config.setdefault("wiring", {})
        self.config["wiring"].update({
            "confirmed": True,
            "connection_note": "已通过驱动功放映射连接确认对话框确认现场接线",
            "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "confirmation_source": "driver_mapping_dialog",
        })
        if self._save_config_file():
            self.add_log_message("现场接线确认已保存")
            self.measurement_controller.set_measurement_port(self.instrument_ctrl)
            return True
        self.add_log_message(f"配置保存失败: {self._last_save_error}")
        return False
        
    def _prepare_amplifier_run(self):
        if self.instrument_ctrl is None:
            self.on_measurement_error("请先连接仪器；上一次测量结束后端口已安全释放")
            return False
        return self.update_and_save_config()

    def _confirm_amplifier_wiring(self):
        self.config.setdefault("wiring", {})
        self.config["wiring"].update({
            "confirmed": True,
            "connection_note": "已通过主功放测试连接确认对话框确认现场接线",
            "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "confirmation_source": "amplifier_test_dialog",
        })
        if self._save_config_file():
            self.add_log_message("现场接线确认已保存")
            self.measurement_controller.set_measurement_port(self.instrument_ctrl)
            return True
        self.add_log_message(f"配置保存失败: {self._last_save_error}")
        return False

    def _store_amplifier_realtime_data(self, data):
        """保留功放实时数据供聊天上下文使用，不接管页面绘图。"""
        if not isinstance(data, dict) or "frequency" not in data:
            return
        buffer = getattr(self, "realtime_buffer", None)
        if buffer is None:
            frequency_value = normalize_frequency(data["frequency"])
            if frequency_value is None:
                return
            frequency = str(frequency_value)
            self.real_time_data[frequency] = data.copy()
            if frequency not in [str(value) for value in self.rt_frequency_list]:
                self.rt_frequency_list.append(frequency_value)
                self.rt_frequency_list.sort()
                if not self.rt_user_browsing:
                    self.rt_current_freq_index = len(self.rt_frequency_list) - 1
            return
        buffer.store(data)

    def _current_realtime_page(self):
        controller = getattr(self, "measurement_controller", None)
        kind = getattr(getattr(controller, "state", None), "kind", None)
        if kind is MeasurementKind.DRIVER_MAPPING:
            return getattr(self, "driver_mapping_page", None)
        if kind is MeasurementKind.AMPLIFIER:
            return getattr(self, "amplifier_page", None)
        for name in ("driver_mapping_page", "amplifier_page"):
            page = getattr(self, name, None)
            if page is not None:
                return page
        return None
        
    def create_visualization_tab(self):
        """创建数据可视化选项卡"""
        self.visualization_page = VisualizationPage(
            config_provider=lambda: self.config,
            temp_dir=TEMP_DIR,
            results_dir=TEST_RESULTS_DIR,
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
            results_dir=TEST_RESULTS_DIR,
            cable_loss_file=CABLE_LOSS_FILE,
            visualizer_factory=DataVisualization,
            log_callback=self.add_log_message,
            parent=self,
        )
        self.file_table = self.export_page.file_table
        self.export_json_btn = self.export_page.export_json_btn
        self.export_csv_btn = self.export_page.export_csv_btn
        self.export_pdf_btn = self.export_page.export_pdf_btn
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
        
    def show_connection_diagram(self, diagram_type: str):
        """显示连接图"""
        dialog = ConnectionDialog(diagram_type, self)
        dialog.exec()
        
    def show_amplifier_connection_diagram(self):
        """显示功放测试连接图，根据驱动模式选择不同图"""
        # 检查当前驱动模式设置
        driver_enabled = self.driver_mode_check.isChecked()
        diagram_type = 'amplifier_test' if driver_enabled else 'amplifier_test_no_driver'
        dialog = ConnectionDialog(diagram_type, self)
        dialog.exec()
        
    def update_amplifier_instruction_text(self):
        """根据驱动模式更新功放测试连接说明"""
        driver_enabled = self.driver_mode_check.isChecked()
        if driver_enabled:
            instruction_html = """
            <h3>主功放测试连接说明:</h3>
            <p>信号源 → 线缆① → 驱动功放 → 线缆③ → 主功放 → 线缆④ → 衰减器 → 线缆② → 频谱仪</p>
            """
        else:
            instruction_html = """
            <h3>主功放测试连接说明（无驱动模式）:</h3>
            <p>信号源 → 线缆① → 主功放 → 线缆④ → 衰减器 → 线缆② → 频谱仪</p>
            """
        self.instruction_text.setHtml(instruction_html)
        
    def add_log_message(self, message: str):
        self.status_panel.add_log_message(message)
        
    def on_log_scroll_changed(self, value):
        self.status_panel.on_log_scroll_changed(value)
            
    def clear_log(self):
        self.status_panel.clear_log()
        
    def update_status(self):
        self.status_panel.update_status()
        
    def connect_instruments(self):
        """连接仪器"""
        self.add_log_message("开始连接仪器...")
        self.connect_btn.setEnabled(False)
        
        # 更新配置并保存到文件
        if not self.update_and_save_config():
            self.connect_btn.setEnabled(True)
            return
        
        # 启动仪器连接工作线程
        self.instrument_worker = InstrumentWorker(str(self.config_path))
        self.instrument_worker.signals.finished.connect(self.on_instrument_connected)
        self.instrument_worker.signals.result.connect(self.on_instrument_controller_ready)
        self.instrument_worker.signals.stopped.connect(self.on_instrument_stopped)
        self.instrument_worker.signals.error.connect(self.on_instrument_error)
        self.instrument_worker.signals.message.connect(self.add_log_message)
        self.instrument_worker.signals.progress.connect(self.progress_bar.setValue)
        self.instrument_worker.start()
        
    def on_instrument_controller_ready(self, controller):
        """Keep the connected controller so window shutdown can clean it up."""
        if self.instrument_ctrl is not None and self.instrument_ctrl is not controller:
            self._close_instrument_port(self.instrument_ctrl)
        self.instrument_ctrl = controller

    @staticmethod
    def _close_instrument_port(port):
        close = getattr(port, "close_all", None)
        if close is not None:
            return close(close_rf=True)
        shutdown = getattr(port, "safe_shutdown", None)
        if shutdown is not None:
            return shutdown()
        return None

    def on_instrument_connected(self):
        """仪器连接完成"""
        self.connect_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.add_log_message("所有仪器连接成功！")
        
    def on_measurement_error(self, error_message):
        """显示 controller 转发的测量错误；端口生命周期由 controller 统一收尾。"""
        self.add_log_message(f"错误: {error_message}")
        QMessageBox.critical(self, "错误", error_message)

    def on_instrument_error(self, error_message):
        """处理仪器连接 worker 的错误。"""
        self.add_log_message(f"错误: {error_message}")
        QMessageBox.critical(self, "错误", error_message)
        
        # 连接 worker 失败时只恢复连接控件；测量页面由 controller 状态驱动。
        self.connect_btn.setEnabled(True)
        
        # 重置进度条
        self.progress_bar.setValue(0)
        if self.instrument_ctrl is not None:
            try:
                self._close_instrument_port(self.instrument_ctrl)
            except Exception as error:
                self.add_log_message(f"仪器清理失败: {error}")
            self.instrument_ctrl = None

    def on_instrument_stopped(self, reason):
        """处理仪器连接 worker 的停止。"""
        self.add_log_message(f"仪器连接线程已停止: {reason}")
        self.connect_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        if self.instrument_ctrl is not None:
            try:
                self._close_instrument_port(self.instrument_ctrl)
            except Exception as error:
                self.add_log_message(f"仪器清理失败: {error}")
            self.instrument_ctrl = None
        
    def _read_instrument_config_from_ui(self) -> dict:
        """从UI读取仪器地址和启用状态，保留现有通道和其他字段。"""
        if hasattr(self, 'config_page'):
            return ConfigFormState(self.config, self.config_page).read_instrument_config()
        return ConfigFormState(self.config, self).read_instrument_config()

    def _read_test_parameters_from_ui(self) -> dict:
        """从UI读取测试参数，不修改self.config。"""
        if hasattr(self, 'config_page'):
            return ConfigFormState(self.config, self.config_page).read_test_parameters()
        return ConfigFormState(self.config, self).read_test_parameters()

    def _read_power_supply_config_from_ui(self) -> dict:
        """从UI读取电源通道详细配置，按电源名称分组返回。"""
        if hasattr(self, 'config_page'):
            return ConfigFormState(self.config, self.config_page).read_power_supply_config()
        return ConfigFormState(self.config, self).read_power_supply_config()

    def _read_power_assignment_from_ui(self) -> dict:
        """从UI读取电源分配配置，返回完整的power_supply_assignment片段。"""
        if hasattr(self, 'config_page'):
            return ConfigFormState(self.config, self.config_page).read_power_assignment()
        return ConfigFormState(self.config, self).read_power_assignment()

    def _build_config_from_ui(self) -> dict:
        """基于 self.config 和当前 UI 状态组装完整配置字典。

        该方法不修改 self.config，不写文件，不记录日志，不弹窗。
        """
        widgets = self.config_page if hasattr(self, 'config_page') else self
        return ConfigFormState(self.config, widgets).build_config()

    def update_config_from_ui(self):
        """从UI更新配置"""
        self.config = self._build_config_from_ui()
        if hasattr(self, 'config_page'):
            self.config_page.config = self.config

    def _save_config_file(self) -> bool:
        """将 self.config 写入 CONFIG_FILE，成功返回 True，失败返回 False。"""
        try:
            with open(getattr(self, "config_path", CONFIG_FILE), 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            self._last_save_error = e
            return False
        
    def update_and_save_config(self):
        """更新UI配置并保存到文件"""
        self.update_config_from_ui()
        if self._save_config_file():
            self.add_log_message("配置已更新并保存")
            return True
        else:
            self.add_log_message(f"配置保存失败: {self._last_save_error}")
            return False
        
    def save_config(self):
        """保存配置"""
        self.update_config_from_ui()
        if self._save_config_file():
            self.add_log_message("配置已保存")
            QMessageBox.information(self, "保存成功", "配置文件已保存")
        else:
            self.add_log_message(f"配置保存失败: {self._last_save_error}")
            QMessageBox.warning(self, "保存失败", f"配置保存失败: {self._last_save_error}")
            
    def load_test_data(self):
        """加载测试数据"""
        return self.visualization_page.load_test_data()
    
    def update_frequency_display(self):
        """更新频率显示"""
        return self.visualization_page.update_frequency_display()
    
    def display_current_frequency_data(self):
        """显示当前频率的数据"""
        return self.visualization_page.display_current_frequency_data()
    
    def prev_frequency(self):
        """切换到上一个频率"""
        return self.visualization_page.prev_frequency()
    
    def next_frequency(self):
        """切换到下一个频率"""
        return self.visualization_page.next_frequency()
    
    def store_real_time_data(self, data):
        """兼容入口：更新上下文并委托当前页面刷新实时图。"""
        if not isinstance(data, dict):
            return False
        page = self._current_realtime_page()
        if page is not None and getattr(page, "realtime_buffer", None) is not None:
            if not page.realtime_buffer.store(data):
                return False
            page._display_current_frequency()
        buffer = getattr(self, "realtime_buffer", None)
        return buffer.store(data) if buffer is not None else True
    
    def update_rt_frequency_display(self):
        """更新实时预览的频点显示"""
        # 现在频点信息直接显示在日志中，不需要更新UI标签
        pass
    
    def display_current_rt_frequency_data(self):
        page = self._current_realtime_page()
        if page is not None and hasattr(page, "_display_current_frequency"):
            page._display_current_frequency()
            return page.realtime_buffer.current()
        return self.realtime_buffer.current()
    
    def rt_prev_frequency(self):
        page = self._current_realtime_page()
        if page is not None and hasattr(page, "previous_frequency"):
            page.previous_frequency()
            return page.realtime_buffer.current()
        return self.realtime_buffer.previous()
    
    def rt_next_frequency(self):
        page = self._current_realtime_page()
        if page is not None and hasattr(page, "next_frequency"):
            page.next_frequency()
            return page.realtime_buffer.current()
        return self.realtime_buffer.next()
    
    def update_rt_nav_buttons(self):
        page = self._current_realtime_page()
        if page is not None and hasattr(page, "_update_navigation"):
            return page._update_navigation()
        return None

    def clear_real_time_data(self):
        """清除实时测量的历史数据"""
        buffer = getattr(self, "realtime_buffer", None)
        if buffer is not None:
            buffer.clear()
        for name in ("driver_mapping_page", "amplifier_page"):
            page = getattr(self, name, None)
            if page is not None and hasattr(page, "clear_realtime_buffer"):
                page.clear_realtime_buffer()
        self.add_log_message("已清除实时测量历史数据")
                
    def generate_report(self):
        """生成报告"""
        return self.visualization_page.generate_report()
            
    def refresh_file_list(self):
        """刷新文件列表"""
        return self.export_page.refresh_file_list()
                
    def export_json(self):
        """导出JSON"""
        return self.export_page.export_json()
        
    def export_csv(self):
        """导出CSV"""
        return self.export_page.export_csv()

    def export_pdf(self):
        """导出PDF"""
        return self.export_page.export_pdf()

    def closeEvent(self, event):
        """窗口关闭事件"""
        if self.measurement_controller.state.is_active:
            reply = QMessageBox.question(self, "退出", "测试正在进行中，确定要退出吗？",
                                       QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            if not self.measurement_controller.shutdown(timeout_ms=5000):
                event.ignore()
                return
        if self.instrument_worker and self.instrument_worker.isRunning():
            reply = QMessageBox.question(self, "退出", "测试正在进行中，确定要退出吗？",
                                       QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.instrument_worker.stop()
                if not self.instrument_worker.wait(5000):
                    self.add_log_message("仪器连接线程未能在关闭期限内停止")
                    event.ignore()
                    return
            else:
                event.ignore()
                return
        else:
            pass
        if self.instrument_ctrl is not None:
            try:
                self._close_instrument_port(self.instrument_ctrl)
            except Exception as error:
                self.add_log_message(f"仪器清理失败: {error}")
            self.instrument_ctrl = None
        if hasattr(self, "tab_widget"):
            try:
                self.tab_widget.currentChanged.disconnect(self._on_page_changed)
            except (RuntimeError, TypeError):
                pass
        for page in getattr(self, "pages", {}).values():
            page.close()
        event.accept()


def build_application(argv=None):
    """创建配置完成的 Qt 应用，不创建窗口或连接仪器。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv if argv is None else argv)
    try:
        setup_logging()
    except Exception as logging_error:
        print(f"日志初始化失败（忽略）: {logging_error}")
    app.setApplicationName("PA自动测试系统")
    app.setApplicationVersion("1.0")
    app.setOrganizationName("PA Test Lab")
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
    "ConnectionDialog", "RealTimePlotWidget", "ChatPanel", "ChatWorker",
    "ChatHistoryDialog", "ChatSettingsDialog", "ConfigFormState", "ConfigPage",
    "VisualizationPage", "ExportPage", "StatusPanel", "RealtimeMeasurementBuffer",
    "normalize_frequency", "MeasurementKind", "MeasurementResultReference",
]


if __name__ == "__main__":
    main()

