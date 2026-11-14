"""历史结果加载、频率浏览、最终报告查看与“另存为”入口。

页面只渲染控件并把用户动作转发出去：

- 历史列表、选中项、已加载结果和频率浏览都在只读视图模型里，页面不解析结果文档、
  不访问文件路径；加载时通过“选择历史结果”对话框列出可选结果（见
  ``history_result_dialog``）；
- 绘图数据由视图模型一次性从正式结果 DTO 的显式属性构建；当前频点结果自带
  压缩目标（``compression_type`` + ``small_signal_gain_db``）时，增益图画出目标
  增益带，没有目标就明确不画；
- “打开测试报告”只针对**当前已加载结果**：页面把结果标识交给
  ``RunHistoryServicePort.report_for_result()``，由应用层定位同一次运行归档的最终
  报告，页面不解析结果标识、不判断产物类型；
- “另存为”只针对**当前已加载结果**：页面把结果标识交给
  ``ResultExportServicePort`` 执行 ``ExportRequest``（JSON / CSV / 测试报告由菜单选择），
  不再自己调用报告服务，也不再在未加载结果时回退到“最新结果”。
"""

from __future__ import annotations

import math
from typing import Any, Callable

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
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
from application.dto.run_history import ResultReportLocation, ResultReportState
from application.ports.gui_data_service import (
    ResultExportServicePort,
    RunHistoryServicePort,
)
from .history_result_dialog import HistoryResultDialog
from .history_view_model import (
    HistoryViewModel,
    result_kind,
    result_plot_series,
)
from .frequency_filmstrip import FrequencyFilmstrip
from .measurement_surface import (
    CONTROL_SURFACE,
    RESULT_SURFACE,
    build_surface,
    hairline,
    section_caption,
)
from .realtime_plot import PlotTargetBand, RealTimePlotWidget
from .theme import (
    MEASUREMENT_LEFT_PANEL_MAX_WIDTH,
    MEASUREMENT_LEFT_PANEL_MIN_WIDTH,
    set_button_variant,
)

_EXPORT_FILTERS = {
    ExportFormat.JSON: "JSON files (*.json)",
    ExportFormat.CSV: "CSV files (*.csv)",
}
# “另存为”菜单：一个入口覆盖 JSON、CSV 与测试报告副本，具体格式由用户选择。
_SAVE_AS_ACTIONS = (
    ("JSON…", ExportFormat.JSON),
    ("CSV…", ExportFormat.CSV),
    ("测试报告…", ExportFormat.REPORT),
)
_REPORT_HINT_UNLOADED = "请先加载一条历史结果"
_REPORT_HINT_READY = "打开这次运行归档的最终报告"
_REPORT_HINT_NO_SERVICE = "未装配历史运行服务，无法打开测试报告"
_REPORT_HINT_NO_REPORT = "当前结果没有可打开的最终报告"

# 主功放结果里的压缩点目标：只有结果自己带 ``compression_type`` 时才画目标带。
_COMPRESSION_TARGETS_DB = {"1db": 1.0, "3db": 3.0, "5db": 5.0}
# 目标带的可视厚度（±dB）：压缩点是一个增益值，这里只把线画成一条容易看见的窄带，
# 不代表任何合格/不合格容差。
_TARGET_BAND_HALF_DB = 0.1

__all__ = [
    "VisualizationPage",
    "result_kind",
    "result_plot_series",
]


def _save_dialog_title(export_format: ExportFormat) -> str:
    """“另存为”文件对话框标题；测试报告不使用文件对话框。"""
    if export_format is ExportFormat.JSON:
        return "另存结果"
    if export_format is ExportFormat.CSV:
        return "另存 CSV"
    raise ValueError(f"该格式不使用文件对话框: {export_format}")


def _save_action_text(export_format: ExportFormat) -> str:
    """日志里区分“另存为”和“生成报告副本”，避免文案与操作不符。"""
    if export_format is ExportFormat.REPORT:
        return "测试报告副本生成"
    return f"{export_format.label} 另存为"


def _suggested_filename(filename: str, suffix: str | None) -> str:
    """文件对话框的默认文件名；后缀与导出格式一致。"""
    if suffix is None:
        raise ValueError("该格式不使用默认文件名")
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return f"{stem}.{suffix.lstrip('.')}"


class VisualizationPage(QWidget):
    """独立持有历史查看状态，不访问仪器或 measurement worker。"""

    def __init__(
        self,
        *,
        history: HistoryViewModel,
        chooser: Callable[[], Any] | None = None,
        export_service: ResultExportServicePort | None = None,
        run_history_service: RunHistoryServicePort | None = None,
        log_callback: Callable[[str], Any] | None = None,
        parent=None,
    ):
        if not isinstance(history, HistoryViewModel):
            raise TypeError("可视化页必须使用只读 HistoryViewModel")
        if export_service is not None and not callable(
            getattr(export_service, "execute", None)
        ):
            raise TypeError("导出必须走导出用例的 execute(request)")
        if run_history_service is not None and not callable(
            getattr(run_history_service, "report_for_result", None)
        ):
            raise TypeError(
                "打开测试报告必须走历史运行服务的 report_for_result(result_id)"
            )
        super().__init__(parent)
        self.history = history
        self.chooser = chooser
        self.export_service = export_service
        self.run_history_service = run_history_service
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

        另存为只针对这个标识执行，页面不持有路径，也不猜“最新结果”。
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
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(12)

        # 可视化和三个测量页使用同一套“左控制面 + 右结果面”：切到 04 时不会突然
        # 从两栏控制台变成一张整宽表单。左栏只放浏览与导出动作，右侧整块留给图。
        left_panel = QWidget()
        left_panel.setMinimumWidth(MEASUREMENT_LEFT_PANEL_MIN_WIDTH)
        left_panel.setMaximumWidth(MEASUREMENT_LEFT_PANEL_MAX_WIDTH)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)

        controls = build_surface(CONTROL_SURFACE, title="结果浏览")
        control_layout = controls.layout()

        self.load_data_btn = QPushButton("加载测试数据")
        set_button_variant(self.load_data_btn, "primary")
        self.load_data_btn.clicked.connect(lambda _checked=False: self.load_test_data())
        control_layout.addWidget(self.load_data_btn)

        self.open_report_button = QPushButton("打开测试报告")
        set_button_variant(self.open_report_button, "secondary")
        self.open_report_button.setToolTip(_REPORT_HINT_UNLOADED)
        self.open_report_button.setEnabled(False)
        self.open_report_button.clicked.connect(
            lambda _checked=False: self.open_test_report()
        )
        control_layout.addWidget(self.open_report_button)

        self.save_as_button = QPushButton("另存为…")
        set_button_variant(self.save_as_button, "secondary")
        self.save_as_button.setToolTip(
            "将当前已加载的历史结果另存为 JSON、CSV 或测试报告副本"
        )
        self.save_as_menu = QMenu(self.save_as_button)
        for text, export_format in _SAVE_AS_ACTIONS:
            action = self.save_as_menu.addAction(text)
            action.triggered.connect(
                lambda _checked=False, value=export_format: self.export(value)
            )
        self.save_as_button.setMenu(self.save_as_menu)
        # 没有已加载结果时没有任何可另存的对象，按钮保持禁用。
        self.save_as_button.setEnabled(False)
        control_layout.addWidget(self.save_as_button)

        control_layout.addWidget(hairline())
        control_layout.addWidget(section_caption("频点浏览"))
        navigation = QHBoxLayout()
        navigation.setSpacing(8)
        self.freq_prev_btn = QPushButton("◀ 上一个")
        set_button_variant(self.freq_prev_btn, "secondary")
        self.freq_prev_btn.setEnabled(False)
        self.freq_prev_btn.clicked.connect(self.prev_frequency)
        navigation.addWidget(self.freq_prev_btn)
        self.freq_label = QLabel("未加载数据")
        self.freq_label.setObjectName("freqBadge")
        self.freq_label.setAlignment(Qt.AlignCenter)
        self.freq_label.setMinimumWidth(120)
        navigation.addWidget(self.freq_label, 1)
        self.freq_next_btn = QPushButton("下一个 ▶")
        set_button_variant(self.freq_next_btn, "secondary")
        self.freq_next_btn.setEnabled(False)
        self.freq_next_btn.clicked.connect(self.next_frequency)
        navigation.addWidget(self.freq_next_btn)
        control_layout.addLayout(navigation)

        self.hint_label = QLabel(
            "结果与报告在测试完成时自动归档；这里可以加载历史结果动态查看、"
            "打开归档的最终报告，或用“另存为…”获取 JSON / CSV / 报告副本。"
        )
        self.hint_label.setProperty("role", "hint")
        self.hint_label.setWordWrap(True)
        control_layout.addWidget(self.hint_label)
        control_layout.addStretch(1)
        left_layout.addWidget(controls)
        left_layout.addStretch(1)
        layout.addWidget(left_panel)

        results = build_surface(RESULT_SURFACE, title="可视化结果")
        result_layout = results.layout()
        # 胶片条在图表上方：先看这条结果覆盖了哪些频点、当前在哪一个，再读曲线。
        # 左栏的上一个/下一个仍保留，用于逐点精调；两者共用同一份视图模型索引。
        self.frequency_filmstrip = FrequencyFilmstrip(results)
        self.frequency_filmstrip.frequency_selected.connect(self._select_frequency)
        result_layout.addWidget(self.frequency_filmstrip)
        self.data_plot_widget = RealTimePlotWidget()
        result_layout.addWidget(self.data_plot_widget, 1)
        layout.addWidget(results, 1)

    def on_activated(self):
        """页面可见时重新校验报告可用性：报告可能在后台补生成或由外部工具更新。"""
        self._refresh_report_button()

    def _log(self, message: str):
        self.log_callback(message)

    def _refresh_report_button(self) -> ResultReportLocation | None:
        """按当前已加载结果刷新“打开测试报告”：页面只消费应用层状态。"""
        loaded = self.history.loaded
        self.open_report_button.setEnabled(False)
        if loaded is None:
            self.open_report_button.setToolTip(_REPORT_HINT_UNLOADED)
            return None
        if self.run_history_service is None:
            self.open_report_button.setToolTip(_REPORT_HINT_NO_SERVICE)
            return None
        try:
            report = self.run_history_service.report_for_result(loaded.result_id)
        except (OSError, ValueError, RuntimeError) as error:
            self.open_report_button.setToolTip(f"无法定位测试报告: {error}")
            return None
        if report.state is ResultReportState.AVAILABLE and report.location:
            self.open_report_button.setEnabled(True)
            self.open_report_button.setToolTip(_REPORT_HINT_READY)
        else:
            self.open_report_button.setToolTip(
                report.reason or _REPORT_HINT_NO_REPORT
            )
        return report

    def _set_empty_state(self, message: str):
        self.history.clear()
        for axis in self.data_plot_widget.figure.axes:
            axis.clear()
        self.data_plot_widget.set_target_bands(())
        self.data_plot_widget.canvas.draw_idle()
        self.frequency_filmstrip.clear()
        self.freq_label.setText(message)
        self.freq_prev_btn.setEnabled(False)
        self.freq_next_btn.setEnabled(False)
        self.save_as_button.setEnabled(False)
        self.open_report_button.setEnabled(False)
        self.open_report_button.setToolTip(_REPORT_HINT_UNLOADED)

    # -- 用户动作 -----------------------------------------------------------

    def load_test_data(self):
        """通过历史结果对话框选择并加载一条历史结果。"""
        try:
            loaded = self._choose_result()
            if loaded is None:
                return False
            view = self.history.show(loaded)
            self.update_frequency_display()
            self.display_current_frequency_data()
            self.save_as_button.setEnabled(True)
            self._refresh_report_button()
            self._log(
                f"已加载{view.label}数据: {view.filename}（结果 {view.result_id}）"
            )
            return True
        except Exception as error:
            self._log(f"数据加载失败: {error}")
            self._set_empty_state("数据加载失败")
            return False

    def _choose_result(self):
        """返回用户选中的历史结果；测试可注入 ``chooser`` 代替真实对话框。"""
        if self.chooser is not None:
            return self.chooser()
        dialog = HistoryResultDialog(
            self.history,
            log_callback=self.log_callback,
            parent=self,
        )
        if dialog.exec() != QDialog.Accepted:
            return None
        return dialog.loaded_result

    def open_test_report(self) -> bool:
        """打开当前加载结果所属运行归档的最终测试报告。

        点击时重新向应用层定位一次，避免报告在别处被删除后仍按旧状态打开；页面不解析
        结果标识、不判断文件类型，也不拼接报告路径。
        """
        loaded = self.history.loaded
        if loaded is None:
            QMessageBox.warning(self, "打开测试报告", _REPORT_HINT_UNLOADED)
            return False
        if self.run_history_service is None:
            QMessageBox.warning(self, "打开测试报告", _REPORT_HINT_NO_SERVICE)
            return False
        try:
            report = self.run_history_service.report_for_result(loaded.result_id)
        except (OSError, ValueError, RuntimeError) as error:
            self._log(f"无法定位测试报告: {error}")
            QMessageBox.warning(self, "无法打开", f"无法定位测试报告: {error}")
            self._refresh_report_button()
            return False
        if report.state is not ResultReportState.AVAILABLE or not report.location:
            message = report.reason or _REPORT_HINT_NO_REPORT
            self._log(f"无法打开测试报告: {message}")
            QMessageBox.warning(self, "无法打开", message)
            self._refresh_report_button()
            return False
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(report.location)):
            self._log(f"系统无法打开测试报告: {report.location}")
            QMessageBox.warning(
                self, "无法打开", f"系统无法打开测试报告: {report.location}"
            )
            return False
        self._log(f"已打开测试报告: {report.location}")
        return True

    def update_frequency_display(self):
        frequency = self.history.current_frequency
        if frequency is None:
            self.freq_label.setText("未加载数据")
            self.freq_prev_btn.setEnabled(False)
            self.freq_next_btn.setEnabled(False)
            self.frequency_filmstrip.clear()
            return
        self.freq_label.setText(
            f"{frequency} GHz ({self.history.frequency_index + 1}/{self.history.frequency_count})"
        )
        self.freq_prev_btn.setEnabled(self.history.has_previous)
        self.freq_next_btn.setEnabled(self.history.has_next)
        loaded = self.history.loaded
        self.frequency_filmstrip.set_frequencies(
            loaded.frequencies if loaded is not None else (),
            self.history.frequency_index,
        )

    def display_current_frequency_data(self):
        loaded = self.history.loaded
        if loaded is None:
            return
        record = loaded.plot_record(self.history.frequency_index)
        if record is None:
            return
        self.data_plot_widget.set_target_bands(
            self._target_bands_for_current_frequency()
        )
        self.data_plot_widget.update_plot(record)
        self._log(f"显示频率 {record['frequency']} GHz 的数据")

    def _target_bands_for_current_frequency(self) -> tuple[PlotTargetBand, ...]:
        """当前频点结果自带压缩目标时，在增益图画出目标增益带。

        目标值只来自结果 DTO 自己的 ``small_signal_gain_db`` 与 ``compression_type``：
        目标增益 = 小信号增益 − 压缩量。没有这两项、或类型不认识时明确不画，
        不按配置猜测，也不把目标带当成合格/不合格判定。
        """
        loaded = self.history.loaded
        if loaded is None:
            return ()
        frequencies = getattr(loaded.result, "frequencies", ())
        index = self.history.frequency_index
        if not isinstance(frequencies, (list, tuple)) or not 0 <= index < len(
            frequencies
        ):
            return ()
        item = frequencies[index]
        small_signal_gain = getattr(item, "small_signal_gain_db", None)
        compression_type = getattr(item, "compression_type", None)
        if small_signal_gain is None or not isinstance(compression_type, str):
            return ()
        target_db = _COMPRESSION_TARGETS_DB.get(compression_type.strip().lower())
        if target_db is None:
            return ()
        try:
            small_signal_gain_db = float(small_signal_gain)
        except (TypeError, ValueError):
            return ()
        if not math.isfinite(small_signal_gain_db):
            return ()
        target_gain = small_signal_gain_db - target_db
        return (
            PlotTargetBand(
                1,
                target_gain - _TARGET_BAND_HALF_DB,
                target_gain + _TARGET_BAND_HALF_DB,
                f"{compression_type.strip()} 压缩目标",
            ),
        )

    def prev_frequency(self):
        if self.history.previous_frequency():
            self.update_frequency_display()
            self.display_current_frequency_data()

    def next_frequency(self):
        if self.history.next_frequency():
            self.update_frequency_display()
            self.display_current_frequency_data()

    def _select_frequency(self, frequency):
        """胶片条点击：按频率找到索引，切换后刷新标签与曲线。"""
        loaded = self.history.loaded
        if loaded is None:
            return
        try:
            index = list(loaded.frequencies).index(float(frequency))
        except (TypeError, ValueError):
            return
        if not self.history.select_frequency(index):
            return
        self.update_frequency_display()
        self.display_current_frequency_data()

    def export(self, export_format: ExportFormat):
        """按选定格式另存**当前已加载**的历史结果；页面只构造导出请求。

        导出规则只在应用层：页面只描述“哪一条结果、哪种格式、目标文件是什么”，由导出
        用例核对结果是否在历史列表中、格式是否适用，并返回实际产物位置。未加载结果时
        不再回退到“最新结果”，否则来源无法追溯到明确的运行与结果标识。
        """
        loaded = self.history.loaded
        if loaded is None:
            QMessageBox.warning(
                self, "另存为", "请先加载要另存的历史结果，再执行另存为"
            )
            return False
        if self.export_service is None:
            QMessageBox.warning(self, "另存为", "未配置导出服务")
            return False
        target = None
        if export_format.needs_target:
            target, _ = QFileDialog.getSaveFileName(
                self,
                _save_dialog_title(export_format),
                _suggested_filename(loaded.filename, export_format.suffix),
                _EXPORT_FILTERS[export_format],
            )
            if not target:
                return False
        try:
            outcome = self.export_service.execute(
                ExportRequest(
                    result_id=loaded.result_id,
                    format=export_format,
                    target=target,
                )
            )
            if outcome.format is not export_format:
                raise TypeError(
                    f"导出服务返回了 {outcome.format.label} 结果，"
                    f"请求的是 {export_format.label}"
                )
            message = self._success_message(outcome)
        except Exception as error:  # noqa: BLE001 - 另存失败必须让用户看到原因
            self._log(f"{_save_action_text(export_format)}失败: {error}")
            QMessageBox.warning(self, "另存为失败", str(error))
            return False
        self._log(message)
        QMessageBox.information(self, "另存为成功", message)
        return True

    @staticmethod
    def _success_message(outcome: ExportOutcome) -> str:
        """把结构化导出结果写成用户可见文案。"""
        if not isinstance(outcome, ExportOutcome):
            raise TypeError("导出服务必须返回 ExportOutcome")
        if outcome.format is ExportFormat.REPORT:
            return f"测试报告副本已生成: {outcome.artifact_path}"
        if outcome.format is ExportFormat.JSON:
            return f"结果已另存为: {outcome.artifact_path}"
        return f"CSV 已另存为: {outcome.artifact_path}"
