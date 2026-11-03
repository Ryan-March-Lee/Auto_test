"""设备发现的展示卡片：一台仪器一张圆角卡片。

卡片结构（自上而下）：

- 设备码缩写：第一行，加粗放大的等宽字，只写缩写设备码本身（如 ``DP8B1519``、
  ``5462``；设备表还没有缩写码时退回完整设备码，见 :func:`device_code`），现场对机时
  一眼看清——卡片里字号最大、最先读到的是“这是哪一台”；
- 图片区：仪器实拍图直接摆在本色卡片上（不加底板、不描边）。照片的留白各不相同
  （有的图仪器几乎顶到边，有的只占六成），所以缩放的对象是**仪器主体的包围盒**而不是
  整张画布：先找出主体边界，再把这一块等比放进图片区（不裁切仪器），几张图的“视觉
  体积”因此对齐；
- 型号与角色（如 ``DP832A·电源``、``SMW200A·信号发生器``）：型号下面的一行小字，
  用间隔号连起来，一眼看出这台仪器的型号和它在本次测试里承担的角色；
- 地址：最下面一行的灰色小字，只写本次扫描得到的 IP（如 ``192.168.1.102``）。地址会
  漂移，所以不参与身份，只作连接信息。

卡片只负责显示与选中状态，不知道扫描结果的数据结构：选择、应用与校验仍由
:class:`presentation.qt.device_discovery_panel.DeviceDiscoveryPanel` 负责。

**卡片内容是一份纯数据**（:class:`InstrumentCardData`）：面板从扫描结果算出“第一行写
什么、图片用哪张、状态说明是什么”，把这份数据同时交给真实卡片
（:class:`InstrumentCard`）和之后的动画资产；卡片与动画因此读同一份内容，动画不必回头
读扫描结果 DTO 或问卡片控件要文字。

卡片尺寸（:data:`CARD_WIDTH` × :data:`CARD_HEIGHT`，必要时缩到
:data:`CARD_MIN_WIDTH`）与圆角（:data:`presentation.qt.theme.CARD_RADIUS`）是设备发现页的
唯一定义：“查找设备”那张卡片按钮（:mod:`presentation.qt.scan_card`）按它取大小与圆角，
因此按钮与仪器卡片始终同形同大；结果区在窄窗口里会把卡片等比收窄到能一行排下 6 张，
宽窗口里则恢复 :data:`CARD_WIDTH`。
图片按“角色 / 型号关键字”在 ``assets/instrument_pic/`` 里查找，找不到时留空；
图片只解码一次并缓存（照片单张最大 4.7 MB，重复解码会明显拖慢一次扫描后的刷新）。

**卡片内部几何也只有这一份**：内容边距、间距、设备码/图片/地址行高与图片内缩以
:data:`CARD_CONTENT_MARGINS`、:data:`CARD_CONTENT_SPACING`、:data:`CARD_CODE_HEIGHT`、
:data:`CARD_PICTURE_HEIGHT`、:data:`CARD_ADDRESS_HEIGHT`、:data:`CARD_PICTURE_MARGIN`
给出，行位置由 :func:`card_row_plan` 一次算定（也提供 :func:`card_inner_rect`、
:func:`card_code_rect`、:func:`card_picture_rect`、:func:`card_address_rect` 这几个
单行查询）。真实卡片按这份行计划钉住每一行的高度，动画正面
（:mod:`presentation.qt.card_assets`）按同一份行计划画字；图片按“仪器主体放大”的规则
由 :func:`picture_target_rect` 统一计算，因此资产不必截取控件也能与真实卡片同形。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QFontMetricsF, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme import (
    TOKENS,
    card_caption_font,
    card_status_font,
    set_tone,
)

__all__ = [
    "CARD_ADDRESS_HEIGHT",
    "CARD_BORDER_WIDTH",
    "CARD_CODE_HEIGHT",
    "CARD_CONTENT_MARGINS",
    "CARD_CONTENT_SPACING",
    "CARD_HEIGHT",
    "CARD_MIN_WIDTH",
    "CARD_PICTURE_HEIGHT",
    "CARD_PICTURE_MARGIN",
    "CARD_WIDTH",
    "INSTRUMENT_PICTURE_DIR",
    "CardRowPlan",
    "InstrumentCard",
    "InstrumentCardData",
    "InstrumentImage",
    "InstrumentPicture",
    "card_address_rect",
    "card_border_width",
    "card_caption_height",
    "card_code_rect",
    "card_inner_rect",
    "card_picture_rect",
    "card_row_plan",
    "card_size",
    "card_status_height",
    "device_code",
    "instrument_picture",
    "instrument_picture_path",
    "picture_target_rect",
]

#: 仪器图片目录：与欢迎页背景一样放在项目根目录的 ``assets/``（场景照片，不是界面字形）。
INSTRUMENT_PICTURE_DIR = (
    Path(__file__).resolve().parents[2] / "assets" / "instrument_pic"
)

# 卡片尺寸：所有卡片同宽等高，成行排列时不会参差。“查找设备”那张卡片按钮
# （:mod:`presentation.qt.scan_card`）也取这一份尺寸，并且与仪器卡片共用同一个圆角
# （:data:`presentation.qt.theme.CARD_RADIUS`），所以按钮始终和仪器卡片同形同大；
# 要改卡片大小只改这里。CARD_WIDTH 是宽窗口下的理想宽度；结果区排不下 6 张时按
# CARD_MIN_WIDTH 等比收窄，卡片不会被挤成一条窄带，也不会因此退到 5 张换行。
CARD_WIDTH = 190
CARD_MIN_WIDTH = 160
#: 卡片高度按“设备码 + 图片 + 型号·角色 + 地址”四行内容算，另留状态说明与行间留白的位置。
CARD_HEIGHT = 276
#: 卡片的边框宽度：样式表里的 1px 描边算在控件内部，布局内容因此还要再缩进 1px。
CARD_BORDER_WIDTH = 1
#: 卡片内容边距（左、上、右、下）：上下留白比左右多一点，第一行设备码不贴卡片上沿。
CARD_CONTENT_MARGINS = (10, 12, 10, 10)
#: 相邻两行之间的间距：设备码、图片、状态说明、型号·角色、地址都按这一个值排。
CARD_CONTENT_SPACING = 8
CARD_PICTURE_HEIGHT = 104
#: 图片不描边、不垫底板，只留一点内缩，让照片不贴到卡片边上。
CARD_PICTURE_MARGIN = 4
#: 设备码标签固定两行高度：加粗放大的等宽字下长序列号会折行，卡片高度因此保持不变。
CARD_CODE_HEIGHT = 56
#: 地址行固定一行高度：有地址时写 IP，没有地址时那一行留空，卡片高度不变。
CARD_ADDRESS_HEIGHT = 16
#: 卡片网格的最大列数：卡片区再宽也不排成一条长带子。6 列让现场的 5 台登记仪器
#: 加 1 台未登记演示设备排成一行，不必在 4 台处就换行。
_MAX_COLUMNS = 6

#: 按“角色”“型号关键字”映射到图片文件名（不含扩展名）。角色优先：同型号的设备换了
#: 角色也不会拿错图；型号关键字兜住没有登记角色但型号已知的设备（如未登记的设备）。
_ROLE_PICTURES = {
    "signal_generator": "signal_source",
    "spectrum_analyzer": "spectrum_analyzer",
    "power_supply": "DC_source",
}
_MODEL_PICTURES = (
    ("smw", "signal_source"),
    ("fpl", "spectrum_analyzer"),
    ("fsu", "spectrum_analyzer"),
    ("fsv", "spectrum_analyzer"),
    ("4082", "spectrum_analyzer"),
    ("dp8", "DC_source"),
    ("dp2", "DC_source"),
)
_PICTURE_SUFFIXES = (".png", ".jpg", ".jpeg")


def _picture_name_for(role: str, model: str) -> str | None:
    """按角色、再按型号关键字选图片；都不匹配时返回 ``None``（用占位图）。"""
    name = _ROLE_PICTURES.get(role)
    if name:
        return name
    folded = (model or "").casefold()
    if folded:
        for keyword, candidate in _MODEL_PICTURES:
            if keyword in folded:
                return candidate
    return None


def instrument_picture_path(role: str = "", model: str = "") -> Path | None:
    """返回这台仪器对应的图片文件；目录里没有这张图时返回 ``None``。"""
    name = _picture_name_for(role or "", model or "")
    if name is None:
        return None
    for suffix in _PICTURE_SUFFIXES:
        candidate = INSTRUMENT_PICTURE_DIR / f"{name}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def device_code(device: Any, visitor_index: int = 0) -> tuple[str, str]:
    """返回 ``(卡片第一行的缩写码, 悬浮提示里的连接附注)``。

    第一行只写缩写设备码——现场核对实物的那一个字符串，不拼型号、也不加角色前缀：

    - **设备表登记过缩写码**：原样写 ``DP8B1519``（电源的缩写码同时是配置里的
      ``supply_id``）、``5462``；
    - **还没有缩写码**（旧设备表还没保存过、未登记设备）：退回完整设备码
      （``DP8B214901519`` / ``MY51234567``）。发现过程绝不生成缩写码——凭空造一个看起来
      像缩写字、却和配置里的引用对不上的字符串，比显示完整设备码更糟；
    - **读不出身份**：``未识别 #n``（``visitor_index`` 由面板分配，同一台设备两次刷新拿到
      同一个号）。

    型号与角色写在卡片下面的型号行（由调用方单独传入），因此缩写码里不再重复型号。

    附注只写连接细节（地址、探测端口），不重复缩写码：身份由悬浮提示里的身份文本表达，
    IP 作为连接信息单独写在卡片最下面一行的灰色小字里（由调用方传入），不占设备码那一行。
    """
    identity = getattr(device, "identity", None)
    if identity is None:
        return f"未识别 #{max(1, int(visitor_index or 0))}", ""
    model = str(getattr(identity, "model", "") or "").strip()
    full_code = str(getattr(identity, "full_code", "") or "").strip()
    label = str(getattr(device, "label", "") or "").strip()
    code = label or full_code or model
    detail: list[str] = []
    address = str(getattr(device, "visa_address", "") or "").strip()
    if not address:
        last_ip = str(getattr(device, "ip", "") or "").strip()
        if last_ip:
            port = str(getattr(device, "port", "") or "").strip()
            address = f"TCPIP0::{last_ip}::inst0::INSTR"
            if port:
                detail.append(f"探测端口 {port}")
    if address:
        detail.append(f"地址 {address}")
    return code, "\n".join(detail)


# ---- 图片解码与“视觉大小”归一化 -------------------------------------------------

#: 找仪器主体边界用的缩略图边长：在缩略图上逐像素找边界，再折回原图坐标。
#: 原图最大 3207×2293，逐像素扫描不值得；这个尺寸上的边界误差约 1%，肉眼看不出来。
_PROBE_SIZE = 128
#: 与背景色的差异阈值（逐通道取最大差）：不超过它的像素算作照片留白。
_BACKGROUND_TOLERANCE = 14


def _content_rect(image: QImage) -> QRectF:
    """返回仪器主体在原图中的包围盒（原图像素坐标）。

    主体 = 与四角背景色都不相同的像素；透明留白因此也算背景。照片本来没有留白、
    或者整张图都判成背景时退回整张图——宁可显示原样，也不裁掉看不清的仪器。
    """
    width = float(image.width())
    height = float(image.height())
    full = QRectF(0.0, 0.0, width, height)
    if width <= 0.0 or height <= 0.0:
        return full
    probe = image.scaled(
        _PROBE_SIZE, _PROBE_SIZE, Qt.KeepAspectRatio, Qt.SmoothTransformation
    ).convertToFormat(QImage.Format_RGBA8888)
    probe_width = probe.width()
    probe_height = probe.height()
    if probe_width <= 1 or probe_height <= 1:
        return full
    stride = probe.bytesPerLine()
    raw = bytes(probe.constBits())

    def pixel(x: int, y: int) -> tuple[int, int, int, int]:
        start = y * stride + 4 * x
        red, green, blue, alpha = raw[start : start + 4]
        return red, green, blue, alpha

    # 照片背景不一定均匀（甚至可能带渐变），所以四角各记一个背景色，命中任一即为留白。
    corners = (
        pixel(0, 0),
        pixel(probe_width - 1, 0),
        pixel(0, probe_height - 1),
        pixel(probe_width - 1, probe_height - 1),
    )

    def is_content(color: tuple[int, int, int, int]) -> bool:
        return all(
            max(abs(color[channel] - corner[channel]) for channel in range(4))
            > _BACKGROUND_TOLERANCE
            for corner in corners
        )

    def row_has_content(y: int) -> bool:
        return any(is_content(pixel(x, y)) for x in range(probe_width))

    def column_has_content(x: int) -> bool:
        return any(is_content(pixel(x, y)) for y in range(probe_height))

    # 从四边往里找第一条有主体的行列：留白只可能连着画布边缘，这样比全图扫描快得多。
    top = next((y for y in range(probe_height) if row_has_content(y)), None)
    if top is None:
        return full
    bottom = next(
        y for y in range(probe_height - 1, top - 1, -1) if row_has_content(y)
    )
    left = next((x for x in range(probe_width) if column_has_content(x)), None)
    if left is None:
        return full
    right = next(
        x for x in range(probe_width - 1, left - 1, -1) if column_has_content(x)
    )
    scale_x = width / probe_width
    scale_y = height / probe_height
    x0 = left * scale_x
    y0 = top * scale_y
    x1 = min(width, (right + 1) * scale_x)
    y1 = min(height, (bottom + 1) * scale_y)
    if x1 <= x0 or y1 <= y0:
        return full
    return QRectF(x0, y0, x1 - x0, y1 - y0)


@dataclass(frozen=True)
class InstrumentPicture:
    """一张已解码的仪器图，以及它的仪器主体在图中占的位置（原图像素坐标）。"""

    pixmap: QPixmap
    content: QRectF

    def is_empty(self) -> bool:
        return self.pixmap.isNull()

    def source_rect(self) -> QRectF:
        """要从原图里取的那一块（原图像素坐标）：仪器主体，没有图片时为空。"""
        if self.is_empty():
            return QRectF()
        if self.content.isEmpty():
            return QRectF(self.pixmap.rect())
        return self.content


class _PictureCache:
    """按文件路径缓存已解码的原图与它的主体边界；键是 ``(路径, 文件大小, 修改时间)``。"""

    def __init__(self) -> None:
        self._items: dict[tuple[str, int, int], InstrumentPicture] = {}

    def picture(self, path: Path | None) -> InstrumentPicture:
        if path is None:
            return InstrumentPicture(QPixmap(), QRectF())
        try:
            stat = path.stat()
        except OSError:
            return InstrumentPicture(QPixmap(), QRectF())
        key = (str(path), int(stat.st_size), int(stat.st_mtime))
        cached = self._items.get(key)
        if cached is not None:
            return cached
        picture = QPixmap(str(path))
        picture_entry = InstrumentPicture(picture, _content_rect(picture.toImage()))
        self._items[key] = picture_entry
        return picture_entry


_PICTURES = _PictureCache()


def instrument_picture(path: Path | None) -> InstrumentPicture:
    """按路径取已解码的仪器图（含主体包围盒）；同一张图只解码一次。

    真实卡片（:class:`InstrumentImage`）与动画正面（:mod:`presentation.qt.card_assets`）
    都从这里取图：两边看到的是同一张位图、同一个主体边界，因此动画正面不需要截取控件。
    """
    return _PICTURES.picture(path)


def picture_target_rect(
    source: QRectF,
    area: QRectF,
    margin: float = CARD_PICTURE_MARGIN,
) -> QRectF:
    """把 ``source``（仪器主体）等比放进 ``area``（图片区），居中并留 ``margin`` 内缩。

    “放大的是主体包围盒而不是整张画布”：照片留白多少不会改变仪器在卡片上的视觉大小。
    真实卡片与动画正面共用这一份落点计算。
    """
    if source.isEmpty() or area.isEmpty():
        return QRectF()
    target = area.adjusted(margin, margin, -margin, -margin)
    if target.width() <= 0.0 or target.height() <= 0.0:
        return QRectF()
    scale = min(target.width() / source.width(), target.height() / source.height())
    width = source.width() * scale
    height = source.height() * scale
    return QRectF(
        target.center().x() - width / 2.0,
        target.center().y() - height / 2.0,
        width,
        height,
    )


# ---- 卡片内部行位置 ----------------------------------------------------------


def card_inner_rect(
    width: int = CARD_WIDTH,
    height: int = CARD_HEIGHT,
    *,
    border: int = CARD_BORDER_WIDTH,
) -> QRectF:
    """卡片内容区（卡片坐标）：扣掉描边与内容边距后的那一块。

    ``border`` 是描边宽度，默认取未选中时的 :data:`CARD_BORDER_WIDTH`（1px）。行计划
    固定用它，不跟着选中状态变：样式表把边框加粗到 2px 时，多出来的那 1px 压在内容区
    的留白上，不改变每一行的位置（见 :func:`card_border_width`）。
    """
    left, top, right, bottom = CARD_CONTENT_MARGINS
    return QRectF(
        float(border + left),
        float(border + top),
        max(
            0.0,
            float(width - 2 * border - left - right),
        ),
        max(
            0.0,
            float(height - 2 * border - top - bottom),
        ),
    )


def card_code_rect(
    width: int = CARD_WIDTH,
    height: int = CARD_HEIGHT,
    *,
    border: int = CARD_BORDER_WIDTH,
) -> QRectF:
    """第一行设备码的区域（卡片坐标）：固定两行高度。"""
    inner = card_inner_rect(width, height, border=border)
    return QRectF(
        inner.left(),
        inner.top(),
        inner.width(),
        min(float(CARD_CODE_HEIGHT), inner.height()),
    )


def card_picture_rect(
    width: int = CARD_WIDTH,
    height: int = CARD_HEIGHT,
    *,
    border: int = CARD_BORDER_WIDTH,
) -> QRectF:
    """图片区的区域（卡片坐标）：紧跟在设备码下面一个行间距。"""
    inner = card_inner_rect(width, height, border=border)
    code = card_code_rect(width, height, border=border)
    top = code.bottom() + CARD_CONTENT_SPACING
    return QRectF(
        inner.left(),
        top,
        inner.width(),
        min(CARD_PICTURE_HEIGHT, max(0.0, inner.bottom() - top)),
    )


def card_address_rect(
    width: int = CARD_WIDTH,
    height: int = CARD_HEIGHT,
    *,
    border: int = CARD_BORDER_WIDTH,
) -> QRectF:
    """最下面地址行的区域（卡片坐标）：贴着内容区底边，固定一行高度。"""
    inner = card_inner_rect(width, height, border=border)
    row_height = min(float(CARD_ADDRESS_HEIGHT), inner.height())
    return QRectF(
        inner.left(),
        inner.bottom() - row_height,
        inner.width(),
        row_height,
    )


def card_size(width: Any = CARD_WIDTH, height: Any = CARD_HEIGHT) -> tuple[int, int]:
    """卡片尺寸的输入契约：宽高必须是正整数。

    真实卡片与动画正面都按这份尺寸排字，因此尺寸只接受明确的整数：``0``、负数、``190.0``
    之类的值一律报错，而不是悄悄画出一张空位图、或者让 :class:`CardAsset` 的元数据与
    位图的实际尺寸对不上（``190.4`` 这类尺寸的取整方向没人能事先说清）。
    """
    for name, value in (("width", width), ("height", height)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"卡片 {name} 必须是整数，收到 {value!r}")
        if value <= 0:
            raise ValueError(f"卡片 {name} 必须是正整数，收到 {value!r}")
    return int(width), int(height)


def _status_line_height() -> int:
    """状态说明一行的高度：折行后的行高，也是这一行的最小高度。"""
    return max(1, QFontMetrics(card_status_font()).height())


def card_caption_height() -> int:
    """型号·角色行高：单行标签的高度（字体取自样式表的 10.5pt、600）。"""
    return max(1, QFontMetrics(card_caption_font()).height())


def card_status_height(text: str, width: float) -> int:
    """状态说明在 ``width`` 宽的内容区里折行后需要的高度；空串为 0。

    真实卡片把这一行的高度直接钉成这个值（不交给布局自行折行分配），动画正面也按它
    留白，所以两边读到的行高是同一个数。
    """
    if not text:
        return 0
    line = _status_line_height()
    if width <= 0.0:
        return line
    wrapped = QFontMetricsF(card_status_font()).boundingRect(
        QRectF(0.0, 0.0, float(width), 1_000_000.0),
        int(Qt.TextWordWrap),
        text,
    )
    return max(line, int(round(wrapped.height())))


class InstrumentImage(QWidget):
    """图片区：按仪器主体等比放入卡片，不裁切仪器，也不垫底板描边。

    缩放的是主体包围盒而不是整张照片：留白多少不再影响仪器在卡片上的视觉大小。
    """

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._picture = InstrumentPicture(QPixmap(), QRectF())
        self.setFixedHeight(CARD_PICTURE_HEIGHT)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_StyledBackground, True)

    def set_picture(self, path: Path | None) -> None:
        self._picture = instrument_picture(path)
        self.update()

    def has_picture(self) -> bool:
        return not self._picture.is_empty()

    def sizeHint(self) -> QSize:
        return QSize(CARD_WIDTH, CARD_PICTURE_HEIGHT)

    def source_rect(self) -> QRectF:
        """要从原图里取的那一块（原图像素坐标）：仪器主体，没有图片时为空。"""
        return self._picture.source_rect()

    def picture_rect(self) -> QRectF:
        """仪器图实际画出的矩形（图片区坐标）：只由主体决定，与照片留白无关。"""
        return picture_target_rect(self.source_rect(), QRectF(self.rect()))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # 图片直接画在卡片本色上：底板与描边都由样式表关掉，这里只画仪器图；
        # 只画主体那一块，等比缩放并居中。
        picture = self._picture.pixmap
        if picture.isNull():
            # 没有对应图片时不画任何图形，只留一行淡字，避免看起来像加载失败。
            # 颜色取界面里的浅灰令牌：动画正面（presentation.qt.card_assets）用同一个值，
            # 两边因此不会一边是令牌色、一边是调色板派生出来的另一种灰。
            painter.setPen(QColor(TOKENS["text_muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "暂无图片")
            return
        destination = self.picture_rect()
        source = self.source_rect()
        if destination.isEmpty() or source.isEmpty():
            return
        painter.drawPixmap(destination, picture, source)


class _ElidedLabel(QLabel):
    """单行标签：放不下时右侧省略，``text()`` 仍返回完整文字。

    卡片只有 170px 宽，``未知设备 (N9020A)`` 这类名字放不下时直接裁掉看起来像排版错误；
    省略号既保持卡片整齐，又不丢信息（完整内容见悬浮提示）。
    """

    def __init__(self, text: str = "", parent: Any = None) -> None:
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(
            self.text(), Qt.ElideRight, max(0, rect.width())
        )
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(rect, int(self.alignment()), text)


def _caption_text(model: str, role_text: str) -> str:
    """卡片底部的“型号·角色”：两段都有的中间用间隔号，缺一段就只写另一段。

    型号可重复，所以型号本身不是身份；角色是设备表登记的固定测试角色，两者放在一起
    才说明“这台仪器是干什么用的”。
    """
    parts = [part.strip() for part in (model or "", role_text or "") if part.strip()]
    return "·".join(parts)


@dataclass(frozen=True)
class InstrumentCardData:
    """一张仪器卡片要显示的全部内容：纯数据，不依赖 Qt 控件，也不认识扫描结果 DTO。

    面板从一次扫描结果里算出这份数据（第一行写哪个设备码、图片用哪张、状态说明是什么），
    再把它交给真实卡片。之后动画资产也从同一份数据渲染正面，因此动画跑起来时不再回头
    读设备扫描结果、也不向卡片控件要文字。

    字段与卡片上的位置一一对应：

    - ``heading``：这台设备在结果里的名字（面板按它找卡片、写日志），卡片本身不画；
    - ``code``：第一行加粗放大的缩写设备码；
    - ``model``：型号，只出现在型号·角色那一行；
    - ``role``：测试角色的界面文案（如 ``电源``），已经是给人看的字，不是领域角色键；
    - ``address``：最下面一行的连接地址（本次扫描到的 IP），没有时那一行留空；
    - ``status``：不可应用的原因或预选说明，空串表示不显示；
    - ``checked``：构建这一份内容时这张卡是否选中；用户之后的选择仍由页面保存；
    - ``picture_path``：图片区要显示的仪器图，``None`` 表示没有对应图片；
    - ``tooltip``：整张卡片的悬浮提示全文；
    - ``checkable``：这张卡能否被选中（不能应用的设备只显示原因）。
    """

    heading: str
    code: str = ""
    model: str = ""
    role: str = ""
    address: str = ""
    status: str = ""
    checked: bool = False
    picture_path: Path | None = None
    tooltip: str = ""
    checkable: bool = True

    def caption(self) -> str:
        """底部那一行“型号·角色”（两段都缺时为空串）。

        真实卡片与之后的动画正面都从这里取整行文字，不各拼一次。
        """
        return _caption_text(self.model, self.role)


# ---- 卡片行计划：真实卡片与动画正面共用的“每一行在哪里” ----------------------


def card_border_width(data: InstrumentCardData) -> int:
    """卡片描边的宽度（像素）：选中时样式表把边框加粗到 2px。

    这个值只影响**画出来的那道边框**：2px 的边框向卡片内部多压 1px，压住的是内容区的
    留白，不改变每一行的位置（行位置始终按 :data:`CARD_BORDER_WIDTH` 计算，见
    :func:`card_row_plan`）。这样同一张卡片在选中/取消时文字不会跳动——Qt 只在第一次
    应用样式表时按边框粗细调整内容边距，之后切换选中并不会重排，行计划因此不能跟着它走。
    """
    checked = bool(data.checked) and bool(data.checkable)
    return 2 if checked else CARD_BORDER_WIDTH


@dataclass(frozen=True)
class CardRowPlan:
    """卡片内部每一行的位置（卡片坐标，已扣掉描边与内容边距）。

    真实卡片按它钉住每一行的高度，动画正面按它画字：两边读到的是同一份几何，动画因此
    不需要截取控件，也不会因为标签自己的折行结果与资产算法不同而错位。矩形为空
    （:meth:`QRectF.isEmpty`）表示数据里没有那一段文字，那一行不显示。
    """

    width: int
    height: int
    inner: QRectF
    code: QRectF
    picture: QRectF
    status: QRectF
    caption: QRectF
    address: QRectF

    def rows(self) -> tuple[QRectF, ...]:
        """实际出现的那几行，自上而下（空行不在其中）。"""
        return tuple(
            row
            for row in (
                self.code,
                self.picture,
                self.status,
                self.caption,
                self.address,
            )
            if not row.isEmpty()
        )


def card_row_plan(
    data: InstrumentCardData,
    width: int = CARD_WIDTH,
    height: int = CARD_HEIGHT,
) -> CardRowPlan:
    """算出一张卡片每一行的位置：设备码、图片、状态说明、型号·角色、地址。

    规则与卡片原来的竖直布局一致：行距 :data:`CARD_CONTENT_SPACING`，型号·角色与地址
    贴着内容区底边，中间的留白被行距与弹性留白吃满。区别是结果唯一：每行高度都由这里
    钉死，状态说明先按宽度折行量出需要的高度，再只允许它占用“除其余各行与两个行距之外”
    的剩余高度；装不下时先让图片变矮，以免把一行字压成看不见。真实卡片上的状态说明都
    只有一行，图片因此始终是 :data:`CARD_PICTURE_HEIGHT`。
    """
    width, height = card_size(width, height)
    inner = card_inner_rect(width, height)
    spacing = CARD_CONTENT_SPACING
    available = int(inner.height())
    code_height = min(CARD_CODE_HEIGHT, available)
    picture_height = min(CARD_PICTURE_HEIGHT, max(0, available - code_height - spacing))
    caption_height = min(card_caption_height(), available)
    address_height = CARD_ADDRESS_HEIGHT if data.address else 0
    status_height = (
        min(card_status_height(data.status, inner.width()), available)
        if data.status
        else 0
    )
    # 状态说明最少保留一行：宁可先缩图片，也不把一行字压成看不见。
    status_floor = min(_status_line_height(), available) if data.status else 0

    def fixed_tail() -> int:
        return caption_height + (spacing + address_height if address_height else 0)

    def fixed_head() -> int:
        rows = code_height + spacing + picture_height
        if status_height:
            rows += spacing + status_height
        return rows

    # 预算不够时先让图片变矮，再让状态说明变矮；其余行与行距保持不变。真实卡片上的状态
    # 说明只有一行（“无法识别”“未登记 · 不可应用”“已按配置预选”），图片因此始终是
    # 104px；只有人工构造的超长说明才会占掉图片的高度，这时宁可少画一点图，也不截断文字。
    overflow = fixed_head() + fixed_tail() + 2 * spacing - available
    if overflow > 0:
        picture_height = max(0, picture_height - overflow)
        overflow = fixed_head() + fixed_tail() + 2 * spacing - available
    if overflow > 0 and status_height > status_floor:
        status_height = max(status_floor, status_height - overflow)

    code = QRectF(inner.left(), inner.top(), inner.width(), float(code_height))
    picture = QRectF(
        inner.left(),
        code.bottom() + spacing,
        inner.width(),
        float(picture_height),
    )
    status = QRectF()
    if status_height:
        status = QRectF(
            inner.left(),
            picture.bottom() + spacing,
            inner.width(),
            float(status_height),
        )
    caption = QRectF(
        inner.left(),
        inner.bottom() - float(fixed_tail()),
        inner.width(),
        float(caption_height),
    )
    address = QRectF()
    if address_height:
        address = QRectF(
            inner.left(),
            caption.bottom() + spacing,
            inner.width(),
            float(address_height),
        )
    return CardRowPlan(
        width=width,
        height=height,
        inner=inner,
        code=code,
        picture=picture,
        status=status,
        caption=caption,
        address=address,
    )


class InstrumentCard(QFrame):
    """一张设备卡片：第一行是加粗放大的设备码缩写，图片其次，下面是型号·角色与地址。

    卡片由 :class:`InstrumentCardData` 构建：凡是卡片上出现的文字与图片都来自那份数据，
    卡片不解析扫描结果，也不替面板决定“写什么”。卡片是可选控件：可以勾选的选择显示为
    “点击切换选中”的整块卡片（``checkable=True``），不能应用的设备（无法识别、未登记、
    未分配角色）只显示带警示色的状态说明，不可选中。
    """

    activated = Signal(object)

    def __init__(
        self,
        data: InstrumentCardData,
        *,
        tag: Any = None,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("instrumentCard")
        self.setFrameShape(QFrame.NoFrame)
        self.setProperty("checked", False)
        self.setProperty("enabled-state", data.checkable)
        # 行计划要等所有标签都在位之后才能应用；``setFixedSize`` 会在构造途中触发一次
        # ``resizeEvent``，那时子控件还不存在。
        self._row_plan_ready = False
        self.setFixedWidth(CARD_WIDTH)
        self.setFixedHeight(CARD_HEIGHT)
        # 尺寸固定：卡片高度不随卡片区拉伸，也不被父窗口压成一条。
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._checkable = bool(data.checkable)
        self._checked = bool(data.checked) and self._checkable
        # 卡片此刻显示的内容：选中状态以卡片实际能显示出来的为准（不能应用的卡片
        # 不会因为数据里勾着就真的选中），所以这里存一份规整后的快照。
        self._data = replace(data, checked=self._checked)
        self.tag = tag
        if self._checkable:
            self.setCursor(Qt.PointingHandCursor)
        if data.tooltip:
            self.setToolTip(data.tooltip)

        layout = QVBoxLayout(self)
        # 上下留白比左右多一点：第一行设备码不贴卡片上沿，卡片上部因此不显得挤。
        # 每行高度由 :func:`card_row_plan` 钉住（见 :meth:`_apply_row_plan`），这里的
        # 弹性留白只能吃掉行计划剩下的那一段。
        layout.setContentsMargins(*CARD_CONTENT_MARGINS)
        layout.setSpacing(CARD_CONTENT_SPACING)

        # 第一行：缩写设备码——只写码本身（型号在最下面一行），现场对机时一眼看清；
        # 这是卡片里字号最大、最加粗的一行。
        self.code_label = QLabel(data.code, self)
        self.code_label.setObjectName("instrumentCardCode")
        self.code_label.setMargin(2)
        # 设备码整段都要能读到：允许折成两行，而不是截断；单行时在两行高度里居中。
        self.code_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.code_label.setWordWrap(True)
        self.code_label.setFixedHeight(CARD_CODE_HEIGHT)
        layout.addWidget(self.code_label)

        # 第二行：仪器图，直接贴在卡片本色上。
        self.image = InstrumentImage(self)
        self.image.setObjectName("instrumentCardImage")
        self.image.set_picture(data.picture_path)
        layout.addWidget(self.image)

        # 状态说明（不能应用的原因）紧跟在图片下：下面留给型号·角色与地址。
        # 状态说明始终存在（可应用的卡片文字为空）：卡片等高，状态出现/消失不会跳动。
        self.status_label = QLabel(data.status, self)
        self.status_label.setObjectName("instrumentCardStatus")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(bool(data.status))
        set_tone(self.status_label, "warning")
        layout.addWidget(self.status_label)

        layout.addStretch(1)

        # 型号·角色：比设备码小一号的一行，写“型号·角色”（如 ``DP832A·电源``）。
        self.name_label = _ElidedLabel(data.caption(), self)
        self.name_label.setObjectName("instrumentCardName")
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setTextInteractionFlags(Qt.NoTextInteraction)
        layout.addWidget(self.name_label)

        # 最下面一行：本次扫描得到的 IP，灰色小字。地址会漂移，只是连接信息。
        self.address_label = _ElidedLabel(data.address, self)
        self.address_label.setObjectName("instrumentCardAddress")
        self.address_label.setAlignment(Qt.AlignCenter)
        self.address_label.setFixedHeight(CARD_ADDRESS_HEIGHT)
        self.address_label.setTextInteractionFlags(Qt.NoTextInteraction)
        self.address_label.setVisible(bool(data.address))
        layout.addWidget(self.address_label)
        self._bind_tooltips()
        self._apply_check_state()
        self._row_plan_ready = True
        self._apply_row_plan()

    # ---------- 行位置 ----------

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_row_plan()

    def changeEvent(self, event) -> None:
        # 样式表重新应用时 Qt 会按边框粗细改动控件的内容边距：行位置要重新对齐一次。
        super().changeEvent(event)
        if event.type() == QEvent.StyleChange:
            self._apply_row_plan()

    def _apply_row_plan(self) -> None:
        """把每一行钉在 :func:`card_row_plan` 给出的位置上。

        行高全部固定之后，竖直布局里只剩计划预留的那段弹性留白，真实卡片的行位置因此
        与动画正面逐像素同源：动画不必截取控件，也不会因为标签自己的折行结果与资产算法
        不同而错开。宽度变化（结果区会把卡片收窄到 :data:`CARD_MIN_WIDTH`）时状态说明
        会重新折行，所以这里也随 ``resizeEvent`` 重算。

        布局边距按“计划的内容区减去控件自己的内容边距”反算：Qt 只在第一次应用样式表时
        按边框粗细设置内容边距，选中/取消之后不会再改它，直接套用会让行位置随选中状态
        漂移 1px。
        """
        if not getattr(self, "_row_plan_ready", False):
            return
        plan = card_row_plan(self._data, self.width(), self.height())
        layout = self.layout()
        if layout is not None:
            margins = self.contentsMargins()
            layout.setContentsMargins(
                int(plan.inner.left()) - margins.left(),
                int(plan.inner.top()) - margins.top(),
                self.width() - int(plan.inner.right()) - margins.right(),
                self.height() - int(plan.inner.bottom()) - margins.bottom(),
            )
        self.code_label.setFixedHeight(max(1, int(plan.code.height())))
        self.image.setFixedHeight(max(1, int(plan.picture.height())))
        self.name_label.setFixedHeight(max(1, int(plan.caption.height())))
        if not plan.status.isEmpty():
            self.status_label.setFixedHeight(int(plan.status.height()))
        if not plan.address.isEmpty():
            self.address_label.setFixedHeight(int(plan.address.height()))

    # ---------- 内容 ----------

    @property
    def data(self) -> InstrumentCardData:
        """这张卡片此刻显示的内容（只读快照）。

        选中与状态说明变化时由 :meth:`set_checked` / :meth:`set_status` 一起更新，
        所以 ``card.data`` 始终等于卡片上写着的、选中的东西；面板把这份快照交给
        动画资产，动画不必回头读扫描结果或向控件要文字。
        """
        return self._data

    def _bind_tooltips(self) -> None:
        base = self.toolTip()
        for label in (
            self.name_label,
            self.code_label,
            self.status_label,
            self.address_label,
        ):
            label.setToolTip(base)

    # ---------- 选中状态 ----------

    @property
    def checkable(self) -> bool:
        return self._checkable

    def is_checked(self) -> bool:
        return self._checked

    def set_checked(self, checked: bool, *, emit: bool = False) -> None:
        """切换选中状态；``emit=False`` 用于扫描后的预选，不触发“用户点击”。"""
        if not self._checkable:
            return
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        self._data = replace(self._data, checked=checked)
        self._apply_check_state()
        # 重新应用样式表可能改动控件的内容边距：行位置再对齐一次（行计划本身不随选中变化，
        # 见 :func:`card_border_width`）。
        self._apply_row_plan()
        if emit:
            self.activated.emit(self)

    def toggle(self) -> None:
        self.set_checked(not self._checked, emit=True)

    def _apply_check_state(self) -> None:
        self.setProperty("checked", self._checked)
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
        self.update()

    def set_status(self, text: str) -> None:
        """更新卡片上的状态说明（例如“已按配置预选”）；空串表示不显示。"""
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))
        self.status_label.setToolTip(self.toolTip())
        if text != self._data.status:
            self._data = replace(self._data, status=text)
        # 状态说明有没有、折几行都会改变行位置：行计划跟着数据重算一次。
        self._apply_row_plan()

    # ---------- 事件 ----------

    def mouseReleaseEvent(self, event) -> None:
        if (
            self._checkable
            and event.button() == Qt.LeftButton
            and self.rect().contains(event.position().toPoint())
        ):
            self.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if self._checkable and event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.toggle()
            event.accept()
            return
        super().keyPressEvent(event)


