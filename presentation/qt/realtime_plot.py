"""测量页面共享的实时绘图控件。

绘图只做展示：默认四张图“输入-输出功率 / 增益 / 效率 / DC 功耗”，配色与字体
统一来自 :mod:`presentation.qt.theme`，因此测量页与历史可视化页的图看起来是同一套。
面板集合可以按页面收窄：驱动映射只测输入-输出与增益，效率与 DC 功耗没有测量数据，
页面用 :data:`DRIVER_MAPPING_PANELS` 只画这两张图。

线损页用 :data:`CABLE_LOSS_PANELS` 画另一种语义的四幅图：每幅一条线缆的
“线损 vs 频率”，横轴是频率(GHz)、纵轴是线损(dB)。它的数据形状不是一次扫频，
调用方通过 :meth:`RealTimePlotWidget.update_plot` 的 ``series`` 字段直接给出
已累积的 ``(x, y)`` 序列。

P2 在原有 2×2 布局上补三种“会说话”的读法，全部只消费调用方已经取好的数据：

- **最新点标注**：每条曲线最后一个点加一个空心圆环，并在点旁标出数值；
- **静态图例**：每个子图右上角一个紧凑图例，只显示“指标 — 单位”，颜色与曲线一致；
  最新值由点旁标注与页面读数条承担，图例不参与每个频点的重绘；
- **目标带**：调用方明确给出目标范围时才画一条淡色水平带。没有目标值时不画，
  这里不发明任何业务语义。

图里不再画加粗标题：子图的语义由黑色加大的横纵轴标签表达，纵轴标签直接写明
被测对象（例如线损页的“线缆1 线损 (dB)”），小面板里省下的标题高度留给坐标区。

性能约束：一张 2×2、带中文轴标签和刻度的图整图 Agg 绘制要 300–500ms。因此本控件把
“静态背景”（坐标轴、刻度、网格、轴标签、图例、目标带、等待提示）只画
一次并缓存在 ``_background``，每个数据点只用背景缓存 + ``restore_region`` 重画曲线、
最新点圆环与数值标注，再一次 ``blit`` 上屏；单点更新从数百毫秒降到十几毫秒。背景只在
首次显示、尺寸变化、量程扩展或静态内容变化时重建。隐藏页面只记录待办，显示后要么
立即 blit，要么把重建推迟到切页过渡之后（:data:`_BACKGROUND_SHOW_DEFER_MS`），避免
把数百毫秒的整图绘制塞进“点标签”的那一刻。

频点胶片条（``show_nav_buttons=True`` 时）把“◀ / ▶”扩展成可点击的频点带；
数据更新仍按 10 Hz 节流，多次到达的重绘请求合并成一次。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Dict

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.text import Text

from infrastructure.logging.app_logging import get_logger

from .frequency_filmstrip import FrequencyFilmstrip
from .theme import MOTION_REVEAL_MS, TOKENS, set_button_variant

logger = get_logger(__name__)

__all__ = [
    "ALL_PANELS",
    "CABLE_LOSS_PANELS",
    "DRIVER_MAPPING_PANELS",
    "PlotTargetBand",
    "RealTimePlotWidget",
]

#: 量程滞回：数据仍在视窗内时只 blit；越界才扩界并重建背景。
#: 一次扩界要整图重绘（百毫秒级），所以新视窗必须给“后面还会到的点”留出余量：
#: 按 span 的 35% 留白，再向外取整到刻度友好的边界。只按 8% 留白时，每个新
#: 扫频点都会再次越界，整条扫频曲线几乎每个点都要重画一遍，页面看上去一直在闪。
_VIEW_PAD_RATIO = 0.35
#: 常数序列（例如整条增益都等于 11dB）没有 span，至少留这么多上下余量。
_VIEW_MIN_PAD = 0.5
#: 扩界取整后的视窗希望容纳的刻度数；太少会让刻度挤在一起，太多会浪费画面。
_VIEW_TICK_COUNT = 5
#: 背景重建防抖：拖拽窗口时只重建一次，而不是每个 resize 事件都整图绘制。
_BACKGROUND_DEBOUNCE_MS = 160
#: 页面显示后延迟重建：等切页过渡播完，再付这一次整图绘制的钱。
_BACKGROUND_SHOW_DEFER_MS = MOTION_REVEAL_MS + 16


class _DeferredCanvas(FigureCanvas):
    """只决定“什么时候画”：隐藏只记待办，可见交给绘图控件 blit 或延迟重建。

    一张四宫格图的整图 Agg 绘制要 300–500ms。这里不再让 matplotlib 在任何
    ``draw_idle`` / ``resizeEvent`` 里立刻排队整图绘制：

    - 不可见：只记 ``_draw_pending``，页面第一次显示时再处理；
    - 可见：把背景标记为脏，并防抖后重建（数据更新走绘图控件的 blit，不经过这里）；
    - 显示瞬间：消费掉待办后交给所有者决定“立即 blit”还是“等过渡结束再重建”。

    防抖与延迟用的是同一个单次定时器；页面重新隐藏时停止，不在后台重建。
    """

    def __init__(self, figure: Figure) -> None:
        super().__init__(figure)
        self._owner: Any | None = None
        self._rebuild_timer = QTimer(self)
        self._rebuild_timer.setSingleShot(True)
        self._rebuild_timer.timeout.connect(self._on_rebuild_timeout)

    # ---- 所有者 -------------------------------------------------------------

    def bind_owner(self, owner: Any) -> None:
        """绑定持有背景缓存的绘图控件；只存引用，不拥有它。"""
        self._owner = owner

    # ---- 调度 ---------------------------------------------------------------

    def schedule_rebuild(self, delay_ms: int | None = None) -> None:
        """安排一次背景重建；``None`` 使用防抖间隔。"""
        interval = _BACKGROUND_DEBOUNCE_MS if delay_ms is None else int(delay_ms)
        if interval < 0:
            raise ValueError("重建延迟不能为负数")
        if self._owner is None:
            return
        self._rebuild_timer.start(interval)

    def cancel_rebuild(self) -> None:
        """取消尚未执行的重建；重复调用是幂等的。"""
        self._rebuild_timer.stop()

    def has_pending_rebuild(self) -> bool:
        """是否有排队的背景重建。"""
        return self._rebuild_timer.isActive()

    def clear_pending_draw(self) -> None:
        """消费 matplotlib 的 ``_draw_pending``，防止 paintEvent 里再排一次整图绘制。"""
        self._draw_pending = False

    # ---- 绘制入口 -----------------------------------------------------------

    def draw_idle(self) -> None:
        """隐藏时不排队；可见时改成“背景置脏 + 防抖重建”。"""
        if not self.isVisible():
            self._draw_pending = True
            return
        owner = self._owner
        if owner is None:
            super().draw_idle()
            return
        owner.invalidate_background()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        # paintEvent 会调用 matplotlib 的 ``_draw_idle``；先消费待办，改为 blit / 延迟重建。
        self.clear_pending_draw()
        owner = self._owner
        if owner is not None:
            owner.on_canvas_shown()

    def hideEvent(self, event: Any) -> None:
        self._rebuild_timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event: Any) -> None:
        # FigureCanvasQT.resizeEvent 会更新 figure 尺寸并调用 self.draw_idle()（已重写）。
        super().resizeEvent(event)
        self.clear_pending_draw()
        owner = self._owner
        if owner is not None and self.isVisible():
            owner.invalidate_background()

    def _on_rebuild_timeout(self) -> None:
        owner = self._owner
        if owner is not None and self.isVisible():
            owner.rebuild_background()


# 中文字体：matplotlib 默认字体没有中文字形，轴标签必须显式指定，否则显示为方框。
_CJK_FONT = {"family": "Microsoft YaHei"}
_CJK = _CJK_FONT["family"]
_GRID_COLOR = TOKENS["table_grid"]
_SPINE_COLOR = TOKENS["border_strong"]
_TICK_COLOR = TOKENS["text_secondary"]
#: 横纵轴标签统一用主题最深的一档文字色（近黑），比刻度更醒目。
_AXIS_LABEL_COLOR = TOKENS["text"]
_AXIS_LABEL_SIZE = 10.0


@dataclass(frozen=True)
class _PanelSpec:
    """一个子图的固定语义：面板名、轴标签、系列色与最新值读数。"""

    key: str
    xlabel: str
    ylabel: str
    color: str
    metric: str
    unit: str
    digits: int


# 四张图固定顺序：面板名、X 轴、Y 轴、曲线颜色、读数标签、单位与小数位。
_PLOT_PANELS = (
    _PanelSpec(
        "input_output",
        "输入功率 (dBm)",
        "输出功率 (dBm)",
        TOKENS["series_1"],
        "输出功率",
        "dBm",
        1,
    ),
    _PanelSpec(
        "gain",
        "输出功率 (dBm)",
        "增益 (dB)",
        TOKENS["series_2"],
        "增益",
        "dB",
        1,
    ),
    _PanelSpec(
        "efficiency",
        "输出功率 (dBm)",
        "效率 (%)",
        TOKENS["series_3"],
        "效率",
        "%",
        1,
    ),
    _PanelSpec(
        "dc_power",
        "测量点",
        "DC功耗 (W)",
        TOKENS["series_4"],
        "DC功耗",
        "W",
        2,
    ),
)

_PANEL_SPECS = {spec.key: spec for spec in _PLOT_PANELS}

#: 线损页的四幅图：每幅一条线缆，横轴频率(GHz)、纵轴线损(dB)。与功放页的四幅图
#: 语义不同——那边是“同一频点的四个指标”，这边是“同一条缆随频率的完整曲线”。
#: 图里没有标题，线缆编号直接写进纵轴标签，读数时不必再对照面板名。
_CABLE_LOSS_PANELS = tuple(
    _PanelSpec(
        f"cable{index}",
        "频率 (GHz)",
        f"线缆{index} 线损 (dB)",
        TOKENS[f"series_{index}"],
        f"线缆{index}",
        "dB",
        3,
    )
    for index in range(1, 5)
)

# 线损面板按同一份键名再次索引：默认面板组与线损面板组共享一个查询表。
_PANEL_SPECS = {
    **{spec.key: spec for spec in _PLOT_PANELS},
    **{spec.key: spec for spec in _CABLE_LOSS_PANELS},
}

#: 默认面板集合：主功放页与历史可视化页的功放结果共用这一组。
ALL_PANELS = tuple(spec.key for spec in _PLOT_PANELS)
#: 驱动映射只测“信号源输入 -> 驱放输出”和增益；效率与 DC 功耗没有测量数据，
#: 画出来只会是永远“等待数据”的空图，因此不请求这两面板。
DRIVER_MAPPING_PANELS = ("input_output", "gain")
#: 线损页的四幅图：线缆1–4 各一幅，横轴频率、纵轴线损。
CABLE_LOSS_PANELS = tuple(spec.key for spec in _CABLE_LOSS_PANELS)


def _panel_keys(panels: Sequence[str] | None) -> tuple[str, ...]:
    """把调用方的面板选择规整成有校验的键元组；``None`` 表示全部面板。"""
    if panels is None:
        return ALL_PANELS
    if isinstance(panels, (str, bytes)) or not isinstance(panels, Sequence):
        raise TypeError("面板集合必须是面板名序列")
    keys = tuple(panels)
    if not keys:
        raise ValueError("至少需要一个面板")
    for key in keys:
        if not isinstance(key, str):
            raise TypeError("面板名必须是字符串")
        if key not in _PANEL_SPECS:
            raise ValueError(f"未知面板: {key}")
    if len(set(keys)) != len(keys):
        raise ValueError("面板名不能重复")
    return keys


@dataclass
class _PanelArtists:
    """一个子图的艺术家集合：静态件只建一次，动态件每个频点只改数据。

    动态件（曲线、最新点圆环、数值标注）在重建背景时临时隐藏，所以背景缓存里不会
    夹带上一个频点的曲线；静态件（坐标轴、刻度、轴标签、图例、目标带、等待提示）
    留在背景里，blit 更新不重排、也不重新测量文字。
    """

    axis: Any
    spec: _PanelSpec
    line: Any
    marker: Any
    legend: Any
    hint: Any | None
    annotation: Any | None = None
    bands: list[Any] = field(default_factory=list)
    xlim: tuple[float, float] = (0.0, 1.0)
    ylim: tuple[float, float] = (0.0, 1.0)
    has_data: bool = False
    #: 视窗是否已经由真实数据建立；构造时的 0..1 占位范围不参与后续扩界合并。
    view_from_data: bool = False

    def dynamics(self) -> list[Any]:
        """需要每帧重画的艺术家；没有数据的面板只有隐藏的空曲线。"""
        artists = [self.line, self.marker]
        if self.annotation is not None:
            artists.append(self.annotation)
        return artists


@dataclass(frozen=True)
class PlotTargetBand:
    """绘图目标带：某个子图里的一条水平参考范围。

    调用方只有在确实有目标值时才构造它（例如结果自带压缩目标）；没有目标时不要
    传空猜测值，图表会保持干净。``label`` 为空时不画文字，只保留淡色带。
    """

    panel: int
    low: float
    high: float
    label: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.panel, bool) or not isinstance(self.panel, int):
            raise TypeError("目标带 panel 必须是整数")
        if not 0 <= self.panel < len(_PLOT_PANELS):
            raise ValueError("目标带 panel 必须是 0–3")
        for name, value in (("low", self.low), ("high", self.high)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"目标带 {name} 必须是数值")
            if not math.isfinite(float(value)):
                raise ValueError(f"目标带 {name} 必须是有限数值")
        if float(self.low) > float(self.high):
            raise ValueError("目标带 low 不能大于 high")
        if not isinstance(self.label, str):
            raise TypeError("目标带 label 必须是字符串")


def _numeric_list(values: Any) -> list[float] | None:
    """把一段序列读成有限浮点列表；空序列或任一非法值返回 ``None``。"""
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        return None
    numbers: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        numbers.append(number)
    return numbers or None


def _indices(values: list[float] | None) -> list[float] | None:
    """没有横轴数据时用测量点序号作横轴。"""
    return None if values is None else [float(index) for index in range(len(values))]


def _pair(
    xs: list[float] | None, ys: list[float] | None
) -> tuple[list[float], list[float]] | None:
    """截到共同长度后配对；任一为空时返回 ``None``。"""
    if xs is None or ys is None:
        return None
    length = min(len(xs), len(ys))
    return (xs[:length], ys[:length]) if length else None


def _nice_step(span: float, tick_count: int = _VIEW_TICK_COUNT) -> float:
    """返回 1/2/2.5/5 乘以十的整数次幂的“好看”步长，让边界落在刻度上。"""
    span = abs(float(span))
    if not math.isfinite(span) or span <= 0.0:
        return 1.0
    raw = span / max(1, int(tick_count))
    magnitude = 1.0
    # 用乘除十把 ``raw`` 归一到 [1, 10)，避免引入单位换算里被禁止的幂与对数。
    while raw >= 10.0:
        raw /= 10.0
        magnitude *= 10.0
    while raw < 1.0:
        raw *= 10.0
        magnitude /= 10.0
    for factor in (1.0, 2.0, 2.5, 5.0, 10.0):
        if raw <= factor * (1.0 + 1e-12):
            return factor * magnitude
    return 10.0 * magnitude


def _padded_bounds(
    values: Sequence[float],
    *,
    pad_ratio: float = _VIEW_PAD_RATIO,
    min_pad: float = _VIEW_MIN_PAD,
    current: tuple[float, float] | None = None,
) -> tuple[float, float]:
    """给数据范围留出余量并向外取整，让后续若干个点不用再次扩界。

    ``pad_ratio`` 按 span 留白（常数序列改用数值比例与 ``min_pad`` 取较大者），
    再把上下界向外取整到一个好看的刻度步长：一次扩界就能覆盖后面一段扫描，
    而不是每个新点都触发一次整图重绘。``current`` 是当前视窗，扩界只增不减：
    新频点的数据范围比旧视窗更窄时，坐标轴不会突然缩回去再弹开。
    """
    low = float(min(values))
    high = float(max(values))
    span = high - low
    if span <= 0.0:
        pad = max(abs(high) * 0.2, min_pad)
    else:
        pad = max(span * pad_ratio, min_pad)
    low -= pad
    high += pad
    if current is not None:
        low = min(low, float(current[0]))
        high = max(high, float(current[1]))
    step = _nice_step(high - low)
    low = math.floor(low / step) * step
    high = math.ceil(high / step) * step
    if high <= low:  # 极端浮点退化时的兜底，matplotlib 不接受零宽视窗。
        high = low + step
    return low, high


class RealTimePlotWidget(QWidget):
    """Plot the common sweep data shape used by measurement pages."""

    prev_clicked = Signal()
    next_clicked = Signal()
    #: 用户点击胶片条上的频点；载荷是频率（GHz），页面负责切换当前频点。
    frequency_selected = Signal(float)

    #: 重绘节流：实时点按频点到达时最多每 100ms 真正画一次，坐标轴仍立即更新。
    DRAW_INTERVAL_MS = 100

    def __init__(self, show_nav_buttons=False, panels=None):
        super().__init__()
        self.setObjectName("plotPanel")
        self.show_nav_buttons = show_nav_buttons
        self._panel_keys = _panel_keys(panels)
        self.figure: Figure | None = None
        self.canvas: _DeferredCanvas | None = None
        # 不用 constrained layout：背景重建只在切页/尺寸变化/量程扩展时发生，固定
        # 边距能把这一次重建里的布局开销省掉；数据更新走 blit，不经过布局引擎。
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.NoFrame)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._canvas_host = QWidget()
        self._canvas_layout = QVBoxLayout(self._canvas_host)
        self._canvas_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area.setWidget(self._canvas_host)
        self._panels: list[_PanelArtists] = []
        self._axes: list[Any] = []
        self._target_bands: tuple[PlotTargetBand, ...] = ()
        self._background: Any | None = None
        self._background_size: tuple[int, int] = (0, 0)
        self._overlay_regions: list[Any] = []
        self._static_dirty = True
        self._render_pending = False
        # 增益图的 x 轴当前是否显示“输出功率”；没有输出功率时退回“测量点”。
        self._output_axis = True
        self._draw_timer = QTimer(self)
        self._draw_timer.setSingleShot(True)
        self._draw_timer.setInterval(self.DRAW_INTERVAL_MS)
        self._draw_timer.timeout.connect(self._flush_draw)
        self._draw_ready = True
        self._draw_pending = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(8)
        if show_nav_buttons:
            row = QHBoxLayout()
            row.setSpacing(8)
            nav_label = QLabel("频点浏览")
            nav_label.setProperty("role", "hint")
            row.addWidget(nav_label)
            self.nav_prev_btn = QPushButton("◀")
            self.nav_next_btn = QPushButton("▶")
            for button in (self.nav_prev_btn, self.nav_next_btn):
                set_button_variant(button, "secondary")
                button.setMinimumSize(34, 28)
                button.setMaximumSize(34, 28)
            self.nav_prev_btn.setToolTip("上一个频点")
            self.nav_next_btn.setToolTip("下一个频点")
            self.nav_prev_btn.clicked.connect(self.prev_clicked.emit)
            self.nav_next_btn.clicked.connect(self.next_clicked.emit)
            row.addWidget(self.nav_prev_btn)
            self.frequency_filmstrip = FrequencyFilmstrip(self)
            self.frequency_filmstrip.frequency_selected.connect(
                self.frequency_selected.emit
            )
            row.addWidget(self.frequency_filmstrip, 1)
            row.addWidget(self.nav_next_btn)
            layout.addLayout(row)
        layout.addWidget(self.scroll_area, 1)
        self.setFocusPolicy(Qt.WheelFocus)
        self._build_figure(self._panel_keys)

    # -- 面板集合 -----------------------------------------------------------

    def set_panels(self, panels: Sequence[str]) -> None:
        """切换要显示的面板集合；集合相同时不重建画布。

        重建会清掉已画曲线与目标带，等待下一次 ``update_plot`` 重新填入；页面在加载
        结果、确认测量类型后调用，因此不会丢掉同一份结果已经画好的数据。
        """
        keys = _panel_keys(panels)
        if keys == self._panel_keys:
            return
        self._panel_keys = keys
        self._build_figure(keys)

    def panel_keys(self) -> tuple[str, ...]:
        """当前显示的面板集合（只读）。"""
        return self._panel_keys

    def _build_figure(self, panel_keys: tuple[str, ...]) -> None:
        """按面板集合重建画布：两张图排一行，四张图排 2×2。"""
        self._discard_canvas()
        figure = Figure(figsize=(6.4, 4.8), dpi=100)
        figure.set_facecolor(TOKENS["surface"])
        canvas = _DeferredCanvas(figure)
        canvas.bind_owner(self)
        canvas.setMinimumSize(480, 340)
        count = len(panel_keys)
        columns = 2 if count > 1 else 1
        rows = math.ceil(count / columns)
        figure.subplots(rows, columns, squeeze=False)
        axes = list(figure.axes)
        for axis in axes[count:]:
            axis.set_visible(False)
        # 先把新画布装进控件状态，再挂到可见布局上：挂载会同步触发 showEvent，
        # 那时 ``on_canvas_shown`` 必须看到的是这一张画布，而不是刚丢掉的旧画布。
        self.figure = figure
        self.canvas = canvas
        self._axes = axes[:count]
        self._panels = []
        self._target_bands = ()
        self._background = None
        self._background_size = (0, 0)
        self._overlay_regions = []
        self._output_axis = True
        self._canvas_layout.addWidget(canvas)
        canvas.installEventFilter(self)
        self._attach_axes()
        self.setup_empty_plots()

    def _discard_canvas(self) -> None:
        """换面板时丢掉旧画布；旧画布的重建定时器必须先停，不能再整图绘制。"""
        canvas = self.canvas
        if canvas is None:
            return
        canvas.cancel_rebuild()
        canvas.removeEventFilter(self)
        self._canvas_layout.removeWidget(canvas)
        canvas.setParent(None)
        canvas.deleteLater()

    def _attach_axes(self) -> None:
        """按当前面板暴露 ``ax1``…``axN``；切到更少面板时删掉多余属性。"""
        for index, axis in enumerate(self._axes, start=1):
            setattr(self, f"ax{index}", axis)
        for index in range(len(self._axes) + 1, len(_PLOT_PANELS) + 1):
            if hasattr(self, f"ax{index}"):
                delattr(self, f"ax{index}")

    # -- 样式 ---------------------------------------------------------------

    @staticmethod
    def _style_axis(axis, xlabel: str, ylabel: str) -> None:
        """统一四张图的字体、网格与边框；中文字体必须逐次指定。

        不画标题：加粗标题在小面板里会挤掉坐标区，横纵轴标签才是读图的主线索，
        因此改用主题最深的文字色并稍微放大。
        """
        axis.set_facecolor(TOKENS["surface"])
        axis.set_xlabel(
            xlabel,
            color=_AXIS_LABEL_COLOR,
            fontdict={**_CJK_FONT, "size": _AXIS_LABEL_SIZE},
        )
        axis.set_ylabel(
            ylabel,
            color=_AXIS_LABEL_COLOR,
            fontdict={**_CJK_FONT, "size": _AXIS_LABEL_SIZE},
        )
        axis.grid(True, color=_GRID_COLOR, linewidth=0.8)
        axis.set_axisbelow(True)
        axis.tick_params(labelsize=7.5, colors=_TICK_COLOR, length=3)
        for spine in axis.spines.values():
            spine.set_color(_SPINE_COLOR)
            spine.set_linewidth(0.8)

    def _apply_layout(self) -> None:
        """按画布尺寸设固定边距与间距：小窗口也不让轴标签和标注互相压住。

        右侧额外留出数值标注的位置：标注锚在最后一个数据点上、再向右偏 7px，最右
        一列的标注会落在坐标区之外；没有这段余量时它会被画布边缘裁掉。行/列间距按
        “至少 N 像素”反推 hspace/wspace：窗口变小时间距不会跟着缩到文字重叠。
        标题去掉后上沿只需容纳刻度，行间距也只需容纳下一排的 x 轴标签。
        """
        width = max(int(self.canvas.width()), 1)
        height = max(int(self.canvas.height()), 1)
        left = max(0.08, 54.0 / width)
        right = 1.0 - max(0.06, 46.0 / width)
        top = 1.0 - max(0.05, 20.0 / height)
        bottom = max(0.10, 44.0 / height)
        usable_width = max(1.0, (right - left) * width)
        usable_height = max(1.0, (top - bottom) * height)
        # 列间距要同时容纳左列的数值标注与右列的 y 轴标签；行间距要容纳上排的
        # x 轴标签。matplotlib 的 *space 是“占平均轴尺寸的比例”。
        wspace = max(0.24, 2.0 * 88.0 / max(1.0, usable_width - 88.0))
        hspace = max(0.30, 2.0 * 52.0 / max(1.0, usable_height - 52.0))
        self.figure.subplots_adjust(
            left=left,
            right=right,
            top=top,
            bottom=bottom,
            wspace=wspace,
            hspace=hspace,
        )

    def setup_empty_plots(self):
        """建立当前面板集合的子图：轴、图例和“等待数据”只建一次，之后只改数据。"""
        self._apply_layout()
        self._panels = [
            self._build_panel(key, axis)
            for key, axis in zip(self._panel_keys, self._axes)
        ]
        self._static_dirty = True
        # 构造时控件还不可见：draw_idle 只记待办，第一次上屏时再 blit / 重建。
        self.canvas.draw_idle()

    def _build_panel(self, key: str, axis) -> _PanelArtists:
        """一个面板的静态件与空动态件；动态件先隐藏，有数据后再显示。"""
        panel_spec = _PANEL_SPECS[key]
        self._style_axis(axis, panel_spec.xlabel, panel_spec.ylabel)
        axis.set_xlim(0.0, 1.0)
        axis.set_ylim(0.0, 1.0)
        line = axis.plot(
            [],
            [],
            color=panel_spec.color,
            marker="o",
            markersize=3,
            linewidth=1.6,
            label=f"{panel_spec.metric} ({panel_spec.unit})",
            zorder=2,
        )[0]
        marker = axis.plot(
            [],
            [],
            linestyle="none",
            marker="o",
            markersize=7.5,
            markerfacecolor=TOKENS["surface"],
            markeredgecolor=panel_spec.color,
            markeredgewidth=1.4,
            zorder=5,
        )[0]
        legend = axis.legend(
            [line],
            [f"{panel_spec.metric} — {panel_spec.unit}"],
            loc="upper right",
            fontsize=7.2,
            frameon=True,
            facecolor=TOKENS["surface"],
            edgecolor=TOKENS["border"],
            # 不透明：图例盖住曲线时，blit 恢复的图例区域与整图绘制逐像素一致。
            framealpha=1.0,
            handlelength=1.1,
            handletextpad=0.4,
            borderpad=0.3,
            labelspacing=0.2,
        )
        for text in legend.get_texts():
            text.set_color(TOKENS["text_secondary"])
            text.set_fontfamily(_CJK)
        line.set_visible(False)
        marker.set_visible(False)
        return _PanelArtists(
            axis=axis,
            spec=panel_spec,
            line=line,
            marker=marker,
            legend=legend,
            hint=self._add_waiting_hint(axis),
        )

    # -- 目标带 -------------------------------------------------------------

    def set_target_bands(self, bands: Sequence[PlotTargetBand]) -> None:
        """写入目标带；空序列清空。目标带属于静态层，变化时重建背景。"""
        if isinstance(bands, (str, bytes)) or not isinstance(bands, Sequence):
            raise TypeError("目标带必须是 PlotTargetBand 序列")
        validated: list[PlotTargetBand] = []
        for band in bands:
            if not isinstance(band, PlotTargetBand):
                raise TypeError("目标带必须是 PlotTargetBand 序列")
            validated.append(band)
        cleaned = tuple(validated)
        if cleaned == self._target_bands:
            return
        self._target_bands = cleaned
        for index, panel in enumerate(self._panels):
            self._remove_bands(panel)
            self._add_target_bands(panel, index)
        # 目标带属于静态层：先置脏，等随后的 update_plot 或防抖重建一起画，
        # 避免“改带一次整图 + 换数据再一次整图”的重复开销。
        self.invalidate_background()

    def target_bands(self) -> tuple[PlotTargetBand, ...]:
        """当前写入的目标带（只读）。"""
        return self._target_bands

    @staticmethod
    def _remove_bands(panel: _PanelArtists) -> None:
        for artist in panel.bands:
            artist.remove()
        panel.bands.clear()

    def _add_target_bands(self, panel: _PanelArtists, panel_index: int) -> None:
        """画淡色水平带；没有目标时什么都不做。"""
        axis = panel.axis
        for band in self._target_bands:
            if band.panel != panel_index:
                continue
            color = TOKENS["primary"]
            panel.bands.append(
                axis.axhspan(
                    float(band.low),
                    float(band.high),
                    facecolor=color,
                    alpha=0.08,
                    edgecolor=color,
                    linewidth=0.0,
                    zorder=0,
                )
            )
            if band.label:
                panel.bands.append(
                    axis.text(
                        0.99,
                        float(band.high),
                        band.label,
                        transform=axis.get_yaxis_transform(),
                        ha="right",
                        va="bottom",
                        fontsize=7.0,
                        color=color,
                        alpha=0.9,
                        family=_CJK,
                    )
                )

    # -- 数据 ---------------------------------------------------------------

    @staticmethod
    def _add_waiting_hint(axis):
        """空面板的“等待数据”；它属于静态背景，数据到达时移除并重建一次。"""
        return axis.text(
            0.5,
            0.52,
            "等待数据",
            transform=axis.transAxes,
            ha="center",
            va="center",
            fontsize=8.5,
            color=TOKENS["text_muted"],
            family=_CJK,
        )

    def update_plot(self, data: Dict[str, Any]):
        try:
            series: dict[str, tuple[list[float], list[float]] | None]
            has_output = False
            if "series" in data:
                # 调用方已经按面板键准备好 (x, y) 序列（线损页的“线损 vs 频率”）。
                series = self._direct_series(data.get("series"))
            else:
                sweep = data.get("sweep_data", {})
                if not isinstance(sweep, Mapping):
                    sweep = {}
                series, has_output = self._sweep_series(sweep)
            static_changed = False
            for panel in self._panels:
                static_changed = (
                    self._apply_series(panel, series.get(panel.spec.key))
                    or static_changed
                )
            # 增益图的横轴是输出功率；没有输出功率时退回“测量点”。面板不在时跳过。
            gain_panel = self._panel("gain")
            if gain_panel is not None and has_output != self._output_axis:
                self._output_axis = has_output
                self._set_output_axis_label(gain_panel)
                static_changed = True
            if static_changed:
                self._static_dirty = True
            self._request_draw()
        except Exception as error:  # noqa: BLE001 - 绘图失败不得中断测量线程
            # 绘图是展示副作用：失败记日志并保持上一帧，不向测量线程抛出。
            logger.warning("绘图更新失败: %s", error)

    def _direct_series(
        self, series: Any
    ) -> dict[str, tuple[list[float], list[float]] | None]:
        """把调用方给的面板→(x, y) 映射读成有限数值对；任何非法项按“无数据”处理。"""
        if not isinstance(series, Mapping):
            return {}
        prepared: dict[str, tuple[list[float], list[float]] | None] = {}
        for key in self._panel_keys:
            value = series.get(key)
            if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
                prepared[key] = None
                continue
            if len(value) != 2:
                prepared[key] = None
                continue
            prepared[key] = _pair(_numeric_list(value[0]), _numeric_list(value[1]))
        return prepared

    @staticmethod
    def _sweep_series(
        sweep: Mapping[str, Any],
    ) -> tuple[dict[str, tuple[list[float], list[float]] | None], bool]:
        """把一次扫频拆成各面板的数据对；没有输出功率时返回 ``False``。"""
        pin = _numeric_list(sweep.get("input_power_dut", sweep.get("input_power_sg")))
        pout = _numeric_list(
            sweep.get("output_power_dut", sweep.get("output_power_driver"))
        )
        gain = _numeric_list(sweep.get("gain"))
        efficiency = _numeric_list(sweep.get("efficiency"))
        dc_power = _numeric_list(sweep.get("dc_power"))
        series = {
            "input_output": _pair(pin, pout),
            "gain": (
                _pair(pout, gain) if pout is not None else _pair(_indices(gain), gain)
            ),
            "efficiency": _pair(pout, efficiency),
            "dc_power": _pair(_indices(dc_power), dc_power),
        }
        return series, pout is not None

    def _panel(self, key: str) -> _PanelArtists | None:
        """按面板名取当前显示的艺术家集合；面板被收窄时返回 ``None``。"""
        for panel in self._panels:
            if panel.spec.key == key:
                return panel
        return None

    def _set_output_axis_label(self, panel: _PanelArtists) -> None:
        """增益图的横轴在“输出功率”和“测量点”之间切换。"""
        panel.axis.set_xlabel(
            "输出功率 (dBm)" if self._output_axis else "测量点",
            color=_TICK_COLOR,
            fontdict={**_CJK_FONT, "size": 8.5},
        )

    def _apply_series(
        self,
        panel: _PanelArtists,
        series: tuple[list[float], list[float]] | None,
    ) -> bool:
        """把一条扫频写进已有艺术家；返回“静态背景是否需要重建”。

        有数据时只改曲线、最新点圆环与数值标注的内容和可见性；等待提示的增删、
        轴标签切换和量程扩展属于静态内容变化，各只发生一次。
        """
        if series is None:
            if not panel.has_data and panel.hint is not None:
                return False
            panel.has_data = False
            panel.line.set_visible(False)
            panel.marker.set_visible(False)
            if panel.annotation is not None:
                panel.annotation.set_visible(False)
            if panel.hint is None:
                panel.hint = self._add_waiting_hint(panel.axis)
            self._reset_view(panel)
            return True

        xs, ys = series
        static_changed = False
        if panel.hint is not None:
            panel.hint.remove()
            panel.hint = None
            static_changed = True
        if panel.annotation is None:
            panel.annotation = self._add_annotation(panel)
            static_changed = True
        panel.has_data = True
        panel.line.set_data(xs, ys)
        panel.marker.set_data([xs[-1]], [ys[-1]])
        panel.annotation.xy = (xs[-1], ys[-1])
        panel.annotation.set_text(f"{ys[-1]:.{panel.spec.digits}f}")
        for artist in (panel.line, panel.marker, panel.annotation):
            if not artist.get_visible():
                artist.set_visible(True)
        return self._expand_view(panel, xs, ys) or static_changed

    def _add_annotation(self, panel: _PanelArtists):
        """最新点旁的数值标注；没有数据的面板上不存在这个艺术家。"""
        return panel.axis.annotate(
            "",
            xy=(0.0, 0.0),
            xytext=(7, 6),
            textcoords="offset points",
            fontsize=7.2,
            fontweight="bold",
            color=panel.spec.color,
            zorder=6,
            annotation_clip=True,
            family=_CJK,
            bbox={
                "boxstyle": "round,pad=0.22",
                "facecolor": TOKENS["surface"],
                "edgecolor": panel.spec.color,
                "linewidth": 0.6,
                "alpha": 0.9,
            },
        )

    def _expand_view(
        self,
        panel: _PanelArtists,
        xs: list[float],
        ys: list[float],
    ) -> bool:
        """数据越出当前视窗才扩界；扩界一次就覆盖后面一段扫描。

        视窗只增不减：新频点的数据范围更窄时坐标轴不会缩回去。因此一条扫频曲线
        的早期扩张只重绘两三次，之后的新点都在同一视窗里 blit。
        """
        x_low, x_high = panel.xlim
        y_low, y_high = panel.ylim
        if (
            x_low <= min(xs)
            and max(xs) <= x_high
            and y_low <= min(ys)
            and max(ys) <= y_high
        ):
            return False
        panel.axis.set_xlim(
            *_padded_bounds(
                xs, current=panel.xlim if panel.view_from_data else None
            )
        )
        panel.axis.set_ylim(
            *_padded_bounds(
                ys, current=panel.ylim if panel.view_from_data else None
            )
        )
        panel.xlim = tuple(float(value) for value in panel.axis.get_xlim())
        panel.ylim = tuple(float(value) for value in panel.axis.get_ylim())
        panel.view_from_data = True
        return True

    @staticmethod
    def _reset_view(panel: _PanelArtists) -> None:
        panel.axis.set_xlim(0.0, 1.0)
        panel.axis.set_ylim(0.0, 1.0)
        panel.xlim = (0.0, 1.0)
        panel.ylim = (0.0, 1.0)
        panel.view_from_data = False

    # -- 频点浏览 -----------------------------------------------------------

    def set_measured_frequencies(
        self, frequencies: Sequence[float], current_index: int = 0
    ) -> None:
        """同步实时测量已经收到数据的频点。

        这里的频点语义必须与配置中的“计划频点”区分开：实时测量页的胶片条只展示
        ``RealtimeMeasurementBuffer`` 已经收到的频点，不能用测试计划预填。单独提供
        这个命名入口，让实时页面的调用点表达并约束这一语义；历史可视化仍使用自己
        的 ``FrequencyFilmstrip.set_frequencies``，不会被这条规则影响。
        """
        self.set_frequencies(frequencies, current_index)

    def set_frequencies(
        self, frequencies: Sequence[float], current_index: int = 0
    ) -> None:
        """同步胶片条；没有胶片条（未请求导航）时静默跳过。"""
        filmstrip = getattr(self, "frequency_filmstrip", None)
        if filmstrip is not None:
            filmstrip.set_frequencies(frequencies, current_index)

    # -- 背景缓存与 blit -----------------------------------------------------

    def clear_plots(self) -> None:
        """清空数据、目标带与视窗，回到“等待数据”空态（可视化页统一入口）。"""
        for panel in self._panels:
            self._remove_bands(panel)
            panel.line.set_data([], [])
            panel.line.set_visible(False)
            panel.marker.set_data([], [])
            panel.marker.set_visible(False)
            if panel.annotation is not None:
                panel.annotation.remove()
                panel.annotation = None
            if panel.hint is None:
                panel.hint = self._add_waiting_hint(panel.axis)
            panel.has_data = False
            self._reset_view(panel)
        self._target_bands = ()
        self._output_axis = True
        gain_panel = self._panel("gain")
        if gain_panel is not None:
            self._set_output_axis_label(gain_panel)
        self._static_dirty = True
        self._request_draw()

    def invalidate_background(self) -> None:
        """静态内容变了：可见时防抖重建一次，隐藏时只标记。"""
        self._static_dirty = True
        schedule = getattr(self.canvas, "schedule_rebuild", None)
        if callable(schedule):
            schedule()

    def on_canvas_shown(self) -> None:
        """页面切到前台：缓存有效就立即 blit，否则等过渡结束再重建。"""
        if self._background_valid():
            self._blit()
            return
        self.canvas.schedule_rebuild(_BACKGROUND_SHOW_DEFER_MS)

    def rebuild_background(self) -> None:
        """整图绘制一次并缓存为背景，随后 blit 动态层。"""
        if not self.canvas.isVisible():
            return
        if self._background_valid():
            # 防抖定时器可能排在一次立即重建之后：已经有效就只补一次 blit。
            self._blit()
            return
        width = int(self.canvas.width())
        height = int(self.canvas.height())
        if width <= 0 or height <= 0:
            return
        self._apply_layout()
        dynamic = [artist for panel in self._panels for artist in panel.dynamics()]
        previous = [(artist, artist.get_visible()) for artist in dynamic]
        for artist in dynamic:
            artist.set_visible(False)
        try:
            self.canvas.draw()
        finally:
            for artist, visible in previous:
                artist.set_visible(visible)
        self.canvas.clear_pending_draw()
        self._background = self.canvas.copy_from_bbox(self.figure.bbox)
        self._background_size = (width, height)
        self._overlay_regions = self._capture_overlays()
        self._static_dirty = False
        self._render_pending = False
        self._blit()

    def _capture_overlays(self) -> list[Any]:
        """截取静态层里压在数据之上的元素（图例、目标带标签）。

        这些元素留在背景缓存里，但按 zorder 必须盖住曲线；blit 时先画曲线，再把
        它们各自的区域恢复一次，既不需要每帧重画图例文字，也不会让曲线穿过图例。
        区域比元素包围盒多留 1px，避免圆角与抗锯齿在边界留下旧像素。
        """
        try:
            renderer = self.canvas.get_renderer()
        except (AttributeError, RuntimeError):
            return []
        boxes: list[Any] = []
        for panel in self._panels:
            try:
                boxes.append(panel.legend.get_window_extent(renderer).padded(1.0))
            except (AttributeError, RuntimeError, ValueError):
                pass
            for artist in panel.bands:
                if not isinstance(artist, Text) or not artist.get_visible():
                    continue
                try:
                    boxes.append(artist.get_window_extent(renderer).padded(1.0))
                except (AttributeError, RuntimeError, ValueError):
                    pass
        regions: list[Any] = []
        for box in boxes:
            try:
                regions.append(self.canvas.copy_from_bbox(box))
            except (AttributeError, RuntimeError, ValueError):
                continue
        return regions

    def _background_valid(self) -> bool:
        """背景缓存是否覆盖当前画布尺寸且没有静态改动。"""
        return (
            self._background is not None
            and not self._static_dirty
            and self._background_size
            == (int(self.canvas.width()), int(self.canvas.height()))
        )

    def _blit(self) -> None:
        """恢复背景后只重画动态层；背景失效时退回一次重建。"""
        if not self._background_valid():
            self.rebuild_background()
            return
        self.canvas.restore_region(self._background)
        for panel in self._panels:
            for artist in (panel.line, panel.marker):
                if artist.get_visible():
                    self.figure.draw_artist(artist)
        # 图例/目标带标签在完整绘制里盖住曲线：先画曲线，再恢复它们的区域，
        # 避免曲线穿过图例；数值标注的 zorder 更高，最后画在它们之上。
        for region in self._overlay_regions:
            self.canvas.restore_region(region)
        for panel in self._panels:
            annotation = panel.annotation
            if annotation is not None and annotation.get_visible():
                self.figure.draw_artist(annotation)
        self.canvas.blit(self.figure.bbox)

    def _render(self) -> None:
        """真正渲染一帧：隐藏只记待办；可见时 blit，背景失效才重建。"""
        if not self.canvas.isVisible():
            self._render_pending = True
            return
        if self._background_valid():
            self._render_pending = False
            self._blit()
        else:
            self.rebuild_background()

    # -- 重绘节流 -----------------------------------------------------------

    def _request_draw(self) -> None:
        """最多每 :attr:`DRAW_INTERVAL_MS` 真正渲染一次，期间只记住“还要画”。"""
        if self._draw_ready:
            self._draw_ready = False
            self._render()
            self._draw_timer.start()
        else:
            self._draw_pending = True

    def _flush_draw(self) -> None:
        self._draw_ready = True
        if self._draw_pending:
            self._draw_pending = False
            self._request_draw()

    def wheelEvent(self, event):
        bar = self.scroll_area.verticalScrollBar()
        step = 50
        delta = -step if event.angleDelta().y() > 0 else step
        bar.setValue(max(bar.minimum(), min(bar.maximum(), bar.value() + delta)))
        event.accept()

    def eventFilter(self, source, event):
        if source == self.canvas and event.type() == QEvent.Type.Wheel:
            self.wheelEvent(event)
            return True
        return super().eventFilter(source, event)
