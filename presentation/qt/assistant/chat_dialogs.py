import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QPushButton, QSpinBox, QVBoxLayout)

from assistant.storage import (has_current_history, list_history_files, load_history_file,
                               load_search_api_config, save_search_api_config)


class ChatHistoryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择历史对话")
        self.setModal(True)
        self.resize(500, 400)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("选择要加载的历史对话:"))
        self.history_list = QListWidget()
        layout.addWidget(self.history_list)
        buttons = QHBoxLayout()
        self.load_btn = QPushButton("加载选定对话")
        self.delete_btn = QPushButton("删除选定对话")
        cancel = QPushButton("取消")
        self.load_btn.clicked.connect(self.accept)
        self.delete_btn.clicked.connect(self.delete_selected)
        cancel.clicked.connect(self.reject)
        for button in (self.load_btn, self.delete_btn, cancel): buttons.addWidget(button)
        layout.addLayout(buttons)
        self.load_history_list()

    def load_history_list(self):
        try:
            if has_current_history(): self.history_list.addItem("📝 当前对话")
            for path in sorted(list_history_files(), key=os.path.getmtime, reverse=True):
                item = self.history_list.addItem(f"💾 {os.path.basename(str(path))}")
                self.history_list.item(self.history_list.count() - 1).setData(Qt.UserRole, str(path))
            if not self.history_list.count(): self.history_list.addItem("📭 无历史记录")
        except Exception as error:
            print(f"加载历史记录失败: {error}")
            self.history_list.addItem("❌ 加载失败")

    def get_selected_history(self):
        item = self.history_list.currentItem()
        if not item: return None
        if item.text().startswith("📝"): return load_history_file()
        path = item.data(Qt.UserRole)
        return load_history_file(path) if path else None

    def delete_selected(self):
        item = self.history_list.currentItem()
        path = item.data(Qt.UserRole) if item else None
        if not path:
            QMessageBox.information(self, "无法删除", "请选择有效的历史记录进行删除。")
            return
        if QMessageBox.question(self, "删除确认", f"确定要删除对话记录吗？\n{os.path.basename(path)}") == QMessageBox.Yes:
            os.remove(path)
            self.history_list.takeItem(self.history_list.row(item))


class ChatSettingsDialog(QDialog):
    def __init__(self, llm_chat, parent=None):
        super().__init__(parent)
        self.llm_chat = llm_chat
        self.setWindowTitle("CHAT 设置")
        layout = QVBoxLayout(self)
        group = QGroupBox("AI模型设置")
        form = QFormLayout(group)
        self.server_url_edit = QLineEdit(llm_chat.server_url)
        self.model_name_edit = QLineEdit(llm_chat.model_name)
        self.temperature_spin = QDoubleSpinBox(); self.temperature_spin.setRange(0, 2); self.temperature_spin.setValue(llm_chat.temperature)
        self.max_tokens_spin = QSpinBox(); self.max_tokens_spin.setRange(100, 8000); self.max_tokens_spin.setValue(llm_chat.max_tokens)
        for label, widget in (("服务器地址:", self.server_url_edit), ("模型名称:", self.model_name_edit), ("温度 (创造性):", self.temperature_spin), ("最大Token数:", self.max_tokens_spin)): form.addRow(label, widget)
        layout.addWidget(group)
        self.auto_save_check = QCheckBox(); self.auto_save_check.setChecked(llm_chat.auto_save)
        self.history_limit_spin = QSpinBox(); self.history_limit_spin.setRange(10, 1000); self.history_limit_spin.setValue(llm_chat.history_limit)
        form.addRow("自动保存历史:", self.auto_save_check); form.addRow("历史记录上限:", self.history_limit_spin)
        self.bing_key_edit = QLineEdit(); self.google_key_edit = QLineEdit(); self.google_cx_edit = QLineEdit()
        self.bing_key_edit.setEchoMode(QLineEdit.Password)
        self.google_key_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Bing API密钥:", self.bing_key_edit)
        form.addRow("Google API密钥:", self.google_key_edit)
        form.addRow("Google搜索引擎ID:", self.google_cx_edit)
        self.load_search_config()
        buttons = QHBoxLayout(); self.test_btn = QPushButton("测试连接"); save = QPushButton("保存设置"); cancel = QPushButton("取消")
        self.test_btn.clicked.connect(self.test_connection)
        save.clicked.connect(self.save_settings); cancel.clicked.connect(self.reject)
        buttons.addWidget(self.test_btn); buttons.addWidget(save); buttons.addWidget(cancel); layout.addLayout(buttons)

    def test_connection(self):
        try:
            import requests
            response = requests.post(self.server_url_edit.text(), json={"model": self.model_name_edit.text(), "messages": [{"role": "user", "content": "ping"}], "stream": False}, timeout=10)
            if response.status_code != 200:
                QMessageBox.warning(self, "连接失败", f"服务器返回错误: {response.status_code}")
            else:
                QMessageBox.information(self, "连接成功", "AI服务连接正常。")
        except ImportError:
            QMessageBox.warning(self, "功能不可用", "AI 连接测试需要可选的 requests 依赖。")
        except Exception as error:
            QMessageBox.critical(self, "连接失败", f"无法连接到AI服务: {error}")

    def load_search_config(self):
        try:
            config = load_search_api_config()
            self.bing_key_edit.setText(config.get("bing_subscription_key", ""))
            self.google_key_edit.setText(config.get("google_api_key", ""))
            self.google_cx_edit.setText(config.get("google_cx", ""))
        except Exception as error:
            print(f"加载搜索API配置失败: {error}")

    def save_search_config(self):
        save_search_api_config({
            "bing_subscription_key": self.bing_key_edit.text(),
            "google_api_key": self.google_key_edit.text(),
            "google_cx": self.google_cx_edit.text(),
            "note": "请在此配置您的搜索API密钥。如果不配置，将使用DuckDuckGo免费搜索（功能有限）。",
        })

    def save_settings(self):
        try:
            self.save_search_config()
            self.llm_chat.update_settings(server_url=self.server_url_edit.text(), model_name=self.model_name_edit.text(), temperature=self.temperature_spin.value(), max_tokens=self.max_tokens_spin.value(), auto_save=self.auto_save_check.isChecked(), history_limit=self.history_limit_spin.value())
            if getattr(self.llm_chat, "function_handler", None):
                self.llm_chat.function_handler.configure_api_keys(bing_key=self.bing_key_edit.text() or None, google_key=self.google_key_edit.text() or None, google_cx=self.google_cx_edit.text() or None)
            QMessageBox.information(self, "设置已保存", "设置已成功保存！")
            self.accept()
        except Exception as error:
            QMessageBox.warning(self, "保存失败", f"无法保存设置: {error}")
