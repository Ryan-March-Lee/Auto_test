"""卡片图片区：缩放的是“仪器主体”，不是整张照片。

原图留白各不相同（现有三张仪器图里，主体只占画布的 70%~96%），按整张画布等比缩放会让
同一行卡片上的仪器看起来大小不一。这里锁住“先找主体包围盒、再把主体等比放进图片区”的
行为，并锁住“照片本来没有留白时不裁掉任何东西”的兜底。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

# 构造 QImage / 控件前需要离屏平台：单独运行本模块时也一样。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from presentation.qt.device_cards import (  # noqa: E402
    InstrumentImage,
    _content_rect,
)

#: 缩略图扫描的取整误差：每个缩略图像素最多代表原图 ``max(边长)/128``。
_PROBE_SIZE = 128


def photo(
    width: int,
    height: int,
    subject: QRect,
    *,
    background: QColor | None = None,
    color: QColor = QColor(40, 60, 90),
) -> QImage:
    """画一张 ``width×height`` 的照片：``subject`` 里是仪器，其余是留白。

    ``background=None`` 表示透明留白（照片抠图），否则是纯色留白（实拍照片）。
    """
    image = QImage(width, height, QImage.Format_RGBA8888)
    image.fill(QColor(0, 0, 0, 0) if background is None else background)
    painter = QPainter(image)
    painter.fillRect(subject, color)
    painter.end()
    return image


class ContentRectTests(unittest.TestCase):
    """``_content_rect``：找出仪器主体在原图里的包围盒。"""

    def assert_box(self, rect, expected, image):
        """允许缩略图取整误差：边界最多差两个缩略图像素。"""
        quantum = 2.0 * max(image.width(), image.height()) / _PROBE_SIZE
        actual = (rect.x(), rect.y(), rect.width(), rect.height())
        for value, wanted in zip(actual, expected):
            self.assertAlmostEqual(value, wanted, delta=quantum)

    def test_transparent_padding_is_excluded(self):
        image = photo(400, 200, QRect(100, 20, 200, 160))

        self.assert_box(_content_rect(image), (100.0, 20.0, 200.0, 160.0), image)

    def test_flat_photo_background_is_excluded(self):
        image = photo(
            600, 300, QRect(150, 60, 300, 180), background=QColor(254, 254, 254)
        )

        self.assert_box(_content_rect(image), (150.0, 60.0, 300.0, 180.0), image)

    def test_photo_without_padding_is_kept_whole(self):
        """整张图一个颜色时判不出主体：退回整张图，绝不裁掉仪器。"""
        image = photo(300, 200, QRect(0, 0, 300, 200))

        rect = _content_rect(image)

        self.assertEqual((rect.width(), rect.height()), (300.0, 200.0))

    def test_loose_padding_does_not_enlarge_the_subject_box(self):
        """同一台仪器、留白更多的照片，主体包围盒不会因此变大。"""
        tight = _content_rect(photo(400, 200, QRect(100, 20, 200, 160)))
        loose = _content_rect(photo(1000, 700, QRect(400, 270, 200, 160)))

        # 同一块 200×160 的仪器，缩略图取整后的误差不超过原图边长的 1%。
        self.assertAlmostEqual(tight.width(), loose.width(), delta=10.0)
        self.assertAlmostEqual(tight.height(), loose.height(), delta=10.0)


class PictureRectTests(unittest.TestCase):
    """``InstrumentImage``：仪器图在卡片上的落点只由主体决定。"""

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)

    def save(self, name: str, image: QImage) -> Path:
        path = Path(self._directory.name) / name
        self.assertTrue(image.save(str(path)))
        return path

    def image_widget(self, path: Path) -> InstrumentImage:
        widget = InstrumentImage()
        widget.resize(172, 104)
        widget.set_picture(path)
        return widget

    def test_loose_padding_does_not_shrink_the_instrument(self):
        tight = self.image_widget(
            self.save("tight.png", photo(400, 200, QRect(100, 20, 200, 160)))
        )
        loose = self.image_widget(
            self.save("loose.png", photo(1000, 700, QRect(400, 270, 200, 160)))
        )

        tight_rect = tight.picture_rect()
        loose_rect = loose.picture_rect()

        self.assertAlmostEqual(tight_rect.width(), loose_rect.width(), delta=1.0)
        self.assertAlmostEqual(tight_rect.height(), loose_rect.height(), delta=1.0)

    def test_instrument_stays_inside_the_picture_area(self):
        widget = self.image_widget(
            self.save("wide.png", photo(1200, 300, QRect(100, 100, 1000, 100)))
        )

        rect = widget.picture_rect()

        self.assertGreaterEqual(rect.left(), widget.rect().left())
        self.assertGreaterEqual(rect.top(), widget.rect().top())
        self.assertLessEqual(rect.right(), widget.rect().right())
        self.assertLessEqual(rect.bottom(), widget.rect().bottom())

    def test_without_a_picture_there_is_nothing_to_draw(self):
        widget = InstrumentImage()
        widget.resize(172, 104)
        widget.set_picture(None)

        self.assertFalse(widget.has_picture())
        self.assertTrue(widget.picture_rect().isEmpty())


if __name__ == "__main__":
    unittest.main()
