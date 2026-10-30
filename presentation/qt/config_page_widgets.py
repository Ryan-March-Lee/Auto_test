"""控件构建辅助。

本模块只负责配置页的固定操作栏布局；页面仍持有控件并负责信号连接，
因此不会把配置解释或业务状态带进 Qt 构建代码。
"""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from .theme import set_button_variant


def build_action_bar(page) -> QWidget:
    """构建配置页底部操作栏，并把需要由页面使用的控件登记到 ``page``。"""
    bar = QWidget()
    bar.setObjectName("pageActionBar")
    row = QHBoxLayout(bar)
    row.setContentsMargins(0, 0, 4, 0)
    row.setSpacing(10)

    dirty_hint = QLabel("修改参数后请保存配置，运行时使用已保存并冻结的配置。")
    dirty_hint.setProperty("role", "hint")
    row.addWidget(dirty_hint, 1)

    page.previous_step_btn = QPushButton("上一步：仪表配置")
    page.previous_step_btn.setToolTip("回到“仪表配置”页，重新扫描并应用本次测试使用的设备")
    set_button_variant(page.previous_step_btn, "secondary")
    page.save_config_btn = QPushButton("保存配置")
    set_button_variant(page.save_config_btn, "primary")
    page.return_welcome_btn = QPushButton("返回欢迎页")
    set_button_variant(page.return_welcome_btn, "secondary")
    row.addWidget(page.previous_step_btn)
    row.addWidget(page.return_welcome_btn)
    row.addWidget(page.save_config_btn)
    return bar
