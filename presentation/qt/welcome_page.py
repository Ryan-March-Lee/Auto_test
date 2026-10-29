"""启动欢迎页与测试模式选择。"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import set_button_variant


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
        layout.setContentsMargins(48, 40, 48, 40)
        layout.setSpacing(0)
        layout.addStretch(2)

        card = QFrame()
        card.setObjectName("welcomeCard")
        card.setMinimumWidth(620)
        card.setMaximumWidth(720)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(40, 36, 40, 36)
        card_layout.setSpacing(10)

        title = QLabel("欢迎使用 PA 自动测试系统")
        title.setObjectName("welcomeTitle")
        title.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(title)

        subtitle = QLabel(
            "功放测试、驱动映射、线损测量与结果归档的统一入口\n"
            "请先选择本次测试的驱动方式"
        )
        subtitle.setObjectName("welcomeSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(subtitle)
        card_layout.addSpacing(24)

        buttons = QHBoxLayout()
        buttons.setSpacing(16)
        no_driver = QPushButton("无驱放测试")
        with_driver = QPushButton("有驱放测试")
        set_button_variant(no_driver, "secondary")
        set_button_variant(with_driver, "primary")
        for button in (no_driver, with_driver):
            button.setMinimumSize(200, 56)
        no_driver.clicked.connect(lambda: self.mode_selected.emit(False))
        with_driver.clicked.connect(lambda: self.mode_selected.emit(True))
        buttons.addStretch(1)
        buttons.addWidget(no_driver)
        buttons.addWidget(with_driver)
        buttons.addStretch(1)
        card_layout.addLayout(buttons)
        card_layout.addSpacing(16)

        hint = QLabel("选择后可先在“仪器配置”页扫描设备、保存配置并创建运行。")
        hint.setObjectName("welcomeSubtitle")
        hint.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(hint)

        layout.addWidget(card, 0, Qt.AlignHCenter)
        layout.addStretch(3)


__all__ = ["WelcomePage"]
