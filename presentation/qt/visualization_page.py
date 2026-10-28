"""历史结果加载、频率浏览和报告副本入口。

页面只渲染控件并把用户动作转发出去：

- 历史列表、选中项、已加载结果和频率浏览都在只读视图模型里，页面不解析结果文档、
  不访问文件路径；
- 绘图数据由视图模型一次性从正式结果 DTO 的显式属性构建；
- 报告只有一个应用层实现（重构计划步骤 8）：页面把**当前已加载结果**的结果标识交给
  ``ResultExportServicePort`` 执行 ``ExportRequest(format=REPORT)``，不再自己调用报告
  服务，也不再在未加载结果时回退到“最新结果”。
"""

from __future__ import annotations

from typing import Any, Callable

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
from application.dto.export_requests import (
    ExportFormat,
    ExportOutcome,
    ExportRequest,
)
from application.ports.gui_data_service import ResultExportServicePort
from .history_view_model import (
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
        export_service: ResultExportServicePort | None = None,
        log_callback: Callable[[str], Any] | None = None,
        parent=None,
    ):
        if not isinstance(history, HistoryViewModel):
            raise TypeError("可视化页必须使用只读 HistoryViewModel")
        if export_service is not None and not callable(
            getattr(export_service, "execute", None)
        ):
            raise TypeError("报告生成必须走导出用例的 execute(request)")
        super().__init__(parent)
        self.history = history
        self.chooser = chooser
        self.export_service = export_service
        self.log_callback = log_callback or (lambda _message: None)
        self._build_ui()

    # -- 只读视图（窗口与聊天上下文只通过这里读取） -------------------------

    @property
    def loaded_result(self) -> MeasurementResult | None:
        loaded = self.history.loaded
        return None if loaded is None else loaded.result

    @property
    def loaded_result_id(self) -> str | None:
        """当前已加载结果在历史查询里的标识；未加载时为 ``None``。

        报告只针对这个标识执行，页面不持有路径，也不猜“最新结果”。
        """
        loaded = self.history.loaded
        return None if loaded is None else loaded.result_id

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
        self.generate_report_btn.setToolTip("基于当前已加载的历史结果生成测试报告副本")
        self.generate_report_btn.clicked.connect(self.generate_report)
        file_layout.addWidget(self.generate_report_btn)
        control_layout.addLayout(file_layout)

        self.hint_label = QLabel(
            "结果与报告在测试完成时自动归档；这里只浏览历史结果，"
            "“生成报告”仅针对当前已加载结果生成报告副本。"
        )
        self.hint_label.setWordWrap(True)
        control_layout.addWidget(self.hint_label)

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
            self._log(
                f"已加载{view.label}数据: {view.filename}（结果 {view.result_id}）"
            )
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
        """对**当前已加载**的历史结果生成测试报告副本。

        报告规则只在应用层：页面只描述“哪一条结果、哪种格式”，由导出用例核对结果是否
        在历史列表中、是否支持报告，并返回实际产物位置。未加载结果时不再回退到“最新
        结果”，否则报告来源无法追溯到明确的运行与结果标识。
        """
        loaded = self.history.loaded
        if loaded is None:
            QMessageBox.warning(
                self, "报告生成", "请先加载要生成报告的历史结果，再生成报告"
            )
            return False
        if self.export_service is None:
            QMessageBox.warning(self, "报告生成", "未配置报告生成服务")
            return False
        try:
            outcome = self.export_service.execute(
                ExportRequest(
                    result_id=loaded.result_id, format=ExportFormat.REPORT
                )
            )
            message = self._report_message(outcome)
        except Exception as error:  # noqa: BLE001 - 生成失败必须让用户看到原因
            self._log(f"报告生成失败: {error}")
            QMessageBox.warning(self, "报告生成", f"报告生成失败: {error}")
            return False
        self._log(message)
        QMessageBox.information(self, "报告生成", message)
        return True

    @staticmethod
    def _report_message(outcome: ExportOutcome) -> str:
        """把结构化导出结果写成用户可见文案。"""
        if not isinstance(outcome, ExportOutcome):
            raise TypeError("报告生成必须返回 ExportOutcome")
        if outcome.format is not ExportFormat.REPORT:
            raise TypeError(f"报告生成返回了 {outcome.format.label} 结果")
        return f"测试报告已生成副本: {outcome.artifact_path}"
