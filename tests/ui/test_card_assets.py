"""卡片资产的行计划与最小渲染契约。

只保留真实卡片行位置、资产元数据尺寸与行不重叠；逐像素正面比对和样式矩阵已收缩。
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
    CARD_BORDER_WIDTH,
    CARD_CONTENT_SPACING,
    CARD_HEIGHT,
    CARD_MIN_WIDTH,
    CARD_PICTURE_HEIGHT,
    CARD_WIDTH,
    InstrumentCard,
    InstrumentCardData,
    card_address_rect,
    card_border_width,
    card_caption_height,
    card_code_rect,
    card_inner_rect,
    card_picture_rect,
    card_row_plan,
    card_status_height,
)
from presentation.qt.theme import CARD_RADIUS, TOKENS  # noqa: E402

#: 参照照片里仪器主体的颜色：与白底、卡片底色、文字色都分得开，便于找包围盒。
_SUBJECT = QColor(0, 200, 0)
_SUBJECT_TOLERANCE = 40
#: 边框以内的画面要逐像素一致；这里留一点余量给字体/抗锯齿的版本差异。
_INTERIOR_TOLERANCE = 0.001
#: 比较“边框以内”时避开的边距：1px 描边与 8px 圆角由样式表的路径描边画出，
#: 圆角外侧是透明（资产）或控件底色（截图），两者本来就不该相等。
_BORDER_INSET = 8


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
    """白底、页面底色与卡片的浅灰底都不算“墨”。

    卡片未选中时是 ``surface_alt``（#FAFBFC），比纯白略暗一点点；把它也当作空白，
    ``_ink_count`` 才能继续回答“这一行有没有画字”。
    """
    return colour.lightness() >= 249


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


def _card_rect(card: InstrumentCard, widget: QWidget) -> QRect:
    """``widget`` 在卡片坐标系里的矩形。

    卡片的子控件是直接挂在卡片上的，``widget.geometry()`` 本来就是卡片坐标；这里把
    这个前提写成断言，免得以后换成嵌套容器时用例悄悄比错了地方。
    """
    assert widget.parent() is card, "子控件必须直接挂在卡片上，几何才是卡片坐标"
    rect = widget.geometry()
    assert card.rect().contains(rect), "子控件不能画到卡片外面"
    return rect


def _rect_tuple(rect) -> tuple[int, int, int, int]:
    return (
        int(round(rect.x())),
        int(round(rect.y())),
        int(round(rect.width())),
        int(round(rect.height())),
    )


def _plain_data(text: str = "DP8B1519") -> InstrumentCardData:
    return InstrumentCardData(
        "DP832A（DP8B1519）",
        code=text,
        model="DP832A",
        role="电源",
        address="192.168.1.102",
    )




class CardAssetContractTests(unittest.TestCase):
    """渲染接口的输入契约与元数据。"""

    @classmethod
    def setUpClass(cls):
        cls.application = _application()

    def test_the_asset_metadata_is_the_size_that_was_really_rendered(self):
        asset = render_card_asset(
            _plain_data(), width=CARD_MIN_WIDTH, device_pixel_ratio=1.5
        )

        self.assertEqual((asset.width, asset.height), (CARD_MIN_WIDTH, CARD_HEIGHT))
        self.assertEqual(asset.size(), QSize(CARD_MIN_WIDTH, CARD_HEIGHT))
        # 逻辑尺寸不变，物理像素按屏幕像素比放大。
        self.assertEqual(
            (asset.front.width(), asset.front.height()),
            (round(CARD_MIN_WIDTH * 1.5), round(CARD_HEIGHT * 1.5)),
        )
        self.assertEqual(asset.front.devicePixelRatio(), 1.5)




class CardRowPlanTests(unittest.TestCase):
    """行位置只有一份：真实卡片按它钉住行高，资产按它画字。"""

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

    def card(self, data: InstrumentCardData, width: int = CARD_WIDTH) -> InstrumentCard:
        host = QWidget()
        host.resize(width + 80, CARD_HEIGHT + 80)
        host.show()
        self.addCleanup(host.deleteLater)
        card = InstrumentCard(data)
        card.setParent(host)
        # 挪开 (0,0)：离屏平台的鼠标停在原点，卡片贴在原点会被判成悬停（边框换色）。
        card.move(40, 40)
        card.setFixedWidth(width)
        card.show()
        self.application.processEvents()
        card.layout().activate()
        self.application.processEvents()
        return card

    def variants(self) -> dict[str, InstrumentCardData]:
        picture = self.save_picture()
        return {
            "full": InstrumentCardData(
                "DP832A（DP8B1519）",
                code="DP8B1519",
                model="DP832A",
                role="电源",
                address="192.168.1.102",
                status="已按配置预选",
                picture_path=picture,
            ),
            "plain": _plain_data(),
            "no-address": InstrumentCardData(
                "DP832A（DP8B1519）", code="DP8B1519", model="DP832A", role="电源"
            ),
            "no-caption": InstrumentCardData(
                "DP8B1519", code="DP8B1519", address="192.168.1.102"
            ),
            "no-picture": _plain_data(),
            "long-code": InstrumentCardData(
                "DP832A", code="DP8B214901519", model="DP832A", role="电源"
            ),
            "long-status": InstrumentCardData(
                "未登记设备 #1",
                code="未识别 #1",
                status=(
                    "未在设备表中登记，无法确定测试角色，请先登记设备再扫描一次"
                    "以完成身份匹配"
                ),
                checkable=False,
            ),
            "checked": InstrumentCardData(
                "DP832A（DP8B1519）",
                code="DP8B1519",
                model="DP832A",
                role="电源",
                address="192.168.1.102",
                checked=True,
            ),
            "not-applicable": InstrumentCardData(
                "N9020A（MY51234567）",
                code="MY51234567",
                model="N9020A",
                role="信号发生器",
                address="192.168.1.102",
                status="未登记 · 不可应用",
                checkable=False,
            ),
        }

    def rows_of(self, card: InstrumentCard):
        return (
            ("code", card.code_label),
            ("picture", card.image),
            ("status", card.status_label),
            ("caption", card.name_label),
            ("address", card.address_label),
        )

    def test_the_live_card_sits_exactly_where_the_plan_says(self):
        """两种宽度、九种内容：真实卡片的行几何 == 行计划（不再靠单组数据碰运气）。"""
        for width in (CARD_WIDTH, CARD_MIN_WIDTH, 175):
            for label, data in self.variants().items():
                with self.subTest(width=width, variant=label):
                    card = self.card(data, width)
                    plan = card_row_plan(card.data, card.width(), card.height())

                    for name, row, widget in (
                        ("code", plan.code, card.code_label),
                        ("picture", plan.picture, card.image),
                        ("status", plan.status, card.status_label),
                        ("caption", plan.caption, card.name_label),
                        ("address", plan.address, card.address_label),
                    ):
                        if row.isEmpty():
                            self.assertFalse(
                                widget.isVisibleTo(card),
                                f"{name} 在计划里没有位置，控件就不该显示",
                            )
                            continue
                        self.assertTrue(
                            widget.isVisibleTo(card), f"{name} 在计划里有位置，控件就该显示"
                        )
                        self.assertEqual(
                            _rect_tuple(_card_rect(card, widget)),
                            _rect_tuple(row),
                            f"{name} 的位置与行计划不一致",
                        )


    def test_rows_never_overlap_and_stay_inside_the_content_area(self):
        for width in (CARD_WIDTH, CARD_MIN_WIDTH, 120):
            for label, data in self.variants().items():
                with self.subTest(width=width, variant=label):
                    plan = card_row_plan(data, width=width)
                    rows = plan.rows()
                    for row in rows:
                        self.assertGreaterEqual(row.left(), plan.inner.left())
                        self.assertLessEqual(row.right(), plan.inner.right())
                        self.assertLessEqual(row.top(), row.bottom())
                    for previous, following in zip(rows, rows[1:]):
                        self.assertLessEqual(
                            previous.bottom(), following.top(), "行与行不能压在一起"
                        )
                    self.assertLessEqual(rows[-1].bottom(), plan.inner.bottom())







if __name__ == "__main__":
    unittest.main()
