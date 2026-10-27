"""历史结果加载、频率浏览和报告入口。

页面只渲染控件并把用户动作转发给 :class:`HistoryViewModel`：历史列表、选中项、
已加载结果和频率浏览都在只读视图模型里，页面不解析结果文档、不访问文件路径。
绘图数据由视图模型一次性从正式结果 DTO 的显式属性构建。
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from application.dto import MeasurementResult
from .history_view_model import (
    AMPLIFIER_RESULT_TYPE,
    HistoryViewModel,
    result_kind,
    result_plot_series,
)
from .realtime_plot import RealTimePlotWidget

__all__ = [
    "VisualizationPage",
    "result_kind",
    "result_plot_series",
]


class VisualizationPage(QWidget):
    """独立持有历史查看状态，不访问仪器或 measurement worker。"""

    def __init__(
        self,
        *,
        history: HistoryViewModel,
        chooser: Callable[[], Any] | None = None,
        config_provider: Callable[[], dict[str, Any]] | None = None,
        report_service: Callable[[MeasurementResult, Mapping[str, Any], str], Any] | None = None,
        log_callback: Callable[[str], Any] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.history = history
        self.chooser = chooser
        self.config_provider = config_provider or (lambda: {})
        self.report_service = report_service
        self.log_callback = log_callback or (lambda _message: None)
        self._build_ui()

    # -- 只读视图（窗口与聊天上下文只通过这里读取） -------------------------

    @property
    def loaded_result(self) -> MeasurementResult | None:
        loaded = self.history.loaded
        return None if loaded is None else loaded.result

    @property
    def loaded_data(self) -> dict[str, dict[str, Any]] | None:
        """按频率分组的绘图数据**副本**；聊天上下文只读使用，不会改动视图模型。"""
        loaded = self.history.loaded
        return None if loaded is None else loaded.series_snapshot()

    @property
    def loaded_result_type(self) -> str | None:
        loaded = self.history.loaded
        return None if loaded is None else loaded.kind

    @property
    def frequency_list(self) -> list[float]:
        loaded = self.history.loaded
        return [] if loaded is None else list(loaded.frequencies)

    @property
    def current_freq_index(self) -> int:
        return self.history.frequency_index

    @property
    def loaded_filename(self) -> str:
        loaded = self.history.loaded
        return "" if loaded is None else loaded.filename

    # -- 界面 ---------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)
        controls = QGroupBox("可视化控制")
        control_layout = QVBoxLayout(controls)
        file_layout = QHBoxLayout()
        self.load_data_btn = QPushButton("加载测试数据")
        self.load_data_btn.clicked.connect(lambda _checked=False: self.load_test_data())
        file_layout.addWidget(self.load_data_btn)
        self.generate_report_btn = QPushButton("生成报告")
        self.generate_report_btn.clicked.connect(self.generate_report)
        file_layout.addWidget(self.generate_report_btn)
        control_layout.addLayout(file_layout)

        frequency_layout = QHBoxLayout()
        frequency_layout.addWidget(QLabel("频率切换:"))
        self.freq_prev_btn = QPushButton("◀ 上一个")
        self.freq_prev_btn.setEnabled(False)
        self.freq_prev_btn.clicked.connect(self.prev_frequency)
        frequency_layout.addWidget(self.freq_prev_btn)
        self.freq_label = QLabel("未加载数据")
        self.freq_label.setAlignment(Qt.AlignCenter)
        frequency_layout.addWidget(self.freq_label)
        self.freq_next_btn = QPushButton("下一个 ▶")
        self.freq_next_btn.setEnabled(False)
        self.freq_next_btn.clicked.connect(self.next_frequency)
        frequency_layout.addWidget(self.freq_next_btn)
        frequency_layout.addStretch()
        control_layout.addLayout(frequency_layout)
        layout.addWidget(controls)

        self.data_plot_widget = RealTimePlotWidget()
        layout.addWidget(self.data_plot_widget)

    def _log(self, message: str):
        self.log_callback(message)

    def _set_empty_state(self, message: str):
        self.history.clear()
        for axis in self.data_plot_widget.figure.axes:
            axis.clear()
        self.data_plot_widget.canvas.draw_idle()
        self.freq_label.setText(message)
        self.freq_prev_btn.setEnabled(False)
        self.freq_next_btn.setEnabled(False)

    # -- 用户动作 -----------------------------------------------------------

    def load_test_data(self):
        """通过结果查询服务选择并加载一条历史结果。"""
        try:
            if self.chooser is None:
                raise RuntimeError("未配置结果读取服务")
            loaded = self.chooser()
            if loaded is None:
                return False
            view = self.history.show(loaded)
            self.update_frequency_display()
            self.display_current_frequency_data()
            self._log(f"已加载{view.label}数据: {view.filename}")
            return True
        except Exception as error:
            self._log(f"数据加载失败: {error}")
            self._set_empty_state("数据加载失败")
            return False

    def update_frequency_display(self):
        frequency = self.history.current_frequency
        if frequency is None:
            self.freq_label.setText("未加载数据")
            self.freq_prev_btn.setEnabled(False)
            self.freq_next_btn.setEnabled(False)
            return
        self.freq_label.setText(
            f"{frequency} GHz ({self.history.frequency_index + 1}/{self.history.frequency_count})"
        )
        self.freq_prev_btn.setEnabled(self.history.has_previous)
        self.freq_next_btn.setEnabled(self.history.has_next)

    def display_current_frequency_data(self):
        loaded = self.history.loaded
        if loaded is None:
            return
        record = loaded.plot_record(self.history.frequency_index)
        if record is None:
            return
        self.data_plot_widget.update_plot(record)
        self._log(f"显示频率 {record['frequency']} GHz 的数据")

    def prev_frequency(self):
        if self.history.previous_frequency():
            self.update_frequency_display()
            self.display_current_frequency_data()

    def next_frequency(self):
        if self.history.next_frequency():
            self.update_frequency_display()
            self.display_current_frequency_data()

    def generate_report(self):
        loaded = self.history.loaded
        if loaded is not None and not loaded.result.is_amplifier:
            QMessageBox.warning(self, "报告生成", "驱动映射数据不支持功放测试报告")
            return False
        try:
            if self.report_service is None:
                raise RuntimeError("未配置报告生成服务")
            if loaded is not None:
                if not loaded.result.point_count:
                    raise ValueError("结果中没有可生成报告的扫描点")
                self.report_service(
                    loaded.result, self.config_provider(), loaded.filename
                )
                self._log("基于已加载数据生成测试报告")
            else:
                try:
                    latest = self.history.load_latest(AMPLIFIER_RESULT_TYPE)
                except FileNotFoundError:
                    QMessageBox.warning(
                        self, "报告生成", "未找到测试数据文件，请先加载数据或进行测试"
                    )
                    return False
                self.update_frequency_display()
                self.display_current_frequency_data()
                self.report_service(
                    latest.result, self.config_provider(), latest.filename
                )
                self._log(f"基于文件 {latest.filename} 生成测试报告")
            QMessageBox.information(self, "报告生成", "测试报告已生成完成")
            return True
        except Exception as error:
            self._log(f"报告生成失败: {error}")
            QMessageBox.warning(self, "报告生成", f"报告生成失败: {error}")
            return False
