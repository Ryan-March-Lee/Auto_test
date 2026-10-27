"""启动欢迎页与测试模式选择。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)


def apply_test_mode(config: dict, driver_enabled: bool) -> dict:
    """把欢迎页选择的测试模式写进配置副本的 ``driver_mode``。"""
    mode = config.get("driver_mode")
    mode = dict(mode) if isinstance(mode, dict) else {}
    mode["enabled"] = bool(driver_enabled)
    config["driver_mode"] = mode
    return config


def confirm_leave_configuration(parent, is_dirty: bool) -> bool:
    """返回欢迎页前的确认：只有配置被改动过才询问，避免丢失未保存的修改。"""
    if not is_dirty:
        return True
    reply = QMessageBox.question(
        parent,
        "返回欢迎页",
        "返回欢迎页将丢失当前未保存的配置，是否返回？",
        QMessageBox.Yes | QMessageBox.No,
    )
    return reply == QMessageBox.Yes


class WelcomePage(QWidget):
    """每次启动首先展示的测试模式入口。"""

    mode_selected = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.addStretch(2)

        title = QLabel("欢迎使用 PA 自动测试系统")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-size: 30px; font-weight: bold; color: #202020;")
        layout.addWidget(title)
        layout.addSpacing(48)

        buttons = QHBoxLayout()
        buttons.setSpacing(24)
        no_driver = QPushButton("无驱放测试")
        with_driver = QPushButton("有驱放测试")
        for button in (no_driver, with_driver):
            button.setMinimumSize(220, 64)
            button.setStyleSheet("font-size: 18px; padding: 12px 24px;")
        no_driver.clicked.connect(lambda: self.mode_selected.emit(False))
        with_driver.clicked.connect(lambda: self.mode_selected.emit(True))
        buttons.addStretch(1)
        buttons.addWidget(no_driver)
        buttons.addWidget(with_driver)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(3)


__all__ = ["WelcomePage"]
