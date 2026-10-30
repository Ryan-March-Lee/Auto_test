"""主窗口初始几何：按屏幕可用区域居中，绝不越过任务栏和屏幕边缘。

窗口原先固定 ``setGeometry(100, 100, 1600, 1000)``，在 1536x864 这类常见笔记本
屏幕上会贴到右下角并被屏幕边缘和任务栏切掉一部分。这里改为：以
:data:`WINDOW_SIZE_HINT` 为期望尺寸，不超过所选屏幕 **可用区域**（已扣除任务栏）
的比例上限，然后居中放置；屏幕比期望尺寸更大时也不铺满，保留原来的 1600x1000。
"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

__all__ = [
    "WINDOW_SIZE_HINT",
    "fit_window_to_screen",
    "screen_for_window",
]

# 期望窗口尺寸：与历史 geometry 一致，大屏上保持同样的阅读宽度。
WINDOW_SIZE_HINT = QSize(1600, 1000)
# 窗口尺寸占屏幕可用区域的比例上限。留出边距，避免窗口紧贴任务栏和屏幕边缘。
SCREEN_FILL_RATIO = 0.92


def screen_for_window(window: QWidget):
    """返回窗口应当放置到的屏幕；没有可用屏幕时返回 ``None``。

    优先使用窗口当前所在的屏幕，多屏环境下不会把窗口从用户正在使用的显示器
    拽回主屏。
    """
    screened = window.screen() if hasattr(window, "screen") else None
    return screened or QGuiApplication.primaryScreen()


def fit_window_to_screen(
    window: QWidget,
    size_hint: QSize = WINDOW_SIZE_HINT,
    *,
    fill_ratio: float = SCREEN_FILL_RATIO,
):
    """把窗口放进屏幕可用区域并居中，返回放置后的几何（无屏幕时为 ``None``）。

    ``size_hint`` 只是期望尺寸：可用区域更小时按比例收缩或整体缩小。窗口的最终
    大小仍受控件最小尺寸约束，那部分交给 Qt；这里只保证初始位置与尺寸不越过屏幕
    可用区域。
    """
    screen = screen_for_window(window)
    if screen is None:
        return None
    available: QRect = screen.availableGeometry()
    if not available.isValid():
        available = screen.geometry()
    maximum = QSize(
        max(1, int(available.width() * fill_ratio)),
        max(1, int(available.height() * fill_ratio)),
    )
    # 等比收缩，避免窄屏上只压纵向时窗口被拉成很扁的长条。
    scale = min(
        1.0,
        maximum.width() / max(1, size_hint.width()),
        maximum.height() / max(1, size_hint.height()),
    )
    width = max(1, int(size_hint.width() * scale))
    height = max(1, int(size_hint.height() * scale))
    left = available.left() + (available.width() - width) // 2
    top = available.top() + (available.height() - height) // 2
    geometry = QRect(left, top, width, height)
    window.setGeometry(geometry)
    return geometry
