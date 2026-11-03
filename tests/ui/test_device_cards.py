"""卡片内容与图片区：一份纯数据画出一张卡片，图片按“仪器主体”缩放。

卡片上的文字与图片都来自 :class:`InstrumentCardData`：真实卡片与之后的动画正面读同一份
数据，动画因此不必回头读扫描结果或问卡片控件要文字。这里锁住数据对象本身（只读、型号·
角色的拼法）以及“卡片显示的就是数据里写的”。

原图留白各不相同（现有三张仪器图里，主体只占画布的 70%~96%），按整张画布等比缩放会让
同一行卡片上的仪器看起来大小不一。这里锁定“先找主体包围盒、再把主体等比放进图片区”的
行为，并锁住“照片本来没有留白时不裁掉任何东西”的兜底。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

# 构造 QImage / 控件前需要离屏平台：单独运行本模块时也一样。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from presentation.qt.device_cards import (  # noqa: E402
    InstrumentCard,
    InstrumentCardData,
    InstrumentImage,
    _content_rect,
    instrument_picture_path,
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


class RuntimePictureTests(unittest.TestCase):
    """仓库里随发布交付的仪器图仍能放进卡片：压缩后尺寸和透明留白不得破坏显示。"""

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_every_role_picture_is_drawn_inside_the_picture_area(self):
        for role in ("signal_generator", "spectrum_analyzer", "power_supply"):
            with self.subTest(role=role):
                path = instrument_picture_path(role)
                self.assertIsNotNone(path, f"{role} 没有可用的仪器图")
                widget = InstrumentImage()
                widget.resize(172, 104)
                widget.set_picture(path)

                self.assertTrue(widget.has_picture())
                source = widget.source_rect()
                self.assertFalse(source.isEmpty())
                rect = widget.picture_rect()
                self.assertFalse(rect.isEmpty())
                self.assertGreaterEqual(rect.left(), float(widget.rect().left()))
                self.assertGreaterEqual(rect.top(), float(widget.rect().top()))
                self.assertLessEqual(rect.right(), float(widget.rect().right()))
                self.assertLessEqual(rect.bottom(), float(widget.rect().bottom()))


class InstrumentCardDataTests(unittest.TestCase):
    """卡片数据：纯数据、只读，拼“型号·角色”的规则只有一份。"""

    def test_the_data_object_is_read_only(self):
        data = InstrumentCardData("SMW200A（105462）", code="105462")

        with self.assertRaises(FrozenInstanceError):
            data.code = "OTHER"  # type: ignore[misc]

    def test_caption_joins_model_and_role_only_when_present(self):
        self.assertEqual(
            InstrumentCardData("h", model="DP832A", role="电源").caption(),
            "DP832A·电源",
        )
        self.assertEqual(InstrumentCardData("h", model="N9020A").caption(), "N9020A")
        self.assertEqual(InstrumentCardData("h", role="电源").caption(), "电源")
        self.assertEqual(InstrumentCardData("h").caption(), "")


class CardFromDataTests(unittest.TestCase):
    """``InstrumentCard`` 由数据构建：卡片上出现的东西全部来自那份数据。"""

    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)

    def picture(self) -> Path:
        path = Path(self._directory.name) / "instrument.png"
        self.assertTrue(photo(400, 200, QRect(100, 20, 200, 160)).save(str(path)))
        return path

    def test_the_card_shows_exactly_what_the_data_says(self):
        picture = self.picture()
        data = InstrumentCardData(
            "DP832A（DP8B1519）",
            code="DP8B1519",
            model="DP832A",
            role="电源",
            address="192.168.1.102",
            status="已按配置预选",
            checked=True,
            picture_path=picture,
            tooltip="DP832A（DP8B1519，电源）\n地址 192.168.1.102",
        )

        card = InstrumentCard(data)

        self.assertEqual(card.code_label.text(), data.code)
        self.assertEqual(card.name_label.text(), "DP832A·电源")
        self.assertEqual(card.address_label.text(), data.address)
        self.assertEqual(card.status_label.text(), data.status)
        self.assertTrue(card.status_label.isVisibleTo(card))
        self.assertEqual(card.toolTip(), data.tooltip)
        self.assertTrue(card.image.has_picture(), "图片路径来自数据")
        self.assertTrue(card.is_checked(), "选中状态来自数据")
        self.assertTrue(card.property("checked"))
        self.assertEqual(card.data, data, "卡片持有的就是这份内容")

    def test_the_snapshot_follows_selection_and_status(self):
        """卡片内容始终等于它此刻显示的东西：动画读到的快照不会停在构建那一刻。"""
        card = InstrumentCard(InstrumentCardData("DP832A（DP8B1519）", code="DP8B1519"))

        card.set_checked(True)
        self.assertTrue(card.data.checked, "勾选后快照跟着更新")

        card.set_status("已按配置预选")
        self.assertEqual(card.data.status, "已按配置预选")

        card.set_status("")
        self.assertEqual(card.data.status, "", "状态说明被清掉，快照里也要清掉")

        card.set_checked(False)
        self.assertFalse(card.data.checked)

    def test_an_unchecked_card_starts_unchecked_and_empty_lines_stay_hidden(self):
        card = InstrumentCard(InstrumentCardData("未识别设备 #1", code="未识别 #1"))

        self.assertFalse(card.is_checked())
        self.assertFalse(card.address_label.isVisibleTo(card), "没有地址时那一行不出现")
        self.assertFalse(card.status_label.isVisibleTo(card), "没有状态说明时不出现")
        self.assertEqual(card.toolTip(), "")

    def test_a_card_that_cannot_be_applied_is_never_checked(self):
        data = InstrumentCardData(
            "N9020A（MY51234567）",
            code="MY51234567",
            model="N9020A",
            role="信号发生器",
            status="未登记 · 不能应用",
            checked=True,
            checkable=False,
        )

        card = InstrumentCard(data)

        self.assertFalse(card.checkable)
        self.assertFalse(card.is_checked(), "不能应用的设备就算数据里勾着也不能选中")
        self.assertFalse(card.data.checked, "快照与卡片实际状态一致")
        self.assertTrue(data.checked, "卡片不改调用方给的那份数据")


if __name__ == "__main__":
    unittest.main()
