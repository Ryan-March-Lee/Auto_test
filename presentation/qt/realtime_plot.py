"""测量页面共享的实时绘图控件。

绘图只做展示：四张图固定为“输入-输出功率 / 增益 / 效率 / DC 功耗”，配色与字体
统一来自 :mod:`presentation.qt.theme`，因此测量页与历史可视化页的图看起来是同一套。
"""

from typing import Any, Dict

from PySide6.QtCore import QEvent, Qt, Signal
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

from .theme import TOKENS, set_button_variant

logger = get_logger(__name__)

# 中文字体：matplotlib 默认字体没有中文字形，标题必须显式指定，否则显示为方框。
_CJK_FONT = {"family": "Microsoft YaHei"}
_GRID_COLOR = TOKENS["table_grid"]
_SPINE_COLOR = TOKENS["border_strong"]
_TICK_COLOR = TOKENS["text_secondary"]
_TITLE_COLOR = TOKENS["text"]

# 四张图固定顺序：标题、X 轴、Y 轴、曲线颜色。
_PLOT_PANELS = (
    ("输入功率 vs 输出功率", "输入功率 (dBm)", "输出功率 (dBm)", TOKENS["series_1"]),
    ("输出功率 vs 增益", "输出功率 (dBm)", "增益 (dB)", TOKENS["series_2"]),
    ("输出功率 vs 效率", "输出功率 (dBm)", "效率 (%)", TOKENS["series_3"]),
    ("DC功耗", "测量点", "DC功耗 (W)", TOKENS["series_4"]),
)


class RealTimePlotWidget(QWidget):
    """Plot the common sweep data shape used by measurement pages."""

    prev_clicked = Signal()
    next_clicked = Signal()

    def __init__(self, show_nav_buttons=False):
        super().__init__()
        self.setObjectName("plotPanel")
        self.show_nav_buttons = show_nav_buttons
        # constrained layout 让四张图自动均分间距，避免紧贴边界或留出大块空白。
        self.figure = Figure(figsize=(6.4, 4.8), dpi=100, layout="constrained")
        self.figure.set_facecolor(TOKENS["surface"])
        self.canvas = FigureCanvas(self.figure)
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
            row.addWidget(self.nav_next_btn)
            row.addStretch()
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
        axis.set_title(title, color=_TITLE_COLOR, fontdict={**_CJK_FONT, "size": 10.5, "fontweight": "bold"})
        axis.set_xlabel(xlabel, color=_TICK_COLOR, fontdict={**_CJK_FONT, "size": 8.5})
        axis.set_ylabel(ylabel, color=_TICK_COLOR, fontdict={**_CJK_FONT, "size": 8.5})
        axis.grid(True, color=_GRID_COLOR, linewidth=0.8)
        axis.set_axisbelow(True)
        axis.tick_params(labelsize=7.5, colors=_TICK_COLOR, length=3)
        for spine in axis.spines.values():
            spine.set_color(_SPINE_COLOR)
            spine.set_linewidth(0.8)

    def setup_empty_plots(self):
        for axis, (title, xlabel, ylabel, _color) in zip(self.figure.axes, _PLOT_PANELS):
            self._style_axis(axis, title, xlabel, ylabel)
        # 空白图延后到控件第一次上屏时再渲染：构造窗口不必同步绘制三张空图，
        # 启动与离屏测试都不再为看不见的画面付出 constrained layout 的绘制开销。
        self.canvas.draw_idle()

    def update_plot(self, data: Dict[str, Any]):
        try:
            for axis in self.figure.axes:
                axis.clear()
            sweep = data.get("sweep_data", {})
            frequency = data.get("frequency", "Unknown")
            pin = sweep.get("input_power_dut", sweep.get("input_power_sg"))
            pout = sweep.get("output_power_dut", sweep.get("output_power_driver"))
            if pin is not None and pout is not None:
                self.ax1.plot(pin, pout, color=_PLOT_PANELS[0][3], marker="o", markersize=3, linewidth=1.6)
            if sweep.get("gain") is not None:
                x_values = pout if pout is not None else range(len(sweep["gain"]))
                self.ax2.plot(x_values, sweep["gain"], color=_PLOT_PANELS[1][3], marker="o", markersize=3, linewidth=1.6)
            if sweep.get("efficiency") is not None and pout is not None:
                self.ax3.plot(pout, sweep["efficiency"], color=_PLOT_PANELS[2][3], marker="o", markersize=3, linewidth=1.6)
            if sweep.get("dc_power") is not None:
                self.ax4.plot(
                    range(len(sweep["dc_power"])), sweep["dc_power"],
                    color=_PLOT_PANELS[3][3], marker="o", markersize=3, linewidth=1.6,
                )
            titles = (
                f"输入 vs 输出功率 @ {frequency} GHz",
                f"增益 @ {frequency} GHz",
                f"效率 @ {frequency} GHz",
                f"DC功耗 @ {frequency} GHz",
            )
            for axis, (default_title, xlabel, ylabel, _color), title in zip(
                self.figure.axes, _PLOT_PANELS, titles
            ):
                self._style_axis(axis, title, xlabel, ylabel)
            # 输入-输出图的横轴就是输入功率；其余两图横轴随数据而定，保持默认标签。
            self.ax2.set_xlabel(
                "输出功率 (dBm)" if pout is not None else "测量点",
                color=_TICK_COLOR, fontdict={**_CJK_FONT, "size": 8.5},
            )
            self.canvas.draw()
        except Exception as error:  # noqa: BLE001 - 绘图失败不得中断测量线程
            # 绘图是展示副作用：失败记日志并保持上一帧，不向测量线程抛出。
            logger.warning("绘图更新失败: %s", error)

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
