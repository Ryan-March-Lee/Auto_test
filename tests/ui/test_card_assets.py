"""卡片资产：不截取控件也能得到与真实卡片同形的正面。

计划步骤 4 的完成标准都在这里：正面的逻辑尺寸与真实卡片一致、内容非空、图片落在
真实卡片画图片的位置、选中/不可应用/空字段的样式一致、高 DPI 下逻辑版面不变，并且
正面画面与 ``InstrumentCard.grab()`` 在边框以内基本一致（边框圆角的抗锯齿差异不
纳入逐像素比较）。

测试允许用 ``grab()`` 作为参照物；生产代码不调用它——正面由
:func:`presentation.qt.card_assets.render_card_front` 直接画出来。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

# 构造 QPixmap / 控件前需要离屏平台：单独运行本模块时也一样。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QSize  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QWidget  # noqa: E402

from presentation.qt.card_assets import (  # noqa: E402
    CARD_ASSET_SHADOW_MARGIN,
    CardAsset,
    render_card_asset,
    render_card_back,
    render_card_front,
)
from presentation.qt.device_cards import (  # noqa: E402
    CARD_HEIGHT,
    CARD_WIDTH,
    InstrumentCard,
    InstrumentCardData,
    card_address_rect,
    card_code_rect,
    card_picture_rect,
    card_inner_rect,
)
from presentation.qt.theme import CARD_RADIUS, TOKENS  # noqa: E402

#: 参照照片里仪器主体的颜色：与白底、卡片底色、文字色都分得开，便于找包围盒。
_SUBJECT = QColor(0, 200, 0)
_SUBJECT_TOLERANCE = 40


def _application():
    """走正式启动路径：卡片的字体与配色必须与运行时同一份样式表。"""
    from presentation.qt.main_window import build_application

    return build_application([])


def photo(width: int, height: int, subject: QRect) -> QImage:
    """画一张 ``width×height`` 的透明留白照片，``subject`` 里是仪器。"""
    image = QImage(width, height, QImage.Format_RGBA8888)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.fillRect(subject, _SUBJECT)
    painter.end()
    return image


def _color_at(pixmap, x: float, y: float) -> QColor:
    """取 ``pixmap`` 在逻辑坐标 ``(x, y)`` 处的颜色（自动换算物理像素）。"""
    ratio = float(pixmap.devicePixelRatio())
    image = pixmap.toImage()
    return QColor.fromRgba(image.pixel(round(x * ratio), round(y * ratio)))


def _subject_box(image: QImage) -> tuple[float, float, float, float] | None:
    """照片主体的包围盒（图片坐标）：与卡片底色、白底都分得开的那一块。"""
    xs: list[int] = []
    ys: list[int] = []
    for y in range(image.height()):
        for x in range(image.width()):
            colour = QColor.fromRgba(image.pixel(x, y))
            if (
                abs(colour.red() - _SUBJECT.red()) <= _SUBJECT_TOLERANCE
                and abs(colour.green() - _SUBJECT.green()) <= _SUBJECT_TOLERANCE
                and abs(colour.blue() - _SUBJECT.blue()) <= _SUBJECT_TOLERANCE
            ):
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return (float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)))


def _is_white(colour: QColor) -> bool:
    return colour.red() > 250 and colour.green() > 250 and colour.blue() > 250


def _ink_count(image: QImage, rect) -> int:
    """``rect`` 里不是白底/透明底的像素数（文字、图片、描边都算）。"""
    count = 0
    for y in range(max(0, int(rect.top())), min(image.height(), int(rect.bottom()) + 1)):
        for x in range(max(0, int(rect.left())), min(image.width(), int(rect.right()) + 1)):
            colour = QColor.fromRgba(image.pixel(x, y))
            if colour.alpha() > 20 and not _is_white(colour):
                count += 1
    return count


def _diff_count(first: QImage, second: QImage, rect) -> int:
    """两张同尺寸图片在 ``rect`` 内颜色不同的像素数。"""
    count = 0
    for y in range(max(0, int(rect.top())), min(first.height(), int(rect.bottom()) + 1)):
        for x in range(max(0, int(rect.left())), min(first.width(), int(rect.right()) + 1)):
            if QColor.fromRgba(first.pixel(x, y)) != QColor.fromRgba(second.pixel(x, y)):
                count += 1
    return count


class CardAssetBasicsTests(unittest.TestCase):
    """资产的尺寸、几何与“正面/牌背都画得出来”。"""

    @classmethod
    def setUpClass(cls):
        cls.application = _application()

    def test_the_asset_uses_the_card_size_radius_and_shadow_margin(self):
        asset = render_card_asset(InstrumentCardData("DP832A（DP8B1519）", code="DP8B1519"))

        self.assertIsInstance(asset, CardAsset)
        self.assertEqual(asset.size(), QSize(CARD_WIDTH, CARD_HEIGHT))
        self.assertEqual((asset.width, asset.height), (CARD_WIDTH, CARD_HEIGHT))
        self.assertEqual(asset.radius, CARD_RADIUS)
        self.assertEqual(asset.shadow_margin, CARD_ASSET_SHADOW_MARGIN)
        self.assertGreater(asset.shadow_margin, 0, "动画要按这个边距把投影画在卡片外面")
        self.assertEqual(
            (asset.front.width(), asset.front.height()),
            (CARD_WIDTH, CARD_HEIGHT),
        )
        self.assertFalse(asset.is_empty())

    def test_the_front_is_filled_and_the_back_is_a_dark_card_back(self):
        asset = render_card_asset(
            InstrumentCardData(
                "DP832A（DP8B1519）",
                code="DP8B1519",
                model="DP832A",
                role="电源",
                address="192.168.1.102",
            )
        )
        front = asset.front.toImage()
        inner = card_inner_rect()
        self.assertGreater(
            _ink_count(front, inner),
            1000,
            "正面不能是一张空白位图",
        )
        centre = _color_at(asset.back, CARD_WIDTH * 0.25, CARD_HEIGHT / 2.0)
        self.assertLess(centre.lightness(), 128, "牌背应是深色卡背")
        self.assertNotEqual(asset.front.toImage(), asset.back.toImage())

    def test_checked_and_unapplicable_cards_use_the_stylesheet_surfaces(self):
        plain = render_card_front(InstrumentCardData("h", code="A"))
        checked = render_card_front(InstrumentCardData("h", code="A", checked=True))
        disabled = render_card_front(
            InstrumentCardData("h", code="A", checkable=False)
        )

        # 取内容区左边缘里的一个点：不会落在文字或图片上。
        sample = (float(card_inner_rect().left()) + 2.0, CARD_HEIGHT / 2.0)
        self.assertEqual(_color_at(plain, *sample).name(), TOKENS["surface"].lower())
        self.assertEqual(_color_at(checked, *sample).name(), TOKENS["primary_soft"].lower())
        self.assertEqual(_color_at(disabled, *sample).name(), TOKENS["surface_alt"].lower())

        # 选中时描主色边；普通卡片描浅灰边（与样式表的两条规则一致）。
        self.assertEqual(_color_at(checked, 0.5, CARD_HEIGHT / 2.0).name(), TOKENS["primary"].lower())
        self.assertEqual(_color_at(plain, 0.0, CARD_HEIGHT / 2.0).name(), TOKENS["border"].lower())

    def test_high_dpi_only_adds_pixels_and_keeps_the_logical_layout(self):
        data = InstrumentCardData(
            "DP832A（DP8B1519）",
            code="DP8B1519",
            model="DP832A",
            role="电源",
            address="192.168.1.102",
        )

        regular = render_card_front(data, device_pixel_ratio=1.0)
        retina = render_card_front(data, device_pixel_ratio=2.0)

        self.assertEqual((retina.width(), retina.height()), (CARD_WIDTH * 2, CARD_HEIGHT * 2))
        self.assertEqual(retina.devicePixelRatio(), 2.0)
        self.assertEqual(
            _color_at(regular, 6.0, CARD_HEIGHT / 2.0).name(),
            _color_at(retina, 6.0, CARD_HEIGHT / 2.0).name(),
            "高 DPI 不应改变卡片底色覆盖的逻辑位置",
        )

        back = render_card_back(device_pixel_ratio=2.0)
        self.assertEqual((back.width(), back.height()), (CARD_WIDTH * 2, CARD_HEIGHT * 2))
        self.assertLess(
            _color_at(back, CARD_WIDTH * 0.25, CARD_HEIGHT / 2.0).lightness(),
            128,
        )


class CardFrontContentTests(unittest.TestCase):
    """正面内容：文字、状态、图片都按真实卡片的行位置画。"""

    @classmethod
    def setUpClass(cls):
        cls.application = _application()

    def setUp(self):
        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)

    def save_picture(self) -> Path:
        path = Path(self._directory.name) / "instrument.png"
        self.assertTrue(photo(400, 200, QRect(100, 20, 200, 160)).save(str(path)))
        return path

    def card_geometry(self, data: InstrumentCardData) -> InstrumentCard:
        """把真实卡片放进一个底板并显示：行位置以 Qt 布局算出来的为准。"""
        host = QWidget()
        host.resize(400, 400)
        host.show()
        self.addCleanup(host.deleteLater)
        card = InstrumentCard(data)
        card.setParent(host)
        card.show()
        self.application.processEvents()
        return card

    def test_empty_rows_stay_empty_while_filled_rows_draw_content(self):
        full = InstrumentCardData(
            "DP832A（DP8B1519）",
            code="DP8B1519",
            model="DP832A",
            role="电源",
            address="192.168.1.102",
            status="已按配置预选",
        )
        bare = InstrumentCardData("DP832A（DP8B1519）")
        card = self.card_geometry(full)

        filled = render_card_front(full).toImage()
        empty = render_card_front(bare).toImage()

        rows = {
            "code": card.code_label.geometry(),
            "status": card.status_label.geometry(),
            "caption": card.name_label.geometry(),
            "address": card.address_label.geometry(),
        }
        for name, row in rows.items():
            with self.subTest(row=name):
                self.assertGreater(
                    _diff_count(filled, empty, row),
                    0,
                    f"{name} 行应该因为数据不同而不同",
                )
        self.assertEqual(
            _ink_count(empty, card_code_rect()),
            0,
            "没有设备码时第一行不画字",
        )
        self.assertEqual(
            _ink_count(empty, card_address_rect()),
            0,
            "没有地址时最下面一行不画字",
        )

    def test_the_result_picture_lands_where_the_real_card_draws_it(self):
        picture = self.save_picture()
        data = InstrumentCardData(
            "DP832A（DP8B1519）",
            code="DP8B1519",
            model="DP832A",
            role="电源",
            picture_path=picture,
        )
        card = self.card_geometry(data)
        front = render_card_front(data).toImage()

        expected = card.image.picture_rect().translated(
            float(card.image.geometry().x()),
            float(card.image.geometry().y()),
        )
        box = _subject_box(front)

        self.assertIsNotNone(box, "正面里应能看到仪器图")
        assert box is not None
        actual = (box[0], box[1], box[2] + 1.0, box[3] + 1.0)
        expected_edges = (
            expected.left(),
            expected.top(),
            expected.right(),
            expected.bottom(),
        )
        for edge, wanted in zip(actual, expected_edges):
            self.assertAlmostEqual(edge, wanted, delta=2.0)

    def test_a_card_without_a_picture_draws_no_instrument(self):
        data = InstrumentCardData("未识别设备 #1", code="未识别 #1")
        front = render_card_front(data).toImage()

        self.assertIsNone(_subject_box(front))
        self.assertGreater(
            _ink_count(front, card_picture_rect()),
            0,
            "没有图片时图片区仍要写出占位文字",
        )

    def test_the_front_matches_the_real_card_inside_the_border(self):
        picture = self.save_picture()
        data = InstrumentCardData(
            "DP832A（DP8B1519）",
            code="DP8B1519",
            model="DP832A",
            role="电源",
            address="192.168.1.102",
            status="已按配置预选",
            picture_path=picture,
        )
        card = self.card_geometry(data)
        real = card.grab().toImage()
        asset = render_card_front(data).toImage()

        self.assertEqual(real.size(), asset.size())
        # 边框与圆角由样式表描边，抗锯齿在不同平台略有差异；比较边框以内的画面。
        interior = QRect(8, 8, CARD_WIDTH - 16, CARD_HEIGHT - 16)
        area = interior.width() * interior.height()
        differences = _diff_count(real, asset, interior)
        self.assertLess(
            differences,
            int(area * 0.02),
            f"边框以内的画面应与真实卡片基本一致（差异 {differences}/{area} 像素）",
        )


if __name__ == "__main__":
    unittest.main()
