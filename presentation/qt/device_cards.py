"""设备发现的展示卡片：一台仪器一张圆角卡片。

卡片结构（自上而下）：

- 图片区：仪器实拍图放在浅色底板中央，按整块图片区等比缩放（不裁切），几张图的
  “视觉体积”因此不受各自照片留白多少影响；
- 文字区：设备名（如 ``SMW200A-1``）、型号与角色（如 ``SMW200A · 信号发生器``）、
  缩减版设备码（``SG/SMW200A/105462``）。

卡片只负责显示与选中状态，不知道扫描结果的数据结构：选择、应用与校验仍由
:class:`presentation.qt.device_discovery_panel.DeviceDiscoveryPanel` 负责。
图片按“角色 / 型号关键字”在 ``assets/instrument_pic/`` 里查找，找不到时留空底板；
图片只解码一次并缓存（照片单张最大 4.7 MB，重复解码会明显拖慢一次扫描后的刷新）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPalette, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme import set_tone

__all__ = [
    "INSTRUMENT_PICTURE_DIR",
    "InstrumentCard",
    "InstrumentImage",
    "device_code",
    "instrument_picture_path",
]

#: 仪器图片目录：与欢迎页背景一样放在项目根目录的 ``assets/``（场景照片，不是界面字形）。
INSTRUMENT_PICTURE_DIR = (
    Path(__file__).resolve().parents[2] / "assets" / "instrument_pic"
)

# 卡片尺寸：所有卡片同宽等高，成行排列时不会参差。
_CARD_WIDTH = 178
_CARD_HEIGHT = 212
_PICTURE_HEIGHT = 96
_PICTURE_MARGIN = 8
#: 设备码标签固定两行高度：等宽字体下长序列号会折行，卡片高度因此保持不变。
_CODE_LABEL_HEIGHT = 34
#: 卡片网格的最大列数：卡片区再宽也不排成一条长带子。
_MAX_COLUMNS = 4

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


def _device_code_role(role: str, is_power_supply: bool) -> str:
    if is_power_supply or role == "power_supply":
        return "PS"
    if role == "signal_generator":
        return "SG"
    if role == "spectrum_analyzer":
        return "SA"
    return "NA"


def _falls_back_to_device_name(device: Any) -> bool:
    """显示名是不是“读不出身份”时的兜底文本（``未知设备 (型号)`` / ``<ip>:<port>``）。"""
    name = str(getattr(device, "display_name", "") or "")
    if name.startswith("未知设备"):
        return True
    return bool(_IP_PATTERN.search(name))


def device_code(device: Any, visitor_index: int = 0) -> tuple[str, str]:
    """返回 ``(缩减版设备码, 悬浮提示全文)``。

    设备码由“角色缩写 / 型号 / 序列号”组成（如 ``SG/SMW200A/105462``），读不出身份时用
    ``未识别 #n``（``visitor_index`` 由面板分配）：同一台设备两次刷新拿到同一个号。提示里
    给出完整的设备码与地址，IP 只作为本次可达的连接细节，不占卡片正面的文字行。
    """
    identity = getattr(device, "identity", None)
    role = str(getattr(device, "known_role", "") or "")
    if identity is None:
        code = f"未识别 #{max(1, int(visitor_index or 0))}"
        reason = str(getattr(device, "error", "") or "端口有应答但 *IDN? 无法解析")
        return code, reason
    model = str(getattr(identity, "model", "") or "").strip()
    serial = str(getattr(identity, "serial", "") or "").strip()
    is_supply = bool(getattr(device, "is_power_supply", False))
    parts = [part for part in (model, serial) if part]
    code = "/".join([_device_code_role(role, is_supply), *parts])
    detail = [code]
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


# ---- 图片解码缓存 -----------------------------------------------------------


class _PictureCache:
    """按文件路径缓存已解码的原图；键是 ``(路径, 文件大小, 修改时间)``。"""

    def __init__(self) -> None:
        self._items: dict[tuple[str, int, int], QPixmap] = {}

    def picture(self, path: Path | None) -> QPixmap:
        if path is None:
            return QPixmap()
        try:
            stat = path.stat()
        except OSError:
            return QPixmap()
        key = (str(path), int(stat.st_size), int(stat.st_mtime))
        cached = self._items.get(key)
        if cached is not None:
            return cached
        picture = QPixmap(str(path))
        self._items[key] = picture
        return picture


_PICTURES = _PictureCache()


class InstrumentImage(QWidget):
    """图片区：浅色底板中央等比放入仪器图，不裁切、不留出参差的边距。"""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._picture = QPixmap()
        self.setFixedHeight(_PICTURE_HEIGHT)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_StyledBackground, True)

    def set_picture(self, path: Path | None) -> None:
        self._picture = _PICTURES.picture(path)
        self.update()

    def has_picture(self) -> bool:
        return not self._picture.isNull()

    def sizeHint(self) -> QSize:
        return QSize(_CARD_WIDTH, _PICTURE_HEIGHT)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        # 底板由样式表画出（带圆角），这里只画仪器图；图片按整块区域等比缩放并居中。
        if self._picture.isNull():
            # 没有对应图片时不画任何图形，只留一行淡字，避免看起来像加载失败。
            painter.setPen(self.palette().color(QPalette.PlaceholderText))
            painter.drawText(self.rect(), Qt.AlignCenter, "暂无图片")
            return
        target = QRectF(self.rect()).adjusted(
            _PICTURE_MARGIN, _PICTURE_MARGIN, -_PICTURE_MARGIN, -_PICTURE_MARGIN
        )
        source_width = float(self._picture.width())
        source_height = float(self._picture.height())
        if source_width <= 0 or source_height <= 0:
            return
        scale = min(target.width() / source_width, target.height() / source_height)
        width = source_width * scale
        height = source_height * scale
        destination = QRectF(
            target.center().x() - width / 2.0,
            target.center().y() - height / 2.0,
            width,
            height,
        )
        painter.drawPixmap(destination, self._picture, QRectF(self._picture.rect()))


class _ElidedLabel(QLabel):
    """单行标签：放不下时右侧省略，``text()`` 仍返回完整文字。

    卡片只有 178px 宽，``未知设备 (N9020A)`` 这类名字放不下时直接裁掉看起来像排版错误；
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


class InstrumentCard(QFrame):
    """一张设备卡片：上方图片，下方设备名、型号角色徽标与缩减版设备码。

    卡片是可选控件：可以勾选的选择显示为“点击切换选中”的整块卡片（``checkable=True``），
    不能应用的设备（无法识别、未登记、未分配角色）只显示带警示色的状态说明，不可选中。
    """

    activated = Signal(object)

    def __init__(
        self,
        heading: str,
        model: str,
        role_text: str,
        code: str,
        *,
        status_text: str = "",
        tooltip: str = "",
        picture: Path | None = None,
        checkable: bool = True,
        checked: bool = False,
        tag: Any = None,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("instrumentCard")
        self.setFrameShape(QFrame.NoFrame)
        self.setProperty("checked", False)
        self.setProperty("enabled-state", checkable)
        self.setFixedWidth(_CARD_WIDTH)
        self.setFixedHeight(_CARD_HEIGHT)
        # 尺寸固定：卡片高度不随卡片区拉伸，也不被父窗口压成一条。
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._checkable = bool(checkable)
        self._checked = bool(checked) and self._checkable
        self.tag = tag
        if self._checkable:
            self.setCursor(Qt.PointingHandCursor)
        if tooltip:
            self.setToolTip(tooltip)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)

        self.image = InstrumentImage(self)
        self.image.setObjectName("instrumentCardImage")
        self.image.set_picture(picture)
        layout.addWidget(self.image)
        layout.addSpacing(2)

        self.title_label = _ElidedLabel(heading, self)
        self.title_label.setObjectName("instrumentCardTitle")
        self.title_label.setTextInteractionFlags(Qt.NoTextInteraction)
        layout.addWidget(self.title_label)

        self.meta_label = _ElidedLabel(self._meta_text(model, role_text), self)
        self.meta_label.setObjectName("instrumentCardMeta")
        layout.addWidget(self.meta_label)

        self.code_label = QLabel(code, self)
        self.code_label.setObjectName("instrumentCardCode")
        self.code_label.setMargin(2)
        # 设备码整段都要能读到：允许折成两行，而不是截断。
        self.code_label.setWordWrap(True)
        self.code_label.setFixedHeight(_CODE_LABEL_HEIGHT)
        layout.addWidget(self.code_label)

        layout.addStretch(1)
        # 状态说明始终存在（可应用的卡片文字为空）：卡片等高，状态出现/消失不会跳动。
        self.status_label = QLabel(status_text, self)
        self.status_label.setObjectName("instrumentCardStatus")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(bool(status_text))
        set_tone(self.status_label, "warning")
        layout.addWidget(self.status_label)
        self._bind_tooltips()
        self._apply_check_state()

    # ---------- 内容 ----------

    @staticmethod
    def _meta_text(model: str, role_text: str) -> str:
        parts = [part for part in (model.strip(), role_text.strip()) if part]
        return " · ".join(parts)

    def _bind_tooltips(self) -> None:
        base = self.toolTip()
        for label in (self.title_label, self.meta_label, self.code_label, self.status_label):
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
        self._apply_check_state()
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


