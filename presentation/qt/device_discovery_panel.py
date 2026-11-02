"""设备发现面板：扫描网段、以卡片展示发现结果并选择本次测试所需设备。

边界：

- 面板只负责界面与扫描线程生命周期；扫描实现由 ``scanner_factory(mode)`` 注入，
  因此离线测试与现场扫描共用同一套界面逻辑。
- 面板不改配置、不连接仪器：用户勾选本次测试所需设备，点页面上的“下一步”时由
  连接页把勾选结果交给配置页，写成仪器连接配置（地址来自本次发现的设备）与设备身份记录。
- **测试角色由设备表登记，界面不让用户选**：现场每台仪器只承担一种角色。卡片正面
  第一行只写加粗放大的缩写设备码（电源就是它的缩写设备码 ``DP8B1519``；设备表还没保存过
  缩写码时退回完整设备码），型号下面一行写“型号·角色”（如 ``SMW200A·信号发生器``），
  最下面一行用灰色小字写本次扫描到的 IP；电源是同一个类别下的多台设备，靠缩写设备码
  区分，不再用“电源 PS3”这类槽位编号。完整身份与连接细节写在悬浮提示里。没登记角色的
  设备不能选中应用。
- **多台电源各自独立**：电源不再按角色键互相覆盖，勾选几台就传递几台；只有信号源与
  频谱仪保持“一个角色一台设备”的唯一性。
- 发现结果**包含未知设备**：未在设备表中登记的完整设备码同样显示一张卡片并标记为未登记；
  身份完全读不出来的地址也留一张卡片，但标记为“无法识别”，不允许被应用成测试角色。
- 结果以卡片呈现（:mod:`presentation.qt.device_cards`）：点击卡片切换选中，卡片区
  按面板宽度自动换行、整组始终居中（不满一行时最后一行也居中），不再是一张横向表格。

版式：扫描行（扫描网段 / 扫描方式）在标题正下方居中，结果区在它下面。结果区**不画外框**
（没有边框、没有底色），所以仪器卡片直接落在页面背景上；还没点过“查找设备”时，那张
卡片按钮就落在结果区顶部、紧跟在扫描行下面（不在整块结果区里居中，免得和扫描行之间隔
一大段空白），点下去之后原地换成等待动画（:class:`presentation.qt.scan_card.ScanWaitStack`：
5 张卡从堆顶一张接一张各转一整圈，叠回去停 0.5s 再来一轮），结果回来后再换成卡片。
等待动画的舞台比卡片大一圈（卡片转到任意角度都要落得下），但牌堆的上沿与左沿正好压在
按钮原来的位置：点下去卡片是在原地起转，不会先往下掉一截（舞台向上多出来的那一截伸进
卡片上方的留白，占位控件只撑高、不撑宽，页面也不会被撑开）。
**扫描结束不等于立刻换画面**：结果（或失败原因）先挂起，等等待动画至少完整播完一轮、
5 张卡都叠回原样（``ScanWaitStack.cycle_finished``）才进入下一步；动画没播完就继续播，
扫描结果等它，而不是把转动中途的牌堆直接收走。搜索一直不回来时动画就一轮接一轮地播。
**结果区的高度是固定的**（两行卡片 + 按钮与状态行）：查找前后、结果多少都不改变它的
大小，卡片超出时只在结果区里滚动。卡片区本身只占实际行数（最多两行）那么高，
统计/失败说明紧跟在卡片下面并横向居中，动作行再排在它下面（“返回”在左，页面级动作
如“连接仪器”在右，由页面通过 :meth:`DeviceDiscoveryPanel.add_action_widget` 加进同一行）；
固定高度多出来的那一截留在动作行下方，不会把状态行顶到离卡片很远的地方。

结果区**不是抽屉**：结果回来就留在那里，没有“收起 / 查看结果”的来回切换。要重新查找时
按“返回”（:meth:`DeviceDiscoveryPanel.request_back`）：清空本次结果与勾选，结果区顶部
重新出现“查找设备”，也就是回到上一页那一步。

“查找设备”不是普通按钮而是一张自绘的深色卡片（:mod:`presentation.qt.scan_card`）：卡片
正中是渐变标题，内部有光带与粒子自己在动；鼠标进来时卡面上的字淡到看不见（0.6s，见那个
模块的说明）。卡片外面只有一层投影，没有霓虹柔光。那张按钮的大小由仪器卡片决定
（:data:`presentation.qt.device_cards.CARD_WIDTH` × :data:`CARD_HEIGHT`），因此与结果区里的
仪器卡片一样大；版面里的占位、可点区域与悬停范围都只有卡片。它只负责外观与动效，点击
行为仍由本面板的 :meth:`DeviceDiscoveryPanel.request_scan` 接手。
"""

from __future__ import annotations

import ipaddress
import re
import threading
from typing import Any, Callable, Sequence

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
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
    CARD_HEIGHT,
    CARD_WIDTH,
    _MAX_COLUMNS,
    InstrumentCard,
    device_code,
    instrument_picture_path,
)
from .input_controls import WheelSafeComboBox
from .scan_card import (
    SCAN_CARD_HEADROOM,
    WAIT_STACK_SIZE,
    ScanPromptCard,
    ScanWaitStack,
)
from .theme import set_button_variant, set_tone

# 角色标签是“本次测试语义”，与设备身份（完整设备码）分开：设备可以换 IP，角色不会变。
# 角色值来自设备表 ``instruments.json`` 的 ``role`` 字段，这里只负责显示。电源的类别是
# ``power_supply``（多台共用，按缩写设备码区分），不再有 ``PS1``..``PS4`` 这类槽位编号。
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
_CARDS_MIN_WIDTH = CARD_WIDTH + _CARDS_SPACING
#: 结果区的高度固定：按“两行卡片 + 按钮与状态行”算出来（
#: :meth:`DeviceDiscoveryPanel._results_box_height`）。高度不随状态变化（未查找 /
#: 正在查找 / 有结果都一样大），所以点一次“查找设备”不会让页面跳动；卡片的行数超过
#: 这个高度时只在结果区里滚动。两行正好放下场地上的 5 台仪器（最多 4 列）。
_RESULTS_BOX_ROWS = 2
#: 结果区的三种状态：还没查找（顶部按钮）、正在查找（进度）、有结果（卡片）。
BOX_STATE_IDLE = "idle"
BOX_STATE_BUSY = "busy"
BOX_STATE_RESULTS = "results"
BOX_STATES: tuple[str, ...] = (BOX_STATE_IDLE, BOX_STATE_BUSY, BOX_STATE_RESULTS)
#: 扫描期间写在结果区里的话：按钮收走后，这里说明正在读取设备。
LOADING_TEXT = "正在查找设备 …"
_IP_PATTERN = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")


def role_label(role: Any) -> str:
    """返回角色的界面标签；未登记或未知角色返回空串。"""
    return ROLE_LABELS.get(str(role or ""), "")


def device_heading(device: Any) -> str:
    """结果表里的一行设备描述。

    每类设备都写“身份在前、类别在后”，具体格式由 ``domain.instruments.labels`` 提供，
    界面不各自拼括号：

    - 电源：``DP832A（DP8B1519，电源）``——型号在前，缩写设备码在括号里；
    - 信号源 / 频谱仪：``SMW200A（5462，信号发生器）``——同样型号在前，缩写设备码用来区分
      同型号的另一台；没有缩写码时退回型号；
    - 未登记设备：``未知设备（型号）``。

    卡片与下拉框都遵循“型号（缩写设备码）”；服务端匹配始终只用 ``device_key``。
    """
    role = canonical_role(getattr(device, "known_role", None))
    heading = str(device.display_name)
    role_text = role_label(role)
    identity = getattr(device, "identity", None)
    short_label = str(getattr(device, "label", "") or "").strip()
    model = str(getattr(identity, "model", "") or "").strip()
    if short_label and model:
        return instrument_display_label(short_label, model, role_text)
    if short_label:
        return f"{short_label}（{role_text}）" if role_text else short_label
    return f"{heading}（{role_text}）" if role_text else heading


def device_applicable(device: Any) -> bool:
    """这台设备能否被应用：身份可解析，且设备表登记了固定测试角色。"""
    return bool(device.has_identity and canonical_role(device.known_role))


def card_heading(device: Any, visitor_index: int = 0) -> str:
    """卡片上的设备名：统一用“型号（缩写设备码）”；读不出身份时用兜底显示名。

    电源与信号源/频谱仪采用同一种写法：型号在前，括号里的缩写设备码区分同型号的另一台
    （电源的缩写码同时是 ``supply_id``）。没有缩写码时（旧设备表还没保存过）只显示型号；
    “未知设备 (型号)”这类兜底文本原样显示。身份完全读不出来的地址改称 ``未识别设备 #n``
    （``visitor_index`` 由面板分配）：它有卡片、有访客编号，但没有可写的身份。
    """
    if getattr(device, "is_unidentified", False):
        return f"未识别设备 #{max(1, int(visitor_index or 0))}"
    if not getattr(device, "registered", False):
        return str(getattr(device, "display_name", "") or "").strip() or "未命名设备"
    label = str(getattr(device, "label", "") or "").strip()
    model = card_model(device)
    identity = getattr(device, "identity", None)
    full_code = str(getattr(identity, "full_code", "") or "").strip()
    if label and model:
        return instrument_display_label(label, model)
    if model and full_code:
        # 还没有缩写设备码（旧设备表未保存过）：卡片标题用型号 + 完整设备码，
        # 既不凭空造一个短码，也让同型号的两台设备各自有唯一标题。
        return f"{model}（{full_code}）"
    if label:
        return label
    return str(getattr(device, "display_name", "") or "").strip() or "未命名设备"


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


class _CardRowsLayout(QVBoxLayout):
    """卡片区的行容器：每一行是一个自带左右留白的行布局，整组按行居中。

    卡片宽度固定，居中交给每一行自己的两个弹性留白：行由
    :meth:`DeviceDiscoveryPanel._rebuild_card_rows` 按当前列数重新生成，最后一行不满时
    也由那一行居中，不靠给整块网格设对齐（那样不满的最后一行会靠左）。
    """

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self.setContentsMargins(2, 2, 2, 2)
        self.setSpacing(_CARDS_SPACING)


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
    """扫描 + 结果区控件；勾选结果由连接页在“下一步”时通过
    :meth:`selected_assignments` 取走。

    版式分两段：扫描行（扫描网段、扫描方式）居中放在标题下面，不进结果区；“查找设备”
    是结果区顶部的那张卡片按钮，紧跟在扫描行下面。结果区不画外框（无边框、无底色），
    卡片直接落在页面背景上；点下去之后按钮收走，换成扫描进度，结果回来后再换成卡片。
    """

    log_message = Signal(str)

    def __init__(
        self,
        *,
        scanner_factory: Callable[[str], Any],
        default_subnet: str | None = None,
        parent: Any = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("deviceDiscoveryPanel")
        # 面板高度贴着内容：结果区的高度是固定的（见 ``_results_box_height``），面板因此不
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
        # 扫描已有结论、但等待动画还没播完一整轮时先挂在这里：结果必须等牌堆复原才
        # 进入下一步。``("completed", 结果)`` / ``("failed", 说明)`` / ``("cancelled", None)``。
        self._pending_outcome: tuple[str, Any] | None = None
        # 配置里保存的设备身份（角色 -> 完整设备码/device_key），用于扫描后按身份预选。
        self.device_bindings: dict[str, dict[str, Any]] = {}
        self._build_ui(default_subnet or DEMO_SUBNET)

    def set_device_bindings(self, bindings: Any) -> None:
        """登记配置里的角色绑定；下一次扫描结果会按身份预选同一台设备。"""
        self.device_bindings = {
            str(role): dict(record)
            for role, record in (bindings or {}).items()
            if isinstance(record, dict)
        }

    # ---------- 返回 ----------
    #
    # 结果区不是抽屉：有结果就把卡片留在那里；要重新查找就按“返回”，回到扫描行下面的
    # “查找设备”，不在“收起 / 查看结果”之间来回切。

    def request_back(self) -> None:
        """返回上一步：清空本次扫描结果与勾选，结果区顶部重新出现“查找设备”。

        本次的发现结果、卡片勾选与状态行都属于这一次查找，返回后整块回到还没查找过的
        样子（扫描行下面那张“查找设备”卡片），用户改网段或换扫描方式再查一次。
        配置里的设备绑定不归面板管，所以返回不动它们；下一次勾选并点“下一步”时整批
        替换掉。要重新绑定，改完网段再查一次即可。
        """
        if self.is_scanning() or self._pending_outcome is not None:
            self._set_status("扫描正在进行，请等待完成")
            return
        self.clear_results()

    def _refresh_results_box_height(self) -> None:
        """让外层布局当场重排一次。

        结果区的高度是固定的（见 :meth:`_results_box_height`），不随内容变化；这里只负责
        把“里面的东西变了”立刻告诉外层布局，按钮才不会慢一拍压在卡片上。
        """
        layout = self.results_box.layout()
        if layout is not None:
            layout.invalidate()
        self._notify_ancestors()

    def _sync_results_height(self) -> None:
        """结果区里的文字变了（多一行或少一行）同样让外层重排一次。"""
        self._refresh_results_box_height()

    def _set_status(self, text: str) -> None:
        """结果区里的状态行：只有真的有话要说时占位（结果统计、失败原因、应用问题）。

        没有结果时不写“尚未扫描”这类常驻提示，所以没有内容时整行隐藏。
        """
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))
        self._sync_results_height()

    def clear_results(self) -> None:
        """清空上一次扫描结果；切换/重新加载配置或按“返回”时调用。

        清空后回到“还没查找过”的样子：结果区顶部重新出现“查找设备”。
        """
        self.devices = []
        self._clear_cards()
        self._set_status("")
        self._set_box_state(BOX_STATE_IDLE)
        # 还没换进结果区的结论属于下一次查找：清空后不能再被动画结束事件补上。
        self._pending_outcome = None

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
        self.scan_btn = ScanPromptCard("查找设备")
        # 外观不取自样式表：它是一张自绘的深色卡片按钮（悬停动效见
        # ``presentation.qt.scan_card``），按钮的大小就是仪器卡片的大小，尺寸由
        # ``presentation.qt.device_cards`` 给出。
        # 扫描期间的等待动画：点下去按钮收走，同一位置换成这一叠 5 张卡，从堆顶一张接
        # 一张各转一整圈，全部叠回去后停 0.5s 再来一轮（5 张只是动画，与设备台数无关）。
        # 它是面板上的一层覆盖画面，不进扫描行里的格子：舞台比卡片大一圈，居中放进格子
        # 会让整叠卡比按钮低一截，点下去卡片就像往下掉了一下。舞台位置由
        # :meth:`_sync_wait_stack_geometry` 钉在按钮原来的位置上。
        self.wait_stack = ScanWaitStack(self)
        self.wait_stack.setVisible(False)
        # 一整轮动画走完（牌堆复原）才允许把扫描结果换进结果区。
        self.wait_stack.cycle_finished.connect(self._on_wait_cycle_finished)
        # 扫描期间的结果区说明：按钮收走后，这里写明正在读取设备。
        self.loading_label = QLabel(LOADING_TEXT)
        self.loading_label.setAlignment(Qt.AlignHCenter)
        self.loading_label.setProperty("role", "hint")
        self.loading_label.setVisible(False)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        # 结果统计/失败原因紧跟在卡片下方，横向落在结果区正中：这句话通常只有一行，
        # 靠左会孤零零地贴在整块宽度的一角。
        self.status_label.setAlignment(Qt.AlignHCenter)
        self.status_label.setProperty("role", "hint")
        self.status_label.setVisible(False)
        self.mode_hint = QLabel("")
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setAlignment(Qt.AlignHCenter)
        self.mode_hint.setProperty("role", "note")
        # 扫描行留在结果区外面：整行按页面中线居中，跟着“仪表配置”标题走；行里只有
        # 扫描网段与扫描方式两项，动作按钮都落在结果区里（见下面的 centre_area），
        # 所以这一行永远是对称的。
        controls = QVBoxLayout()
        controls.setSpacing(8)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        self.subnet_label = QLabel("扫描网段:")
        self.subnet_label.setProperty("role", "fieldLabel")
        self.mode_label = QLabel("扫描方式:")
        self.mode_label.setProperty("role", "fieldLabel")
        row.addWidget(self.subnet_label)
        row.addWidget(self.subnet_edit)
        row.addSpacing(16)
        row.addWidget(self.mode_label)
        row.addWidget(self.mode_combo)
        row.addStretch(1)
        controls.addLayout(row)
        controls.addWidget(self.mode_hint)
        layout.addLayout(controls)
        self.mode_combo.currentIndexChanged.connect(self._update_mode_hint)

        # 结果区：一台仪器一张卡片，按宽度自动换行。卡片区放在可伸缩的滚动区里，
        # 结果多时只滚动卡片区，不把操作按钮推出可视范围。结果区本身不画外框
        # （见主题里的 ``QFrame#discoveryResultsBox``），卡片直接落在页面背景上。
        self.results_box = QFrame()
        self.results_box.setObjectName("discoveryResultsBox")
        # 高度在 _build_ui 末尾一次钉死（见 _results_box_height）：状态与结果数量都不改变
        # 它的大小，点一次“查找设备”不会让页面变大或变小。
        self.results_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        box = QVBoxLayout(self.results_box)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(8)
        # 结果区里：还没点过“查找设备”时按钮紧跟在扫描行下面；点下去之后同一块换成
        # 扫描进度。
        self.centre_area = QWidget(self.results_box)
        self.centre_area.setObjectName("discoveryCentreArea")
        # 没有卡片时它占满整块结果区，卡片贴着扫描行往下排（不在整块里居中：那样会在
        # 扫描行和卡片之间留下一大段空白），多出来的高度留在卡片下面。
        self.centre_area.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        centre = QVBoxLayout(self.centre_area)
        centre.setContentsMargins(0, 0, 0, 0)
        centre.setSpacing(8)
        # 卡片上方留出投影的位置：卡片背后那层装饰是 card 的同级控件，会被这个容器的上沿
        # 裁掉；这一圈空白的宽度由装饰的伸出版面决定（``scan_card.SCAN_CARD_HEADROOM``）。
        # 紫→蓝底卡与霓虹柔光都已删除，这里只关系投影。
        centre.addSpacing(SCAN_CARD_HEADROOM)
        # 中间这一行自己按可见项居中：没有卡片时只有一张“查找设备”卡片；扫描期间
        # 同一位置换成那一叠等待动画（两者互斥，同一时刻只有一个可见）。等待动画画在
        # 面板上、不进这一行；这里用一个和按钮同宽、和舞台同高的透明占位把行撑到舞台
        # 高度，行里看不见卡片时也保留原来的纵向排布，下面的说明行不会跳上来；宽度
        # 仍与按钮一致，等待时页面也不会被撑宽。
        centre_row = QHBoxLayout()
        centre_row.setSpacing(10)
        centre_row.addStretch(1)
        self.centre_placeholder = QWidget(self.centre_area)
        self.centre_placeholder.setObjectName("discoveryCentrePlaceholder")
        self.centre_placeholder.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.centre_placeholder.setFocusPolicy(Qt.NoFocus)
        self.centre_placeholder.setFixedSize(CARD_WIDTH, WAIT_STACK_SIZE)
        self.centre_placeholder.setVisible(False)
        centre_row.addWidget(self.centre_placeholder)
        centre_row.addWidget(self.scan_btn)
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
        # 卡片区只占卡片实际需要的行数（最多两行）：多于两行时在卡片区里滚动。
        # 高度在 :meth:`_rebuild_card_rows` 里按当前行数设置，状态行因此紧跟在卡片下面，
        # 不会被一块固定两行高、却只放了一行卡片的空白推到结果区最底下。
        self.cards_area.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.cards_host = QWidget()
        self.cards_host.setObjectName("instrumentCardHost")
        # 卡片按行摆放：每一行左右各顶一个等大的弹性留白，所以无论有几张卡片、排成
        # 几列，整组都落在卡片区正中，最后一行不满时也居中（不是靠左补空位）。
        self.cards_layout = _CardRowsLayout(self.cards_host)
        self.cards_area.setWidget(self.cards_host)
        self.cards_area.setVisible(False)
        box.addWidget(self.cards_area)
        # 结果统计 / 失败原因紧跟在卡片下面，横向居中；动作行留在它下面：
        # “返回”在左，页面级动作（如“连接仪器”）由页面加进这一行的右端。
        # 结果区高度固定（按两行卡片算），卡片只有一行时多出来的高度落到最下面，
        # 不再把这句话和按钮顶到结果区底部、离卡片很远。
        box.addWidget(self.status_label)

        self.actions_row = QWidget(self.results_box)
        self.actions_row.setObjectName("discoveryActionsRow")
        actions = QHBoxLayout(self.actions_row)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(10)
        self.back_btn = QPushButton("返回")
        self.back_btn.setToolTip("清空本次查找的结果与勾选，回到扫描行下面的“查找设备”")
        set_button_variant(self.back_btn, "ghost")
        self.back_btn.setVisible(False)
        actions.addWidget(self.back_btn)
        actions.addStretch(1)
        # 页面之后追加的动作按顺序落在右端；高度计算要把它们一并算进去。
        self.action_layout = actions
        self._action_widgets: list[QWidget] = [self.back_btn]
        # 返回只对结果有效，跟在状态行下面。
        box.addWidget(self.actions_row)
        # 固定高度里多出来的那一截留在按钮下面：卡片、状态行与按钮整组贴着结果区上沿，
        # 中间不再出现一大段空白。
        box.addStretch(1)
        layout.addWidget(self.results_box)

        self.scan_btn.clicked.connect(self.request_scan)
        self.back_btn.clicked.connect(self.request_back)
        self._update_mode_hint()
        self._refresh_cards_geometry()
        # 它的高度在这里一次钉死：此后无论查找前后还是结果多少，都是同一个大小。
        self.results_box.setFixedHeight(self._results_box_height())
        self._set_box_state(BOX_STATE_IDLE)

    # ---------- 结果区状态 ----------

    def _results_box_height(self, rows: int = _RESULTS_BOX_ROWS) -> int:
        """结果区的固定高度：``rows`` 行卡片 + 里面的动作行与状态行。

        高度只由卡片尺寸与内部控件算出来，跟状态（未查找 / 正在查找 / 有结果）和结果
        数量都无关：点一次“查找设备”不会让它变大或变小，页面也就不会跟着跳一下。
        设备多到超过这个高度时，卡片区在里面滚动。动作行按其中最高的按钮计算，所以
        页面把“连接仪器”加进同一行时不会把它挤出结果区。
        """
        layout = self.results_box.layout()
        if layout is None:
            return 0
        margins = layout.contentsMargins()
        action_row_height = max(
            (widget.sizeHint().height() for widget in self._action_widgets),
            default=0,
        )
        chrome = (
            margins.top()
            + margins.bottom()
            + action_row_height
            + self.status_label.sizeHint().height()
            + 2 * layout.spacing()
        )
        return chrome + rows * CARD_HEIGHT + (rows - 1) * _CARDS_SPACING

    def add_action_widget(self, widget: QWidget) -> None:
        """把一个页面级动作放到“返回”同一行的右端，并重算结果区固定高度。

        面板只提供版面：动作由页面创建与接线（例如连接页的“连接仪器”），面板不认识
        它的语义，也不替它发信号。
        """
        self.action_layout.addWidget(widget)
        self._action_widgets.append(widget)
        self.results_box.setFixedHeight(self._results_box_height())

    def box_state(self) -> str:
        """结果区当前状态：``idle``（还没查找）/ ``busy``（正在查找）/ ``results``（有结果）。"""
        return str(self.results_box.property("boxState") or BOX_STATE_IDLE)

    def _set_box_state(self, state: str) -> None:
        """切换结果区状态：顶部的按钮/进度、卡片区与操作按钮随状态收放。

        结果区不画外框（无边框、无底色），所以状态切换只影响它里面的内容，不改变外观
        底色：卡片直接落在页面背景上。
        """
        if state not in BOX_STATES:
            raise ValueError(f"未知的结果区状态: {state}")
        self.results_box.setProperty("boxState", state)
        self._sync_box_content()
        style = self.results_box.style()
        if style is not None:
            style.unpolish(self.results_box)
            style.polish(self.results_box)
        self.results_box.update()
        self._refresh_results_box_height()
        self._notify_ancestors()
        self._sync_wait_stack_geometry()

    def _sync_wait_stack_geometry(self) -> None:
        """把等待动画的牌堆钉在“查找设备”卡片原来的位置。

        舞台（:data:`WAIT_STACK_SIZE`）比卡片大一圈，卡片转到任意角度才落得下。如果让
        舞台按扫描行居中，整叠卡的圆心会比按钮低半个差值，点下去卡片就像往下掉了一截。
        这里以按钮格子（扫描期间是同大的透明占位）的上沿为牌堆上沿，把舞台多出来的
        那一截伸到卡片上方的留白里：牌堆与按钮重合，旋转只绕自己的中心。
        """
        placeholder = self.centre_placeholder
        if not placeholder.isVisible():
            return
        owner = placeholder.parentWidget()
        owner_layout = owner.layout() if owner is not None else None
        if owner_layout is not None:
            owner_layout.activate()
        stage = self.wait_stack.size()
        origin = placeholder.mapTo(self, placeholder.rect().topLeft())
        deck_inset = max(0, (stage.height() - CARD_HEIGHT) // 2)
        self.wait_stack.setGeometry(
            origin.x() + (placeholder.width() - stage.width()) // 2,
            origin.y() - deck_inset,
            stage.width(),
            stage.height(),
        )
        # 覆盖层要压在结果区与扫描行之上；它不吃鼠标，层级只影响这一块的画面。
        self.wait_stack.raise_()

    def _sync_box_content(self) -> None:
        """结果区里只放当前状态该有的东西：查找 / 正在查找 / 卡片与操作按钮。

        还没查找时结果区顶部是“查找设备”，扫描期间是进度说明，结果回来才换成一格卡片；
        卡片看得见时这一块整块让位——任何时刻都只有明确的那几样东西，扫描行始终
        只有网段与扫描方式两项、可以稳稳居中。
        """
        state = self.box_state()
        idle = state == BOX_STATE_IDLE
        busy = state == BOX_STATE_BUSY
        results = state == BOX_STATE_RESULTS
        # 先收起焦点，再隐藏控件：否则 Qt 会把焦点交给页面里下一个可聚焦控件，并把
        # 外层滚动区滚到它那里，扫描行就跟着离开了点之前的位置。
        self._release_focus_before_hiding(
            [
                widget
                for widget, visible in (
                    (self.centre_area, idle or busy),
                    (self.scan_btn, idle),
                    (self.wait_stack, busy),
                    (self.loading_label, busy),
                    (self.cards_area, results),
                    (self.back_btn, results),
                )
                if not visible
            ]
        )
        self.centre_area.setVisible(idle or busy)
        self.centre_placeholder.setVisible(busy)
        self.scan_btn.setVisible(idle)
        self.wait_stack.setVisible(busy)
        self.loading_label.setVisible(busy)
        self.cards_area.setVisible(results)
        # 返回只对结果有效：没有卡片时不显示用不上的按钮。
        self.back_btn.setVisible(results)

    def _release_focus_before_hiding(self, widgets: Sequence[QWidget]) -> None:
        """把焦点从即将收走的控件上收回面板，隐藏它们时页面就不滚动。

        Qt 在焦点控件被隐藏时会把焦点交给下一个可聚焦控件，外层滚动区（仪表配置页
        的滚动区）再滚过去让人看见它：点一次“查找设备”，卡片一收走，焦点就落到
        结果区动作行里的“连接仪器”，整页向上滚一截，扫描行（扫描网段 / 扫描方式）也就
        不在点之前的位置了。面板自己就在原处，先把焦点交给它，滚动区不必移动，
        扫描行因此始终留在原处。键盘用户之后照常按 Tab 继续走。
        """
        focused = QApplication.focusWidget()
        if focused is None:
            return
        for widget in widgets:
            if widget is focused or widget.isAncestorOf(focused):
                self.setFocus(Qt.OtherFocusReason)
                return

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

        点下去就是“开始加载设备”：结果区顶部的按钮收走，原地换成那一叠卡的等待动画
        （5 张卡从堆顶一张接一张各转一整圈，叠回去停 0.5s 再来一轮）；卡片区等结果
        回来再出现，扫描期间不出现用不上的操作按钮。扫描结束后结果先挂起，等一下动画
        至少完整走完一轮（牌堆复原）才换进结果区。
        """
        if self.is_scanning() or self._pending_outcome is not None:
            self._set_status("扫描正在进行，请等待完成")
            return
        subnet = self.subnet_edit.text().strip()
        self._pending_outcome = None
        self.devices = []
        self._clear_cards()
        self._set_controls_enabled(False)
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
        if not enabled:
            # 禁用焦点控件和隐藏它一样会让焦点跳到下一个控件、把页面滚走：先收回焦点。
            self._release_focus_before_hiding(
                [self.scan_btn, self.subnet_edit, self.mode_combo]
            )
        self.scan_btn.setEnabled(enabled)
        self.subnet_edit.setEnabled(enabled)
        self.mode_combo.setEnabled(enabled)

    def _on_progress(self, done: int, total: int) -> None:
        if self._closed:
            return
        self.status_label.setText(f"正在扫描 … {done}/{total}")
        self.status_label.setVisible(True)

    def _on_completed(self, result: Any) -> None:
        """扫描成功：先挂起结果，等一轮等待动画播完（牌堆复原）再换进结果区。"""
        if self._closed:
            return
        self._pending_outcome = ("completed", result)

    def _on_wait_cycle_finished(self) -> None:
        """一整轮等待动画走完（牌堆复原）：这时才允许进入下一步。

        扫描一结束就把结论挂在这里；动画至少完整播完一轮、5 张卡都叠回原样才切换
        画面，转动的牌堆不会在中途被收走。结果还没回来就什么也不做，动画继续下一轮。
        """
        if self._closed:
            return
        pending = self._pending_outcome
        if pending is None:
            return
        self._pending_outcome = None
        kind, payload = pending
        if kind == "completed":
            self._show_results(payload)
        elif kind == "failed":
            self._show_failure(payload)
        else:
            self._show_cancelled()

    def _show_results(self, result: Any) -> None:
        """把一次成功的扫描结果放进结果区，并写统计与预选说明。"""
        devices = list(result.devices)
        self.devices = devices
        self.last_mode = getattr(result, "mode", self.current_mode())
        # 有设备就把卡片放进结果区；一台都没读到就回到顶部的“查找设备”，用户可以改
        # 网段再试。
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
        # 预选说明会追加在扫描统计后面，因此先写统计再预选。
        self._preselect_bound_devices()
        self._sync_results_height()
        self.log_message.emit(f"设备扫描完成: {self.status_label.text()}")

    def _on_failed(self, message: str) -> None:
        """扫描失败：同样等这一轮动画播完再收走牌堆。"""
        if self._closed:
            return
        self._pending_outcome = ("failed", message)

    def _show_failure(self, message: str) -> None:
        """失败后没有结果：结果区顶部重新出现“查找设备”，用户改网段或换扫描方式再试。"""
        self._set_box_state(BOX_STATE_IDLE)
        self._set_status(f"扫描失败: {message}")
        self.log_message.emit(f"设备扫描失败: {message}")

    def _on_cancelled(self) -> None:
        """扫描取消：等这一轮动画播完再回到“查找设备”。"""
        if self._closed:
            return
        self._pending_outcome = ("cancelled", None)

    def _show_cancelled(self) -> None:
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
        self._pending_outcome = None
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
        # 卡片被删掉时同样先收回焦点：焦点控件消失也会让滚动区去找下一个控件。
        self._release_focus_before_hiding([self.cards_area])
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
        型号（缩写设备码）、完整设备码与设备键按已知字段拼好，卡片提示不另写一套。其它仪器
        仍按“型号 / 厂商”给身份，未改登记方式的旧数据不会因为这条规则变样。地址作为连接信息
        单独一行，不从身份码反解析。
        """
        identity = device.identity
        lines: list[str] = [device_heading(device)]
        if identity is not None and device.is_power_supply:
            lines.append(
                supply_identity_text(
                    device.label, identity.model, identity.full_code, identity.device_key
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
                "设备表里还没有这台电源的缩写设备码（label），当前显示的是型号，"
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
                # 型号下面的一行只写“型号·角色”；读不出型号时留空——第一行已经写着
                # 缩写码，“这是哪一台”不会因为型号缺失而说不清，也不再重复兜底标题。
                model,
                code,
                role_text=role_label(role),
                # 最下面一行的灰色小字只写本次扫描到的 IP；地址会漂移，不参与身份。
                address=str(getattr(device, "ip", "") or ""),
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

        只按 ``厂商:完整设备码`` 匹配，不做“仅按完整设备码”或“按缩写设备码”的猜测。角色以
        设备表登记为准；电源按 ``supply_id`` 逐台比对，旧槽位（``PS3``）只是一段兼容输入，
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

    def _on_card_activated(self, card: InstrumentCard) -> None:
        """用户点击了某张卡片：记录这次选择。"""
        for row in self._rows:
            if row["card"] is card:
                row["user_checked"] = card.is_checked()
                break
        self._sync_card_checks()

    def _apply_cards_layout(self) -> None:
        """把卡片按面板宽度排成居中的行。

        卡片区的高度由结果区分配（结果少时多余空间留在里面，结果多时在里面滚动），
        所以这里不再按内容钉高，只负责把卡片摆进新的行。
        """
        self._rebuild_card_rows()

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
        """按当前宽度决定列数，并把卡片按新列数就位（整组始终居中）。

        结果区的高度是固定的（按两行卡片算），但卡片区本身只有实际行数那么高：状态行
        因此紧跟在最后一行卡片下面。卡片多到超过两行时，超出部分在卡片区里滚动。
        """
        if not self._rows:
            return
        self._rebuild_card_rows()

    def _cards_area_height(self, rows: int) -> int:
        """``rows`` 行卡片在卡片区里需要的高度（最多按结果区的两行算）。"""
        rows = max(1, min(int(rows), _RESULTS_BOX_ROWS))
        margins = self.cards_layout.contentsMargins()
        return (
            margins.top()
            + margins.bottom()
            + rows * CARD_HEIGHT
            + (rows - 1) * _CARDS_SPACING
        )

    def _rebuild_card_rows(self) -> None:
        """按当前列数重排卡片：一行一个行布局，卡片两侧的留白等大。

        居中不靠“给整块网格设对齐”：卡片固定宽度，行布局自己带着左右两个弹性留白，
        所以卡片只有一张时那一张在正中，五张排成 4 + 1 时最后一行的那张也在正中；
        宽度变化只是换列数，居中规则不变。

        卡片区的高度跟着实际行数走（最多两行）：这样状态行紧贴在最后一行卡片下面，
        不会隔着一段为第二行预留的空白。
        """
        self._clear_card_rows()
        cards = self.cards()
        if not cards:
            self.cards_host.setMinimumHeight(0)
            self.cards_area.setFixedHeight(0)
            self._refresh_results_box_height()
            return
        columns = self._column_count_for(self._panel_cards_width())
        for start in range(0, len(cards), columns):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(_CARDS_SPACING)
            row.addStretch(1)
            for card in cards[start : start + columns]:
                row.addWidget(card)
            row.addStretch(1)
            self.cards_layout.addLayout(row)
        # 多出来的高度留在所有卡片下面：卡片贴着卡片区顶部排，不在竖直方向拉散。
        self.cards_layout.addStretch(1)
        rows = (len(cards) + columns - 1) // columns
        self.cards_area.setFixedHeight(self._cards_area_height(rows))
        # 卡片区刚从不可见变为可见时布局还没激活，卡片位置会全是 (0, 0)；
        # 这里直接激活一次，宽度变化时卡片立即按新列数就位。
        self.cards_layout.activate()
        self.cards_host.updateGeometry()
        self._refresh_results_box_height()

    def _clear_card_rows(self) -> None:
        """拆掉当前的行布局；卡片本身不动，随后会被重新放进新的行里。"""
        layout = self.cards_layout
        while layout.count():
            item = layout.takeAt(0)
            row = item.layout()
            if row is None:
                continue
            while row.count():
                row_item = row.takeAt(0)
                widget = row_item.widget()
                if widget is not None:
                    widget.setParent(self.cards_host)
            # 行布局已经从容器里取出、里面也清空了：交回 Python 立即销毁，
            # 不用 deleteLater 排队（下一次重排时它已经不占位置）。
            row.setParent(None)

    def _notify_ancestors(self) -> None:
        """把“内容变了”一路上报，并让外层布局当场重排。

        结果区里的东西变了（卡片区显隐、状态行多一行、卡片换列）要跨好几层布局才生效：
        只调用 ``updateGeometry()`` 时外层布局要等下一轮事件才重排，里面的按钮会短暂压在
        卡片上。这里逐层失效并重新激活一遍布局，让变化当场生效。
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
        layout = self.layout()
        if layout is not None:
            layout.activate()
        self._sync_wait_stack_geometry()

    def selected_assignments(self) -> tuple[list[dict[str, Any]], list[str]]:
        """返回 ``(要应用的设备, 问题说明)``；身份/角色缺失、唯一角色重复都算问题。

        电源是同一个类别下的多台设备：勾选几台就返回几条，不再按角色键互相判定
        “重复”；只有信号源与频谱仪保持“一个角色一台设备”。每条记录都带短标签
        （``label`` / ``supply_id``）与身份（``full_code`` / ``device_key``），配置页不需要
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
                # 电源引用是缩写设备码（= supply_id）：没有它就没有可写的配置引用。
                problems.append(
                    f"{device.display_name} 在设备表里还没有缩写设备码（supply_id），"
                    "没有可写的电源引用；请先保存一次设备表补上缩写设备码后重新扫描"
                )
                continue
            identity = device.identity
            assignments.append(
                {
                    "role": role_value,
                    "label": device.label,
                    "supply_id": device.supply_id,
                    "address": device.visa_address,
                    "vendor": identity.vendor,
                    "model": identity.model,
                    "full_code": identity.full_code,
                    "device_key": identity.device_key,
                    "ip": device.ip,
                    "port": device.port,
                    "known": device.is_known,
                    "simulated": self.last_mode == SCAN_MODE_SIMULATED,
                }
            )
        return assignments, problems

    def show_message(self, message: str) -> None:
        """显示一次绑定写入的结果（由配置会话回写，避免面板自己猜结果）。"""
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

