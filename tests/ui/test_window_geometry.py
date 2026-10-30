"""主窗口初始几何：必须落在屏幕可用区域内并居中。

回归问题是窗口写死 ``100,100,1600,1000``：在 1536x864 的屏幕上会有一部分跑到屏幕
右侧和任务栏后面，用户每次启动都要手动挪窗口。这里用固定尺寸的假屏幕验证定位规则
本身，不依赖测试机的真实分辨率。
"""

import unittest

from PySide6.QtCore import QRect, QSize


def _fake_screen(available: QRect, geometry: QRect | None = None):
    return type(
        "FakeScreen",
        (),
        {
            "availableGeometry": lambda self: available,
            "geometry": lambda self: geometry or available,
        },
    )()


class _ProbeWindow:
    """只记录 ``setGeometry`` 的替身窗口；``screen_for_window`` 只用到 ``screen()``。"""

    def __init__(self, screen):
        self._screen = screen
        self.geometry = None

    def screen(self):
        return self._screen

    def setGeometry(self, geometry):
        self.geometry = geometry


class WindowGeometryTests(unittest.TestCase):
    def _fit(self, available, hint=QSize(1600, 1000)):
        from presentation.qt.window_geometry import fit_window_to_screen

        window = _ProbeWindow(_fake_screen(available))
        return fit_window_to_screen(window, hint), window.geometry

    def test_small_laptop_screen_window_stays_inside_available_area(self):
        available = QRect(0, 0, 1536, 816)

        geometry, applied = self._fit(available)

        self.assertEqual(geometry, applied)
        self.assertTrue(available.contains(geometry), f"{geometry} 越出 {available}")

    def test_window_is_centred_on_the_available_area(self):
        available = QRect(0, 0, 1536, 816)

        geometry, _ = self._fit(available)

        self.assertEqual(geometry.center().x(), available.center().x())
        self.assertEqual(geometry.center().y(), available.center().y())

    def test_large_screen_keeps_the_comfortable_window_size(self):
        available = QRect(0, 0, 2560, 1400)

        geometry, _ = self._fit(available)

        self.assertEqual(geometry.size(), QSize(1600, 1000))
        self.assertEqual(geometry.center(), available.center())

    def test_taskbar_and_secondary_screen_offsets_are_respected(self):
        # 副屏位于主屏右侧且任务栏在下方：可用区域原点不是 (0, 0)。
        available = QRect(1920, 0, 1280, 680)

        geometry, _ = self._fit(available)

        self.assertTrue(available.contains(geometry), f"{geometry} 越出 {available}")
        self.assertGreaterEqual(geometry.top(), available.top())
        self.assertLessEqual(geometry.bottom(), available.bottom())

    def test_invalid_available_area_falls_back_to_screen_geometry(self):
        from presentation.qt.window_geometry import fit_window_to_screen

        screen = _fake_screen(QRect(0, 0, 0, 0), QRect(0, 0, 1024, 768))
        window = _ProbeWindow(screen)

        geometry = fit_window_to_screen(window)

        self.assertTrue(QRect(0, 0, 1024, 768).contains(geometry))

    def test_window_never_exceeds_the_absolute_minimum_size(self):
        available = QRect(0, 0, 1280, 720)

        geometry, _ = self._fit(available)

        self.assertGreater(geometry.width(), 0)
        self.assertGreater(geometry.height(), 0)
        self.assertTrue(available.contains(geometry))


if __name__ == "__main__":
    unittest.main()
