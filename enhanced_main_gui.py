"""
增强版GUI - 包含连接图显示和更多功能
"""

import sys
import os
import json
import time
import traceback
import markdown
from pathlib import Path
from datetime import datetime
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
from instrument_control import InstrumentControl
from cable_loss_measurement import CableLossMeasurement  
from driver_power_mapping import DriverPowerMapping
from amplifier_measurement import AmplifierMeasurement
from data_visualization import DataVisualization
from enhanced_workers import EnhancedCableLossMeasurement, EnhancedDriverPowerMapping, EnhancedAmplifierMeasurement
from connection_diagrams import ConnectionDiagram
import sys
import os
# 添加当前目录到Python路径，以便导入llm模块
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)
from llm import LLMChat
from project_paths import (
    CABLE_LOSS_FILE,
    CHAT_HISTORY_FILE,
    CONFIG_FILE,
    ICONS_DIR,
    PROJECT_ROOT,
    SEARCH_API_CONFIG_FILE,
)


class ChatWorker(QThread):
    """AI聊天工作线程，避免阻塞主UI线程"""
    response_ready = Signal(str)
    error_occurred = Signal(str)
    
    def __init__(self, llm_chat, message, context):
        super().__init__()
        self.llm_chat = llm_chat
        self.message = message
        self.context = context
        
    def run(self):
        """在后台线程中执行AI请求"""
        try:
            response = self.llm_chat.chat(self.message, self.context)
            self.response_ready.emit(response)
        except Exception as e:
            self.error_occurred.emit(str(e))


class ChatHistoryDialog(QDialog):
    """历史对话选择对话框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择历史对话")
        self.setModal(True)
        self.resize(500, 400)
        
        layout = QVBoxLayout(self)
        
        # 说明文本
        info_label = QLabel("选择要加载的历史对话:")
        layout.addWidget(info_label)
        
        # 历史记录列表
        self.history_list = QListWidget()
        self.load_history_list()
        layout.addWidget(self.history_list)
        
        # 按钮
        button_layout = QHBoxLayout()
        
        self.load_btn = QPushButton("加载选定对话")
        self.load_btn.clicked.connect(self.accept)
        button_layout.addWidget(self.load_btn)
        
        self.delete_btn = QPushButton("删除选定对话")
        self.delete_btn.clicked.connect(self.delete_selected)
        button_layout.addWidget(self.delete_btn)
        
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
        
    def load_history_list(self):
        """加载历史记录列表"""
        try:
            import glob
            from datetime import datetime
            
            # 查找所有历史文件
            history_files = [str(path) for path in PROJECT_ROOT.glob("chat_history_*.json")]
            
            # 添加当前活动对话
            if CHAT_HISTORY_FILE.exists():
                self.history_list.addItem("📝 当前对话")
            
            # 添加已保存的历史文件
            if history_files:
                # 按修改时间排序
                history_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                
                for file_path in history_files:
                    filename = os.path.basename(file_path)
                    # 尝试从文件名解析时间戳
                    try:
                        timestamp_str = filename.replace('chat_history_', '').replace('.json', '')
                        if timestamp_str:  # 如果有时间戳
                            # 格式：YYYYMMDD_HHMMSS
                            if len(timestamp_str) >= 8:
                                dt = datetime.strptime(timestamp_str[:8], '%Y%m%d')
                                display_name = f"💾 {dt.strftime('%Y年%m月%d日')} - {filename}"
                            else:
                                display_name = f"💾 {filename}"
                        else:
                            display_name = f"💾 {filename}"
                    except:
                        display_name = f"💾 {filename}"
                    
                    self.history_list.addItem(display_name)
                    # 存储实际文件名
                    self.history_list.item(self.history_list.count() - 1).setData(Qt.UserRole, file_path)
            
            if self.history_list.count() == 0:
                self.history_list.addItem("📭 无历史记录")
                    
        except Exception as e:
            print(f"加载历史记录失败: {e}")
            self.history_list.addItem("❌ 加载失败")
    
    def get_selected_history(self):
        """获取选定的历史对话"""
        current_item = self.history_list.currentItem()
        if current_item:
            text = current_item.text()
            if text.startswith("📝 当前对话"):
                # 返回当前对话历史
                try:
                    import json
                    with open(CHAT_HISTORY_FILE, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except:
                    return []
            elif text.startswith("💾"):
                # 获取实际文件路径
                file_path = current_item.data(Qt.UserRole)
                if file_path:
                    try:
                        import json
                        with open(file_path, 'r', encoding='utf-8') as f:
                            return json.load(f)
                    except Exception as e:
                        print(f"加载历史文件失败: {e}")
                        return []
        return None
    
    def delete_selected(self):
        """删除选定的对话"""
        current_item = self.history_list.currentItem()
        if current_item:
            text = current_item.text()
            
            if text.startswith("📝 当前对话"):
                QMessageBox.information(self, "无法删除", "无法删除当前活动对话，请先开始新对话。")
                return
            elif text.startswith("💾"):
                file_path = current_item.data(Qt.UserRole)
                if file_path:
                    reply = QMessageBox.question(self, "删除确认", 
                                               f"确定要删除对话记录吗？\n{os.path.basename(file_path)}",
                                               QMessageBox.Yes | QMessageBox.No)
                    if reply == QMessageBox.Yes:
                        try:
                            os.remove(file_path)
                            self.history_list.takeItem(self.history_list.row(current_item))
                            QMessageBox.information(self, "删除成功", "历史对话已删除")
                        except Exception as e:
                            QMessageBox.warning(self, "删除失败", f"无法删除文件: {e}")
            else:
                QMessageBox.information(self, "无法删除", "请选择有效的历史记录进行删除。")


class ChatSettingsDialog(QDialog):
    """聊天设置对话框"""
    def __init__(self, llm_chat, parent=None):
        super().__init__(parent)
        self.llm_chat = llm_chat
        self.setWindowTitle("CHAT 设置")
        self.setModal(True)
        self.resize(450, 300)
        
        layout = QVBoxLayout(self)
        
        # 模型设置组
        model_group = QGroupBox("AI模型设置")
        model_layout = QFormLayout(model_group)
        
        # 服务器地址
        self.server_url_edit = QLineEdit()
        self.server_url_edit.setText(self.llm_chat.server_url)
        model_layout.addRow("服务器地址:", self.server_url_edit)
        
        # 模型名称
        self.model_name_edit = QLineEdit()
        self.model_name_edit.setText(self.llm_chat.model_name)
        model_layout.addRow("模型名称:", self.model_name_edit)
        
        # 温度设置
        self.temperature_spin = QDoubleSpinBox()
        self.temperature_spin.setRange(0.0, 2.0)
        self.temperature_spin.setSingleStep(0.1)
        self.temperature_spin.setValue(self.llm_chat.temperature)
        model_layout.addRow("温度 (创造性):", self.temperature_spin)
        
        # 最大Token数
        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(100, 8000)
        self.max_tokens_spin.setValue(self.llm_chat.max_tokens)
        model_layout.addRow("最大Token数:", self.max_tokens_spin)
        
        layout.addWidget(model_group)
        
        # 功能设置组
        function_group = QGroupBox("功能设置")
        function_layout = QFormLayout(function_group)
        
        # 自动保存历史
        self.auto_save_check = QCheckBox()
        self.auto_save_check.setChecked(self.llm_chat.auto_save)
        function_layout.addRow("自动保存历史:", self.auto_save_check)
        
        # 历史记录数量限制
        self.history_limit_spin = QSpinBox()
        self.history_limit_spin.setRange(10, 1000)
        self.history_limit_spin.setValue(self.llm_chat.history_limit)
        function_layout.addRow("历史记录上限:", self.history_limit_spin)
        
        layout.addWidget(function_group)
        
        # 搜索API设置组
        search_group = QGroupBox("联网搜索API设置")
        search_layout = QFormLayout(search_group)
        
        # Bing搜索API密钥
        self.bing_key_edit = QLineEdit()
        self.bing_key_edit.setEchoMode(QLineEdit.Password)
        self.bing_key_edit.setPlaceholderText("可选：Bing搜索API密钥")
        search_layout.addRow("Bing API密钥:", self.bing_key_edit)
        
        # Google搜索API密钥
        self.google_key_edit = QLineEdit()
        self.google_key_edit.setEchoMode(QLineEdit.Password)
        self.google_key_edit.setPlaceholderText("可选：Google搜索API密钥")
        search_layout.addRow("Google API密钥:", self.google_key_edit)
        
        # Google自定义搜索引擎ID
        self.google_cx_edit = QLineEdit()
        self.google_cx_edit.setPlaceholderText("可选：Google自定义搜索引擎ID")
        search_layout.addRow("Google搜索引擎ID:", self.google_cx_edit)
        
        # 说明文本
        info_label = QLabel("提示：如果不配置API密钥，将使用免费的DuckDuckGo搜索（功能有限）")
        info_label.setStyleSheet("color: #666; font-size: 11px;")
        info_label.setWordWrap(True)
        search_layout.addRow(info_label)
        
        layout.addWidget(search_group)
        
        # 加载搜索API配置
        self.load_search_config()
        
        # 按钮
        button_layout = QHBoxLayout()
        
        test_btn = QPushButton("测试连接")
        test_btn.clicked.connect(self.test_connection)
        button_layout.addWidget(test_btn)
        
        save_btn = QPushButton("保存设置")
        save_btn.clicked.connect(self.save_settings)
        button_layout.addWidget(save_btn)
        
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
        
    def test_connection(self):
        """测试AI服务连接"""
        try:
            import requests
            url = self.server_url_edit.text()
            model = self.model_name_edit.text()
            
            self.setCursor(Qt.WaitCursor)
            response = requests.post(url, json={
                "model": model,
                "messages": [{"role": "user", "content": "Hello, please reply with 'Connection test successful'"}],
                "stream": False,
                "options": {"max_tokens": 50}
            }, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                reply = data.get('message', {}).get('content', '')
                QMessageBox.information(self, "连接成功", f"AI服务连接正常！\n\n回复: {reply}")
            else:
                QMessageBox.warning(self, "连接失败", f"服务器返回错误: {response.status_code}")
                
        except requests.exceptions.Timeout:
            QMessageBox.critical(self, "连接超时", "连接超时，请检查服务器地址和网络连接。")
        except requests.exceptions.ConnectionError:
            QMessageBox.critical(self, "连接失败", "无法连接到AI服务，请检查服务器地址是否正确。")
        except Exception as e:
            QMessageBox.critical(self, "连接失败", f"无法连接到AI服务: {str(e)}")
        finally:
            self.setCursor(Qt.ArrowCursor)
    
    def load_search_config(self):
        """加载搜索API配置"""
        try:
            config_file = SEARCH_API_CONFIG_FILE
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.bing_key_edit.setText(config.get('bing_subscription_key', ''))
                    self.google_key_edit.setText(config.get('google_api_key', ''))
                    self.google_cx_edit.setText(config.get('google_cx', ''))
        except Exception as e:
            print(f"加载搜索API配置失败: {e}")
    
    def save_search_config(self):
        """保存搜索API配置"""
        try:
            config = {
                'bing_subscription_key': self.bing_key_edit.text(),
                'google_api_key': self.google_key_edit.text(),
                'google_cx': self.google_cx_edit.text(),
                'note': "请在此配置您的搜索API密钥。如果不配置，将使用DuckDuckGo免费搜索（功能有限）。"
            }
            
            with open(SEARCH_API_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            
            # 更新Function Handler的API配置
            if hasattr(self.llm_chat, 'function_handler') and self.llm_chat.function_handler:
                self.llm_chat.function_handler.configure_api_keys(
                    bing_key=config['bing_subscription_key'] or None,
                    google_key=config['google_api_key'] or None,
                    google_cx=config['google_cx'] or None
                )
                
        except Exception as e:
            print(f"保存搜索API配置失败: {e}")

    def save_settings(self):
        """保存设置"""
        try:
            # 保存搜索API配置
            self.save_search_config()
            
            # 更新LLMChat的配置
            self.llm_chat.update_settings(
                server_url=self.server_url_edit.text(),
                model_name=self.model_name_edit.text(),
                temperature=self.temperature_spin.value(),
                max_tokens=self.max_tokens_spin.value(),
                auto_save=self.auto_save_check.isChecked(),
                history_limit=self.history_limit_spin.value()
            )
            QMessageBox.information(self, "设置已保存", "设置已成功保存！")
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"无法保存设置: {str(e)}")


class ChatPanel(QWidget):
    """聊天面板组件"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.llm_chat = LLMChat()
        self.main_window = parent
        self.current_worker = None  # 当前的AI工作线程
        self.thinking_message_visible = False  # 跟踪"思考中"消息状态
        self.init_ui()
    
    def init_ui(self):
        """初始化聊天界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # 标题栏
        header_layout = QHBoxLayout()
        
        title_label = QLabel("CHAT")
        title_label.setStyleSheet("font-weight: bold; font-size: 16px; color: #333;")
        header_layout.addWidget(title_label)
        
        header_layout.addStretch()
        
        # 功能按钮区
        button_layout = QHBoxLayout()
        button_layout.setSpacing(5)
        
        # 新对话按钮
        self.new_chat_btn = QPushButton()
        self.new_chat_btn.setIcon(QIcon(str(ICONS_DIR / "plus.png")))
        self.new_chat_btn.setIconSize(QSize(23, 23))
        self.new_chat_btn.setMaximumSize(30, 30)
        self.new_chat_btn.setToolTip("开始新对话")
        self.new_chat_btn.setStyleSheet("""
            QPushButton { 
                background: transparent; 
                border: none; 
                border-radius: 4px;
            }
            QPushButton:hover { 
                background-color: rgba(0, 0, 0, 0.1); 
            }
            QPushButton:pressed { 
                background-color: rgba(0, 0, 0, 0.2); 
            }
        """)
        self.new_chat_btn.clicked.connect(self.start_new_conversation)
        button_layout.addWidget(self.new_chat_btn)
        
        # 历史记录按钮
        self.history_btn = QPushButton()
        self.history_btn.setIcon(QIcon(str(ICONS_DIR / "history.png")))
        self.history_btn.setIconSize(QSize(13, 13))
        self.history_btn.setMaximumSize(30, 30)
        self.history_btn.setToolTip("查看历史对话")
        self.history_btn.setStyleSheet("""
            QPushButton { 
                background: transparent; 
                border: none; 
                border-radius: 4px;
            }
            QPushButton:hover { 
                background-color: rgba(0, 0, 0, 0.1); 
            }
            QPushButton:pressed { 
                background-color: rgba(0, 0, 0, 0.2); 
            }
        """)
        self.history_btn.clicked.connect(self.show_history_dialog)
        button_layout.addWidget(self.history_btn)
        
        # 设置按钮
        self.settings_btn = QPushButton()
        self.settings_btn.setIcon(QIcon(str(ICONS_DIR / "setting.png")))
        self.settings_btn.setIconSize(QSize(20, 20))
        self.settings_btn.setMaximumSize(30, 30)
        self.settings_btn.setToolTip("设置")
        self.settings_btn.setStyleSheet("""
            QPushButton { 
                background: transparent; 
                border: none; 
                border-radius: 4px;
            }
            QPushButton:hover { 
                background-color: rgba(0, 0, 0, 0.1); 
            }
            QPushButton:pressed { 
                background-color: rgba(0, 0, 0, 0.2); 
            }
        """)
        self.settings_btn.clicked.connect(self.show_settings_dialog)
        button_layout.addWidget(self.settings_btn)
        
        # 关闭按钮
        close_btn = QPushButton()
        close_btn.setIcon(QIcon(str(ICONS_DIR / "close.png")))
        close_btn.setIconSize(QSize(23, 23))
        close_btn.setMaximumSize(30, 30)
        close_btn.setToolTip("关闭聊天")
        close_btn.setStyleSheet("""
            QPushButton { 
                background: transparent; 
                border: none; 
                border-radius: 4px;
            }
            QPushButton:hover { 
                background-color: rgba(0, 0, 0, 0.1); 
            }
            QPushButton:pressed { 
                background-color: rgba(0, 0, 0, 0.2); 
            }
        """)
        close_btn.clicked.connect(self.hide_chat_panel)
        button_layout.addWidget(close_btn)
        
        header_layout.addLayout(button_layout)
        layout.addLayout(header_layout)
        
        # 网络搜索开关（移到单独一行）
        search_layout = QHBoxLayout()
        self.web_search_checkbox = QCheckBox("联网搜索")
        self.web_search_checkbox.setToolTip("开启后AI可以搜索最新的技术资料")
        self.web_search_checkbox.setStyleSheet("""
            QCheckBox {
                color: #333333;
                font-size: 12px;
                font-weight: normal;
            }
        """)
        self.web_search_checkbox.stateChanged.connect(self.on_web_search_changed)
        search_layout.addWidget(self.web_search_checkbox)
        search_layout.addStretch()
        layout.addLayout(search_layout)
        
        # 聊天显示区域
        self.chat_display = QTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setMinimumHeight(300)
        self.chat_display.setStyleSheet("""
            QTextEdit {
                background-color: #f8f9fa;
                border: 1px solid #dee2e6;
                border-radius: 5px;
                padding: 10px;
                font-family: 'Microsoft YaHei', sans-serif;
                text-align: left;
            }
        """)
        layout.addWidget(self.chat_display)
        
        # 输入区域
        input_layout = QVBoxLayout()
        
        # 快捷按钮
        shortcut_layout = QHBoxLayout()
        
        analyze_test_btn = QPushButton("分析测试数据")
        analyze_test_btn.clicked.connect(self.analyze_test_data)
        shortcut_layout.addWidget(analyze_test_btn)
        
        troubleshoot_btn = QPushButton("故障诊断")
        troubleshoot_btn.clicked.connect(self.troubleshoot_issues)
        shortcut_layout.addWidget(troubleshoot_btn)
        
        clear_btn = QPushButton("清除历史")
        clear_btn.clicked.connect(self.clear_chat_history)
        shortcut_layout.addWidget(clear_btn)
        
        input_layout.addLayout(shortcut_layout)
        
        # 消息输入框
        self.message_input = QTextEdit()
        self.message_input.setMaximumHeight(80)
        self.message_input.setPlaceholderText("输入您的问题...")
        self.message_input.setStyleSheet("""
            QTextEdit {
                border: 1px solid #ced4da;
                border-radius: 5px;
                padding: 8px;
                font-family: 'Microsoft YaHei', sans-serif;
            }
        """)
        input_layout.addWidget(self.message_input)
        
        # 发送按钮
        send_btn = QPushButton("发送 (Ctrl+Enter)")
        send_btn.clicked.connect(self.send_message)
        send_btn.setStyleSheet("""
            QPushButton {
                background-color: #007bff;
                color: white;
                border: none;
                border-radius: 5px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #0056b3;
            }
        """)
        input_layout.addWidget(send_btn)
        
        layout.addLayout(input_layout)
        
        # 设置快捷键
        self.message_input.installEventFilter(self)
        
        # 设置联网搜索复选框初始状态
        self.web_search_checkbox.setChecked(self.llm_chat.enable_web_search)
        
        # 加载历史对话
        self.load_chat_history()
    
    def closeEvent(self, event):
        """窗口关闭事件"""
        # 停止所有正在运行的AI请求
        if self.current_worker and self.current_worker.isRunning():
            self.current_worker.terminate()
            self.current_worker.wait()
        
        # 保存当前对话
        if hasattr(self, 'llm_chat') and self.llm_chat.conversation_history:
            try:
                self.llm_chat.save_history()
            except:
                pass
        
        super().closeEvent(event)
    
    def eventFilter(self, obj, event):
        """事件过滤器，处理快捷键"""
        if obj == self.message_input and event.type() == QKeyEvent.Type.KeyPress:
            if event.key() == Qt.Key_Return and event.modifiers() == Qt.ControlModifier:
                self.send_message()
                return True
        return super().eventFilter(obj, event)
    
    def on_web_search_changed(self, state):
        """网络搜索开关改变"""
        enabled = state == Qt.CheckState.Checked.value
        self.llm_chat.set_web_search(enabled)
        status = "已开启" if enabled else "已关闭"
        search_tip = "（AI将尝试提供最新信息）" if enabled else "（AI将使用训练数据回答）"
        self.add_system_message(f"联网搜索功能{status} {search_tip}")
    
    def hide_chat_panel(self):
        """隐藏聊天面板"""
        if self.main_window:
            self.main_window.toggle_chat_panel()
    
    def add_system_message(self, message):
        """添加系统消息"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.chat_display.append(f'<div style="color: #6c757d; font-size: 12px; margin: 5px 0;">[{timestamp}] {message}</div>')
    
    def add_user_message(self, message):
        """添加用户消息"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_message = message.replace('\n', '<br>')
        # 使用table布局确保右对齐
        self.chat_display.append(f'''
            <table style="width: 100%; margin: 10px 0; border-collapse: collapse;">
                <tr>
                    <td style="width: 20%;"></td>
                    <td style="text-align: right; vertical-align: top; width: 80%;">
                        <div style="background-color: #007bff; color: white; padding: 8px 12px; border-radius: 15px; display: inline-block; max-width: 100%; text-align: left;">
                            {formatted_message}
                        </div>
                        <div style="color: #6c757d; font-size: 10px; margin-top: 2px; text-align: right;">{timestamp}</div>
                    </td>
                </tr>
            </table>
        ''')
    
    def add_assistant_message(self, message):
        """添加AI助手消息"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # 将Markdown转换为HTML
        try:
            # 移除可能破坏QTextEdit的特殊字符
            safe_message = message.replace('```', '\n```\n') 
            html_message = markdown.markdown(message, extensions=['fenced_code', 'tables', 'nl2br'])
            # 简单的修复，使代码块在QTextEdit中显示得更好一点
            html_message = html_message.replace('<pre><code>', '<div style="background-color: #f1f3f5; padding: 10px; border-radius: 5px; font-family: Consolas, monospace;"><pre><code>')
            html_message = html_message.replace('</code></pre>', '</code></pre></div>')
        except Exception as e:
            html_message = message.replace('\n', '<br>')

        # 使用table布局确保左对齐
        self.chat_display.append(f'''
            <table style="width: 100%; margin: 10px 0; border-collapse: collapse;">
                <tr>
                    <td style="text-align: left; vertical-align: top; width: 80%;">
                        <div style="background-color: #e9ecef; color: #333; padding: 8px 12px; border-radius: 15px; display: inline-block; max-width: 100%; text-align: left;">
                            {html_message}
                        </div>
                        <div style="color: #6c757d; font-size: 10px; margin-top: 2px; text-align: left;">{timestamp}</div>
                    </td>
                    <td style="width: 20%;"></td>
                </tr>
            </table>
        ''')
    
    def send_message(self):
        """发送消息"""
        message = self.message_input.toPlainText().strip()
        if not message:
            return
        
        # 如果正在处理请求，则忽略新请求
        if self.current_worker and self.current_worker.isRunning():
            self.add_system_message("请等待当前请求完成...")
            return
        
        # 显示用户消息
        self.add_user_message(message)
        self.message_input.clear()
        
        # 获取测试上下文
        context = self.get_test_context()
        
        # 显示思考中状态
        self.add_system_message("🤔 AI正在思考...")
        
        # 使用后台线程获取AI回复
        self.get_ai_response(message, context)
    
    def get_ai_response(self, message, context):
        """获取AI回复（使用后台线程）"""
        # 停止之前的请求（如果有的话）
        if self.current_worker and self.current_worker.isRunning():
            self.current_worker.terminate()
            self.current_worker.wait()
        
        # 创建新的工作线程
        self.current_worker = ChatWorker(self.llm_chat, message, context)
        self.current_worker.response_ready.connect(self.on_ai_response_ready)
        self.current_worker.error_occurred.connect(self.on_ai_error)
        self.current_worker.finished.connect(self.on_ai_finished)
        
        # 启动线程
        self.current_worker.start()
        self.thinking_message_visible = True
    
    def on_ai_response_ready(self, response):
        """AI响应就绪回调"""
        if self.thinking_message_visible:
            self.remove_thinking_message()
        self.add_assistant_message(response)
    
    def on_ai_error(self, error_message):
        """AI错误回调"""
        if self.thinking_message_visible:
            self.remove_thinking_message()
        self.add_system_message(f"错误: {error_message}")
    
    def on_ai_finished(self):
        """AI线程完成回调"""
        self.thinking_message_visible = False
        if self.current_worker:
            self.current_worker.deleteLater()
            self.current_worker = None
    
    def remove_thinking_message(self):
        """移除思考中消息"""
        self.thinking_message_visible = False
        # 简单的方式：重新加载聊天历史（保持最后的用户消息）
        # 这里可以用更精确的HTML操作，但为了稳定性使用简单方法
        pass
    
    def get_test_context(self):
        """获取当前测试上下文信息"""
        if not self.main_window:
            return "功放测试系统"
        
        context_info = []
        context_info.append("功放测试系统环境")
        
        # 获取当前配置信息
        try:
            config = self.main_window.config
            if config:
                context_info.append(f"测试频率: {config.get('test_frequencies', 'N/A')}")
                context_info.append(f"驱动模式: {'启用' if config.get('driver_mode', {}).get('enabled', False) else '禁用'}")
                context_info.append(f"衰减器: {config.get('attenuator', {}).get('type', 'N/A')}")
        except:
            pass
        
        # 获取当前选中的标签页
        current_tab = 0
        try:
            current_tab = self.main_window.tab_widget.currentIndex()
            tab_names = ["仪器配置", "线损测量", "驱动映射", "功放测试", "数据可视化", "数据导出"]
            if 0 <= current_tab < len(tab_names):
                context_info.append(f"当前页面: {tab_names[current_tab]}")
        except:
            pass
        
        # 尝试把实际的测试数据（实时曲线或加载文件里的数据）附带给大模型
        try:
            data_to_analyze = None
            current_freq = None
            
            # 首先查看是否在数据可视化页面并加载了数据
            if current_tab == 4 and getattr(self.main_window, 'loaded_data', None):
                if hasattr(self.main_window, 'frequency_list') and self.main_window.frequency_list:
                    freq_idx = self.main_window.current_freq_index
                    if 0 <= freq_idx < len(self.main_window.frequency_list):
                        current_freq = str(self.main_window.frequency_list[freq_idx])
                        if current_freq in self.main_window.loaded_data:
                            data_to_analyze = self.main_window.loaded_data[current_freq]
            
            # 如果没有加载历史数据，查看是否有实时测量数据（内存中的测试数据）
            if not data_to_analyze and getattr(self.main_window, 'real_time_data', None):
                if hasattr(self.main_window, 'rt_frequency_list') and self.main_window.rt_frequency_list:
                    freq_idx = self.main_window.rt_current_freq_index
                    if 0 <= freq_idx < len(self.main_window.rt_frequency_list):
                        current_freq = str(self.main_window.rt_frequency_list[freq_idx])
                        if current_freq in self.main_window.real_time_data:
                            data_to_analyze = self.main_window.real_time_data[current_freq]
                            
            if data_to_analyze:
                context_info.append(f"\\n【抓取到的后台测试数据样例 (正在查看频率: {current_freq}GHz)】")
                sweep = data_to_analyze.get('sweep_data', {})
                
                # 为主功放格式数据
                if 'input_power_dut' in sweep and 'output_power_dut' in sweep:
                    pin = [round(x, 2) for x in sweep['input_power_dut']] if isinstance(sweep['input_power_dut'], list) else sweep['input_power_dut']
                    pout = [round(x, 2) for x in sweep['output_power_dut']] if isinstance(sweep['output_power_dut'], list) else sweep['output_power_dut']
                    gain = [round(x, 2) for x in sweep['gain']] if isinstance(sweep.get('gain'), list) else sweep.get('gain', [])
                    eff = [round(x, 2) for x in sweep['efficiency']] if isinstance(sweep.get('efficiency'), list) else sweep.get('efficiency', [])
                    
                    context_info.append(f"输入功率(Pin)数组: {pin}")
                    context_info.append(f"输出功率(Pout)数组: {pout}")
                    if gain:
                        context_info.append(f"增益(Gain)数组: {gain}")
                        context_info.append(f"核心指标 -> 最大增益: {max(gain):.2f} dB")
                    if eff:
                        context_info.append(f"效率(PAE)数组: {eff}")
                        context_info.append(f"核心指标 -> 最大效率: {max(eff):.2f} %")
                    if pout:
                        context_info.append(f"核心指标 -> 最大输出功率: {max(pout):.2f} dBm")
                # 驱动功放格式数据
                elif 'input_power_sg' in sweep and 'output_power_driver' in sweep:
                    pin = [round(x, 2) for x in sweep['input_power_sg']] if isinstance(sweep['input_power_sg'], list) else sweep['input_power_sg']
                    pout = [round(x, 2) for x in sweep['output_power_driver']] if isinstance(sweep['output_power_driver'], list) else sweep['output_power_driver']
                    gain = [round(x, 2) for x in sweep['gain']] if isinstance(sweep.get('gain'), list) else sweep.get('gain', [])
                    
                    context_info.append(f"信号源输入(Pin)数组: {pin}")
                    context_info.append(f"驱动输出(Pout)数组: {pout}")
                    if gain:
                        context_info.append(f"驱动增益(Gain)数组: {gain}")
                        context_info.append(f"核心指标 -> 最大驱动增益: {max(gain):.2f} dB")
        except Exception as e:
            context_info.append(f"【尝试抓取数据时遇到异常】: {e}")
            
        return "\\n".join(context_info)
    
    def analyze_test_data(self):
        """分析测试数据快捷按钮"""
        self.message_input.setPlainText("请帮我分析当前的测试数据，包括增益、效率、压缩点等关键指标，并指出可能存在的问题。")
        self.send_message()
    
    def troubleshoot_issues(self):
        """故障诊断快捷按钮"""
        self.message_input.setPlainText("我在测试过程中遇到了问题，请帮我进行故障诊断和排查。可能的问题包括功率不稳定、增益异常、效率偏低等。")
        self.send_message()
    
    def clear_chat_history(self):
        """清除聊天历史"""
        reply = QMessageBox.question(self, "确认", "确定要清除所有聊天历史吗？", 
                                   QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.llm_chat.clear_history()
            self.chat_display.clear()
            self.add_system_message("聊天历史已清除")
    
    def start_new_conversation(self):
        """开始新对话"""
        if self.llm_chat.conversation_history:
            reply = QMessageBox.question(self, "新对话", "确定要开始新对话吗？当前对话将被保存。", 
                                       QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return
        
        try:
            # 保存当前对话到带时间戳的文件
            if self.llm_chat.conversation_history:
                from datetime import datetime
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_file = PROJECT_ROOT / f"chat_history_{timestamp}.json"
                
                # 先保存到备份文件
                import json
                with open(backup_file, 'w', encoding='utf-8') as f:
                    json.dump(self.llm_chat.conversation_history, f, 
                             ensure_ascii=False, indent=2)
                
                self.add_system_message(f"✅ 对话已保存为: {backup_file}")
            
            # 清空显示区域
            QTimer.singleShot(1000, self._clear_and_start_new)  # 延迟1秒后清空
            
        except Exception as e:
            QMessageBox.warning(self, "保存失败", f"保存当前对话失败: {str(e)}")
    
    def _clear_and_start_new(self):
        """清空并开始新对话（延迟执行）"""
        # 清空显示区域
        self.chat_display.clear()
        
        # 重置对话历史
        self.llm_chat.conversation_history = []
        
        # 显示新对话开始消息
        self.add_system_message("🆕 已开始新对话")
        
        # 显示欢迎消息
        model_name = getattr(self.llm_chat, 'model_name', '').lower()
        if 'qwen' in model_name:
            model_display_name = 'Qwen'
        elif 'deepseek' in model_name:
            model_display_name = 'DeepSeek'
        else:
            model_display_name = getattr(self.llm_chat, 'model_name', 'AI')
            
        welcome_msg = f"你好！我是{model_display_name} AI助手，专门为功放测试系统提供技术支持。我可以帮你：\n\n• 分析测试数据和结果\n• 诊断测试过程中的问题\n• 提供功放设计和测试建议\n• 解答技术疑问\n\n有什么需要帮助的吗？"
        self.add_assistant_message(welcome_msg)
    
    def show_history_dialog(self):
        """显示历史对话选择对话框"""
        dialog = ChatHistoryDialog(self)
        if dialog.exec() == QDialog.Accepted:
            selected_history = dialog.get_selected_history()
            if selected_history:
                self.load_selected_history(selected_history)
    
    def show_settings_dialog(self):
        """显示设置对话框"""
        dialog = ChatSettingsDialog(self.llm_chat, self)
        dialog.exec()
    
    def load_selected_history(self, history_data):
        """加载选定的历史对话"""
        self.chat_display.clear()
        self.llm_chat.conversation_history = history_data
        self.load_chat_history()
        self.add_system_message("已加载历史对话")
    
    def load_chat_history(self):
        """加载聊天历史"""
        try:
            for msg in self.llm_chat.conversation_history[-20:]:  # 显示最近20条
                if msg["role"] == "user":
                    self.add_user_message(msg["content"])
                elif msg["role"] == "assistant":
                    self.add_assistant_message(msg["content"])
        except Exception as e:
            print(f"加载聊天历史失败: {e}")


class ConnectionDialog(QDialog):
    """连接说明对话框"""
    def __init__(self, diagram_type: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("连接说明")
        self.setModal(True)
        self.resize(800, 600)
        
        layout = QVBoxLayout(self)
        
        # 创建对应的连接图
        if diagram_type == 'cable_loss_path1':
            fig = ConnectionDiagram.create_cable_loss_path1()
        elif diagram_type == 'cable_loss_path2':
            fig = ConnectionDiagram.create_cable_loss_path2()
        elif diagram_type == 'driver_mapping':
            fig = ConnectionDiagram.create_driver_mapping()
        elif diagram_type == 'amplifier_test':
            fig = ConnectionDiagram.create_amplifier_test()
        elif diagram_type == 'amplifier_test_no_driver':
            fig = ConnectionDiagram.create_amplifier_test_no_driver()
        else:
            fig = Figure()
            
        # 显示图表
        canvas = FigureCanvas(fig)
        layout.addWidget(canvas)
        
        # 添加确认按钮
        button_layout = QHBoxLayout()
        self.ok_btn = QPushButton("我已正确连接")
        self.cancel_btn = QPushButton("取消")
        
        self.ok_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)
        
        button_layout.addWidget(self.cancel_btn)
        button_layout.addWidget(self.ok_btn)
        layout.addLayout(button_layout)


class WorkerSignals(QObject):
    """工作线程的信号类"""
    finished = Signal()
    error = Signal(str)
    result = Signal(object)
    progress = Signal(int)
    message = Signal(str)
    data_update = Signal(dict)
    step_pause = Signal(str)  # 步骤暂停信号


class BaseWorker(QThread):
    """基础工作线程类"""
    def __init__(self):
        super().__init__()
        self.signals = WorkerSignals()
        self.should_stop = False
        
    def stop(self):
        self.should_stop = True
        
    def emit_message(self, message: str):
        self.signals.message.emit(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")


class InstrumentWorker(BaseWorker):
    """仪器连接和配置工作线程"""
    def __init__(self, config_path: str):
        super().__init__()
        self.config_path = config_path
        
    def run(self):
        try:
            self.emit_message("正在初始化仪器控制...")
            self.signals.progress.emit(25)
            self.instrument_ctrl = InstrumentControl(self.config_path)
            self.signals.progress.emit(75)
            self.emit_message("仪器连接成功！")
            self.signals.progress.emit(100)
            self.signals.result.emit(self.instrument_ctrl)
            self.signals.finished.emit()
        except Exception as e:
            self.signals.error.emit(f"仪器连接失败: {str(e)}")


class CableLossWorker(BaseWorker):
    """线损测量工作线程，支持分步骤测量"""
    def __init__(self, config_path: str):
        super().__init__()
        self.config_path = config_path
        self.loss_measurement = None
        
    def run(self):
        try:
            self.emit_message("开始线损测量...")
            self.loss_measurement = EnhancedCableLossMeasurement(
                self.config_path,
                progress_callback=self.signals.progress.emit,
                message_callback=self.signals.message.emit
            )
            # 设置步骤暂停回调
            self.loss_measurement.set_step_pause_callback(self.on_step_pause)
            # 开始第一步测量
            self.loss_measurement.measure_all_frequencies()
        except Exception as e:
            self.signals.error.emit(f"线损测量失败: {str(e)}")
            
    def on_step_pause(self, message):
        """步骤暂停回调 - 通知主线程需要用户操作"""
        self.signals.step_pause.emit(message)
        
    def continue_measurement(self):
        """继续第二步测量"""
        if self.loss_measurement:
            self.loss_measurement.continue_to_step2()
            self.emit_message("线损测量完成！")
            self.signals.finished.emit()
            
    def stop(self):
        """停止线损测量并立即关闭仪器"""
        super().stop()  # 设置should_stop标志
        if self.loss_measurement:
            self.loss_measurement.stop_measurement()  # 停止测量类
            # 立即关闭仪器输出
            try:
                if hasattr(self.loss_measurement, 'inst_ctrl') and self.loss_measurement.inst_ctrl:
                    self.loss_measurement.inst_ctrl.rf_output_off()
                    self.loss_measurement.inst_ctrl.power_off_sequence()
                    self.emit_message("紧急停止：已关闭信号源输出和电源")
            except Exception as e:
                self.emit_message(f"紧急停止时关闭仪器失败: {str(e)}")
        self.quit()  # 强制退出线程


class DriverMappingWorker(BaseWorker):
    """驱动映射工作线程"""
    def __init__(self, config_path: str):
        super().__init__()
        self.config_path = config_path
        self.mapping = None
        
    def run(self):
        try:
            self.emit_message("开始驱动功放映射测量...")
            self.mapping = EnhancedDriverPowerMapping(
                self.config_path,
                progress_callback=self.signals.progress.emit,
                message_callback=self.signals.message.emit,
                data_callback=self.signals.data_update.emit
            )
            self.mapping.measure_all_frequencies()
            self.emit_message("驱动功放映射测量完成！")
            self.signals.finished.emit()
        except Exception as e:
            self.signals.error.emit(f"驱动映射测量失败: {str(e)}")
            
    def stop(self):
        """停止驱动映射测量并立即关闭仪器"""
        super().stop()  # 设置should_stop标志
        if self.mapping:
            self.mapping.stop_measurement()  # 停止测量类
            # 立即关闭仪器输出
            try:
                if hasattr(self.mapping, 'inst_ctrl') and self.mapping.inst_ctrl:
                    self.mapping.inst_ctrl.rf_output_off()
                    self.mapping.inst_ctrl.power_off_sequence()
                    self.emit_message("紧急停止：已关闭信号源输出和电源")
            except Exception as e:
                self.emit_message(f"紧急停止时关闭仪器失败: {str(e)}")
        self.quit()  # 强制退出线程


class AmplifierWorker(BaseWorker):
    """功放测试工作线程"""
    def __init__(self, config_path: str):
        super().__init__()
        self.config_path = config_path
        self.amp_measurement = None
        
    def run(self):
        try:
            self.emit_message("开始主功放测量...")
            self.amp_measurement = EnhancedAmplifierMeasurement(
                self.config_path,
                progress_callback=self.signals.progress.emit,
                message_callback=self.signals.message.emit,
                data_callback=self.signals.data_update.emit
            )
            self.amp_measurement.measure_all_frequencies()
            self.emit_message("主功放测量完成！")
            self.signals.finished.emit()
        except Exception as e:
            self.signals.error.emit(f"主功放测量失败: {str(e)}")
            
    def stop(self):
        """停止测量并立即关闭仪器"""
        super().stop()  # 设置should_stop标志
        if self.amp_measurement:
            self.amp_measurement.stop_measurement()  # 停止测量类
            # 立即关闭仪器输出
            try:
                if hasattr(self.amp_measurement, 'inst_ctrl') and self.amp_measurement.inst_ctrl:
                    self.amp_measurement.inst_ctrl.rf_output_off()
                    self.amp_measurement.inst_ctrl.power_off_sequence()
                    self.emit_message("紧急停止：已关闭信号源输出和电源")
            except Exception as e:
                self.emit_message(f"紧急停止时关闭仪器失败: {str(e)}")
        self.quit()  # 强制退出线程


class RealTimePlotWidget(QWidget):
    """实时数据可视化控件"""
    # 定义信号
    prev_clicked = Signal()
    next_clicked = Signal()
    
    def __init__(self, show_nav_buttons=False):
        super().__init__()
        self.show_nav_buttons = show_nav_buttons
        self.init_ui()
        
    def init_ui(self):
        layout = QVBoxLayout(self)
        
        # 创建滚动区域
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        
        # 创建包含matplotlib图形的容器widget
        canvas_widget = QWidget()
        canvas_layout = QVBoxLayout(canvas_widget)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        
        # 1) 放大整幅图（例如 18x12 英寸，120 dpi）
        self.figure = Figure(figsize=(3, 5), dpi=85)
        self.canvas = FigureCanvas(self.figure)
        # 2) 让 Canvas 的最小像素尺寸跟 Figure 对齐（避免显示太小）
        W = int(self.figure.get_figwidth() * self.figure.get_dpi())
        H = int(self.figure.get_figheight() * self.figure.get_dpi())
        self.canvas.setMinimumSize(W, H)

        canvas_layout.addWidget(self.canvas)
        
        # 将canvas widget放入滚动区域
        scroll_area.setWidget(canvas_widget)
        
        if self.show_nav_buttons:
            # 创建一个容器来组合滚动区域和导航按钮
            container_widget = QWidget()
            container_layout = QVBoxLayout(container_widget)
            container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.setSpacing(0)
            
            # 创建导航按钮行 - 左对齐
            nav_row = QWidget()
            nav_row_layout = QHBoxLayout(nav_row)
            nav_row_layout.setContentsMargins(5, 5, 5, 5)
            nav_row_layout.setSpacing(2)  # 按钮之间有小间距
            
            self.nav_prev_btn = QPushButton("◀")
            self.nav_prev_btn.setMaximumSize(30, 30)
            self.nav_prev_btn.setMinimumSize(30, 30)
            self.nav_prev_btn.setStyleSheet("""
                QPushButton { 
                    background-color: #4CAF50; 
                    color: white;
                    border: 2px solid #45a049; 
                    border-radius: 5px; 
                    font-size: 14px;
                    font-weight: bold;
                }
                QPushButton:hover { 
                    background-color: #45a049; 
                    border-color: #3e8e41;
                }
                QPushButton:pressed { 
                    background-color: #3e8e41; 
                }
                QPushButton:disabled {
                    background-color: #cccccc;
                    color: #666666;
                    border-color: #999999;
                }
            """)
            self.nav_prev_btn.clicked.connect(self.prev_clicked.emit)
            nav_row_layout.addWidget(self.nav_prev_btn)
            
            self.nav_next_btn = QPushButton("▶")
            self.nav_next_btn.setMaximumSize(30, 30)
            self.nav_next_btn.setMinimumSize(30, 30)
            self.nav_next_btn.setStyleSheet("""
                QPushButton { 
                    background-color: #4CAF50; 
                    color: white;
                    border: 2px solid #45a049; 
                    border-radius: 5px; 
                    font-size: 14px;
                    font-weight: bold;
                }
                QPushButton:hover { 
                    background-color: #45a049; 
                    border-color: #3e8e41;
                }
                QPushButton:pressed { 
                    background-color: #3e8e41; 
                }
                QPushButton:disabled {
                    background-color: #cccccc;
                    color: #666666;
                    border-color: #999999;
                }
            """)
            self.nav_next_btn.clicked.connect(self.next_clicked.emit)
            nav_row_layout.addWidget(self.nav_next_btn)
            
            nav_row_layout.addStretch()  # 保持按钮在左边
            nav_row.setMaximumHeight(40)
            
            # 将导航按钮和滚动区域组合
            container_layout.addWidget(nav_row)
            container_layout.addWidget(scroll_area)
            
            layout.addWidget(container_widget)
        else:
            # 不显示导航按钮，直接添加滚动区域
            layout.addWidget(scroll_area)
        
        # 保存滚动区域的引用以便在wheelEvent中使用
        self.scroll_area = scroll_area
        
        # 启用鼠标滚轮滚动支持 - 设置为接受所有焦点
        self.setFocusPolicy(Qt.WheelFocus)
        self.canvas.setFocusPolicy(Qt.WheelFocus)
        
        # 确保鼠标跟踪启用
        self.setMouseTracking(True)
        self.canvas.setMouseTracking(True)
        
        # 为canvas安装事件过滤器以捕获滚轮事件
        self.canvas.installEventFilter(self)
        
        # 初始化子图
        self.ax1 = self.figure.add_subplot(221)
        self.ax2 = self.figure.add_subplot(222)  
        self.ax3 = self.figure.add_subplot(223)
        self.ax4 = self.figure.add_subplot(224)
        

        # 使用subplots_adjust进行精细布局控制 - 在滚动区域中提供更充足的空间
        self.figure.subplots_adjust(
            left=0.08,    # 左边距
            bottom=0.10,  # 下边距 - 增加以留出坐标轴标签空间
            right=0.95,   # 右边距
            top=0.90,     # 上边距 - 减少以留出标题空间
            wspace=0.20,  # 子图间水平间距 - 减小间距让图更大
            hspace=0.45   # 子图间垂直间距 - 减小间距让图更大
        )
        
        # 设置初始图表
        self.setup_empty_plots()
        
    def setup_empty_plots(self):
        """设置空的初始图表"""
        # 确保中文字体设置 - 减小字体避免重叠
        title_font = {'family': 'Microsoft YaHei', 'size': 11}
        label_font = {'family': 'Microsoft YaHei', 'size': 8}
        
        self.ax1.set_title('输入功率 vs 输出功率', fontdict=title_font)
        self.ax1.set_xlabel('输入功率 (dBm)', fontdict=label_font)
        self.ax1.set_ylabel('输出功率 (dBm)', fontdict=label_font)
        self.ax1.grid(True)
        self.ax1.tick_params(labelsize=7)
        
        self.ax2.set_title('输出功率 vs 增益', fontdict=title_font)
        self.ax2.set_xlabel('输出功率 (dBm)', fontdict=label_font)
        self.ax2.set_ylabel('增益 (dB)', fontdict=label_font)
        self.ax2.grid(True)
        self.ax2.tick_params(labelsize=7)
        
        self.ax3.set_title('输出功率 vs 效率', fontdict=title_font)
        self.ax3.set_xlabel('输出功率 (dBm)', fontdict=label_font)
        self.ax3.set_ylabel('效率 (%)', fontdict=label_font)
        self.ax3.grid(True)
        self.ax3.tick_params(labelsize=7)
        
        self.ax4.set_title('DC功耗', fontdict=title_font)
        self.ax4.set_xlabel('测量点', fontdict=label_font)
        self.ax4.set_ylabel('DC功耗 (W)', fontdict=label_font)
        self.ax4.grid(True)
        self.ax4.tick_params(labelsize=7)
        
        self.canvas.draw()
        
    def update_plot(self, data: Dict[str, Any]):
        """更新实时图表"""
        try:
            # 确保中文字体设置 - 减小字体避免重叠
            title_font = {'family': 'Microsoft YaHei', 'size': 11}
            label_font = {'family': 'Microsoft YaHei', 'size': 8}
            
            # 清除之前的图表
            self.ax1.clear()
            self.ax2.clear()
            self.ax3.clear()
            self.ax4.clear()
            
            # 根据数据类型绘制不同的图表
            if 'sweep_data' in data:
                sweep = data['sweep_data']
                frequency = data.get('frequency', 'Unknown')
                
                # Pin vs Pout
                if 'input_power_dut' in sweep and 'output_power_dut' in sweep:
                    self.ax1.plot(sweep['input_power_dut'], sweep['output_power_dut'], 'bo-')
                elif 'input_power_sg' in sweep and 'output_power_driver' in sweep:
                    self.ax1.plot(sweep['input_power_sg'], sweep['output_power_driver'], 'ro-')
                
                self.ax1.set_xlabel('输入功率 (dBm)', fontdict=label_font)
                self.ax1.set_ylabel('输出功率 (dBm)', fontdict=label_font)
                self.ax1.set_title(f'输入 vs 输出功率 @ {frequency} GHz', fontdict=title_font)
                self.ax1.grid(True)
                self.ax1.tick_params(labelsize=7)
                
                # 增益图 - 区分不同数据类型
                if 'output_power_dut' in sweep and 'gain' in sweep:
                    # 功放测试数据：输出功率 vs 增益
                    self.ax2.plot(sweep['output_power_dut'], sweep['gain'], 'ro-')
                    self.ax2.set_xlabel('输出功率 (dBm)', fontdict=label_font)
                    self.ax2.set_ylabel('增益 (dB)', fontdict=label_font)
                    self.ax2.set_title(f'输出功率 vs 增益 @ {frequency} GHz', fontdict=title_font)
                elif 'input_power_sg' in sweep and 'gain' in sweep:
                    # 驱动映射数据：输入功率 vs 增益
                    self.ax2.plot(sweep['input_power_sg'], sweep['gain'], 'go-')
                    self.ax2.set_xlabel('输入功率 (dBm)', fontdict=label_font)
                    self.ax2.set_ylabel('增益 (dB)', fontdict=label_font)
                    self.ax2.set_title(f'输入功率 vs 增益 @ {frequency} GHz', fontdict=title_font)
                elif 'gain' in sweep:
                    # 其他情况：测量点 vs 增益
                    self.ax2.plot(range(len(sweep['gain'])), sweep['gain'], 'ro-')
                    self.ax2.set_xlabel('测量点', fontdict=label_font)
                    self.ax2.set_ylabel('增益 (dB)', fontdict=label_font)
                    self.ax2.set_title(f'增益 @ {frequency} GHz', fontdict=title_font)
                
                self.ax2.grid(True)
                self.ax2.tick_params(labelsize=7)
                
                # Pout vs Efficiency
                if 'output_power_dut' in sweep and 'efficiency' in sweep:
                    self.ax3.plot(sweep['output_power_dut'], sweep['efficiency'], 'go-')
                    self.ax3.set_xlabel('输出功率 (dBm)', fontdict=label_font)
                    self.ax3.set_ylabel('效率 (%)', fontdict=label_font)
                    self.ax3.set_title(f'输出功率 vs 效率 @ {frequency} GHz', fontdict=title_font)
                    self.ax3.grid(True)
                    self.ax3.tick_params(labelsize=7)
                
                # DC Power
                if 'dc_power' in sweep:
                    self.ax4.plot(range(len(sweep['dc_power'])), sweep['dc_power'], 'mo-')
                    self.ax4.set_xlabel('测量点', fontdict=label_font)
                    self.ax4.set_ylabel('DC功耗 (W)', fontdict=label_font)
                    self.ax4.set_title(f'DC功耗 @ {frequency} GHz', fontdict=title_font)
                    self.ax4.grid(True)
                    self.ax4.tick_params(labelsize=7)
                
            # 重新应用布局调整以避免重叠 - 在滚动区域中提供更充足的空间
            self.figure.subplots_adjust(
                left=0.08,    # 左边距
                bottom=0.10,  # 下边距 - 增加以留出坐标轴标签空间
                right=0.95,   # 右边距
                top=0.90,     # 上边距 - 减少以留出标题空间
                wspace=0.20,  # 子图间水平间距 - 减小间距让图更大
                hspace=0.45   # 子图间垂直间距 - 减小间距让图更大
            )
            self.canvas.draw()
            
        except Exception as e:
            print(f"Plot update error: {e}")
    
    def wheelEvent(self, event):
        """处理鼠标滚轮事件，支持滚动条操作"""
        # 检查是否有滚动区域
        if not hasattr(self, 'scroll_area') or not self.scroll_area:
            event.ignore()
            return
            
        # 获取滚动条
        scrollbar = self.scroll_area.verticalScrollBar()
        
        # 检查滚动条是否可见和可用
        if not scrollbar.isVisible():
            event.ignore()
            return
        
        # 计算滚动步长 (可以调整这个值来控制滚动速度)
        scroll_step = 50  # 增加滚动步长使滚动更明显
        
        # 获取当前位置和范围
        current_value = scrollbar.value()
        min_value = scrollbar.minimum()
        max_value = scrollbar.maximum()
        
        # 向上滚动
        if event.angleDelta().y() > 0:
            new_value = max(min_value, current_value - scroll_step)
        # 向下滚动
        else:
            new_value = min(max_value, current_value + scroll_step)
        
        # 设置新值
        scrollbar.setValue(new_value)
        
        # 接受事件，阻止进一步传播
        event.accept()
    
    def eventFilter(self, source, event):
        """事件过滤器，用于处理canvas上的滚轮事件"""
        # 检查是否是canvas的滚轮事件
        if source == self.canvas and event.type() == QEvent.Type.Wheel:
            # 调用我们自己的滚轮处理方法
            self.wheelEvent(event)
            return True  # 表示事件已处理
        
        # 对于其他事件，调用父类的处理方法
        return super().eventFilter(source, event)


class MainWindow(QMainWindow):
    """主窗口类"""
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PA自动测试系统 v1.0")
        self.setGeometry(100, 100, 1600, 1000)
        
        # 初始化变量
        self.config = {}
        self.instrument_ctrl = None
        self.current_worker = None
        self.emergency_stop = False
        
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
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                self.config = json.load(f)
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
        
        # 创建各个选项卡
        self.create_config_tab()
        self.create_cable_loss_tab()
        self.create_driver_mapping_tab()
        self.create_amplifier_test_tab()
        self.create_visualization_tab()
        self.create_data_export_tab()
        
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
        
        self.tab_widget.addTab(tab, "仪器配置")
    
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
        
    def create_cable_loss_tab(self):
        """创建线损测量选项卡"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # 连接说明
        instruction_group = QGroupBox("连接说明")
        instruction_layout = QVBoxLayout(instruction_group)
        
        # 添加连接图示和说明文字
        instruction_text = QTextEdit()
        instruction_text.setMaximumHeight(200)
        instruction_text.setHtml("""
        <h3>线损测量连接说明:</h3>
        <p><b>步骤1 - 路径1测量:</b></p>
        <p>信号源 → 线缆① → 衰减器 → 线缆② → 频谱仪</p>
        <br>
        <p><b>步骤2 - 路径2测量:</b></p>  
        <p>信号源 → 线缆① → 线缆③ → 线缆④ → 衰减器 → 线缆② → 频谱仪</p>
        <br>
        <p style="color: red;"><b>注意:</b> 每个步骤会提示您重新连接线缆，请按提示操作</p>
        """)
        instruction_layout.addWidget(instruction_text)
        
        # 连接图按钮
        diagram_layout = QHBoxLayout()
        self.show_path1_btn = QPushButton("查看路径1连接图")
        self.show_path1_btn.clicked.connect(lambda: self.show_connection_diagram('cable_loss_path1'))
        diagram_layout.addWidget(self.show_path1_btn)
        
        self.show_path2_btn = QPushButton("查看路径2连接图")
        self.show_path2_btn.clicked.connect(lambda: self.show_connection_diagram('cable_loss_path2'))
        diagram_layout.addWidget(self.show_path2_btn)
        
        instruction_layout.addLayout(diagram_layout)
        layout.addWidget(instruction_group)
        
        # 控制按钮
        control_group = QGroupBox("测量控制")
        control_layout = QHBoxLayout(control_group)
        
        self.cable_loss_btn = QPushButton("开始线损测量")
        self.cable_loss_btn.clicked.connect(self.start_cable_loss_measurement)
        control_layout.addWidget(self.cable_loss_btn)
        
        # 加载结果按钮
        self.load_cable_results_btn = QPushButton("加载测量结果")
        self.load_cable_results_btn.clicked.connect(self.load_cable_loss_results)
        control_layout.addWidget(self.load_cable_results_btn)
        
        layout.addWidget(control_group)
        
        # 结果显示
        result_group = QGroupBox("测量结果")
        result_layout = QVBoxLayout(result_group)
        
        self.cable_loss_table = QTableWidget()
        self.cable_loss_table.setColumnCount(5)
        self.cable_loss_table.setHorizontalHeaderLabels(['频率(GHz)', '线缆1(dB)', '线缆2(dB)', '线缆3(dB)', '线缆4(dB)'])
        result_layout.addWidget(self.cable_loss_table)
        
        layout.addWidget(result_group)
        
        self.tab_widget.addTab(tab, "线损测量")
        
    def create_driver_mapping_tab(self):
        """创建驱动映射选项卡"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # 连接说明
        instruction_group = QGroupBox("连接说明")
        instruction_layout = QVBoxLayout(instruction_group)
        
        instruction_text = QTextEdit()
        instruction_text.setMaximumHeight(120)
        instruction_text.setHtml("""
        <h3>驱动功放映射测量连接说明:</h3>
        <p>信号源 → 线缆① → 驱动功放 → 线缆③ → 衰减器 → 线缆② → 频谱仪</p>
        """)
        instruction_layout.addWidget(instruction_text)
        
        # 连接图按钮
        self.show_driver_diagram_btn = QPushButton("查看连接图")
        self.show_driver_diagram_btn.clicked.connect(lambda: self.show_connection_diagram('driver_mapping'))
        instruction_layout.addWidget(self.show_driver_diagram_btn)
        
        layout.addWidget(instruction_group)
        
        # 控制按钮
        control_group = QGroupBox("测量控制")
        control_layout = QHBoxLayout(control_group)
        
        self.driver_mapping_btn = QPushButton("开始驱动映射")
        self.driver_mapping_btn.clicked.connect(self.start_driver_mapping)
        control_layout.addWidget(self.driver_mapping_btn)
        
        # 紧急停止按钮
        self.driver_emergency_stop_btn = QPushButton("紧急停止")
        self.driver_emergency_stop_btn.setStyleSheet("QPushButton { background-color: #dc3545; color: white; font-weight: bold; }")
        self.driver_emergency_stop_btn.setEnabled(False)  # 初始状态禁用
        self.driver_emergency_stop_btn.clicked.connect(self.emergency_stop_driver_mapping)
        control_layout.addWidget(self.driver_emergency_stop_btn)
        
        layout.addWidget(control_group)
        
        # 实时可视化 - 显示导航按钮
        self.driver_plot_widget = RealTimePlotWidget(show_nav_buttons=True)
        # 连接频点切换信号
        self.driver_plot_widget.prev_clicked.connect(self.rt_prev_frequency)
        self.driver_plot_widget.next_clicked.connect(self.rt_next_frequency)
        layout.addWidget(self.driver_plot_widget)
        
        self.tab_widget.addTab(tab, "驱动映射")
        
    def create_amplifier_test_tab(self):
        """创建功放测试选项卡"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        
        # 连接说明
        instruction_group = QGroupBox("连接说明")
        instruction_layout = QVBoxLayout(instruction_group)
        
        self.instruction_text = QTextEdit()
        self.instruction_text.setMaximumHeight(120)
        self.update_amplifier_instruction_text()  # 根据驱动模式更新连接说明
        instruction_layout.addWidget(self.instruction_text)
        
        # 连接图按钮
        self.show_amp_diagram_btn = QPushButton("查看连接图")
        self.show_amp_diagram_btn.clicked.connect(self.show_amplifier_connection_diagram)
        instruction_layout.addWidget(self.show_amp_diagram_btn)
        
        layout.addWidget(instruction_group)
        
        # 控制按钮
        control_group = QGroupBox("测量控制")
        control_layout = QHBoxLayout(control_group)
        
        self.amplifier_test_btn = QPushButton("开始功放测试")
        self.amplifier_test_btn.clicked.connect(self.start_amplifier_test)
        control_layout.addWidget(self.amplifier_test_btn)
        
        # 紧急停止按钮
        self.emergency_stop_btn = QPushButton("紧急停止")
        self.emergency_stop_btn.setStyleSheet("QPushButton { background-color: red; color: white; font-weight: bold; font-size: 14px; }")
        self.emergency_stop_btn.clicked.connect(self.emergency_stop_test)
        control_layout.addWidget(self.emergency_stop_btn)
        
        layout.addWidget(control_group)
        
        # 实时可视化 - 显示导航按钮
        self.amplifier_plot_widget = RealTimePlotWidget(show_nav_buttons=True)
        # 连接频点切换信号
        self.amplifier_plot_widget.prev_clicked.connect(self.rt_prev_frequency)
        self.amplifier_plot_widget.next_clicked.connect(self.rt_next_frequency)
        layout.addWidget(self.amplifier_plot_widget)
        
        self.tab_widget.addTab(tab, "功放测试")
        
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
        
        self.tab_widget.addTab(tab, "数据可视化")
        
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
        
        self.tab_widget.addTab(tab, "数据导出")
        
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
        self.current_worker = InstrumentWorker(str(CONFIG_FILE))
        self.current_worker.signals.finished.connect(self.on_instrument_connected)
        self.current_worker.signals.error.connect(self.on_worker_error)
        self.current_worker.signals.message.connect(self.add_log_message)
        self.current_worker.signals.progress.connect(self.progress_bar.setValue)
        self.current_worker.start()
        
    def on_instrument_connected(self):
        """仪器连接完成"""
        self.connect_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.add_log_message("所有仪器连接成功！")
        
    def start_cable_loss_measurement(self):
        """开始线损测量"""
        # 显示连接确认对话框
        dialog = ConnectionDialog('cable_loss_path1', self)
        if dialog.exec() != QDialog.Accepted:
            return
            
        # 更新配置并保存到文件
        if not self.update_and_save_config():
            return
            
        self.add_log_message("开始线损测量...")
        self.cable_loss_btn.setEnabled(False)
        
        self.current_worker = CableLossWorker(str(CONFIG_FILE))
        self.current_worker.signals.finished.connect(lambda: self.on_measurement_finished(self.cable_loss_btn))
        self.current_worker.signals.error.connect(self.on_worker_error)
        self.current_worker.signals.message.connect(self.add_log_message)
        self.current_worker.signals.progress.connect(self.progress_bar.setValue)
        # 添加步骤暂停信号处理
        self.current_worker.signals.step_pause.connect(self.on_cable_loss_step_pause)
        self.current_worker.start()
        
    def on_cable_loss_step_pause(self, message):
        """处理线损测量步骤暂停"""
        # 显示第二步的连接确认对话框（带图示）
        dialog = ConnectionDialog('cable_loss_path2', self)
        
        if dialog.exec() == QDialog.Accepted:
            # 继续第二步测量
            if self.current_worker and hasattr(self.current_worker, 'continue_measurement'):
                self.current_worker.continue_measurement()
        else:
            # 用户取消，停止测量
            if self.current_worker:
                self.current_worker.stop()
            self.cable_loss_btn.setEnabled(True)
            self.progress_bar.setValue(0)
            self.add_log_message("线损测量已取消")
        
    def start_driver_mapping(self):
        """开始驱动映射"""
        # 显示连接确认对话框
        dialog = ConnectionDialog('driver_mapping', self)
        if dialog.exec() != QDialog.Accepted:
            return
            
        # 清除之前的实时测量历史数据
        self.clear_real_time_data()
            
        # 更新配置并保存到文件
        if not self.update_and_save_config():
            return
            
        self.add_log_message("开始驱动功放映射...")
        self.driver_mapping_btn.setEnabled(False)
        self.driver_emergency_stop_btn.setEnabled(True)  # 启用紧急停止按钮
        
        self.current_worker = DriverMappingWorker(str(CONFIG_FILE))
        self.current_worker.signals.finished.connect(lambda: self.on_measurement_finished(self.driver_mapping_btn))
        self.current_worker.signals.error.connect(self.on_worker_error)
        self.current_worker.signals.message.connect(self.add_log_message)
        self.current_worker.signals.progress.connect(self.progress_bar.setValue)
        self.current_worker.signals.data_update.connect(self.store_real_time_data)
        self.current_worker.start()
        
    def start_amplifier_test(self):
        """开始功放测试"""
        # 显示连接确认对话框
        dialog = ConnectionDialog('amplifier_test', self)
        if dialog.exec() != QDialog.Accepted:
            return
            
        # 清除之前的实时测量历史数据
        self.clear_real_time_data()
            
        # 更新配置并保存到文件
        if not self.update_and_save_config():
            return
            
        self.add_log_message("开始主功放测试...")
        self.amplifier_test_btn.setEnabled(False)
        self.emergency_stop_btn.setEnabled(True)  # 启用紧急停止按钮
        self.emergency_stop = False
        
        self.current_worker = AmplifierWorker(str(CONFIG_FILE))
        self.current_worker.signals.finished.connect(lambda: self.on_measurement_finished(self.amplifier_test_btn))
        self.current_worker.signals.error.connect(self.on_worker_error)
        self.current_worker.signals.message.connect(self.add_log_message)
        self.current_worker.signals.progress.connect(self.progress_bar.setValue)
        self.current_worker.signals.data_update.connect(self.store_real_time_data)
        self.current_worker.start()
        
    def emergency_stop_test(self):
        """紧急停止测试"""
        reply = QMessageBox.question(self, "紧急停止", "确定要紧急停止当前测试吗？",
                                   QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.emergency_stop = True
            if self.current_worker:
                self.current_worker.stop()
            self.add_log_message("用户执行紧急停止！")
            self.progress_bar.setValue(0)
    
    def emergency_stop_driver_mapping(self):
        """紧急停止驱动映射测试"""
        reply = QMessageBox.question(self, "紧急停止", "确定要紧急停止当前驱动映射测试吗？",
                                   QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.emergency_stop = True
            if self.current_worker:
                self.current_worker.stop()
            self.add_log_message("用户执行驱动映射紧急停止！")
            self.progress_bar.setValue(0)
            # 恢复按钮状态
            self.driver_mapping_btn.setEnabled(True)
            
    def on_measurement_finished(self, button):
        """测量完成"""
        button.setEnabled(True)
        self.progress_bar.setValue(100)
        self.add_log_message("测量完成！")
        
        # 禁用对应的紧急停止按钮
        if button == self.driver_mapping_btn:
            self.driver_emergency_stop_btn.setEnabled(False)
        elif button == self.amplifier_test_btn:
            if hasattr(self, 'emergency_stop_btn'):
                self.emergency_stop_btn.setEnabled(False)
        
        # 如果是线损测量完成，加载结果到表格
        if button == self.cable_loss_btn:
            self.load_cable_loss_results()
        
        # 刷新文件列表
        self.refresh_file_list()
        
    def on_worker_error(self, error_message):
        """工作线程错误处理"""
        self.add_log_message(f"错误: {error_message}")
        QMessageBox.critical(self, "错误", error_message)
        
        # 重新启用所有按钮
        self.connect_btn.setEnabled(True)
        self.cable_loss_btn.setEnabled(True)
        self.driver_mapping_btn.setEnabled(True)
        self.amplifier_test_btn.setEnabled(True)
        
        # 重置进度条
        self.progress_bar.setValue(0)
        
    def load_cable_loss_results(self):
        """加载线损测量结果到表格"""
        try:
            with open(CABLE_LOSS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            cable_losses = data.get('cable_losses', {})
            
            # 设置表格行数
            self.cable_loss_table.setRowCount(len(cable_losses))
            
            # 填充数据
            for row, (frequency, losses) in enumerate(cable_losses.items()):
                # 频率
                self.cable_loss_table.setItem(row, 0, QTableWidgetItem(f"{frequency}"))
                # 线缆1
                self.cable_loss_table.setItem(row, 1, QTableWidgetItem(f"{losses.get('cable1', 0):.3f}"))
                # 线缆2  
                self.cable_loss_table.setItem(row, 2, QTableWidgetItem(f"{losses.get('cable2', 0):.3f}"))
                # 线缆3
                self.cable_loss_table.setItem(row, 3, QTableWidgetItem(f"{losses.get('cable3', 0):.3f}"))
                # 线缆4
                self.cable_loss_table.setItem(row, 4, QTableWidgetItem(f"{losses.get('cable4', 0):.3f}"))
            
            # 调整列宽
            self.cable_loss_table.resizeColumnsToContents()
            
            self.add_log_message(f"已加载线损测量结果，共 {len(cable_losses)} 个频点")
            
        except FileNotFoundError:
            self.add_log_message("未找到线损测量结果文件")
        except Exception as e:
            self.add_log_message(f"加载线损测量结果失败: {e}")
        
    def update_config_from_ui(self):
        """从UI更新配置"""
        # 更新仪器地址
        if 'instruments' not in self.config:
            self.config['instruments'] = {}
            
        self.config['instruments']['signal_generator'] = {
            'address': self.sg_address.text(),
            'enabled': self.sg_enabled.isChecked()
        }
        self.config['instruments']['spectrum_analyzer'] = {
            'address': self.sa_address.text(),
            'enabled': self.sa_enabled.isChecked()
        }
        
        if 'power_supplies' not in self.config['instruments']:
            self.config['instruments']['power_supplies'] = {}
            
        # 保持现有的电源配置结构，只更新地址和启用状态
        ps_widgets = [
            ('PS1', self.ps1_address, self.ps1_enabled),
            ('PS2', self.ps2_address, self.ps2_enabled),
            ('PS3', self.ps3_address, self.ps3_enabled),
            ('PS4', self.ps4_address, self.ps4_enabled)
        ]
        for ps_name, address_widget, enabled_widget in ps_widgets:
            if ps_name not in self.config['instruments']['power_supplies']:
                self.config['instruments']['power_supplies'][ps_name] = {}
            self.config['instruments']['power_supplies'][ps_name]['address'] = address_widget.text()
            self.config['instruments']['power_supplies'][ps_name]['enabled'] = enabled_widget.isChecked()
            
        # 更新测试参数
        try:
            freq_list = eval(self.freq_edit.text())
            self.config['test_frequencies'] = freq_list
        except:
            pass
            
        self.config['signal_source'] = {
            'start_power': self.start_power.value(),
            'stop_power': self.stop_power.value(),
            'step': self.power_step.value()
        }
        
        self.config['compression_point'] = {'type': self.compression_combo.currentText()}
        self.config['attenuator'] = {'type': self.attenuator_combo.currentText()}
        self.config['driver_mode'] = {'enabled': self.driver_mode_check.isChecked()}
        
        # 更新DUT配置
        if 'dut_config' not in self.config:
            self.config['dut_config'] = {}
        self.config['dut_config']['max_input_power'] = self.max_input_power.value()
        
        # 根据PA单元数量设置电源数量
        pa_unit_count = int(self.pa_unit_count_combo.currentText())
        self.config['dut_config']['power_supply_count'] = pa_unit_count
        
        # 更新电源详细配置
        if hasattr(self, 'power_config_widgets'):
            if 'power_supplies' not in self.config['instruments']:
                self.config['instruments']['power_supplies'] = {}
                
            for ps_name, ps_widgets in self.power_config_widgets.items():
                if ps_name not in self.config['instruments']['power_supplies']:
                    self.config['instruments']['power_supplies'][ps_name] = {}
                
                # 不在此处更新地址，因为已经在上面从主要地址输入框更新过了
                # 注释掉以下行以防覆盖用户在“仪器连接配置”中修改的地址
                # self.config['instruments']['power_supplies'][ps_name]['address'] = ps_widgets['address'].text()
                
                # 更新通道配置
                if 'channels' not in self.config['instruments']['power_supplies'][ps_name]:
                    self.config['instruments']['power_supplies'][ps_name]['channels'] = {}
                
                for ch_name, ch_widgets in ps_widgets['channels'].items():
                    self.config['instruments']['power_supplies'][ps_name]['channels'][ch_name] = {
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
        
        # 更新电源分配配置
        if hasattr(self, 'power_assignment_widgets'):
            if 'power_supply_assignment' not in self.config:
                self.config['power_supply_assignment'] = {}
            
            # 驱动功放电源分配
            driver_enabled = self.power_assignment_widgets['driver_enabled'].isChecked()
            self.config['power_supply_assignment']['driver_amplifier'] = {
                'power_supply_count': 1 if driver_enabled else 0,
                'supplies': {}
            }
            
            if driver_enabled:
                driver_ps = self.power_assignment_widgets['driver_power'].currentText()
                self.config['power_supply_assignment']['driver_amplifier']['supplies']['main'] = {
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
            
            # 如果有PA Unit2，添加配置
            if pa_unit_count >= 2:
                unit2_ps = self.power_assignment_widgets['pa_unit2_power'].currentText()
                dut_supplies['peaking'] = {  # 保持peaking键名以兼容现有配置
                    'name': unit2_ps,
                    'channel': ['CH1', 'CH2']
                }
            
            # 如果有PA Unit3，添加配置
            if pa_unit_count >= 3:
                unit3_ps = self.power_assignment_widgets['pa_unit3_power'].currentText()
                dut_supplies['peaking2'] = {  # 保持peaking2键名以兼容现有配置
                    'name': unit3_ps,
                    'channel': ['CH1', 'CH2']
                }
            
            self.config['power_supply_assignment']['dut_amplifier'] = {
                'power_supply_count': pa_unit_count,
                'supplies': dut_supplies
            }
        
    def update_and_save_config(self):
        """更新UI配置并保存到文件"""
        self.update_config_from_ui()
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
            self.add_log_message("配置已更新并保存")
            return True
        except Exception as e:
            self.add_log_message(f"配置保存失败: {e}")
            return False
        
    def save_config(self):
        """保存配置"""
        self.update_config_from_ui()
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
            self.add_log_message("配置已保存")
            QMessageBox.information(self, "保存成功", "配置文件已保存")
        except Exception as e:
            self.add_log_message(f"配置保存失败: {e}")
            QMessageBox.warning(self, "保存失败", f"配置保存失败: {e}")
            
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
                temp_file = PROJECT_ROOT / 'temp_loaded_data.json'
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(report_data, f, indent=2, ensure_ascii=False)
                
                visualizer.create_summary_report(str(temp_file), original_filename)
                
                # 清理临时文件
                if temp_file.exists():
                    temp_file.unlink()
                    
                self.add_log_message("基于已加载数据生成测试报告")
            else:
                # 查找最新的测试数据文件
                dut_files = sorted(PROJECT_ROOT.glob('amplifier_measurement_*.json'), key=lambda x: x.stat().st_mtime)
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
            files = list(PROJECT_ROOT.glob(pattern))
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
                shutil.copy2(PROJECT_ROOT / filename, save_path)
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
                visualizer.generate_csv_report(str(PROJECT_ROOT / filename))
                
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
            visualizer.create_summary_report(str(PROJECT_ROOT / filename))
            
            self.add_log_message("PDF报告已生成在test_results文件夹中")
            QMessageBox.information(self, "导出成功", "PDF报告已生成在test_results文件夹中")
        except Exception as e:
            self.add_log_message(f"PDF导出失败: {e}")
            QMessageBox.warning(self, "导出失败", str(e))
        
    def closeEvent(self, event):
        """窗口关闭事件"""
        if self.current_worker and self.current_worker.isRunning():
            reply = QMessageBox.question(self, "退出", "测试正在进行中，确定要退出吗？",
                                       QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                if self.current_worker:
                    self.current_worker.stop()
                    self.current_worker.wait()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()


def main():
    app = QApplication(sys.argv)
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

