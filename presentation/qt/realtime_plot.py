"""测量页面共享的实时绘图控件。

绘图只做展示：四张图固定为“输入-输出功率 / 增益 / 效率 / DC 功耗”，配色与字体
统一来自 :mod:`presentation.qt.theme`，因此测量页与历史可视化页的图看起来是同一套。

P2 在原有 2×2 布局上补三种“会说话”的读法，全部只消费调用方已经取好的数据：

- **最新点标注**：每条曲线最后一个点加一个空心圆环，并在点旁标出数值；
- **图例即值芯片**：每个子图右上角一个紧凑图例，显示“指标 最新值 单位”，
  颜色与曲线一致，不需要再猜哪条线是哪条；
- **目标带**：调用方明确给出目标范围时才画一条淡色水平带。没有目标值时不画，
  这里不发明任何业务语义。

频点胶片条（``show_nav_buttons=True`` 时）把“◀ / ▶”扩展成可点击的频点带；
重绘按 10 Hz 节流，隐藏时仍沿用 :class:`_DeferredCanvas` 的“不排队绘制”策略。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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

from infrastructure.logging.app_logging import get_logger

from .frequency_filmstrip import FrequencyFilmstrip
from .theme import TOKENS, set_button_variant

logger = get_logger(__name__)

__all__ = ["PlotTargetBand", "RealTimePlotWidget"]


class _DeferredCanvas(FigureCanvas):
    """画布不可见时只记下“需要重画”，等真正显示出来再画。

    页面切换会让隐藏的标签页跟着重排，matplotlib 的 ``draw_idle`` 会立刻排队
    重画；一张四宫格图在这台机器上要三四百毫秒，正好落在“仪器排布”页的入场 /
    返回动画里，把动画卡停。隐藏期间不排队，标签页第一次显示时再补一张，顺带
    省掉启动时为看不见的图付出的绘制时间。
    """

    def draw_idle(self) -> None:
        if not self.isVisible():
            self._draw_pending = True
            return
        super().draw_idle()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if getattr(self, "_draw_pending", False):
            # matplotlib 的 draw_idle 看到这个标志会直接返回，先清掉再排队。
            self._draw_pending = False
            super().draw_idle()


# 中文字体：matplotlib 默认字体没有中文字形，标题必须显式指定，否则显示为方框。
_CJK_FONT = {"family": "Microsoft YaHei"}
_CJK = _CJK_FONT["family"]
_GRID_COLOR = TOKENS["table_grid"]
_SPINE_COLOR = TOKENS["border_strong"]
_TICK_COLOR = TOKENS["text_secondary"]
_TITLE_COLOR = TOKENS["text"]


@dataclass(frozen=True)
class _PanelSpec:
    """一个子图的固定语义：标题、轴标签、系列色与最新值读数。"""

    title: str
    xlabel: str
    ylabel: str
    color: str
    metric: str
    unit: str
    digits: int


# 四张图固定顺序：标题、X 轴、Y 轴、曲线颜色、读数标签、单位与小数位。
_PLOT_PANELS = (
    _PanelSpec(
        "输入功率 vs 输出功率",
        "输入功率 (dBm)",
        "输出功率 (dBm)",
        TOKENS["series_1"],
        "输出功率",
        "dBm",
        1,
    ),
    _PanelSpec(
        "输出功率 vs 增益",
        "输出功率 (dBm)",
        "增益 (dB)",
        TOKENS["series_2"],
        "增益",
        "dB",
        1,
    ),
    _PanelSpec(
        "输出功率 vs 效率",
        "输出功率 (dBm)",
        "效率 (%)",
        TOKENS["series_3"],
        "效率",
        "%",
        1,
    ),
    _PanelSpec(
        "DC功耗",
        "测量点",
        "DC功耗 (W)",
        TOKENS["series_4"],
        "DC功耗",
        "W",
        2,
    ),
)


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


class RealTimePlotWidget(QWidget):
    """Plot the common sweep data shape used by measurement pages."""

    prev_clicked = Signal()
    next_clicked = Signal()
    #: 用户点击胶片条上的频点；载荷是频率（GHz），页面负责切换当前频点。
    frequency_selected = Signal(float)

    #: 重绘节流：实时点按频点到达时最多每 100ms 真正画一次，坐标轴仍立即更新。
    DRAW_INTERVAL_MS = 100

    def __init__(self, show_nav_buttons=False):
        super().__init__()
        self.setObjectName("plotPanel")
        self.show_nav_buttons = show_nav_buttons
        # constrained layout 让四张图自动均分间距，避免紧贴边界或留出大块空白。
        self.figure = Figure(figsize=(6.4, 4.8), dpi=100, layout="constrained")
        self.figure.set_facecolor(TOKENS["surface"])
        self.canvas = _DeferredCanvas(self.figure)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QScrollArea.NoFrame)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        canvas_widget = QWidget()
        canvas_layout = QVBoxLayout(canvas_widget)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.addWidget(self.canvas)
        self.scroll_area.setWidget(canvas_widget)
        self.canvas.setMinimumSize(480, 340)
        self.figure.subplots(2, 2)
        axes = self.figure.axes
        self.ax1, self.ax2, self.ax3, self.ax4 = axes
        self._target_bands: tuple[PlotTargetBand, ...] = ()
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
        self.canvas.installEventFilter(self)
        self.setup_empty_plots()

    # -- 样式 ---------------------------------------------------------------

    @staticmethod
    def _style_axis(axis, title: str, xlabel: str, ylabel: str) -> None:
        """统一四张图的字体、网格与边框；中文字体必须逐次指定。"""
        axis.set_facecolor(TOKENS["surface"])
        axis.set_title(
            title,
            color=_TITLE_COLOR,
            fontdict={**_CJK_FONT, "size": 10.5, "fontweight": "bold"},
        )
        axis.set_xlabel(xlabel, color=_TICK_COLOR, fontdict={**_CJK_FONT, "size": 8.5})
        axis.set_ylabel(ylabel, color=_TICK_COLOR, fontdict={**_CJK_FONT, "size": 8.5})
        axis.grid(True, color=_GRID_COLOR, linewidth=0.8)
        axis.set_axisbelow(True)
        axis.tick_params(labelsize=7.5, colors=_TICK_COLOR, length=3)
        for spine in axis.spines.values():
            spine.set_color(_SPINE_COLOR)
            spine.set_linewidth(0.8)

    def setup_empty_plots(self):
        for index, (axis, panel) in enumerate(zip(self.figure.axes, _PLOT_PANELS)):
            self._style_axis(axis, panel.title, panel.xlabel, panel.ylabel)
            self._paint_waiting_hint(axis)
            self._apply_target_bands(axis, index)
        # 空白图延后到控件第一次上屏时再渲染：构造窗口不必同步绘制三张空图，
        # 启动与离屏测试都不再为看不见的画面付出 constrained layout 的绘制开销。
        self.canvas.draw_idle()

    # -- 目标带 -------------------------------------------------------------

    def set_target_bands(self, bands: Sequence[PlotTargetBand]) -> None:
        """写入目标带；空序列清空。只接受已经验证过的 :class:`PlotTargetBand`。"""
        if isinstance(bands, (str, bytes)) or not isinstance(bands, Sequence):
            raise TypeError("目标带必须是 PlotTargetBand 序列")
        validated: list[PlotTargetBand] = []
        for band in bands:
            if not isinstance(band, PlotTargetBand):
                raise TypeError("目标带必须是 PlotTargetBand 序列")
            validated.append(band)
        self._target_bands = tuple(validated)

    def target_bands(self) -> tuple[PlotTargetBand, ...]:
        """当前写入的目标带（只读）。"""
        return self._target_bands

    def _apply_target_bands(self, axis, panel_index: int) -> None:
        """画淡色水平带；没有目标时什么都不做。"""
        for band in self._target_bands:
            if band.panel != panel_index:
                continue
            color = TOKENS["primary"]
            axis.axhspan(
                float(band.low),
                float(band.high),
                facecolor=color,
                alpha=0.08,
                edgecolor=color,
                linewidth=0.0,
                zorder=0,
            )
            if band.label:
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

    # -- 数据 ---------------------------------------------------------------

    @staticmethod
    def _paint_waiting_hint(axis) -> None:
        axis.text(
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
            for axis in self.figure.axes:
                axis.clear()
            sweep = data.get("sweep_data", {})
            if not isinstance(sweep, Mapping):
                sweep = {}
            frequency = data.get("frequency", "Unknown")
            pin = _numeric_list(
                sweep.get("input_power_dut", sweep.get("input_power_sg"))
            )
            pout = _numeric_list(
                sweep.get("output_power_dut", sweep.get("output_power_driver"))
            )
            gain = _numeric_list(sweep.get("gain"))
            efficiency = _numeric_list(sweep.get("efficiency"))
            dc_power = _numeric_list(sweep.get("dc_power"))
            points = (
                _pair(pin, pout),
                _pair(pout, gain) if pout is not None else _pair(_indices(gain), gain),
                _pair(pout, efficiency),
                _pair(_indices(dc_power), dc_power),
            )
            titles = (
                f"输入 vs 输出功率 @ {frequency} GHz",
                f"增益 @ {frequency} GHz",
                f"效率 @ {frequency} GHz",
                f"DC功耗 @ {frequency} GHz",
            )
            for index, (axis, panel, title, series) in enumerate(
                zip(self.figure.axes, _PLOT_PANELS, titles, points)
            ):
                self._style_axis(axis, title, panel.xlabel, panel.ylabel)
                self._apply_target_bands(axis, index)
                if series is None:
                    self._paint_waiting_hint(axis)
                    continue
                self._draw_series(axis, panel, series)
            # 输入-输出图的横轴就是输入功率；其余两图横轴随数据而定，保持默认标签。
            self.ax2.set_xlabel(
                "输出功率 (dBm)" if pout is not None else "测量点",
                color=_TICK_COLOR,
                fontdict={**_CJK_FONT, "size": 8.5},
            )
            self._request_draw()
        except Exception as error:  # noqa: BLE001 - 绘图失败不得中断测量线程
            # 绘图是展示副作用：失败记日志并保持上一帧，不向测量线程抛出。
            logger.warning("绘图更新失败: %s", error)

    def _draw_series(
        self,
        axis,
        panel: _PanelSpec,
        series: tuple[list[float], list[float]],
    ) -> None:
        """画曲线、最新点圆环与“图例即值芯片”。"""
        xs, ys = series
        line = axis.plot(
            xs,
            ys,
            color=panel.color,
            marker="o",
            markersize=3,
            linewidth=1.6,
            label=panel.metric,
            zorder=2,
        )[0]
        latest_x, latest_y = xs[-1], ys[-1]
        axis.plot(
            [latest_x],
            [latest_y],
            linestyle="none",
            marker="o",
            markersize=7.5,
            markerfacecolor=TOKENS["surface"],
            markeredgecolor=panel.color,
            markeredgewidth=1.4,
            zorder=5,
        )
        axis.annotate(
            f"{latest_y:.{panel.digits}f}",
            xy=(latest_x, latest_y),
            xytext=(7, 6),
            textcoords="offset points",
            fontsize=7.2,
            fontweight="bold",
            color=panel.color,
            zorder=6,
            annotation_clip=True,
            family=_CJK,
            bbox={
                "boxstyle": "round,pad=0.22",
                "facecolor": TOKENS["surface"],
                "edgecolor": panel.color,
                "linewidth": 0.6,
                "alpha": 0.9,
            },
        )
        legend = axis.legend(
            [line],
            [f"{panel.metric} {latest_y:.{panel.digits}f} {panel.unit}"],
            loc="upper right",
            fontsize=7.2,
            frameon=True,
            facecolor=TOKENS["surface"],
            edgecolor=TOKENS["border"],
            framealpha=0.92,
            handlelength=1.1,
            handletextpad=0.4,
            borderpad=0.3,
            labelspacing=0.2,
        )
        for text in legend.get_texts():
            text.set_color(TOKENS["text_secondary"])
            text.set_fontfamily(_CJK)

    # -- 频点浏览 -----------------------------------------------------------

    def set_frequencies(
        self, frequencies: Sequence[float], current_index: int = 0
    ) -> None:
        """同步胶片条；没有胶片条（未请求导航）时静默跳过。"""
        filmstrip = getattr(self, "frequency_filmstrip", None)
        if filmstrip is not None:
            filmstrip.set_frequencies(frequencies, current_index)

    # -- 重绘节流 -----------------------------------------------------------

    def _request_draw(self) -> None:
        """最多每 :attr:`DRAW_INTERVAL_MS` 真正重绘一次，期间只记住“还要画”。"""
        if self._draw_ready:
            self._draw_ready = False
            self.canvas.draw_idle()
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
