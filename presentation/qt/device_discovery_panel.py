"""设备发现面板：扫描网段、以卡片展示发现结果并选择本次测试所需设备。

边界：

- 面板只负责界面与扫描线程生命周期；扫描实现由 ``scanner_factory(mode)`` 注入，
  因此离线测试与现场扫描共用同一套界面逻辑。
- 面板不改配置、不连接仪器：用户确认“应用到配置”后，只把所选设备交给配置页，
  由配置页写成仪器连接配置（地址来自本次发现的设备）与设备身份记录。
- **测试角色由设备表登记，界面不让用户选**：现场每台仪器只承担一种角色。卡片正面
  第一行是居中放大的仪器名（型号，如 ``SMW200A``），最下面一行是设备码缩写（信号源与
  频谱仪是``角色缩写/型号/序列号``，电源就是它的短标签 ``DP8B1519``）；电源是同一个
  类别下的多台设备，靠短标签区分，不再用“电源 PS3”这类槽位编号。角色与完整身份写在
  悬浮提示里。没登记角色的设备不能选中应用。
- **多台电源各自独立**：电源不再按角色键互相覆盖，勾选几台就传递几台；只有信号源与
  频谱仪保持“一个角色一台设备”的唯一性。
- 发现结果**包含未知设备**：未在设备表中登记的序列号同样显示一张卡片并标记为未登记；
  身份完全读不出来的地址也留一张卡片，但标记为“无法识别”，不允许被应用成测试角色。
- 结果以卡片呈现（:mod:`presentation.qt.device_cards`）：点击卡片切换选中，卡片区
  按面板宽度自动换行，不再是一张横向表格。

版式：扫描行（扫描网段 / 扫描方式）在标题正下方居中，结果框在它下面；框里在还没点过
“查找设备”时铺一层浅灰底、把按钮放在正中，点下去之后按钮与灰底一起收走，框中央换成
“正在查找设备”，结果回来后再换成卡片。**结果框的高度是固定的**（两行卡片 + 框内的按钮
与状态行）：查找前后、结果多少都不改变框的大小，卡片超出时只在框内滚动。
"""

from __future__ import annotations

import ipaddress
import re
import threading
from typing import Any, Callable, Sequence

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from application.ports.device_discovery import (
    DEMO_SUBNET,
    SCAN_MODE_NETWORK,
    SCAN_MODE_SIMULATED,
    DeviceDiscoveryError,
)
from domain.instruments.labels import instrument_display_label, supply_identity_text
from domain.instruments.models import (
    POWER_SUPPLY_ROLE,
    canonical_role,
)

from .config_form_bindings import (
    binding_device_key,
    binding_records,
    power_supply_bindings,
)
from .device_cards import (
    _CARD_HEIGHT,
    _CARD_WIDTH,
    _MAX_COLUMNS,
    InstrumentCard,
    device_code,
    instrument_picture_path,
)
from .input_controls import WheelSafeComboBox
from .theme import set_button_variant, set_tone

# 角色标签是“本次测试语义”，与设备身份（序列号）分开：设备可以换 IP，角色不会变。
# 角色值来自设备表 ``instruments.json`` 的 ``role`` 字段，这里只负责显示。电源的类别是
# ``power_supply``（多台共用，按短标签区分），不再有 ``PS1``..``PS4`` 这类槽位编号。
ROLE_LABELS: dict[str, str] = {
    "signal_generator": "信号发生器",
    "spectrum_analyzer": "频谱分析仪",
    POWER_SUPPLY_ROLE: "电源",
}
SCAN_MODE_CHOICES: tuple[tuple[str, str], ...] = (
    (SCAN_MODE_SIMULATED, "模拟设备（离线演示）"),
    (SCAN_MODE_NETWORK, "真实网络扫描"),
)
# 卡片网格的间距：列间留白由网格自己算，卡片本身不设外边距。
_CARDS_SPACING = 10
_CARDS_MIN_WIDTH = _CARD_WIDTH + _CARDS_SPACING
#: 卡片区的最小高度：结果框再矮也要露出一行卡片的位置（约图片区 + 文字区）。
_CARD_MIN_AREA_HEIGHT = 190
#: 结果框（圆角矩形）的高度固定：按“两行卡片 + 框内的按钮与状态行”算出来（
#: :meth:`DeviceDiscoveryPanel._results_box_height`）。高度不随状态变化（未查找 /
#: 正在查找 / 有结果都一样大），所以点一次“查找设备”不会让页面跳动；卡片的行数超过
#: 这个高度时只在框内滚动。两行正好放下场地上的 5 台仪器（最多 4 列）。
_RESULTS_BOX_ROWS = 2
#: 结果框的三种状态：还没查找（灰底 + 中央按钮）、正在查找（中央进度）、有结果（卡片）。
BOX_STATE_IDLE = "idle"
BOX_STATE_BUSY = "busy"
BOX_STATE_RESULTS = "results"
BOX_STATES: tuple[str, ...] = (BOX_STATE_IDLE, BOX_STATE_BUSY, BOX_STATE_RESULTS)
#: 扫描期间写在框中央的话：按钮与灰底收走后，这里说明正在读取设备。
LOADING_TEXT = "正在查找设备 …"
_IP_PATTERN = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")


def role_label(role: Any) -> str:
    """返回角色的界面标签；未登记或未知角色返回空串。"""
    return ROLE_LABELS.get(str(role or ""), "")


def device_heading(device: Any) -> str:
    """结果表里的一行设备描述。

    每类设备都写“身份在前、类别在后”，具体格式由 ``domain.instruments.labels`` 提供，
    界面不各自拼括号：

    - 电源：``DP8B1519（DP832A，电源）``——短标签就是 ``supply_id``，是业务主键，排在最前；
    - 信号源 / 频谱仪：``SMW200A（5462，信号发生器）``——型号在前，短标签用来区分同型号的
      另一台；没有标签时退回资产名 ``SMW200A-1（信号发生器）``（旧设备表还没保存过）；
    - 未登记设备：``未知设备（型号）``。

    资产名里的 ``-1`` 只是文件里的软件资产名，只要设备表保存过就有短标签，界面上不会出现。
    """
    role = canonical_role(getattr(device, "known_role", None))
    heading = str(device.display_name)
    label = role_label(role)
    if role == POWER_SUPPLY_ROLE:
        identity = getattr(device, "identity", None)
        model = getattr(identity, "model", "") or ""
        return f"{heading}（{model}，{label}）" if model else f"{heading}（{label}）"
    identity = getattr(device, "identity", None)
    short_label = str(getattr(device, "label", "") or "").strip()
    model = str(getattr(identity, "model", "") or "").strip()
    if short_label and model:
        return instrument_display_label(short_label, model, label)
    return f"{heading}（{label}）" if label else heading


def device_applicable(device: Any) -> bool:
    """这台设备能否被应用：身份可解析，且设备表登记了固定测试角色。"""
    return bool(device.has_identity and canonical_role(device.known_role))


def card_heading(device: Any, visitor_index: int = 0) -> str:
    """卡片上的设备名：电源用短标签，其它登记设备用“型号（短标签）”；读不出身份时用兜底显示名。

    电源的短标签是现场核对实物用的名字（如 ``DP8B1519``），也是配置里的 ``supply_id``，
    所以单独显示；信号源与频谱仪用 ``SMW200A（5462）``——型号在前，括号里的短标签区分同型号
    的另一台。没有标签时（旧设备表还没保存过）才退回资产名 ``SMW200A-1``；“未知设备 (型号)”
    这类兜底文本原样显示。身份完全读不出来的地址改称 ``未识别设备 #n``（``visitor_index``
    由面板分配）：它有卡片、有访客编号，但没有可写的身份。
    """
    role = canonical_role(getattr(device, "known_role", None))
    label = str(getattr(device, "label", "") or "").strip()
    model = card_model(device)
    if role == POWER_SUPPLY_ROLE and label:
        return label
    if label and model:
        return instrument_display_label(label, model)
    name = str(getattr(device, "display_name", "") or "").strip()
    if getattr(device, "is_unidentified", False):
        return f"未识别设备 #{max(1, int(visitor_index or 0))}"
    return name or "未命名设备"


def card_model(device: Any) -> str:
    """卡片上的型号；读不出身份时为空串（角色徽标单独显示状态）。"""
    identity = getattr(device, "identity", None)
    return str(getattr(identity, "model", "") or "").strip() if identity is not None else ""


def card_status_text(device: Any) -> str:
    """不能选中的卡片上的状态说明；可应用的设备返回空串。"""
    if device.is_unidentified:
        return "无法识别"
    if not device.is_known:
        return "未登记 · 不能应用"
    if not device.has_role:
        return "未分配角色 · 不能应用"
    return ""


def subnet_from_address(address: Any) -> str | None:
    """从已有 VISA 地址推导 ``/24`` 网段，作为扫描框的初值。"""
    if not isinstance(address, str):
        return None
    match = _IP_PATTERN.search(address)
    if match is None:
        return None
    try:
        return str(ipaddress.IPv4Network(f"{match.group(1)}/24", strict=False))
    except ValueError:
        return None


class _CardGridLayout(QGridLayout):
    """卡片网格：卡片按固定宽度从左往右排，多出来的横向空间留在右侧。"""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setContentsMargins(2, 2, 2, 2)
        self.setHorizontalSpacing(_CARDS_SPACING)
        self.setVerticalSpacing(_CARDS_SPACING)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)


class DiscoveryWorker(QThread):
    """在后台线程执行一次扫描；只上报结果与失败，不触碰界面。"""

    progress = Signal(int, int)
    completed = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self,
        scanner_factory: Callable[[str], Any],
        subnet: str,
        mode: str,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.scanner_factory = scanner_factory
        self.subnet = subnet
        self.mode = mode
        self.scanner: Any = None
        # 取消标记在构造时创建并交给扫描器：即使取消发生在扫描开始之前也不会丢失。
        self.cancel_event = threading.Event()

    def run(self) -> None:
        try:
            scanner = self.scanner_factory(self.mode)
        except (DeviceDiscoveryError, OSError, ValueError) as error:
            self.failed.emit(f"无法开始扫描: {error}")
            return
        scanner.set_cancel_event(self.cancel_event)
        if self.isInterruptionRequested():
            self.cancel_event.set()
        self.scanner = scanner
        try:
            result = scanner.scan(
                self.subnet,
                progress=lambda done, total: self.progress.emit(int(done), int(total)),
            )
        except DeviceDiscoveryError as error:
            self.failed.emit(str(error))
            return
        except (OSError, ValueError, RuntimeError) as error:
            # 扫描失败也必须回到界面：否则按钮一直停在“正在扫描”，用户无法重试。
            self.failed.emit(f"扫描失败: {error}")
            return
        if self.cancel_event.is_set() or self.isInterruptionRequested():
            self.cancelled.emit()
            return
        self.completed.emit(result)


class DeviceDiscoveryPanel(QWidget):
    """扫描 + 结果框控件；通过 :attr:`apply_requested` 交付用户选中的设备。

    版式分两段：扫描行（扫描网段、扫描方式）居中放在标题下面，不进结果框；“查找设备”
    是结果框正中的落点。还没点它时框里铺一层浅灰底，点下去之后按钮与灰底一起收走，
    框中央换成扫描进度，结果回来后再换成卡片。
    """

    apply_requested = Signal(object)
    clear_requested = Signal()
    log_message = Signal(str)

    def __init__(
        self,
        *,
        scanner_factory: Callable[[str], Any],
        default_subnet: str | None = None,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        # 面板高度贴着内容：结果框的高度是固定的（见 ``_results_box_height``），面板因此不
        # 会随着扫描状态或结果数量改变大小；多出来的高度留给页面里的弹性空白。用
        # Minimum（而不是 Maximum）：Maximum 会让布局把它压到 0，卡片区就被裁掉一半
        # 并与按钮重叠。
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self.scanner_factory = scanner_factory
        self.worker: DiscoveryWorker | None = None
        self.devices: list[Any] = []
        self.last_mode: str = SCAN_MODE_SIMULATED
        self._rows: list[dict[str, Any]] = []
        # 读不出身份的地址按“访客”编号（1、2、…），同一台设备两次刷新拿到同一个号。
        self._visitor_numbers: dict[tuple[str, int], int] = {}
        self._closed = False
        # 是否点过“查找设备”：浅灰底只属于点之前的那一次空框。
        self._scanned = False
        # 配置里保存的设备身份（角色 -> 序列号/device_key），用于扫描后按身份预选。
        self.device_bindings: dict[str, dict[str, Any]] = {}
        self._build_ui(default_subnet or DEMO_SUBNET)

    def set_device_bindings(self, bindings: Any) -> None:
        """登记配置里的角色绑定；下一次扫描结果会按身份预选同一台设备。"""
        self.device_bindings = {
            str(role): dict(record)
            for role, record in (bindings or {}).items()
            if isinstance(record, dict)
        }

    # ---------- 展开/收起 ----------
    #
    # 设备发现是一条会打开/收起的抽屉：收起时框里不放卡片，展开后才是卡片网格。

    def is_open(self) -> bool:
        return bool(self.property("open"))

    def set_open(self, open_: bool) -> None:
        """展开/收起结果卡片：展开后显示卡片与操作按钮，收起后框中央换回“查看结果”。

        面板宽度由配置页分栏决定，展开/收起都不改变框的大小（框高固定，卡片多了在框内
        滚动）：卡片区声明“不要求宽度”（横向 ``Ignored``），所以卡片再多也不会把左栏撑宽。
        """
        open_ = bool(open_)
        if open_ == self.is_open():
            return
        self.setProperty("open", open_)
        self.cards_area.setVisible(open_)
        self._show_actions(open_)
        self._sync_box_centre()
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
        self._refresh_cards_geometry()
        self._refresh_results_box_height()
        self._notify_ancestors()

    def _refresh_results_box_height(self) -> None:
        """让外层布局当场重排一次。

        结果框的高度是固定的（见 :meth:`_results_box_height`），不随内容变化；这里只负责
        把“框里的东西变了”立刻告诉外层布局，框内的按钮才不会慢一拍压在卡片上。
        """
        layout = self.results_box.layout()
        if layout is not None:
            layout.invalidate()
        self._notify_ancestors()

    def _sync_results_height(self) -> None:
        """框里的文字变了（多一行或少一行）同样让外层重排一次。"""
        self._refresh_results_box_height()

    def _show_actions(self, visible: bool) -> None:
        """操作按钮只属于“展开”状态：收起时不显示用不上的按钮。"""
        for widget in (self.apply_btn, self.clear_btn, self.collapse_btn):
            widget.setVisible(visible)

    def _set_status(self, text: str) -> None:
        """框内的状态行：只有真的有话要说时占位（结果统计、失败原因、应用问题）。

        空框里不写“尚未扫描”这类常驻提示，所以没有内容时整行隐藏。
        """
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))
        self._sync_results_height()

    def clear_results(self) -> None:
        """清空上一次扫描结果；切换/重新加载配置时调用，避免旧结果看起来像当前配置。

        清空后回到“还没查找过”的样子：框中央重新出现“查找设备”，空框重新铺浅灰底。
        """
        self.devices = []
        self._clear_cards()
        self.apply_btn.setEnabled(False)
        self._set_status("")
        self.reopen_btn.setVisible(False)
        self._scanned = False
        self.set_open(False)
        self._set_box_state(BOX_STATE_IDLE)

    # ---------- 界面 ----------

    def _build_ui(self, default_subnet: str) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.subnet_edit = QLineEdit(str(default_subnet))
        self.subnet_edit.setMaximumWidth(200)
        self.subnet_edit.setToolTip("扫描范围，例如 192.168.1.0/24；单个地址按 /24 处理")
        self.mode_combo = WheelSafeComboBox()
        self.mode_combo.addItems([label for _value, label in SCAN_MODE_CHOICES])
        self.scan_btn = QPushButton("查找设备")
        self.scan_btn.setToolTip("扫描网段并读取每台设备的 *IDN?；不会改变任何仪器状态")
        set_button_variant(self.scan_btn, "secondary")
        # 扫描期间的框中央说明：按钮收走后，这里写明正在读取设备。
        self.loading_label = QLabel(LOADING_TEXT)
        self.loading_label.setAlignment(Qt.AlignHCenter)
        self.loading_label.setProperty("role", "hint")
        self.loading_label.setVisible(False)
        # 收起后仍要能回到结果：一个明确的“查看结果”，不靠再扫一次。
        self.reopen_btn = QPushButton("查看结果")
        self.reopen_btn.setToolTip("展开上一次扫描得到的设备卡片")
        set_button_variant(self.reopen_btn, "ghost")
        self.reopen_btn.setVisible(False)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("role", "hint")
        self.status_label.setVisible(False)
        self.mode_hint = QLabel("")
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setAlignment(Qt.AlignHCenter)
        self.mode_hint.setProperty("role", "hint")
        # 扫描行留在结果框外面：整行按页面中线居中，跟着“仪表配置”标题走；行里只有
        # 扫描网段与扫描方式两项，动作按钮都落在结果框里（见下面的 centre_area），
        # 所以这一行永远是对称的。
        controls = QVBoxLayout()
        controls.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        row.addWidget(QLabel("扫描网段:"))
        row.addWidget(self.subnet_edit)
        row.addSpacing(16)
        row.addWidget(QLabel("扫描方式:"))
        row.addWidget(self.mode_combo)
        row.addStretch(1)
        controls.addLayout(row)
        controls.addWidget(self.mode_hint)
        layout.addLayout(controls)
        self.mode_combo.currentIndexChanged.connect(self._update_mode_hint)

        # 结果框：一台仪器一张卡片，按框宽自动换行。卡片区放在可伸缩的滚动区里，
        # 结果多时只滚动卡片区，不把框内的操作按钮推出可视范围。
        self.results_box = QFrame()
        self.results_box.setObjectName("discoveryResultsBox")
        # 高度在 _build_ui 末尾一次钉死（见 _results_box_height）：状态与结果数量都不改变
        # 框的大小，点一次“查找设备”不会让框变大或变小。
        self.results_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        box = QVBoxLayout(self.results_box)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(8)
        # 框中央的落点：还没点过“查找设备”时，框内铺浅灰底、按钮放在正中；点下去之后
        # 按钮与灰底一起消失，同一块中央换成扫描进度；收起结果后它又变成“查看结果”。
        self.centre_area = QWidget(self.results_box)
        self.centre_area.setObjectName("discoveryCentreArea")
        # 没有卡片时它占满整块框，内部的弹性留白把按钮压在框心。
        self.centre_area.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        centre = QVBoxLayout(self.centre_area)
        centre.setContentsMargins(0, 0, 0, 0)
        centre.setSpacing(8)
        centre.addStretch(1)
        # 中间这一行自己按可见项居中：空框里只有一个“查找设备”，收起后两个按钮并排。
        centre_row = QHBoxLayout()
        centre_row.setSpacing(10)
        centre_row.addStretch(1)
        centre_row.addWidget(self.scan_btn)
        centre_row.addWidget(self.reopen_btn)
        centre_row.addStretch(1)
        centre.addLayout(centre_row)
        # 扫描期间的说明单独一行：它只在 busy 时出现，不挤上面那一行的居中。
        centre.addWidget(self.loading_label, 0, Qt.AlignHCenter)
        centre.addStretch(1)
        box.addWidget(self.centre_area)
        self.cards_area = QScrollArea(self.results_box)
        self.cards_area.setObjectName("instrumentCardArea")
        self.cards_area.setWidgetResizable(True)
        self.cards_area.setFrameShape(QFrame.NoFrame)
        self.cards_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cards_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.cards_area.setMinimumHeight(_CARD_MIN_AREA_HEIGHT)
        # 结果多时它自己滚动（卡片网格在滚动区里向上排），结果少时把多余高度留在框里。
        self.cards_area.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        self.cards_host = QWidget()
        self.cards_host.setObjectName("instrumentCardHost")
        self.cards_layout = _CardGridLayout(self.cards_host)
        self.cards_area.setWidget(self.cards_host)
        self.cards_area.setVisible(False)
        box.addWidget(self.cards_area)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        self.apply_btn = QPushButton("应用所选设备到配置")
        self.apply_btn.setEnabled(False)
        self.apply_btn.setToolTip(
            "把勾选设备的当前地址与身份（序列号）写入仪器连接配置；"
            "测试角色取自设备表登记的 role"
        )
        set_button_variant(self.apply_btn, "primary")
        self.clear_btn = QPushButton("清除设备绑定")
        self.clear_btn.setToolTip("删除配置中记录的角色与设备身份对应关系，不改动地址")
        set_button_variant(self.clear_btn, "ghost")
        self.collapse_btn = QPushButton("收起结果")
        self.collapse_btn.setToolTip("收起设备卡片，框中央换回“查看结果”")
        set_button_variant(self.collapse_btn, "ghost")
        self.collapse_btn.setVisible(False)
        actions.addWidget(self.apply_btn)
        actions.addWidget(self.clear_btn)
        actions.addWidget(self.collapse_btn)
        actions.addStretch(1)
        # 应用/清除只对框里的结果有效，因此留在框内的底部。
        box.addLayout(actions)
        box.addWidget(self.status_label)
        layout.addWidget(self.results_box)

        self.scan_btn.clicked.connect(self.request_scan)
        self.reopen_btn.clicked.connect(lambda: self.set_open(True))
        self.apply_btn.clicked.connect(self.request_apply)
        self.clear_btn.clicked.connect(self.request_clear)
        self.collapse_btn.clicked.connect(lambda: self.set_open(False))
        self._update_mode_hint()
        # 收起状态：只留扫描行与框中央的“查找设备”，卡片区不参与布局。
        self.setProperty("open", False)
        self._show_actions(False)
        self._refresh_cards_geometry()
        # 框的高度在这里一次钉死：此后无论查找前后还是结果多少，框都是同一个大小。
        self.results_box.setFixedHeight(self._results_box_height())
        self._set_box_state(BOX_STATE_IDLE)

    # ---------- 结果框状态 ----------

    def _results_box_height(self, rows: int = _RESULTS_BOX_ROWS) -> int:
        """结果框的固定高度：``rows`` 行卡片 + 框内的按钮行与状态行。

        高度只由卡片尺寸与框内控件算出来，跟状态（未查找 / 正在查找 / 有结果）和结果
        数量都无关：点一次“查找设备”不会让框变大或变小，页面也就不会跟着跳一下。
        设备多到超过这个高度时，卡片区在框内滚动。
        """
        layout = self.results_box.layout()
        if layout is None:
            return 0
        margins = layout.contentsMargins()
        chrome = (
            margins.top()
            + margins.bottom()
            + self.apply_btn.sizeHint().height()
            + self.status_label.sizeHint().height()
            + 2 * layout.spacing()
        )
        return chrome + rows * _CARD_HEIGHT + (rows - 1) * _CARDS_SPACING

    def box_state(self) -> str:
        """结果框当前状态：``idle``（还没查找）/ ``busy``（正在查找）/ ``results``（有结果）。"""
        return str(self.results_box.property("boxState") or BOX_STATE_IDLE)

    def _set_box_state(self, state: str) -> None:
        """切换结果框状态：中央的按钮/进度与浅灰底随状态收放。

        浅灰底只属于“还没点过查找设备”的空框：``_scanned`` 一旦置位（点过按钮），
        灰底就不再回来，失败或取消后重新出现的只是中央的按钮。
        """
        if state not in BOX_STATES:
            raise ValueError(f"未知的结果框状态: {state}")
        self.results_box.setProperty("boxState", state)
        self.results_box.setProperty(
            "filled", state == BOX_STATE_IDLE and not self._scanned
        )
        self._sync_box_centre()
        style = self.results_box.style()
        if style is not None:
            style.unpolish(self.results_box)
            style.polish(self.results_box)
        self.results_box.update()
        self._refresh_results_box_height()
        self._notify_ancestors()

    def _sync_box_centre(self) -> None:
        """框中央那一块只装“当前该做的那一下”：查找 / 正在查找 / 查看结果。

        卡片看得见时它整块让位；看不见时它占住框心——空框里是“查找设备”，扫描期间是
        进度说明，收起结果后是“查找设备 + 查看结果”（收起后仍能再查一次）。因此框里
        任何时候都只有明确的几个动作，扫描行始终只有网段与扫描方式两项、可以稳稳居中。
        """
        state = self.box_state()
        idle = state == BOX_STATE_IDLE
        collapsed_results = state == BOX_STATE_RESULTS and not self.is_open()
        self.centre_area.setVisible(idle or state == BOX_STATE_BUSY or collapsed_results)
        # 还没查找时“查找设备”就是这一块的主按钮；收起结果后它退成“再查一次”的入口。
        self.scan_btn.setVisible(idle or collapsed_results)
        self.loading_label.setVisible(state == BOX_STATE_BUSY)
        self.reopen_btn.setVisible(collapsed_results and bool(self._rows))

    # ---------- 扫描 ----------

    def current_mode(self) -> str:
        index = self.mode_combo.currentIndex()
        return SCAN_MODE_CHOICES[index][0] if 0 <= index < len(SCAN_MODE_CHOICES) else SCAN_MODE_SIMULATED

    def _update_mode_hint(self, *_args) -> None:
        """模拟模式给出显著提示：演示结果不能被误当成现场设备。"""
        simulated = self.current_mode() == SCAN_MODE_SIMULATED
        self.mode_hint.setText(
            "模拟数据仅用于离线演示：结果不会写入设备表，也不能用于真实测量。"
            if simulated
            else "将向网段内每个地址发送一条 *IDN? 只读查询；不会改变仪器状态。"
        )
        set_tone(self.mode_hint, "warning" if simulated else "muted")

    def request_scan(self) -> None:
        """启动后台扫描；同一时刻只允许一个扫描线程。

        点下去就是“开始加载设备”：结果框中央的按钮与浅灰底一起收走，换成扫描进度；
        卡片区等结果回来再展开，扫描期间框里不出现用不上的操作按钮。
        """
        if self.is_scanning():
            self._set_status("扫描正在进行，请等待完成")
            return
        subnet = self.subnet_edit.text().strip()
        self.devices = []
        self._clear_cards()
        self.apply_btn.setEnabled(False)
        self._set_controls_enabled(False)
        self._scanned = True
        self.set_open(False)
        self._set_box_state(BOX_STATE_BUSY)
        self._set_status(f"正在扫描 {subnet or '(空)'} …")
        worker = DiscoveryWorker(self.scanner_factory, subnet, self.current_mode(), self)
        worker.progress.connect(self._on_progress)
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished.connect(self._on_worker_finished)
        self.worker = worker
        worker.start()

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.scan_btn.setEnabled(enabled)
        self.subnet_edit.setEnabled(enabled)
        self.mode_combo.setEnabled(enabled)
        self.clear_btn.setEnabled(enabled)

    def _on_progress(self, done: int, total: int) -> None:
        if self._closed:
            return
        self.status_label.setText(f"正在扫描 … {done}/{total}")
        self.status_label.setVisible(True)

    def _on_completed(self, result: Any) -> None:
        if self._closed:
            return
        devices = list(result.devices)
        self.devices = devices
        self.last_mode = getattr(result, "mode", self.current_mode())
        # 有设备就展开卡片；一台都没读到就回到空框中央的“查找设备”，用户可以改网段再试
        # （已经点过一次，浅灰底不再回来）。
        self.set_open(bool(devices))
        self._set_box_state(BOX_STATE_RESULTS if devices else BOX_STATE_IDLE)
        self._fill_cards(devices)
        known = sum(1 for device in devices if device.is_known)
        usable = sum(1 for device in devices if device_applicable(device))
        no_role = sum(1 for device in devices if device.is_known and not device.has_role)
        unregistered = sum(1 for device in devices if device.is_unknown_identity)
        unidentified = sum(1 for device in devices if device.is_unidentified)
        text = f"发现 {len(devices)} 台设备：已登记 {known} 台，其中可应用 {usable} 台"
        if no_role:
            text += f"，未分配角色 {no_role} 台"
        if unregistered:
            text += f"，未登记 {unregistered} 台"
        if unidentified:
            text += f"，无法识别 {unidentified} 个地址"
        failures = tuple(result.failures)
        notices = tuple(getattr(result, "notices", ()) or ())
        if failures:
            text += "；" + "；".join(failures)
        if notices:
            text += "；提示：" + "；".join(notices)
        if self.last_mode == SCAN_MODE_SIMULATED:
            text += "（模拟数据，仅用于离线演示；不能用于真实测量）"
        self.status_label.setText(text)
        self.status_label.setVisible(True)
        self.reopen_btn.setVisible(not self.is_open() and bool(self._rows))
        # 预选说明会追加在扫描统计后面，因此先写统计再预选。
        self._preselect_bound_devices()
        self.apply_btn.setEnabled(any(device_applicable(device) for device in devices))
        self._sync_results_height()
        self.log_message.emit(f"设备扫描完成: {self.status_label.text()}")

    def _on_failed(self, message: str) -> None:
        if self._closed:
            return
        self.reopen_btn.setVisible(False)
        # 失败后框里没有结果：中央重新出现“查找设备”，用户改网段或换扫描方式再试。
        self._set_box_state(BOX_STATE_IDLE)
        self._set_status(f"扫描失败: {message}")
        self.log_message.emit(f"设备扫描失败: {message}")

    def _on_cancelled(self) -> None:
        if self._closed:
            return
        self.apply_btn.setEnabled(False)
        self._set_box_state(BOX_STATE_IDLE)
        self._set_status("扫描已取消，结果未更新")
        self.log_message.emit("设备扫描已取消")

    def _on_worker_finished(self) -> None:
        worker = self.worker
        self.worker = None
        self._set_controls_enabled(True)
        if self._closed:
            return
        if worker is not None:
            worker.deleteLater()

    def is_scanning(self) -> bool:
        worker = self.worker
        return worker is not None and worker.isRunning()

    def shutdown(self, timeout_ms: int = 3000) -> bool:
        """页面关闭时协作停止扫描；不强杀线程，失败时保留引用由调用方处理。"""
        self._closed = True
        worker = self.worker
        if worker is None:
            return True
        worker.cancel_event.set()
        worker.requestInterruption()
        scanner = worker.scanner
        if scanner is not None:
            scanner.request_cancel()
        if worker.isRunning() and not worker.wait(timeout_ms):
            return False
        self.worker = None
        return True

    # ---------- 结果卡片 ----------

    def card_count(self) -> int:
        return len(self._rows)

    def cards(self) -> list[InstrumentCard]:
        """按显示顺序返回结果卡片。"""
        return [row["card"] for row in self._rows]

    def card_headings(self) -> list[str]:
        """按显示顺序返回卡片上的设备名（测试与界面自检用）。"""
        return [heading for heading, _row in self._cards_by_heading()]

    def card_for(self, heading: str) -> InstrumentCard:
        """按设备名找卡片；找不到时抛出 ``KeyError``。"""
        for card_heading_value, row in self._cards_by_heading():
            if card_heading_value == heading:
                return row["card"]
        raise KeyError(heading)

    def card_tooltip(self, heading: str) -> str:
        """卡片（含状态说明）的悬浮提示全文。"""
        return self.card_for(heading).toolTip()

    def card_status(self, heading: str) -> str:
        """卡片上的状态说明；可应用的卡片没有状态说明，返回空串。"""
        return self.card_for(heading).status_label.text()

    def card_checked(self, heading: str) -> bool:
        """这张卡片当前是否被选中。"""
        return self.card_for(heading).is_checked()

    def set_card_checked(self, heading: str, checked: bool) -> None:
        """选中/取消一张卡片；等价于用户点了它一下，不改其他卡片。"""
        card = self.card_for(heading)
        card.set_checked(checked, emit=False)
        self._on_card_activated(card)

    def _cards_by_heading(self) -> list[tuple[str, dict[str, Any]]]:
        return [(row["heading"], row) for row in self._rows]

    def _clear_cards(self) -> None:
        for row in self._rows:
            row["card"].setParent(None)
            row["card"].deleteLater()
        self._rows = []
        self._apply_cards_layout()

    def _visitor_index(self, device: Any) -> int:
        """读不出身份的地址的“访客序号”：同一地址重复出现时编号不变。"""
        key = (str(getattr(device, "ip", "") or ""), int(getattr(device, "port", 0) or 0))
        number = self._visitor_numbers.get(key)
        if number is None:
            number = len(self._visitor_numbers) + 1
            self._visitor_numbers[key] = number
        return number

    def _card_tooltip(self, device: Any, code_detail: str) -> str:
        """卡片的悬浮提示：身份详情、角色变化与无法应用的原因都写在这里。

        电源的身份文本只有一份实现（:func:`domain.instruments.labels.supply_identity_text`）：
        短标签、型号、完整序列号与设备键按已知字段拼好，卡片提示不另写一套。其它仪器仍按
        “型号 / 厂商”给身份，未改登记方式的旧数据不会因为这条规则变样。地址作为连接信息
        单独一行，不从身份码反解析。
        """
        identity = device.identity
        lines: list[str] = [device_heading(device)]
        if identity is not None and device.is_power_supply:
            lines.append(
                supply_identity_text(
                    device.label, identity.model, identity.serial, identity.device_key
                )
            )
            if device.idn:
                lines.append(str(device.idn))
        elif identity is not None:
            lines.append(f"{identity.model} / {identity.vendor}")
            if device.label:
                lines.append(f"短标签 {device.label}")
            if device.idn:
                lines.append(str(device.idn))
        elif device.error:
            lines.append(device.error)
        if code_detail:
            lines.append(code_detail)
        status = card_status_text(device)
        if device.is_unidentified:
            lines.append("端口有应答但 *IDN? 无法解析，不能应用")
        elif device.is_unknown_identity:
            lines.append(
                "未在设备表 instruments.json 中登记，无法确定测试角色；请先登记设备"
            )
        elif not device.has_role:
            lines.append("设备表没有登记这台设备的测试角色（role）；补上后才能应用")
        elif device.is_power_supply and not device.label:
            lines.append(
                "设备表里还没有这台电源的短标签（label），当前显示的是资产名，"
                "也无法写入配置引用；保存一次设备表后会自动补上"
            )
        elif status:
            lines.append(status)
        return "\n".join(line for line in lines if line)

    def _fill_cards(self, devices: Sequence[Any]) -> None:
        self._clear_cards()
        rows: list[dict[str, Any]] = []
        for device in devices:
            visitor_index = 0
            if not device.has_identity or device.is_unidentified:
                visitor_index = self._visitor_index(device)
            heading = card_heading(device, visitor_index)
            model = card_model(device)
            role = canonical_role(getattr(device, "known_role", None))
            picture_role = role or ("power_supply" if device.is_power_supply else "")
            code, code_detail = device_code(device, visitor_index)
            card = InstrumentCard(
                # 卡片第一行只写仪器名（型号）；读不出型号时退回卡片标题的兜底文本
                # （``未识别设备 #n``），卡片上仍留有“这是哪台”的说法。
                model or heading,
                code,
                status_text=card_status_text(device),
                tooltip=self._card_tooltip(device, code_detail),
                picture=instrument_picture_path(picture_role, model),
                checkable=device_applicable(device),
                tag=device,
                parent=self.cards_host,
            )
            card.activated.connect(lambda item: self._on_card_activated(item))
            rows.append(
                {
                    "device": device,
                    "card": card,
                    "heading": heading,
                    # None 表示用户还没动过这张卡片：配置预选可以生效；
                    # True/False 是用户自己的选择，预选不能覆盖它。
                    "user_checked": None,
                    "bound": False,
                }
            )
        self._rows = rows
        self._apply_cards_layout()

    def _preselect_bound_devices(self) -> None:
        """按配置里保存的身份预选：IP 变了也能找回同一台设备。

        只按 ``厂商:序列号`` 匹配，不做“仅按序列号”或“按短标签”的猜测。角色以设备表
        登记为准；电源按 ``supply_id`` 逐台比对，旧槽位（``PS3``）只是一段兼容输入，
        折叠后仍是电源类别，不会因为“PS3 → 电源”被误报成角色变化。
        """
        if not self.device_bindings:
            return
        bound = {
            binding_device_key(record): key
            for key, record in binding_records(self.device_bindings)
            if binding_device_key(record) is not None
        }
        if not bound:
            return
        power_keys = set(power_supply_bindings(self.device_bindings))
        matched = 0
        moved: list[str] = []
        for row in self._rows:
            device = row["device"]
            key = device.device_key
            if key is None or key not in bound:
                continue
            if not device_applicable(device):
                # 设备表没登记角色时不能预选：应用后只会写进一个猜出来的角色。
                continue
            row["bound"] = True
            matched += 1
            bound_key = bound[key]
            # 绑定键可能是类别（``power_supply``）或短标签（``DP8B1519``）：类别折叠后
            # 比较，短标签只在双方都是电源时按“是不是这台电源”比对。
            bound_role = canonical_role(bound_key) or bound_key
            same_category = bound_role == device.role
            same_supply = device.is_power_supply and bound_key in power_keys
            if not (same_category or same_supply):
                moved.append(
                    f"{device.display_name} 配置里是 {role_label(bound_role) or bound_role}，"
                    f"设备表登记为 {role_label(device.role)}"
                )
        if matched:
            text = f"{self.status_label.text()}；已按配置中的设备身份预选 {matched} 台"
            if moved:
                text += "；角色以设备表为准（" + "；".join(moved) + "）"
            self.status_label.setText(text)
        self._sync_card_checks()

    def _sync_card_checks(self) -> None:
        """按“用户点击”与“配置预选”刷新卡片选中状态。

        用户自己的选择优先：点过（选上或取消）的卡片不再被配置预选改写；没动过的卡片
        才按身份预选，并在卡片上写明“已按配置预选”。
        """
        for row in self._rows:
            card = row["card"]
            user_checked = row["user_checked"]
            checked = bool(row["bound"]) if user_checked is None else bool(user_checked)
            card.set_checked(checked, emit=False)
            preselected = bool(row["bound"]) and user_checked is None
            card.set_status("已按配置预选" if preselected else "")
        # 只要有一台可以应用的设备，“应用”就可用：用户先勾选再应用，但不必先勾选才看得见按钮。
        self.apply_btn.setEnabled(
            any(device_applicable(row["device"]) for row in self._rows)
        )

    def _on_card_activated(self, card: InstrumentCard) -> None:
        """用户点击了某张卡片：记录这次选择，并同步按钮的可用状态。"""
        for row in self._rows:
            if row["card"] is card:
                row["user_checked"] = card.is_checked()
                break
        self._sync_card_checks()

    def _apply_cards_layout(self) -> None:
        """把卡片按面板宽度排成网格。

        卡片区的高度由结果框分配（结果少时多余空间留在框里，结果多时框内滚动），
        所以这里不再按内容钉高，只负责把卡片摆进网格。
        """
        layout = self.cards_layout
        for card in self.cards():
            layout.removeWidget(card)
        if not self._rows:
            self.cards_host.setMinimumHeight(0)
            self._refresh_results_box_height()
            return
        # 先按面板宽度定列数（窗口刚显示时滚动区视口还没布局，宽度可能是 0）。
        columns = self._column_count_for(self._panel_cards_width())
        for index, card in enumerate(self.cards()):
            row, column = divmod(index, columns)
            layout.addWidget(card, row, column)
        self._refresh_cards_geometry()

    def _panel_cards_width(self) -> int:
        """估算卡片区可用宽度：滚动区视口还没布局时退回面板宽度减去边框与边距。"""
        viewport = self.cards_area.viewport().width()
        if viewport > 0:
            return viewport
        return max(_CARDS_MIN_WIDTH, self.width() - 4 * _CARDS_SPACING)

    def _column_count_for(self, width: int) -> int:
        fits = max(0, int(width)) // _CARDS_MIN_WIDTH
        return max(1, min(int(fits), _MAX_COLUMNS))

    def _refresh_cards_geometry(self) -> None:
        """按当前宽度决定列数，并把卡片按新列数就位。

        框的高度是固定的：结果少时卡片在框内靠上排、下面留白，结果多时卡片区内部滚动，
        框的大小始终不变。
        """
        if not self._rows:
            return
        columns = self._column_count_for(self._panel_cards_width())
        layout = self.cards_layout
        for index, card in enumerate(self.cards()):
            row, column = divmod(index, columns)
            layout.removeWidget(card)
            layout.addWidget(card, row, column)
        # 卡片区刚从收起变为展开时布局还没激活，卡片位置会全是 (0, 0)；
        # 这里直接激活一次，宽度变化时卡片立即按新列数就位。
        self.cards_layout.activate()
        self.cards_host.updateGeometry()
        self._refresh_results_box_height()

    def _notify_ancestors(self) -> None:
        """把“内容变了”一路上报，并让外层布局当场重排。

        框里的东西变了（卡片区显隐、状态行多一行、卡片换列）要跨好几层布局才生效：只
        调用 ``updateGeometry()`` 时外层布局要等下一轮事件才重排，框内按钮会短暂压在卡片
        上。这里逐层失效并重新激活一遍布局，让变化当场生效。
        """
        self.updateGeometry()
        parent = self.parentWidget()
        while parent is not None:
            layout = parent.layout()
            if layout is not None:
                layout.invalidate()
                layout.activate()
            parent.updateGeometry()
            parent = parent.parentWidget()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_cards_geometry()

    def selected_assignments(self) -> tuple[list[dict[str, Any]], list[str]]:
        """返回 ``(要应用的设备, 问题说明)``；身份/角色缺失、唯一角色重复都算问题。

        电源是同一个类别下的多台设备：勾选几台就返回几条，不再按角色键互相判定
        “重复”；只有信号源与频谱仪保持“一个角色一台设备”。每条记录都带短标签
        （``label`` / ``supply_id``）与身份（``serial`` / ``device_key``），配置页不需要
        从显示文本反解析。
        """
        assignments: list[dict[str, Any]] = []
        problems: list[str] = []
        used_roles: dict[str, str] = {}
        for row in self._rows:
            if not row["card"].is_checked():
                continue
            device = row["device"]
            if not device.has_identity:
                problems.append(
                    f"{device.ip}:{device.port} 无法识别身份，不能应用；请检查设备响应后重试"
                )
                continue
            role_value = device.role
            if not role_value:
                problems.append(
                    f"{device.display_name} 在设备表中没有登记测试角色，不能应用"
                )
                continue
            if role_value != POWER_SUPPLY_ROLE:
                # 信号源与频谱仪是“一个角色一台设备”：同一角色被勾选两次就整批拒绝。
                # 电源不参与这里——多台电源各自独立传递，不按角色键互相覆盖。
                if role_value in used_roles:
                    problems.append(
                        f"角色 {role_label(role_value)} 被重复选择："
                        f"{used_roles[role_value]} 与 {device.display_name}"
                    )
                    continue
                used_roles[role_value] = device.display_name
            elif not device.supply_id:
                # 电源引用是短标签（= supply_id）：没有标签就没有可写的配置引用。
                problems.append(
                    f"{device.display_name} 在设备表里还没有短标签（supply_id），"
                    "没有可写的电源引用；请先保存一次设备表补上标签后重新扫描"
                )
                continue
            identity = device.identity
            assignments.append(
                {
                    "role": role_value,
                    "label": device.label,
                    "supply_id": device.supply_id,
                    "address": device.visa_address,
                    "name": device.known_name,
                    "vendor": identity.vendor,
                    "model": identity.model,
                    "serial": identity.serial,
                    "device_key": identity.device_key,
                    "ip": device.ip,
                    "port": device.port,
                    "known": device.is_known,
                    "simulated": self.last_mode == SCAN_MODE_SIMULATED,
                }
            )
        return assignments, problems

    def request_apply(self) -> None:
        assignments, problems = self.selected_assignments()
        if problems:
            self._set_status("；".join(problems))
            return
        if not assignments:
            self._set_status("请先勾选本次测试所需的设备（测试角色来自设备表登记，不需要选择）")
            return
        self.apply_requested.emit(assignments)

    def request_clear(self) -> None:
        if not self.device_bindings:
            self._set_status("配置中没有设备绑定记录")
            return
        self.clear_requested.emit()

    def show_message(self, message: str) -> None:
        """显示一次应用/清除操作的结果（由配置页回写，避免面板自己猜结果）。"""
        if not self._closed:
            self._set_status(message)


__all__ = [
    "BOX_STATES",
    "BOX_STATE_BUSY",
    "BOX_STATE_IDLE",
    "BOX_STATE_RESULTS",
    "DeviceDiscoveryPanel",
    "DiscoveryWorker",
    "LOADING_TEXT",
    "ROLE_LABELS",
    "SCAN_MODE_CHOICES",
    "binding_device_key",
    "card_heading",
    "card_model",
    "card_status_text",
    "device_applicable",
    "device_heading",
    "role_label",
    "subnet_from_address",
]

