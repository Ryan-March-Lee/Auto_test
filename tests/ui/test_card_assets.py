"""卡片资产：不截取控件，也能得到与真实卡片同形的正面。

计划步骤 4 的完成标准都在这里：正面的逻辑尺寸与真实卡片一致、内容非空、图片落在
真实卡片画图片的位置、选中/不可应用/空字段的样式一致、高 DPI 下逻辑版面不变，并且
正面画面与 ``InstrumentCard.grab()`` 在边框以内一致。

两条边界各有一组用例：

- :class:`CardRowPlanTests` 锁住“行位置只有一份”：真实卡片按
  :func:`presentation.qt.device_cards.card_row_plan` 钉住行高，资产按同一份行计划画字，
  所以两种宽度、选中/不可应用、长设备码、长状态、空地址都对得上；
- :class:`CardAssetContractTests` 锁住渲染接口的输入契约：尺寸只收正整数、屏幕像素比
  拿不到时按 1.0、资产的元数据与位图实际尺寸一致。

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
        self.assertEqual(
            _color_at(checked, *sample).name(), TOKENS["primary_soft"].lower()
        )
        self.assertEqual(
            _color_at(disabled, *sample).name(), TOKENS["surface_alt"].lower()
        )

        # 选中时描主色边；普通卡片描浅灰边（与样式表的两条规则一致）。
        self.assertEqual(
            _color_at(checked, 0.5, CARD_HEIGHT / 2.0).name(),
            TOKENS["primary"].lower(),
        )
        self.assertEqual(
            _color_at(plain, 0.0, CARD_HEIGHT / 2.0).name(), TOKENS["border"].lower()
        )

    def test_high_dpi_only_adds_pixels_and_keeps_the_logical_layout(self):
        data = _plain_data()

        regular = render_card_front(data, device_pixel_ratio=1.0)
        retina = render_card_front(data, device_pixel_ratio=2.0)

        self.assertEqual(
            (retina.width(), retina.height()), (CARD_WIDTH * 2, CARD_HEIGHT * 2)
        )
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

    def test_sizes_must_be_positive_integers(self):
        data = _plain_data()
        for bad_size in (0, -1, -240):
            with self.subTest(size=bad_size):
                with self.assertRaises(ValueError):
                    render_card_front(data, width=bad_size)
                with self.assertRaises(ValueError):
                    render_card_back(height=bad_size)
                with self.assertRaises(ValueError):
                    render_card_asset(data, height=bad_size)
        for bad_size in (190.0, "190", None, True):
            with self.subTest(size=bad_size):
                with self.assertRaises(TypeError):
                    render_card_front(data, width=bad_size)
                with self.assertRaises(TypeError):
                    render_card_asset(data, width=bad_size)

    def test_a_bad_device_pixel_ratio_falls_back_to_one(self):
        data = _plain_data()
        for bad_ratio in (0, -2.0, float("nan"), float("inf"), None, "retina"):
            with self.subTest(ratio=bad_ratio):
                front = render_card_front(data, device_pixel_ratio=bad_ratio)
                self.assertEqual(front.devicePixelRatio(), 1.0)
                self.assertEqual(
                    (front.width(), front.height()), (CARD_WIDTH, CARD_HEIGHT)
                )


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

    def test_the_plan_can_be_computed_without_a_widget(self):
        plan = card_row_plan(_plain_data())

        self.assertEqual(plan.width, CARD_WIDTH)
        self.assertEqual(plan.height, CARD_HEIGHT)
        self.assertEqual(_rect_tuple(plan.inner), _rect_tuple(card_inner_rect()))
        self.assertEqual(_rect_tuple(plan.code), _rect_tuple(card_code_rect()))
        self.assertEqual(_rect_tuple(plan.picture), _rect_tuple(card_picture_rect()))
        self.assertEqual(_rect_tuple(plan.address), _rect_tuple(card_address_rect()))
        self.assertEqual(plan.caption.height(), card_caption_height())
        self.assertTrue(plan.status.isEmpty(), "没有状态说明时那一行不占位置")
        self.assertEqual(
            [row.top() for row in plan.rows()],
            sorted(row.top() for row in plan.rows()),
            "行按自上而下的顺序给出",
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

    def test_an_unusually_long_status_takes_room_from_the_picture(self):
        """状态说明按宽度折行后要占的位置由图片让出来，行距与底部两行不动。"""
        short = card_row_plan(_plain_data())
        long_text = "未在设备表中登记，无法确定测试角色，请先登记设备再扫描一次以完成身份匹配"
        long = card_row_plan(
            InstrumentCardData("h", code="DP8B1519", address="1.2.3.4", status=long_text)
        )

        self.assertEqual(
            long.status.height(),
            card_status_height(long_text, long.inner.width()),
            "状态说明拿到的就是折行后的高度（不截断文字）",
        )
        self.assertLess(long.picture.height(), short.picture.height(), "由图片让出高度")
        self.assertEqual(
            _rect_tuple(long.caption), _rect_tuple(short.caption), "型号行位置不变"
        )
        self.assertEqual(
            _rect_tuple(long.address), _rect_tuple(short.address), "地址行位置不变"
        )
        self.assertGreaterEqual(
            long.picture.height(), 0, "再长的说明也不能把图片挤成负数"
        )

    def test_selecting_a_card_does_not_move_its_rows(self):
        """选中只把描边加粗到 2px（向内容区多压 1px），每一行的位置不变。"""
        plain = card_row_plan(_plain_data())
        checked = card_row_plan(
            InstrumentCardData("h", code="DP8B1519", address="192.168.1.102", checked=True)
        )

        self.assertEqual(card_border_width(_plain_data()), 1)
        self.assertEqual(
            card_border_width(
                InstrumentCardData("h", code="DP8B1519", checked=True)
            ),
            2,
        )
        for name in ("inner", "code", "picture", "caption", "address"):
            with self.subTest(row=name):
                self.assertEqual(
                    _rect_tuple(getattr(checked, name)),
                    _rect_tuple(getattr(plain, name)),
                    "选中/取消不应让内容跳 1px",
                )

    def test_the_picture_area_keeps_its_full_height_for_real_cards(self):
        """真实卡片的状态说明都只有一行：图片不该因此变矮。"""
        for status in ("已按配置预选", "未登记 · 不可应用", "未分配角色 · 不可应用", "无法识别"):
            for address in ("", "192.168.1.102"):
                with self.subTest(status=status, address=bool(address)):
                    plan = card_row_plan(
                        InstrumentCardData(
                            "h", code="DP8B1519", address=address, status=status
                        )
                    )
                    self.assertEqual(plan.picture.height(), CARD_PICTURE_HEIGHT)


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

    def card_geometry(
        self,
        data: InstrumentCardData,
        width: int = CARD_WIDTH,
    ) -> InstrumentCard:
        """把真实卡片放进一个底板并显示：行位置以 Qt 布局算出来的为准。"""
        host = QWidget()
        host.resize(width + 80, CARD_HEIGHT + 80)
        host.show()
        self.addCleanup(host.deleteLater)
        card = InstrumentCard(data)
        card.setParent(host)
        card.move(40, 40)
        card.setFixedWidth(width)
        card.show()
        self.application.processEvents()
        card.layout().activate()
        self.application.processEvents()
        return card

    def variants(self, picture: Path) -> dict[str, InstrumentCardData]:
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
            "plain": InstrumentCardData(
                "DP832A（DP8B1519）",
                code="DP8B1519",
                model="DP832A",
                role="电源",
                address="192.168.1.102",
                picture_path=picture,
            ),
            "no-address": InstrumentCardData(
                "DP832A（DP8B1519）",
                code="DP8B1519",
                model="DP832A",
                role="电源",
                picture_path=picture,
            ),
            "no-picture": InstrumentCardData(
                "未识别设备 #1",
                code="未识别 #1",
                address="192.168.1.102",
            ),
            "long-code": InstrumentCardData(
                "DP832A", code="DP8B214901519", model="DP832A", role="电源"
            ),
            "checked": InstrumentCardData(
                "DP832A（DP8B1519）",
                code="DP8B1519",
                model="DP832A",
                role="电源",
                address="192.168.1.102",
                checked=True,
                picture_path=picture,
            ),
            "not-applicable": InstrumentCardData(
                "N9020A（MY51234567）",
                code="MY51234567",
                model="N9020A",
                role="信号发生器",
                address="192.168.1.102",
                status="未登记 · 不可应用",
                checkable=False,
                picture_path=picture,
            ),
            "long-status": InstrumentCardData(
                "未识别设备 #1",
                code="未识别 #1",
                status=(
                    "未在设备表中登记，无法确定测试角色，请先登记设备再扫描一次"
                    "以完成身份匹配"
                ),
                checkable=False,
            ),
        }

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
            "code": _card_rect(card, card.code_label),
            "status": _card_rect(card, card.status_label),
            "caption": _card_rect(card, card.name_label),
            "address": _card_rect(card, card.address_label),
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

    def test_the_front_matches_the_card_inside_the_border_at_every_width(self):
        """两种宽度、八种内容：边框以内逐像素一致（0 像素差异，留一点余量给抗锯齿）。"""
        picture = self.save_picture()
        for width in (CARD_WIDTH, CARD_MIN_WIDTH):
            for label, data in self.variants(picture).items():
                with self.subTest(width=width, variant=label):
                    card = self.card_geometry(data, width)
                    real = card.grab().toImage()
                    asset = render_card_front(data, width=width).toImage()

                    self.assertEqual(real.size(), asset.size())
                    interior = QRect(
                        _BORDER_INSET,
                        _BORDER_INSET,
                        asset.width() - 2 * _BORDER_INSET,
                        asset.height() - 2 * _BORDER_INSET,
                    )
                    differences = _diff_count(real, asset, interior)
                    allowed = int(
                        interior.width() * interior.height() * _INTERIOR_TOLERANCE
                    )
                    self.assertLessEqual(
                        differences,
                        allowed,
                        f"边框以内的画面应与真实卡片一致（差异 {differences}/{allowed} 像素）",
                    )

    def test_the_front_follows_the_card_when_the_status_or_selection_changes(self):
        """卡片在运行时改状态、改选中之后，资产仍然与它同形。"""
        data = _plain_data()
        card = self.card_geometry(data)

        card.set_status("已按配置预选")
        card.set_checked(True)
        self.application.processEvents()

        current = card.data
        asset = render_card_front(current).toImage()
        real = card.grab().toImage()
        interior = QRect(
            _BORDER_INSET,
            _BORDER_INSET,
            asset.width() - 2 * _BORDER_INSET,
            asset.height() - 2 * _BORDER_INSET,
        )

        self.assertEqual(current.status, "已按配置预选")
        self.assertTrue(current.checked)
        self.assertLessEqual(
            _diff_count(real, asset, interior),
            int(interior.width() * interior.height() * _INTERIOR_TOLERANCE),
        )


if __name__ == "__main__":
    unittest.main()
