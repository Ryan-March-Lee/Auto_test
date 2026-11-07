"""电源通道卡片：一张横卡表示一台电源的两个通道，也是通道参数的唯一编辑面。

这张卡只有正面，不参与设备发现的发牌动画，因此没有牌背。卡面内容自上而下：

- **设备码铭牌**：顶部正中的白色圆角牌，等宽加粗字只写 ``label``（``DP8B1519``），
  压住左右两半的分界线；
- **两个通道**：左右各一半，通道底色从卡片顶边一直铺到卡底；卡面不再写
  ``CH1`` / ``CH2``，通道身份只由左右位置、底色和悬浮提示表达；
- **供电角色滚筒**：每个通道贴外侧上角一个 84×40 的深色竖直滚筒，三档
  ``OFF`` / ``DUT`` / ``Driver`` 像老虎机一样上下滚动。``OFF`` 关闭通道，
  ``DUT`` 表示该通道给功放供电，``Driver`` 表示给驱放供电；当前档位居中放大、
  白色发光，上下相邻档位缩小、变暗并带柔化，红色指示点与玻璃高光保留参考实现的
  质感。滚筒只响应点击循环（不响应滚轮），点一下到下一档、第三下回到 ``OFF``，
  即 ``OFF → DUT → Driver → OFF``；键盘 ``Space`` / ``Enter`` 同样循环。档位文字
  不再带 01/02/03 编号，整行垂直居中并向左侧红点靠拢；
- **``Cal.`` 效率计算标记**：滚筒靠卡面中央的一侧一个 ``Cal.`` 圆角小开关，按下表示
  该通道参与效率计算；两个通道可各自独立标记，也可以同时标记。未启用时是白底细边、
  灰字，启用后填满通道强调色、白字并带一圈柔和辉光，切换时播放 240 ms 的填充与
  轻微弹跳。它不改变供电角色、上电顺序或通道开关；页面会把第一个已上电的标记
  通道写成现有运行链路的单个漏极；
- **两个可编辑值**：每个通道第一行左右各一个，``VS``（电压设定）、``IS``（电流设定）；
  数值框是真实的 ``QDoubleSpinBox``，``Driver`` / ``DUT`` 档位下可以直接输入，配置
  读写仍走页面/表单 Adapter，卡片不自己写文件；
- **两个保护勾选框**：每个通道第二行左右各一个，左边电压保护、右边电流保护；
  两个勾选框的卡面文字统一是「10%保护」——左右位置（分别压在 VS / IS 数值框下方）
  和悬浮提示区分电压/电流，勾选状态来自配置里的 ``voltage.protection_enabled`` /
  ``current.protection_enabled``；卡面不再显示保护数值，也不再有底部那行说明文字，
  阈值仍由配置读取按工作值的 110% 推导。保护行紧贴工作值行；
- **上电顺序 / 间隔**：最下面一行，左边下拉框选择本通道在其上电计划里的位置，
  右边输入该通道开启后到下一条上电命令的等待秒数（缺省 2s）。不在计划里的通道
  这一行禁用。

**档位就是上电角色**：``Driver`` / ``DUT`` 不只是显示标注，页面会把该通道同步勾进
``power_supply_assignment.driver_amplifier`` / ``dut_amplifier`` 的 ``power_plan``；
``OFF`` 同时从两份计划里移除。角色因此仍由现有计划结构表达，不新增通道字段。一个
通道只能属于一份计划；页面在卡片与计划编辑器之间做双向同步。``OFF`` 时该通道整块
降为灰色（``disabled_bg``），数值框与勾选框一起禁用；``Driver`` / ``DUT`` 时恢复
通道底色（左浅蓝、右浅琥珀）。驱放测试模式关闭时 ``Driver`` 档被禁用，循环会跳过它。

卡片同时支持两种尺寸：``render_power_card`` 按 440×186 的原始逻辑尺寸导出静态
位图，``PowerSupplyCard`` 则按卡片墙给的缩放比例设置控件尺寸；绘制统一在
``painter.scale`` 之后按原始坐标进行，因此放大/缩小后的卡面与导出图同源。数值输入框、
角色滚筒与保护勾选框都是真实子控件，鼠标与键盘操作由 Qt 直接处理。身份详情（完整
设备码、设备键）与数值提示放在悬浮提示里。

**卡面与交互共用一份绘制**：:func:`render_power_card` 把同一个
:func:`_paint_power_card` 画到透明位图上，所以导出的 PNG 预览与页面上的真实卡片
逐像素同源；滚筒的悬停、按下、焦点与滚动位置也由这份绘制按状态参数画出来，不看
控件截图。用户换档时控件在 700 ms 内按 ``OutExpo`` 插值重绘，导出位图直接取终态。

**尺寸与字体**：卡片固定为 :data:`POWER_CARD_WIDTH` × :data:`POWER_CARD_HEIGHT`
（440×186），圆角取 :data:`POWER_CARD_RADIUS`（12，比通用卡片更圆，
在 440 宽的卡面上能看出轮廓）；角色滚筒 84×40，三档每行约 13.3 px，当前档字
约 12 px，卡片缩放时滚筒与字号一起缩放。字体系列取自
:func:`presentation.qt.theme.card_status_font` 与
:func:`presentation.qt.theme.card_code_font`。颜色全部来自
:data:`presentation.qt.theme.TOKENS`，其中包括两个通道的 ``channel_cool`` /
``channel_warm`` 与 ``mode_selector_*`` 一组滚筒色。

**通道色块**：两个通道各有一块内缩 5、中央留 10 白缝的圆角色块（见
:data:`_PANEL_INSET` / :data:`_PANEL_GAP`）。色块带一层很浅的纵向渐变和贴缝一侧的
内阴影，顶部强调线裁在圆角里；卡面因此不再画中央分隔线——两台通道的区分交给两块
实色之间的白缝，而不是一条 1px 描边。关闭的通道整块换成 ``disabled_bg``，白缝仍然
保留。

**运行约束**：位图用 ``QPixmap`` 绘制，必须在创建 ``QApplication`` 之后、在 GUI
线程里调用。卡片控件本身也只在 GUI 线程中使用。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Mapping

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QFont,
    QFontMetricsF,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSpinBox,
    QSizePolicy,
    QWidget,
)

from domain.instruments.labels import supply_identity_text

from application.dto.amplifier_inputs import (
    DEFAULT_POWER_ON_DELAY_S,
    MAX_INTER_CHANNEL_DELAY_S,
)

from .input_controls import KeyboardOnlyDoubleSpinBox, WheelSafeComboBox
from .theme import (
    ASSETS_DIR,
    TOKENS,
    card_code_font,
    card_status_font,
)

__all__ = [
    "CHANNEL_MODES",
    "CHANNEL_NAMES",
    "DEFAULT_POWER_ON_DELAY_S",
    "CardDelayField",
    "CardOrderCombo",
    "CardValueField",
    "ChannelModeSelector",
    "EfficiencyCalibrationToggle",
    "POWER_CARD_HEIGHT",
    "POWER_CARD_RADIUS",
    "POWER_CARD_SIZE",
    "POWER_CARD_WIDTH",
    "PROTECTION_KINDS",
    "PowerCardLayout",
    "PowerChannelData",
    "PowerChannelLayout",
    "PowerSupplyCard",
    "PowerSupplyCardData",
    "ProtectionCheck",
    "power_card_layout",
    "render_power_card",
]

#: 电源固定两个通道，顺序也是卡面上的左右顺序：左边 CH1，右边 CH2。
CHANNEL_NAMES = ("CH1", "CH2")

#: 通道供电角色，顺序既是滚筒从上到下的档位顺序，也是点击切换顺序：
#: 点一下切到下一档，第三下回到 OFF，滚筒随之向上滚一档。
CHANNEL_MODES = ("off", "dut", "driver")
CHANNEL_MODE_LABELS = {
    "off": "OFF",
    "driver": "Driver",
    "dut": "DUT",
}

#: 卡片逻辑尺寸：440×186，在通道行下多留一行“上电顺序 / 间隔”。
POWER_CARD_WIDTH = 440
POWER_CARD_HEIGHT = 186
POWER_CARD_SIZE = QSize(POWER_CARD_WIDTH, POWER_CARD_HEIGHT)

#: 卡片圆角：比通用卡片（``theme.CARD_RADIUS`` = 8）更大，440 宽的卡面才看得出轮廓。
POWER_CARD_RADIUS = 12.0
#: 通道色块到卡片外沿的内缩；两块色块之间因此留下 :data:`_PANEL_GAP` 宽的白缝。
_PANEL_INSET = 5.0
#: 两块通道色块中央的白缝宽度；通道区分靠这条缝，卡面不再画 1px 分隔线。
_PANEL_GAP = 10.0
#: 通道色块的圆角；比卡片圆角小 2，内缩后与卡片轮廓平行。
_PANEL_RADIUS = 10.0

# ---- 卡面几何：所有位置都由这一组常量算出，绘制与控件命中区共用 -----------------
_PADDING = 14.0
_HALF_WIDTH = POWER_CARD_WIDTH / 2.0
# 供电角色滚筒：贴住左右外侧上角。再压扁一档到 40 高，并把顶边下移到 12：
# 色块顶边的强调线在 y=5–7，滚筒自身还有向上约 4.5 的外阴影，顶边留在 6 会把
# 那条色条盖掉；下移后色条完整露出，滚筒底边 52 仍在 VS/IS 行（y=72）上方。
# 三档每行约 13.3 逻辑像素，当前档字约 12px。
_SELECTOR_WIDTH = 84.0
_SELECTOR_HEIGHT = 40.0
_SELECTOR_TOP = 12.0
_SELECTOR_INSET_X = 12.0
_SELECTOR_RADIUS = 11.0
_SELECTOR_ROW_HEIGHT = _SELECTOR_HEIGHT / 3.0
#: ``Cal.`` 效率计算标记：贴在角色滚筒靠卡面中央的一侧，与滚筒同一条中线。
#: 左右两侧各留 8 的间距，正好落在滚筒与顶部设备码铭牌之间。
_CALIBRATION_WIDTH = 36.0
_CALIBRATION_HEIGHT = 22.0
_CALIBRATION_GAP = 8.0
_CALIBRATION_RADIUS = 11.0
_CALIBRATION_TOP = _SELECTOR_TOP + (_SELECTOR_HEIGHT - _CALIBRATION_HEIGHT) / 2.0
# 设备码铭牌：顶部正中的白色圆角牌，宽度足够放下 8 位等宽设备码而不被省略。
_TITLE_WIDTH = 148.0
_TITLE_HEIGHT = 46.0
_TITLE_TOP = 8.0
#: ``VS`` / ``IS`` 一行的顶边：铭牌与角色滚筒让出的空行删除后，工作值直接上移。
_GRID_TOP = 72.0
_ROW_HEIGHT = 34.0
#: 工作值行与保护行之间不留空行：保护勾选紧贴 VS/IS，视觉上属于同一组设置。
_VALUE_PROTECTION_GAP = 0.0
#: 保护行与最下面的顺序/间隔行之间留足距离，把两组设置分开。
_PROTECTION_SETTING_GAP = 16.0
_BOX_HEIGHT = 30.0
_LABEL_WIDTH = 24.0
_LABEL_GAP = 4.0
#: 同一行两个格子之间的横向间距；与纵向的行间距分开取值。
_CELL_GAP = 10.0
_CELL_WIDTH = (_HALF_WIDTH - 2.0 * _PADDING - _CELL_GAP) / 2.0
_BOX_WIDTH = _CELL_WIDTH - _LABEL_WIDTH - _LABEL_GAP
#: 第一行只剩工作值：VS（电压设定）、IS（电流设定）。
_VALUE_TEXTS = ("VS", "IS")
#: 两个工作值的字段名，顺序与 :data:`_VALUE_TEXTS` 一致。
VALUE_KINDS = ("voltage", "current")
#: 卡面数值框的显示上下界：只约束输入框能填到哪里，正式校验的合法范围在 domain。
VALUE_LIMITS = {
    "voltage": (0.0, 50.0),
    "current": (0.0, 10.0),
}
#: 数值框保留的小数位，与配置里保护阈值的显示精度一致。
VALUE_DECIMALS = 2
#: 第二行是保护勾选框，顺序也是卡面上的左右顺序：左电压、右电流。
PROTECTION_KINDS = ("voltage", "current")
#: 四个勾选框的卡面文字统一写「10%保护」：电压/电流的区分交给左右位置（分别压在
#: VS / IS 数值框下方）与悬浮提示，卡面不再出现“电压/电流”字样，也不再有底部说明行。
_PROTECTION_TEXTS = ("10%保护", "10%保护")
#: 保护勾选框一行比工作值一行矮一点，紧贴 VS/IS 行时仍留出文字高度。
_PROTECTION_ROW_HEIGHT = 26.0
#: 最下面一行：左边上电顺序、右边上电间隔；行高保持 30。
_SETTING_ROW_HEIGHT = 30.0
#: 顺序/间隔控件本身比行矮 2，和数值框的纵向留白一致。
_SETTING_BOX_HEIGHT = 28.0
_SETTING_TOP = (
    _GRID_TOP
    + _ROW_HEIGHT
    + _VALUE_PROTECTION_GAP
    + _PROTECTION_ROW_HEIGHT
    + _PROTECTION_SETTING_GAP
)
#: 勾选框方块固定 14 逻辑像素，并与上一行的 ``VS`` / ``IS`` 标签共用同一条中线；
#: 文字从数值框的左边缘开始，因此「10%保护」与「12.00」左对齐。
_INDICATOR_SIZE = 14.0
_INDICATOR_INSET = (_LABEL_WIDTH - _INDICATOR_SIZE) / 2.0

# ---- 配色：全部来自 TOKENS，关闭态只换 token，不另造一套灰 ----------------------
_SURFACE = QColor(TOKENS["surface"])
_SURFACE_ALT = QColor(TOKENS["surface_alt"])
_COOL = QColor(TOKENS["channel_cool"])
_WARM = QColor(TOKENS["channel_warm"])
_OFF = QColor(TOKENS["disabled_bg"])
_BORDER = QColor(TOKENS["border"])
_BORDER_STRONG = QColor(TOKENS["border_strong"])
_TEXT = QColor(TOKENS["text"])
_TEXT_MUTED = QColor(TOKENS["text_muted"])
_DISABLED_TEXT = QColor(TOKENS["disabled_text"])
_PRIMARY = QColor(TOKENS["primary"])
_WARNING = QColor(TOKENS["warning"])
# 角色滚筒：深色玻璃槽体、金属拉丝底、红色指示点；当前档白字，邻档灰字。
_SELECTOR_BODY = QColor(TOKENS["mode_selector_body"])
_SELECTOR_BODY_BOTTOM = QColor(TOKENS["mode_selector_body_bottom"])
_SELECTOR_METAL = QColor(TOKENS["mode_selector_metal"])
_SELECTOR_METAL_DARK = QColor(TOKENS["mode_selector_metal_dark"])
_SELECTOR_BORDER = QColor(TOKENS["mode_selector_border"])
_SELECTOR_BORDER_HOVER = QColor(TOKENS["mode_selector_border_hover"])
_SELECTOR_ACCENT = QColor(TOKENS["mode_selector_accent"])
_SELECTOR_TEXT = QColor(TOKENS["mode_selector_text"])
_SELECTOR_TEXT_IDLE = QColor(TOKENS["mode_selector_text_idle"])
_SELECTOR_FOCUS = QColor(TOKENS["mode_selector_focus"])


# ---- 数据 ------------------------------------------------------------------


def _finite_non_negative(value: Any, name: str) -> float:
    """通道数值的显示口径：非 bool 的有限非负数；其余输入直接报错。

    卡片是只读资产，不替调用方把非法值静默换成 0：错误值应该停在构造阶段，
    而不是在卡面上显示成看起来正常的 ``0.00``。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} 必须是数字，收到 {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须是有限数字，收到 {value!r}")
    if number < 0.0:
        raise ValueError(f"{name} 不能为负数，收到 {value!r}")
    return number


def _power_delay(value: Any) -> float:
    """上电间隔：有限、非负且不超过计划间隔的统一上限。"""
    number = _finite_non_negative(value, "上电间隔")
    if number > MAX_INTER_CHANNEL_DELAY_S:
        raise ValueError(
            f"上电间隔不能超过 {MAX_INTER_CHANNEL_DELAY_S:g}s，收到 {number}s"
        )
    return number


@dataclass(frozen=True)
class PowerChannelData:
    """一个电源通道在卡面上的快照：供电角色 + 两个工作值 + 两个保护开关。

    ``channel`` 只能是 ``CH1`` / ``CH2``；``mode`` 是 ``off`` / ``driver`` /
    ``dut``，分别表示通道关闭、给驱放供电、给功放供电。``enabled`` 保留为派生
    字段（``mode != "off"``），旧调用方仍可读；构造时如果 ``enabled=False``，
    ``mode`` 会被规整成 ``off``，两者不会出现矛盾状态。两个数值依次是电压设定、
    电流设定，单位分别是 V、A。保护阈值不再出现在卡面上，只保留“是否启用电压/
    电流保护”两个状态，阈值仍由配置读取按工作值的 110% 推导。``efficiency_calibration_enabled``
    是卡面 ``Cal.`` 开关的效率计算标记，与通道开关无关，两个通道可以同时为真。
    数据类只做“有限且非负”的显示校验，0–50 V / 0–10 A 的输入上下界由卡面输入控件
    （:class:`CardValueField`）约束，不在数据快照里夹值。
    """

    channel: str
    enabled: bool = True
    voltage_set: float = 0.0
    current_set: float = 0.0
    voltage_protection_enabled: bool = False
    current_protection_enabled: bool = False
    #: 是否标记为效率计算通道；两个通道可以同时为真。
    efficiency_calibration_enabled: bool = False
    mode: str = "driver"
    #: 本通道在其上电计划里的 1-based 位置；不在计划里时为 ``None``。
    power_order: int | None = None
    #: 所在计划的路由总数；决定顺序下拉框的选项数量，``0`` 表示不参与上电。
    power_order_count: int = 0
    #: 本通道上电后等待多少秒再发下一条上电命令；缺省 2s。
    power_delay_s: float = DEFAULT_POWER_ON_DELAY_S

    def __post_init__(self) -> None:
        if self.channel not in CHANNEL_NAMES:
            raise ValueError(
                f"通道必须是 {' / '.join(CHANNEL_NAMES)}，收到 {self.channel!r}"
            )
        if self.mode not in CHANNEL_MODES:
            raise ValueError(
                f"通道角色必须是 {' / '.join(CHANNEL_MODES)}，收到 {self.mode!r}"
            )
        for field, label in (
            ("enabled", "enabled"),
            ("voltage_protection_enabled", "电压保护"),
            ("current_protection_enabled", "电流保护"),
            ("efficiency_calibration_enabled", "效率计算标记"),
        ):
            if not isinstance(getattr(self, field), bool):
                raise TypeError(f"通道 {self.channel} 的 {label} 必须是 bool")
        for field, label in (
            ("voltage_set", "电压设定"),
            ("current_set", "电流设定"),
        ):
            object.__setattr__(
                self,
                field,
                _finite_non_negative(getattr(self, field), f"{self.channel} {label}"),
            )
        # ``enabled`` 与 ``mode`` 只保留一份真值：关闭就是 off，开启时保留角色。
        mode = "off" if not self.enabled else self.mode
        object.__setattr__(self, "mode", mode)
        object.__setattr__(self, "enabled", mode != "off")
        if (
            isinstance(self.power_order_count, bool)
            or not isinstance(self.power_order_count, int)
            or self.power_order_count < 0
        ):
            raise ValueError(
                f"通道 {self.channel} 的上电顺序总数必须是非负整数，"
                f"收到 {self.power_order_count!r}"
            )
        if self.power_order is not None:
            if (
                isinstance(self.power_order, bool)
                or not isinstance(self.power_order, int)
                or self.power_order < 1
            ):
                raise ValueError(
                    f"通道 {self.channel} 的上电顺序必须是正整数或 None，"
                    f"收到 {self.power_order!r}"
                )
            if self.power_order > self.power_order_count:
                raise ValueError(
                    f"通道 {self.channel} 的上电顺序 {self.power_order} "
                    f"超出计划路由数 {self.power_order_count}"
                )
        object.__setattr__(
            self, "power_delay_s", _power_delay(self.power_delay_s)
        )

    def protection_enabled(self, kind: str) -> bool:
        """某种保护的启用状态；``kind`` 只能是 ``voltage`` / ``current``。"""
        if kind == "voltage":
            return self.voltage_protection_enabled
        if kind == "current":
            return self.current_protection_enabled
        raise KeyError(f"保护类型必须是 voltage / current，收到 {kind!r}")

    def with_mode(self, mode: str) -> "PowerChannelData":
        """返回一份只改供电角色的新快照；``off`` 同时把 ``enabled`` 置为假。"""
        if mode not in CHANNEL_MODES:
            raise ValueError(
                f"通道角色必须是 {' / '.join(CHANNEL_MODES)}，收到 {mode!r}"
            )
        return replace(self, mode=mode, enabled=mode != "off")

    def with_protection(self, kind: str, enabled: bool) -> "PowerChannelData":
        """返回一份只改某种保护开关的新快照。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        self.protection_enabled(kind)
        return replace(self, **{f"{kind}_protection_enabled": enabled})

    def with_value(self, kind: str, value: Any) -> "PowerChannelData":
        """返回一份只改电压/电流设定值的新快照；非法值在这里就报错。"""
        if kind not in VALUE_KINDS:
            raise KeyError(f"工作值类型必须是 voltage / current，收到 {kind!r}")
        number = _finite_non_negative(value, f"{self.channel} {kind}")
        field = "voltage_set" if kind == "voltage" else "current_set"
        return replace(self, **{field: number})

    def with_efficiency_calibration(self, enabled: bool) -> "PowerChannelData":
        """返回一份只改效率计算标记的新快照；不改供电角色与通道开关。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        return replace(self, efficiency_calibration_enabled=enabled)

    def with_power_role(
        self, order: int | None, count: int, delay: Any
    ) -> "PowerChannelData":
        """同时更新上电顺序、可选顺序数与上电间隔。"""
        return replace(
            self,
            power_order=order,
            power_order_count=count,
            power_delay_s=delay,
        )

    def with_power_order(self, order: int | None) -> "PowerChannelData":
        """只改上电顺序，保留计划路由数与间隔。"""
        return replace(self, power_order=order)

    def with_power_delay(self, delay: Any) -> "PowerChannelData":
        """只改上电间隔，保留顺序与计划路由数。"""
        return replace(self, power_delay_s=delay)


@dataclass(frozen=True)
class PowerSupplyCardData:
    """一张电源通道卡片的全部内容：设备码缩写 + 两个通道 + 身份详情。

    ``channels`` 可以按任意顺序传入，构造时会规整成 ``CH1``、``CH2`` 的左右
    顺序，因此调用方不必关心卡面从哪里开始画。两个通道缺一个、重复或名称不合法
    都会在这里报错，而不是画出一张缺一半的卡。

    ``model`` / ``full_code`` / ``device_key`` 只用于悬浮提示里的身份详情（卡面放不下），
    缺哪个就少显示哪一段；它们不参与路由或读写。
    """

    code: str
    channels: tuple[PowerChannelData, ...]
    model: str | None = None
    full_code: str | None = None
    device_key: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, str):
            raise TypeError(f"设备码缩写必须是字符串，收到 {self.code!r}")
        object.__setattr__(self, "code", self.code.strip())
        for field in ("model", "full_code", "device_key"):
            value = getattr(self, field)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"{field} 必须是字符串或 None，收到 {value!r}")
            if isinstance(value, str):
                object.__setattr__(self, field, value.strip() or None)
        channels = tuple(self.channels)
        if len(channels) != len(CHANNEL_NAMES):
            raise ValueError(
                f"电源卡片必须正好有 {len(CHANNEL_NAMES)} 个通道，收到 {len(channels)} 个"
            )
        for item in channels:
            if not isinstance(item, PowerChannelData):
                raise TypeError(f"通道数据必须是 PowerChannelData，收到 {item!r}")
        by_name = {item.channel: item for item in channels}
        if set(by_name) != set(CHANNEL_NAMES):
            raise ValueError(
                f"通道必须正好是 {' / '.join(CHANNEL_NAMES)}，收到 "
                f"{sorted(by_name)}"
            )
        object.__setattr__(
            self,
            "channels",
            tuple(by_name[name] for name in CHANNEL_NAMES),
        )

    def channel(self, name: str) -> PowerChannelData:
        """按通道名取快照；名称不合法时抛 ``KeyError``。"""
        for item in self.channels:
            if item.channel == name:
                return item
        raise KeyError(f"卡片里没有通道 {name!r}")

    def with_channel_enabled(self, name: str, enabled: bool) -> "PowerSupplyCardData":
        """兼容旧调用：``True`` 保留已有角色（原本是 off 时给 ``driver``）。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        current = self.channel(name)
        mode = current.mode if enabled and current.mode != "off" else "off"
        if enabled and mode == "off":
            mode = "driver"
        return self.with_channel_mode(name, mode)

    def with_channel_mode(self, name: str, mode: str) -> "PowerSupplyCardData":
        """返回一份只改某个通道供电角色的新快照（其余值原样保留）。"""
        if mode not in CHANNEL_MODES:
            raise ValueError(
                f"通道角色必须是 {' / '.join(CHANNEL_MODES)}，收到 {mode!r}"
            )
        self.channel(name)
        return replace(
            self,
            channels=tuple(
                item.with_mode(mode) if item.channel == name else item
                for item in self.channels
            ),
        )

    def with_channel_power(
        self,
        name: str,
        order: int | None,
        count: int,
        delay: Any,
    ) -> "PowerSupplyCardData":
        """返回一份只改某个通道上电顺序/可选顺序数/间隔的新快照。"""
        self.channel(name)
        return replace(
            self,
            channels=tuple(
                item.with_power_role(order, count, delay)
                if item.channel == name
                else item
                for item in self.channels
            ),
        )

    def channel_protection(self, name: str, kind: str) -> bool:
        """某个通道某种保护的启用状态；名称/类型不合法时抛 ``KeyError``。"""
        return self.channel(name).protection_enabled(kind)

    def with_channel_protection(
        self, name: str, kind: str, enabled: bool
    ) -> "PowerSupplyCardData":
        """返回一份只改某个通道某种保护开关的新快照（其余值原样保留）。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        self.channel(name)
        return replace(
            self,
            channels=tuple(
                item.with_protection(kind, enabled) if item.channel == name else item
                for item in self.channels
            ),
        )

    def with_channel_value(
        self, name: str, kind: str, value: Any
    ) -> "PowerSupplyCardData":
        """返回一份只改某个通道某种工作值的新快照（其余值原样保留）。"""
        self.channel(name)
        return replace(
            self,
            channels=tuple(
                item.with_value(kind, value) if item.channel == name else item
                for item in self.channels
            ),
        )

    def channel_efficiency_calibration(self, name: str) -> bool:
        """某个通道是否被标记为效率计算通道。"""
        return self.channel(name).efficiency_calibration_enabled

    def with_channel_efficiency_calibration(
        self, name: str, enabled: bool
    ) -> "PowerSupplyCardData":
        """返回一份只改某个通道效率计算标记的新快照。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        self.channel(name)
        return replace(
            self,
            channels=tuple(
                item.with_efficiency_calibration(enabled)
                if item.channel == name
                else item
                for item in self.channels
            ),
        )


# ---- 几何 ------------------------------------------------------------------


@dataclass(frozen=True)
class PowerChannelLayout:
    """一个通道在卡面坐标系里的位置：面板、角色滚筒、两个值与两个勾选框。"""

    channel: str
    #: 通道整块命中区（半个卡片），滚筒控件与编辑控件都落在里面。
    panel: QRectF
    #: 实际绘制的圆角色块：从 :attr:`panel` 内缩 5，两块之间留 10 白缝。
    panel_surface: QRectF
    #: 供电角色滚筒（OFF / DUT / Driver）的卡面矩形，也是透明命中区。
    mode_selector: QRectF
    #: ``Cal.`` 效率计算标记的整块命中区：贴在滚筒靠卡面中央的一侧。
    calibration_toggle: QRectF
    value_labels: tuple[QRectF, ...]
    value_boxes: tuple[QRectF, ...]
    #: 两个保护勾选框的整块命中区（方块 + 文字），顺序是电压、电流。
    protection_checks: tuple[QRectF, ...]
    #: 两个保护勾选框的方块本身，绘制与焦点框用它。
    protection_indicators: tuple[QRectF, ...]
    #: 两个保护勾选框的文字区，左边缘与上一行数值框对齐。
    protection_labels: tuple[QRectF, ...]
    #: 最下面一行的两个标签：左边「顺序」、右边「间隔」。
    power_labels: tuple[QRectF, ...]
    #: 最下面一行的两个控件：上电顺序下拉框、上电间隔数值框。
    power_controls: tuple[QRectF, ...]


@dataclass(frozen=True)
class PowerCardLayout:
    """整张卡片的几何：卡片、设备码铭牌与左右两个通道。"""

    card: QRectF
    #: 顶部正中设备码铭牌的矩形（不再是一条通栏标题带）。
    title: QRectF
    channels: tuple[PowerChannelLayout, ...]


def power_card_layout() -> PowerCardLayout:
    """算出整张卡片的几何；绘制、命中区与测试都读这一份结果。

    卡片尺寸固定，因此不需要宽高参数。左右通道按 :data:`CHANNEL_NAMES` 的顺序
    排列，每个通道的角色滚筒贴住自己外侧的上角，工作值与保护勾选框依次往下排，
    最下面是上电顺序/间隔；保护行紧贴工作值行。:attr:`PowerChannelLayout.panel` 是
    半个卡片的命中区；真正画出来的圆角色块是内缩 5、中央留 10 白缝的
    :attr:`PowerChannelLayout.panel_surface`。
    """
    card = QRectF(0.0, 0.0, float(POWER_CARD_WIDTH), float(POWER_CARD_HEIGHT))
    title = QRectF(
        (POWER_CARD_WIDTH - _TITLE_WIDTH) / 2.0,
        _TITLE_TOP,
        _TITLE_WIDTH,
        _TITLE_HEIGHT,
    )
    channels: list[PowerChannelLayout] = []
    for index, name in enumerate(CHANNEL_NAMES):
        left = index * _HALF_WIDTH
        panel = QRectF(
            left,
            0.0,
            _HALF_WIDTH,
            float(POWER_CARD_HEIGHT),
        )
        panel_surface = QRectF(
            left + _PANEL_INSET,
            _PANEL_INSET,
            _HALF_WIDTH - _PANEL_INSET - _PANEL_GAP / 2.0,
            float(POWER_CARD_HEIGHT) - 2.0 * _PANEL_INSET,
        )
        # 滚筒贴住本通道的外侧上角：左通道靠左，右通道靠右，两个滚筒的水平线一致。
        if index == 0:
            selector_x = left + _SELECTOR_INSET_X
        else:
            selector_x = left + _HALF_WIDTH - _SELECTOR_INSET_X - _SELECTOR_WIDTH
        mode_selector = QRectF(
            selector_x,
            _SELECTOR_TOP,
            _SELECTOR_WIDTH,
            _SELECTOR_HEIGHT,
        )
        # ``Cal.`` 标记贴在滚筒朝卡面中央的一侧，与滚筒同一条水平中线；
        # 位置由滚筒外沿算出，左右两个通道因此镜像对称。
        if index == 0:
            calibration_x = mode_selector.right() + _CALIBRATION_GAP
        else:
            calibration_x = mode_selector.left() - _CALIBRATION_GAP - _CALIBRATION_WIDTH
        calibration_toggle = QRectF(
            calibration_x,
            _CALIBRATION_TOP,
            _CALIBRATION_WIDTH,
            _CALIBRATION_HEIGHT,
        )
        value_labels: list[QRectF] = []
        value_boxes: list[QRectF] = []
        for value_index in range(len(_VALUE_TEXTS)):
            cell_x = left + _PADDING + value_index * (_CELL_WIDTH + _CELL_GAP)
            cell_y = _GRID_TOP
            value_labels.append(
                QRectF(cell_x, cell_y, _LABEL_WIDTH, _ROW_HEIGHT)
            )
            value_boxes.append(
                QRectF(
                    cell_x + _LABEL_WIDTH + _LABEL_GAP,
                    cell_y + (_ROW_HEIGHT - _BOX_HEIGHT) / 2.0,
                    _BOX_WIDTH,
                    _BOX_HEIGHT,
                )
            )
        protection_checks: list[QRectF] = []
        protection_indicators: list[QRectF] = []
        protection_labels: list[QRectF] = []
        for protection_index in range(len(PROTECTION_KINDS)):
            cell_x = left + _PADDING + protection_index * (_CELL_WIDTH + _CELL_GAP)
            cell_y = _GRID_TOP + _ROW_HEIGHT + _VALUE_PROTECTION_GAP
            protection_checks.append(
                QRectF(cell_x, cell_y, _CELL_WIDTH, _PROTECTION_ROW_HEIGHT)
            )
            # 方块与上一行 ``VS`` / ``IS`` 标签同一条中线；文字与数值框左对齐。
            protection_indicators.append(
                QRectF(
                    cell_x + _INDICATOR_INSET,
                    cell_y + (_PROTECTION_ROW_HEIGHT - _INDICATOR_SIZE) / 2.0,
                    _INDICATOR_SIZE,
                    _INDICATOR_SIZE,
                )
            )
            protection_labels.append(
                QRectF(
                    cell_x + _LABEL_WIDTH + _LABEL_GAP,
                    cell_y,
                    _BOX_WIDTH,
                    _PROTECTION_ROW_HEIGHT,
                )
            )
        power_labels: list[QRectF] = []
        power_controls: list[QRectF] = []
        for setting_index in range(2):
            cell_x = left + _PADDING + setting_index * (_CELL_WIDTH + _CELL_GAP)
            power_labels.append(
                QRectF(cell_x, _SETTING_TOP, _LABEL_WIDTH, _SETTING_ROW_HEIGHT)
            )
            power_controls.append(
                QRectF(
                    cell_x + _LABEL_WIDTH + _LABEL_GAP,
                    _SETTING_TOP + (_SETTING_ROW_HEIGHT - _SETTING_BOX_HEIGHT) / 2.0,
                    _BOX_WIDTH,
                    _SETTING_BOX_HEIGHT,
                )
            )
        channels.append(
            PowerChannelLayout(
                channel=name,
                panel=panel,
                panel_surface=panel_surface,
                mode_selector=mode_selector,
                calibration_toggle=calibration_toggle,
                value_labels=tuple(value_labels),
                value_boxes=tuple(value_boxes),
                protection_checks=tuple(protection_checks),
                protection_indicators=tuple(protection_indicators),
                protection_labels=tuple(protection_labels),
                power_labels=tuple(power_labels),
                power_controls=tuple(power_controls),
            )
        )
    return PowerCardLayout(card=card, title=title, channels=tuple(channels))


# ---- 绘制 ------------------------------------------------------------------


def _rounded(rect: QRectF, inset: float = 0.0) -> QPainterPath:
    """卡片轮廓：圆角取电源卡自己的半径；``inset`` 用于把描边画在轮廓以内。"""
    adjusted = rect.adjusted(inset, inset, -inset, -inset)
    radius = max(1.0, POWER_CARD_RADIUS - inset)
    path = QPainterPath()
    path.addRoundedRect(adjusted, radius, radius)
    return path


def _value_label_font() -> QFont:
    """两个值的缩写 ``VS`` / ``IS``：10pt 等宽加粗。"""
    font = QFont(card_code_font())
    font.setPointSizeF(10.0)
    return font


def _value_font() -> QFont:
    """数值本身：12pt 等宽加粗，保证 ``50.00`` 这类五位数也放得下。"""
    font = QFont(card_code_font())
    font.setPointSizeF(12.0)
    return font


def _protection_font() -> QFont:
    """勾选框文字“电压保护”“电流保护”：9pt 中等字重，用界面字体保证中文清晰。"""
    font = QFont(card_status_font())
    font.setPointSizeF(9.0)
    font.setWeight(QFont.Weight.DemiBold)
    return font


def _calibration_font() -> QFont:
    """``Cal.`` 标记：8.5pt 等宽加粗，放在 36×22 的小圆角矩形里仍清晰。"""
    font = QFont(card_code_font())
    font.setPointSizeF(8.5)
    font.setWeight(QFont.Weight.Bold)
    return font


def _accent(index: int) -> QColor:
    """通道强调色：左边主色，右边警告色。"""
    return _PRIMARY if index == 0 else _WARNING


def _panel_fill(index: int, enabled: bool) -> QColor:
    """通道底色：开启时左浅蓝/右浅琥珀，关闭时统一降到禁用灰。"""
    if not enabled:
        return _OFF
    return _COOL if index == 0 else _WARM


def _rounded_rect(rect: QRectF, radius: float) -> QPainterPath:
    """任意圆角矩形轮廓；铭牌与投影用同一个半径族。"""
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    return path


def _paint_title(painter: QPainter, data: PowerSupplyCardData, rect: QRectF) -> None:
    """设备码铭牌：顶部正中的白色圆角牌，压住左右两半的分界线。

    牌面比通道底色亮，设备码因此不会被两半的蓝/黄底色染色；外沿叠两层极淡的
    ``text`` 色圆角矩形当投影，不依赖模糊滤镜，离屏渲染与页面显示一致。
    """
    radius = 12.0
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    for grow, alpha in ((3.0, 0.045), (1.6, 0.070)):
        shadow = rect.adjusted(-grow, -grow + 2.0, grow, grow + 2.0)
        painter.fillPath(
            _rounded_rect(shadow, radius + grow),
            _with_alpha(_TEXT, alpha),
        )
    plate = _rounded_rect(rect, radius)
    painter.fillPath(plate, _SURFACE)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(_BORDER, 1.0))
    painter.drawPath(plate)

    font = card_code_font()
    painter.setFont(font)
    painter.setPen(_TEXT if data.code else _TEXT_MUTED)
    metrics = QFontMetricsF(font)
    available = max(0.0, rect.width() - 20.0)
    elided = metrics.elidedText(data.code or "—", Qt.ElideRight, available)
    painter.drawText(
        rect.adjusted(10.0, 0.0, -10.0, 0.0),
        int(Qt.AlignCenter),
        elided,
    )
    painter.restore()


def _paint_channel_panel(
    painter: QPainter,
    layout: PowerChannelLayout,
    index: int,
    enabled: bool,
) -> None:
    """通道圆角色块：内缩、浅纵向渐变、顶部强调线与贴缝内阴影。

    两块色块之间是卡片的白色底面（:data:`_PANEL_GAP`），不再画 1px 分隔线；关闭的
    通道整块换成 ``disabled_bg``，白缝与圆角保持原样。
    """
    surface = layout.panel_surface
    path = _rounded_rect(surface, _PANEL_RADIUS)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setClipPath(path)
    fill = _panel_fill(index, enabled)
    gradient = QLinearGradient(surface.topLeft(), surface.bottomLeft())
    # 渐变只压在上半张，下半张保持纯底色：卡面缩放或高 DPI 取色时颜色稳定。
    gradient.setColorAt(0.0, _mix_color(fill, _SURFACE, 0.55 if enabled else 0.35))
    gradient.setColorAt(0.55, fill)
    gradient.setColorAt(1.0, fill)
    painter.fillPath(path, QBrush(gradient))
    # 顶部强调线裁在色块圆角里，左右两端跟着圆角收进去。
    accent = _accent(index) if enabled else _BORDER_STRONG
    painter.fillRect(
        QRectF(surface.left(), surface.top(), surface.width(), 2.0),
        accent,
    )
    # 贴缝一侧的内阴影：白缝看起来把两块色块切开，而不是并排贴在卡面上。
    seam_width = 8.0
    alpha = 0.10 if enabled else 0.06
    if index == 0:
        seam = QLinearGradient(
            surface.right() - seam_width, 0.0, surface.right(), 0.0
        )
        seam.setColorAt(0.0, _with_alpha(_TEXT, 0.0))
        seam.setColorAt(1.0, _with_alpha(_TEXT, alpha))
    else:
        seam = QLinearGradient(
            surface.left() + seam_width, 0.0, surface.left(), 0.0
        )
        seam.setColorAt(0.0, _with_alpha(_TEXT, 0.0))
        seam.setColorAt(1.0, _with_alpha(_TEXT, alpha))
    painter.fillRect(surface, QBrush(seam))
    painter.restore()


def _paint_value(
    painter: QPainter,
    label_rect: QRectF,
    box_rect: QRectF,
    label: str,
    value: float,
    index: int,
    enabled: bool,
) -> None:
    """一个值：左侧缩写 + 右侧数值框；关闭时标签与数值一起降到禁用色。"""
    painter.save()
    text_color = _TEXT if enabled else _DISABLED_TEXT
    painter.setFont(_value_label_font())
    painter.setPen(text_color)
    painter.drawText(label_rect, int(Qt.AlignCenter), label)

    pen_width = 1.5 if enabled else 1.0
    inset = pen_width / 2.0
    box = box_rect.adjusted(inset, inset, -inset, -inset)
    painter.setBrush(_SURFACE if enabled else _SURFACE_ALT)
    painter.setPen(
        QPen(_accent(index) if enabled else _BORDER_STRONG, pen_width)
    )
    painter.drawRoundedRect(box, 6.0, 6.0)

    font = _value_font()
    painter.setFont(font)
    painter.setPen(text_color)
    available = max(0.0, box_rect.width() - 12.0)
    text = QFontMetricsF(font).elidedText(
        f"{value:.2f}",
        Qt.ElideRight,
        available,
    )
    painter.drawText(
        box_rect.adjusted(6.0, 0.0, -6.0, 0.0),
        int(Qt.AlignCenter),
        text,
    )
    painter.restore()


def _paint_power_setting(
    painter: QPainter,
    box_rect: QRectF,
    text: str,
    index: int,
    enabled: bool,
) -> None:
    """最下面一行的顺序/间隔框：导出位图上画出与真实控件同形的框与值。

    页面上的真实下拉框/数值框会盖在这块绘制之上；导出位图没有控件，因此由这里
    补上同样的白底、强调色描边与居中文字。
    """
    painter.save()
    pen_width = 1.5 if enabled else 1.0
    inset = pen_width / 2.0
    box = box_rect.adjusted(inset, inset, -inset, -inset)
    painter.setBrush(_SURFACE if enabled else _SURFACE_ALT)
    painter.setPen(
        QPen(_accent(index) if enabled else _BORDER_STRONG, pen_width)
    )
    painter.drawRoundedRect(box, 6.0, 6.0)
    painter.setFont(_value_font())
    painter.setPen(_TEXT if enabled else _DISABLED_TEXT)
    painter.drawText(box_rect, int(Qt.AlignCenter), text)
    painter.restore()


def _paint_protection(
    painter: QPainter,
    label_rect: QRectF,
    indicator_rect: QRectF,
    label: str,
    checked: bool,
    index: int,
    enabled: bool,
    *,
    hovered: bool,
    focused: bool,
) -> None:
    """一个保护勾选框：左侧方块 + 右侧文字；关闭通道时整组降到禁用色。

    方块用通道强调色填满表示已勾选，未勾选只画描边；悬停只提亮描边，焦点在方块
    外侧画一圈主色框，键盘用户能看出当前停在哪个勾选框上。
    """
    painter.save()
    accent = _accent(index)
    text_color = _TEXT if enabled else _DISABLED_TEXT
    if checked:
        fill = accent if enabled else _BORDER_STRONG
        border = fill
    else:
        fill = _SURFACE if enabled else _SURFACE_ALT
        border = accent if hovered and enabled else _BORDER_STRONG
    pen_width = 1.5 if enabled else 1.0
    inset = pen_width / 2.0
    box = indicator_rect.adjusted(inset, inset, -inset, -inset)
    painter.setBrush(fill)
    painter.setPen(QPen(border, pen_width))
    painter.drawRoundedRect(box, 3.0, 3.0)

    if checked:
        tick = QPainterPath(
            QPointF(
                indicator_rect.left() + indicator_rect.width() * 0.26,
                indicator_rect.top() + indicator_rect.height() * 0.52,
            )
        )
        tick.lineTo(
            QPointF(
                indicator_rect.left() + indicator_rect.width() * 0.43,
                indicator_rect.top() + indicator_rect.height() * 0.70,
            )
        )
        tick.lineTo(
            QPointF(
                indicator_rect.left() + indicator_rect.width() * 0.75,
                indicator_rect.top() + indicator_rect.height() * 0.33,
            )
        )
        painter.setBrush(Qt.NoBrush)
        painter.setPen(
            QPen(
                QColor("#FFFFFF") if enabled else _SURFACE,
                1.9,
                Qt.SolidLine,
                Qt.RoundCap,
                Qt.RoundJoin,
            )
        )
        painter.drawPath(tick)

    if focused:
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_PRIMARY, 1.5))
        painter.drawRoundedRect(
            indicator_rect.adjusted(-2.5, -2.5, 2.5, 2.5), 4.0, 4.0
        )

    painter.setFont(_protection_font())
    painter.setPen(text_color)
    painter.drawText(
        label_rect,
        int(Qt.AlignLeft | Qt.AlignVCenter),
        label,
    )
    painter.restore()


def _paint_calibration_toggle(
    painter: QPainter,
    rect: QRectF,
    *,
    marked: bool,
    enabled: bool,
    index: int,
    progress: float | None = None,
    hovered: bool = False,
    focused: bool = False,
    pressed: bool = False,
) -> None:
    """``Cal.`` 效率计算标记：白底细边的圆角小开关，按下后填色并发光。

    ``progress`` 是 0–1 的显示进度：控件切换时由卡片在 240 ms 内插值，导出位图
    不传时直接取终态。进度进行中会带一点轻微放大再回落，颜色、描边与辉光强度
    都按同一个进度连续变化；通道 ``OFF`` 时开关禁用，但已经标记的状态仍以灰阶
    保留，重新打开通道后恢复强调色。
    """
    value = 1.0 if marked else 0.0
    if progress is not None:
        value = min(1.0, max(0.0, float(progress)))
    accent = _accent(index) if enabled else _BORDER_STRONG
    idle_fill = _SURFACE if enabled else _SURFACE_ALT
    hover_fill = _mix_color(idle_fill, accent, 0.12)
    active_fill = accent if enabled else _BORDER_STRONG
    idle_text = accent if hovered and enabled else (
        _TEXT_MUTED if enabled else _DISABLED_TEXT
    )
    active_text = QColor("#FFFFFF") if enabled else _SURFACE
    idle_border = accent if hovered and enabled else _BORDER_STRONG
    active_border = _mix_color(accent, QColor("#FFFFFF"), 0.28)
    fill = hover_fill if pressed and value < 0.5 else idle_fill
    fill_top = _mix_color(fill, active_fill.lighter(112), value)
    fill_bottom = _mix_color(fill, active_fill.darker(112), value)
    border = _mix_color(idle_border, active_border, value)
    text_color = _mix_color(idle_text, active_text, value)
    pen_width = 1.0 + 0.3 * (value if enabled else 0.0)
    pulse = math.sin(math.pi * value) if 0.0 < value < 1.0 else 0.0
    scale = 1.0 + 0.05 * pulse
    if pressed:
        scale *= 0.97

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    if abs(scale - 1.0) > 1e-6:
        painter.translate(rect.center())
        painter.scale(scale, scale)
        painter.translate(-rect.center())

    # 外圈辉光：只随标记进度出现，越接近启用越亮，不额外画常驻阴影。
    if value > 0.01 and enabled:
        for grow, alpha in ((1.4, 0.20), (2.8, 0.12), (4.2, 0.06)):
            glow = _rounded_rect(
                rect.adjusted(-grow, -grow, grow, grow),
                _CALIBRATION_RADIUS + grow,
            )
            painter.fillPath(glow, _with_alpha(accent, alpha * value))

    inset = pen_width / 2.0
    body = rect.adjusted(inset, inset, -inset, -inset)
    path = _rounded_rect(body, _CALIBRATION_RADIUS - inset)
    gradient = QLinearGradient(body.topLeft(), body.bottomLeft())
    gradient.setColorAt(0.0, fill_top)
    gradient.setColorAt(1.0, fill_bottom)
    painter.fillPath(path, QBrush(gradient))
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(border, pen_width))
    painter.drawPath(path)

    # 启用态顶部一条很淡的高光，圆角小片的玻璃质感与滚筒同一族。
    if value > 0.01 and enabled:
        painter.save()
        painter.setClipPath(path)
        highlight = QRectF(body.left(), body.top(), body.width(), 1.0)
        painter.fillRect(highlight, _with_alpha(QColor("#FFFFFF"), 0.30 * value))
        painter.restore()

    if focused:
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_PRIMARY, 1.5))
        painter.drawRoundedRect(
            rect.adjusted(-2.5, -2.5, 2.5, 2.5),
            _CALIBRATION_RADIUS + 2.0,
            _CALIBRATION_RADIUS + 2.0,
        )

    painter.setFont(_calibration_font())
    painter.setPen(text_color)
    painter.drawText(rect, int(Qt.AlignCenter), "Cal.")
    painter.restore()


def _mix_color(start: QColor, end: QColor, amount: float) -> QColor:
    """按 0–1 的比例混合两种颜色；比例越界时夹到端点。"""
    ratio = min(1.0, max(0.0, float(amount)))
    return QColor.fromRgbF(
        start.redF() + (end.redF() - start.redF()) * ratio,
        start.greenF() + (end.greenF() - start.greenF()) * ratio,
        start.blueF() + (end.blueF() - start.blueF()) * ratio,
        start.alphaF() + (end.alphaF() - start.alphaF()) * ratio,
    )


def _with_alpha(color: QColor, alpha: float) -> QColor:
    """同一颜色换一个 0–1 的透明度，用于逐层叠出辉光与内阴影。"""
    tinted = QColor(color)
    tinted.setAlphaF(min(1.0, max(0.0, float(alpha))))
    return tinted


def _selector_label_font(active: bool) -> QFont:
    """滚筒档位文字：当前档更大更重，邻档缩小；字距收紧接近参考的 Inter。"""
    font = QFont(card_status_font())
    font.setPointSizeF(9.0 if active else 6.0)
    font.setWeight(QFont.Weight.Black if active else QFont.Weight.DemiBold)
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.2)
    return font


def _paint_mode_selector(
    painter: QPainter,
    rect: QRectF,
    mode: str,
    *,
    position: float | None = None,
    hovered: bool = False,
    focused: bool = False,
    pressed: bool = False,
    driver_available: bool = True,
) -> None:
    """竖直老虎机滚筒：OFF / DUT / Driver 三档上下滚动。

    外观按参考实现复刻：深色槽体、金属拉丝底、红色指示点、玻璃高光、内阴影。
    参考里三档绕圆心旋转，这里改成竖直方向的滚筒——当前档居中放大、白色发光，
    上下档缩小、变暗并柔化，滚动时连续插值。``position`` 是显示顺序的连续档位
    （0=OFF，1=DUT，2=Driver），不传时取 ``mode`` 的终态；导出位图因此总是画最终
    样子。点击取下一档、滚筒向上滚一档，因此显示顺序就是 :data:`CHANNEL_MODES`
    的顺序：OFF → DUT → Driver → OFF。档位文字不带编号，只占一行、垂直居中，
    并整体向左侧红点靠拢。``driver_available=False`` 时 Driver 档被划掉，循环
    也会跳过它。
    """
    if mode not in CHANNEL_MODES:
        raise ValueError(
            f"通道角色必须是 {' / '.join(CHANNEL_MODES)}，收到 {mode!r}"
        )
    index = CHANNEL_MODES.index(mode)
    position = float(index) if position is None else float(position)
    slot = rect.adjusted(0.75, 0.75, -0.75, -0.75)
    radius = _SELECTOR_RADIUS
    path = _rounded_rect(slot, radius)

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)

    # 卡面上的柔和外阴影：滚筒是深色实体，需要一点落影才不像贴纸。
    for grow, alpha in ((1.5, 0.12), (3.0, 0.07), (4.5, 0.035)):
        shadow = _rounded_rect(
            rect.adjusted(-grow, -grow + 1.0, grow, grow + 1.0),
            radius + grow,
        )
        painter.fillPath(shadow, _with_alpha(QColor("#0F172A"), alpha))

    body = QLinearGradient(slot.topLeft(), slot.bottomLeft())
    body.setColorAt(0.0, _SELECTOR_BODY.lighter(118))
    body.setColorAt(0.45, _SELECTOR_BODY)
    body.setColorAt(1.0, _SELECTOR_BODY_BOTTOM)
    painter.fillPath(path, QBrush(body))

    painter.save()
    painter.setClipPath(path)
    # 金属拉丝：参考实现是右侧一个重复锥形渐变的圆盘，这里用同心的锥形色带
    # 从槽体右侧扫过，只保留很低的对比度，避免抢走档位文字。
    disc_center = QPointF(slot.right() + slot.width() * 0.55, slot.center().y())
    cone = QConicalGradient(disc_center, 0.0)
    for step in range(36):
        tone = _SELECTOR_METAL if step % 2 == 0 else _SELECTOR_METAL_DARK
        cone.setColorAt(step / 36.0, tone)
    cone.setColorAt(1.0, _SELECTOR_METAL)
    painter.setOpacity(0.55)
    painter.fillRect(slot, QBrush(cone))
    painter.setOpacity(1.0)

    # 上下边缘的暗角：档位在进出槽体时逐渐没入黑暗，像真的滚筒。
    edge = QLinearGradient(slot.topLeft(), slot.bottomLeft())
    edge.setColorAt(0.0, QColor(0, 0, 0, 215))
    edge.setColorAt(0.18, QColor(0, 0, 0, 0))
    edge.setColorAt(0.82, QColor(0, 0, 0, 0))
    edge.setColorAt(1.0, QColor(0, 0, 0, 215))
    painter.fillRect(slot, QBrush(edge))

    # 三档沿竖直方向排布；position 连续变化时逐帧插值。用 -2..4 覆盖滚动时的
    # 上下越界档位，取模后仍然只画三档，因此 2→0 的循环没有视觉跳跃。
    if pressed:
        painter.translate(0.0, 0.8)
    row_height = _SELECTOR_ROW_HEIGHT
    # 文字整体向左侧红点靠拢：中心只比槽体中轴偏右 1 像素，左边缘贴近指示点辉光。
    text_center_x = slot.center().x() + 1.0
    text_width = slot.width() - 22.0
    for step in range(-2, 5):
        step_mode = CHANNEL_MODES[step % len(CHANNEL_MODES)]
        offset = step - position
        distance = abs(offset)
        if distance > 2.25:
            continue
        emphasis = max(0.0, 1.0 - distance)
        active = emphasis > 0.55
        alpha = 0.10 + 0.90 * (emphasis ** 1.45)
        if step_mode == "driver" and not driver_available:
            alpha *= 0.28
        scale_x = 1.0 - 0.08 * min(distance, 2.0)
        scale_y = 1.0 - 0.20 * min(distance, 2.0)
        center_y = slot.center().y() + offset * row_height
        if abs(center_y - slot.center().y()) > slot.height() * 0.62:
            continue

        painter.save()
        painter.translate(text_center_x, center_y)
        painter.scale(scale_x, scale_y)
        block = QRectF(-text_width / 2.0, -row_height / 2.0, text_width, row_height)

        # 档位文字不再带 01/02/03 编号，整行只放一个标签并垂直居中。
        label = CHANNEL_MODE_LABELS[step_mode]
        label_font = _selector_label_font(active)
        painter.setFont(label_font)
        label_rect = block
        label_color = _SELECTOR_TEXT if active else _SELECTOR_TEXT_IDLE
        if active:
            # 当前档的柔光：参考实现的 text-shadow: 0 0 20px rgba(255,255,255,.2)。
            painter.setOpacity(alpha * 0.18)
            painter.setPen(label_color)
            for dx, dy in ((0.0, 1.4), (1.2, 0.0), (-1.2, 0.0)):
                painter.drawText(
                    label_rect.translated(dx, dy),
                    int(Qt.AlignHCenter | Qt.AlignVCenter),
                    label,
                )
        # 邻档用多次微偏移叠出柔化；当前档只画一次，保持锐利。
        copies = 1 if active else 3
        painter.setPen(label_color)
        for copy in range(copies):
            painter.setOpacity(
                alpha * (1.0 if active else 0.42)
            )
            dx = 0.0 if copies == 1 else (copy - 1) * 0.8
            painter.drawText(
                label_rect.translated(dx, 0.0),
                int(Qt.AlignHCenter | Qt.AlignVCenter),
                label,
            )
        if step_mode == "driver" and not driver_available:
            painter.setOpacity(0.75)
            painter.setPen(QPen(_SELECTOR_TEXT_IDLE, 1.0))
            strike_y = block.center().y()
            painter.drawLine(
                QPointF(block.left() + block.width() * 0.14, strike_y),
                QPointF(block.left() + block.width() * 0.86, strike_y),
            )
        painter.restore()

    # 玻璃高光：135° 白色高光 → 透明 → 右下压暗，压在文字和暗角之上。
    glass = QLinearGradient(slot.topLeft(), slot.bottomRight())
    glass.setColorAt(0.0, QColor(255, 255, 255, 30))
    glass.setColorAt(0.50, QColor(255, 255, 255, 0))
    glass.setColorAt(1.0, QColor(0, 0, 0, 70))
    painter.fillPath(path, QBrush(glass))
    painter.restore()

    painter.setBrush(Qt.NoBrush)
    painter.setPen(
        QPen(
            _SELECTOR_BORDER_HOVER if hovered else _SELECTOR_BORDER,
            1.4,
        )
    )
    painter.drawPath(_rounded_rect(slot, radius))

    # 左侧红色指示点：参考实现 ::before 的 6px 圆点 + 三层辉光。
    dot = QPointF(slot.left() + 10.0, slot.center().y())
    for glow_radius, alpha in ((9.0, 0.10), (6.0, 0.18), (4.0, 0.32)):
        glow = QRadialGradient(dot, glow_radius)
        glow.setColorAt(0.0, _with_alpha(_SELECTOR_ACCENT, alpha))
        glow.setColorAt(1.0, _with_alpha(_SELECTOR_ACCENT, 0.0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(glow))
        painter.drawEllipse(dot, glow_radius, glow_radius)
    painter.setBrush(_SELECTOR_ACCENT)
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(dot, 2.2, 2.2)
    painter.setBrush(_with_alpha(QColor("#FFFFFF"), 0.75))
    painter.drawEllipse(dot - QPointF(0.6, 0.6), 0.7, 0.7)

    if focused:
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(_SELECTOR_FOCUS, 1.4))
        painter.drawPath(
            _rounded_rect(rect.adjusted(2.0, 2.0, -2.0, -2.0), radius - 1.0)
        )
    painter.restore()


def _paint_power_card(
    painter: QPainter,
    data: PowerSupplyCardData,
    *,
    reel_positions: Mapping[str, float] | None = None,
    hover_selector: str | None = None,
    focus_selector: str | None = None,
    pressed_selector: str | None = None,
    driver_available: bool = True,
    hover_protection: tuple[str, str] | None = None,
    focus_protection: tuple[str, str] | None = None,
    calibration_progress: Mapping[str, float] | None = None,
    hover_calibration: str | None = None,
    focus_calibration: str | None = None,
    pressed_calibration: str | None = None,
) -> None:
    """把整张卡画到 ``painter`` 上；控件与导出位图共用这一份绘制。

    ``reel_positions`` 是“通道 -> 显示顺序的连续档位（0=OFF，1=DUT，2=Driver）”，控件用它播
    700 ms 的滚筒滚动；不传时取数据的终态，导出位图因此总是画最终样子。
    ``calibration_progress`` 同理是“通道 -> 0–1 的标记进度”，不传时按数据终态。
    """
    layout = power_card_layout()
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    painter.setClipPath(_rounded(layout.card))
    painter.fillPath(_rounded(layout.card), _SURFACE)

    for index, channel_layout in enumerate(layout.channels):
        channel = data.channels[index]
        _paint_channel_panel(painter, channel_layout, index, channel.enabled)

    # 两块色块之间是卡片白底（白缝），不再画中央分隔线；顶部正中由设备码铭牌盖住。

    _paint_title(painter, data, layout.title)

    for index, channel_layout in enumerate(layout.channels):
        channel = data.channels[index]
        _paint_mode_selector(
            painter,
            channel_layout.mode_selector,
            channel.mode,
            position=(reel_positions or {}).get(channel.channel),
            hovered=hover_selector == channel.channel,
            focused=focus_selector == channel.channel,
            pressed=pressed_selector == channel.channel,
            driver_available=driver_available,
        )
        _paint_calibration_toggle(
            painter,
            channel_layout.calibration_toggle,
            marked=channel.efficiency_calibration_enabled,
            enabled=channel.enabled,
            index=index,
            progress=(calibration_progress or {}).get(channel.channel),
            hovered=hover_calibration == channel.channel,
            focused=focus_calibration == channel.channel,
            pressed=pressed_calibration == channel.channel,
        )
        values = (channel.voltage_set, channel.current_set)
        for value_index, (label_rect, box_rect) in enumerate(
            zip(channel_layout.value_labels, channel_layout.value_boxes)
        ):
            _paint_value(
                painter,
                label_rect,
                box_rect,
                _VALUE_TEXTS[value_index],
                values[value_index],
                index,
                channel.enabled,
            )
        for kind_index, kind in enumerate(PROTECTION_KINDS):
            _paint_protection(
                painter,
                channel_layout.protection_labels[kind_index],
                channel_layout.protection_indicators[kind_index],
                _PROTECTION_TEXTS[kind_index],
                channel.protection_enabled(kind),
                index,
                channel.enabled,
                hovered=hover_protection == (channel.channel, kind),
                focused=focus_protection == (channel.channel, kind),
            )
        # 最下面一行的两个标签；下拉框与间隔输入框是真实子控件，自己绘制框体。
        painter.setFont(_protection_font())
        painter.setPen(_TEXT if channel.enabled else _DISABLED_TEXT)
        for label_rect, text in zip(channel_layout.power_labels, ("顺序", "间隔")):
            painter.drawText(label_rect, int(Qt.AlignCenter), text)
        order_text = (
            str(channel.power_order)
            if channel.power_order is not None
            else "—"
        )
        for box_rect, text in zip(
            channel_layout.power_controls,
            (order_text, f"{channel.power_delay_s:.2f}"),
        ):
            _paint_power_setting(
                painter, box_rect, text, index, channel.enabled
            )

    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(_BORDER, 1.0))
    painter.drawPath(_rounded(layout.card, 0.5))
    painter.restore()


def _device_pixel_ratio(value: float) -> float:
    """屏幕像素比规整成正的有限数；拿不到时按 1.0 处理（只影响清晰度）。"""
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(ratio) or ratio <= 0.0:
        return 1.0
    return ratio


def render_power_card(
    data: PowerSupplyCardData,
    *,
    device_pixel_ratio: float = 1.0,
    driver_available: bool = True,
) -> QPixmap:
    """按一份卡片数据渲染完整正面（透明底，不含投影），用于预览或静态导出。

    逻辑尺寸始终是 :data:`POWER_CARD_WIDTH` × :data:`POWER_CARD_HEIGHT`；高 DPI 只
    放大物理像素。``driver_available`` 控制 Driver 档是否可读（只影响滚筒外观，
    不改数据）。必须在 GUI 线程、创建 ``QApplication`` 之后调用。
    """
    if not isinstance(data, PowerSupplyCardData):
        raise TypeError(f"data 必须是 PowerSupplyCardData，收到 {data!r}")
    ratio = _device_pixel_ratio(device_pixel_ratio)
    pixmap = QPixmap(
        max(1, round(POWER_CARD_WIDTH * ratio)),
        max(1, round(POWER_CARD_HEIGHT * ratio)),
    )
    pixmap.fill(Qt.transparent)
    pixmap.setDevicePixelRatio(ratio)
    painter = QPainter(pixmap)
    try:
        _paint_power_card(painter, data, driver_available=driver_available)
    finally:
        painter.end()
    return pixmap


# ---- 控件 ------------------------------------------------------------------


class ChannelModeSelector(QWidget):
    """通道供电角色滚筒的透明命中区：外观由父卡片统一绘制。

    控件保留 Qt 的键盘焦点与无障碍语义；点击一次按
    ``OFF → DUT → Driver → OFF`` 循环，``Space`` / ``Enter`` 同样循环。滚轮不切换
    档位——它留给页面滚动，避免鼠标经过卡片时误改配置。控件本身不画像素，卡面
    因此仍由 :func:`_paint_power_card` 这一份绘制决定。
    """

    cycle_requested = Signal(str)
    hover_changed = Signal(str, bool)
    focus_changed = Signal(str, bool)
    pressed_changed = Signal(str, bool)

    def __init__(self, channel: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if channel not in CHANNEL_NAMES:
            raise ValueError(f"通道必须是 {' / '.join(CHANNEL_NAMES)}，收到 {channel!r}")
        self.channel = channel
        self._pressed = False
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(f"通道 {channel} 供电角色")
        self.setAccessibleDescription(
            "点击循环 OFF、DUT、Driver；OFF 关闭通道，DUT 给功放供电，"
            "Driver 给驱放供电。切换会同步下方上电计划。"
        )

    def sizeHint(self) -> QSize:
        return QSize(round(_SELECTOR_WIDTH), round(_SELECTOR_HEIGHT))

    def paintEvent(self, event) -> None:
        # 外观由父卡片统一画；这里只接受事件，不留下任何控件自己的底色。
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._pressed = True
            self.pressed_changed.emit(self.channel, True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._pressed:
            self._pressed = False
            self.pressed_changed.emit(self.channel, False)
            if self.rect().contains(event.position().toPoint()):
                self.cycle_requested.emit(self.channel)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.isAutoRepeat():
            event.accept()
            return
        if event.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self.cycle_requested.emit(self.channel)
            event.accept()
            return
        super().keyPressEvent(event)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.hover_changed.emit(self.channel, True)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.hover_changed.emit(self.channel, False)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.focus_changed.emit(self.channel, True)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.focus_changed.emit(self.channel, False)


class ProtectionCheck(QAbstractButton):
    """保护勾选框的透明命中区：外观由父卡片按勾选状态绘制。

    与 :class:`ChannelModeSelector` 一样保留 Qt 的键盘焦点、``Space`` 激活与
    无障碍语义。``kind`` 只能是 ``voltage`` / ``current``；控件本身不画像素。
    """

    hover_changed = Signal(str, str, bool)
    focus_changed = Signal(str, str, bool)

    def __init__(
        self, channel: str, kind: str, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        if channel not in CHANNEL_NAMES:
            raise ValueError(f"通道必须是 {' / '.join(CHANNEL_NAMES)}，收到 {channel!r}")
        if kind not in PROTECTION_KINDS:
            raise ValueError(
                f"保护类型必须是 {' / '.join(PROTECTION_KINDS)}，收到 {kind!r}"
            )
        self.channel = channel
        self.kind = kind
        self.setCheckable(True)
        self.setAutoExclusive(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        label = "电压保护" if kind == "voltage" else "电流保护"
        self.setAccessibleName(f"通道 {channel} {label}")
        self.setAccessibleDescription("勾选后保护阈值自动取比设定值高 10% 的值")

    def sizeHint(self) -> QSize:
        return QSize(round(_CELL_WIDTH), round(_PROTECTION_ROW_HEIGHT))

    def paintEvent(self, event) -> None:
        # 外观由父卡片统一画；这里只接受事件，不留下任何控件自己的底色。
        event.accept()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.hover_changed.emit(self.channel, self.kind, True)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.hover_changed.emit(self.channel, self.kind, False)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.focus_changed.emit(self.channel, self.kind, True)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.focus_changed.emit(self.channel, self.kind, False)


class EfficiencyCalibrationToggle(QAbstractButton):
    """``Cal.`` 效率计算标记的透明命中区：外观由父卡片按标记状态绘制。

    与 :class:`ProtectionCheck` 一样是真实的 ``QAbstractButton``：保留键盘焦点、
    ``Space`` 激活与无障碍语义，控件本身不画像素。两个通道的标记互相独立，
    可以同时启用；它不改变通道开关或供电角色。
    """

    hover_changed = Signal(str, bool)
    focus_changed = Signal(str, bool)

    def __init__(self, channel: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if channel not in CHANNEL_NAMES:
            raise ValueError(f"通道必须是 {' / '.join(CHANNEL_NAMES)}，收到 {channel!r}")
        self.channel = channel
        self.setCheckable(True)
        self.setAutoExclusive(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(f"通道 {channel} 效率计算标记")
        self.setAccessibleDescription(
            "按下表示该通道参与效率计算；两个通道可以同时标记。"
            "标记不改变供电角色、上电顺序或通道开关。"
        )

    def sizeHint(self) -> QSize:
        return QSize(round(_CALIBRATION_WIDTH), round(_CALIBRATION_HEIGHT))

    def paintEvent(self, event) -> None:
        # 外观由父卡片统一画；这里只接受事件，不留下任何控件自己的底色。
        event.accept()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.hover_changed.emit(self.channel, True)

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.hover_changed.emit(self.channel, False)

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self.focus_changed.emit(self.channel, True)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.focus_changed.emit(self.channel, False)


def _valid_scale(value: Any) -> float:
    """缩放比例规整成正的有限数；拿不到时按 1.0 处理（只影响卡片显示尺寸）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(number) or number <= 0.0:
        return 1.0
    return number


def _scaled_geometry(rect: QRectF, scale: float) -> QRect:
    """把卡面逻辑矩形换算成控件几何；缩放后仍至少保留 1 像素命中区。"""
    return QRect(
        round(rect.x() * scale),
        round(rect.y() * scale),
        max(1, round(rect.width() * scale)),
        max(1, round(rect.height() * scale)),
    )


class CardValueField(KeyboardOnlyDoubleSpinBox):
    """卡面上的 VS / IS 输入框：外观与卡面数值框一致，只接受键盘输入。

    数值框直接用 Qt 的浮点输入能力（选中、输入、方向键、焦点），外观由样式表按卡面
    令牌画成白底细边、等宽加粗居中字。宽度不足以显示完整数字时仍可输入，显示与普通
    数值框一致。缩放时字号、边框宽度和圆角一起按比例调整，保证缩小后的卡片仍能操作。
    """

    def __init__(
        self,
        channel: str,
        kind: str,
        *,
        scale: float = 1.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if channel not in CHANNEL_NAMES:
            raise ValueError(f"通道必须是 {' / '.join(CHANNEL_NAMES)}，收到 {channel!r}")
        if kind not in VALUE_KINDS:
            raise ValueError(
                f"工作值类型必须是 {' / '.join(VALUE_KINDS)}，收到 {kind!r}"
            )
        self.channel = channel
        self.kind = kind
        self._scale = _valid_scale(scale)
        self.setObjectName("powerCardValue")
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setAlignment(Qt.AlignCenter)
        self.setDecimals(VALUE_DECIMALS)
        self.setRange(*VALUE_LIMITS[kind])
        self.setSingleStep(0.1)
        self.setKeyboardTracking(True)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        label = "VS 电压设定" if kind == "voltage" else "IS 电流设定"
        unit = "V" if kind == "voltage" else "A"
        self.setAccessibleName(f"通道 {channel} {label}")
        self.setAccessibleDescription(f"范围 0–{VALUE_LIMITS[kind][1]:g} {unit}，两位小数")
        self.setToolTip(
            f"{channel} {label}（{unit}）\n"
            f"范围 0–{VALUE_LIMITS[kind][1]:g}，两位小数；点击后可直接输入。"
        )
        self.set_scale(self._scale)

    def set_scale(self, scale: float) -> None:
        """按卡片缩放比例调整字体、边框和圆角。"""
        scale = _valid_scale(scale)
        self._scale = scale
        font = QFont(card_code_font())
        font.setPointSizeF(12.0 * scale)
        self.setFont(font)
        self.setStyleSheet(_value_field_style(self.channel, scale))


def _value_field_style(channel: str, scale: float) -> str:
    """数值框的卡面样式：与 ``_paint_value`` 的数值框同色同圆角，按缩放调整。"""
    accent = TOKENS["primary"] if channel == "CH1" else TOKENS["warning"]
    focus_background = (
        TOKENS["primary_soft"] if channel == "CH1" else TOKENS["warning_soft"]
    )
    border = max(1, round(1.5 * scale))
    radius = max(2, round(6.0 * scale))
    return (
        "QDoubleSpinBox {"
        f" background-color: {TOKENS['surface']};"
        f" border: {border}px solid {accent};"
        f" border-radius: {radius}px;"
        f" color: {TOKENS['text']};"
        f" font-family: \"{TOKENS['font_mono']}\";"
        f" font-size: {max(6, round(16.0 * scale))}px;"
        " font-weight: 700;"
        " padding: 0px;"
        " min-height: 0px;"
        f" selection-background-color: {TOKENS['primary']};"
        " selection-color: #FFFFFF;"
        "}"
        " QDoubleSpinBox:focus {"
        f" border: {border}px solid {accent};"
        f" background-color: {focus_background};"
        " padding: 0px;"
        " min-height: 0px;"
        " }"
        " QDoubleSpinBox:disabled {"
        f" background-color: {TOKENS['surface_alt']};"
        f" border: {border}px solid {TOKENS['border_strong']};"
        f" color: {TOKENS['disabled_text']};"
        " }"
        " QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {"
        " width: 0px; height: 0px; border: none; background: transparent;"
        " }"
    )


def _order_combo_style(channel: str, scale: float) -> str:
    """上电顺序下拉框：与通道数值框同色细边，按卡片缩放调整字号与圆角。"""
    accent = TOKENS["primary"] if channel == "CH1" else TOKENS["warning"]
    focus_background = (
        TOKENS["primary_soft"] if channel == "CH1" else TOKENS["warning_soft"]
    )
    border = max(1, round(1.5 * scale))
    radius = max(2, round(6.0 * scale))
    arrow = (ASSETS_DIR / "chevron-down.svg").as_posix()
    arrow_muted = (ASSETS_DIR / "chevron-down-muted.svg").as_posix()
    arrow_size = max(6, round(9.0 * scale))
    return (
        "QComboBox {"
        f" background-color: {TOKENS['surface']};"
        f" border: {border}px solid {accent};"
        f" border-radius: {radius}px;"
        f" color: {TOKENS['text']};"
        f" font-size: {max(7, round(13.0 * scale))}px;"
        " font-weight: 600;"
        " padding: 0px 13px 0px 4px;"
        " min-height: 0px;"
        "}"
        " QComboBox:focus {"
        f" border: {border}px solid {accent};"
        f" background-color: {focus_background};"
        " }"
        " QComboBox:disabled {"
        f" background-color: {TOKENS['surface_alt']};"
        f" border: {border}px solid {TOKENS['border_strong']};"
        f" color: {TOKENS['disabled_text']};"
        " }"
        " QComboBox::drop-down {"
        " width: 12px; border: none; background: transparent;"
        " }"
        " QComboBox::down-arrow {"
        f" image: url({arrow}); width: {arrow_size}px; height: {arrow_size}px;"
        " }"
        " QComboBox::down-arrow:disabled {"
        f" image: url({arrow_muted});"
        " }"
    )


class CardOrderCombo(WheelSafeComboBox):
    """卡面上的上电顺序下拉框：只列 1..N，滚轮不会误改顺序。"""

    def __init__(
        self,
        channel: str,
        *,
        scale: float = 1.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if channel not in CHANNEL_NAMES:
            raise ValueError(f"通道必须是 {' / '.join(CHANNEL_NAMES)}，收到 {channel!r}")
        self.channel = channel
        self._scale = _valid_scale(scale)
        self.setObjectName("powerCardOrder")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setAccessibleName(f"通道 {channel} 上电顺序")
        self.setAccessibleDescription(
            "选择本通道在上电计划里的位置；1 表示最先上电。"
            "只有参与上电的通道可以修改。"
        )
        self.set_scale(self._scale)

    def set_scale(self, scale: float) -> None:
        scale = _valid_scale(scale)
        self._scale = scale
        font = QFont(card_status_font())
        font.setPointSizeF(9.0 * scale)
        font.setWeight(QFont.Weight.DemiBold)
        self.setFont(font)
        self.setStyleSheet(_order_combo_style(self.channel, scale))


class CardDelayField(KeyboardOnlyDoubleSpinBox):
    """卡面上的上电间隔输入框：单位秒，两位小数，只接受键盘输入。"""

    def __init__(
        self,
        channel: str,
        *,
        scale: float = 1.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if channel not in CHANNEL_NAMES:
            raise ValueError(f"通道必须是 {' / '.join(CHANNEL_NAMES)}，收到 {channel!r}")
        self.channel = channel
        self._scale = _valid_scale(scale)
        self.setObjectName("powerCardDelay")
        self.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.setAlignment(Qt.AlignCenter)
        self.setDecimals(2)
        self.setRange(0.0, MAX_INTER_CHANNEL_DELAY_S)
        self.setSingleStep(0.5)
        self.setKeyboardTracking(True)
        self.setMinimumSize(0, 0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setAccessibleName(f"通道 {channel} 上电间隔")
        self.setAccessibleDescription(
            f"范围 0–{MAX_INTER_CHANNEL_DELAY_S:g} 秒，两位小数；"
            "通道开启后等待这么久再发下一条上电命令。"
        )
        self.set_scale(self._scale)

    def set_scale(self, scale: float) -> None:
        scale = _valid_scale(scale)
        self._scale = scale
        font = QFont(card_code_font())
        font.setPointSizeF(11.0 * scale)
        self.setFont(font)
        self.setStyleSheet(_value_field_style(self.channel, scale))


class PowerSupplyCard(QWidget):
    """配置页的电源通道卡片：两个通道的角色滚筒、工作值与保护都在这张卡上编辑。

    每个通道贴外侧上角一个 84×40 的深色竖直滚筒，档位 ``OFF`` / ``DUT`` /
    ``Driver`` 点击循环；滚筒靠卡面中央的一侧还有 ``Cal.`` 效率计算标记，按下后
    填满通道强调色并发光。``OFF`` 时整块灰显、VS/IS、保护勾选、``Cal.``、顺序与
    间隔都不可操作；``DUT`` / ``Driver`` 时恢复通道底色并可编辑。档位变化只更新
    卡片内部快照并发信号，由页面同步到下方两份上电计划；卡片本身不写配置。

    滚筒、``Cal.`` 标记、两个数值框、四个保护勾选框以及最下面一行的顺序下拉框和
    间隔输入框都是可聚焦控件：``Tab`` 依次经过它们，``Space`` / ``Enter`` 循环滚筒
    档位、``Space`` 切换保护勾选或 ``Cal.`` 标记，数值框用键盘输入。卡片支持按比例
    缩放，缩放时所有子控件的几何与字号一起调整；绘制统一在缩放后的坐标系里完成。
    """

    channel_mode_changed = Signal(str, str)
    protection_toggled = Signal(str, str, bool)
    #: ``(channel, marked)``：效率计算标记被用户按下或取消。
    efficiency_calibration_toggled = Signal(str, bool)
    value_changed = Signal(str, str, float)
    #: ``(channel, order)``：用户在顺序下拉框里选了新的 1-based 位置。
    power_order_changed = Signal(str, int)
    #: ``(channel, seconds)``：上电间隔被修改。
    power_delay_changed = Signal(str, float)

    def __init__(
        self,
        data: PowerSupplyCardData,
        parent: QWidget | None = None,
        *,
        scale: float = 1.0,
    ) -> None:
        super().__init__(parent)
        if not isinstance(data, PowerSupplyCardData):
            raise TypeError(f"data 必须是 PowerSupplyCardData，收到 {data!r}")
        self._scale = _valid_scale(scale)
        self.setObjectName("powerSupplyCard")
        self.setFixedSize(
            QSize(
                max(1, round(POWER_CARD_WIDTH * self._scale)),
                max(1, round(POWER_CARD_HEIGHT * self._scale)),
            )
        )
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.NoFocus)
        self._data = data
        self._layout = power_card_layout()
        self._hover_selector: str | None = None
        self._focus_selector: str | None = None
        self._pressed_selector: str | None = None
        self._hover_protection: tuple[str, str] | None = None
        self._focus_protection: tuple[str, str] | None = None
        self._hover_calibration: str | None = None
        self._focus_calibration: str | None = None
        self._pressed_calibration: str | None = None
        self._driver_available = True
        # 每个通道的连续档位：0=OFF、1=DUT、2=Driver；用户换档时在 700 ms 内插值。
        self._reel_positions: dict[str, float] = {
            name: float(CHANNEL_MODES.index(data.channel(name).mode))
            for name in CHANNEL_NAMES
        }
        self._reel_animations: dict[str, QVariantAnimation] = {}
        self._reel_targets: dict[str, int] = {}
        #: 每个通道 ``Cal.`` 标记的显示进度（0=未标记，1=已标记）。
        self._calibration_progress: dict[str, float] = {
            name: 1.0
            if data.channel(name).efficiency_calibration_enabled
            else 0.0
            for name in CHANNEL_NAMES
        }
        self._calibration_animations: dict[str, QVariantAnimation] = {}

        self._selectors: dict[str, ChannelModeSelector] = {}
        self._calibration_toggles: dict[str, EfficiencyCalibrationToggle] = {}
        self._value_fields: dict[tuple[str, str], CardValueField] = {}
        self._protection_checks: dict[tuple[str, str], ProtectionCheck] = {}
        self._order_combos: dict[str, CardOrderCombo] = {}
        self._delay_fields: dict[str, CardDelayField] = {}

        for channel_layout in self._layout.channels:
            name = channel_layout.channel
            channel_data = data.channel(name)

            selector = ChannelModeSelector(name, self)
            selector.setGeometry(
                _scaled_geometry(channel_layout.mode_selector, self._scale)
            )
            selector.cycle_requested.connect(self._on_selector_cycle)
            selector.hover_changed.connect(self._on_selector_hover)
            selector.focus_changed.connect(self._on_selector_focus)
            selector.pressed_changed.connect(self._on_selector_pressed)
            self._selectors[name] = selector

            calibration = EfficiencyCalibrationToggle(name, self)
            calibration.setChecked(channel_data.efficiency_calibration_enabled)
            calibration.setEnabled(channel_data.enabled)
            calibration.setGeometry(
                _scaled_geometry(channel_layout.calibration_toggle, self._scale)
            )
            calibration.toggled.connect(
                lambda marked, channel=name: self._on_calibration_toggled(
                    channel, marked
                )
            )
            calibration.hover_changed.connect(self._on_calibration_hover)
            calibration.focus_changed.connect(self._on_calibration_focus)
            calibration.pressed.connect(
                lambda channel=name: self._on_calibration_pressed(channel, True)
            )
            calibration.released.connect(
                lambda channel=name: self._on_calibration_pressed(channel, False)
            )
            self._calibration_toggles[name] = calibration

            for kind, box_rect in zip(VALUE_KINDS, channel_layout.value_boxes):
                field = CardValueField(name, kind, scale=self._scale, parent=self)
                field.setValue(
                    channel_data.voltage_set
                    if kind == "voltage"
                    else channel_data.current_set
                )
                field.setEnabled(channel_data.enabled)
                field.setGeometry(_scaled_geometry(box_rect, self._scale))
                field.valueChanged.connect(
                    lambda value, channel=name, value_kind=kind: self._on_value_changed(
                        channel, value_kind, value
                    )
                )
                self._value_fields[(name, kind)] = field

            for kind, check_rect in zip(
                PROTECTION_KINDS, channel_layout.protection_checks
            ):
                check = ProtectionCheck(name, kind, self)
                check.setChecked(channel_data.protection_enabled(kind))
                check.setEnabled(channel_data.enabled)
                check.setGeometry(_scaled_geometry(check_rect, self._scale))
                check.toggled.connect(
                    lambda checked, channel=name, check_kind=kind: (
                        self._on_protection_toggled(channel, check_kind, checked)
                    )
                )
                check.hover_changed.connect(self._on_protection_hover)
                check.focus_changed.connect(self._on_protection_focus)
                self._protection_checks[(name, kind)] = check

            order_combo = CardOrderCombo(name, scale=self._scale, parent=self)
            order_combo.setGeometry(
                _scaled_geometry(channel_layout.power_controls[0], self._scale)
            )
            order_combo.currentIndexChanged.connect(
                lambda index, channel=name: self._on_power_order_selected(
                    channel, index
                )
            )
            self._order_combos[name] = order_combo

            delay_field = CardDelayField(name, scale=self._scale, parent=self)
            delay_field.setGeometry(
                _scaled_geometry(channel_layout.power_controls[1], self._scale)
            )
            delay_field.valueChanged.connect(
                lambda value, channel=name: self._on_power_delay_changed(
                    channel, value
                )
            )
            self._delay_fields[name] = delay_field
            self._apply_power_setting(name)

        # 焦点顺序按“角色滚筒 → Cal. 标记 → VS → IS → 电压保护 → 电流保护 → 顺序 → 间隔”
        # 逐通道向后。
        order: list[QWidget] = []
        for name in CHANNEL_NAMES:
            order.append(self._selectors[name])
            order.append(self._calibration_toggles[name])
            order.extend(self._value_fields[(name, kind)] for kind in VALUE_KINDS)
            order.extend(
                self._protection_checks[(name, kind)] for kind in PROTECTION_KINDS
            )
            order.append(self._order_combos[name])
            order.append(self._delay_fields[name])
        for previous, following in zip(order, order[1:]):
            self.setTabOrder(previous, following)
        self._update_tooltips()

    def set_scale(self, scale: float) -> None:
        """按卡片墙给出的比例缩放整张卡（子控件几何、字号与卡面绘制一起变）。"""
        scale = _valid_scale(scale)
        width = max(1, round(POWER_CARD_WIDTH * scale))
        height = max(1, round(POWER_CARD_HEIGHT * scale))
        if abs(scale - self._scale) <= 1e-6 and self.width() == width:
            return
        self._scale = scale
        self.setFixedSize(QSize(width, height))
        for channel_layout in self._layout.channels:
            name = channel_layout.channel
            self._selectors[name].setGeometry(
                _scaled_geometry(channel_layout.mode_selector, scale)
            )
            self._calibration_toggles[name].setGeometry(
                _scaled_geometry(channel_layout.calibration_toggle, scale)
            )
            for kind, box_rect in zip(VALUE_KINDS, channel_layout.value_boxes):
                field = self._value_fields[(name, kind)]
                field.set_scale(scale)
                field.setGeometry(_scaled_geometry(box_rect, scale))
            for kind, check_rect in zip(
                PROTECTION_KINDS, channel_layout.protection_checks
            ):
                self._protection_checks[(name, kind)].setGeometry(
                    _scaled_geometry(check_rect, scale)
                )
            order_combo = self._order_combos[name]
            order_combo.set_scale(scale)
            order_combo.setGeometry(
                _scaled_geometry(channel_layout.power_controls[0], scale)
            )
            delay_field = self._delay_fields[name]
            delay_field.set_scale(scale)
            delay_field.setGeometry(
                _scaled_geometry(channel_layout.power_controls[1], scale)
            )
        self.update()

    @property
    def scale(self) -> float:
        return self._scale

    # ---------- 读取 ----------

    def sizeHint(self) -> QSize:
        return QSize(
            max(1, round(POWER_CARD_WIDTH * self._scale)),
            max(1, round(POWER_CARD_HEIGHT * self._scale)),
        )

    @property
    def data(self) -> PowerSupplyCardData:
        """卡片此刻显示的快照（角色档位已随用户操作更新）。"""
        return self._data

    def channel_mode_selector(self, channel: str) -> ChannelModeSelector:
        """取某个通道的角色滚筒命中控件；名称不合法时抛 ``KeyError``。"""
        selector = self._selectors.get(channel)
        if selector is None:
            raise KeyError(f"卡片里没有通道 {channel!r}")
        return selector

    def channel_mode(self, channel: str) -> str:
        """某个通道此刻的供电角色：``off`` / ``driver`` / ``dut``。"""
        return self._data.channel(channel).mode

    def calibration_toggle(self, channel: str) -> EfficiencyCalibrationToggle:
        """取某个通道的 ``Cal.`` 效率计算标记控件；名称不合法时抛 ``KeyError``。"""
        toggle = self._calibration_toggles.get(channel)
        if toggle is None:
            raise KeyError(f"卡片里没有通道 {channel!r} 的 Cal. 标记")
        return toggle

    def channel_efficiency_calibration(self, channel: str) -> bool:
        """某个通道当前是否被标记为效率计算通道。"""
        return bool(self.calibration_toggle(channel).isChecked())

    def channel_enabled(self, channel: str) -> bool:
        """某个通道此刻是否处于开启状态（``driver`` / ``dut`` 都算开启）。"""
        return self.channel_mode(channel) != "off"

    def value_field(self, channel: str, kind: str) -> CardValueField:
        """取某个通道的 VS / IS 输入框；不合法时抛 ``KeyError``。"""
        field = self._value_fields.get((channel, kind))
        if field is None:
            raise KeyError(f"卡片里没有通道 {channel!r} 的 {kind!r} 输入框")
        return field

    def channel_value(self, channel: str, kind: str) -> float:
        """某个通道当前的电压/电流设定值。"""
        return float(self.value_field(channel, kind).value())

    def order_combo(self, channel: str) -> CardOrderCombo:
        """取某个通道的上电顺序下拉框；不合法时抛 ``KeyError``。"""
        combo = self._order_combos.get(channel)
        if combo is None:
            raise KeyError(f"卡片里没有通道 {channel!r} 的上电顺序下拉框")
        return combo

    def delay_field(self, channel: str) -> CardDelayField:
        """取某个通道的上电间隔输入框；不合法时抛 ``KeyError``。"""
        field = self._delay_fields.get(channel)
        if field is None:
            raise KeyError(f"卡片里没有通道 {channel!r} 的上电间隔输入框")
        return field

    def channel_power_order(self, channel: str) -> int | None:
        """某个通道当前选择的上电顺序（1-based）；不参与上电时为 ``None``。"""
        data = self.order_combo(channel).currentData()
        return data if isinstance(data, int) else None

    def channel_power_delay(self, channel: str) -> float:
        """某个通道当前的上电间隔（秒）。"""
        return float(self.delay_field(channel).value())

    def channel_controls(self, channel: str) -> dict[str, Any]:
        """一个通道的控件映射，形状与 :class:`ConfigFormState` 读取的表单一致。"""
        return {
            "voltage": self.value_field(channel, "voltage"),
            "current": self.value_field(channel, "current"),
            "voltage_protection_enabled": self.protection_check(channel, "voltage"),
            "current_protection_enabled": self.protection_check(channel, "current"),
            "power_order": self.order_combo(channel),
            "power_on_delay": self.delay_field(channel),
        }

    def protection_check(self, channel: str, kind: str) -> ProtectionCheck:
        """取某个通道某种保护的透明命中控件；不合法时抛 ``KeyError``。"""
        check = self._protection_checks.get((channel, kind))
        if check is None:
            raise KeyError(f"卡片里没有通道 {channel!r} 的 {kind!r} 保护勾选框")
        return check

    def channel_protection(self, channel: str, kind: str) -> bool:
        """某个通道某种保护此刻是否启用。"""
        return self.protection_check(channel, kind).isChecked()

    # ---------- 写入 ----------

    def set_data(self, data: PowerSupplyCardData) -> None:
        """整张卡换一份快照；不触发角色、保护或数值信号。"""
        if not isinstance(data, PowerSupplyCardData):
            raise TypeError(f"data 必须是 PowerSupplyCardData，收到 {data!r}")
        self._data = data
        for channel_layout in self._layout.channels:
            name = channel_layout.channel
            channel = data.channel(name)
            self._set_reel_position(name, CHANNEL_MODES.index(channel.mode))
            for kind, value in (
                ("voltage", channel.voltage_set),
                ("current", channel.current_set),
            ):
                field = self._value_fields[(name, kind)]
                if field.value() != value:
                    field.blockSignals(True)
                    field.setValue(value)
                    field.blockSignals(False)
                field.setEnabled(channel.enabled)
            for kind in PROTECTION_KINDS:
                check = self._protection_checks[(name, kind)]
                checked = channel.protection_enabled(kind)
                if check.isChecked() != checked:
                    check.blockSignals(True)
                    check.setChecked(checked)
                    check.blockSignals(False)
                check.setEnabled(channel.enabled)
            calibration = self._calibration_toggles[name]
            marked = channel.efficiency_calibration_enabled
            if calibration.isChecked() != marked:
                calibration.blockSignals(True)
                calibration.setChecked(marked)
                calibration.blockSignals(False)
            calibration.setEnabled(channel.enabled)
            self._set_calibration_progress(name, marked)
            self._apply_power_setting(name)
        self._update_tooltips()
        self.update()

    def set_channel_power(
        self,
        channel: str,
        order: int | None,
        count: int,
        delay: Any,
        *,
        emit: bool = False,
    ) -> None:
        """程序化设置某个通道的上电顺序/顺序总数/间隔；默认不发信号。"""
        self._data = self._data.with_channel_power(channel, order, count, delay)
        self._apply_power_setting(channel)
        self._update_tooltips()
        self.update()
        if emit:
            if order is not None:
                self.power_order_changed.emit(channel, order)
            self.power_delay_changed.emit(channel, float(self.channel_power_delay(channel)))

    def _apply_power_setting(self, channel: str) -> None:
        """把当前快照里的顺序选项与间隔写进控件；不读配置、不发信号。"""
        setting = self._data.channel(channel)
        order = setting.power_order
        count = setting.power_order_count
        delay = setting.power_delay_s
        combo = self._order_combos[channel]
        field = self._delay_fields[channel]
        combo.blockSignals(True)
        combo.clear()
        if count > 0:
            for value in range(1, count + 1):
                combo.addItem(str(value), value)
            index = (order - 1) if order is not None else 0
            combo.setCurrentIndex(max(0, min(count - 1, index)))
        else:
            combo.addItem("—", None)
            combo.setCurrentIndex(0)
        combo.blockSignals(False)
        enabled = self._data.channel(channel).enabled
        combo.setEnabled(enabled and count > 0)
        if field.value() != float(delay):
            field.blockSignals(True)
            field.setValue(float(delay))
            field.blockSignals(False)
        field.setEnabled(enabled)

    def _on_power_order_selected(self, channel: str, index: int) -> None:
        """顺序下拉框被用户改选：更新快照并转发给页面重排计划。"""
        order = self._order_combos[channel].itemData(index)
        if not isinstance(order, int):
            return
        current = self._data.channel(channel)
        self._data = self._data.with_channel_power(
            channel, order, current.power_order_count, current.power_delay_s
        )
        self._update_tooltips()
        self.power_order_changed.emit(channel, order)

    def _on_power_delay_changed(self, channel: str, value: Any) -> None:
        """间隔输入框被用户修改：更新快照并计入页面 dirty。"""
        current = self._data.channel(channel)
        self._data = self._data.with_channel_power(
            channel,
            current.power_order,
            current.power_order_count,
            value,
        )
        self._update_tooltips()
        self.power_delay_changed.emit(channel, float(value))

    def set_channel_mode(
        self,
        channel: str,
        mode: str,
        *,
        emit: bool = False,
        animate: bool = True,
    ) -> None:
        """程序化设置某个通道的供电角色；默认不播动画、不发信号。"""
        if mode not in CHANNEL_MODES:
            raise ValueError(
                f"通道角色必须是 {' / '.join(CHANNEL_MODES)}，收到 {mode!r}"
            )
        self.channel_mode_selector(channel)
        if self._data.channel(channel).mode != mode:
            self._data = self._data.with_channel_mode(channel, mode)
            self._apply_channel_mode(channel, mode, animate=animate)
        if emit:
            self.channel_mode_changed.emit(channel, mode)

    def set_channel_enabled(
        self,
        channel: str,
        enabled: bool,
        *,
        emit: bool = False,
    ) -> None:
        """兼容旧调用：``True`` 保留已有角色（原本是 off 时给 ``driver``）。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        current = self._data.channel(channel).mode
        if not enabled:
            mode = "off"
        elif current == "off":
            mode = "driver"
        else:
            mode = current
        self.set_channel_mode(channel, mode, emit=emit)

    def set_driver_available(self, available: bool) -> None:
        """驱放测试模式关闭时把 Driver 档从循环里拿掉，并清掉已选 Driver。"""
        available = bool(available)
        self._driver_available = available
        if not available:
            for name in CHANNEL_NAMES:
                if self._data.channel(name).mode == "driver":
                    self.set_channel_mode(name, "off", animate=False)
        self._update_tooltips()
        self.update()

    def reset_channel_modes(self) -> None:
        """把所有通道回到 OFF；不写配置、不发信号。"""
        for name in CHANNEL_NAMES:
            self.set_channel_mode(name, "off", animate=False)

    def reset_channel_switches(self) -> None:
        """兼容旧名字：把所有通道回到 OFF。"""
        self.reset_channel_modes()

    def set_value(
        self,
        channel: str,
        kind: str,
        value: Any,
        *,
        emit: bool = False,
    ) -> None:
        """程序化设置某个通道的工作值；默认不触发 ``value_changed``。"""
        field = self.value_field(channel, kind)
        number = _finite_non_negative(value, f"{channel} {kind}")
        if field.value() != number:
            field.blockSignals(True)
            field.setValue(number)
            field.blockSignals(False)
            self._data = self._data.with_channel_value(channel, kind, number)
            self._update_tooltips()
            self.update()
        if emit:
            self.value_changed.emit(channel, kind, number)

    def set_protection_enabled(
        self,
        channel: str,
        kind: str,
        enabled: bool,
        *,
        emit: bool = False,
    ) -> None:
        """程序化设置某个保护勾选框；``emit=True`` 时仍发一次信号。"""
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        check = self.protection_check(channel, kind)
        if check.isChecked() != enabled:
            check.blockSignals(True)
            check.setChecked(enabled)
            check.blockSignals(False)
            self._data = self._data.with_channel_protection(
                channel, kind, enabled
            )
            self._update_tooltips()
            self.update()
        if emit:
            self.protection_toggled.emit(channel, kind, enabled)

    def set_efficiency_calibration(
        self,
        channel: str,
        marked: bool,
        *,
        emit: bool = False,
        animate: bool = False,
    ) -> None:
        """程序化设置某个通道的效率计算标记；默认直接到终态、不发信号。"""
        if not isinstance(marked, bool):
            raise TypeError("marked 必须是 bool")
        toggle = self.calibration_toggle(channel)
        if toggle.isChecked() != marked:
            toggle.blockSignals(True)
            toggle.setChecked(marked)
            toggle.blockSignals(False)
            self._data = self._data.with_channel_efficiency_calibration(
                channel, marked
            )
            if animate and self.isVisible():
                self._animate_calibration(channel, marked)
            else:
                self._set_calibration_progress(channel, marked)
            self._update_tooltips()
            self.update()
        if emit:
            self.efficiency_calibration_toggled.emit(channel, marked)

    # ---------- 滚筒动画 ----------

    def _set_reel_position(self, channel: str, index: int) -> None:
        """把某个通道的滚筒直接定到某一档；程序化路径不播滚动动画。"""
        animation = self._reel_animations.get(channel)
        if animation is not None:
            animation.stop()
        self._reel_positions[channel] = float(index)
        self.update()

    def _animate_reel(self, channel: str, mode: str) -> None:
        """用户换档后按参考实现的 700 ms / OutExpo 滚动，方向始终向上。"""
        target_index = CHANNEL_MODES.index(mode)
        current = self._reel_positions.get(channel, float(target_index))
        count = len(CHANNEL_MODES)
        # 显示顺序与点击顺序一致（OFF → DUT → Driver）；滚筒向上滚一档，
        # 位置加一，下一档从槽体下方进入中心。
        delta = (target_index - (current % count)) % count
        if delta < 1e-6:
            self._set_reel_position(channel, target_index)
            return
        target_raw = current + delta

        animation = self._reel_animations.get(channel)
        if animation is None:
            animation = QVariantAnimation(self)
            animation.setDuration(700)
            animation.setEasingCurve(QEasingCurve.OutExpo)
            animation.valueChanged.connect(
                lambda value, name=channel: self._on_reel_value(name, value)
            )
            animation.finished.connect(
                lambda name=channel: self._on_reel_finished(name)
            )
            self._reel_animations[channel] = animation
        self._reel_targets[channel] = target_index
        animation.stop()
        animation.setStartValue(current)
        animation.setEndValue(target_raw)
        animation.start()

    def _on_reel_value(self, channel: str, value: Any) -> None:
        self._reel_positions[channel] = float(value)
        self.update()

    def _on_reel_finished(self, channel: str) -> None:
        target = self._reel_targets.get(channel, 0)
        self._reel_positions[channel] = float(target)
        self.update()

    # ---------- Cal. 标记动画 ----------

    def _set_calibration_progress(self, channel: str, marked: bool) -> None:
        """把某个通道的标记进度直接定到终态；程序化回填不播动画。"""
        animation = self._calibration_animations.get(channel)
        if animation is not None:
            animation.stop()
        self._calibration_progress[channel] = 1.0 if marked else 0.0
        self.update()

    def _animate_calibration(self, channel: str, marked: bool) -> None:
        """用户按下标记后 240 ms 内连续完成颜色填充与轻微弹跳。"""
        target = 1.0 if marked else 0.0
        current = self._calibration_progress.get(channel, target)
        if abs(current - target) < 1e-6:
            self._set_calibration_progress(channel, marked)
            return
        animation = self._calibration_animations.get(channel)
        if animation is None:
            animation = QVariantAnimation(self)
            animation.setDuration(240)
            animation.setEasingCurve(QEasingCurve.OutCubic)
            animation.valueChanged.connect(
                lambda value, name=channel: self._on_calibration_value(name, value)
            )
            animation.finished.connect(
                lambda name=channel: self._on_calibration_finished(name)
            )
            self._calibration_animations[channel] = animation
        animation.stop()
        animation.setStartValue(current)
        animation.setEndValue(target)
        animation.start()

    def _on_calibration_value(self, channel: str, value: Any) -> None:
        self._calibration_progress[channel] = float(value)
        self.update()

    def _on_calibration_finished(self, channel: str) -> None:
        marked = self._data.channel(channel).efficiency_calibration_enabled
        self._calibration_progress[channel] = 1.0 if marked else 0.0
        self.update()

    # ---------- 滚筒事件 ----------

    def _on_selector_cycle(self, channel: str) -> None:
        """点击档位：按 OFF → DUT → Driver → OFF 循环；Driver 禁用时跳过。"""
        available = [
            mode
            for mode in CHANNEL_MODES
            if mode != "driver" or self._driver_available
        ]
        current = self._data.channel(channel).mode
        current_index = available.index(current) if current in available else -1
        next_mode = available[(current_index + 1) % len(available)]
        self.set_channel_mode(channel, next_mode, emit=True, animate=True)

    def _apply_channel_mode(self, channel: str, mode: str, *, animate: bool) -> None:
        """同步卡面快照与子控件的可用状态（不发信号）。"""
        enabled = mode != "off"
        for kind in VALUE_KINDS:
            self._value_fields[(channel, kind)].setEnabled(enabled)
        for kind in PROTECTION_KINDS:
            self._protection_checks[(channel, kind)].setEnabled(enabled)
        self._calibration_toggles[channel].setEnabled(enabled)
        count = self._data.channel(channel).power_order_count
        self._order_combos[channel].setEnabled(enabled and count > 0)
        self._delay_fields[channel].setEnabled(enabled)
        if animate and self.isVisible():
            self._animate_reel(channel, mode)
        else:
            self._set_reel_position(channel, CHANNEL_MODES.index(mode))
        self._update_tooltips()
        self.update()

    def _on_selector_hover(self, channel: str, hovered: bool) -> None:
        self._hover_selector = channel if hovered else None
        self.update()

    def _on_selector_focus(self, channel: str, focused: bool) -> None:
        self._focus_selector = channel if focused else None
        self.update()

    def _on_selector_pressed(self, channel: str, pressed: bool) -> None:
        self._pressed_selector = channel if pressed else None
        self.update()

    def _on_protection_toggled(
        self, channel: str, kind: str, enabled: bool
    ) -> None:
        """用户勾选/取消保护：更新卡面快照并发信号，配置由页面读取。"""
        self._data = self._data.with_channel_protection(
            channel, kind, bool(enabled)
        )
        self._update_tooltips()
        self.update()
        self.protection_toggled.emit(channel, kind, bool(enabled))

    def _on_value_changed(self, channel: str, kind: str, value: float) -> None:
        """用户输入 VS / IS：更新卡面快照并发信号，配置由页面读取。"""
        self._data = self._data.with_channel_value(channel, kind, value)
        self._update_tooltips()
        self.update()
        self.value_changed.emit(channel, kind, float(value))

    def _on_protection_hover(
        self, channel: str, kind: str, hovered: bool
    ) -> None:
        self._hover_protection = (channel, kind) if hovered else None
        self.update()

    def _on_protection_focus(
        self, channel: str, kind: str, focused: bool
    ) -> None:
        self._focus_protection = (channel, kind) if focused else None
        self.update()

    def _on_calibration_toggled(self, channel: str, marked: bool) -> None:
        """用户按下/取消 ``Cal.``：更新快照、播放动效并发信号，配置由页面决定。"""
        self._data = self._data.with_channel_efficiency_calibration(
            channel, bool(marked)
        )
        self._animate_calibration(channel, bool(marked))
        self._update_tooltips()
        self.update()
        self.efficiency_calibration_toggled.emit(channel, bool(marked))

    def _on_calibration_hover(self, channel: str, hovered: bool) -> None:
        self._hover_calibration = channel if hovered else None
        self.update()

    def _on_calibration_focus(self, channel: str, focused: bool) -> None:
        self._focus_calibration = channel if focused else None
        self.update()

    def _on_calibration_pressed(self, channel: str, pressed: bool) -> None:
        self._pressed_calibration = channel if pressed else None
        self.update()

    # ---------- 提示与绘制 ----------

    def _update_tooltips(self) -> None:
        """悬浮提示补上卡面放不下的信息：身份详情、单位、范围与通道角色。"""
        identity = supply_identity_text(
            self._data.code or None,
            self._data.model,
            self._data.full_code,
            self._data.device_key,
        )
        lines = [
            identity or "未识别电源",
            "左侧 CH1 · 右侧 CH2；卡面不再标注通道号，位置与底色即通道",
            "点击 OFF / DUT / Driver 滚筒切换供电角色：OFF 关闭通道，"
            "DUT 给功放供电，Driver 给驱放供电",
            "Cal. 是效率计算标记，可多选；它不改变供电角色、上电顺序或通道开关，"
            "第一个已上电的标记通道会作为当前运行链路的漏极",
            "档位会同步下方上电计划；VS 电压设定（V） · IS 电流设定（A） · "
            "顺序/间隔决定该通道的上电位置与等待时间",
        ]
        for channel in self._data.channels:
            if channel.power_order is not None:
                power_text = (
                    f"上电第 {channel.power_order}/{channel.power_order_count} 位 · "
                    f"间隔 {channel.power_delay_s:.2f}s"
                )
            else:
                power_text = "未参与上电"
            calibration_text = (
                "效率计算 开" if channel.efficiency_calibration_enabled else "效率计算 关"
            )
            lines.append(
                f"{channel.channel} {CHANNEL_MODE_LABELS[channel.mode]}："
                f"VS {channel.voltage_set:.2f} V · IS {channel.current_set:.2f} A · "
                f"电压保护 {'开' if channel.voltage_protection_enabled else '关'} · "
                f"电流保护 {'开' if channel.current_protection_enabled else '关'} · "
                f"{calibration_text} · {power_text}"
            )
        text = "\n".join(lines)
        self.setToolTip(text)
        for channel in self._data.channels:
            selector = self._selectors[channel.channel]
            driver_note = (
                ""
                if self._driver_available
                else "\n当前驱放测试模式关闭，Driver 档不可选。"
            )
            selector.setToolTip(
                f"{channel.channel} 当前 {CHANNEL_MODE_LABELS[channel.mode]}\n"
                "点击循环 OFF → DUT → Driver → OFF；"
                "OFF 关闭通道，DUT 给功放供电，Driver 给驱放供电。\n"
                "档位会写入下方对应的上电计划。"
                f"{driver_note}\n{text}"
            )
            for kind in PROTECTION_KINDS:
                label = "电压保护" if kind == "voltage" else "电流保护"
                check = self._protection_checks[(channel.channel, kind)]
                check.setToolTip(
                    f"{channel.channel} {label}\n"
                    "勾选后保护阈值自动取比设定值高 10% 的值，无需手工填写。\n"
                    f"{'点击取消' if channel.protection_enabled(kind) else '点击启用'}"
                )
            calibration = self._calibration_toggles[channel.channel]
            calibration.setToolTip(
                f"{channel.channel} 效率计算标记（Cal.）\n"
                "按下表示该通道参与效率计算；两个通道可以同时标记。\n"
                "不改变供电角色、上电顺序或通道开关；第一个已上电的标记通道"
                "会作为当前运行链路的漏极。\n"
                f"{'点击取消标记' if channel.efficiency_calibration_enabled else '点击标记'}"
            )
            combo = self._order_combos[channel.channel]
            if channel.power_order is not None:
                order_text = (
                    f"当前第 {channel.power_order} 位（共 "
                    f"{channel.power_order_count} 位）"
                )
            else:
                order_text = "当前不参与上电"
            combo.setToolTip(
                f"{channel.channel} 上电顺序\n{order_text}\n"
                "选择 1..N 调整本通道在计划里的位置；1 表示最先上电。"
            )
            self._delay_fields[channel.channel].setToolTip(
                f"{channel.channel} 上电间隔（s）\n"
                "本通道开启后等待这么久，再向下一个仪器发送上电命令；缺省 2s。"
            )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        try:
            # 卡面按 440×186 的逻辑坐标绘制，再缩放到控件实际尺寸；用实际宽高比例
            # 而不是四舍五入前的 scale，右/下边缘不会被裁掉半像素。
            painter.scale(
                self.width() / POWER_CARD_WIDTH,
                self.height() / POWER_CARD_HEIGHT,
            )
            _paint_power_card(
                painter,
                self._data,
                reel_positions=self._reel_positions,
                hover_selector=self._hover_selector,
                focus_selector=self._focus_selector,
                pressed_selector=self._pressed_selector,
                driver_available=self._driver_available,
                hover_protection=self._hover_protection,
                focus_protection=self._focus_protection,
                calibration_progress=self._calibration_progress,
                hover_calibration=self._hover_calibration,
                focus_calibration=self._focus_calibration,
                pressed_calibration=self._pressed_calibration,
            )
        finally:
            painter.end()
