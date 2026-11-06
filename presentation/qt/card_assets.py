"""仪器卡片的动画资产：不截取控件，也能得到完整、稳定的正面与牌背。

发牌动画原来用 ``InstrumentCard.grab()`` 取正面：卡片必须先挂进布局、显示出来、
完成一次重排，动画才能拿到画面，于是动画时间轴反过来依赖页面状态。这里把“卡片长
什么样”从控件里拆出来：:class:`CardAsset` 只装几张位图与卡片几何，动画开始前由
:func:`render_card_asset` 按 :class:`~presentation.qt.device_cards.InstrumentCardData`
一次渲染好，之后每帧只贴图，不再读控件，也不再重绘文字。

正面与真实卡片共用同一份内容、同一套行位置与同一份字体：

- 文字与图片来自 ``InstrumentCardData``，型号·角色仍由 ``data.caption()`` 拼一次；
- 每一行的位置由 :func:`~presentation.qt.device_cards.card_row_plan` 一次算定，真实
  卡片用同一份行计划钉住行高、资产用同一份行计划画字；行位置因此不是“照着截图猜出来
  的”，也不会因为标签自己的折行结果而错开；
- 字体取自 :mod:`presentation.qt.theme` 的 ``card_*_font()``：这些函数读样式表里同一个
  字号令牌，所以资产上的字与真实卡片上的字同名、同号、同字重；
- 图片按“仪器主体包围盒等比放进图片区”的规则，与 ``InstrumentImage`` 共用
  :func:`~presentation.qt.device_cards.picture_target_rect` 与
  :func:`~presentation.qt.device_cards.instrument_picture`；
- 选中、不可应用与空字段的颜色/留白与卡片样式表一致：未选中的可勾选卡片是灰底、
  型号·角色灰字、仪器图去色变淡；选中后换成白底黑字与彩色原图，两种状态的描边都是
  1px；不可应用的整块沿用未选中的灰底灰字，但不参与选中，图片保持原样。

它**不是**控件：没有父控件、没有信号，也不吃鼠标或键盘。位图按逻辑尺寸
（:data:`~presentation.qt.device_cards.CARD_WIDTH` ×
:data:`~presentation.qt.device_cards.CARD_HEIGHT`）渲染，再按传入的屏幕像素比生成
物理像素；因此高 DPI 下只是更清晰，文字与图片的逻辑位置不变。
:data:`CARD_ASSET_SHADOW_MARGIN` 是动画画投影时要在卡片外面预留的边距（位图本身
只含卡片，不含投影）。

**运行约束**：位图用 ``QPixmap`` 绘制，所以必须在创建 ``QApplication`` 之后、在 GUI
线程里调用（Qt 的 ``QPixmap`` 不是线程安全的，也不保证无 GUI 时可用）。动画在页面
线程里开始，天然满足这个条件；如果之后要在后台预渲染，先改成 ``QImage`` 再转
``QPixmap``，不要把这里的位图直接拿到工作线程。

尺寸参数只接受正整数（见 :func:`~presentation.qt.device_cards.card_size`）：宽高写错
时直接报错，而不是画出一张尺寸与资产元数据对不上的图。屏幕像素比拿不到或不是正数时
按 1.0 处理——那只是清晰度下降，不影响版面。

牌背（:func:`render_card_back`）就是“查找设备”按钮与等待动画最上面那张牌的那张
**绿色光子卡面**：深色 45° 渐变、三团青绿光晕与靛蓝四角角标，由
:mod:`presentation.qt.photon_card` 画出同一份位图；牌背朝上时，动画再按
:attr:`CardAsset.back_particles` 叠一层外飘的绿色粒子。发牌动画正面用
:attr:`CardAsset.front`、背面用 :attr:`CardAsset.back`
（计划步骤 7 已把发牌层拆成独立的
:class:`presentation.qt.card_deal_animation.CardDealAnimation`）。
面板的旧 ``card.grab()`` 截图路径只在
:data:`presentation.qt.instrument_discovery_panel._USE_SCREENSHOT_FRONTS`
打开时用于排障对照，默认不走。
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from .device_cards import (
    CARD_HEIGHT,
    CARD_PICTURE_MARGIN,
    CARD_PICTURE_MUTED_OPACITY,
    CARD_WIDTH,
    CardRowPlan,
    InstrumentCardData,
    card_border_width,
    card_row_plan,
    card_size,
    instrument_picture,
    picture_target_rect,
)
from .photon_card import render_photon_card
from .theme import (
    CARD_RADIUS,
    TOKENS,
    card_address_font,
    card_caption_font,
    card_code_font,
    card_placeholder_font,
    card_status_font,
)

__all__ = [
    "CARD_ASSET_SHADOW_MARGIN",
    "CardAsset",
    "render_card_asset",
    "render_card_back",
    "render_card_front",
]

#: 动画画投影时要在卡片外面预留的边距：位图只含卡片本身，投影由动画层按这个值补。
CARD_ASSET_SHADOW_MARGIN = 12

# ---- 配色：与全局样式表里的仪器卡片一致 --------------------------------------

_SURFACE = QColor(TOKENS["surface"])
_SURFACE_ALT = QColor(TOKENS["surface_alt"])
_BORDER = QColor(TOKENS["border"])
_TEXT = QColor(TOKENS["text"])
_TEXT_SECONDARY = QColor(TOKENS["text_secondary"])
_TEXT_MUTED = QColor(TOKENS["text_muted"])
_WARNING = QColor(TOKENS["warning"])


@dataclass(frozen=True)
class CardAsset:
    """一张卡片的稳定动画资产：正面、牌背与几何，不依赖任何控件。

    ``front`` / ``back`` 只含卡片本身（逻辑尺寸 ``width`` × ``height``，物理像素按
    屏幕像素比放大）；投影要画在卡片外面，由动画按 :attr:`shadow_margin` 留出边距。
    ``radius`` 与真实卡片共用 :data:`presentation.qt.theme.CARD_RADIUS`。

    :attr:`back_particles` 说明牌背是否还要叠一层绿色粒子：粒子是动效，不能烘进位图，
    由动画每帧按相位画。真实的发牌资产打开它（牌背取绿色光子卡面）；测试里直接构造的
    纯色资产默认关掉，画面因此只由位图决定。

    资产里全是 ``QPixmap``：只在 GUI 线程、创建 ``QApplication`` 之后渲染和读取
    （见模块文档的“运行约束”）。
    """

    front: QPixmap
    back: QPixmap
    width: int
    height: int
    radius: float
    shadow_margin: int
    back_particles: bool = False

    def size(self) -> QSize:
        """卡片的逻辑尺寸（动画落点、缩放都按它算）。"""
        return QSize(self.width, self.height)

    def is_empty(self) -> bool:
        """正面或牌背没有画出来时为空：动画应跳过这一张。"""
        return self.front.isNull() or self.back.isNull()


def _device_pixel_ratio(value: float) -> float:
    """把屏幕像素比规整成正的有限数；拿不到时按 1.0 处理（只影响清晰度，不影响版面）。"""
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(ratio) or ratio <= 0.0:
        return 1.0
    return ratio


def _canvas(width: int, height: int, ratio: float) -> QPixmap:
    """按 ``ratio`` 开一张透明画布：逻辑坐标仍是 ``width × height``。"""
    pixmap = QPixmap(
        max(1, round(width * ratio)),
        max(1, round(height * ratio)),
    )
    pixmap.fill(Qt.transparent)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def _rounded(card: QRectF, inset: float = 0.0) -> QPainterPath:
    """卡片轮廓：圆角与真实卡片同值；``inset`` 用于把描边画在轮廓以内。"""
    rect = card.adjusted(inset, inset, -inset, -inset)
    radius = max(1.0, CARD_RADIUS - inset)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _stroke(painter: QPainter, card: QRectF, color: QColor, width: float) -> None:
    """贴边描一道线（样式表的 ``border`` 画在控件内部，这里也只占卡片里面的像素）。"""
    painter.save()
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(color, width))
    painter.drawPath(_rounded(card, width / 2.0))
    painter.restore()


def _checked(data: InstrumentCardData) -> bool:
    """卡片实际显示出来的选中状态：不能应用的卡片不会被选中。"""
    return bool(data.checked) and bool(data.checkable)


def _muted(data: InstrumentCardData) -> bool:
    """仪器图是否去色变淡：未选中但可勾选。选中与不可应用的卡片都画彩色原图。"""
    return bool(data.checkable) and not _checked(data)


def _paint_surface(painter: QPainter, data: InstrumentCardData, card: QRectF) -> None:
    """卡片底色与描边：选中是白底，未选中与不可应用都是灰底，描边一律 1px。"""
    stroke = float(card_border_width(data))
    painter.fillPath(_rounded(card), _SURFACE if _checked(data) else _SURFACE_ALT)
    _stroke(painter, card, _BORDER, stroke)


def _paint_code(painter: QPainter, data: InstrumentCardData, plan: CardRowPlan) -> None:
    """第一行设备码：加粗等宽的缩写码，两行高度内居中；放不下时按标签实际范围裁切。"""
    if not data.code:
        return
    row = plan.code
    painter.save()
    painter.setClipRect(row)
    painter.setFont(card_code_font())
    painter.setPen(_TEXT)
    # QLabel 的 ``setMargin(2)``：文字在标签里再内缩 2px。
    text_rect = row.adjusted(2.0, 2.0, -2.0, -2.0).toAlignedRect()
    painter.drawText(
        text_rect,
        int(Qt.AlignHCenter | Qt.AlignVCenter | Qt.TextWordWrap),
        data.code,
    )
    painter.restore()


def _paint_picture(
    painter: QPainter,
    data: InstrumentCardData,
    plan: CardRowPlan,
) -> None:
    """图片区：只画仪器主体那一块，等比放进图片区并居中（与真实卡片同一份落点）。"""
    area = plan.picture
    if area.isEmpty():
        return
    picture = instrument_picture(data.picture_path)
    if picture.is_empty():
        painter.save()
        painter.setClipRect(area)
        painter.setFont(card_placeholder_font())
        painter.setPen(_TEXT_MUTED)
        painter.drawText(area.toAlignedRect(), int(Qt.AlignCenter), "暂无图片")
        painter.restore()
        return
    source = picture.source_rect()
    target = picture_target_rect(source, area, CARD_PICTURE_MARGIN)
    if target.isEmpty():
        return
    muted = _muted(data)
    painter.save()
    if muted:
        painter.setOpacity(CARD_PICTURE_MUTED_OPACITY)
    painter.drawPixmap(target, picture.display_pixmap(muted), source)
    painter.restore()


def _paint_status(painter: QPainter, data: InstrumentCardData, plan: CardRowPlan) -> None:
    """图片下面那行状态说明（例如“未登记 · 不能应用”）；空串不画，行计划里也不留位置。"""
    if not data.status or plan.status.isEmpty():
        return
    row = plan.status
    painter.save()
    painter.setClipRect(row)
    painter.setFont(card_status_font())
    painter.setPen(_WARNING)
    painter.drawText(
        row.toAlignedRect(),
        int(Qt.AlignLeft | Qt.AlignVCenter | Qt.TextWordWrap),
        data.status,
    )
    painter.restore()


def _paint_caption(painter: QPainter, data: InstrumentCardData, plan: CardRowPlan) -> None:
    """型号·角色：单行居中，放不下时右侧省略（与真实卡片的 ``_ElidedLabel`` 一致）。"""
    text = data.caption()
    if not text or plan.caption.isEmpty():
        return
    row = plan.caption
    metrics = QFontMetricsF(card_caption_font())
    painter.save()
    painter.setClipRect(row)
    painter.setFont(card_caption_font())
    painter.setPen(_TEXT if _checked(data) else _TEXT_SECONDARY)
    painter.drawText(
        row.toAlignedRect(),
        int(Qt.AlignHCenter | Qt.AlignVCenter),
        metrics.elidedText(text, Qt.ElideRight, row.width()),
    )
    painter.restore()


def _paint_address(painter: QPainter, data: InstrumentCardData, plan: CardRowPlan) -> None:
    """最下面一行的地址：灰色小字，单行居中；没有地址时整行不画。"""
    if not data.address or plan.address.isEmpty():
        return
    row = plan.address
    metrics = QFontMetricsF(card_address_font())
    painter.save()
    painter.setClipRect(row)
    painter.setFont(card_address_font())
    painter.setPen(_TEXT_SECONDARY)
    painter.drawText(
        row.toAlignedRect(),
        int(Qt.AlignHCenter | Qt.AlignVCenter),
        metrics.elidedText(data.address, Qt.ElideRight, row.width()),
    )
    painter.restore()


def render_card_front(
    data: InstrumentCardData,
    *,
    width: int = CARD_WIDTH,
    height: int = CARD_HEIGHT,
    device_pixel_ratio: float = 1.0,
) -> QPixmap:
    """按一份卡片数据画出完整正面：不需要控件，也不调用 ``QWidget.grab()``。

    ``width`` / ``height`` 只接受正整数；行位置由
    :func:`~presentation.qt.device_cards.card_row_plan` 给出，与真实卡片同源。必须在
    GUI 线程、``QApplication`` 之后调用（``QPixmap`` 的运行约束）。
    """
    width, height = card_size(width, height)
    plan = card_row_plan(data, width, height)
    pixmap = _canvas(width, height, _device_pixel_ratio(device_pixel_ratio))
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        card = QRectF(0.0, 0.0, float(width), float(height))
        painter.setClipPath(_rounded(card))
        _paint_surface(painter, data, card)
        _paint_code(painter, data, plan)
        _paint_picture(painter, data, plan)
        _paint_status(painter, data, plan)
        _paint_caption(painter, data, plan)
        _paint_address(painter, data, plan)
    finally:
        painter.end()
    return pixmap


def render_card_back(
    width: int = CARD_WIDTH,
    height: int = CARD_HEIGHT,
    *,
    device_pixel_ratio: float = 1.0,
) -> QPixmap:
    """绿色光子牌背：与等待动画最上面那张牌同一张卡面（不含投影）。

    深色 45° 渐变、三团青绿光晕与靛蓝四角角标都由
    :func:`presentation.qt.photon_card.render_photon_card` 画出；发牌牌背、等待牌堆
    最上面那张与“查找设备”按钮的卡面因此是同一份位图，不会各画一套。牌背上的绿色
    粒子是动效，由 :class:`presentation.qt.card_deal_animation.CardDealAnimation`
    每帧另画（见 :attr:`CardAsset.back_particles`）。

    尺寸契约与 :func:`render_card_front` 相同（正整数、GUI 线程）。
    """
    width, height = card_size(width, height)
    return render_photon_card(width, height, _device_pixel_ratio(device_pixel_ratio))


def render_card_asset(
    data: InstrumentCardData,
    *,
    width: int = CARD_WIDTH,
    height: int = CARD_HEIGHT,
    device_pixel_ratio: float = 1.0,
) -> CardAsset:
    """从一份卡片数据渲染正面与牌背；动画只读这份结果，不再问控件要画面。

    尺寸契约与 :func:`render_card_front` 相同；``CardAsset.width`` / ``height`` 就是
    实际渲染尺寸，与位图一致。
    """
    width, height = card_size(width, height)
    ratio = _device_pixel_ratio(device_pixel_ratio)
    return CardAsset(
        front=render_card_front(
            data,
            width=width,
            height=height,
            device_pixel_ratio=ratio,
        ),
        back=render_card_back(
            width=width,
            height=height,
            device_pixel_ratio=ratio,
        ),
        width=width,
        height=height,
        radius=float(CARD_RADIUS),
        shadow_margin=CARD_ASSET_SHADOW_MARGIN,
        back_particles=True,
    )
