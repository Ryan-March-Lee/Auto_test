"""启动欢迎页与测试模式选择。"""

from functools import lru_cache

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import WELCOME_BACKGROUND_FILE


@lru_cache(maxsize=1)
def _welcome_background() -> QPixmap:
    """欢迎页背景图；只解码一次，缩放交给绘制时完成。"""
    return QPixmap(str(WELCOME_BACKGROUND_FILE))


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
    """每次启动首先展示的测试模式入口。

    整页只有背景图、标题与两个模式按钮：背景图由本页自绘铺满，标题与按钮摆在图中
    偏上的天空区域，避免压住下方的仪器场景；图上再叠一层自上而下变淡的黑色蒙版，
    让白字在明暗不定的照片底上依然醒目。
    """

    mode_selected = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("welcomePage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 40)
        layout.setSpacing(0)
        layout.addStretch(2)

        title = QLabel("功放自动测试系统")
        title.setObjectName("welcomeTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        subtitle = QLabel("请选择本次测试模式")
        subtitle.setObjectName("welcomeSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addSpacing(6)
        layout.addWidget(subtitle)
        layout.addSpacing(28)

        no_driver = QPushButton("无驱放测试")
        with_driver = QPushButton("有驱放测试")
        for button in (no_driver, with_driver):
            button.setObjectName("welcomeChoice")
            button.setCursor(Qt.PointingHandCursor)
        no_driver.clicked.connect(lambda: self.mode_selected.emit(False))
        with_driver.clicked.connect(lambda: self.mode_selected.emit(True))

        buttons = QHBoxLayout()
        buttons.setSpacing(16)
        buttons.addStretch(1)
        buttons.addWidget(no_driver)
        buttons.addWidget(with_driver)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        layout.addStretch(5)

    def paintEvent(self, event):
        """背景图 + 覆盖其上的黑色渐变蒙版。

        样式表的 ``background-image`` 在这里不可靠：页面里放了按钮（子控件），且页面上方
        有 ``addStretch`` 时，Qt 只在按钮与边距附近画这层背景，中间大片区域退回窗口底色
        （实测如此，与布局参数组合有关）。自绘则不受布局结构影响。

        蒙版自上而下逐渐变淡（顶部最深），标题与按钮所在的偏上区域因此有足够对比度，
        下方的仪器与桌面保留原图亮度；子控件在这些之后再叠上去，不受蒙版影响。
        """
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().window())
        background = _welcome_background()
        if not background.isNull():
            painter.drawPixmap(self.rect(), background, background.rect())
        mask = QLinearGradient(0.0, 0.0, 0.0, float(self.height()))
        for stop, alpha in _MASK_STOPS:
            mask.setColorAt(stop, QColor(0, 0, 0, alpha))
        painter.fillRect(self.rect(), QBrush(mask))


#: 蒙版沿页面高度（相对位置, 黑色不透明度 0..255）：顶部最深，到底部完全透出原图。
_MASK_STOPS = ((0.0, 130), (0.28, 96), (0.55, 40), (0.78, 0), (1.0, 0))


__all__ = ["WelcomePage"]
