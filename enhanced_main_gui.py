"""
增强版GUI - 包含连接图显示和更多功能
"""

import sys
import os
import json
import time
import traceback
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import threading

from PySide6 import QtGui
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
    QTabWidget, QLabel, QLineEdit, QPushButton, QTextEdit, QGroupBox,
    QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox, QProgressBar,
    QTableWidget, QTableWidgetItem, QSplitter, QFrame, QGridLayout,
    QMessageBox, QFileDialog, QFormLayout, QScrollArea, QDialog,
    QListWidget
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QObject, QEvent, QSize
from PySide6.QtGui import QPixmap, QFont, QIcon, QTextCursor, QKeyEvent

import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import matplotlib
matplotlib.use('Qt5Agg')

# 设置matplotlib支持中文显示
import matplotlib.font_manager as fm
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'Arial Unicode MS', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题
plt.rcParams['axes.titleweight'] = 'bold'
# 清除字体缓存以确保设置生效
matplotlib.font_manager._get_font.cache_clear()

# 导入我们的测试模块和连接图
from app.gui_runtime import (
    connect_instruments,
)
from data_visualization import DataVisualization
from presentation.qt.pages import PageContext, build_pages
from presentation.qt.measurement_controller import MeasurementController
from presentation.qt.measurement_state import MeasurementKind, MeasurementResultReference
from presentation.qt.measurement_worker_factories import build_measurement_worker_factories
from presentation.qt.workers import BaseWorker, InstrumentWorker
import sys
import os
# 添加当前目录到Python路径，以便导入llm模块
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
from project_paths import (
    CABLE_LOSS_FILE,
    CONFIG_FILE,
    ICONS_DIR,
    PROJECT_ROOT,
    TEMP_DIR,
    TEST_RESULTS_DIR,
)
from config_io import load_config_file
from result_reading import load_measurement_result
from app_logging import setup_logging
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


class MainWindow(QMainWindow):
    """主窗口类"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PA自动测试系统 v1.0")
        self.setGeometry(100, 100, 1600, 1000)
        
        # 初始化变量
        self.config = {}
        self.instrument_ctrl = None
        self.instrument_worker = None
        self.measurement_controller = MeasurementController(
            build_measurement_worker_factories(), lambda: str(CONFIG_FILE)
        )
        self.measurement_controller.signals.thread_finished.connect(
            self._on_measurement_controller_thread_finished
        )
        self.measurement_controller.signals.finished.connect(self.refresh_file_list)
        self.measurement_controller.signals.message.connect(self.add_log_message)
        self.measurement_controller.signals.rejected.connect(self.add_log_message)
        
        # 数据可视化相关变量
        self.loaded_data = None
        self.frequency_list = []
        self.current_freq_index = 0
        
        # 实时图预览历史数据存储
        self.real_time_data = {}  # 存储实时测量的所有频点数据
        self.rt_frequency_list = []  # 实时测量的频点列表
        self.rt_current_freq_index = 0  # 当前显示的频点索引
        self.rt_user_browsing = False  # 用户是否在手动浏览历史数据
        self.log_user_scrolling = False  # 用户是否在手动滚动查看历史日志
        
        # 加载配置
        self.load_config()
        
        # 初始化UI
        self.init_ui()
        
        # 设置定时器用于日志更新
        self.log_timer = QTimer()
        self.log_timer.timeout.connect(self.update_status)
        self.log_timer.start(100)  # 100ms更新一次
        
    def load_config(self):
        """加载配置文件"""
        try:
            self.config = load_config_file(CONFIG_FILE)
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
            config_path_provider=lambda: str(CONFIG_FILE),
            prepare_cable_loss=self._prepare_cable_loss_run,
            confirm_cable_loss=self._confirm_cable_loss_wiring,
            prepare_driver_mapping=self._prepare_driver_mapping_run,
            confirm_driver_mapping=self._confirm_driver_mapping_wiring,
            prepare_amplifier=self._prepare_amplifier_run,
            confirm_amplifier=self._confirm_amplifier_wiring,
            driver_mode_provider=lambda: self.driver_mode_check.isChecked(),
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
                value=load_measurement_result(CABLE_LOSS_FILE),
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
        tab = QWidget()
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
        self.connect_btn.clicked.connect(self.connect_instruments)
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
        
        return tab
    
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
        self.save_config_btn.clicked.connect(self.save_config)
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
        frequency = str(data["frequency"])
        self.real_time_data[frequency] = data.copy()
        if frequency not in [str(value) for value in self.rt_frequency_list]:
            try:
                self.rt_frequency_list.append(float(frequency))
            except (TypeError, ValueError):
                return
            self.rt_frequency_list.sort()
            if not self.rt_user_browsing:
                self.rt_current_freq_index = len(self.rt_frequency_list) - 1

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
        frequency = str(data["frequency"])
        self.real_time_data[frequency] = data.copy()
        if frequency not in [str(value) for value in self.rt_frequency_list]:
            try:
                self.rt_frequency_list.append(float(frequency))
            except (TypeError, ValueError):
                return
            self.rt_frequency_list.sort()
            if not self.rt_user_browsing:
                self.rt_current_freq_index = len(self.rt_frequency_list) - 1
        
    def create_visualization_tab(self):
        """创建数据可视化选项卡"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # 控制面板
        control_group = QGroupBox("可视化控制")
        control_layout = QVBoxLayout(control_group)
        
        # 第一行：文件操作按钮
        file_layout = QHBoxLayout()
        self.load_data_btn = QPushButton("加载测试数据")
        self.load_data_btn.clicked.connect(self.load_test_data)
        file_layout.addWidget(self.load_data_btn)
        
        self.generate_report_btn = QPushButton("生成报告")
        self.generate_report_btn.clicked.connect(self.generate_report)
        file_layout.addWidget(self.generate_report_btn)
        
        control_layout.addLayout(file_layout)
        
        # 第二行：频率切换控件
        freq_layout = QHBoxLayout()
        freq_layout.addWidget(QLabel("频率切换:"))
        
        self.freq_prev_btn = QPushButton("◀ 上一个")
        self.freq_prev_btn.setEnabled(False)
        self.freq_prev_btn.clicked.connect(self.prev_frequency)
        freq_layout.addWidget(self.freq_prev_btn)
        
        self.freq_label = QLabel("未加载数据")
        self.freq_label.setAlignment(Qt.AlignCenter)
        self.freq_label.setStyleSheet("QLabel { background-color: #f0f0f0; padding: 5px; border: 1px solid #ccc; }")
        freq_layout.addWidget(self.freq_label)
        
        self.freq_next_btn = QPushButton("下一个 ▶")
        self.freq_next_btn.setEnabled(False)
        self.freq_next_btn.clicked.connect(self.next_frequency)
        freq_layout.addWidget(self.freq_next_btn)
        
        freq_layout.addStretch()  # 添加弹性空间
        control_layout.addLayout(freq_layout)
        
        layout.addWidget(control_group)
        
        # 数据显示区域
        self.data_plot_widget = RealTimePlotWidget()
        layout.addWidget(self.data_plot_widget)
        
        return tab
        
    def create_data_export_tab(self):
        """创建数据导出选项卡"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # 文件列表
        file_group = QGroupBox("数据文件")
        file_layout = QVBoxLayout(file_group)
        
        self.file_table = QTableWidget()
        self.file_table.setColumnCount(3)
        self.file_table.setHorizontalHeaderLabels(['文件名', '类型', '修改时间'])
        file_layout.addWidget(self.file_table)
        
        # 刷新按钮
        refresh_btn = QPushButton("刷新文件列表")
        refresh_btn.clicked.connect(self.refresh_file_list)
        file_layout.addWidget(refresh_btn)
        
        layout.addWidget(file_group)
        
        # 导出控制
        export_group = QGroupBox("导出控制")
        export_layout = QHBoxLayout(export_group)
        
        self.export_json_btn = QPushButton("导出JSON")
        self.export_json_btn.clicked.connect(self.export_json)
        export_layout.addWidget(self.export_json_btn)
        
        self.export_csv_btn = QPushButton("导出CSV")  
        self.export_csv_btn.clicked.connect(self.export_csv)
        export_layout.addWidget(self.export_csv_btn)
        
        self.export_pdf_btn = QPushButton("导出PDF报告")
        self.export_pdf_btn.clicked.connect(self.export_pdf)
        export_layout.addWidget(self.export_pdf_btn)
        
        layout.addWidget(export_group)
        
        # 初始加载文件列表
        self.refresh_file_list()
        
        return tab
        
    def create_status_panel(self, main_layout):
        """创建状态面板"""
        status_frame = QFrame()
        status_frame.setFrameStyle(QFrame.StyledPanel)
        status_layout = QVBoxLayout(status_frame)
        
        # 进度条
        progress_layout = QHBoxLayout()
        progress_label = QLabel("测量进度:")
        progress_label.setStyleSheet("color: black; font-weight: bold;")
        progress_layout.addWidget(progress_label)
        self.progress_bar = QProgressBar()
        progress_layout.addWidget(self.progress_bar)
        status_layout.addLayout(progress_layout)
        
        # 日志显示
        log_group = QGroupBox("实时日志")
        log_group.setStyleSheet("QGroupBox { color: black; font-weight: bold; }")
        log_layout = QVBoxLayout(log_group)
        
        self.log_text = QTextEdit()
        self.log_text.setMaximumHeight(180)
        self.log_text.setFont(QFont("Consolas", 9))
        
        # 添加滚动条事件监听
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.valueChanged.connect(self.on_log_scroll_changed)
        
        log_layout.addWidget(self.log_text)
        
        # 日志控制按钮
        log_btn_layout = QHBoxLayout()
        
        clear_log_btn = QPushButton("清除日志")
        clear_log_btn.clicked.connect(self.clear_log)
        log_btn_layout.addWidget(clear_log_btn)
        
        # CHAT按钮
        self.ai_assistant_btn = QPushButton("💬 CHAT")
        self.ai_assistant_btn.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #218838;
            }
            QPushButton:checked {
                background-color: #1e7e34;
            }
        """)
        self.ai_assistant_btn.setCheckable(True)
        self.ai_assistant_btn.clicked.connect(self.toggle_chat_panel)
        log_btn_layout.addWidget(self.ai_assistant_btn)
        
        log_layout.addLayout(log_btn_layout)
        
        status_layout.addWidget(log_group)
        
        main_layout.addWidget(status_frame)
        
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
        """添加日志消息"""
        self.log_text.append(message)
        # 只在用户没有手动滚动时才自动滚动到底部
        if not self.log_user_scrolling:
            cursor = self.log_text.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.log_text.setTextCursor(cursor)
            # 确保滚动到最底部
            scrollbar = self.log_text.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        
    def on_log_scroll_changed(self, value):
        """处理日志滚动事件"""
        scrollbar = self.log_text.verticalScrollBar()
        # 如果用户滚动到了底部，重置浏览标志
        if value >= scrollbar.maximum() - 5:  # 给予一些容错空间
            self.log_user_scrolling = False
        elif value < scrollbar.maximum() - 10:  # 用户向上滚动了一定距离
            self.log_user_scrolling = True
            
    def clear_log(self):
        """清除日志"""
        self.log_text.clear()
        self.log_user_scrolling = False  # 重置滚动标志
        
    def update_status(self):
        """更新状态"""
        # 这里可以添加定期状态更新逻辑
        pass
        
    def connect_instruments(self):
        """连接仪器"""
        self.add_log_message("开始连接仪器...")
        self.connect_btn.setEnabled(False)
        
        # 更新配置并保存到文件
        if not self.update_and_save_config():
            self.connect_btn.setEnabled(True)
            return
        
        # 启动仪器连接工作线程
        self.instrument_worker = InstrumentWorker(str(CONFIG_FILE))
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
        import copy
        instruments = self.config.get('instruments', {})
        if not isinstance(instruments, dict):
            instruments = {}
        else:
            instruments = copy.deepcopy(instruments)

        existing_power_supplies = instruments.get('power_supplies', {})
        if not isinstance(existing_power_supplies, dict):
            existing_power_supplies = {}
        else:
            existing_power_supplies = copy.deepcopy(existing_power_supplies)

        ps_widgets = [
            ('PS1', self.ps1_address, self.ps1_enabled),
            ('PS2', self.ps2_address, self.ps2_enabled),
            ('PS3', self.ps3_address, self.ps3_enabled),
            ('PS4', self.ps4_address, self.ps4_enabled)
        ]
        power_supplies = existing_power_supplies
        for ps_name, address_widget, enabled_widget in ps_widgets:
            ps_config = power_supplies.get(ps_name, {})
            if not isinstance(ps_config, dict):
                ps_config = {}
            else:
                ps_config = copy.deepcopy(ps_config)
            ps_config['address'] = address_widget.text()
            ps_config['enabled'] = enabled_widget.isChecked()
            power_supplies[ps_name] = ps_config

        # 保留现代运行映射中的 model 及其他扩展字段。旧实现整体替换
        # 这两个对象，会在每次保存时把实际仪器型号清空。
        signal_generator = instruments.get('signal_generator', {})
        if not isinstance(signal_generator, dict):
            signal_generator = {}
        signal_generator = copy.deepcopy(signal_generator)
        signal_generator['address'] = self.sg_address.text()
        signal_generator['enabled'] = self.sg_enabled.isChecked()
        instruments['signal_generator'] = signal_generator

        spectrum_analyzer = instruments.get('spectrum_analyzer', {})
        if not isinstance(spectrum_analyzer, dict):
            spectrum_analyzer = {}
        spectrum_analyzer = copy.deepcopy(spectrum_analyzer)
        spectrum_analyzer['address'] = self.sa_address.text()
        spectrum_analyzer['enabled'] = self.sa_enabled.isChecked()
        instruments['spectrum_analyzer'] = spectrum_analyzer
        instruments['power_supplies'] = power_supplies
        return {'instruments': instruments}

    def _read_test_parameters_from_ui(self) -> dict:
        """从UI读取测试参数，不修改self.config。"""
        result = {}
        try:
            freq_list = eval(self.freq_edit.text())
            result['test_frequencies'] = freq_list
        except:
            pass
        result['signal_source'] = {
            'start_power': self.start_power.value(),
            'stop_power': self.stop_power.value(),
            'step': self.power_step.value()
        }
        result['compression_point'] = {'type': self.compression_combo.currentText()}
        result['attenuator'] = {'type': self.attenuator_combo.currentText()}
        result['driver_mode'] = {'enabled': self.driver_mode_check.isChecked()}
        return result

    def _read_power_supply_config_from_ui(self) -> dict:
        """从UI读取电源通道详细配置，按电源名称分组返回。"""
        if not hasattr(self, 'power_config_widgets'):
            return {}
        result = {}
        for ps_name, ps_widgets in self.power_config_widgets.items():
            channels = {}
            for ch_name, ch_widgets in ps_widgets['channels'].items():
                channels[ch_name] = {
                    'voltage': {
                        'value': ch_widgets['voltage'].value(),
                        'protection': ch_widgets['voltage_protection'].value(),
                        'protection_enabled': ch_widgets['voltage_protection_enabled'].isChecked()
                    },
                    'current': {
                        'value': ch_widgets['current'].value(),
                        'protection': ch_widgets['current_protection'].value(),
                        'protection_enabled': ch_widgets['current_protection_enabled'].isChecked()
                    }
                }
            result[ps_name] = {'channels': channels}
        return {'instruments': {'power_supplies': result}}

    def _read_power_assignment_from_ui(self) -> dict:
        """从UI读取电源分配配置，返回完整的power_supply_assignment片段。"""
        if not hasattr(self, 'power_assignment_widgets'):
            return {}

        # 驱动功放电源分配
        driver_enabled = self.power_assignment_widgets['driver_enabled'].isChecked()
        driver_amplifier = {
            'power_supply_count': 1 if driver_enabled else 0,
            'supplies': {}
        }
        if driver_enabled:
            driver_ps = self.power_assignment_widgets['driver_power'].currentText()
            driver_amplifier['supplies']['main'] = {
                'name': driver_ps,
                'channel': ['CH1', 'CH2']
            }

        # DUT功放电源分配
        pa_unit_count = int(self.pa_unit_count_combo.currentText())
        unit1_ps = self.power_assignment_widgets['pa_unit1_power'].currentText()
        dut_supplies = {
            'carrier': {  # 保持carrier键名以兼容现有配置
                'name': unit1_ps,
                'channel': ['CH1', 'CH2']
            }
        }
        if pa_unit_count >= 2:
            unit2_ps = self.power_assignment_widgets['pa_unit2_power'].currentText()
            dut_supplies['peaking'] = {  # 保持peaking键名以兼容现有配置
                'name': unit2_ps,
                'channel': ['CH1', 'CH2']
            }
        if pa_unit_count >= 3:
            unit3_ps = self.power_assignment_widgets['pa_unit3_power'].currentText()
            dut_supplies['peaking2'] = {  # 保持peaking2键名以兼容现有配置
                'name': unit3_ps,
                'channel': ['CH1', 'CH2']
            }

        return {
            'power_supply_assignment': {
                'driver_amplifier': driver_amplifier,
                'dut_amplifier': {
                    'power_supply_count': pa_unit_count,
                    'supplies': dut_supplies
                }
            }
        }

    def _build_config_from_ui(self) -> dict:
        """基于 self.config 和当前 UI 状态组装完整配置字典。

        该方法不修改 self.config，不写文件，不记录日志，不弹窗。
        """
        import copy
        config = copy.deepcopy(self.config)

        # 合并仪器地址/启用状态（保留已有 channels 和未知字段）
        instrument_fragment = self._read_instrument_config_from_ui()
        config.setdefault('instruments', {})
        config['instruments'] = instrument_fragment['instruments']

        # 合并测试参数
        test_params = self._read_test_parameters_from_ui()
        config.update(test_params)

        # 更新 DUT 配置
        config.setdefault('dut_config', {})
        config['dut_config']['max_input_power'] = self.max_input_power.value()
        pa_unit_count = int(self.pa_unit_count_combo.currentText())
        config['dut_config']['power_supply_count'] = pa_unit_count

        # 合并电源通道详细参数（只替换对应 PS 的 channels，保留地址和启用状态）
        power_supply_fragment = self._read_power_supply_config_from_ui()
        if power_supply_fragment:
            fragment_supplies = power_supply_fragment.get('instruments', {}).get('power_supplies', {})
            config.setdefault('instruments', {})
            config_power_supplies = config['instruments'].get('power_supplies', {})
            if not isinstance(config_power_supplies, dict):
                config_power_supplies = {}
            else:
                config_power_supplies = dict(config_power_supplies)
            for ps_name, ps_fragment in fragment_supplies.items():
                ps_config = config_power_supplies.get(ps_name, {})
                if not isinstance(ps_config, dict):
                    ps_config = {}
                else:
                    ps_config = dict(ps_config)
                existing_channels = ps_config.get('channels', {})
                if not isinstance(existing_channels, dict):
                    existing_channels = {}
                else:
                    existing_channels = dict(existing_channels)
                for channel_name, channel_config in ps_fragment.get('channels', {}).items():
                    existing_channels[channel_name] = channel_config
                ps_config['channels'] = existing_channels
                config_power_supplies[ps_name] = ps_config
            config['instruments']['power_supplies'] = config_power_supplies

        # 合并电源分配配置
        assignment_fragment = self._read_power_assignment_from_ui()
        if assignment_fragment:
            config['power_supply_assignment'] = assignment_fragment['power_supply_assignment']

        # 任意参数、仪器或电源分配变更都必须重新确认现场接线。
        config['wiring'] = {
            'confirmed': False,
            'connection_note': None,
            'confirmed_at': None,
            'confirmation_source': None,
        }

        return config

    def update_config_from_ui(self):
        """从UI更新配置"""
        self.config = self._build_config_from_ui()

    def _save_config_file(self) -> bool:
        """将 self.config 写入 CONFIG_FILE，成功返回 True，失败返回 False。"""
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
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
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择测试数据文件", "", 
            "JSON files (*.json);;All files (*.*)"
        )
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 初始化数据存储
                self.loaded_data = None
                self.current_freq_index = 0
                self.frequency_list = []
                # 保存原始文件名用于报告生成
                self.loaded_filename = Path(file_path).name
                
                # 处理不同类型的数据文件
                if 'results' in data:
                    # 功放测试数据格式
                    self.loaded_data = data['results']
                    self.frequency_list = sorted([float(f) for f in data['results'].keys()])
                    self.add_log_message(f"已加载功放测试数据: {Path(file_path).name}")
                elif 'power_mapping' in data:
                    # 驱动映射数据格式，需要转换为标准格式
                    self.loaded_data = {}
                    for freq, power_map in data['power_mapping'].items():
                        # 转换驱动映射数据为sweep_data格式
                        input_powers = [float(p) for p in power_map.keys()]
                        output_powers = list(power_map.values())
                        
                        # 计算增益 (Gain = Pout - Pin)
                        gains = [pout - pin for pin, pout in zip(input_powers, output_powers)]
                        
                        self.loaded_data[freq] = {
                            'sweep_data': {
                                'input_power_sg': input_powers,
                                'output_power_driver': output_powers,
                                'gain': gains  # 添加增益数据
                            }
                        }
                    self.frequency_list = sorted([float(f) for f in data['power_mapping'].keys()])
                    self.add_log_message(f"已加载驱动映射数据: {Path(file_path).name}")
                else:
                    raise ValueError("不支持的数据格式")
                
                # 更新UI状态
                if self.frequency_list:
                    self.current_freq_index = 0
                    self.update_frequency_display()
                    self.display_current_frequency_data()
                    
                    # 启用频率切换按钮
                    self.freq_prev_btn.setEnabled(len(self.frequency_list) > 1)
                    self.freq_next_btn.setEnabled(len(self.frequency_list) > 1)
                else:
                    raise ValueError("未找到有效的频率数据")
                    
            except Exception as e:
                self.add_log_message(f"数据加载失败: {e}")
                # 重置UI状态
                self.freq_label.setText("数据加载失败")
                self.freq_prev_btn.setEnabled(False)
                self.freq_next_btn.setEnabled(False)
    
    def update_frequency_display(self):
        """更新频率显示"""
        if hasattr(self, 'frequency_list') and self.frequency_list:
            current_freq = self.frequency_list[self.current_freq_index]
            total_freq = len(self.frequency_list)
            self.freq_label.setText(f"{current_freq} GHz ({self.current_freq_index + 1}/{total_freq})")
        else:
            self.freq_label.setText("未加载数据")
    
    def display_current_frequency_data(self):
        """显示当前频率的数据"""
        if hasattr(self, 'loaded_data') and self.loaded_data and hasattr(self, 'frequency_list'):
            current_freq = str(self.frequency_list[self.current_freq_index])
            if current_freq in self.loaded_data:
                freq_data = self.loaded_data[current_freq]
                self.data_plot_widget.update_plot({
                    'frequency': float(current_freq),
                    'sweep_data': freq_data.get('sweep_data', {})
                })
                self.add_log_message(f"显示频率 {current_freq} GHz 的数据")
    
    def prev_frequency(self):
        """切换到上一个频率"""
        if hasattr(self, 'frequency_list') and self.frequency_list:
            if self.current_freq_index > 0:
                self.current_freq_index -= 1
                self.update_frequency_display()
                self.display_current_frequency_data()
    
    def next_frequency(self):
        """切换到下一个频率"""
        if hasattr(self, 'frequency_list') and self.frequency_list:
            if self.current_freq_index < len(self.frequency_list) - 1:
                self.current_freq_index += 1
                self.update_frequency_display()
                self.display_current_frequency_data()
    
    def store_real_time_data(self, data):
        """存储实时测量数据并更新图表"""
        if 'frequency' in data and 'sweep_data' in data:
            frequency = str(data['frequency'])
            
            # 存储数据到历史记录
            self.real_time_data[frequency] = data.copy()
            
            # 更新频点列表
            if frequency not in [str(f) for f in self.rt_frequency_list]:
                self.rt_frequency_list.append(float(frequency))
                self.rt_frequency_list.sort()
                # 只在用户没有手动浏览时才跳转到最新频点
                if not self.rt_user_browsing:
                    self.rt_current_freq_index = len(self.rt_frequency_list) - 1
        
        # 更新实时图表（显示当前数据）
        if hasattr(self, 'driver_plot_widget'):
            self.driver_plot_widget.update_plot(data)
        if hasattr(self, 'amplifier_plot_widget'):
            self.amplifier_plot_widget.update_plot(data)
        
        # 更新导航按钮状态
        self.update_rt_nav_buttons()
        
        # 更新频点显示标签
        self.update_rt_frequency_display()
    
    def update_rt_frequency_display(self):
        """更新实时预览的频点显示"""
        # 现在频点信息直接显示在日志中，不需要更新UI标签
        pass
    
    def display_current_rt_frequency_data(self):
        """显示当前选择的实时频点数据"""
        if self.rt_frequency_list and 0 <= self.rt_current_freq_index < len(self.rt_frequency_list):
            current_freq = str(self.rt_frequency_list[self.rt_current_freq_index])
            if current_freq in self.real_time_data:
                freq_data = self.real_time_data[current_freq]
                
                # 更新图表
                if hasattr(self, 'driver_plot_widget'):
                    self.driver_plot_widget.update_plot(freq_data)
                if hasattr(self, 'amplifier_plot_widget'):
                    self.amplifier_plot_widget.update_plot(freq_data)
                
                # 更新导航按钮状态
                self.update_rt_nav_buttons()
                
                self.add_log_message(f"显示实时测量频率 {current_freq} GHz 的数据")
    
    def rt_prev_frequency(self):
        """切换到上一个实时测量频率"""
        if self.rt_frequency_list:
            if self.rt_current_freq_index > 0:
                self.rt_current_freq_index -= 1
                self.rt_user_browsing = True  # 标记用户正在手动浏览
                self.update_rt_frequency_display()
                self.display_current_rt_frequency_data()
    
    def rt_next_frequency(self):
        """切换到下一个实时测量频率"""
        if self.rt_frequency_list:
            if self.rt_current_freq_index < len(self.rt_frequency_list) - 1:
                self.rt_current_freq_index += 1
                self.rt_user_browsing = True  # 标记用户正在手动浏览
                # 如果到达最新频点，重置浏览标志
                if self.rt_current_freq_index == len(self.rt_frequency_list) - 1:
                    self.rt_user_browsing = False
                self.update_rt_frequency_display()
                self.display_current_rt_frequency_data()
    
    def update_rt_nav_buttons(self):
        """更新实时预览导航按钮的状态"""
        has_data = len(self.rt_frequency_list) > 1
        has_prev = self.rt_current_freq_index > 0
        has_next = self.rt_current_freq_index < len(self.rt_frequency_list) - 1
        
        # 更新驱动映射的导航按钮
        if hasattr(self, 'driver_plot_widget') and hasattr(self.driver_plot_widget, 'nav_prev_btn'):
            self.driver_plot_widget.nav_prev_btn.setEnabled(has_data and has_prev)
            self.driver_plot_widget.nav_next_btn.setEnabled(has_data and has_next)
        
        # 更新功放测试的导航按钮
        if hasattr(self, 'amplifier_plot_widget') and hasattr(self.amplifier_plot_widget, 'nav_prev_btn'):
            self.amplifier_plot_widget.nav_prev_btn.setEnabled(has_data and has_prev)
            self.amplifier_plot_widget.nav_next_btn.setEnabled(has_data and has_next)

    def clear_real_time_data(self):
        """清除实时测量的历史数据"""
        self.real_time_data = {}
        self.rt_frequency_list = []
        self.rt_current_freq_index = 0
        self.rt_user_browsing = False  # 重置用户浏览标志
        
        # 更新频点显示和按钮状态
        self.update_rt_frequency_display()
        self.update_rt_nav_buttons()
        
        self.add_log_message("已清除实时测量历史数据")
                
    def generate_report(self):
        """生成报告"""
        try:
            visualizer = DataVisualization()
            
            # 首先检查是否有已加载的数据
            if hasattr(self, 'loaded_data') and self.loaded_data:
                # 使用已加载的数据生成报告
                # 获取原始文件名
                original_filename = getattr(self, 'loaded_filename', '已加载的测试数据')
                # 构造完整的数据结构
                report_data = {
                    'results': self.loaded_data,
                    'measurement_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'config': getattr(self, 'config', {}),
                    'original_filename': original_filename
                }
                
                # 创建临时文件用于报告生成
                TEMP_DIR.mkdir(parents=True, exist_ok=True)
                temp_file = TEMP_DIR / 'temp_loaded_data.json'
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(report_data, f, indent=2, ensure_ascii=False)
                
                visualizer.create_summary_report(str(temp_file), original_filename)
                
                # 清理临时文件
                if temp_file.exists():
                    temp_file.unlink()
                    
                self.add_log_message("基于已加载数据生成测试报告")
            else:
                # 查找最新的测试数据文件
                dut_files = sorted(TEST_RESULTS_DIR.glob('amplifier_measurement_*.json'), key=lambda x: x.stat().st_mtime)
                if not dut_files:
                    QMessageBox.warning(self, "报告生成", "未找到测试数据文件，请先加载数据或进行测试")
                    return
                    
                latest_file = dut_files[-1]
                visualizer.create_summary_report(str(latest_file))
                self.add_log_message(f"基于文件 {latest_file.name} 生成测试报告")
            
            QMessageBox.information(self, "报告生成", "测试报告已生成完成")
            
        except Exception as e:
            self.add_log_message(f"报告生成失败: {e}")
            # 添加更详细的错误信息用于调试
            import traceback
            self.add_log_message(f"详细错误信息: {traceback.format_exc()}")
            QMessageBox.warning(self, "报告生成", f"报告生成失败: {e}")
            
    def refresh_file_list(self):
        """刷新文件列表"""
        self.file_table.setRowCount(0)
        
        # 查找各种数据文件
        file_patterns = [
            (CABLE_LOSS_FILE.name, '线损数据'),
            ('driver_power_mapping_*.json', '驱动映射'),
            ('amplifier_measurement_*.json', '功放测试'),
        ]
        
        row = 0
        for pattern, file_type in file_patterns:
            files = list(TEST_RESULTS_DIR.glob(pattern))
            for file_path in sorted(files, key=lambda x: x.stat().st_mtime, reverse=True):
                self.file_table.insertRow(row)
                self.file_table.setItem(row, 0, QTableWidgetItem(file_path.name))
                self.file_table.setItem(row, 1, QTableWidgetItem(file_type))
                self.file_table.setItem(row, 2, QTableWidgetItem(
                    datetime.fromtimestamp(file_path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                ))
                row += 1
                
    def export_json(self):
        """导出JSON"""
        current_row = self.file_table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "导出", "请先选择要导出的文件")
            return
            
        filename = self.file_table.item(current_row, 0).text()
        save_path, _ = QFileDialog.getSaveFileName(
            self, "保存JSON文件", filename, "JSON files (*.json)"
        )
        if save_path:
            try:
                import shutil
                shutil.copy2(TEST_RESULTS_DIR / filename, save_path)
                self.add_log_message(f"JSON文件已导出: {save_path}")
                QMessageBox.information(self, "导出成功", f"文件已导出到: {save_path}")
            except Exception as e:
                self.add_log_message(f"JSON导出失败: {e}")
                QMessageBox.warning(self, "导出失败", str(e))
        
    def export_csv(self):
        """导出CSV"""
        current_row = self.file_table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "导出", "请先选择要导出的文件")
            return
            
        filename = self.file_table.item(current_row, 0).text()
        if not filename.startswith('amplifier_measurement_'):
            QMessageBox.warning(self, "导出", "只有功放测试数据支持CSV导出")
            return
            
        save_path, _ = QFileDialog.getSaveFileName(
            self, "保存CSV文件", filename.replace('.json', '.csv'), "CSV files (*.csv)"
        )
        if save_path:
            try:
                visualizer = DataVisualization()
                visualizer.generate_csv_report(str(TEST_RESULTS_DIR / filename))
                
                # 使用当前可视化实例创建的目录，避免跨秒时计算到错误路径。
                import shutil
                generated_csv = visualizer.output_dir / "full_sweep_data.csv"
                if generated_csv.exists():
                    shutil.copy2(generated_csv, save_path)
                else:
                    raise FileNotFoundError(f"未生成CSV文件: {generated_csv}")
                    
                self.add_log_message(f"CSV文件已导出: {save_path}")
                QMessageBox.information(self, "导出成功", f"CSV文件已导出到: {save_path}")
            except Exception as e:
                self.add_log_message(f"CSV导出失败: {e}")
                QMessageBox.warning(self, "导出失败", str(e))
        
    def export_pdf(self):
        """导出PDF"""
        current_row = self.file_table.currentRow()
        if current_row < 0:
            QMessageBox.warning(self, "导出", "请先选择要导出的文件")
            return
            
        filename = self.file_table.item(current_row, 0).text()
        if not filename.startswith('amplifier_measurement_'):
            QMessageBox.warning(self, "导出", "只有功放测试数据支持PDF导出")
            return
            
        try:
            visualizer = DataVisualization()
            visualizer.create_summary_report(str(TEST_RESULTS_DIR / filename))
            
            self.add_log_message("PDF报告已生成在test_results文件夹中")
            QMessageBox.information(self, "导出成功", "PDF报告已生成在test_results文件夹中")
        except Exception as e:
            self.add_log_message(f"PDF导出失败: {e}")
            QMessageBox.warning(self, "导出失败", str(e))
        
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


def main():
    app = QApplication(sys.argv)
    # 阶段 0.3：最小日志（幂等；launcher 已初始化时保持原文件）。
    try:
        setup_logging()
    except Exception as logging_error:
        print(f"日志初始化失败（忽略）: {logging_error}")
    # 使用Windows原生样式以获得正常的按钮显示
    # app.setStyle('Fusion')
    
    # 设置应用图标和信息
    app.setApplicationName("PA自动测试系统")
    app.setApplicationVersion("1.0")
    app.setOrganizationName("PA Test Lab")
    
    # 设置应用样式
    app.setStyleSheet("""
    QMainWindow {
        background-color: white;
    }
    QDialog {
        background-color: white;
    }
    QTabWidget::pane {
        border: 1px solid #c0c0c0;
        background-color: white;
    }
    QTabBar::tab {
        background-color: #e0e0e0;
        color: black;
        padding: 8px 16px;
        margin-right: 2px;
    }
    QTabBar::tab:selected {
        background-color: white;
        color: black;
        border-bottom: 2px solid #0078d4;
    }
    QTabBar::tab:hover {
        background-color: #f5f5f5;
        color: black;
    }
    QGroupBox {
        background-color: white;
        color: black;
        font-weight: bold;
        border: 2px solid #c0c0c0;
        border-radius: 5px;
        margin-top: 1ex;
        padding-top: 10px;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 10px;
        padding: 0 5px 0 5px;
        color: black;
    }
    QLabel {
        background-color: transparent;
        color: black;
    }
    QLineEdit {
        background-color: white;
        color: black;
        border: 1px solid #ccc;
        padding: 4px;
        border-radius: 3px;
    }
    QLineEdit:focus {
        border: 1px solid #0078d4;
    }
    QSpinBox, QDoubleSpinBox {
        background-color: white;
        color: black;
        border: 1px solid #ccc;
        padding: 4px;
        border-radius: 3px;
    }
    QSpinBox:focus, QDoubleSpinBox:focus {
        border: 1px solid #0078d4;
    }
    QComboBox {
        background-color: white;
        color: black;
        border: 1px solid #ccc;
        padding: 4px;
        border-radius: 3px;
    }
    QComboBox:focus {
        border: 1px solid #0078d4;
    }
    QComboBox QAbstractItemView {
        background-color: white;
        color: black;
        selection-background-color: #0078d4;
        selection-color: white;
        border: 1px solid #ccc;
    }
    QCheckBox {
        background-color: transparent;
        color: black;
    }
    QTextEdit, QPlainTextEdit {
        background-color: white;
        color: black;
        border: 1px solid #ccc;
        border-radius: 3px;
    }
    QTextEdit:focus, QPlainTextEdit:focus {
        border: 1px solid #0078d4;
    }
    QProgressBar {
        background-color: #f0f0f0;
        border: 1px solid #ccc;
        border-radius: 3px;
        text-align: center;
        color: black;
        height: 20px;
    }
    QProgressBar::chunk {
        background-color: #0078d4;
        border-radius: 2px;
    }
    QTableWidget {
        background-color: white;
        color: black;
        gridline-color: #e0e0e0;
        border: 1px solid #ccc;
    }
    QTableWidget::item {
        color: black;
        padding: 4px;
    }
    QTableWidget::item:selected {
        background-color: #0078d4;
        color: white;
    }
    QHeaderView::section {
        background-color: #f5f5f5;
        color: black;
        border: 1px solid #d0d0d0;
        padding: 4px;
    }
    QScrollArea {
        background-color: white;
        border: none;
    }
    QScrollBar:vertical {
        background-color: #f5f5f5;
        width: 14px;
        border: none;
        margin: 0px;
    }
    QScrollBar::handle:vertical {
        background-color: #c0c0c0;
        border-radius: 7px;
        min-height: 20px;
        margin: 2px;
    }
    QScrollBar::handle:vertical:hover {
        background-color: #a0a0a0;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
    }
    QScrollBar:horizontal {
        background-color: #f5f5f5;
        height: 14px;
        border: none;
        margin: 0px;
    }
    QScrollBar::handle:horizontal {
        background-color: #c0c0c0;
        border-radius: 7px;
        min-width: 20px;
        margin: 2px;
    }
    QScrollBar::handle:horizontal:hover {
        background-color: #a0a0a0;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
        width: 0px;
    }
    QPushButton {
        background-color: #0078d4;
        color: white;
        border: none;
        padding: 8px 16px;
        border-radius: 4px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #106ebe;
    }
    QPushButton:pressed {
        background-color: #005a9e;
    }
    QPushButton:disabled {
        background-color: #cccccc;
        color: #666666;
    }

    """)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

