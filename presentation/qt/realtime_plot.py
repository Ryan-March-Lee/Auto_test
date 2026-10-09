"""Reusable real-time measurement plot widget."""

from typing import Any, Dict

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure


class RealTimePlotWidget(QWidget):
    """Plot the common sweep data shape used by measurement pages."""

    prev_clicked = Signal()
    next_clicked = Signal()

    def __init__(self, show_nav_buttons=False):
        super().__init__()
        self.show_nav_buttons = show_nav_buttons
        self.figure = Figure(figsize=(3, 5), dpi=85)
        self.canvas = FigureCanvas(self.figure)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        canvas_widget = QWidget()
        canvas_layout = QVBoxLayout(canvas_widget)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.addWidget(self.canvas)
        self.scroll_area.setWidget(canvas_widget)
        width = int(self.figure.get_figwidth() * self.figure.get_dpi())
        height = int(self.figure.get_figheight() * self.figure.get_dpi())
        self.canvas.setMinimumSize(width, height)
        self.figure.subplots(2, 2)
        axes = self.figure.axes
        self.ax1, self.ax2, self.ax3, self.ax4 = axes
        layout = QVBoxLayout(self)
        if show_nav_buttons:
            row = QHBoxLayout()
            self.nav_prev_btn = QPushButton("◀")
            self.nav_next_btn = QPushButton("▶")
            for button in (self.nav_prev_btn, self.nav_next_btn):
                button.setMinimumSize(30, 30)
                button.setMaximumSize(30, 30)
            self.nav_prev_btn.clicked.connect(self.prev_clicked.emit)
            self.nav_next_btn.clicked.connect(self.next_clicked.emit)
            row.addWidget(self.nav_prev_btn)
            row.addWidget(self.nav_next_btn)
            row.addStretch()
            layout.addLayout(row)
        layout.addWidget(self.scroll_area)
        self.setFocusPolicy(Qt.WheelFocus)
        self.canvas.installEventFilter(self)
        self.setup_empty_plots()

    def setup_empty_plots(self):
        labels = (
            (self.ax1, "输入功率 vs 输出功率", "输入功率 (dBm)", "输出功率 (dBm)"),
            (self.ax2, "输出功率 vs 增益", "输出功率 (dBm)", "增益 (dB)"),
            (self.ax3, "输出功率 vs 效率", "输出功率 (dBm)", "效率 (%)"),
            (self.ax4, "DC功耗", "测量点", "DC功耗 (W)"),
        )
        for axis, title, xlabel, ylabel in labels:
            axis.set_title(title, fontdict={"family": "Microsoft YaHei", "size": 11})
            axis.set_xlabel(xlabel, fontdict={"family": "Microsoft YaHei", "size": 8})
            axis.set_ylabel(ylabel, fontdict={"family": "Microsoft YaHei", "size": 8})
            axis.grid(True)
            axis.tick_params(labelsize=7)
        self.figure.tight_layout()
        self.canvas.draw()

    def update_plot(self, data: Dict[str, Any]):
        try:
            for axis in self.figure.axes:
                axis.clear()
            sweep = data.get("sweep_data", {})
            frequency = data.get("frequency", "Unknown")
            pin = sweep.get("input_power_dut", sweep.get("input_power_sg"))
            pout = sweep.get("output_power_dut", sweep.get("output_power_driver"))
            if pin is not None and pout is not None:
                self.ax1.plot(pin, pout, "bo-")
            if sweep.get("gain") is not None:
                x_values = pout if pout is not None else range(len(sweep["gain"]))
                self.ax2.plot(x_values, sweep["gain"], "ro-")
                self.ax2.set_xlabel("输出功率 (dBm)" if pout is not None else "测量点")
                self.ax2.set_ylabel("增益 (dB)")
            if sweep.get("efficiency") is not None and pout is not None:
                self.ax3.plot(pout, sweep["efficiency"], "go-")
                self.ax3.set_xlabel("输出功率 (dBm)")
                self.ax3.set_ylabel("效率 (%)")
            if sweep.get("dc_power") is not None:
                self.ax4.plot(range(len(sweep["dc_power"])), sweep["dc_power"], "mo-")
                self.ax4.set_xlabel("测量点")
                self.ax4.set_ylabel("DC功耗 (W)")
            self.ax1.set_title(f"输入 vs 输出功率 @ {frequency} GHz")
            self.ax2.set_title(f"增益 @ {frequency} GHz")
            self.ax3.set_title(f"效率 @ {frequency} GHz")
            self.ax4.set_title(f"DC功耗 @ {frequency} GHz")
            for axis in self.figure.axes:
                axis.grid(True)
                axis.tick_params(labelsize=7)
            self.figure.tight_layout()
            self.canvas.draw()
        except Exception as error:
            print(f"Plot update error: {error}")

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
