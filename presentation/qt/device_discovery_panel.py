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
（没有边框、没有底色），所以仪器卡片直接落在页面背景上。结果区里分成上下两块：上面是
结果卡片的落点（有结果时卡片从结果区顶部排起），下面是“查找设备”/等待动画的落点——两者
被一段引导高度（:data:`_CENTRE_LEAD`）隔开，按钮整体落在卡片下沿之下，过渡动画不会把
按钮与卡片叠在一起。点“查找设备”后按钮原地换成等待动画
（:class:`presentation.qt.scan_card.ScanWaitStack`：7 张卡叠成一叠，以中间那张为准摊成
一把扇形，停一下、轻轻捻过一遍，再叠回原样；停一下再来一轮），结果回来后再换成上方的
卡片。等待动画的舞台比卡片大一圈（扇形要摊得下），但牌堆的上沿与左沿正好压在按钮原来的
位置：点下去卡片是在原地摊开，不会先往下掉一截（舞台向上多出来的那一截伸进卡片上方的
留白，占位控件只撑高、不撑宽，页面也不会被撑开）。
**扫描结束不等于立刻换画面**：结果（或失败原因）先挂起，等等待动画至少完整播完一轮、
所有卡都叠回原样（``ScanWaitStack.cycle_finished``）才进入下一步；动画没播完就继续播，
扫描结果等它，而不是把摊开中途的牌堆直接收走。搜索一直不回来时动画就一轮接一轮地播。
**结果卡片由发牌动画带出来**（:class:`presentation.qt.card_deal_animation.CardDealAnimation`）：等待
动画收拢后，牌从按钮牌堆原来的位置一张张飞向结果位，背面朝上（背面取
:attr:`presentation.qt.card_assets.CardAsset.back`）；全部落定后停一下，再从左到右逐张
翻开，把仪器卡片露出来。翻开的正面是发牌前由 :mod:`presentation.qt.card_assets` 按卡片
内容渲染好的资产，不截取卡片控件，因此动画不依赖卡片是否已经显示、是否完成重排。拿到扫描
结果时卡片数（一台仪器一张）就已经定了，结果区先按这个数量把版面排定，发牌因此直接落在
卡片最终的位置上，翻牌也翻在同一处。发牌期间真正的卡片用透明度效果藏起来、暂不可点；
动画翻到哪一张就发一次 ``card_revealed``，面板据此恢复哪一张，翻完最后一张才恢复交互并
写出“发现 N 台设备 …”。发牌画面挂在**页面根容器**的覆盖层上
（:mod:`presentation.qt.animation_overlay`，由页面通过 :meth:`DeviceDiscoveryPanel.attach_animation_overlay`
挂进来），不在结果区或卡片滚动区里：起点与每张牌的落点在动画开始时一次性换算到覆盖层
坐标，滚动与页头收放都不会挪动已经开始的动画；页面尺寸或滚动位置变化时页面取消这一次
发牌并恢复卡片（``reason=surface-changed``）。面板单独使用（没有页面）时退回挂在结果区上。
**流程状态是显式的**（计划步骤 8）：页面只在 :meth:`DeviceDiscoveryPanel._set_phase`
推进 ``IDLE → SCANNING → RESULT_PENDING → DEALING → INTERACTIVE``（失败、取消与空结果
回 ``IDLE``，关闭进 ``CLOSED``），可见性、状态行与卡片是否可点都由当前状态一次渲染。
扫描结果不再各自调用 ``setVisible`` 驱动发牌；等待牌堆是扫描期间独立的 loading 动效，
“结果要等牌堆完整收拢一轮”这一产品要求由 :data:`WAIT_FOR_FULL_CYCLE` 明确表达。
**结果区的高度由页面给出的可用高度决定**（宽裕时按两行卡片 + 按钮与状态行；窗口矮时
由页面压到一行）：扫描状态与结果数量本身不改变它的大小，只有页面重新分配可用高度时
才会伸缩，卡片超出时在结果区里滚动。卡片区本身只占实际行数（最多两行）那么高，
统计/失败说明紧跟在卡片下面并横向居中；扫描期间的“正在查找设备 …”与进度直接写在这条
状态行里，不另占一行，所以灰字紧贴按钮下方的舞台。动作行再排在它下面（“返回”在左，
页面级动作如“连接仪器”在右，由页面通过 :meth:`DeviceDiscoveryPanel.add_action_widget`
加进同一行）；固定高度多出来的那一截留在动作行下方，不会把状态行顶到离卡片很远的地方。

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
from dataclasses import replace
from enum import Enum
from typing import Any, Callable, Sequence

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QSpacerItem, QVBoxLayout, QWidget,
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

from .animation_diagnostics import log_event
from .animation_overlay import AnimationOverlay, widget_rect_in
from .card_assets import CardAsset, render_card_asset
from .card_deal_animation import (
    DEAL_FRONT_SOURCE_ASSET,
    DEAL_FRONT_SOURCE_SCREENSHOT,
    CardDealAnimation,
    CardDealItem,
)
from .config_form_bindings import (
    binding_device_key,
    binding_records,
    power_supply_bindings,
)
from .device_cards import (
    CARD_HEIGHT,
    CARD_MIN_WIDTH,
    CARD_WIDTH,
    _MAX_COLUMNS,
    InstrumentCard,
    InstrumentCardData,
    device_code,
    instrument_picture_path,
)
from .input_controls import WheelSafeComboBox
from .scan_card import (
    SCAN_CARD_HEADROOM,
    WAIT_STACK_HEIGHT,
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
#: 排一列至少要的宽度：卡片可以收窄到 :data:`CARD_MIN_WIDTH`，但不能再窄。
_CARDS_MIN_WIDTH = CARD_MIN_WIDTH + _CARDS_SPACING
#: 结果区最多按“两行卡片 + 按钮与状态行”算高（
#: :meth:`DeviceDiscoveryPanel._results_box_height`）。窗口够高时就是这个高度；窗口
#: 较矮时由页面给出可用高度（:meth:`DeviceDiscoveryPanel.set_available_height`），
#: 结果区收起到一行卡片，卡片多出来的行在卡片区内部滚动，页面本身不再多出滚动条。
#: 高度只随窗口大小变化、不随状态变化（未查找 / 正在查找 / 有结果都一样大），所以点
#: 一次“查找设备”不会让页面跳动。最多 6 列，两行可放 12 台；场地的 5 台登记仪器加 1 台
#: 未登记演示设备在默认窗口宽度下排成一行。
_RESULTS_BOX_ROWS = 2
#: “查找设备”按钮 / 等待动画在结果区里的引导高度。结果卡片在页面顶部出现，按钮必须
#: 整体落在卡片下沿之下（两者都是 276px 高），过渡动画才不会把卡片和按钮叠在一起。
#: 窗口矮时按结果区的可用高度截断，宁可少留一点空白，也不把状态行挤出结果区。
_CENTRE_LEAD = 208
#: 结果区的三种状态：还没查找（顶部按钮）、正在查找（进度）、有结果（卡片）。
BOX_STATE_IDLE = "idle"
BOX_STATE_BUSY = "busy"
BOX_STATE_RESULTS = "results"
BOX_STATES: tuple[str, ...] = (BOX_STATE_IDLE, BOX_STATE_BUSY, BOX_STATE_RESULTS)


class DiscoveryPhase(str, Enum):
    """发现页的流程状态（计划步骤 8）：页面自己只在一个地方决定“现在在哪一步”。

    ``boxState`` 只描述结果区长什么样；它不能区分“扫描线程还在跑”和“结果已挂起、
    等待牌堆收拢”，也不能区分“正在发牌”和“翻牌完成可交互”。这里把完整流程显式
    写成枚举，转移只经过 :meth:`DeviceDiscoveryPanel._set_phase`：

    - ``IDLE``：还没查找，或本次结果为空 / 失败 / 已取消；
    - ``SCANNING``：扫描线程正在执行，等待牌堆播放；
    - ``RESULT_PENDING``：扫描已有结论，等这一轮等待动画收拢（显式策略
      :data:`WAIT_FOR_FULL_CYCLE`）；
    - ``DEALING``：发牌/翻牌覆盖层正在播放，真实卡片隐藏且不可点；
    - ``INTERACTIVE``：翻牌完成，真实卡片可交互；
    - ``CLOSED``：页面正在关闭，不再保证控件可用性。
    """

    IDLE = "idle"
    SCANNING = "scanning"
    RESULT_PENDING = "result-pending"
    DEALING = "dealing"
    INTERACTIVE = "interactive"
    CLOSED = "closed"


#: 流程状态 → 结果区外观状态。失败与取消不是持久状态，两者都落回 ``IDLE`` 布局，
#: 只由状态行说明原因；``CLOSED`` 不改外观（页面正在关闭）。
_PHASE_BOX_STATES: dict[DiscoveryPhase, str] = {
    DiscoveryPhase.IDLE: BOX_STATE_IDLE,
    DiscoveryPhase.SCANNING: BOX_STATE_BUSY,
    DiscoveryPhase.RESULT_PENDING: BOX_STATE_BUSY,
    DiscoveryPhase.DEALING: BOX_STATE_RESULTS,
    DiscoveryPhase.INTERACTIVE: BOX_STATE_RESULTS,
}

#: 允许的状态转移（计划步骤 8）：状态机自己拒绝没定义过的跳转，而不是悄悄停在两个
#: 状态之间。``IDLE`` 可以从任何未关闭状态回来（清空结果、空结果、失败、取消），
#: ``CLOSED`` 是终态，之后不再有转移。
_PHASE_TRANSITIONS: dict[DiscoveryPhase, frozenset[DiscoveryPhase]] = {
    DiscoveryPhase.IDLE: frozenset({DiscoveryPhase.SCANNING, DiscoveryPhase.CLOSED}),
    DiscoveryPhase.SCANNING: frozenset(
        {DiscoveryPhase.RESULT_PENDING, DiscoveryPhase.IDLE, DiscoveryPhase.CLOSED}
    ),
    DiscoveryPhase.RESULT_PENDING: frozenset(
        {DiscoveryPhase.DEALING, DiscoveryPhase.IDLE, DiscoveryPhase.CLOSED}
    ),
    DiscoveryPhase.DEALING: frozenset(
        {DiscoveryPhase.INTERACTIVE, DiscoveryPhase.IDLE, DiscoveryPhase.CLOSED}
    ),
    DiscoveryPhase.INTERACTIVE: frozenset(
        {DiscoveryPhase.IDLE, DiscoveryPhase.SCANNING, DiscoveryPhase.CLOSED}
    ),
    DiscoveryPhase.CLOSED: frozenset(),
}

#: 结果消费策略（计划步骤 8）：等待牌堆是扫描期间独立的 loading 动效，不是发牌的一部分。
#: 结果到达时不打断正在摊开 / 收拢的那一轮，牌堆复原（``cycle_finished``）之后才消费；
#: 这是状态机里的显式策略，不散落在多个回调中。改成 ``False`` 时扫描结论一到就直接
#: 进入发牌或空结果，不再等牌堆收拢（产品的“完整一轮”要求因此可以单独关闭）。
WAIT_FOR_FULL_CYCLE = True

#: 扫描期间写在状态行里的话：按钮收走后，这里说明正在读取设备与进度。
LOADING_TEXT = "正在查找设备 …"
#: 排障开关：默认 ``False``，发牌正面由
#: :func:`presentation.qt.card_assets.render_card_asset` 按卡片数据渲染
#: （:attr:`CardAsset.front`）。改成 ``True`` 时正面退回旧的 ``card.grab()`` 截图，
#: 只用于对照排障“资产画面与真实卡片不一致”；不要接到配置或界面上，也不要让它
#: 成为第二条长期实现路径（计划步骤 5：回退只用于排障）。
_USE_SCREENSHOT_FRONTS = False
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
    """在后台线程执行一次扫描；只上报结果与失败，不触碰界面。

    ``token`` 是这一次查找的代次（由面板递增）：结果迟到时面板按它判断“这份结论
    是不是已经作废的扫描发来的”，从而不会把旧结果重新画回页面。
    """

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
        *,
        token: int = 0,
    ) -> None:
        super().__init__(parent)
        self.scanner_factory = scanner_factory
        self.subnet = subnet
        self.mode = mode
        self.token = int(token)
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
        if self.cancel_event.is_set():
            # 取消可能早于线程真正跑起来：只置事件不够，扫描器也要收到“别扫了”。
            # 迟到的结果仍由面板按代次丢弃，这里只是让线程更快收尾。
            scanner.request_cancel()
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
    是结果区里给卡片让出位置后的那张卡片按钮，落在结果卡片落点之下。结果区不画外框
    （无边框、无底色），卡片直接落在页面背景上；点下去之后按钮收走，换成等待动画，
    结果回来后再换成上方的卡片。
    """

    log_message = Signal(str)
    #: 结果区状态变化（``idle`` / ``busy`` / ``results``）；连接页据此隐去/复原页头文字。
    box_state_changed = Signal(str)
    #: 流程状态变化（``DiscoveryPhase`` 的值）；日志与测试按它复现一次查找。
    phase_changed = Signal(str)

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
        #: 流程状态（计划步骤 8）：扫描 / 等待 / 发牌 / 可交互只经过 ``_set_phase``。
        self._phase = DiscoveryPhase.IDLE
        #: 当前这次查找的代次：清空、重扫与关闭都会递增，迟到结果据此作废。
        self._scan_token = 0
        # 配置里保存的设备身份（角色 -> 完整设备码/device_key），用于扫描后按身份预选。
        self.device_bindings: dict[str, dict[str, Any]] = {}
        # 页面给出的结果区可用高度（``None`` 表示没有页面约束：按两行卡片算）。
        # 它只随窗口尺寸变化，不随扫描状态变化。
        self._available_height: int | None = None
        # 页面级动画覆盖层（技术债重构步骤 6）：有页面挂进来时，发牌画面挂在页面根容器
        # 上，而不是结果区里；没有页面时退回挂在结果区上（面板单独使用的情形）。
        self._animation_overlay: AnimationOverlay | None = None
        # “两行卡片”的结果区高度：窗口缩放时的上限，建面板时记一次，不随状态文字变化。
        self._results_height_max = 0
        self._build_ui(default_subnet or DEMO_SUBNET)
        self._watch_animation_surfaces()

    def set_device_bindings(self, bindings: Any) -> None:
        """登记配置里的角色绑定；下一次扫描结果会按身份预选同一台设备。"""
        self.device_bindings = {
            str(role): dict(record)
            for role, record in (bindings or {}).items()
            if isinstance(record, dict)
        }

    # ---------- 动画覆盖层 ----------
    #
    # 发牌画面属于页面，而不是结果区：结果区在滚动区里，坐标会随滚动与页头收放变化。
    # 页面把根容器上的覆盖层挂进来（:class:`presentation.qt.animation_overlay.AnimationOverlay`），
    # 面板只负责在开始时把落点换算到覆盖层坐标并交给动画。

    def attach_animation_overlay(self, overlay: AnimationOverlay) -> None:
        """把发牌画面搬到页面级覆盖层上。

        覆盖层与页面根容器同大、不进布局、不吃鼠标；发牌层成为它的子控件并铺满它。
        动画坐标因此只跟页面走：``QScrollArea`` 的滚动与结果区内部布局都不再参与换算。
        在动画进行中调用会先把这一次发牌收尾（恢复卡片），避免把运行中的画面搬到另一
        个坐标系里。
        """
        if overlay is self._animation_overlay:
            return
        layer = self.deal_layer
        if layer.is_running():
            layer.cancel(reveal=True, reason="overlay-attached")
            # 换坐标系前先把这一次发牌按完成路径收尾：卡片恢复显示，流程回到可交互。
            self._on_deal_reveal_finished()
        self._animation_overlay = overlay
        if layer.parentWidget() is not overlay:
            layer.setParent(overlay)
        # 覆盖层尺寸一变，发牌层先跟上新的宿主矩形；要不要取消这次发牌由页面的
        # “surface-changed”策略决定（面板自己的卡片区变化由下面的看护负责）。
        overlay.resized.connect(self._sync_deal_layer_geometry)
        self._sync_deal_layer_geometry()

    def animation_overlay(self) -> AnimationOverlay | None:
        """当前挂着的页面级覆盖层；面板单独使用时为 ``None``。"""
        return self._animation_overlay

    def _watch_animation_surfaces(self) -> None:
        """盯住会挪动卡片、让已冻结的覆盖层坐标失效的控件。

        页面根容器的尺寸与页面滚动由连接页盯着；卡片区自己的视口尺寸与滚动条位置
        归面板：结果区在页面里也可能单独重排（滚动条出现、页头让出或收回高度），这时
        不取消就会把牌画到旧位置上。
        """
        self.cards_area.viewport().installEventFilter(self)
        for bar in (
            self.cards_area.verticalScrollBar(),
            self.cards_area.horizontalScrollBar(),
        ):
            bar.valueChanged.connect(self._on_animation_surface_changed)

    def _on_animation_surface_changed(self, *_args: Any) -> None:
        """卡片区的几何或滚动变了：落点真的挪了才取消这一次发牌。

        布局落定期间视口常会再收到一两次 resize（滚动条出现/消失、首帧排版），此时
        卡片位置往往没变；直接取消会让发牌动画永远播不出来。因此这里先按当前布局重算
        一次落点与冻结值比较：卡片动了就按 ``surface-changed`` 取消并恢复卡片；只是
        可见区域变了，就刷新裁剪遮罩，落点仍保持冻结。没有在播时什么也不做。
        """
        layer = getattr(self, "deal_layer", None)
        if layer is None or not layer.is_running():
            return
        current_clip = self._deal_clip_rect(layer)
        if self._deal_targets_moved(layer) or self._deal_clip_shrank(layer, current_clip):
            self.cancel_deal_reveal(reveal=True, reason="surface-changed")
            return
        layer.refresh_clip_rect(current_clip)

    def _deal_targets_moved(self, layer: CardDealAnimation) -> bool:
        """当前卡片位置是否已经偏离本次动画冻结的落点（容差半像素）。"""
        frozen = layer.targets()
        cards = self.cards()
        if len(frozen) != len(cards):
            return True
        for index, card in enumerate(cards):
            current = self._deal_target_rect(card, layer)
            target = frozen[index]
            if (
                abs(current.x() - target.x()) > 0.5
                or abs(current.y() - target.y()) > 0.5
                or abs(current.width() - target.width()) > 0.5
                or abs(current.height() - target.height()) > 0.5
            ):
                return True
        return False

    @staticmethod
    def _deal_clip_shrank(layer: CardDealAnimation, current: QRectF | None) -> bool:
        """卡片区可见区域是否变小了：变窄可能触发重排，变矮可能裁到卡片。

        变大只需刷新遮罩；卡片区整个不可见（``current`` 为空）时同样按失效处理。
        """
        frozen = layer.clip_rect()
        if frozen is None:
            return False
        if current is None:
            return True
        return (
            current.width() + 0.5 < frozen.width()
            or current.height() + 0.5 < frozen.height()
        )

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

        结果区高度由窗口给出或按两行卡片算（见 :meth:`_results_box_height` 与
        :meth:`set_available_height`），不随内容变化；这里只负责把“里面的东西变了”
        立刻告诉外层布局，按钮才不会慢一拍压在卡片上。
        """
        layout = self.results_box.layout()
        if layout is not None:
            layout.invalidate()
        self._notify_ancestors()

    def _sync_results_height(self) -> None:
        """结果区里的文字变了（多一行或少一行）：重算高度让卡片区让出位置。

        说明行换行后卡在窗口里的结果区要重新分配“说明 + 一行卡片”的高度，窗口够高时
        结果区仍是两行卡片、大小不变。
        """
        self._apply_results_height()

    def _set_status(self, text: str) -> None:
        """结果区里的状态行：只有真的有话要说时占位（结果统计、失败原因、应用问题）。

        没有结果时不写“尚未扫描”这类常驻提示，所以没有内容时整行隐藏。可见性仍由
        当前流程状态决定：发牌期间即使已经备好统计也先藏着，翻牌完成后再出现。
        页面关闭后不再改控件：这时用户已经看不到这一页，也没有下一次查找。
        """
        if self._phase is DiscoveryPhase.CLOSED:
            return
        self.status_label.setText(text)
        self.status_label.setVisible(self._status_visible(self._phase))
        self._sync_results_height()

    def clear_results(self) -> None:
        """清空上一次扫描结果；切换/重新加载配置或按“返回”时调用。

        清空后回到“还没查找过”的样子：结果区顶部重新出现“查找设备”。流程状态也回到
        ``IDLE``，正在播的发牌先按“卡片即将删除”取消，不会留下透明或不可点的卡片。
        正在跑的扫描同时作废（协作取消 + 代次失效）：它的结论与界面无关了，迟到信号
        只负责释放线程，不能把旧结果补回页面。
        """
        self.devices = []
        # 还没换进结果区的结论属于下一次查找：清空后不能再被动画结束事件补上。
        self._pending_outcome = None
        self._invalidate_scan()
        self._clear_cards(reason="results-cleared")
        self._set_status("")
        self._set_phase(DiscoveryPhase.IDLE, outcome="results-cleared")

    def _invalidate_scan(self) -> None:
        """让当前这次查找作废：代次递增，正在跑的线程协作停止。

        只置取消标记、不等待线程：``worker.finished`` 之后照常释放引用并恢复扫描设置，
        但这一次扫描的进度、结果、失败与取消都不许再改页面。清空结果与页面关闭走这里；
        开始新查找时 ``request_scan`` 同样递增代次，因此“旧扫描把结果画回来”没有别的入口。
        """
        self._scan_token += 1
        worker = self.worker
        if worker is None:
            return
        worker.cancel_event.set()
        worker.requestInterruption()
        scanner = worker.scanner
        if scanner is not None:
            scanner.request_cancel()

    def _is_stale_scan_signal(self) -> bool:
        """这次信号是否来自已经作废的那次扫描。

        直接在测试里调用处理器时没有发送者（``None``），按“当前扫描”处理；真实信号
        一定来自 :class:`DiscoveryWorker`，代次对不上就不再更新页面。
        """
        sender = self.sender()
        return isinstance(sender, DiscoveryWorker) and sender.token != self._scan_token

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
        # 扫描期间的等待动画：点下去按钮收走，同一位置换成这一叠 7 张卡，以中间那张
        # 为准摊成一把扇形、捻过一遍再叠回原样（7 张只是动画，与设备台数无关）。它是
        # 面板上的一层覆盖画面，不进扫描行里的格子：舞台比卡片大一圈，居中放进格子
        # 会让整叠卡比按钮低一截，点下去卡片就像往下掉了一下。舞台位置由
        # :meth:`_sync_wait_stack_geometry` 钉在按钮原来的位置上。
        self.wait_stack = ScanWaitStack(self)
        self.wait_stack.setVisible(False)
        # 一整轮动画走完（牌堆复原）才允许把扫描结果换进结果区。
        self.wait_stack.cycle_finished.connect(self._on_wait_cycle_finished)
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
        # 所以这一行永远是对称的。整块扫描提示收在一个容器里：连接页在开始查找后把
        # 标题与这块一起淡出，结果卡片因此独自占据界面的中间偏上位置。
        self.scan_header = QWidget(self)
        self.scan_header.setObjectName("discoveryScanHeader")
        header = QVBoxLayout(self.scan_header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
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
        header.addLayout(row)
        header.addWidget(self.mode_hint)
        layout.addWidget(self.scan_header)
        self.mode_combo.currentIndexChanged.connect(self._update_mode_hint)

        # 结果区：一台仪器一张卡片，按宽度自动换行。卡片区放在可伸缩的滚动区里，
        # 结果多时只滚动卡片区，不把操作按钮推出可视范围。结果区本身不画外框
        # （见主题里的 ``QFrame#discoveryResultsBox``），卡片直接落在页面背景上。
        self.results_box = QFrame()
        self.results_box.setObjectName("discoveryResultsBox")
        # 高度在 _build_ui 末尾一次算定（见 _results_box_height / set_available_height）：
        # 状态与结果数量都不改变它的大小，点一次“查找设备”不会让页面变大或变小。
        self.results_box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        box = QVBoxLayout(self.results_box)
        box.setContentsMargins(12, 12, 12, 12)
        box.setSpacing(8)
        # 结果区里：按钮被引导高度压在结果卡片落点之下；点下去之后同一块换成
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
        # 再往后是引导高度：把“查找设备”与等待动画压到结果卡片下沿之下（按钮与卡片
        # 同高，二者在结果区里必须错开，过渡动画才不会叠在一起）；窗口矮时会被截断。
        self._centre_lead = QSpacerItem(
            0, _CENTRE_LEAD, QSizePolicy.Fixed, QSizePolicy.Fixed
        )
        centre.addSpacerItem(self._centre_lead)
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
        self.centre_placeholder.setFixedSize(CARD_WIDTH, WAIT_STACK_HEIGHT)
        self.centre_placeholder.setVisible(False)
        centre_row.addWidget(self.centre_placeholder)
        centre_row.addWidget(self.scan_btn)
        centre_row.addStretch(1)
        centre.addLayout(centre_row)
        # “正在查找设备 …”与进度不另占一行：扫描期间直接写进下面的状态行，
        # 灰字因此紧贴按钮下方的舞台，而不是在舞台下面再隔一段。
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

        # 结果卡片不是直接出现：等待动画收拢后，这一层从按钮牌堆往卡片位发牌（背面朝上），
        # 全部发完再逐张翻开。它是页面级覆盖层里的画面，不进布局、不吃鼠标；卡片自己先挂一层
        # 透明度效果藏起来，翻到哪张才把哪张露出来。
        self.deal_layer = CardDealAnimation(self.results_box)
        self.deal_layer.setVisible(False)
        self.deal_layer.card_revealed.connect(self._on_deal_card_revealed)
        self.deal_layer.finished.connect(self._on_deal_reveal_finished)

        self.scan_btn.clicked.connect(self.request_scan)
        self.back_btn.clicked.connect(self.request_back)
        self._update_mode_hint()
        self._refresh_cards_geometry()
        # 高度按“两行卡片”算定；页面可以用 set_available_height 把它收进窗口。
        self._capture_results_height_max()
        self._apply_results_height()
        self._set_phase(DiscoveryPhase.IDLE, outcome="initial")

    # ---------- 结果区状态 ----------

    def set_available_height(self, height: int) -> None:
        """按页面给出的可用高度调整结果区：窗口矮时收起到一行卡片。

        调用方（仪表配置页）在窗口尺寸变化时把“结果区真正能用的高度”交进来。结果区
        不再固定按两行卡片占位：窗口放得下两行时仍是两行，放不下时压到一行，卡片多
        出来的行在卡片区内部滚动。高度只在窗口尺寸变化时才调整，扫描状态与结果数量
        都不影响它，因此点一次“查找设备”页面仍然不会跳动。
        """
        height = max(0, int(height))
        if self._available_height == height:
            return
        self._available_height = height
        self._apply_results_height()

    def _results_chrome_height(self) -> int:
        """结果区里除卡片行以外的高度：内外边距、状态行、动作行与间距。"""
        layout = self.results_box.layout()
        if layout is None:
            return 0
        margins = layout.contentsMargins()
        action_row_height = max(
            (widget.sizeHint().height() for widget in self._action_widgets),
            default=0,
        )
        return (
            margins.top()
            + margins.bottom()
            + action_row_height
            + self.status_label.sizeHint().height()
            + 2 * layout.spacing()
        )

    def _minimum_results_height(self) -> int:
        """结果区的最小高度：一行卡片 + 状态行与动作行（未查找时放得下扫描卡片）。

        状态行高度读的是当前文字：长说明换行时结果区随之留出那一行，不会被裁掉。
        """
        return self._results_chrome_height() + CARD_HEIGHT

    def _results_target_height(self) -> int:
        """当前窗口下结果区应有的高度：不低于一行卡片，不高于两行卡片。

        上限在建面板时记下（:meth:`_capture_results_height_max`），不随扫描状态或
        说明文字变化；下限按当前说明行算，窗口极矮时宁可让页面滚动，也不裁掉说明。
        """
        maximum = self._results_height_max or self._results_box_height()
        if self._available_height is None:
            return maximum
        return max(self._minimum_results_height(), min(maximum, self._available_height))

    def _capture_results_height_max(self) -> None:
        """记下“两行卡片”的结果区高度：此后它只是窗口缩放时的上限。"""
        self._results_height_max = self._results_box_height()

    def _visible_cards_height(self) -> int | None:
        """卡片区在结果区里能拿到的高度；没有窗口约束时返回 ``None``（按行数算）。"""
        if self._available_height is None:
            return None
        return max(0, self.results_box.height() - self._results_chrome_height())

    def _apply_results_height(self) -> None:
        """把结果区的高度设成当前窗口允许的值，并让卡片区跟着重新分配高度。"""
        target = self._results_target_height()
        self.results_box.setFixedHeight(target)
        self._apply_centre_lead(target)
        self._refresh_cards_geometry()
        self._refresh_results_box_height()
        # 结果区一变高/变矮，钉在原按钮位置上的等待动画要跟着重新对一次。
        self._sync_wait_stack_geometry()
        self._sync_deal_layer_geometry()

    def _centre_lead_chrome(self) -> int:
        """引导高度计算用的结果区固定开销：状态行按“只占一行”算。

        ``status_label.sizeHint()`` 在布局前会按 0 宽度折行，长统计文本会虚高；这里
        只取字体一行的高度（未查找/扫描期间状态行始终是“正在查找设备 …”一行），
        按钮在查找前后因此落在同一个位置。
        """
        layout = self.results_box.layout()
        if layout is None:
            return 0
        margins = layout.contentsMargins()
        action_row_height = max(
            (widget.sizeHint().height() for widget in self._action_widgets),
            default=0,
        )
        return (
            margins.top()
            + margins.bottom()
            + action_row_height
            + self.status_label.fontMetrics().height()
            + 2 * layout.spacing()
        )

    def _apply_centre_lead(self, box_height: int) -> None:
        """把“查找设备”/等待动画压到结果卡片下沿之下。

        按钮与结果卡片同高（:data:`CARD_HEIGHT`），结果卡片在页面顶部出现；按钮在
        结果区里必须整体落到卡片下沿之下，过渡动画才不会把两者叠在一起。引导高度取
        :data:`_CENTRE_LEAD`，窗口矮到放不下时按“结果区剩余高度 - 舞台高度 - 投影
        留白”截断：宁可少留一点空白，也不把状态行挤出结果区。
        """
        centre_height = max(0, box_height - self._centre_lead_chrome())
        max_lead = max(
            0, centre_height - SCAN_CARD_HEADROOM - WAIT_STACK_HEIGHT
        )
        self._centre_lead.changeSize(
            0, min(_CENTRE_LEAD, max_lead), QSizePolicy.Fixed, QSizePolicy.Fixed
        )
        centre_layout = self.centre_area.layout()
        if centre_layout is not None:
            centre_layout.invalidate()

    def _results_box_height(self, rows: int = _RESULTS_BOX_ROWS) -> int:
        """结果区按 ``rows`` 行卡片计算的高度。

        高度只由卡片尺寸与内部控件算出来，跟状态（未查找 / 正在查找 / 有结果）和结果
        数量都无关：点一次“查找设备”不会让它变大或变小，页面也就不会跟着跳一下。
        设备多到超过窗口给出的高度时，卡片区在里面滚动。动作行按其中最高的按钮计算，
        所以页面把“连接仪器”加进同一行时不会把它挤出结果区。
        """
        return (
            self._results_chrome_height()
            + rows * CARD_HEIGHT
            + (rows - 1) * _CARDS_SPACING
        )

    def add_action_widget(self, widget: QWidget) -> None:
        """把一个页面级动作放到“返回”同一行的右端，并重算结果区高度。

        面板只提供版面：动作由页面创建与接线（例如连接页的“连接仪器”），面板不认识
        它的语义，也不替它发信号。
        """
        self.action_layout.addWidget(widget)
        self._action_widgets.append(widget)
        # 动作行的高度可能变了：两行卡片的上限跟着重算一次。
        self._capture_results_height_max()
        self._apply_results_height()

    def box_state(self) -> str:
        """结果区当前状态：``idle``（还没查找）/ ``busy``（正在查找）/ ``results``（有结果）。

        它只是外观状态，由 :meth:`phase` 推导；判断流程“现在在哪一步”请用
        :meth:`phase`，不要用控件是否可见或 ``boxState`` 反推。
        """
        return str(self.results_box.property("boxState") or BOX_STATE_IDLE)

    def phase(self) -> DiscoveryPhase:
        """发现页的流程状态；日志与测试按它复现一次查找。"""
        return self._phase

    def is_interactive(self) -> bool:
        """真实卡片是否已经可以交互（翻牌完成）。"""
        return self._phase is DiscoveryPhase.INTERACTIVE

    def _set_phase(self, phase: DiscoveryPhase, *, outcome: str | None = None) -> None:
        """进入下一个流程状态：唯一的转移入口，可见变化都交给 :meth:`_render_phase`。

        扫描完成、等待周期结束、发牌完成与返回不再是各自调用 ``setVisible`` 的独立
        路径：它们只推进状态，页面长什么样由当前状态一次渲染。没定义过的跳转（含
        ``CLOSED`` 之后的一切）直接报错，不会悄悄停在两个状态之间。
        """
        if not isinstance(phase, DiscoveryPhase):
            raise ValueError(f"未知的发现页状态: {phase!r}")
        previous = self._phase
        if phase is not previous and phase not in _PHASE_TRANSITIONS[previous]:
            raise ValueError(
                f"发现页状态不能从 {previous.value} 进入 {phase.value}"
            )
        self._phase = phase
        self._render_phase()
        if previous is phase:
            return
        self._log_phase(previous, phase, outcome)
        self.phase_changed.emit(phase.value)

    def _log_phase(
        self,
        previous: DiscoveryPhase,
        phase: DiscoveryPhase,
        outcome: str | None,
    ) -> None:
        """写一条固定字段的流程状态日志：``animation event=discovery_phase ...``。

        与发牌诊断共用同一份日志，按 ``event=discovery_phase`` 检索一次查找的完整
        路径（空闲 → 扫描 → 等待 → 发牌 → 可交互 / 空结果）。
        """
        pending = self._pending_outcome[0] if self._pending_outcome is not None else None
        log_event(
            "discovery_phase",
            previous=previous.value,
            phase=phase.value,
            box_state=_PHASE_BOX_STATES.get(phase) or self.box_state(),
            pending=pending,
            # 进入 DEALING 时卡片刚拿到还没建好，``devices`` 已经知道要发几张；
            # 两者取大，日志里的卡片数在进入发牌那一刻就是本次结果的真实数量。
            cards=max(len(self._rows), len(self.devices)),
            outcome=outcome,
        )

    def _render_phase(self) -> None:
        """按当前流程状态一次渲染结果区：按钮 / 等待牌堆 / 卡片 / 操作按钮与状态行。

        这里是页面状态唯一的渲染入口。失败与取消都映射回 ``IDLE`` 布局，状态行保留
        说明；``DEALING`` 期间卡片区禁用，翻牌完成进入 ``INTERACTIVE`` 才可交互。
        结果区不画外框（无边框、无底色），状态切换只影响里面的内容，不改变外观底色。
        """
        phase = self._phase
        if phase is DiscoveryPhase.CLOSED:
            # 页面正在关闭：不再保证控件的可见性与可用性，只停止动画（由 shutdown 负责）。
            return
        box_state = _PHASE_BOX_STATES[phase]
        box_changed = self.box_state() != box_state
        self.results_box.setProperty("boxState", box_state)

        idle = phase is DiscoveryPhase.IDLE
        waiting = phase in (DiscoveryPhase.SCANNING, DiscoveryPhase.RESULT_PENDING)
        showing = phase in (DiscoveryPhase.DEALING, DiscoveryPhase.INTERACTIVE)
        # 先收起焦点，再隐藏控件：否则 Qt 会把焦点交给页面里下一个可聚焦控件，并把
        # 外层滚动区滚到它那里，扫描行就跟着离开了点之前的位置。
        self._release_focus_before_hiding(
            [
                widget
                for widget, visible in (
                    (self.centre_area, idle or waiting),
                    (self.scan_btn, idle),
                    (self.wait_stack, waiting),
                    (self.cards_area, showing),
                    (self.back_btn, showing),
                )
                if not visible
            ]
        )
        self.centre_area.setVisible(idle or waiting)
        self.centre_placeholder.setVisible(waiting)
        self.scan_btn.setVisible(idle)
        self.wait_stack.setVisible(waiting)
        self.cards_area.setVisible(showing)
        # 返回只对结果有效：没有卡片时不显示用不上的按钮。
        self.back_btn.setVisible(showing)
        # 真实卡片只在翻牌完成后可交互：发牌期间卡片藏起来，也不接受点击。
        self.cards_area.setEnabled(phase is DiscoveryPhase.INTERACTIVE)
        self.status_label.setVisible(self._status_visible(phase))

        if box_changed:
            style = self.results_box.style()
            if style is not None:
                style.unpolish(self.results_box)
                style.polish(self.results_box)
        self.results_box.update()
        self._refresh_results_box_height()
        self._notify_ancestors()
        self._sync_wait_stack_geometry()
        if box_changed:
            self.box_state_changed.emit(box_state)

    def _status_visible(self, phase: DiscoveryPhase) -> bool:
        """当前状态是否应显示状态行：扫描中始终显示，发牌中隐藏，其余看有没有话要说。"""
        if phase in (DiscoveryPhase.SCANNING, DiscoveryPhase.RESULT_PENDING):
            return True
        if phase is DiscoveryPhase.DEALING:
            return False
        return bool(self.status_label.text())

    def _sync_wait_stack_geometry(self) -> None:
        """把等待动画的牌堆钉在“查找设备”卡片原来的位置。

        舞台（:data:`WAIT_STACK_HEIGHT` / :data:`WAIT_STACK_WIDTH`）比卡片大一圈，扇形才
        摊得下。如果让舞台按扫描行居中，整叠牌的圆心会比按钮低半个差值，点下去卡片就像
        往下掉了一截。这里以按钮格子（扫描期间是同大的透明占位）的上沿为牌堆上沿，把
        舞台多出来的那一截伸到卡片上方的留白里：叠着的牌与按钮重合，摊开也只从原位置
        往外散。
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

        点下去就是“开始加载设备”：卡片按钮收走，原地换成那一叠卡的等待动画
        （7 张卡以中间那张为准摊成一把扇形、捻过一遍再叠回原样）；卡片区等结果回来
        再出现，扫描期间不出现用不上的操作按钮。扫描结束后结果先挂起，等一下动画至少
        完整走完一轮（牌堆复原）才换进结果区。

        只有 ``IDLE`` / ``INTERACTIVE`` 能开始新查找：已经在扫描、结果还没消费或正在
        发牌时，界面路径上都点不到这个按钮，程序调用也按同一张状态表拒绝。
        """
        if self._phase is DiscoveryPhase.CLOSED:
            return
        if self._phase is DiscoveryPhase.DEALING:
            # 卡片与覆盖层都指着这份结果：等它翻完，或先“返回”再查。
            return
        if (
            self._phase is DiscoveryPhase.SCANNING
            or self._phase is DiscoveryPhase.RESULT_PENDING
            or self.is_scanning()
            or self._pending_outcome is not None
        ):
            self._set_status("扫描正在进行，请等待完成")
            return
        subnet = self.subnet_edit.text().strip()
        self._pending_outcome = None
        self.devices = []
        self._clear_cards(reason="new-scan")
        self._set_controls_enabled(False)
        self._set_status(f"{LOADING_TEXT} {subnet or '(空)'}")
        self._scan_token += 1
        worker = DiscoveryWorker(
            self.scanner_factory,
            subnet,
            self.current_mode(),
            self,
            token=self._scan_token,
        )
        worker.progress.connect(self._on_progress)
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_cancelled)
        worker.finished.connect(self._on_worker_finished)
        self.worker = worker
        self._set_phase(DiscoveryPhase.SCANNING, outcome="scan-started")
        try:
            worker.start()
        except (RuntimeError, OSError):
            # 线程启动失败（资源耗尽、线程对象不可用等）：先回到可重试的空闲状态，
            # 不留在“正在查找”的假忙碌里；异常照常向外传播，不静默吞掉。
            self.worker = None
            worker.deleteLater()
            self._set_controls_enabled(True)
            self._set_status("扫描失败: 无法启动扫描线程，请重试")
            self._set_phase(DiscoveryPhase.IDLE, outcome="scan-start-failed")
            raise

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
        if self._closed or self._is_stale_scan_signal():
            return
        self._set_status(f"{LOADING_TEXT} {done}/{total}")

    def _on_completed(self, result: Any) -> None:
        """扫描成功：先把结论挂起，再按显式策略决定什么时候消费它。

        等待牌堆是扫描期间独立的 loading 动效。产品的“至少完整一轮”要求由
        :data:`WAIT_FOR_FULL_CYCLE` 明确表达：为真时进入 ``RESULT_PENDING``，等牌堆
        复原再换画面；为假时扫描结论一到就进入发牌或空结果，不再依赖牌堆周期。
        已经作废的扫描（清空或关闭之后）迟到的结果不再更新页面。
        """
        if self._closed or self._is_stale_scan_signal():
            return
        self._hold_outcome("completed", result)

    def _hold_outcome(self, kind: str, payload: Any) -> None:
        """把扫描结论挂到 ``RESULT_PENDING``，并应用结果消费策略。"""
        self._pending_outcome = (kind, payload)
        self._set_phase(DiscoveryPhase.RESULT_PENDING, outcome=kind)
        if not WAIT_FOR_FULL_CYCLE:
            self._consume_pending_outcome()

    def _on_wait_cycle_finished(self) -> None:
        """一整轮等待动画走完（牌堆复原）：``RESULT_PENDING`` 这时才允许进入下一步。

        扫描一结束就把结论挂在这里；动画至少完整播完一轮、所有卡都叠回原样才切换
        画面，转动的牌堆不会在中途被收走。结果还没回来就什么也不做，动画继续下一轮。
        """
        if self._closed:
            return
        if self._phase is not DiscoveryPhase.RESULT_PENDING:
            return
        self._consume_pending_outcome()

    def _consume_pending_outcome(self) -> None:
        """消费一次挂起结论：成功结果进发牌 / 空结果，失败与取消回 ``IDLE`` 布局。"""
        pending = self._pending_outcome
        if pending is None or self._closed:
            return
        self._pending_outcome = None
        kind, payload = pending
        if kind == "completed":
            self._enter_results(payload)
        elif kind == "failed":
            self._show_failure(payload)
        else:
            self._show_cancelled()

    def _enter_results(self, result: Any) -> None:
        """把一次成功的扫描结果带进页面：先备好卡片与资产，再进入 ``DEALING``。

        流程对应计划步骤 8：

        .. code-block:: text

            扫描完成 → 生成 CardAsset 与目标槽位 → DEALING → 覆盖层播放 → INTERACTIVE

        有设备时卡片先按最终版面就位，但真实控件用透明度效果藏起来（仍占着版面），
        由发牌覆盖层逐张翻开；翻完最后一张才进入 ``INTERACTIVE``。空结果跳过发牌，
        直接回 ``IDLE`` 并显示“发现 0 台设备 …”。真实卡片在 ``DEALING`` 期间不可点。
        """
        # 发牌起点是牌堆现在还留在屏幕上的位置；进入 DEALING 会收走等待牌堆，所以先记。
        deck_anchor = None
        if self.wait_stack.isVisible():
            deck_anchor = self.wait_stack.mapToGlobal(self.wait_stack.rect().center())
        devices = list(result.devices)
        self.devices = devices
        self.last_mode = getattr(result, "mode", self.current_mode())
        if not devices:
            # 空结果不发牌：跳过动画并直接回到查找画面，用户可以改网段再试。
            self._fill_cards(devices)
            self._set_status(self._results_summary(result, devices))
            self.deal_layer.skip(reason="no-cards")
            self._set_phase(DiscoveryPhase.IDLE, outcome="completed-empty")
            self.log_message.emit(f"设备扫描完成: {self.status_label.text()}")
            return
        # 先把结果区切到 DEALING（卡片区可见、状态行隐藏、卡片区禁用），再生成卡片：
        # 卡片因此在可见的卡片区里完成布局，落点冻结之后不会因为刚显示出来而重排。
        self._set_phase(DiscoveryPhase.DEALING, outcome="completed")
        self._fill_cards(devices)
        # 预选说明会追加在扫描统计后面，因此先写统计再预选。
        self.status_label.setText(self._results_summary(result, devices))
        self._preselect_bound_devices()
        self._sync_results_height()
        self.log_message.emit(f"设备扫描完成: {self.status_label.text()}")
        self._start_deal_reveal(deck_anchor)

    @staticmethod
    def _results_summary(result: Any, devices: Sequence[Any]) -> str:
        """结果统计与提示：数量、未登记/无法识别、扫描器给出的失败与提示。"""
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
        if getattr(result, "mode", None) == SCAN_MODE_SIMULATED:
            text += "（模拟数据，仅用于离线演示；不能用于真实测量）"
        return text

    def _start_deal_reveal(self, deck_anchor: Any) -> None:
        """把结果卡片藏起来，交给发牌覆盖层发出去、再逐张翻开。

        卡片数在拿到扫描结果时就定了（一台仪器一张卡片），版面也已经按这个数量排好：
        先把最终版面落定，再按卡片此刻的内容渲染每张卡的动画资产（正面不截控件），
        然后用透明度效果把它们藏起来（仍然占着版面）。随后把每张卡的落点与卡片区裁剪
        矩形一次性换算到覆盖层坐标；发牌因此落在卡片最终所在的位置，翻开也翻在同一处，
        动画期间不再读卡片控件的坐标。
        """
        cards = self.cards()
        if not cards:
            # 没有卡片就没有发牌：由动画层统一记一条 skip，日志里没有 start 时能分清
            # “没启动”，而不是“启动了但没画出来”。状态也回到可交互，不停在 DEALING。
            self.deal_layer.skip(reason="no-cards")
            if self._phase is DiscoveryPhase.DEALING:
                self._set_phase(DiscoveryPhase.INTERACTIVE, outcome="deal-skipped")
            return
        # 卡片先按最终版面就位，再为每张卡渲染动画资产；随后用透明度效果把它们藏起来
        # （仍然占着版面），覆盖层翻到哪张就把哪张恢复显示。
        self._settle_deal_layout()
        front_source = self._deal_front_source()
        assets = self._deal_assets(cards, front_source)
        layer = self.deal_layer
        self._sync_deal_layer_geometry()
        # 落点与裁剪区在这一刻一次性换算到覆盖层坐标：动画期间不再读卡片控件的位置，
        # 滚动或内部布局变化都不会把已经飞出去的牌拽到别处（页面用取消代替追踪）。
        # 先把所有输入算齐再藏卡片：任何校验失败都不会留下“看不见又点不了”的半状态。
        targets = [self._deal_target_rect(card, layer) for card in cards]
        clip = self._deal_clip_rect(layer)
        anchor = (
            layer.mapFromGlobal(deck_anchor)
            if deck_anchor is not None
            else layer.rect().center()
        )
        # 每一项只带资产、落点与身份（卡片在结果区里的位置）：动画不持有卡片控件，
        # 翻到哪一张通过 ``card_revealed`` 交回来，由面板自己恢复对应的真实卡片。
        items = [
            CardDealItem(asset=asset, target_rect=target, index=index)
            for index, (asset, target) in enumerate(zip(assets, targets))
        ]
        for card in cards:
            effect = QGraphicsOpacityEffect(card)
            effect.setOpacity(0.0)
            card.setGraphicsEffect(effect)
        # 发牌期间卡片还不能点：覆盖层不吃鼠标，但下面的卡片已经看不见了。
        self.cards_area.setEnabled(False)
        started = False
        try:
            layer.start(
                items,
                QPointF(anchor),
                clip_rect=clip,
                front_source=front_source,
            )
            started = True
        finally:
            # 输入的份数/取值是内部约定，正常不会失败；但任何启动异常（包括 Qt 在
            # 绘制前的错误）都不能把面板留在“卡片透明、卡片区不可点”的半状态。
            # 用 finally 而不是 except：先按统一路径清理本次发牌与卡片效果，异常随后
            # 照常向外传播，既不被吞掉，也不给宽泛捕获再开一个口子。
            if not started:
                self._abort_deal_reveal()
                # 启动失败时面板已经恢复卡片与卡片区；状态也回到可交互，不留下
                # “卡片看得见却停在发牌中”的半状态。产品决定：结果立即可用，
                # 但故障必须被看见（异常继续传播），不能静默降级。
                self.log_message.emit("发牌动画未启动，结果已直接显示；请查看运行日志")
                if self._phase is DiscoveryPhase.DEALING:
                    self._set_phase(
                        DiscoveryPhase.INTERACTIVE, outcome="deal-start-failed"
                    )

    @staticmethod
    def _deal_target_rect(card: InstrumentCard, layer: QWidget) -> QRectF:
        """这张卡在发牌覆盖层里的落点：动画开始时算一次。

        卡片与覆盖层分属页面根容器的两个分支，换算经屏幕坐标中转（见
        :func:`presentation.qt.animation_overlay.widget_rect_in`），窗口在桌面上的
        位置不会混进落点。
        """
        return widget_rect_in(card, layer)

    def _deal_clip_rect(self, layer: QWidget) -> QRectF | None:
        """卡片区可见视口在覆盖层里的矩形：动画开始时算一次。"""
        viewport = self.cards_area.viewport()
        if not viewport.isVisible():
            return None
        return widget_rect_in(viewport, layer)

    def _deal_front_source(self) -> str:
        """本次发牌正面的来源：默认 ``asset``，排障回退为 ``screenshot``。

        这是唯一的判断点：:meth:`_deal_assets` 按它决定要不要截图，诊断也写同一个值，
        因此日志里的来源与画面实际用的来源不会对不上。
        """
        return (
            DEAL_FRONT_SOURCE_SCREENSHOT
            if _USE_SCREENSHOT_FRONTS
            else DEAL_FRONT_SOURCE_ASSET
        )

    def _deal_assets(
        self, cards: Sequence[InstrumentCard], front_source: str
    ) -> list[CardAsset]:
        """按 ``cards`` 的顺序返回每张卡的动画资产（与发牌顺序一一对应）。

        取的正是调用方随后按同一顺序为每张卡构造的 ``CardDealItem`` 列表（每张读自己的
        ``data`` 快照，预选与状态说明都已经写在上面），不按设备名回查：卡片标题
        并不保证唯一（同型号、同缩写码或未登记设备都可能重名），用 ``heading`` 当关联键
        会把某一台的画面套到另一台上。

        正面由 :func:`presentation.qt.card_assets.render_card_asset` 直接画出，不读控件
        画面，因此发牌不再依赖卡片是否显示过、是否完成一次重排，也不再看
        ``QWidget.grab()``；尺寸取卡片自己的逻辑尺寸，与落点一致。

        ``front_source`` 由 :meth:`_deal_front_source` 定：``screenshot``（排障回退）时
        只把正面换回旧截图，牌背、几何与其余绘制仍走资产，保证两条路径的差别只有
        “正面怎么来”，且诊断写下的来源与实际使用的画面不会不一致。
        """
        ratio = float(self.devicePixelRatioF())
        screenshot = front_source == DEAL_FRONT_SOURCE_SCREENSHOT
        assets: list[CardAsset] = []
        for card in cards:
            asset = render_card_asset(
                card.data,
                width=max(1, int(card.width())),
                height=max(1, int(card.height())),
                device_pixel_ratio=ratio,
            )
            if screenshot:
                asset = replace(asset, front=card.grab())
            assets.append(asset)
        return assets

    def _settle_deal_layout(self) -> None:
        """让结果区的布局在渲染动画资产、开始发牌之前落定。

        卡片才刚挂进卡片区的行布局（:meth:`_rebuild_card_rows` 里已经重新显示，
        隐藏的控件在布局里占 0 高），这里再逐层激活一次：结果区的框、卡片区的行
        布局都按最终列数就位，后面发牌取的落点与翻牌的位置才是同一个。
        """
        box_layout = self.results_box.layout()
        if box_layout is not None:
            box_layout.activate()
        self.cards_layout.activate()

    def _sync_deal_layer_geometry(self) -> None:
        """覆盖层铺满它的宿主：页面级覆盖层（有页面时）或结果区（面板单独使用时）。"""
        layer = getattr(self, "deal_layer", None)
        if layer is None:
            return
        overlay = self._animation_overlay
        if overlay is not None:
            overlay.sync_geometry()
            layer.setGeometry(overlay.rect())
        else:
            layer.setGeometry(self.results_box.rect())
        if layer.isVisible():
            layer.raise_()

    def _on_deal_card_revealed(self, index: int) -> None:
        """动画翻到第 ``index`` 张：撤掉透明度效果，让真实卡片露出来。

        动画不持有卡片控件，翻开哪一张由 :attr:`CardDealAnimation.card_revealed`
        交回来；这里恢复的正是发牌开始前藏起来的那张卡片。索引越界（结果已被换掉或
        清空）时什么也不做，不会去碰别的控件。
        """
        if self._closed:
            return
        cards = self.cards()
        if 0 <= index < len(cards):
            cards[index].setGraphicsEffect(None)

    def _on_deal_reveal_finished(self) -> None:
        """发牌与翻牌结束：真实卡片恢复交互，状态行这时才出现。

        自然完成、``finish()`` 与取消（``reveal=True``）都走这里；它只负责把状态推进到
        ``INTERACTIVE``，可见性与可点状态由 :meth:`_render_phase` 一次渲染。
        """
        if self._closed:
            return
        # 正常路径每张都由 ``card_revealed`` 恢复；这里再兜一次底，保证中断或信号
        # 漏掉时也不会留下“透明但占着位置”的卡片。
        for card in self.cards():
            if card.graphicsEffect() is not None:
                card.setGraphicsEffect(None)
        if self._phase is DiscoveryPhase.DEALING:
            self._set_phase(DiscoveryPhase.INTERACTIVE, outcome="deal-finished")
        else:
            # 迟到的收尾信号（结果已经被清空或换掉）：只重画当前状态，不倒退流程。
            self._render_phase()

    def cancel_deal_reveal(
        self, *, reveal: bool = True, reason: str = "unspecified"
    ) -> bool:
        """取消正在播的发牌/翻牌；没有在播时什么也不做，返回是否取消了一次动画。

        页面在根容器尺寸变化或滚动位置变化时调用它（计划步骤 6：动画中途不追踪复杂
        布局）。``reveal=True`` 把卡片恢复显示，流程进入 ``INTERACTIVE``，结果立刻以
        最终版面出现；``reveal=False`` 交给调用方随后清空或关闭页面。
        """
        layer = getattr(self, "deal_layer", None)
        if layer is None or not layer.is_running():
            return False
        layer.cancel(reveal=reveal, reason=reason)
        if reveal:
            # 取消后同样算“这一段结束了”：恢复卡片交互，有结果时把统计行放出来。
            self._on_deal_reveal_finished()
        return True

    def _cancel_deal_reveal(self, *, reveal: bool, reason: str) -> None:
        """停止发牌覆盖层；``reason`` 写进诊断日志，``reveal=False`` 用于卡片即将被删。

        它只停动画，不决定页面状态：调用方随后会进入 ``IDLE``（清空）或 ``CLOSED``
        （关闭），可见性仍由 :meth:`_render_phase` 一次渲染。
        """
        layer = getattr(self, "deal_layer", None)
        if layer is None:
            return
        layer.cancel(reveal=reveal, reason=reason)

    def _abort_deal_reveal(self) -> None:
        """发牌启动失败后的兜底清理：停掉时间轴，恢复卡片效果与卡片区交互。

        启动失败可能发生在 ``start()`` 已经改过一部分状态之后，因此这里不假设它什么
        都没做：先取消本次发牌（不恢复显示，卡片马上由下面统一撤掉效果），再把每张
        卡片上的透明度效果清掉、恢复卡片区可点。调用方随后继续抛出原异常。
        """
        layer = getattr(self, "deal_layer", None)
        if layer is not None:
            layer.cancel(reveal=False, reason="start-failed")
        for card in self.cards():
            if card.graphicsEffect() is not None:
                card.setGraphicsEffect(None)
        self.cards_area.setEnabled(True)

    def _on_failed(self, message: str) -> None:
        """扫描失败：同样按结果消费策略等这一轮动画播完再收走牌堆。"""
        if self._closed or self._is_stale_scan_signal():
            return
        self._hold_outcome("failed", message)

    def _show_failure(self, message: str) -> None:
        """失败后没有结果：结果区顶部重新出现“查找设备”，用户改网段或换扫描方式再试。"""
        self._set_status(f"扫描失败: {message}")
        self._set_phase(DiscoveryPhase.IDLE, outcome="failed")
        self.log_message.emit(f"设备扫描失败: {message}")

    def _on_cancelled(self) -> None:
        """扫描取消：同样按结果消费策略等这一轮动画播完再回到“查找设备”。"""
        if self._closed or self._is_stale_scan_signal():
            return
        self._hold_outcome("cancelled", None)

    def _show_cancelled(self) -> None:
        self._set_status("扫描已取消，结果未更新")
        self._set_phase(DiscoveryPhase.IDLE, outcome="cancelled")
        self.log_message.emit("设备扫描已取消")

    def _on_worker_finished(self) -> None:
        """线程收尾：释放引用并恢复扫描设置包络；不推进流程状态。

        只认当前那一个 worker：旧线程（已经重扫过）的收尾不能把新的这次扫描的设置
        重新打开或清掉它的引用。
        """
        sender = self.sender()
        if isinstance(sender, DiscoveryWorker) and sender is not self.worker:
            # 旧线程的收尾：不动当前这次扫描的设置与引用，只让它自己回收。
            if not self._closed:
                sender.deleteLater()
            return
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
        self._invalidate_scan()
        self._cancel_deal_reveal(reveal=False, reason="page-shutdown")
        self._set_phase(DiscoveryPhase.CLOSED, outcome="page-shutdown")
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

    def card_data(self, heading: str) -> InstrumentCardData:
        """这张卡片此刻显示的全部内容；找不到时抛出 ``KeyError``。

        这是真实卡片与之后的动画资产共用的那一份快照：发牌动画开始时读它，就不必回头
        读扫描结果 DTO，也不必向卡片控件要文字。快照由卡片自己维护（选中与状态说明
        变化时一起更新，见 :meth:`presentation.qt.device_cards.InstrumentCard.set_checked`
        与 :meth:`~presentation.qt.device_cards.InstrumentCard.set_status`），面板只负责
        按设备名把它交出去。
        """
        return self.card_for(heading).data

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
        return [(row["card"].data.heading, row) for row in self._rows]

    def _clear_cards(self, *, reason: str = "cards-cleared") -> None:
        # 正在发牌/翻牌的覆盖层还指着这些卡片：先停下它（不恢复显示，卡片马上就会删掉），
        # 再清空。``reason`` 只进诊断日志，便于复现“发牌为什么被取消”。
        self._cancel_deal_reveal(reveal=False, reason=reason)
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
        self._clear_cards(reason="cards-refilled")
        rows: list[dict[str, Any]] = []
        for device in devices:
            visitor_index = 0
            if not device.has_identity or device.is_unidentified:
                visitor_index = self._visitor_index(device)
            model = card_model(device)
            role = canonical_role(getattr(device, "known_role", None))
            picture_role = role or ("power_supply" if device.is_power_supply else "")
            code, code_detail = device_code(device, visitor_index)
            # 卡片上要显示的东西在这里一次算定（:class:`InstrumentCardData`）：真实卡片与
            # 之后的动画正面都读这一份，动画因此不必回头读扫描结果或问卡片要文字。
            data = InstrumentCardData(
                heading=card_heading(device, visitor_index),
                # 第一行只写缩写设备码；型号与角色只出现在下面那一行“型号·角色”里。读不出
                # 型号时那一行留空——第一行已经写着缩写码，“这是哪一台”不会因为型号缺失
                # 而说不清，也不再重复兜底标题。
                code=code,
                model=model,
                role=role_label(role),
                # 最下面一行的灰色小字只写本次扫描到的 IP；地址会漂移，不参与身份。
                address=str(getattr(device, "ip", "") or ""),
                status=card_status_text(device),
                tooltip=self._card_tooltip(device, code_detail),
                picture_path=instrument_picture_path(picture_role, model),
                checkable=device_applicable(device),
            )
            card = InstrumentCard(data, tag=device, parent=self.cards_host)
            card.activated.connect(lambda item: self._on_card_activated(item))
            rows.append(
                {
                    "device": device,
                    "card": card,
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

        卡片的选中与状态说明变化时，卡片自己把内容快照一起更新（
        :meth:`presentation.qt.device_cards.InstrumentCard.set_checked` /
        :meth:`~presentation.qt.device_cards.InstrumentCard.set_status`），所以面板不另存
        一份：动画读的 :meth:`card_data` 就是卡片此刻显示的那份内容。
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
        """卡片区可用于排一行的最大宽度（已扣掉行容器的左右留白）。

        视口宽度会因竖向滚动条出现而变窄；列数要先按“不出现滚动条”的整块宽度算，
        否则卡片从多行变回一行时，旧的窄视口会让窗口变宽后仍少排一列。
        """
        viewport = self.cards_area.viewport().width()
        inner = self.cards_area.width() - 2 * self.cards_area.frameWidth()
        width = max(viewport, inner)
        if width > 0:
            margins = self.cards_layout.contentsMargins()
            return max(0, width - margins.left() - margins.right())
        return max(_CARDS_MIN_WIDTH, self.width() - 4 * _CARDS_SPACING)

    def _column_count_for(self, width: int) -> int:
        # 一行 n 张卡只需要 n 张卡宽加 (n-1) 个间距，最后一张后面不再留间距；
        # 先补上一个间距再整除，才不会在“刚好放得下 6 张”的宽度上少算一列。
        fits = max(0, (int(width) + _CARDS_SPACING) // _CARDS_MIN_WIDTH)
        return max(1, min(int(fits), _MAX_COLUMNS))

    def _card_width_for(self, available: int, columns: int) -> int:
        """按当前行宽把卡片分到最宽，但不超过理想宽度 :data:`CARD_WIDTH`。

        宽窗口恢复 190，卡片不会因为固定宽度而缩在中间；窄窗口才等比收窄到
        :data:`CARD_MIN_WIDTH`，保证 6 张仍能排成一行。
        """
        gaps = max(0, columns - 1) * _CARDS_SPACING
        width = (max(0, int(available)) - gaps) // max(1, int(columns))
        return max(CARD_MIN_WIDTH, min(CARD_WIDTH, width))

    def _refresh_cards_geometry(self) -> None:
        """按当前宽度决定列数，并把卡片按新列数就位（整组始终居中）。

        结果区的高度按两行卡片算（窗口矮时由页面压到一行），但卡片区本身只有实际
        行数那么高：状态行因此紧跟在最后一行卡片下面。卡片多到超过结果区的高度时，
        超出部分在卡片区里滚动。
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

        居中不靠“给整块网格设对齐”：行布局自己带着左右两个弹性留白，所以卡片只有一张
        时那一张在正中，五张排成 4 + 1 时最后一行的那张也在正中；宽度变化只是换列数，
        居中规则不变。卡片宽度随可用宽度在 :data:`CARD_MIN_WIDTH` 与 :data:`CARD_WIDTH`
        之间收缩：宽窗口保持理想宽度，窄窗口只收到刚好铺满一行，不会为了超过 6 列而变窄。

        卡片区的高度跟着实际行数走（最多两行，且不超过结果区剩下的高度）：这样状态行
        紧贴在最后一行卡片下面，不会隔着一段为第二行预留的空白；窗口矮到放不下时，
        卡片区内部滚动，页面本身不出现滚动条。
        """
        self._clear_card_rows()
        cards = self.cards()
        if not cards:
            self.cards_host.setMinimumHeight(0)
            self.cards_area.setFixedHeight(0)
            self._refresh_results_box_height()
            return
        available = self._panel_cards_width()
        columns = self._column_count_for(available)
        rows = (len(cards) + columns - 1) // columns
        # 结果区放不下这些行时（超过两行，或窗口矮到只给得出一行），卡片区会出现竖向
        # 滚动条；排卡片要按变窄后的宽度重算一次，否则最右一张会被裁掉一截。
        limit = self._visible_cards_height()
        if limit is not None and self._cards_area_height(rows) > limit:
            scrollbar = self.cards_area.verticalScrollBar().sizeHint().width()
            available = max(_CARDS_MIN_WIDTH, available - scrollbar)
            columns = self._column_count_for(available)
            rows = (len(cards) + columns - 1) // columns
        # 宽窗口把卡片恢复到理想宽度；只有排不下时才收窄到刚好铺满一行。
        card_width = self._card_width_for(available, min(columns, len(cards)))
        for card in cards:
            card.setFixedWidth(card_width)
            # 卡片刚被重新挂到 :attr:`cards_host` 下（见 :meth:`_clear_card_rows`）：
            # ``QWidget.setParent`` 会把它藏起来，而隐藏的控件在布局里占 0 高，卡片的位置
            # 要等下一轮事件才出现。这里当场显示出来，紧接着的布局激活才真的把卡片摆到
            # 最终位置——发牌动画一开始就要按这个位置发牌（见 :meth:`_start_deal_reveal`）。
            card.show()
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
        area_height = self._cards_area_height(rows)
        if limit is not None:
            # 卡片区最多占结果区剩下的高度；再多的行在卡片区内部滚动。
            area_height = min(area_height, max(CARD_HEIGHT, limit))
        self.cards_area.setFixedHeight(area_height)
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

    def eventFilter(self, watched: Any, event: Any) -> bool:
        """卡片区视口尺寸变化：落点或裁剪区可能已经失效。

        覆盖层挂在页面根容器上，卡片区视口的 resize 不会改变覆盖层尺寸，页面收不到
        通知；这里把变化交给 :meth:`_on_animation_surface_changed` 判断——卡片真的
        挪了就取消这一次发牌（``surface-changed``），只是可见区域变了就刷新裁剪遮罩。
        """
        cards_area = getattr(self, "cards_area", None)
        if (
            cards_area is not None
            and watched is cards_area.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            self._on_animation_surface_changed()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # 先把外层布局激活，卡片区（以及它的视口）拿到新宽度后再决定列数；否则会按
        # resize 前的视口宽度少排一列，窗口变宽后仍停在旧列数。
        layout = self.layout()
        if layout is not None:
            layout.activate()
        self._refresh_cards_geometry()
        self._sync_wait_stack_geometry()
        self._sync_deal_layer_geometry()
        # 面板自身尺寸变化同样会挪动卡片：页面根容器没变时（例如滚动条出现把视口挤窄）
        # 覆盖层收不到通知，这里补上同一策略。
        self._on_animation_surface_changed()

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
    "DiscoveryPhase",
    "DeviceDiscoveryPanel",
    "DiscoveryWorker",
    "LOADING_TEXT",
    "ROLE_LABELS",
    "SCAN_MODE_CHOICES",
    "WAIT_FOR_FULL_CYCLE",
    "binding_device_key",
    "card_heading",
    "card_model",
    "card_status_text",
    "device_applicable",
    "device_heading",
    "role_label",
    "subnet_from_address",
]

