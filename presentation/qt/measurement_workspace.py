"""测量工作台外壳：阶段条 + 状态簇共用顶栏，下面是单个内容槽。

测量区的连续感来自“一个始终不动的框架”：顶栏和进度发丝线在切页时保持原位置，
只有内容槽里的内容在换。本模块只负责这块框架的组装与展示，不拥有页面栈，也
不导入任何页面类型：

- 内容由窗口通过 :meth:`MeasurementWorkspace.set_content` 注入，第一步继续由
  窗口里的 ``QTabWidget`` 承载页面；外壳不调用页面的 ``on_activated`` /
  ``on_deactivated``，也不决定页面生命周期。
- 阶段条与状态簇共用一条 52px 顶栏：左侧是 01–04 阶段，右侧是运行标识、模式徽标
  与“配置”入口。状态簇只展示调用方传入的文本，不读配置、不读运行服务，避免在
  表现层形成第二个业务真相源。阶段编号与标题只由阶段条表达。
- 阶段条仍是受控视图：点击阶段只经 :attr:`MeasurementWorkspace.stage_selected`
  发出导航请求，窗口切页完成后调用 :meth:`set_current_stage` 同步。
- ``show_utility`` 用于不属于 01–04 阶段的工具页（例如“测试配置”）：它清空当前
  阶段（阶段条不点亮任何一项），顶栏也不显示工具页标题；此时右侧“配置”动作
  没有去处，随之禁用，离开工具页后恢复。
- 类名不带 ``Page`` 后缀，避免被页面注册门禁误登记为页面。
- 阶段切换的进入方向由本模块按阶段顺序回答
  （:meth:`MeasurementWorkspace.stage_reveal_direction`），过渡也由本模块在内容槽里
  播放（:meth:`MeasurementWorkspace.reveal_current_page`）：只动标签宿主当前显示的
  那一页，顶栏、发丝线与外壳本身不位移。窗口不参与动画细节。
- 阶段条下方的 2px 发丝线（:class:`MeasurementProgressHairline`）只确认“测量在推进”：
  数值来自已有的 ``signals.progress``，有活动测量时显示、空闲时保持空白；它固定占
  2px，不推动内容区，也不替代状态面板里的详细进度条。
- 阶段摘要（:meth:`MeasurementWorkspace.set_stage_summary`）由页面从已经显示的数据
  里取好再交给外壳：阶段条显示 ``headline`` 一句话。页头不再另存一份 KPI 文本，
  避免同一份摘要出现两个展示位。外壳不统计频点、不读结果 DTO，也不在工具页上
  显示摘要。

设计约束：

- 外壳不拥有内容栈、不导入页面类、不调用页面生命周期；
- 状态簇只做展示，``run_id`` 与模式文本由窗口传入；无运行时显示“未连接”；
  模式文本只由徽标显示，运行标识不再重复拼一遍模式；
- 内容槽只接受一个 widget；替换时从布局移除旧 widget，**不销毁**它，页面所有权
  仍归窗口/标签页；
- 颜色与字号只取 :mod:`presentation.qt.theme` 的既有语义角色，不在本模块写
  颜色值或字号。

阶段集合由构造参数决定：外壳接受 ``stages`` 时，当前阶段的查找也只在这个集合里
进行，不再回落到全局 :data:`~presentation.qt.measurement_stage.MEASUREMENT_STAGES`，
否则自定义阶段会出现“画得出、同步不了”的半成品状态。内容槽与状态簇只接受
调用方自己的控件；外壳的结构控件（阶段条、顶栏、状态簇、进度发丝线、内容宿主及
其子控件）不能被搬进自己里面，否则会拆掉常驻框架。
"""

from __future__ import annotations

import math
from types import MappingProxyType
from typing import Mapping

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .measurement_stage import (
    MEASUREMENT_STAGES,
    StageDescriptor,
    StageState,
)
from .measurement_stage_rail import MeasurementStageRail
from .motion import RevealDirection, RevealHandle, reveal
from .stage_summary import StageSummary
from .theme import MOTION_REVEAL_MS, TOKENS, set_button_variant

__all__ = [
    "MeasurementProgressHairline",
    "MeasurementWorkspace",
    "mode_text",
]

#: 没有 run 标识时顶栏显示的固定文案；步骤 6 只传入文本，不在外壳判断连接状态。
_UNCONNECTED_RUN_TEXT = "未连接"

#: 驱动模式文案：状态簇与测试模式徽标共用同一份来源，避免三处字面量各自漂移。
_MODE_TEXT_DRIVER = "有驱放测试"
_MODE_TEXT_NO_DRIVER = "无驱放测试"
_MODE_TEXT_UNSET = "未选择测试模式"

#: 顶栏：阶段条与状态簇共用一行。左右 16px 与此前页头的外边距保持一致；阶段条
#: 固定 52px 高，状态簇垂直居中，整条顶栏不会因为按钮更高而变高。
_TOP_BAR_MARGINS = (16, 0, 16, 0)
#: 阶段条与状态簇之间的细分隔线高度。
_TOP_BAR_DIVIDER_HEIGHT = 22
_CONTENT_MARGINS = (16, 0, 12, 12)


def mode_text(driver_enabled: bool | None) -> str:
    """把驱动模式折成状态簇与徽标共用的唯一文案。

    ``None`` 表示还没有选择测试模式（欢迎页或刚返回欢迎页）；``True`` / ``False``
    分别表示有驱放测试与无驱放测试。这是纯展示格式，不读取配置，也不决定页面状态。
    """
    if driver_enabled is None:
        return _MODE_TEXT_UNSET
    if not isinstance(driver_enabled, bool):
        raise TypeError("驱动模式必须是 bool 或 None")
    return _MODE_TEXT_DRIVER if driver_enabled else _MODE_TEXT_NO_DRIVER


def _clean_text(value: str | None) -> str | None:
    """把可选文本规整成“去首尾空白后的非空字符串”或 ``None``。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("文本必须是字符串或 None")
    cleaned = value.strip()
    return cleaned or None


class _ElidedLabel(QLabel):
    """单行状态文本：按可用宽度中间省略，tooltip 保留完整值。

    ``text()`` 仍返回当前显示文本，``full_text()`` 返回完整值；布局按完整文本的
    宽度申请空间，窗口不够宽时由布局压缩再到 ``resizeEvent`` 里省略。
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self._full_text = ""
        self.setMinimumWidth(0)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt 覆盖方法
        self._full_text = text
        self._apply_elision(allow_growth=True)

    def full_text(self) -> str:
        return self._full_text

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt 覆盖方法
        metrics = self.fontMetrics()
        width = metrics.horizontalAdvance(self._full_text)
        return QSize(width + 2, metrics.height())

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt 覆盖方法
        super().resizeEvent(event)
        self._apply_elision()

    def _apply_elision(self, *, allow_growth: bool = False) -> None:
        width = self.contentsRect().width()
        if allow_growth:
            # 文本刚写入时布局还没走到新宽度：先按 sizeHint 展示全文，避免在旧宽度
            # 上提前省略。真正的窗口宽度由随后的 resizeEvent 收敛。
            width = max(width, self.sizeHint().width())
        if width <= 0:
            QLabel.setText(self, self._full_text)
            self.setToolTip("")
            return
        shown = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideMiddle, width
        )
        QLabel.setText(self, shown)
        if shown != self._full_text:
            self.setToolTip(self._full_text)
        else:
            self.setToolTip("")


class MeasurementProgressHairline(QWidget):
    """顶栏下方的 2px 进度发丝线：只确认“测量在走”，不承担详细进度。

    它是阶段条与内容区之间的常驻一条线，高度固定（不显示时也占位，避免测量开始/
    结束时内容区上下跳 2px）。只在有活动测量时绘制轨道与进度，空闲时整块保持空白；
    数值来自已有的 ``signals.progress``，不新增数值来源，也不替代状态面板里的进度条。

    轨道是 2px 的细线而不是可交互控件：不接收鼠标事件，也没有自己的计时器；动画
    只存在于阶段条（呼吸点与完成扫过），本控件只按进度值重绘。
    """

    #: 固定高度：发丝线不参与页面排版，显示/隐藏不推动内容区。
    HEIGHT = 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("measurementProgressHairline")
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._value = 0
        self._active = False

    # ---- 读取 ---------------------------------------------------------------

    def value(self) -> int:
        """当前进度（0–100）；空闲时保留上次值没有意义，因此恒为 0。"""
        return self._value

    def is_active(self) -> bool:
        """是否有活动测量在驱动这条线。"""
        return self._active

    def fill_width(self) -> int:
        """按当前宽度与进度算出的填充像素；空闲时为 0。"""
        if not self._active:
            return 0
        return int(round(self.width() * self._value / 100.0))

    # ---- 写入 ---------------------------------------------------------------

    def set_value(self, value: int | float) -> None:
        """写入 0–100 的进度；越界值夹紧，非数值或非有限值直接拒绝。

        ``bool`` 与字符串抛 ``TypeError``，``NaN``/``±inf`` 抛 ``ValueError``：后者
        无法夹紧，放行只会得到一个随平台变化的宽度。空闲时没有显示位置，直接忽略：
        迟到的进度不能把已经收起的发丝线重新变成一个非零值，下一次激活仍从 0 开始。
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError("进度必须是整数或浮点数")
        if not math.isfinite(float(value)):
            raise ValueError("进度必须是有限数值")
        if not self._active:
            return
        clamped = max(0, min(100, int(round(float(value)))))
        if clamped == self._value:
            return
        self._value = clamped
        self.update()

    def set_active(self, active: bool) -> None:
        """打开/关闭这条线；进入或离开活动测量时进度都回到 0。"""
        if not isinstance(active, bool):
            raise TypeError("active 必须是 bool")
        if active == self._active:
            return
        self._active = active
        self._value = 0
        self.update()

    # ---- 绘制 ---------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 覆盖方法
        """活动时画轨道 + 已走过的一段；空闲时保持空白，不吸引视线。"""
        if not self._active:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor(TOKENS["border"]))
        width = self.fill_width()
        if width <= 0:
            return
        painter.fillRect(
            0, 0, width, self.HEIGHT, QColor(TOKENS["primary"])
        )


class MeasurementWorkspace(QWidget):
    """测量工作台的常驻外壳：顶栏、进度发丝线与内容槽。

    顶栏左侧是阶段条，右侧是运行标识、模式徽标与“配置”入口，两者共用同一条
    52px 行。调用方只负责写入“当前在哪、各阶段什么状态、运行标识是什么、测量
    是否在走、走到多少”，外壳不读配置、不读控制器、也不切换页面。点击阶段只发出
    :attr:`stage_selected`；状态簇的“配置”动作只发出
    :attr:`configuration_requested`，由窗口决定导航到哪一页。
    """

    #: 用户请求进入某个测量阶段；载荷是阶段 key，窗口负责真正切页。
    stage_selected = Signal(str)

    #: 用户请求回到测试配置；配置入口是工具动作，不是第五个阶段。
    configuration_requested = Signal()

    #: 外壳动效开关变化：窗口把它转发给页面的读数条等自绘件。
    animations_changed = Signal(bool)

    def __init__(
        self,
        stages: tuple[StageDescriptor, ...] = MEASUREMENT_STAGES,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._stages = tuple(stages)
        # 当前阶段的查找表与阶段条同源（同一份 ``stages``），不回落全局默认阶段：
        # 自定义阶段必须既能画出来，也能同步顶栏。
        self._stages_by_key: Mapping[str, StageDescriptor] = MappingProxyType(
            {stage.key: stage for stage in self._stages}
        )
        self._current_stage_key: str | None = None
        # 上一次同步到的阶段：只用于决定本次进入方向，不参与顶栏文本或阶段条。
        self._previous_stage_key: str | None = None
        self._page_reveal_handle: RevealHandle | None = None
        self._utility_view = False
        self._content: QWidget | None = None
        self._right_widgets: list[QWidget] = []
        self._run_id: str | None = None
        self._mode_text: str | None = None
        self._animations_enabled = True
        # 阶段摘要按 key 保存：切回某个阶段时阶段条能重新显示它，而不用窗口再推一次。
        self._stage_summaries: dict[str, StageSummary] = {}

        self._build_ui()
        self.set_run_identity(None, None)

    # ---- 构造 ---------------------------------------------------------------

    def _build_ui(self) -> None:
        """自上而下：顶栏（阶段条 + 状态簇）→ 进度发丝线 → 内容槽。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_top_bar())
        self.progress_hairline = MeasurementProgressHairline(self)
        layout.addWidget(self.progress_hairline)
        layout.addWidget(self._build_content_host(), 1)

    def _build_top_bar(self) -> QWidget:
        """阶段条与状态簇共用的一条顶栏，高度只由阶段条决定。"""
        self.top_bar = QWidget(self)
        self.top_bar.setObjectName("measurementWorkspaceTopBar")
        # 顶栏用样式表画白色柔光带：普通 QWidget 只有在显式声明时才绘制 QSS 背景。
        self.top_bar.setAttribute(
            Qt.WidgetAttribute.WA_StyledBackground, True
        )
        top_layout = QHBoxLayout(self.top_bar)
        top_layout.setContentsMargins(*_TOP_BAR_MARGINS)
        top_layout.setSpacing(12)

        self.rail = MeasurementStageRail(self._stages, self.top_bar)
        self.rail.setObjectName("measurementWorkspaceRail")
        self.rail.stage_selected.connect(self._on_rail_stage_selected)
        top_layout.addWidget(self.rail, 1)

        self.top_bar_divider = QFrame(self.top_bar)
        self.top_bar_divider.setObjectName("measurementWorkspaceTopBarDivider")
        self.top_bar_divider.setFixedSize(1, _TOP_BAR_DIVIDER_HEIGHT)
        top_layout.addWidget(
            self.top_bar_divider,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )

        self.header = self._build_status_cluster()
        top_layout.addWidget(
            self.header,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        return self.top_bar

    def _build_status_cluster(self) -> QWidget:
        """顶栏右端的状态簇：运行标识、外部状态控件与配置入口。

        阶段编号、标题与副标题由阶段条表达，状态簇只保留会话级信息；模式文本由
        窗口写入徽标（:meth:`add_right_widget`），运行标识不再重复拼一遍模式。
        """
        self.header = QFrame(self.top_bar)
        self.header.setObjectName("measurementWorkspaceStatus")
        self._right_layout = QHBoxLayout(self.header)
        self._right_layout.setContentsMargins(0, 0, 0, 0)
        self._right_layout.setSpacing(8)

        self.run_identity_label = _ElidedLabel("", self.header)
        self.run_identity_label.setObjectName("measurementWorkspaceRunIdentity")
        self.run_identity_label.setProperty("role", "hint")
        self.run_identity_label.setTextFormat(Qt.TextFormat.PlainText)
        self.run_identity_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._right_layout.addWidget(self.run_identity_label)

        self.configuration_btn = QPushButton("配置", self.header)
        self.configuration_btn.setObjectName("measurementWorkspaceConfigButton")
        self.configuration_btn.setAccessibleName("打开测试配置")
        self.configuration_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        set_button_variant(self.configuration_btn, "ghost")
        self.configuration_btn.clicked.connect(
            self._emit_configuration_requested
        )
        # “配置”始终是状态簇的最后一个动作；add_right_widget 插在它前面。
        self._right_layout.addWidget(self.configuration_btn)

        return self.header

    def _build_content_host(self) -> QWidget:
        """内容槽的宿主：只放一个 widget，替换时只做移除与重挂。"""
        self.content_host = QWidget(self)
        self.content_host.setObjectName("measurementWorkspaceContent")
        self._content_layout = QVBoxLayout(self.content_host)
        self._content_layout.setContentsMargins(*_CONTENT_MARGINS)
        self._content_layout.setSpacing(0)
        return self.content_host

    # ---- 读取 ---------------------------------------------------------------

    def stage_rail(self) -> MeasurementStageRail:
        """常驻阶段条；窗口在步骤 5 之后可据此做细粒度检查。"""
        return self.rail

    def current_stage(self) -> str | None:
        """当前阶段 key；工具页（测试配置等）为 ``None``。"""
        return self._current_stage_key

    def is_utility_view(self) -> bool:
        """顶栏当前是否显示工具页（不点亮任何阶段）。"""
        return self._utility_view

    def has_stage(self, key: str) -> bool:
        """``key`` 是否属于构造时接收的阶段集合。

        窗口的 :class:`~presentation.qt.workspace_binding.WorkspaceBinding` 用它分流
        顶栏：阶段页走 :meth:`set_current_stage`，其余已登记页面（例如测试配置）走
        :meth:`show_utility`。非字符串抛 ``TypeError``；未知 key 只返回 ``False``，
        不在查询里抛异常。
        """
        if not isinstance(key, str):
            raise TypeError("阶段 key 必须是字符串")
        return key in self._stages_by_key

    def content_widget(self) -> QWidget | None:
        """内容槽里的当前 widget；没有内容时为 ``None``。"""
        return self._content

    def right_widgets(self) -> tuple[QWidget, ...]:
        """顶栏状态簇里由调用方添加的 widget，按添加顺序返回。"""
        return tuple(self._right_widgets)

    def run_identity(self) -> tuple[str | None, str | None]:
        """顶栏展示的 ``(run_id, mode_text)``；未设置的部分为 ``None``。"""
        return self._run_id, self._mode_text

    def stage_summary(self, key: str) -> StageSummary | None:
        """某阶段最近一次写入的摘要；从未写入时为 ``None``。"""
        if not isinstance(key, str):
            raise TypeError("阶段 key 必须是字符串")
        return self._stage_summaries.get(key)

    def progress_value(self) -> int:
        """顶栏发丝线的当前进度（0–100）；没有活动测量时为 0。"""
        return self.progress_hairline.value()

    def progress_active(self) -> bool:
        """顶栏发丝线是否显示（有活动测量）。"""
        return self.progress_hairline.is_active()

    def animations_enabled(self) -> bool:
        """外壳的动效开关：阶段条指示块、呼吸点、完成扫过与切页过渡共用一个开关。"""
        return self._animations_enabled

    def stage_reveal_direction(
        self, previous_key: str | None, current_key: str | None
    ) -> RevealDirection | None:
        """按阶段顺序给出切页时内容进入的方向；``None`` 表示瞬时切换。

        只有“阶段 → 阶段”的切换才有过场：``previous_key`` 为 ``None``（首次显示，
        或从测试配置这类工具页进入）时返回 ``None``，配置页的整屏叙事语言不会被带进
        测量区。向后切（序号增大）从右向左轻移，向前切反之；同一个阶段、未知 key
        同样返回 ``None``，不抛异常打断切页。

        这里只回答方向，不播放动画：过渡由 :meth:`reveal_current_page` 在真正切页后
        播放，动画开关与“测量进行中瞬时切换”也由那里统一判定。
        """
        if not isinstance(previous_key, str) or not isinstance(current_key, str):
            return None
        previous = self._stages_by_key.get(previous_key)
        current = self._stages_by_key.get(current_key)
        if previous is None or current is None or previous.key == current.key:
            return None
        if current.order > previous.order:
            return RevealDirection.FROM_RIGHT
        return RevealDirection.FROM_LEFT

    def reveal_current_page(self, *, animate: bool = True) -> RevealHandle | None:
        """对刚进入的内容页播放一次“淡入 + 10px 轻移”；返回本次过渡句柄。

        只在“阶段 → 阶段”的切页后调用（通常是 :meth:`set_current_stage` 之后）：
        首次显示、从测试配置页进入、``animate=False``（测量进行中）或外壳动效已
        关闭时都直接返回 ``None``，页面的旧过渡也会被停止并摘除效果，保证同一时刻
        最多一个过渡。只动画进入页，旧页由标签宿主立即隐藏。进入方向只消费一次：
        同一页被重复同步不会重播。
        """
        self._cancel_page_reveal()
        direction = self.stage_reveal_direction(
            self._previous_stage_key, self._current_stage_key
        )
        # 进入方向只消费一次：同一页被重复同步时不会重播，下一次阶段切换仍按顺序判断。
        self._previous_stage_key = self._current_stage_key
        if direction is None or not animate or not self._animations_enabled:
            return None
        target = self._reveal_target()
        if target is None:
            return None
        self._page_reveal_handle = reveal(
            target,
            direction,
            MOTION_REVEAL_MS,
            # 页面底色即工作区画布色：先铺不透明底色，matplotlib 画布在半透明阶段
            # 才不会透出效果输出下面的黑底。
            background=TOKENS["canvas"],
        )
        return self._page_reveal_handle

    def page_reveal_handle(self) -> RevealHandle | None:
        """当前切页过渡的句柄；收尾或没有过渡时为 ``None``。"""
        return self._page_reveal_handle

    def _reveal_target(self) -> QWidget | None:
        """内容槽里真正要过渡的控件：宿主有当前页时动当前页，否则动宿主本身。"""
        content = self._content
        if content is None:
            return None
        current_widget = getattr(content, "currentWidget", None)
        if callable(current_widget):
            page = current_widget()
            if isinstance(page, QWidget):
                return page
        return content

    def _cancel_page_reveal(self) -> None:
        """停止并摘除正在播放的切页过渡；重复调用是幂等的。"""
        handle, self._page_reveal_handle = self._page_reveal_handle, None
        if handle is not None:
            handle.cancel()

    # ---- 写入：阶段与状态簇 -------------------------------------------------

    def set_current_stage(self, key: str | None) -> None:
        """设置当前阶段；``None`` 表示没有当前阶段。

        阶段条是受控视图：这里由窗口在真正切页后调用。key 必须是构造时传入的阶段
        集合里的 key；未知 key 抛 ``KeyError``，且不会改动顶栏任何文本。
        """
        if key is None:
            self._enter_no_stage_view()
            return
        if not isinstance(key, str):
            raise TypeError("当前阶段 key 必须是字符串或 None")
        try:
            descriptor = self._stages_by_key[key]
        except KeyError:
            raise KeyError(f"未知测量阶段: {key!r}") from None
        self.rail.set_current(descriptor.key)
        self._previous_stage_key = self._current_stage_key
        self._current_stage_key = descriptor.key
        self._utility_view = False
        self.configuration_btn.setEnabled(True)

    def show_utility(self) -> None:
        """显示一个不属于 01–04 阶段的工具页（例如“测试配置”）。

        阶段条不点亮任何一项；状态簇不显示工具页标题，只保留运行标识与“配置”
        动作。工具页的说明由页面内容自己承担。
        """
        self.rail.set_current(None)
        self._current_stage_key = None
        self._utility_view = True
        # 已经站在工具页上时“配置”没有去处：按钮保留位置但不可用，离开后恢复。
        self.configuration_btn.setEnabled(False)

    def set_run_identity(
        self, run_id: str | None, mode_label: str | None
    ) -> None:
        """写入状态簇展示的 run 标识与模式文本；只展示，不判断连接状态。

        ``run_id`` 为空时显示“未连接”。模式文本只由徽标展示，运行标识不再重复
        拼一遍；文本超出可见宽度时中间省略，完整值放进 tooltip。
        """
        self._run_id = _clean_text(run_id)
        self._mode_text = _clean_text(mode_label)
        self.run_identity_label.setText(self._format_run_identity())

    def _format_run_identity(self) -> str:
        # 没有运行标识时只写“未连接”；不能拼成“本次运行 未连接”这类自相矛盾的文案。
        if self._run_id is None:
            return _UNCONNECTED_RUN_TEXT
        return f"本次运行 {self._run_id}"

    def _enter_no_stage_view(self) -> None:
        """没有当前阶段：阶段条收起指示块，状态簇停在空的工作区状态。"""
        self.rail.set_current(None)
        self._current_stage_key = None
        self._utility_view = False
        self.configuration_btn.setEnabled(False)

    # ---- 写入：阶段状态与动效 -------------------------------------------------

    def set_stage_summary(
        self, key: str, summary: StageSummary | None
    ) -> None:
        """写入一个阶段的摘要；``None`` 清空该阶段的摘要。

        摘要由页面从已经显示的数据里取好（见 :mod:`presentation.qt.stage_summary`），
        外壳只把它放到阶段条：``headline`` 是顶部唯一展示位，不再另存一份 KPI 文本。
        key 必须是构造时接收的阶段集合里的 key。
        """
        if not isinstance(key, str):
            raise TypeError("阶段 key 必须是字符串")
        if key not in self._stages_by_key:
            raise KeyError(f"未知测量阶段: {key!r}")
        if summary is None:
            self._stage_summaries.pop(key, None)
            self.rail.set_summary(key, "")
            return
        if not isinstance(summary, StageSummary):
            raise TypeError("阶段摘要必须是 StageSummary 或 None")
        self._stage_summaries[key] = summary
        self.rail.set_summary(key, summary.headline)

    def set_stage_state(self, key: str, state: StageState | str) -> None:
        """更新一个阶段的运行状态；只接受五个运行状态，``DISABLED`` 由启用状态派生。"""
        self.rail.set_state(key, state)

    def set_stage_enabled(self, key: str, enabled: bool) -> None:
        """启用/停用一个阶段；停用只影响交互与显示，编号与保存的状态都不丢。"""
        self.rail.set_stage_enabled(key, enabled)

    def set_progress(self, value: int | float) -> None:
        """写入顶栏发丝线的进度；数值来自已有的 ``signals.progress``。"""
        self.progress_hairline.set_value(value)

    def set_measurement_active(self, active: bool) -> None:
        """有活动测量时显示发丝线，空闲时隐藏；不改变阶段状态或页面导航。"""
        self.progress_hairline.set_active(active)

    def set_animations_enabled(self, enabled: bool) -> None:
        """打开/关闭外壳动效。

        关闭时指示块立即落到当前阶段、呼吸点与完成扫过一起停止，切页也不再播放
        过渡；重新打开只恢复还在进行中的呼吸，播完的扫过不会重播。
        """
        if not isinstance(enabled, bool):
            raise TypeError("enabled 必须是 bool")
        self._animations_enabled = enabled
        self.rail.set_animations_enabled(enabled)
        if not enabled:
            self._cancel_page_reveal()
        self.animations_changed.emit(enabled)

    # ---- 写入：内容与状态簇 ---------------------------------------------------

    def _structural_widgets(self) -> tuple[QWidget, ...]:
        """常驻框架自己的控件：它们不能被搬进内容槽或状态簇。"""
        return (
            self.top_bar,
            self.rail,
            self.header,
            self.progress_hairline,
            self.content_host,
        )

    def _ensure_external_widget(self, widget: QWidget, usage: str) -> None:
        """拒绝把外壳自己、它的结构控件（含子控件）或它的祖先交给外壳摆放。

        ``set_content`` / ``add_right_widget`` 只负责摆放调用方的控件；把常驻框架
        的一部分重新挂进去会拆掉阶段条或状态簇，属于调用错误而不是显示选择。
        """
        if widget is self or widget.isAncestorOf(self):
            raise ValueError(f"{usage}不能是工作台本身或它的祖先")
        for structural in self._structural_widgets():
            if widget is structural or structural.isAncestorOf(widget):
                raise ValueError(f"{usage}不能是工作台自己的结构控件")

    def set_content(self, widget: QWidget | None) -> None:
        """设置内容槽里的唯一 widget；``None`` 清空内容。

        替换时先把旧 widget 从布局移除并摘掉父级，**不销毁**它：页面所有权仍归
        窗口/标签页，外壳只负责摆放。重复设置同一个 widget 是幂等的。
        """
        if widget is not None and not isinstance(widget, QWidget):
            raise TypeError("内容必须是 QWidget 或 None")
        if widget is self._content:
            return
        # 旧内容即将离开展示区：正在播放的过渡必须一起收尾，不能留在已摘下的控件上。
        self._cancel_page_reveal()
        if widget is not None:
            self._ensure_external_widget(widget, "内容")

        previous = self._content
        if previous is not None:
            previous.hide()
            self._content_layout.removeWidget(previous)
            previous.setParent(None)

        self._content = widget
        if widget is not None:
            self._content_layout.addWidget(widget, 1)
            widget.show()

    def add_right_widget(self, widget: QWidget) -> None:
        """把一个状态 widget 追加到顶栏状态簇；“配置”动作保持在最右端。

        重复添加同一个 widget 是幂等的；widget 原来属于别的父控件时由 Qt 重新
        挂接，调用方仍持有它，外壳不销毁。
        """
        if not isinstance(widget, QWidget):
            raise TypeError("右侧状态控件必须是 QWidget")
        if any(existing is widget for existing in self._right_widgets):
            return
        self._ensure_external_widget(widget, "右侧状态控件")
        # 最后一项始终是“配置”动作，状态控件插在它前面。
        self._right_layout.insertWidget(
            self._right_layout.count() - 1, widget
        )
        self._right_widgets.append(widget)

    # ---- 内部：信号转发 -------------------------------------------------------

    def _on_rail_stage_selected(self, key: str) -> None:
        """阶段条点击只转发导航请求，不在这里写 ``current``。"""
        self.stage_selected.emit(key)

    def _emit_configuration_requested(self) -> None:
        self.configuration_requested.emit()
